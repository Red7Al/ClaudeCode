# ======================================================================================================================
# File:         price_audit.py
# Author:       Alex Hind
# Created:      2026-06-29
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily audit + backfill of the Supabase price_history "golden" dataset (user 2026-06-29). For every monitored
# instrument it re-fetches the recent OHLCV window from the authoritative source (yfinance today; IG can be added),
# compares it against what is stored, records any discrepancy, then UPSERTs so the stored series is corrected and
# extended. Each price keeps its source + recorded_at / updated_at (handled by price_store). A summary row is written
# to price_audit_log so each run is itself auditable.
#
# Usage:
#   python price_audit.py                 # daily audit — re-verify the last AUDIT_LOOKBACK_DAYS for every ticker
#   python price_audit.py --backfill 1100 # one-off deep backfill (~3y) for tickers that are missing / short
#   python price_audit.py --tickers ABF.L,DIS [--lookback 30] [--source YF] [--slack]
#
# A discrepancy = stored close differs from the freshly-fetched close for the same bar_date by > DISCREPANCY_TOL_PCT.
# These are reported (and corrected) — that is the point of the audit: keep the golden set provably correct.
#
# Environment: SUPABASE_USER, SUPABASE_DB_PASSWORD (via db_pool); optionally SLACK_TWITTER for the --slack summary.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-29  Alex Hind   Initial build — per-ticker re-fetch/compare/correct, discrepancy detection,
#                                 price_audit_log run record, optional Slack summary.
# ======================================================================================================================

import argparse
import logging
import time
from datetime import datetime, timezone, timedelta

import pandas as pd

import price_store
from db_pool import get_db

log = logging.getLogger("price_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

AUDIT_LOOKBACK_DAYS = 30        # daily mode re-verifies the trailing month (catches YF revisions + gaps)
DISCREPANCY_TOL_PCT = 0.1       # |stored-fetched|/fetched*100 above this = a real discrepancy
PER_TICKER_PAUSE_S = 0.0        # raise if yfinance starts throttling

# IG-as-truth cross-check (user 2026-06-29: "prefer IG as the truth"). IG has a small weekly price-history
# allowance, so it is a SHORT recent-window verifier, not the bulk source: agreeing bars are flagged
# double_checked; disagreeing bars are overwritten with the IG value (rescaled to the stored units) and
# re-sourced 'IG'. Skip once the allowance runs low so a daily audit never exhausts it.
IG_VERIFY_DAYS = 7
IG_MIN_REMAINING = 300
IG_SANITY_MAX_DRIFT_PCT = 50    # after unit-rescaling, a still-huge gap = epic/unit mismatch -> don't corrupt

_AUDIT_LOG_DDL = """create table if not exists price_audit_log (
    id              bigserial primary key,
    run_at          timestamptz not null default now(),
    mode            text        not null,
    source          text        not null,
    tickers_checked int         not null,
    bars_written    int         not null,
    discrepancies   int         not null,
    max_drift_pct   double precision,
    duration_s      double precision,
    notes           text
)"""


def _ensure_audit_log(db):
    db.run(_AUDIT_LOG_DDL)


def _universe():
    """{ticker: yahoo_symbol} for every monitored instrument."""
    from run_hvf_report import UNIVERSE
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    out = {}
    for tickers in UNIVERSE.values():
        for tk in tickers:
            out[tk] = YAHOO_MAP.get(tk, tk)
    return out


def audit_ticker(ticker, yahoo_symbol, window_days, source, db):
    """Re-fetch the trailing `window_days`, compare to stored, UPSERT. Returns
    (bars_written, discrepancies, max_drift_pct)."""
    import yfinance as yf
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=window_days)
    try:
        dl = yf.download(yahoo_symbol, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    except Exception as e:
        log.warning(f"{ticker}: fetch failed: {e}")
        return 0, 0, None
    if dl is None or dl.empty:
        log.warning(f"{ticker}: source returned no data")
        return 0, 0, None
    if getattr(dl.columns, "nlevels", 1) > 1:
        dl = dl.copy()
        dl.columns = dl.columns.get_level_values(0)

    # Compare against what is stored for the same window BEFORE overwriting.
    discrepancies, max_drift = 0, 0.0
    stored = price_store.get_bars(ticker, start, end, db=db)
    if stored is not None and not stored.empty:
        s_close = stored["Close"]
        for idx, c in dl["Close"].items():
            if pd.isna(c):
                continue
            ts = pd.Timestamp(idx).normalize()
            if ts in s_close.index:
                old = float(s_close.loc[ts])
                drift = abs(old - float(c)) / float(c) * 100 if c else 0.0
                if drift > DISCREPANCY_TOL_PCT:
                    discrepancies += 1
                    max_drift = max(max_drift, drift)
                    log.info(f"{ticker} {ts.date()}: stored {old:g} -> source {float(c):g} ({drift:.2f}% drift) - correcting")

    written = price_store.upsert_bars(ticker, dl, source, db=db)
    return written, discrepancies, (max_drift or None)


def _ig_verify(ticker, db):
    """IG-as-truth cross-check. Returns (corrected, double_checked, remaining_allowance). (0, 0, None)
    when IG can't be used for this ticker. IG prices are rescaled to the stored (YF) units before any
    comparison/overwrite, because IG quotes some instruments in cents/pence (run_data_quality_audit logic)."""
    try:
        from ig_shim import get_epic, get_prices_df
    except Exception:
        return 0, 0, None
    try:
        epic = get_epic(ticker)
    except Exception:
        epic = None
    if not epic:
        return 0, 0, None
    try:
        ig_df, remaining = get_prices_df(epic, resolution="DAY", count=IG_VERIFY_DAYS)
    except Exception as e:
        log.warning(f"{ticker}: IG fetch failed: {e}")
        return 0, 0, None
    if ig_df is None or ig_df.empty:
        return 0, 0, remaining
    ig_df = ig_df.copy()
    ig_df.index = pd.to_datetime(ig_df.index).normalize()
    stored = price_store.get_bars(ticker, ig_df.index.min().date(), ig_df.index.max().date(), db=db)
    if stored is None or stored.empty:
        return 0, 0, remaining
    overlap = [d for d in ig_df.index if d in stored.index]
    if not overlap:
        return 0, 0, remaining

    import math
    ig_med = float(ig_df.loc[overlap, "Close"].median())
    y_med = float(stored.loc[overlap, "Close"].median())
    if ig_med <= 0 or y_med <= 0:
        return 0, 0, remaining
    snapped = 10 ** round(math.log10(ig_med / y_med))      # IG units / stored units
    igy = ig_df[["Open", "High", "Low", "Close"]] / snapped  # IG, rescaled into the stored units

    agree, fix = [], []
    for d in overlap:
        try:
            ig_c, st_c = float(igy.loc[d, "Close"]), float(stored.loc[d, "Close"])
        except Exception:
            continue
        if st_c <= 0:
            continue
        drift = abs(ig_c - st_c) / st_c * 100
        if drift > IG_SANITY_MAX_DRIFT_PCT:
            continue                                        # unit/epic mismatch — never corrupt the row
        (fix if drift > DISCREPANCY_TOL_PCT else agree).append(d)

    corrected = 0
    if fix:
        fdf = igy.loc[fix].copy()
        fdf["Volume"] = float("nan")
        for d in fix:
            log.info(f"{ticker} {d.date()}: stored {float(stored.loc[d, 'Close']):g} -> IG (truth) "
                     f"{float(igy.loc[d, 'Close']):g} - correcting")
        corrected = price_store.upsert_bars(ticker, fdf, "IG", db=db)
    dc = price_store.set_double_checked(ticker, [d.date() for d in agree], db=db) if agree else 0
    return corrected, dc, remaining


def run(mode, window_days, source, tickers=None, slack=False, use_ig=None):
    t0 = time.time()
    uni = _universe()
    if tickers:
        uni = {tk: uni.get(tk, tk) for tk in tickers}
    if use_ig is None:
        use_ig = (mode == "daily")   # IG cross-check only in daily mode; the deep backfill is YF-only (allowance)
    log.info(f"price audit [{mode}] - {len(uni)} instruments, window {window_days}d, "
             f"source {source}, IG-truth {'on' if use_ig else 'off'}")

    total = len(uni)
    db = get_db()
    tot_written = tot_disc = checked = ig_corrected = ig_dc = pruned = 0
    max_drift = 0.0
    ig_ok = use_ig
    try:
        price_store.ensure_schema(db)
        _ensure_audit_log(db)
        for i, (tk, ysym) in enumerate(uni.items(), 1):
            w, d, md = audit_ticker(tk, ysym, window_days, source, db)
            checked += 1
            tot_written += w
            tot_disc += d
            if md:
                max_drift = max(max_drift, md)
            if ig_ok:                                   # IG-as-truth cross-check (user 2026-06-29)
                c, dc, rem = _ig_verify(tk, db)
                ig_corrected += c
                ig_dc += dc
                if rem is not None and rem < IG_MIN_REMAINING:
                    log.warning(f"IG allowance low ({rem}) - stopping IG cross-checks for the rest of this run")
                    ig_ok = False
            if i % 25 == 0 or i == total:
                log.info(f"  progress {i}/{total} - {tot_written} bars, {tot_disc} YF fixes, "
                         f"{ig_corrected} IG fixes, {ig_dc} double-checked")
            if PER_TICKER_PAUSE_S:
                time.sleep(PER_TICKER_PAUSE_S)

        try:                                            # keep only RETENTION_YEARS (user 2026-06-29)
            pruned = price_store.prune_older_than()
        except Exception as e:
            log.warning(f"prune failed: {e}")

        dur = time.time() - t0
        notes = f"{checked} checked; IG fixes {ig_corrected}; double-checked {ig_dc}; pruned {pruned}"
        db.run("insert into price_audit_log (mode,source,tickers_checked,bars_written,discrepancies,"
               "max_drift_pct,duration_s,notes) values (:m,:s,:tc,:bw,:dc,:md,:du,:no)",
               m=mode, s=source, tc=checked, bw=tot_written + ig_corrected, dc=tot_disc + ig_corrected,
               md=(max_drift or None), du=round(dur, 1), no=notes)
    finally:
        db.close()

    summary = (f"Price audit [{mode}] done: {checked} instruments, {tot_written} bars written, "
               f"{tot_disc} YF + {ig_corrected} IG discrepancies corrected, {ig_dc} double-checked "
               f"(max drift {max_drift:.2f}%), pruned {pruned}, {dur:.0f}s")
    log.info(summary)
    if slack:
        try:
            from intraday_signals import _post_text_to_slack  # best-effort; skip if unavailable
            _post_text_to_slack(summary)
        except Exception as e:
            log.warning(f"slack summary skipped: {e}")
    try:   # record this run in the web app's Batch Activity (user 2026-08-11, P-12). Shared by both
        # "Price Data Refresh" (04:30 UTC) and "Price History Audit" (23:00 UTC) — same script/mode
        # ("daily") at both cron times, so this can't distinguish which job triggered it by name alone.
        from web_store import append_batch
        append_batch("cron-job.org", f"Price audit [{mode}] — {checked} instruments, {tot_written} bars written", by="cron")
    except Exception:
        pass
    return summary


def main():
    ap = argparse.ArgumentParser(description="Audit/backfill the Supabase price_history golden dataset.")
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="deep backfill window in days (e.g. 1100 ~ 3y) instead of the daily audit window")
    ap.add_argument("--lookback", type=int, default=AUDIT_LOOKBACK_DAYS,
                    help=f"daily audit window in days (default {AUDIT_LOOKBACK_DAYS})")
    ap.add_argument("--tickers", type=str, help="comma-separated subset (default: whole universe)")
    ap.add_argument("--source", type=str, default="YF", help="source label to record (default YF)")
    ap.add_argument("--slack", action="store_true", help="post the run summary to Slack")
    ap.add_argument("--no-ig", action="store_true", help="skip the IG-as-truth cross-check (YF only)")
    a = ap.parse_args()
    tickers = [t.strip() for t in a.tickers.split(",") if t.strip()] if a.tickers else None
    if a.backfill:
        run("backfill", a.backfill, a.source, tickers, a.slack, use_ig=(False if a.no_ig else False))
    else:
        run("daily", a.lookback, a.source, tickers, a.slack, use_ig=(False if a.no_ig else True))


if __name__ == "__main__":
    main()
