# Order timing and RVOL — why a volume floor cannot gate placement

**Status:** agreed approach, 2026-09-03. This is a change in how we think about the numeric filters, so
it is written down rather than left in a commit message.

**Short version.** RVOL is a property of the *break bar*. Our orders reach IG days *before* the break.
So at the moment we place an order, the number an RVOL floor is supposed to test does not yet exist. An
RVOL floor can therefore never gate placement — it can only act at, or after, the fill.

---

## 1. What was believed, and what is actually true

The saved trading filters read like placement rules:

| Filter | What it looks like it does |
|---|---|
| `min_risk_reward` | refuse to order a setup below this R:R |
| `min_rvol` | refuse to order a setup below this relative volume |
| `min_quality`, `min_volume_score` | likewise |
| `require_above_vwap`, `require_atr_expanding` | likewise |

Two things are wrong with that reading.

**First**, the floors have never gated trading at all — they are display filters. `/api/place-order`
checks only `_user_trade_allows` (direction, location, market), and the Order Bridge, which is the only
enabled execution source, gates on its own `bridge_min_quality` plus proximity to entry. This was found
on 2026-08-29 and parked.

**Second, and this is the new finding:** for RVOL and its relatives, placement-time enforcement is not
merely absent, it is **impossible**.

### The measurement

Over the last 400 working orders:

| | count | share |
|---|---|---|
| placed **before** the trigger they were waiting for | 222 | **55.5%** |
| placed on the trigger date | 31 | 7.8% |
| placed after, no later trigger | 147 | 36.8% |

Where the order preceded its break, the gap was a **median of 8 days**, up to 44. Of the 59 pending
orders at the time of measuring, **23 had no trigger at all yet** — still waiting for the break.

That is the whole argument. A working order is an instruction placed *in anticipation* of a break. RVOL,
VolumeScore, above-VWAP and ATR-expanding are all measured **on the break bar**. They cannot be consulted
before the thing they measure has happened.

R:R and Quality are different — they are properties of the *levels*, known as soon as the setup is ready,
and therefore genuinely testable at placement.

---

## 2. Why this matters beyond the order screen

**The Best Settings recommendations filter on RVOL at the trigger.** The replay selects trades with
`rvol >= 1.8` and enters at the entry level on the trigger day. Live, that selection is not available at
the moment the order is placed.

So a recommendation carrying an RVOL floor — which is most of them, including Balanced and Growth — is
**not reproducible as a pre-placed working-order strategy**. This is very likely a real component of the
long-running complaint that an applied recommendation never reproduces its headline figure.

Anyone quoting a Best Settings number should know that the number assumes you can act on break-bar volume.

---

## 3. The agreed solution: both entry paths, not one

Waiting for the break gives a real gate but loses the guaranteed fill at the exact level. Pre-placing
gives the fill but no gate. **We do both, for different cases.**

### Path A — the rule going forward: order *after* the break

For any setup whose configuration carries a break-bar floor (RVOL, VolumeScore, VWAP, ATR):

1. do **not** pre-place a working order;
2. wait for the break;
3. read the break bar's RVOL and friends;
4. order only if the floors are met.

This is a real gate. The cost is that entry is at the post-break price rather than at the exact entry
level, so expected slippage against the backtest must be measured, not assumed.

### Path B — the safety net: check at fill, exit if weak

Path A cannot cover everything:

- the 61 orders already sitting on the book, placed under the old model;
- instruments whose break happens in a session nobody is watching — Asian and US markets break overnight
  relative to UK hours, and a pre-placed order fills without anyone present;
- any setup where a pre-placed order is still preferred for the entry price.

For those, the check moves to the **fill**:

1. the order fills on the break;
2. the break bar's RVOL is read;
3. if it is below the floor, the position is closed immediately.

This costs the spread on every rejected break. It is the only path that approximates what the backtest
assumes, because it makes the same selection — just one bar later and with costs.

### How they fit together

```
setup becomes READY
      |
      +-- floors are levels-only (R:R, Quality)  -> pre-place, gate at placement      [enforceable today]
      |
      +-- floors include break-bar metrics       -> Path A: wait for the break, then order
                                                     |
                                                     +-- cannot wait (overnight, legacy order)
                                                         -> Path B: fill, then exit if the break was weak
```

**Enforce at placement:** R:R, Quality, instrument value, direction/location/market.
**Enforce at or after fill:** RVOL, VolumeScore, above-VWAP, ATR-expanding.

---

## 4. There is no "stale" state — only "not yet applicable"

An earlier draft of this note described RVOL as *decaying* after the break, and treated a pending order
reading below the floor as a stale-but-real failure. That framing was wrong, and the account owner
corrected it:

> RVOL, ATR and VWAP are only relevant at the break.

Which is sharper and simpler. These are measurements **of one bar**. For an order that has not broken
yet, they are not stale, not breaching, and not decayed — they are **not applicable**. There is nothing
to judge, because the event they describe has not happened.

That removes a whole verdict from the audit. A pending order can only be judged on:

- **R:R, Quality, instrument value** — properties of the setup's levels, fixed when it became ready;
- **direction / location / market** — the account's own switches.

And it means the metrics are evaluated exactly once, at the break, and never revisited.

### A widening that was considered and rejected

Measuring volume over a **±2 day window** around the trigger was proposed, on the reasoning that what we
want to see is a positive change in volume rather than one specific bar. Measured over 6,704 resolved
triggers, the same 1.8 floor applied three ways:

| measure | passes | share | win rate | mean return |
|---|---|---|---|---|
| **single break bar** | 1,234 | 18.4% | **28.0%** | **3.05%** |
| trigger−2 → trigger | 1,850 | 27.6% | 26.5% | 2.46% |
| ±2 days | 2,498 | 37.3% | 26.9% | 2.44% |
| no volume filter | 6,704 | 100% | 25.4% | 1.90% |

RVOL carries real signal — every variant beats no filter. But **widening dilutes it**: more setups pass,
at a lower win rate *and* a lower mean return. A wider window admits spikes that happened on a *different*
day, and the signal is "volume confirmed **this** break". The ±2 form also cannot be a live gate at all,
since two of its bars follow the trigger.

**Decided 2026-09-03: keep the single break bar.** If the goal is more order flow, the lever to measure is
the 1.8 threshold itself, not the window.

### What this changed on the live book

Applying break-bar floors to pending orders produced 40 "failures" that were not failures at all. Judged
correctly — durable criteria only, against the setup each order was actually placed from:

| | orders |
|---|---|
| meet every durable floor — **keep** | **36** |
| below the R:R floor — cancel | 21 |
| blocked by direction / location / market — cancel | 5 (2 also below R:R) |
| **cannot be judged** | **0** |

Note the last row. Under the old basis, five orders were unjudgeable — four of them no longer appear in
the current snapshot at all. Joining to the setup they were placed from resolves **every one of them**.
The decision to cancel unconfirmable orders is no longer needed, because there are none.

---

## 5. What the order tables should show — and what they showed instead

**The display rule, decided 2026-09-03: the metric columns on an order row are the instrument's metrics
*as at the date on that row*. Nothing more.** MCap, RVOL, VWAP and ATR for that instrument on that date.

That is deliberately NOT "the setup that caused this order". Attributing a setup is the hard problem
described above; reading an instrument's metrics on a given day is not. The columns exist so you can look
at a row and see what the instrument was doing when the order was placed.

It is also already buildable from code we have, using the same functions the VolumeScore itself uses, so
there is no new definition of RVOL, VWAP or ATR anywhere:

```
volume_score._bar_index(bars, date)      -> the bar for that date
volume_score._rvol_at(bars, i)           -> RVOL on it
volume_score._above_vwap(bars, i, bull)  -> above/below the 20-bar VWAP, direction-aware
volume_score._atr_expanding(bars, i)     -> ATR expanding on it
```

### What they showed instead

`_attach_setup_metrics` tried to answer the harder question and matched on **"the latest trigger at or
before placement"**. For an order placed before its own break that cannot select the right setup: the
row's `triggered_date` is still null or later than placement, so it is excluded and an **older,
unrelated break** is used.

Measured on the live book: **34 of 61 pending orders (55.7%) displayed metrics from a different setup.**

```
9021.T   placed 2026-08-19
   table showed  ready 2026-03-17  trig 2026-04-14   rvol 1.13  rr 9.48
   actually from ready 2026-08-19  trig 2026-08-21   rvol 0.83  rr 8.62

4519.T   placed 2026-09-01
   table showed  ready 2026-02-06  trig 2026-01-30   rvol 2.30  rr 7.90
   actually from ready 2026-08-25  trig none         rvol none  rr 5.72
```

Note the shape of the mistake, because it is instructive. The resolver is a *single shared definition*,
which is the right pattern and is why it was reused for the IG Account columns. Sharing one definition
guarantees two screens agree. It does not make either of them right. **Consistency is not correctness**,
and correctness was never checked.

The second lesson is smaller and sharper: the question being answered was more complicated than the
question being asked. "Show me RVOL for this row" became "resolve the causing setup and show its RVOL",
and the harder question is the one that has no reliable answer.

## 6. What this changes for anyone reading a number

- An order's displayed RVOL/VWAP/ATR is **not trustworthy** until the columns are rebuilt on the row's own
  date. They currently show another setup's figures for the majority of rows.
- A Best Settings recommendation with an RVOL floor **assumes** break-bar selection. It is achievable only
  via Path A or Path B above.
- "This order breaches my filters" needs qualifying: against the setup it was placed from, or against
  today? Those are different questions with different answers.

---

## Evidence

Everything above was measured on the live database on 2026-09-03, not inferred:

| Claim | Measured |
|---|---|
| orders precede their break | 222 of 400 (55.5%), median 8 days |
| pending orders with no break yet | 23 of 59 |
| RVOL known at the break | 6,899 of 7,078 triggers (97.5%) |
| RVOL unknown, equities (a real gap) | 18 (0.3%) |
| RVOL unknown, FX/indices/commodities (no volume by nature) | 161 (2.3%) |
| setups clearing R:R ≥ 5.0 | 4,494 of 7,078 (63.5%) |
| ...that also clear RVOL ≥ 1.8 | 755 (16.8%) |
| orders showing the wrong setup's metrics | 34 of 61 (55.7%) |
| pending orders meeting every durable floor | 36 of 61 |
| pending orders below the R:R floor | 21 |
| pending orders blocked by direction/location/market | 5 |
| pending orders that cannot be judged, once joined on ready_date | 0 |

Scripts used are one-off measurements, not committed. The queries are simple enough to reproduce from
the numbers above; `order_filter_audit.py` is the committed, tested version of the order-versus-filter
judgement.
