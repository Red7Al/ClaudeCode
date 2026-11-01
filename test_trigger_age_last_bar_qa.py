"""Independent QA of a2b2976: a report trigger's age by its instrument's own bars (summary `last_bar`), the audit's
"delivered" bar, the Storage bucket/upload fixes and the bridge's IONOS source directory.

Fixtures are the real values of the live files of 2026-10-31 (price bars file built 2026-10-31T03:53:59Z, snapshot
2026-10-30T19:08:13Z), read by the tester: MET / 0016.HK newest close 2026-10-30; SAP.DE, TSCO.L and BTCUSD newest
close 2026-10-29 (their 10-30 bar was absent from that file); ANSS in the snapshot with no bars. Only tickers, dates
and closes are copied -- no scanner output. MONEY PATH: nothing here reaches IG, Supabase or IONOS.
"""

import csv
import datetime
import gzip
import json
import os
import shutil
import subprocess
import sys
import types

import pandas as pd
import pytest

import bars_file
import price_audit
import publish_report_summary as prs
import scanner_snapshot_store as store
from hvf_web import order_bridge, server
from test_bridge_report_triggers import MET_MCAP, MET_SCAN, MET_SETUP, _wire

SNAP_GEN = "2026-10-30T19:08:13.687802+00:00"
BARS_BUILT = "2026-10-31T03:53:59.366289+00:00"
LIVE_TAILS = {   # (bar_date, close) -- the newest rows of the live price bars file, 2026-10-31
    "MET": [("2026-10-29", 100.830001831055), ("2026-10-30", 100.019996643066)],
    "0016.HK": [("2026-10-29", 106.900001525879), ("2026-10-30", 108.900001525879)],
    "SAP.DE": [("2026-10-28", 211.199996948242), ("2026-10-29", 208.899993896484)],
    "TSCO.L": [("2026-10-28", 532.0), ("2026-10-29", 531.0)],
    "BTCUSD": [("2026-10-28", 81267.5859375), ("2026-10-29", 77775.109375)],
}


# -- publish_report_summary.last_bar_section ---------------------------------------------------------------

def _bars_dir(tmp_path, tails, built=BARS_BUILT, meta_extra=None):
    d = tmp_path / "bars"
    d.mkdir()
    rows = []
    for tk, tail in tails.items():
        for bd, c in tail:
            rows.append(bars_file._row_out((tk, bd, c, c, c, c, 1000, "yf", False,
                                            datetime.datetime(2026, 10, 31, 3, 50, tzinfo=datetime.timezone.utc))))
    with gzip.open(d / bars_file.DATA_NAME, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(bars_file.COLUMNS)
        w.writerows(rows)
    meta = {"built_at": built, "rows": len(rows), **(meta_extra or {})}
    (d / bars_file.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return str(d)


def _snap(tickers, gen=SNAP_GEN):
    return {"generated_utc": gen, "records": [{"ticker": t} for t in tickers]}


def test_last_bar_is_each_snapshot_instruments_newest_close_from_the_bars_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, LIVE_TAILS))
    out = prs.last_bar_section(_snap(["MET", "0016.HK", "SAP.DE", "TSCO.L", "BTCUSD", "ANSS"]))
    assert out == {"MET": "2026-10-30", "0016.HK": "2026-10-30", "SAP.DE": "2026-10-29", "TSCO.L": "2026-10-29",
                   "BTCUSD": "2026-10-29"}, "ANSS has no bars and is left out, not invented"


def test_last_bar_skips_a_newest_row_with_no_close(monkeypatch, tmp_path):
    tails = {"MET": [("2026-10-29", 100.83), ("2026-10-30", None)], "NOCLOSE": [("2026-10-30", None)]}
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, tails))
    assert prs.last_bar_section(_snap(["MET", "NOCLOSE"])) == {"MET": "2026-10-29"}


def test_last_bar_leaves_out_instruments_not_in_the_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, LIVE_TAILS))
    assert prs.last_bar_section(_snap(["MET"])) == {"MET": "2026-10-30"}


def test_no_bars_file_in_the_job_omits_the_section(monkeypatch, tmp_path):
    monkeypatch.delenv("PRICE_BARS_DIR", raising=False)
    assert prs.last_bar_section(_snap(["MET"])) is None
    monkeypatch.setenv("PRICE_BARS_DIR", str(tmp_path))           # a directory with no meta
    assert prs.last_bar_section(_snap(["MET"])) is None


@pytest.mark.parametrize("built,used", [
    ("2026-10-31T19:08:13.687802+00:00", True),    # exactly 24 h after the snapshot
    ("2026-10-31T19:08:14.687802+00:00", False),   # 24 h + 1 s after
    ("2026-10-29T19:08:13.687802+00:00", True),    # exactly 24 h before
    ("2026-10-29T19:08:12.687802+00:00", False),   # 24 h + 1 s before: a stale file
    ("2026-10-27T03:53:59+00:00", False),
])
def test_a_bars_file_not_within_24h_of_the_snapshot_omits_the_section(monkeypatch, tmp_path, built, used):
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, LIVE_TAILS, built=built))
    assert (prs.last_bar_section(_snap(["MET"])) is not None) is used


def test_a_snapshot_with_no_generated_time_omits_the_section(monkeypatch, tmp_path):
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, LIVE_TAILS))
    assert prs.last_bar_section({"records": [{"ticker": "MET"}]}) is None


def test_bars_metadata_without_a_build_time_omits_the_section_not_the_build(monkeypatch, tmp_path):
    d = _bars_dir(tmp_path, LIVE_TAILS)
    meta = json.loads(open(os.path.join(d, bars_file.META_NAME), encoding="utf-8").read())
    meta.pop("built_at")
    open(os.path.join(d, bars_file.META_NAME), "w", encoding="utf-8").write(json.dumps(meta))
    monkeypatch.setenv("PRICE_BARS_DIR", d)
    assert prs.last_bar_section(_snap(["MET"])) is None


def test_a_bars_file_that_fails_its_row_count_omits_the_section_not_the_build(monkeypatch, tmp_path):
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, LIVE_TAILS, meta_extra={"rows": 99}))
    assert prs.last_bar_section(_snap(["MET"])) is None


def test_an_unparseable_snapshot_time_omits_the_section(monkeypatch, tmp_path):
    monkeypatch.setenv("PRICE_BARS_DIR", _bars_dir(tmp_path, LIVE_TAILS))
    assert prs.last_bar_section(_snap(["MET"], gen="not-a-time")) is None


def test_the_builder_writes_the_section():
    import inspect
    assert 'sections["last_bar"] = last_bar_section(snap)' in inspect.getsource(prs.build)


# -- order_bridge._candidates: the trigger's age by its instrument's own last bar ------------------------------

def _bridge(monkeypatch, caplog, last_bar, setups, records=None, today="2026-11-02"):
    """Placed tickers of one pass; `last_bar` is the summary section (None: absent)."""
    recs = records or [MET_SCAN]
    placed = _wire(monkeypatch, recs, setups)
    monkeypatch.setattr(server, "_mcap_map", lambda: {r["ticker"]: MET_MCAP for r in recs})
    monkeypatch.setattr(server, "_utc_today", lambda: datetime.date.fromisoformat(today))
    asked = []

    def _section(name, snap=None):
        asked.append((name, (snap or {}).get("generated_utc")))
        return last_bar if name == "last_bar" else None
    monkeypatch.setattr(server, "_summary_section", _section)
    caplog.set_level("INFO", logger="order_bridge")
    summary = order_bridge.run_bridge()
    return [s["ticker"] for s, kw in placed], asked, summary


def _warnings(caplog, needle):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and needle in r.getMessage()]


def _trig(tk, trig_date, direction="BULL"):
    return dict(MET_SCAN, ticker=tk), {tk: [dict(MET_SETUP, trig_date=trig_date, direction=direction)]}


def test_the_section_is_asked_for_with_this_snapshots_generated_time(monkeypatch, caplog):
    _placed, asked, _ = _bridge(monkeypatch, caplog, {"MET": "2026-10-23"}, {"MET": [MET_SETUP]})
    assert ("last_bar", "2026-10-30T19:08:13.687802+00:00") in asked


def test_last_bar_equal_to_the_trigger_date_is_orderable(monkeypatch, caplog):
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-23"}, {"MET": [MET_SETUP]}, today="2026-10-26")
    assert placed == ["MET"]


def test_a_holiday_after_the_trigger_costs_its_order(monkeypatch, caplog):
    """c180e70, a deliberate decision: no bar on D+1 (exchange closed), but 2 weekdays have passed -- the weekday
    limit always applies, so the trigger is NOT ordered (replay: one more day late averaged -0.23R)."""
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-23"}, {"MET": [MET_SETUP]}, today="2026-10-27")
    assert placed == []
    (w,) = _warnings(caplog, "past their trigger day")
    assert "MET 2026-10-23 (2d)" in w and "(bar" not in w
    assert not _warnings(caplog, "judged by weekdays"), "a last-bar date was there: it is not a weekday-only ticker"


def test_a_gap_in_the_bars_never_keeps_a_trigger_orderable(monkeypatch, caplog):
    """QA Defect 1 (a2b2976): SAP.DE's newest close in the live bars file of 2026-10-31 was 10-29 (its 10-30 bar
    missing). A 10-29 trigger on Monday 11-02 is 2 weekdays old: not ordered, gap or no gap."""
    rec, setups = _trig("SAP.DE", "2026-10-29")
    placed, _a, _s = _bridge(monkeypatch, caplog, {"SAP.DE": "2026-10-29"}, setups, records=[rec], today="2026-11-02")
    assert placed == []


def test_a_dead_feed_never_keeps_a_trigger_orderable(monkeypatch, caplog):
    """IPF.L in the live files: an OPEN trigger of 2026-08-04 whose bars stop on 2026-08-04."""
    rec, setups = _trig("IPF.L", "2026-08-04")
    placed, _a, _s = _bridge(monkeypatch, caplog, {"IPF.L": "2026-08-04"}, setups, records=[rec], today="2026-10-30")
    assert placed == []


def test_a_trigger_failing_both_checks_is_listed_once(monkeypatch, caplog):
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-27"}, {"MET": [MET_SETUP]}, today="2026-10-27")
    assert placed == []
    (w,) = _warnings(caplog, "past their trigger day")
    assert w.count("MET 2026-10-23") == 1 and "1 report trigger(s)" in w


def test_a_bar_after_the_trigger_bar_ages_it_whatever_the_weekdays(monkeypatch, caplog):
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-26"}, {"MET": [MET_SETUP]}, today="2026-10-26")
    assert placed == []
    (w,) = _warnings(caplog, "past their trigger day")
    assert "MET 2026-10-23 (bar 2026-10-26)" in w


def test_a_last_bar_older_than_the_trigger_is_orderable(monkeypatch, caplog):
    """The trigger history can run ahead of the bars file: no bar AFTER the trigger bar means not yet aged."""
    rec, setups = _trig("MET", "2026-10-30")
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-29"}, setups, records=[rec], today="2026-10-30")
    assert placed == ["MET"]


def test_a_friday_trigger_is_orderable_on_monday_when_no_bar_followed(monkeypatch, caplog):
    rec, setups = _trig("MET", "2026-10-30")
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-30"}, setups, records=[rec], today="2026-11-02")
    assert placed == ["MET"]


def test_each_instrument_is_judged_on_its_own_exchange_bars(monkeypatch, caplog):
    """The live file of 2026-10-31: on a 10-29 trigger MET (US) and 0016.HK (Asia) have traded 10-30 since --
    aged; SAP.DE and TSCO.L (Europe) have no bar after 10-29 -- orderable."""
    tks = ["MET", "0016.HK", "SAP.DE", "TSCO.L"]
    recs = [dict(MET_SCAN, ticker=t) for t in tks]
    setups = {t: [dict(MET_SETUP, trig_date="2026-10-29")] for t in tks}
    lb = {t: LIVE_TAILS[t][-1][0] for t in tks}
    placed, _a, _s = _bridge(monkeypatch, caplog, lb, setups, records=recs, today="2026-10-30")
    assert sorted(placed) == ["SAP.DE", "TSCO.L"]
    (w,) = _warnings(caplog, "past their trigger day")
    assert "MET 2026-10-29 (bar 2026-10-30)" in w and "0016.HK 2026-10-29 (bar 2026-10-30)" in w


def test_crypto_ages_on_its_weekend_bar(monkeypatch, caplog):
    """Crypto trades every day: a Friday trigger has a Saturday bar by Monday, so it is aged, although only one
    weekday has passed."""
    rec, setups = _trig("BTCUSD", "2026-10-30")
    placed, _a, _s = _bridge(monkeypatch, caplog, {"BTCUSD": "2026-11-01"}, setups, records=[rec], today="2026-11-02")
    assert placed == []


def test_an_aged_trigger_never_falls_through_to_the_ready_path(monkeypatch, caplog):
    ready = dict(MET_SCAN, status="READY", current_price=98.6)          # near entry, over the quality floor
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-26"}, {"MET": [MET_SETUP]}, records=[ready],
                             today="2026-10-26")
    assert placed == []


def test_a_ticker_missing_from_last_bar_falls_back_to_weekdays_and_is_named(monkeypatch, caplog):
    recs = [MET_SCAN, dict(MET_SCAN, ticker="ANSS")]
    setups = {"MET": [MET_SETUP], "ANSS": [MET_SETUP]}
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-23"}, setups, records=recs, today="2026-10-26")
    assert sorted(placed) == ["ANSS", "MET"], "ANSS: 1 weekday after the trigger -- orderable by the fallback"
    (w,) = _warnings(caplog, "judged by weekdays")
    assert w.endswith(": ANSS"), w


def test_a_ticker_missing_from_last_bar_is_aged_by_weekdays(monkeypatch, caplog):
    recs = [dict(MET_SCAN, ticker="ANSS")]
    placed, _a, _s = _bridge(monkeypatch, caplog, {"MET": "2026-10-23"}, {"ANSS": [MET_SETUP]}, records=recs,
                             today="2026-10-27")
    assert placed == []
    (w,) = _warnings(caplog, "past their trigger day")
    assert "ANSS 2026-10-23 (2d)" in w


@pytest.mark.parametrize("today,ordered", [("2026-10-26", True), ("2026-10-27", False)])
def test_with_no_section_every_trigger_is_judged_by_weekdays(monkeypatch, caplog, today, ordered):
    placed, _a, _s = _bridge(monkeypatch, caplog, None, {"MET": [MET_SETUP]}, today=today)
    assert (placed == ["MET"]) is ordered
    (w,) = _warnings(caplog, "judged by weekdays")
    assert "MET" in w


def test_one_weekday_warning_line_names_every_fallback_ticker(monkeypatch, caplog):
    tks = ["MET", "ANSS", "IPF.L"]
    recs = [dict(MET_SCAN, ticker=t) for t in tks]
    _bridge(monkeypatch, caplog, None, {t: [MET_SETUP] for t in tks}, records=recs, today="2026-10-26")
    (w,) = _warnings(caplog, "judged by weekdays")
    assert "3 report trigger(s)" in w and all(t in w for t in tks)


@pytest.mark.parametrize("bad,section", [
    (b, s) for b in (None, "", 20261023, "2026-10-2", "2026-10-23T00:00:00", "23/10/2026", "2026-13-45")
    for s in ({"MET": "2026-10-23", "MET2": "2026-10-23"}, None)
])
def test_an_unreadable_trigger_date_skips_only_that_trigger(monkeypatch, caplog, bad, section):
    recs = [MET_SCAN, dict(MET_SCAN, ticker="MET2", status="READY", current_price=98.6)]
    setups = {"MET": [MET_SETUP], "MET2": [dict(MET_SETUP, trig_date=bad)]}
    placed, _a, summary = _bridge(monkeypatch, caplog, section, setups, records=recs, today="2026-10-26")
    assert placed == ["MET"] and "aborted" not in summary
    assert _warnings(caplog, "MET2 has an unreadable trigger date")


def test_an_unparseable_ten_character_date_is_skipped_by_the_weekday_fallback(monkeypatch, caplog):
    recs = [MET_SCAN, dict(MET_SCAN, ticker="MET2")]
    setups = {"MET": [MET_SETUP], "MET2": [dict(MET_SETUP, trig_date="2026-13-45")]}
    placed, _a, _s = _bridge(monkeypatch, caplog, None, setups, records=recs, today="2026-10-26")
    assert placed == ["MET"] and _warnings(caplog, "MET2 has an unreadable trigger date")


# -- price_audit: only a bar WITH a close counts as delivered -------------------------------------------------

def _df(rows, tz=None):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in rows])
    if tz:
        idx = idx.tz_localize(tz)
    return pd.DataFrame({"Open": [c for _, c in rows], "Close": [c for _, c in rows]}, index=idx)


def test_the_newest_closed_bar_skips_a_trailing_row_with_no_close():
    nan = float("nan")
    assert price_audit._newest_closed_bar(_df([("2026-10-29", 208.9), ("2026-10-30", nan)])) == datetime.date(2026, 10, 29)
    assert price_audit._newest_closed_bar(_df([("2026-10-29", 208.9), ("2026-10-30", 210.0)])) == datetime.date(2026, 10, 30)


def test_the_newest_closed_bar_is_none_for_no_closes_or_no_frame():
    nan = float("nan")
    assert price_audit._newest_closed_bar(_df([("2026-10-30", nan)])) is None
    assert price_audit._newest_closed_bar(pd.DataFrame()) is None
    assert price_audit._newest_closed_bar(None) is None


def test_the_newest_closed_bar_reads_an_exchange_local_index_and_a_multiindex_frame():
    nan = float("nan")
    local = _df([("2026-10-29", 208.9), ("2026-10-30", nan)], tz="Europe/Berlin")
    assert price_audit._newest_closed_bar(local) == datetime.date(2026, 10, 29)
    multi = _df([("2026-10-29", 208.9), ("2026-10-30", nan)])
    multi.columns = pd.MultiIndex.from_tuples([(c, "SAP.DE") for c in multi.columns])
    assert price_audit._newest_closed_bar(multi) == datetime.date(2026, 10, 29)


def _fake_yf(monkeypatch, download=None, history=None):
    yf = types.SimpleNamespace(download=lambda *a, **k: download,
                               Ticker=lambda sym: types.SimpleNamespace(history=lambda **k: history))
    monkeypatch.setitem(sys.modules, "yfinance", yf)


def test_the_first_fetch_records_the_newest_bar_with_a_close(monkeypatch):
    """SAP.DE on 2026-10-31: Yahoo's frame ends 10-30 with no close; the bar delivered is 10-29, so 10-30 is missing."""
    nan = float("nan")
    _fake_yf(monkeypatch, download=_df([("2026-10-28", 211.2), ("2026-10-29", 208.9), ("2026-10-30", nan)]))
    monkeypatch.setattr(price_audit, "_today", lambda: datetime.date(2026, 10, 31))
    monkeypatch.setattr(price_audit.price_store, "get_bars", lambda *a, **k: None)
    monkeypatch.setattr(price_audit.price_store, "upsert_bars", lambda *a, **k: 2)
    last_bar = {}
    price_audit.audit_ticker("SAP.DE", "SAP.DE", 10, "yf", db=None, last_bar=last_bar)
    assert last_bar["SAP.DE"] == (datetime.date(2026, 10, 29), datetime.date(2026, 10, 30))


def test_a_first_fetch_with_no_close_at_all_is_recorded_as_missing(monkeypatch):
    nan = float("nan")
    _fake_yf(monkeypatch, download=_df([("2026-10-30", nan)]))
    monkeypatch.setattr(price_audit, "_today", lambda: datetime.date(2026, 10, 31))
    monkeypatch.setattr(price_audit.price_store, "get_bars", lambda *a, **k: None)
    monkeypatch.setattr(price_audit.price_store, "upsert_bars", lambda *a, **k: 0)
    last_bar = {}
    price_audit.audit_ticker("BTCUSD", "BTC-USD", 10, "yf", db=None, last_bar=last_bar)
    lb, exp = last_bar["BTCUSD"]
    assert lb < exp


@pytest.mark.parametrize("close,recovered", [(float("nan"), False), (209.5, True)])
def test_the_retry_recovers_only_a_bar_with_a_close(monkeypatch, close, recovered):
    _fake_yf(monkeypatch, history=_df([("2026-10-29", 208.9), ("2026-10-30", close), ("2026-10-31", 210.0)]))
    upserted = []
    monkeypatch.setattr(price_audit.price_store, "upsert_bars", lambda tk, h, *a, **k: upserted.append(len(h)))
    got, untried = price_audit._retry_prev_bar({"SAP.DE": "SAP.DE"}, {"SAP.DE": datetime.date(2026, 10, 30)},
                                               datetime.date(2026, 10, 31), "yf", db=None)
    assert (got == ["SAP.DE"]) is recovered and untried == []
    assert upserted == [2], "today's partial bar is never stored"


# -- scanner_snapshot_store ---------------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code, self._body, self.text = status_code, body or {}, text
        self.content = json.dumps(self._body).encode() if body else b""

    def json(self):
        return self._body


KEY = "qa-placeholder-not-a-real-storage-key"


def _store(monkeypatch, responder):
    calls = []
    monkeypatch.setattr(store, "_bucket", lambda: "scanner-artifacts")
    monkeypatch.setattr(store, "_project_url", lambda: "https://example.supabase.co")
    monkeypatch.setattr(store, "_secret_key", lambda purpose: KEY)
    monkeypatch.setattr(store, "_request", lambda method, url, **k: calls.append((method, url, k)) or responder(method, url))
    return calls


def test_a_missing_bucket_is_created_private_accepting_gzip_and_json(monkeypatch):
    missing = _Resp(400, {"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"})
    calls = _store(monkeypatch, lambda m, u: missing if m == "GET" else _Resp(200))
    store.ensure_private_bucket()
    body = calls[1][2]["json"]
    assert body["public"] is False and body["allowed_mime_types"] == ["application/json", "application/gzip"]


def _snapshot():
    return {"generated_utc": "2026-10-30T19:08:13.687802+00:00", "count": 1, "records": [{"ticker": "MET"}]}


def test_a_refused_upload_carries_storages_reason_and_never_the_key(monkeypatch):
    reason = '{"statusCode":"415","error":"invalid_mime_type","message":"mime type application/gzip is not supported"}'
    calls = _store(monkeypatch, lambda m, u: _Resp(400, text=reason))
    monkeypatch.setattr(store, "ensure_private_bucket", lambda: "scanner-artifacts")
    with pytest.raises(store.SnapshotStoreError) as exc:
        store.publish_snapshot(_snapshot(), source="qa")
    msg = str(exc.value)
    assert "(400)" in msg and "invalid_mime_type" in msg and "application/gzip is not supported" in msg
    assert KEY not in msg and KEY in str(calls[0][2]["headers"]), "the key travels in the headers only"


def test_the_reason_is_cut_at_300_characters_and_an_empty_body_is_handled(monkeypatch):
    _store(monkeypatch, lambda m, u: _Resp(500, text="x" * 1000))
    monkeypatch.setattr(store, "ensure_private_bucket", lambda: "scanner-artifacts")
    with pytest.raises(store.SnapshotStoreError) as exc:
        store.publish_snapshot(_snapshot(), source="qa")
    assert str(exc.value) == "snapshot upload failed (500): " + "x" * 300
    _store(monkeypatch, lambda m, u: _Resp(503, text=None))
    with pytest.raises(store.SnapshotStoreError, match=r"\(503\): $"):
        store.publish_snapshot(_snapshot(), source="qa")


def test_an_upload_that_already_exists_is_not_an_error(monkeypatch):
    _store(monkeypatch, lambda m, u: _Resp(409, text='{"error":"Duplicate","message":"The resource already exists"}'))
    monkeypatch.setattr(store, "ensure_private_bucket", lambda: "scanner-artifacts")
    monkeypatch.setattr(store, "_record_publication", lambda meta: 1)
    assert store.publish_snapshot(_snapshot(), source="qa")["version_id"] == 1


# -- fetch_ionos_evening.sh: the source directory, run for real against a fake ssh ------------------------------

def _bash():
    for cand in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if cand and os.path.isfile(cand) and "system32" not in cand.lower():
            return cand
    return None


def _fetch(tmp_path, env_extra):
    bash = _bash()
    if not bash:
        pytest.skip("no bash")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text('#!/usr/bin/env bash\necho "${@: -1}" >> "$FAKE_SSH_LOG"\necho \'{"ok": 1}\'\n',
                              encoding="utf-8", newline="\n")
    (fake / "ssh").chmod(0o755)
    dest = tmp_path / "dest"
    dest.mkdir()
    log = tmp_path / "ssh.log"
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "fetch_ionos_evening.sh")
    pydir = os.path.dirname(sys.executable)
    env = dict(os.environ, IONOS_SSH_KEY="k", IONOS_USER="u", IONOS_HOST="h", RUNNER_TEMP=str(tmp_path),
               FAKE_SSH_LOG=str(log), **env_extra)
    if "IONOS_SRC_DIR" not in env_extra:
        env.pop("IONOS_SRC_DIR", None)
    cmd = f'export PATH="$(cygpath -u "{fake}" 2>/dev/null || echo "{fake}"):$(cygpath -u "{pydir}" 2>/dev/null || echo "{pydir}"):$PATH"; ' \
          f'bash "$(cygpath -u "{script}" 2>/dev/null || echo "{script}")" snapshot.json "{dest.as_posix()}"'
    r = subprocess.run([bash, "-c", cmd], env=env, capture_output=True, text=True)
    return r, log.read_text(encoding="utf-8") if log.exists() else "", dest


def test_the_fetch_script_reads_the_evening_folder_by_default(tmp_path):
    r, remote, dest = _fetch(tmp_path, {})
    assert r.returncode == 0, r.stderr
    assert remote.strip() == "test -f /home/www/data/evening/snapshot.json && cat /home/www/data/evening/snapshot.json"
    assert json.loads((dest / "snapshot.json").read_text(encoding="utf-8")) == {"ok": 1}


def test_the_fetch_script_reads_the_given_source_folder(tmp_path):
    r, remote, _dest = _fetch(tmp_path, {"IONOS_SRC_DIR": "/home/www/site/hvf_web"})
    assert r.returncode == 0, r.stderr
    assert remote.strip() == "test -f /home/www/site/hvf_web/snapshot.json && cat /home/www/site/hvf_web/snapshot.json"


def test_the_bridge_reads_the_sites_pair_and_the_other_reader_is_unchanged():
    import yaml
    wf = yaml.safe_load(open(".github/workflows/trading-order-bridge.yml", encoding="utf-8"))
    steps = list(wf["jobs"].values())[0]["steps"]
    by = {s.get("name"): s for s in steps}
    for name in ("Fetch the latest Scanner snapshot", "Fetch the report summary"):
        assert by[name]["env"]["IONOS_SRC_DIR"] == "${{ secrets.IONOS_DIR }}/hvf_web", name
    assert "age > 36" in by["Fetch the latest Scanner snapshot"]["run"]
    assert "2026-10-31" in wf["name"]
    # The site's pair is where the summary jobs install it.
    rs = open(".github/workflows/trading-report-summary.yml", encoding="utf-8").read()
    assert "${IONOS_DIR}/hvf_web/report_summary.json" in rs
    tp = yaml.safe_load(open(".github/workflows/trading-thread-precompute.yml", encoding="utf-8"))
    for s in list(tp["jobs"].values())[0]["steps"]:
        if "fetch_ionos_evening.sh" in str(s.get("run")):
            assert "IONOS_SRC_DIR" not in (s.get("env") or {}), "the thread precompute keeps the evening folder"
