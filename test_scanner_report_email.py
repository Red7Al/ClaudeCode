"""Tests for run_scanner_report_email.py (ChangeRequest P-07, 2026-08-07: daily Scanner Report email)."""

import run_scanner_report_email as rse


def _row(**kw):
    base = {"ticker": "TEST", "name": "Test Co", "direction": "BULL", "market": "US",
            "has_signal": True, "quality": 60, "rr": 3.5, "rvol": 1.8, "status": "TRIGGERED"}
    base.update(kw)
    return base


def test_email_body_lists_setups_and_reflects_saved_filters_note():
    subject, text, html = rse.email_body("Silver", [_row(ticker="ABC")], "2026-08-07T05:30:00Z")

    assert "2026-08-07" in subject
    assert "1 setup" in subject
    assert "ABC" in text and "ABC" in html
    assert "rows your Scanner Report shows" in text
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
    assert "No setups are in your Scanner Report today" in text
    assert "No setups are in your Scanner Report today" in html


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
    monkeypatch.setattr(rse, "screen_rows", lambda name: (snap["records"], False))
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
    monkeypatch.setattr(_sre, "screen_rows", lambda name: ([], False))
    monkeypatch.setattr(_sre, "email_body", lambda name, rows, gen, unavailable=False: ("s", "t", "<p>h</p>"))

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


# ------------------------------------------------------------------------------------------------------
# The email sends the rows the login's Scanner Report shows (owner 2026-10-09, P-01 "SCANNER REPORT EMAIL
# STILL USES THE OLD RULE"). Measured 2026-10-11 on the live snapshot of 2026-10-10 19:58 for the owner:
# old rule 18 rows, the rendered live page 29, the new email 29 -- same tickers, same order.
# ------------------------------------------------------------------------------------------------------
import shutil

import pytest

_node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _rec(ticker, **kw):
    base = {"ticker": ticker, "name": ticker, "has_signal": True, "status": "TRIGGERED", "report_ok": True,
            "direction": "BULL", "market": "US", "location": "US", "quality": 60, "rr": 3.0, "mcap": 5e9}
    base.update(kw)
    return base


@_node_only
def test_screen_rows_runs_the_pages_own_filter_over_the_logins_payloads(monkeypatch):
    from hvf_web import server, web_users as wu
    seen = []
    monkeypatch.setattr(wu, "token_for", lambda n: "tok-" + n)
    recs = {"records": [
        _rec("OLD", trig_date="2026-09-01"),
        _rec("NEW", trig_date="2026-10-09"),
        _rec("NOTDATE"),                                          # no trigger date: blank days-since sorts last
        _rec("OFFMKT", market="NSE", trig_date="2026-10-09"),     # market switched off by this login
        _rec("FAILED", report_ok=False, trig_date="2026-10-09"),  # failed his limits on its trigger date
        _rec("LOWQ", quality=10, trig_date="2026-10-09"),         # below his min_quality
        _rec("FORMING", status="DEVELOPING", report_ok=None),     # not TRIGGERED: the default Status view hides it
        _rec("NOSIG", has_signal=False, trig_date="2026-10-09")], # Squeeze only
        "report_unavailable": False}
    cfg = {"limits": {"min_quality": 50}, "markets_off": ["NSE"], "markets_disabled": [], "trade": {}, "filters": {}}
    monkeypatch.setattr(server, "api_records",
                        lambda: seen.append(server.request.headers.get("X-Auth")) or server.jsonify(recs))
    monkeypatch.setattr(server, "api_config", lambda: server.jsonify(cfg))
    rows, unavailable = rse.screen_rows("Alex")
    assert [r["ticker"] for r in rows] == ["NEW", "OLD", "NOTDATE"]
    assert seen == ["tok-Alex"] and unavailable is False


@_node_only
def test_screen_rows_honours_a_saved_status_choice(monkeypatch):
    from hvf_web import server, web_users as wu
    monkeypatch.setattr(wu, "token_for", lambda n: "tok")
    recs = {"records": [_rec("T", trig_date="2026-10-09"), _rec("D", status="DEVELOPING", report_ok=None)]}
    cfg = {"limits": {}, "markets_off": [], "markets_disabled": [], "trade": {}, "filters": {"mf_status": "DEVELOPING"}}
    monkeypatch.setattr(server, "api_records", lambda: server.jsonify(recs))
    monkeypatch.setattr(server, "api_config", lambda: server.jsonify(cfg))
    assert [r["ticker"] for r in rse.screen_rows("Alex")[0]] == ["D"]


def test_email_has_a_triggered_column_sorted_newest_first(monkeypatch):
    """Owner 2026-10-12: "need trigger date column and sort by this descending". Same-day rows keep the
    screen's order (stable); a row with no trigger date goes last."""
    rows = [_row(ticker="AUG", trig_date="2026-08-20"), _row(ticker="NONE"),
            _row(ticker="OCT_A", trig_date="2026-10-08"), _row(ticker="OCT_B", trig_date="2026-10-08T00:00:00"),
            _row(ticker="SEP", trig_date="2026-09-15")]
    subject, text, html = rse.email_body("Alex", rows, "2026-10-12T05:30:00Z")
    for body in (text, html):
        idx = [body.index(t) for t in ("OCT_A", "OCT_B", "SEP", "AUG", "NONE")]
        assert idx == sorted(idx), body
    assert "Triggered" in text and "Triggered &darr;</th>" in html
    assert "2026-10-08" in html and "2026-10-08T00" not in html, "the date, not the timestamp"


def test_email_keeps_the_screens_order_and_says_when_triggered_rows_are_hidden():
    rows = [_row(ticker="FIRST", quality=10), _row(ticker="SECOND", quality=90)]
    subject, text, html = rse.email_body("Alex", rows, "2026-10-11T05:30:00Z", True)
    assert text.index("FIRST") < text.index("SECOND") and html.index("FIRST") < html.index("SECOND")
    assert "Triggered setups are hidden today" in text and "Triggered setups are hidden today" in html
    assert "hidden today" not in rse.email_body("Alex", rows, "2026-10-11T05:30:00Z")[1]


def test_a_login_whose_rows_cannot_be_built_is_a_failed_send_not_a_crash(monkeypatch):
    from hvf_web import server, web_users as wu
    import trade_email, web_store
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"records": [{"ticker": "X"}], "generated_utc": "G"})
    monkeypatch.setattr(wu, "list_users", lambda: [{"name": "A", "email": "a@example.com", "enabled": True},
                                                   {"name": "B", "email": "b@example.com", "enabled": True}])
    monkeypatch.setattr(wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(web_store, "append_batch", lambda *a, **k: None)
    monkeypatch.delenv("SCANNER_EMAIL_ONLY", raising=False)

    def _rows(name):
        if name == "A":
            raise RuntimeError("boom")
        return [], False
    monkeypatch.setattr(rse, "screen_rows", _rows)
    sent = []
    monkeypatch.setattr(trade_email, "send_simple_email", lambda s, t, html=None, recipients=None: sent.append(recipients) or True)
    assert rse.main() == 1 and sent == [["b@example.com"]]


def test_email_has_no_status_column_every_row_is_triggered():
    """Owner 2026-10-13: "status can be be removed from table ... as they are all triggered" (measured that
    day: 22 of 22 report rows TRIGGERED). Header and cells must stay aligned: 7 columns each."""
    rows = [_row(ticker="AAA", trig_date="2026-10-08", status="TRIGGERED")]
    subject, text, html = rse.email_body("Alex", rows, "2026-10-13T05:30:00Z")
    assert "Status" not in text and ">Status<" not in html and "TRIGGERED" not in text and "TRIGGERED" not in html
    assert html.count("<th ") == 7 and html.count("<td ") == 7
