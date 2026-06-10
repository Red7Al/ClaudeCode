# =============================================================================
# File:         run_hvf_report.py
# Author:       Alex Hind
# Created:      2026-06-05
#
# Description:
# -----------------------------------------------------------------------------
# Daily HVF Report — scans FTSE 100, FTSE 250 and S&P 500 constituents
# for Hunt Volatility Funnel patterns and posts a structured report to Slack.
#
# Three sections:
#   READY/TRIGGERED  — patterns meeting ≥2.5:1 R:R, tradeable now
#   DEVELOPING       — valid pattern structure, R:R < 2.5:1, watch list
#   IN PLAY          — patterns that have TRIGGERED and are open/active
#
# Multi-timeframe scanner (daily-220, daily-90, daily-60, daily-30, weekly):
#   Shorter timeframes catch post-peak reversals and tight recent funnels
#   that the standard 220-day lookback misses.
#
# Usage:
#   python run_hvf_report.py
#
# Or trigger via GitHub Actions: "HVF Daily Report" workflow.
#
# Environment Variables:
#   SLACK_SIGNALS (webhook URL for #claude-trading-signals)
#   SUPABASE_USER, SUPABASE_DB_PASSWORD (for logging results)
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-05  Alex Hind   Initial build. Multi-timeframe HVF scanner
#                                 covering FTSE 100, FTSE 250, S&P 500 and DAX.
# 1.0.2   2026-06-05  Alex Hind   Removed DAX (Germany) from UNIVERSE scan.
#                                 DAX suspended until further notice.
# 1.0.3   2026-06-05  Alex Hind   Fixed stale 2.0/2:1 references in categorise()
#                                 docstring and Slack summary block — now 2.5.
#                                 Results logged to hvf_scan_log table.
# 1.0.1   2026-06-05  Alex Hind   Raised tradeable filter to rr >= 2.5 and
#                                 updated footer label and section descriptions
#                                 to reflect new 2.5:1 threshold.
# =============================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
import logging
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(override=True)

from config import HVF_MIN_RR     # single source of truth for the R:R threshold
from notify import fmt            # 'TICKER (Full Name)' for every instrument shown

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hvf_report")

# ---------------------------------------------------------------------------
# Universe definitions
# ---------------------------------------------------------------------------

FTSE100 = [
    # Financials
    "HSBA.L", "BARC.L", "LLOY.L", "NWG.L", "STAN.L", "PRU.L", "AV.L",
    "LGEN.L", "MNG.L", "LSEG.L", "BEZ.L", "HSX.L", "ADM.L",
    # Energy
    "BP.L", "SHEL.L",
    # Mining / Materials
    "RIO.L", "AAL.L", "GLEN.L", "ANTO.L", "FRES.L", "MNDI.L",
    # Pharma / Healthcare
    "AZN.L", "GSK.L", "HLN.L", "SN..L", "HIK.L", "RKT.L",
    # Consumer / Retail
    "ULVR.L", "DGE.L", "NXT.L", "MKS.L", "BRBY.L", "WTB.L", "REL.L",
    # Tech / Telecoms
    "SAGE.L", "VOD.L", "BT-A.L", "AUTO.L", "RMV.L", "EXPN.L",
    # Industrials / Defence
    "BA..L", "RR.L", "WEIR.L", "SMIN.L", "IMI.L", "HLMA.L",
    "CPG.L", "ITRK.L", "BNZL.L", "INF.L", "WPP.L",
    # Housebuilders / Real estate
    "PSN.L", "TW..L", "BDEV.L", "SGRO.L", "LAND.L", "BLND.L",
    # Utilities
    "NG..L", "SSE.L", "SVT.L", "UU..L", "CNA.L",
    # Travel / Leisure
    "IHG.L", "IAG.L", "EZJ.L",
    # Tobacco / FMCG
    "IMB.L", "BATS.L",
    # Other
    "CRDA.L", "RS1.L", "MRO.L", "CCH.L",
]

FTSE250 = [
    # Financials / Asset managers
    "JUP.L", "MAN.L", "ABDN.L", "ICG.L", "3IN.L", "HICL.L", "INPP.L",
    # Housebuilders
    "BWY.L", "MCS.L", "CLDN.L",
    # Retail / Consumer
    "JD..L", "SPD.L", "OCDO.L", "PETS.L", "THG.L",
    # Media / Tech
    "FOUR.L", "AUTO.L", "JUST.L", "DPLM.L", "FDM.L",
    # Healthcare
    "HIK.L", "CTEC.L", "NXR.L",
    # Industrials
    "RWI.L", "CLLN.L", "SXS.L", "IMI.L", "HWDN.L",
    # Energy / Resources
    "ENQ.L", "TLW.L", "HBR.L",
    # Travel / Leisure
    "TUI.L", "SDR.L", "HOTC.L",
    # Real estate
    "HMSO.L", "PHP.L", "BBOX.L", "EBOX.L",
    # Food / Beverages
    "HFD.L", "BAKK.L",
]

SP500 = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "BLK", "AXP",
    # Healthcare
    "LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV", "TMO",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX",
    # Energy
    "XOM", "CVX", "COP",
    # Industrials
    "CAT", "DE", "HON", "RTX", "LMT", "GE",
    # Tech (broader)
    "AMD", "INTC", "ORCL", "CRM", "ADBE", "NOW", "PLTR", "CRWD",
    # Communication
    "NFLX", "DIS", "T", "VZ",
    # Materials / Utilities
    "LIN", "APD", "NEE", "SO",
]

UNIVERSE = {
    "FTSE 100":  FTSE100,
    "FTSE 250":  FTSE250,
    "S&P 500":   SP500,
    # DAX suspended 2026-06-05 — re-add when reinstated
}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_universe() -> dict:
    """
    Scan all instruments in all indices.
    Returns dict keyed by index name, each value a list of HVF result dicts.
    """
    from price_action import get_hvf_signal_mtf, get_trend_structure

    results = {}
    total   = sum(len(v) for v in UNIVERSE.values())
    done    = 0

    for index_name, tickers in UNIVERSE.items():
        index_results = []
        log.info(f"Scanning {index_name} ({len(tickers)} tickers)...")
        for ticker in tickers:
            try:
                trend = get_trend_structure(ticker)
                hvf   = get_hvf_signal_mtf(ticker, trend_hint=trend)
                if hvf.get("hvf_type"):
                    hvf["ticker"]     = ticker
                    hvf["index"]      = index_name
                    hvf["long_trend"] = trend.get("signal", "")
                    index_results.append(hvf)
                time.sleep(0.3)   # polite to Yahoo Finance
            except Exception as e:
                log.warning(f"  {ticker} failed: {e}")
            done += 1
            if done % 20 == 0:
                log.info(f"  {done}/{total} done")

        results[index_name] = index_results
        log.info(f"  {index_name}: {len(index_results)} patterns found")

    return results


# ---------------------------------------------------------------------------
# Categorise results
# ---------------------------------------------------------------------------

SIGNAL_RANK = {"TRIGGERED": 3, "READY": 2, "DEVELOPING": 1}


def categorise(all_results: dict) -> tuple:
    """
    Split into three report sections:
      tradeable  — READY or TRIGGERED with R:R >= 2.5
      developing — DEVELOPING (valid pattern, R:R < 2.5, watch list)
    Returns (tradeable, developing) — each a flat list sorted by quality.
    """
    tradeable  = []
    developing = []

    for index_name, results in all_results.items():
        for r in results:
            sig = r.get("hvf_signal", "")
            rr  = r.get("risk_reward") or 0
            if sig in ("READY", "TRIGGERED") and rr >= HVF_MIN_RR:
                tradeable.append(r)
            elif sig == "DEVELOPING":
                developing.append(r)

    tradeable.sort(key=lambda r: (
        SIGNAL_RANK.get(r.get("hvf_signal", ""), 0),
        r.get("pattern_quality", 0)
    ), reverse=True)

    developing.sort(key=lambda r: r.get("risk_reward") or 0, reverse=True)
    return tradeable, developing


# ---------------------------------------------------------------------------
# Slack report
# ---------------------------------------------------------------------------

def _fmt_price(p, suffix=""):
    """Format price with appropriate decimal places."""
    if p is None:
        return "-"
    return f"{p:,.1f}{suffix}"


def _rr(r):
    rr = r.get("risk_reward")
    return f"{rr:.1f}:1" if rr else "-"


def _dir_emoji(hvf_type):
    return "🟢" if hvf_type == "BULLISH" else "🔴"


def _signal_emoji(sig):
    return {"TRIGGERED": "⚡", "READY": "✅", "DEVELOPING": "👀"}.get(sig, "")


def build_slack_blocks(tradeable, developing, scan_time: str) -> list:
    """Build Slack Block Kit message for the daily HVF report."""
    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text",
                 "text": f"HVF Daily Report — {scan_time}"}
    })
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn",
                 "text": (f"*{len(tradeable)} tradeable* (READY/TRIGGERED ≥{HVF_MIN_RR}:1 R:R)  |  "
                          f"*{len(developing)} developing* (valid pattern, R:R < {HVF_MIN_RR}:1)\n"
                          f"Indices: FTSE 100 · FTSE 250 · S&P 500")}
    })
    blocks.append({"type": "divider"})

    # ── TRADEABLE ────────────────────────────────────────────────────────────
    if tradeable:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*⚡ TRADEABLE — {len(tradeable)} setups*"}
        })

        # Group by index
        from itertools import groupby
        tradeable_sorted = sorted(tradeable, key=lambda r: r.get("index", ""))
        for index_name, group in groupby(tradeable_sorted, key=lambda r: r.get("index", "")):
            group = list(group)
            lines = []
            for r in group:
                d  = _dir_emoji(r.get("hvf_type"))
                s  = _signal_emoji(r.get("hvf_signal"))
                t  = r.get("ticker", "")
                rr = _rr(r)
                tf = r.get("hvf_timeframe", "").replace("daily-", "d")
                entry  = _fmt_price(r.get("h3_level"))
                stop   = _fmt_price(r.get("stop_level"))
                target = _fmt_price(r.get("target"))
                q      = r.get("pattern_quality", 0)
                lines.append(
                    f"{d}{s} *{fmt(t)}*  R:R {rr}  Q={q}  [{tf}]\n"
                    f"    Entry {entry}  Stop {stop}  Target {target}"
                )

            # Slack limits block text to 3000 chars — chunk if needed
            chunk = "\n".join(lines)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*{index_name}*\n{chunk[:2900]}"}
            })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_No tradeable HVF setups found today._"}
        })

    blocks.append({"type": "divider"})

    # ── DEVELOPING ───────────────────────────────────────────────────────────
    if developing:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*👀 DEVELOPING — {len(developing)} on watch*"}
        })

        dev_sorted = sorted(developing, key=lambda r: r.get("index", ""))
        for index_name, group in groupby(dev_sorted, key=lambda r: r.get("index", "")):
            group = list(group)
            lines = []
            for r in group:
                d  = _dir_emoji(r.get("hvf_type"))
                t  = r.get("ticker", "")
                rr = _rr(r)
                tf = r.get("hvf_timeframe", "").replace("daily-", "d")
                entry  = _fmt_price(r.get("h3_level"))
                stop   = _fmt_price(r.get("stop_level"))
                target = _fmt_price(r.get("target"))
                lines.append(
                    f"{d}👀 *{fmt(t)}*  R:R {rr}  [{tf}]  "
                    f"Entry {entry}  Stop {stop}  Target {target}"
                )
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*{index_name}*\n" + "\n".join(lines[:2900])}
            })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_No developing patterns today._"}
        })

    # Footer
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                      "text": (f"HVF scanner: daily-30 · daily-60 · daily-90 · "
                               f"daily-220 · weekly | "
                               f"Min {HVF_MIN_RR}:1 R:R to trade | "
                               f"Generated {scan_time} UTC")}]
    })

    return blocks


def post_to_slack(blocks: list):
    import requests
    url = os.environ.get("SLACK_SIGNALS", "")
    if not url:
        log.warning("SLACK_SIGNALS not set — skipping Slack post")
        return
    resp = requests.post(url, json={"blocks": blocks}, timeout=15)
    if resp.status_code != 200:
        log.error(f"Slack post failed: {resp.status_code} {resp.text[:200]}")
    else:
        log.info("HVF report posted to #claude-trading-signals")


# ---------------------------------------------------------------------------
# DB logging
# ---------------------------------------------------------------------------

def log_to_db(tradeable: list, developing: list, scan_time: str):
    """
    Write a summary row to hvf_scan_log table (created by run_schema.py).
    Gracefully skips if table doesn't exist yet.
    """
    try:
        import pg8000.native
        conn = _pool_get_db()
        for r in tradeable + developing:
            conn.run(
                """insert into hvf_scan_log
                   (scan_time, ticker, index_name, hvf_type, hvf_signal,
                    hvf_timeframe, pattern_quality, risk_reward,
                    entry_level, stop_level, target)
                   values (:ts, :tk, :ix, :ht, :hs, :tf, :q, :rr, :el, :sl, :tg)
                   on conflict do nothing""",
                ts=scan_time, tk=r.get("ticker"), ix=r.get("index"),
                ht=r.get("hvf_type"), hs=r.get("hvf_signal"),
                tf=r.get("hvf_timeframe"),
                q=r.get("pattern_quality"), rr=r.get("risk_reward"),
                el=r.get("h3_level"), sl=r.get("stop_level"), tg=r.get("target")
            )
        conn.close()
        log.info(f"Logged {len(tradeable)+len(developing)} patterns to hvf_scan_log")
    except Exception as e:
        log.warning(f"DB log failed (non-critical): {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log.info(f"HVF Daily Report starting — {scan_time} UTC")

    all_results = scan_universe()
    tradeable, developing = categorise(all_results)

    # Summary to stdout
    total_patterns = sum(len(v) for v in all_results.values())
    log.info(f"Scan complete: {total_patterns} patterns found, "
             f"{len(tradeable)} tradeable, {len(developing)} developing")

    for r in tradeable:
        log.info(f"  TRADEABLE: {r['ticker']} {r['hvf_type']} {r['hvf_signal']} "
                 f"Q={r.get('pattern_quality')} R:R={r.get('risk_reward')} [{r.get('hvf_timeframe')}]")
    for r in developing:
        log.info(f"  DEVELOPING: {r['ticker']} {r['hvf_type']} "
                 f"R:R={r.get('risk_reward')} [{r.get('hvf_timeframe')}]")

    # Post to Slack
    blocks = build_slack_blocks(tradeable, developing, scan_time)
    post_to_slack(blocks)

    # X draft reports — one tweet-ready Slack post per tradeable instrument
    if tradeable:
        try:
            from intraday_signals import _generate_x_drafts
            _generate_x_drafts(tradeable)
        except Exception as e:
            log.warning(f"X draft generation failed (non-critical): {e}")

    # Log to DB
    log_to_db(tradeable, developing, scan_time)

    log.info("HVF Daily Report complete.")


if __name__ == "__main__":
    main()
