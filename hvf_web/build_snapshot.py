# ======================================================================================================================
# File:         hvf_web/build_snapshot.py
# Author:       Alex Hind
# Created:      2026-06-27
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Builds the data snapshot for the HVF website (user 2026-06-27). Scans the universe with the live engine, enriches each
# instrument with fundamentals (sector / P-E / insider ownership / broker analysis + its change over months), derives the
# RW Rule 1-5 verdicts, and captures the exact X-tweet text — then writes hvf_web/snapshot.json. The Flask server
# (hvf_web/server.py) reads that file; PNGs are rendered on demand by the server, not stored here.
#
# Heavy (≈150 instruments x engine + 2 yfinance calls). Run periodically: `python -m hvf_web.build_snapshot`.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-27  Alex Hind   Initial build.
# ======================================================================================================================

import os
import json
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hvf_web.build")

_HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(_HERE, "snapshot.json")


def _location_of(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".L"):
        return "UK"
    if t.endswith("=X") or t in ("USDJPY", "EURUSD", "GBPUSD"):
        return "FX"
    return "US"


def _derive_rules(r: dict) -> list:
    """RW Rule 1-5 verdicts derived from the result fields. Each = {n, name, verdict, note}.
    verdict in PASS / DEVELOPING / FAIL. Mirrors hvf_clean's rule order."""
    trend = (r.get("long_trend") or "").upper()
    typ   = r.get("hvf_type")
    h1, h3 = r.get("h1_level"), r.get("h3_level")
    l1, l3 = r.get("l1_level"), r.get("l3_level")
    tight  = r.get("tightness") or r.get("convergence")
    rr     = r.get("risk_reward")
    target = r.get("target")
    sig    = r.get("hvf_signal")
    rules = []
    # Rule 1 — clear prior trend, direction matches
    r1 = "PASS" if trend in ("STRONG_UPTREND", "UPTREND", "STRONG_DOWNTREND", "DOWNTREND") else "FAIL"
    rules.append({"n": 1, "name": "Prior trend", "verdict": r1,
                  "note": f"trend {trend or 'n/a'} -> {('BULLISH' if 'UP' in trend else 'BEARISH') if r1=='PASS' else 'no clear trend'}"})
    # Rule 2 — three alternating real swings
    have = all(x is not None for x in (h1, h3, l1, l3))
    r2 = "PASS" if have else "DEVELOPING"
    rules.append({"n": 2, "name": "Three swings", "verdict": r2,
                  "note": (f"H1 {h1} > H3 {h3}; L1 {l1} < L3 {l3}" if have else "swings not all confirmed")})
    # Rule 3 — tightness <= 35%
    if isinstance(tight, (int, float)):
        r3 = "PASS" if tight <= 0.35 else "FAIL"
        rules.append({"n": 3, "name": "Tightness <=35%", "verdict": r3, "note": f"tightness {tight*100:.0f}%"})
    else:
        rules.append({"n": 3, "name": "Tightness <=35%", "verdict": "DEVELOPING", "note": "not computable"})
    # Rule 4 — levels / target
    r4 = "PASS" if (target and target > 0) else "FAIL"
    rules.append({"n": 4, "name": "Levels & target", "verdict": r4,
                  "note": (f"entry {h3 if typ=='BULLISH' else l3}, target {target}" if r4=="PASS" else "target not computable")})
    # Rule 5 — R:R >= 3
    if isinstance(rr, (int, float)):
        r5 = "PASS" if rr >= 3.0 else "DEVELOPING"
        rules.append({"n": 5, "name": "R:R >= 3:1", "verdict": r5, "note": f"R:R {rr:.2f}:1"})
    else:
        rules.append({"n": 5, "name": "R:R >= 3:1", "verdict": "DEVELOPING", "note": "no R:R"})
    return rules


def _months_to_go(r: dict):
    """Rough funnel duration estimate: weeks of span already formed vs an ~12-week typical apex.
    Returns a float 'months to go' (>=0) or None."""
    span = r.get("funnel_span_weeks")
    if not isinstance(span, (int, float)):
        return None
    remaining_weeks = max(0.0, 12.0 - float(span))
    return round(remaining_weeks / 4.33, 1)


def build():
    from run_hvf_report import scan_universe
    from quality_report import fundamentals
    import yfinance as yf
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}

    log.info("scanning universe ...")
    scan = scan_universe()
    records = [r for results in scan.values() for r in results]
    log.info(f"{len(records)} HVF patterns found; enriching ...")

    # NB tweet text + card PNGs are rendered LAZILY by the server (per ticker, on demand) — NOT here.
    # Rendering all ~150 cards in the build blew OpenBLAS memory (collect-mode _generate_x_drafts).
    out = []
    for r in records:
        tk = r.get("ticker")
        try:
            f = fundamentals(tk)
        except Exception:
            f = {}
        try:
            info = yf.Ticker(YAHOO_MAP.get(tk, tk)).info or {}
        except Exception:
            info = {}
        pe = info.get("trailingPE") or info.get("forwardPE")
        broker = None
        if f.get("analyst_rated"):
            broker = {"buys": f.get("analyst_buys"), "holds": f.get("analyst_holds"),
                      "rated": f.get("analyst_rated"), "trend": f.get("analyst_trend")}
        out.append({
            "ticker": tk, "name": f.get("name") or r.get("name") or tk,
            "direction": "BULL" if r.get("hvf_type") == "BULLISH" else "BEAR",
            "location": _location_of(tk),
            "market": r.get("index"),
            "sector": f.get("sector"),
            "status": r.get("hvf_signal"),
            "quality": r.get("pattern_quality"),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) and pe > 0 else None,
            "timeframe": r.get("hvf_timeframe"),
            "months_to_go": _months_to_go(r),
            "rr": r.get("risk_reward"),
            "insider_pct": f.get("insider_pct"),
            "entry": r.get("h3_level"), "stop": r.get("stop_level"), "target": r.get("target"),
            "current_price": r.get("current_price"),
            "h3_date": r.get("h3_date"), "l3_date": r.get("l3_date"), "h1_date": r.get("h1_date"),
            "rules": _derive_rules(r),
            "tweet": None,            # rendered lazily by the server (/api/tweet/<ticker>)
            "broker": broker,
            # raw fields the server needs to re-render the card PNG
            "_card": {k: r.get(k) for k in (
                "ticker", "hvf_type", "hvf_signal", "hvf_timeframe", "h3_level", "stop_level", "target",
                "risk_reward", "h1_level", "h2_level", "l1_level", "l2_level", "l3_level",
                "h1_date", "h2_date", "h3_date", "l1_date", "l2_date", "l3_date", "current_price")},
        })

    snapshot = {"generated_utc": datetime.now(timezone.utc).isoformat(),
                "count": len(out), "records": out}
    with open(SNAPSHOT, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, default=str)
    log.info(f"wrote {SNAPSHOT} ({len(out)} records)")


if __name__ == "__main__":
    build()
