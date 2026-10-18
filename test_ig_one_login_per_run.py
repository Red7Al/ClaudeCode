"""A nightly run makes ONE IG login attempt, not one per instrument (owner 2026-10-18: "please do not have 1140
login errors in such close proximity to each other"). MEASURED: the 2026-10-17 23:01 price audit made 1,140 POST
/session attempts, all refused 401. These tests run the REAL price_audit.run and the REAL ig_shim login code;
only Yahoo, the database and the HTTP call to IG are replaced, and the assertion is the number of logins."""
import ig_shim
import price_audit
import run_data_quality_audit as dqa
from test_price_audit_prev_bar import PREV, _frame, _wire


class _Refused:
    status_code, headers = 401, {}

    def json(self):
        return {"errorCode": "error.security.invalid-details"}

    def raise_for_status(self):
        raise ig_shim.requests.HTTPError("401 Client Error: Unauthorized for url: https://api.ig.com/gateway/deal/session")


def _refuse_logins(monkeypatch):
    posts = []
    monkeypatch.setattr(ig_shim, "session", ig_shim.IGSession(api_key="k", username="u", password="p"))
    monkeypatch.setattr(ig_shim.requests, "post", lambda url, **k: posts.append(url) or _Refused())
    monkeypatch.setattr(ig_shim, "get_epic", lambda t: "KA.D.X.DAILY.IP")
    return posts


def test_the_price_audit_makes_one_login_attempt_for_the_whole_run(monkeypatch):
    tickers = [f"T{i}.L" for i in range(40)]
    _wire(monkeypatch, {t: _frame([PREV]) for t in tickers}, {t: _frame([PREV]) for t in tickers})
    posts = _refuse_logins(monkeypatch)
    price_audit.run("daily", 5, "YF", use_ig=True, max_seconds=0)
    assert len(posts) == 1, f"{len(posts)} IG login attempts for {len(tickers)} instruments"


def test_a_backfill_never_logs_in(monkeypatch):
    _wire(monkeypatch, {"A.L": _frame([PREV])}, {"A.L": _frame([PREV])})
    posts = _refuse_logins(monkeypatch)
    price_audit.run("backfill", 5, "YF", max_seconds=0)
    assert posts == []


def test_the_data_quality_batch_makes_one_login_attempt(monkeypatch):
    posts = _refuse_logins(monkeypatch)
    audited = []
    monkeypatch.setattr(dqa.sys, "argv", ["run_data_quality_audit.py"] + [f"T{i}.L" for i in range(25)])
    monkeypatch.setattr(dqa, "_audit_ticker", lambda t: audited.append(t) or {})
    stop = []
    monkeypatch.setattr(dqa, "_identity_sweep_all_cached", lambda: stop.append("phase 2") or [])
    monkeypatch.setattr(dqa, "_absurd_outcomes", lambda *a, **k: (_ for _ in ()).throw(StopIteration("phase 3")))
    try:
        dqa.main()
    except StopIteration:                     # phase 3 onward is not under test; the logins are
        pass
    assert len(posts) == 1 and audited == [] and stop == ["phase 2"]


def test_an_empty_batch_makes_no_login(monkeypatch):
    posts = _refuse_logins(monkeypatch)
    monkeypatch.setattr(dqa.sys, "argv", ["run_data_quality_audit.py", "0"])
    monkeypatch.setattr(dqa, "_pick_batch", lambda cap: [])
    monkeypatch.setattr(dqa, "_identity_sweep_all_cached", lambda: [])
    monkeypatch.setattr(dqa, "_absurd_outcomes", lambda *a, **k: (_ for _ in ()).throw(StopIteration("phase 3")))
    try:
        dqa.main()
    except StopIteration:
        pass
    assert posts == []


def test_a_refused_login_is_written_into_the_run_notes(monkeypatch):
    tickers = [f"T{i}.L" for i in range(3)]
    db, _ups = _wire(monkeypatch, {t: _frame([PREV]) for t in tickers}, {t: _frame([PREV]) for t in tickers})
    _refuse_logins(monkeypatch)
    price_audit.run("daily", 5, "YF", use_ig=True, max_seconds=0)
    assert "IG LOGIN REFUSED" in db.inserts[-1]["no"]          # price_audit.py binds notes as :no


def test_an_accepted_login_still_cross_checks_every_instrument(monkeypatch):
    """The other side: a login IG accepts leaves the per-instrument IG check running."""
    tickers = [f"T{i}.L" for i in range(4)]
    _wire(monkeypatch, {t: _frame([PREV]) for t in tickers}, {t: _frame([PREV]) for t in tickers})
    logins, verified = [], []
    monkeypatch.setattr(ig_shim.session, "ensure_authenticated", lambda: logins.append(1))
    monkeypatch.setattr(price_audit, "_ig_verify", lambda tk, db: verified.append(tk) or (0, 0, 9000))
    price_audit.run("daily", 5, "YF", use_ig=True, max_seconds=0)
    assert logins == [1] and verified == tickers
