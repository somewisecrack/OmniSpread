"""Regression tests for the guarded NSE client.

Every test here mocks the network. Nothing in this module touches NSE, so the
suite stays runnable when the edge is blocking us (which it frequently is).
"""
import pandas as pd
import pytest
import requests

import nse_client
from nse_client import NseUnavailable, fetch_future, fetch_option, request_timeout


def test_request_timeout_injects_a_default_and_restores_the_original():
    original = requests.Session.request
    seen = {}

    def fake_request(self, *args, **kwargs):
        seen.update(kwargs)
        return "ok"

    requests.Session.request = fake_request
    try:
        with request_timeout(7.5):
            requests.Session().request("GET", "https://example.invalid")
        assert seen["timeout"] == 7.5
    finally:
        requests.Session.request = original
    assert requests.Session.request is original


def test_request_timeout_does_not_override_an_explicit_timeout():
    original = requests.Session.request
    seen = {}

    def fake_request(self, *args, **kwargs):
        seen.update(kwargs)
        return "ok"

    requests.Session.request = fake_request
    try:
        with request_timeout(7.5):
            requests.Session().request("GET", "https://example.invalid", timeout=1.0)
        assert seen["timeout"] == 1.0
    finally:
        requests.Session.request = original


def test_request_timeout_restores_the_original_after_an_exception():
    original = requests.Session.request
    with pytest.raises(RuntimeError):
        with request_timeout(5.0):
            raise RuntimeError("boom")
    assert requests.Session.request is original


def _patch_nselib(monkeypatch, future=None, option=None):
    """Install a stub `nselib` package so no real request is ever made.

    The stub mirrors the real layout (`nselib.libutil`, `nselib.derivatives.get_func`,
    each exposing `nse_urlfetch`) so the Chrome-transport patching is exercised
    rather than skipped.
    """
    import sys
    import types

    def sentinel(url, origin_url="http://nseindia.com"):
        raise AssertionError("the stub transport must never be called")

    libutil = types.ModuleType("nselib.libutil")
    libutil.nse_urlfetch = sentinel
    get_func = types.ModuleType("nselib.derivatives.get_func")
    get_func.nse_urlfetch = sentinel

    derivatives = types.ModuleType("nselib.derivatives")
    derivatives.get_func = get_func
    derivatives.future_price_volume_data = future or (lambda **kw: pd.DataFrame())
    derivatives.option_price_volume_data = option or (lambda **kw: pd.DataFrame())

    module = types.ModuleType("nselib")
    module.libutil = libutil
    module.derivatives = derivatives

    for name, mod in [
        ("nselib", module),
        ("nselib.libutil", libutil),
        ("nselib.derivatives", derivatives),
        ("nselib.derivatives.get_func", get_func),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    return libutil, get_func, sentinel


def test_fetch_future_returns_the_frame_on_success(monkeypatch):
    frame = pd.DataFrame([{"SYMBOL": "NIFTY", "CLOSING_PRICE": 100.0}])
    _patch_nselib(monkeypatch, future=lambda **kw: frame)
    assert fetch_future(symbol="NIFTY", instrument="FUTIDX").equals(frame)


def test_timeout_is_reported_as_a_block_not_a_bad_request(monkeypatch):
    def tarpit(**kwargs):
        raise requests.Timeout("Read timed out. (read timeout=15)")

    _patch_nselib(monkeypatch, future=tarpit)
    with pytest.raises(NseUnavailable) as excinfo:
        fetch_future(symbol="NIFTY", instrument="FUTIDX")
    message = str(excinfo.value)
    assert "did not respond" in message
    assert "refusing" in message


def test_non_json_body_is_relabelled_away_from_invalid_parameters(monkeypatch):
    """An Akamai deny page reaches nselib as 'Invalid parameters'; say what it is."""
    def blocked(**kwargs):
        raise ValueError(" Invalid parameters : NSE error:Expecting value: line 2 column 1 (char 1)")

    _patch_nselib(monkeypatch, option=blocked)
    with pytest.raises(NseUnavailable) as excinfo:
        fetch_option(symbol="NIFTY", instrument="OPTIDX")
    message = str(excinfo.value)
    assert "Access Denied" in message
    assert "blocked rather than rejected" in message


def test_unexpected_errors_keep_their_own_message(monkeypatch):
    _patch_nselib(monkeypatch, future=lambda **kw: (_ for _ in ()).throw(KeyError("SYMBOL")))
    with pytest.raises(NseUnavailable) as excinfo:
        fetch_future(symbol="NIFTY", instrument="FUTIDX")
    assert "SYMBOL" in str(excinfo.value)


def test_chrome_transport_rebinds_both_modules_and_restores_them(monkeypatch):
    """nselib does `from libutil import *`, so both names must be rebound."""
    libutil, get_func, sentinel = _patch_nselib(monkeypatch)
    with nse_client.chrome_transport():
        assert libutil.nse_urlfetch is nse_client._chrome_urlfetch
        assert get_func.nse_urlfetch is nse_client._chrome_urlfetch
    assert libutil.nse_urlfetch is sentinel
    assert get_func.nse_urlfetch is sentinel


def test_chrome_transport_restores_after_an_exception(monkeypatch):
    libutil, get_func, sentinel = _patch_nselib(monkeypatch)
    with pytest.raises(RuntimeError):
        with nse_client.chrome_transport():
            raise RuntimeError("boom")
    assert libutil.nse_urlfetch is sentinel
    assert get_func.nse_urlfetch is sentinel


def test_chrome_transport_can_be_disabled_by_env(monkeypatch):
    libutil, get_func, sentinel = _patch_nselib(monkeypatch)
    monkeypatch.setattr(nse_client, "USE_CHROME_TRANSPORT", False)
    with nse_client.chrome_transport():
        assert libutil.nse_urlfetch is sentinel  # left alone


def test_chrome_transport_degrades_when_nselib_internals_are_absent(monkeypatch):
    """A layout change upstream must not break the call."""
    import sys
    import types

    bare = types.ModuleType("nselib")
    monkeypatch.setitem(sys.modules, "nselib", bare)
    for name in ["nselib.libutil", "nselib.derivatives", "nselib.derivatives.get_func"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    with nse_client.chrome_transport():
        pass  # must not raise


def test_guard_applies_the_module_timeout(monkeypatch):
    """The bounded timeout must actually be in force during an nselib call."""
    captured = {}
    original = requests.Session.request

    def fake_request(self, *args, **kwargs):
        captured.update(kwargs)
        return "ok"

    def calls_out(**kwargs):
        requests.Session().request("GET", "https://www.nseindia.com/api")
        return pd.DataFrame()

    _patch_nselib(monkeypatch, future=calls_out)
    requests.Session.request = fake_request
    try:
        fetch_future(symbol="NIFTY", instrument="FUTIDX")
    finally:
        requests.Session.request = original
    assert captured["timeout"] == nse_client.DEFAULT_TIMEOUT_SECONDS
