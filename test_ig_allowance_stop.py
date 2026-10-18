"""A spent IG price-history allowance must stop the price refresh's IG cross-check, not be retried per instrument.

MEASURED 2026-10-13: until 2026-10-08 IG answered with a remaining count (164 on 10-08) and the refresh stopped
the cross-check after the 2nd instrument (IG_MIN_REMAINING), then ran ~1 s per instrument. From 2026-10-09 IG
refused instead -- HTTP 403 errorCode=error.public-api.exceeded-account-historical-data-allowance, 1,103 times in
the 2026-10-13 morning run -- and get_prices_df returned remaining=None, so the stop never fired: every
instrument paid for an epic lookup and a refused call, ~3.2 s each, and the run hit its time budget on 4 of 5
days (TRUNCATED at 1,415/1,773 on 10-13)."""

import pandas as pd
import requests

import ig_shim
import price_audit


def _refusal(code="error.public-api.exceeded-account-historical-data-allowance"):
    resp = requests.Response()
    resp.status_code = 403
    resp._content = ('{"errorCode":"%s"}' % code).encode()
    return requests.HTTPError("403 Client Error: Forbidden", response=resp)


def _raise(exc):
    def _get(*a, **k):
        raise exc
    return _get


def test_a_spent_allowance_reads_as_zero_remaining(monkeypatch):
    monkeypatch.setattr(ig_shim.session, "get", _raise(_refusal()))
    df, remaining = ig_shim.get_prices_df("KA.D.CPG.DAILY.IP", count=7)
    assert df.empty and remaining == 0


def test_any_other_failure_still_reads_as_unknown(monkeypatch):
    monkeypatch.setattr(ig_shim.session, "get", _raise(_refusal("error.price-history.io-error")))
    assert ig_shim.get_prices_df("KA.D.CPG.DAILY.IP", count=7)[1] is None
    monkeypatch.setattr(ig_shim.session, "get", _raise(requests.ConnectionError("timeout")))
    assert ig_shim.get_prices_df("KA.D.CPG.DAILY.IP", count=7)[1] is None


class _Db:
    def __init__(self):
        self.inserts = []

    def run(self, sql, **kw):
        if "insert into price_audit_log" in sql:
            self.inserts.append(kw)
        return []

    def close(self):
        pass


def test_the_refresh_stops_asking_ig_after_the_first_refusal(monkeypatch):
    calls = []
    db = _Db()
    monkeypatch.setattr(ig_shim.session, "get", lambda *a, **k: calls.append(a) or _raise(_refusal())())
    monkeypatch.setattr(ig_shim.session, "ensure_authenticated", lambda: None)   # login accepted; never real IG
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: "KA.D.X.DAILY.IP")
    monkeypatch.setattr(price_audit, "_universe", lambda: {f"T{i}.L": f"T{i}.L" for i in range(5)})
    monkeypatch.setattr(price_audit, "get_db", lambda *a, **k: db)
    monkeypatch.setattr(price_audit, "_ensure_audit_log", lambda d: None)
    monkeypatch.setattr(price_audit.price_store, "ensure_schema", lambda d: None)
    monkeypatch.setattr(price_audit.price_store, "prune_older_than", lambda: 0)
    monkeypatch.setattr(price_audit, "audit_ticker", lambda *a, **k: (1, 0, None))
    price_audit.run("daily", 30, "YF", use_ig=True, max_seconds=0)
    assert len(calls) == 1, f"IG asked {len(calls)} times for 5 instruments after refusing the first"
    assert db.inserts[-1]["tc"] == 5
