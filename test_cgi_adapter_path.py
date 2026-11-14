"""IONOS hands the adapter the RAW url. A ticker such as ^GDAXI arrives as %5EGDAXI and must reach Flask decoded, or
/api/pricebars/^GDAXI answers 'unknown ticker' and the instrument page has no price chart (measured live 2026-11-14:
18 index tickers affected, 39 of 40 other punctuated tickers fine)."""
import importlib.util
import pathlib

import pytest

ADAPTER = pathlib.Path(__file__).parent / "cgi-bin" / "app.py"


def _adapter(monkeypatch):
    spec = importlib.util.spec_from_file_location("cgi_adapter_under_test", ADAPTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = {}
    monkeypatch.setattr(mod.app, "wsgi_app", lambda env, start: seen.update(env) or [])
    return mod, seen


@pytest.mark.parametrize("uri,path", [
    ("/cgi-bin/app.py/api/pricebars/%5EGDAXI?days=365", "/api/pricebars/^GDAXI"),
    ("/api/pricebars/%5EFTMC", "/api/pricebars/^FTMC"),
    ("/api/pricebars/AAPL?days=5", "/api/pricebars/AAPL"),
    ("/api/pricebars/B%26M.L", "/api/pricebars/B&M.L"),
])
def test_percent_encoded_ticker_reaches_flask_decoded(monkeypatch, uri, path):
    mod, seen = _adapter(monkeypatch)
    mod.application({"REQUEST_URI": uri}, lambda *a: None)
    assert seen["PATH_INFO"] == path

