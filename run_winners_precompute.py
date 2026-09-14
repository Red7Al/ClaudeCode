# ======================================================================================================================
# File:         run_winners_precompute.py
# Author:       Alex Hind
# Created:      2026-08-23
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Builds the /api/winners payloads ahead of time and stores them, so Best Settings does not pay for the
# build on a user's first visit.
#
# WHY. Building one window costs about 33 seconds -- the squeeze_history replay in _sqa_all_rows plus the
# per-trigger feature pass -- and Best Settings requests TWO windows (annual, then the deferred three-year)
# every time the tab is opened. Measured live on 2026-08-22: /api/winners 34.9s and ?years=3 16.9s. Keying
# the in-process caches by window (2026-08-23) took repeat loads to under 1.3s, but a cold worker still
# paid the full cost, and the shared host serialises requests so the two stacked up.
#
# This runs after the daily data refresh and writes each window to web_json_store, the same mechanism
# run_best_settings_audit.py already uses for best_settings_full_grid_audit.
#
# SAFETY. The server uses a stored payload ONLY when it was built from the dataset now in play: the
# snapshot's generated_utc is recorded alongside it and must match, and the copy must be under a day old.
# A missed, failed or stale precompute therefore makes the page slow, never wrong. The payload is produced
# by server._winners_payload -- the same function the endpoint calls -- so the stored copy cannot drift
# into being a lookalike build of a different population (memory: results-winners-same-dataset).
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-23  Alex Hind   Initial build.
# ======================================================================================================================

import argparse
import logging
import os
import sys
import time

log = logging.getLogger("winners_precompute")

WINDOWS = (1, 3)     # the two the web app actually asks for: the annual cards and the three-year evidence


def _dataset_key() -> str:
    """The snapshot generation time the SERVER will compare against.

    The server keys a stored payload to hvf_web/snapshot.json's generated_utc. A GitHub runner usually has
    no built snapshot, so reading it locally yielded an empty key and every stored payload was silently
    rejected -- which is how the first run stored 3,782 and 11,672 rows that the site then ignored
    entirely (2026-08-23). Two sources, in order:

      1. The local snapshot, when this runs straight after a snapshot build in the same job. That is the
         exact file about to be published, so the key cannot race.
      2. The live site's /api/status, for the standalone scheduled run. There is a small race if a new
         snapshot publishes immediately afterwards; the consequence is a rejected payload and a slow
         page, never a wrong one.
    """
    try:
        from hvf_web import server
        local = (server._load_snapshot() or {}).get("generated_utc") or ""
        if local:
            log.info("dataset key from the local snapshot: %s", local)
            return local
    except Exception as ex:
        log.info("no usable local snapshot (%s); asking the live site instead", ex)
    try:
        import json as _json
        import urllib.request
        url = os.environ.get("SITE_URL", "https://www.squeezescanner.cloud").rstrip("/") + "/api/status"
        with urllib.request.urlopen(url, timeout=60) as r:
            live = (_json.loads(r.read().decode("utf-8")) or {}).get("generated_utc") or ""
        if live:
            log.info("dataset key from %s: %s", url, live)
        return live
    except Exception as ex:
        log.error("could not read the live snapshot generation time: %s", ex)
        return ""


def build_performance(dataset: str, dry_run: bool = False) -> bool:
    """Precompute the /api/performance payload. True on success.

    ADDED 2026-09-13, owner report "performance cards not showing if user not logged on". That endpoint
    answered {"rows":[],"warming":true} on every call, permanently: its background build needs 34.5
    seconds (measured) while the request returns the marker immediately, so the thread has to outlive
    the response and on the shared host it does not. Precomputing here removes the background thread
    from the equation instead of trying to make it recoverable.

    Deliberately built in THIS script rather than a new one: it is already invoked from the scanner
    snapshot workflow immediately after publication, with the exact snapshot.json on disk, so the
    dataset key cannot race. A new script would have needed its own schedule, and this repository's
    signature defect is correct code that nothing ever calls.

    Uses server._build_perf_payload -- the same function the endpoint calls -- so the stored copy cannot
    drift into a lookalike build of a different population.
    """
    from hvf_web import server
    import web_store

    started = time.time()
    try:
        payload = server._build_perf_payload()
    except Exception as ex:
        log.error("  performance build FAILED: %s", ex)
        return False
    rows = len(payload.get("rows") or [])
    took = time.time() - started
    if not rows:
        # Same rule as the windows above: storing an empty population would serve "no trades" quickly
        # instead of the truth slowly, and the page would look answered rather than broken.
        log.error("  performance build produced NO rows in %.1fs; refusing to store it", took)
        return False
    doc = {"payload": server._json_safe(payload), "dataset": dataset, "built_at": time.time(),
           "rows": rows, "build_seconds": round(took, 1)}
    if dry_run:
        log.info("  performance: %d rows in %.1fs (dry run, not stored)", rows, took)
        return True
    if web_store.save_json_store(server._PERF_STORE_KEY, doc):
        log.info("  performance: %d rows in %.1fs -> %s", rows, took, server._PERF_STORE_KEY)
        return True
    log.error("  performance: built %d rows but the store write FAILED", rows)
    return False


def build(years_list=WINDOWS, dry_run=False) -> int:
    from hvf_web import server
    import web_store

    dataset = _dataset_key()
    if not dataset:
        log.error("no dataset key could be determined; the server would reject these payloads, so "
                  "nothing will be stored. Run this after a snapshot build, or set SITE_URL.")
        return len(list(years_list))

    # REFUSE TO BUILD WITHOUT A SNAPSHOT. _sqa_all_rows takes name, sector, location and current_price
    # FROM the snapshot; with none loaded every row gets location None and name falling back to the bare
    # ticker, and storing that REPLACES a good copy with a worse one under a dataset key the server
    # accepts. MEASURED 2026-09-14: the 05:24 run stored 6,444 annual and 17,477 three-year rows with
    # ZERO real company names and ZERO locations, against 6,252 and 6,441 the evening before -- and the
    # live snapshot matched, so the degraded copy was served all day until the evening snapshot job
    # rebuilt it. Broken through the whole working day, healthy overnight, which is why nobody caught it.
    #
    # The cause is environmental and outside this script: a bare runner has no hvf_web/snapshot.json (it
    # is not in git) and must fetch it from Supabase Storage, which has returned 402 since 2026-08-16.
    # This is the same "refuse rather than store something wrong" rule already applied to an empty
    # population below -- slow or missing is recoverable, a plausible-looking wrong answer is not.
    snap = {}
    try:
        snap = server._load_snapshot() or {}
    except Exception as ex:
        log.error("could not load a snapshot (%s)", ex)
    if not (snap.get("records") or []):
        log.error("NO SNAPSHOT RECORDS are loaded, so every row would carry no location and the ticker "
                  "in place of its name. Refusing to store anything rather than overwrite a good copy "
                  "with a worse one. Run this after a snapshot build, or restore Supabase Storage.")
        return len(list(years_list)) + 1        # +1: the performance payload is refused too

    failures = 0
    for years in years_list:
        started = time.time()
        try:
            payload = server._winners_payload(years)
        except Exception as ex:
            log.error("  %d-year build FAILED: %s", years, ex)
            failures += 1
            continue
        rows = len(payload.get("rows") or [])
        took = time.time() - started
        if not rows:
            # Storing an empty population would serve "no trades" fast instead of the truth slowly.
            log.error("  %d-year build produced NO rows in %.1fs; refusing to store it", years, took)
            failures += 1
            continue
        doc = {"payload": server._json_safe(payload), "dataset": dataset, "built_at": time.time(),
               "rows": rows, "build_seconds": round(took, 1)}
        if dry_run:
            log.info("  %d-year: %d rows in %.1fs (dry run, not stored)", years, rows, took)
            continue
        if web_store.save_json_store(server._winners_store_key(years), doc):
            log.info("  %d-year: %d rows in %.1fs -> %s", years, rows, took, server._winners_store_key(years))
        else:
            log.error("  %d-year: built %d rows but the store write FAILED", years, rows)
            failures += 1
    if not build_performance(dataset, dry_run=dry_run):
        failures += 1
    return failures


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Precompute the /api/winners payloads into the JSON store.")
    ap.add_argument("--years", type=str, default="", help="comma-separated windows (default: 1,3)")
    ap.add_argument("--dry-run", action="store_true", help="build and report, write nothing")
    a = ap.parse_args()

    windows = WINDOWS
    if a.years.strip():
        windows = tuple(max(1, min(4, int(y))) for y in a.years.split(",") if y.strip())

    # +1 for the /api/performance payload, which build() now precomputes alongside the windows.
    total = len(windows) + 1
    log.info("Precomputing %s and the performance payload", ", ".join(f"{y}y winners" for y in windows))
    failures = build(windows, dry_run=a.dry_run)
    if failures:
        log.error("%d of %d payload(s) failed; the site falls back to building those live",
                  failures, total)
        return 1
    log.info("All %d payload(s) stored.", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
