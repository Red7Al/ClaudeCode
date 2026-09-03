#!/usr/bin/env python3
# ======================================================================================================================
# File:         run_best_settings_cards.py
# Author:       Alex Hind
# Created:      2026-09-03
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Precomputes the PUBLIC Best Settings cards and stores them, so a logged-out visitor sees the cards
# without ever receiving the per-trade rows they are computed from.
#
# WHY. The cards were always computed in the browser out of /api/winners rows. Those rows ARE the
# Transaction evidence, and serving them to anonymous visitors is exactly what the user forbade
# ("logged out users should see cards BUT NOT THE UNDERLYING EVIDENCE TABLE", 2026-09-03). Aggregates
# have to be produced somewhere the rows are already allowed to be, and only the summary published.
#
# The search is NOT reimplemented here. hvf_web/best_settings.js is the page's own code and it runs under
# Node (verified present on the IONOS host and on the GitHub runner). Writing a fourth Python wallet
# replay for the public page would put this repository's unresolved audit-vs-client return divergence on
# the one surface anonymous visitors see.
#
# SAFETY. The stored payload records the snapshot generation it was built from, and the server serves it
# only while that matches the dataset now in play. A missed, failed or stale run therefore leaves the
# logged-out page saying the cards are being recalculated -- slow or absent, never wrong.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-09-03  Alex Hind   Initial build.
# ======================================================================================================================

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

log = logging.getLogger("best_settings_cards")

STORE_KEY = "best_settings_cards"

# The model a logged-out visitor's cards are calculated on. These are the app's own defaults
# (hvf_web/app.js: WINNERS_WALLET / WINNERS_STAKE / WINNERS_MAXOPEN / MIN_TRADE), so signing in and
# leaving the Model boxes untouched reproduces exactly these numbers.
PUBLIC_MODEL = {"wallet": 10000, "minTrade": 25, "stake": 0.05, "maxOpen": 20}

# Rows the browser search would never look at. Dropping them before Node keeps the job file to a size
# that starts quickly; every field the search or the card actually reads is listed.
ROW_FIELDS = ("ticker", "trig_date", "exit_date", "perf", "market", "mcap", "rr", "quality",
              "volume_score", "rvol", "above_vwap", "atr_expanding", "state", "outcome", "days_open")


def _dataset_key() -> str:
    """The snapshot generation the SERVER will compare a stored payload against.

    Same two sources, in the same order, as run_winners_precompute._dataset_key: the local snapshot when
    this runs straight after a build, else the live site. Getting this wrong stores a payload the server
    silently ignores, which is how the first winners precompute stored 15,454 rows nothing ever read.
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
        import urllib.request
        url = os.environ.get("SITE_URL", "https://www.squeezescanner.cloud").rstrip("/") + "/api/status"
        with urllib.request.urlopen(url, timeout=60) as r:
            live = (json.loads(r.read().decode("utf-8")) or {}).get("generated_utc") or ""
        if live:
            log.info("dataset key from %s: %s", url, live)
        return live
    except Exception as ex:
        log.error("could not read the live snapshot generation time: %s", ex)
        return ""


def _slim(rows):
    return [{k: r.get(k) for k in ROW_FIELDS if r.get(k) is not None} for r in rows or []]


def build(dry_run: bool = False, node: str = "") -> int:
    from hvf_web import server
    import web_store

    node = node or shutil.which("node") or ""
    if not node:
        log.error("node is not on PATH; the page's own search cannot be run and nothing will be stored")
        return 1

    dataset = _dataset_key()
    if not dataset:
        log.error("no dataset key could be determined; the server would reject this payload, so nothing "
                  "will be stored. Run this after a snapshot build, or set SITE_URL.")
        return 1

    started = time.time()

    def rows_for(years):
        # run_winners_precompute stores these minutes earlier in the same job, keyed to the same
        # snapshot. Reusing that copy saves about 50 seconds of DB replay and, more importantly, means
        # the public cards are computed from the SAME payload the signed-in page will be served.
        stored = server._winners_stored(years)
        if stored:
            log.info("  %d-year rows from the precomputed payload", years)
            return stored.get("rows") or []
        return server._winners_payload(years).get("rows") or []

    try:
        annual = rows_for(1)
        three = rows_for(3)
    except Exception as ex:
        log.error("the winners payloads could not be built: %s", ex)
        return 1
    if len(annual) < 10:
        # Storing an empty card set would tell every logged-out visitor there is no recommendation,
        # confidently and wrongly. Leave the previous payload in place instead.
        log.error("only %d annual rows; refusing to publish a card set from that", len(annual))
        return 1
    log.info("annual %d rows, three-year %d rows in %.1fs", len(annual), len(three), time.time() - started)

    job = dict(PUBLIC_MODEL, rows=_slim(annual), rows3y=_slim(three))
    fd, job_path = tempfile.mkstemp(suffix=".json", prefix="best_settings_job_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        node_started = time.time()
        try:
            proc = subprocess.run([node, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      "tools", "best_settings_cards.js"), job_path],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=int(os.environ.get("BEST_CARDS_TIMEOUT", "1800")))
        except subprocess.TimeoutExpired:
            log.error("the card search did not finish inside BEST_CARDS_TIMEOUT; nothing will be stored")
            return 1
    finally:
        try:
            os.unlink(job_path)
        except OSError:
            pass
    for line in (proc.stderr or "").splitlines():
        log.info("  node: %s", line)
    if proc.returncode != 0:
        log.error("the card search failed (exit %d); nothing will be stored", proc.returncode)
        return 1
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError as ex:
        log.error("the card search produced unreadable output: %s", ex)
        return 1
    cards = payload.get("cards") or []
    if not cards:
        log.error("the card search produced no cards; refusing to store that over a working payload")
        return 1

    # A last, explicit check that no per-trade row escaped into the public payload. This is the whole
    # point of the job, so it is asserted rather than assumed -- a future field added to the summary must
    # not be able to smuggle the evidence out.
    leaked = [k for k in ("rows", "proof", "seq", "choices") if k in payload]
    if leaked:
        log.error("the payload carries %s, which would expose per-trade evidence; refusing to store it",
                  ", ".join(leaked))
        return 1

    doc = {"payload": payload, "dataset": dataset, "built_at": time.time(),
           "cards": len(cards), "build_seconds": round(time.time() - node_started, 1)}
    if dry_run:
        log.info("%d cards in %.1fs (dry run, not stored)", len(cards), doc["build_seconds"])
        for c in cards:
            log.info("  %-18s %+7.1f%%  dd %4.1f%%  %5d funded of %6d eligible",
                     c.get("label"), (c.get("ret") or 0) * 100, (c.get("dd") or 0) * 100,
                     c.get("n") or 0, c.get("eligible") or 0)
        return 0
    if not web_store.save_json_store(STORE_KEY, doc):
        log.error("built %d cards but the store write FAILED", len(cards))
        return 1
    log.info("%d cards in %.1fs -> %s", len(cards), doc["build_seconds"], STORE_KEY)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Precompute the public Best Settings cards into the JSON store.")
    ap.add_argument("--dry-run", action="store_true", help="build and report, write nothing")
    ap.add_argument("--node", default="", help="path to node (default: whatever is on PATH)")
    a = ap.parse_args()
    return build(dry_run=a.dry_run, node=a.node)


if __name__ == "__main__":
    sys.exit(main())
