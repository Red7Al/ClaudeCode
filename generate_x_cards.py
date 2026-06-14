# ======================================================================================================================
# File:         generate_x_cards.py
# Author:       Alex Hind
# Created:      2026-06-12
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Generate X (Twitter) post-card PNGs locally for today's tradeable HVF setups, saved to x_drafts\post_<TICKER>.png
# for manual cut-and-paste into X. Uses the SAME renderer as production Slack drafts
# (intraday_signals.render_x_post_card) so the local files are pixel-identical to what Slack receives.
#
# Use cases:
#   - Slack chart upload unavailable (e.g. bot token missing files:write scope — 2026-06-12)
#   - Regenerating a card for a specific instrument on demand
#
# Tickers come from today's hvf_scan_log rows (TRADEABLE = READY/TRIGGERED), re-scanned live via get_hvf_signal_mtf to
# recover the pivot dates/levels the funnel needs (the scan log stores levels only). Output is WEIGHT-ORDERED:
# TRIGGERED first, then quality desc (user 2026-06-12: all lists in weight order).
#
# Usage:
#   python generate_x_cards.py                 # top 10 of today's tradeable setups
#   python generate_x_cards.py 20              # top 20
#   python generate_x_cards.py NVDA HIK.L      # specific tickers only
#
# Environment Variables Required:
#   SUPABASE_USER / SUPABASE_DB_PASSWORD   (only when reading today's list from hvf_scan_log)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-14  Alex Hind   Code-review: _today_tradeable sort now uses price_action.hvf_weight() (single source of
#                                 truth for weight order). Behaviour identical for READY/TRIGGERED rows.
# 1.0.0   2026-06-12  Alex Hind   Initial build — local fallback while SLACK_BOT_TOKEN lacks files:write.
# ======================================================================================================================

import io
import os
import sys
import logging

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate_x_cards")

OUT_DIR = "x_drafts"


def _today_tradeable(limit: int) -> list:
    """Today's tradeable tickers from hvf_scan_log, weight-ordered (TRIGGERED first, quality desc)."""
    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run(
            """select distinct on (ticker) ticker, hvf_signal, pattern_quality
                 from hvf_scan_log
                where scan_time::date = current_date
                  and hvf_signal in ('READY', 'TRIGGERED')
                order by ticker, recorded_at desc""")
    finally:
        db.close()
    from price_action import hvf_weight          # (ticker, hvf_signal, pattern_quality)
    rows.sort(key=lambda r: hvf_weight(r[1], r[2]))
    return [r[0] for r in rows[:limit]]


def main():
    args = sys.argv[1:]
    if args and args[0].isdigit():
        tickers = _today_tradeable(int(args[0]))
    elif args:
        tickers = args
    else:
        tickers = _today_tradeable(10)

    if not tickers:
        log.info("No tradeable setups found for today — nothing to render.")
        return

    from price_action import get_hvf_signal_mtf, get_trend_structure
    from intraday_signals import render_x_post_card

    os.makedirs(OUT_DIR, exist_ok=True)
    ok = failed = 0
    for ticker in tickers:
        try:
            trend = get_trend_structure(ticker)
            r = get_hvf_signal_mtf(ticker, trend_hint=trend)
            if not r.get("hvf_type"):
                log.warning(f"{ticker}: no HVF pattern on re-scan — skipped")
                failed += 1
                continue
            r["ticker"] = ticker
            png = render_x_post_card(r)
            if not png:
                log.warning(f"{ticker}: card render failed")
                failed += 1
                continue
            path = os.path.join(OUT_DIR, f"post_{ticker.replace('.', '_')}.png")
            with open(path, "wb") as f:
                f.write(png)
            log.info(f"{ticker}: saved {path} ({len(png):,} bytes)")
            ok += 1
        except Exception as e:
            log.warning(f"{ticker}: failed — {e}")
            failed += 1

    log.info(f"Done: {ok} cards saved to {OUT_DIR}\\, {failed} failed")


if __name__ == "__main__":
    main()
