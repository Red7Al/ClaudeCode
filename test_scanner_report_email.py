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
