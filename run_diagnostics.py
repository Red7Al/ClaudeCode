# =============================================================================
# File:         run_diagnostics.py
# Author:       Alex Hind
# Created:      2026-06-03
#
# Description:
# -----------------------------------------------------------------------------
# Post-deployment diagnostics. Run this after every code change to confirm
# each layer of the signal stack is working correctly.
#
# Posts a clear ✅ / ❌ / ⚠ report to #claude-trading-alerts in Slack so you
# can see from your phone whether the deployment worked.
#
# What it tests:
#   Macro layer     VIX, DXY, FRED yield curve, SPX market stress
#   Options layer   call/put ratio for each test ticker (including ETF proxies
#                   for indices that have no direct options chains)
#   GEX layer       Gamma exposure computation (was broken with KeyError 'gamma')
#   Price action    PA score and verdict per test ticker
#   Signal stack    primary_count, confirmation_count, trade_signal per ticker
#   DB writes       signal_log INSERT and positions INSERT (test row, then rollback)
#   IG              Health check: connectivity, balance, open positions
#
# Usage:
#   python run_diagnostics.py                     # tests NVDA, SPX500, XAUUSD
#   DIAG_TICKERS=NVDA,SNDK,SPX500 python run_diagnostics.py
#
# GitHub Actions: trigger "Deployment Diagnostics" workflow manually from
#   the Actions tab. Always safe — never places real trades.
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-03  Alex Hind   Initial build. Tests macro, options, GEX,
#                                 price action, signal stack, DB writes, and
#                                 IG connectivity. Posts ✅/❌/⚠ report to
#                                 #claude-trading-alerts.
# 1.0.2   2026-06-07  Alex Hind   Sync signal_log test INSERT to production: add
#                                 director_cluster_strong (prod is now 45 cols, the
#                                 test was a stale 44). Verified all 45 columns exist
#                                 in the live schema and match scan_instrument().
# 1.0.1   2026-06-05  Alex Hind   Extended Slack coverage test: sends a probe
#                                 message to all 5 channels so the user can
#                                 confirm every webhook is wired correctly
#                                 after deployment.
# =============================================================================

import os
import sys
import logging
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv; load_dotenv(override=True)

import requests as _requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("diagnostics")

# Primary channel for the diagnostic report
SLACK_URL  = os.environ.get("SLACK_ALERTS", "")

# All five channels — tested by test_slack_channels() to confirm each webhook works
SLACK_WEBHOOKS = {
    "SLACK_TRADES":  os.environ.get("SLACK_TRADES",  ""),
    "SLACK_SIGNALS": os.environ.get("SLACK_SIGNALS", ""),
    "SLACK_ALERTS":  os.environ.get("SLACK_ALERTS",  ""),
    "SLACK_WEEKLY":  os.environ.get("SLACK_WEEKLY",  ""),
    "SLACK_DAILY":   os.environ.get("SLACK_DAILY",   ""),
}

DIAG_TICKERS = [
    t.strip().upper()
    for t in os.environ.get("DIAG_TICKERS", "NVDA,SPX500,XAUUSD").split(",")
    if t.strip()
]


# =============================================================================
# Result helpers
# =============================================================================

def ok(name: str, value: str, detail: str = "") -> dict:
    return {"name": name, "status": "ok", "value": value, "detail": detail}

def fail(name: str, error: str, detail: str = "") -> dict:
    return {"name": name, "status": "fail", "value": error, "detail": detail}

def warn(name: str, value: str, detail: str = "") -> dict:
    return {"name": name, "status": "warn", "value": value, "detail": detail}


# =============================================================================
# Individual test functions
# =============================================================================

def test_vix() -> dict:
    try:
        import yfinance as yf
        v = float(yf.Ticker("^VIX").fast_info["lastPrice"])
        return ok("VIX", f"{v:.2f}")
    except Exception as e:
        return fail("VIX", str(e))


def test_dxy() -> dict:
    try:
        import yfinance as yf
        v = float(yf.Ticker("DX-Y.NYB").fast_info["lastPrice"])
        return ok("DXY", f"{v:.3f}")
    except Exception as e:
        return fail("DXY", str(e))


def test_fred_yield_curve() -> dict:
    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        return warn("FRED yield curve", "FRED_API_KEY not set")
    try:
        resp = _requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "DGS10", "api_key": key, "file_type": "json",
                    "sort_order": "desc", "limit": 2},
            timeout=10
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return fail("FRED yield curve", "No observations returned")
        return ok("FRED yield curve", f"DGS10={obs[0]['value']}%")
    except Exception as e:
        return fail("FRED yield curve", str(e))


def test_market_stress() -> dict:
    try:
        from signals import get_market_stress
        r = get_market_stress()
        level  = r["stress_level"]
        spx_ch = r.get("spx_change_pct")
        detail = f"SPX {spx_ch:+.2f}%" if spx_ch is not None else ""
        status = "ok" if level in ("NORMAL", "STRESS") else "warn"
        return {"name": "Market stress", "status": status,
                "value": level, "detail": detail}
    except Exception as e:
        return fail("Market stress", str(e))


def test_options_signal(ticker: str) -> dict:
    """Test that options signal returns a valid call_put_ratio (not None = data source works)."""
    try:
        from signals import get_options_signal
        from config import OPTIONS_PROXY_MAP
        r = get_options_signal(ticker)
        proxy = OPTIONS_PROXY_MAP.get(ticker)
        proxy_str = f" (proxy: {proxy})" if proxy else ""
        cpr   = r.get("call_put_ratio")
        bias  = r.get("options_bias", "NEUTRAL")
        if cpr is None:
            return warn(f"Options {ticker}{proxy_str}", "NEUTRAL — no options chain data",
                        "May be outside market hours or ticker not supported")
        return ok(f"Options {ticker}{proxy_str}",
                  f"call_put={cpr:.3f} → {bias}")
    except Exception as e:
        return fail(f"Options {ticker}", str(e))


def test_gex(ticker: str) -> dict:
    """Test GEX computation — previously always failed with KeyError 'gamma'."""
    try:
        from signals import get_gex_bias
        r = get_gex_bias(ticker)
        gex  = r.get("gex")
        bias = r.get("gex_bias", "NEUTRAL")
        if gex is None:
            return warn(f"GEX {ticker}", "No gamma data (market closed or ETF proxy lacks greeks)",
                        "GEX is a confirmation signal — OK to be absent outside US hours")
        return ok(f"GEX {ticker}", f"gex={gex:.1f} → {bias}")
    except Exception as e:
        return fail(f"GEX {ticker}", str(e))


def test_price_action(ticker: str) -> dict:
    try:
        from price_action import analyse_price_action
        r = analyse_price_action(ticker)
        verdict = r.get("verdict", "?")
        score   = r.get("pa_score", 0)
        hvf_str = ""
        if r.get("hvf_type"):
            hvf_str = (f" | HVF:{r['hvf_type']}({r.get('hvf_signal')}) "
                       f"H3={r.get('hvf_h3_level')} "
                       f"target={r.get('hvf_target')} "
                       f"R:R={r.get('hvf_risk_reward')} "
                       f"quality={r.get('hvf_quality')}")
        status  = "ok" if verdict in ("CONFIRM_LONG", "CONFIRM_SHORT", "WAIT") else "fail"
        return {"name": f"PA {ticker}", "status": status,
                "value": f"{verdict} {score:+.0f}{hvf_str}",
                "detail": f"breakout={r.get('range_breakout')} trend={r.get('trend_structure')} MA={r.get('ma_signal')}"}
    except Exception as e:
        return fail(f"PA {ticker}", str(e))


def test_full_scan(ticker: str) -> dict:
    """Run the complete scan_instrument() and report what fired."""
    try:
        from signals import scan_instrument, get_macro_gate
        macro = get_macro_gate("DIAG")
        sig   = scan_instrument(ticker, "DIAG", macro)

        pc     = sig.get("primary_count", 0)
        cc     = sig.get("confirmation_count", 0)
        fired  = sig.get("trade_signal", False)
        pa     = sig.get("pa_verdict", "?")
        dir_   = sig.get("direction", "—")
        opts   = sig.get("options_bias", "—")
        bb     = sig.get("bb_breakout_dir", "—")
        blocked = sig.get("intraday_blocked", False)

        if blocked:
            return warn(f"Scan {ticker}", f"INTRADAY BLOCKED: {sig.get('intraday_reason','')[:60]}")

        detail = (f"opts={opts} bb={bb} | "
                  f"PA={pa} | primaries={pc} confs={cc} dir={dir_}")

        if fired:
            return ok(f"Scan {ticker}", f"TRADE SIGNAL — {dir_}", detail)
        elif pc >= 1 and cc >= 1:
            return warn(f"Scan {ticker}", f"Close ({pc}P {cc}C) — PA blocked" if pa == "WAIT" else f"Close ({pc}P {cc}C)", detail)
        elif pc >= 1:
            return warn(f"Scan {ticker}", f"{pc} primary, needs 1 confirmation", detail)
        else:
            return ok(f"Scan {ticker}", f"No signal ({pc}P {cc}C)", detail)
    except Exception as e:
        return fail(f"Scan {ticker}", str(e)[:80], traceback.format_exc()[-200:])


def test_signal_log_insert() -> dict:
    """
    Write a test row to signal_log then delete it.

    IMPORTANT: this INSERT must stay in sync with the production INSERT in
    signals.py scan_instrument(). A column-count mismatch here gives a
    false-pass — the test looks green while production can still fail with
    a pg8000 '08P01 bind message supplies N params, requires M' error.
    Last verified against production INSERT: 2026-06-07 (45 columns, incl director_cluster_strong).
    """
    try:
        import pg8000.native
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        # 45-column INSERT — must exactly match signals.py scan_instrument() INSERT.
        # Run two tickers (_TEST_OIL_ commodity-style, _TEST_EQ_ equity-style) to
        # surface any instrument-type-specific binding bugs (OIL failed 2026-06-04).
        for test_ticker in ("_TEST_OIL_", "_TEST_EQ_"):
            conn.run(
                """insert into signal_log
                   (session, ticker, macro_gate_pass, options_bias, call_put_ratio, iv_rank,
                    gex_bias, vwap_position, cot_bias, bb_squeeze, bb_breakout_dir,
                    director_signal, director_cluster_strong, activist_signal, senate_signal, senate_senator,
                    elite_senate_primary, elite_senator_name, potus_primary,
                    notable_investor, social_mention, primary_count, confirmation_count,
                    direction, pa_verdict, pa_score, trade_triggered,
                    adx_signal, adx_dir, obv_signal, volume_signal, volume_ratio,
                    hvf_type, hvf_signal, hvf_h3_level, hvf_stop_level,
                    hvf_target, hvf_risk_reward, hvf_quality,
                    orb_signal, orb_dir, week52_signal, week52_dir,
                    sector_etf, sector_dir)
                   values (:v_session, :v_ticker, :v_mgp, :v_opts_bias, :v_call_put, :v_ivr,
                           :v_gex_bias, :v_vwap_pos, :v_cot_bias, :v_bb_squeeze, :v_bb_breakout,
                           :v_director, :v_dir_cluster, :v_activist, :v_senate, :v_senator_name,
                           :v_elite_senate, :v_elite_senator, :v_potus,
                           :v_notable, :v_social, :v_primaries, :v_confirms, :v_direction,
                           :v_pa_verdict, :v_pa_score, :v_triggered,
                           :v_adx, :v_adx_dir, :v_obv, :v_vol_sig, :v_vol_ratio,
                           :v_hvf_type, :v_hvf_sig, :v_hvf_h3, :v_hvf_stop,
                           :v_hvf_target, :v_hvf_rr, :v_hvf_quality,
                           :v_orb_sig, :v_orb_dir, :v_week52_sig, :v_week52_dir,
                           :v_sector_etf, :v_sector_dir)""",
                v_session="DIAG", v_ticker=test_ticker,
                v_mgp=True, v_opts_bias="NEUTRAL", v_call_put=None, v_ivr=None,
                v_gex_bias="NEUTRAL", v_vwap_pos=None, v_cot_bias="NEUTRAL",
                v_bb_squeeze=False, v_bb_breakout=None,
                v_director=False, v_dir_cluster=False, v_activist=False, v_senate=False, v_senator_name=None,
                v_elite_senate=False, v_elite_senator=None, v_potus=False,
                v_notable=None, v_social=None, v_primaries=0, v_confirms=0,
                v_direction=None, v_pa_verdict="WAIT", v_pa_score=None, v_triggered=False,
                v_adx="NEUTRAL", v_adx_dir=None, v_obv="NEUTRAL",
                v_vol_sig="NORMAL", v_vol_ratio=None,
                v_hvf_type=None, v_hvf_sig=None, v_hvf_h3=None, v_hvf_stop=None,
                v_hvf_target=None, v_hvf_rr=None, v_hvf_quality=None,
                v_orb_sig=None, v_orb_dir=None, v_week52_sig=None, v_week52_dir=None,
                v_sector_etf=None, v_sector_dir=None
            )
        # Clean up both test rows immediately
        conn.run("delete from signal_log where session = 'DIAG'")
        conn.close()
        return ok("signal_log INSERT", "✓ write + delete succeeded (44-col INSERT, 2 test tickers)")
    except Exception as e:
        return fail("signal_log INSERT", str(e)[:120])


def test_positions_insert() -> dict:
    """Write + rollback a test positions row. Verifies trade logging fix."""
    try:
        import pg8000.native
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        # Need a valid user_id — fetch first available
        rows = conn.run("select id from user_profiles limit 1")
        uid  = rows[0][0] if rows else "00000000-0000-0000-0000-000000000001"

        conn.run(
            """insert into positions
               (user_id, epic, ticker, direction, size, open_price,
                stop_loss, take_profit, deal_id, paper_trade, session, signal_summary)
               values (:v_uid, :v_epic, :v_ticker, :v_dir, :v_size, :v_open,
                       :v_stop, :v_tp, :v_deal, :v_paper, :v_session, :v_signal)""",
            v_uid=uid, v_epic="DIAG.TEST", v_ticker="_TEST_", v_dir="BUY", v_size=0.0,
            v_open=0.0, v_stop=0.0, v_tp=0.0,
            v_deal="DIAG-TEST-DELETE-ME", v_paper=True, v_session="DIAG",
            v_signal="diagnostics test row"
        )
        conn.run("delete from positions where deal_id = 'DIAG-TEST-DELETE-ME'")
        conn.close()
        return ok("positions INSERT", "✓ write + delete succeeded")
    except Exception as e:
        return fail("positions INSERT", str(e)[:120])


def test_ig_health() -> dict:
    try:
        from ig_shim import health_check
        r = health_check()
        if r["status"] == "OK":
            bal   = r.get("balance", "?")
            avail = r.get("available", "?")
            pos   = r.get("open_positions", 0)
            return ok("IG health", f"balance={bal} available={avail} positions={pos}")
        else:
            return fail("IG health", r.get("error", "Unknown"))
    except Exception as e:
        return fail("IG health", str(e))


# =============================================================================
# Slack report
# =============================================================================

def test_slack_channels() -> list:
    """
    Send a lightweight probe message to all 5 Slack channels.
    Returns one result dict per channel — confirms each webhook URL is set
    and returns HTTP 200. Run as part of diagnostics after any deployment.
    """
    ts  = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    results = []
    channel_names = {
        "SLACK_TRADES":  "#claude-trading-trades",
        "SLACK_SIGNALS": "#claude-trading-signals",
        "SLACK_ALERTS":  "#claude-trading-alerts",
        "SLACK_WEEKLY":  "#claude-trading-weekly",
        "SLACK_DAILY":   "#claude-trading-daily",
    }
    for secret_name, url in SLACK_WEBHOOKS.items():
        channel = channel_names[secret_name]
        name    = f"Slack {channel}"
        if not url:
            results.append(warn(name, "NOT SET — webhook URL missing from environment"))
            continue
        try:
            probe = {"text": f"🔬 Diagnostics probe — {ts} — {channel} webhook OK"}
            resp  = _requests.post(url, json=probe, timeout=10)
            if resp.status_code == 200:
                results.append(ok(name, "✅ Webhook responded 200"))
            else:
                results.append(fail(name, f"HTTP {resp.status_code} — {resp.text[:80]}"))
        except Exception as e:
            results.append(fail(name, str(e)))
    return results


def post_report(results: list, tickers: list):
    """Post test results to #claude-trading-alerts."""
    if not SLACK_URL:
        # Fall back to stdout
        print(f"\n{'='*60}")
        print(f"Deployment Diagnostics — {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
        print(f"Tickers: {', '.join(tickers)}")
        print(f"{'='*60}")
        for r in results:
            icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}.get(r["status"], "❓")
            print(f"{icon} {r['name']:<35} {r['value']}")
            if r.get("detail"):
                print(f"     {r['detail']}")
        n_fail = sum(1 for r in results if r["status"] == "fail")
        n_warn = sum(1 for r in results if r["status"] == "warn")
        n_ok   = sum(1 for r in results if r["status"] == "ok")
        print(f"\n{'='*60}")
        print(f"  ✅ {n_ok} passed   ⚠️  {n_warn} warnings   ❌ {n_fail} failures")
        return

    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_warn = sum(1 for r in results if r["status"] == "warn")
    n_ok   = sum(1 for r in results if r["status"] == "ok")

    overall_emoji = "✅" if n_fail == 0 else "❌"
    overall_text  = "All checks passed" if n_fail == 0 else f"{n_fail} failure(s) — action needed"

    # Build lines for each result
    lines = ""
    prev_group = None
    GROUP_PREFIXES = {
        "VIX": "Macro",
        "DXY": "Macro",
        "FRED": "Macro",
        "Market": "Macro",
        "Options": "Options",
        "GEX": "GEX",
        "PA ": "Price action",
        "Scan": "Signal stack",
        "signal_log": "DB writes",
        "positions": "DB writes",
        "IG": "IG",
        "Slack": "Slack channels",
    }
    for r in results:
        icon  = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(r["status"], "❓")
        group = next((v for k, v in GROUP_PREFIXES.items() if r["name"].startswith(k)), "Other")
        if group != prev_group:
            lines += f"\n*{group}*\n"
            prev_group = group
        detail_str = f"  `{r['detail']}`" if r.get("detail") else ""
        lines += f"{icon} {r['name']} — {r['value']}{detail_str}\n"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"🔬 Diagnostics {overall_emoji} — {overall_text}"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Tickers:* {', '.join(tickers)}  |  "
                    f"✅ {n_ok}  ⚠️ {n_warn}  ❌ {n_fail}\n{lines.strip()}"
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")}]
        },
    ]
    try:
        resp = _requests.post(SLACK_URL, json={"blocks": blocks}, timeout=10)
        if resp.status_code == 200:
            log.info("Diagnostics posted to Slack")
        else:
            log.error(f"Slack post failed: {resp.status_code}")
    except Exception as e:
        log.error(f"Slack post failed: {e}")


# =============================================================================
# Main
# =============================================================================

def main():
    tickers = DIAG_TICKERS
    log.info(f"Running diagnostics for: {', '.join(tickers)}")
    log.info("Never places real trades — read-only except for DB write tests (self-cleaning).")

    results = []

    # ── Macro layer ────────────────────────────────────────────────────────────
    results.append(test_vix())
    results.append(test_dxy())
    results.append(test_fred_yield_curve())
    results.append(test_market_stress())

    # ── Options layer (includes ETF proxy test for indices) ────────────────────
    for ticker in tickers:
        results.append(test_options_signal(ticker))

    # ── GEX (gamma fix verification) ─────────────────────────────────────────
    for ticker in tickers:
        results.append(test_gex(ticker))

    # ── Price action ─────────────────────────────────────────────────────────
    for ticker in tickers:
        results.append(test_price_action(ticker))

    # ── Full signal scan (primary_count, confirmation_count, trade_signal) ────
    for ticker in tickers:
        results.append(test_full_scan(ticker))

    # ── Database write tests ──────────────────────────────────────────────────
    results.append(test_signal_log_insert())
    results.append(test_positions_insert())

    # ── IG connectivity ───────────────────────────────────────────────────────
    results.append(test_ig_health())

    # ── Slack channel coverage (all 5 webhooks) ───────────────────────────────
    results.extend(test_slack_channels())

    # ── Report ────────────────────────────────────────────────────────────────
    post_report(results, tickers)

    # Exit 1 if any failures (useful for CI)
    n_fail = sum(1 for r in results if r["status"] == "fail")
    if n_fail:
        log.error(f"{n_fail} test(s) failed")
        sys.exit(1)
    else:
        log.info(f"All tests passed ({len(results)} checks)")


if __name__ == "__main__":
    main()
