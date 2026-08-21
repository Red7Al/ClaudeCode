"""Authoritative daily Best Settings audit.

This is deliberately independent of a browser session.  It reviews every enforceable
configuration in the retained annual and three-year populations, records the data
coverage first, and only publishes a recommendation when the complete trigger-time
feature set is present.  It never sends orders or changes a user's configuration.
"""

import datetime as dt
import logging
import math
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("best_settings_audit")

MODEL = {"wallet": 10000, "minimum_trade": 25, "calc_model": "2026-08-15-exit-rule"}
STAKES, OPENS = (1, 2, 3, 5, 7.5, 10), (3, 5, 8, 12, 20, 25, 50)
RRS, QUALS, VSCORES, RVOLS, BOOLS = (3, 5, 8), (0, 50, 75), (0, 4, 8), (0, 1.5, 1.8), (False, True)


def _effective_max_open(stake_fraction):
    return max(1, math.floor(1 / max(0.000001, stake_fraction)))


def _leverage(row):
    return {"FX": 30, "Indices": 20, "Commodities": 10}.get(row.get("market"), 5)


def _exit_date(row):
    # Exact browser rule: unresolved trades retain capacity through the review, but their recorded
    # mark-to-market return is settled once at the window end for the reported final wallet.
    if str(row.get("outcome") or "") == "OPEN":
        return "9999-99-99"
    return str(row.get("exit_date") or "9999-99-99")[:10]


def _replay(rows, stake_fraction, requested_max_open):
    wallet = peak = 1.0
    drawdown = 0.0
    peak_open = 0
    funded = 0
    open_rows = []
    min_stake = MODEL["minimum_trade"] / MODEL["wallet"]
    max_open = min(requested_max_open, _effective_max_open(stake_fraction))

    def settle(until):
        nonlocal wallet, peak, drawdown
        open_rows.sort(key=lambda item: item[0])
        while open_rows and open_rows[0][0] <= until:
            _, margin, net = open_rows.pop(0)
            del margin
            wallet += net
            peak = max(peak, wallet)
            drawdown = max(drawdown, (peak - wallet) / peak if peak else 0)

    for row in rows:
        settle(str(row.get("trig_date") or "")[:10])
        stake = wallet * stake_fraction
        margin = stake / _leverage(row)
        used = sum(item[1] for item in open_rows)
        if stake + 1e-12 < min_stake or len(open_rows) >= max_open or used + margin > wallet + 1e-9:
            continue
        performance = float(row["return_pct"])
        open_rows.append((_exit_date(row), margin, stake * performance / 100.0))
        funded += 1
        peak_open = max(peak_open, len(open_rows))
    settle("9999-99-99")
    return {"return": wallet - 1, "max_drawdown": drawdown, "funded_trades": funded, "peak_open": peak_open}


def _robust(rows, stake_fraction, max_open):
    result = _replay(rows, stake_fraction, max_open)
    quarters = defaultdict(list)
    for row in rows:
        date = str(row.get("trig_date") or "")
        if len(date) >= 7:
            quarters[f"{date[:4]} Q{(int(date[5:7]) - 1) // 3 + 1}"].append(row)
    returns = [_replay(part, stake_fraction, max_open)["return"] for part in quarters.values()]
    positive = sum(value > 0 for value in returns)
    consistency = positive / len(returns) if returns else 0
    result.update({
        "quarters": len(returns), "positive_quarters": positive,
        "score": (result["return"] / (result["max_drawdown"] + .02))
                 * (.5 + .5 * consistency) * min(1, result["funded_trades"] / 40),
    })
    return result


def _dedupe(rows):
    best = {}
    for row in rows:
        key = (row.get("ticker"), str(row.get("trig_date") or "")[:10])
        current = best.get(key)
        if current is None or float(row.get("return_pct") or -float("inf")) > float(current.get("return_pct") or -float("inf")):
            best[key] = row
    return sorted(best.values(), key=lambda r: (str(r.get("trig_date") or ""), str(r.get("ticker") or "")))


def _scopes(rows):
    markets = defaultdict(int)
    for row in rows:
        if row.get("market"):
            markets[row["market"]] += 1
    result = [("All markets", lambda r: True)]
    for market, count in sorted(markets.items(), key=lambda pair: (-pair[1], pair[0]))[:5]:
        if count >= 30:
            result.append((f"Market: {market}", lambda r, market=market: r.get("market") == market))
    for label, lower, upper in (("MCap < 2bn", 0, 2e9), ("MCap 2–10bn", 2e9, 1e10),
                                ("MCap 10–100bn", 1e10, 1e11), ("MCap 100bn+", 1e11, None)):
        result.append((label, lambda r, lower=lower, upper=upper:
                       r.get("mcap") is not None and float(r["mcap"]) >= lower
                       and (upper is None or float(r["mcap"]) < upper)))
    return result


def _eligible(rows, test, rr, quality, volume_score, rvol, require_vwap, require_atr):
    return [row for row in rows if test(row) and row.get("rr") is not None and float(row["rr"]) >= rr
            and (not quality or float(row["quality"]) >= quality)
            and (not volume_score or float(row["volume_score"]) >= volume_score)
            and (not rvol or float(row["rvol"]) >= rvol)
            and (not require_vwap or row["above_vwap"] is True)
            and (not require_atr or row["atr_expanding"] is True)]


def _full_grid(rows):
    """Evaluate the complete enforceable grid.  No quick-score or finalist shortlist is permitted."""
    evaluated = 0
    best = None
    for scope, test in _scopes(rows):
        for rr in RRS:
            for quality in QUALS:
                for volume_score in VSCORES:
                    for rvol in RVOLS:
                        for require_vwap in BOOLS:
                            for require_atr in BOOLS:
                                sequence = _eligible(rows, test, rr, quality, volume_score, rvol, require_vwap, require_atr)
                                if len(sequence) < 20:
                                    continue
                                for stake in STAKES:
                                    seen_open = set()
                                    for requested_open in OPENS:
                                        max_open = min(requested_open, _effective_max_open(stake / 100.0))
                                        if max_open in seen_open:
                                            continue
                                        seen_open.add(max_open)
                                        result = _robust(sequence, stake / 100.0, max_open)
                                        if result["funded_trades"] < 20:
                                            continue
                                        evaluated += 1
                                        candidate = {"scope": scope, "min_rr": rr, "min_quality": quality,
                                                     "min_volume_score": volume_score, "min_rvol": rvol,
                                                     "require_above_vwap": require_vwap,
                                                     "require_atr_expanding": require_atr,
                                                     "max_position_pct": stake, "max_open": max_open,
                                                     "eligible_trades": len(sequence), **result}
                                        if best is None or (candidate["score"], candidate["return"]) > (best["score"], best["return"]):
                                            best = candidate
    return {"evaluated_configurations": evaluated, "best": best}


def run():
    from hvf_web import server
    import web_store

    today = dt.date.today()
    rows = _dedupe([row for row in server._sqa_all_rows()
                    if row.get("trig_date") and row.get("return_pct") is not None
                    and str(row["trig_date"]) >= (today - dt.timedelta(days=365 * 3)).isoformat()])
    features = server._volscore_trigger_feature_map(3)
    missing = defaultdict(int)
    enriched = []
    for row in rows:
        row = dict(row)
        feature = features.get((row["ticker"], str(row["trig_date"])[:10]), {})
        row.update(feature)
        for name in ("rvol", "volume_score", "above_vwap", "atr_expanding"):
            if row.get(name) is None:
                missing[name] += 1
        enriched.append(row)
    report = {"schema": 1, "audit_date": today.isoformat(), "model": MODEL,
              "data_through": max((str(r["trig_date"])[:10] for r in enriched), default=None),
              "population_rows": len(enriched), "missing_trigger_features": dict(missing),
              "status": "blocked_data_quality" if missing else "running"}
    if missing:
        report["headline"] = "No supported recommendation: trigger-feature evidence is incomplete."
        web_store.save_json_store("best_settings_full_grid_audit", report)
        log.error("Best Settings audit blocked: %s", dict(missing))
        return report

    annual = _full_grid([r for r in enriched if str(r["trig_date"]) >= (today - dt.timedelta(days=365)).isoformat()])
    three_year = _full_grid(enriched)
    best_annual, best_three = annual["best"], three_year["best"]
    supported = (best_annual is not None and best_three is not None and best_three["funded_trades"] > 125
                 and best_three["return"] >= best_annual["return"] * .8)
    report.update({"annual": annual, "three_year": three_year, "status": "complete",
                   "three_year_supported": supported,
                   "headline": ("Three-year configuration independently verified." if supported else
                                "No supported three-year recommendation under the stated evidence rule.")})
    if not web_store.save_json_store("best_settings_full_grid_audit", report):
        raise RuntimeError("full-grid audit was calculated but could not be persisted")
    log.info("Best Settings full-grid audit complete: annual=%s, three-year=%s, supported=%s",
             annual["evaluated_configurations"], three_year["evaluated_configurations"], supported)
    return report


if __name__ == "__main__":
    run()
