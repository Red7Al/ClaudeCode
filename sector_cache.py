# ======================================================================================================================
# File:         sector_cache.py
# Author:       Alex Hind
# Created:      2026-07-17
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Persistent ticker -> GICS sector cache (user 2026-07-17: "any ticker will be able to provide location, market, sector").
# Sector is a stable Yahoo property, but build_snapshot only fetched it for instruments with a LIVE signal — so 1059 of
# 1309 universe tickers had sector=None, which left the P-21b squeeze analysis with almost no sector to work with.
#
# Supabase is the shared primary copy (`web_json_store:sector_cache`) for multi-host/IONOS use; the original
# sector_cache.json remains a compatibility cache. FX / futures / indices legitimately have no sector and
# cache as "" (resolved, not missing) so they are not re-fetched every run.
#
# Public API:
#   get_sector(ticker)        cached sector, or None if not yet resolved (never hits the network)
#   backfill(tickers=None)    resolve missing sectors via yfinance and write back; returns count newly resolved
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-07-17  Alex Hind   Initial build — ticker->sector cache mirroring name_cache, one-off backfill.
# ======================================================================================================================

import json
import logging
import os
import time

log = logging.getLogger("sector_cache")

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_HERE, "sector_cache.json")

_cache = None       # ticker -> sector ("" = resolved but no sector, e.g. FX/futures)


def _load():
    global _cache
    if _cache is None:
        try:
            import web_store
            remote = web_store.load_json_store("sector_cache")
            if isinstance(remote, dict):
                _cache = remote
        except Exception as e:
            log.warning(f"Supabase sector cache unavailable; using local compatibility copy: {e}")
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except Exception:
            _cache = {}
    return _cache


def _save(cache):
    remote_ok = False
    try:
        import web_store
        remote_ok = web_store.save_json_store("sector_cache", cache)
    except Exception as e:
        log.warning(f"could not write Supabase sector cache: {e}")
    local_ok = False
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, CACHE_PATH)   # atomic: a killed backfill never leaves a half-written cache
        local_ok = True
    except OSError as e:
        log.warning(f"could not write local sector compatibility cache: {e}")
    if not (remote_ok or local_ok):
        raise OSError("sector cache could not be saved to Supabase or locally")


def get_sector(ticker: str):
    """Cached sector for `ticker`, or None if not resolved yet. Never hits the network — the hot path
    (snapshot build, squeeze analysis) must stay fast. "" in the cache (FX/futures) returns None too."""
    v = _load().get(ticker)
    return v or None


def backfill(tickers=None, force=False) -> int:
    """Resolve sectors for `tickers` (default: the whole universe) that are not already cached, via
    yfinance. Returns the number newly resolved. `force` re-resolves even cached ones."""
    import yfinance as yf
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    if tickers is None:
        from run_hvf_report import UNIVERSE
        tickers = sorted({t for ts in UNIVERSE.values() for t in ts})

    cache = _load()
    new = 0
    for i, tk in enumerate(tickers, 1):
        if not force and tk in cache:
            continue
        try:
            sec = (yf.Ticker(YAHOO_MAP.get(tk, tk)).info or {}).get("sector")
        except Exception as e:
            # yfinance rate-limits a long burst of .info calls ("Too Many Requests"). Leave the ticker
            # UNCACHED so the next run retries it, and pause so we stop hammering a limit that is already
            # tripped — the tail of a ~1300-ticker run is where this bites.
            log.debug(f"{tk}: sector lookup failed: {e}")
            if "Too Many Requests" in str(e) or "Rate limited" in str(e):
                time.sleep(2.0)
            continue
        cache[tk] = sec or ""              # "" = resolved, no sector (FX/futures/index)
        new += 1
        time.sleep(0.2)                    # be a good citizen — a steady drip does not trip the limiter
        if new % 50 == 0:
            _save(cache)                   # checkpoint, so a long run is resumable
            log.info(f"  sector backfill: {i}/{len(tickers)} seen, {new} newly resolved")
    _save(cache)
    return new


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    import argparse
    ap = argparse.ArgumentParser(description="Backfill the ticker->sector cache from yfinance.")
    ap.add_argument("--force", action="store_true", help="re-resolve even already-cached tickers")
    a = ap.parse_args()
    n = backfill(force=a.force)
    have = sum(1 for v in _load().values() if v)
    log.info(f"Sector cache: {n} newly resolved; {have} tickers now carry a sector "
             f"({len(_load())} cached total).")


if __name__ == "__main__":
    main()
