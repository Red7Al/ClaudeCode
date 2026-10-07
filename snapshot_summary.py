# ======================================================================================================================
# File:         snapshot_summary.py
# Author:       Alex Hind (via Claude)
# Created:      2026-10-07
#
# Write the DERIVED summary values into the snapshot, so the website reads them from the file it already
# has instead of re-deriving them from raw bars on every cache miss.
#
# WHY (account owner, repeatedly, and recorded as owner-endorsed in docs/HANDOVER-20260928.md section 3.1 --
# "if derived are causing issue - store them, rather than derive", and again 2026-10-07: "the summary data
# should be available in IONOS flat data files - this as been suggested multiple times"):
#
#   MEASURED 2026-10-07 on the live /api/records payload -- rvol, volume_score, above_vwap, atr_expanding
#   and mcap were present-but-None in 1,773 of 1,773 records. The snapshot schema has always carried the
#   fields; nothing ever filled them. price_action.get_hvf_signal_mtf, which produces the signal rows,
#   never computes VWAP position or merges atr_expanding, so hvf_web/build_snapshot.py passes None through.
#
#   So hvf_web/server.py rebuilt them per worker instead. MEASURED by differencing pg_stat_statements: ONE
#   call of the three-year map read 1,102,245 rows and the one-year map about 400,000 per call, against a
#   Supabase free-tier allowance of 5 GB a MONTH for the whole organisation. The data changes twice a day
#   (the 05:00 price refresh and the 18:30 scan), so nearly every one of those reads returned bars
#   identical to the previous read.
#
# THE PATTERN IS ALREADY PROVEN HERE. instrument_metrics_daily is written once a day, and because of that
# _live_instrument_metrics covers all 1,773 instruments in 1,772 rows and ZERO bars (measured the same day).
# This does the same for the remaining four.
#
# ONE DEFINITION, NOT A SECOND ONE. The values are computed by calling hvf_web.server's own helpers, the
# same functions /api/records and the Scanner Report email call, so a stored value cannot drift from what
# the endpoint would have produced (see memory results-winners-same-dataset -- two surfaces computing the
# same figure separately is this repository's recurring defect).
#
# RUNS WHERE THE DATA ALREADY IS. The publish job has the database and a working numpy; the IONOS web tier
# has neither cheaply. Enriching at publish time costs ONE set of reads a day instead of one per worker.
# ======================================================================================================================

import logging

log = logging.getLogger("snapshot_summary")

# The fields written, and the helper each comes from. Kept as data so the test can assert the full set
# rather than trusting a list in prose.
SUMMARY_FIELDS = ("rvol", "volume_score", "above_vwap", "atr_expanding", "mcap")


def enrich(snapshot: dict) -> dict:
    """Fill SUMMARY_FIELDS on every record, in place, and return the snapshot.

    FAIL-SOFT, deliberately: a helper that cannot reach the database leaves its field as it was rather
    than aborting the publication. A snapshot that publishes with an empty column degrades to the old
    behaviour (the server recomputes it); a snapshot that fails to publish takes the whole site down with
    it. The caller is the publish path, so that trade is not close.
    """
    records = (snapshot or {}).get("records") or []
    if not records:
        return snapshot

    from hvf_web.server import (_snapshot_rvol, _snapshot_volscore, _live_vwap_atr, _mcap_map)

    filled = {f: 0 for f in SUMMARY_FIELDS}

    def _safe(label, fn, default):
        try:
            return fn()
        except Exception as exc:                      # noqa: BLE001 - see the fail-soft note above
            log.warning("snapshot summary: %s unavailable (%s); leaving its field as found", label, exc)
            return default

    rvol = _safe("rvol", lambda: _snapshot_rvol(snapshot), {})
    vscore = _safe("volume_score", lambda: _snapshot_volscore(snapshot), {})
    vwap_atr = _safe("vwap/atr", lambda: _live_vwap_atr(snapshot), {})
    mcaps = _safe("mcap", lambda: _mcap_map() or {}, {})

    for r in records:
        tk = r.get("ticker")
        if not tk:
            continue
        v = rvol.get(tk)
        if v is not None:
            r["rvol"] = v
            filled["rvol"] += 1
        score = (vscore.get(tk) or {}).get("score")
        if score is not None:
            r["volume_score"] = score
            filled["volume_score"] += 1
        av, ae = vwap_atr.get(tk, (None, None))
        if av is not None:
            r["above_vwap"] = av
            filled["above_vwap"] += 1
        if ae is not None:
            r["atr_expanding"] = ae
            filled["atr_expanding"] += 1
        mc = mcaps.get(tk)
        if mc is not None:
            r["mcap"] = mc
            filled["mcap"] += 1

    snapshot["summary_fields"] = list(SUMMARY_FIELDS)
    log.info("snapshot summary written: %s over %d records",
             ", ".join(f"{k}={v}" for k, v in filled.items()), len(records))
    return snapshot


def carries_summary(snapshot: dict) -> bool:
    """Whether this snapshot was built with the summary values in it.

    Keyed on the marker written above, NOT on 'does any record have a non-None rvol'. RVOL is legitimately
    None for every non-TRIGGERED row, and on a day with no triggers at all it would be None everywhere --
    so a value-sniffing check would silently fall back to recomputing on exactly the quiet days. An
    explicit marker cannot be fooled that way, and an older snapshot without it keeps the old behaviour.
    """
    return bool((snapshot or {}).get("summary_fields"))
