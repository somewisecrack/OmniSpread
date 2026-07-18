"""Guarded access to NSE derivatives data via nselib.

nselib issues its requests with no timeout, and NSE's Akamai edge does not
always fail fast: an unwelcome client may be tarpitted rather than refused, so
the underlying socket can hang indefinitely. Inside a request handler that
stalls the whole call, which is how a blocked NSE turns into a proxy reset and
an opaque "Internal Server Error" in the browser.

This module wraps the nselib entry points so that:

  * every HTTP call gets a bounded timeout, and
  * the various low-level failures collapse into one NseUnavailable error whose
    message says what actually happened.

nselib reports a non-JSON response as "Invalid parameters", which is misleading
when the real cause is an edge block, so that case is re-labelled here.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

import requests

logger = logging.getLogger("OmniSpread.nse")

# NSE either answers quickly or not at all; a short ceiling keeps a blocked
# edge from occupying a worker.
DEFAULT_TIMEOUT_SECONDS = 15.0


class NseUnavailable(RuntimeError):
    """NSE could not be reached or refused to serve this host."""


@contextmanager
def request_timeout(seconds: float = DEFAULT_TIMEOUT_SECONDS):
    """Apply a default timeout to requests made by libraries that omit one."""
    original = requests.Session.request

    def with_timeout(self, *args, **kwargs):
        kwargs.setdefault("timeout", seconds)
        return original(self, *args, **kwargs)

    requests.Session.request = with_timeout
    try:
        yield
    finally:
        requests.Session.request = original


def _describe(exc: Exception) -> str:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return (
            "NSE did not respond within "
            f"{DEFAULT_TIMEOUT_SECONDS:.0f}s. The edge is most likely refusing "
            "this host (NSE blocks many non-Indian and datacenter addresses)."
        )
    text = str(exc)
    if "Expecting value" in text or "Invalid parameters" in text:
        # nselib surfaces any non-JSON body as "Invalid parameters", but an
        # Akamai "Access Denied" page produces exactly the same symptom.
        return (
            "NSE returned a non-JSON response (usually an Akamai 'Access Denied' "
            "page). The request was blocked rather than rejected for bad input."
        )
    return text


def _guard(call, /, **kwargs):
    with request_timeout():
        try:
            return call(**kwargs)
        except Exception as exc:  # noqa: BLE001 - deliberately collapsed
            message = _describe(exc)
            logger.warning("NSE fetch failed (%s): %s", kwargs.get("symbol"), message)
            raise NseUnavailable(message) from exc


def fetch_future(**kwargs):
    """Historical futures prices. Raises NseUnavailable when NSE is blocked."""
    from nselib import derivatives

    return _guard(derivatives.future_price_volume_data, **kwargs)


def fetch_option(**kwargs):
    """Historical option chain. Raises NseUnavailable when NSE is blocked."""
    from nselib import derivatives

    return _guard(derivatives.option_price_volume_data, **kwargs)
