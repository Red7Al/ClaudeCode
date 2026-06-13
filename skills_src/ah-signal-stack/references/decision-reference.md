# Decision reference — every signal, threshold and code location

Verified against signals.py / config.py on 2026-06-12. The code is the source of truth;
re-check before quoting a value.

## Constants (config.py)

| Constant | Value | Meaning |
|---|---|---|
| VIX_GATE_THRESHOLD | 35.0 | VIX above → macro gate fails |
| YIELD_SPREAD_GATE_THRESHOLD | −1.0 | 2y/10y spread below → gate fails |
| SPX_HIGH_STRESS_PCT | −2.5 | SPX down more → gate fails (no new entries) |
| SPX_STRESS_PCT | −1.0 | SPX down 1–2.5% → sizes halved (gate still passes) |
| MIN_PRIMARY_SIGNALS | 2 | primaries needed (HVF / elite-senate / POTUS bypass) |
| MIN_CONFIRMATION_SIGNALS | 1 | direction-aligned confirmations needed |
| MIN_RISK_REWARD / DEFAULT_TARGET_RR | 3.0 | hard R:R floor; HVF_MIN_RR aliased to it |
| MAX_SPREAD_PCT | 0.005 | spread < 0.5% of mid |
| MAX_SPREAD_TO_STOP_RATIO | 0.5 | spread < half the stop distance |
| MAX_TRADES_PER_INSTRUMENT_PER_DAY | 5 | per-name daily cap |
| SESSION_TRADE_CAPS | AUS 3 / UK 3 / US 4 | per-session daily cap |
| INTRADAY_GUARD_ATR_MULTIPLIER | 2.0 | block if moved > 2× ATR from open |
| PA_CONFIRM_THRESHOLDS | equities ±40, crypto/FX lower | price-action verdict gate |

## Primary signals (signals.py ~1660–1732) — each +1 to primary_count

| Primary | Fires when | Direction source |
|---|---|---|
| Options flow | options_bias BULLISH/BEARISH (call/put ratio + IV rank) | the bias |
| Bollinger breakout | bb_breakout_dir BULLISH/BEARISH | the breakout |
| High-vol + VWAP (BB substitute) | no BB, but HIGH_VOLUME and price ABOVE/BELOW VWAP | VWAP side |
| HVF | hvf_signal READY or TRIGGERED | hvf_type |
| ADX directional | ADX ≥ 20 AND \|+DI − −DI\| ≥ 5 | +DI vs −DI |
| ORB | orb_dir BULLISH/BEARISH (30-min opening range break) | the break |
| 52-week extreme | price at yearly high/low | toward the extreme |
| Elite senator / POTUS | primary_fired (≥70% win-rate senator, or POTUS mention) | BULLISH |

Direction = majority vote of `primary_dir`. Tie → direction None → no trade.
Bypass to pass stage 2 alone: HVF fired, or elite-senate/POTUS.

## Confirmation signals (signals.py ~1707–1862) — each +1, ALL direction-aligned

| Confirmation | Counts when | Alignment rule |
|---|---|---|
| Director buys | director_signal AND BUY | long-side only |
| Activist 13D | activist_signal AND BUY | long-side only |
| Senate buy | senate_signal AND BUY | long-side only |
| Superinvestor | superinvestor_signal AND BUY | long-side only |
| Social mention | social_signal AND BUY | long-side only |
| COT positioning | bias == side (BULLISH↔BUY / BEARISH↔SELL) | side match; >14d stale → neutral |
| ADX strong trend | adx_signal STRONG_TREND AND dominant DI matches side | side match |
| OBV | bull div/confirming on BUY; bear on SELL | side match |
| Commodity macro | score sign matches side (commodities only) | side match |
| Sector ETF | sector_dir matches side (equities only) | side match |

`conf_count` and `confirmations_fired` apply the SAME rules — no misaligned confirmation
is ever counted or named.

## Trade-fire expression (signals.py ~1881)

```python
primary_gate = (primary_count >= MIN_PRIMARY_SIGNALS) or hvf_fired or potus_or_senate
trade_signal = (
    macro.get("macro_gate_pass", False)
    and primary_gate
    and conf_count >= MIN_CONFIRMATION_SIGNALS
    and direction is not None
    and pa_confirmed          # CONFIRM_LONG for BUY / CONFIRM_SHORT for SELL
)
```
