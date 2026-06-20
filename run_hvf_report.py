# ======================================================================================================================
# File:         run_hvf_report.py
# Author:       Alex Hind
# Created:      2026-06-05
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily HVF Report — scans FTSE 100, FTSE 250 and S&P 500 constituents
# for Hunt Volatility Funnel patterns and posts a structured report to Slack.
#
# Three sections:
#   READY/TRIGGERED  — patterns meeting ≥2.5:1 R:R, tradeable now
#   DEVELOPING       — valid pattern structure, R:R < 2.5:1, watch list
#   IN PLAY          — patterns that have TRIGGERED and are open/active
#
# Multi-timeframe scanner (daily-220, daily-180, daily-90, daily-60, daily-30, weekly):
#   Shorter timeframes catch post-peak reversals and tight recent funnels
#   that the standard 220-day lookback misses.
#
# Usage:
#   python run_hvf_report.py
#
# Or trigger via GitHub Actions: "HVF Daily Report" workflow.
#
# Environment Variables:
#   SLACK_SIGNALS (webhook URL for #claude-trading-signals)
#   SUPABASE_USER, SUPABASE_DB_PASSWORD (for logging results)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.14.0  2026-06-19  Alex Hind   Report list correctness (user 2026-06-19): each per-market list is numbered from 1,
#                                 restarting per market (However #2), and the sub-header reads "top N of M candidates"
#                                 (However #3) for both TRADEABLE and DEVELOPING. ("confirmed"/watch list numbering is
#                                 handled where that report is built.)
# 1.13.0  2026-06-17  Alex Hind   Morning live-X (user 2026-06-17): X drafts now changed_only (top X_DRAFT_PER_MARKET/market,
#                                 re-shown only when confirmations change); the top X_PUBLISH_TOP_N/market of that changed
#                                 set are auto-published LIVE to X (_publish_top_per_market_to_x -> publish_tickers_to_x,
#                                 spaced to avoid overlap). Analytical report unchanged (still PER_MARKET_TOP_N).
# 1.12.0  2026-06-16  Alex Hind   Per-market sections (user 2026-06-16: "top 10 by market"): TRADEABLE and DEVELOPING are
#                                 now GROUPED by market (FTSE100 / FTSE250 / S&P500…), each showing its top PER_MARKET_TOP_N
#                                 in weight order, instead of one flat global top-HVF_REPORT_TOP_N list. Reverses the
#                                 2026-06-13 flat-list change. Line-building extracted to _tradeable_line/_developing_line;
#                                 grouping via price_action.group_by_market; _index_short delegates to market_short (SoT).
# 1.11.0  2026-06-15  Alex Hind   Each tradeable line gains a compact technical read (user 2026-06-15): "TA: N Buy / N
#                                 Sell / N Hold · Div growth ±x%" via technical_summary.summary_line (full per-indicator
#                                 detail is in the dossier). Supplementary context only; one extra yfinance fetch per line.
# 1.10.0  2026-06-15  Alex Hind   "Also on" now lists FULL figures (Entry/Stop/Target/R:R/Q) per other timeframe (user
#                                 2026-06-15), not just a state emoji — mtf_timeframes carries the per-timeframe levels.
#                                 Headline stays the primary (anchored/validated); "Also on" rows are raw detection.
# 1.9.0   2026-06-15  Alex Hind   Backlog #9b: tradeable lines whose stop is < TIGHT_STOP_MIN_PCT of price now carry a
#                                 "⚠️ Stop only N% of price — too tight for IG intraday; not auto-traded" label (user
#                                 2026-06-15, option b). The funnel stays in the report (valid pattern); it just isn't
#                                 traded. The inflated R:R on these is the tell of the tiny stop.
# 1.8.0   2026-06-15  Alex Hind   Report lines now show the FULL instrument name next to every ticker (user 2026-06-15;
#                                 memory/feedback_instrument_names). notify.fmt() only knows the ~76 epic_lookup names, so
#                                 scanned constituents (VOD.L, NXT.L, …) showed a bare ticker — new _label() resolves them
#                                 via the yfinance-backed _resolve_name (epic_lookup → yfinance, cached). Display only.
# 1.7.0   2026-06-14  Alex Hind   Code-review: tradeable sort now uses the canonical price_action.hvf_weight() key (single
#                                 source of truth for the "all lists in weight order" rule — was a local SIGNAL_RANK dict,
#                                 now removed). Behaviour identical for READY/TRIGGERED lists; R:R is now a deterministic
#                                 tiebreaker where two setups share signal+quality. developing sort (R:R only) unchanged.
# 1.5.0   2026-06-13  Alex Hind   FIX (code-review): `from itertools import groupby` was imported inside the `if tradeable:`
#                                 block, making it a function-local — on a day with DEVELOPING setups but ZERO tradeable
#                                 ones the developing branch raised UnboundLocalError and the whole report crashed (no
#                                 Slack post). Hoisted the import to module scope (used by both sections).
# 1.6.0   2026-06-13  Alex Hind   Report shows TOP HVF_REPORT_TOP_N setups in global WEIGHT order (TRIGGERED first then
#                                 quality), NOT grouped by index; each line tagged with its market (user 2026-06-13:
#                                 "too many setups, not in weight order"). Full counts stay in the summary line. Removed
#                                 the now-unused groupby import.
# 1.4.0   2026-06-13  Alex Hind   One instrument, all timeframes (user 2026-06-13): each setup is shown ONCE and the
#                                 other timeframes its funnel appears on are listed inline ("Also on:" with a state emoji
#                                 in TRADEABLE, "· also …" in DEVELOPING), sourced from the new mtf_timeframes field on
#                                 the get_hvf_signal_mtf result. Footer legend added. Which instruments/sections appear is
#                                 unchanged — the scanner still returns one chosen result per instrument.
# 1.3.0   2026-06-12  Alex Hind   categorise(): UK (.L) tradeable setups are IG-validated (validate_hvf_with_ig)
#                                 before posting — weight-ordered first so the best setups get the allowance, capped at
#                                 15/run; IG mismatches are demoted to DEVELOPING.
# 1.2.0   2026-06-11  Alex Hind   FTSE100 expanded from 71 to 100 constituents; FTSE250 expanded from 40 to 250
#                                 constituents. Both sourced from Wikipedia 2026-06-11. BT.A (LSE) corrected to BT-A.L
#                                 (Yahoo ticker).
# 1.1.0   2026-06-11  Alex Hind   FTSE250 expanded from 40 to 250 constituents (full index sourced from Wikipedia
#                                 2026-06-11).
# 1.0.0   2026-06-05  Alex Hind   Initial build. Multi-timeframe HVF scanner covering FTSE 100, FTSE 250, S&P 500 and
#                                 DAX.
# 1.0.2   2026-06-05  Alex Hind   Removed DAX (Germany) from UNIVERSE scan. DAX suspended until further notice.
# 1.0.3   2026-06-05  Alex Hind   Fixed stale 2.0/2:1 references in categorise() docstring and Slack summary block — now
#                                 2.5. Results logged to hvf_scan_log table.
# 1.0.1   2026-06-05  Alex Hind   Raised tradeable filter to rr >= 2.5 and updated footer label and section descriptions
#                                 to reflect new 2.5:1 threshold.
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
import logging
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(override=True)

from config import HVF_MIN_RR, PER_MARKET_TOP_N, MARKET_ORDER, X_PUBLISH_TOP_N   # single source of truth for thresholds/limits
# Display labels go through _label() (yfinance-backed) — notify.fmt() alone only
# knows the ~76 epic_lookup names, so scanned constituents showed a bare ticker.

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hvf_report")

# ----------------------------------------------------------------------------------------------------------------------
# Universe definitions
# ----------------------------------------------------------------------------------------------------------------------

FTSE100 = [
    # Full 100 constituents — sourced from Wikipedia 2026-06-11, converted to Yahoo .L format.
    # BT.A (LSE) → BT-A.L (Yahoo uses hyphen); SN. → SN.L; NG. → NG.L; BA. → BA.L; JD. → JD.L
    "III.L", "ADM.L", "AAF.L", "ALW.L", "AAL.L", "ANTO.L", "ABF.L", "AZN.L",
    "AUTO.L", "AV.L", "BAB.L", "BA.L", "BARC.L", "BTRW.L", "BEZ.L", "BKG.L",
    "BP.L", "BATS.L", "BLND.L", "BT-A.L", "BNZL.L", "BRBY.L", "CNA.L", "CCEP.L",
    "CCH.L", "CPG.L", "CTEC.L", "CRDA.L", "DCC.L", "DGE.L", "DPLM.L", "EDV.L",
    "ENT.L", "EXPN.L", "FCIT.L", "FRES.L", "GAW.L", "GLEN.L", "GSK.L", "HLN.L",
    "HLMA.L", "HSX.L", "HWDN.L", "HSBA.L", "ICG.L", "IGG.L", "IHG.L", "IMI.L",
    "IMB.L", "INF.L", "IAG.L", "ITRK.L", "JD.L", "BGEO.L", "KGF.L", "LAND.L",
    "LGEN.L", "LLOY.L", "LMP.L", "LSEG.L", "MNG.L", "MKS.L", "MRO.L", "MTLN.L",
    "MNDI.L", "NG.L", "NWG.L", "NXT.L", "PSON.L", "PSH.L", "PSN.L", "PCT.L",
    "PRU.L", "RKT.L", "REL.L", "RTO.L", "RMV.L", "RIO.L", "RR.L", "SGE.L",
    "SBRY.L", "SDR.L", "SMT.L", "SGRO.L", "SVT.L", "SHEL.L", "SMIN.L", "SN.L",
    "SPX.L", "SSE.L", "STAN.L", "SDLF.L", "STJ.L", "TSCO.L", "BBOX.L", "ULVR.L",
    "UU.L", "VOD.L", "WEIR.L", "WTB.L",
]

FTSE250 = [
    # Full 250 constituents — sourced from Wikipedia 2026-06-11, converted to Yahoo .L format.
    # Investment trusts / closed-end funds are included; scanner handles Yahoo misses gracefully.
    "3IN.L", "FOUR.L", "AAS.L", "ABDN.L", "ASL.L", "AEP.L", "ALFA.L", "ATT.L",
    "AO.L", "APN.L", "ASHM.L", "AIE.L", "AML.L", "ATYM.L", "AGT.L", "AVON.L",
    "BME.L", "BGFD.L", "USA.L", "BBY.L", "BCG.L", "BNKR.L", "BAG.L", "AJB.L",
    "BWY.L", "BHMG.L", "BYG.L", "BPCR.L", "BRGE.L", "BRSC.L", "BRWM.L", "BSIF.L",
    "BOY.L", "BREE.L", "BPT.L", "BUT.L", "BYIT.L", "CCR.L", "CLDN.L", "CGT.L",
    "CWR.L", "CHG.L", "CSN.L", "CHRY.L", "CTY.L", "CKN.L", "CBG.L", "CMCX.L",
    "COA.L", "CCC.L", "COST.L", "CWK.L", "CURY.L", "CVSG.L", "DLN.L", "DSCV.L",
    "DOM.L", "DRX.L", "DOCS.L", "DNLM.L", "EZJ.L", "EDIN.L", "EWI.L", "ELM.L",
    "ENOG.L", "ESCT.L", "EWG.L", "FCSS.L", "FEML.L", "FEV.L", "FSV.L", "FGT.L",
    "FGP.L", "FGEN.L", "FSG.L", "FRAS.L", "FCH.L", "GFRD.L", "GAMA.L", "GBG.L",
    "GCP.L", "GEN.L", "GNS.L", "GSCT.L", "GDWN.L", "GFTU.L", "GRI.L", "GPE.L",
    "UKW.L", "GNC.L", "GRG.L", "HMSO.L", "HBR.L", "HVPE.L", "HWG.L", "HAS.L",
    "HTWS.L", "HFEL.L", "HSL.L", "HRI.L", "HGT.L", "HICL.L", "HIK.L", "HILS.L",
    "HFG.L", "HOC.L", "BOWL.L", "HTG.L", "IBST.L", "ICGT.L", "IEM.L", "INCH.L",
    "IHP.L", "IPF.L", "INPP.L", "IWG.L", "IAD.L", "INVP.L", "IPO.L", "ITH.L",
    "ITV.L", "JMAT.L", "JSG.L", "JAM.L", "JCH.L", "JEMI.L", "JMGI.L", "JEDT.L",
    "JEGI.L", "JGGI.L", "JIGI.L", "JFJ.L", "JTC.L", "JUP.L", "KNOS.L", "KLR.L",
    "KIE.L", "LRE.L", "LWDB.L", "EMG.L", "MSLH.L", "MEGP.L", "MRC.L", "MRCH.L",
    "MTRO.L", "MAB.L", "MTO.L", "GROW.L", "MNKS.L", "MONY.L", "MOON.L", "MGAM.L",
    "MGNS.L", "MUT.L", "MYI.L", "NBPE.L", "NCC.L", "N91.L", "NAS.L", "OCI.L",
    "OCDO.L", "OSB.L", "OXB.L", "OXIG.L", "ONT.L", "PHI.L", "PAGE.L", "PAF.L",
    "PINT.L", "PIN.L", "PAG.L", "PEY.L", "PPET.L", "PNN.L", "PNL.L", "PETS.L",
    "PTEC.L", "PLUS.L", "PCGH.L", "POLN.L", "PPH.L", "PFD.L", "PHP.L", "PRN.L",
    "QQ.L", "QLT.L", "RNK.L", "RPI.L", "RAT.L", "RSW.L", "RHIM.L", "RCP.L",
    "ROR.L", "RS1.L", "RTW.L", "RICA.L", "SAFE.L", "SAGA.L", "SVS.L", "MNTN.L",
    "ATR.L", "SDP.L", "SOI.L", "SAIN.L", "SEIT.L", "SNR.L", "SEQI.L", "SRP.L",
    "SHC.L", "SHAW.L", "SRE.L", "SCT.L", "SPI.L", "SSPG.L", "SUPR.L", "SYNC.L",
    "THRL.L", "TATE.L", "TW.L", "TBCG.L", "TEP.L", "TMPL.L", "TEM.L", "TRIG.L",
    "THG.L", "TCAP.L", "TRN.L", "TPK.L", "TRY.L", "TRST.L", "TFIF.L", "UTG.L",
    "UEM.L", "VSVS.L", "VCT.L", "VEIL.L", "VOF.L", "VTY.L", "FAN.L", "WOSG.L",
    "JDW.L", "SMWH.L", "WIX.L", "WIZZ.L", "WKP.L", "WWH.L", "WPP.L", "XPP.L",
    "XPS.L", "ZIG.L",
]

SP500 = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "BLK", "AXP",
    # Healthcare
    "LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV", "TMO",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX",
    # Energy
    "XOM", "CVX", "COP",
    # Industrials
    "CAT", "DE", "HON", "RTX", "LMT", "GE",
    # Tech (broader)
    "AMD", "INTC", "ORCL", "CRM", "ADBE", "NOW", "PLTR", "CRWD",
    # Communication
    "NFLX", "DIS", "T", "VZ",
    # Materials / Utilities
    "LIN", "APD", "NEE", "SO",
]

UNIVERSE = {
    "FTSE 100":  FTSE100,
    "FTSE 250":  FTSE250,
    "S&P 500":   SP500,
    # DAX suspended 2026-06-05 — re-add when reinstated
}


# ----------------------------------------------------------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------------------------------------------------------

def scan_universe() -> dict:
    """
    Scan all instruments in all indices.
    Returns dict keyed by index name, each value a list of HVF result dicts.
    """
    from price_action import get_hvf_signal_mtf, get_trend_structure

    results = {}
    total   = sum(len(v) for v in UNIVERSE.values())
    done    = 0

    for index_name, tickers in UNIVERSE.items():
        index_results = []
        log.info(f"Scanning {index_name} ({len(tickers)} tickers)...")
        for ticker in tickers:
            try:
                trend = get_trend_structure(ticker)
                hvf   = get_hvf_signal_mtf(ticker, trend_hint=trend)
                if hvf.get("hvf_type"):
                    hvf["ticker"]     = ticker
                    hvf["index"]      = index_name
                    hvf["long_trend"] = trend.get("signal", "")
                    index_results.append(hvf)
                time.sleep(0.3)   # polite to Yahoo Finance
            except Exception as e:
                log.warning(f"  {ticker} failed: {e}")
            done += 1
            if done % 20 == 0:
                log.info(f"  {done}/{total} done")

        results[index_name] = index_results
        log.info(f"  {index_name}: {len(index_results)} patterns found")

    return results


# ----------------------------------------------------------------------------------------------------------------------
# Categorise results
# ----------------------------------------------------------------------------------------------------------------------

def categorise(all_results: dict) -> tuple:
    """
    Split into three report sections:
      tradeable  — READY or TRIGGERED with R:R >= 2.5
      developing — DEVELOPING (valid pattern, R:R < 2.5, watch list)
    Returns (tradeable, developing) — each a flat list sorted by quality.
    """
    tradeable  = []
    developing = []

    for index_name, results in all_results.items():
        for r in results:
            sig = r.get("hvf_signal", "")
            rr  = r.get("risk_reward") or 0
            if sig in ("READY", "TRIGGERED") and rr >= HVF_MIN_RR:
                tradeable.append(r)
            elif sig == "DEVELOPING":
                developing.append(r)

    from price_action import hvf_weight
    tradeable.sort(key=lambda r: hvf_weight(
        r.get("hvf_signal"), r.get("pattern_quality"), r.get("risk_reward")))

    # ── IG validation for UK tradeable setups (user 2026-06-12: IG data is the
    # arbiter). Yahoo's LSE feed contains phantom prints, so every .L setup is
    # corroborated pivot-by-pivot against IG broker candles BEFORE posting:
    # pass → entry/stop/target recomputed from IG levels; fail → demoted to
    # DEVELOPING. Weight-ordered first so the best setups get the allowance;
    # capped per run to protect the 10,000/week budget. US feeds are clean —
    # no allowance spent there.
    from price_action import validate_hvf_with_ig
    IG_VALIDATE_MAX = 15
    validated = 0
    still_tradeable = []
    for r in tradeable:
        if r.get("ticker", "").endswith(".L") and validated < IG_VALIDATE_MAX:
            r = validate_hvf_with_ig(r["ticker"], r)
            validated += 1
        if r.get("hvf_signal") in ("READY", "TRIGGERED"):
            still_tradeable.append(r)
        else:
            developing.append(r)   # demoted by IG mismatch
    tradeable = still_tradeable

    developing.sort(key=lambda r: r.get("risk_reward") or 0, reverse=True)
    return tradeable, developing


# ----------------------------------------------------------------------------------------------------------------------
# Slack report
# ----------------------------------------------------------------------------------------------------------------------

def _fmt_price(p, suffix=""):
    """Format price with appropriate decimal places."""
    if p is None:
        return "-"
    return f"{p:,.1f}{suffix}"


def _rr(r):
    rr = r.get("risk_reward")
    return f"{rr:.1f}:1" if rr else "-"


def _label(ticker: str) -> str:
    """
    'TICKER (Full Name)' for display (memory/feedback_instrument_names — never a
    bare ticker). notify.fmt() only knows the ~76 names in epic_lookup, so most
    scanned constituents (VOD.L, NXT.L, …) fell back to the bare ticker. Resolve
    those via the yfinance-backed intraday_signals._resolve_name (epic_lookup →
    yfinance, cached per process). Falls back to the ticker if unresolved.
    """
    try:
        from intraday_signals import _resolve_name
        name = _resolve_name(ticker)
    except Exception:
        name = ticker
    return f"{ticker} ({name})" if name and name != ticker else ticker


def _dir_emoji(hvf_type):
    return "🟢" if hvf_type == "BULLISH" else "🔴"


def _signal_emoji(sig):
    return {"TRIGGERED": "⚡", "READY": "✅", "DEVELOPING": "👀"}.get(sig, "")


def _tf_short(label):
    """daily-90 → d90, weekly → weekly. Compact timeframe tag for the report."""
    return (label or "").replace("daily-", "d")


def _other_timeframes(r):
    """The timeframes — other than the primary/best one — the same instrument's
    funnel also appears on, for the 'report instrument once, list all timeframes'
    rule (feedback_hvf_timeframe_grouping). Already weight-ordered (TRIGGERED >
    READY > DEVELOPING, then quality) by get_hvf_signal_mtf.mtf_timeframes."""
    primary = r.get("hvf_timeframe")
    return [c for c in (r.get("mtf_timeframes") or [])
            if c.get("hvf_timeframe") and c.get("hvf_timeframe") != primary]


def _index_short(index_name: str) -> str:
    """Short market tag for a report line. Delegates to price_action.market_short so the
    daily report, X drafts and quality reports label markets identically (SoT)."""
    from price_action import market_short
    return market_short(index_name)


def _chunk_lines(lines, limit=2900):
    """Pack rendered lines into <=limit-char chunks (Slack caps a section at 3000)."""
    out, cur = [], ""
    for ln in lines:
        if cur and len(cur) + 1 + len(ln) > limit:
            out.append(cur); cur = ln
        else:
            cur = (cur + "\n" + ln) if cur else ln
    if cur:
        out.append(cur)
    return out


def _tradeable_line(r) -> str:
    """One rendered TRADEABLE report line (extracted 2026-06-16 so the per-market grouping
    loop stays readable; logic unchanged)."""
    d  = _dir_emoji(r.get("hvf_type"))
    s  = _signal_emoji(r.get("hvf_signal"))
    t  = r.get("ticker", "")
    rr = _rr(r)
    tf = _tf_short(r.get("hvf_timeframe", ""))
    idx = _index_short(r.get("index", ""))
    entry  = _fmt_price(r.get("h3_level"))
    stop   = _fmt_price(r.get("stop_level"))
    target = _fmt_price(r.get("target"))
    q      = r.get("pattern_quality", 0)
    line = (f"{d}{s} *{_label(t)}*  R:R {rr}  Q={q}  [{tf}] · {idx}\n"
            f"    Entry {entry}  Stop {stop}  Target {target}")
    # Tight-stop label (backlog #9b): a funnel whose stop is < TIGHT_STOP_MIN_PCT of price
    # is structurally untradeable at IG intraday (spread + tick noise), so we DON'T trade it
    # — but it stays in the report, plainly labelled, because the pattern itself is valid
    # (user 2026-06-15, option b). The inflated R:R is the tell of the tiny stop.
    if r.get("tight_stop_intraday"):
        sp = r.get("stop_pct")
        line += (f"\n    ⚠️ Stop only {sp}% of price — too tight for IG intraday "
                 f"(spread + tick noise); not auto-traded.")
    # One instrument, all timeframes (feedback_hvf_timeframe_grouping): list the other
    # timeframes the same funnel appears on — FULL figures per date range (user 2026-06-15).
    others = _other_timeframes(r)
    if others:
        line += "\n    Also on:"
        for c in others:
            c_rr  = c.get("risk_reward")
            c_rrs = f"{c_rr:.1f}:1" if isinstance(c_rr, (int, float)) and c_rr else "—"
            line += (f"\n      {_signal_emoji(c.get('hvf_signal'))} {_tf_short(c.get('hvf_timeframe'))}  "
                     f"Entry {_fmt_price(c.get('h3_level'))}  Stop {_fmt_price(c.get('stop_level'))}  "
                     f"Target {_fmt_price(c.get('target'))}  R:R {c_rrs}  Q={c.get('pattern_quality', '—')}")
    # Supplementary technical read (user 2026-06-15) — compact one-liner; context only.
    try:
        from technical_summary import get_technical_summary, summary_line
        _ta = summary_line(get_technical_summary(t))
        if _ta:
            line += f"\n    {_ta}"
    except Exception:
        pass
    return line


def _developing_line(r) -> str:
    """One rendered DEVELOPING report line (extracted 2026-06-16; logic unchanged)."""
    d  = _dir_emoji(r.get("hvf_type"))
    t  = r.get("ticker", "")
    rr = _rr(r)
    tf = _tf_short(r.get("hvf_timeframe", ""))
    idx = _index_short(r.get("index", ""))
    entry  = _fmt_price(r.get("h3_level"))
    stop   = _fmt_price(r.get("stop_level"))
    target = _fmt_price(r.get("target"))
    others = _other_timeframes(r)
    also = ("  · also " + ", ".join(_tf_short(c.get("hvf_timeframe")) for c in others)) if others else ""
    return (f"{d}👀 *{_label(t)}*  R:R {rr}  [{tf}] · {idx}  "
            f"Entry {entry}  Stop {stop}  Target {target}{also}")


def build_slack_blocks(tradeable, developing, scan_time: str) -> list:
    """Build Slack Block Kit message for the daily HVF report."""
    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text",
                 "text": f"HVF Daily Report — {scan_time}"}
    })
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn",
                 "text": (f"*{len(tradeable)} tradeable* (READY/TRIGGERED ≥{HVF_MIN_RR}:1 R:R)  |  "
                          f"*{len(developing)} developing* (valid pattern, R:R < {HVF_MIN_RR}:1)\n"
                          f"Indices: FTSE 100 · FTSE 250 · S&P 500")}
    })
    blocks.append({"type": "divider"})

    # ── TRADEABLE ─────────────────────────────────────────────────────────────────────────────────────────────────────
    if tradeable:
        # Grouped into per-market sections, top PER_MARKET_TOP_N each (user 2026-06-16:
        # "top 10 by market"). categorise() already sorted tradeable by hvf_weight
        # (TRIGGERED first, then quality), so each market keeps canonical weight order.
        from collections import Counter
        from price_action import group_by_market
        totals = Counter(r.get("index") for r in tradeable)
        groups = group_by_market(tradeable, n=PER_MARKET_TOP_N, market_order=MARKET_ORDER)
        shown  = sum(len(rs) for _, rs in groups)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*⚡ TRADEABLE — top {PER_MARKET_TOP_N}/market · {shown} of {len(tradeable)}*"}
        })
        for market, rows in groups:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*{_index_short(market)}* — top {len(rows)} of {totals.get(market, len(rows))} candidates"}
            })
            # However #2 (user 2026-06-19): number each list from 1, restarting per market.
            _numbered = [f"{i}. {_tradeable_line(r)}" for i, r in enumerate(rows, 1)]
            for blk in _chunk_lines(_numbered):
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": blk}})
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_No tradeable HVF setups found today._"}
        })

    blocks.append({"type": "divider"})

    # ── DEVELOPING ────────────────────────────────────────────────────────────────────────────────────────────────────
    if developing:
        # Per-market sections too, top PER_MARKET_TOP_N each (user 2026-06-16).
        # categorise() ordered developing by R:R desc, preserved within each market.
        from collections import Counter
        from price_action import group_by_market
        totals = Counter(r.get("index") for r in developing)
        groups = group_by_market(developing, n=PER_MARKET_TOP_N, market_order=MARKET_ORDER)
        shown  = sum(len(rs) for _, rs in groups)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*👀 DEVELOPING — top {PER_MARKET_TOP_N}/market · {shown} of {len(developing)} on watch*"}
        })
        for market, rows in groups:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*{_index_short(market)}* — top {len(rows)} of {totals.get(market, len(rows))} candidates"}
            })
            # However #2 (user 2026-06-19): number each list from 1, restarting per market.
            _numbered = [f"{i}. {_developing_line(r)}" for i, r in enumerate(rows, 1)]
            for blk in _chunk_lines(_numbered):
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": blk}})
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_No developing patterns today._"}
        })

    # Footer
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                      "text": (f"HVF scanner: daily-30 · daily-60 · daily-90 · "
                               f"daily-180 · daily-220 · weekly | "
                               f"Min {HVF_MIN_RR}:1 R:R to trade | "
                               f"\"Also on\" = other timeframes the same funnel appears on "
                               f"(⚡ triggered · ✅ ready · 👀 developing), each with its OWN raw "
                               f"entry/stop/target/R:R; the headline figures are for the primary "
                               f"timeframe in [brackets] (the only one exhaustion-anchored + IG-validated) | "
                               f"Generated {scan_time} UTC")}]
    })

    return blocks


def post_to_slack(blocks: list):
    import requests
    url = os.environ.get("SLACK_SIGNALS", "")
    if not url:
        log.warning("SLACK_SIGNALS not set — skipping Slack post")
        return
    resp = requests.post(url, json={"blocks": blocks}, timeout=15)
    if resp.status_code != 200:
        log.error(f"Slack post failed: {resp.status_code} {resp.text[:200]}")
    else:
        log.info("HVF report posted to #claude-trading-signals")


# ----------------------------------------------------------------------------------------------------------------------
# DB logging
# ----------------------------------------------------------------------------------------------------------------------

def log_to_db(tradeable: list, developing: list, scan_time: str):
    """
    Write a summary row to hvf_scan_log table (created by run_schema.py).
    Gracefully skips if table doesn't exist yet.
    """
    try:
        import pg8000.native
        conn = _pool_get_db()
        for r in tradeable + developing:
            conn.run(
                """insert into hvf_scan_log
                   (scan_time, ticker, index_name, hvf_type, hvf_signal,
                    hvf_timeframe, pattern_quality, risk_reward,
                    entry_level, stop_level, target)
                   values (:ts, :tk, :ix, :ht, :hs, :tf, :q, :rr, :el, :sl, :tg)
                   on conflict do nothing""",
                ts=scan_time, tk=r.get("ticker"), ix=r.get("index"),
                ht=r.get("hvf_type"), hs=r.get("hvf_signal"),
                tf=r.get("hvf_timeframe"),
                q=r.get("pattern_quality"), rr=r.get("risk_reward"),
                el=r.get("h3_level"), sl=r.get("stop_level"), tg=r.get("target")
            )
        conn.close()
        log.info(f"Logged {len(tradeable)+len(developing)} patterns to hvf_scan_log")
    except Exception as e:
        log.warning(f"DB log failed (non-critical): {e}")


# ----------------------------------------------------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------------------------------------------------

def _publish_top_per_market_to_x(posted: list):
    """Publish the top X_PUBLISH_TOP_N per market (of the changed draft set) LIVE to X
    (user 2026-06-17). `posted` are the instruments _generate_x_drafts actually drafted, already
    in market-grouped weight order; threads are spaced so they don't overlap (publish_tickers_to_x)."""
    if not posted:
        log.info("Live X: no changed drafts to publish today.")
        return
    from price_action import group_by_market
    groups  = group_by_market(posted, n=X_PUBLISH_TOP_N, market_order=MARKET_ORDER)
    tickers = [r.get("ticker") for _, rows in groups for r in rows if r.get("ticker")]
    if not tickers:
        return
    log.info(f"Live X: publishing top {X_PUBLISH_TOP_N}/market = {len(tickers)} instrument(s): {tickers}")
    from publish_one_to_x import publish_tickers_to_x
    publish_tickers_to_x(tickers)


def main():
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log.info(f"HVF Daily Report starting — {scan_time} UTC")

    all_results = scan_universe()
    tradeable, developing = categorise(all_results)

    # Summary to stdout
    total_patterns = sum(len(v) for v in all_results.values())
    log.info(f"Scan complete: {total_patterns} patterns found, "
             f"{len(tradeable)} tradeable, {len(developing)} developing")

    for r in tradeable:
        log.info(f"  TRADEABLE: {r['ticker']} {r['hvf_type']} {r['hvf_signal']} "
                 f"Q={r.get('pattern_quality')} R:R={r.get('risk_reward')} [{r.get('hvf_timeframe')}]")
    for r in developing:
        log.info(f"  DEVELOPING: {r['ticker']} {r['hvf_type']} "
                 f"R:R={r.get('risk_reward')} [{r.get('hvf_timeframe')}]")

    # Post to Slack
    blocks = build_slack_blocks(tradeable, developing, scan_time)
    post_to_slack(blocks)

    # X draft reports — one tweet-ready Slack post per tradeable instrument, top X_DRAFT_PER_MARKET
    # per market, ONLY re-shown when confirmations changed (user 2026-06-17). Then the top
    # X_PUBLISH_TOP_N per market OF THAT CHANGED SET are auto-published LIVE to X, spaced so the
    # threads don't overlap on the timeline.
    if tradeable:
        posted = []
        try:
            from intraday_signals import _generate_x_drafts
            posted = _generate_x_drafts(tradeable, changed_only=True) or []
        except Exception as e:
            log.warning(f"X draft generation failed (non-critical): {e}")
        try:
            _publish_top_per_market_to_x(posted)
        except Exception as e:
            log.warning(f"live X publish failed (non-critical): {e}")

    # Log to DB
    log_to_db(tradeable, developing, scan_time)

    log.info("HVF Daily Report complete.")


if __name__ == "__main__":
    main()
