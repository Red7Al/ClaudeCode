"""The Fundamentals and Broker panels are served from a precompute, never fetched on the request thread.

WHY THIS FILE EXISTS. Both endpoints called yfinance inline. yfinance imports pandas, pandas imports numpy,
and numpy is SIGSYS-killed on the IONOS host, so both answered HTTP 500 after ~120 SECONDS and had been
dead for weeks (measured live 2026-09-18, ChangeRequests/20260918.txt P-01 DEAD CARDS).

The most important test here is test_the_server_never_imports_yfinance: a try/except cannot rescue a
SIGSYS, because it kills the process rather than raising. Re-introducing the import as a "fallback" would
not degrade the panel, it would take the whole worker down — so the guard is the import, not the call.
"""

import ast
import inspect
import pathlib

import pytest

import run_fundamentals_precompute as rfp
from hvf_web import server

ROOT = pathlib.Path(__file__).parent


# ── what counts as an instrument with a company behind it ─────────────────────────────────────────────

@pytest.mark.parametrize("ticker,expected", [
    ("AAF.L", True), ("AAPL", True),
    ("GBPCAD=X", False), ("CL=F", False), ("^FTSE", False),
    ("BTC-USD", False), ("ETH-USDT", False), ("", False),
])
def test_only_equities_are_fetched(ticker, expected):
    assert rfp.is_equity(ticker) is expected


def test_the_server_and_the_fetcher_agree_on_what_is_an_equity():
    """Two definitions of 'has no company behind it' would drift, and the panel would then either fetch
    nothing for a real company or store junk for an index."""
    for t in ("AAF.L", "AAPL", "GBPCAD=X", "CL=F", "^FTSE", "BTC-USD", "ETH-USDT"):
        assert rfp.is_equity(t) is not server._is_non_equity(t), t


# ── the endpoints serve the stored copy ───────────────────────────────────────────────────────────────

def _stub_panel(monkeypatch, store_key, records):
    monkeypatch.setitem(server._PANEL_CACHE, store_key, {"data": records, "ts": 9e18})


def test_fundamentals_serves_the_precomputed_record(monkeypatch):
    _stub_panel(monkeypatch, server._FUND_STORE_KEY,
                {"AAF.L": {"currency": "GBp", "kpis": {"trailingPE": 12.5, "beta": 0.9}}})
    body = server.app.test_client().get("/api/fundamentals/AAF.L").get_json()
    assert body["currency"] == "GBp"
    assert body["kpis"]["trailingPE"] == 12.5
    assert body["stale"] is False


def test_broker_serves_the_precomputed_record(monkeypatch):
    _stub_panel(monkeypatch, server._BROKER_STORE_KEY,
                {"AAF.L": {"up6": 3, "down6": 1, "up12": 5, "down12": 2, "available": 1}})
    body = server.app.test_client().get("/api/broker/AAF.L").get_json()
    assert (body["up6"], body["down6"], body["up12"], body["down12"]) == (3, 1, 5, 2)
    assert body["available"] is True


def test_a_missing_instrument_says_why_rather_than_rendering_blank(monkeypatch):
    """A blank card that looks identical to 'this company has no data' is the silent failure the whole
    item exists to end."""
    _stub_panel(monkeypatch, server._FUND_STORE_KEY, {})
    body = server.app.test_client().get("/api/fundamentals/AAF.L").get_json()
    assert body["kpis"] == {}
    assert "not been collected" in (body.get("note") or ""), body


def test_a_non_equity_is_answered_without_touching_the_store(monkeypatch):
    def explode(_key):
        raise AssertionError("the store must not be consulted for a non-equity instrument")
    monkeypatch.setattr(server, "_panel_records", explode)
    body = server.app.test_client().get("/api/fundamentals/GBPCAD=X").get_json()
    assert body["kpis"] == {}
    assert "non-equity" in (body.get("note") or "").lower()


# ── the guard that matters ────────────────────────────────────────────────────────────────────────────

def test_the_server_never_imports_yfinance():
    """A SIGSYS kills the process; it does not raise. So a 'safe' try/except fallback would not degrade
    the panel, it would take the worker down. The import itself is therefore the thing forbidden."""
    tree = ast.parse((ROOT / "hvf_web" / "server.py").read_text(encoding="utf-8", errors="replace"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("yfinance", "pandas", "numpy", "matplotlib"):
        assert banned not in imported, f"hvf_web/server.py imports {banned}, which is SIGSYS-killed on the host"


def test_the_panel_reader_does_not_fetch_anything():
    # Assert on the CODE with the docstring stripped: that docstring explains at length why yfinance is
    # banned here, and would otherwise satisfy the very rule it describes.
    fn = ast.parse(inspect.getsource(server._panel_records)).body[0]
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    src = ast.unparse(fn)
    for banned in ("yfinance", "urlopen", "requests"):
        assert banned not in src, f"_panel_records must only read the store, found {banned}"


# ── refusing to store nothing ─────────────────────────────────────────────────────────────────────────

def test_an_empty_fetch_is_refused_rather_than_overwriting_a_good_copy(monkeypatch):
    """A Yahoo outage must make the panel stale, never blank -- the same rule the winners precompute
    applies, and the reason that one caught a real degraded write."""
    monkeypatch.setattr(rfp, "universe", lambda: ["AAF.L", "AAPL"])
    monkeypatch.setattr(rfp, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(rfp, "fetch_broker", lambda t: None)
    written = []
    import web_store
    monkeypatch.setattr(web_store, "save_json_store", lambda k, d: written.append(k) or True)

    assert rfp.build() == 2, "both payloads should be reported as failed"
    assert written == [], "nothing may be written when the fetch produced nothing"


def test_a_good_fetch_is_stored_under_both_keys(monkeypatch):
    monkeypatch.setattr(rfp, "universe", lambda: ["AAF.L", "^FTSE"])
    monkeypatch.setattr(rfp, "fetch_fundamentals", lambda t: {"currency": "GBp", "kpis": {"beta": 1.0}})
    monkeypatch.setattr(rfp, "fetch_broker", lambda t: {"up6": 1, "down6": 0, "up12": 1, "down12": 0,
                                                       "available": 1})
    saved = {}
    import web_store
    monkeypatch.setattr(web_store, "save_json_store", lambda k, d: saved.__setitem__(k, d) or True)

    assert rfp.build() == 0
    assert set(saved) == {rfp.FUND_STORE_KEY, rfp.BROKER_STORE_KEY}
    # ^FTSE is an index and must never reach the store
    assert list(saved[rfp.FUND_STORE_KEY]["records"]) == ["AAF.L"]
    assert saved[rfp.FUND_STORE_KEY]["count"] == 1


def test_an_empty_universe_is_refused(monkeypatch):
    monkeypatch.setattr(rfp, "universe", lambda: [])
    assert rfp.build() == 2
