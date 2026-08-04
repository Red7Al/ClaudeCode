"""Compare shorter VolumeScore windows with the unchanged production recipe."""

from __future__ import annotations

import datetime as dt
import heapq
import itertools
from contextlib import contextmanager

import volume_score as vs
from hvf_web import server


CANDIDATES = {
    "early-5": (5, 5, 5, 5, 15),
    "early-8": (8, 8, 8, 7, 20),
    "early-10": (10, 10, 10, 7, 30),
    "early-15": (15, 15, 15, 10, 45),
    "production": (20, 20, 20, 14, 60),
}
WINDOW_NAMES = ("RVOL_BARS", "VWAP_BARS", "OBV_BARS", "ATR_PERIOD", "LVN_LOOKBACK")
START_WALLET = 10_000.0
STAKE_FRACTION = 0.02
DEFAULT_MAX_OPEN = 10
DEFAULT_LEVERAGE = {"fx": 30.0, "equities": 5.0, "commodities": 10.0, "indices": 20.0}


@contextmanager
def score_windows(values):
    """Apply a candidate temporarily and always restore production constants."""
    original = tuple(getattr(vs, name) for name in WINDOW_NAMES)
    try:
        for name, value in zip(WINDOW_NAMES, values):
            setattr(vs, name, value)
        yield
    finally:
        for name, value in zip(WINDOW_NAMES, original):
            setattr(vs, name, value)


def _population():
    cut12 = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    rows = [r for r in server._sqa_all_rows()
            if (r.get("trig_date") or "") >= cut12 and r.get("trig_date") and r.get("entry")]
    earliest = {}
    for row in rows:
        trigger = dt.date.fromisoformat(str(row["trig_date"])[:10])
        earliest[row["ticker"]] = min(earliest.get(row["ticker"], trigger), trigger)
    if not earliest:
        return rows, {}
    from db_pool import get_db
    db = get_db()
    try:
        bars = server._perf_bars(db, earliest, lookback_days=server._VOLSCORE_LOOKBACK_DAYS)
    finally:
        db.close()
    return rows, bars


def _leverage_for(row, leverage):
    market = row.get("market") or ""
    kind = "fx" if market == "FX" else "indices" if market == "Indices" else \
           "commodities" if market == "Commodities" else "equities"
    return float(leverage[kind])


def _stake_compound(rows, start=START_WALLET, stake_fraction=STAKE_FRACTION,
                    max_open=DEFAULT_MAX_OPEN, leverage=None):
    """Replay with user max-open plus cash reserved as margin until each close."""
    leverage = leverage or DEFAULT_LEVERAGE
    seq = sorted((r for r in rows if r.get("return_pct") is not None and r.get("trig_date") and r.get("exit_date")),
                 key=lambda r: r["trig_date"])
    wallet, reserved_margin, taken, skipped = float(start), 0.0, 0, 0
    counter = itertools.count()
    open_positions = []

    def close(position):
        nonlocal wallet, reserved_margin
        _exit_date, _seq, stake, margin, return_pct = position
        wallet = max(0.0, wallet + stake * return_pct / 100.0)
        reserved_margin = max(0.0, reserved_margin - margin)

    for row in seq:
        while open_positions and open_positions[0][0] <= row["trig_date"]:
            close(heapq.heappop(open_positions))
        stake = wallet * stake_fraction
        margin = stake / _leverage_for(row, leverage)
        if len(open_positions) >= max_open or margin > wallet - reserved_margin:
            skipped += 1
            continue
        reserved_margin += margin
        heapq.heappush(open_positions, (row["exit_date"], next(counter), stake, margin, row["return_pct"]))
        taken += 1
    while open_positions:
        close(heapq.heappop(open_positions))
    return {"final": round(wallet), "trades": taken, "skipped": skipped, "max_open": max_open}


def _metrics(rows, max_open=DEFAULT_MAX_OPEN):
    resolved = [r for r in rows if r.get("return_pct") is not None]
    passed = [r for r in resolved if r["score"] >= vs.PASS_THRESHOLD]
    failed = [r for r in resolved if r["score"] < vs.PASS_THRESHOLD]

    def avg(items):
        return sum(r["return_pct"] for r in items) / len(items) if items else 0.0

    def win(items):
        return 100 * sum(r["return_pct"] > server._SQA_BE for r in items) / len(items) if items else 0.0

    compound = _stake_compound(passed, max_open=max_open) if passed else None

    return {
        "resolved": len(resolved), "passed": len(passed),
        "keep_pct": 100 * len(passed) / len(resolved) if resolved else 0.0,
        "pass_win_pct": win(passed), "fail_win_pct": win(failed),
        "pass_avg_return": avg(passed), "fail_avg_return": avg(failed),
        "return_separation": avg(passed) - avg(failed),
        "win_separation": win(passed) - win(failed),
        "compound_final": compound.get("final") if compound else None,
        "compound_trades": compound.get("trades") if compound else 0,
    }


def compare_candidates(max_open=DEFAULT_MAX_OPEN):
    rows, bars_by_ticker = _population()
    results = []
    for label, windows in CANDIDATES.items():
        scored = []
        with score_windows(windows):
            for row in rows:
                bars = bars_by_ticker.get(row["ticker"], [])
                trigger = dt.date.fromisoformat(str(row["trig_date"])[:10])
                dates = [bar[0] for bar in bars]
                if trigger not in dates:
                    later = [date for date in dates if date >= trigger]
                    trigger = later[0] if later else (dates[-1] if dates else trigger)
                result = vs.volume_score(
                    bars, trigger, row["direction"] == "BULLISH",
                    squeeze_strong=row.get("quality") is not None and row["quality"] >= 60,
                )
                scored.append({**row, "score": result["score"]})
        results.append({"candidate": label, "windows": windows, **_metrics(scored, max_open=max_open)})
    return results


def main():
    for max_open in (3, 5, 10, 20):
        print(f"\nmax_open={max_open}; wallet £10,000; position 2%; margin reserved using IG retail leverage")
        print("candidate  windows                 kept       win pass/fail   avg return pass/fail   wallet final/trades   separation")
        for r in compare_candidates(max_open=max_open):
            print(f"{r['candidate']:<10} {str(r['windows']):<23} {r['passed']:>4}/{r['resolved']:<4} "
                  f"{r['keep_pct']:>5.1f}%  {r['pass_win_pct']:>5.1f}/{r['fail_win_pct']:<5.1f}  "
                  f"{r['pass_avg_return']:>7.2f}/{r['fail_avg_return']:<7.2f}  "
                  f"£{(r['compound_final'] or 0):>8,.0f}/{r['compound_trades']:<4}  "
                  f"return {r['return_separation']:+.2f}, win {r['win_separation']:+.1f}pt")


if __name__ == "__main__":
    main()
