"""The website must never import yfinance/numpy on the order path (2026-10-21).

numpy is fatal on the IONOS host. "Create an order" hung inside ig_shim._yahoo_name's `import yfinance` (stack
dump taken on the host) and the page reported "Placement failed - no answer from server". After IG accepts an
order, the Slack notice (notify.fmt -> instrument_name.company_name) and the order email (investment case and
charts) would have hung the same way, reporting a failure for an order that went in. On the host the fixed path
took 1.8 s with numpy and yfinance never loaded (measured)."""
import sys

import pytest

import instrument_name
import trade_email


@pytest.fixture
def web_tier(monkeypatch):
    monkeypatch.setenv("HVF_WEB_TIER", "1")
    from hvf_web import server
    names = {"DCC.L": "DCC plc", "XYZ": "XYZ"}
    monkeypatch.setattr(server, "_record", lambda t: {"ticker": t, "name": names[t]} if t in names else {})
    def no_yahoo(*a, **k):
        raise AssertionError("the website reached yfinance")
    monkeypatch.setattr(instrument_name, "_yf_info", no_yahoo)
    monkeypatch.setitem(sys.modules, "yfinance", None)    # an import of yfinance now raises ImportError
    instrument_name._NAME_CACHE.clear()
    yield
    instrument_name._NAME_CACHE.clear()


def test_company_name_on_the_website_is_the_name_the_scan_stored(web_tier, monkeypatch):
    monkeypatch.setattr(instrument_name, "_from_epic_lookup", lambda t: "")
    assert instrument_name.company_name("DCC.L") == "DCC plc"
    assert instrument_name.company_name("XYZ") == "", "a stored name equal to the ticker is no name"


def test_the_epic_identity_check_reads_the_stored_name_on_the_website(web_tier):
    import ig_shim
    ig_shim._YNAME_CACHE.pop("DCC.L", None)
    assert ig_shim._yahoo_name("DCC.L") == "DCC plc"
    ig_shim._YNAME_CACHE.pop("DCC.L", None)


def test_the_order_email_on_the_website_is_the_levels_only(web_tier, monkeypatch):
    sent = []
    monkeypatch.setattr(trade_email, "send_simple_email",
                        lambda subject, text, html=None, recipients=None: sent.append((subject, text, recipients)) or True)
    def heavy(*a, **k):
        raise AssertionError("the website built the investment case or charts")
    monkeypatch.setattr(trade_email, "_investment_case", heavy)
    monkeypatch.setattr(trade_email, "build_charts", heavy)
    ok = trade_email.send_trade_email("DCC.L", "BUY", {}, {"level": 6000, "stop_level": 5800, "limit_level": 7000,
                                                           "otype": "STOP", "good_till": "2026-11-04"},
                                      size=0.12, session_name="WEB_MANUAL", event="Working order placed",
                                      deal_ref="DIAAAA", recipients=["owner@example.com"])
    assert ok is True and len(sent) == 1
    subject, text, rcpts = sent[0]
    assert subject == "Working order placed: DCC.L BUY @ 6000 [DIAAAA]" and rcpts == ["owner@example.com"]
    assert "Entry 6000  Stop 5800  Target 7000  Size 0.12" in text and "IG ref DIAAAA" in text


def test_the_github_jobs_still_get_the_full_email(monkeypatch):
    monkeypatch.delenv("HVF_WEB_TIER", raising=False)
    calls = []
    monkeypatch.setattr(trade_email, "_investment_case", lambda *a, **k: calls.append("case") or ("t", "<p>h</p>"))
    monkeypatch.setattr(trade_email, "build_charts", lambda *a, **k: calls.append("charts") or [])
    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setattr(trade_email, "_send_via_resend", lambda *a, **k: True)
    assert trade_email.send_trade_email("DCC.L", "BUY", {}, {"level": 1}, recipients=["a@b.c"]) is True
    assert calls == ["case", "charts"]
