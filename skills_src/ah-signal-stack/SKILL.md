---
name: ah-signal-stack
description: >
  The A&A Trading signal stack and trade-decision pipeline — how the system decides whether
  to trade, which direction, and at what size (signals.py / run_session.py / config.py). Use
  this skill whenever the user asks: why did/didn't X trade, what fired on a setup, explain a
  signal or the macro gate, change a threshold or add a signal, why a confirmation counted (or
  didn't), how position size was computed, or anything about primaries/confirmations/R:R/
  stress mode. Also trigger for "should this have traded?", "what's blocking entries?",
  "why is conf_count low?". Every rule here is verified against the live code — quote the code,
  never memory. HVF pattern detection itself is covered by ah-hvf-analysis; this skill is the
  decision layer that consumes HVF plus every other signal.
---

# AH Signal Stack — the trade-decision pipeline

`signals.py::scan_instrument` produces one signal dict per instrument; `run_session.py`
turns a firing signal into a sized order. This skill is the source-of-truth map of that
decision. When this doc and the code disagree, the code wins.

## The decision — a trade fires ONLY when ALL of these are true

```
trade_signal = macro_gate_pass            # 1. regime is tradeable
           AND primary_gate               # 2. ≥2 primaries OR a bypass
           AND conf_count ≥ 1             # 3. ≥1 direction-aligned confirmation
           AND direction is not None      # 4. primaries agree on a side
           AND pa_confirmed               # 5. price action confirms that side
```
(signals.py ~line 1881). Miss any one → no trade. Then execution applies its own gates
(R:R, spread, caps) before an order is placed.

## 1. Macro gate (signals.py::get_macro_gate) — must PASS

| Check | Fails when | Constant |
|---|---|---|
| VIX | VIX > 35 | VIX_GATE_THRESHOLD = 35.0 |
| Yield curve (2y/10y) | spread < −1.0% | YIELD_SPREAD_GATE_THRESHOLD = −1.0 |
| SPX intraday stress | SPX down > 2.5% on the day → no new entries | SPX_HIGH_STRESS_PCT = −2.5 |

SPX down 1–2.5% does NOT fail the gate but sets STRESS mode → position sizes halved
(SPX_STRESS_PCT = −1.0).

## 2. Primary signals — need primary_count ≥ 2, OR a bypass

`MIN_PRIMARY_SIGNALS = 2`. Each fires with a direction; the trade direction is the
MAJORITY vote of all primary directions (ties → no direction → no trade).

Primaries (each +1): **Options flow** (bias BULLISH/BEARISH from call/put + IV) ·
**Bollinger breakout** (or, if no BB, **high volume + price vs VWAP** as a substitute) ·
**HVF** (READY/TRIGGERED) · **ADX directional** (ADX ≥ 20 and |+DI − −DI| ≥ 5) ·
**ORB** (30-min opening-range break) · **52-week extreme** · **Elite senator / POTUS buy**.

**Bypasses** (pass stage 2 with a single signal): HVF fired alone; or an elite-senator
(≥70% win-rate) / POTUS primary. These are high-conviction enough to not need a second
primary.

## 3. Confirmation signals — need conf_count ≥ 1, EVERY one direction-aligned

`MIN_CONFIRMATION_SIGNALS = 1`. **THE house rule (user 2026-06-12): "Bearish is not
confirmation for a buy."** Every confirmation must agree with the trade side:

- **Director buys / Activist 13D / Senate buy / Superinvestor / Social** — long-side
  evidence: count on **BUY only** (insider buying argues for longs, never shorts).
- **COT positioning** — `bias` must equal the side (BULLISH↔BUY, BEARISH↔SELL).
  Neutralised when the report is > 14 days old (staleness, user 2026-06-12).
- **ADX strong trend** — counts only when the dominant DI matches the side (+DI>−DI for
  BUY).
- **OBV** — bullish divergence/confirming on BUY, bearish on SELL.
- **Commodity macro** / **Sector ETF** — score/direction must match the side.

`conf_count` and the named `confirmations_fired` list use IDENTICAL rules, so emails,
tweets and Slack never name a misaligned confirmation. This change can only LOWER counts
— stricter is the correct direction for a confirmation-logic error.

## 4 & 5. Direction + Price-action confirm

Direction = majority of primary directions. **PA confirm** is the falling-knife guard:
`price_action.verdict` must be CONFIRM_LONG (for BUY) or CONFIRM_SHORT (for SELL). Even a
full bullish stack will NOT fire a long if the chart itself says WAIT. PA threshold is
per-instrument (equities ±40, crypto/FX lower — config PA_CONFIRM_THRESHOLDS); HVF
TRIGGERED halves it (the breakout is the price vote).

## Execution gates (run_session.py / ig_shim.py) — after the signal fires

- **R:R ≥ 3.0** (MIN_RISK_REWARD / DEFAULT_TARGET_RR; HVF_MIN_RR aliased to it) — computed
  from the ENTRY level, never current price.
- **Spread** < 0.5% of mid (MAX_SPREAD_PCT) AND < 0.5× stop distance
  (MAX_SPREAD_TO_STOP_RATIO); 15 retries × 20s.
- **Tight-stop guard**: stop ≥ 0.5% of price on instruments ≥ 500pt (the SNDK noise-stop
  class).
- **Caps**: 5 trades/instrument/day (MAX_TRADES_PER_INSTRUMENT_PER_DAY); session caps
  AUS 3 / UK 3 / US 4.
- **Intraday guard**: blocked if the instrument has moved > 2.0× ATR from today's open
  (INTRADAY_GUARD_ATR_MULTIPLIER).

## Position sizing (run_session.py)

```
risk_amount = available_balance × risk_per_trade × stress_mult
size        = calculate_position_size(epic, stop_distance, risk_amount, available_funds)
```
risk_per_trade from the user profile (Owner 2%, Wife 1%, Son 2% paper). stress_mult = 0.5
in SPX stress mode, else 1.0. Size 0 → trade skipped + alert (never a 0.5 fallback —
that caused INSUFFICIENT_FUNDS). Blocked-but-valid signals → alert_missed_trade
(classified, deduped, corrective action).

## Non-negotiables when answering

- Quote the live signal_log row or re-run scan_instrument — never assert from memory.
- Confirmations are direction-aligned: if asked "why did COT BEARISH not confirm my BUY?"
  the answer is "it shouldn't, and no longer does."
- R:R below 3.0 is never tradeable. Macro-gate failures and cap blocks must be explained
  in plain English (the corrective-action vocabulary).

## Reference files

- `references/decision-reference.md` — every signal, its threshold, and code location
- `references/troubleshooting.md` — the "why did/didn't X trade?" playbook
