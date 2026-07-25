# ======================================================================================================================
# File:         run_hvf_report.py
# Author:       Alex Hind
# Created:      2026-06-05
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily HVF Report — scans FTSE 100, FTSE 250, S&P 500 constituents and a
# Commodities basket (metals + energy) for Hunt Volatility Funnel patterns
# and posts a structured report to Slack.
#
# Three sections:
#   READY/TRIGGERED  — patterns meeting ≥2.5:1 R:R, tradeable now
#   DEVELOPING       — valid pattern structure, R:R < 2.5:1, watch list
#   IN PLAY          — patterns that have TRIGGERED and are open/active
#
# Multi-timeframe scanner (daily-240, daily-180, daily-90, daily-60, daily-30, weekly):
#   Shorter timeframes catch post-peak reversals and tight recent funnels
#   that the standard 240-day lookback misses.
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
# 1.30.0  2026-07-06  Alex Hind   (user 2026-07-06) UNIVERSE market renames: Germany->DAX, Shanghai->SSE (Shanghai),
#                                 Japan->Nikkei 225, Australia->ASX, India->NSE (India). Locations unchanged (country).
# 1.29.0  2026-06-30  Alex Hind   (user 2026-06-30) UNIVERSE: 'Indices & FX' split into separate 'Indices' (24) and
#                                 'FX' (32) markets; INDICES_FX kept as a legacy alias. Report header updated.
# 1.28.0  2026-06-30  Alex Hind   (user 2026-06-29) HVF Slack report horizon cap: setups whose expected time-to-target
#                                 (target_horizon_days, the H1->H3 span) exceeds 9 months are dropped from the Slack
#                                 blocks (both tradeable + developing) — e.g. MDLZ "~19 months to target" no longer shows.
# 1.27.0  2026-06-26  Alex Hind   (user 2026-06-26, C) post_to_slack now also posts the report to the SLACK_RW_HVF webhook
#                                 (extra HVF-report channel) alongside SLACK_SIGNALS; each webhook is independent (a missing
#                                 or failing one doesn't stop the others). Secret added to trading-hvf-report.yml.
# 1.26.0  2026-06-26  Alex Hind   (user 2026-06-26) Daily report hides a market's DEVELOPING watch list once that market
#                                 already has > DEVELOPING_HIDE_IF_TRADEABLE_OVER (10) TRADEABLE setups — plenty to act on,
#                                 so the watch list is noise. The DEVELOPING header notes which markets were hidden.
# 1.25.0  2026-06-22  Alex Hind   (user 2026-06-22) HVF_REPORT_MODE: normal | slack_only | slack_png. slack_png posts the
#                                 analytical report + Slack drafts WITH the card + 3yr PNGs for ALL tradeable, NO live X — for
#                                 reviewing the full output (visuals included) without touching X.
# 1.24.0  2026-06-22  Alex Hind   (user 2026-06-22) Slack-only mode: HVF_REPORT_SKIP_X / --no-x posts the analytical #signals
#                                 report WITHOUT any X drafts or live-X publish — for reviewing the order without touching X.
# 1.23.0  2026-06-22  Alex Hind   (user 2026-06-22) ALL markets: added Crypto basket + the rest of the major FX; the report
#                                 now spans UK+US equities, commodities, indices, FX, crypto (FTSE/S&P were only examples).
#                                 Each market ordered by action_score (R:R ÷ distance-to-entry, DESC). _fmt_price now scales
#                                 decimals to magnitude so FX/small crypto don't render as "1.3".
# 1.22.0  2026-06-22  Alex Hind   (user 2026-06-22) Add "Indices & FX" basket to UNIVERSE (SPX500/NASDAQ/UK100/JPN225/HK50 +
#                                 USDJPY) — "where are JPN225 and USDJPY?". Sub-£1 FX (GBPUSD/EURUSD/AUDUSD) deferred until the
#                                 report price format carries more decimals. MARKET_ORDER + "Scanned:" line updated.
# 1.21.0  2026-06-22  Alex Hind   (user 2026-06-22) Report lines tag a PROLONGED consolidation ("coiling ~Nwk") when the
#                                 funnel has been forming >=PROLONGED_FUNNEL_WEEKS (8wk, H1->H3) — recognise the long period.
# 1.20.0  2026-06-22  Alex Hind   (user 2026-06-22) Entry-distance rule: a would-be TRADEABLE setup whose ENTRY is >10%
#                                 (MAX_DEVELOPING_DISTANCE_PCT) from the live price is MOVED to developing (not published) —
#                                 not actionable now. Combined with the existing developing display filter, a >10% setup is
#                                 thus neither published nor shown. now-vs-entry is the relevance metric (entry→target unfiltered).
# 1.19.0  2026-06-22  Alex Hind   (user 2026-06-22) Commodities basket added to UNIVERSE (metals + energy via YAHOO_MAP);
#                                 DEVELOPING section now hides setups whose ENTRY is >MAX_DEVELOPING_DISTANCE_PCT (10%) from
#                                 the live price (now-vs-entry relevance filter); clearer section headers ("showing the top
#                                 N per market (X shown of Y that qualify)" instead of "top 10/market · 28 of 49").
# 1.18.0  2026-06-20  Alex Hind   (user 2026-06-20) categorise() drops missed-entry TRIGGERED setups (entry_chase_pct >
#                                 MAX_ENTRY_CHASE_PCT); "Q=" relabelled "Quality N/100"; blank line between instruments.
# 1.17.0  2026-06-19  Alex Hind   Expected time-to-target next to R:R on the TRADEABLE line (user 2026-06-19) via
#                                 price_action.target_horizon — Slack only (#signals), never on the X card/tweet.
# 1.16.0  2026-06-19  Alex Hind   Current price + % distance (user 2026-06-19): every TRADEABLE/DEVELOPING line now shows
#                                 "Now <price>" and each level's % from the live price, via price_action.pct_from_current.
# 1.15.0  2026-06-19  Alex Hind   D220 -> D240 (user 2026-06-19): scanner header + footer now read daily-240 (the
#                                 long-term daily scan window changed in price_action 1.19.0).
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
import sys
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
import logging
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(override=True)

from config import (HVF_MIN_RR, PER_MARKET_TOP_N, MARKET_ORDER, X_PUBLISH_TOP_N,
                    DEVELOPING_HIDE_IF_TRADEABLE_OVER)   # single source of truth for thresholds/limits
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

# Top ~100 S&P 500 by market cap (user 2026-06-29). Yahoo symbols (BRK-B not BRK.B). Overlaps with the
# NASDAQ 100 list below are de-duplicated at scan time, so a name is only scanned/recorded once.
SP500 = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "GOOG", "META", "AVGO", "BRK-B", "TSLA",
    "LLY", "JPM", "WMT", "V", "ORCL", "MA", "UNH", "XOM", "COST", "NFLX",
    "JNJ", "HD", "PG", "ABBV", "BAC", "PLTR", "KO", "CVX", "TMUS", "CRM",
    "WFC", "CSCO", "PM", "IBM", "ABT", "MCD", "GE", "LIN", "MRK", "NOW",
    "ACN", "PEP", "AXP", "ISRG", "T", "DIS", "INTU", "GS", "RTX", "VZ",
    "AMD", "TXN", "MS", "CAT", "BKNG", "QCOM", "ADBE", "BSX", "SPGI", "BLK",
    "NEE", "PGR", "HON", "AMGN", "TJX", "SYK", "UNP", "LOW", "GILD", "ETN",
    "C", "ADP", "DE", "BX", "COP", "VRTX", "MMC", "CB", "FI", "MDT",
    "ADI", "LMT", "BMY", "PANW", "MU", "AMAT", "PLD", "SBUX", "KKR", "ANET",
    "MO", "SO", "INTC", "NKE", "ICE", "GEV", "CME", "DUK", "SHW", "WM",
    "TMO", "PFE", "DHR",
]

# NASDAQ 100 constituents (user 2026-06-29). Yahoo symbols; overlaps with the S&P 500 list above are
# de-duplicated at scan time. Index membership drifts over time — invalid/delisted names are skipped
# gracefully by the engine and flagged by the price audit.
NASDAQ100 = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "AVGO", "TSLA", "NFLX",
    "COST", "TMUS", "CSCO", "PEP", "AMD", "ADBE", "TXN", "QCOM", "INTU", "AMAT",
    "AMGN", "ISRG", "BKNG", "HON", "CMCSA", "ADP", "VRTX", "GILD", "MU", "ADI",
    "LRCX", "PANW", "REGN", "MELI", "KLAC", "SBUX", "SNPS", "CDNS", "CRWD", "MDLZ",
    "CTAS", "MAR", "ORLY", "ABNB", "CSX", "MRVL", "NXPI", "FTNT", "DASH", "ADSK",
    "ROP", "WDAY", "PCAR", "TTD", "MNST", "AEP", "PAYX", "KDP", "ODFL", "FAST",
    "ROST", "CPRT", "EA", "BKR", "VRSK", "EXC", "XEL", "CTSH", "GEHC", "KHC",
    "DDOG", "IDXX", "DXCM", "TTWO", "CCEP", "ANSS", "ON", "CDW", "BIIB", "ZS",
    "GFS", "MDB", "ARM", "WBD", "APP", "MSTR", "PLTR", "LIN", "PYPL", "LULU",
    "FANG", "AXON", "PDD", "TEAM",
]

COMMODITIES = [
    # Internal names resolved to Yahoo futures via config.YAHOO_MAP (user 2026-06-22:
    # "what about commodities?"). Metals + energy that the HVF detector can fetch daily.
    "XAUUSD",    # Gold       (GC=F)
    "XAGUSD",    # Silver     (SI=F)
    "OIL",       # WTI Crude  (CL=F)
    "COPPER",    # Copper     (HG=F)
    "NATGAS",    # Nat Gas    (NG=F)
    "PLATINUM",  # Platinum   (PL=F)
    "PALLADIUM", # Palladium  (PA=F)
    # Broader commodity coverage (user 2026-06-29: "add all commodities") as raw Yahoo futures symbols.
    "BZ=F",   # Brent Crude
    "RB=F",   # RBOB Gasoline
    "HO=F",   # Heating Oil
    "ZC=F",   # Corn
    "ZW=F",   # Wheat
    "ZS=F",   # Soybeans
    "ZM=F",   # Soybean Meal
    "ZL=F",   # Soybean Oil
    "KC=F",   # Coffee
    "SB=F",   # Sugar
    "CT=F",   # Cotton
    "CC=F",   # Cocoa
    "OJ=F",   # Orange Juice
    "LE=F",   # Live Cattle
    "HE=F",   # Lean Hogs
    "GF=F",   # Feeder Cattle
    "ZO=F",   # Oats
    "ZR=F",   # Rough Rice
]

# Split into separate markets (user 2026-06-30) — INDICES and FX were one "Indices & FX" basket.
INDICES = [
    # Major market indices. Internal names resolved via config.YAHOO_MAP; raw Yahoo symbols pass through.
    "SPX500",   # S&P 500 index (^GSPC)
    "NASDAQ",   # Nasdaq 100   (^IXIC)
    "UK100",    # FTSE 100     (^FTSE)
    "JPN225",   # Nikkei 225   (^N225)
    "HK50",     # Hang Seng    (^HSI)
    "^DJI",       # Dow Jones Industrial Average
    "^RUT",       # Russell 2000
    "^GDAXI",     # DAX (Germany)
    "^FCHI",      # CAC 40 (France)
    "^STOXX50E",  # Euro Stoxx 50
    "^FTMC",      # FTSE 250
    "^AEX",       # AEX (Netherlands)
    "^IBEX",      # IBEX 35 (Spain)
    "^SSMI",      # SMI (Switzerland)
    "^AXJO",      # ASX 200 (Australia)
    "^GSPTSE",    # S&P/TSX (Canada)
    "^BSESN",     # BSE Sensex (India)
    "^NSEI",      # Nifty 50 (India)
    "^KS11",      # KOSPI (S. Korea)
    "^TWII",      # Taiwan Weighted
    "^STI",       # Straits Times (Singapore)
    "^BVSP",      # Bovespa (Brazil)
    "^MXX",       # IPC (Mexico)
    "000001.SS",  # Shanghai Composite
]

FX = [
    # Majors (internal names via YAHOO_MAP) + crosses (raw Yahoo =X symbols). Sub-£1 FX display
    # correctly via the adaptive _fmt_price (more decimals on small prices).
    "USDJPY",   # USD/JPY      (USDJPY=X)
    "GBPUSD",   # GBP/USD      (GBPUSD=X)
    "EURUSD",   # EUR/USD      (EURUSD=X)
    "AUDUSD",   # AUD/USD      (AUDUSD=X)
    "USDCAD=X", "USDCHF=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "GBPJPY=X",
    "EURCHF=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X", "NZDJPY=X", "EURAUD=X",
    "GBPAUD=X", "EURCAD=X", "GBPCAD=X", "AUDNZD=X", "AUDCAD=X", "EURNZD=X",
    "USDSGD=X", "USDHKD=X", "USDNOK=X", "USDSEK=X", "USDMXN=X", "USDZAR=X",
    "USDCNY=X", "USDINR=X", "USDTRY=X", "USDPLN=X",
]

INDICES_FX = INDICES + FX   # legacy alias — anything still importing the combined list keeps working

CRYPTO = [
    # 24/7 crypto (user 2026-06-22: ALL markets). Resolved to Yahoo via config.YAHOO_MAP.
    "BTCUSD",   # Bitcoin   (BTC-USD)
    "ETHUSD",   # Ethereum  (ETH-USD)
    "XRPUSD",   # XRP       (XRP-USD)
    "SOLUSD",   # Solana    (SOL-USD)
    "BNBUSD",   # BNB       (BNB-USD)
]


GERMANY = [
    # Top German equities (DAX 40 + MDAX, .DE) by market cap — Yahoo-verified (user 2026-07-03).
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MBG.DE", "BMW.DE", "VOW3.DE",
    "BAS.DE", "BAYN.DE", "ADS.DE", "MUV2.DE", "DHL.DE", "IFX.DE", "DB1.DE", "EOAN.DE",
    "RWE.DE", "HEN3.DE", "MRK.DE", "DBK.DE", "VNA.DE", "BEI.DE", "HEI.DE", "FRE.DE",
    "CON.DE", "PAH3.DE", "PUM.DE", "QIA.DE", "RHM.DE", "SRT3.DE", "SHL.DE", "SY1.DE",
    "ZAL.DE", "ENR.DE", "MTX.DE", "BNR.DE", "CBK.DE", "HNR1.DE", "DTG.DE", "P911.DE",
    "AFX.DE", "BC8.DE", "BOSS.DE", "EVK.DE", "EVT.DE", "FRA.DE", "HFG.DE", "JUN3.DE",
    "KGX.DE", "KRN.DE", "LEG.DE", "LHA.DE", "LXS.DE", "NDA.DE", "NEM.DE", "PSM.DE",
    "RAA.DE", "SDF.DE", "TEG.DE", "TKA.DE", "UTDI.DE", "WCH.DE", "G1A.DE", "AIXA.DE",
    "DUE.DE", "FPE3.DE", "FIE.DE", "GXI.DE", "HLE.DE", "SAX.DE", "SZG.DE", "COK.DE",
    "TLX.DE", "G24.DE", "NDX1.DE", "PBB.DE", "S92.DE", "BFSA.DE", "ADN1.DE", "DBAN.DE",
    "DEQ.DE", "DWNI.DE", "ELG.DE", "GBF.DE", "HAB.DE", "INH.DE", "KWS.DE", "NOEJ.DE",
    "PFV.DE", "SFQ.DE", "STO3.DE", "TTK.DE", "WAF.DE", "WSU.DE",
]

SHANGHAI = [
    # Top Shanghai-listed equities (.SS: 600/601/603/605/688 boards) by market cap — Yahoo-verified.
    "600519.SS", "601398.SS", "601288.SS", "601857.SS", "600036.SS", "601988.SS", "600028.SS", "601318.SS",
    "600030.SS", "600276.SS", "600887.SS", "601668.SS", "600900.SS", "601628.SS", "600016.SS", "601166.SS",
    "600000.SS", "601088.SS", "600809.SS", "603288.SS", "688981.SS", "601012.SS", "600585.SS", "601390.SS",
    "601601.SS", "600309.SS", "603259.SS", "600438.SS", "601336.SS", "601899.SS", "601138.SS", "600690.SS",
    "600104.SS", "601111.SS", "600050.SS", "601818.SS", "601066.SS", "601211.SS", "601688.SS", "600745.SS",
    "603501.SS", "688111.SS", "688012.SS", "600031.SS", "600406.SS", "601225.SS", "601985.SS", "601998.SS",
    "600048.SS", "601009.SS", "601377.SS", "600588.SS", "600570.SS", "600346.SS", "603986.SS", "600011.SS",
    "600362.SS", "600018.SS", "601186.SS", "601800.SS", "601328.SS", "601169.SS", "600919.SS", "603260.SS",
    "688036.SS", "688169.SS", "600893.SS", "600703.SS", "600760.SS", "601633.SS", "600886.SS", "601238.SS",
    "601607.SS", "603799.SS", "600460.SS", "688396.SS", "600426.SS", "603195.SS", "605499.SS", "600089.SS",
    "600009.SS", "601319.SS", "601360.SS", "601995.SS", "688008.SS", "600732.SS", "600233.SS", "600183.SS",
    "600522.SS", "600845.SS", "600741.SS", "601618.SS", "601728.SS", "600583.SS", "601788.SS", "600157.SS",
]

HONGKONG = [
    # Top Hong Kong equities (.HK) by market cap — Yahoo-verified (user 2026-07-03).
    "0700.HK", "0941.HK", "1299.HK", "0005.HK", "9988.HK", "3690.HK", "0939.HK", "1398.HK",
    "0883.HK", "2318.HK", "0388.HK", "1810.HK", "0016.HK", "0001.HK", "2628.HK", "0027.HK",
    "0002.HK", "0003.HK", "0006.HK", "0012.HK", "0066.HK", "0175.HK", "1211.HK", "2020.HK",
    "2269.HK", "0288.HK", "0669.HK", "1024.HK", "9618.HK", "9999.HK", "3988.HK", "0762.HK",
    "0857.HK", "0386.HK", "0688.HK", "0960.HK", "1109.HK", "0968.HK", "2382.HK", "0981.HK",
    "1177.HK", "2331.HK", "2015.HK", "9868.HK", "1088.HK", "0836.HK", "0267.HK", "0322.HK",
    "0291.HK", "0151.HK", "1928.HK", "0019.HK", "0017.HK", "0101.HK", "0083.HK", "1113.HK",
    "0004.HK", "0345.HK", "6862.HK", "9633.HK", "2313.HK", "1044.HK", "1876.HK", "2319.HK",
    "0868.HK", "6098.HK", "1997.HK", "0777.HK", "3968.HK", "1093.HK", "1099.HK", "6618.HK",
    "9961.HK", "2359.HK", "1801.HK", "6160.HK", "2196.HK", "9926.HK", "0241.HK", "3692.HK",
    "9922.HK", "0013.HK", "0992.HK", "0384.HK", "0270.HK", "1038.HK", "6823.HK", "2688.HK",
    "1128.HK", "0880.HK", "2007.HK", "2202.HK", "0817.HK", "1918.HK",
]


JAPAN = [
    # Top Japanese equities (Nikkei constituents, .T) by market cap — Yahoo-verified (user 2026-07-04).
    "7203.T", "6758.T", "9984.T", "6861.T", "8306.T", "9432.T", "9433.T", "4063.T",
    "8035.T", "6098.T", "9983.T", "8058.T", "8001.T", "8031.T", "7974.T", "4519.T",
    "4568.T", "6501.T", "6902.T", "6367.T", "6594.T", "7267.T", "7201.T", "7751.T",
    "6971.T", "6503.T", "8316.T", "8411.T", "8766.T", "8750.T", "2914.T", "4502.T",
    "4503.T", "4523.T", "6273.T", "6301.T", "6326.T", "6857.T", "6954.T", "6981.T",
    "7011.T", "7013.T", "7012.T", "7269.T", "7270.T", "7733.T", "7741.T", "8002.T",
    "8053.T", "8591.T", "8604.T", "8801.T", "8802.T", "9020.T", "9022.T", "9101.T",
    "9104.T", "9107.T", "9201.T", "9202.T", "9501.T", "9735.T", "9766.T", "2502.T",
    "2503.T", "2802.T", "3382.T", "4452.T", "4901.T", "4911.T", "5108.T", "5401.T",
    "5713.T", "6178.T", "6752.T", "6762.T", "6920.T", "6963.T", "6988.T", "7182.T",
    "7309.T", "7532.T", "7832.T", "7936.T", "8015.T", "8113.T", "8267.T", "8630.T",
    "8725.T", "8830.T", "9021.T", "9434.T", "9843.T", "4661.T", "4543.T", "4578.T",
    "6146.T", "6723.T", "3659.T", "2413.T", "4307.T", "4684.T", "6702.T", "6504.T",
    "5802.T", "5019.T", "5020.T", "1605.T", "8697.T", "4188.T", "4005.T", "3407.T",
]

AUSTRALIA = [
    # Top Australian equities (S&P/ASX, .AX) by market cap — Yahoo-verified (user 2026-07-04).
    "BHP.AX", "CBA.AX", "CSL.AX", "NAB.AX", "WBC.AX", "ANZ.AX", "WES.AX", "MQG.AX",
    "GMG.AX", "FMG.AX", "RIO.AX", "TLS.AX", "WOW.AX", "TCL.AX", "ALL.AX", "WDS.AX",
    "REA.AX", "COL.AX", "QBE.AX", "SUN.AX", "XRO.AX", "CPU.AX", "RMD.AX", "STO.AX",
    "ORG.AX", "AMC.AX", "SHL.AX", "JHX.AX", "IAG.AX", "BXB.AX", "S32.AX", "NST.AX",
    "EVN.AX", "MIN.AX", "PLS.AX", "WTC.AX", "CAR.AX", "SEK.AX", "ASX.AX", "APA.AX",
    "MPL.AX", "TWE.AX", "COH.AX", "RHC.AX", "SGP.AX", "MGR.AX", "GPT.AX", "DXS.AX",
    "VCX.AX", "SCG.AX", "LLC.AX", "QAN.AX", "ALD.AX", "AGL.AX", "ORI.AX", "CTD.AX",
    "FLT.AX", "JBH.AX", "HVN.AX", "PMV.AX", "BRG.AX", "DMP.AX", "TAH.AX", "TLC.AX",
    "A2M.AX", "ELD.AX", "GNC.AX", "NUF.AX", "ALQ.AX", "CWY.AX", "BSL.AX", "SGM.AX",
    "ILU.AX", "LYC.AX", "IGO.AX", "LTR.AX", "SFR.AX", "WHC.AX", "NHC.AX", "YAL.AX",
    "PDN.AX", "BOE.AX", "CMM.AX", "RRL.AX", "PRU.AX", "NXT.AX", "MP1.AX", "TNE.AX",
    "PME.AX", "WOR.AX", "SOL.AX", "BEN.AX", "BOQ.AX", "HUB.AX", "NWL.AX", "AMP.AX",
    "CGF.AX", "ASB.AX", "CDA.AX", "QUB.AX", "AZJ.AX", "CHC.AX", "BWP.AX", "EDV.AX",
    "MTS.AX", "BAP.AX", "ARB.AX", "NCK.AX", "LOV.AX",
]

INDIA = [
    # Top Indian equities (NIFTY 100, .NS) by market cap — Yahoo-verified (user 2026-07-04).
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "BHARTIARTL.NS", "SBIN.NS", "LICI.NS",
    "ITC.NS", "HINDUNILVR.NS", "LT.NS", "KOTAKBANK.NS", "AXISBANK.NS", "MARUTI.NS", "SUNPHARMA.NS", "BAJFINANCE.NS",
    "TITAN.NS", "ULTRACEMCO.NS", "ASIANPAINT.NS", "NTPC.NS", "ONGC.NS", "ADANIENT.NS", "ADANIPORTS.NS", "POWERGRID.NS",
    "M&M.NS", "TATASTEEL.NS", "WIPRO.NS", "HCLTECH.NS", "COALINDIA.NS", "JSWSTEEL.NS", "BAJAJFINSV.NS", "NESTLEIND.NS",
    "GRASIM.NS", "HINDALCO.NS", "DRREDDY.NS", "CIPLA.NS", "APOLLOHOSP.NS", "DIVISLAB.NS", "EICHERMOT.NS", "BRITANNIA.NS",
    "TECHM.NS", "INDUSINDBK.NS", "HEROMOTOCO.NS", "BAJAJ-AUTO.NS", "TATACONSUM.NS", "SBILIFE.NS", "HDFCLIFE.NS", "BPCL.NS",
    "IOC.NS", "SHRIRAMFIN.NS", "PIDILITIND.NS", "SIEMENS.NS", "DLF.NS", "VBL.NS", "TRENT.NS", "HAL.NS",
    "BEL.NS", "VEDL.NS", "GAIL.NS", "AMBUJACEM.NS", "DABUR.NS", "GODREJCP.NS", "MARICO.NS", "BERGEPAINT.NS",
    "COLPAL.NS", "MUTHOOTFIN.NS", "BAJAJHLDNG.NS", "ICICIPRULI.NS", "ICICIGI.NS", "SBICARD.NS", "INDIGO.NS", "DMART.NS",
    "PNB.NS", "BANKBARODA.NS", "CANBK.NS", "IRCTC.NS", "IRFC.NS", "PFC.NS", "RECLTD.NS", "NHPC.NS",
    "TATAPOWER.NS", "ADANIGREEN.NS", "ADANIPOWER.NS", "JIOFIN.NS", "NAUKRI.NS", "TVSMOTOR.NS", "MOTHERSON.NS", "BOSCHLTD.NS",
    "ABB.NS", "CGPOWER.NS", "HAVELLS.NS", "TORNTPHARM.NS", "LUPIN.NS", "AUROPHARMA.NS", "ZYDUSLIFE.NS", "SHREECEM.NS",
    "ACC.NS", "JSWENERGY.NS", "INDHOTEL.NS", "GODREJPROP.NS", "CHOLAFIN.NS", "IDFCFIRSTB.NS", "YESBANK.NS", "BANDHANBNK.NS",
]

EURONEXT = [
    # Top 100 Euronext equities by market cap across the pan-European venues (user 2026-07-14):
    # Paris (.PA), Amsterdam (.AS), Milan (.MI), Brussels (.BR), Oslo (.OL), Lisbon (.LS), Dublin (.IR).
    # Paris (Euronext Paris)
    "MC.PA", "OR.PA", "RMS.PA", "CDI.PA", "TTE.PA", "SAN.PA", "AI.PA", "SU.PA",
    "EL.PA", "AIR.PA", "DG.PA", "CS.PA", "BNP.PA", "SAF.PA", "KER.PA", "BN.PA",
    "RI.PA", "DSY.PA", "ORA.PA", "ACA.PA", "GLE.PA", "ENGI.PA", "CAP.PA", "VIE.PA",
    "HO.PA", "ML.PA", "SGO.PA", "PUB.PA", "LR.PA", "EN.PA", "CA.PA", "STLAP.PA",
    "VIV.PA", "WLN.PA", "EDEN.PA", "AMUN.PA", "ERF.PA", "AKE.PA", "BVI.PA", "ALO.PA",
    "RNO.PA", "FR.PA", "TEP.PA", "STMPA.PA",
    # Amsterdam (Euronext Amsterdam)
    "ASML.AS", "PRX.AS", "INGA.AS", "ADYEN.AS", "AD.AS", "PHIA.AS", "WKL.AS", "HEIA.AS",
    "ASM.AS", "DSFIR.AS", "AKZA.AS", "KPN.AS", "NN.AS", "ABN.AS", "RAND.AS", "AGN.AS",
    "BESI.AS", "IMCD.AS", "MT.AS", "UMG.AS",
    # Milan (Euronext Milan / Borsa Italiana)
    "ENEL.MI", "ISP.MI", "UCG.MI", "ENI.MI", "RACE.MI", "G.MI", "MONC.MI", "PST.MI",
    "SRG.MI", "TRN.MI", "PIRC.MI", "LDO.MI", "CPR.MI", "MB.MI", "FBK.MI", "BAMI.MI",
    # Brussels (Euronext Brussels)
    "ABI.BR", "KBC.BR", "UCB.BR", "SOLB.BR", "GBLB.BR", "COLR.BR", "AGS.BR", "UMI.BR",
    "PROX.BR",
    # Oslo (Euronext Oslo Børs)
    "EQNR.OL", "DNB.OL", "TEL.OL", "AKRBP.OL", "MOWI.OL", "NHY.OL",
    # Lisbon (Euronext Lisbon)
    "EDP.LS", "GALP.LS", "JMT.LS",
    # Dublin (Euronext Dublin)
    "RYA.IR", "KRX.IR",
]

# Government / sovereign-bond ETFs (user 2026-07-24, ToDo P-02 L312) — SCAN-ONLY: no IG epics, so they
# are scanned + reported but never traded (like the planned scan-only equities). Ticker == Yahoo symbol
# (YAHOO_MAP identity fallback), so no YAHOO_MAP entries are needed; _location_of routes US-listed -> US
# and .L -> UK. Sovereign only (US Treasuries across the curve, TIPS, international & EM government, UK
# gilts) — deliberately NO broad-aggregate funds (AGG/BND) which mix in corporates. This is the verified
# core; extend toward 50 with euro-area / JGB sovereign ETFs once their exact Yahoo symbols are confirmed
# (wrong symbols 404 on every scan — see the old UK250 fix).
GOVT_BONDS = [
    # US Treasuries by maturity
    "SHV", "BIL", "GBIL", "TBIL", "SHY", "VGSH", "SCHO", "SPTS", "UTWO",
    "IEI", "VGIT", "SCHR", "SPTI", "IEF", "UTEN", "TLH",
    "VGLT", "SPTL", "SCHQ", "TLT", "EDV",
    "USFR", "TFLO", "GOVT",
    # US Treasury inflation-protected (TIPS)
    "TIP", "VTIP", "SCHP", "STIP", "SPIP", "LTPZ",
    # International & emerging-market sovereign (US-listed)
    "BWX", "BWZ", "IGOV", "ISHG", "WIP", "EMB", "VWOB", "EMLC", "PCY", "EBND",
    # UK gilts (LSE)
    "IGLT.L", "VGOV.L", "IGLS.L", "INXG.L",
]

# BSE India (user 2026-07-24 P-02 L319; added 2026-07-25) — SCAN-ONLY, no IG epics (NSE India is already
# live with .NS; this is the Bombay Stock Exchange listing with .BO). Curated verified large-caps
# (Sensex/Nifty names); ticker==Yahoo symbol. Ampersand/hyphen tickers omitted (Yahoo-symbol risk).
# Extend toward top-100 with more verified .BO symbols.
BSE_INDIA = [
    "RELIANCE.BO", "TCS.BO", "HDFCBANK.BO", "INFY.BO", "ICICIBANK.BO", "HINDUNILVR.BO", "SBIN.BO",
    "BHARTIARTL.BO", "ITC.BO", "KOTAKBANK.BO", "LT.BO", "AXISBANK.BO", "BAJFINANCE.BO", "ASIANPAINT.BO",
    "MARUTI.BO", "HCLTECH.BO", "SUNPHARMA.BO", "TITAN.BO", "ULTRACEMCO.BO", "WIPRO.BO", "NESTLEIND.BO",
    "ONGC.BO", "NTPC.BO", "POWERGRID.BO", "TATAMOTORS.BO", "TATASTEEL.BO", "ADANIENT.BO", "ADANIPORTS.BO",
    "JSWSTEEL.BO", "COALINDIA.BO", "GRASIM.BO", "HINDALCO.BO", "DRREDDY.BO", "CIPLA.BO", "BAJAJFINSV.BO",
    "BRITANNIA.BO", "EICHERMOT.BO", "HEROMOTOCO.BO", "TECHM.BO", "INDUSINDBK.BO", "APOLLOHOSP.BO",
    "BPCL.BO", "TATACONSUM.BO", "SBILIFE.BO", "HDFCLIFE.BO", "DIVISLAB.BO",
]

# Shenzhen (SZSE) China (user 2026-07-24 P-02 L320; added 2026-07-25) — SCAN-ONLY (Shanghai .SS + Hong
# Kong already live; this is Shenzhen with .SZ). Curated verified large-caps (000/002/300 boards).
SHENZHEN = [
    "000001.SZ", "000002.SZ", "000063.SZ", "000100.SZ", "000333.SZ", "000338.SZ", "000568.SZ", "000651.SZ",
    "000725.SZ", "000858.SZ", "002027.SZ", "002142.SZ", "002230.SZ", "002304.SZ", "002352.SZ", "002415.SZ",
    "002475.SZ", "002594.SZ", "002714.SZ", "300059.SZ", "300124.SZ", "300750.SZ", "300760.SZ",
]

UNIVERSE = {
    "FTSE 100":     FTSE100,
    "FTSE 250":     FTSE250,
    "NASDAQ 100":   NASDAQ100,       # listed before S&P 500 so dual-listed mega-caps file under NASDAQ
    "S&P 500":      SP500,
    "DAX":          GERMANY,        # DAX 40 + MDAX equities; Location "Germany" (user 2026-07-06)
    "Euronext":     EURONEXT,       # top 100 pan-European Euronext equities; Location "Europe" (user 2026-07-14)
    "SSE (Shanghai)": SHANGHAI,     # Shanghai Stock Exchange equities; Location China (user 2026-07-06)
    "Hong Kong":    HONGKONG,       # top Hong Kong equities (user 2026-07-03)
    "Nikkei 225":   JAPAN,          # Nikkei 225 equities; Location Japan (user 2026-07-06)
    "ASX":          AUSTRALIA,      # ASX equities; Location Australia (user 2026-07-06)
    "NSE (India)":  INDIA,          # NSE / NIFTY equities; Location India (user 2026-07-06)
    "Commodities":  COMMODITIES,    # metals + energy
    "Indices":      INDICES,        # major market indices (split from "Indices & FX", user 2026-06-30)
    "FX":           FX,             # currency pairs (split from "Indices & FX", user 2026-06-30)
    "Crypto":       CRYPTO,         # top-5 by market cap
    "Government Bonds": GOVT_BONDS,  # sovereign-bond ETFs, SCAN-ONLY / no IG epics (user 2026-07-24, P-02 L312)
    "BSE (India)":      BSE_INDIA,   # Bombay Stock Exchange equities (.BO), SCAN-ONLY (user 2026-07-24, P-02 L319)
    "SZSE (Shenzhen)":  SHENZHEN,    # Shenzhen equities (.SZ), SCAN-ONLY (user 2026-07-24, P-02 L320)
    # DAX suspended 2026-06-05 — re-add when reinstated
    # NB (user 2026-06-22): FTSE 100/250 and S&P 500 are the EQUITY coverage — examples of markets,
    # not the whole universe. The report spans all asset classes: UK + US equities, commodities,
    # indices, FX and crypto. Widen the equity lists / add more US names here as needed.
}


# ----------------------------------------------------------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------------------------------------------------------

def scan_universe(progress_cb=None) -> dict:
    """
    Scan all instruments in all indices.
    Returns dict keyed by index name, each value a list of HVF result dicts.
    progress_cb(done, total) is called after each instrument (user 2026-06-29: live refresh progress).
    """
    from price_action import get_hvf_signal_mtf, get_trend_structure

    results = {}
    # De-dup across markets (user 2026-06-29: NASDAQ 100 + S&P 500 overlap heavily) — a ticker is scanned
    # once, attributed to the first market that lists it.
    total   = len({t for v in UNIVERSE.values() for t in v})
    done    = 0
    seen    = set()

    for index_name, tickers in UNIVERSE.items():
        index_results = []
        log.info(f"Scanning {index_name} ({len(tickers)} tickers)...")
        for ticker in tickers:
            if ticker in seen:
                continue
            seen.add(ticker)
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
            if progress_cb:
                try:
                    progress_cb(done, total)
                except Exception:
                    pass
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

    from price_action import entry_chase_pct
    from config import MAX_ENTRY_CHASE_PCT, MIN_PUBLISH_QUALITY
    _missed = _subq = 0

    for index_name, results in all_results.items():
        for r in results:
            sig = r.get("hvf_signal", "")
            rr  = r.get("risk_reward") or 0
            q   = r.get("pattern_quality") or 0
            if sig in ("READY", "TRIGGERED") and rr >= HVF_MIN_RR:
                # Missed-entry guard (user 2026-06-20): a TRIGGERED setup whose price has already
                # run > MAX_ENTRY_CHASE_PCT past the entry is no longer actionable — don't publish
                # it (would be chasing). READY setups haven't reached entry yet, so never "missed".
                chase = entry_chase_pct(r)
                if sig == "TRIGGERED" and chase is not None and chase > MAX_ENTRY_CHASE_PCT:
                    _missed += 1
                    continue
                # Quality gate (user 2026-06-20): the headline "tradeable" list is the DECENT
                # setups only — sub-MIN_PUBLISH_QUALITY ones drop to the developing watch list so
                # the per-market "N candidates" count is credible (was inflated by Q38/Q48 junk).
                if q < MIN_PUBLISH_QUALITY:
                    _subq += 1
                    developing.append(r)
                    continue
                tradeable.append(r)
            elif sig == "DEVELOPING":
                developing.append(r)

    if _missed or _subq:
        log.info(f"categorise: excluded {_missed} missed-entry setup(s) (> {MAX_ENTRY_CHASE_PCT}% "
                 f"past entry); demoted {_subq} sub-quality setup(s) (< {MIN_PUBLISH_QUALITY}) to developing")

    # Order by "most relevant for action now" (user 2026-06-22): R:R ÷ distance-to-entry, DESC —
    # high R:R AND close to triggering ranks top (price_action.action_score). group_by_market keeps
    # this order within each market.
    from price_action import action_score
    tradeable.sort(key=action_score, reverse=True)

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

    # ── Entry-distance rule (user 2026-06-22) — the now-vs-entry % is the key relevance filter ────
    from config import MAX_DEVELOPING_DISTANCE_PCT
    def _entry_within_watch(r):
        cur, entry = r.get("current_price"), r.get("h3_level")
        if not isinstance(cur, (int, float)) or not cur or not isinstance(entry, (int, float)):
            return True   # unknown distance — keep (don't move/hide on missing data)
        return abs(entry / cur - 1) * 100 <= MAX_DEVELOPING_DISTANCE_PCT

    # (D) A would-be tradeable setup whose ENTRY is >MAX_DEVELOPING_DISTANCE_PCT (10%) from the live
    # price is not actionable now — MOVE it from tradeable to the developing watch list (so it is
    # NOT published), regardless of R:R / quality.
    _far = [r for r in tradeable if not _entry_within_watch(r)]
    if _far:
        tradeable    = [r for r in tradeable if _entry_within_watch(r)]
        developing.extend(_far)
        log.info(f"categorise: moved {len(_far)} tradeable setup(s) with entry > "
                 f"{MAX_DEVELOPING_DISTANCE_PCT}% from price to developing (not actionable now)")

    # (E) Developing DISPLAY relevance: don't show developing setups whose entry is >10% from price
    # (user 2026-06-22: "for developing don't bother to show anything that is over 10% away"). So a
    # far setup is removed from publishing (D) AND from the watch-list display (E).
    _before = len(developing)
    developing = [r for r in developing if _entry_within_watch(r)]
    if _before != len(developing):
        log.info(f"categorise: hid {_before - len(developing)} developing setup(s) with entry "
                 f"> {MAX_DEVELOPING_DISTANCE_PCT}% from price")

    developing.sort(key=action_score, reverse=True)
    return tradeable, developing


# ----------------------------------------------------------------------------------------------------------------------
# Slack report
# ----------------------------------------------------------------------------------------------------------------------

def _fmt_price(p, suffix=""):
    """Format price with decimals scaled to magnitude (user 2026-06-22: FX/small-cap crypto were
    rendering as "1.3"). Small prices (FX ~1.13, XRP ~0.52) get 4 dp; mid prices 2 dp; large
    (indices, BTC) 1 dp."""
    if p is None:
        return "-"
    ap = abs(p)
    dec = 4 if ap < 10 else (2 if ap < 1000 else 1)
    return f"{p:,.{dec}f}{suffix}"


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
    # Current price + % distance of each level from it (user 2026-06-19): every line that
    # shows entry/stop/target also shows the live price and how far each level is from it.
    from price_action import pct_from_current, target_horizon
    cur    = r.get("current_price")
    now    = _fmt_price(cur)
    e_pct  = pct_from_current(r.get("h3_level"),   cur)
    s_pct  = pct_from_current(r.get("stop_level"), cur)
    t_pct  = pct_from_current(r.get("target"),     cur)
    _wrap  = lambda p: f" ({p})" if p else ""
    # Expected time-to-target next to R:R (user 2026-06-19) — Slack only (this report posts to
    # #signals; it never goes on the X card/tweet).
    _hz    = target_horizon(r)
    _hz_s  = f"  ·  {_hz} to target" if _hz else ""
    # Prolonged-consolidation tag (user 2026-06-22): flag a funnel that has been forming a long time.
    from price_action import funnel_span_weeks, PROLONGED_FUNNEL_WEEKS
    _wks   = funnel_span_weeks(r)
    _coil  = f"  ·  coiling ~{_wks}wk" if (_wks and _wks >= PROLONGED_FUNNEL_WEEKS) else ""
    line = (f"{d}{s} *{_label(t)}*  R:R {rr}{_hz_s}  Quality {q}/100{_coil}  [{tf}] · {idx}\n"
            f"    Now {now}  Entry {entry}{_wrap(e_pct)}  Stop {stop}{_wrap(s_pct)}  Target {target}{_wrap(t_pct)}")
    # Tight-stop label (backlog #9b): a funnel whose stop is < TIGHT_STOP_MIN_PCT of price
    # is structurally untradeable at IG intraday (spread + tick noise), so we DON'T trade it
    # — but it stays in the report, plainly labelled, because the pattern itself is valid
    # (user 2026-06-15, option b). The inflated R:R is the tell of the tiny stop.
    if r.get("tight_stop_intraday"):
        sp = r.get("stop_pct")
        line += (f"\n    ⚠️ Stop only {sp}% of price — too tight for IG intraday "
                 f"(spread + tick noise); not auto-traded. The {rr} R:R is INFLATED by the tiny stop.")
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
    from price_action import pct_from_current
    cur    = r.get("current_price")
    now    = _fmt_price(cur)
    _wrap  = lambda p: f" ({p})" if p else ""
    e_pct  = _wrap(pct_from_current(r.get("h3_level"),   cur))
    s_pct  = _wrap(pct_from_current(r.get("stop_level"), cur))
    t_pct  = _wrap(pct_from_current(r.get("target"),     cur))
    others = _other_timeframes(r)
    also = ("  · also " + ", ".join(_tf_short(c.get("hvf_timeframe")) for c in others)) if others else ""
    from price_action import funnel_span_weeks, PROLONGED_FUNNEL_WEEKS
    _wks  = funnel_span_weeks(r)
    _coil = f"  · coiling ~{_wks}wk" if (_wks and _wks >= PROLONGED_FUNNEL_WEEKS) else ""
    return (f"{d}👀 *{_label(t)}*  R:R {rr}  [{tf}] · {idx}  "
            f"Now {now}  Entry {entry}{e_pct}  Stop {stop}{s_pct}  Target {target}{t_pct}{_coil}{also}")


def build_slack_blocks(tradeable, developing, scan_time: str, per_market_n: int = None) -> list:
    """Build Slack Block Kit message for the daily HVF report. per_market_n overrides how many
    setups to show per market (user 2026-07-03: arw-rw-hvf wants the top 3, not 10)."""
    _pm = per_market_n or PER_MARKET_TOP_N
    # Horizon cap (user 2026-06-29): do NOT show anything longer than 9 months to target in the HVF
    # Slack channel. target_horizon_days = the funnel's H1->H3 span (the expected time-to-target);
    # 9 months ~ 274 days. Applied here so it only affects this Slack report, not the web app / X.
    from price_action import target_horizon_days
    def _within_horizon(r):
        _d = target_horizon_days(r)
        return not (_d and _d / 30.44 > 9)
    tradeable  = [r for r in tradeable if _within_horizon(r)]
    developing = [r for r in developing if _within_horizon(r)]

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
                          f"Scanned: FTSE 100 · FTSE 250 · NASDAQ 100 · S&P 500 · DAX · SSE (Shanghai) · Hong Kong · Nikkei 225 · ASX · NSE (India) · Commodities · Indices · FX · Crypto")}
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
        groups = group_by_market(tradeable, n=_pm, market_order=MARKET_ORDER)
        shown  = sum(len(rs) for _, rs in groups)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": (f"*⚡ TRADEABLE setups* — showing the top {_pm} per market "
                              f"({shown} shown of {len(tradeable)} that qualify)")}
        })
        for market, rows in groups:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*{_index_short(market)}* — top {len(rows)} of {totals.get(market, len(rows))} candidates"}
            })
            # However #2 (user 2026-06-19): number each list from 1, restarting per market.
            # Trailing newline per entry = a blank line between instruments (user 2026-06-20:
            # "with so much detail a spaced line is needed between each instrument").
            _numbered = [f"{i}. {_tradeable_line(r)}\n" for i, r in enumerate(rows, 1)]
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
        # Hide a market's DEVELOPING watch list when it ALREADY has more than 10 tradeable setups
        # (user 2026-06-26): there's plenty to act on in that market, so the watch list is just noise.
        _trd_by_mkt = Counter(r.get("index") for r in tradeable)
        groups = group_by_market(developing, n=_pm, market_order=MARKET_ORDER)
        _hidden_mkts = [m for m, _ in groups if _trd_by_mkt.get(m, 0) > DEVELOPING_HIDE_IF_TRADEABLE_OVER]
        groups = [(m, rs) for m, rs in groups if _trd_by_mkt.get(m, 0) <= DEVELOPING_HIDE_IF_TRADEABLE_OVER]
        shown  = sum(len(rs) for _, rs in groups)
        _hidden_note = (f"  ·  hidden for {len(_hidden_mkts)} market(s) with >"
                        f"{DEVELOPING_HIDE_IF_TRADEABLE_OVER} tradeable: "
                        f"{', '.join(_index_short(m) for m in _hidden_mkts)}") if _hidden_mkts else ""
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": (f"*👀 DEVELOPING (watch list)* — showing the top {_pm} per market "
                              f"({shown} shown of {len(developing)} on watch){_hidden_note}")}
        })
        for market, rows in groups:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*{_index_short(market)}* — top {len(rows)} of {totals.get(market, len(rows))} candidates"}
            })
            # However #2 (user 2026-06-19): number each list from 1, restarting per market.
            # Blank line between instruments (user 2026-06-20).
            _numbered = [f"{i}. {_developing_line(r)}\n" for i, r in enumerate(rows, 1)]
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
                               f"daily-180 · daily-240 · weekly | "
                               f"Min {HVF_MIN_RR}:1 R:R to trade | "
                               f"\"Also on\" = other timeframes the same funnel appears on "
                               f"(⚡ triggered · ✅ ready · 👀 developing), each with its OWN raw "
                               f"entry/stop/target/R:R; the headline figures are for the primary "
                               f"timeframe in [brackets] (the only one exhaustion-anchored + IG-validated) | "
                               f"Generated {scan_time} UTC")}]
    })

    return blocks


def post_to_slack(blocks: list, rw_blocks: list = None):
    # Post the report to the primary channel AND any additional channels (user 2026-06-26, C):
    # SLACK_RW_HVF is an extra HVF-report channel. Each is an independent webhook; a missing/failed
    # one doesn't stop the others. rw_blocks (top-3-per-market) is used for SLACK_RW_HVF when given
    # (user 2026-07-03); #signals always gets the full `blocks`.
    import requests
    targets = [("SLACK_SIGNALS", True, blocks), ("SLACK_RW_HVF", False, rw_blocks or blocks)]
    posted_any = False
    for env_name, warn_missing, _blocks in targets:
        url = os.environ.get(env_name, "")
        if not url:
            if warn_missing:
                log.warning(f"{env_name} not set — skipping Slack post")
            continue
        try:
            resp = requests.post(url, json={"blocks": _blocks}, timeout=15)
            if resp.status_code != 200:
                log.error(f"Slack post failed ({env_name}): {resp.status_code} {resp.text[:200]}")
            else:
                posted_any = True
                log.info(f"HVF report posted ({env_name})")
        except Exception as e:
            log.error(f"Slack post error ({env_name}): {e}")
    return posted_any


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
    # Record every TRIGGERED funnel to Supabase for performance tracking (user 2026-06-30, HVF status).
    try:
        from hvf_recorder import record_triggers
        record_triggers([r for res in all_results.values() for r in res], "daily_report")
    except Exception as _e:
        log.warning(f"hvf_triggers recording skipped: {_e}")
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
    # arw-rw-hvf gets the top 3 per market (user 2026-07-03: 10 is too much); #signals keeps the full set.
    rw_blocks = build_slack_blocks(tradeable, developing, scan_time, per_market_n=3)
    post_to_slack(blocks, rw_blocks=rw_blocks)
    try:   # record this run in the web app's Batch Activity (user 2026-07-03)
        from web_store import append_batch
        append_batch("cron-job.org", f"HVF report — {len(tradeable)} tradeable / {len(developing)} developing", by="cron")
    except Exception:
        pass

    # Output mode (user 2026-06-22):
    #   normal     — analytical #signals report + changed-only Slack drafts (with PNGs) + live X publish.
    #   slack_only — analytical #signals report ONLY (review the order; no drafts, no X).
    #   slack_png  — analytical report + Slack drafts WITH the card + 3yr PNGs for ALL tradeable, NO X.
    _mode = os.environ.get("HVF_REPORT_MODE", "").strip().lower()
    if not _mode and (os.environ.get("HVF_REPORT_SKIP_X", "").strip().lower() in ("1", "true", "yes")
                      or "--no-x" in sys.argv):
        _mode = "slack_only"        # back-compat with the earlier flag

    # X-draft Slack posts (card + 3yr PNG per instrument, top X_DRAFT_PER_MARKET/market). These go to
    # the Slack draft channel — NOT to X. The LIVE X publish is the separate step below, gated on mode.
    if tradeable and _mode != "slack_only":
        posted = []
        try:
            from intraday_signals import _generate_x_drafts
            # slack_png reviews EVERYTHING (not just changed); normal posts only changed instruments.
            posted = _generate_x_drafts(tradeable, changed_only=(_mode != "slack_png")) or []
        except Exception as e:
            log.warning(f"X draft generation failed (non-critical): {e}")
        if _mode in ("", "normal"):
            try:
                _publish_top_per_market_to_x(posted)   # the only step that posts to LIVE X
            except Exception as e:
                log.warning(f"live X publish failed (non-critical): {e}")
        else:
            log.info(f"HVF_REPORT_MODE={_mode}: Slack drafts posted WITH PNGs — no live X publish.")
    elif _mode == "slack_only":
        log.info("HVF_REPORT_MODE=slack_only — analytical #signals report only; no drafts, no X.")

    # Log to DB
    log_to_db(tradeable, developing, scan_time)

    log.info("HVF Daily Report complete.")


if __name__ == "__main__":
    main()
