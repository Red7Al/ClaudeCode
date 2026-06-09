# =============================================================================
# File:         run_session.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# -----------------------------------------------------------------------------
# Entry point for GitHub Actions scheduled workflows.
# Called with a session name argument, runs the appropriate routine.
#
# Usage:
#   python run_session.py AUS_OPEN
#   python run_session.py UK_OPEN
#   python run_session.py US_OPEN
#   python run_session.py AUS_MONITOR
#   python run_session.py UK_MONITOR
#   python run_session.py SESSION_CLOSE
#   python run_session.py WEEKEND_REVIEW
#   python run_session.py PREMARKET_BRIEF
#   python run_session.py POSITION_MONITOR
#
# All credentials loaded from environment variables (GitHub Secrets).
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-01  Alex Hind   Initial build. Routes all session names to
#                                 their respective handlers. Duplicate-run guard
#                                 added via already_ran_today().
# 1.1.0   2026-06-05  Alex Hind   Added PREMARKET_BRIEF handler — was listed in
#                                 usage help but crashed with "Unknown session"
#                                 when triggered by the Sunday scheduled task.
# 1.2.0   2026-06-05  Alex Hind   Fix 3: when calculated position size is zero,
#                                 fire Slack alert via alert_circuit_breaker().
#                                 Previously silently skipped — violates no-silent-
#                                 failures policy.
# 1.4.0   2026-06-06  Alex Hind   Code-review fixes: (a) hoist get_user_profile()
#                                 before the per-ticker loop — was per-signal,
#                                 risking inconsistent paper_trade/risk on DB
#                                 flakiness; (b) pass available_funds to
#                                 calculate_position_size to eliminate duplicate
#                                 get_account_balance() call; (c) fix
#                                 alert_circuit_breaker first arg — was passing
#                                 the reason string as user, now passes profile name.
#                                 Same available_funds fix applied in run_session_open.
# 1.3.0   2026-06-06  Alex Hind   run_monitor rescan: replace simplified inline
#                                 size calculation with calculate_position_size().
#                                 Previous code used risk_amount/stop_dist with a
#                                 raw 0.02 hardcode and no margin/min-size check —
#                                 produced limit_dist=0 when stop_dist=0, causing
#                                 IG INVALID_STOP_OR_LIMIT rejections. Now mirrors
#                                 the session open path exactly, respects user
#                                 profile risk_per_trade, and alerts on size=0.
#                                 Also adds stress_mult to the monitor rescan path
#                                 so SPX stress mode is respected consistently.
# 1.7.0   2026-06-06  Alex Hind   run_monitor closure detection: the 1.6.0 per-deal
#                                 verification relied on get_close_reason returning
#                                 UNKNOWN for still-open deals, but for an open
#                                 position IG's activity history returns the OPEN (or
#                                 a trailing-stop AMEND) activity, which get_close_reason
#                                 misreads as a SYSTEM/STOP_HIT close — so a transient
#                                 empty-list glitch could falsely close live positions.
#                                 Now disambiguate at the source: re-fetch the
#                                 positions list. Glitch (re-fetch returns positions)
#                                 ⇒ skip; error ⇒ skip; confirmed empty ⇒ record
#                                 closures. get_close_reason is only called once a deal
#                                 is confirmed gone from the list.
# 1.6.0   2026-06-06  Alex Hind   run_monitor closure detection: replace the blanket
#                                 "skip when IG returns 0 positions" guard (1.5.0)
#                                 with per-deal verification. The blanket skip never
#                                 detected a genuine simultaneous mass-close (e.g. all
#                                 stops hit in a flash crash) — those DB rows would
#                                 stay open forever. Now, when IG returns an empty
#                                 list but the DB has deals, each deal is checked
#                                 against IG activity history and closed only if IG
#                                 confirms a close reason (UNKNOWN ⇒ still open ⇒
#                                 skip). Closure body extracted to _record_closure().
# 1.5.0   2026-06-06  Alex Hind   Fix 4 bugs: (a) run_monitor closure loop: guard
#                                 against IG returning 0 positions when DB has
#                                 entries (transient API glitch), which would
#                                 falsely log all positions as closed; (b) pnl
#                                 hardcoded as 0.0 in trade_closed() — now
#                                 computed from open_price, close_price, direction,
#                                 size; (c) HVF limit_dist overwritten after
#                                 calculate_position_size() — saved and restored
#                                 when HVF target was set (both session-open and
#                                 monitor-rescan paths).
# 1.3.1   2026-06-06  Alex Hind   Fix circuit breaker alert in size=0 path: was
#                                 passing the reason string as the user field,
#                                 showing "Triggered trade skipped..." as User in
#                                 Slack. Now passes profile name (e.g. "Owner").
# =============================================================================

import sys
import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging

from config import DEFAULT_TARGET_RR   # default take-profit = stop * this (3:1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("run_session")


OWNER_USER_ID = "770a76b5-0e84-460b-b575-186c724dabdd"


def already_ran_today(session_name: str) -> bool:
    """
    Return True if this session already produced a macro_snapshot today.

    Used to prevent duplicate runs when the cron-job.org trigger fires late and
    overlaps with a watchdog-triggered manual run, or when the same workflow
    is dispatched more than once in a day.

    Override by setting FORCE_RUN=true in the workflow environment — useful
    when you deliberately want to re-scan mid-session after a code change.
    """
    if os.environ.get("FORCE_RUN", "").lower() in ("true", "1", "yes"):
        log.info(f"FORCE_RUN=true — skipping duplicate-run guard for {session_name}")
        return False
    import pg8000.native
    try:
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        rows = conn.run(
            """select count(*) from macro_snapshot
               where  session = :v_sess
                 and  date(snapshot_time at time zone 'UTC') = current_date""",
            v_sess=session_name
        )
        conn.close()
        count = int(rows[0][0]) if rows else 0
        if count > 0:
            log.info(f"{session_name} already ran today ({count} macro snapshot(s)) — skipping. "
                     f"Set FORCE_RUN=true to override.")
            return True
        return False
    except Exception as e:
        log.warning(f"Could not check duplicate-run guard: {e} — proceeding with run")
        return False  # If the check itself fails, proceed rather than silently skip


def get_user_profile(user_id: str = OWNER_USER_ID) -> dict:
    """
    Fetch the user profile from Supabase.
    Returns risk_per_trade (as a decimal, e.g. 0.02), paper_trade flag,
    daily_loss_limit, max_open_pos, and name.
    """
    import pg8000.native
    try:
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        rows = conn.run(
            """select name, risk_per_trade, daily_loss_limit,
                      max_open_pos, paper_trade
               from   user_profiles where id = :uid""",
            uid=user_id
        )
        conn.close()
        if rows:
            return {
                "user_id":         user_id,
                "name":            rows[0][0],
                "risk_per_trade":  float(rows[0][1]) / 100.0,  # e.g. 2.0 → 0.02
                "daily_loss_limit": float(rows[0][2]),
                "max_open_pos":    int(rows[0][3]),
                "paper_trade":     bool(rows[0][4]),
            }
    except Exception as e:
        log.warning(f"Could not fetch user profile: {e}")
    # Safe defaults if DB unavailable
    return {"user_id": user_id, "name": "Owner", "risk_per_trade": 0.02,
            "daily_loss_limit": 3.0, "max_open_pos": 5, "paper_trade": False}


def run_session_open(session_name: str):
    """Run a session open scan and execute trades."""
    from signals import run_session_scan
    from notify import (session_summary, alert_macro_gate_failed,
                        alert_calendar_block, trade_opened, alert_circuit_breaker,
                        session_heartbeat)
    from ig_shim import open_trade, get_account_balance, health_check, calculate_position_size, get_epic

    # Guard: skip if this session already ran today.
    # Prevents duplicate signal_log noise from delayed cron + watchdog + manual triggers.
    # Set FORCE_RUN=true in the workflow env to bypass when a re-scan is intentional.
    if already_ran_today(session_name):
        return

    # Load user profile — risk %, paper trade flag, limits
    profile = get_user_profile()
    log.info(f"Trading as: {profile['name']} | "
             f"risk={profile['risk_per_trade']*100:.1f}% | "
             f"paper={profile['paper_trade']}")

    # Health check
    hc = health_check()
    if hc["status"] != "OK":
        alert_circuit_breaker("System", "ALL",
            f"IG unreachable at {session_name}: {hc.get('error')}")
        return

    # Full signal scan
    result = run_session_scan(session_name)

    if result.get("block_trading"):
        alert_calendar_block(result["block_reason"], session_name)
        return

    macro = result["macro"]
    if not macro.get("macro_gate_pass"):
        alert_macro_gate_failed(
            macro["gate_reason"], macro["vix"],
            macro["yield_spread"], session_name)
        return

    instruments_scanned = result["instruments_scanned"]
    session_summary(session_name, macro,
                    instruments_scanned,
                    result["trade_candidates"])

    # Execute trades — stop once 3 are PLACED (not merely attempted). Walk up to
    # the 6 highest-conviction candidates so a broken-epic / spread-blocked name
    # does not consume a slot a good setup could have used (IBM HVF R:R 5.75 was
    # never reached under the old [:3]-attempted cap). Candidates are conviction-
    # ordered in signals.run_session_scan.
    trades_placed = 0
    stress_mult   = macro.get("stress_size_multiplier", 1.0)
    for sig in result["trade_candidates"][:6]:
        if trades_placed >= 3:
            break

        ticker     = sig["ticker"]
        direction  = sig["direction"]
        stop_dist  = sig.get("stop_distance", 0)
        limit_dist = round(stop_dist * DEFAULT_TARGET_RR, 4)

        # If an HVF pattern fired, use its more precise stop and target instead
        # of the generic ATR-based ones. HVF stop = just outside L3/H3 level.
        # HVF target = H1-L1 range from midpoint — typically much better than 2×.
        hvf_stop  = sig.get("hvf_stop_level")
        hvf_target = sig.get("hvf_target")
        try:
            import yfinance as yf
            from config import YAHOO_MAP
            yticker = YAHOO_MAP.get(ticker, ticker)
            current = float(yf.Ticker(yticker).fast_info.get("lastPrice", 0) or 0)
            if hvf_stop and hvf_target and current > 0:
                hvf_stop_dist   = abs(current - hvf_stop)
                hvf_limit_dist  = abs(hvf_target - current)
                if hvf_stop_dist > 0 and hvf_limit_dist > hvf_stop_dist:
                    stop_dist  = round(hvf_stop_dist,  4)
                    limit_dist = round(hvf_limit_dist, 4)
                    log.info(f"{ticker}: using HVF stop/target "
                             f"(stop_dist={stop_dist} limit_dist={limit_dist} "
                             f"R:R={sig.get('hvf_risk_reward')})")
        except Exception:
            pass   # keep ATR-based distances if price fetch fails

        signal_str = (
            f"Options:{sig.get('options_bias','—')} "
            f"BB:{sig.get('bb_breakout_dir','—')} "
            f"HVF:{sig.get('hvf_type','—')}({sig.get('hvf_signal','—')}) "
            f"COT:{sig.get('cot_bias','—')} "
            f"PA:{sig.get('pa_verdict','—')} "
            f"Confs:{sig.get('confirmation_count',0)}"
        )

        # Size: margin-aware, uses risk_per_trade from user profile.
        # Apply stress multiplier if SPX is in a down day (1.0–2.5% drop).
        try:
            bal         = get_account_balance()
            available   = bal["available"]
            risk_amount = available * profile["risk_per_trade"] * stress_mult
            epic        = get_epic(ticker)
            if epic:
                _saved_limit_dist = limit_dist  # preserve HVF target before size calc adjusts stop_dist
                size, stop_dist = calculate_position_size(
                    epic, stop_dist, risk_amount, available_funds=available
                )
                # Only recalculate limit_dist when HVF did not supply a precise target.
                # calculate_position_size may adjust stop_dist (e.g. to IG minimum),
                # so blindly setting limit_dist = stop_dist * DEFAULT_TARGET_RR here discards the HVF level.
                if sig.get("hvf_stop_level") and sig.get("hvf_target"):
                    limit_dist = _saved_limit_dist
                else:
                    limit_dist = round(stop_dist * DEFAULT_TARGET_RR, 4)
            else:
                size = 0.0
        except Exception as e:
            log.warning(f"Size calculation failed: {e}")
            size = 0.0
        if stress_mult < 1.0:
            log.info(f"Stress mode: size reduced {int((1-stress_mult)*100)}% for {ticker}")

        if size <= 0:
            # Trade was triggered by signals but blocked at execution — NOT a silent skip.
            # Alert to Slack so the user can see which trades were ready but couldn't fire.
            msg = (
                f"{ticker} ({direction}) — trade triggered by signals but skipped: "
                f"calculated size is zero. Likely cause: account available balance is "
                f"too small to meet IG's minimum deal size for this instrument. "
                f"Review account balance or reduce risk_per_trade in user_profiles."
            )
            log.warning(msg)
            alert_circuit_breaker(profile.get("name", "Owner"), ticker, msg)
            continue

        trade_result = open_trade(
            user_id=profile["user_id"],
            ticker=ticker, direction=direction, size=size,
            stop_distance=stop_dist, limit_distance=limit_dist,
            session_name=session_name, signal_summary=signal_str,
            paper_trade=profile["paper_trade"]
        )

        if trade_result:
            trade_opened(ticker, direction, size,
                         trade_result["level"], trade_result["stop_level"],
                         trade_result["limit_level"], session_name, signal_str,
                         user=profile["name"])
            trades_placed += 1

    # Scan social feeds for new picks at each session open
    try:
        from social_monitor import scan_social_feeds
        scan_social_feeds(max_age_hours=8)   # picks since last session
    except Exception as e:
        log.warning(f"Social feed scan failed: {e}")

    log.info(f"{session_name} complete. Trades placed: {trades_placed}")

    # ── Heartbeat — always post so you know the session ran ───────────────────
    SCHEDULED = {"AUS_OPEN": "00:00", "UK_OPEN": "08:00", "US_OPEN": "14:30"}
    try:
        from datetime import datetime, timezone
        actual_utc = datetime.now(timezone.utc).strftime("%H:%M")
        session_heartbeat(
            session_name        = session_name,
            scheduled_utc       = SCHEDULED.get(session_name, "??:??"),
            actual_utc          = actual_utc,
            instruments_scanned = instruments_scanned,
            trades_placed       = trades_placed,
            gate_pass           = macro.get("macro_gate_pass", True),
            gate_reason         = macro.get("gate_reason", "—"),
            market_stress       = macro.get("market_stress", "NORMAL"),
            spx_change_pct      = macro.get("spx_change_pct"),
        )
    except Exception as e:
        log.debug(f"Heartbeat post failed (non-critical): {e}")


def run_monitor(session_name: str = "AUS_MONITOR"):
    """
    Check open positions and update trailing stops.
    Also scans session instruments for new trade entries — signals can fire
    at any point in the session, not just at the open.
    """
    from ig_shim import (get_open_positions, get_snapshot, update_stop,
                         get_close_reason, _log_trade_close_to_db, health_check,
                         open_trade, get_account_balance, calculate_position_size,
                         get_epic)
    from notify import trade_closed, alert_circuit_breaker, alert_system_error, alert_position_deterioration
    from signals import scan_instrument, get_macro_gate
    from intraday_signals import scan_intraday
    from config import ATR_MULTIPLIERS, ATR_MULTIPLIER_DEFAULT, SESSION_INSTRUMENTS, MAX_TRADES_PER_SESSION, SESSION_TRADE_CAPS
    import pg8000.native
    from datetime import datetime, timezone

    # Map monitor session to the open session's instrument list
    SESSION_MAP = {
        "AUS_MONITOR":      "AUS_OPEN",
        "UK_MONITOR":       "UK_OPEN",
        "POSITION_MONITOR": "US_OPEN",
    }

    hc = health_check()
    if hc["status"] != "OK":
        alert_circuit_breaker("System", "ALL",
            f"IG unreachable: {hc.get('error')}")
        return

    ig_positions = get_open_positions()
    open_tickers = set()

    if ig_positions:
        log.info(f"Monitoring {len(ig_positions)} position(s)")
    else:
        log.info(f"{session_name}: no open positions — skipping stop review")

    # ── Part 1: trailing stops ────────────────────────────────────────────────
    for pos in ig_positions:
        open_tickers.add(pos["market"].get("instrumentName", ""))
        epic      = pos["market"]["epic"]
        deal_id   = pos["position"]["dealId"]
        direction = pos["position"]["direction"]
        stop      = pos["position"].get("stopLevel", 0)

        snap  = get_snapshot(epic)
        price = snap.get("bid", 0) if direction == "BUY" else snap.get("offer", 0)
        if not price:
            continue

        ticker = epic.split(".")[-3] if "." in epic else epic
        mult   = ATR_MULTIPLIERS.get(ticker, ATR_MULTIPLIER_DEFAULT)
        stop_dist = price * 0.015 * mult

        if direction == "BUY":
            new_stop = round(price - stop_dist, 4)
            if stop and new_stop > float(stop) * 1.005:
                update_stop(deal_id, new_stop)
                log.info(f"Stop raised: {epic} {stop} -> {new_stop}")
        else:
            new_stop = round(price + stop_dist, 4)
            if stop and new_stop < float(stop) * 0.995:
                update_stop(deal_id, new_stop)
                log.info(f"Stop lowered: {epic} {stop} -> {new_stop}")

        # Intraday deterioration check — flag positions showing exit signals
        try:
            intra = scan_intraday(ticker)
            if not intra.get("hold_flag") and intra.get("alert"):
                alert_position_deterioration(
                    session=session_name,
                    ticker=ticker,
                    direction=direction,
                    reasons=intra["alert"]
                )
                log.warning(f"{session_name} position alert: {ticker} — {intra['alert']}")
        except Exception as e:
            log.debug(f"Intraday scan skipped for {ticker}: {e}")

    # Detect positions closed by IG
    conn = pg8000.native.Connection(
        host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
        database="postgres", user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
    )
    our_deals  = {r[0]: r for r in conn.run(
        "select deal_id, ticker, direction, open_price, size, opened_at from positions"
    )}
    ig_deal_ids = {pos["position"]["dealId"] for pos in ig_positions}
    conn.close()

    # Record one detected closure: log to DB, compute realised P&L, notify Slack.
    def _record_closure(deal_id, row, close_reason, close_price):
        ticker, direction, open_price, size = row[1], row[2], float(row[3]), float(row[4])
        opened_at = row[5] if len(row) > 5 else None
        open_tickers.add(ticker)
        # close_price is 0.0 when IG's activity/transaction history did not surface
        # the close — fall back to a live market snapshot so the P&L is not a
        # misleading £0.00 (entry == close). Snapshot is approximate but real.
        actual_close = close_price
        if not actual_close:
            try:
                from ig_shim import get_epic, get_snapshot
                snap  = get_snapshot(get_epic(ticker))
                actual_close = snap.get("bid", 0) if direction == "BUY" else snap.get("offer", 0)
            except Exception as e:
                log.warning(f"Close-price snapshot fallback failed for {ticker}: {e}")
            actual_close = actual_close or open_price
        _log_trade_close_to_db(deal_id, actual_close, close_reason)
        pnl = round(
            (actual_close - open_price) * size if direction == "BUY"
            else (open_price - actual_close) * size,
            2
        )
        trade_closed(ticker, direction, open_price, actual_close, pnl, close_reason,
                     opened_at=opened_at)
        log.info(f"Detected closure: {ticker} {deal_id} — {close_reason} @ {actual_close}")

    if not ig_positions and our_deals:
        # IG returned an empty positions list while the DB still holds open deals.
        # Ambiguous: either a transient API glitch (a 200 with an empty array while
        # the positions are really still open) or a genuine mass-close (e.g. every
        # stop hit in a flash crash). Disambiguate AT THE SOURCE by re-fetching the
        # positions list: a transient empty-glitch is very unlikely to repeat on an
        # immediate second call, while a genuine close stays closed.
        #
        # Do NOT infer open-vs-closed from get_close_reason here. For a still-open
        # deal, IG's activity history returns the OPEN activity (or a trailing-stop
        # AMEND), which get_close_reason can misread as a SYSTEM/STOP_HIT close —
        # that would falsely close live positions, the exact data loss this guard
        # exists to prevent. get_close_reason is only safe once a deal is CONFIRMED
        # gone from the positions list.
        log.warning(
            f"{session_name}: IG returned 0 positions but {len(our_deals)} exist in DB "
            f"— re-fetching to confirm before any closure"
        )
        try:
            confirm = get_open_positions()
        except Exception as e:
            confirm = None
            log.warning(f"{session_name}: positions re-fetch errored ({e})")

        if confirm:
            # Re-fetch found positions → the first empty result was a glitch.
            log.warning(f"{session_name}: re-fetch returned {len(confirm)} position(s) "
                        f"— transient glitch, skipping closure detection this pass")
        elif confirm is None:
            # Re-fetch errored → still ambiguous → skip to avoid false closures.
            log.warning(f"{session_name}: re-fetch inconclusive — skipping closure detection this pass")
        else:
            # Re-fetch also empty → positions are genuinely gone. Record closures.
            log.warning(f"{session_name}: re-fetch confirms 0 positions — recording "
                        f"{len(our_deals)} closure(s)")
            for deal_id, row in our_deals.items():
                if deal_id.startswith("PAPER-"):
                    continue
                close_reason, close_price = get_close_reason(deal_id)
                _record_closure(deal_id, row, close_reason, close_price)
    else:
        for deal_id, row in our_deals.items():
            if deal_id not in ig_deal_ids and not deal_id.startswith("PAPER-"):
                close_reason, close_price = get_close_reason(deal_id)
                _record_closure(deal_id, row, close_reason, close_price)

    # ── Part 2: scan for new entries ─────────────────────────────────────────
    try:
        conn2 = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        _grp = (session_name or "").split("_")[0].upper()
        today_count = conn2.run(
            "select count(*) from trade_log where session like :g and date(opened_at) = current_date",
            g=_grp + "%"
        )
        db_tickers = {r[0] for r in conn2.run("select ticker from positions")}
        conn2.close()
        open_tickers |= db_tickers
        trades_today    = int(today_count[0][0]) if today_count else 0
        slots_remaining = max(0, SESSION_TRADE_CAPS.get(_grp, MAX_TRADES_PER_SESSION) - trades_today)
    except Exception as e:
        log.error(f"Could not check trade count: {e}")
        slots_remaining = 0

    if slots_remaining > 0:
        open_session = SESSION_MAP.get(session_name, "US_OPEN")
        candidates   = [t for t in SESSION_INSTRUMENTS.get(open_session, [])
                        if t not in open_tickers]
        log.info(f"{session_name}: scanning {len(candidates)} instruments for new entries "
                 f"({slots_remaining} slot(s) remaining)")
        try:
            macro       = get_macro_gate(session_name)
            stress_mult = macro.get("stress_size_multiplier", 1.0)
            if macro.get("macro_gate_pass"):
                # Fetch profile once per monitor pass, not per signal.
                # Previously fetched inside the per-signal block, causing:
                #   (a) N Supabase round-trips for N signals in one session
                #   (b) If DB fails mid-scan, later tickers get the safe-default
                #       profile (paper_trade=False) even for paper-only users,
                #       which could trigger a live trade.
                profile    = get_user_profile()
                new_trades = 0
                for ticker in candidates:
                    if new_trades >= slots_remaining:
                        break
                    try:
                        sig = scan_instrument(ticker, session_name, macro)
                        if sig.get("trade_signal"):
                            stop_dist  = sig.get("stop_distance", 0)
                            limit_dist = round(stop_dist * DEFAULT_TARGET_RR, 4)

                            # Use calculate_position_size() — same path as session open.
                            # Previously used a simplified inline calculation that:
                            #   (a) didn't enforce IG minimum deal size
                            #   (b) didn't check margin constraint
                            #   (c) produced limit_dist=0 when stop_dist=0, causing
                            #       IG to reject the order (INVALID_STOP_OR_LIMIT).
                            # Now mirrors run_session_open exactly — reads the risk %
                            # from the user profile so Wife/Son profiles are respected.
                            size = 0.0
                            try:
                                bal         = get_account_balance()
                                available   = bal["available"]
                                risk_amount = available * profile["risk_per_trade"] * stress_mult
                                epic_code   = get_epic(ticker)
                                if epic_code:
                                    # Pass available_funds so calculate_position_size
                                    # skips its internal get_account_balance() call —
                                    # avoids a duplicate IG API call per instrument
                                    # and ensures margin check uses the same balance
                                    # snapshot as the risk calculation.
                                    _saved_limit_dist = limit_dist
                                    size, stop_dist = calculate_position_size(
                                        epic_code, stop_dist, risk_amount,
                                        available_funds=available
                                    )
                                    if sig.get("hvf_stop_level") and sig.get("hvf_target"):
                                        limit_dist = _saved_limit_dist
                                    else:
                                        limit_dist = round(stop_dist * DEFAULT_TARGET_RR, 4)
                            except Exception as e:
                                log.warning(f"Monitor size calc failed for {ticker}: {e}")

                            if size <= 0:
                                msg = (
                                    f"{ticker} ({sig['direction']}) — monitor rescan trade "
                                    f"skipped: calculated size is zero. "
                                    f"Likely cause: margin too small for IG minimum deal size."
                                )
                                log.warning(msg)
                                alert_circuit_breaker(
                                    profile["name"], ticker, msg
                                )
                                continue

                            signal_str = (
                                f"Options:{sig.get('options_bias','—')} "
                                f"BB:{sig.get('bb_breakout_dir','—')} "
                                f"Vol:{sig.get('volume_signal','—')} "
                                f"COT:{sig.get('cot_bias','—')} "
                                f"Confs:{sig.get('confirmation_count',0)} "
                                f"[{session_name} rescan]"
                            )
                            result = open_trade(
                                user_id=profile["user_id"],
                                ticker=ticker,
                                direction=sig["direction"],
                                size=size,
                                stop_distance=stop_dist,
                                limit_distance=limit_dist,
                                session_name=session_name,
                                signal_summary=signal_str,
                                paper_trade=profile["paper_trade"]
                            )
                            if result:
                                log.info(f"{session_name} NEW TRADE: {ticker} {sig['direction']}")
                                new_trades += 1
                    except Exception as e:
                        log.warning(f"Monitor scan failed for {ticker}: {e}")
            else:
                log.info(f"{session_name}: macro gate closed — {macro.get('gate_reason')}")
        except Exception as e:
            log.error(f"{session_name} new-entry scan failed: {e}")
    else:
        log.info(f"{session_name}: daily trade limit reached — no new entries scanned")


def run_session_close():
    """End of day — hold vs close decisions."""
    from ig_shim import get_open_positions, close_trade, get_snapshot
    from notify import trade_closed, session_summary

    positions  = get_open_positions()
    closed = held = 0

    for pos in positions:
        epic      = pos["market"]["epic"]
        deal_id   = pos["position"]["dealId"]
        direction = pos["position"]["direction"]
        level     = float(pos["position"]["level"])
        size      = float(pos["position"]["size"])
        stop      = pos["position"].get("stopLevel", 0)

        snap  = get_snapshot(epic)
        price = snap.get("bid", 0) if direction == "BUY" else snap.get("offer", 0)
        if not price:
            held += 1
            continue

        pnl       = (price - level) * size if direction == "BUY" else (level - price) * size
        stop_dist = abs(level - float(stop)) if stop else 0
        risk_amt  = stop_dist * size

        should_close = False
        reason       = "SESSION_CLOSE"

        if "CL." in epic or "CL=" in epic:
            should_close = True
            reason       = "SESSION_CLOSE_OIL"
        elif risk_amt > 0 and pnl >= risk_amt * 1.5:
            should_close = True
            reason       = "SESSION_CLOSE_PROFIT_LOCK"

        if should_close:
            if close_trade(deal_id, reason=reason):
                trade_closed(epic, direction, level, price, round(pnl, 2), reason)
                closed += 1
        else:
            held += 1

    log.info(f"Session close: closed={closed} held={held}")


def refresh_senator_scores():
    """
    Score US senators by their equity trading performance vs SPX.
    Data source: Quiver Quant API (free tier — senate trades only).
    Formula: score = win_rate × avg_excess_return
    Requires: QUIVER_QUANT_API_KEY environment variable.

    For each senator trade (purchase only):
      - Fetch stock price at disclosure date and 30 days later (Yahoo Finance)
      - Compare to SPX return over same window
      - excess_return = stock_30d_return - spx_30d_return
      - Tally win_rate (% where excess_return > 0) and avg_excess_return
    """
    import requests as req
    import pg8000.native
    import yfinance as yf
    import numpy as np
    from datetime import datetime, timedelta

    api_key = os.environ.get("QUIVER_QUANT_API_KEY", "")
    if not api_key:
        log.warning("QUIVER_QUANT_API_KEY not set — skipping senator scoring")
        return

    log.info("Fetching congress trades from Quiver Quant...")
    try:
        resp = req.get(
            "https://api.quiverquant.com/beta/historical/congress",
            headers={"Authorization": f"Token {api_key}"},
            timeout=30
        )
        resp.raise_for_status()
        trades = resp.json()
    except Exception as e:
        log.error(f"Quiver Quant fetch failed: {e}")
        return

    # Filter: Senate purchases only, last 2 years
    cutoff = datetime.now() - timedelta(days=730)
    senate_trades = []
    for t in trades:
        if t.get("Chamber", "").lower() != "senate":
            continue
        if t.get("Transaction", "").lower() not in ("purchase", "buy"):
            continue
        try:
            trade_date = datetime.strptime(t["Date"][:10], "%Y-%m-%d")
        except Exception:
            continue
        if trade_date < cutoff:
            continue
        senate_trades.append({
            "senator":  t.get("Representative", ""),
            "ticker":   t.get("Ticker", "").upper(),
            "date":     trade_date,
            "amount":   t.get("Amount", ""),
        })

    log.info(f"Scoring {len(senate_trades)} senate purchase records...")

    # Score each trade: 30-day return vs SPX
    scored = {}   # {senator: [excess_returns]}
    spy    = yf.Ticker("SPY")

    for trade in senate_trades:
        ticker = trade["ticker"]
        date   = trade["date"]
        end    = date + timedelta(days=35)
        if end > datetime.now():
            continue   # not enough history yet
        try:
            stock = yf.Ticker(ticker)
            s_hist = stock.history(start=date.strftime("%Y-%m-%d"),
                                   end=end.strftime("%Y-%m-%d"))
            m_hist = spy.history(start=date.strftime("%Y-%m-%d"),
                                 end=end.strftime("%Y-%m-%d"))
            if len(s_hist) < 5 or len(m_hist) < 5:
                continue
            stock_ret = (float(s_hist["Close"].iloc[-1]) - float(s_hist["Close"].iloc[0])) \
                        / float(s_hist["Close"].iloc[0])
            spx_ret   = (float(m_hist["Close"].iloc[-1]) - float(m_hist["Close"].iloc[0])) \
                        / float(m_hist["Close"].iloc[0])
            excess    = round(stock_ret - spx_ret, 4)
            senator   = trade["senator"]
            if senator not in scored:
                scored[senator] = []
            scored[senator].append(excess)
        except Exception:
            continue

    # Build senator_scores records
    conn = pg8000.native.Connection(
        host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
        database="postgres", user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
    )

    updated = 0
    for senator, returns in scored.items():
        if len(returns) < 5:
            continue   # minimum 5 trades to qualify
        win_rate         = round(sum(1 for r in returns if r > 0) / len(returns), 4)
        avg_excess       = round(float(np.mean(returns)), 4)
        score            = round(win_rate * avg_excess, 6)
        qualified        = score > 0 and len(returns) >= 5
        try:
            conn.run(
                """insert into senator_scores
                   (senator_name, trade_count, win_rate, avg_excess_return, score, qualified, last_updated)
                   values (:n, :tc, :wr, :ae, :sc, :q, now())
                   on conflict (senator_name) do update
                   set trade_count=:tc, win_rate=:wr, avg_excess_return=:ae,
                       score=:sc, qualified=:q, last_updated=now()""",
                n=senator, tc=len(returns), wr=win_rate,
                ae=avg_excess, sc=score, q=qualified
            )
            updated += 1
        except Exception as e:
            log.warning(f"Failed to upsert senator {senator}: {e}")

    conn.close()
    log.info(f"Senator scores updated: {updated} senators, "
             f"{sum(1 for r in scored.values() if len(r)>=5 and sum(1 for x in r if x>0)/len(r)*sum(1 for x in r if x>0)/len(r)>0)} qualified")


def run_weekend_review():
    """COT refresh, senator scoring, weekly digest."""
    import requests as req
    import pg8000.native
    from notify import weekly_digest
    from config import CFTC_CODES
    from cot_analysis import refresh_all_cot
    from datetime import datetime, timedelta

    # Refresh COT
    log.info("Refreshing COT data...")
    refresh_all_cot()

    # Refresh senator scores from Quiver Quant
    log.info("Refreshing senator scores...")
    refresh_senator_scores()

    # Refresh superinvestor holdings from Dataroma
    log.info("Refreshing superinvestor holdings from Dataroma...")
    refresh_superinvestors()

    # Weekly P&L
    conn = pg8000.native.Connection(
        host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
        database="postgres", user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
    )
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows  = conn.run(
        f"select sum(pnl), count(*), sum(case when pnl>0 then 1 else 0 end) "
        f"from trade_log where closed_at >= '{since}'"
    )
    total_pnl   = float(rows[0][0] or 0)
    trade_count = int(rows[0][1]   or 0)
    win_count   = int(rows[0][2]   or 0)
    win_rate    = (win_count / trade_count * 100) if trade_count > 0 else 0

    best_rows = conn.run(
        f"select ticker, direction, pnl from trade_log "
        f"where closed_at >= '{since}' order by pnl desc limit 1"
    )
    best_trade = (f"{best_rows[0][0]} {best_rows[0][1]} +£{best_rows[0][2]:.2f}"
                  if best_rows else "None")

    senator_rows = conn.run(
        "select senator_name, score, win_rate, trade_count "
        "from senator_scores where qualified=true order by score desc limit 5"
    )
    top_senators = [
        {"name": r[0], "score": float(r[1] or 0),
         "win_rate": float(r[2] or 0), "trade_count": int(r[3] or 0)}
        for r in senator_rows
    ]

    inv_rows = conn.run(
        f"select investor_name, action, ticker from notable_investors "
        f"where recorded_at >= '{since}' order by recorded_at desc limit 5"
    )
    superinvestor_changes = [
        {"investor": r[0], "action": r[1], "ticker": r[2]}
        for r in inv_rows
    ]
    conn.close()

    weekly_digest(
        stats={"total_pnl": total_pnl, "trade_count": trade_count,
               "win_rate": win_rate, "best_trade": best_trade},
        top_senators=top_senators,
        superinvestor_changes=superinvestor_changes
    )
    log.info("Weekend review complete")


def refresh_superinvestors():
    """
    Scrape Dataroma holdings pages for tracked superinvestors.
    Inserts new BUY / ADD / NEW rows into notable_investors.
    Idempotent — ON CONFLICT DO NOTHING prevents duplicates across weekly runs.
    """
    import requests as req
    import pg8000.native
    import re
    import time as _time
    from html.parser import HTMLParser
    from datetime import date

    MANAGERS = [
        ("Warren Buffett",  "BRK"),
        ("Bill Ackman",     "PS"),
        ("Michael Burry",   "MSB"),
        ("David Tepper",    "DAV"),
        ("Carl Icahn",      "ICA"),
        ("Seth Klarman",    "BAU"),
        ("Chase Coleman",   "CHA"),
        ("Terry Smith",     "TER"),
        ("Nelson Peltz",    "NEL"),
        ("George Soros",    "SFM"),
    ]

    class DataromaParser(HTMLParser):
        """Extract (ticker, activity) pairs from a Dataroma holdings page."""

        def __init__(self):
            super().__init__()
            self.rows = []           # list of completed rows
            self._row = []           # cells in current row
            self._cell = ""          # text accumulating in current cell
            self._ticker = None      # ticker found in href of current row
            self._in_td = False

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self._row = []
                self._ticker = None
            elif tag == "td":
                self._in_td = True
                self._cell = ""
            elif tag == "a":
                href = dict(attrs).get("href", "")
                m = re.search(r"stock=([A-Z]{1,6})", href)
                if m:
                    self._ticker = m.group(1)

        def handle_endtag(self, tag):
            if tag == "td":
                self._row.append(self._cell.strip())
                self._in_td = False
            elif tag == "tr":
                if self._ticker and len(self._row) >= 3:
                    self.rows.append((self._ticker, self._row[2]))  # col 2 = activity

        def handle_data(self, data):
            if self._in_td:
                self._cell += data

    conn = pg8000.native.Connection(
        host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
        database="postgres", user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
    )

    inserted = 0
    today    = date.today().isoformat()
    BUY_ACTIONS = {"buy", "add", "new"}

    for name, code in MANAGERS:
        try:
            resp = req.get(
                f"https://www.dataroma.com/m/holdings.php?m={code}",
                headers={"User-Agent": "Mozilla/5.0 (compatible; EndToEndTrading/1.0)"},
                timeout=15
            )
            if resp.status_code != 200:
                log.warning(f"Dataroma {name} ({code}): HTTP {resp.status_code}")
                continue

            parser = DataromaParser()
            parser.feed(resp.text)

            for ticker, activity in parser.rows:
                if activity.lower().strip() not in BUY_ACTIONS:
                    continue
                action = activity.upper().strip()
                try:
                    conn.run(
                        """insert into notable_investors
                           (investor_name, ticker, action, source, disclosed_at, notes)
                           values (:n, :t, :a, 'Dataroma', :d, :notes)
                           on conflict do nothing""",
                        n=name, t=ticker, a=action, d=today,
                        notes=f"Dataroma 13F — {action}"
                    )
                    inserted += 1
                except Exception as e:
                    log.warning(f"Insert failed {name}/{ticker}: {e}")

            buys = sum(1 for _, act in parser.rows if act.lower().strip() in BUY_ACTIONS)
            log.info(f"Dataroma {name} ({code}): {len(parser.rows)} holdings, {buys} buys/adds")
            _time.sleep(1)  # polite crawl delay

        except Exception as e:
            log.warning(f"Dataroma fetch failed for {name} ({code}): {e}")

    conn.close()
    log.info(f"Superinvestor refresh complete: {inserted} new entries inserted")


# =============================================================================
# Schema self-heal
# Runs at the top of every session. ADD COLUMN IF NOT EXISTS is a no-op when
# the column already exists — safe to run every time, no manual trigger needed.
# =============================================================================

REQUIRED_SCHEMA = [
    # signal_log — batch 1 (added 2026-06-02)
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS call_put_ratio  numeric",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS primary_count   integer",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS direction        text",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS pa_verdict       text",
    # signal_log — batch 2 (added 2026-06-03)
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS adx_signal      text",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS obv_signal      text",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS volume_signal   text",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS volume_ratio    numeric",
    # signal_log — batch 3 (added 2026-06-04): Hunt Volatility Funnel
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_type        text",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_signal      text",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_h3_level    numeric",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_stop_level  numeric",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_target      numeric",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_risk_reward numeric",
    "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_quality     integer",
]


def ensure_schema():
    """
    Idempotently apply any outstanding schema changes.
    Called once at the start of every session — takes <1 second when
    all columns already exist (Postgres short-circuits IF NOT EXISTS).
    Logs a warning to Slack if any statement fails.
    """
    import pg8000.native
    try:
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres",
            user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"],
            ssl_context=True
        )
        for sql in REQUIRED_SCHEMA:
            try:
                conn.run(sql)
            except Exception as e:
                log.warning(f"Schema statement failed: {sql[:60]} — {e}")
        conn.close()
        log.info("Schema self-heal: OK")
    except Exception as e:
        log.error(f"Schema self-heal could not connect: {e}")
        try:
            from notify import alert_system_error
            alert_system_error("STARTUP", "ensure_schema",
                               "Schema self-heal failed — DB unreachable", str(e))
        except Exception:
            pass


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_session.py <SESSION_NAME>")
        print("Sessions: AUS_OPEN AUS_MONITOR UK_OPEN UK_MONITOR US_OPEN")
        print("          SESSION_CLOSE WEEKEND_REVIEW PREMARKET_BRIEF POSITION_MONITOR")
        sys.exit(1)

    session = sys.argv[1].upper()
    log.info(f"Starting session: {session}")
    ensure_schema()

    if session in ("AUS_OPEN", "UK_OPEN", "US_OPEN"):
        run_session_open(session)
    elif session in ("AUS_MONITOR", "UK_MONITOR", "POSITION_MONITOR"):
        run_monitor(session)
    elif session == "US_MONITOR":
        from intraday_signals import run_us_monitor
        run_us_monitor(notify_slack=True)
    elif session == "SESSION_CLOSE":
        run_session_close()
    elif session == "WEEKEND_REVIEW":
        run_weekend_review()
    elif session == "PREMARKET_BRIEF":
        # Sunday pre-market scan — 24/7 instruments (crypto, gold, FX) before Asia open.
        # Instruments defined in config.SESSION_INSTRUMENTS["PREMARKET_BRIEF"].
        # HVF report (run_hvf_report.py) is run separately by the SKILL.md prompt.
        run_session_open(session)
    else:
        log.error(f"Unknown session: {session}")
        sys.exit(1)
