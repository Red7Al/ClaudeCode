# ======================================================================================================================
# File:         run_cot_report.py
# Author:       Alex Hind
# Created:      2026-06-07
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Weekly Commitment of Traders (COT) report. Posts a structured summary of the
# latest CFTC positioning to Slack #claude-trading-weekly.
#
# COT data is published weekly by the CFTC (Friday ~15:30 ET, positions as of
# the prior Tuesday) and ingested into the Supabase cot_snapshot table by
# refresh_all_cot() during the weekend review. This script READS that table and
# renders a human-readable report — it does not fetch from CFTC itself, so it
# must run AFTER refresh_all_cot has populated the latest week.
#
# The report covers, per instrument (grouped Metals / Energy / FX / Indices):
#   - Directional bias + composite COT score (-100..+100)
#   - Commercial (smart money) net positioning and week-over-week change
#   - Managed money (large speculator) net positioning
#   - Extreme-positioning flags (commercial / managed money percentile rank)
#   - Price-vs-positioning divergence
#   - Open-interest signal (real-money buy/sell, covering, liquidation)
# A headline section surfaces the strongest bullish/bearish reads, any extreme
# positioning, and any active divergences.
#
# A freshness guard flags the report when the latest cot_snapshot week is older
# than COT_STALE_DAYS — i.e. the weekend refresh did not run / found no new data.
#
# Usage:
#   python run_cot_report.py
#
# Or trigger via the weekend-review Cloud Routine, immediately after the COT
# refresh step.
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD   (read cot_snapshot)
#   SLACK_WEEKLY                          (#claude-trading-weekly webhook)
#   SLACK_ALERTS                          (optional — error surfacing)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.2.0   2026-06-19  Alex Hind   SELF-HEAL stale data (user 2026-06-19: "old data is of no use"): main() no longer just
#                                 warns when the snapshot is stale/empty — it calls cot_analysis.refresh_all_cot() (live
#                                 CFTC fetch) and re-reads before rendering. Only if CFTC has no newer week does it show an
#                                 (accurate) "latest available week is N days old — refreshed live just now" note instead
#                                 of the old "the weekend refresh may not have run" warning.
# 1.1.0   2026-06-07  Alex Hind   Add market-context lines for Gold, Silver, the US indices (S&P 500, NASDAQ) and a UK
#                                 proxy (MSCI EAFE positioning paired with the FTSE 100 price trend): each states
#                                 whether COT positioning is aligned with the price trend, and why. New "UK /
#                                 International (proxy)" group carries a note that CFTC has no FTSE/UK COT. Price trends
#                                 fetched via yfinance in an isolated enrich step so build_report stays pure.
# 1.0.0   2026-06-07  Alex Hind   Initial build. Reads latest cot_snapshot week, renders grouped per-instrument report
#                                 with a headline section and a freshness guard, posts to #claude-trading-weekly.
#                                 Surfaces failures to #claude-trading-alerts (no silent failures).
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import requests
from datetime import datetime, timezone, date

import pg8000.native

from config import YAHOO_MAP

log = logging.getLogger("cot_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SLACK_URL     = os.environ.get("SLACK_WEEKLY", "")   # weekly COT report → #claude-trading-weekly

# Flag the report as stale if the latest snapshot week is older than this many
# days (CFTC publishes weekly, so anything beyond ~10 days means a missed refresh).
COT_STALE_DAYS = 10

# Full instrument names (memory: always show full name alongside the ticker).
INSTRUMENT_NAMES = {
    "XAUUSD": "Gold",
    "XAGUSD": "Silver",
    "OIL":    "Crude Oil (WTI)",
    "GBPUSD": "British Pound",
    "AUDUSD": "Australian Dollar",
    "USDJPY": "Japanese Yen",
    "EURUSD": "Euro",
    "SPX500": "S&P 500",
    "NASDAQ": "NASDAQ 100",
    "MSCI_EAFE": "MSCI EAFE (developed ex-US, incl. UK)",
}

# Report grouping + ordering by asset class.
ASSET_CLASSES = [
    ("Metals",     ["XAUUSD", "XAGUSD"]),
    ("Energy",     ["OIL"]),
    ("FX",         ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]),
    ("US Indices", ["SPX500", "NASDAQ"]),
    ("UK / International (proxy)", ["MSCI_EAFE"]),
]

# Short note rendered under specific group headers.
GROUP_NOTES = {
    "UK / International (proxy)":
        "_CFTC has no FTSE/UK index COT — MSCI EAFE (developed markets ex-US, "
        "includes the UK) is shown as a positioning proxy, paired with the "
        "FTSE 100 price trend._",
}

# Instruments that get a market-context line (COT positioning vs price trend).
CONTEXT_INSTRUMENTS = ["XAUUSD", "XAGUSD", "SPX500", "NASDAQ", "MSCI_EAFE"]

# Price ticker / name for the context trend line, overriding YAHOO_MAP where the
# positioning proxy and the price reference differ: EAFE positioning is paired
# with the FTSE 100 price per the UK-proxy decision (2026-06-07).
CONTEXT_PRICE_TICKER = {"MSCI_EAFE": "^FTSE"}
CONTEXT_PRICE_NAME   = {"MSCI_EAFE": "FTSE 100"}


# ----------------------------------------------------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------------------------------------------------

def get_db():
    return _pool_get_db()


def fetch_latest_cot(db) -> list[dict]:
    """
    Return all cot_snapshot rows for the most recent report_date, as dicts.
    Empty list if the table has no rows.
    """
    cols = [
        "instrument", "report_date", "bias", "cot_score",
        "comm_net", "comm_net_change", "managed_money_net", "managed_money_change",
        "comm_extreme", "mm_extreme", "price_divergence", "oi_signal",
        "comm_net_pct_rank", "mm_net_pct_rank", "open_interest", "oi_change",
    ]
    rows = db.run(
        f"""select {", ".join(cols)}
            from cot_snapshot
            where report_date = (select max(report_date) from cot_snapshot)
            order by instrument"""
    )
    return [dict(zip(cols, r)) for r in (rows or [])]


# ----------------------------------------------------------------------------------------------------------------------
# Format helpers
# ----------------------------------------------------------------------------------------------------------------------

def _num(v, default=None):
    """Coerce a DB numeric/None to float, or return default."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _fmt_contracts(v) -> str:
    """Format a contract count with a sign and thousands separators ('—' if None)."""
    n = _num(v)
    if n is None:
        return "—"
    return f"{n:+,.0f}"


def _bias_emoji(bias: str) -> str:
    return {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get((bias or "").upper(), "⚪")


def _score_str(score) -> str:
    s = _num(score)
    return f"{s:+.0f}" if s is not None else "—"


def _extreme_tag(comm_extreme: str, mm_extreme: str) -> str:
    """Short human tag for extreme positioning, or '' if both normal."""
    tags = []
    if comm_extreme and comm_extreme != "NORMAL":
        tags.append(f"commercials {comm_extreme.replace('_', ' ').lower()}")
    if mm_extreme and mm_extreme != "NORMAL":
        tags.append(f"managed money {mm_extreme.replace('_', ' ').lower()}")
    return " ⚠ " + "; ".join(tags) if tags else ""


def _name(ticker: str) -> str:
    return INSTRUMENT_NAMES.get(ticker, ticker)


# ----------------------------------------------------------------------------------------------------------------------
# Market context — COT positioning vs price trend (the "why aligned / not")
# ----------------------------------------------------------------------------------------------------------------------

def _price_trend(yticker: str, weeks: int = 13) -> tuple:
    """
    Return (label, pct_change) over ~`weeks` of weekly closes.
    label is 'rising' / 'falling' / 'flat'; both None on failure. yfinance is
    imported here so the rest of the module stays import-light and pure.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(yticker).history(period=f"{weeks * 7 + 10}d", interval="1wk")
        if len(hist) < 3:
            return (None, None)
        first = float(hist["Close"].iloc[0])
        last  = float(hist["Close"].iloc[-1])
        if not first:
            return (None, None)
        pct = (last - first) / first * 100
        label = "rising" if pct > 3 else "falling" if pct < -3 else "flat"
        return (label, pct)
    except Exception as e:
        log.warning(f"Price trend fetch failed for {yticker}: {e}")
        return (None, None)


def market_context(row: dict, trend_label, trend_pct, price_name: str) -> str:
    """
    One-line plain-English read of whether COT positioning is aligned with the
    price trend, and why. Commercials = hedgers (smart money); managed money =
    large speculators (trend followers). Extremes/divergence are already shown on
    the ⚠ line, so this focuses on direction + price alignment.
    """
    score = _num(row.get("cot_score")) or 0.0
    comm  = _num(row.get("comm_net")) or 0.0
    mm    = _num(row.get("managed_money_net")) or 0.0

    comm_dir = "net long" if comm > 0 else "net short" if comm < 0 else "flat"
    mm_dir   = "net long" if mm  > 0 else "net short" if mm  < 0 else "flat"

    cot_bull = score >= 10
    cot_bear = score <= -10

    if trend_label is None:
        align = "price trend unavailable — alignment unknown"
    elif not (cot_bull or cot_bear):
        align = (f"COT balanced (score {score:+.0f}) against a {trend_label} price "
                 f"— no clear positioning edge")
    elif cot_bull and trend_label == "rising":
        align = "ALIGNED — bullish positioning confirms the rising price"
    elif cot_bear and trend_label == "falling":
        align = "ALIGNED — bearish positioning confirms the falling price"
    elif cot_bull and trend_label == "falling":
        align = ("NOT ALIGNED — smart money accumulating into a falling price "
                 "(bullish divergence; watch for a turn)")
    elif cot_bear and trend_label == "rising":
        align = ("NOT ALIGNED — smart money positioned short into a rising price "
                 "(reversal risk)")
    else:  # flat price, directional COT
        lean = "bullish" if cot_bull else "bearish"
        align = f"COT leans {lean} ({score:+.0f}) — positioning ahead of a flat price"

    pct_str = f"{trend_pct:+.1f}%/13wk" if trend_pct is not None else "n/a"
    return (f"Commercials {comm_dir}, managed money {mm_dir}; "
            f"{price_name} {trend_label or '—'} ({pct_str}). {align}.")


def enrich_with_context(rows: list[dict]) -> list[dict]:
    """
    Attach a 'market_context' string to the CONTEXT_INSTRUMENTS rows. Best-effort:
    on a price-fetch failure the context still renders with 'unavailable'. All
    network access (yfinance) is isolated here so build_report stays pure.
    """
    by = {r.get("instrument"): r for r in rows}
    for tk in CONTEXT_INSTRUMENTS:
        r = by.get(tk)
        if not r:
            continue
        price_tk   = CONTEXT_PRICE_TICKER.get(tk) or YAHOO_MAP.get(tk, tk)
        price_name = CONTEXT_PRICE_NAME.get(tk, _name(tk))
        label, pct = _price_trend(price_tk)
        r["market_context"] = market_context(r, label, pct, price_name)
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# Headline detection
# ----------------------------------------------------------------------------------------------------------------------

def build_headlines(rows: list[dict]) -> list[str]:
    """
    Surface the few rows a reader should look at first:
      - strongest bullish / bearish by composite COT score
      - any extreme commercial OR managed-money positioning
      - any active price-vs-positioning divergence
    Returns a list of mrkdwn bullet strings (may be empty).
    """
    lines = []
    scored = [r for r in rows if _num(r.get("cot_score")) is not None]

    if scored:
        top = max(scored, key=lambda r: _num(r["cot_score"]))
        bot = min(scored, key=lambda r: _num(r["cot_score"]))
        if _num(top["cot_score"]) > 0:
            lines.append(
                f"• Most bullish: *{_name(top['instrument'])}* ({top['instrument']}) "
                f"COT {_score_str(top['cot_score'])}, bias {top.get('bias', '—')}"
            )
        if _num(bot["cot_score"]) < 0 and bot["instrument"] != top["instrument"]:
            lines.append(
                f"• Most bearish: *{_name(bot['instrument'])}* ({bot['instrument']}) "
                f"COT {_score_str(bot['cot_score'])}, bias {bot.get('bias', '—')}"
            )

    extremes = [
        r for r in rows
        if (r.get("comm_extreme") and r["comm_extreme"] != "NORMAL")
        or (r.get("mm_extreme") and r["mm_extreme"] != "NORMAL")
    ]
    for r in extremes:
        lines.append(
            f"• Extreme positioning: *{_name(r['instrument'])}* ({r['instrument']})"
            f"{_extreme_tag(r.get('comm_extreme'), r.get('mm_extreme'))} "
            f"— often precedes a reversal"
        )

    divergences = [
        r for r in rows
        if r.get("price_divergence") and r["price_divergence"] not in ("NONE", None)
    ]
    for r in divergences:
        lines.append(
            f"• Divergence: *{_name(r['instrument'])}* ({r['instrument']}) "
            f"{r['price_divergence'].lower()} — smart money diverging from price"
        )

    return lines


# ----------------------------------------------------------------------------------------------------------------------
# Report builder
# ----------------------------------------------------------------------------------------------------------------------

def build_report(rows: list[dict], generated_at: datetime = None, refreshed: bool = False) -> str:
    """
    Build the full COT report text (Slack mrkdwn). Pure function — operates on a
    list of row dicts (cot_snapshot columns, or analyse_cot output), so it can
    render either the stored snapshot or a freshly computed set.

    `refreshed` = a live CFTC refresh was already attempted before building (so any
    remaining staleness is CFTC not having published yet, not a missed refresh job).
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    lines = []

    if not rows:
        return ("*Weekly COT Report*\n"
                "No COT data available in cot_snapshot. The weekend refresh may "
                "not have run yet.")

    report_date = rows[0].get("report_date")
    report_date_str = report_date.isoformat() if hasattr(report_date, "isoformat") else str(report_date)

    # ── Header + freshness guard ──────────────────────────────────────────────────────────────────────────────────────
    lines.append("*Weekly COT Report*")
    lines.append(f"_CFTC positioning as of {report_date_str} (Tuesday close)_")
    lines.append("─" * 48)

    stale_note = _staleness_note(report_date, generated_at, refreshed)
    if stale_note:
        lines.append(stale_note)
        lines.append("")

    # ── Headlines ─────────────────────────────────────────────────────────────────────────────────────────────────────
    headlines = build_headlines(rows)
    if headlines:
        lines.append("*Headlines*")
        lines.extend(headlines)
        lines.append("")

    # ── Per-asset-class detail ────────────────────────────────────────────────────────────────────────────────────────
    by_instrument = {r["instrument"]: r for r in rows}
    for class_name, tickers in ASSET_CLASSES:
        present = [t for t in tickers if t in by_instrument]
        if not present:
            continue
        lines.append(f"*{class_name}*")
        if class_name in GROUP_NOTES:
            lines.append(f"  {GROUP_NOTES[class_name]}")
        for t in present:
            r = by_instrument[t]
            emoji = _bias_emoji(r.get("bias"))
            lines.append(
                f"  {emoji} *{_name(t)}* ({t}) — {r.get('bias', '—')}  "
                f"COT {_score_str(r.get('cot_score'))}"
            )
            # Detail line: commercial net (+ change), managed money net, OI signal
            detail = (
                f"      Commercials {_fmt_contracts(r.get('comm_net'))} "
                f"(Δ {_fmt_contracts(r.get('comm_net_change'))})  ·  "
                f"Managed money {_fmt_contracts(r.get('managed_money_net'))}  ·  "
                f"OI {(r.get('oi_signal') or 'NEUTRAL').replace('_', ' ').title()}"
            )
            lines.append(detail)
            extreme = _extreme_tag(r.get("comm_extreme"), r.get("mm_extreme"))
            diverg  = r.get("price_divergence")
            tags = []
            if extreme:
                tags.append(extreme.strip(" ⚠"))
            if diverg and diverg not in ("NONE", None):
                tags.append(f"{diverg.lower()} divergence")
            if tags:
                lines.append(f"      ⚠ {'; '.join(tags)}")
            # Market context: COT positioning vs price trend (aligned / not aligned)
            if r.get("market_context"):
                lines.append(f"      ↳ {r['market_context']}")
        lines.append("")

    # ── Legend + footer ───────────────────────────────────────────────────────────────────────────────────────────────
    lines.append(
        "_Commercials = hedgers (smart money); Managed money = large speculators. "
        "COT score combines positioning percentile, extremes, divergence and open "
        "interest into a -100..+100 directional read._"
    )
    lines.append(f"_Generated {generated_at.strftime('%d %b %Y %H:%M UTC')}_")

    return "\n".join(lines)


def _staleness_note(report_date, generated_at: datetime, refreshed: bool = False):
    """Return a warning string if the snapshot week is older than COT_STALE_DAYS, else ''.
    If a live refresh was already attempted (`refreshed`), the note says CFTC simply has
    no newer week yet — not that the refresh job was missed (it wasn't; we just ran it)."""
    try:
        rd = report_date if isinstance(report_date, date) else \
            datetime.strptime(str(report_date)[:10], "%Y-%m-%d").date()
        age_days = (generated_at.date() - rd).days
        if age_days > COT_STALE_DAYS:
            if refreshed:
                return (f"ℹ *Latest available week is {age_days} days old* — refreshed live from CFTC "
                        f"just now; CFTC has not yet published a newer week (weekly release is Fri "
                        f"~15:30 ET, positions as of the prior Tuesday).")
            return (f"⚠ *Data may be stale* — latest COT week is {age_days} days old. "
                    f"The weekend refresh (refresh_all_cot) may not have run.")
    except Exception:
        pass
    return ""


# ----------------------------------------------------------------------------------------------------------------------
# Slack
# ----------------------------------------------------------------------------------------------------------------------

def post_to_slack(text: str) -> bool:
    from notify import slack_enabled
    if not slack_enabled("weekly"):
        return False   # Slack #weekly channel disabled (user 2026-08-01)
    if not SLACK_URL:
        log.warning("SLACK_WEEKLY not set — printing report to stdout instead")
        print(text)
        return False
    try:
        resp = requests.post(SLACK_URL, json={"text": text}, timeout=15)
        if resp.status_code == 200:
            log.info("COT report posted to #claude-trading-weekly")
            return True
        log.error(f"Slack post failed: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Slack post failed: {e}")
        return False


def _alert_failure(component: str, detail: str):
    """Surface a failure to #claude-trading-alerts (no silent failures)."""
    try:
        from notify import alert_system_error
        alert_system_error("WEEKEND_REVIEW", component,
                           "COT report failed", detail)
    except Exception as e:
        log.error(f"Could not surface COT report failure to Slack: {e}")


# ----------------------------------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------------------------------

def _row_age_days(rows, generated_at: datetime = None):
    """Age (days) of the latest snapshot week, or None if no rows / unparseable."""
    if not rows:
        return None
    generated_at = generated_at or datetime.now(timezone.utc)
    rd = rows[0].get("report_date")
    try:
        d = rd if isinstance(rd, date) else datetime.strptime(str(rd)[:10], "%Y-%m-%d").date()
        return (generated_at.date() - d).days
    except Exception:
        return None


def _load_latest_cot():
    db = get_db()
    try:
        return fetch_latest_cot(db)
    finally:
        db.close()


def main():
    log.info("Generating weekly COT report...")
    try:
        rows = _load_latest_cot()
    except Exception as e:
        log.error(f"Could not read cot_snapshot: {e}")
        _alert_failure("cot_snapshot read", str(e))
        raise

    # Self-heal (user 2026-06-19): stale or empty data is useless — never just warn and
    # serve an old week. Pull the latest from CFTC via refresh_all_cot() and re-read. Only
    # if CFTC genuinely has nothing newer (refreshed=True below) do we render a freshness note.
    age = _row_age_days(rows)
    refreshed = False
    if (not rows) or (age is not None and age > COT_STALE_DAYS):
        log.warning(f"COT snapshot stale/empty (age={age}d, threshold {COT_STALE_DAYS}d) — "
                    f"running refresh_all_cot() to self-heal")
        try:
            from cot_analysis import refresh_all_cot
            refresh_all_cot()                      # live CFTC fetch + persist for every instrument
            refreshed = True
            rows = _load_latest_cot()
            log.info(f"refresh_all_cot complete — latest snapshot now {_row_age_days(rows)}d old")
        except Exception as e:
            log.error(f"COT self-heal refresh failed: {e}")
            _alert_failure("refresh_all_cot self-heal", str(e))

    if not rows:
        msg = "COT report: cot_snapshot is empty even after a live refresh — nothing to report."
        log.warning(msg)
        _alert_failure("cot_snapshot empty after refresh", msg)

    rows = enrich_with_context(rows)   # adds price-trend market context (best-effort)
    report = build_report(rows, refreshed=refreshed)
    log.info("Report built. Posting to Slack...")
    post_to_slack(report)
    try:   # record this run in the web app's Batch Activity (user 2026-08-11, P-12)
        from web_store import append_batch
        append_batch("cron-job.org", f"COT report — {len(rows)} instrument(s)" + (" (self-healed)" if refreshed else ""), by="cron")
    except Exception:
        pass


if __name__ == "__main__":
    main()
