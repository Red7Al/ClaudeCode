# Troubleshooting — "why did / didn't X trade?"

Always answer from the live signal_log row or a fresh scan, never from memory. Pull the
most recent row first:

```sql
select session_time, direction, macro_gate_pass, options_bias, bb_breakout_dir,
       hvf_type, hvf_signal, cot_bias, adx_signal, pa_verdict,
       primary_count, confirmation_count, trade_triggered, signal_summary
from signal_log where ticker = :t order by session_time desc limit 3;
```

Then walk the five gates IN ORDER — the FIRST failing gate is the answer:

## 1. Macro gate failed → nothing trades, system-wide
- VIX > 35, yield spread < −1%, or SPX down > 2.5% today.
- Symptom: `macro_gate_pass = false` on every ticker that scan.
- Not a per-instrument problem — say "the whole session was risk-off".

## 2. Primary gate failed → primary_count < 2 and no bypass
- Fewer than 2 primaries fired and neither Squeeze nor elite-senate/POTUS bypassed.
- Check which primaries are present in the row (options_bias, bb_breakout_dir, hvf_*,
  adx, orb, week52). "Only options flow fired; needs a second primary or an Squeeze."

## 3. Direction is None → primaries disagree
- Equal BULLISH and BEARISH primaries → no majority → no trade.
- "Options says up, ADX says down — the primaries cancelled out."

## 4. conf_count < 1 → no aligned confirmation
- THE common confusion: a confirmation fired but on the WRONG side. COT BEARISH does not
  confirm a BUY; insider buying does not confirm a SELL. This is correct behaviour
  (user 2026-06-12). Quote `confirmations_fired` — if empty, none aligned.
- Or COT was the only candidate and it's > 14 days stale (neutralised).

## 5. pa_confirmed false → price action says WAIT
- pa_verdict is WAIT, or CONFIRM_LONG on a SELL (mismatched). The falling-knife guard.
- "Every fundamental was bullish but the chart hadn't turned — PA gate held it."

## Fired but NOT placed → an execution gate blocked it (check #alerts)
The signal was valid; ig_shim refused at execution. Classes (alert_missed_trade):
- **TIGHT_STOP** — stop < 0.5% of price on a ≥500pt instrument (structural; retry won't fix)
- **SPREAD_VS_STOP / SPREAD_TOO_WIDE** — spread too big vs stop / vs mid (often clears mid-session)
- **INSUFFICIENT_FUNDS** — margin exhausted (add funds / close a position)
- **SIZE_ZERO** — risk-based size below IG minimum (account too small for this instrument)
- **CAP** — instrument or session daily cap reached
Each alert already carries the plain-English corrective action — relay that.

## Nonsense number? Resolve before reporting (user directive)
A 99% distance, an inverted R:R, a 54% watch — suspect a unit/scale or wrong-instrument
bug FIRST (run the data-quality audit, check the epic), fix in code, and only surface a
clear explanation. Never paste a raw nonsense figure at the user.

## Sanity commands
```
# one ticker — scan_instrument needs the macro gate dict (3 args)
python -c "import signals as s; m=s.get_macro_gate('US_OPEN'); \
  r=s.scan_instrument('NVDA','US_OPEN',m); \
  print('dir',r['direction'],'prim',r['primary_count'],'conf',r['confirmation_count'],'fire',r['trade_signal'])"
python run_diagnostics.py        # full signal-stack health → #alerts
```
