# =============================================================================
# File:         run_uk_morning_brief.py
# Author:       Alex Hind
# Created:      2026-06-03
#
# Description:
# -----------------------------------------------------------------------------
# 9am UTC Monday & Friday brief covering the UK session so far (07:00–09:00 UTC).
# Posts to #claude-trading-signals via SLACK_SIGNALS webhook.
#
# Covers:
#   - Macro conditions from today's UK_OPEN snapshot
#   - Every instrument scanned — what signals fired and why it did/didn't trade
#   - Trades placed this session (if any)
#   - Current open positions
#
# Usage:
#   python run_uk_morning_brief.py
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD, SLACK_SIGNALS
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv()
import logging
import requests
import pg8000.native
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("uk_morning_brief")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SLACK_URL     = os.environ.get("SLACK_DAILY", "")
WINDOW_HOURS  = 2      # look back 2 hours from now


def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        ssl_context=True
    )


def post_slack(blocks: list):
    if not SLACK_URL:
        log.warning("SLACK_SIGNALS not set — printing to stdout instead")
        for b in blocks:
            if b.get("type") == "section":
                txt = b.get("text", {}).get("text", "")
                print(txt)
            elif b.get("type") == "header":
                print(f"\n=== {b['text']['text']} ===")
        return
    resp = requests.post(SLACK_URL, json={"blocks": blocks}, timeout=10)
    if resp.status_code != 200:
        log.error(f"Slack post failed: {resp.status_code} {resp.text}")


def main():
    now = datetime.now(timezone.utc)

    # Allow a fixed window via env vars — e.g. for replaying a past session
    # BRIEF_START_UTC = "2026-06-03 07:00:00"
    # BRIEF_END_UTC   = "2026-06-03 11:00:00"
    start_env = os.environ.get("BRIEF_START_UTC", "")
    end_env   = os.environ.get("BRIEF_END_UTC",   "")

    if start_env and end_env:
        since    = datetime.strptime(start_env, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        until    = datetime.strptime(end_env,   "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        since_ts = start_env
        until_ts = end_env
        today    = since.strftime("%Y-%m-%d")
        day_name = since.strftime("%A %d %b") + f" ({start_env[11:16]}–{end_env[11:16]} UTC)"
        log.info(f"Building UK brief for fixed window: {start_env} → {end_env}")
    else:
        since    = now - timedelta(hours=WINDOW_HOURS)
        until    = now
        since_ts = since.strftime("%Y-%m-%d %H:%M:%S")
        until_ts = now.strftime("%Y-%m-%d %H:%M:%S")
        today    = now.strftime("%Y-%m-%d")
        day_name = now.strftime("%A %d %b")
        log.info(f"Building UK morning brief for {day_name} (window: last {WINDOW_HOURS}h)")

    db = get_db()

    # ── Macro snapshot ────────────────────────────────────────────────────────
    macro_rows = db.run(
        """select vix, dxy, yield_spread, macro_gate_pass, gate_reason, snapshot_time
           from   macro_snapshot
           where  session in ('UK_OPEN', 'UK_MONITOR')
             and  snapshot_time >= :s
             and  snapshot_time <= :u
           order  by snapshot_time desc
           limit  1""",
        s=since_ts, u=until_ts
    )
    macro = {}
    if macro_rows:
        macro = {
            "vix":           float(macro_rows[0][0] or 0),
            "dxy":           float(macro_rows[0][1] or 0),
            "yield_spread":  float(macro_rows[0][2] or 0),
            "gate_pass":     macro_rows[0][3],
            "gate_reason":   macro_rows[0][4] or "All macro conditions normal",
            "snapshot_time": macro_rows[0][5],
        }

    # ── Signal log for window ─────────────────────────────────────────────────
    signal_rows = db.run(
        """select ticker, session, primary_count, confirmation_count,
                  direction, trade_triggered, pa_verdict,
                  options_bias, bb_breakout_dir, cot_bias, adx_signal, volume_signal,
                  director_signal, senate_signal, notable_investor, social_mention,
                  session_time
           from   signal_log
           where  session_time >= :s
             and  session_time <= :u
           order  by primary_count desc nulls last,
                     confirmation_count desc nulls last,
                     session_time""",
        s=since_ts, u=until_ts
    )

    # ── Trades opened in window ───────────────────────────────────────────────
    trade_rows = db.run(
        """select ticker, direction, size, open_price, stop_loss, session, signal_summary, opened_at
           from   positions
           where  opened_at >= :s
             and  opened_at <= :u
           order  by opened_at""",
        s=since_ts, u=until_ts
    )

    # ── All open positions ────────────────────────────────────────────────────
    pos_rows = db.run(
        """select ticker, direction, size, open_price, stop_loss, session, opened_at
           from   positions
           order  by opened_at"""
    )

    db.close()

    # ── Build Slack blocks ────────────────────────────────────────────────────
    blocks = []

    # Header
    gate_emoji = "✅" if macro.get("gate_pass") else "🚫"
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"🇬🇧 UK Morning Brief — {day_name}"}
    })

    # Macro
    if macro:
        vix    = macro["vix"]
        spread = macro["yield_spread"]
        dxy    = macro["dxy"]
        vix_label = (
            "calm 😴" if vix < 15 else
            "normal" if vix < 20 else
            "slightly elevated ⚠️" if vix < 25 else
            "elevated 🚨"
        )
        spread_label = (
            "normal ✅" if spread >= 0.3 else
            "flat ⚠️"   if spread >= 0 else
            "inverted 🚨"
        )
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Macro Gate:*\n{gate_emoji} {macro['gate_reason']}"},
                {"type": "mrkdwn", "text": f"*VIX:*\n{vix} — {vix_label}"},
                {"type": "mrkdwn", "text": f"*Yield Spread:*\n{spread:+.2f}% — {spread_label}"},
                {"type": "mrkdwn", "text": f"*DXY:*\n{dxy}"},
            ]
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No macro snapshot found for UK session today_"}
        })

    blocks.append({"type": "divider"})

    # Signal scan results
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn",
                 "text": f"*Signal Scan — last {WINDOW_HOURS}h ({len(signal_rows)} instruments)*"}
    })

    if signal_rows:
        scan_lines = ""
        for r in signal_rows:
            (ticker, session, primaries, confs, direction, triggered,
             pa_verdict, options_bias, bb_dir, cot_bias, adx_signal, vol_signal,
             director, senate, notable_inv, social, scan_time) = r

            primaries = primaries or 0
            confs     = confs or 0

            if triggered:
                status = "🟢 *TRADE*"
            elif primaries >= 2:
                status = "🟡 close"
            elif primaries == 1:
                status = "🔸 1 primary"
            else:
                status = "⚪ no signal"

            dir_str = f" → *{direction}*" if direction else ""
            pa_str  = f" PA:{pa_verdict}" if pa_verdict and pa_verdict != "WAIT" else ""

            fired = []
            if options_bias and options_bias != "NEUTRAL": fired.append(f"Options:{options_bias}")
            if bb_dir:        fired.append(f"BB:{bb_dir}")
            if adx_signal and adx_signal == "STRONG_TREND": fired.append("ADX:STRONG")
            if vol_signal and vol_signal == "HIGH_VOLUME":  fired.append("Vol:HIGH")
            if cot_bias and cot_bias not in ("NEUTRAL", None): fired.append(f"COT:{cot_bias}")
            if director:      fired.append("DirectorBuy✓")
            if senate:        fired.append("Senate✓")
            if notable_inv:   fired.append(f"Investor:{str(notable_inv)[:15]}")
            if social:        fired.append("Social✓")

            signals_str = "  `" + "  |  ".join(fired) + "`" if fired else ""
            scan_lines += (
                f"{status}  *{ticker}*{dir_str}  "
                f"_{primaries}P {confs}C{pa_str}_\n"
                f"{signals_str}\n"
            )

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": scan_lines.strip()}
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_No instruments scanned in this window — UK_OPEN may not have run yet_"}
        })

    blocks.append({"type": "divider"})

    # Trades placed in window
    if trade_rows:
        trade_lines = f"*Trades Placed This Session — {len(trade_rows)}*\n"
        for r in trade_rows:
            ticker, direction, size, open_price, stop_loss, session, sig_summary, opened_at = r
            emoji = "🟢" if direction == "BUY" else "🔴"
            trade_lines += (
                f"{emoji} *{ticker}* {direction}  size:{size}  "
                f"entry:{open_price}  SL:{stop_loss}\n"
                f"  _{sig_summary}_\n"
            )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": trade_lines.strip()}
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Trades Placed This Session — 0*\n_No trades placed_"}
        })

    # Open positions
    if pos_rows:
        pos_lines = f"*Open Positions — {len(pos_rows)}*\n"
        for r in pos_rows:
            ticker, direction, size, open_price, stop_loss, session, opened_at = r
            emoji = "🟢" if direction == "BUY" else "🔴"
            pos_lines += (
                f"{emoji} *{ticker}* {direction}  "
                f"size:{size}  entry:{open_price}  SL:{stop_loss}  [{session}]\n"
            )
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": pos_lines.strip()}
        })

    # Footer
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                      "text": f"EndToEndTrading | UK Morning Brief | "
                              f"{now.strftime('%d %b %Y %H:%M UTC')}"}]
    })

    post_slack(blocks)
    log.info("UK morning brief sent")


if __name__ == "__main__":
    main()
