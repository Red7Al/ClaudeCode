# ======================================================================================================================
# File:         run_data_quality_audit.py
# Author:       Alex Hind
# Created:      2026-06-12
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Nightly Yahoo-vs-IG price data audit (user 2026-06-12: "Consider using IG to review yahoo data on a regular basis
# each evening"). Yahoo's LSE feed contains phantom prints (RR.L fake 1,420 high vs IG's real 1,345.9 — caused a
# genuine HVF to be missed) and the epic search once mapped LAND.L to the wrong company entirely. This audit is the
# regression net for both failure classes:
#
#   1. CLOSE deviation  — Yahoo close vs IG close per day. A large deviation (>2%) means the ticker is mapped to the
#                         WRONG INSTRUMENT or wrong currency — the most dangerous data failure possible.
#   2. Phantom wicks    — Yahoo High above IG High (or Low below IG Low) by >1%: bad exchange prints that poison
#                         pivot detection. Counted per ticker over the comparison window.
#
# Coverage & budget: rotates through the UK universe (FTSE 100 + 250 — Yahoo's US feed is clean), auditing the
# least-recently-audited tickers first, ~30 daily candles each. The IG historical-price allowance is 10,000
# points/week shared with HVF validation, so the audit self-throttles: it only spends allowance above a 5,000-point
# reserve and caps each night's batch. Full UK universe coverage takes ~2 weeks per rotation.
#
# Output: rows upserted to data_quality_log; Slack summary in WEIGHT order (CRITICAL mismatches first, then by
# phantom-wick count) — CRITICAL findings go to #alerts, otherwise a digest goes to #signals.
#
# Usage:
#   python run_data_quality_audit.py            # auto batch size from allowance
#   python run_data_quality_audit.py 10         # cap batch at 10 tickers
#   python run_data_quality_audit.py RR.L BP.L  # audit specific tickers
#
# Environment Variables Required:
#   IG_API_KEY / IG_USERNAME / IG_PASSWORD / IG_ACCOUNT_ID
#   SUPABASE_USER / SUPABASE_DB_PASSWORD
#   SLACK_ALERTS, SLACK_SIGNALS
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-12  Alex Hind   Initial build — nightly rotation audit, allowance-aware, CRITICAL → #alerts.
# ======================================================================================================================

import io
import os
import sys
import logging
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("data_quality_audit")

COMPARE_DAYS      = 30      # daily candles compared per ticker
ALLOWANCE_RESERVE = 5000    # never audit below this remaining IG allowance
MAX_BATCH         = 25      # hard cap per night
CLOSE_CRITICAL    = 0.02    # >2% close deviation = wrong instrument / currency
WICK_TOLERANCE    = 0.01    # Yahoo extreme beyond IG extreme by >1% = phantom


def _uk_universe() -> list:
    from run_hvf_report import FTSE100, FTSE250
    return FTSE100 + FTSE250


def _pick_batch(limit: int) -> list:
    """Least-recently-audited UK tickers first (never-audited before all others)."""
    from db_pool import get_db
    universe = _uk_universe()
    db = get_db()
    try:
        rows = db.run("select ticker, max(audit_date) from data_quality_log group by ticker")
    finally:
        db.close()
    last = {r[0]: r[1] for r in rows}
    ordered = sorted(universe, key=lambda t: (t in last, last.get(t) or ""))
    return ordered[:limit]


def _audit_ticker(ticker: str) -> dict:
    """Compare Yahoo vs IG daily candles for one ticker. Returns the audit row dict."""
    import yfinance as yf
    from ig_shim import get_epic, get_prices_df

    row = {"ticker": ticker, "days_compared": 0, "close_max_dev_pct": None,
           "phantom_high_wicks": 0, "phantom_low_wicks": 0,
           "verdict": "NO_IG_DATA", "detail": "", "remaining": None}

    epic = get_epic(ticker)
    if not epic:
        row["verdict"] = "NO_EPIC"
        row["detail"]  = "ticker has no IG epic (not tradeable on IG, or UK epic refused)"
        return row

    ig_df, remaining = get_prices_df(epic, resolution="DAY", count=COMPARE_DAYS)
    row["remaining"] = remaining
    if ig_df.empty:
        return row

    y_df = yf.Ticker(ticker).history(period=f"{COMPARE_DAYS + 10}d", interval="1d")
    if y_df is None or y_df.empty:
        row["verdict"] = "NO_YAHOO_DATA"
        return row
    y_df = y_df.copy()
    y_df.index = y_df.index.tz_localize(None).normalize()

    # ── Scale normalisation: IG quotes US shares in CENTS and some instruments
    # in different units (NVDA: IG 20,100 vs Yahoo $201 → a fake "99% deviation").
    # Snap the median close ratio to the nearest power of 10 and rescale Yahoo
    # before comparing; only residual deviation is a real data problem.
    scale_note = ""
    overlap = [d for d in ig_df.index if d in y_df.index]
    if overlap:
        ig_med = float(ig_df.loc[overlap, "Close"].median())
        y_med  = float(y_df.loc[overlap, "Close"].median())
        if ig_med > 0 and y_med > 0:
            import math
            ratio = ig_med / y_med
            snapped = 10 ** round(math.log10(ratio))
            if snapped != 1:
                y_df[["Open", "High", "Low", "Close"]] *= snapped
                scale_note = f"(Yahoo rescaled ×{snapped:g} — IG quotes different units) "

    worst_close, worst_close_day = 0.0, None
    examples = []
    n = 0
    for dt, ig in ig_df.iterrows():
        if dt not in y_df.index:
            continue
        y = y_df.loc[dt]
        n += 1
        if ig["Close"] > 0:
            dev = abs(float(y["Close"]) - float(ig["Close"])) / float(ig["Close"])
            if dev > worst_close:
                worst_close, worst_close_day = dev, dt.date()
        if float(ig["High"]) > 0 and float(y["High"]) > float(ig["High"]) * (1 + WICK_TOLERANCE):
            row["phantom_high_wicks"] += 1
            examples.append(f"{dt.date()} high {float(y['High']):.1f} (Yahoo) vs {float(ig['High']):.1f} (IG)")
        if float(ig["Low"]) > 0 and float(y["Low"]) < float(ig["Low"]) * (1 - WICK_TOLERANCE):
            row["phantom_low_wicks"] += 1
            examples.append(f"{dt.date()} low {float(y['Low']):.1f} (Yahoo) vs {float(ig['Low']):.1f} (IG)")

    row["days_compared"]     = n
    row["close_max_dev_pct"] = round(worst_close * 100, 3)

    if n == 0:
        row["verdict"] = "NO_OVERLAP"
    elif worst_close > CLOSE_CRITICAL:
        row["verdict"] = "CRITICAL_MISMATCH"
        row["detail"]  = scale_note + (f"closes deviate up to {worst_close*100:.1f}% (worst {worst_close_day}) — "
                                       f"Yahoo and IG may be quoting DIFFERENT INSTRUMENTS or currencies")
    elif row["phantom_high_wicks"] + row["phantom_low_wicks"] > 0:
        row["verdict"] = "PHANTOM_WICKS"
        row["detail"]  = scale_note + "; ".join(examples[:4])
    else:
        row["verdict"] = "OK"
        row["detail"]  = scale_note.strip()
    return row


def _save(rows: list):
    from db_pool import get_db
    db = get_db()
    try:
        for r in rows:
            db.run(
                """insert into data_quality_log
                       (audit_date, ticker, days_compared, close_max_dev_pct,
                        phantom_high_wicks, phantom_low_wicks, verdict, detail)
                   values (current_date, :t, :n, :c, :ph, :pl, :v, :d)
                   on conflict (audit_date, ticker) do update
                   set days_compared = excluded.days_compared,
                       close_max_dev_pct = excluded.close_max_dev_pct,
                       phantom_high_wicks = excluded.phantom_high_wicks,
                       phantom_low_wicks = excluded.phantom_low_wicks,
                       verdict = excluded.verdict, detail = excluded.detail""",
                t=r["ticker"], n=r["days_compared"], c=r["close_max_dev_pct"],
                ph=r["phantom_high_wicks"], pl=r["phantom_low_wicks"],
                v=r["verdict"], d=r["detail"][:500])
    finally:
        db.close()


def _post_slack(rows: list, remaining):
    """Weight order: CRITICAL first, then by phantom count desc. CRITICAL → #alerts, else digest → #signals."""
    import requests
    from notify import fmt

    rank = {"CRITICAL_MISMATCH": 0, "PHANTOM_WICKS": 1, "NO_OVERLAP": 2,
            "NO_IG_DATA": 3, "NO_YAHOO_DATA": 3, "NO_EPIC": 4, "OK": 5}
    rows = sorted(rows, key=lambda r: (rank.get(r["verdict"], 9),
                                       -(r["phantom_high_wicks"] + r["phantom_low_wicks"])))
    critical = [r for r in rows if r["verdict"] == "CRITICAL_MISMATCH"]
    wicky    = [r for r in rows if r["verdict"] == "PHANTOM_WICKS"]
    ok_n     = sum(1 for r in rows if r["verdict"] == "OK")

    lines = []
    for r in rows:
        if r["verdict"] == "OK":
            continue
        lines.append(f"• {fmt(r['ticker'])} — *{r['verdict']}* "
                     f"(close dev {r['close_max_dev_pct'] or 0:.2f}%, "
                     f"{r['phantom_high_wicks']}+{r['phantom_low_wicks']} phantom wicks)\n"
                     f"    _{(r['detail'] or '—')[:180]}_")

    header = (f"🔍 Data Quality Audit — Yahoo vs IG broker data: "
              f"{len(rows)} tickers, {len(critical)} critical, {len(wicky)} with phantom wicks, {ok_n} clean")
    body = "\n".join(lines) if lines else "_All audited tickers matched IG broker data — no issues found._"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": (f"Phantom wick = Yahoo extreme beyond IG's by >1% (bad exchange print). "
                                f"Critical = closes deviate >2% (possible wrong instrument). "
                                f"IG allowance remaining: {remaining} | "
                                + datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"))}]},
    ]
    url = os.environ.get("SLACK_ALERTS" if critical else "SLACK_SIGNALS", "")
    if url:
        try:
            requests.post(url, json={"blocks": blocks}, timeout=10)
            log.info(f"Audit summary posted to {'#alerts' if critical else '#signals'}")
        except Exception as e:
            log.error(f"Audit Slack post failed: {e}")


def main():
    args = sys.argv[1:]
    if args and not args[0].isdigit():
        batch = args
    else:
        cap = int(args[0]) if args else MAX_BATCH
        batch = _pick_batch(cap)

    log.info(f"Auditing {len(batch)} tickers (30 daily candles each vs IG)...")
    rows, remaining = [], None
    for ticker in batch:
        try:
            r = _audit_ticker(ticker)
            remaining = r.pop("remaining", None) or remaining
            rows.append(r)
            log.info(f"  {ticker:<8} {r['verdict']:<18} close_dev={r['close_max_dev_pct']}% "
                     f"wicks={r['phantom_high_wicks']}+{r['phantom_low_wicks']}")
            if remaining is not None and remaining < ALLOWANCE_RESERVE:
                log.warning(f"IG allowance {remaining} below {ALLOWANCE_RESERVE} reserve — stopping batch early")
                break
        except Exception as e:
            log.warning(f"  {ticker}: audit failed — {e}")

    if rows:
        _save(rows)
        _post_slack(rows, remaining)
    log.info(f"Audit complete: {len(rows)} tickers, allowance remaining {remaining}")


if __name__ == "__main__":
    main()
