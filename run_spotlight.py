# =============================================================================
# File:         run_spotlight.py
# Author:       Alex Hind
# Created:      2026-06-03
#
# Description:
# -----------------------------------------------------------------------------
# On-demand ticker spotlight — queries signal_log, notable_investors and
# social_mentions for one or more tickers and posts a summary to Slack.
#
# Usage:
#   SPOTLIGHT_TICKER=PLTR,DELL,NBIS python run_spotlight.py
#
# Or trigger via the "Spotlight" GitHub Actions workflow.
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD, SLACK_SIGNALS
#   SPOTLIGHT_TICKER  — comma-separated tickers (e.g. "PLTR,DELL,NBIS")
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import requests
import pg8000.native
from datetime import datetime, timezone

from notify import fmt   # 'TICKER (Full Name)' for every instrument shown

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("spotlight")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SLACK_URL     = os.environ.get("SLACK_SIGNALS", "")


def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        ssl_context=True
    )


def spotlight_ticker(conn, ticker: str) -> dict:
    """Pull all available data for one ticker from the DB."""

    # Signal log — most recent 10 scans
    sig_rows = conn.run(
        """select session, primary_count, confirmation_count, direction,
                  options_bias, bb_breakout_dir, cot_bias,
                  trade_triggered, pa_verdict, session_time
           from   signal_log
           where  ticker = :t
           order  by session_time desc
           limit  10""",
        t=ticker
    )

    # Notable investors / social picks
    ni_rows = conn.run(
        """select investor_name, action, disclosed_at, source, notes
           from   notable_investors
           where  ticker = :t
           order  by disclosed_at desc
           limit  10""",
        t=ticker
    )

    # Social mentions
    sm_rows = conn.run(
        """select author, platform, sentiment, post_time, post_text
           from   social_mentions
           where  :t = any(tickers_found)
           order  by post_time desc
           limit  5""",
        t=ticker
    )

    # IG epic
    epic_rows = conn.run(
        """select epic, description from epic_lookup where ticker = :t limit 1""",
        t=ticker
    )

    return {
        "ticker":    ticker,
        "signals":   sig_rows,
        "investors": ni_rows,
        "social":    sm_rows,
        "epic":      epic_rows[0] if epic_rows else None,
    }


def build_blocks(results: list) -> list:
    """Build Slack Block Kit blocks for all spotlighted tickers."""
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    tickers_str = ", ".join(r["ticker"] for r in results)

    blocks = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"🔍 Ticker Spotlight — {tickers_str}"}
    }]

    for data in results:
        ticker  = data["ticker"]
        epic    = data["epic"]
        signals = data["signals"]
        investors = data["investors"]
        social  = data["social"]

        epic_str = f"  IG: `{epic[0]}`  _{epic[1]}_" if epic else "  IG epic: not in lookup table"

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{fmt(ticker)}*\n{epic_str}"}
        })

        # Signal scan history
        if signals:
            best = signals[0]  # already sorted desc by session_time
            primaries = best[1] or 0
            confs     = best[2] or 0
            direction = best[3]
            triggered = best[7]

            if triggered:
                verdict = "🟢 *TRADE TRIGGERED* on last scan"
            elif primaries >= 2 and confs >= 1:
                verdict = "🟡 *Very close* — 2+ primaries AND confirmation on last scan"
            elif primaries >= 2:
                verdict = "🟡 *2 primaries* on last scan — needs 1 confirmation to fire"
            elif primaries == 1:
                verdict = "🔸 *1 primary signal* on last scan — 1 short"
            else:
                verdict = "⚪ No primary signals on last scan"

            scan_lines = ""
            for r in signals[:5]:
                p, c  = r[1] or 0, r[2] or 0
                fired = "🟢" if r[7] else ("🟡" if p >= 2 else ("🔸" if p == 1 else "⚪"))
                scan_lines += (
                    f"{fired} `{str(r[9])[:10]}` [{r[0]}]  "
                    f"P:{p} C:{c} dir:{r[3] or '—'}  "
                    f"opts:{r[4] or '—'} bb:{r[5] or '—'} cot:{r[6] or '—'}"
                    f"  pa:{r[8] or '—'}\n"
                )

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*Signal history ({len(signals)} scans):*\n{verdict}\n\n{scan_lines.strip()}"}
            })
        else:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": "*Signal history:* ⚪ No scan data yet "
                                 "— will appear after next session scan"}
            })

        # Notable investors / social picks
        inv_lines = ""
        for r in investors:
            inv_lines += f"• {r[2]}  *{r[0]}*  `{r[1]}`  [{r[3]}]  _{str(r[4] or '')[:50]}_\n"

        sm_lines = ""
        for r in social:
            sm_lines += f"• `{str(r[3])[:10]}`  @{r[0]} ({r[2] or '—'})  _{str(r[4])[:80]}_\n"

        intel_text = ""
        if inv_lines:
            intel_text += f"*Investors/picks ({len(investors)}):*\n{inv_lines}"
        else:
            intel_text += "*Investors/picks:* none on record\n"

        if sm_lines:
            intel_text += f"\n*Social mentions ({len(social)}):*\n{sm_lines}"
        else:
            intel_text += "\n*Social mentions:* none yet — picks up on next hourly scan"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": intel_text.strip()}
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                      "text": f"EndToEndTrading Spotlight | {now}"}]
    })
    return blocks


def main():
    ticker_env = os.environ.get("SPOTLIGHT_TICKER", "")
    tickers    = [t.strip().upper() for t in ticker_env.split(",") if t.strip()]

    if not tickers:
        log.error("No tickers specified. Set SPOTLIGHT_TICKER env var.")
        return

    log.info(f"Spotlight: {', '.join(tickers)}")

    conn    = get_db()
    results = [spotlight_ticker(conn, t) for t in tickers]
    conn.close()

    blocks = build_blocks(results)

    if SLACK_URL:
        resp = requests.post(SLACK_URL, json={"blocks": blocks}, timeout=10)
        if resp.status_code == 200:
            log.info("Spotlight posted to Slack")
        else:
            log.error(f"Slack post failed: {resp.status_code} {resp.text}")
    else:
        # Print summary to stdout if no Slack URL
        for data in results:
            t = data["ticker"]
            print(f"\n{'='*50}")
            print(f"  {t}  |  signals:{len(data['signals'])}  "
                  f"investors:{len(data['investors'])}  social:{len(data['social'])}")
            if data["signals"]:
                r = data["signals"][0]
                print(f"  Last scan: P:{r[1]} C:{r[2]} dir:{r[3]} triggered:{r[7]}")


if __name__ == "__main__":
    main()
