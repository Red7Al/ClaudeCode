"""Backend tests for /api/pricebars — the client-drawn price chart (ChangeRequests 2026-09-13, P-01).

This endpoint exists because the server-rendered one could not run here at all: /api/pricewin rendered with
matplotlib, matplotlib imports numpy, and numpy is SIGSYS-killed on the IONOS host, so it returned HTTP 500
after ~120s on every call and the instrument detail chart was dead on the live site for weeks.

The most valuable test in this file is therefore test_the_price_path_never_pulls_numpy_back_in: a correct
payload that reintroduces the import would be a silent return of the original outage.
"""

import subprocess
import sys
import textwrap

from hvf_web import server


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def run(self, sql, **params):
        self.sql = sql
        self.params = params
        return self.rows

    def close(self):
        pass


def _patch_db(monkeypatch, rows):
    db = FakeDb(rows)
    monkeypatch.setattr("db_pool.get_db", lambda: db, raising=False)
    return db


def test_price_bars_returns_plain_date_and_close_pairs(monkeypatch):
    _patch_db(monkeypatch, [("2026-01-01", 10.5), ("2026-01-02", 11.0)])
    assert server._price_bars("ABC", 365) == [["2026-01-01", 10.5], ["2026-01-02", 11.0]]


def test_price_bars_skips_a_row_it_cannot_read_rather_than_failing(monkeypatch):
    """One unreadable close is a missing point. Raising instead would turn a single bad row into a dead
    chart -- which is the failure mode this whole endpoint was written to end."""
    _patch_db(monkeypatch, [("2026-01-01", 10.0), ("2026-01-02", None), ("2026-01-03", "x"),
                            ("2026-01-04", 12.0)])
    assert server._price_bars("ABC", 365) == [["2026-01-01", 10.0], ["2026-01-04", 12.0]]


def test_price_bars_asks_for_the_requested_window(monkeypatch):
    db = _patch_db(monkeypatch, [])
    server._price_bars("ABC", 365)
    assert db.params["t"] == "ABC"
    assert "bar_date>=:start" in db.sql and "order by bar_date" in db.sql


def _record(**card):
    return {"ticker": "AAF.L", "_card": card}


def test_the_endpoint_carries_the_levels_and_the_pivots_in_the_window(monkeypatch):
    _patch_db(monkeypatch, [("2026-01-01", 100.0), ("2026-06-01", 110.0)])
    monkeypatch.setattr(server, "_record", lambda t: _record(
        hvf_type="BULLISH", h3_level=115.0, stop_level=85.0, target=130.0,
        h1_date="2026-02-02", h1_level=108.0, l1_date="2026-03-03", l1_level=95.0))
    body = server.app.test_client().get("/api/pricebars/AAF.L?days=365").get_json()

    assert body["levels"] == {"entry": 115.0, "stop": 85.0, "target": 130.0}
    assert body["direction"] == "BULLISH"
    assert {(p["date"], p["kind"]) for p in body["pivots"]} == {("2026-02-02", "high"), ("2026-03-03", "low")}


def test_a_pivot_outside_the_window_is_left_out(monkeypatch):
    """The PNG only overlaid pivots inside the window. Keeping one outside it would draw a real pivot on a
    date it never happened, because the client clamps to the ends of the series it was given."""
    _patch_db(monkeypatch, [("2026-01-01", 100.0), ("2026-06-01", 110.0)])
    monkeypatch.setattr(server, "_record", lambda t: _record(
        h1_date="2020-01-01", h1_level=50.0, h2_date="2026-02-02", h2_level=108.0))
    body = server.app.test_client().get("/api/pricebars/AAF.L?days=365").get_json()
    assert [p["date"] for p in body["pivots"]] == ["2026-02-02"]


def test_an_unknown_ticker_is_404(monkeypatch):
    monkeypatch.setattr(server, "_record", lambda t: None)
    assert server.app.test_client().get("/api/pricebars/NOPE/").status_code in (404, 308)
    assert server.app.test_client().get("/api/pricebars/NOPE").status_code == 404


def test_no_bars_is_an_empty_series_not_an_error(monkeypatch):
    _patch_db(monkeypatch, [])
    monkeypatch.setattr(server, "_record", lambda t: _record())
    r = server.app.test_client().get("/api/pricebars/AAF.L")
    assert r.status_code == 200
    assert r.get_json()["bars"] == [] and r.get_json()["pivots"] == []


def test_the_price_path_never_pulls_numpy_back_in():
    """THE POINT OF THE WHOLE ENDPOINT. price_store imports pandas at module level and pandas imports
    numpy, which is SIGSYS-killed on the host -- so routing this through price_store would reintroduce the
    exact outage in a payload that still looks correct in every other test here."""
    # The docstring explains at length why price_store is avoided, so assert on the CODE with the
    # docstring stripped -- otherwise the comment describing the rule would satisfy the rule.
    import ast
    import inspect
    fn = ast.parse(textwrap.dedent(inspect.getsource(server._price_bars))).body[0]
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)
    assert "price_store" not in code, "_price_bars must not go through price_store (it imports pandas)"

    script = textwrap.dedent("""
        import sys
        from hvf_web import server
        heavy = [m for m in ("numpy", "pandas", "matplotlib", "yfinance") if m in sys.modules]
        print(",".join(heavy))
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "", f"importing the server now pulls in {out.stdout.strip()}"
