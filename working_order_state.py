#!/usr/bin/env python3
# ======================================================================================================================
# File:         working_order_state.py
# Created:      2026-09-11
#
# THE ONE PLACE that decides what a working_orders row actually is. Nothing else may infer it.
#
# WHY. "Absent from IG's working-order book" has three different causes, and three pieces of code were each deciding
# for themselves which one it was:
#   * it FILLED and became a position;
#   * it is WATCHING, so no IG order was ever placed (the row carries a synthetic WATCH-... id);
#   * it is genuinely dead.
# run_working_order_sweep read absence as dead, full stop. On 2026-09-04 it expired 85 rows: 12 were fills, recovered
# by hand ("the sweep could not tell a fill from an expiry"), and 19 were live WATCHING rows still inside good-till.
# reconcile_fills then added a fourth opinion. One column, four interpretations.
#
# So absence is interpreted here and nowhere else. The states are what is TRUE of the row, not what to write in the
# status column -- the caller chooses its own label, because the sweep and the reconciler want different words for the
# same fact.
#
# IT FAILS SAFE. Anything that cannot be determined is LIVE, which every caller treats as leave alone. DEAD requires
# positive evidence, never merely an unanswered question.
# ======================================================================================================================

WATCHING = "WATCHING"   # no IG order exists yet, by design -- absence proves nothing
LIVE     = "LIVE"       # IG is holding it, or we cannot tell: leave it alone
FILLED   = "FILLED"     # gone from IG and an open position matches it
DEAD     = "DEAD"       # gone from IG, nothing matches, and it cannot come back

SIZE_ABS_TOLERANCE = 0.011      # smallest dealable increments; a fill may differ slightly from the request
SIZE_PCT_TOLERANCE = 0.05


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _size_matches(a, b):
    x, y = _num(a), _num(b)
    return x is not None and y is not None and abs(y - x) <= max(SIZE_ABS_TOLERANCE, abs(x) * SIZE_PCT_TOLERANCE)


def _day(v):
    """'YYYY-MM-DD' from a date, datetime or string; None if there isn't one.

    Normalised HERE rather than in each caller: the sweep reads placed_at::date (a date) and IG returns an ISO
    string, and comparing those two raises TypeError. A caller that has to remember a conversion will eventually
    forget, and this function exists precisely so nobody has to remember anything.
    """
    if v is None or v == "":
        return None
    return (v.isoformat() if hasattr(v, "isoformat") else str(v))[:10]


def classify(rows, ig_order_ids, positions, now=None, claimed=()):
    """{deal_id: (state, matched_position_or_None)} for every row given.

    `rows`      [{deal_id, status, epic, direction, size, placed_at, good_till}]
    `positions` [{deal_id, epic, direction, size, created}] -- IG's open positions
    `claimed`   position deal ids already recorded as some other row's fill

    Matching is unambiguous or it does not happen: exactly one candidate position for exactly one candidate row,
    same epic and direction, size within tolerance, and the position opened no earlier than the order was placed.
    A position two rows could claim is given to neither -- a wrong attribution feeds the wrong setup's R:R into a
    decision that closes a real position, and leaving it alone costs nothing.
    """
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    claimed = {str(c) for c in (claimed or []) if c}
    out, cands = {}, {}

    def expired(row):
        gt = row.get("good_till")
        return bool(gt and gt < now)

    # Pass 1 -- the rows whose state does not depend on matching at all.
    open_rows = []
    for r in rows:
        did = str(r.get("deal_id") or "")
        if str(r.get("status") or "") == WATCHING or did.startswith("WATCH-"):
            # Never expected at IG. It can only die of old age.
            out[did] = (DEAD if expired(r) else WATCHING, None)
        elif not did or did.startswith("PAPER-"):
            # Nothing to ask IG about: no id, or a paper order IG never saw.
            out[did] = (DEAD if expired(r) else LIVE, None)
        elif did in ig_order_ids:
            out[did] = (LIVE, None)
        else:
            open_rows.append(r)

    # Pass 2 -- gone from IG: filled, or dead. Candidates both ways before anything is decided.
    for r in open_rows:
        ok = []
        for p in positions:
            if str(p.get("deal_id") or "") in claimed:
                continue
            if str(p.get("epic") or "") != str(r.get("epic") or ""):
                continue
            if str(p.get("direction") or "") != str(r.get("direction") or ""):
                continue
            if not _size_matches(r.get("size"), p.get("size")):
                continue
            placed, created = _day(r.get("placed_at")), _day(p.get("created"))
            if placed and created and created < placed:
                continue        # a position older than the order cannot be its fill

            ok.append(p)
        cands[str(r.get("deal_id"))] = ok

    wanted = {}
    for did, ok in cands.items():
        for p in ok:
            wanted.setdefault(str(p.get("deal_id")), []).append(did)

    for r in open_rows:
        did = str(r.get("deal_id"))
        ok = cands.get(did) or []
        if len(ok) == 1 and len(wanted.get(str(ok[0].get("deal_id")), [])) == 1:
            out[did] = (FILLED, ok[0])
        elif ok:
            out[did] = (LIVE, None)      # ambiguous: refuse to guess, leave it alone
        else:
            out[did] = (DEAD, None)
    return out
