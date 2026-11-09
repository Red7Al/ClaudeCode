"""Independent test-specialist additions for the get_epic alert gate (ig_shim._epic_alert / live_tickers).

The author's tests mock live_tickers() itself, so nothing proved the SQL that defines "live", that the connection is
closed, that the real notify.alert_system_error really suppresses ONLY the email, or that each of get_epic's four alert
sites reaches the gate. Mutation run (specialist, 2026-11-09) found those holes; these tests close them."""
import pytest

import ig_shim
import notify


class _FakeDb:
    """A DB double that records SQL and closes; returns the rows it was built with for the live-ticker query."""
    opened = 0
    closed = 0

    def __init__(self, rows=None, raise_on_run=False, first_select=None):
        self.rows, self.raise_on_run, self.sql = rows or [], raise_on_run, []
        self.first_select = first_select
        _FakeDb.opened += 1

    def run(self, sql, **kw):
        self.sql.append(sql)
        if self.raise_on_run:
            raise RuntimeError("boom")
        if self.first_select is not None and sql.lstrip().startswith("select epic"):
            return self.first_select
        return self.rows

    def close(self):
        _FakeDb.closed += 1


@pytest.fixture(autouse=True)
def _reset_counts():
    _FakeDb.opened = _FakeDb.closed = 0


def _use_db(monkeypatch, db):
    # The real path is ig_shim.get_db -> db_pool.get_db (imported inside the function): patch the source.
    monkeypatch.setattr("db_pool.get_db", lambda: db)


# --- live_tickers: the one definition of "live" -----------------------------------------------------------------

def test_live_tickers_reads_pending_watching_orders_and_all_positions(monkeypatch):
    db = _FakeDb(rows=[("SHAW.L",), ("AAPL",), ("SHAW.L",)])
    _use_db(monkeypatch, db)
    assert ig_shim.live_tickers() == {"SHAW.L", "AAPL"}
    sql = " ".join(db.sql).lower()
    assert "working_orders" in sql and "positions" in sql, "an open position is live too"
    assert "'pending'" in sql and "'watching'" in sql
    for dead in ("deleted", "filled", "expired", "cancelled"):
        assert dead not in sql, f"a {dead} order is not live"


def test_live_tickers_closes_the_connection_on_success_and_on_error(monkeypatch):
    _use_db(monkeypatch, _FakeDb(rows=[("X",)]))
    ig_shim.live_tickers()
    assert (_FakeDb.opened, _FakeDb.closed) == (1, 1)
    _use_db(monkeypatch, _FakeDb(raise_on_run=True))
    with pytest.raises(RuntimeError):
        ig_shim.live_tickers()
    assert (_FakeDb.opened, _FakeDb.closed) == (2, 2), "a failed read must not leak a pool slot (pool of 15)"


def test_live_tickers_tolerates_an_empty_result(monkeypatch):
    _use_db(monkeypatch, _FakeDb(rows=None))
    assert ig_shim.live_tickers() == set()


def test_forty_alerts_open_and_close_forty_connections(monkeypatch):
    """The Morning Chain raised 42 alerts; every one reads the live set once. Pin that it never leaks."""
    monkeypatch.setattr("db_pool.get_db", lambda: _FakeDb(rows=[]))      # a NEW connection per call, as the pool does
    monkeypatch.setattr(notify, "alert_system_error", lambda *a, **k: None)
    for i in range(42):
        ig_shim._epic_alert(f"T{i}.L", "x")
    assert _FakeDb.opened == _FakeDb.closed == 42


# --- the real notify.alert_system_error: email=False suppresses ONLY the email ---------------------------------

def test_real_alert_with_email_false_still_posts_slack_and_sends_no_email(monkeypatch):
    emailed, posted = [], []
    monkeypatch.setattr(notify, "_email_alert", lambda subject, text: emailed.append(subject) or True)
    monkeypatch.setattr(notify, "_send", lambda channel, blocks: posted.append(channel) or True)
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: set())
    ig_shim._epic_alert("SHAW.L", "wrong-instrument risk")
    assert emailed == [] and posted == ["alerts"]


def test_real_alert_for_a_live_ticker_emails_with_the_get_epic_subject(monkeypatch):
    emailed, posted = [], []
    monkeypatch.setattr(notify, "_email_alert", lambda subject, text: emailed.append(subject) or True)
    monkeypatch.setattr(notify, "_send", lambda channel, blocks: posted.append(channel) or True)
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: {"SHAW.L"})
    ig_shim._epic_alert("SHAW.L", "wrong-instrument risk")
    assert emailed == ["System alert: get_epic - wrong-instrument risk"] and posted == ["alerts"]


def test_an_alert_that_raises_never_breaks_get_epic(monkeypatch):
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: set())

    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(notify, "alert_system_error", boom)
    ig_shim._epic_alert("SHAW.L", "x")          # must not raise


# --- the .L normalisation ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("alert_ticker,live,expected", [
    ("SHAW.L", {"SHAW.L"}, True),
    ("SHAW.L", {"SHAW"}, True),            # order stored under the bare code
    ("SHAW.L", {"SHAWX"}, False),          # a different ticker is not a prefix match
    ("SHAW.L", {"AAPL", "BA.L"}, False),
    ("CCEP", {"CCEP.L"}, False),           # documented limit: a bare alert does not match a .L live order
    (".L", {"L", ""}, False),              # degenerate: len('.L') == 2, not stripped
])
def test_live_matching(monkeypatch, alert_ticker, live, expected):
    calls = []
    monkeypatch.setattr(notify, "alert_system_error",
                        lambda session, component, summary, detail="", email=True: calls.append(email))
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: set(live))
    ig_shim._epic_alert(alert_ticker, "x")
    assert calls == [expected]


# --- each of get_epic's four sites reaches the gate, driven through get_epic itself -----------------------------

def _drive(monkeypatch, *, ticker, y_name, markets, cached=None, close=None, live=()):
    calls = []
    monkeypatch.setattr(notify, "alert_system_error",
                        lambda session, component, summary, detail="", email=True: calls.append(
                            (summary, email)))
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: set(live))
    monkeypatch.setattr(ig_shim, "get_db", lambda: _FakeDb(rows=[], first_select=cached))
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    monkeypatch.setattr(ig_shim, "_SEARCH_TERM_ALIASES", {})
    monkeypatch.setattr(ig_shim, "_heuristic_epic", lambda t: None)
    monkeypatch.setattr(ig_shim, "_yahoo_name", lambda t: y_name)
    monkeypatch.setattr(ig_shim, "_last_close", lambda t: close)
    monkeypatch.setattr(ig_shim.session, "get",
                        lambda path, params=None, version=None: {"markets": markets})
    return calls


@pytest.mark.parametrize("live,expected", [((), False), (("FOO",), True)])
def test_site_no_name_match_goes_through_the_gate(monkeypatch, live, expected):
    calls = _drive(monkeypatch, ticker="FOO", y_name="Zzyzx Holdings plc", live=live,
                   markets=[{"epic": "UB.D.FOOUS.DAILY.IP", "instrumentName": "Foo Corp", "expiry": "DFB"}])
    assert ig_shim.get_epic("FOO") is None
    assert [e for s, e in calls if "no IG search result matches" in s] == [expected]


@pytest.mark.parametrize("live,expected", [((), False), (("FOO",), True)])
def test_site_price_mismatch_goes_through_the_gate(monkeypatch, live, expected):
    calls = _drive(monkeypatch, ticker="FOO", y_name="Foo Corp", close=100.0, live=live,
                   markets=[{"epic": "UB.D.FOOUS.DAILY.IP", "instrumentName": "Foo Corp", "expiry": "DFB",
                             "bid": 5.0, "offer": 5.1}])
    assert ig_shim.get_epic("FOO") is None
    assert [e for s, e in calls if "trades near its last close" in s] == [expected]


@pytest.mark.parametrize("live,expected", [((), True), (("FOO",), True)])
def test_site_cached_epic_mismatch_is_always_emailed(monkeypatch, live, expected):
    """Owner 2026-11-09: a wrong instrument must be found before a live order, so this site is NOT gated on a live
    order (the specialist's first version asserted False for a non-live ticker; the owner's rule reversed that)."""
    calls = _drive(monkeypatch, ticker="FOO", y_name="Foo Corp", live=live, markets=[],
                   cached=[("UB.D.BARUS.DAILY.IP", "Bar Industries")])
    assert ig_shim.get_epic("FOO") is None          # purged, then no IG market for 'FOO'
    assert [e for s, e in calls if "does not match" in s] == [expected]


def test_a_resolvable_ticker_raises_no_alert_at_all(monkeypatch):
    calls = _drive(monkeypatch, ticker="FOO", y_name="Foo Corp", live=(),
                   markets=[{"epic": "UB.D.FOOUS.DAILY.IP", "instrumentName": "Foo Corp", "expiry": "DFB"}])
    monkeypatch.setattr(ig_shim, "get_db", lambda: _FakeDb(rows=[]))
    assert ig_shim.get_epic("FOO") == "UB.D.FOOUS.DAILY.IP"
    assert calls == []
