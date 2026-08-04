---
name: ah-web-formatting
description: >
  Layout and formatting rules for the Squeeze web app (hvf_web/index.html) — table wrapping and column
  widths, the chart strip's order/height/stacking/width behaviour, instrument-name truncation, and the
  multi-select filter component. Also carries the verification recipe for this specific page, which has
  several traps that silently produce wrong conclusions (display:contents wrappers have no box;
  requestAnimationFrame never fires in the preview pane; logged-out data collapses whole columns to
  "—"). Use whenever adding or changing ANY table, chart, filter or card in hvf_web/index.html, or when
  the user reports "wrapping", "cramped", "white space", "scrolling" or "spacing" on a page.
---

# Squeeze web app — formatting rules

Everything here was established by fixing a real defect (user requests 2026-07-17, P-08…P-25). Each rule
states WHY, because the naive alternative is what broke.

## 1. Tables

**Doc tables do not wrap by default.**

```css
th,td{white-space:nowrap}                                            /* base */
.doc .tablewrap table th,.doc .tablewrap table td{white-space:nowrap;word-break:break-word}
```

- A cell that SHOULD wrap declares `style="white-space:normal"` **inline** — inline beats the
  stylesheet, so it survives the nowrap default. This is the mechanism the whole scheme relies on.
  Current wrapping cells: syslog message, user note, market notes, Change-Request requirement text.
- `word-break:break-word` stays in the rule: it is inert while `nowrap`, and still saves the wrapping
  cells from one giant unbroken string.
- **Never** set `white-space:normal` on a whole table. That was the old rule and it forced EVERY cell to
  wrap; any squeezed column then broke mid-word — Change Requests rendered "Completed" as "Complet/ed".
  It also spawned six per-view rules (`#view-orderops/-preorders/-activity/-jobs/-users/-performance`)
  that existed only to re-assert nowrap. Those are now redundant; do not add a seventh.
- A short-label column next to a greedy `white-space:normal` column also wants `width:1%`, so it sizes
  to its longest label and hands the remaining width to the text column.
- Too wide is fine: `.doc .tablewrap{overflow-x:auto}` scrolls sideways. `#view-preorders` chose that
  trade-off deliberately.

**Widths.** Table views are normally `max-width:1240px`. A deliberately dense table may use the full
available application width when this materially reduces horizontal scrolling; Squeeze History is the
reference (`width:100%;max-width:none;margin-inline:0`). An accidental narrower outlier is a bug:
`view-users` sat at 840px, its
`width:auto` table outgrew it, and because `#view-users .tablewrap` is `overflow-x:visible` the overflow
pushed the whole PAGE sideways while ~500px sat unused beside it — "scrolling AND white space" from one
cause. `#view-users table{width:100%}` fills the width; `#view-performance` keeps `width:auto`
shrink-to-fit because its columns are numeric and stretching them reads worse.

**Instrument names.** Use `nm40(name)` in table cells — 40 chars, full name in a `title`, HTML-escaped.
Michelin's legal name is 80 chars and wrecks layout. Detail views keep the full name.

**Every column sorts.** EVERY table column must be clickable to sort by that column (user 2026-07-20).
The house pattern, reused across Scanner / Performance / Order Ops / Squeeze History:

- Give each header `class="clk"` and a `data-<prefix>="<field>"` attribute naming the row key it sorts
  (e.g. `data-pf`, `data-sqh`). The `<field>` is the property on the row object, not the display label.
- Keep module sort state as `let <x>SortK="…", <x>SortDir=-1` (`-1` = descending first click).
- Wire once: `document.querySelectorAll("th[data-<prefix>]").forEach(th=>th.onclick=()=>{…toggle dir if
  same key else -1; set key; repaint; _sortArrows("data-<prefix>", sortK, sortDir);});`
- Sort with the shared `genSort(rows,k,dir)` — it already pushes null/`""` to the bottom, compares numbers
  numerically and everything else with `localeCompare`. Don't hand-roll a comparator.
- `_sortArrows(attr,key,dir)` stamps the ▲/▼ marker on the active header; call it after every sort so the
  indicator tracks the column.
- A `sortK` of `""` means "server / natural order" — a valid default when the API already returns a
  sensible order (Squeeze History returns newest-first, and only sorts once a header is clicked).

## 2. Chart strip (`.viz`)

**Order** (left → right): `Location`, `Market`, `Sector` on the LHS (in that order), then the rest;
`Ticker` far right wherever it appears. `.vizbars{display:contents}` means **DOM order IS visual order**.
(User 2026-07-24/25, P-03 L29 / P-05 L182/L340 — this SUPERSEDES the earlier P-12a "Market/Sector lead":
Location now leads. Applied on Scanner, Performance, Squeeze History and Pre-orders.)

**Heights.** `.viz{align-items:stretch}` — cards sharing a row take the tallest's height. `.viz` wraps
and each flex LINE stretches independently, so a card is only matched against its own row.

**Filling the card.** Stretch alone just relocates the waste (a 1-bar card measured 68px of dead space).
`.vizbox>.bars{flex:1;justify-content:space-evenly}` plus capped growth:

```css
.vizbox>.bars>.bar{flex:1 1 auto;max-height:30px}
.vizbox>.bars>.bar .fill{height:min(100%,18px);min-height:11px}
.vizbox>.bars>.bar .tk,.vizbox>.bars>.bar .n{font-size:clamp(10px,1.6vh,12px)}
```

The caps matter: uncapped, a 1-bar card gets one absurd 150px block. The selector is
`.vizbox>.bars>.bar` (direct child) so the **pie's legend** — nested a level deeper — keeps its 11px
colour swatches instead of being inflated into fake bars.

**Colour/order by a metric.** `barChart(...,opts)` takes an optional `{metric:{key:value}}` map (user
2026-07-26, P-05 L281). When present the bars are ORDERED by `value` desc and TINTED green (≥0) / red (<0),
intensity scaled to the biggest `|value|` shown; **bar length still encodes the count**, so length = how
many and colour = how good. Used by the Results Market & Location charts (avg return per group, from
`avgX()`). Omit `opts` for the default count-order + `colorFn` behaviour — every other chart is unchanged.

**Width.** Cards are `flex:1 1 auto;min-width:0` (cap `max-width:340px`) so they spend the row's spare
width. Skip this and enlarging label text just crams it: `.bar .tk` is a shrinking column sharing its
row with a fixed-width fill and count, so on a 186px card the label got ~68px and "NASDAQ 100" /
"Europe (West)" collided with their bars while 256px sat empty to the right.

**Stacking** — `packViz(id)` after every strip render. **It stacks ONLY when the strip WRAPS to more than
one row** (user 2026-07-27, P-06). If every card fits on a single row, `packViz` leaves them side by side —
`align-items:stretch` + the `space-evenly` bars fill each card cleanly and the row spaces evenly, matching
Performance → Results. Stacking a short card *under* another only pays off to reclaim vertical space once
the row has wrapped; doing it while there is horizontal room was the "inconsistent spacing / big white
gaps" (a 99px gap before a `flex:none` `.vizcol`, "Status under Direction") the user reported on Scanner +
My Pre-orders. So `packViz` measures the current row count first (`getBoundingClientRect().top`, skipping
zero-rect `display:contents` wrappers); returns early if ≤1 row; otherwise drops Month-Week (below) and, if
still wrapped, does the greedy pin-aware consolidation (short cards ≤60% of the tallest share a `.vizcol`).
Idempotent — unwraps existing `.vizcol` first. Preserves order.

> **The decision must be MEASURED, never hardcoded.** Logged out, Market/Location/Timeframe each show a
> single "—" bar and look obviously pairable; logged in they carry 6–8 bars and must stay side by side.
> Same code, opposite layouts. A hidden view (the `file://` snapshot) reports `top:0` for every card → one
> row → no stacking, the safe default; **verify layout on a VISIBLE view** — reveal it
> (`el.classList.remove('hidden')`), inject realistic data, set an explicit wide viewport (the preview
> pane defaults narrow, which fakes a wrap), then measure.

**NEVER stack Location, Market or Sector** (user 2026-07-27, P-06). These three primary charts are
always standalone cards — `packViz` pins them via `VIZ_NOSTACK`/`_vizLabel` (matched on the `<h5>`
header; the winners strip's "— net £" suffix is stripped before the compare) so they are never moved
into a `.vizcol` and nothing is ever stacked onto them. Height alone used to tuck a short Location under
another card ("Direction under Location"); it must not. Only the compact charts (Direction, Status,
Timeframe, Win/Loss, Outcome, Month …) may pair. **The Performance → Results strip is the gold
standard** — every card in its own `.vizsector`, L/M/S leading, no awkward stacks.

**Drop Month-Week if the strip wraps to a second row** (user 2026-07-27, P-06). After packing, `packViz`
checks whether the real flex participants span more than one row (distinct `getBoundingClientRect().top`,
skipping zero-rect `display:contents` wrappers); if so and a "Month-Week" card exists it removes that one
card and re-packs once. Month-Week is the most disposable date chart, so it is sacrificed to keep the
strip on one row. Guarded to no-op when no real layout is available (the static `file://` snapshot
reports top 0), so it never blindly drops the chart during offline verification — confirm on the live
logged-in app.

## 3. Multi-select filters (`.msel`)

Progressive enhancement: the real `<select multiple>` stays in the DOM (visually hidden) and remains the
single source of truth, so `pass()`, reset, show-all, saved defaults and chart click-to-filter keep
working untouched. The dropdown drives `option.selected`, dispatches `input` on the select, then
`msyncAll()` repaints button + checkboxes. Call `msyncAll()` after any BULK write to the selects
(reset/showall/applyUserDefaults/fillSel) or the button label goes stale. Options arrive late via
`fillSel()`, so build each popup on open.

**Checkbox and radio controls must override the global form-field width.** The page-level
`select,input{width:100%}` rule is appropriate for text and number fields but makes an unscoped checkbox
fill its container and crush its label. For pill/toggle controls, set the checkbox/radio to an explicit
size (`width` and `height` around 18px, `flex:0 0 auto`, zero margin), keep the label `white-space:nowrap`,
and provide a minimum 44px-high tap target. Verify at iPad widths as well as desktop.

## 4. Verifying changes on THIS page — the traps

Measure and assert. Do not eyeball, and do not trust a summary you built without checking it.

- **`display:contents` wrappers have no box.** `.vizbars`/`getBoundingClientRect()` returns all zeros
  and sorts first, so a naive left-to-right sort reports a wrong order. Walk to the leaf elements that
  actually have geometry.
- **`requestAnimationFrame` never fires** when the preview pane isn't painting — `await`ing it hangs the
  tool for 30s and looks like a render bug. Don't.
- **Screenshots time out** on the snapshot-heavy pages. Use DOM measurement.
- **Logged out, `LIMITED` strips `market`/`location`/`timeframe`** from `/api/records`; every row
  collapses to "—". Never tune layout on that. Inject a realistic distribution first:
  `DATA.forEach((r,i)=>{r.market=M[i%M.length];…}); render();`
- **Read every heading, not the first.** Taking only the first `h5` per element made stacked columns
  report their partner as a *missing chart* and nearly sent me chasing a non-existent bug.
- **Wrapping check**: lines = `el.getBoundingClientRect().height / lineHeight` on the **text element**
  (the `<b>`), not the cell — a row grows with its neighbouring wrapped cell and false-positives.
- **Truncation check**: `el.scrollWidth > el.clientWidth`.
- **"Cramped" is usually horizontal.** Confirm with `scrollHeight > clientHeight` before assuming
  vertical overflow; on every reported case it was the label column, not the card height.

## 5. Checklist for a new table or chart

1. Short-value columns nowrap (default); only the long-text column gets inline `white-space:normal`.
2. Long-label column next to it → `width:1%`.
3. Instrument names → `nm40()`.
4. Every header sortable: `class="clk"` + `data-<prefix>` on each `<th>`, wired to `genSort` +
   `_sortArrows`. No exceptions.
5. View `max-width:1240px` unless there's a stated reason.
6. Chart strip: Location, Market, Sector left (in that order); Ticker right; call `packViz()` after render.
   Location/Market/Sector are NEVER stacked (pinned in `packViz`); Month-Week is dropped if it would wrap
   to a second row. Gold standard = Performance → Results.
7. Verify by measuring, with realistic (not logged-out) data.

## 6. House standards for EVERY chart/table screen (user 2026-07-24, P-03 L24–L39)

These are STANDING rules — apply them to every screen you add or touch, not just when asked. Status
noted as of 2026-07-24.

**Tables**
- Every column sortable (L25) — house pattern in §1 (`class="clk"` + `data-<prefix>`, `genSort`,
  `_sortArrows`). Applies to the *detail* tables too (the Change-Requests per-file breakdown was the last
  gap, fixed 2026-07-24). ✅ broadly compliant.
- **Gap below the last row, above the horizontal scrollbar (user 2026-08-01).** A table with few rows but
  many columns shows a horizontal scrollbar jammed right under the last row — it reads as noise. `.doc
  .tablewrap` carries `padding-bottom:22px` (widened from 14px, user 2026-08-02) so the scrollbar always
  sits well clear of the content. Keep it; do not remove it per-view.
- **A chart strip NEVER wraps to a second row (user 2026-08-01).** Charts placed side by side must stay on
  ONE row — if the strip would spill to a second row, consolidate (stack short cards into `.vizcol`s, drop
  the most disposable card) until it fits. This supersedes the earlier "stack only when already wrapped"
  softness: the target is always a single row. `packViz` owns this; verify on the live logged-in app (the
  `file://` snapshot reports one row for everything — see §2/§4 traps).
- **The date-filter row NEVER wraps across rows either (user 2026-08-01).** The From/To + quick-window +
  location controls must stay on a single line; if space is tight, shrink/scroll within the row rather than
  letting a control drop to a second row. Same one-row discipline as the chart strip.
- When a column is sorted — default OR manually — its ▲/▼ arrow must be visible (L39). `_sortArrows` shows
  it on click; tables with a **default** sort should also call `_sortArrows` on first paint. ⚠️ partial —
  manual sorts show it; not every table stamps the arrow on initial render. Gap logged.

**Charts (`.viz` strip)**
- Header text is **bold** — `.vizbox h5{font-weight:700}` (L37). ✅ (was un-bold until 2026-07-24).
- Order left→right: Location, then Market, then Sector, then the rest; Ticker far right (L29, §2). ✅
- Multi-select on every chart (L30) — the `.msel` component (§3); the real `<select multiple>` stays the
  source of truth. ✅ where charts are wired to a `pff_*/pof_*/sqf_*` filter key.
- Spacing: consistent between cards, bars left-aligned, labels readable — all handled by `packViz()` +
  the `.viz`/`.vizbox` CSS in §2 (L34/L35/L36). Call `packViz(id)` after every strip render. ✅
- NEVER stack Location, Market or Sector (P-06, 2026-07-27) — pinned standalone in `packViz`; Month-Week
  is dropped if it would spill to a second row. Gold standard strip = Performance → Results. See §2.
- Do NOT change a card's size/position when a selection is made (L33): `packViz` measures with a
  `.measuring` class and packs deterministically, so selection must not re-pack differently. ✅ as long as
  selection only filters data and you re-`packViz` with the same inputs.
- On selection, show the impact on the OTHER charts **without hiding bars** (L31): a selected value should
  recolour/annotate the other charts rather than dropping their bars to zero/removing them. ✅ done via
  **brushing** (user 2026-07-26): each chart is counted over the rows that pass every OTHER filter but not
  its own, so all option-bars stay visible with the selected value(s) highlighted (● + `active`) and the
  header shows the `▶ N ✕` clear badge. Performance was always brushed (`_cross`/`byX`/`byXFull`); the
  Scanner (`pass(r,except)` + `renderViz`'s `by(field)`) and My Pre-orders (`POF` dims + brushed `pby`)
  were collapsing to only the picked value and now brush too. Order Ops (`oby` over full `OO_ROWS`) and
  Squeeze History (`by` over search-only `base`) never collapsed — they already show the full mix with the
  selection highlighted. **This matters most for DEFAULT selections** (`_seedStatusDefaults`): a seeded
  default must read as "one option selected among the others", never as "the data got filtered and nothing
  is selected".
- Any date-filtered screen must have a **Month** and **Month-Week** chart (L24). Present on the perf/report
  and squeeze-history strips (`barChart("Month…")` + the `_mw()` month-week bucket); ⚠️ not audited as
  universal across every date-filtered screen. Gap logged.

**Data**
- Never store data in local files — always the Supabase database (L26). The one deliberate exception is
  `hvf_web/snapshot.json`, a rebuildable CACHE of a scan (not a source of record); everything durable
  (price_history, squeeze_history, web_users, app_config, x_publications…) lives in Supabase.
- Run instrument backfills wherever 15 months of history is missing (L27) via `price_audit.py --backfill`
  (see [[deploy-cron-tasks]]); new universe additions (e.g. the 2026-07-24 bond ETFs) get a scoped backfill
  on deploy.

When a standard is only partially met, don't silently leave it — log a specific P-03 follow-up in the
ChangeRequests file (marker LAST on the line — see [[cr-status-live]]).
