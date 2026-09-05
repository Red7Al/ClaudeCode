"""Tests for run_scanner_report_email.py (ChangeRequest P-07, 2026-08-07: daily Scanner Report email)."""

import run_scanner_report_email as rse


def _row(**kw):
    base = {"ticker": "TEST", "name": "Test Co", "direction": "BULL", "market": "US",
            "has_signal": True, "quality": 60, "rr": 3.5, "rvol": 1.8, "status": "TRIGGERED"}
    base.update(kw)
    return base


def test_user_rows_excludes_rows_without_a_signal():
    rows = [_row(ticker="A", has_signal=True), _row(ticker="B", has_signal=False)]

    out = rse.user_rows(rows, {})

    assert [r["ticker"] for r in out] == ["A"]


def test_user_rows_applies_saved_quality_rr_rvol_floors():
    rows = [_row(ticker="LOW", quality=40, rr=2.0, rvol=0.5),
            _row(ticker="HIGH", quality=80, rr=4.0, rvol=2.5)]

    out = rse.user_rows(rows, {"f_qmin": 50, "f_rrmin": 3, "f_rvmin": 1})

    assert [r["ticker"] for r in out] == ["HIGH"]


def test_user_rows_treats_missing_floor_values_as_no_restriction():
    rows = [_row(ticker="A", quality=None, rr=None, rvol=None)]

    out = rse.user_rows(rows, {"f_qmin": "", "f_rrmin": None})

    assert [r["ticker"] for r in out] == ["A"]


def test_user_rows_excludes_unscored_rows_when_a_floor_is_set():
    rows = [_row(ticker="UNSCORED", quality=None)]

    out = rse.user_rows(rows, {"f_qmin": 50})

    assert out == []


def test_user_rows_hard_filters_by_my_trading_filters(monkeypatch):
    """User instruction (2026-08-11): "The scanner report MUST also match the user trading filter
    settings" -> Hard filter. A row passing the display-only 'Scanner Report filter defaults' must still
    be excluded if it fails the user's saved My Trading Filters (trading_limits.check_limits)."""
    import trading_limits
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "require_above_vwap": 1})
    rows = [_row(ticker="BELOW_VWAP", above_vwap=False), _row(ticker="ABOVE_VWAP", above_vwap=True)]

    out = rse.user_rows(rows, {}, "Alex")

    assert [r["ticker"] for r in out] == ["ABOVE_VWAP"]


def test_user_rows_unknown_vwap_atr_fails_open_like_the_order_placement_gate(monkeypatch):
    """Consistent with trading_limits.check_limits everywhere else it's used: a row with no VWAP/ATR data
    (e.g. a READY/DEVELOPING setup _live_vwap_atr couldn't fetch bars for) is NOT excluded just because
    it's unproven — matches the fail-open behaviour of the automated order-placement path."""
    import trading_limits
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "require_above_vwap": 1,
                                       "require_atr_expanding": 1})
    rows = [_row(ticker="UNKNOWN", above_vwap=None, atr_expanding=None)]

    out = rse.user_rows(rows, {}, "Alex")

    assert [r["ticker"] for r in out] == ["UNKNOWN"]


def test_user_rows_no_username_falls_back_to_code_defaults(monkeypatch):
    """user_rows() must stay usable without a real login (e.g. ad-hoc testing) — trading_limits.user_limits
    already returns code defaults for an empty/None name, so this should behave the same as before this
    fix for any row that already passed the Scanner Report filter defaults."""
    out = rse.user_rows([_row(ticker="ABC")], {})
    assert [r["ticker"] for r in out] == ["ABC"]


def test_email_body_lists_setups_and_reflects_saved_filters_note():
    subject, text, html = rse.email_body("Silver", [_row(ticker="ABC")], "2026-08-07T05:30:00Z")

    assert "2026-08-07" in subject
    assert "1 setup" in subject
    assert "ABC" in text and "ABC" in html
    assert "Scanner Report filter defaults" in text
    assert "Silver" in text
    # Every <td style="...."> must close cleanly right before the '>' — regression check for a
    # string-concat bug where extra CSS (";color:...;font-weight:600") landed AFTER the closing quote
    # (e.g. style="...#eee";color:...), which is invalid HTML and silently dropped the colour/alignment.
    import re
    assert html.count("<td") == len(re.findall(r'<td style="[^"]*">', html))
    assert '";' not in html   # the exact signature of a quote closed early with more CSS trailing it


def test_email_body_handles_no_matching_setups():
    subject, text, html = rse.email_body("Guest", [], "2026-08-07T05:30:00Z")

    assert "0 setups" in subject
    assert "No setups matched" in text
    assert "No setups matched" in html


def test_email_body_caps_table_and_notes_the_remainder():
    rows = [_row(ticker=f"T{i}", quality=100 - i) for i in range(rse.MAX_ROWS + 5)]

    subject, text, html = rse.email_body("Gold", rows, "2026-08-07T05:30:00Z")

    assert "5 more" in text
    assert "5 more" in html


def test_main_sends_one_email_per_enabled_user_with_an_email(monkeypatch):
    import hvf_web.server as server
    from hvf_web import web_users as wu

    snap = {"generated_utc": "2026-08-07T05:30:00Z",
            "records": [{"ticker": "ABC", "market": "US", "direction": "BULL",
                        "has_signal": True, "quality": 70, "rr": 4.0, "status": "TRIGGERED"}]}
    monkeypatch.setattr(server, "_load_snapshot", lambda: snap)
    monkeypatch.setattr(server, "_snapshot_rvol", lambda s: {"ABC": 2.0})
    monkeypatch.setattr(server, "_snapshot_volscore", lambda s: {})
    monkeypatch.setattr(server, "_live_vwap_atr", lambda s: {})   # no DB in this test — see its own docstring
    monkeypatch.setattr(wu, "list_users", lambda: [
        {"name": "Silver", "email": "silver@example.com", "enabled": True},
        {"name": "NoEmail", "email": "", "enabled": True},
        {"name": "Disabled", "email": "disabled@example.com", "enabled": False},
    ])
    monkeypatch.setattr(wu, "get_settings", lambda name: {"filters": {}})
    events = []
    monkeypatch.setattr(wu, "log_event", lambda name, event: events.append((name, event)))
    sent = []
    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email",
                        lambda subject, text, html=None, recipients=None: sent.append(recipients) or True)
    monkeypatch.setattr("web_store.append_batch", lambda *a, **k: None, raising=False)

    rse.main()

    assert sent == [["silver@example.com"]]
    assert events == [("Silver", "Scanner report emailed (1 setups)")]


# ------------------------------------------------------------------------------------------------------
# Recipient restriction (user 2026-08-23: "just for eahind@yahoo.co.uk for now").
#
# This report was written on 2026-08-07 and nothing ever invoked it, so it had never executed once. The
# first live send is therefore also the first time anyone sees its output, and going straight to every
# enabled account holder would make that debut public. SCANNER_EMAIL_ONLY narrows delivery; empty means
# everyone, which is the intended end state.
# ------------------------------------------------------------------------------------------------------

import run_scanner_report_email as _sre


def test_empty_means_everyone(monkeypatch):
    """The restriction must be opt-IN, or clearing it would silently stop all delivery."""
    monkeypatch.delenv("SCANNER_EMAIL_ONLY", raising=False)
    assert _sre._only_recipients() == set()

    monkeypatch.setenv("SCANNER_EMAIL_ONLY", "   ")
    assert _sre._only_recipients() == set()


def test_addresses_are_parsed_and_case_folded(monkeypatch):
    monkeypatch.setenv("SCANNER_EMAIL_ONLY", " Eahind@Yahoo.co.uk , second@example.com ;third@example.com ")

    assert _sre._only_recipients() == {"eahind@yahoo.co.uk", "second@example.com", "third@example.com"}


def test_a_restricted_run_delivers_only_to_the_named_address(monkeypatch):
    """THE GUARD. Everyone else must be skipped, not emailed and not counted as a failure."""
    sent = []
    users = [{"name": "Alex", "email": "eahind@yahoo.co.uk", "enabled": True},
             {"name": "Sam", "email": "sam@example.com", "enabled": True},
             {"name": "Casey", "email": "CASEY@example.com", "enabled": True}]

    # PATCH THE REAL MODULE'S FUNCTIONS, not sys.modules. main() does `from hvf_web import web_users`,
    # which reads the ATTRIBUTE on the already-imported package -- setting sys.modules["hvf_web.web_users"]
    # does not change that, so this test used to run against the LIVE user store. It still passed, because
    # the restriction logic gives the same answer either way, while writing to the production activity log
    # on every run: 13 rows between 2026-09-04 and 2026-09-05, one per suite run, each recorded as the
    # account owner having been emailed. A test that reaches production is not isolated no matter how
    # green it is.
    from hvf_web import web_users as _real_wu
    monkeypatch.setattr(_real_wu, "list_users", lambda: users)
    monkeypatch.setattr(_real_wu, "get_settings", lambda name: {"filters": {}})
    monkeypatch.setattr(_real_wu, "log_event", lambda *a, **k: None)
    # main() also appends to the shared batch log. Block it, or the suite keeps writing there too.
    import web_store
    monkeypatch.setattr(web_store, "append_batch", lambda *a, **k: None)
    monkeypatch.setattr(_sre, "build_rows", lambda snap: [])
    monkeypatch.setattr(_sre, "user_rows", lambda rows, filters, name: [])
    monkeypatch.setattr(_sre, "email_body", lambda name, rows, gen: ("s", "t", "<p>h</p>"))

    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email",
                        lambda subject, text, html=None, recipients=None: sent.append(recipients[0]) or True)

    from hvf_web import server
    monkeypatch.setattr(server, "_load_snapshot",
                        lambda: {"records": [{"ticker": "X"}], "generated_utc": "2026-08-23T06:00:00Z"})
    monkeypatch.setenv("SCANNER_EMAIL_ONLY", "eahind@yahoo.co.uk")

    _sre.main()

    assert sent == ["eahind@yahoo.co.uk"], f"delivery escaped the restriction: {sent}"


def test_the_workflow_defaults_to_the_restricted_address():
    """A workflow defaulting to everyone would make one careless dispatch a public send."""
    from pathlib import Path
    import yaml
    wf = yaml.safe_load((Path(__file__).parent / ".github" / "workflows"
                         / "trading-scanner-report-email.yml").read_text(encoding="utf-8"))

    assert wf[True]["workflow_dispatch"]["inputs"]["only"]["default"] == "eahind@yahoo.co.uk"
    steps = wf["jobs"]["email"]["steps"]
    assert any("snapshot" in (s.get("name") or "").lower() for s in steps), (
        "a runner has no snapshot and Storage is 402; without seeding it the run silently sends nothing")


def test_resend_failure_falls_back_to_yahoo(monkeypatch):
    """THE BUG. Both senders RETURNED Resend's result, so a Resend error skipped a working Yahoo path.

    config.EMAIL_FROM is a yahoo.co.uk address and Resend rejects unverified sending domains with a 403,
    so with RESEND_API_KEY set EVERY email through here failed -- trade confirmations and bounce alerts
    included -- while both docstrings promised a fallback. Found when the Scanner Report's first ever send
    failed on 2026-08-23.
    """
    import trade_email
    calls = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("YAHOO_USER", "u")
    monkeypatch.setenv("YAHOO_APP_PASSWORD", "p")
    monkeypatch.setattr(trade_email, "_send_via_resend",
                        lambda *a, **k: calls.append("resend") or False)   # 403, unverified domain
    monkeypatch.setattr(trade_email, "_send_via_yahoo", lambda *a, **k: calls.append("yahoo") or True)

    assert trade_email.send_simple_email("s", "t", recipients=["x@example.com"]) is True
    assert calls == ["resend", "yahoo"], f"no fallback occurred: {calls}"


def test_a_working_resend_still_short_circuits(monkeypatch):
    """The fallback must not double-send when Resend succeeds."""
    import trade_email
    calls = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("YAHOO_USER", "u")
    monkeypatch.setenv("YAHOO_APP_PASSWORD", "p")
    monkeypatch.setattr(trade_email, "_send_via_resend", lambda *a, **k: calls.append("resend") or True)
    monkeypatch.setattr(trade_email, "_send_via_yahoo", lambda *a, **k: calls.append("yahoo") or True)

    assert trade_email.send_simple_email("s", "t", recipients=["x@example.com"]) is True
    assert calls == ["resend"], "Yahoo was called even though Resend succeeded"


def test_the_trade_email_sender_falls_back_too(monkeypatch):
    """send_trade_email carried the identical defect and must be fixed identically."""
    import trade_email
    calls = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("YAHOO_USER", "u")
    monkeypatch.setenv("YAHOO_APP_PASSWORD", "p")
    monkeypatch.setattr(trade_email, "_send_via_resend", lambda *a, **k: calls.append("resend") or False)
    monkeypatch.setattr(trade_email, "_send_via_yahoo", lambda *a, **k: calls.append("yahoo") or True)

    trade_email.send_trade_email("IWG.L", "BULLISH", {}, {"entry": 1, "stop": 0.9, "target": 1.2},
                                 recipients=["x@example.com"])

    assert calls == ["resend", "yahoo"], f"no fallback in send_trade_email: {calls}"


def test_resend_uses_its_test_sender_unless_a_verified_domain_is_configured(monkeypatch):
    """THE 403. _from_addr() preferred config.EMAIL_FROM (a yahoo.co.uk SMTP identity), so Resend's own
    safe default was never reached and every send was rejected as an unverified domain.

    GitHub runners block outbound SMTP, which is why Resend exists here at all -- so a Resend rejection is
    not recoverable by falling back, and the sender has to be right.
    """
    import trade_email
    captured = {}
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.setenv("EMAIL_FROM", "eahind@yahoo.co.uk")

    class _Resp:
        status_code = 200
        text = "{}"

    import requests
    monkeypatch.setattr(requests, "post",
                        lambda url, **kw: captured.update(kw.get("json") or {}) or _Resp())
    trade_email._send_via_resend("s", "t", "<p>h</p>", [], ["x@example.com"])

    assert captured["from"] == "onboarding@resend.dev", (
        "an unverifiable EMAIL_FROM must not reach Resend; it returns 403 and nothing is delivered")


def test_resend_from_overrides_once_a_domain_is_verified(monkeypatch):
    import trade_email
    captured = {}
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_FROM", "alerts@squeezescanner.cloud")

    class _Resp:
        status_code = 200
        text = "{}"

    import requests
    monkeypatch.setattr(requests, "post",
                        lambda url, **kw: captured.update(kw.get("json") or {}) or _Resp())
    trade_email._send_via_resend("s", "t", "<p>h</p>", [], ["x@example.com"])

    assert captured["from"] == "alerts@squeezescanner.cloud"
