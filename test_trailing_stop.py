# ======================================================================================================
# Automated Stop-Loss Amendment — the trailing-stop formula (ig_shim.compute_trailing_stop).
#
# This is the formula that will move REAL IG stops once amend_open_stops() is wired in, and today it
# drives the "let winners run" and stop-loss simulations the decision to enable it rests on. It had no
# direct test of its own: every existing test monkeypatches it to a constant, so the arithmetic itself
# was never checked and a 31% gain giving back to 13% went unnoticed.
# ======================================================================================================
import os

os.environ.setdefault("IG_API_KEY", "test")
os.environ.setdefault("IG_USERNAME", "test")
os.environ.setdefault("IG_PASSWORD", "test")
os.environ.setdefault("IG_ACCOUNT_ID", "test")
os.environ.setdefault("SUPABASE_USER", "test")
os.environ.setdefault("SUPABASE_DB_PASSWORD", "test")

import ig_shim  # noqa: E402


def _kept_pct(direction, entry, stop, price, threshold):
    """Percentage of the move from entry that the amended stop actually locks in."""
    new_stop = ig_shim.compute_trailing_stop(direction, entry, stop, price, threshold)
    if new_stop is None:
        return None
    return ((new_stop - entry) / entry * 100.0) if direction == "BUY" else ((entry - new_stop) / entry * 100.0)


def test_a_31_percent_run_retains_at_least_28_percent():
    """The user's requirement, 2026-08-16: "if we achieved 31% ... we must get at least 28%".

    Verbatim from the case that exposed it — 600048.SS, a SHORT, marked +31% and simulated back to
    +13.38% by the old formula.
    """
    for direction, entry, stop, price in (("BUY", 100.0, 95.0, 131.0),      # long, +31%
                                          ("SELL", 100.0, 105.0, 69.0)):    # short, +31%
        kept = _kept_pct(direction, entry, stop, price, 0.92)
        assert kept is not None, f"{direction}: no amendment produced on a 31% run"
        assert kept >= 28.0, f"{direction}: only {kept:.2f}% of a 31% run retained"


def test_threshold_means_the_share_of_the_run_retained():
    """0.5 keeps half the run, 0.9 keeps ninety percent — the meaning the setting always claimed."""
    assert round(_kept_pct("BUY", 100.0, 95.0, 131.0, 0.5), 4) == 15.5
    assert round(_kept_pct("BUY", 100.0, 95.0, 131.0, 0.9), 4) == 27.9
    assert round(_kept_pct("SELL", 100.0, 105.0, 69.0, 0.9), 4) == 27.9
    # The example previously recorded in the source header, now delivering its stated intent.
    assert ig_shim.compute_trailing_stop("BUY", 105.0, 100.0, 120.0, 0.5) == 112.5


def test_the_stop_does_not_depend_on_how_many_bars_were_held():
    """The old rule ADDED an increment each bar, so the level tracked bar count, not the peak.

    Walking the same price path one bar at a time must land on the same stop as jumping straight to the
    final price -- otherwise the same trade sampled daily and weekly gets different stops.
    """
    entry, threshold = 100.0, 0.9
    stop = 95.0
    for price in (105.0, 112.0, 118.0, 124.0, 131.0):          # incremental walk
        moved = ig_shim.compute_trailing_stop("BUY", entry, stop, price, threshold)
        if moved is not None:
            stop = moved
    direct = ig_shim.compute_trailing_stop("BUY", entry, 95.0, 131.0, threshold)
    assert stop == direct, f"path-dependent: walked to {stop}, jumped to {direct}"


def test_never_widens_and_never_trails_at_a_loss():
    # Price below entry on a long: no amendment at all.
    assert ig_shim.compute_trailing_stop("BUY", 100.0, 95.0, 85.0, 0.9) is None
    # An already-tighter stop is left alone rather than loosened back toward entry.
    assert ig_shim.compute_trailing_stop("BUY", 100.0, 129.0, 131.0, 0.9) is None
    assert ig_shim.compute_trailing_stop("SELL", 100.0, 71.0, 69.0, 0.9) is None
    # Threshold off = feature off.
    assert ig_shim.compute_trailing_stop("BUY", 100.0, 95.0, 131.0, 0) is None


def test_ratchets_upward_as_price_advances():
    entry, stop = 100.0, 95.0
    levels = []
    for price in (110.0, 120.0, 130.0):
        stop = ig_shim.compute_trailing_stop("BUY", entry, stop, price, 0.9) or stop
        levels.append(stop)
    assert levels == sorted(levels), f"stop must only tighten: {levels}"
    assert levels[-1] > entry, "after a 30% run the stop must be above entry"


# ------------------------------------------------------------------------------------------------------
# ABOVE TARGET the take-profit is dropped and the stop trails a fixed DISTANCE below the running price.
#
# The units deliberately differ from the pre-target rule, chosen with the trade-off measured. A
# share-of-the-run trail is tighter -- 4.0 points handed back on a +79% run against 8.9 for a 5% distance
# -- but it cannot be expressed as an IG order. A distance can, and IG then moves the stop tick by tick,
# continuously, including overnight and at weekends, instead of only when our job happens to run. These
# tests model exactly what the live order will do (user 2026-08-16, TRAIL = 4%).
# ------------------------------------------------------------------------------------------------------
TRAIL = 0.04


def _bars(closes):
    return [(f"2026-01-{i + 1:02d}", c * 1.005, c * 0.995, c) for i, c in enumerate(closes)]


def _run(direction, entry, stop, target, closes, thr, stop_thr=0.0):
    from hvf_web import server
    outcome, exit_price = server._run_path(direction, entry, stop, target, _bars(closes), thr, stop_thr)
    gain = ((exit_price - entry) / entry * 100.0) if direction == "BULLISH" else ((entry - exit_price) / entry * 100.0)
    return outcome, exit_price, gain


def test_give_back_above_target_is_bounded_by_the_trail_distance():
    """A +79% run past a +20% target keeps running and hands back only the trail distance."""
    closes = [100, 112, 125, 140, 155, 170, 179, 150, 130, 118, 105]
    _, exit_price, gain = _run("BULLISH", 100.0, 95.0, 120.0, closes, TRAIL)
    assert gain > 70.0, f"only {gain:.2f}% realised from a 79% peak -- the trail is too loose"
    # The stop sits TRAIL below the peak close, so the give-back is that distance on the peak price.
    assert abs(exit_price - 179.0 * (1 - TRAIL)) < 1e-6, f"exited at {exit_price}, expected {179.0 * (1 - TRAIL)}"


def test_a_tighter_trail_retains_more():
    closes = [100, 112, 125, 140, 155, 170, 179, 150, 130, 118, 105]
    tight = _run("BULLISH", 100.0, 95.0, 120.0, closes, 0.04)[2]
    loose = _run("BULLISH", 100.0, 95.0, 120.0, closes, 0.10)[2]
    assert tight > loose, f"4% -> {tight:.2f}%, 10% -> {loose:.2f}%"


def test_the_target_gain_is_never_surrendered():
    """Target hit then an immediate collapse: the exit must be the target, never worse.

    This is what stops the 4% distance from ever costing more than the target itself -- when the trail
    level would fall below target, the target floor wins.
    """
    closes = [100, 112, 121, 108, 99, 94]
    for thr in (0.04, 0.25):
        _, exit_price, gain = _run("BULLISH", 100.0, 95.0, 120.0, closes, thr)
        assert exit_price >= 120.0 - 1e-9, f"thr={thr}: exited at {exit_price}, below the 120 target"
        assert gain >= 20.0 - 1e-9


def test_before_target_the_hard_stop_stands():
    """stop_thr defaults to 0: below target nothing trails, the calculated stop does its job."""
    closes = [100, 108, 115, 118, 105, 96, 94]          # never reaches the 120 target
    outcome, exit_price, _ = _run("BULLISH", 100.0, 95.0, 120.0, closes, TRAIL, 0.0)
    assert exit_price == 95.0, f"expected the hard stop at 95, got {exit_price}"
    assert outcome == "STOPPED"


def test_short_above_target_behaves_as_the_mirror():
    closes = [100, 88, 75, 60, 45, 30, 21, 50, 70, 82]      # short running to +79%
    _, exit_price, gain = _run("BEARISH", 100.0, 105.0, 80.0, closes, TRAIL)
    assert gain > 70.0, f"short only realised {gain:.2f}% from a 79% peak"
    assert exit_price <= 80.0 + 1e-9, "short must never exit above its target level"


# ======================================================================================================
# Let winners run, live (user 2026-08-17: "wire in to stop costing money").
#
# Three phases: hard stop -> stop moved TO target when touched -> IG native trailing once price is 5%
# beyond target. The ordering is the whole safety argument, so it is what these tests pin.
# ======================================================================================================
def _pos(deal_id="D1", direction="BUY", stop=90.0, bid=100.0, offer=100.2, epic="E", **extra):
    return {"position": dict({"dealId": deal_id, "direction": direction, "stopLevel": stop}, **extra),
            "market": {"bid": bid, "offer": offer, "epic": epic}}


def _wire(monkeypatch, positions, targets, enabled=True, uplift=0.05, trail=0.04):
    calls = {"stop": [], "trail": []}
    monkeypatch.setattr(ig_shim, "LIVE_LET_WINNERS_RUN_ENABLED", True)
    monkeypatch.setattr(ig_shim, "_lwr_cfg", lambda user: (enabled, uplift, trail))
    monkeypatch.setattr(ig_shim, "_lwr_targets", lambda: targets)
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: positions)
    monkeypatch.setattr(ig_shim, "update_stop",
                        lambda d, lvl: calls["stop"].append((d, lvl)) or True)
    monkeypatch.setattr(ig_shim, "attach_trailing_stop",
                        lambda d, dist, increment=None: calls["trail"].append((d, round(dist, 4))) or True)
    return calls


def test_lwr_below_target_touches_nothing(monkeypatch):
    """Before the target is reached the original hard stop must stand -- untouched."""
    calls = _wire(monkeypatch, [_pos(bid=95.0)], {"D1": (110.0, "Alex")})
    out = ig_shim.run_let_winners_run()
    assert calls["stop"] == [] and calls["trail"] == []
    assert out["skipped"] == 1


def test_lwr_moves_stop_to_target_on_touch(monkeypatch):
    """Phase 2. Target touched but not yet 5% beyond: the stop goes ON the target and no trail is set.

    Attaching a 4% trail here would place the stop at 110*0.96 = 105.6, BELOW the target -- handing back
    the gain this phase exists to lock. That is why the trail waits.
    """
    calls = _wire(monkeypatch, [_pos(bid=112.0)], {"D1": (110.0, "Alex")})
    ig_shim.run_let_winners_run()
    assert calls["stop"] == [("D1", 110.0)]
    assert calls["trail"] == []


def test_lwr_hands_over_to_ig_once_five_percent_beyond(monkeypatch):
    """Phase 3. At target x1.05 the 4% trail sits at target x1.008 -- already above target, and a
    trailing stop only ratchets up, so the guarantee survives the handover."""
    calls = _wire(monkeypatch, [_pos(bid=115.5)], {"D1": (110.0, "Alex")})     # 110 * 1.05 = 115.5
    ig_shim.run_let_winners_run()
    assert calls["trail"] == [("D1", round(115.5 * 0.04, 4))]
    assert calls["stop"] == []
    # The level IG will hold must clear the original target, or the whole design is unsound.
    assert 115.5 - 115.5 * 0.04 > 110.0


def test_lwr_does_not_re_attach_when_already_trailing(monkeypatch):
    calls = _wire(monkeypatch, [_pos(bid=130.0, trailingStopDistance=5.0)], {"D1": (110.0, "Alex")})
    ig_shim.run_let_winners_run()
    assert calls["trail"] == [] and calls["stop"] == []


def test_lwr_short_mirrors_the_long(monkeypatch):
    """A SELL runs the other way: target BELOW entry, handover at target x0.95, trail above price."""
    calls = _wire(monkeypatch, [_pos(direction="SELL", stop=110.0, bid=94.0, offer=94.5)], {"D1": (100.0, "Alex")})
    ig_shim.run_let_winners_run()
    assert calls["trail"] == [("D1", round(94.5 * 0.04, 4))]   # offer is the exit side for a short


def test_lwr_short_locks_at_target_before_handover(monkeypatch):
    calls = _wire(monkeypatch, [_pos(direction="SELL", stop=110.0, bid=98.0, offer=98.5)], {"D1": (100.0, "Alex")})
    ig_shim.run_let_winners_run()
    assert calls["stop"] == [("D1", 100.0)] and calls["trail"] == []


def test_lwr_never_widens_a_stop(monkeypatch):
    """A stop already tighter than the target must not be loosened back to it."""
    calls = _wire(monkeypatch, [_pos(stop=112.0, bid=113.0)], {"D1": (110.0, "Alex")})
    ig_shim.run_let_winners_run()
    assert calls["stop"] == [] and calls["trail"] == []


def test_lwr_disabled_does_nothing(monkeypatch):
    calls = _wire(monkeypatch, [_pos(bid=200.0)], {"D1": (110.0, "Alex")}, enabled=False)
    out = ig_shim.run_let_winners_run()
    assert calls["stop"] == [] and calls["trail"] == [] and out["users_on"] == []


def test_lwr_live_management_is_safety_disabled_by_default(monkeypatch):
    """A historical-report preference must never amend an IG stop while live routing is incomplete."""
    monkeypatch.setattr(ig_shim, "LIVE_LET_WINNERS_RUN_ENABLED", False)
    monkeypatch.setattr(ig_shim, "get_open_positions",
                        lambda: (_ for _ in ()).throw(AssertionError("must not query IG when disabled")))
    out = ig_shim.run_let_winners_run()
    assert out["disabled"] is True
    assert out["checked"] == 0 and out["locked"] == [] and out["trailing"] == []


def test_lwr_skips_positions_with_no_recorded_target(monkeypatch):
    """No target means no phase boundary. Leave the position entirely alone rather than guess."""
    calls = _wire(monkeypatch, [_pos(bid=500.0)], {"OTHER": (110.0, "Alex")})
    ig_shim.run_let_winners_run()
    assert calls["stop"] == [] and calls["trail"] == []


def test_lwr_is_gated_per_user_not_globally(monkeypatch):
    """User 2026-08-17: "let winners run settings must only be used if that setting is enabled in
    settings for a user".

    Two open positions, two owners, one switch on. Only the enabled owner's position may be touched --
    one login turning the feature on must never change how another login's money is managed.
    """
    monkeypatch.setattr(ig_shim, "LIVE_LET_WINNERS_RUN_ENABLED", True)
    calls = {"stop": [], "trail": []}
    monkeypatch.setattr(ig_shim, "_lwr_cfg",
                        lambda user: (user == "Alex", 0.05, 0.04))
    monkeypatch.setattr(ig_shim, "_lwr_targets",
                        lambda: {"D1": (110.0, "Alex"), "D2": (110.0, "Sam")})
    monkeypatch.setattr(ig_shim, "get_open_positions",
                        lambda: [_pos("D1", bid=115.5), _pos("D2", bid=115.5)])
    monkeypatch.setattr(ig_shim, "update_stop", lambda d, lvl: calls["stop"].append(d) or True)
    monkeypatch.setattr(ig_shim, "attach_trailing_stop",
                        lambda d, dist, increment=None: calls["trail"].append(d) or True)

    out = ig_shim.run_let_winners_run()

    assert calls["trail"] == ["D1"], "only the owner who enabled it may be managed"
    assert "D2" not in calls["trail"] and "D2" not in calls["stop"]
    assert out["users_on"] == ["Alex"]


def test_lwr_reads_the_users_own_setting(monkeypatch):
    """_lwr_cfg must consult that login's saved limits, and fail CLOSED on anything unexpected."""
    monkeypatch.setattr("hvf_web.web_users.get_settings",
                        lambda n: {"limits": {"let_winners_run": 1, "let_winners_run_trail": 4}}
                        if n == "Alex" else {})
    on, uplift, trail = ig_shim._lwr_cfg("Alex")
    assert on is True
    assert trail == 0.04, "the trail must come from the USER's let_winners_run_trail, not an app setting"
    assert uplift == 0.05
    assert ig_shim._lwr_cfg("Sam")[0] is False, "a user who never set it is OFF"
    assert ig_shim._lwr_cfg(None)[0] is False, "no user means OFF, never a default-on"

    # Switch on but no trail configured: fail closed rather than invent a percentage.
    monkeypatch.setattr("hvf_web.web_users.get_settings",
                        lambda n: {"limits": {"let_winners_run": 1, "let_winners_run_trail": 0}})
    assert ig_shim._lwr_cfg("Alex")[0] is False

    def _boom(_n):
        raise RuntimeError("settings store down")
    monkeypatch.setattr("hvf_web.web_users.get_settings", _boom)
    assert ig_shim._lwr_cfg("Alex")[0] is False, "unreadable settings must fail closed"


def test_lwr_owner_profile_has_explicit_alex_binding_only():
    """Legacy profile UUIDs are not web logins; unknown profiles must never be guessed."""
    assert ig_shim._web_login_for_trading_profile("770a76b5-0e84-460b-b575-186c724dabdd") == "Alex"
    assert ig_shim._web_login_for_trading_profile("516e7f8d-59b9-42b0-978e-d676d1245385") is None
    assert ig_shim._web_login_for_trading_profile(None) is None


def test_lwr_refuses_a_trail_wider_than_the_uplift(monkeypatch):
    """The invariant the whole design rests on (user 2026-08-17: "is 25% off the target price not
    nonsense to you?").

    Handover is at target x (1+uplift) and the stop then sits trail% below it, so the guarantee only
    holds while  trail < uplift / (1 + uplift)  -- under 4.76% for a 5% uplift.

    25% was what was actually saved before the user caught it: target x 1.05 x 0.75 = target x 0.7875,
    twenty-one percent BELOW target. That inverts the "can never finish below target" guarantee instead
    of merely weakening it. A value that breaks the invariant is a bug, not a preference, so the code
    must refuse it rather than trust the setting.
    """
    def _settings(trail):
        return lambda n: {"limits": {"let_winners_run": 1, "let_winners_run_trail": trail}}

    monkeypatch.setattr("hvf_web.web_users.get_settings", _settings(25))
    assert ig_shim._lwr_cfg("Alex")[0] is False, "a 25% trail must be refused, not honoured"

    monkeypatch.setattr("hvf_web.web_users.get_settings", _settings(4))
    on, uplift, trail = ig_shim._lwr_cfg("Alex")
    assert on is True and trail == 0.04

    # The boundary is 5/105 = 4.76190...%, where the stop lands exactly ON the target rather than above
    # it. Test either side of it with values that are unambiguous in binary floating point -- 4.7619
    # itself is a hair BELOW the true ratio and is legitimately accepted.
    monkeypatch.setattr("hvf_web.web_users.get_settings", _settings(4.77))
    assert ig_shim._lwr_cfg("Alex")[0] is False, "at or past the boundary the stop is not above target"
    monkeypatch.setattr("hvf_web.web_users.get_settings", _settings(4.7))
    assert ig_shim._lwr_cfg("Alex")[0] is True

    # And the property that matters, stated directly.
    on, uplift, trail = ig_shim._lwr_cfg("Alex")
    assert (1 + uplift) * (1 - trail) > 1.0, "handover stop must sit ABOVE the target"


def test_lwr_gates_the_working_order_path_not_the_market_order_path():
    """Regression guard for a mistake worth not repeating (2026-08-17).

    The first version of this feature omitted the take-profit in open_trade(), which places an immediate
    MARKET order via /positions/otc. That path belongs to the session monitors, which are OFF -- WEB_BRIDGE
    is the only enabled execution source, and it reaches IG through place_working_order() ->
    /workingorders/otc. So the change would never have affected a single real trade. It also referenced a
    `profile` argument open_trade does not take, which would have raised NameError the moment a session
    monitor was re-enabled.

    Structural check against the source: the gate must live on the working-order payload, open_trade must
    keep its unconditional limitDistance, and the working_orders ROW must still record the target even
    when IG is not told about it -- reconcile reads it from there on fill, and it is the only place the
    monitor can learn the target.
    """
    import inspect

    wo_src = inspect.getsource(ig_shim.place_working_order)
    assert 'if not let_run:\n        body["limitLevel"] = str(limit_level)' in wo_src, (
        "the take-profit must be omitted on the WORKING ORDER payload when let_run is set"
    )
    assert "v_limit=limit_level" in inspect.getsource(ig_shim), (
        "the working_orders row must still store the target; reconcile reads it on fill"
    )

    ot_src = inspect.getsource(ig_shim.open_trade)
    assert '"limitDistance":  str(limit_distance),' in ot_src, (
        "open_trade is NOT the let-winners-run path and must keep its take-profit"
    )
    assert "profile" not in ot_src.replace("# ", "").split("body = {")[1], (
        "open_trade takes no profile argument - referencing one is a NameError waiting to happen"
    )

    sig_src = inspect.getsource(ig_shim.place_hvf_order_from_sig)
    assert 'let_run=_lwr_cfg(profile.get("name"))[0]' in sig_src, (
        "the gate decision belongs where the profile is known, not inside place_working_order"
    )


# ======================================================================================================
# Observe-only mode (2026-08-23).
#
# The live let-winners-run path has NEVER executed: it has been safety-disabled since it was written, so
# every assurance about it comes from tests and historical replay rather than from the broker. Observe
# mode runs the complete decision path against the real account -- same owner-scoped sessions, same live
# quotes, same arithmetic -- and records what it WOULD do, so the live path can be judged on real evidence
# without first taking it.
#
# The whole value of that rests on one property: in observe mode it must be INCAPABLE of mutating. These
# tests assert exactly that, including the case where the position qualifies for action.
# ======================================================================================================

def _observe(monkeypatch, positions, targets, enabled=True, uplift=0.05, trail=0.04):
    """Wire observe mode with the mutators replaced by tripwires that fail the test if called."""
    tripped = []
    monkeypatch.setattr(ig_shim, "LIVE_LET_WINNERS_RUN_ENABLED", False)
    monkeypatch.setattr(ig_shim, "LWR_OBSERVE_ONLY", True)
    monkeypatch.setattr(ig_shim, "_lwr_cfg", lambda user: (enabled, uplift, trail))
    monkeypatch.setattr(ig_shim, "_lwr_targets", lambda: targets)
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: positions)
    monkeypatch.setattr(ig_shim, "update_stop",
                        lambda *a, **k: tripped.append(("update_stop", a)) or True)
    monkeypatch.setattr(ig_shim, "attach_trailing_stop",
                        lambda *a, **k: tripped.append(("attach_trailing_stop", a)) or True)
    return tripped


def test_observe_records_the_target_lock_without_sending_it(monkeypatch):
    """Price has touched the target: live mode would move the stop. Observe must only record it."""
    tripped = _observe(monkeypatch, [_pos(bid=112.0)], {"D1": (110.0, "Alex")})

    out = ig_shim.run_let_winners_run()

    assert tripped == [], f"observe mode called a mutating IG endpoint: {tripped}"
    assert out["observe_only"] is True
    assert out["locked"] == [], "nothing was actually locked"
    assert [w["action"] for w in out["would"]] == ["update_stop"]
    assert out["would"][0]["new_stop"] == 110.0


def test_observe_records_the_trailing_handover_without_sending_it(monkeypatch):
    """Price is beyond target x (1 + uplift): live mode would attach an IG trailing stop."""
    tripped = _observe(monkeypatch, [_pos(bid=200.0)], {"D1": (110.0, "Alex")})

    out = ig_shim.run_let_winners_run()

    assert tripped == [], f"observe mode called a mutating IG endpoint: {tripped}"
    assert [w["action"] for w in out["would"]] == ["attach_trailing_stop"]
    assert out["trailing"] == []


def test_observe_is_silent_when_nothing_qualifies(monkeypatch):
    tripped = _observe(monkeypatch, [_pos(bid=95.0)], {"D1": (110.0, "Alex")})

    out = ig_shim.run_let_winners_run()

    assert tripped == []
    assert out["would"] == []


def test_observe_never_runs_while_the_live_switch_is_on(monkeypatch):
    """The two switches must not be confusable: with live enabled, observe is not the mode in play."""
    monkeypatch.setattr(ig_shim, "LIVE_LET_WINNERS_RUN_ENABLED", True)
    monkeypatch.setattr(ig_shim, "LWR_OBSERVE_ONLY", True)
    monkeypatch.setattr(ig_shim, "_lwr_cfg", lambda user: (True, 0.05, 0.04))
    monkeypatch.setattr(ig_shim, "_lwr_targets", lambda: {"D1": (110.0, "Alex")})
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [_pos(bid=112.0)])
    calls = []
    monkeypatch.setattr(ig_shim, "update_stop", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(ig_shim, "attach_trailing_stop", lambda *a, **k: calls.append(a) or True)

    out = ig_shim.run_let_winners_run()

    assert out.get("observe_only") is False
    assert calls, "with the live switch on, the real path must run"


def test_both_switches_off_does_nothing_at_all(monkeypatch):
    """The shipped state. Neither observes nor acts."""
    monkeypatch.setattr(ig_shim, "LIVE_LET_WINNERS_RUN_ENABLED", False)
    monkeypatch.setattr(ig_shim, "LWR_OBSERVE_ONLY", False)
    monkeypatch.setattr(ig_shim, "_lwr_targets",
                        lambda: (_ for _ in ()).throw(AssertionError("must not even read targets")))

    out = ig_shim.run_let_winners_run()

    assert out["disabled"] is True
    assert out["checked"] == 0


def test_the_shipped_state_is_both_switches_off():
    """Neither may be committed as on. Live places real stops; observe would surprise on a live account."""
    import re
    from pathlib import Path
    src = Path(ig_shim.__file__).read_text(encoding="utf-8")

    assert re.search(r"^LIVE_LET_WINNERS_RUN_ENABLED = False$", src, re.M)
    assert re.search(r"^LWR_OBSERVE_ONLY = False$", src, re.M)
