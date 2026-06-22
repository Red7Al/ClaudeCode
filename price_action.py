# ======================================================================================================================
# File:         price_action.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Price action confirmation signals for the EndToEndTrading system.
#
# PURPOSE — Avoiding the falling knife
# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# COT data and macro fundamentals tell you WHAT to trade and in WHICH
# DIRECTION. Price action tells you WHEN to enter.
#
# Even if COT is bullish and fundamentals are aligned, you do not enter
# until price confirms. This is the difference between analysis and execution.
#
# Without price confirmation you risk:
#   - Buying into a structural downtrend that hasn't reversed yet
#   - Catching a falling knife — fundamentals turn bullish but price keeps falling
#   - Poor timing that turns a correct thesis into a losing trade
#
# Signals implemented:
# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
#
#   1. Range Breakout
#      Price breaks above a multi-month consolidation high (bullish)
#      or below a consolidation low (bearish).
#      Uses the highest high and lowest low of the past 3 months (60 trading days).
#      Most reliable when accompanied by expanding volume.
#
#   2. Trend Structure — Higher Highs / Higher Lows
#      A bullish trend is confirmed by a series of higher swing highs and
#      higher swing lows. A bearish trend by lower highs and lower lows.
#      Computed over the last 10 weekly candles.
#      The cleanest entry is on a pullback to a higher low, not at the high.
#
#   3. Volatility Compression → Expansion (Enhanced BB Squeeze)
#      Already computed in signals.py (BB squeeze). Here we also check
#      ATR contraction as a second compression measure.
#      ATR dropping to a 3-month low then expanding = high-probability breakout.
#
#   4. Moving Average Crossovers (20 / 50 / 200 SMA)
#      20 SMA > 50 SMA > 200 SMA = full bullish alignment ("Golden alignment")
#      Price above 200 SMA = long-term uptrend
#      Price crossing 50 SMA from below = medium-term momentum turning bullish
#      Used as a filter — not a signal on its own.
#
#   5. Failed Breakdown (Bullish)
#      Price breaks below a key support level (multi-month low) but then
#      closes back above it. The breakdown failed — sellers tried and failed.
#      One of the most reliable bullish signals in commodities.
#
#   6. Failed Breakout (Bearish)
#      Price breaks above a key resistance level but then closes back below.
#      Buyers tried and failed — bearish signal.
#
# Composite Price Action Score:
# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
#   Each signal contributes to a score from -100 to +100.
#   CONFIRM_LONG  = score >= +40  (price structure supports a long entry)
#   CONFIRM_SHORT = score <= -40  (price structure supports a short entry)
#   WAIT          = between -40 and +40 (no clear confirmation — do not enter)
#
# Data source: Yahoo Finance OHLCV (daily and weekly candles)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.30.0  2026-06-22  Alex Hind   (user 2026-06-22) funnel_span_weeks() + PROLONGED_FUNNEL_WEEKS (8): display-only helper to
#                                 recognise + comment on a prolonged consolidation (H1->H3 span). No detection change.
# 1.29.0  2026-06-21  Alex Hind   FIX get_trend_structure misclassification (user 2026-06-21, from "NKE 3yr looks bearish —
#                                 does it meet HVF?"): the classifier checked hh_hl thresholds BEFORE comparing to lh_ll, so a
#                                 net-bearish name (NKE: hh_hl=3, lh_ll=6) was tagged UPTREND and admitted a BULLISH funnel on a
#                                 multi-year downtrend. Now a trend requires its direction to outweigh the other; ties -> SIDEWAYS.
# 1.28.0  2026-06-20  Alex Hind   DISABLE the prior-trend gate pending calibration (CI regression case 10 failed): at 20% it
#                                 rejected the HIK.L frozen known-good (its prior impulse measures 12.5%) AND the MTF then
#                                 surfaced a wrong-direction bearish override. Full suite back to 27/27. Helper kept; the
#                                 20%-vs-validated-setups calibration is a user decision (BACKLOG).
# 1.27.0  2026-06-20  Alex Hind   Code-review fix: the prior-trend gate was only in get_hvf_signal (daily) — WEEKLY funnels
#                                 (_run_hvf_on_hist) bypassed it. Extracted shared _prior_trend_pct and applied it to BOTH
#                                 paths (weekly lookback 52 bars). No more gate drift between the two parallel detectors.
# 1.26.0  2026-06-20  Alex Hind   Prior-trend magnitude gate (Rule 1, user 2026-06-20): get_hvf_signal rejects a funnel whose
#                                 prior impulse (bounded ~120-bar run-up into H1/L1) is < MIN_PRIOR_TREND_PCT (20%).
#                                 Skipped when <15 pre-pivot bars (can't measure); per-timeframe, so the MTF scan keeps a
#                                 name if ANY timeframe has a valid >=20% impulse + funnel. Regression 19/0 (fixtures'
#                                 funnels sit early -> gate skips them). Real-world cut ~20% (not the naive 73%).
# 1.25.0  2026-06-20  Alex Hind   entry_chase_pct() helper (user 2026-06-20): signed % price has run past entry, to drop
#                                 missed-entry setups. support_resistance default window 20 -> 60 bars (a 20-bar S/R on a
#                                 tight coil was an absurd ~1% band).
# 1.24.0  2026-06-19  Alex Hind   support_resistance() + near_support_resistance() helpers (user 2026-06-19): standard
#                                 swing-low/high S/R and a "price near a level" check, so a setup near support/resistance
#                                 gets extra consideration (from the OCDO support criticism).
# 1.23.0  2026-06-19  Alex Hind   target_horizon() helper (user 2026-06-19): rough expected time-to-target from the funnel
#                                 formation span (H1->H3), timeframe fallback. Slack-only consumers; never on the X card.
# 1.22.0  2026-06-19  Alex Hind   Fix stale R:R-gate comment (said "= 2.5"; live HVF_MIN_RR is 3.0). Comment no longer
#                                 hardcodes the number — it drifts (2.0 -> 2.5 -> 3.0). Comment-only; no behaviour change.
# 1.21.0  2026-06-19  Alex Hind   analyse_price_action now returns current_price (user 2026-06-19) so consumers (e.g.
#                                 social_monitor's mention line) can show "% from current price" without a second fetch.
# 1.20.0  2026-06-19  Alex Hind   pct_from_current() canonical helper (user 2026-06-19): signed "% from current price" for a
#                                 level, so every report printing entry/stop/target next to the live price uses one format.
# 1.19.0  2026-06-19  Alex Hind   D220 -> D240 (user 2026-06-19, "global"): the long-term daily scan is now 240 bars,
#                                 not 220 — scan tuple (240,"daily-240"), get_hvf_signal/_get_daily defaults, and the IG
#                                 candle cap all 240. Historical war-story comments (RR.L/BP at 220) and test geometry
#                                 (synthetic 220-bar frames; the 220.5 PRICE literal) left as-is on purpose.
# 1.18.0  2026-06-19  Alex Hind   hvf_weight: R:R desc is now the PRIMARY sort (user 2026-06-19), then signal
#                                 (TRIGGERED>READY>DEVELOPING), then quality. Was signal->quality->R:R. Callers must pass
#                                 risk_reward for it to take effect.
# 1.17.0  2026-06-16  Alex Hind   group_by_market() + market_short() — CANONICAL per-market grouping for HVF outputs
#                                 (user 2026-06-16: "top 10 by market"). Groups already-weight-sorted rows by market,
#                                 caps to config.PER_MARKET_TOP_N each, orders by config.MARKET_ORDER. Single source of
#                                 truth shared by run_hvf_report, intraday_signals (X drafts) and quality_report.
# 1.16.0  2026-06-15  Alex Hind   mtf_timeframes now also carries each timeframe's h3_level/stop_level/target (user
#                                 2026-06-15: the dossier shows ALL figures per date range, not just the chosen one).
#                                 ADDITIVE — raw per-timeframe levels; only `best` is exhaustion-anchored/IG-validated.
#                                 No detection/selection change; suite green.
# 1.15.0  2026-06-15  Alex Hind   Backlog #9b (groundwork, INERT): get_hvf_signal_mtf now annotates the chosen result with
#                                 stop_pct + tight_stop_intraday (stop < config.TIGHT_STOP_MIN_PCT of price), carried into
#                                 analyse_price_action as hvf_stop_pct / hvf_tight_stop_intraday. ADDITIVE only — nothing
#                                 reads the flag for a decision yet (no trade/report behaviour change); shadow_diff_tight_
#                                 stop.py shows the blast radius for review before the skip/label wiring is merged. Suite green.
# 1.14.0  2026-06-14  Alex Hind   Code-review: new hvf_weight(signal, quality, risk_reward) — CANONICAL weight-order sort
#                                 key (TRIGGERED>READY>DEVELOPING, quality desc, R:R desc), single source of truth for the
#                                 "all lists in weight order" rule. Consolidates 6 duplicated sort keys (intraday drafts
#                                 ×2, run_hvf_report, generate_x_cards, quality_report, shadow_diff). Display-only; the MTF
#                                 best-selection (get_hvf_signal_mtf) is unchanged — detection untouched, suite green.
# 1.0.0   2026-05-30  Alex Hind   Initial build. Six price action signals with composite confirmation score.
# 1.1.0   2026-06-05  Alex Hind   Raised HVF_MIN_RR from 2.0 to 2.5 in both single-timeframe and multi-timeframe
#                                 scanners to match MIN_RISK_REWARD in config.py.
# 1.3.0   2026-06-06  Alex Hind   Fix 1: remove `if thresh != default` guard so config threshold is always authoritative
#                                 — previously changing PA_CONFIRM_THRESHOLD_DEFAULT had zero effect on standard
#                                 equities/indices. Fix 2 (HVF bypass): extend to cover direction conflicts: previously
#                                 only fired when verdict==WAIT, so a low-threshold crypto with pa_score=+22 would get
#                                 CONFIRM_LONG even while HVF was BEARISH+TRIGGERED. Now: always apply HVF direction,
#                                 force WAIT when HVF direction conflicts with PA score at bypass threshold.
# 1.4.0   2026-06-06  Alex Hind   _run_hvf_on_hist: add allow_bearish_override guard matching get_hvf_signal. Without
#                                 the guard, STRONG_UPTREND weekly charts had their trend overridden to DOWNTREND
#                                 whenever strict_dec or peak_dec fired — generating spurious short signals even on a
#                                 confirmed strong uptrend.
# 1.2.0   2026-06-05  Alex Hind   Per-instrument PA threshold (Fix 1): crypto now uses threshold=25, FX/commodities
#                                 30-35, equities/indices keep default 40. HVF TRIGGERED bypass (Fix 2): threshold
#                                 halved when HVF has confirmed entry — price has voted.
# 1.10.0  2026-06-12  Alex Hind   MERGE 9a: apply_exhaustion_amp1() wired into get_hvf_signal_mtf — every chosen result's
#                                 target amplitude is re-anchored to the prior trend's TRUE exhaustion extreme
#                                 (official Hunt method), target+R:R recomputed, R:R gate re-applied; entry/stop
#                                 unchanged. validate_hvf_with_ig keeps the re-anchored AMP1 (does not undo it).
#                                 Targets on daily-window setups grow (we were clipping the top); weekly/long
#                                 windows unchanged. Suite 24 cases green incl. case 13; shadow-diff was reviewed.
# 1.11.0  2026-06-13  Alex Hind   get_hvf_signal_mtf attaches mtf_timeframes — the full, weight-ordered set of every
#                                 timeframe whose funnel was detected — to the chosen result, so the HVF report can show
#                                 each instrument ONCE and list all its timeframes (user 2026-06-13). DETECTION UNCHANGED:
#                                 `best` selection, AMP1 re-anchor and the invariant guard are untouched; only the chosen
#                                 timeframe carries AMP1/IG numbers, the rest are raw-state annotations. Additive key only
#                                 — no existing key/value changes, so no detection shadow-diff needed; suite stays green.
# 1.12.0  2026-06-13  Alex Hind   Liquidity penalty on the quality SCORE (user 2026-06-13): _liquidity_penalty() subtracts
#                                 points (HVF_LIQUIDITY_TIERS_GBP) from the chosen result's pattern_quality by recent
#                                 median daily turnover (.L pence ÷100 to £), so thin small trusts no longer top the list.
#                                 Detection, timeframe choice and the R:R gate are untouched — quality only reorders the
#                                 list/drafts. One short daily fetch per instrument on the chosen result. Suite green
#                                 (no quality assertions); shadow-diff not required (no detection/level change).
# 1.13.0  2026-06-13  Alex Hind   Invariant-suppressed results now log to hvf_suppressed_log for reporting INSTEAD of a
#                                 Slack #alerts ping (user 2026-06-13: "log it, no slack alert" — e.g. DSCV.L R:R 100.73).
#                                 Suppression unchanged (bad data still never posted/traded); detection untouched; suite green.
# 1.9.2   2026-06-12  Alex Hind   PROTOTYPE compute_exhaustion_amp1() (backlog 9a, NOT wired into detection) — official-method
#                                 AMP1: re-anchors ONLY the clipped exhaustion extreme to full history, keeps the
#                                 funnel's own first-pullback pivot (avoids RW's 52wk-low over-extension). For shadow-
#                                 diffing via shadow_diff_amp1.py before any merge. No production behaviour change.
# 1.9.1   2026-06-12  Alex Hind   current_price stored on every HVF result (daily + weekly paths, empty dicts) — feeds
#                                 the "Now:" display in tweets and post cards (user 2026-06-12).
# 1.9.0   2026-06-12  Alex Hind   daily-180 timeframe added to the MTF scan (user request) — six-month window for
#                                 funnels whose H1 falls between the 90- and 220-day lookbacks. Blast radius covered:
#                                 scan loop + docstrings here, _tf_desc ("6-month") in intraday_signals, run_hvf_report
#                                 header/footer, AH skill docs (repacked). Verified: suite 22/22 green (fixture anchors
#                                 unchanged); live shadow-diff on 6 tickers — only HIK.L changed, traced to its breakout
#                                 fading into the 12-Jun close (1,480.0 vs 1,480.36 trigger), i.e. market movement,
#                                 not the new timeframe.
# 1.8.2   2026-06-12  Alex Hind   Absurd-target rejection at DETECTION (proactive #alerts sweep): ETHUSD bearish
#                                 funnel projected target −606 (full AMP1 below zero after a large prior range) and
#                                 alerted every 5-min scan; OCDO.L same class. Bearish targets ≤10% of entry, or any
#                                 target ≤0, are rejected before emission (daily + weekly paths) — the invariant guard
#                                 remains as backstop. Verified live: ETHUSD no pattern, META BEARISH valid, OCDO.L
#                                 target now positive. Suite case 12 added (22 cases).
# 1.8.1   2026-06-12  Alex Hind   FIX invariant false positive: h3_level stores the ENTRY in every result, and for
#                                 BEARISH patterns entry = L3 — so h3_level == l3_level BY DESIGN and the naive H3<=L3
#                                 check wrongly suppressed a legitimate META BEARISH TRIGGERED setup (18:56 UTC alert).
#                                 Bullish compares directly; bearish reconstructs true H3 from the stop (stop/1.002).
#                                 Regression suite had NO bearish detection case — three added (detect, invariants
#                                 accept entry==L3, target<entry<stop ordering). META verified live: BEARISH TRIGGERED
#                                 entry 594.81 R:R 4.56 invariants clean. Suite now 21 cases.
# 1.8.0   2026-06-12  Alex Hind   HVF correctness guarantees (user: "changes must NOT negatively impact the correct
#                                 calculation of the HVF method"): (a) check_hvf_invariants() — geometry rules every
#                                 emitted pattern must satisfy (H1>L1, H3>L3, convergence in (0,0.7), positive target,
#                                 stop<entry<target ordering, sane R:R, chronological pivots); (b) runtime guard in
#                                 get_hvf_signal_mtf alerts + suppresses any violating result (OCDO.L negative-target
#                                 class can never post); (c) flat-top tolerance applied to the DAILY path — the 1.6.0
#                                 fix had only landed in the weekly path (caught by test_hvf_method.py case 2);
#                                 (d) ig_validation_log daily cache (1.7.1). Covered by test_hvf_method.py: 18 cases,
#                                 frozen fixtures in tests_fixtures/, CI gate trading-hvf-tests.yml on every push.
# 1.7.0   2026-06-12  Alex Hind   IG broker data as ARBITER (user: "if the IG data is more accurate, please use it").
#                                 New validate_hvf_with_ig(): every UK (.L) tradeable setup is corroborated pivot-by-
#                                 pivot against IG daily candles before posting/trading — pass → entry/stop/target/R:R
#                                 recomputed from IG levels; fail → demoted to DEVELOPING with the mismatch named.
#                                 Weekly pivots compare across their whole week; synthetic L3 (midpoint/current-price
#                                 fallbacks, now flagged l3_synthetic) skips comparison. Allowance-guarded: skipped
#                                 below 1,500 of the 10,000/week IG budget (verified live). US feeds are clean — no
#                                 allowance spent.
# 1.6.0   2026-06-12  Alex Hind   THREE HVF detection fixes (user's colleague spotted a genuine RR.L funnel our scanner
#                                 missed — investigation proved them right):
#                                 (1) _sanitise_ohlc: phantom exchange wicks in Yahoo data clipped before pivot detection
#                                 (RR.L 29-May high printed 1,420 vs IG's real 1,345.9; lows 990/1,051/1,107 vs real
#                                 1,163/1,207/1,269 — the fake 1,420 broke the descending-highs line). Applied in
#                                 _get_daily, _get_weekly and the weekly MTF fetch.
#                                 (2) Spike-wick swing-low filter REMOVED: it excluded every low whose close sat >40% of
#                                 the range above the low — i.e. every hammer/capitulation bottom, the most structural
#                                 lows there are. RR.L had ONE swing low in 220 bars; no funnel could ever assemble.
#                                 Bad prints are now handled by (1) at the data layer.
#                                 (3) Flat-top tolerance: H3 may be flat vs H2 (0.5%, mirroring the existing flat-base
#                                 tolerance on lows) — a flat ceiling against rising lows is converging pressure.
#                                 Verified: RR.L now BULLISH READY entry 1,330 (colleague's level); HIK.L/LAND.L/MONY.L
#                                 unchanged; NVDA's old pattern was a false positive built on filtered-away lows and
#                                 correctly no longer detects.
# 1.5.0   2026-06-10  Alex Hind   HVF pivots now expose calendar dates (h1_date..l3_date) so the trade-open email can
#                                 draw the funnel on the real price timeline. Added to get_hvf_signal, _run_hvf_on_hist
#                                 and surfaced by analyse_price_action (hvf_h1_date..hvf_l3_date).
#
# Dependencies:
# ----------------------------------------------------------------------------------------------------------------------
#   pip install yfinance pandas numpy
# ======================================================================================================================

import logging
import numpy as np
import pandas as pd
import yfinance as yf

from config import (YAHOO_MAP, HVF_MIN_RR, PA_CONFIRM_THRESHOLDS,
                    PA_CONFIRM_THRESHOLD_DEFAULT, HVF_LIQUIDITY_TIERS_GBP,
                    TIGHT_STOP_MIN_PCT, MIN_PRIOR_TREND_PCT)

# Rule 1 (prior trend): minimum bars of pre-pivot history in the window needed to MEASURE the
# prior-trend magnitude. Below this we can't assess it, so the gate is skipped (the structural
# trend gate still applies, and the longer-timeframe scan re-checks with full history).
_MIN_PRIOR_BARS = 15
# Measure the prior IMPULSE over a BOUNDED window before the pivot (not the entire history — an
# ancient low would inflate the move and pass everything). Daily ~120 bars (~6mo run-up); weekly
# ~52 bars (~1yr) — fewer bars, longer calendar, matched to each timeframe's scale.
_PRIOR_LOOKBACK = 120
_PRIOR_LOOKBACK_WEEKLY = 52


def _prior_trend_pct(hist, pivot_bar, pivot_level, bullish, lookback):
    """Prior-impulse magnitude (%) leading INTO the funnel (HVF Rule 1). Bullish: rise from the
    lowest low in the `lookback` bars before H1 up to H1; bearish: fall from the highest high
    before L1 down to L1. Returns None when there's too little pre-pivot history to measure (the
    gate is then skipped). Bounded lookback so an ancient extreme can't inflate the move. SHARED
    by the daily (get_hvf_signal) and weekly (_run_hvf_on_hist) paths so the gate can't drift
    between the two parallel detectors."""
    try:
        if pivot_bar is None or pivot_bar < _MIN_PRIOR_BARS:
            return None
        lo = max(0, pivot_bar - lookback)
        if bullish:
            origin = float(hist["Low"].values[lo:pivot_bar].min())
            return (pivot_level - origin) / origin * 100 if origin > 0 else None
        origin = float(hist["High"].values[lo:pivot_bar].max())
        return (origin - pivot_level) / pivot_level * 100 if pivot_level > 0 else None
    except Exception:
        return None

log = logging.getLogger("price_action")

# Score threshold to confirm entry
CONFIRM_LONG_THRESHOLD  =  40
CONFIRM_SHORT_THRESHOLD = -40

# Lookback periods
RANGE_BREAKOUT_DAYS  = 60    # 3 months for range definition
TREND_STRUCTURE_WKLY = 10    # 10 weekly candles for HH/HL analysis
MA_CROSSOVER_DAYS    = 210   # enough for 200 SMA
FAILED_BREAK_DAYS    = 5     # candles to look back for failed break


# ======================================================================================================================
# Data fetching helper
# ======================================================================================================================

def _sanitise_ohlc(df: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    """
    Clip phantom wicks — bad exchange prints in Yahoo data that poison pivot
    detection (user 2026-06-12: RR.L showed a fake 1,420p high on 29 May vs
    IG's real 1,345.9p, plus fake lows of 990/1,051/1,107p vs IG's
    1,163/1,207/1,269p — the phantom high broke the descending-highs line so
    the genuine HVF funnel a colleague spotted was never detected).

    Rule: a wick (High above the bar body, or Low below it) longer than
    1.5 × the 20-bar rolling median of the bar range is not plausible trading
    — real RR.L wicks in the affected window were ≤ 45p while the phantoms
    were 80–180p. Such wicks are clipped back to the bar body. The rolling
    median is robust to the phantoms themselves (they are a small minority of
    bars). Applies to every pattern function via _get_daily/_get_weekly.
    """
    if df is None or df.empty or not {"Open", "High", "Low", "Close"}.issubset(df.columns):
        return df
    df = df.copy()
    body_hi = df[["Open", "Close"]].max(axis=1)
    body_lo = df[["Open", "Close"]].min(axis=1)
    rng_med = (df["High"] - df["Low"]).rolling(20, min_periods=5).median()
    rng_med = rng_med.fillna((df["High"] - df["Low"]).median())
    limit   = 1.5 * rng_med
    bad_up  = (df["High"] - body_hi) > limit
    bad_dn  = (body_lo - df["Low"]) > limit
    n_bad   = int(bad_up.sum()) + int(bad_dn.sum())
    if n_bad:
        df.loc[bad_up, "High"] = body_hi[bad_up]
        df.loc[bad_dn, "Low"]  = body_lo[bad_dn]
        log.info(f"OHLC sanitise {ticker}: clipped {n_bad} phantom wick(s) "
                 f"(>1.5× median bar range beyond the body)")
    return df


def _get_daily(ticker: str, days: int = 240) -> pd.DataFrame:
    """Fetch daily OHLCV data for a ticker, phantom wicks clipped. Empty DataFrame on failure."""
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period=f"{days + 30}d", interval="1d")
        hist = _sanitise_ohlc(hist, ticker)
        return hist.tail(days) if len(hist) >= days else hist
    except Exception as e:
        log.warning(f"Daily data fetch failed for {ticker}: {e}")
        return pd.DataFrame()


def _get_weekly(ticker: str, weeks: int = 30) -> pd.DataFrame:
    """Fetch weekly OHLCV data for a ticker, phantom wicks clipped. Empty DataFrame on failure."""
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period=f"{weeks * 7 + 30}d", interval="1wk")
        hist = _sanitise_ohlc(hist, ticker)
        return hist.tail(weeks) if len(hist) >= weeks else hist
    except Exception as e:
        log.warning(f"Weekly data fetch failed for {ticker}: {e}")
        return pd.DataFrame()


# ======================================================================================================================
# Signal 1 — Range Breakout
# ======================================================================================================================

def get_range_breakout(ticker: str) -> dict:
    """
    Detect a breakout from a multi-month consolidation range.

    Bullish: price closes above the 60-day highest high
    Bearish: price closes below the 60-day lowest low

    Returns:
        signal:      BULLISH_BREAKOUT / BEARISH_BREAKOUT / NONE
        range_high:  60-day high (resistance)
        range_low:   60-day low  (support)
        last_close:  current closing price
        pct_vs_high: how close to the high (%)
        pct_vs_low:  how close to the low (%)
    """
    result = {
        "signal":      "NONE",
        "range_high":  None,
        "range_low":   None,
        "last_close":  None,
        "pct_vs_high": None,
        "pct_vs_low":  None,
    }
    hist = _get_daily(ticker, days=RANGE_BREAKOUT_DAYS + 5)
    if hist.empty or len(hist) < 10:
        return result

    # Use all but the last candle to define the range
    range_data  = hist.iloc[:-1]
    current     = hist.iloc[-1]

    range_high  = float(range_data["High"].max())
    range_low   = float(range_data["Low"].min())
    last_close  = float(current["Close"])

    result["range_high"] = round(range_high, 4)
    result["range_low"]  = round(range_low,  4)
    result["last_close"] = round(last_close, 4)
    result["pct_vs_high"] = round((last_close - range_high) / range_high * 100, 2)
    result["pct_vs_low"]  = round((last_close - range_low)  / range_low  * 100, 2)

    if last_close > range_high:
        result["signal"] = "BULLISH_BREAKOUT"
    elif last_close < range_low:
        result["signal"] = "BEARISH_BREAKOUT"

    return result


# ======================================================================================================================
# Signal 2 — Trend Structure (Higher Highs / Higher Lows)
# ======================================================================================================================

def get_trend_structure(ticker: str) -> dict:
    """
    Assess trend structure using weekly swing highs and lows.

    Bullish trend: series of higher swing highs and higher swing lows
    Bearish trend: series of lower swing highs and lower swing lows

    Uses last 10 weekly candles. Counts how many consecutive higher/lower
    pivots appear to classify trend strength.

    Returns:
        signal:         STRONG_UPTREND / UPTREND / DOWNTREND / STRONG_DOWNTREND / SIDEWAYS
        hh_hl_count:    number of higher high / higher low pairs (positive = bullish)
        lh_ll_count:    number of lower high / lower low pairs (positive = bearish)
        last_weekly_close: most recent weekly close
    """
    result = {
        "signal":             "SIDEWAYS",
        "hh_hl_count":        0,
        "lh_ll_count":        0,
        "last_weekly_close":  None,
    }
    hist = _get_weekly(ticker, weeks=TREND_STRUCTURE_WKLY + 4)
    if hist.empty or len(hist) < 4:
        return result

    closes = hist["Close"].values
    highs  = hist["High"].values
    lows   = hist["Low"].values

    result["last_weekly_close"] = round(float(closes[-1]), 4)

    # Count higher highs + higher lows vs lower highs + lower lows
    hh_hl = 0
    lh_ll = 0

    for i in range(2, len(hist)):
        hh = highs[i] > highs[i-1]
        hl = lows[i]  > lows[i-1]
        lh = highs[i] < highs[i-1]
        ll = lows[i]  < lows[i-1]

        if hh and hl:
            hh_hl += 1
        if lh and ll:
            lh_ll += 1

    result["hh_hl_count"] = hh_hl
    result["lh_ll_count"] = lh_ll

    # Classify by the DOMINANT direction (user 2026-06-21 / fix). The old logic checked the
    # hh_hl thresholds BEFORE comparing to lh_ll, so a net-BEARISH name (e.g. NKE: hh_hl=3 but
    # lh_ll=6) was mislabelled UPTREND — which then admitted a BULLISH HVF on a stock in a
    # multi-year DOWNTREND. An up/down-trend now requires its direction to actually outweigh the
    # other (hh_hl > lh_ll for up, lh_ll > hh_hl for down); ties / weak structure = SIDEWAYS.
    if hh_hl >= 5 and hh_hl > lh_ll:
        result["signal"] = "STRONG_UPTREND"
    elif hh_hl >= 3 and hh_hl > lh_ll:
        result["signal"] = "UPTREND"
    elif lh_ll >= 5 and lh_ll > hh_hl:
        result["signal"] = "STRONG_DOWNTREND"
    elif lh_ll >= 3 and lh_ll > hh_hl:
        result["signal"] = "DOWNTREND"
    else:
        result["signal"] = "SIDEWAYS"

    return result


# ======================================================================================================================
# Signal 3 — ATR Compression / Expansion
# ======================================================================================================================

def get_atr_compression(ticker: str, period: int = 14) -> dict:
    """
    Detect ATR compression followed by expansion.

    ATR at a 3-month low = volatility compressed = coiling for a breakout.
    ATR expanding after a period of compression = breakout confirmed.

    This is the ATR equivalent of the Bollinger Band squeeze in signals.py.
    Together they provide two independent compression measures.

    Returns:
        compressed:       True if ATR is at or near its 3-month low
        expanding:        True if ATR increased this week vs last week
        atr_current:      current 14-period ATR
        atr_pct_rank:     percentile rank of current ATR vs 60 days
    """
    result = {
        "compressed":    False,
        "expanding":     False,
        "atr_current":   None,
        "atr_pct_rank":  50.0,
    }
    hist = _get_daily(ticker, days=80)
    if hist.empty or len(hist) < period + 5:
        return result

    high  = hist["High"]
    low   = hist["Low"]
    close = hist["Close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean().dropna()

    if len(atr) < 5:
        return result

    current_atr = float(atr.iloc[-1])
    prev_atr    = float(atr.iloc[-2])
    atr_series  = atr.values

    # Percentile rank — low rank = compressed
    pct_rank    = float(np.sum(atr_series <= current_atr) / len(atr_series) * 100)

    result["atr_current"]  = round(current_atr, 4)
    result["atr_pct_rank"] = round(pct_rank, 1)
    result["compressed"]   = pct_rank <= 20     # ATR in bottom 20th percentile
    result["expanding"]    = current_atr > prev_atr and pct_rank <= 30

    return result


# ======================================================================================================================
# Signal 4 — Moving Average Alignment
# ======================================================================================================================

def get_ma_alignment(ticker: str) -> dict:
    """
    Assess moving average structure using 20, 50, and 200 SMA.

    Full bullish alignment: 20 SMA > 50 SMA > 200 SMA AND price above all three
    Full bearish alignment: 20 SMA < 50 SMA < 200 SMA AND price below all three

    Returns:
        signal:      FULL_BULL / PARTIAL_BULL / NEUTRAL / PARTIAL_BEAR / FULL_BEAR
        price:       current price
        sma20:       20-day SMA
        sma50:       50-day SMA
        sma200:      200-day SMA
        price_vs_200: % above/below 200 SMA (long-term context)
        golden_cross: True if 50 SMA recently crossed above 200 SMA (within 20 days)
        death_cross:  True if 50 SMA recently crossed below 200 SMA
    """
    result = {
        "signal":       "NEUTRAL",
        "price":        None,
        "sma20":        None,
        "sma50":        None,
        "sma200":       None,
        "price_vs_200": None,
        "golden_cross": False,
        "death_cross":  False,
    }
    hist = _get_daily(ticker, days=MA_CROSSOVER_DAYS)
    if hist.empty or len(hist) < 200:
        return result

    close  = hist["Close"]
    sma20  = float(close.rolling(20).mean().iloc[-1])
    sma50  = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1])
    price  = float(close.iloc[-1])

    result["price"]        = round(price,  4)
    result["sma20"]        = round(sma20,  4)
    result["sma50"]        = round(sma50,  4)
    result["sma200"]       = round(sma200, 4)
    result["price_vs_200"] = round((price - sma200) / sma200 * 100, 2)

    # Check for recent golden/death cross (50 SMA vs 200 SMA in last 20 days)
    sma50_series  = close.rolling(50).mean().dropna()
    sma200_series = close.rolling(200).mean().dropna()
    if len(sma50_series) >= 20 and len(sma200_series) >= 20:
        recent_50  = sma50_series.iloc[-20:]
        recent_200 = sma200_series.iloc[-20:]
        diff       = recent_50.values - recent_200.values
        crosses    = np.diff(np.sign(diff))
        if any(crosses > 0):
            result["golden_cross"] = True
        if any(crosses < 0):
            result["death_cross"]  = True

    # Determine signal
    bulls = sum([price > sma20, price > sma50, price > sma200, sma20 > sma50, sma50 > sma200])
    bears = sum([price < sma20, price < sma50, price < sma200, sma20 < sma50, sma50 < sma200])

    if bulls == 5:
        result["signal"] = "FULL_BULL"
    elif bulls >= 3:
        result["signal"] = "PARTIAL_BULL"
    elif bears == 5:
        result["signal"] = "FULL_BEAR"
    elif bears >= 3:
        result["signal"] = "PARTIAL_BEAR"
    else:
        result["signal"] = "NEUTRAL"

    return result


# ======================================================================================================================
# Signal 5 — Candlestick Patterns (daily bars)
# ======================================================================================================================

def get_candlestick_pattern(ticker: str) -> dict:
    """
    Detect high-conviction single and two-bar candlestick patterns on daily bars.

    Patterns detected:
      BULLISH_ENGULFING  Today's bull candle fully engulfs yesterday's bear candle.
                         Strong reversal — institutional buying absorbed all sellers.
      BEARISH_ENGULFING  Today's bear candle fully engulfs yesterday's bull candle.
      HAMMER             Long lower wick (≥2× body), close near high.
                         Rejection of lower prices — buyers stepped in.
      SHOOTING_STAR      Long upper wick (≥2× body), close near low.
                         Rejection of higher prices — sellers stepped in.
      MARUBOZU_BULL      Near-bodyless candle, close at/near high, high volume.
                         Pure conviction — no wick = no hesitation.
      MARUBOZU_BEAR      Close at/near low, high volume.
      NONE               No significant pattern.
    """
    result = {"pattern": "NONE", "pattern_direction": None, "description": ""}
    hist = _get_daily(ticker, days=5)
    if len(hist) < 2:
        return result

    today = hist.iloc[-1]
    prev  = hist.iloc[-2]

    t_open  = float(today["Open"])
    t_close = float(today["Close"])
    t_high  = float(today["High"])
    t_low   = float(today["Low"])
    p_open  = float(prev["Open"])
    p_close = float(prev["Close"])

    t_body  = abs(t_close - t_open)
    t_range = t_high - t_low
    if t_range == 0:
        return result

    t_bull = t_close > t_open
    t_bear = t_close < t_open
    p_bull = p_close > p_open
    p_bear = p_close < p_open

    lower_wick = (min(t_open, t_close) - t_low)
    upper_wick = (t_high - max(t_open, t_close))

    # ── Bullish engulfing ─────────────────────────────────────────────────────────────────────────────────────────────
    if t_bull and p_bear and t_open < p_close and t_close > p_open:
        result["pattern"]           = "BULLISH_ENGULFING"
        result["pattern_direction"] = "BULLISH"
        result["description"]       = (
            "Bullish engulfing — today's buying completely absorbed yesterday's selling. "
            "High-conviction reversal signal."
        )
        return result

    # ── Bearish engulfing ─────────────────────────────────────────────────────────────────────────────────────────────
    if t_bear and p_bull and t_open > p_close and t_close < p_open:
        result["pattern"]           = "BEARISH_ENGULFING"
        result["pattern_direction"] = "BEARISH"
        result["description"]       = (
            "Bearish engulfing — today's selling completely absorbed yesterday's buying. "
            "High-conviction reversal signal."
        )
        return result

    # ── Hammer (bullish) ──────────────────────────────────────────────────────────────────────────────────────────────
    if (lower_wick >= 2 * t_body and
            upper_wick <= 0.3 * t_body and
            t_body > 0):
        result["pattern"]           = "HAMMER"
        result["pattern_direction"] = "BULLISH"
        result["description"]       = (
            f"Hammer — lower wick {lower_wick/t_body:.1f}× body. "
            "Buyers rejected lower prices; sellers exhausted."
        )
        return result

    # ── Shooting star (bearish) ───────────────────────────────────────────────────────────────────────────────────────
    if (upper_wick >= 2 * t_body and
            lower_wick <= 0.3 * t_body and
            t_body > 0):
        result["pattern"]           = "SHOOTING_STAR"
        result["pattern_direction"] = "BEARISH"
        result["description"]       = (
            f"Shooting star — upper wick {upper_wick/t_body:.1f}× body. "
            "Sellers rejected higher prices; buyers exhausted."
        )
        return result

    # ── Marubozu bull (full conviction) ───────────────────────────────────────────────────────────────────────────────
    if (t_bull and
            lower_wick <= 0.05 * t_range and
            upper_wick <= 0.05 * t_range):
        result["pattern"]           = "MARUBOZU_BULL"
        result["pattern_direction"] = "BULLISH"
        result["description"]       = "Bullish Marubozu — no wicks, pure buying conviction."
        return result

    # ── Marubozu bear ─────────────────────────────────────────────────────────────────────────────────────────────────
    if (t_bear and
            lower_wick <= 0.05 * t_range and
            upper_wick <= 0.05 * t_range):
        result["pattern"]           = "MARUBOZU_BEAR"
        result["pattern_direction"] = "BEARISH"
        result["description"]       = "Bearish Marubozu — no wicks, pure selling conviction."
        return result

    return result


# ======================================================================================================================
# Signal 6 & 7 — Failed Breakdown (Bullish) / Failed Breakout (Bearish)
# ======================================================================================================================

def get_failed_break(ticker: str) -> dict:
    """
    Detect failed breakdowns (bullish) and failed breakouts (bearish).

    Failed Breakdown (BULLISH):
        Price briefly closes below the 60-day range low (triggers stops)
        but within 1-3 candles recovers back above it.
        This traps short sellers and often leads to sharp moves higher.
        One of the highest-conviction signals in commodity markets.

    Failed Breakout (BEARISH):
        Price briefly closes above the 60-day range high
        but within 1-3 candles falls back below it.
        Traps late buyers — bearish signal.

    Returns:
        signal:         FAILED_BREAKDOWN / FAILED_BREAKOUT / NONE
        level:          the support/resistance level that was tested
        candles_since:  how many candles since the failed break
        description:    plain English narrative
    """
    result = {
        "signal":       "NONE",
        "level":        None,
        "candles_since": None,
        "description":  "",
    }
    hist = _get_daily(ticker, days=RANGE_BREAKOUT_DAYS + FAILED_BREAK_DAYS + 5)
    if hist.empty or len(hist) < RANGE_BREAKOUT_DAYS + 3:
        return result

    # Range defined on the 60-day window before the last 5 candles
    range_window = hist.iloc[-(RANGE_BREAKOUT_DAYS + FAILED_BREAK_DAYS):-(FAILED_BREAK_DAYS)]
    recent       = hist.iloc[-FAILED_BREAK_DAYS:]

    range_high = float(range_window["High"].max())
    range_low  = float(range_window["Low"].min())

    # Check each recent candle for a failed break
    for i, (idx, row) in enumerate(recent.iterrows()):
        # Failed breakdown: close went below range_low but current price is back above
        if float(row["Close"]) < range_low:
            # Check if subsequent candles recovered above range_low
            subsequent = recent.iloc[i+1:]
            if not subsequent.empty and float(subsequent["Close"].iloc[-1]) > range_low:
                result["signal"]       = "FAILED_BREAKDOWN"
                result["level"]        = round(range_low, 4)
                result["candles_since"] = len(subsequent)
                result["description"]  = (
                    f"Failed breakdown below {range_low:.4f} — "
                    f"sellers rejected, price recovered in {len(subsequent)} candle(s)"
                )
                return result

        # Failed breakout: close went above range_high but current price is back below
        if float(row["Close"]) > range_high:
            subsequent = recent.iloc[i+1:]
            if not subsequent.empty and float(subsequent["Close"].iloc[-1]) < range_high:
                result["signal"]       = "FAILED_BREAKOUT"
                result["level"]        = round(range_high, 4)
                result["candles_since"] = len(subsequent)
                result["description"]  = (
                    f"Failed breakout above {range_high:.4f} — "
                    f"buyers rejected, price fell back in {len(subsequent)} candle(s)"
                )
                return result

    return result


# ======================================================================================================================
# Composite Price Action Score and Confirmation
# ======================================================================================================================

def compute_price_action_score(
    range_breakout:  dict,
    trend_structure: dict,
    atr_compression: dict,
    ma_alignment:    dict,
    failed_break:    dict,
    candlestick:     dict = None,
) -> tuple[float, str]:
    """
    Combine all price action signals into a composite score (-100 to +100)
    and a confirmation verdict.

    Weighting:
        Range breakout:       ±30  (high conviction directional signal)
        Trend structure:      ±25  (structural context)
        MA alignment:         ±20  (trend filter)
        Failed break:         ±15  (high conviction reversal)
        ATR compression:      ±10  (timing confirmation)

    Returns:
        score:   float from -100 to +100
        verdict: CONFIRM_LONG / CONFIRM_SHORT / WAIT
    """
    score = 0.0

    # ── Range breakout ────────────────────────────────────────────────────────────────────────────────────────────────
    breakout_scores = {
        "BULLISH_BREAKOUT": +30,
        "BEARISH_BREAKOUT": -30,
        "NONE":               0,
    }
    score += breakout_scores.get(range_breakout.get("signal", "NONE"), 0)

    # ── Trend structure ───────────────────────────────────────────────────────────────────────────────────────────────
    trend_scores = {
        "STRONG_UPTREND":    +25,
        "UPTREND":           +15,
        "SIDEWAYS":            0,
        "DOWNTREND":         -15,
        "STRONG_DOWNTREND":  -25,
    }
    score += trend_scores.get(trend_structure.get("signal", "SIDEWAYS"), 0)

    # ── MA alignment ──────────────────────────────────────────────────────────────────────────────────────────────────
    ma_scores = {
        "FULL_BULL":    +20,
        "PARTIAL_BULL": +10,
        "NEUTRAL":        0,
        "PARTIAL_BEAR": -10,
        "FULL_BEAR":    -20,
    }
    score += ma_scores.get(ma_alignment.get("signal", "NEUTRAL"), 0)

    # Golden/death cross bonus
    if ma_alignment.get("golden_cross"):
        score += 5
    if ma_alignment.get("death_cross"):
        score -= 5

    # ── Failed break (high conviction reversal) ───────────────────────────────────────────────────────────────────────
    failed_scores = {
        "FAILED_BREAKDOWN": +15,
        "FAILED_BREAKOUT":  -15,
        "NONE":               0,
    }
    score += failed_scores.get(failed_break.get("signal", "NONE"), 0)

    # ── ATR compression + expansion (timing) ──────────────────────────────────────────────────────────────────────────
    if atr_compression.get("expanding") and atr_compression.get("compressed"):
        score += 10     # Compressed AND expanding = breakout confirmed
    elif atr_compression.get("compressed"):
        score += 5      # Still coiling — slight timing bonus

    # ── Candlestick pattern ───────────────────────────────────────────────────────────────────────────────────────────
    if candlestick:
        candle_scores = {
            "BULLISH_ENGULFING": +15,
            "BEARISH_ENGULFING": -15,
            "HAMMER":            +10,
            "SHOOTING_STAR":     -10,
            "MARUBOZU_BULL":     +12,
            "MARUBOZU_BEAR":     -12,
            "NONE":                0,
        }
        score += candle_scores.get(candlestick.get("pattern", "NONE"), 0)

    score = round(max(-100, min(100, score)), 1)

    if score >= CONFIRM_LONG_THRESHOLD:
        verdict = "CONFIRM_LONG"
    elif score <= CONFIRM_SHORT_THRESHOLD:
        verdict = "CONFIRM_SHORT"
    else:
        verdict = "WAIT"

    return score, verdict


# ======================================================================================================================
# Signal 8 — Hunt Volatility Funnel (HVF)
#
# The HVF is a continuation breakout pattern developed by Francis Hunt
# (TheMarketSniper). It is significantly more rigorous than a standard
# BB squeeze because it requires:
#
#   1. A clear prior trend (continuation, not reversal)
#   2. Exactly 3 alternating inflection points:
#        Lower Highs: H1 > H2 > H3
#        Higher Lows: L1 < L2 < L3
#      Volatility contracts into a funnel shape.
#   3. All levels are horizontal (pivot highs/lows), not trendline-based.
#   4. Entry: pending buy-stop at H3 (bullish) or sell-stop at L3 (bearish)
#   5. Stop: horizontal level just below L3 (bullish) or above H3 (bearish)
#   6. Target: H1-L1 distance measured from the midpoint of H3/L3
#      (often gives 3-5× R:R on quality setups — much better than 2× ATR)
#
# This replaces / upgrades our existing BB squeeze primary signal.
# ======================================================================================================================

def _find_swing_highs_lows(hist: pd.DataFrame, n: int = 5) -> tuple:
    """
    Find significant swing highs and lows using a lookback window of N bars.

    A swing high: the High at bar i is the maximum in the window [i-n, i+n]
                  AND it is higher than the immediate neighbours.
    A swing low:  the Low  at bar i is the minimum in the same window
                  AND it is lower than the immediate neighbours.

    N=5 on daily data gives 11-bar pivots — filters out minor noise while
    capturing the significant turns that form HVF inflection points.

    Bad exchange prints are handled upstream by _sanitise_ohlc (phantom wicks
    clipped at the data layer), so pivots here are taken from clean High/Low
    series with no close-position filtering — a hammer bottom (close well off
    the low) is a structural low, not noise.

    Returns:
        swing_highs: list of (bar_index, price) tuples, oldest first
        swing_lows:  list of (bar_index, price) tuples, oldest first
    """
    swing_highs = []
    swing_lows  = []

    highs  = hist["High"].values
    lows   = hist["Low"].values
    closes = hist["Close"].values
    n_bars = len(hist)

    for i in range(n, n_bars - n):
        window_hi = highs[i - n : i + n + 1]
        window_lo = lows[i  - n : i + n + 1]

        if (highs[i] == window_hi.max()
                and highs[i] > highs[i - 1]
                and highs[i] > highs[i + 1]):
            swing_highs.append((i, float(highs[i])))

        if (lows[i] == window_lo.min()
                and lows[i] < lows[i - 1]
                and lows[i] < lows[i + 1]):
            # NOTE (2026-06-12): the former "spike-wick filter" here (excluding
            # lows whose close sat >40% of the range above the low) was REMOVED.
            # It threw away every hammer/capitulation bottom — the MOST
            # structural lows that exist (a reversal day by definition closes
            # well off its low) — leaving RR.L with ONE swing low in 220 bars,
            # so no funnel could ever assemble and a genuine HVF was missed
            # (user's colleague spotted it 2026-06-12). The bad-print problem
            # the filter was patched in for (BP 500p phantom wick, 26-May-2026)
            # is now handled at the data layer by _sanitise_ohlc, which clips
            # implausible wicks before pivot detection.
            swing_lows.append((i, float(lows[i])))

    return swing_highs, swing_lows


def get_hvf_signal(ticker: str, lookback_days: int = 240,
                   trend_hint: dict = None) -> dict:
    """
    Detect a Hunt Volatility Funnel (HVF) continuation pattern.

    The algorithm:
      1. Fetch daily candles (lookback_days).
      2. Confirm a prior trend (uptrend → look for bullish HVF; downtrend → bearish).
      3. Find swing highs/lows (N=5 lookback).
      4. Search for the best valid triplet:
           H1 > H2 > H3   (lower highs — volatility contracting from above)
           L1 < L2 < L3   (higher lows — volatility contracting from below)
         with the pairs interleaving correctly in time and the funnel converging
         by at least 30% from H1-L1 to H3-L3.
      5. Calculate:
           Entry  = H3 (bullish pending buy-stop) or L3 (bearish sell-stop)
           Stop   = L3 − 0.2% (bullish) or H3 + 0.2% (bearish)
           Target = midpoint(H3, L3) ± (H1 − L1)

    Args:
        ticker:       instrument ticker
        lookback_days: bars of history to scan
        trend_hint:   pre-computed trend dict (from get_trend_structure) to
                      avoid a duplicate Yahoo Finance call when called from
                      analyse_price_action()

    Returns dict with keys:
        hvf_type:        "BULLISH" | "BEARISH" | None
        hvf_signal:      "TRIGGERED" (price past H3/L3) | "READY" | None
        h3_level:        pending-order entry price
        l3_level:        the third low (used for stop calculation)
        stop_level:      exact stop price
        target:          calculated price target
        risk_reward:     R:R at current price
        h1_level:        first high (for context)
        l1_level:        first low (for context)
        pattern_range:   H1-L1 distance
        bars_since_h3:   how recently H3 formed (freshness)
        pattern_quality: 0-100 composite quality score
        convergence:     current funnel width / initial funnel width (lower = tighter)
    """
    result = {
        "hvf_type": None, "hvf_signal": None,
        "h3_level": None, "l3_level": None, "stop_level": None,
        "target": None,   "risk_reward": None,
        "h1_level": None, "l1_level": None, "pattern_range": None,
        "bars_since_h3": None, "pattern_quality": 0, "convergence": None,
        "volume_confirmed": False, "current_price": None,
    }

    try:
        hist = _get_daily(ticker, days=lookback_days)
        if len(hist) < 60:
            return result

        current_price = float(hist["Close"].iloc[-1])
        result["current_price"] = round(current_price, 6)   # for tweet/card "Now:" display

        # Prior trend — use hint if supplied (avoids double API call)
        if trend_hint:
            trend_signal = trend_hint.get("signal", "SIDEWAYS")
        else:
            trend = get_trend_structure(ticker)
            trend_signal = trend.get("signal", "SIDEWAYS")

        # Swing detection — must happen before trend override (which uses swing data)
        swing_highs_5, swing_lows_5 = _find_swing_highs_lows(hist, n=5)
        swing_highs_3, swing_lows_3 = _find_swing_highs_lows(hist, n=3)

        if len(swing_highs_5) < 3 and len(swing_highs_3) < 3:
            return result
        if len(swing_lows_5) < 3 and len(swing_lows_3) < 3:
            return result

        # ── Recent-trend override ─────────────────────────────────────────────────────────────────────────────────────
        # The long-term 240-day trend can mask a post-peak reversal.
        # Example: BP rallied 355→609p (220-day = UPTREND) but then peaked at 609p
        # in March 2026 and has been printing lower highs ever since.  A bearish HVF
        # is forming but the dominant lookback never switches direction.
        #
        # Two detection methods — either triggers the override:
        #
        # A) Strict declining: last 3 swing highs are strictly h[-3]>h[-2]>h[-1]
        #    AND the H1→H3 decline is ≥5% (ignores minor oscillations in an uptrend)
        #
        # B) Peak-and-decline: there is a dominant peak in the last 90 bars AND
        #    current price is ≥7% below that peak AND every subsequent swing high
        #    after the peak is below the peak.  Catches BP-style tops where minor
        #    bounces prevent strict monotonic decline but the overall direction is clear.

        recent_swings_h = swing_highs_3[-5:] if len(swing_highs_3) >= 5 else swing_highs_3

        # Method A — strict monotonic decline with magnitude check
        strict_declining = (
            len(recent_swings_h) >= 3 and
            recent_swings_h[-1][1] < recent_swings_h[-2][1] < recent_swings_h[-3][1] and
            (recent_swings_h[-3][1] - recent_swings_h[-1][1]) / recent_swings_h[-3][1] >= 0.05
        )

        # Method B — peak-and-decline (handles bounces like BP 553→575 within downtrend)
        # Only fires when the peak IS the dominant high of the full lookback — i.e. a
        # genuine major top (BP 609p = 16-yr high), not routine consolidation in an
        # uptrend (Lloyds pulling back 8% after a local high that isn't the overall max).
        peak_and_decline = False
        if swing_highs_3:
            full_lookback_high = float(hist["High"].max())
            # Find the highest swing high in the last 90 bars
            post_bars = [(i, p) for i, p in swing_highs_3 if i >= len(hist) - 90]
            if post_bars:
                peak_bar, peak_price = max(post_bars, key=lambda x: x[1])
                pct_below_peak       = (peak_price - current_price) / peak_price
                at_all_time_high     = peak_price >= full_lookback_high * 0.98  # within 2% of max
                post_peak_highs      = [(i, p) for i, p in swing_highs_3 if i > peak_bar]
                all_below_peak       = all(p < peak_price for _, p in post_peak_highs)
                peak_and_decline = (
                    at_all_time_high and                 # peak must be the dominant high
                    pct_below_peak >= 0.07 and           # ≥7% off that peak
                    len(post_peak_highs) >= 2 and        # at least 2 lower highs formed
                    all_below_peak
                )

        # Rising highs (for bullish override)
        recent_highs_rising = (
            len(recent_swings_h) >= 3 and
            recent_swings_h[-1][1] > recent_swings_h[-2][1] > recent_swings_h[-3][1]
        )

        # Peak-and-decline only overrides when the long-term trend is NOT
        # STRONG_UPTREND — a 7-10% pullback in a confirmed strong uptrend is
        # normal consolidation, not a reversal. Reserve the override for stocks
        # where the long-term trend was moderate (UPTREND) or mixed (SIDEWAYS).
        allow_bearish_override = trend_signal not in ("STRONG_UPTREND",)

        if (strict_declining or peak_and_decline) and allow_bearish_override:
            effective_trend = "DOWNTREND"
        elif recent_highs_rising:
            effective_trend = "UPTREND"
        else:
            effective_trend = trend_signal

        # HVF only valid when there is a clear directional trend to continue
        if effective_trend not in ("STRONG_UPTREND", "UPTREND",
                                   "STRONG_DOWNTREND", "DOWNTREND"):
            return result

        bullish = effective_trend in ("STRONG_UPTREND", "UPTREND")

        # Search both swing sets; take the highest-quality pattern found
        best_pattern = None
        best_quality  = 0

        for swing_highs, swing_lows in [(swing_highs_5, swing_lows_5),
                                        (swing_highs_3, swing_lows_3)]:
            if len(swing_highs) < 3 or len(swing_lows) < 3:
                continue

            # Use the most recent 10 swings of each type for the search
            recent_highs = swing_highs[-10:]
            recent_lows  = swing_lows[-10:]

            # ── Search for valid H1>H2>H3 with interleaved L1<L2<L3 ───────────────────────────────────────────────────
            for hi in range(len(recent_highs)):
                for hj in range(hi + 1, len(recent_highs)):
                    for hk in range(hj + 1, len(recent_highs)):
                        h1, h2, h3 = recent_highs[hi], recent_highs[hj], recent_highs[hk]

                        # Condition 1: lower highs. H1 must sit meaningfully
                        # above H2 (initial contraction), but H3 may be FLAT vs
                        # H2 (0.5% tolerance) — the mirror image of the
                        # flat-base tolerance on lows below. A flat ceiling
                        # against rising lows is still converging pressure
                        # (RR.L 2026-06-12: highs 1,328/1,330/1,337 flat top vs
                        # lows rising 1,078→1,203 — rejected by strict h1>h2>h3
                        # by 0.1%). NOTE 2026-06-12: this tolerance initially
                        # landed only in the weekly path; the regression suite
                        # (test_hvf_method.py case 2) caught the daily gap.
                        if not (h1[1] > h2[1] * 1.005 and h3[1] <= h2[1] * 1.005):
                            continue

                        # Condition 2: H3 must be recent (within 60 daily bars)
                        bars_since_h3 = len(hist) - 1 - h3[0]
                        if bars_since_h3 > 60:
                            continue

                        # Condition 3: Pattern must span at least 10 bars (H1→H3)
                        if h3[0] - h1[0] < 10:
                            continue

                        # Find the most significant low between each pair of highs
                        lows_h1_h2 = [l for l in recent_lows if h1[0] < l[0] < h2[0]]
                        lows_h2_h3 = [l for l in recent_lows if h2[0] < l[0] < h3[0]]

                        if not lows_h1_h2 or not lows_h2_h3:
                            continue

                        # L1 = lowest low between H1 and H2
                        # L2 = lowest low between H2 and H3
                        l1 = min(lows_h1_h2, key=lambda x: x[1])
                        l2 = min(lows_h2_h3, key=lambda x: x[1])

                        # Condition 4: higher lows — L2 must be at or above L1.
                        # Allow 0.5% tolerance: a flat base (L2 ≈ L1) with declining
                        # highs still produces converging pressure and is a valid setup.
                        # This handles data-source precision differences (e.g. Yahoo
                        # Finance showing BP April lows as 531.3 vs 531.6 — 0.3p apart).
                        if l2[1] < l1[1] * 0.995:
                            continue

                        # L3: third higher low — after L2, at or above L2 (0.5% tol), below H3
                        l3_candidates = [
                            l for l in recent_lows
                            if l[0] > l2[0]
                            and l[1] >= l2[1] * 0.995
                            and l[1] < h3[1]
                        ]

                        l3_synthetic = False   # True when L3 is derived, not a real candle pivot
                        if l3_candidates:
                            l3 = max(l3_candidates, key=lambda x: x[0])
                        elif current_price > h3[1]:
                            all_lows_after_l2 = [
                                l for l in recent_lows
                                if l[0] > l2[0] and l[1] > l2[1] and l[1] < h3[1]
                            ]
                            all_lows_wider = [
                                l for l in swing_lows
                                if l[0] > l2[0] and l[1] > l2[1] and l[1] < h3[1]
                            ]
                            combined = all_lows_after_l2 + all_lows_wider
                            if combined:
                                l3 = max(combined, key=lambda x: x[1])
                            else:
                                l3 = (h3[0] - 1, round((l2[1] + h3[1]) / 2, 6))
                                l3_synthetic = True
                        else:
                            if l2[1] < current_price < h3[1]:
                                l3 = (len(hist) - 1, current_price)
                                l3_synthetic = True
                            else:
                                continue

                        # Condition 5: funnel must be converging (≥30% contraction)
                        # Also enforce a minimum funnel width: H3-L3 must be ≥1% of
                        # price — prevents degenerate patterns where flat lows + 0.5%
                        # tolerance produce a near-zero H3-L3 range and infinite R:R.
                        initial_range = h1[1] - l1[1]
                        current_range = h3[1] - l3[1]
                        min_width     = current_price * 0.01
                        if initial_range <= 0 or current_range < min_width:
                            continue
                        convergence = current_range / initial_range
                        if convergence >= 0.70:
                            continue

                        # ── Quality scoring ───────────────────────────────────────────────────────────────────────────
                        quality = 0
                        quality += int((1.0 - convergence) * 50)
                        quality += max(0, 30 - bars_since_h3)
                        quality += 20 if effective_trend.startswith("STRONG") else 10
                        span_h1_h2 = h2[0] - h1[0]
                        span_h2_h3 = h3[0] - h2[0]
                        if span_h1_h2 > 0:
                            symmetry = min(span_h1_h2, span_h2_h3) / max(span_h1_h2, span_h2_h3)
                            quality += int(symmetry * 10)
                        quality = min(100, quality)

                        if quality > best_quality:
                            best_quality = quality
                            best_pattern = {
                                "h1": h1, "h2": h2, "h3": h3,
                                "l1": l1, "l2": l2, "l3": l3,
                                "l3_synthetic": l3_synthetic,
                                "convergence": convergence,
                                "bars_since_h3": bars_since_h3,
                                "initial_range": initial_range,
                            }

        if best_pattern is None:
            return result

        # ── Unpack best pattern ───────────────────────────────────────────────────────────────────────────────────────
        h1 = best_pattern["h1"]
        h2 = best_pattern["h2"]
        h3 = best_pattern["h3"]
        l1 = best_pattern["l1"]
        l2 = best_pattern["l2"]
        l3 = best_pattern["l3"]
        initial_range = best_pattern["initial_range"]

        # ── Pivot DATES ───────────────────────────────────────────────────────────────────────────────────────────────
        # best_pattern stores each pivot as (bar_index, price). Map the index to a
        # calendar date so the trade-open email can overlay the funnel on the real
        # price timeline (lower-highs line H1→H3, higher-lows line L1→L3). User
        # 2026-06-10. Set on `result` now so BOTH the DEVELOPING and the tradeable
        # result paths below inherit them (neither overwrites the *_date keys).
        def _pivot_date(piv):
            try:
                return hist.index[piv[0]].strftime("%Y-%m-%d")
            except Exception:
                return None
        result.update({
            "h1_date": _pivot_date(h1), "h2_date": _pivot_date(h2), "h3_date": _pivot_date(h3),
            "l1_date": _pivot_date(l1), "l2_date": _pivot_date(l2), "l3_date": _pivot_date(l3),
            "l3_synthetic": best_pattern.get("l3_synthetic", False),
        })

        # ── Volume profile check (Pattern Checker criterion #4) ───────────────────────────────────────────────────────
        # Volume should DECLINE as price compresses into the funnel (the coil),
        # then EXPAND on the breakout above H3 (bullish) or below L3 (bearish).
        # We measure:
        #   funnel_avg_vol  = average volume from H1 to H3 (the compression window)
        #   pre_h1_avg_vol  = average volume in the equivalent window before H1
        #   breakout_vol    = volume on the most recent bar (or bar after H3 for TRIGGERED)
        volume_confirmed = False
        try:
            vol_series = hist["Volume"].values
            h1_bar = h1[0]; h3_bar = h3[0]
            funnel_len = h3_bar - h1_bar

            if funnel_len > 2:
                funnel_vols  = vol_series[h1_bar:h3_bar + 1]
                pre_h1_vols  = vol_series[max(0, h1_bar - funnel_len):h1_bar]
                funnel_avg   = float(funnel_vols.mean())  if len(funnel_vols)  > 0 else None
                pre_h1_avg   = float(pre_h1_vols.mean()) if len(pre_h1_vols)  > 0 else None
                recent_vol   = float(vol_series[-1])
                overall_avg  = float(vol_series.mean())

                vol_declining = (funnel_avg is not None and pre_h1_avg is not None
                                 and funnel_avg < pre_h1_avg * 0.85)   # 15% drier in funnel
                vol_expanding = recent_vol > overall_avg * 1.2          # 20% above average on break

                volume_confirmed = vol_declining or vol_expanding
        except Exception:
            volume_confirmed = False  # don't fail HVF just because volume data is missing

        # ── Entry, stop, and target ───────────────────────────────────────────────────────────────────────────────────
        # Target = midpoint(H3, L3) ± (H1 - L1)  [Hunt's formula]
        midpoint_h3_l3 = (h3[1] + l3[1]) / 2.0

        if bullish:
            hvf_type    = "BULLISH"
            entry_level = round(h3[1], 6)                        # buy-stop pending at H3
            stop_price  = round(l3[1] * 0.998, 6)               # 0.2% below L3
            target      = round(midpoint_h3_l3 + initial_range, 6)
        else:
            hvf_type    = "BEARISH"
            entry_level = round(l3[1], 6)                        # sell-stop pending at L3
            stop_price  = round(h3[1] * 1.002, 6)               # 0.2% above H3
            target      = round(midpoint_h3_l3 - initial_range, 6)

        # Has price already triggered? (broken above H3 for bullish)
        if bullish:
            triggered = current_price > h3[1]
        else:
            triggered = current_price < l3[1]
        hvf_signal_str = "TRIGGERED" if triggered else "READY"

        # ── Absurd-target rejection (ETHUSD 2026-06-12: bearish target −606,
        # alerting every 5-min scan; OCDO.L showed the same class). When the
        # projected move (full AMP1) exceeds the price level itself, Hunt's
        # formula extrapolates below zero — a pattern that cannot physically
        # complete. Reject at detection so it never reaches the invariant
        # guard, Slack, or a trade. Floor at 10% of entry: a target implying
        # a >90% collapse is not a tradeable continuation projection.
        if target <= entry_level * 0.10 and not bullish:
            log.info(f"HVF {ticker}: BEARISH target {target} below 10% of entry "
                     f"{entry_level} — projection not physically tradeable, rejected")
            return result
        if target <= 0:
            log.info(f"HVF {ticker}: target {target} not a positive price — rejected")
            return result

        # Risk/reward calculated from the ENTRY LEVEL (H3 for bullish, L3 for bearish).
        # Using entry — not current price — gives a stable R:R that is a property of
        # the setup itself, not a function of where price happens to be at scan time.
        # A READY trade with current price near the stop would give misleadingly huge
        # R:R from current price (Lloyds case: current ~100p, stop 100.5p → 443:1).
        risk   = abs(entry_level - stop_price)
        reward = abs(target - entry_level)
        rr     = round(reward / risk, 2) if risk > 0 else 0.0

        # ── Prior-trend magnitude gate (Rule 1) — DISABLED 2026-06-20 pending calibration ─────────────────────────────
        # The 20% gate rejected the HIK.L frozen known-good (prior impulse measures 12.5%) and,
        # with the bullish funnel killed, the MTF surfaced a WRONG-DIRECTION bearish override — so
        # it broke a validated fixture AND produced a worse detection (CI regression case 10).
        # The helper (_prior_trend_pct) is kept + tested; re-enable here once the threshold is
        # agreed and the frozen fixtures are updated deliberately. See BACKLOG "Prior-trend gate".
        # _pm = _prior_trend_pct(hist, h1[0] if bullish else l1[0], h1[1] if bullish else l1[1],
        #                        bullish, _PRIOR_LOOKBACK)
        # if _pm is not None and _pm < MIN_PRIOR_TREND_PCT:
        #     return result

        # ── R:R gate (Pattern Checker criterion #5) ───────────────────────────────────────────────────────────────────
        # Threshold imported from config.HVF_MIN_RR (aliased to MIN_RISK_REWARD).
        # Single source of truth — do not hardcode the number here (it drifts; was 2.0 -> 2.5 -> 3.0).
        if rr < HVF_MIN_RR:
            log.info(f"HVF {ticker}: pattern found but R:R {rr} < {HVF_MIN_RR} — DEVELOPING (watch, not trade)")
            result.update({
                "hvf_type":         hvf_type,
                "hvf_signal":       "DEVELOPING",       # new state: real pattern, R:R not there yet
                "h3_level":         entry_level,
                "l3_level":         round(l3[1], 6),
                "stop_level":       stop_price,
                "target":           target,
                "risk_reward":      rr,
                "h1_level":         round(h1[1], 6),
                "h2_level":         round(h2[1], 6),
                "l1_level":         round(l1[1], 6),
                "l2_level":         round(l2[1], 6),
                "pattern_range":    round(initial_range, 6),
                "bars_since_h3":    best_pattern["bars_since_h3"],
                "pattern_quality":  best_quality,
                "convergence":      round(best_pattern["convergence"], 3),
                "volume_confirmed": volume_confirmed,
            })
            return result

        result.update({
            "hvf_type":          hvf_type,
            "hvf_signal":        hvf_signal_str,
            "h3_level":          entry_level,
            "l3_level":          round(l3[1], 6),
            "stop_level":        stop_price,
            "target":            target,
            "risk_reward":       rr,
            "h1_level":          round(h1[1], 6),
            "h2_level":          round(h2[1], 6),
            "l1_level":          round(l1[1], 6),
            "l2_level":          round(l2[1], 6),
            "pattern_range":     round(initial_range, 6),
            "bars_since_h3":     best_pattern["bars_since_h3"],
            "pattern_quality":   best_quality,
            "convergence":       round(best_pattern["convergence"], 3),
            "volume_confirmed":  volume_confirmed,
        })

        log.info(
            f"HVF {ticker}: {hvf_type} {hvf_signal_str} | "
            f"quality={best_quality} convergence={best_pattern['convergence']:.2f} | "
            f"H3={entry_level} stop={stop_price} target={target} R:R={rr} "
            f"vol_confirmed={volume_confirmed}"
        )

    except Exception as e:
        log.warning(f"HVF detection failed for {ticker}: {e}")

    return result


# ======================================================================================================================
# Multi-timeframe HVF wrapper
# ======================================================================================================================

def _liquidity_penalty(ticker: str) -> int:
    """
    Quality-score penalty for thin liquidity (user 2026-06-13): illiquid names must
    not rank high on the HVF list. Returns 0 (liquid, or liquidity unknown — never
    penalise on missing data) down to the most negative tier in HVF_LIQUIDITY_TIERS_GBP
    (e.g. -40 for very thin small investment trusts).

    Liquidity = recent median DAILY turnover (Close × Volume) over the last ~30
    sessions. ".L" prices are quoted in pence, so turnover is ÷100 to pounds before
    the GBP tiers are applied. One short daily fetch per instrument; computed on the
    chosen MTF result only (not per timeframe), so it never changes which timeframe
    was selected — it only reorders instruments against each other.
    """
    try:
        import yfinance as yf
        yt   = YAHOO_MAP.get(ticker, ticker)
        hist = yf.Ticker(yt).history(period="2mo", interval="1d")
        if hist is None or hist.empty or "Volume" not in hist or "Close" not in hist:
            return 0
        turn = float((hist["Close"] * hist["Volume"]).tail(30).median())
        if not turn or turn != turn:          # 0 or NaN → unknown, do not penalise
            return 0
        if ticker.endswith(".L"):
            turn /= 100.0                      # pence → pounds
        for floor, penalty in HVF_LIQUIDITY_TIERS_GBP:
            if turn >= floor:
                return penalty
        return 0
    except Exception as e:
        log.debug(f"liquidity penalty failed for {ticker}: {e}")
        return 0


def get_hvf_signal_mtf(ticker: str, trend_hint: dict = None) -> dict:
    """
    Run HVF detection across three timeframes and return the best result.

    Timeframes:
      daily-240   Standard full-history scan — catches mature multi-month funnels
      daily-180   Six-month scan (user 2026-06-12) — funnels that formed after an
                  H1 the 240-day window dates too early, without losing freshness
      daily-90    Shorter daily scan — catches post-peak reversals forming over
                  the last 3 months (e.g. a stock topping after a big rally)
      weekly      Weekly candles via a separate fetch — catches large-scale funnels
                  spanning many months that daily noise can mask

    Priority: TRIGGERED > READY > DEVELOPING, then highest quality within state.
    The timeframe that produced the result is recorded in hvf_timeframe.
    """
    yticker = YAHOO_MAP.get(ticker, ticker)

    candidates = []

    # ── daily-240, daily-180, daily-90, daily-60, daily-30 ────────────────────────────────────────────────────────────
    for days, label in [(240, "daily-240"), (180, "daily-180"), (90, "daily-90"),
                        (60, "daily-60"),   (30, "daily-30")]:
        try:
            r = get_hvf_signal(ticker, lookback_days=days, trend_hint=trend_hint)
            if r.get("hvf_type"):
                r["hvf_timeframe"] = label
                candidates.append(r)
        except Exception as e:
            log.debug(f"HVF {label} failed for {ticker}: {e}")

    # ── weekly ────────────────────────────────────────────────────────────────────────────────────────────────────────
    try:
        import yfinance as yf
        t        = yf.Ticker(yticker)
        hist_wk  = _sanitise_ohlc(t.history(period="3y", interval="1wk").dropna(), ticker)
        if len(hist_wk) >= 30:
            # Patch _get_daily to return weekly bars for this call
            # by temporarily overriding the history fetch inline
            r_wk = _run_hvf_on_hist(ticker, hist_wk)
            if r_wk.get("hvf_type"):
                r_wk["hvf_timeframe"] = "weekly"
                candidates.append(r_wk)
    except Exception as e:
        log.debug(f"HVF weekly failed for {ticker}: {e}")

    if not candidates:
        empty = {k: None for k in [
            "hvf_type", "hvf_signal", "h3_level", "l3_level", "stop_level",
            "target", "risk_reward", "h1_level", "l1_level", "pattern_range",
            "bars_since_h3", "pattern_quality", "convergence", "volume_confirmed", "current_price",
        ]}
        empty["hvf_timeframe"] = None
        return empty

    # Pick best: TRIGGERED > READY > DEVELOPING, then quality
    signal_rank = {"TRIGGERED": 3, "READY": 2, "DEVELOPING": 1}
    best = max(candidates,
               key=lambda r: (signal_rank.get(r.get("hvf_signal", ""), 0),
                              r.get("pattern_quality", 0)))

    # ── Official-method AMP1 exhaustion anchor (backlog 9a, merged 2026-06-12):
    # re-anchor the target amplitude to the prior trend's true exhaustion extreme,
    # recompute target + R:R, re-apply the R:R gate. Entry/stop unchanged. One
    # long-history fetch per instrument (on the chosen result only).
    best = apply_exhaustion_amp1(ticker, best)

    # ── Runtime invariant guard (user 2026-06-12): a result that breaks the
    # pattern's own geometry must NEVER reach Slack or a trade. Alert and
    # suppress instead of surfacing nonsense (e.g. OCDO.L negative target).
    violations = check_hvf_invariants(best)
    if violations:
        log.error(f"HVF invariant violation {ticker}: {violations} — result suppressed")
        # Bad-data setup caught by the runtime guard — never posted/traded. Record it in
        # hvf_suppressed_log for periodic reporting; NO Slack #alerts ping (user 2026-06-13:
        # these are caught-and-binned data-quality events, not actionable alerts).
        try:
            from db_pool import get_db as _gdb
            _sdb = _gdb()
            _sdb.run("""insert into hvf_suppressed_log
                        (ticker, hvf_timeframe, hvf_type, risk_reward, violations)
                        values (:t, :tf, :ht, :rr, :v)""",
                     t=ticker, tf=best.get("hvf_timeframe"), ht=best.get("hvf_type"),
                     rr=best.get("risk_reward"), v="; ".join(violations))
            _sdb.close()
        except Exception as e:
            log.warning(f"hvf_suppressed_log insert failed (non-critical): {e}")
        empty = {k: None for k in [
            "hvf_type", "hvf_signal", "h3_level", "l3_level", "stop_level",
            "target", "risk_reward", "h1_level", "l1_level", "pattern_range",
            "bars_since_h3", "pattern_quality", "convergence", "volume_confirmed", "current_price",
        ]}
        empty["hvf_timeframe"] = None
        return empty

    # ── Liquidity penalty (user 2026-06-13): demote illiquid names so thin small
    # trusts can't top the list. Applied to the chosen result's quality SCORE only —
    # detection, the timeframe choice and the R:R tradeable gate are all unaffected.
    if best.get("pattern_quality") is not None:
        _liq_pen = _liquidity_penalty(ticker)
        if _liq_pen:
            best["pattern_quality"] = max(0, min(100, best["pattern_quality"] + _liq_pen))

    # ── Multi-timeframe roll-up (user 2026-06-13, feedback_hvf_timeframe_grouping):
    # the report shows each instrument ONCE and lists every timeframe its funnel appears
    # on, instead of a separate row per timeframe. Attach the full candidate set — compact
    # and weight-ordered (TRIGGERED > READY > DEVELOPING, then quality) — to the chosen
    # result. Detection is UNCHANGED: `best` is still the single chosen timeframe and only
    # it carries the AMP1-anchored / IG-validated numbers. The other timeframes are raw
    # detections, so we expose their state for the "also on" annotation but never their
    # un-anchored R:R as if it were tradeable; the primary entry/stop/target/R:R shown by
    # the report still come from `best`.
    best["mtf_timeframes"] = sorted(
        ({"hvf_timeframe":   c.get("hvf_timeframe"),
          "hvf_signal":      c.get("hvf_signal"),
          "hvf_type":        c.get("hvf_type"),
          "risk_reward":     c.get("risk_reward"),
          "pattern_quality": c.get("pattern_quality"),
          # Per-timeframe levels (user 2026-06-15: the dossier shows ALL figures for
          # each date range, not just the chosen one). These are the RAW per-timeframe
          # detection levels — only the chosen `best` timeframe carries the AMP1-
          # anchored target / R:R and IG validation (best is one of `candidates` and
          # apply_exhaustion_amp1 mutated it in place, so best's row is already anchored).
          "h3_level":        c.get("h3_level"),
          "stop_level":      c.get("stop_level"),
          "target":          c.get("target")}
         for c in candidates),
        key=lambda c: (signal_rank.get(c["hvf_signal"], 0), c["pattern_quality"] or 0),
        reverse=True,
    )

    # ── Tight-stop flag (backlog #9b, user 2026-06-15) ────────────────────────────────────────────────────────────────
    # A stop closer than TIGHT_STOP_MIN_PCT of price is eaten by spread + tick noise
    # intraday — the funnel is valid on a daily/weekly timeframe but structurally
    # untradeable at IG (proven: SNDK 0.35% stopped out in 3 min; AMD 0.098% churned
    # for pennies, 2026-06-15). Computed HERE at pattern evaluation — not at trade
    # time — so it is known before any trade is attempted and cannot be skipped by an
    # IG-call exception the way the execution-time guard (open_trade 4d, fail-open)
    # can. ADDITIVE: detection output is unchanged; these two fields only annotate the
    # chosen result. The trade path skips a flagged setup silently; the report still
    # shows it, labelled. The percentage is scale-invariant (stop ÷ entry).
    _entry, _stop = best.get("h3_level"), best.get("stop_level")
    if _entry and _stop:
        _sp = abs(_entry - _stop) / _entry * 100.0
        best["stop_pct"]            = round(_sp, 3)
        best["tight_stop_intraday"] = _sp < TIGHT_STOP_MIN_PCT
    else:
        best["stop_pct"]            = None
        best["tight_stop_intraday"] = False
    return best


def hvf_weight(signal: str, quality, risk_reward=0.0) -> tuple:
    """
    CANONICAL weight-order sort key (best first) for HVF setups. Use directly with
    `sorted(..., key=...)` (ascending). Single source of truth for the "all lists in weight
    order" rule.

    Order (user 2026-06-19): **R:R descending is the PRIMARY focus**, then TRIGGERED > READY >
    DEVELOPING, then pattern quality desc. (Was signal -> quality -> R:R until 2026-06-19.)
    NOTE: callers MUST pass risk_reward for the new primary sort to take effect — a 2-arg call
    leaves R:R=0 and sorts only by signal/quality.

    Call with the raw fields so it works for both dict results and DB-row tuples:
        sorted(results, key=lambda r: hvf_weight(r["hvf_signal"], r["hvf_quality"], r["risk_reward"]))
    """
    rank = {"TRIGGERED": 0, "READY": 1, "DEVELOPING": 2}.get(signal, 3)
    return (-(risk_reward or 0), rank, -(quality or 0))


def pct_from_current(level, current) -> str:
    """Signed % a price level sits from the current price, e.g. '+3.2%' / '-8.1%'.
    Returns '' when either value is missing/zero. CANONICAL helper (user 2026-06-19):
    every report that prints entry/stop/target alongside the live price uses this, so
    the "% from current price" wording is identical across X cards, the daily report,
    the dossier, Slack alerts and email."""
    if not isinstance(level, (int, float)) or not isinstance(current, (int, float)) or not current:
        return ""
    return f"{(level / current - 1) * 100:+.1f}%"


def support_resistance(ticker: str, lookback: int = 60):
    """Standard trader support/resistance — the swing low (support) and swing high (resistance)
    over the last `lookback` daily bars. Returns (support, resistance) or (None, None). CANONICAL
    helper so the X card and the analysis agree. Default 60 bars (user 2026-06-20): a 20-bar
    window on a tightly-coiled funnel gives an absurd ~1% S/R band; 60 bars reflects real
    structural levels."""
    try:
        df = _get_daily(ticker, days=max(lookback + 10, 80))
        if df is None or df.empty:
            return None, None
        rc = df.tail(lookback)
        return float(rc["Low"].squeeze().min()), float(rc["High"].squeeze().max())
    except Exception:
        return None, None


def near_support_resistance(price, support, resistance, tol_pct: float = 2.5):
    """Whether `price` sits within `tol_pct`% of support or resistance (user 2026-06-19: price
    near a level needs extra consideration — a setup can bounce/reject right there). Returns
    ('support'|'resistance'|None, distance_pct) for the nearer level inside tolerance, else (None, None)."""
    if not isinstance(price, (int, float)) or not price:
        return None, None
    best, best_d = None, None
    for label, lvl in (("support", support), ("resistance", resistance)):
        if isinstance(lvl, (int, float)) and lvl:
            d = abs(price / lvl - 1) * 100
            if d <= tol_pct and (best_d is None or d < best_d):
                best, best_d = label, d
    return best, best_d


def entry_chase_pct(r: dict):
    """How far price has run PAST the entry in the trade's direction, as a signed % (user
    2026-06-20). Positive = price is beyond the entry (chasing / missed); <=0 = the entry is
    still reachable. None when price or entry is missing. Used to drop 'missed by a mile'
    setups (e.g. a TRIGGERED long whose price already ran +50% past the H3 break)."""
    cur, entry, t = r.get("current_price"), r.get("h3_level"), r.get("hvf_type")
    if not isinstance(cur, (int, float)) or not isinstance(entry, (int, float)) or not entry:
        return None
    if t == "BULLISH":
        return (cur - entry) / entry * 100.0
    if t == "BEARISH":
        return (entry - cur) / entry * 100.0
    return None


def _humanize_days(days: int) -> str:
    """Round a day count to a human phrase: '~9 days' / '~6 weeks' / '~4 months'."""
    if days < 14:
        return f"~{days} days"
    if days < 70:
        return f"~{round(days / 7)} weeks"
    return f"~{round(days / 30)} months"


# A funnel that has been forming this many CALENDAR weeks (H1 -> H3) or longer is a PROLONGED
# consolidation worth flagging in the report/long-report narrative (user 2026-06-22). Display-only.
PROLONGED_FUNNEL_WEEKS = 8


def funnel_span_weeks(r: dict):
    """Calendar weeks the funnel has been forming, H1 -> H3 (user 2026-06-22: recognise + comment on
    a prolonged consolidation). Returns an int (rounded weeks) or None when the pivot dates are
    absent. Display helper only — no effect on detection."""
    from datetime import datetime
    h1d, h3d = r.get("h1_date"), r.get("h3_date")
    try:
        if h1d and h3d:
            days = (datetime.fromisoformat(str(h3d)[:10]) - datetime.fromisoformat(str(h1d)[:10])).days
            if days > 0:
                return round(days / 7)
    except Exception:
        pass
    return None


def target_horizon(r: dict) -> str:
    """Rough EXPECTED time to reach target (user 2026-06-19) — Slack only, never on the X
    card/tweet. Heuristic: a measured move tends to play out over a time similar to the
    funnel's formation span (H1 -> H3); falls back to the scan timeframe when pivot dates are
    absent. Returns '' if nothing usable. CANONICAL so every Slack report phrases it the same."""
    from datetime import datetime
    days = None
    h1d, h3d = r.get("h1_date"), r.get("h3_date")
    try:
        if h1d and h3d:
            d1 = datetime.fromisoformat(str(h1d)[:10])
            d3 = datetime.fromisoformat(str(h3d)[:10])
            days = (d3 - d1).days
    except Exception:
        days = None
    if not days or days <= 0:
        days = {"daily-30": 21, "daily-60": 42, "daily-90": 63,
                "daily-180": 120, "daily-240": 160, "weekly": 180}.get(r.get("hvf_timeframe") or "")
    if not days or days <= 0:
        return ""
    return _humanize_days(days)


def market_short(market_name) -> str:
    """Short market tag for grouped report sub-headers (user 2026-06-16). Single source
    of truth so the daily report, X drafts and quality reports label markets identically."""
    return {"FTSE 100": "FTSE100", "FTSE 250": "FTSE250",
            "S&P 500": "S&P500"}.get(market_name, market_name or "?")


def group_by_market(rows, n=None, market_of=None, market_order=None) -> list:
    """CANONICAL per-market grouping for HVF outputs (user 2026-06-16: "top 10 by market").

    Groups `rows` by market PRESERVING the input order within each market (so callers pass
    rows already sorted by hvf_weight / R:R), caps each market to its top `n` (None = no
    cap), and returns a list of (market_name, [rows]) ordered by `market_order` — markets
    not listed fall to the end, alphabetically.

    `market_of(row)` extracts the market label; it defaults to the dict "index" field, so
    it works directly on HVF result dicts. Pass a lambda for tuple/DB-row inputs, e.g.
    `group_by_market(rows, n=10, market_of=lambda r: r[3], market_order=MARKET_ORDER)`.
    """
    from collections import OrderedDict
    if market_of is None:
        market_of = lambda r: (r.get("index") if isinstance(r, dict) else None) or "?"
    buckets: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        buckets.setdefault(market_of(r) or "?", []).append(r)
    order = list(market_order or [])
    def _key(m):
        return (order.index(m) if m in order else len(order), m)
    return [(m, (rs[:n] if n else rs)) for m, rs in sorted(buckets.items(), key=lambda kv: _key(kv[0]))]


def check_hvf_invariants(r: dict) -> list:
    """
    Geometry invariants every emitted HVF result MUST satisfy by definition.
    Returns a list of violation strings (empty = valid). Shared by the runtime
    guard in get_hvf_signal_mtf and the regression suite (test_hvf_method.py)
    so production and tests can never disagree about what "correct" means
    (user 2026-06-12: changes must NOT negatively impact correct HVF calculation).
    """
    v = []
    if not r or not r.get("hvf_type"):
        return v
    h1, h3 = r.get("h1_level"), r.get("h3_level")
    l1, l3 = r.get("l1_level"), r.get("l3_level")
    entry  = h3 if r["hvf_type"] == "BULLISH" else l3
    stop, target, rr = r.get("stop_level"), r.get("target"), r.get("risk_reward")

    if h1 is not None and l1 is not None and h1 <= l1:
        v.append(f"H1 {h1} <= L1 {l1} (initial range not positive)")
    # Funnel-inversion check. NOTE: h3_level stores the ENTRY in every result,
    # and for BEARISH patterns entry = L3 — so h3_level == l3_level BY DESIGN
    # there (META false-suppression 2026-06-12 18:56 UTC). For bullish compare
    # directly; for bearish reconstruct the true H3 from the stop (stop = H3 ×
    # 1.002) before comparing.
    if r["hvf_type"] == "BULLISH":
        if h3 is not None and l3 is not None and h3 <= l3:
            v.append(f"H3 {h3} <= L3 {l3} (funnel inverted)")
    elif r["hvf_type"] == "BEARISH" and stop is not None and l3 is not None:
        h3_true = stop / 1.002
        if h3_true <= l3 * 0.999:
            v.append(f"true H3 {h3_true:.4f} (from stop) <= L3 {l3} (funnel inverted)")
    if r.get("convergence") is not None and not (0 < r["convergence"] < 0.70):
        v.append(f"convergence {r['convergence']} outside (0, 0.70)")
    if target is not None and target <= 0:
        v.append(f"target {target} is not a positive price")
    if None not in (entry, stop, target):
        if r["hvf_type"] == "BULLISH" and not (stop < entry < target):
            v.append(f"BULLISH order broken: stop {stop} < entry {entry} < target {target} expected")
        if r["hvf_type"] == "BEARISH" and not (target < entry < stop):
            v.append(f"BEARISH order broken: target {target} < entry {entry} < stop {stop} expected")
    if rr is not None and not (0 < rr < 100):
        v.append(f"risk_reward {rr} outside sane range (0, 100)")
    dates = [r.get(k) for k in ("h1_date", "h2_date", "h3_date") if r.get(k)]
    if dates != sorted(dates):
        v.append(f"high pivot dates not chronological: {dates}")
    return v


def compute_exhaustion_amp1(ticker: str, result: dict, long_days: int = 500,
                            exhaustion_lookback: int = 260):
    """
    OFFICIAL-METHOD AMP1 (backlog 9a — PROTOTYPE, not wired into get_hvf_signal).
    Francis Hunt anchors AMP1 at the prior trend's ACTUAL exhaustion extremes, not the
    in-window swing pivots: AMP1 = (exhaustion high − first natural-support pullback low)
    for a bullish funnel, mirrored for bearish. Entry/stop stay at the funnel's 3rd pivots;
    only the target AMPLITUDE is re-anchored.

    Returns a dict {amp1_official, target_official, exhaustion, first_pullback, amp1_window,
    target_window} for shadow-diffing against the live values, or None when it cannot be
    computed. Pure read — never mutates `result`.

    Caveat: the exhaustion extreme is the dominant High/Low in the ~`exhaustion_lookback`
    trading days BEFORE the funnel's H2 (captures the immediately-preceding trend, not an
    ancient cycle top). Shadow-diff before trusting.
    """
    import pandas as pd
    if not result.get("hvf_type"):
        return None
    h3, l3 = result.get("h3_level"), result.get("l3_level")
    h2_date = result.get("h2_date")
    if h3 is None or l3 is None or not h2_date:
        return None
    try:
        hist = _get_daily(ticker, days=long_days)   # sanitised long history
        if hist is None or hist.empty:
            return None
        cutoff = pd.Timestamp(h2_date)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        idx = hist.index
        if getattr(idx, "tz", None) is not None:
            hist = hist.copy()
            hist.index = idx.tz_localize(None)
        prior = hist[hist.index <= cutoff].tail(exhaustion_lookback)
        if len(prior) < 20:
            return None

        # Hunt's L1 = the funnel's OWN first pullback low (the "final natural support" the
        # detector already found in-window) — NOT the deepest low over the year (that is
        # RW's 52wk-low over-extension error). So re-anchor ONLY the clipped EXHAUSTION
        # extreme to full history; keep the funnel's other first-pivot. Never pull inward.
        h1, l1 = result.get("h1_level"), result.get("l1_level")
        if h1 is None or l1 is None:
            return None
        bullish = result["hvf_type"] == "BULLISH"
        mid = (h3 + l3) / 2.0
        if bullish:
            exh  = max(float(prior["High"].max()), h1)   # true exhaustion high ≥ detected H1
            pull = l1                                    # funnel's first pullback low (kept)
            amp1 = exh - pull
            target = round(mid + amp1, 6)
        else:
            exh  = min(float(prior["Low"].min()), l1)    # true exhaustion low ≤ detected L1
            pull = h1                                    # funnel's first pullback high (kept)
            amp1 = pull - exh
            target = round(mid - amp1, 6)
        if amp1 <= 0 or target <= 0:
            return None

        amp1_window = h1 - l1
        return {
            "amp1_official":  round(amp1, 6),
            "target_official": target,
            "exhaustion":     round(exh, 6),
            "first_pullback": round(pull, 6),
            "amp1_window":    round(amp1_window, 6) if amp1_window is not None else None,
            "target_window":  result.get("target"),
        }
    except Exception as e:
        log.warning(f"compute_exhaustion_amp1 {ticker} failed: {e}")
        return None


def apply_exhaustion_amp1(ticker: str, result: dict) -> dict:
    """
    MERGE of backlog 9a (user 2026-06-12): re-anchor the target AMPLITUDE to the prior
    trend's TRUE exhaustion extreme (official HVF method), recompute target + R:R, and
    re-apply the R:R gate. Mutates and returns `result`. Entry (H3/L3) and stop are
    UNCHANGED — only the target amplitude and the state may move.

    Called once per instrument on the chosen MTF result (not per-timeframe — cost bound).
    No-op when the funnel already anchors at the exhaustion (window reached it) or when the
    long-history fetch fails (the in-window target stands; result["amp1_anchored"]=False).
    """
    result.setdefault("amp1_anchored", False)
    if not result.get("hvf_type"):
        return result
    off = compute_exhaustion_amp1(ticker, result)
    if not off:
        return result

    bullish = result["hvf_type"] == "BULLISH"
    entry = result.get("h3_level") if bullish else result.get("l3_level")
    stop  = result.get("stop_level")
    if entry is None or stop is None:
        return result

    moved = abs((off["amp1_official"] or 0) - (off["amp1_window"] or 0)) > 1e-6
    result["pattern_range"]   = off["amp1_official"]
    result["target"]          = off["target_official"]
    result["exhaustion_level"] = off["exhaustion"]
    result["amp1_anchored"]   = moved

    risk = abs(entry - stop)
    if risk > 0:
        result["risk_reward"] = round(abs(result["target"] - entry) / risk, 2)

    # Re-apply the R:R gate with the re-anchored R:R. A bigger AMP1 can PROMOTE a
    # DEVELOPING setup to tradeable, or (rarely, bearish) demote — keep the gate honest.
    rr = result.get("risk_reward") or 0
    px = result.get("current_price")
    if rr < HVF_MIN_RR:
        result["hvf_signal"] = "DEVELOPING"
    else:
        if px is not None:
            triggered = (px > entry) if bullish else (px < entry)
            result["hvf_signal"] = "TRIGGERED" if triggered else "READY"
        elif result.get("hvf_signal") == "DEVELOPING":
            result["hvf_signal"] = "READY"   # promoted but price unknown → READY
    if moved:
        log.info(f"HVF {ticker}: AMP1 re-anchored to exhaustion {off['exhaustion']} → "
                 f"target {result['target']} R:R {result.get('risk_reward')} "
                 f"({result['hvf_signal']})")
    return result


def validate_hvf_with_ig(ticker: str, result: dict, min_allowance: int = 1500) -> dict:
    """
    Re-validate a Yahoo-detected HVF setup against IG broker candles — the
    arbiter data source (user 2026-06-12: "if the IG data is more accurate,
    use it"). Yahoo's LSE feed contains phantom prints, so every UK tradeable
    setup is corroborated pivot-by-pivot before it is posted or traded.

    For each pivot (H1..H3 by High, L1..L3 by Low) the Yahoo level must be
    within 1.5% of the IG candle's High/Low on the pivot date (±1 trading
    day). If ALL pivots corroborate: entry/stop/target/R:R are RECOMPUTED
    from the IG levels (Hunt's formula) and result["ig_validated"] = True.
    If any pivot fails: the setup is demoted to DEVELOPING and
    result["ig_validated"] = False with the mismatch named.

    Budget: only call for .L tickers (US feeds are clean). One call costs
    `count` points of the 10,000/week IG allowance; validation is skipped
    (ig_validated = None) when the remaining allowance is below
    min_allowance, when the ticker has no IG epic, or on any fetch error —
    the Yahoo result then stands unchanged.
    """
    result.setdefault("ig_validated", None)
    if not result.get("hvf_type"):
        return result

    # ── Daily cache: the 2-hourly watches re-validate the same setups all day;
    # one IG fetch per ticker per day is enough (pivots are historical, levels
    # stable intraday). Saves most of the validation allowance burn.
    try:
        from db_pool import get_db as _gdb
        _db = _gdb()
        try:
            rows = _db.run(
                """select ig_validated, mismatch, entry_level, stop_level, target, risk_reward
                     from ig_validation_log
                    where trade_date = current_date and ticker = :t""", t=ticker)
        finally:
            _db.close()
        if rows:
            v, mism, e_, s_, tg_, rr_ = rows[0]
            result["ig_validated"] = v
            if v is False:
                result["hvf_signal"]  = "DEVELOPING"
                result["ig_mismatch"] = mism
            elif v is True and e_ is not None:
                key = "h3_level" if result["hvf_type"] == "BULLISH" else "l3_level"
                result[key]           = float(e_)
                result["stop_level"]  = float(s_) if s_ is not None else result.get("stop_level")
                result["target"]      = float(tg_) if tg_ is not None else result.get("target")
                result["risk_reward"] = float(rr_) if rr_ is not None else result.get("risk_reward")
            log.info(f"IG validation {ticker}: cache hit (validated={v})")
            return result
    except Exception as e:
        log.debug(f"IG validation cache read failed ({e}) — validating fresh")

    try:
        from ig_shim import get_epic, get_prices_df
        epic = get_epic(ticker)
        if not epic:
            log.info(f"IG validation {ticker}: no epic — skipped")
            return result

        # Enough candles to reach back to H1 (+buffer), capped at 240
        pivot_dates = [result.get(k) for k in
                       ("h1_date", "h2_date", "h3_date", "l1_date", "l2_date", "l3_date")
                       if result.get(k)]
        if not pivot_dates:
            return result
        oldest = min(pd.Timestamp(d) for d in pivot_dates)
        bars_needed = int((pd.Timestamp.now() - oldest).days * 0.72) + 15   # ~trading days
        count = max(60, min(240, bars_needed))

        ig_df, remaining = get_prices_df(epic, resolution="DAY", count=count)
        if remaining is not None and remaining < min_allowance:
            log.warning(f"IG validation {ticker}: allowance {remaining} < {min_allowance} "
                        f"reserve — skipped (Yahoo levels stand)")
            return result
        if ig_df.empty:
            return result

        # Weekly patterns label pivots with the WEEK-START date, so the pivot
        # extreme can sit anywhere in that week — compare against the whole
        # week of daily IG candles. Daily patterns compare the pivot's OWN
        # day (±1 day only as a holiday/timezone fallback) — a window minimum
        # would wrongly pull in deeper lows from neighbouring days.
        is_weekly = (result.get("hvf_timeframe") == "weekly")

        checks = [("h1", "High"), ("h2", "High"), ("h3", "High"),
                  ("l1", "Low"),  ("l2", "Low"),  ("l3", "Low")]
        ig_levels = {}
        for piv, col in checks:
            y_level = result.get(f"{piv}_level")
            y_date  = result.get(f"{piv}_date")
            if y_level is None or y_date is None:
                continue
            if piv == "l3" and result.get("l3_synthetic"):
                continue   # derived level (midpoint/current price) — no candle to compare
            d = pd.Timestamp(y_date)
            if is_weekly:
                window = ig_df.loc[d: d + pd.Timedelta(days=6)]
            else:
                window = ig_df.loc[d: d]
                if window.empty:
                    window = ig_df.loc[d - pd.Timedelta(days=1): d + pd.Timedelta(days=1)]
            if window.empty:
                continue   # pivot predates fetched window — not a failure
            ig_level = float(window[col].max() if col == "High" else window[col].min())
            ig_levels[piv] = ig_level
            if abs(y_level - ig_level) / ig_level > 0.015:
                result["ig_validated"] = False
                result["ig_mismatch"]  = (f"{piv.upper()} {y_level:g} (Yahoo) vs "
                                          f"{ig_level:g} (IG) on {y_date}")
                result["hvf_signal"]   = "DEVELOPING"
                log.warning(f"IG validation {ticker}: FAILED — {result['ig_mismatch']} "
                            f"— demoted to DEVELOPING")
                _cache_ig_validation(ticker, result)
                return result

        # All pivots corroborated — recompute levels from IG data (Yahoo value
        # stands in for any pivot that had no comparable IG candle, e.g. a
        # synthetic L3 or a pivot older than the fetched window).
        if all(k in ig_levels for k in ("h1", "h3")):
            h1 = ig_levels["h1"]
            h3 = ig_levels["h3"]
            l1 = ig_levels.get("l1", result.get("l1_level"))
            l3 = ig_levels.get("l3", result.get("l3_level"))
            # AMP1 for the target: when the result was exhaustion-anchored (9a),
            # KEEP that amplitude (pattern_range) — re-deriving h1−l1 from in-window
            # IG pivots would undo the re-anchor. Yahoo and IG agree on the pivots
            # (validated above), so the Yahoo-derived AMP1 is consistent with the
            # IG midpoint. Otherwise use the in-window IG range as before.
            initial_range = result["pattern_range"] if result.get("amp1_anchored") \
                else (h1 - l1)
            midpoint      = (h3 + l3) / 2.0
            if result["hvf_type"] == "BULLISH":
                result["h3_level"]   = round(h3, 6)
                result["stop_level"] = round(l3 * 0.998, 6)
                result["target"]     = round(midpoint + initial_range, 6)
                risk   = h3 - result["stop_level"]
                reward = result["target"] - h3
            else:
                result["l3_level"]   = round(l3, 6)
                result["stop_level"] = round(h3 * 1.002, 6)
                result["target"]     = round(midpoint - initial_range, 6)
                risk   = result["stop_level"] - l3
                reward = l3 - result["target"]
            if risk > 0:
                result["risk_reward"] = round(reward / risk, 2)
        result["ig_validated"] = True
        log.info(f"IG validation {ticker}: PASSED — levels recomputed from broker data")
        _cache_ig_validation(ticker, result)
    except Exception as e:
        log.warning(f"IG validation {ticker} errored (Yahoo levels stand): {e}")
    return result


def _cache_ig_validation(ticker: str, result: dict):
    """Persist today's IG validation outcome so later runs reuse it (one IG fetch per ticker per day)."""
    try:
        from db_pool import get_db as _gdb
        entry = result.get("h3_level") if result.get("hvf_type") == "BULLISH" else result.get("l3_level")
        _db = _gdb()
        try:
            _db.run(
                """insert into ig_validation_log
                       (trade_date, ticker, ig_validated, mismatch,
                        entry_level, stop_level, target, risk_reward)
                   values (current_date, :t, :v, :m, :e, :s, :tg, :rr)
                   on conflict (trade_date, ticker) do update
                   set ig_validated = excluded.ig_validated,
                       mismatch     = excluded.mismatch,
                       entry_level  = excluded.entry_level,
                       stop_level   = excluded.stop_level,
                       target       = excluded.target,
                       risk_reward  = excluded.risk_reward""",
                t=ticker, v=result.get("ig_validated"), m=result.get("ig_mismatch"),
                e=entry, s=result.get("stop_level"),
                tg=result.get("target"), rr=result.get("risk_reward"))
        finally:
            _db.close()
    except Exception as e:
        log.debug(f"IG validation cache write failed for {ticker}: {e}")


def _run_hvf_on_hist(ticker: str, hist) -> dict:
    """
    Run the HVF pattern search on a pre-fetched history DataFrame.
    Used by get_hvf_signal_mtf for weekly-bar analysis without a second API call.
    Mirrors the core logic of get_hvf_signal but accepts hist directly.
    """
    result = {
        "hvf_type": None, "hvf_signal": None,
        "h3_level": None, "l3_level": None, "stop_level": None,
        "target": None, "risk_reward": None,
        "h1_level": None, "l1_level": None, "pattern_range": None,
        "bars_since_h3": None, "pattern_quality": 0, "convergence": None,
        "volume_confirmed": False, "current_price": None,
    }
    try:
        if len(hist) < 20:
            return result

        current_price = float(hist["Close"].iloc[-1])
        result["current_price"] = round(current_price, 6)   # for tweet/card "Now:" display
        trend         = get_trend_structure(ticker)
        trend_signal  = trend.get("signal", "SIDEWAYS")

        sw_h5, sw_l5 = _find_swing_highs_lows(hist, n=5)
        sw_h3, sw_l3 = _find_swing_highs_lows(hist, n=2)   # n=2 for weekly (fewer bars)

        # Recent-trend override — peak-and-decline or strict declining
        rsh = sw_h3[-5:] if len(sw_h3) >= 5 else sw_h3
        strict_dec = (len(rsh) >= 3 and rsh[-1][1] < rsh[-2][1] < rsh[-3][1]
                      and (rsh[-3][1] - rsh[-1][1]) / rsh[-3][1] >= 0.05)
        peak_dec = False
        if sw_h3:
            post_bars = [(i, p) for i, p in sw_h3 if i >= len(hist) - 40]  # 40 weeks ~1yr
            if post_bars:
                pk_bar, pk_price = max(post_bars, key=lambda x: x[1])
                pct_off = (pk_price - current_price) / pk_price
                post_pk = [(i, p) for i, p in sw_h3 if i > pk_bar]
                peak_dec = (pct_off >= 0.07 and len(post_pk) >= 2
                            and all(p < pk_price for _, p in post_pk))
        rising = (len(rsh) >= 3 and rsh[-1][1] > rsh[-2][1] > rsh[-3][1])
        # Mirror the guard in get_hvf_signal: STRONG_UPTREND suppresses
        # bearish override — strict_dec / peak_dec should not flip a confirmed
        # strong uptrend into a DOWNTREND on historical replay.
        allow_bearish_override = trend_signal not in ("STRONG_UPTREND",)
        effective_trend = ("DOWNTREND" if (strict_dec or peak_dec) and allow_bearish_override else
                           "UPTREND"   if rising else trend_signal)

        if effective_trend not in ("STRONG_UPTREND", "UPTREND",
                                   "STRONG_DOWNTREND", "DOWNTREND"):
            return result

        bullish = effective_trend in ("STRONG_UPTREND", "UPTREND")
        best_pattern = None
        best_quality  = 0

        for swing_highs, swing_lows in [(sw_h5, sw_l5), (sw_h3, sw_l3)]:
            if len(swing_highs) < 3 or len(swing_lows) < 3:
                continue
            rh = swing_highs[-10:]
            rl = swing_lows[-10:]

            for hi in range(len(rh)):
                for hj in range(hi + 1, len(rh)):
                    for hk in range(hj + 1, len(rh)):
                        h1, h2, h3 = rh[hi], rh[hj], rh[hk]
                        # Flat-top tolerance — mirrors the daily path (see
                        # get_hvf_signal Condition 1, 2026-06-12).
                        if not (h1[1] > h2[1] * 1.005 and h3[1] <= h2[1] * 1.005):
                            continue
                        bars_since_h3 = len(hist) - 1 - h3[0]
                        if bars_since_h3 > 40 or h3[0] - h1[0] < 4:
                            continue
                        lows_12 = [l for l in rl if h1[0] < l[0] < h2[0]]
                        lows_23 = [l for l in rl if h2[0] < l[0] < h3[0]]
                        if not lows_12 or not lows_23:
                            continue
                        l1 = min(lows_12, key=lambda x: x[1])
                        l2 = min(lows_23, key=lambda x: x[1])
                        if l2[1] < l1[1] * 0.995:
                            continue
                        l3c = [l for l in rl if l[0] > l2[0] and l[1] >= l2[1] * 0.995 and l[1] < h3[1]]
                        l3_synthetic = False
                        if l3c:
                            l3 = max(l3c, key=lambda x: x[0])
                        elif l2[1] < current_price < h3[1]:
                            l3 = (len(hist) - 1, current_price)
                            l3_synthetic = True
                        else:
                            continue
                        ir = h1[1] - l1[1]; cr = h3[1] - l3[1]
                        if ir <= 0 or cr <= 0: continue
                        conv = cr / ir
                        if conv >= 0.70: continue
                        quality = (int((1.0 - conv) * 50) + max(0, 20 - bars_since_h3) +
                                   (20 if effective_trend.startswith("STRONG") else 10))
                        quality = min(100, quality)
                        if quality > best_quality:
                            best_quality = quality
                            best_pattern = {"h1": h1, "h2": h2, "h3": h3,
                                            "l1": l1, "l2": l2, "l3": l3,
                                            "l3_synthetic": l3_synthetic,
                                            "convergence": conv, "bars_since_h3": bars_since_h3,
                                            "initial_range": ir}

        if not best_pattern:
            return result

        h1 = best_pattern["h1"]; h3 = best_pattern["h3"]
        l1 = best_pattern["l1"]; l3 = best_pattern["l3"]
        ir = best_pattern["initial_range"]
        mid = (h3[1] + l3[1]) / 2.0

        if bullish:
            hvf_type = "BULLISH"; entry = round(h3[1], 4)
            stop = round(l3[1] * 0.998, 4); target = round(mid + ir, 4)
            triggered = current_price > h3[1]
        else:
            hvf_type = "BEARISH"; entry = round(l3[1], 4)
            stop = round(h3[1] * 1.002, 4); target = round(mid - ir, 4)
            triggered = current_price < l3[1]

        # Absurd-target rejection — mirrors the daily path (ETHUSD 2026-06-12:
        # bearish target −606; projection below 10% of entry cannot complete).
        if target <= 0 or (hvf_type == "BEARISH" and target <= entry * 0.10):
            log.info(f"HVF weekly {ticker}: target {target} not physically tradeable — rejected")
            return result

        risk = abs(entry - stop)          # R:R from entry level, not current price
        rr   = round(abs(target - entry) / risk, 2) if risk > 0 else 0.0

        # Prior-trend magnitude gate (Rule 1) — DISABLED 2026-06-20 pending calibration (mirrors
        # the daily path; see get_hvf_signal + BACKLOG). Re-enable both together.
        # _pm = _prior_trend_pct(hist, h1[0] if bullish else l1[0], h1[1] if bullish else l1[1],
        #                        bullish, _PRIOR_LOOKBACK_WEEKLY)
        # if _pm is not None and _pm < MIN_PRIOR_TREND_PCT:
        #     return result

        # R:R gate must apply BEFORE TRIGGERED — a pattern that has broken out
        # but has poor R:R (e.g. entry already far past H3) is DEVELOPING, not
        # TRIGGERED. Without this gate the weekly scan was promoting low-R:R
        # patterns to TRIGGERED, which then won the mtf ranking over high-R:R
        # daily READY patterns (TRIGGERED rank=3 beats READY rank=2 regardless).
        # This matched the production bug: NVDA weekly TRIGGERED R:R=0.09 beat
        # NVDA daily READY R:R=5+ in the multi-timeframe wrapper (2026-06-04).
        if rr < HVF_MIN_RR:          # threshold from config — single source of truth
            hvf_sig = "DEVELOPING"
        elif triggered:
            hvf_sig = "TRIGGERED"
        else:
            hvf_sig = "READY"

        def _pivot_date(piv):
            try:
                return hist.index[piv[0]].strftime("%Y-%m-%d")
            except Exception:
                return None
        result.update({
            "hvf_type": hvf_type, "hvf_signal": hvf_sig,
            "h3_level": entry, "l3_level": round(l3[1], 4),
            "stop_level": stop, "target": target, "risk_reward": rr,
            "h1_level": round(h1[1], 4), "l1_level": round(l1[1], 4),
            "pattern_range": round(ir, 4),
            "bars_since_h3": best_pattern["bars_since_h3"],
            "pattern_quality": best_quality,
            "convergence": round(best_pattern["convergence"], 3),
            "volume_confirmed": False,
            # Pivot dates for the trade-email funnel overlay (user 2026-06-10).
            "h1_date": _pivot_date(best_pattern["h1"]), "h2_date": _pivot_date(best_pattern["h2"]),
            "h3_date": _pivot_date(best_pattern["h3"]), "l1_date": _pivot_date(best_pattern["l1"]),
            "l2_date": _pivot_date(best_pattern["l2"]), "l3_date": _pivot_date(best_pattern["l3"]),
            "l3_synthetic": best_pattern.get("l3_synthetic", False),
        })
    except Exception as e:
        log.warning(f"HVF weekly scan failed for {ticker}: {e}")
    return result


# ======================================================================================================================
# Master function — full price action analysis for one instrument
# ======================================================================================================================

def analyse_price_action(ticker: str) -> dict:
    """
    Run the full price action analysis for one instrument.

    Returns a dict with all signal components, composite score,
    and a clear verdict: CONFIRM_LONG / CONFIRM_SHORT / WAIT.

    The verdict is the gating condition for trade execution:
    - Even if COT + macro + fundamentals are all bullish,
      if verdict = WAIT, no trade is placed.
    - This prevents catching falling knives and entering too early.
    """
    log.info(f"Running price action analysis: {ticker}")

    range_bo    = get_range_breakout(ticker)
    trend       = get_trend_structure(ticker)
    atr_comp    = get_atr_compression(ticker)
    ma_align    = get_ma_alignment(ticker)
    failed      = get_failed_break(ticker)
    candlestick = get_candlestick_pattern(ticker)
    # HVF: multi-timeframe scan (daily-240/180/90/60/30, weekly) — best result wins
    hvf         = get_hvf_signal_mtf(ticker, trend_hint=trend)

    score, verdict = compute_price_action_score(range_bo, trend, atr_comp, ma_align, failed, candlestick)

    # ── Fix 1: per-instrument PA threshold ────────────────────────────────────────────────────────────────────────────
    # compute_price_action_score uses the fixed module-level ±40 thresholds.
    # Override the verdict here using the per-class threshold from config.
    # Crypto uses 25, FX/commodities 30–35, equities/indices keep the default 40.
    thresh = PA_CONFIRM_THRESHOLDS.get(ticker, PA_CONFIRM_THRESHOLD_DEFAULT)
    # Always re-evaluate verdict against the config threshold.
    # Removing the previous `if thresh != PA_CONFIRM_THRESHOLD_DEFAULT` guard
    # which caused two bugs:
    #   (a) compute_price_action_score() uses hardcoded ±40 — changing
    #       PA_CONFIRM_THRESHOLD_DEFAULT had zero effect on standard instruments.
    #   (b) Any instrument added to PA_CONFIRM_THRESHOLDS with value 40 would
    #       still silently use the hardcoded threshold.
    # Now: the config value is always authoritative regardless of what it equals.
    if score >= thresh:
        verdict = "CONFIRM_LONG"
    elif score <= -thresh:
        verdict = "CONFIRM_SHORT"
    else:
        verdict = "WAIT"

    # ── Fix 2: HVF TRIGGERED bypass ───────────────────────────────────────────────────────────────────────────────────
    # If the HVF pattern has already triggered (price through H3/L3), halve the
    # effective threshold — price has voted via the pattern, so less PA score
    # confirmation is required to proceed.
    # Floor at 15 to prevent near-zero thresholds on very low-threshold instruments.
    #
    # IMPORTANT: also resolves a direction conflict.  If Fix 1 set a CONFIRM in
    # the WRONG direction (e.g. CONFIRM_LONG with pa_score=+22 for a low-thresh
    # crypto) while the HVF has triggered BEARISH, we must override the verdict —
    # entering a BUY against a triggered bearish HVF is exactly the falling-knife
    # scenario this module was designed to prevent.
    # Previous guard `verdict == "WAIT"` was too narrow: it skipped the bypass
    # whenever Fix 1 had already set a (possibly conflicting) verdict.
    hvf_signal = hvf.get("hvf_signal")
    hvf_type   = hvf.get("hvf_type")
    if hvf_signal == "TRIGGERED":
        bypass = max(thresh * 0.5, 15)
        if hvf_type == "BULLISH" and score >= bypass:
            if verdict != "CONFIRM_LONG":
                log.info(
                    f"HVF TRIGGERED bypass: {ticker} → CONFIRM_LONG "
                    f"(pa_score={score:+.1f} bypass={bypass:.0f} was={verdict})"
                )
            verdict = "CONFIRM_LONG"
        elif hvf_type == "BEARISH" and score <= -bypass:
            if verdict != "CONFIRM_SHORT":
                log.info(
                    f"HVF TRIGGERED bypass: {ticker} → CONFIRM_SHORT "
                    f"(pa_score={score:+.1f} bypass={-bypass:.0f} was={verdict})"
                )
            verdict = "CONFIRM_SHORT"
        else:
            # HVF triggered but PA score doesn't support its direction even at
            # the reduced bypass threshold.  Force WAIT to avoid a trade that
            # contradicts both the HVF pattern and the PA score.
            if verdict not in ("WAIT",):
                log.info(
                    f"HVF TRIGGERED conflict: {ticker} hvf={hvf_type} but "
                    f"pa_score={score:+.1f} does not reach bypass={bypass:.0f} — "
                    f"overriding {verdict} → WAIT"
                )
            verdict = "WAIT"

    result = {
        "ticker":           ticker,
        "verdict":          verdict,        # CONFIRM_LONG / CONFIRM_SHORT / WAIT
        "pa_score":         score,

        # Individual signals
        "range_breakout":   range_bo.get("signal"),
        "range_high":       range_bo.get("range_high"),
        "range_low":        range_bo.get("range_low"),

        "trend_structure":  trend.get("signal"),
        "hh_hl_count":      trend.get("hh_hl_count"),
        "lh_ll_count":      trend.get("lh_ll_count"),

        "atr_compressed":   atr_comp.get("compressed"),
        "atr_expanding":    atr_comp.get("expanding"),
        "atr_pct_rank":     atr_comp.get("atr_pct_rank"),

        "ma_signal":        ma_align.get("signal"),
        "sma20":            ma_align.get("sma20"),
        "sma50":            ma_align.get("sma50"),
        "sma200":           ma_align.get("sma200"),
        "price_vs_200":     ma_align.get("price_vs_200"),
        "golden_cross":     ma_align.get("golden_cross"),
        "death_cross":      ma_align.get("death_cross"),

        "failed_break":      failed.get("signal"),
        "failed_break_desc": failed.get("description"),

        "candlestick":       candlestick.get("pattern"),
        "candlestick_dir":   candlestick.get("pattern_direction"),
        "candlestick_desc":  candlestick.get("description"),

        # Hunt Volatility Funnel
        "hvf_type":          hvf.get("hvf_type"),          # BULLISH / BEARISH / None
        "hvf_signal":        hvf.get("hvf_signal"),        # READY / TRIGGERED / None
        "hvf_h3_level":      hvf.get("h3_level"),          # pending entry level
        "hvf_l3_level":      hvf.get("l3_level"),          # third low (stop reference)
        "hvf_h1_level":      hvf.get("h1_level"),          # first lower-high (funnel top) — for the email chart
        "hvf_h2_level":      hvf.get("h2_level"),          # second lower-high
        "hvf_l1_level":      hvf.get("l1_level"),          # first higher-low (funnel bottom) — for the email chart
        "hvf_l2_level":      hvf.get("l2_level"),          # second higher-low
        # Pivot DATES for the trade-open email's funnel-on-price overlay (user 2026-06-10).
        "hvf_h1_date":       hvf.get("h1_date"),
        "hvf_h2_date":       hvf.get("h2_date"),
        "hvf_h3_date":       hvf.get("h3_date"),
        "hvf_l1_date":       hvf.get("l1_date"),
        "hvf_l2_date":       hvf.get("l2_date"),
        "hvf_l3_date":       hvf.get("l3_date"),
        "hvf_stop_level":    hvf.get("stop_level"),        # exact stop price
        "hvf_target":        hvf.get("target"),            # H1-L1 range target
        "hvf_risk_reward":   hvf.get("risk_reward"),       # pre-calculated R:R
        "hvf_quality":       hvf.get("pattern_quality"),   # 0-100
        "hvf_convergence":   hvf.get("convergence"),       # funnel tightness
        "hvf_bars_since_h3": hvf.get("bars_since_h3"),    # freshness
        "hvf_timeframe":     hvf.get("hvf_timeframe"),    # daily-240 / daily-90 / weekly
        "hvf_volume_confirmed": hvf.get("volume_confirmed", False),
        # Tight-stop flag (backlog #9b) — carried through so the trade path can skip a
        # structurally-untradeable funnel silently (stop < TIGHT_STOP_MIN_PCT of price).
        "hvf_tight_stop_intraday": hvf.get("tight_stop_intraday", False),
        "hvf_stop_pct":            hvf.get("stop_pct"),
        # Live price (user 2026-06-19) so consumers can show "% from current price"
        # (e.g. social_monitor's mention line) without a second fetch.
        "current_price":     hvf.get("current_price"),
    }

    log.info(
        f"Price action {ticker}: verdict={verdict} score={score:+.1f} | "
        f"breakout={range_bo.get('signal')} trend={trend.get('signal')} "
        f"MA={ma_align.get('signal')} failed={failed.get('signal')} "
        f"candle={candlestick.get('pattern')} "
        f"HVF={hvf.get('hvf_type')}({hvf.get('hvf_signal')})"
    )

    return result


# ======================================================================================================================
# Entry point — run analysis for a list of instruments
# Usage: python price_action.py
# ======================================================================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    instruments = ["XAUUSD", "OIL", "SPX500", "NVDA", "GBPUSD"]

    print("\nPrice Action Confirmation Analysis")
    print(f"{'Ticker':<10} {'Verdict':<15} {'Score':>7}  {'Breakout':<20} {'Trend':<18} {'MA':<14} {'Failed Break'}")
    print("-" * 100)

    for ticker in instruments:
        r = analyse_price_action(ticker)
        print(
            f"{r['ticker']:<10} {r['verdict']:<15} {r['pa_score']:>+7.1f}  "
            f"{r['range_breakout'] or 'NONE':<20} "
            f"{r['trend_structure'] or 'UNKNOWN':<18} "
            f"{r['ma_signal'] or 'UNKNOWN':<14} "
            f"{r['failed_break'] or 'NONE'}"
        )
