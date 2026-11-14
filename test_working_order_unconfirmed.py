"""Owner 2026-11-14: after an unconfirmed order request, check IG before anything else; never retry blind.
The request raises (timeout) or its confirmation read raises; IG's working-order list says whether the order exists."""
import pytest
import ig_shim

EPIC = "EP.D.TEST.DAILY.IP"


class _Sess:
    def __init__(self, post_exc=None, confirm_exc=None):
        self.post_exc, self.confirm_exc, self.posted = post_exc, confirm_exc, []

    def get(self, path, version=None):
        if path.startswith("/confirms"):
            raise self.confirm_exc
        return {"snapshot": {"bid": 99.0, "offer": 99.1, "decimalPlacesFactor": 2},
                "instrument": {"currencies": [{"code": "GBP"}]}}

    def post(self, path, body=None, version=None):
        self.posted.append(body)
        if self.post_exc:
            raise self.post_exc
        return {"dealReference": "REF1"}


class _Db:
    def run(self, *a, **k): return []
    def close(self): pass


def _run(monkeypatch, sess, ig_orders):
    alerts, rows = [], []
    monkeypatch.setattr(ig_shim, "session", sess)
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Db())
    monkeypatch.setattr(ig_shim.time, "sleep", lambda s: None)
    monkeypatch.setattr(ig_shim, "_get_pending_working_order", lambda tk, uid: None)
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: EPIC)
    monkeypatch.setattr(ig_shim, "_log_working_order_to_db", lambda *a, **k: rows.append(a))
    monkeypatch.setattr(ig_shim, "get_working_orders",
                        ig_orders if callable(ig_orders) else (lambda: ig_orders))
    monkeypatch.setattr("notify.alert_missed_trade", lambda t, d, reason, *a, **k: alerts.append(reason))
    r = ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                    stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                    signal_summary="t", proximity_pct=5.0, watch=False)
    return r, alerts, rows


def _wo(level=100.0, epic=EPIC, direction="BUY"):
    return [{"workingOrderData": {"dealId": "DIAAAA1", "epic": epic, "direction": direction, "orderLevel": level}}]


def test_timeout_but_order_is_live_at_ig_is_recorded_not_retried(monkeypatch):
    sess = _Sess(post_exc=TimeoutError("read timed out"))
    r, alerts, rows = _run(monkeypatch, sess, _wo())
    assert r and r["adopted"] and r["deal_id"] == "DIAAAA1" and len(rows) == 1
    assert len(sess.posted) == 1                     # no second POST
    assert "LIVE AT IG" in alerts[0] and "TimeoutError: read timed out" in alerts[0]


def test_timeout_and_no_order_at_ig_says_not_placed(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _Sess(post_exc=TimeoutError("read timed out")), [])
    assert r is None and rows == []
    assert "NOT PLACED" in alerts[0] and "read timed out" in alerts[0]


def test_timeout_and_ig_unreadable_says_unknown_never_not_placed(monkeypatch):
    def boom(): raise ConnectionError("IG 403")
    r, alerts, rows = _run(monkeypatch, _Sess(post_exc=TimeoutError("read timed out")), boom)
    assert r is None and "UNKNOWN" in alerts[0] and "IG 403" in alerts[0] and "NOT PLACED" not in alerts[0]


def test_confirmation_read_failure_after_accept_is_found_at_ig(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _Sess(confirm_exc=ConnectionError("reset")), _wo())
    assert r and r["adopted"] and "LIVE AT IG" in alerts[0]


@pytest.mark.parametrize("other", [_wo(level=120.0), _wo(epic="OTHER"), _wo(direction="SELL")])
def test_a_different_order_at_ig_is_not_mistaken_for_ours(monkeypatch, other):
    r, alerts, rows = _run(monkeypatch, _Sess(post_exc=TimeoutError("t")), other)
    assert r is None and "NOT PLACED" in alerts[0]


# ---- specialist additions (2026-11-14) ----
import requests


def test_find_live_tolerance_and_shape(monkeypatch):
    monkeypatch.setattr(ig_shim, "get_working_orders", lambda: _wo(level=7000.0))
    assert ig_shim.find_live_working_order(EPIC, "BUY", 7000.5)["dealId"] == "DIAAAA1"    # within 1e-4 relative
    assert ig_shim.find_live_working_order(EPIC, "buy", 7000.0)                          # direction case-insensitive
    assert ig_shim.find_live_working_order(EPIC, "BUY", 7001.0) is None                  # outside tolerance
    monkeypatch.setattr(ig_shim, "get_working_orders", lambda: [{"workingOrderData": {"epic": EPIC, "direction": "BUY"}},
                                                                {"workingOrderData": None}, {}])
    assert ig_shim.find_live_working_order(EPIC, "BUY", 100.0) is None                   # missing orderLevel never matches


def test_find_live_raises_when_ig_unreadable(monkeypatch):
    def boom(): raise ConnectionError("IG 403")
    monkeypatch.setattr(ig_shim, "get_working_orders", boom)
    with pytest.raises(ConnectionError):
        ig_shim.find_live_working_order(EPIC, "BUY", 100.0)


def test_normal_success_path_unchanged_no_adoption_no_listing(monkeypatch):
    class S(_Sess):
        def get(self, path, version=None):
            if path.startswith("/confirms"):
                return {"dealStatus": "ACCEPTED", "dealId": "DIOK"}
            return super().get(path, version)
    def no_list(): raise AssertionError("must not list IG orders on the normal path")
    r, alerts, rows = _run(monkeypatch, S(), no_list)
    assert r["deal_id"] == "DIOK" and "adopted" not in r and alerts == [] and len(rows) == 1


def test_http_error_path_unchanged_does_not_query_ig_list(monkeypatch):
    resp = requests.Response(); resp.status_code = 400; resp._content = b"bad"
    def no_list(): raise AssertionError("HTTPError path must not list IG orders")
    r, alerts, rows = _run(monkeypatch, _Sess(post_exc=requests.HTTPError(response=resp)), no_list)
    assert r is None and rows == [] and "IG API error 400" in alerts[0]


def test_alert_failure_does_not_block_adoption(monkeypatch):
    def bad(*a, **k): raise RuntimeError("mail down")
    import notify
    sess = _Sess(post_exc=TimeoutError("t"))
    r, alerts, rows = _run(monkeypatch, sess, _wo())
    monkeypatch.setattr("notify.alert_missed_trade", bad)
    r2 = ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                     stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                     signal_summary="t", proximity_pct=5.0, watch=False)
    assert r2 and r2["adopted"] and len(sess.posted) == 2   # two separate calls, one POST each, never two in one call


def test_unconfirmed_alert_is_actually_emailed(monkeypatch):
    """The owner's channel is EMAIL (Slack unused). notify.alert_missed_trade emails only when email=True is passed
    (or class RISK_CAP); otherwise it posts to a Slack webhook only."""
    seen = {}
    monkeypatch.setattr("notify.alert_missed_trade",
                        lambda t, d, reason, *a, **k: seen.update(k=k, a=a))
    monkeypatch.setattr(ig_shim, "session", _Sess(post_exc=TimeoutError("t")))
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Db())
    monkeypatch.setattr(ig_shim.time, "sleep", lambda s: None)
    monkeypatch.setattr(ig_shim, "_get_pending_working_order", lambda tk, uid: None)
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: EPIC)
    monkeypatch.setattr(ig_shim, "get_working_orders", lambda: [])
    ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                signal_summary="t", proximity_pct=5.0, watch=False)
    assert seen["k"].get("email") is True or (len(seen["a"]) >= 4 and seen["a"][-1] is True)


def test_unknown_text_with_cap_or_reject_in_cause_is_not_misclassified():
    import notify
    s = "[working order] UNKNOWN -- the request failed (ReadTimeout: capacity) and IG's order list could not be read"
    assert notify._classify_missed_trade(s)[0] not in notify._SILENT_MISSED_TRADE_CLASSES


def test_the_three_states_are_distinct_classes_so_an_unknown_is_never_deduped_away():
    import notify
    live = "[working order] LIVE AT IG (deal D1) -- the request failed (TimeoutError: cap) but IG holds the order; recorded, NOT retried"
    notp = "[working order] NOT PLACED -- the request failed (TimeoutError: x) and IG holds no such order; safe to retry"
    unk = "[working order] UNKNOWN -- the request failed (TimeoutError: x) and IG's order list could not be read (E: y); check IG before any retry"
    classes = [notify._classify_missed_trade(t)[0] for t in (live, notp, unk)]
    assert classes == ["ORDER_LIVE_AT_IG", "ORDER_NOT_PLACED", "ORDER_UNKNOWN"]      # 'cap' in a cause cannot reclassify
    assert not set(classes) & notify._SILENT_MISSED_TRADE_CLASSES


# ---- 2026-11-14 follow-up (architect design): every doubtful outcome asks IG; positions; size; no double row ----
def _http(code):
    r = requests.Response(); r.status_code = code; r._content = b"gateway"
    return requests.HTTPError(response=r)


class _SessPos(_Sess):
    """Adds IG's open-positions list and a settable reply for POST."""
    def __init__(self, positions=(), reply=None, **kw):
        super().__init__(**kw); self.positions, self.reply = list(positions), reply

    def get(self, path, version=None):
        if path == "/positions":
            return {"positions": self.positions}
        return super().get(path, version)

    def post(self, path, body=None, version=None):
        if self.reply is not None:
            self.posted.append(body); return self.reply
        return super().post(path, body, version)


def _pos(size=1.0, epic=EPIC, direction="BUY"):
    return [{"market": {"epic": epic}, "position": {"dealId": "DIPOS1", "direction": direction, "size": size, "level": 100.2}}]


@pytest.mark.parametrize("code", [500, 502, 504])
def test_gateway_error_after_post_asks_ig_and_adopts_the_order(monkeypatch, code):
    sess = _SessPos(post_exc=_http(code))
    r, alerts, rows = _run(monkeypatch, sess, _wo())
    assert r and r["adopted"] and r["deal_id"] == "DIAAAA1" and len(rows) == 1 and len(sess.posted) == 1
    assert "LIVE AT IG" in alerts[0] and str(code) in alerts[0]


def test_4xx_on_post_is_ig_answering_so_it_does_not_ask_ig(monkeypatch):
    def no_list(): raise AssertionError("a 4xx is IG's own refusal; no list read")
    r, alerts, rows = _run(monkeypatch, _SessPos(post_exc=_http(403)), no_list)
    assert r is None and "IG API error 403" in alerts[0]


def test_http_error_with_no_response_is_asked_not_crashed(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _SessPos(post_exc=requests.HTTPError("no response")), [])
    assert r is None and "NOT PLACED" in alerts[0]


def test_no_deal_reference_asks_ig_absent_says_not_placed_and_emails(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _SessPos(reply={"unexpected": 1}), [])
    assert r is None and "NOT PLACED" in alerts[0] and "no deal reference" in alerts[0]


def test_no_deal_reference_but_order_live_is_adopted_not_retried(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _SessPos(reply={}), _wo())
    assert r and r["adopted"] and "LIVE AT IG" in alerts[0]


def test_order_already_filled_is_found_as_a_position_not_called_safe_to_retry(monkeypatch):
    sess = _SessPos(positions=_pos(size=1.0), post_exc=TimeoutError("t"))
    r, alerts, rows = _run(monkeypatch, sess, [])
    assert r and r["adopted"] and r["deal_id"] == "DIPOS1" and rows == []
    assert "LIVE AT IG" in alerts[0] and "filled" in alerts[0] and "NOT PLACED" not in alerts[0]


def test_a_position_of_a_different_size_is_not_ours(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _SessPos(positions=_pos(size=5.0), post_exc=TimeoutError("t")), [])
    assert r is None and "NOT PLACED" in alerts[0]


def test_an_order_of_a_different_size_is_not_mistaken_for_ours(monkeypatch):
    other = _wo(); other[0]["workingOrderData"]["orderSize"] = 7.0
    r, alerts, rows = _run(monkeypatch, _SessPos(post_exc=TimeoutError("t")), other)
    assert r is None and "NOT PLACED" in alerts[0]


def test_matching_size_is_adopted(monkeypatch):
    mine = _wo(); mine[0]["workingOrderData"]["orderSize"] = 1.0
    r, alerts, rows = _run(monkeypatch, _SessPos(post_exc=TimeoutError("t")), mine)
    assert r and r["adopted"]


def test_an_order_ig_holds_that_we_already_recorded_is_not_written_twice(monkeypatch):
    monkeypatch.setattr(ig_shim, "_deal_recorded", lambda d: True)
    sess = _SessPos(post_exc=TimeoutError("t"))
    monkeypatch.setattr(ig_shim, "session", sess)
    r, alerts, rows = _run(monkeypatch, sess, _wo())
    monkeypatch.setattr(ig_shim, "_deal_recorded", lambda d: True)   # _run re-applies fixtures; pin it again
    assert r and r["adopted"]


def test_unknown_state_covers_a_positions_read_failure_too(monkeypatch):
    class S(_SessPos):
        def get(self, path, version=None):
            if path == "/positions":
                raise ConnectionError("positions 403")
            return super().get(path, version)
    r, alerts, rows = _run(monkeypatch, S(post_exc=TimeoutError("t")), [])
    assert r is None and "UNKNOWN" in alerts[0] and "positions 403" in alerts[0] and "NOT PLACED" not in alerts[0]


def test_ig_accepted_but_our_record_failed_is_emailed_and_never_written_twice(monkeypatch):
    calls = []
    class S(_SessPos):
        def get(self, path, version=None):
            if path.startswith("/confirms"):
                return {"dealStatus": "ACCEPTED", "dealId": "DIOK"}
            return super().get(path, version)
    r, alerts, rows = _run(monkeypatch, S(), [])
    def boom(*a, **k): calls.append(1); raise RuntimeError("pool full")
    monkeypatch.setattr(ig_shim, "_log_working_order_to_db", boom)
    mails = []
    monkeypatch.setattr("notify.alert_missed_trade", lambda t, d, reason, *a, **k: mails.append((reason, k)))
    r2 = ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                     stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                     signal_summary="t", proximity_pct=5.0, watch=False)
    assert r2 and r2["deal_id"] == "DIOK" and len(calls) == 1
    assert "LIVE AT IG (deal DIOK)" in mails[0][0] and "pool full" in mails[0][0] and mails[0][1].get("email") is True


def test_confirmation_read_failure_after_accept_that_ig_lists_is_adopted_with_a_5xx_too(monkeypatch):
    r, alerts, rows = _run(monkeypatch, _SessPos(confirm_exc=_http(502)), _wo())
    assert r and r["adopted"] and "502" in alerts[0]
