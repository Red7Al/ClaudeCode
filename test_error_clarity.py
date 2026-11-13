"""Owner 2026-11-13: every error 100% clear. AAPL (epic cached and resolving) was reported 'no IG epic' because a failed
step left epic=None and describe_size_skip read that as 'missing'. Each test names the REAL failure."""
import ig_shim


def _size(monkeypatch, **patch):
    monkeypatch.setattr(ig_shim, "get_epic", patch.get("get_epic", lambda t: "UA.D.AAPL.DAILY.IP"))
    monkeypatch.setattr(ig_shim, "get_snapshot", patch.get("get_snapshot", lambda e: {"bid": 100, "offer": 100.1}))
    monkeypatch.setattr(ig_shim, "get_account_balance", patch.get("bal", lambda: {"balance": 1000, "available": 900}))
    return ig_shim.size_working_order("AAPL", "BUY", 100.0, 95.0, 110.0, {"risk_per_trade": 0.02}, 1.0)


def test_epic_lookup_exception_is_named_not_called_missing(monkeypatch):
    def boom(t): raise ConnectionError("pool full")
    r = _size(monkeypatch, get_epic=boom)
    assert "FAILED" in r["reason"] and "ConnectionError: pool full" in r["reason"] and "no IG epic" not in r["reason"]


def test_genuinely_missing_epic_still_says_so(monkeypatch):
    r = _size(monkeypatch, get_epic=lambda t: None)
    assert "no IG epic was found" in r["reason"]


def test_snapshot_failure_after_epic_is_named(monkeypatch):
    def boom(e): raise TimeoutError("IG price read timed out")
    r = _size(monkeypatch, get_snapshot=boom)
    assert "TimeoutError: IG price read timed out" in r["reason"] and "no IG epic" not in r["reason"]


def test_balance_failure_is_not_shown_as_too_small_account(monkeypatch):
    def boom(): raise ConnectionError("IG 403")
    r = _size(monkeypatch, bal=boom)
    assert "IG 403" in r["reason"] and "account too small" not in r["reason"]


# ---- specialist additions (2026-11-13) ----
import logging
import sys
import types

import notify


class _Sess:
    def __init__(self, mkt): self.mkt = mkt
    def get(self, path, version="1"): return self.mkt


_MKT = {"snapshot": {"bid": 100, "offer": 100.1},
        "instrument": {"marginFactor": 20},
        "dealingRules": {"minDealSize": {"value": 0.04}}}


def test_calculate_position_size_balance_failure_records_error_not_too_small(monkeypatch):
    monkeypatch.setattr(ig_shim, "session", _Sess(_MKT))
    def boom(): raise ConnectionError("IG 403")
    monkeypatch.setattr(ig_shim, "get_account_balance", boom)
    ig_shim.LAST_SIZE_ERROR.pop("AAPL", None)   # isolate: the ERROR record in LAST_SIZE_SKIP must carry the text alone
    size, _ = ig_shim.calculate_position_size("EPIC.X", 5.0, 20.0)
    assert size == 0.0
    sk = ig_shim.LAST_SIZE_SKIP.get("EPIC.X")
    assert sk[0] == "ERROR" and "could not read IG available funds" in sk[1] and "IG 403" in sk[1]
    txt = ig_shim.describe_size_skip("AAPL", "EPIC.X")
    assert "FAILED" in txt and "IG 403" in txt and "account too small" not in txt.lower()


def test_calculate_position_size_with_available_funds_does_not_read_balance(monkeypatch):
    monkeypatch.setattr(ig_shim, "session", _Sess(_MKT))
    def boom(): raise AssertionError("must not be called")
    monkeypatch.setattr(ig_shim, "get_account_balance", boom)
    size, _ = ig_shim.calculate_position_size("EPIC.Y", 5.0, 20.0, available_funds=900.0)
    assert size > 0


def test_generic_zero_size_text_does_not_guess_and_classifies_size_zero():
    ig_shim.LAST_SIZE_SKIP.pop("EPIC.Z", None)
    ig_shim.LAST_SIZE_ERROR.pop("AAPL", None)
    txt = ig_shim.describe_size_skip("AAPL", "EPIC.Z")
    assert "no failure was recorded" in txt and "wrong/404" not in txt and "garbage" not in txt
    assert notify._classify_missed_trade(txt)[0] == "SIZE_ZERO"


def test_failed_texts_are_not_silenced():
    for e in ["ConnectionError: pool full", "RuntimeError: could not read IG available funds: ValueError: x"]:
        ig_shim.LAST_SIZE_ERROR["AAPL"] = e
        try:
            txt = ig_shim.describe_size_skip("AAPL", "EPIC.Q")
        finally:
            ig_shim.LAST_SIZE_ERROR.pop("AAPL", None)
        cls = notify._classify_missed_trade(txt)[0]
        assert cls not in notify._SILENT_MISSED_TRADE_CLASSES and cls != "SIZE_ZERO"


def test_size_working_order_clears_stale_error(monkeypatch):
    ig_shim.LAST_SIZE_ERROR["AAPL"] = "stale"
    monkeypatch.setattr(ig_shim, "get_epic", lambda t: None)
    monkeypatch.setattr(ig_shim, "get_snapshot", lambda e: {})
    r = ig_shim.size_working_order("AAPL", "BUY", 100.0, 95.0, 110.0, {"risk_per_trade": 0.02}, 1.0)
    assert "stale" not in r["reason"] and "no IG epic was found" in r["reason"]


def test_yahoo_name_failure_not_cached_and_logged(monkeypatch, caplog):
    monkeypatch.delenv("HVF_WEB_TIER", raising=False)
    ig_shim._YNAME_CACHE.pop("ZZTEST", None)
    calls = {"n": 0}
    def boom(t):
        calls["n"] += 1
        raise OSError("yahoo down")
    fake = types.SimpleNamespace(Ticker=boom)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    with caplog.at_level(logging.WARNING):
        assert ig_shim._yahoo_name("ZZTEST") == ""
        assert ig_shim._yahoo_name("ZZTEST") == ""
    assert calls["n"] == 2 and "ZZTEST" not in ig_shim._YNAME_CACHE
    assert "yahoo down" in caplog.text


def test_detect_ig_scale_logs_swallowed_error(caplog):
    with caplog.at_level(logging.WARNING):
        assert ig_shim.detect_ig_scale("x", 1.0) == 1.0
    assert "detect_ig_scale failed" in caplog.text


def test_stop_amend_threshold_logs_swallowed_error(monkeypatch, caplog):
    import config_store
    def boom(*a, **k): raise RuntimeError("db down")
    monkeypatch.setattr(config_store, "get_value", boom)
    with caplog.at_level(logging.WARNING):
        assert ig_shim.stop_amend_threshold() == 0.0
    assert "stop_amend_threshold unreadable" in caplog.text and "db down" in caplog.text


def test_a_failure_is_shown_once_not_repeated_on_a_later_different_skip():
    """Specialist finding 1: LAST_SIZE_ERROR leaked into a later genuine ACCOUNT_TOO_SMALL skip on the same thread."""
    ig_shim.LAST_SIZE_ERROR["AAPL"] = "ConnectionError: pool full"
    assert "pool full" in ig_shim.describe_size_skip("AAPL", "EPIC.S")
    ig_shim.LAST_SIZE_SKIP["EPIC.S"] = ("ACCOUNT_TOO_SMALL", 483.85, 167.40, 0.04)
    try:
        txt = ig_shim.describe_size_skip("AAPL", "EPIC.S")
    finally:
        ig_shim.LAST_SIZE_SKIP.pop("EPIC.S", None)
    assert "pool full" not in txt and "account too small" in txt
