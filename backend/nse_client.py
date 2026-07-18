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
import os
from contextlib import contextmanager

import requests

logger = logging.getLogger("OmniSpread.nse")

# NSE either answers quickly or not at all; a short ceiling keeps a blocked
# edge from occupying a worker.
DEFAULT_TIMEOUT_SECONDS = 15.0

# NSE sits behind Akamai, which fingerprints the TLS handshake. nselib uses
# plain `requests`, whose handshake is not Chrome's, and Akamai now answers it
# with 403 no matter how browser-like the headers are. curl_cffi reproduces
# Chrome's handshake and is admitted. Set OMNISPREAD_NSE_TRANSPORT=requests to
# fall back to nselib's own transport.
NSE_HOME = "https://www.nseindia.com/"
IMPERSONATE = "chrome"
USE_CHROME_TRANSPORT = os.environ.get("OMNISPREAD_NSE_TRANSPORT", "chrome") != "requests"

_session = None


def _chrome_session(refresh: bool = False):
    """A curl_cffi session carrying Akamai's cookies, created once and reused."""
    global _session
    if _session is not None and not refresh:
        return _session
    from curl_cffi import requests as curl_requests

    session = curl_requests.Session(impersonate=IMPERSONATE)
    # The bot-manager cookies (_abck, ak_bmsc, bm_sz) are only issued to a
    # client that loads the site itself first.
    session.get(NSE_HOME, timeout=DEFAULT_TIMEOUT_SECONDS)
    _session = session
    return session


def _chrome_urlfetch(url, origin_url="http://nseindia.com"):
    """Drop-in replacement for nselib.libutil.nse_urlfetch."""
    session = _chrome_session()
    referer = origin_url if origin_url.startswith("https://") else NSE_HOME
    try:
        session.get(referer, headers={"Referer": NSE_HOME}, timeout=DEFAULT_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - priming is best-effort
        logger.debug("Referer prime failed for %s", referer)
    return session.get(
        url,
        headers={
            "Referer": referer,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


@contextmanager
def chrome_transport():
    """Route nselib's HTTP through a Chrome-fingerprinted client.

    nselib does `from nselib.libutil import *`, so the name has to be rebound in
    each module that imported it, not just on libutil.
    """
    if not USE_CHROME_TRANSPORT:
        yield
        return

    try:
        from nselib import libutil
        from nselib.derivatives import get_func
    except ImportError:
        # Patching is an enhancement, not a requirement: if nselib's internals
        # move, fall back to its own transport rather than failing the call.
        logger.debug("nselib internals not patchable; using its own transport")
        yield
        return

    targets = [libutil, get_func]
    originals = [getattr(module, "nse_urlfetch", None) for module in targets]
    for module in targets:
        module.nse_urlfetch = _chrome_urlfetch
    try:
        yield
    finally:
        for module, original in zip(targets, originals):
            if original is not None:
                module.nse_urlfetch = original


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
    with request_timeout(), chrome_transport():
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
