"""P-25/P-26: get_epic Step 1c (epic_lookup_removed) and the migrations that create the table.
Fixture rows are live epic_lookup_removed rows read 2026-11-22 (SHOP, 7270.T)."""
import datetime as dt
import logging

import pytest

import ig_shim
import run_schema

UTC = dt.timezone.utc
SHOP_ROW = [dt.datetime(2026, 11, 8, 12, 42, 4, 230517, tzinfo=UTC),
            "P-02 2026-11-08: IG 'Shopify Inc (CA)' at 21047/21047 is not Shopify Inc. at 148.91",
            "KN.D.SHOPCN.DAILY.IP"]
OLDER_ROW = [dt.datetime(2026, 11, 7, 8, 11, 20, tzinfo=UTC), "older removal", "UA.D.OLD.DAILY.IP"]
MARKET = {"epic": "UA.D.SHOPUS.DAILY.IP", "instrumentName": "Shopify Inc", "expiry": "DFB", "instrumentType": "SHARES", "bid": 148.9, "offer": 149.0}


class _Db:
    def __init__(self, removed=(), raises=None):
        self.removed, self.raises, self.sql, self.params = list(removed), raises, [], []

    def run(self, sql, **kw):
        self.sql.append(sql)
        if "from epic_lookup_removed" in sql:
            self.params.append(kw)
            if self.raises:
                raise self.raises
            return self.removed          # the SQL itself filters cleared rows and takes the newest: fake returns what it would
        return []

    def close(self):
        pass


def _removed_sql(db):
    return " ".join([x for x in db.sql if "from epic_lookup_removed" in x][0].split()).lower()


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    monkeypatch.setattr(ig_shim, "_heuristic_epic", lambda t: None)
    monkeypatch.setattr(ig_shim, "_yahoo_name", lambda t: "Shopify Inc.")
    monkeypatch.setattr(ig_shim, "_last_close", lambda t: [148.9])
    monkeypatch.setattr(ig_shim, "EPIC_REMOVED_WOULD_REFUSE", 0)
    monkeypatch.delenv("HVF_WEB_TIER", raising=False)
    searches = []
    monkeypatch.setattr(ig_shim.session, "get",
                        lambda path, params=None, version=None: searches.append(path) or {"markets": [MARKET]})
    alerts = []
    monkeypatch.setattr(ig_shim, "_epic_alert", lambda *a, **k: alerts.append((a, k)))

    def wire(db, enforce=False):
        monkeypatch.setattr(ig_shim, "get_db", lambda: db)
        monkeypatch.setattr(ig_shim, "EPIC_REMOVED_ENFORCE", enforce)
        return searches, alerts
    return wire


def test_uncleared_row_logs_in_log_only_mode_and_still_searches(wired, caplog):
    searches, alerts = wired(_Db([SHOP_ROW]))
    with caplog.at_level(logging.WARNING):
        assert ig_shim.get_epic("SHOP") == "UA.D.SHOPUS.DAILY.IP"
    assert "instrument-master has not cleared it, so IG was not searched" in caplog.text
    assert "KN.D.SHOPCN.DAILY.IP was removed from the cache as the wrong instrument on 2026-11-08" in caplog.text
    assert ig_shim.EPIC_REMOVED_WOULD_REFUSE == 1 and searches and not alerts


def test_uncleared_row_refuses_in_enforce_mode_without_searching(wired):
    searches, alerts = wired(_Db([SHOP_ROW]), enforce=True)
    assert ig_shim.get_epic("SHOP") is None
    why = ig_shim.epic_missing_why("SHOP")
    assert "KN.D.SHOPCN.DAILY.IP was removed from the cache as the wrong instrument" in why and "IG was not searched" in why
    assert not searches and len(alerts) == 1 and not alerts[0][1].get("always")


def test_cleared_row_or_no_row_resolves(wired):
    db = _Db([])                     # the query keeps only cleared_at is null rows: a cleared row comes back as no row
    searches, _ = wired(db, enforce=True)
    assert ig_shim.get_epic("SHOP") == "UA.D.SHOPUS.DAILY.IP"
    assert searches, "IG must be searched when nothing uncleared is held"
    assert "cleared_at is null" in _removed_sql(db) and db.params[0] == {"t": "SHOP"}


def test_two_removals_one_cleared_uses_the_newest_uncleared_row(wired):
    db = _Db([SHOP_ROW])
    wired(db, enforce=True)
    assert ig_shim.get_epic("SHOP") is None
    q = _removed_sql(db)
    assert "cleared_at is null order by removed_at desc limit 1" in q


@pytest.mark.parametrize("enforce", [False, True])
def test_read_error_fails_safe_and_shows_only_the_exception_type(wired, caplog, enforce):
    secret = "relation epic_lookup_removed does not exist host=db.secret.example"
    searches, _ = wired(_Db(raises=RuntimeError(secret)), enforce=enforce)
    with caplog.at_level(logging.WARNING):
        got = ig_shim.get_epic("SHOP")
    if enforce:
        assert got is None and not searches
        why = ig_shim.epic_missing_why("SHOP")
        assert "epic_lookup_removed could not be read (RuntimeError; the log has the full error)" in why
        assert "secret.example" not in why
    else:
        assert searches and "could not be read (RuntimeError)" in caplog.text
        assert "secret.example" not in caplog.text


def test_pin_bypasses_the_guard(wired, monkeypatch):
    db = _Db([SHOP_ROW])
    wired(db, enforce=True)
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {"SHOP": "UA.D.SHOPUS.DAILY.IP"})
    assert ig_shim.get_epic("SHOP") == "UA.D.SHOPUS.DAILY.IP"
    assert not [s for s in db.sql if "epic_lookup_removed" in s]


def test_cache_hit_is_untouched(wired):
    class _Hit(_Db):
        def run(self, sql, **kw):
            if sql.startswith("select epic, description from epic_lookup"):
                return [("UA.D.SHOPUS.DAILY.IP", "Shopify Inc")]
            return super().run(sql, **kw)
    db = _Hit([SHOP_ROW])
    wired(db, enforce=True)
    assert ig_shim.get_epic("SHOP") == "UA.D.SHOPUS.DAILY.IP"
    assert not [s for s in db.sql if "epic_lookup_removed" in s]


# ---- migrations ----
def _sql(prefix):
    return [s for n, s in run_schema.MIGRATIONS if n.startswith(prefix)]


def test_migrations_create_the_table_rls_columns_and_index_idempotently():
    stmts = _sql("epic_lookup_removed")
    assert len(stmts) == 4
    names = [n for n, _ in run_schema.MIGRATIONS if n.startswith("epic_lookup_removed")]
    assert names[0].endswith("create") and names[-1].endswith("index uncleared by ticker")
    create, rls, cols, idx = [s.upper() for s in stmts]
    assert create.startswith("CREATE TABLE IF NOT EXISTS EPIC_LOOKUP_REMOVED") and "PRIMARY KEY" not in create
    assert "ENABLE ROW LEVEL SECURITY" in rls
    assert cols.count("ADD COLUMN IF NOT EXISTS") == 2 and "CLEARED_AT" in cols and "CLEARED_NOTE" in cols
    assert idx.startswith("CREATE INDEX IF NOT EXISTS EPIC_LOOKUP_REMOVED_TICKER_OPEN_IDX")
    assert "WHERE CLEARED_AT IS NULL" in idx
    for s in create, rls, cols, idx:
        assert not any(w in s for w in ("DROP ", "DELETE ", "TRUNCATE", "UPDATE "))


# ---- added by the independent test specialist 2026-11-22 ----------------------------------------------------------------
def test_the_shipped_default_is_log_only_and_the_query_is_keyed_on_the_ticker_alone(wired):
    assert ig_shim.EPIC_REMOVED_ENFORCE is False or True      # the fixture sets it per test; the real default is read below
    src = open(ig_shim.__file__, encoding="utf-8").read()
    assert "\nEPIC_REMOVED_ENFORCE = False\n" in src.replace("\r\n", "\n")
    db = _Db([])
    wired(db)
    ig_shim.get_epic("SHOP")
    assert "where ticker = :t and cleared_at is null" in _removed_sql(db) and " epic " not in _removed_sql(db).split("where")[1]


class DatabaseError(Exception):
    """pg8000's name for a server error; its text names the missing column and the host."""


@pytest.mark.parametrize("enforce", [False, True])
def test_before_the_migration_a_missing_cleared_at_column_fails_safe_with_the_type_only(wired, caplog, enforce):
    err = DatabaseError({"S": "ERROR", "C": "42703", "M": 'column "cleared_at" does not exist', "host": "db.secret.example"})
    searches, _ = wired(_Db(raises=err), enforce=enforce)
    with caplog.at_level(logging.WARNING):
        got = ig_shim.get_epic("SHOP")
    if enforce:
        assert got is None and "DatabaseError" in ig_shim.epic_missing_why("SHOP")
    else:
        assert got == "UA.D.SHOPUS.DAILY.IP" and searches                 # today's behaviour: carries on and searches
        assert "DatabaseError" in caplog.text and "cleared_at" not in caplog.text and "secret.example" not in caplog.text


@pytest.mark.xfail(strict=True, reason="DEFECT (specialist 2026-11-22): the ENFORCE read-error log line carries {e}; the order page "
                   "forwards every log line that names the ticker (getMessage), so a connect error's host/user would reach the browser. "
                   "Use the type plus exc_info=True like the _last_close handler. Latent: ENFORCE is False. Remove this mark when fixed.")
def test_enforce_read_error_log_line_carries_no_error_text(wired, caplog):
    wired(_Db(raises=RuntimeError("host=db.secret.example password=s3cret")), enforce=True)
    with caplog.at_level(logging.INFO):
        ig_shim.get_epic("SHOP")
    assert not any("secret" in r.getMessage() for r in caplog.records)
