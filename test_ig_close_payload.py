"""The body ig_shim.close_trade sends to IG to close a position.

WHY THIS FILE EXISTS. MEASURED 2026-09-25 from the live database: every close this system had ever
attempted was rejected by IG with HTTP 400 {"errorCode":"validation.mutual-exclusive-value.request"} --
all 7 rows in auto_closed_positions (8002.T, DVN, AKE.PA, ARM, INTC, HLMA.L twice, back to 2026-09-17),
zero successes, and trade_log carrying 42 closes with not one from AUTO_VOLUME_TEST_FAILED or
WEB_USER_CONFIRMED. The body identified the position BOTH ways at once -- dealId AND epic+expiry -- and
IG accepts one or the other. git blame put that dict at the initial commit 29f33b1 on 2026-06-01, so it
had never worked once.

THE COST OF NOT HAVING THIS TEST was not a failing build, it was silence: the auto-closer reported
success on every pass, and AGENTS.md already recorded "the auto-closer ran 300 green passes and closed
nothing" without anyone finding the cause. Three callers share this one function -- the auto-closer, the
session monitors, and the website's own Close Position button -- so all three were dead.

These tests assert the SHAPE of the request without sending one. The live confirmation is separate and
has to be done against a real position; it cannot be unit-tested, and a green run here must never be read
as proof that IG accepted anything.
"""

import types

import pytest

import ig_shim


def _capture_close_body(monkeypatch, direction="BUY", size=0.02):
    """Call close_trade against a fake session and return the body it tried to send."""
    sent = {}

    def _fake_delete(path, body=None, version=None):
        sent["path"], sent["body"], sent["version"] = path, dict(body or {}), version
        return {}                      # no dealReference -> close_trade returns False, sends nothing else

    monkeypatch.setattr(ig_shim, "get_position_by_deal", lambda d: {
        "market": {"epic": "EC.D.AKEFP.DAILY.IP", "expiry": "DFB"},
        "position": {"dealId": d, "direction": direction, "size": size}})
    monkeypatch.setattr(ig_shim, "session",
                        types.SimpleNamespace(delete=_fake_delete), raising=False)
    monkeypatch.setattr(ig_shim, "_set_close_outcome", lambda *a, **k: None)

    ig_shim.close_trade("DIAAAAR7WPVBJAS")
    return sent


def test_the_close_body_never_identifies_the_position_twice(monkeypatch):
    """THE DEFECT. dealId and epic+expiry are mutually exclusive; sending both is a 400 every time."""
    body = _capture_close_body(monkeypatch)["body"]
    assert body.get("dealId") == "DIAAAAR7WPVBJAS", "the dealId is how we identify the position"
    assert "epic" not in body, "epic is mutually exclusive with dealId -- this is the 2026-09-25 defect"
    assert "expiry" not in body, "expiry belongs to the epic form of the request, not the dealId form"


def test_the_close_body_still_carries_what_ig_requires(monkeypatch):
    """Removing the wrong fields must not remove the right ones."""
    body = _capture_close_body(monkeypatch)["body"]
    for required in ("dealId", "direction", "size", "orderType"):
        assert required in body, f"{required} is required to close a position"
    assert body["orderType"] == "MARKET"


@pytest.mark.parametrize("held,closing", [("BUY", "SELL"), ("SELL", "BUY")])
def test_the_close_is_the_opposite_direction_to_the_position(monkeypatch, held, closing):
    """Arkema SA, the position this was first confirmed against, is a SELL -- so its close is a BUY. Get
    this backwards and the 'close' doubles the position instead of ending it."""
    body = _capture_close_body(monkeypatch, direction=held)["body"]
    assert body["direction"] == closing


def test_the_size_is_sent_as_the_positions_own_size(monkeypatch):
    body = _capture_close_body(monkeypatch, size=0.02)["body"]
    assert body["size"] == "0.02", "closing a different size would leave a residual position open"


def test_it_goes_to_the_otc_positions_endpoint(monkeypatch):
    sent = _capture_close_body(monkeypatch)
    assert sent["path"] == "/positions/otc"
    assert sent["version"] == "1"
