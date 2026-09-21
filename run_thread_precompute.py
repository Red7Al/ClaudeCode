#!/usr/bin/env python
"""Precompute the X-thread text for every instrument that has a card, and store it for the web tier.

WHY THIS EXISTS. /api/thread returned HTTP 500 after ~120 seconds and the X-thread card on the
instrument detail panel rendered as nothing. It builds its text by calling into quality_report and
intraday_signals, and both reach numpy -- which is SIGSYS-killed on the IONOS web host, because the
seccomp filter kills any process calling mbind(2) (the bundled OpenBLAS does). It kills the PROCESS, so
try/except cannot catch it; the request simply dies and the gateway times out.

MAKING THAT CALL TREE NUMPY-FREE WAS TRIED AND ABANDONED, deliberately. Five layers in
(intraday_signals' text helpers, instrument_name -> yfinance, quality_report.fundamentals,
_kpi_block, then price_action) the report was still reaching numpy, and each layer removed had DEGRADED
the output -- measured on AAF.L, 8 report parts fell to 5 and then 4 as network paths were stripped. The
generator legitimately needs pandas for the dividend series and price_action for the chart story. So the
compute stays where it works and only the RESULT travels, which is how fundamentals_by_ticker,
broker_by_ticker, winners_rows_1y and performance_rows_12m already work.

Runs in GitHub Actions as its OWN nightly job (trading-thread-precompute.yml), not inside Scanner
Snapshot Publish: at ~9s per instrument across 422 carded instruments it takes roughly an hour, and the
publish workflow already runs ~70 minutes. Adding an hour to the critical publication path to refresh a
panel card is the wrong trade. It takes its input from the publish run's `scanner-snapshot` artifact,
so the text always describes a real published scan.
"""

import argparse
import json
import logging
import os
import time

log = logging.getLogger("thread_precompute")

STORE_KEY = "thread_by_ticker"
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hvf_web", "snapshot.json")


def _records(path: str = None) -> list:
    """Snapshot records that HAVE a card. Only those instruments get a thread on the panel.

    path defaults to SNAPSHOT at CALL time, not at definition time. A `path: str = SNAPSHOT` default
    freezes the module constant into the signature, so pointing SNAPSHOT somewhere else has no effect
    and the function silently keeps reading the original file.
    """
    path = path or SNAPSHOT
    with open(path, encoding="utf-8") as fh:
        snap = json.load(fh)
    recs = [r for r in (snap.get("records") or []) if r.get("_card") and r.get("ticker")]
    log.info("snapshot %s: %d records, %d with a card",
             snap.get("generated_utc"), len(snap.get("records") or []), len(recs))
    return recs, (snap.get("generated_utc") or "")


def _parts_for(rec: dict) -> list:
    """The same parts /api/thread used to build itself: the lead tweet, then the long report.

    Mirrors the endpoint exactly -- card fields plus name and index -- so the stored text is what the
    endpoint would have produced, not a lookalike built from different inputs.
    """
    card = dict(rec.get("_card") or {})
    card["name"] = rec.get("name")
    card["index"] = rec.get("market")
    parts = []
    try:
        from intraday_signals import _generate_x_drafts
        drafts = _generate_x_drafts([card], post=False, collect=True)
        if drafts and drafts[0].get("tweet"):
            parts.append(drafts[0]["tweet"])
    except Exception as exc:
        log.warning("  lead tweet failed for %s: %s", rec.get("ticker"), exc)
    try:
        from quality_report import publish_long_report_for
        parts += [p for p in (publish_long_report_for(card, post=False) or []) if p]
    except Exception as exc:
        log.warning("  long report failed for %s: %s", rec.get("ticker"), exc)
    return parts


def build(limit: int = 0, dry_run: bool = False) -> int:
    """Build and store the payload. Returns 0 on success, non-zero on failure (the workflow's gate)."""
    # Read the snapshot BEFORE importing web_store: importing it opens a Supabase connection, and
    # there is no reason to open one only to discover there is nothing to store.
    recs, generated = _records()
    if limit:
        recs = recs[:limit]
    if not recs:
        log.error("no snapshot records carry a card; refusing to store an empty payload")
        return 2

    import web_store

    out, t0 = {}, time.time()
    for i, rec in enumerate(recs, 1):
        tk = rec["ticker"]
        try:
            parts = _parts_for(rec)
            if parts:
                out[tk] = parts
        except Exception as exc:
            log.warning("  %s failed: %s", tk, exc)
        if i % 50 == 0:
            log.info("  %d/%d (%.0fs elapsed)", i, len(recs), time.time() - t0)

    log.info("built %d threads from %d records in %.0fs", len(out), len(recs), time.time() - t0)

    # REFUSE TO STORE AN EMPTY RESULT over a good older copy -- the rule the fundamentals and winners
    # precomputes both apply. A bad run must leave the panel STALE, never blank.
    if not out:
        log.error("produced NOTHING; refusing to overwrite the stored copy")
        return 2
    if dry_run:
        log.info("dry run: %d threads, not stored", len(out))
        return 0

    doc = {"built_at": time.time(), "dataset": generated, "count": len(out), "records": out}
    if not web_store.save_json_store(STORE_KEY, doc):
        log.error("built %d threads but the store write FAILED", len(out))
        return 1
    log.info("%d threads -> %s (dataset %s)", len(out), STORE_KEY, generated)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="only the first N instruments (testing)")
    ap.add_argument("--dry-run", action="store_true", help="build and report, store nothing")
    a = ap.parse_args()
    return build(limit=a.limit, dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
