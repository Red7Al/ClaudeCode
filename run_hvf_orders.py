# ======================================================================================================================
# File:         run_hvf_orders.py
# Author:       Alex Hind
# Created:      2026-06-19
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily HVF ORDERS publication (user 2026-06-19: "new slack-orders daily publication running RW-hvf-analysis").
# Runs the same HVF analysis pipeline as the daily report (scan_universe + categorise from run_hvf_report) but posts
# ONLY the actionable TRADEABLE setups — the ones at/near entry — to the #arw-claude-orders Slack channel via the
# SLACK_ORDERS webhook, framed as an actionable orders list. Developing/watch setups are intentionally excluded (they
# live in the #signals daily report).
#
# Reuses run_hvf_report's _tradeable_line renderer (single source of truth), so each order carries the live price,
# each level's % from price, R:R and the expected time-to-target — identical formatting to the daily report.
#
# Distinct from working_orders_report.py: that posts the engine-managed working_orders (already placed on IG); THIS
# posts fresh HVF analysis (candidate orders) to the same channel.
#
# Usage:
#   python run_hvf_orders.py
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD   (scan + DB log, inherited from run_hvf_report)
#   SLACK_ORDERS                          (#arw-claude-orders webhook)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-19  Alex Hind   Initial build — daily HVF orders publication to #arw-claude-orders. Reuses
#                                 run_hvf_report.scan_universe/categorise/_tradeable_line; tradeable-only; R:R-first,
#                                 per-market "top N of M candidates", numbered from 1.
# ======================================================================================================================

import os
import logging
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv(override=True)
import requests

from config import MARKET_ORDER, PER_MARKET_TOP_N, HVF_MIN_RR
from price_action import group_by_market

log = logging.getLogger("hvf_orders")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SLACK_ORDERS = os.environ.get("SLACK_ORDERS", "")


def build_orders_blocks(tradeable: list, scan_time: str) -> list:
    """Slack blocks for the actionable HVF orders list — tradeable setups only, grouped per
    market in R:R-first weight order, numbered from 1, each line carrying live price / % from
    price / R:R / time-to-target (via run_hvf_report._tradeable_line)."""
    from run_hvf_report import _tradeable_line, _index_short, _chunk_lines

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"HVF Orders — {scan_time}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f"*{len(tradeable)} actionable HVF setup(s)* (READY/TRIGGERED, R:R ≥ {HVF_MIN_RR:g}:1). "
                           f"Candidate orders from today's scan — not yet placed.")}},
        {"type": "divider"},
    ]
    if not tradeable:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "_No actionable HVF orders today._"}})
        return blocks

    totals = Counter(r.get("index") for r in tradeable)
    groups = group_by_market(tradeable, n=PER_MARKET_TOP_N, market_order=MARKET_ORDER)
    for market, rows in groups:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*{_index_short(market)}* — top {len(rows)} of "
                                        f"{totals.get(market, len(rows))} candidates"}})
        numbered = [f"{i}. {_tradeable_line(r)}" for i, r in enumerate(rows, 1)]
        for blk in _chunk_lines(numbered):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": blk}})

    blocks.append({"type": "divider"})
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn",
                                 "text": (f"Each line: live price, each level's % from price, R:R and the expected "
                                          f"time-to-target. Order = R:R first. Generated {scan_time} UTC.")}]})
    return blocks


def post_to_slack(blocks: list) -> bool:
    from notify import slack_enabled
    if not slack_enabled("orders"):
        return False   # Slack #orders channel disabled (user 2026-08-01)
    if not SLACK_ORDERS:
        log.warning("SLACK_ORDERS not set — printing orders to stdout instead (expected on a local run)")
        return False
    try:
        resp = requests.post(SLACK_ORDERS, json={"blocks": blocks}, timeout=15)
        if resp.status_code == 200:
            log.info("HVF orders posted to #arw-claude-orders")
            return True
        log.error(f"Slack post failed: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Slack post failed: {e}")
        return False


def main():
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log.info(f"HVF Orders publication starting — {scan_time} UTC")
    from run_hvf_report import scan_universe, categorise
    all_results = scan_universe()
    tradeable, _developing = categorise(all_results)
    log.info(f"Scan complete: {len(tradeable)} actionable (tradeable) setup(s)")
    blocks = build_orders_blocks(tradeable, scan_time)
    post_to_slack(blocks)


if __name__ == "__main__":
    main()
