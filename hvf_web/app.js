let DATA=[], SEL=null, sortK="dist_entry", sortDir=1, POS={}, PUB={}, IGWO=new Set(), signalOnly=true;   // default: ABS(Dist→Entry) ascending (closest first)
// Has the snapshot fetch come back yet? DATA starts empty, so without this a tab rendering before the
// fetch resolves shows an empty table that looks exactly like "nothing matched" -- the user could not
// tell a slow load from a genuinely empty result (user 2026-08-18: "when preorders loads to screen
// there is no message for 'Data loading' whilst we wait for content").
let DATA_LOADED=false;
const $=id=>document.getElementById(id);
// In-app confirm (user 2026-07-18): the native confirm() prefixes the host ("…says"), which looked
// broken behind the old ngrok tunnel and is still noise on squeezescanner.cloud. This styled dialog
// keeps the message clean and on-brand. Returns a Promise.
// `rows` (optional) renders [label, value] pairs as a compact TWO-COLUMN grid instead of forcing the
// caller to jam everything into the message as bullet lines - fourteen of those in a 440px box needed a
// scroll bar (user 2026-08-15: "Apply this configuration should give a form that does NOT need a scroll
// bar"). Values are still written with textContent, so nothing here can inject markup. The box is capped
// to the viewport with only the grid allowed to scroll, so the buttons can never be pushed out of reach.
function appConfirm(msg,{title="Please confirm",ok="Confirm",cancel="Cancel",rows=null}={}){
  return new Promise(res=>{
    const ov=document.createElement("div");
    ov.style.cssText="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px";
    const box=document.createElement("div");
    box.style.cssText="background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:10px;max-width:"
      +(rows?"660px":"440px")+";width:100%;box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden;"
      +"max-height:calc(100vh - 32px);display:flex;flex-direction:column";
    box.innerHTML=`<div style="padding:14px 18px;border-bottom:1px solid var(--line);font-weight:700;flex:0 0 auto">${title}</div>`
      +`<div data-msg style="padding:14px 18px ${rows?'8px':'16px'};font-size:13.5px;line-height:1.5;color:var(--fg);flex:0 0 auto"></div>`
      +(rows?`<div data-rows style="padding:0 18px 14px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 22px;font-size:12.5px;overflow:auto;min-height:0"></div>`:``)
      +`<div style="padding:12px 18px;border-top:1px solid var(--line);display:flex;gap:8px;justify-content:flex-end;flex:0 0 auto">`
      +`<button class="btn" data-x>${cancel}</button>`
      +`<button class="btn" data-ok style="border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent)">${ok}</button></div>`;
    box.querySelector("[data-msg]").textContent=msg;   // textContent so the message can't inject markup
    if(rows){
      const grid=box.querySelector("[data-rows]");
      rows.forEach(([label,value])=>{
        const line=document.createElement("div");
        line.style.cssText="display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:4px 0;border-bottom:1px solid var(--line)";
        const l=document.createElement("span"); l.className="muted"; l.textContent=label;
        const v=document.createElement("b"); v.style.cssText="text-align:right;white-space:nowrap"; v.textContent=String(value);
        line.append(l,v); grid.appendChild(line);
      });
    }
    const done=v=>{ov.remove();document.removeEventListener("keydown",onKey);res(v);};
    const onKey=e=>{if(e.key==="Escape")done(false);else if(e.key==="Enter")done(true);};
    box.querySelector("[data-x]").onclick=()=>done(false);
    box.querySelector("[data-ok]").onclick=()=>done(true);
    ov.onclick=e=>{if(e.target===ov)done(false);};
    document.addEventListener("keydown",onKey);
    ov.appendChild(box);document.body.appendChild(ov);
    box.querySelector("[data-ok]").focus();
  });
}
// Trimmed 2026-08-16 when the filters moved to Squeeze History. f_mkt/f_sec stay because they still
// carry the saved market/sector scope for Back Test and Apply-this-configuration, even though pass() no
// longer filters on them. Every loop over F dereferences $(id) unguarded, so an id listed here that has
// no element throws on load.
const F=["f_search","f_mkt","f_sec"];
function syncScannerNameSearch(value){if($("f_search"))$("f_search").value=value||"";applyAll();}
const LOC={UK:"United Kingdom",US:"United States",FX:"FX",
  "Europe (West)":"Europe (West)","Europe (East)":"Europe (East)","Asia":"Asia","Other":"Other"};
const locName=v=>LOC[v]||v||"";
// Broker leverage by instrument type (user 2026-07-03). Type derived from the instrument's market.
let LEVERAGE={fx:30,equities:5,commodities:10,indices:20};   // IG UK retail (FCA/ESMA) defaults (user 2026-07-24, P-02)
function levType(r){const m=r.market||"";if(m==="FX")return"fx";if(m==="Indices")return"indices";if(m==="Commodities")return"commodities";if(m==="Crypto")return"equities";return"equities";}
const levOf=r=>LEVERAGE[levType(r)];
const f2=v=>(v==null||v==="")?"":(+v).toFixed(2);
const disp=t=>(t||"").replace(/\.L$/,"");
// Long legal names wreck table layout — Michelin's runs to 80 characters ("Compagnie Générale des
// Établissements Michelin Société en commandite par actions"). Table reports show the first 40 with the
// full name on hover (user 2026-07-17, P-23). Detail views keep the full name.
const NAME_MAX=40;
const _esc=s=>String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
const nm40=n=>{const s=String(n??"");
  return s.length<=NAME_MAX?_esc(s):`<span title="${_esc(s)}">${_esc(s.slice(0,NAME_MAX-1))}…</span>`;};
const qcol=q=>q>=70?"var(--bull)":q>=50?"#d29922":"var(--bear)";   // quality colour: green / amber / red
// RVOL on the trigger bar (user 2026-07-17, P-30) — that day's volume / the mean of the previous 20.
// >=2 = a real participation spike (green), >=1 = above normal (amber), <1 = thin (muted). Blank, never
// 0 or 1.0, where there is no real volume to measure: FX and indices would otherwise read as "normal".
const rvolCell=v=>v==null?'<span class="muted">—</span>'
  :`<b style="color:${v>=2?'var(--bull)':v>=1?'#d29922':'var(--muted)'}">${(+v).toFixed(1)}×</b>`;
// Scanner RVOL with a LABELLED fallback (user 2026-08-28: "Rows still has empty data e.g. RVOL!!!").
//
// Two different RVOLs reach the client. `rvol` is measured on the setup's own break bar and exists only
// for TRIGGERED rows; `current_rvol` is today's, for every instrument. The Scanner rendered `rvol` alone,
// so every READY and DEVELOPING row showed a dash while the value sat unused in the same payload.
// Measured 2026-08-29: 124 of 261 signal rows blank, and 114 of those had a live value available.
//
// The fallback is MARKED, never silently substituted. Presenting today's RVOL as the trigger's is
// precisely the clobber bug of 2026-08-17 that test_order_ops_enrichment_keeps_the_servers_values
// exists to prevent -- a value from the wrong moment is worse than a dash, because a dash is honest.
const rvolScannerCell=r=>{
  if(r.rvol!=null)return rvolCell(r.rvol);
  if(r.current_rvol==null)return rvolCell(null);
  const d=r.current_rvol_date?` on ${r.current_rvol_date}`:'';
  return `<span title="Today's RVOL${d}, not the trigger bar's — this setup has not triggered yet" style="opacity:.75">`
       + `${rvolCell(r.current_rvol)}<span class="muted" style="font-size:9px;vertical-align:super">now</span></span>`;
};
// VolumeScore cell (0–12, user 2026-07-24, P-02): 8+ green (trade-worthy), 5–7 amber, below grey.
const volScoreCell=v=>v==null?'<span class="muted">—</span>'
  :`<b style="color:${v>=8?'var(--bull)':v>=5?'#d29922':'var(--muted)'}" title="${v} of 12">${v}</b>`;
// "since trig" colour: green when price has moved in the trade's favour, red against.
const trigCol=r=>{const c=r.chg_since_trig;if(c==null)return"var(--fg)";return c<0?"var(--bear)":c>0?"var(--bull)":"var(--fg)";};  // red when negative

function uniq(k){return [...new Set(DATA.map(r=>r[k]).filter(x=>x!=null))].sort();}
function fillSel(id,k,label){const s=$(id);uniq(k).forEach(v=>{const o=document.createElement("option");o.value=v;o.textContent=label?label(v):v;s.appendChild(o);});
  if(DATA.some(r=>!r[k])){const o=document.createElement("option");o.value="—";o.textContent="— (none)";s.appendChild(o);}}
const num=v=>v===""||v==null?null:parseFloat(v);
// THE tick/cross cell. Green tick, red cross, muted dash for unknown -- one definition, used by every
// table (user 2026-08-30: "across all tables make a tick GREEN and a cross RED").
// It already existed and was already correct; the Scanner, New orders and Squeeze History tables each
// carried their own UNCOLOURED copy instead of calling it, which is how they drifted apart. Kept here
// with the other shared cell formatters so it is found before a fifth copy gets written.
// null/undefined is UNKNOWN and must never render as a cross -- a missing metric is not a failed one.
const _tickCross=v=>v==null?'<span class="muted">—</span>':v?'<span style="color:var(--bull);font-weight:700">✓</span>':'<span style="color:var(--bear);font-weight:700">✗</span>';
// The Introduction example image failed to load. SAY SO. The previous handler was
// onerror="this.style.display='none'", which made a broken picture and a page that never had one look
// identical -- the fourth silent-failure of this shape on this site (user 2026-08-30). Nothing else on
// the page depends on the picture, so the message is reassuring rather than alarming.
function introCardFailed(img){
  if(img)img.style.display="none";
  const cap=document.getElementById("intro-card-cap");
  if(cap)cap.innerHTML='<b style="color:var(--bear)">⚠ The example chart could not be loaded.</b> '+
    '<span class="muted">The method is described in full beside it; nothing else on this page depends on the picture.</span>';
}
function daysSince(d){if(!d)return null;const t=Date.parse(d);if(isNaN(t))return null;return Math.round((Date.now()-t)/864e5);}

function augment(r){
  r.dist_entry=(r.entry!=null&&r.current_price)? +(((r.entry-r.current_price)/r.current_price)*100).toFixed(2):null;
  // Return since it triggered, vs the entry (trigger) level. DIRECTION-AWARE so it agrees with the
  // Performance tab: a BEAR wins when price falls below entry (user 2026-07-18, RRL.AX read -8.44%
  // "loss" here while Performance correctly showed +7.7% — it was direction-agnostic). BULL: (cur-entry)/entry,
  // BEAR: (entry-cur)/entry. Only meaningful for TRIGGERED rows.
  r.chg_since_trig=(r.status==="TRIGGERED"&&r.entry&&r.current_price)? +((((r.direction==="BEAR"?(r.entry-r.current_price):(r.current_price-r.entry)))/r.entry)*100).toFixed(1):null;
  // "Days since" and "Triggered" now share ONE date (the trigger pivot: L3 for a long, H3 for a short),
  // so they're always in sync — Days since = today minus the date shown in the Triggered column.
  const _trigref=(r.direction==="BULL"?r.l3_date:r.h3_date)||r.l3_date||r.h3_date;
  r.days_since=daysSince(_trigref);
  r.trig_date=(r.status==="TRIGGERED")?_trigref:null;
  r.added=_trigref?String(_trigref).slice(0,10):null;   // when the setup completed (joined the dataset)
  // Expected time-to-target = the squeeze's H1->H3 formation span (same heuristic as the Slack report).
  if(r.h1_date&&r.h3_date){const _sp=Math.round((Date.parse(r.h3_date)-Date.parse(r.h1_date))/864e5);
    r.tgt_months=_sp>0? +(_sp/30.44).toFixed(1):null;
    r.tgt_str=_sp>0?(_sp<63?`~${Math.max(1,Math.round(_sp/7))}wk`:`~${Math.round(_sp/30.44)}mo`):null;
  } else {r.tgt_months=null;r.tgt_str=null;}
  r.source="Scan "+((r.timeframe||"").replace("daily-","D")||"?");   // what put it in the dataset
  r.open_pos=POS[r.ticker]??POS[disp(r.ticker)]??0;
  r.x_posts=PUB[r.ticker]??PUB[disp(r.ticker)]??0;
}

let TRADE_HIDE={};   // per-user Trading (Squeeze) allow-lists {directions,markets,locations}; empty list = no restriction (user 2026-07-06)
let MARKETS_DISABLED=new Set(), MARKETS_OFF=new Set();   // app-level (admin) + per-user market on/off switches (user 2026-07-11)
// Favourites (user 2026-07-10) — a ★ column on Scanner / My Pre-orders / Pre-orders to my IG, kept in
// this browser (localStorage), keyed by the displayed ticker.
let FAVS=new Set(); try{FAVS=new Set(JSON.parse(localStorage.getItem('sq_favs')||'[]'));}catch(e){}
function toggleFav(tk){tk=disp(tk); FAVS.has(tk)?FAVS.delete(tk):FAVS.add(tk);
  localStorage.setItem('sq_favs',JSON.stringify([...FAVS]));
  if(typeof render==='function')render(); if(typeof renderPreorders==='function')renderPreorders(); if(typeof paintOrderOps==='function')paintOrderOps();}
const _favCell=tk=>{const on=FAVS.has(disp(tk));
  return `<td style="cursor:pointer;text-align:center" onclick="event.stopPropagation();toggleFav('${(tk||'').replace(/'/g,'')}')" title="${on?'Remove from favourites':'Add to favourites'}"><span style="color:${on?'#e3b341':'var(--muted)'};font-size:15px">${on?'★':'☆'}</span></td>`;};
function tradeVisible(r){
  const ok=(v,key)=>{const a=TRADE_HIDE[key];if(!a||!a.length)return true;if(v==null)return true;return a.includes(v);};
  // Market is governed by the Markets (User) on/off switch (markets_off) + admin markets_disabled — this
  // now gates BOTH visibility and trading (user 2026-08-01). Trading (Squeeze) keeps only direction/location.
  if(r.market && (MARKETS_DISABLED.has(r.market)||MARKETS_OFF.has(r.market)))return false;
  return ok(r.direction,"directions")&&ok(r.location,"locations");
}
// `except` (optional) names ONE chart dimension to skip — used for chart "brushing" (user 2026-07-26):
// each chart is counted over rows that pass every OTHER filter but not its own, so all option-bars stay
// visible with the selected one(s) highlighted, rather than the strip collapsing to only the picked value
// (P-03 L31 standard). Undefined (the table's call) applies every filter, exactly as before.
// Which values of ONE dimension the user's own Trading filters exclude -- asked of tradeVisible itself
// rather than re-derived from MARKETS_OFF / TRADE_HIDE.
//
// WHY THIS EXISTS. A Best Settings card said "All markets" while Shanghai was switched off (user
// 2026-08-30). The label and the filter were two separate implementations of one fact, so nothing could
// notice them drifting apart. The first fix re-read MARKETS_OFF and MARKETS_DISABLED -- correct, but
// still a SECOND implementation, and the same shape that caused the bug. This asks the one function that
// actually decides.
//
// A probe row carries ONLY the dimension under test. tradeVisible treats a null field as "no opinion",
// so its other rules pass the probe and the answer isolates that dimension. Any rule later added to
// tradeVisible is therefore picked up here automatically, which is the whole point.
function tradeExcludedValues(dim,values){
  return [...new Set(values||[])].filter(v=>v!=null&&v!=="")
    .filter(v=>!tradeVisible({[dim]:v})).sort();
}
// "All locations" carried the SAME defect the market scope did: tradeVisible gates location through
// TRADE_HIDE.locations and the Back Test summary never asked. Its sibling marketScope() handled the
// market case correctly all along, which is exactly how one dimension drifts from another.
function _locScopeLabel(){
  const off=tradeExcludedValues("location",typeof uniq==='function'?uniq("location"):[]);
  return off.length?`All enabled locations (${off.length} off)`:"All locations";
}
// Everything currently narrowing the Scanner table, in plain words.
//
// WHY (user 2026-08-30: "i can see only 4 items in the table but items in sector card suggest there
// should be more"): the chart bars are deliberately BRUSHED -- each bar is counted over rows passing
// every filter EXCEPT its own dimension -- so selecting one sector leaves every other sector's bar at
// full height while the table narrows to the one clicked. That is the house standard (P-03 L31) and the
// counts are not wrong: MEASURED with no filters against the live 1,773-record snapshot, the table and
// the sector bars agree exactly at 346 each. What was missing is the page saying a filter is ON, so a
// short table looked like it had lost rows. "Squeeze only" is listed too, because it is on by default
// and is what takes 1,773 scanned down to a few hundred.
function _activeScannerFilters(){
  const nice={mf_sector:"Sector",mf_market:"Market",mf_direction:"Direction",
              mf_location:"Location",mf_status:"Status",mf_timeframe:"Timeframe"};
  const out=[];
  Object.keys(nice).forEach(id=>{const s=setOf(id);if(s&&s.size)out.push(`${nice[id]}: ${[...s].join(", ")}`);});
  const q=(($("f_search")||{}).value||"").trim();
  if(q)out.push(`Search: "${q}"`);
  if(signalOnly)out.push("Squeeze only");
  return out;
}
function pass(r,except){
  const _q=($("f_search").value||"").trim().toLowerCase();
  if(_q && !((r.ticker||"").toLowerCase().includes(_q) || (r.name||"").toLowerCase().includes(_q)))return false;
  if(signalOnly && r.has_signal===false)return false;
  if(!tradeVisible(r))return false;   // per-user Trading (Squeeze) market/direction/location filter (user 2026-07-06)
  const g=id=>$(id).value;
  // Only the CHART multi-select sets (mf_*) gate these fields now. dm()/sel(), which combined a sidebar
  // dropdown with the chart set, went with the sidebar to Squeeze History on 2026-08-16 and were left
  // uncalled; removed rather than kept as dead code implying a filter that no longer exists.
  // "—" is the missing-value bucket (no-signal rows have null direction/status/etc.).
  // except===true skips ALL chart-dimension filters (keeps search / signalOnly / numeric ranges) — used to
  // build the chart-selection-INDEPENDENT seed base so brushed charts keep a constant bar set (P-04 #65).
  const _all=(except===true);
  if(!_all && except!=="direction" && !inSet("mf_direction",r.direction||"—"))return false;
  if(!_all && except!=="location"  && !inSet("mf_location",r.location||"—"))return false;
  if(!_all && except!=="market"    && !inSet("mf_market",r.market||"—"))return false;
  if(!_all && except!=="sector"    && !inSet("mf_sector",r.sector||"—"))return false;
  if(!_all && except!=="status"    && !inSet("mf_status",r.status||"—"))return false;
  if(!_all && except!=="timeframe" && !inSet("mf_timeframe",r.timeframe||"—"))return false;
  // The numeric range filters (quality, R:R, dist-to-entry, days-since, RVOL, P/E, insider %) moved to
  // Squeeze History on 2026-08-16 along with their controls. Reading a removed element here would throw
  // on every row. The equivalents there are in SQH_RANGES, with the same null-passes rule.
  // Personal "My Trading Filters" floors (user 2026-08-11: "the scanner report MUST also match the user
  // trading filter settings") — HARD filter (user's explicit choice, not just a visual flag): a setup you
  // couldn't pin or place yourself (My Pre-orders / place-order, per Configuration → My Trading Filters)
  // is hidden from the Scanner Report table too, not just excluded from My Pre-orders. Same fields/logic
  // as renderPreorders() below so the two views can never silently disagree. Logged-out/no-limits-set
  // users have MY_LIMITS={}, so every num() call below is null and this is a no-op for them.
  {const rrMin=num(MY_LIMITS.min_risk_reward), qMin=num(MY_LIMITS.min_quality), vsMin=num(MY_LIMITS.min_volume_score), rvMin=num(MY_LIMITS.min_rvol);
   const ivMin=num(MY_LIMITS.min_instrument_value), ivMax=num(MY_LIMITS.max_instrument_value);
   if(rrMin!=null&&r.rr!=null&&r.rr<rrMin)return false;
   if(qMin!=null&&r.quality!=null&&r.quality<qMin)return false;
   if(vsMin!=null&&r.volume_score!=null&&r.volume_score<vsMin)return false;
   if(rvMin!=null&&rvMin>0&&r.rvol!=null&&r.rvol<rvMin)return false;
   if(+MY_LIMITS.require_above_vwap&&r.above_vwap===false)return false;
   if(+MY_LIMITS.require_atr_expanding&&r.atr_expanding===false)return false;
   if(r.mcap!=null&&ivMin!=null&&ivMin>0&&r.mcap<ivMin)return false;
   if(r.mcap!=null&&ivMax!=null&&ivMax>0&&r.mcap>ivMax)return false;}
  return true;
}

// ── Multi-select chart filters (user 2026-07-03): a filter input holds a SET of selected values,
// so clicking several bars (e.g. DELETED + PENDING) keeps them all. SEP is a control char that never
// appears in a value. Chart-only filter inputs are hidden <input>s; the Scanner uses mf_* hidden
// inputs alongside its single-select sidebar dropdowns.
const SEP="~|~";
function setOf(id){const el=$(id);if(!el)return null;const v=el.value||"";return v?new Set(v.split(SEP)):null;}
function inSet(id,val){const s=setOf(id);return !s||s.has(val==null||val===""?"—":String(val));}
function toggleIn(id,val){const el=$(id);if(!el)return;
  if(val===""){el.value="";return;}                       // clear
  const s=new Set((el.value||"").split(SEP).filter(Boolean));const k=String(val);
  s.has(k)?s.delete(k):s.add(k); el.value=[...s].join(SEP);}
// Date-range filter (user 2026-07-03): each dated table has <prefix>-from / <prefix>-to date inputs.
function dateActive(pfx){return !!((($(pfx+"-from")||{}).value)||(($(pfx+"-to")||{}).value));}
function applyDateFilter(pfx,rows,getTs){
  const from=(($(pfx+"-from")||{}).value)||null, to=(($(pfx+"-to")||{}).value)||null;
  if(!from&&!to)return rows;
  return rows.filter(r=>{const d=(getTs(r)||"").slice(0,10); if(!d)return true;
    if(from&&d<from)return false; if(to&&d>to)return false; return true;});
}
function dateFilterBar(pfx,painter){
  return `<label class="muted" style="font-size:12px">From <input type="date" id="${pfx}-from" onchange="${painter}" style="width:auto"></label>`+
         `<label class="muted" style="font-size:12px">To <input type="date" id="${pfx}-to" onchange="${painter}" style="width:auto"></label>`;
}
function barChart(title,counts,fk,colorFn,labelDesc,opts){
  const sel=setOf(fk);
  // A caller that owns its OWN filter state (the Transaction evidence panel keeps its selection in
  // opts.filters, not in a hidden input) passes selectedValue + onclickFor instead of relying on the
  // shared data-fk dispatcher. Added 2026-08-30 so that panel could stop hand-rolling its own bars:
  // it had twelve cards of bespoke markup sitting above its table, which is how they came to look
  // nothing like the rest of the site. One bar implementation, two ways of hooking a click.
  const selValue=(opts&&opts.selectedValue!==undefined)?String(opts.selectedValue||""):null;
  const onclickFor=opts&&opts.onclickFor;
  const nsel=selValue!==null?(selValue?1:0):(sel?sel.size:0);
  // opts.metric (user 2026-07-26, P-05 L281): a {key: avgReturn%} map. When present the bars are ORDERED
  // by avg return desc and TINTED green (positive) / red (negative), intensity scaled to the biggest |avg|
  // shown. Bar LENGTH still encodes trade count, so length = how many, colour = how good. Used by the
  // Results Market & Location charts. Absent → the original count-order + colorFn behaviour, unchanged.
  const metric=opts&&opts.metric, profitMode=!!(opts&&opts.profit);
  let entries=Object.entries(counts);
  entries.sort(profitMode?((a,b)=>b[1]-a[1]):metric?((a,b)=>((metric[b[0]]==null?-1e9:metric[b[0]])-(metric[a[0]]==null?-1e9:metric[a[0]])))
    :labelDesc?((a,b)=>String(b[0]).localeCompare(String(a[0]))):((a,b)=>b[1]-a[1]));
  entries=entries.slice(0,8);
  const max=Math.max(1,...entries.map(e=>profitMode?Math.abs(e[1]):e[1]));
  const mmax=metric?Math.max(1,...entries.map(e=>Math.abs(metric[e[0]]||0))):0;
  const mBg=v=>`rgba(${v>=0?'34,163,74':'220,38,38'},${(Math.min(Math.abs(v)/mmax,1)*0.7+0.18).toFixed(2)})`;
  // The bar scales to the CARD, not to a fixed 72px (user 2026-07-17, P-16a). The old hardcoded ceiling
  // meant a wide card (Market/Location run ~246px since the cards started claiming spare width) drew a
  // stubby 72px bar and left the rest of the row empty — wasted space INSIDE the card. A .track flexes
  // into whatever width is going and the fill takes its share as a %.
  // EVERY BAR IN A CHART STARTS AT THE SAME X (user 2026-09-05: "make sure the left side of the bar are
  // all aligned"). .bar .tk is width:auto, so each label sized to its own text and pushed its track to a
  // different offset -- "United States" started its bar further right than "FX". The label column is now
  // sized once per chart, to that chart's longest label, so the tracks line up without forcing a global
  // width that would either truncate long sector names or waste half a narrow card on short ones.
  //
  // Done in JS rather than CSS because flex rows cannot share a column width between siblings, and the
  // grid alternative needs display:contents on .bar -- which has no box, breaking both the click target
  // and the packViz height measurement this page relies on.
  const _lab=Math.min(22,Math.max(6,...entries.map(e=>String(e[0]).length)));
  const rows=entries.map(([k,n])=>{const on=selValue!==null?String(k)===selValue:!!(sel&&sel.has(String(k)));
    const mv=metric?metric[k]:null;
    const bg=profitMode?(n>=0?'var(--bull)':'var(--bear)'):metric?(mv==null?'var(--muted)':mBg(mv)):(colorFn?colorFn(k):'var(--accent)');
    const pLabel=profitMode?`${n>=0?'+':'−'}${Math.abs(n).toLocaleString(undefined,{maximumFractionDigits:2})}${opts.currency?' '+opts.currency:''}`:'';
    const tip=profitMode?`${k}: ${pLabel} profit — click to filter`:metric?`${k}: avg return ${mv==null?'—':(mv>0?'+':'')+mv.toFixed(1)+'%'} · ${n} trade${n===1?'':'s'} — click to filter`:`click to filter ${k}`;
    const hook=onclickFor?` onclick="${onclickFor(k)}"`:` data-fk="${fk}" data-fv="${k}"`;
    return `<div class="bar clk${on?' active':''}"${hook} title="${tip}"><span class="tk" style="flex:0 0 ${_lab}ch"><span class="selmk">${on?'●':''}</span>${k}</span>
    <span class="track"><span class="fill" style="width:${Math.max(2,Math.round((profitMode?Math.abs(n):n)/max*100))}%;background:${bg};opacity:${nsel&&!on?0.4:1}"></span></span><span class="n">${profitMode?pLabel:n}</span></div>`;}).join("");
  return `<div class="vizbox${nsel?' filtered':''}"><h5>${title}${nsel?` <span class="afilt clk"${onclickFor?` onclick="${opts.clearOnclick||''}"`:` data-fk="${fk}" data-fv=""`} title="clear filter">▶ ${nsel} ✕</span>`:''}</h5><div class="bars">${rows}</div></div>`;
}
function pieChart(title,counts,fk){
  const sel=setOf(fk), nsel=sel?sel.size:0, has=k=>sel&&sel.has(String(k));
  const entries=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  const total=entries.reduce((s,e)=>s+e[1],0)||1;
  const cols=["#58a6ff","#3fb950","#f85149","#d29922","#a371f7","#db61a2","#e3b341","#79c0ff","#56d364","#ff7b72"];
  let a=0,seg="";
  entries.forEach(([k,n],i)=>{const frac=n/total,a2=a+frac*2*Math.PI;const on=has(k);
    const x1=20+18*Math.cos(a-Math.PI/2),y1=20+18*Math.sin(a-Math.PI/2);
    const x2=20+18*Math.cos(a2-Math.PI/2),y2=20+18*Math.sin(a2-Math.PI/2);
    seg+=`<path class="clk" data-fk="${fk}" data-fv="${k}" d="M20,20 L${x1.toFixed(2)},${y1.toFixed(2)} A18,18 0 ${frac>0.5?1:0},1 ${x2.toFixed(2)},${y2.toFixed(2)} Z" fill="${cols[i%cols.length]}" stroke="${on?'var(--fg)':'none'}" stroke-width="${on?1.5:0}" opacity="${nsel&&!on?0.4:1}"><title>click to filter ${k}</title></path>`;a=a2;});
  const legend=entries.slice(0,8).map(([k,n],i)=>{const on=has(k);
    return `<div class="bar clk${on?' active':''}" data-fk="${fk}" data-fv="${k}"><span class="fill" style="width:10px;background:${cols[i%cols.length]}"></span><span class="tk" style="text-align:left;width:auto" title="click to filter ${k}"><span class="selmk">${on?'●':''}</span>${k}</span><span class="n">${n}</span></div>`;}).join("");
  return `<div class="vizbox${nsel?' filtered':''}"><h5>${title}${nsel?` <span class="afilt clk" data-fk="${fk}" data-fv="" title="clear filter">▶ ${nsel} ✕</span>`:''}</h5><div style="display:flex;gap:8px;align-items:center">
    <svg viewBox="0 0 40 40" width="86" height="86">${seg}</svg><div class="bars" style="min-width:120px">${legend}</div></div></div>`;
}
// ── P-15: stack short charts into a shared column (user 2026-07-17) ─────────────────────────────────
// "If charts next to each other total less than the tallest, stack them over each other." Purely a
// MEASURED decision — never hardcode which charts pair up. Logged out, LIMITED strips market/location/
// timeframe so each collapses to a single "—" bar and stacking is obviously right; logged in the same
// charts carry 6-8 bars and must stay side by side. Same code has to serve both.
//
// Order is preserved: cards are packed left-to-right, so P-12a (Market+Sector lead) and P-11a (Ticker
// far right) still hold — a stacked column simply occupies the slot its first card would have had.
const VIZ_GAP=10;
// Cards that must NEVER be stacked — always standalone (user 2026-07-27, P-06): the primary Location,
// Market and Sector charts. The naive height-only packer would tuck a short Location under another card
// ("Direction under Location") on some data; these three stay side-by-side as their own cards, matching
// the Performance → Results strip (the gold standard). Matched on the card's <h5> header (the winners
// strip suffixes "— net £", so compare the part before the em dash).
const VIZ_NOSTACK=new Set(["Location","Market","Sector"]);
const _vizLabel=el=>{const box=el.classList.contains("vizsector")?el.firstElementChild:el;
  const hh=box&&box.querySelector("h5"); if(!hh)return"";
  // Cut at the em-dash ("Location — net £" on the winners strip) OR the "▶ N ✕" clear-badge that a
  // filtered chart appends — so the pin holds regardless of the winners suffix or the active-filter state.
  return hh.textContent.split(/[—▶]/)[0].trim();};
function packViz(id,_retry){
  const c=document.getElementById(id); if(!c) return;
  // Undo any previous packing so this is idempotent across re-renders.
  c.querySelectorAll(".vizcol").forEach(col=>{
    while(col.firstChild) col.parentNode.insertBefore(col.firstChild, col);
    col.remove();});
  // Flex items are .vizsector wrappers and (via .vizbars display:contents) bare .vizbox cards.
  const flat=()=>[...c.children].flatMap(ch=>ch.classList.contains("vizbars")?[...ch.children]:[ch]);
  const items=flat();
  if(items.length<3) return;
  // Visual ROW count of the current (unstacked) strip. Skips zero-rect display:contents wrappers; a hidden
  // view (the file:// snapshot) reports top 0 for everything → one row → no stacking, the safe default.
  const rowCount=()=>{const t=flat().map(el=>el.getBoundingClientRect()).filter(r=>r.height>0).map(r=>Math.round(r.top));
    return t.length?new Set(t.map(v=>Math.round(v/8))).size:1;};
  // ONLY stack when the strip WRAPS (user 2026-07-27, P-06). If every card already fits on ONE row, leave
  // them side by side — align-items:stretch + the space-evenly bars fill each card cleanly and the row
  // spaces evenly, matching Performance → Results (the gold standard). Stacking a short card UNDER another
  // only pays off to reclaim vertical space once the row has wrapped; doing it while there is horizontal
  // room is exactly the "inconsistent spacing / big white gaps" the user reported on Scanner + My
  // Pre-orders. Measured, never hardcoded.
  if(rowCount()<=1) return;
  // Wrapped. First try dropping the Month-Week card — the most disposable date chart — if that alone
  // un-wraps the strip (user 2026-07-27, P-06). Re-pack once after removing it.
  if(!_retry){
    const mw=items.find(el=>_vizLabel(el)==="Month-Week");
    if(mw){ (mw.closest(".vizsector")||mw).remove(); return packViz(id,true); }
  }
  if(rowCount()<=1) return;   // dropping Month-Week was enough
  // Still wrapped: consolidate SHORT cards into shared columns to reduce ragged height. Pin Location/
  // Market/Sector (never stacked). Greedy left-to-right, preserving order.
  c.classList.add("measuring");                       // natural heights, not the stretched ones
  const its=flat(), h=its.map(el=>el.getBoundingClientRect().height);
  c.classList.remove("measuring");
  const tall=Math.max(...h); if(!tall) return;
  const pinned=its.map(el=>VIZ_NOSTACK.has(_vizLabel(el)));   // Location/Market/Sector — never stacked
  const used=new Array(its.length).fill(false);
  for(let i=0;i<its.length;i++){
    if(used[i]||pinned[i]||h[i]>tall*0.6) continue;   // tall or pinned cards are never stacked
    let stack=[i], total=h[i];
    for(let j=i+1;j<its.length;j++){
      if(used[j]||pinned[j]||h[j]>tall*0.6) continue;
      if(total+VIZ_GAP+h[j]<=tall){ stack.push(j); total+=VIZ_GAP+h[j]; }
    }
    if(stack.length<2) continue;                      // nothing gained
    const col=document.createElement("div"); col.className="vizcol";
    its[stack[0]].parentNode.insertBefore(col, its[stack[0]]);
    stack.forEach(k=>{ used[k]=true;
      const el=its[k], box=el.classList.contains("vizsector")?el.firstElementChild:el;
      col.appendChild(box); if(el!==box) el.remove(); });
  }
}
function renderViz(rows){
  // Brushing (user 2026-07-26, P-03 L31): each chart counts over rows that pass every OTHER filter but
  // not its own (pass(r,field)), so ALL option-bars stay visible with the selected value(s) highlighted —
  // the strip no longer collapses to just the picked option (which read as "data filtered, nothing
  // selected"). `field` is both the row property counted and the dimension skipped in pass().
  // Seed EVERY option value (count 0 if none) from the chart-selection-INDEPENDENT base (all non-chart
  // filters applied, all six chart dims skipped) so a selection never drops a bar — the bar SET stays
  // constant, packViz packs identically, and card size/position don't change on selection (P-04 #65 /
  // house L31+L33). Then count the brushed subset on top.
  const by=(field)=>{const seen={};
    DATA.forEach(r=>{if(!pass(r,true))return;const v=r[field]||"—";if(!(v in seen))seen[v]=0;});
    DATA.forEach(r=>{if(!pass(r,field))return;const v=r[field]||"—";seen[v]++;});
    return seen;};
  // Bars wrap among themselves in the top row; the Sector pie gets its own row so nothing ever
  // stacks directly above or below it (user 2026-06-28).
  // Sector pie is its OWN left column; the bar charts wrap among themselves to the right — so the
  // sector chart never sits below (or above) another chart (user 2026-06-28).
  // Sector pie and the Market chart each get their OWN column — nothing may stack above or below
  // the Market chart on any page (user 2026-07-03: it will have many entries).
  // Scanner charts use mf_* multi-select inputs (user 2026-07-03) — separate from the single-select
  // sidebar dropdowns; pass() honours both.
  // Chart order (user 2026-07-18, P-01/P-02): Market leftmost, Location right of Market, Sector right of
  // Location — the far-left trio — then the compact bars. Supersedes the 2026-07-17 Market+Sector pairing
  // (P-01 now puts Location between them). Matches the Performance tab. .vizbars is display:contents, so
  // DOM order IS left-to-right order.
  $("viz").innerHTML=
    `<div class="vizsector">`+barChart("Location",by("location"),"mf_location")+`</div>`+   /* Location, Market, Sector on LHS (user 2026-07-24/25, P-03 L29 / P-05 L182 — supersedes the earlier P-12a "Market first") */
    `<div class="vizsector">`+barChart("Market",by("market"),"mf_market")+`</div>`+
    `<div class="vizsector">`+pieChart("Sector",by("sector"),"mf_sector")+`</div>`+
    `<div class="vizbars">`+
      barChart("Direction",by("direction"),"mf_direction",k=>k==="BULL"?"var(--bull)":"var(--bear)")+
      barChart("Status",by("status"),"mf_status",k=>k==="TRIGGERED"?"var(--bull)":k==="READY"?"var(--accent)":"#d29922")+
      barChart("Timeframe",by("timeframe"),"mf_timeframe")+
    `</div>`;
  packViz("viz");   // P-15
}

function render(){
  const _sv=r=>sortK==="_fav"?(FAVS.has(disp(r.ticker))?1:0):sortK==="dist_entry"?(r.dist_entry==null?1e9:Math.abs(r.dist_entry)):r[sortK];   // ★ sortable; Dist→Entry sorts by |distance| (user 2026-07-11)
  const rows=DATA.filter(pass).sort((a,b)=>{
    const x=_sv(a),y=_sv(b);const av=x==null?-1e9:x,bv=y==null?-1e9:y;
    return (av<bv?-1:av>bv?1:0)*sortDir;});
  const _nT=rows.filter(r=>r.status==="TRIGGERED").length,_nR=rows.filter(r=>r.status==="READY").length,_nD=rows.filter(r=>r.status==="DEVELOPING").length;
  const _af=_activeScannerFilters();
  $("count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> instruments visible <span class="muted">of ${DATA.length} scanned (${_nT} TRIGGERED · ${_nR} READY · ${_nD} DEVELOPING). Click a row to open its detail; use Filters or the charts to narrow.</span>`+(_af.length?`<div style="font-size:12px;margin-top:3px">Narrowed by <b>${_af.map(_esc).join("</b> · <b>")}</b></div>`:"")+(LIMITED?` <b style="color:#d29922">· <a href="#" onclick="showLogin();return false" style="color:#d29922;text-decoration:underline">log in to unlock the full data</a></b>`:"");
  $("sctab-count").textContent=`(${rows.length})`;
  renderViz(rows);
  $("rows").innerHTML=rows.map(r=>`<tr data-t="${r.ticker}" class="${SEL===r.ticker?'sel':''}">
    ${_favCell(r.ticker)}<td>${nm40(r.name)}</td>
    <td>${r.direction?`<span class="tag ${r.direction==='BULL'?'bull':'bear'}">${r.direction}</span>`:''}</td>
    <td>${ob(_mcapFmt(r.mcap))}</td>
    <td>${ob(rvolScannerCell(r))}</td><td>${_tickCross(r.above_vwap)}</td><td>${_tickCross(r.atr_expanding)}</td><td>${ob(volScoreCell(r.volume_score))}</td>
    <td>${ob(r.rr!=null?r.rr.toFixed(1):'')}</td><td>${ob(r.quality!=null?`<b style="color:${qcol(r.quality)}">${r.quality}</b>`:'')}</td>
    <td>${ob(r.dist_entry!=null?(r.dist_entry>0?'+':'')+r.dist_entry+'%':'')}</td><td>${ob(r.status||'')}</td>
    <td>${ob(r.trig_date?r.trig_date.slice(0,10):'')}</td><td>${ob(r.days_since??'')}</td>
    <td>${ob(f2(r.current_price))}</td>
    <td>${ob(r.tgt_str||'')}</td>
    <td>${r.market||''}</td><td>${ob(r.x_posts?`<b>${r.x_posts}</b>`:'')}</td>
    <td>${ob(r.chg_since_trig!=null?`<b style="color:${trigCol(r)}">${r.chg_since_trig>0?'+':''}${r.chg_since_trig}%</b>`:'')}</td>
    <td>${ob(r.pe??'')}</td><td>${ob(r.insider_pct!=null?r.insider_pct.toFixed(1)+'%':'')}</td><td>${ob((r.timeframe||'').replace('daily-','D'))}</td><td>${ob(locName(r.location))}</td><td>${ob(levOf(r)?levOf(r)+'x':'')}</td>
    <td>${r.sector||''}</td><td><b>${disp(r.ticker)}</b></td><td>${AUTH&&isPreorder(r)?'<span style="color:var(--bull)" title="In your My Pre-orders">✓</span>':''}</td></tr>`).join("")
    || `<tr><td colspan="27" class="empty">No setups match the filters.</td></tr>`;
  document.querySelectorAll("#rows tr[data-t]").forEach(tr=>tr.onclick=()=>{
    if(LIMITED){showTab("scanner");$("loginpanel").classList.remove("hidden");$("view-scanner").classList.add("hidden");return;}
    tr.dataset.t===SEL?closeDetail():showDetail(tr.dataset.t);});
  _sortArrows("data-k",sortK,sortDir);   // show the sort arrow (incl. the default Dist→Entry, user 2026-07-11)
}

// The instrument detail lives inside the Scanner view, so opening one from another tab has to jump to
// Scanner first. Closing it then stranded you there (user 2026-07-17, P-29: "clicking on instrument name
// within performance works but on closing the detail, it goes to Scanner tab"). openDetailFrom() records
// the origin tab and closeDetail() returns to it.
let DETAIL_FROM=null;
function openDetailFrom(tab,t){DETAIL_FROM=tab;showTab("scanner");showDetail(t);}
function closeDetail(){
  SEL=null;$("detail").classList.add("hidden");
  const back=DETAIL_FROM; DETAIL_FROM=null;
  if(back&&back!=="scanner"&&tabAllowed(back)){showTab(back);return;}   // back where we came from
  render();
}

function showDetail(t){
  SEL=t; render();
  const r=DATA.find(x=>x.ticker===t); if(!r){return;}
  const days=PRICE_CHART_DAYS;
  let broker="<span class='muted'>no broker data</span>";
  if(r.broker){const b=r.broker;let tr="";
    if(b.trend&&b.trend.length===3){tr=` · buys ${b.trend[0]}→${b.trend[1]} (${b.trend[1]>b.trend[0]?'strengthening':'cooling'}) over ~3mo`;}
    broker=`${b.buys??'?'} Buy / ${b.holds??'?'} Hold of ${b.rated??'?'} rated${tr}`;}
  $("detail").classList.remove("hidden");
  $("detail").innerHTML=`
    <button class="btn" onclick="closeDetail()" style="float:right">✕ Close (back to list)</button>
    ${AUTH&&r.has_signal?`<button class="btn" onclick="pushToPreorder('${r.ticker}')" style="float:right;margin-right:8px">${(PINNED.has(r.ticker)||PINNED.has(disp(r.ticker)))?'✓ In Pre-orders':'➕ Push to Pre-orders'}</button>`:''}
    <h2>${disp(r.ticker)} <span class="muted" style="font-size:13px">${r.name||''}</span> <span class="tag ${r.direction==='BULL'?'bull':'bear'}">${r.direction}</span></h2>
    <div class="sub">${locName(r.location)} · ${r.market||''} · ${r.sector||''} · ${r.status||''} · Q${r.quality??'?'} · R:R ${r.rr!=null?r.rr.toFixed(2):'?'} · ${(r.timeframe||'').replace('daily-','D')}</div>
    ${IS_ADMIN?`<div class="card"><h4>Squeeze Rules 1–5 — justification</h4><div id="rulesbox" class="sqh-loading">⏳ Data loading…</div></div>`:''}
    <div class="card"><h4>Quality (${r.quality??'?'}/100) — how it's scored</h4>
      <div class="muted" style="font-size:12px">Q combines three things: <b>tightness</b> (how compressed the squeeze is vs its first amplitude — up to 50 pts, tighter = higher), <b>freshness</b> (how recently the 3rd high formed — up to 30 pts, more recent = higher), and <b>symmetry</b> (how evenly the swings are spaced — up to 20 pts). 100 = a very tight, fresh, symmetric squeeze; a low Q means a loose, older, or lopsided one.</div></div>
    ${AUTH?`<div class="card"><h4>VolumeScore — breakout confirmation (0–12)</h4><div id="volscorebox" class="sqh-loading">⏳ Data loading…</div></div>`:''}
    <div class="card"><h4>Price — last ${days} days (filter-reactive)</h4>
      <img id="pw" src="/api/pricewin/${r.ticker}?days=${days}&theme=${document.documentElement.classList.contains('light')?'light':'dark'}"></div>
    <div class="card"><h4>X post card</h4><img loading="lazy" src="/api/card/${r.ticker}"></div>
    <div class="card"><h4>Levels</h4>
      <div class="kv"><span>Now</span><b>${f2(r.current_price)}</b></div>
      <div class="kv"><span>Entry</span><b>${f2(r.entry)}</b></div>
      <div class="kv"><span>Stop</span><b>${f2(r.stop)}</b></div>
      <div class="kv"><span>Target</span><b>${f2(r.target)}</b></div>
      <div class="kv"><span>Dist to entry</span><b>${r.dist_entry!=null?(r.dist_entry>0?'+':'')+r.dist_entry+'%':'?'}</b></div>
      ${r.pe!=null?`<div class="kv"><span>P/E</span><b>${r.pe}</b></div>`:''}
      ${r.insider_pct!=null?`<div class="kv"><span>Insider %</span><b>${r.insider_pct.toFixed(2)}%</b></div>`:''}</div>
    <div class="card"><h4>Fundamentals — key metrics</h4><div id="fundbox" class="sqh-loading">⏳ Data loading…</div></div>
    <div class="card" id="brokercard" style="${r.broker?'':'display:none'}"><h4>Broker analysis</h4><span id="brokerbase">${broker}</span><div id="brokerchg" class="muted sqh-loading" style="margin-top:6px">⏳ Data loading…</div></div>
    <div class="card"><h4>Relevant X visuals</h4><div id="xvisuals" class="xvisual-grid sqh-loading">⏳ Data loading…</div></div>
    <div class="card"><h4>On X</h4><div id="xlinks" class="sqh-loading">⏳ Data loading…</div></div>
    <div class="card"><h4>X thread (all pages)</h4><div class="tweet sqh-loading" id="tweetbox">⏳ Data loading…</div></div>`;
  fetch(`/api/thread/${r.ticker}`).then(x=>x.json()).then(j=>{
    const el=document.getElementById("tweetbox"); if(!el||SEL!==r.ticker)return;
    el.classList.remove("sqh-loading");
    const parts=j.parts||[]; if(!parts.length){el.textContent="(no thread — not a publishable setup)";return;}
    el.innerHTML=parts.map((p,i)=>`<div style="padding:6px 0;border-top:${i?'1px solid var(--line)':'none'}">${(p||'').replace(/</g,'&lt;').replace(/(https?:\/\/\S+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>')}</div>`).join("");
  }).catch(()=>{const el=document.getElementById("tweetbox");if(el){el.classList.remove("sqh-loading");el.textContent="Thread unavailable.";}});
  fetch(`/api/fundamentals/${r.ticker}`).then(x=>x.json()).then(j=>{
    const el=document.getElementById("fundbox"); if(!el||SEL!==r.ticker)return;
    el.classList.remove("sqh-loading");
    const k=j.kpis||{}, sym=({GBp:"£",GBP:"£",USD:"$",EUR:"€"})[j.currency]||"";
    const money=v=>{if(v==null)return null;const a=Math.abs(v),s=v<0?"-":"";
      return a>=1e9?`${s}${sym}${(a/1e9).toFixed(2)}bn`:a>=1e6?`${s}${sym}${(a/1e6).toFixed(1)}m`:a>=1e3?`${s}${sym}${(a/1e3).toFixed(0)}k`:`${s}${sym}${a.toFixed(0)}`;};
    const pct=v=>v==null?null:(v*100).toFixed(1)+"%", rat=v=>v==null?null:(+v).toFixed(2), cash=v=>v==null?null:sym+rat(v);
    // Colour-code anything concerning/bad (user 2026-06-28): red = poor, amber = watch, green = strong.
    const C={r:"var(--bear)",a:"#d29922",g:"var(--bull)"};
    const grade={
      trailingPE:v=>v<0?'r':v>80?'r':v>40?'a':null, forwardPE:v=>v<0?'r':v>80?'r':v>40?'a':null,
      pegRatio:v=>v<0?'a':v>3?'r':v>2?'a':(v>0&&v<1?'g':null),
      priceToBook:v=>v<0?'r':v>10?'a':null, priceToSales:v=>v>20?'a':null, evToEbitda:v=>v<0?'r':v>20?'a':null,
      trailingEps:v=>v<0?'r':null, forwardEps:v=>v<0?'a':null,
      dividendYield:v=>v>0.10?'a':null, payoutRatio:v=>v<0?'a':v>1?'r':v>0.8?'a':null,
      freeCashflow:v=>v<0?'r':null, operatingCashflow:v=>v<0?'a':null,
      grossMargin:v=>v<0?'r':null, operatingMargin:v=>v<0?'r':v<0.05?'a':v>0.2?'g':null, profitMargin:v=>v<0?'r':v<0.05?'a':v>0.2?'g':null,
      roe:v=>v<0?'r':v>0.15?'g':null, roa:v=>v<0?'a':null,
      revenueGrowth:v=>v<-0.10?'r':v<0?'a':v>0.10?'g':null, earningsGrowth:v=>v<-0.20?'r':v<0?'a':v>0.15?'g':null,
      debtToEquity:v=>v>200?'r':v>100?'a':null, currentRatio:v=>v<1?'r':v<1.5?'a':v>2?'g':null, quickRatio:v=>v<1?'a':null,
      beta:v=>v>2?'a':null,
    };
    const col=key=>{if(!key||k[key]==null||!grade[key])return"var(--fg)";const g=grade[key](k[key]);return g?C[g]:"var(--fg)";};
    const rows=[
      ["Market cap",money(k.marketCap)],["Revenue (ttm)",money(k.totalRevenue)],["EBITDA",money(k.ebitda)],
      ["P/E (trailing)",rat(k.trailingPE),"trailingPE"],["P/E (forward)",rat(k.forwardPE),"forwardPE"],["PEG",rat(k.pegRatio),"pegRatio"],
      ["Price / Book",rat(k.priceToBook),"priceToBook"],["Price / Sales",rat(k.priceToSales),"priceToSales"],["EV / EBITDA",rat(k.evToEbitda),"evToEbitda"],
      ["EPS (ttm)",cash(k.trailingEps),"trailingEps"],["EPS (forward)",cash(k.forwardEps),"forwardEps"],
      ["Dividend yield",pct(k.dividendYield),"dividendYield"],["Dividend / share",cash(k.dividendRate)],["Payout ratio",pct(k.payoutRatio),"payoutRatio"],
      ["Free cash flow",money(k.freeCashflow),"freeCashflow"],["Operating cash flow",money(k.operatingCashflow),"operatingCashflow"],
      ["Gross margin",pct(k.grossMargin),"grossMargin"],["Operating margin",pct(k.operatingMargin),"operatingMargin"],["Profit margin",pct(k.profitMargin),"profitMargin"],
      ["Return on equity",pct(k.roe),"roe"],["Return on assets",pct(k.roa),"roa"],
      ["Revenue growth",pct(k.revenueGrowth),"revenueGrowth"],["Earnings growth",pct(k.earningsGrowth),"earningsGrowth"],
      ["Debt / Equity",rat(k.debtToEquity),"debtToEquity"],["Current ratio",rat(k.currentRatio),"currentRatio"],["Quick ratio",rat(k.quickRatio),"quickRatio"],
      ["Beta",rat(k.beta),"beta"],
      ["52-week range",(k.fiftyTwoWeekLow!=null&&k.fiftyTwoWeekHigh!=null)?`${sym}${rat(k.fiftyTwoWeekLow)} – ${sym}${rat(k.fiftyTwoWeekHigh)}`:null],
    ].filter(x=>x[1]!=null);
    el.innerHTML=rows.length?`<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px">`+
      rows.map(([lab,val,key])=>`<div class="kv"><span>${lab}</span><b style="color:${col(key)}">${val}</b></div>`).join("")+`</div>`
      +`<div class="muted" style="font-size:11px;margin-top:6px"><span style="color:var(--bear)">red</span> = poor · <span style="color:#d29922">amber</span> = watch · <span style="color:var(--bull)">green</span> = strong${j.stale?' · <span style="color:#d29922">last known values (live fetch temporarily unavailable)</span>':''}</div>`
      :"no fundamentals available (often the case for funds / indices)";
  }).catch(()=>{const el=document.getElementById("fundbox");if(el)el.textContent="fundamentals unavailable";});
  fetch(`/api/broker/${r.ticker}`).then(x=>x.json()).then(j=>{
    // No placeholder noise (user 2026-06-30): hide the change line when there's no history, hide the
    // WHOLE card when there's neither rating data nor history; show the card when either exists.
    const el=document.getElementById("brokerchg"), card=document.getElementById("brokercard");
    if(!el||SEL!==r.ticker)return;
    el.classList.remove("sqh-loading");
    if(!j.available){el.style.display="none"; if(!r.broker&&card)card.style.display="none"; return;}
    const net=(u,d)=>`${u-d>=0?'+':''}${u-d} net (${u}↑ / ${d}↓)`;
    el.innerHTML=`Coverage change — 0–6 mo: <b>${net(j.up6,j.down6)}</b> · 0–12 mo: <b>${net(j.up12,j.down12)}</b>`;
    if(card)card.style.display="";
    if(!r.broker){const b=document.getElementById("brokerbase");if(b)b.style.display="none";}
  }).catch(()=>{const el=document.getElementById("brokerchg");if(el)el.style.display="none";});
  // Squeeze Rules 1–5 justification is admin-only (user 2026-07-24, P-02): don't expose the per-rule
  // PASS/FAIL diagnostics to non-admin subscribers. Card is omitted above too, so guard the fetch.
  // The X-Auth header was missing here while /api/rules is admin-gated, so this ALWAYS came back
  // 403 {"error":"admin only"} and the card read "no rule detail" for every instrument, triggered or not
  // (user 2026-08-25: "we have items listed as Triggered but ... it says no rule detail - this must exist
  // if triggered"). It never worked. The sibling /api/volscore fetch three lines below always sent it.
  //
  // The 403 was invisible because it is valid JSON: .catch() never fired, j.rules was undefined, and
  // `(j.rules||[])` rendered the empty state. A refusal now says so instead of impersonating "no data".
  if(IS_ADMIN) fetch(`/api/rules/${r.ticker}`,{headers:{"X-Auth":AUTH}})
    .then(x=>x.ok?x.json():Promise.reject(new Error(x.status===403?"admin only":`HTTP ${x.status}`)))
    .then(j=>{
      const el=document.getElementById("rulesbox"); if(!el||SEL!==r.ticker)return;
      el.classList.remove("sqh-loading");
      el.innerHTML=(j.rules||[]).map(u=>`<div class="rule"><div class="top"><span class="v ${u.verdict}">${u.verdict}</span><b>Rule ${u.n} — ${u.name}</b></div><div class="why">${(u.detail||u.note||'').replace(/</g,'&lt;')}</div></div>`).join("")
        || "<span class='muted'>No rule detail was produced for this setup.</span>";
    }).catch(err=>{const el=document.getElementById("rulesbox");
      if(!el)return; el.classList.remove("sqh-loading");
      el.innerHTML=`<span class="muted">Squeeze rules could not be loaded (${String(err.message||err).replace(/</g,'&lt;')}).</span>`;});
  // VolumeScore breakdown (user 2026-07-24, P-02) — logged-in only; blank until the setup has triggered.
  if(AUTH) fetch(`/api/volscore/${r.ticker}`,{headers:{"X-Auth":AUTH}}).then(x=>x.json()).then(j=>{
    const el=document.getElementById("volscorebox"); if(!el||SEL!==r.ticker)return;
    el.classList.remove("sqh-loading");
    const v=j.volscore;
    if(!v){el.innerHTML='<span class="muted" style="font-size:12px">Computed once the setup triggers (needs the break bar + volume history).</span>';return;}
    const col=v.pass?'var(--bull)':(v.score>=5?'#d29922':'var(--muted)');
    const mark=g=>g===true?'<b style="color:var(--bull)">✓</b>':g===false?'<span style="color:var(--bear)">✕</span>':'<span class="muted">n/a</span>';
    el.innerHTML=`<div class="kv"><span><b style="color:${col};font-size:16px">${v.score}</b> / ${v.max} — ${v.pass?'<b style="color:var(--bull)">tradeable (≥'+v.threshold+')</b>':'below the '+v.threshold+' threshold'}</span></div>`+
      v.components.map(c=>`<div class="kv" style="font-size:12px"><span>${mark(c.got)} ${c.label}${c.note?` <span class="muted">(${c.note})</span>`:''}</span><b class="muted">+${c.earned}/${c.points}</b></div>`).join("");
  }).catch(()=>{document.getElementById("volscorebox")&&(document.getElementById("volscorebox").textContent="VolumeScore unavailable");});
  fetch(`/api/links/${r.ticker}`).then(x=>x.json()).then(j=>{
    const el=document.getElementById("xlinks"); if(!el||SEL!==r.ticker)return;
    el.classList.remove("sqh-loading");
    let h="";
    if(j.ours) h+=`<div class="kv"><span>Our latest post</span><a href="${j.ours.url}" target="_blank" rel="noopener">view on X →</a></div>`;
    else h+=`<div class="kv"><span>Our latest post</span><span class="muted">not yet published</span></div>`;
    if(j.mentions&&j.mentions.length){
      h+=`<div class="muted" style="margin-top:6px">Tracked accounts on $${disp(r.ticker)}:</div>`;
      h+=j.mentions.map(m=>`<div class="kv"><a href="${m.url}" target="_blank" rel="noopener">@${(m.account||'').replace(/^@/,'')}</a><span class="muted">${m.date?m.date.slice(0,10):''}</span></div>`).join("");
    } else { h+=`<div class="muted" style="margin-top:4px">No tracked-account mentions.</div>`; }
    el.innerHTML=h;
    paintXVisuals(j.visuals||[]);
  }).catch(()=>{
    const links=document.getElementById("xlinks"),visuals=document.getElementById("xvisuals");
    if(links){links.classList.remove("sqh-loading");links.textContent="X links could not be loaded.";}
    if(visuals){visuals.classList.remove("sqh-loading");visuals.textContent="X visuals could not be loaded.";}
  });
}

function _safeXPostUrl(value){
  try{
    const u=new URL(value);
    const host=u.hostname.toLowerCase().replace(/^www\./,"");
    return u.protocol==="https:"&&(host==="x.com"||host==="twitter.com")&&/\/status\/\d+/.test(u.pathname)?u.href:"";
  }catch(_){return "";}
}
function _loadXWidgets(root){
  const paint=()=>window.twttr?.widgets?.load(root);
  if(window.twttr?.widgets){paint();return;}
  let script=document.querySelector('script[data-x-widgets]');
  if(!script){
    script=document.createElement("script");script.async=true;script.src="https://platform.twitter.com/widgets.js";
    script.dataset.xWidgets="1";document.head.appendChild(script);
  }
  script.addEventListener("load",paint,{once:true});
}
function paintXVisuals(visuals){
  const el=document.getElementById("xvisuals");if(!el)return;
  const expected=[
    {key:"ratedmarkets",account:"@ratedmarkets"},
    {key:"investingvisual",account:"@InvestingVisual"},
  ];
  const byKey=Object.fromEntries((visuals||[]).map(v=>[v.key,v]));
  let embeds=0;
  el.classList.remove("sqh-loading");
  el.innerHTML=expected.map(source=>{
    const item=byKey[source.key]||source,url=_safeXPostUrl(item.url),date=item.date?String(item.date).slice(0,10):"";
    if(!url)return `<section class="xvisual-source"><h5>${source.account}</h5><span class="muted">No instrument-matched post stored.</span></section>`;
    embeds++;
    return `<section class="xvisual-source"><h5>${source.account}${date?` <span class="muted">· ${date}</span>`:""}</h5>`+
      `<blockquote class="twitter-tweet" data-dnt="true" data-theme="${document.documentElement.classList.contains('light')?'light':'dark'}"><a href="${url}" target="_blank" rel="noopener">View post on X</a></blockquote></section>`;
  }).join("");
  if(embeds)_loadXWidgets(el);
}

function applyAll(){render(); if(SEL&&!$("detail").classList.contains("hidden")){const r=DATA.find(x=>x.ticker===SEL);if(r&&pass(r))showDetail(SEL);else{$("detail").classList.add("hidden");SEL=null;}}}

// ── Multi-select filter dropdowns (user 2026-07-17, P-08) ────────────────────────────────────────────
// Wrap each sidebar <select multiple> in a dropdown: closed it shows what's picked; open it's a checkbox
// list (no ctrl/cmd-click). The <select> stays the single source of truth — every read (pass()), every
// write (reset / showall / applyUserDefaults / chart clicks) keeps working untouched; msyncAll() just
// repaints the button + boxes from it. Options arrive later via fillSel(), so the list is built on open.
// Scanner entries removed 2026-08-16: those selects have moved to Squeeze History, and the two that
// remain (f_mkt/f_sec) are hidden scope carriers -- wrapping them would render a dropdown button for a
// control the user cannot see.
const MSEL_IDS=["sqf_dir","sqf_loc","sqf_mkt","sqf_sec","sqf_tf","sqf_out"];
function _mselLabel(sel){
  const on=[...sel.selectedOptions].filter(o=>o.value!=="");
  if(!on.length)return "All";
  if(on.length===1)return on[0].textContent;
  return `${on.length} selected`;
}
function msyncAll(){
  MSEL_IDS.forEach(id=>{
    const sel=$(id), wrap=sel&&sel.closest(".msel"); if(!wrap)return;
    const on=[...sel.selectedOptions].filter(o=>o.value!=="").length;
    const btn=wrap.querySelector(".msel-btn");
    btn.querySelector(".msel-txt").textContent=_mselLabel(sel);
    btn.classList.toggle("on",on>0);
    wrap.querySelectorAll(".msel-pop input").forEach(cb=>{
      const o=[...sel.options].find(o=>o.value===cb.value); cb.checked=!!(o&&o.selected);});
  });
}
function _mselBuild(wrap,sel){
  const pop=wrap.querySelector(".msel-pop");
  pop.innerHTML=[...sel.options].map(o=>
    `<label><input type="checkbox" value="${o.value.replace(/"/g,"&quot;")}"${o.selected?" checked":""}> ${o.textContent}</label>`).join("")
    +`<button type="button" class="msel-clear">Clear</button>`;
  pop.querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
    const o=[...sel.options].find(o=>o.value===cb.value); if(o)o.selected=cb.checked;
    sel.dispatchEvent(new Event("input",{bubbles:true}));   // drives the existing applyAll listener
    msyncAll();});
  pop.querySelector(".msel-clear").onclick=()=>{
    [...sel.options].forEach(o=>o.selected=false);
    sel.dispatchEvent(new Event("input",{bubbles:true}));
    msyncAll();};
}
function mselInit(){
  MSEL_IDS.forEach(id=>{
    const sel=$(id); if(!sel||sel.closest(".msel"))return;
    const wrap=document.createElement("div"); wrap.className="msel";
    sel.parentNode.insertBefore(wrap,sel); wrap.appendChild(sel);
    wrap.insertAdjacentHTML("beforeend",
      `<button type="button" class="msel-btn"><span class="msel-txt">All</span><span class="msel-caret">▼</span></button>`
      +`<div class="msel-pop hidden"></div>`);
    const btn=wrap.querySelector(".msel-btn"), pop=wrap.querySelector(".msel-pop");
    btn.onclick=e=>{e.stopPropagation();
      const opening=pop.classList.contains("hidden");
      document.querySelectorAll(".msel-pop").forEach(p=>p.classList.add("hidden"));   // one open at a time
      if(opening){_mselBuild(wrap,sel);pop.classList.remove("hidden");}};
    pop.onclick=e=>e.stopPropagation();
  });
  msyncAll();
}
document.addEventListener("click",()=>document.querySelectorAll(".msel-pop").forEach(p=>p.classList.add("hidden")));
document.addEventListener("keydown",e=>{if(e.key==="Escape")document.querySelectorAll(".msel-pop").forEach(p=>p.classList.add("hidden"));});
mselInit();

document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{const k=th.dataset.k;sortDir=(sortK===k)?-sortDir:-1;sortK=k;render();_sortArrows("data-k",sortK,sortDir);});
document.querySelectorAll("th[data-pk]").forEach(th=>th.onclick=()=>{const k=th.dataset.pk;poSortDir=(poSortK===k)?-poSortDir:1;poSortK=k;renderPreorders();_sortArrows("data-pk",poSortK,poSortDir);});
document.querySelectorAll("th[data-ok]").forEach(th=>th.onclick=()=>{const k=th.dataset.ok;ooSortDir=(ooSortK===k)?-ooSortDir:-1;ooSortK=k;paintOrderOps();_sortArrows("data-ok",ooSortK,ooSortDir);});
document.querySelectorAll("th[data-igp]").forEach(th=>th.onclick=()=>{const k=th.dataset.igp;igpSortDir=(igpSortK===k)?-igpSortDir:-1;igpSortK=k;paintIgAccount();_sortArrows("data-igp",igpSortK,igpSortDir);});   // IG Account positions sort (user 2026-08-01)
document.querySelectorAll("th[data-igo]").forEach(th=>th.onclick=()=>{const k=th.dataset.igo;igoSortDir=(igoSortK===k)?-igoSortDir:-1;igoSortK=k;paintIgAccount();_sortArrows("data-igo",igoSortK,igoSortDir);});   // IG Account orders sort (user 2026-08-01)
document.querySelectorAll("th[data-ak]").forEach(th=>th.onclick=()=>{const k=th.dataset.ak;acSortDir=(acSortK===k)?-acSortDir:-1;acSortK=k;paintActivity();});
F.forEach(id=>$(id).addEventListener("input",applyAll));
// Click a chart bar / pie slice to set that filter (toggle off if already set) — user 2026-06-27.
// Delegated so EVERY chart on EVERY tab filters (user 2026-07-03), not just the Scanner's #viz.
document.addEventListener("click",e=>{const el=e.target.closest("[data-fk]");if(!el)return;
  const k=el.dataset.fk; if(!$(k))return;
  toggleIn(k, el.dataset.fv);          // multi-select: toggle this value in the set ("" = clear all)
  if(k.startsWith("pof_"))renderPreorders();
  else if(k.startsWith("oof_"))paintOrderOps();
  else if(k.startsWith("pff_"))renderPerformance();
  else if(k.startsWith("ordpf_"))paintOrdersPerf();   // winners charts click-to-filter (P-10 L122/L123)
  else if(k.startsWith("sqf_"))paintSqueezeHist();
  else if(k.startsWith("acf_"))paintActivity();
  else if(k.startsWith("vcf_")||k.startsWith("vmf_"))paintVersion();
  else if(k.startsWith("bcf_"))paintBatch();
  else if(k.startsWith("xf_"))paintXposts();
  else if(k.startsWith("slf_"))paintSyslogs();
  else if(k.startsWith("igo_")||k.startsWith("igp_"))paintIgAccount();
  else if(k.startsWith("inf_"))paintInstruments();   // Instruments cross-filter cards (user 2026-08-07, ChangeRequest P-08)
  else applyAll();});
// Window of the row-detail price chart, in days. Was a slider on the Scanner (f_days); removed
// 2026-08-17 because the squeeze engine already bounds how far back a setup can form, so moving it
// changed the picture without changing what qualifies.
const PRICE_CHART_DAYS=365;
const MF_IDS=["mf_sector","mf_direction","mf_status","mf_location","mf_market","mf_timeframe"];
$("reset").onclick=()=>{
  if(CUR_TAB==="squeezehist")return sqhReset();
  F.forEach(id=>{$(id).value="";});MF_IDS.forEach(id=>$(id).value="");
  // Built-in defaults first, then the user's OWN saved defaults on top (Config tab, user 2026-07-03).
  // The Quality/R:R/Dist/Days defaults went with their controls to Squeeze History on 2026-08-16, and
  // the chart-window slider was removed on 2026-08-17 -- nothing chart-related is a Scanner reset now.
  applyUserDefaults();msyncAll();applyAll();};   // msyncAll: repaint the P-08 dropdowns after a bulk write
$("showall").onclick=()=>{
  if(CUR_TAB==="squeezehist")return sqhReset();
  F.forEach(id=>{$(id).value="";});MF_IDS.forEach(id=>$(id).value="");signalOnly=false;$("signalonly").innerHTML='Show Squeeze Only <span style="color:var(--bear)">✗</span>';msyncAll();applyAll();};
$("togglefilters").onclick=()=>{
  if(CUR_TAB==="preorders")return togglePoFilters();      // same header button drives the Pre-orders asides (P-03)
  if(CUR_TAB==="orderops")return toggleOoFilters();
  if(CUR_TAB==="squeezehist")return toggleSqhFilters();   // filters moved here from the Scanner (2026-08-16)
};   // the Scanner has no filter panel any more — nothing left to toggle
$("signalonly").onclick=()=>{signalOnly=!signalOnly;$("signalonly").innerHTML="Show Squeeze Only "+(signalOnly?'<span style="color:var(--bull)">✓</span>':'<span style="color:var(--bear)">✗</span>');applyAll();};
let _refStart=0, _refBase=null, _refId=null, _lastTens=-1;
// The markets picker (P-15, 2026-07-31) was removed on 2026-08-16 -- a location IS a group of markets,
// so the locations picker below expresses every scope the pair could. Its five functions
// (buildRefreshMktPanel / onRefreshMktAll / onRefreshMktToggle / updateRefreshMktLabel /
// toggleRefreshMkt) and the REFRESH_MARKETS set went with it on 2026-08-17: nothing could add to that
// set once the panel was gone, so the union at the Refresh handler was always union-with-empty.
// REFRESH_MKT_LIST survives -- it is the canonical market list from /api/records and the Operations tab
// still reads it through _refreshMktList().
let REFRESH_MKT_LIST=null;   // canonical market list from /api/records (authed); falls back to uniq("market")
function _refreshMktList(){return (REFRESH_MKT_LIST&&REFRESH_MKT_LIST.length)?REFRESH_MKT_LIST:uniq("market");}
// Rebuild a CHOICE of LOCATIONS (user 2026-08-16: "rebuild snapshot would also be good to filter which
// markets / locations - just do locations for now"). A location is a GROUP of markets, so this resolves
// to markets and reuses the existing, proven refresh path -- no scanner, workflow or server change, and
// no second filter for the backend to disagree with. Picking "US" is exactly picking its markets.
let REFRESH_LOCATIONS=new Set();
function _locMarketMap(){
  const m={};
  (DATA||[]).forEach(r=>{const l=locName(r.location)||"", k=r.market;
    if(l&&k)(m[l]||(m[l]=new Set())).add(k);});
  return m;
}
function _refreshMarketsForLocations(){
  const map=_locMarketMap(), out=new Set();
  REFRESH_LOCATIONS.forEach(l=>(map[l]||new Set()).forEach(k=>out.add(k)));
  return out;
}
function buildRefreshLocPanel(){
  const p=$("refresh-loc-panel"); if(!p)return;
  const map=_locMarketMap(), locs=Object.keys(map).sort(), all=REFRESH_LOCATIONS.size===0;
  const rows=locs.map(l=>{
    const on=!all&&REFRESH_LOCATIONS.has(l), n=map[l].size;
    return `<label style="display:flex;align-items:center;gap:7px;padding:3px 4px;font-size:12.5px;cursor:pointer;white-space:nowrap"><input type="checkbox" data-loc="${l.replace(/"/g,'&quot;')}" ${on?'checked':''} onchange="onRefreshLocToggle(this)"> ${_esc(l)} <span class="muted" style="font-size:11px">(${n} market${n===1?'':'s'})</span></label>`;
  }).join("");
  p.innerHTML=`<label style="display:flex;align-items:center;gap:7px;padding:3px 4px;font-size:12.5px;cursor:pointer;border-bottom:1px solid var(--line);margin-bottom:4px"><input type="checkbox" id="refresh-loc-all" ${all?'checked':''} onchange="onRefreshLocAll(this)"> <b>All locations</b></label>`+rows
    +(locs.length?'':'<div class="muted" style="font-size:12px;padding:4px">No locations in the current snapshot.</div>');
  updateRefreshLocLabel();
}
function onRefreshLocAll(cb){ if(cb.checked)REFRESH_LOCATIONS.clear(); buildRefreshLocPanel(); }
function onRefreshLocToggle(cb){
  const l=cb.getAttribute("data-loc");
  if(cb.checked)REFRESH_LOCATIONS.add(l); else REFRESH_LOCATIONS.delete(l);
  buildRefreshLocPanel();
}
function updateRefreshLocLabel(){
  const b=$("refresh-loc-btn"); if(!b)return;
  const n=REFRESH_LOCATIONS.size;
  b.textContent = n===0 ? "Rebuild locations: All ▾" : (n===1 ? `Rebuild locations: ${[...REFRESH_LOCATIONS][0]} ▾` : `Rebuild locations: ${n} selected ▾`);
}
function toggleRefreshLoc(e){
  if(e)e.stopPropagation();
  const p=$("refresh-loc-panel"); if(!p)return;
  if(p.style.display!=="none"){p.style.display="none";return;}
  buildRefreshLocPanel(); p.style.display="block";
  setTimeout(()=>{const h=ev=>{if(!$("refresh-loc-wrap").contains(ev.target)){p.style.display="none";document.removeEventListener("click",h);}};document.addEventListener("click",h);},0);
}
$("refresh").onclick=async()=>{ if($("refresh").disabled)return;
  // Locations are the only scope control now (the markets picker was removed 2026-08-16); a location
  // resolves to its markets, so the request body the backend sees is unchanged.
  const picked=[...new Set(_refreshMarketsForLocations())];
  const locs=[...REFRESH_LOCATIONS];
  const scope = picked.length
    ? (locs.length ? `${locs.join(', ')} — ${picked.length} market${picked.length>1?'s':''} (${picked.join(', ')})`
                   : `the selected market${picked.length>1?'s':''} (${picked.join(', ')})`)
    : `the full universe (${DATA.length||'884+'} instruments)`;
  if(!await appConfirm(`Rescans ${scope} — takes a few minutes. The page keeps working with the current data and reloads automatically when the rebuild finishes.`,{title:"Rebuild the snapshot now?",ok:"↻ Rebuild"}))return;
  $("refresh").disabled=true; $("refresh").textContent="↻ Starting…"; _refStart=Date.now(); _lastTens=-1;
  fetch("/api/refresh",{method:"POST",headers:{"X-Auth":AUTH,"Content-Type":"application/json"},
        body:JSON.stringify(picked.length?{markets:picked}:{})}).then(async x=>{const j=await x.json().catch(()=>({}));if(!x.ok||!j.started)throw new Error(j.error||(j.busy?"A refresh is already running.":`HTTP ${x.status}`));return j;})
    .then(j=>{_refBase=j.base_generated||null;_refId=j.refresh_id||null;$("refresh").title="";$("refresh").textContent=`⏳ Queued via ${j.worker||'external worker'}…`;pollRefresh();})
    .catch(err=>{$("refresh").textContent="↻ Refresh failed";$("refresh").title=err.message||"Refresh could not be queued";$("refresh").disabled=false;});};
function pollRefresh(){
  fetch("/api/status"+(_refId?`?refresh_id=${encodeURIComponent(_refId)}`:"")).then(x=>x.json()).then(s=>{
    if(s.refresh_id)_refId=s.refresh_id;
    if(s.refresh_error){$("refresh").className="";$("refresh").textContent="↻ Refresh failed";$("refresh").title=s.refresh_error;$("refresh").disabled=false;_refId=null;return;}
    const waitingForExternal=_refBase&&s.generated_utc===_refBase&&(Date.now()-_refStart)<2*60*60*1000;
    if(s.refreshing||waitingForExternal){
      const p=s.progress||{}, done=p.done||0, total=p.total||0;
      let eta="";
      if(_refStart&&done>0&&total>0){const rate=done/((Date.now()-_refStart)/1000); if(rate>0){const mins=Math.max(1,Math.ceil((total-done)/rate/60)); eta=` · ~${mins} min to complete`;}}
      $("refresh").className="sqh-loading";$("refresh").textContent=total?`⏳ Data loading… ${done}/${total}${eta}`:(waitingForExternal?"⏳ Queued/running on GitHub Actions…":"⏳ Data loading…");
      // Flash green for a second each time another 10 instruments complete (user 2026-06-29).
      const tens=Math.floor(done/10);
      if(tens>_lastTens){_lastTens=tens;$("refresh").style.color="var(--bull)";setTimeout(()=>{$("refresh").style.color="";},1000);}
      setTimeout(pollRefresh,3000);
    } else {_refBase=null;_refId=null;$("refresh").className="";$("refresh").style.color="";$("refresh").textContent="↻ Done — reloading…"; setTimeout(()=>location.reload(),800);}
  }).catch(()=>setTimeout(pollRefresh,5000));
}
$("theme").onclick=()=>{const l=document.documentElement.classList.toggle("light");$("theme").textContent=l?"☀️ Light":"🌙 Dark";if(SEL)showDetail(SEL);};

// ── Tabs: Scanner | Pre-orders ───────────────────────────────────────────────────────────────────
// signal, not yet triggered, and quality >= 25 (user 2026-06-29: drop the low-quality noise)
// signal, not yet triggered, Q>=25, and NOT already placed on IG (removed once it's a live working order).
let PINNED=new Set(), OVERRIDES={};   // pinned tickers + per-ticker entry/stop/target overrides (user 2026-07-03/04)
let MY_LIMITS={};   // the user's personal floors from Configuration → My trading limits (min_risk_reward, min_quality) — applied to My Pre-orders (user 2026-07-24, P-02)
const isPreorder=r=>(PINNED.has(r.ticker)||PINNED.has(disp(r.ticker))) ||
  (r.has_signal && (r.status==="READY"||r.status==="DEVELOPING") && !(r.quality!=null && r.quality<25) && !IGWO.has(r.ticker) && !IGWO.has(disp(r.ticker)));
const TABS=["welcome","whatwedo","intro","risk","appendix","terms","scanner","instruments","preorders","orderops","igaccount","fees","activity","batch","users","xposts","config","configadmin","version","syslogs","jobs","sysdocs","docs","markets","marketsadmin","performance","squeezehist","mfm","changereq"];
// Visible when logged out. "performance" was REMOVED on 2026-08-28 at the requester's instruction
// ("remove the evidence tab and row count if user not logged in"). It had been public, and because the
// table's only row filter is the viewer's OWN saved configuration (_pfMatchesCurrentConfig -> MY_LIMITS,
// which is loaded with the token and therefore empty when logged out), an anonymous visitor received the
// UNFILTERED superset -- 4,932 recorded triggers with entry, stop, target, R:R, quality, RVOL and
// realised outcome -- roughly ten times what the account owner sees on the same screen.
// "performance" is public again from 2026-08-29. It was removed on 2026-08-28 on a MISREADING of
// "remove the evidence tab and row count if user not logged in" -- that meant the evidence TABLE and
// the count, not the whole tab, and taking the tab away was reported straight back as a bug. What is
// withheld from a logged-out visitor is now done properly at the SERVER (_public_perf_response):
// only dated returns leave, so the monthly compounding chart still draws and no trade detail ships.
const PUBLIC_TABS=["welcome","whatwedo","intro","risk","appendix","terms","scanner","instruments","performance"];
const ADMIN_TABS=["batch","users","version","xposts","syslogs","jobs","sysdocs","changereq","configadmin","marketsadmin","squeezehist","fees"];  // admin only
const SUPPORT_TABS=["batch","syslogs","jobs","sysdocs"];   // Support role: read-only ops visibility plus the system runbook.
const FEATURE_TABS=[];                                         // X Posts is always available to admin users
// Tabs hidden by DEFAULT (user 2026-07-10): visible only once the user opts in via Tab Visibility.
const DEFAULT_HIDDEN=new Set(["mfm"]);   // IG Account is now default-visible (user 2026-07-11)
// Parent tabs (user 2026-07-10): the grouped children are reached through a sub-nav. Operations =
// admin operations; Settings = config + markets (its admin children are gated by role).
const TAB_GROUPS={operations:["users","batch","xposts","changereq","syslogs","jobs","version"],
                  settings:["config","markets","configadmin","marketsadmin"]};   // user buttons left, admin right
const parentOf=t=>{for(const p in TAB_GROUPS)if(TAB_GROUPS[p].includes(t))return p;return null;};
// The tab bar's own left-to-right order, with each grouped parent ("Settings", "Operations") expanded in
// place — the order a user actually sees. Used by the Tab Visibility panel so its chips read like the bar.
// Derived from the #tab-* buttons in markup order; TABS is the membership list, not the display order.
const SCREEN_TAB_ORDER=["welcome","whatwedo","intro","risk","appendix","performance","scanner","preorders",
                        "orderops","igaccount","fees","activity","mfm","sysdocs","docs","terms","instruments",
                        "squeezehist","config","markets","configadmin","marketsadmin","users","batch","xposts",
                        "changereq","syslogs","jobs","version"];
// Any tab added to TABS but not yet placed above still appears, at the end, rather than vanishing from the
// visibility panel — a missing chip would silently strand that tab at whatever state it was last saved with.
const screenTabOrder=()=>[...SCREEN_TAB_ORDER.filter(t=>TABS.includes(t)),...TABS.filter(t=>!SCREEN_TAB_ORDER.includes(t))];
let FEATURES={xposts:false};
const PREORDERS_TO_IG_ENABLED=false;   // server-side enforcement mirrors this UI gate
let AUTH=localStorage.getItem("sq_auth")||"";
let ROLE="guest", IS_ADMIN=false, IS_SUPPORT=false, HIDDEN_TABS=[], SHOWN_TABS=[];
// Which tabs the current session may see (login + role + per-user tab-visibility choices).
function tabAllowed(t){
  if(t==="orderops"&&!PREORDERS_TO_IG_ENABLED)return false;
  if(FEATURE_TABS.includes(t)&&!FEATURES[t])return false;   // feature tab off by default (user 2026-07-03)
  if(ADMIN_TABS.includes(t)&&!IS_ADMIN&&!(IS_SUPPORT&&SUPPORT_TABS.includes(t)))return false;    // admin tabs require admin (Support gets its 3 read-only tabs too)
  if(t==="mfm"&&!goldOnly())return false;   // Multi-Factor Momentum is Gold-tier (user 2026-07-11; Gold ONLY 2026-07-17)
  if(!PUBLIC_TABS.includes(t)&&!AUTH)return false;      // login-required tab, not logged in
  if(DEFAULT_HIDDEN.has(t)&&!SHOWN_TABS.includes(t))return false;   // default-hidden unless opted in (user 2026-07-10)
  if(t==="config")return true;                          // config always available once logged in
  if(t==="users"&&IS_ADMIN)return true;   // admins ALWAYS get User Management (user 2026-07-11). "How it works (System)" used to be force-shown here too, but must honour Tab Visibility — it falls through to the HIDDEN_TABS check below (user 2026-07-17, P-25)
  if(HIDDEN_TABS.includes(t))return false;              // user chose to hide it (Tab Visibility) — applies to admin tabs too
  return true;
}
function applyTabVisibility(){
  TABS.forEach(t=>{const b=$("tab-"+t);if(b)b.style.display=tabAllowed(t)?"":"none";});   // top-level buttons; grouped children have none
  // Parent tabs (Operations/Settings) show when the session can reach at least one of their children.
  Object.keys(TAB_GROUPS).forEach(p=>{const b=$("tab-"+p);if(b)b.style.display=TAB_GROUPS[p].some(tabAllowed)?"":"none";});
  const rb=$("refresh"); if(rb) rb.dataset.adminonly="1";
  colorTabs();
  syncStickyOffsets();   // tab set changed → re-measure the sticky header+tabs height (P-75)
}
// Sticky tab bar offsets (user 2026-08-03, P-75): measure the pinned header + tab-bar heights and publish
// them as CSS vars so the subnav, filter sidebar and detail panel sit BELOW the pinned tabs — and stay
// correct when the tabs wrap to a second row on a narrow screen. Cheap; safe to call often.
function syncStickyOffsets(){
  const hdr=document.querySelector("header"), tabs=document.querySelector(".tabs");
  const h=hdr?hdr.offsetHeight:49, t=tabs?tabs.offsetHeight:41;
  const rs=document.documentElement.style;
  rs.setProperty("--hdr-h", h+"px");
  rs.setProperty("--sticky-top", (h+t-1)+"px");   // tabs overlap the header border by 1px: no content seam
}
function detectIPadMini(){
  const ua=navigator.userAgent||"", appleTouch=/Macintosh/.test(ua)&&navigator.maxTouchPoints>1;
  const ipad=/iPad/.test(ua)||appleTouch, shortSide=Math.min(screen.width||0,screen.height||0);
  document.documentElement.classList.toggle("ipad-mini",ipad&&shortSide>=700&&shortSide<=820);
  syncStickyOffsets();
}
window.addEventListener("resize", detectIPadMini);
window.addEventListener("load", syncStickyOffsets);
detectIPadMini();
// Header controls change after login/role/config loading and may wrap without a window resize. Observe the
// actual sticky elements so their offsets stay joined to the header at all times (user 2026-08-04, P-03).
if(typeof ResizeObserver!=="undefined"){
  const _stickyRO=new ResizeObserver(syncStickyOffsets);
  [document.querySelector("header"),document.querySelector(".tabs")].filter(Boolean).forEach(el=>_stickyRO.observe(el));
}
// Each tab a progressively darker shade of grey, left -> right (user 2026-07-03). Light text keeps
// the darker right-hand tabs readable in both themes; the active tab gets a bright accent contained
// inside its own box so it cannot bleed into the strip divider.
function colorTabs(){
  const btns=[...document.querySelectorAll(".tabs .tab")].filter(b=>b.id!=="logout");
  const n=btns.length;
  btns.forEach((b,i)=>{
    const active=b.classList.contains("active");
    const t=i/(n-1||1);                 // 0..1 left->right
    const lum=Math.round(20+58*t);      // grey lightness 20% -> 78% (dark left -> light right)
    b.style.borderColor="transparent";
    b.style.borderBottom="none";
    b.style.boxShadow="none";
    if(active){                          // selected main tab -> green (user 2026-07-11)
      b.style.background="var(--bull)";
      b.style.color="#ffffff";
      b.style.boxShadow="inset 0 -3px 0 var(--bull)";
      b.style.fontWeight="700";
    } else {
      b.style.background=`hsl(210 8% ${lum}%)`;
      b.style.color=lum<50?"#e8edf2":"#1b1f24";
      b.style.fontWeight="500";
    }
  });
}
function renderSubnav(parent,active){
  const sn=$("subnav"); if(!sn)return;
  const kids=parent?TAB_GROUPS[parent].filter(tabAllowed):[];
  if(!kids.length){sn.style.display="none";sn.innerHTML="";sn.className="subnav";return;}
  sn.className="subnav"+(parent?" subnav-"+parent:"");
  sn.style.display="flex";
  sn.innerHTML=kids.map(c=>`<button class="subpill${ADMIN_TABS.includes(c)?' adminpill':''}${c===active?' active':''}" onclick="showTab('${c}')">${TAB_LABELS[c]||c}</button>`).join("");
}
// Tab-visit history for the header ◀ ▶ buttons (user 2026-07-11).
let TAB_HIST=[], TAB_HI=-1, _navFromHist=false;
function _updateNavBtns(){const b=$("nav-back"),f=$("nav-fwd");if(b)b.disabled=TAB_HI<=0;if(f)f.disabled=TAB_HI>=TAB_HIST.length-1;}
function tabHistGo(dir){const ni=TAB_HI+dir;if(ni<0||ni>=TAB_HIST.length)return;TAB_HI=ni;_navFromHist=true;showTab(TAB_HIST[ni]);_navFromHist=false;}
let CUR_TAB="welcome";
function showTab(name){
  if(TAB_GROUPS[name]){const kids=TAB_GROUPS[name].filter(tabAllowed);name=kids[0]||"welcome";}   // parent -> first child
  if(!tabAllowed(name))name="welcome";                  // never open a tab this session can't see
  if(!_navFromHist && TAB_HIST[TAB_HI]!==name){TAB_HIST=TAB_HIST.slice(0,TAB_HI+1);TAB_HIST.push(name);TAB_HI=TAB_HIST.length-1;}
  try{sessionStorage.setItem("sq_tab",name);}catch(e){}   // survive the post-refresh reload so we don't dump the user on Introduction (user 2026-07-18)
  CUR_TAB=name;   // active tab — the header "Show Filters" button routes on this (P-03)
  _updateNavBtns();
  const locked=!AUTH && !PUBLIC_TABS.includes(name);    // login-required tab, not logged in -> login panel
  const parent=parentOf(name);
  TABS.forEach(t=>{const v=$("view-"+t);if(v)v.classList.toggle("hidden",locked||t!==name);});
  [...TABS,...Object.keys(TAB_GROUPS)].forEach(t=>{const b=$("tab-"+t);if(b)b.classList.remove("active");});
  const ab=parent?$("tab-"+parent):$("tab-"+name); if(ab)ab.classList.add("active");   // highlight the parent for grouped children
  renderSubnav(locked?null:parent,name);
  colorTabs();
  $("loginpanel").classList.toggle("hidden",!locked);
  // Header controls are Scanner tools; Refresh is admin-only (user 2026-07-03). "Show Filters" also serves
  // My Pre-orders and Pre-orders-to-IG, same placement as the Scanner (user 2026-07-18, P-03).
  const sc=(name==="scanner")&&!locked;
  // Squeeze History owns the filter sidebar since 2026-08-16, so it gets Reset / Show all / Show Filters.
  // "Squeeze Only" stays Scanner-only: a history row has no has_signal field for it to act on.
  const sqh=(name==="squeezehist")&&!locked;
  // Scanner dropped from filterTab on 2026-08-16: its sidebar is gone, so offering "Show Filters" there
  // would open nothing.
  const filterTab=(name==="squeezehist"||name==="preorders"||name==="orderops")&&!locked;
  $("signalonly").style.display="none";   // hidden 2026-08-16 (user: "can be hidden for now")
  // Scanner dropped 2026-08-16: with its sidebar gone there is nothing there to reset, and offering
  // the buttons implied filters that no longer exist (user: "All these buttons are on the scanner
  // report page"). "Show Squeeze Only" stays -- it is a real Scanner control over has_signal.
  ["reset","showall"].forEach(id=>{const b=$(id);if(b)b.style.display=sqh?"":"none";});
  const tf=$("togglefilters");
  if(tf){tf.style.display=filterTab?"":"none";
    if(filterTab){const shown=name==="preorders"?!( $("po-filters")||{classList:{contains:()=>true}}).classList.contains("hidden")
        :name==="orderops"?!( $("oo-filters")||{classList:{contains:()=>true}}).classList.contains("hidden")
        :!( $("sqh-filters")||{classList:{contains:()=>true}}).classList.contains("hidden");
      tf.innerHTML="Show Filters "+(shown?'<span style="color:var(--bull)">✓</span>':'<span style="color:var(--bear)">✗</span>');}}
  const rb=$("refresh"); if(rb) rb.style.display=(sc&&IS_ADMIN)?"":"none";
  const rlw=$("refresh-loc-wrap"); if(rlw){rlw.style.display=(sc&&IS_ADMIN)?"":"none"; if(sc&&IS_ADMIN)updateRefreshLocLabel();}
  if(locked)return;
  const R={preorders:renderPreorders,activity:renderActivity,orderops:renderOrderOps,xposts:renderXposts,
    config:renderConfig,configadmin:renderConfig,users:renderUsers,syslogs:renderSyslogs,jobs:renderJobs,
    version:renderVersion,batch:renderBatch,markets:renderMarkets,marketsadmin:renderMarketsAdmin,
    performance:renderPerformance,changereq:renderCR,igaccount:renderIgAccount,
    squeezehist:renderSqueezeHist,fees:renderFees,instruments:renderInstruments,docs:renderDocs};
  if(R[name])R[name]();
}
// ── X Posts tab (user 2026-07-03): all published tweets + charts, click-to-filter, sortable. ──
let X_ROWS=[], xSortK="published_at", xSortDir=-1;
const _xmonth=e=>(e.published_at||'').slice(0,7);
function paintXposts(){
  const by=fn=>X_ROWS.reduce((m,e)=>{const v=fn(e)||'—';m[v]=(m[v]||0)+1;return m;},{});
  $("x-viz").innerHTML=
    `<div class="vizsector">`+barChart("Market",by(e=>e.market),"xf_market")+`</div>`+
    `<div class="vizsector">`+barChart("Month",by(_xmonth),"xf_month",null,true)+`</div>`+
    `<div class="vizsector">`+barChart("Month-Week",by(e=>_mw(e.published_at)),"xf_mweek",null,true)+`</div>`+
    `<div class="vizsector">`+barChart("Ticker",by(e=>disp(e.ticker)),"xf_ticker")+`</div>`;   /* Ticker far right (P-11a) */
  if($("x-datebar")&&!$("x-datebar").innerHTML)$("x-datebar").innerHTML=dateFilterBar("x","paintXposts()");
  const _xq=(($("x-search")||{}).value||"").toLowerCase().trim();
  let rows=X_ROWS.filter(e=>inSet("xf_ticker",disp(e.ticker))&&inSet("xf_market",e.market)&&inSet("xf_month",_xmonth(e))&&inSet("xf_mweek",_mw(e.published_at)));
  if(_xq)rows=rows.filter(e=>(e.name||"").toLowerCase().includes(_xq)||disp(e.ticker).toLowerCase().includes(_xq));
  rows=applyDateFilter("x",rows,e=>e.published_at);
  rows=genSort(rows,xSortK,xSortDir);
  $("xtab-count")&&($("xtab-count").textContent=`(${X_ROWS.length})`);
  $("x-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> tweets${rows.length!==X_ROWS.length?` <span class="muted">of ${X_ROWS.length}</span>`:''}`;
  $("x-rows").innerHTML=rows.map(e=>`<tr><td>${e.published_at||''}</td><td>${nm40(e.name)}</td><td>${e.market||''}</td><td>${e.thread>1?e.thread+' pts':'1'}</td><td><b>${disp(e.ticker)}</b></td><td><a href="${e.url}" target="_blank" rel="noopener">open ↗</a></td></tr>`).join("")||`<tr><td colspan="6" class="empty">No tweets yet.</td></tr>`;
}
function renderXposts(){_rowsLoading("x-rows","renderXposts()");fetch("/api/x-posts",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();}).then(j=>{_rowsLoaded("x-rows");X_ROWS=j.rows||[];paintXposts();}).catch(()=>_rowsFault("x-rows","Try signing in again.","renderXposts()"));}
document.querySelectorAll("th[data-xk]").forEach(th=>th.onclick=()=>{const k=th.dataset.xk;xSortDir=(xSortK===k)?-xSortDir:-1;xSortK=k;paintXposts();_sortArrows("data-xk",xSortK,xSortDir);});
// ── Admin tabs (user 2026-07-03): User Management, Version History, Batch Activity, Tab Visibility ──
const TAB_LABELS={welcome:"Introduction",whatwedo:"What we do",intro:"How it works",risk:"Risk awareness",appendix:"Appendix",terms:"T&C",scanner:"Scanner Report",xposts:"X Posts",
  preorders:"My Pre-orders",orderops:"Pre-orders to my IG",activity:"My Activity",batch:"Batch Activity",
  users:"User Management",version:"Version History",syslogs:"System Logs",jobs:"Scheduled Jobs",sysdocs:"How it works (System)",
  config:"Configuration (User)",markets:"Markets (User)",marketsadmin:"Markets (Admin)",configadmin:"Configuration (Admin)",
  performance:"Performance",mfm:"Trading (Multi-Factor Momentum)",changereq:"Change Requests",igaccount:"IG Account",
  squeezehist:"Squeeze History",fees:"Fees",instruments:"Instruments"};
// ── System Logs (admin, user 2026-07-04): health cards + log table + clickable charts ──────────────
let SL_ROWS=[], slSortK="ts", slSortDir=-1;
function paintSyslogs(){
  const by=fn=>SL_ROWS.reduce((m,e)=>{const v=fn(e)||'—';m[v]=(m[v]||0)+1;return m;},{});
  const lvlCol=k=>k==="ERROR"?"var(--bear)":k==="WARNING"?"#d29922":k==="INFO"?"var(--accent)":"var(--muted)";
  $("sl-viz").innerHTML=
    `<div class="vizsector">`+barChart("Level",by(e=>e.level),"slf_level",lvlCol)+`</div>`+
    `<div class="vizsector">`+barChart("Module",by(e=>e.logger),"slf_logger")+`</div>`+
    `<div class="vizsector">`+barChart("Hour",by(e=>(e.ts||'').slice(11,13)+":00"),"slf_hour")+`</div>`+
    `<div class="vizsector">`+barChart("Month",by(e=>(e.ts||'').slice(0,7)),"slf_month",null,true)+`</div>`+
    `<div class="vizsector">`+barChart("Month-Week",by(e=>_mw(e.ts)),"slf_mweek",null,true)+`</div>`;
  if($("sl-datebar")&&!$("sl-datebar").innerHTML)$("sl-datebar").innerHTML=dateFilterBar("sl","paintSyslogs()");
  let rows=SL_ROWS.filter(e=>inSet("slf_level",e.level)&&inSet("slf_logger",e.logger)&&inSet("slf_hour",(e.ts||'').slice(11,13)+":00")&&inSet("slf_month",(e.ts||'').slice(0,7))&&inSet("slf_mweek",_mw(e.ts)));
  rows=applyDateFilter("sl",rows,e=>e.ts);
  const _slq=(($("sl-search")||{}).value||"").toLowerCase().trim();   // free-text search by message (user 2026-07-17)
  if(_slq)rows=rows.filter(e=>(e.message||"").toLowerCase().includes(_slq));
  rows=genSort(rows,slSortK,slSortDir);
  $("sl-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> log records${rows.length!==SL_ROWS.length?` <span class="muted">of ${SL_ROWS.length}</span>`:''}`;
  $("sl-rows").innerHTML=rows.slice(0,400).map(e=>`<tr><td style="white-space:nowrap">${e.ts||''}</td><td><b style="color:${lvlCol(e.level)}">${e.level||''}</b></td><td>${e.logger||''}</td><td class="muted" style="white-space:normal;word-break:break-word">${(e.message||'').replace(/</g,"&lt;")}</td></tr>`).join("")||`<tr><td colspan="4" class="empty">No log records yet.</td></tr>`;
}
// ── Scheduled Jobs tab (admin, user 2026-07-06): cron definitions + GitHub Actions run stats ──
let JOB_ROWS=[], jSortK="last_time", jSortDir=-1;   // default: last run, newest first (user 2026-07-11)
function _jstCol(k){return k==="success"?"var(--bull)":(k==="failure"||k==="timed_out"||k==="startup_failure")?"var(--bear)":(k==="in_progress"||k==="queued")?"var(--accent)":"var(--muted)";}
function _jstyleCol(k){return k==="Multi-factor momentum"?"var(--accent)":k==="Squeeze"?"var(--bull)":"var(--muted)";}
function _jcatCol(k){return k==="Pricing"?"#d29922":"var(--fg)";}
// Cross-filter (2026-08-07, ChangeRequest P-09 — Scheduler "allow all charts to have multi select and
// cross filter"): each chart now counts rows passing every OTHER chart's selection (same brushed pattern as
// Squeeze History/Back Test), not the full unfiltered JOB_ROWS as before. "Recent failures" also used to be
// wired to a dead filter key ("jf_none", never checked by the table filter below) — clicking a bar there
// silently did nothing; it's now a real per-job filter ("jf_job", matched against the job title).
function paintJobs(){
  const _jDims=[["jf_style",e=>e.trading_style],["jf_cat",e=>e.category],["jf_status",e=>e.last_status],["jf_job",e=>e.title]];
  const _pass=(id,v)=>inSet(id,v||"—");
  const byX=exceptId=>{const m={};JOB_ROWS.forEach(e=>{
    for(const [id,fn] of _jDims){if(id!==exceptId&&!_pass(id,fn(e)))return;}
    const v=_jDims.find(d=>d[0]===exceptId)[1](e)||"—"; m[v]=(m[v]||0)+1;});return m;};
  const failByJob=exceptId=>{const m={};JOB_ROWS.forEach(e=>{if(!e.failures)return;
    for(const [id,fn] of _jDims){if(id!==exceptId&&!_pass(id,fn(e)))return;}
    m[e.title]=e.failures;});return m;};
  $("jobs-viz").innerHTML=
    `<div class="vizsector">`+barChart("Trading style",byX("jf_style"),"jf_style",_jstyleCol)+`</div>`+
    `<div class="vizsector">`+barChart("Category",byX("jf_cat"),"jf_cat")+`</div>`+
    `<div class="vizsector">`+barChart("Last status",byX("jf_status"),"jf_status",_jstCol)+`</div>`+
    `<div class="vizsector">`+barChart("Recent failures",failByJob("jf_job"),"jf_job",()=>"var(--bear)")+`</div>`;
  let rows=JOB_ROWS.filter(e=>_jDims.every(([id,fn])=>_pass(id,fn(e))));
  rows=genSort(rows,jSortK,jSortDir);
  const totF=JOB_ROWS.reduce((s,e)=>s+(e.failures||0),0);
  $("jobs-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> scheduled jobs${rows.length!==JOB_ROWS.length?` <span class="muted">of ${JOB_ROWS.length}</span>`:""} <span class="muted">· ${totF} recent failure(s)</span>`;
  $("jobs-rows").innerHTML=rows.map(e=>`<tr>
    <td><b>${e.title||""}</b>${e.purpose?`<div class="muted" style="font-size:11.5px;max-width:360px;white-space:normal;line-height:1.35;margin-top:3px">${e.purpose}</div>`:""}</td><td><span class="tag" style="background:var(--chip);color:${_jcatCol(e.category)}">${e.category||""}</span></td>
    <td><b style="color:${_jstyleCol(e.trading_style)}">${e.trading_style||""}</b></td>
    <td>${e.frequency||e.cron||""}</td>
    <td><b style="color:${_jstCol(e.last_status)}">${e.last_status||"—"}</b></td>
    <td>${e.last_time?e.last_time.replace("T"," ").replace("Z",""):"—"}</td>
    <td>${e.last_duration_s!=null?(e.last_duration_s<60?e.last_duration_s+"s":Math.floor(e.last_duration_s/60)+"m "+(e.last_duration_s%60)+"s"):"—"}</td>
    <td>${e.executions!=null?e.executions:""}</td>
    <td>${e.failures!=null?`<b style="color:${e.failures?"var(--bear)":"var(--muted)"}">${e.failures}</b>${e.failures_window?` <span class="muted" style="font-size:11px">/${e.failures_window}</span>`:""}`:""}</td>
    <td class="muted" style="font-size:12px">${e.workflow||""}</td></tr>`).join("")||`<tr><td colspan="10" class="empty">No scheduled jobs found.</td></tr>`;
}
function renderJobs(force){
  $("jobs-rows").innerHTML=`<tr><td colspan="10" class="empty"><span class="sqh-loading">⏳ Data loading…</span></td></tr>`;
  fetch("/api/scheduled-jobs"+(force?"?refresh=1":""),{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{JOB_ROWS=j.jobs||[];
      const totF=JOB_ROWS.reduce((s,e)=>s+(e.failures||0),0);
      const cards=[["Jobs",JOB_ROWS.length],["Categories",new Set(JOB_ROWS.map(e=>e.category)).size],["Recent failures",totF]];   // "Stats/GitHub API" card removed (user 2026-07-11)
      $("jobs-health").innerHTML=cards.map(([k,v])=>`<div class="fcard"><div class="body"><div class="muted" style="font-size:12px">${k}</div><div style="font-size:20px;font-weight:800">${v}</div></div></div>`).join("")+(j.generated_utc?`<div class="fcard"><div class="body"><div class="muted" style="font-size:12px">As of</div><div style="font-size:13px">${j.generated_utc} UTC</div>${j.error?`<div style="color:#d29922;font-size:11px;margin-top:4px">${j.error}</div>`:""}</div></div>`:"");
      paintJobs();})
    .catch(()=>{$("jobs-rows").innerHTML=`<tr><td colspan="10" class="empty" style="color:var(--bear)">Run stats could not be loaded. Use Refresh to retry.</td></tr>`;});
}
document.querySelectorAll("th[data-jk]").forEach(th=>th.onclick=()=>{const k=th.dataset.jk;jSortDir=(jSortK===k)?-jSortDir:1;jSortK=k;paintJobs();_sortArrows("data-jk",jSortK,jSortDir);});
function renderSyslogs(){
  if(!($("slf_level")||{}).value)$("slf_level").value="ERROR"+SEP+"WARNING";   // default to problems only (user 2026-07-06)
  _rowsLoading("sl-rows","renderSyslogs()");
  fetch("/api/system-logs",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{
      const h=j.health||{},c=h.db_counts||{};
      const card=(icon,title,val,sub)=>`<div class="fcard"><div class="ic">${icon}</div><h3>${title}</h3><div class="body" style="font-size:20px;font-weight:700;color:var(--fg)">${val}</div><div class="muted" style="font-size:12px">${sub||''}</div></div>`;
      $("sl-health").innerHTML=
        card("⏱️","Uptime",`${h.uptime_mins??'?'} min`,`started ${h.server_started||'?'} · Python ${h.python||'?'}`)+
        card("🗄️","Database",h.db_ping_ms!=null?`${h.db_ping_ms} ms`:"unreachable",h.db_error?h.db_error:`orders ${c.working_orders??'?'} · triggers ${c.hvf_triggers??'?'} · tweets ${c.x_publications??'?'}`)+
        card("📊","Snapshot",`${h.snapshot_count??'?'} instruments`,`generated ${(h.snapshot_generated||'?').slice(0,16)}${h.refreshing?' · REFRESHING NOW':''}`)+
        card("📋","Logs held",`${(j.logs||[]).length}`,`batch rows ${c.batch_activity??'?'} · activity rows ${c.activity_log??'?'}`);
      SL_ROWS=j.logs||[];paintSyslogs();})
    .catch(()=>{$("sl-rows").innerHTML=`<tr><td colspan="4" class="empty">Could not load (admin only).</td></tr>`;});
}
document.querySelectorAll("th[data-sk]").forEach(th=>th.onclick=()=>{const k=th.dataset.sk;slSortDir=(slSortK===k)?-slSortDir:-1;slSortK=k;paintSyslogs();});
function pwStrength(s){
  // Password complexity badge (user 2026-07-06). "unknown" = set before this was tracked; "Locked" =
  // seeded account that has never set a password. Colour: Strong green, Fair amber, Weak red.
  const col={Strong:"var(--bull)",Fair:"#d29922",Weak:"var(--bear)",Locked:"var(--muted)"}[s]||"var(--muted)";
  const lbl=s||"unknown";
  return `<span class="tag" style="background:var(--chip);color:${col};font-weight:700">${lbl}</span>`;
}
function renderUsers(){
  _rowsLoading("users-rows","renderUsers()");
  fetch("/api/users",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{_rowsLoaded("users-rows");const subs=j.subscriptions||["gold","silver","guest"];
      $("users-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${(j.users||[]).length}</b> accounts`;
      $("users-rows").innerHTML=(j.users||[]).map(u=>`<tr>
        <td><b>${u.name}</b></td><td>${u.email||''}</td>
        <td style="text-align:center"><label style="cursor:pointer" title="Admin — full access"><input type="checkbox" ${u.admin?'checked':''} onchange="saveUser('${u.name}',{admin:this.checked})"></label></td>
        <td style="text-align:center"><label style="cursor:pointer" title="Support — read-only: System Logs, Batch Activity, Scheduled Jobs — nothing else admin-only"><input type="checkbox" ${u.support?'checked':''} onchange="saveUser('${u.name}',{support:this.checked})"></label></td>
        <td><select onchange="saveUser('${u.name}',{subscription:this.value})" style="width:auto">${subs.map(s=>`<option ${u.subscription===s?'selected':''}>${s}</option>`).join("")}</select></td>
        <td><button class="btn" onclick="saveUser('${u.name}',{enabled:${!u.enabled}})">${u.enabled?'<span style="color:var(--bull)">Enabled</span> — disable':'<span style="color:var(--bear)">Disabled</span> — enable'}</button></td>
        <td>${_discEditor(u)}</td>
        <td>${pwStrength(u.pwd_strength)}${u.has_ig===false
            ? `<div><button class="btn" style="padding:2px 6px;font-size:11px;margin-top:4px" title="Set a temporary password to give to the user out-of-band (e.g. when email isn't configured). Admin only; not available for IG-linked accounts." onclick="setTempPassword('${u.name}')">Set temp password</button></div>`
            : (u.has_ig===true ? `<div class="muted" style="font-size:10.5px;margin-top:4px" title="This account has IG credentials, so a temporary password cannot be set here — it must use the email-based reset.">🔒 IG-linked — use email reset</div>` : ``)}</td>
        <td>${_igAuditCell(u)}</td></tr>`).join("");
      paintRequests(j.requests||[]);})
    .catch(()=>{$("users-rows").innerHTML=`<tr><td colspan="9" class="empty">Could not load (admin only).</td></tr>`;});
}
// Per-user fee-discount editor (user 2026-08-02, P-20/P-40): mgmt % + perf % + optional start/end window,
// Save, and a history reveal. Both default 0; dates default none (open-ended).
function _discEditor(u){
  const d=(u.fee_discount||{}), n=u.name, hist=d.history||[];
  const esc=n.replace(/'/g,"\\'");
  const inp=(k,v,ph)=>`<input id="disc-${k}-${n}" type="${k==='m'||k==='p'?'number':'date'}" ${k==='m'||k==='p'?'min="0" max="100" step="0.5"':''} value="${v!=null?v:''}" placeholder="${ph}" style="width:${k==='m'||k==='p'?'52px':'128px'};padding:3px 5px;font-size:12px">`;
  const active = d.mgmt_pct>0||d.perf_pct>0;
  return `<div style="display:flex;flex-direction:column;gap:4px;min-width:300px;background:color-mix(in srgb,var(--muted) 6%,transparent);border:1px solid var(--line);border-radius:8px;padding:8px 10px">
    <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
      <span class="muted" style="font-size:11px">Mgmt</span>${inp('m',d.mgmt_pct,'0')}<span class="muted" style="font-size:11px">%</span>
      <span class="muted" style="font-size:11px">Perf</span>${inp('p',d.perf_pct,'0')}<span class="muted" style="font-size:11px">%</span>
    </div>
    <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
      <span class="muted" style="font-size:11px">From</span>${inp('s',d.start,'')}
      <span class="muted" style="font-size:11px">To</span>${inp('e',d.end,'')}
    </div>
    <div style="display:flex;align-items:center;gap:8px">
      <button class="btn" style="padding:3px 9px;font-size:12px" onclick="saveDiscount('${esc}')">Save discount</button>
      ${active?`<span class="tag" style="background:color-mix(in srgb,var(--bull) 22%,transparent);color:var(--bull);border:1px solid var(--bull);font-size:11px;font-weight:700">✓ active</span>`:''}
      ${hist.length?`<a href="#" style="font-size:11px" onclick="toggleDiscHist('${esc}');return false">history (${hist.length})</a>`:''}
    </div>
    <div id="disc-hist-${n}" style="display:none;font-size:11px" class="muted"></div>
  </div>`;
}
function saveDiscount(name){
  const g=k=>($("disc-"+k+"-"+name)||{}).value;
  const patch={fee_discount:{mgmt_pct:parseFloat(g('m'))||0,perf_pct:parseFloat(g('p'))||0,start:g('s')||null,end:g('e')||null}};
  saveUser(name,patch);
}
function toggleDiscHist(name){
  const box=$("disc-hist-"+name); if(!box)return;
  if(box.style.display!=="none"){box.style.display="none";return;}
  // Pull the freshest history from the last render's data via a small refetch of /api/users.
  fetch("/api/users",{headers:{"X-Auth":AUTH}}).then(r=>r.json()).then(j=>{
    const u=(j.users||[]).find(x=>x.name===name)||{}, h=((u.fee_discount||{}).history)||[];
    box.innerHTML = h.length? h.slice().reverse().map(e=>`• Mgmt ${e.mgmt_pct||0}% / Perf ${e.perf_pct||0}%`+
      `${e.start||e.end?` · ${e.start||'—'}→${e.end||'—'}`:''}`+
      `${e.set_by?` · set by ${e.set_by}`:''}${e.retired_at?` · retired ${e.retired_at}`:''}`).join("<br>")
      : "No prior discounts.";
    box.style.display="";
  }).catch(()=>{box.innerHTML="Could not load history.";box.style.display="";});
}
// Per-user IG account audit trail (user 2026-08-03, P-25): a reveal that lazily fetches the encrypted
// IG-identity history from /api/ig-account-audit (admin only). The full account number never leaves the
// server — only the account name + a masked last-3 number are shown, newest first.
function _igAuditCell(u){
  const n=u.name, esc=n.replace(/'/g,"\\'");
  return `<div style="min-width:150px">
    <a href="#" style="font-size:11px" onclick="toggleIgAudit('${esc}');return false">IG audit ▾</a>
    <div id="igaud-${n}" style="display:none;font-size:11px;margin-top:4px;max-width:260px;white-space:normal" class="muted"></div>
  </div>`;
}
function toggleIgAudit(name){
  const box=$("igaud-"+name); if(!box)return;
  if(box.style.display!=="none"){box.style.display="none";return;}
  box.className="muted sqh-loading"; box.innerHTML="⏳ Data loading…"; box.style.display="";
  fetch("/api/ig-account-audit?user="+encodeURIComponent(name),{headers:{"X-Auth":AUTH}})
    .then(r=>{if(!r.ok)throw 0;return r.json();}).then(j=>{
      box.classList.remove("sqh-loading");
      const a=j.audit||[];
      box.innerHTML = a.length ? a.map(e=>
        `• <b style="color:var(--fg)">${(e.account_name||'—').replace(/</g,"&lt;")}</b> ${e.account_masked||''}`+
        ` <span class="muted">· ${e.at||''}${e.source?` · ${e.source}`:''}</span>`).join("<br>")
        : "No IG account captured yet — it records when the credentials change or the IG Account tab is opened.";
    }).catch(()=>{box.classList.remove("sqh-loading");box.innerHTML="Could not load (admin only).";});
}
// Admin "+ Add user" modal (user 2026-08-07): creates a locked account directly, no self-service
// request needed. Styled to match appConfirm. Resolves {name,email,subscription,admin} or null.
// Pragmatic client-side email check (user 2026-08-08) — mirrors web_users.valid_email server-side.
// Rejects clearly-invalid addresses (no "@", spaces/commas, missing dotted domain) e.g. "x@gmail,com".
function validEmail(e){return /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/.test((e||"").trim());}
function appAddUser(){
  return new Promise(res=>{
    const ov=document.createElement("div");
    ov.style.cssText="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px";
    const box=document.createElement("div");
    box.style.cssText="background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:10px;max-width:420px;width:100%;box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden";
    box.innerHTML=`<div style="padding:14px 18px;border-bottom:1px solid var(--line);font-weight:700">Add user</div>`
      +`<div style="padding:16px 18px;display:flex;flex-direction:column;gap:10px;font-size:13.5px">`
      +`<label>Name<br><input id="au-name" type="text" style="width:100%;margin-top:4px;box-sizing:border-box" autocomplete="off"></label>`
      +`<label>Email<br><input id="au-email" type="email" style="width:100%;margin-top:4px;box-sizing:border-box" autocomplete="off"></label>`
      +`<label>Subscription<br><select id="au-sub" style="width:100%;margin-top:4px"><option value="guest">guest</option><option value="silver">silver</option><option value="gold">gold</option></select></label>`
      +`<label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input id="au-admin" type="checkbox"> Admin</label>`
      +`<label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input id="au-support" type="checkbox"> Support <span class="muted" style="font-size:11px">(read-only ops: System Logs, Batch Activity, Scheduled Jobs)</span></label>`
      +`<div class="muted" style="font-size:11.5px">Created locked — the new user sets their password via 'Forgot password?' using this email.</div>`
      +`<div id="au-err" style="color:var(--bear);font-size:12px;min-height:16px"></div>`
      +`</div>`
      +`<div style="padding:12px 18px;border-top:1px solid var(--line);display:flex;gap:8px;justify-content:flex-end">`
      +`<button class="btn" data-x>Cancel</button>`
      +`<button class="btn" data-ok style="border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent)">Create</button></div>`;
    const done=v=>{ov.remove();document.removeEventListener("keydown",onKey);res(v);};
    const submit=()=>{
      const nm=box.querySelector("#au-name").value.trim(), em=box.querySelector("#au-email").value.trim();
      if(!nm){box.querySelector("#au-err").textContent="Name is required.";return;}
      if(!validEmail(em)){box.querySelector("#au-err").textContent="Enter a valid email address (e.g. name@example.com).";return;}
      done({name:nm,email:em,subscription:box.querySelector("#au-sub").value,admin:box.querySelector("#au-admin").checked,support:box.querySelector("#au-support").checked});
    };
    const onKey=e=>{if(e.key==="Escape")done(null);else if(e.key==="Enter"&&document.activeElement?.tagName!=="SELECT")submit();};
    box.querySelector("[data-x]").onclick=()=>done(null);
    box.querySelector("[data-ok]").onclick=submit;
    ov.onclick=e=>{if(e.target===ov)done(null);};
    document.addEventListener("keydown",onKey);
    ov.appendChild(box);document.body.appendChild(ov);
    box.querySelector("#au-name").focus();
  });
}
async function addUser(){
  const r=await appAddUser();
  if(!r)return;
  fetch("/api/users",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},
    body:JSON.stringify({name:r.name,action:"create",email:r.email,subscription:r.subscription,admin:r.admin,support:r.support})})
    .then(x=>x.json())
    .then(j=>{
      if(!j.ok){$("users-msg").style.color="var(--bear)";$("users-msg").textContent=j.error||"Could not create user.";return;}
      $("users-msg").style.color="var(--bull)";$("users-msg").textContent=`Created ${r.name}. They'll receive a setup email at ${r.email}.`;
      renderUsers();
    })
    .catch(()=>{$("users-msg").style.color="var(--bear)";$("users-msg").textContent="Could not create user.";});
}
function paintRequests(reqs){
  $("req-section").style.display=reqs.length?"":"none";
  $("req-count").textContent=reqs.length?`(${reqs.length})`:"";
  $("req-rows").innerHTML=reqs.map(r=>`<tr>
    <td>${r.ts||''}</td><td><b>${r.name}</b></td><td>${r.email||''}</td><td class="muted" style="white-space:normal;max-width:260px">${(r.note||'').replace(/</g,"&lt;")}</td>
    <td><button class="btn" style="padding:3px 8px" onclick="reqAction('${r.name}','approve')">✓ Approve</button> <button class="btn" style="padding:3px 8px" onclick="reqAction('${r.name}','reject')">✕ Reject</button></td></tr>`).join("");
}
async function reqAction(name,action){
  if(action==="approve"&&!await appConfirm(`Creates a guest account (locked) and emails them a setup link at their registered address so they can set a password.`,{title:`Approve '${name}'?`,ok:"✓ Approve"}))return;
  fetch("/api/users",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({name,action})})
    .then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(()=>{$("users-msg").style.color="var(--bull)";$("users-msg").textContent=`${action==='approve'?'Approved':'Rejected'} ${name}.`;renderUsers();})
    .catch(()=>{$("users-msg").style.color="var(--bear)";$("users-msg").textContent="Action failed.";});
}
function saveUser(name,patch){
  fetch("/api/users",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({name,...patch})})
    .then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(()=>{$("users-msg").style.color="var(--bull)";$("users-msg").textContent=`Updated ${name}.`;renderUsers();})
    .catch(()=>{$("users-msg").style.color="var(--bear)";$("users-msg").textContent="Update failed.";});
}
// Documentation tab (user 2026-08-08, P-13): lists the guide .docx the current role may see and downloads
// them. The server filters by role too; downloads go through fetch with the X-Auth header (a plain <a>
// link can't carry it), so the token never appears in a URL.
const DOC_CATS={"User Guide":{n:"01",blurb:"Everyday guides for anyone with a login."},
                "Support":{n:"02",blurb:"For Support and Admin staff helping users and running the desk."},
                "Operations":{n:"03",blurb:"For Admin staff operating and maintaining the platform."}};
function renderDocs(){
  const box=$("docs-list");
  if(!box)return;
  box.innerHTML=`<div class="muted"><span class="sqh-loading">⏳ Data loading…</span></div>`;
  // Not a table, so it cannot use _rowsLoading; same guarantee though -- a hung request must report.
  clearTimeout(_ROWS_WATCHDOG["docs-list"]);
  _ROWS_WATCHDOG["docs-list"]=setTimeout(()=>{
    if(box&&/Data loading/.test(box.textContent))
      box.innerHTML=`<div class="empty" role="alert"><b style="color:var(--bear)">⚠ Could not load.</b> `+
        `<span class="muted">This is taking longer than expected and may have failed.</span> `+
        `<button class="btn" style="margin-left:8px" onclick="renderDocs()">↻ Retry</button></div>`;
  },45000);
  fetch("/api/guides",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{
      const gs=j.guides||[];
      clearTimeout(_ROWS_WATCHDOG["docs-list"]);
      if(!gs.length){box.innerHTML=`<div class="empty">No documents are available for your role yet.</div>`;return;}
      const byCat={}; gs.forEach(g=>{(byCat[g.category]=byCat[g.category]||[]).push(g);});
      const order=[...Object.keys(DOC_CATS).filter(c=>byCat[c]),...Object.keys(byCat).filter(c=>!DOC_CATS[c])];
      box.innerHTML=order.map(c=>{
        const m=DOC_CATS[c]||{n:"",blurb:""};
        return `<section class="doc-cat">
          <div class="doc-cat-head">${m.n?`<span class="num">${m.n}</span>`:""}<div><h2>${c}</h2>${m.blurb?`<p>${m.blurb}</p>`:""}</div></div>
          <div class="doc-tiles">`+
          byCat[c].map(g=>`<button class="doc-tile" onclick="openGuide('${g.slug}')">
            <span class="doc-tile-type">Guide</span>
            <h4>${(g.title||g.slug).replace(/</g,"&lt;")}</h4>
            <p>${(g.subtitle||"").replace(/</g,"&lt;")}</p>
            <strong>Read →</strong></button>`).join("")+
          `</div></section>`;
      }).join("");
    })
    .catch(()=>{box.innerHTML=`<div class="empty">Could not load the documentation list.</div>`;});
}
function openGuide(slug){
  const box=$("docs-list");
  if(!box)return;
  const back=`<button class="btn" style="margin-bottom:14px" onclick="renderDocs()">← All documents</button>`;
  box.innerHTML=`<div class="muted"><span class="sqh-loading">⏳ Data loading…</span></div>`;
  // The HTML is our own build-time content (escaped in _build_guides.js) and role-gated by the server.
  fetch("/api/guides/"+encodeURIComponent(slug),{headers:{"X-Auth":AUTH}})
    .then(r=>{if(!r.ok)throw 0;return r.text();})
    .then(html=>{box.innerHTML=back+html;window.scrollTo(0,0);})
    .catch(()=>{box.innerHTML=back+`<div class="empty">Could not open that document — you may not have access.</div>`;});
}
// Admin-only temporary-password modal (user 2026-08-08): for activating a locked account out-of-band when
// email isn't configured. Only offered for accounts WITHOUT IG credentials (the server enforces this too).
function _genTempPassword(){
  const cs="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
  const a=new Uint32Array(14); (crypto||window.crypto).getRandomValues(a);
  return Array.from(a,x=>cs[x%cs.length]).join("");
}
function appSetTempPassword(name){
  return new Promise(res=>{
    const ov=document.createElement("div");
    ov.style.cssText="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px";
    const box=document.createElement("div");
    box.style.cssText="background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:10px;max-width:440px;width:100%;box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden";
    box.innerHTML=`<div style="padding:14px 18px;border-bottom:1px solid var(--line);font-weight:700">Set temporary password — ${name}</div>`
      +`<div style="padding:16px 18px;display:flex;flex-direction:column;gap:10px;font-size:13.5px">`
      +`<div class="muted" style="font-size:11.5px">For activating an account out-of-band when email isn't configured. Give this password to the user through a secure channel; they can change it later. Not available for accounts with IG credentials.</div>`
      +`<label>Temporary password<br><div style="display:flex;gap:6px;margin-top:4px"><input id="stp-pwd" type="text" style="flex:1;box-sizing:border-box" autocomplete="off" spellcheck="false"><button class="btn" data-gen type="button" style="white-space:nowrap">Generate</button></div></label>`
      +`<div id="stp-err" style="color:var(--bear);font-size:12px;min-height:16px"></div>`
      +`</div>`
      +`<div style="padding:12px 18px;border-top:1px solid var(--line);display:flex;gap:8px;justify-content:flex-end">`
      +`<button class="btn" data-x>Cancel</button>`
      +`<button class="btn" data-ok style="border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent)">Set password</button></div>`;
    const done=v=>{ov.remove();document.removeEventListener("keydown",onKey);res(v);};
    box.querySelector("#stp-pwd").value=_genTempPassword();
    box.querySelector("[data-gen]").onclick=()=>{box.querySelector("#stp-pwd").value=_genTempPassword();};
    const submit=()=>{
      const pw=box.querySelector("#stp-pwd").value.trim();
      if(pw.length<4){box.querySelector("#stp-err").textContent="At least 4 characters.";return;}
      done(pw);
    };
    const onKey=e=>{if(e.key==="Escape")done(null);else if(e.key==="Enter")submit();};
    box.querySelector("[data-x]").onclick=()=>done(null);
    box.querySelector("[data-ok]").onclick=submit;
    ov.onclick=e=>{if(e.target===ov)done(null);};
    document.addEventListener("keydown",onKey);
    ov.appendChild(box);document.body.appendChild(ov);
    box.querySelector("#stp-pwd").focus();box.querySelector("#stp-pwd").select();
  });
}
async function setTempPassword(name){
  const pw=await appSetTempPassword(name);
  if(!pw)return;
  fetch("/api/users",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},
    body:JSON.stringify({name,action:"set_temp_password",new_pwd:pw})})
    .then(x=>x.json())
    .then(j=>{
      if(!j.ok){$("users-msg").style.color="var(--bear)";$("users-msg").textContent=j.error||"Could not set the password.";return;}
      $("users-msg").style.color="var(--bull)";$("users-msg").textContent=`Temporary password set for ${name}: ${pw} — give it to them securely; they can change it later.`;
      renderUsers();
    })
    .catch(()=>{$("users-msg").style.color="var(--bear)";$("users-msg").textContent="Could not set the password.";});
}
let VER_ROWS=[], verSortK="date", verSortDir=-1;
const _vmonth=e=>(e.date||'').slice(0,7);   // YYYY-MM
function paintVersion(){
  const cats=VER_ROWS.reduce((m,e)=>{m[e.category||'Feature']=(m[e.category||'Feature']||0)+1;return m;},{});
  const months=VER_ROWS.reduce((m,e)=>{const v=_vmonth(e)||'—';m[v]=(m[v]||0)+1;return m;},{});
  $("ver-viz").innerHTML=`<div class="vizsector">`+barChart("By category",cats,"vcf_category")+`</div>`+
    `<div class="vizsector">`+barChart("By month",months,"vmf_month",null,true)+`</div>`+
    `<div class="vizsector">`+barChart("Month-Week",VER_ROWS.reduce((m,e)=>{const v=_mw(e.date);m[v]=(m[v]||0)+1;return m;},{}),"vmf_mweek",null,true)+`</div>`;
  if($("ver-datebar")&&!$("ver-datebar").innerHTML)$("ver-datebar").innerHTML=dateFilterBar("ver","paintVersion()");
  const _vq=(($("ver-search")||{}).value||"").trim().toLowerCase();
  let rows=VER_ROWS.filter(e=>inSet("vcf_category",e.category||'Feature')&&inSet("vmf_month",_vmonth(e))&&inSet("vmf_mweek",_mw(e.date))&&(!_vq||(e.summary||'').toLowerCase().includes(_vq)));
  rows=applyDateFilter("ver",rows,e=>e.date);
  rows=genSort(rows,verSortK,verSortDir);
  const filt=(setOf("vcf_category")||setOf("vmf_month")||dateActive("ver"));
  $("vertab-count")&&($("vertab-count").textContent=`(${VER_ROWS.length})`);
  $("ver-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> changes${filt?` <span class="muted">of ${VER_ROWS.length}</span>`:''}`;
  $("ver-rows").innerHTML=rows.map(e=>`<tr><td style="white-space:nowrap">${e.date||''}</td><td style="white-space:nowrap"><code>${e.version}</code></td><td style="white-space:nowrap"><span class="tag" style="background:var(--chip);color:var(--fg)">${e.category||'Feature'}</span></td><td>${(e.summary||'').replace(/</g,"&lt;")}</td></tr>`).join("")||`<tr><td colspan="4" class="empty">No entries.</td></tr>`;}
// Shared "Data loading…" row for the admin tables (user 2026-08-23: "when data sets loading are slow we
// need to see 'Data loading' e.g. batch activity, system logs ... perhaps all in this section").
// Scheduled Jobs and Change Requests already did this by hand; Batch Activity, System Logs, Version
// History and My Activity showed an empty table until their fetch returned, which on a slow admin query
// is indistinguishable from "there is nothing here".
// The column span is read from the table's own header, so it stays right when a column is added.
function _rowsCols(body){
  const table=body.closest("table");
  return table?Math.max(1,table.querySelectorAll("thead th").length):1;
}
// A "Data loading" message must never be the LAST thing a user sees. If the fetch fails, or simply never
// returns, the same row is replaced by what went wrong plus a way to retry (user 2026-08-23: "if there is
// a fault and you are not able to resolve - provide this as an update where you may previously put 'data
// loading'"). The watchdog covers what no catch handler can: a request that HANGS rather than rejecting,
// which would otherwise spin forever and merely look like slow data.
const _ROWS_WATCHDOG={};
function _rowsLoading(id,retry){
  const body=$(id); if(!body)return;
  body.innerHTML=`<tr><td colspan="${_rowsCols(body)}" class="empty"><span class="sqh-loading">⏳ Data loading…</span></td></tr>`;
  clearTimeout(_ROWS_WATCHDOG[id]);
  _ROWS_WATCHDOG[id]=setTimeout(()=>{
    const b=$(id);
    if(b&&/Data loading/.test(b.textContent))
      _rowsFault(id,"This is taking longer than expected and may have failed.",retry);
  },45000);
}
function _rowsFault(id,message,retry){
  const body=$(id); if(!body)return;
  clearTimeout(_ROWS_WATCHDOG[id]);
  const again=retry?` <button class="btn" style="margin-left:8px" onclick="${retry}">↻ Retry</button>`:"";
  body.innerHTML=`<tr><td colspan="${_rowsCols(body)}" class="empty" role="alert">`+
    `<b style="color:var(--bear)">⚠ Could not load.</b> <span class="muted">${message||""}</span>${again}</td></tr>`;
}
function _rowsLoaded(id){ clearTimeout(_ROWS_WATCHDOG[id]); }
function renderVersion(){_rowsLoading("ver-rows","renderVersion()");fetch("/api/version-history",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();}).then(j=>{_rowsLoaded("ver-rows");VER_ROWS=j.entries||[];paintVersion();}).catch(()=>_rowsFault("ver-rows","Version history is admin-only; check you are still signed in.","renderVersion()"));}
let BATCH_ROWS=[], batchSortK="ts", batchSortDir=-1;
const _bmonth=e=>(e.ts||'').slice(0,7);   // YYYY-MM (date range chart)
function paintBatch(){
  // Charts (user 2026-07-03): Source, By, and month (date range) — all click-to-filter.
  const by=(fn)=>BATCH_ROWS.reduce((m,e)=>{const v=fn(e)||'—';m[v]=(m[v]||0)+1;return m;},{});
  $("batch-viz").innerHTML=
    `<div class="vizsector">`+barChart("Source",by(e=>e.source),"bcf_source")+`</div>`+
    `<div class="vizsector">`+barChart("Operator",by(e=>e.by),"bcf_by")+`</div>`+
    `<div class="vizsector">`+barChart("Month",by(_bmonth),"bcf_month",null,true)+`</div>`+
    `<div class="vizsector">`+barChart("Month-Week",by(e=>_mw(e.ts)),"bcf_mweek",null,true)+`</div>`;
  if($("batch-datebar")&&!$("batch-datebar").innerHTML)$("batch-datebar").innerHTML=dateFilterBar("batch","paintBatch()");
  // Operator dropdown (user 2026-08-03, P-65): lists EVERY operator (the chart caps at 8) and drives the
  // same bcf_by filter key, so it stays in sync with the Operator chart's brush. A single chart-selected
  // value shows here; multiple brushed values fall back to "All operators" (the dropdown can't hold a set).
  const _op=$("batch-op");
  if(_op){const ops=[...new Set(BATCH_ROWS.map(e=>e.by||'—'))].sort((a,b)=>a.localeCompare(b));
    const bs=setOf("bcf_by"); const want=(bs&&bs.size===1)?[...bs][0]:"";
    _op.innerHTML=`<option value="">All operators</option>`+ops.map(o=>`<option value="${String(o).replace(/"/g,'&quot;')}"${o===want?' selected':''}>${String(o).replace(/</g,"&lt;")}</option>`).join("");
    _op.value=want;}
  let rows=BATCH_ROWS;
  [["bcf_source",e=>e.source],["bcf_by",e=>e.by],["bcf_month",_bmonth],["bcf_mweek",e=>_mw(e.ts)]].forEach(([id,fn])=>{
    rows=rows.filter(e=>inSet(id,fn(e)));});
  rows=applyDateFilter("batch",rows,e=>e.ts);
  const _bq=(($("batch-search")||{}).value||"").toLowerCase().trim();   // free-text search by event (user 2026-07-17)
  if(_bq)rows=rows.filter(e=>(e.event||"").toLowerCase().includes(_bq));
  $("batab-count")&&($("batab-count").textContent=`(${BATCH_ROWS.length})`);
  $("batch-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> batch runs${rows.length!==BATCH_ROWS.length?` <span class="muted">of ${BATCH_ROWS.length}</span>`:''}`;
  $("batch-rows").innerHTML=genSort(rows,batchSortK,batchSortDir).map(e=>`<tr><td>${e.ts||''}</td><td><span class="tag" style="background:var(--chip);color:var(--fg)">${e.source||''}</span></td><td>${(e.event||'').replace(/</g,"&lt;")}</td><td>${e.by||''}</td></tr>`).join("")||`<tr><td colspan="4" class="empty">No entries.</td></tr>`;}
function renderBatch(){_rowsLoading("batch-rows","renderBatch()");fetch("/api/batch-activity",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();}).then(j=>{_rowsLoaded("batch-rows");BATCH_ROWS=j.entries||[];paintBatch();}).catch(()=>_rowsFault("batch-rows","Batch activity is admin-only; check you are still signed in.","renderBatch()"));}
// P-65 (user 2026-08-03): the Operator dropdown writes the single-select choice into the shared bcf_by
// filter key (empty = All), so picking here and clicking the Operator chart stay in one source of truth.
function batchOpPick(){const el=$("batch-op"),bc=$("bcf_by");if(!el||!bc)return;bc.value=el.value||"";paintBatch();}
function renderTabVis(hidden){
  hidden=new Set(hidden||HIDDEN_TABS||[]);
  const allow=t=>(PUBLIC_TABS.includes(t)||AUTH)&&(!ADMIN_TABS.includes(t)||IS_ADMIN||(IS_SUPPORT&&SUPPORT_TABS.includes(t)));
  const chip=(t,checked,dis)=>`<label style="display:inline-flex;align-items:center;gap:5px;font-size:13px;background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:5px 10px;cursor:${dis?'default':'pointer'};${dis?'opacity:.6':''}"><input type="checkbox" ${dis?'class="" disabled':'class="tv"'} data-t="${t}" ${checked?'checked':''}> ${TAB_LABELS[t]||t}</label>`;
  // Default-hidden tabs tick only when opted in (SHOWN_TABS); others tick unless hidden.
  const chipFor=t=>chip(t,DEFAULT_HIDDEN.has(t)?SHOWN_TABS.includes(t):!hidden.has(t),false);
  // Grouped, and ordered by the tab bar WITHIN each group (user 2026-08-22: "sort the tabs in visibility
  // section in the order they are on the screen", then "the groupings have been removed - please put them
  // back and order within the groupings"). The first pass dropped the headings entirely, which lost the
  // grouping; the point was only ever that the ORDER inside them was arbitrary, so a tab was hard to find.
  const head=x=>`<div class="muted" style="width:100%;font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin:12px 0 2px">${x}</div>`;
  const INSTR=["performance","scanner","instruments","preorders","orderops","igaccount"];   // Instrument Operations group (user 2026-07-11; "instruments" joined 2026-08-07, ChangeRequest P-08)
  const grouped=new Set([].concat(...Object.values(TAB_GROUPS),INSTR,["activity"]));   // My Activity shown under Application Operations (user 2026-07-11)
  const nav=screenTabOrder();
  const byScreen=list=>[...list].sort((a,b)=>nav.indexOf(a)-nav.indexOf(b));   // tab-bar order inside the group
  const setg=byScreen(TAB_GROUPS.settings.filter(t=>t!=="config"&&allow(t)));
  // X Posts is an admin Operations tab and is not user-configurable.
  const ops=byScreen([...TAB_GROUPS.operations,"activity"].filter(allow));
  const instr=byScreen(INSTR.filter(allow));
  const rest=byScreen(TABS.filter(t=>t!=="config"&&!grouped.has(t)&&allow(t)));
  const chipUsers=t=>(t==="users"&&IS_ADMIN)?chip(t,true,true):chipFor(t);   // admins can't disable User Management
  let html=head("Settings")+chip("config",true,true)+setg.map(chipFor).join("");
  if(instr.length)html+=head("Instrument Operations")+instr.map(chipFor).join("");
  if(ops.length)html+=head("Application Operations")+ops.map(chipUsers).join("");
  if(rest.length)html+=head("Other")+rest.map(chipFor).join("");
  $("tabvis-list").classList.remove("sqh-loading");
  $("tabvis-list").innerHTML=html;
}
function saveTabVis(){
  const boxes=[...document.querySelectorAll(".tv")];
  const hidden=boxes.filter(c=>!c.checked).map(c=>c.dataset.t);
  const shown=boxes.filter(c=>c.checked&&DEFAULT_HIDDEN.has(c.dataset.t)).map(c=>c.dataset.t);   // opt-in for default-hidden tabs
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({hidden_tabs:hidden,shown_tabs:shown})})
    .then(r=>{if(!r.ok)throw 0;HIDDEN_TABS=hidden;SHOWN_TABS=shown;applyTabVisibility();$("tabvis-msg").style.color="var(--bull)";$("tabvis-msg").textContent="Saved.";})
    .catch(()=>{$("tabvis-msg").style.color="var(--bear)";$("tabvis-msg").textContent="Save failed.";});
}
document.querySelectorAll("th[data-vk]").forEach(th=>th.onclick=()=>{const k=th.dataset.vk;verSortDir=(verSortK===k)?-verSortDir:-1;verSortK=k;paintVersion();_sortArrows("data-vk",verSortK,verSortDir);});
document.querySelectorAll("th[data-bk]").forEach(th=>th.onclick=()=>{const k=th.dataset.bk;batchSortDir=(batchSortK===k)?-batchSortDir:-1;batchSortK=k;paintBatch();_sortArrows("data-bk",batchSortK,batchSortDir);});
// ── Configuration tab (user 2026-07-03): per-user filter defaults + shared execution switches ──────
let USER_FILTERS={};
// Saved filter defaults now span three surfaces: what is left of the Scanner (search + the hidden
// market/sector scope + the chart window), the Pre-orders tabs, and the Squeeze History filters that
// moved off the Scanner on 2026-08-16.
// f_days dropped 2026-08-17 with its slider. A saved f_days may still sit in an existing user's stored
// defaults; applyUserDefaults ignores keys whose element is gone, so it is inert rather than an error.
const FILTER_IDS=()=>F.concat(["pof_direction","pof_status","pof_location","pof_market","pof_timeframe","pof_sector"],
                              SQH_FILTER_IDS());
function applyUserDefaults(){
  Object.entries(USER_FILTERS).forEach(([k,v])=>{const el=$(k);if(el&&v!=null&&v!=="")el.value=v;});
}
// Built-in default chart-Status selections (user 2026-07-25, P-05 L188/L203/L225/L341). Seeded ONCE on
// load, only when the filter is unset, so the user's own click / saved default always wins and
// reset/showall can clear it. Values are the chart's raw status codes, SEP-joined for multi-select.
let _statusSeeded=false;
function _seedStatusDefaults(){
  if(_statusSeeded)return; _statusSeeded=true;
  const seed=(id,vals)=>{const el=$(id); if(el&&!el.value)el.value=vals.join(SEP);};
  if(!(USER_FILTERS&&USER_FILTERS.mf_status))seed("mf_status",["TRIGGERED"]);   // Scanner default = Triggered (user 2026-08-06)
  seed("pof_status",["READY","TRIGGERED"]);   // My Pre-orders (L203)
  seed("oof_status",["PENDING","WATCHING"]);  // Pre-orders to IG — Pending + Waiting (L225)
  // Squeeze History: no default outcome pre-selection (user 2026-08-01) — show every outcome, sorted by
  // triggered date desc (see sqhSortK default).
}
// Gold-tier gate — the SINGLE rule for every Gold-only feature (Trading (Momentum) config panel, the
// Multi-Factor Momentum tab). Gold means Gold (user 2026-07-17): admin is an access-control axis, not a
// subscription level, so it no longer unlocks Gold features for a Silver/Guest account. Seed admins
// default to a gold subscription, so this does not lock them out of anything.
const goldOnly=()=>ROLE==="gold";
const advancedPfAllowed=()=>IS_ADMIN&&(ROLE==="silver"||ROLE==="gold");
function toggleAdvancedPf(){
  if(!advancedPfAllowed())return;
  const nav=$("pf-advanced-nav"); if(nav)nav.classList.remove("hidden");
  pfPanel("analysis");
}
// Configuration card navigation (user 2026-07-03): show one config panel at a time.
function confShow(panel){
  if(panel==="trading_mo"&&!goldOnly())panel="preferences";   // never open a Gold panel for a lesser tier
  document.querySelectorAll("#view-config .confpanel").forEach(p=>p.classList.toggle("hidden",p.dataset.panel!==panel));
  document.querySelectorAll("#confnav .pill").forEach(b=>b.classList.toggle("active",b.dataset.panel===panel));
}
// Configuration (Admin) sub-tabs — the same one-panel-at-a-time approach as Configuration (User), which
// the user rates highly (user 2026-07-17, P-28). Separate class (.confapanel) and nav from confShow's so
// the two pages cannot select each other's panels.
function confAdminShow(panel){
  document.querySelectorAll("#view-configadmin .confapanel").forEach(p=>p.classList.toggle("hidden",p.dataset.panel!==panel));
  document.querySelectorAll("#confadminnav .pill").forEach(b=>b.classList.toggle("active",b.dataset.panel===panel));
}
function renderCredentials(){
  fetch("/api/credentials",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{
      const cards=(j.sections||[]).map(sec=>{
        const ro=!sec.editable;
        const rows=sec.fields.map(f=>{
          const state=f.set?`<span class="cstate on">Set</span>`:`<span class="cstate off">Not set</span>`;
          const valchip=`<span class="cval${f.set?'':' unset'}" title="current value (last 4 shown)">${f.set?f.masked:'not set'}</span>`;
          // Read-only: show the value chip. Editable: show the current value chip + an input to change it.
          const right=ro?valchip
            :`${valchip}<input type="password" class="cred-in" data-sec="${sec.id}" data-key="${f.key}" placeholder="${f.set?'change…':'enter value'}" autocomplete="new-password">`;
          // Per-webhook Slack send/not-send toggle (user 2026-08-01) — one against EACH channel.
          const slackTog=(sec.id==="Slack")?`<label style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;margin-left:12px;flex:none" title="Send alerts to this channel"><input type="checkbox" class="slack-ch" data-ch="${f.key.replace('slack_','')}" onchange="saveSlackChannel(this.dataset.ch,this.checked)" style="width:16px;height:16px;accent-color:var(--accent)"><b style="color:var(--fg);font-size:12px">send</b></label>`:"";
          return `<div class="credrow"><span class="clabel">${f.label} ${state}</span>${right}${slackTog}</div>`;})
          .join("");
        // A locked section must say WHY it is locked. The default wording blames the administrator and
        // shared credentials, which is right for Supabase/Slack and plainly wrong for a guest looking at
        // their OWN IG section, so the server sends the reason with the section (user 2026-08-30).
        const btn=ro?`<span class="muted" style="font-size:12px">🔒 ${_esc(sec.locked_reason||"Read-only — the administrator manages these shared credentials.")}</span>`
          :`<button class="btn" style="margin-top:6px" onclick="saveCreds('${sec.id}')">Save ${sec.id} credentials</button>`;
        const amber=sec.admin_only?' style="border:1px solid #d29922;background:color-mix(in srgb,#d29922 8%,transparent)"':'';
        const head=`<h4>${sec.id}${sec.admin_only?' <span style="color:#d29922;font-size:11px;font-weight:600">🔶 ADMIN</span>':sec.scope==='app'?' <span class="muted" style="font-size:11px;font-weight:400">(shared)</span>':' <span class="muted" style="font-size:11px;font-weight:400">(your account)</span>'}</h4>
          <p class="muted" style="font-size:12px;margin:0 0 8px">${sec.note}${sec.id==="Slack"?' <b style="color:var(--fg)">Tick "send" beside a channel to enable it; untick to silence that webhook.</b>':''}</p>${rows}${btn}
          <div id="cred-msg-${sec.id}" style="font-size:12px;margin-top:6px"></div>`;
        // Admin-only sections live on Configuration (Admin), user sections on Configuration (User)
        // (user 2026-07-17, P-03) — each as a switchable panel behind its own pill (P-28).
        return sec.admin_only
          ? {admin:true, id:sec.id, html:`<div class="confapanel hidden" data-panel="${sec.id}"><div class="card" style="margin-bottom:0"${amber}>${head}</div></div>`}
          : {admin:false, html:`<div class="confpanel hidden" data-panel="${sec.id}"><div class="card" style="margin-bottom:0"${amber}>${head}</div></div>`};
      });
      $("cfg-creds").classList.remove("sqh-loading");
      $("cfg-creds").innerHTML=cards.filter(c=>!c.admin).map(c=>c.html).join("");
      const adminBox=$("cfgadmin-creds");
      if(adminBox)adminBox.innerHTML=cards.filter(c=>c.admin).map(c=>c.html).join("");
      _applySlackToggles();   // reflect each channel's current send/off state on the just-rendered toggles
      // One nav pill per credential section: user sections onto Configuration (User) with Email (Yahoo)
      // last (user 2026-07-17, P-05); admin sections onto Configuration (Admin)'s own nav (P-28).
      document.querySelectorAll("#confnav .pill.credpill,#confadminnav .pill.credpill").forEach(p=>p.remove());
      const icons={IG:"🔑",Supabase:"🗄️","X Credentials":"𝕏",Slack:"💬",Server:"🖥️","Email (Yahoo)":"✉️"};
      (j.sections||[]).filter(s=>s.scope==="app").forEach(s=>{
        const admin=!!s.admin_only, nav=$(admin?"confadminnav":"confnav");
        if(!nav)return;
        const b=document.createElement("button");
        b.className="pill credpill"+(admin?" adminpill":"");
        b.dataset.panel=s.id; b.textContent=`${icons[s.id]||"🧩"} ${s.id}`;
        b.onclick=()=>(admin?confAdminShow:confShow)(s.id);
        nav.appendChild(b);
      });
      // Email (Yahoo) section is disabled — no longer used (user 2026-08-01): drop its pill.
      document.querySelectorAll('#confnav .credpill,#confadminnav .credpill').forEach(p=>{if(p.dataset.panel==="Email (Yahoo)")p.remove();});
      if(window.ENGINE_VALS)Object.entries(ENGINE_VALS).forEach(([k,v])=>{if($("eng-"+k))$("eng-"+k).value=v;});
      const cur=document.querySelector("#confnav .pill.active")?.dataset.panel;
      confShow(document.querySelector(`#view-config .confpanel[data-panel="${cur}"]`)?cur:"preferences");
      const curA=document.querySelector("#confadminnav .pill.active")?.dataset.panel;
      confAdminShow(document.querySelector(`#view-configadmin .confapanel[data-panel="${curA}"]`)?curA:"xpub");
    })
    .catch(()=>{$("cfg-creds").classList.remove("sqh-loading");$("cfg-creds").innerHTML=`<div class="muted">Could not load credentials — try logging in again.</div>`;});
}
function saveCreds(secId){
  const values={};
  document.querySelectorAll(`.cred-in[data-sec="${secId}"]`).forEach(i=>{if(i.value!=="")values[i.dataset.key]=i.value;});
  const msg=$("cred-msg-"+secId);
  if(!Object.keys(values).length){msg.style.color="var(--muted)";
    // The Slack "send" toggles save instantly on click — this button only saves changed webhook URLs.
    msg.textContent=secId==="Slack"?"Nothing to save — the ‘send’ checkboxes already save automatically when you tick them.":"No credential field was changed (enter a value to update it).";return;}
  fetch("/api/credentials",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({section:secId,values})})
    .then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{msg.style.color="var(--bull)";msg.textContent=`Saved ${(j.saved||[]).length} field(s).`;renderCredentials();if(secId==="IG"&&typeof _igStatus==="function")_igStatus(true);})
    .catch(()=>{msg.style.color="var(--bear)";msg.textContent="Save failed.";});
}
function renderConfig(){
  renderCredentials();
  // Trading (Momentum) is DISABLED — no longer used (user 2026-08-01): keep its pill hidden for everyone.
  const _tm=document.querySelector('#confnav .pill[data-panel="trading_mo"]');
  if(_tm)_tm.style.display="none";
  if(!goldOnly()&&document.querySelector('#view-config .confpanel[data-panel="trading_mo"]:not(.hidden)'))confShow("preferences");
  fetch("/api/config",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{
      USER_FILTERS=j.filters||{};
      HIDDEN_TABS=j.hidden_tabs||[]; renderTabVis(HIDDEN_TABS);   // tab-visibility card (user 2026-07-03)
      if(j.leverage){LEVERAGE=j.leverage;["fx","equities","commodities","indices"].forEach(k=>{if($("lev-"+k))$("lev-"+k).value=j.leverage[k];});}
      if(j.limits){MY_LIMITS=j.limits;Object.entries(j.limits).forEach(([k,v])=>{const el=$("lim-"+k);if(el){if(el.type==='checkbox')el.checked=!!v;else el.value=Array.isArray(v)?v.join(", "):v;}});if(typeof renderPreorders==='function')renderPreorders();if(typeof applyWinnersDefaults==='function')applyWinnersDefaults();if(typeof winnersRunChange==='function')winnersRunChange();}   // apply saved winners-Model defaults (P-10 L158) + render the let-winners-run card if opted in (P-08)
      // Engine settings (admin only) now live on the Configuration (Admin) tab; just fill the inputs.
      if(j.engine){window.ENGINE_VALS=j.engine;Object.entries(j.engine).forEach(([k,v])=>{if($("eng-"+k))$("eng-"+k).value=v;});}
      // Morning Squeeze tweet markets (admin, user 2026-07-06): checkbox chips over the universe's markets.
      if($("xhvf-markets")){const picked=new Set(j.x_hvf_markets||[]);
        // Use the canonical market list (P-15) — robust when DATA doesn't carry `market`. Bigger accent
        // checkboxes + roomier chips so the picker is legible/tappable on iPad (user 2026-08-02, P-12).
        const mkts=(typeof _refreshMktList==='function'?_refreshMktList():uniq("market"));
        $("xhvf-markets").classList.remove("sqh-loading");
        $("xhvf-markets").innerHTML=(mkts.length?mkts:[...picked]).map(m=>{const on=picked.has(m);
          return `<label style="display:inline-flex;align-items:center;gap:8px;font-size:14px;font-weight:500;color:var(--fg);background:${on?'color-mix(in srgb,var(--accent) 16%,transparent)':'var(--chip)'};border:1px solid ${on?'var(--accent)':'var(--line)'};border-radius:999px;padding:8px 14px;cursor:pointer;line-height:1"><input type="checkbox" class="xhvf-mk" data-v="${m}" ${on?"checked":""} style="width:17px;height:17px;accent-color:var(--accent);margin:0" onchange="this.parentElement.style.background=this.checked?'color-mix(in srgb,var(--accent) 16%,transparent)':'var(--chip)';this.parentElement.style.borderColor=this.checked?'var(--accent)':'var(--line)'"> ${m}</label>`;}).join("");}
      if($("cfg-bridge")){$("cfg-bridge").checked=!!j.bridge;$("cfg-bridge").title=j.has_ig_creds?"Toggle the shared bridge execution setting.":"IG credentials are required before turning this on; the control remains available so the missing prerequisite is clear.";}
      BRIDGE_ON=!!j.bridge; if(typeof paintBridgeBadge==='function')paintBridgeBadge();   // Pre-orders bridge badge (P-06)
      if($("featrow-xposts"))$("featrow-xposts").style.display=j.is_admin?"":"none";
      if(j.features){FEATURES=j.features;if($("feat-xposts"))$("feat-xposts").checked=!!j.features.xposts;}
      SLACK_CHANNELS=j.slack_channels||{}; _applySlackToggles();   // per-channel Slack send/off (user 2026-08-01)
      const n=Object.keys(USER_FILTERS).length;
      $("cfg-defaults").classList.remove("sqh-loading");
      $("cfg-defaults").classList.add("cfg-defaults-ready");
      $("cfg-defaults").textContent=n?("Saved defaults: "+Object.entries(USER_FILTERS).map(([k,v])=>`${k.replace('f_','')}=${v}`).join(" · ")):"No saved defaults yet — using the built-in ones.";
      const _xd=j.exec_descriptions||{};
      $("cfg-exec").classList.remove("sqh-loading");
      $("cfg-exec").innerHTML=(j.exec_sources||[]).map(s=>{const on=(j.exec||{})[s]!==false;
        return `<div class="kv" style="align-items:flex-start"><span><b>${s}</b><br><span class="muted" style="font-size:12px">${_xd[s]||''}</span></span><label style="cursor:pointer;white-space:nowrap"><span class="yn">${on?"Yes":"No"}</span> <input type="checkbox" class="cfg-ex" data-s="${s}" ${on?"checked":""}></label></div>`;}).join("");
      document.querySelectorAll(".cfg-ex").forEach(c=>c.onchange=()=>{const yn=c.parentElement.querySelector(".yn");if(yn)yn.textContent=c.checked?"Yes":"No";});
      // Trade filters: DIRECTION only (user 2026-08-01). Market moved out of Trading (Squeeze) — the
      // per-user Markets (User) on/off switch now governs both visibility AND trading. Empty stored = all allowed.
      const tf=j.trade||{}, groups=[["directions",["BULL","BEAR"]]];
      TRADE_HIDE=tf;   // sync per-user hide (user 2026-07-06)
      $("cfg-trade").className="";
      $("cfg-trade").innerHTML=groups.map(([g,opts])=>{
        const allowed=tf[g]||[];
        return `<div style="margin:10px 0"><b style="font-size:12px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em">${g}</b><div class="cfg-tf-options">`+
          opts.map(o=>{const on=(!allowed.length||allowed.includes(o));
            return `<label class="cfg-tf-option" style="background:${on?'color-mix(in srgb,var(--accent) 14%,transparent)':'var(--chip)'};border:1px solid ${on?'var(--accent)':'var(--line)'}"><input type="checkbox" class="cfg-tf" data-g="${g}" data-v="${o}" ${on?"checked":""} onchange="this.parentElement.style.background=this.checked?'color-mix(in srgb,var(--accent) 14%,transparent)':'var(--chip)';this.parentElement.style.borderColor=this.checked?'var(--accent)':'var(--line)'"> ${o}</label>`;}).join("")+`</div></div>`;}).join("");
    })
    .catch(()=>{$("cfg-exec").classList.remove("sqh-loading");$("cfg-exec").textContent="Could not load — try logging in again.";
      if($("xhvf-markets"))$("xhvf-markets").classList.remove("sqh-loading");});
}
function saveFilterDefaults(){
  const f={};FILTER_IDS().forEach(k=>{const el=$(k);if(el&&el.value!=="")f[k]=el.value;});
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({filters:f})})
    .then(r=>{if(!r.ok)throw 0;USER_FILTERS=f;$("cfg-msg").style.color="var(--bull)";$("cfg-msg").textContent="Filter defaults saved.";renderConfig();})
    .catch(()=>{$("cfg-msg").style.color="var(--bear)";$("cfg-msg").textContent="Save failed.";});
}
function clearFilterDefaults(){
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({filters:{}})})
    .then(()=>{USER_FILTERS={};renderConfig();});
}
function saveFeatures(){
  // Guarded: feat-xposts does not exist in the markup, and saveFeatures has no caller either (found
  // 2026-09-04). Left in place rather than deleted because the feature may be mid-build, but it must not
  // be able to throw if something wires it up before the checkbox exists.
  const _fx=$("feat-xposts"); if(!_fx){console.warn("feature save skipped: its checkbox is not in the page");return;}
  const feat={xposts:_fx.checked};
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({features:feat})})
    .then(r=>{if(!r.ok)throw 0;FEATURES=feat;applyTabVisibility();$("eng-msg").style.color="var(--bull)";$("eng-msg").textContent="Feature saved.";})
    .catch(()=>{$("eng-msg").style.color="var(--bear)";$("eng-msg").textContent="Save failed (admin only).";});
}
function saveBridge(){
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({bridge:$("cfg-bridge").checked})})
    .then(async r=>{const el=$("bridge-msg"),j=await r.json().catch(()=>({}));if(r.ok){el.style.color="var(--bull)";el.textContent="Bridge setting saved.";BRIDGE_ON=$("cfg-bridge").checked;paintBridgeBadge();}else{el.style.color="var(--bear)";el.textContent=j.error||"Bridge setting could not be saved.";$("cfg-bridge").checked=false;}})
    .catch(()=>{$("bridge-msg").style.color="var(--bear)";$("bridge-msg").textContent="Bridge setting could not be saved.";$("cfg-bridge").checked=false;});
}
function saveEngine(){
  const eng={};["wo_lifespan_days","x_max_per_day","superinvestor_lookback_days","min_senator_trades","spread_retry_attempts","spread_retry_wait_secs","bridge_min_quality","stop_amend_threshold"].forEach(k=>{const el=$("eng-"+k);if(!el)return;const v=parseFloat(el.value);if(v>=0)eng[k]=v;});
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({engine:eng})})
    .then(r=>{if(!r.ok)throw 0;$("eng-msg").style.color="var(--bull)";$("eng-msg").textContent="Engine settings saved.";})
    .catch(()=>{$("eng-msg").style.color="var(--bear)";$("eng-msg").textContent="Save failed (admin only).";});
}
function saveXPub(){
  // X publishing card on Configuration (Admin): numeric publishing limits + morning Squeeze tweet markets
  // (user 2026-07-06; single editor since 2026-07-17 P-10).
  const eng={};["x_max_per_day","superinvestor_lookback_days","min_senator_trades"].forEach(k=>{const el=$("eng-"+k);if(!el)return;const v=parseFloat(el.value);if(v>=0)eng[k]=v;});
  const mk=[...document.querySelectorAll(".xhvf-mk:checked")].map(c=>c.dataset.v);
  const msg=$("xpub-msg");
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({engine:eng,x_hvf_markets:mk})})
    .then(r=>{if(!r.ok)throw 0;return r.json().catch(()=>({}));})
    .then(()=>{window.ENGINE_VALS={...(window.ENGINE_VALS||{}),...eng};
               msg.style.color="var(--bull)";msg.textContent="X publishing settings saved.";})
    .catch(()=>{msg.style.color="var(--bear)";msg.textContent="Save failed (admin only).";});
}
function saveLeverage(){
  const lev={};["fx","equities","commodities","indices"].forEach(k=>{const v=parseFloat($("lev-"+k).value);if(v>0)lev[k]=v;});
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({leverage:lev})})
    .then(r=>{if(!r.ok)throw 0;LEVERAGE={...LEVERAGE,...lev};render();$("lev-msg").style.color="var(--bull)";$("lev-msg").textContent="Leverage saved.";})
    .catch(()=>{$("lev-msg").style.color="var(--bear)";$("lev-msg").textContent="Save failed.";});
}
function saveLimits(){
  const num=(id,int)=>{const el=$("lim-"+id);if(!el)return undefined;const v=(int?parseInt:parseFloat)(el.value);return isFinite(v)&&v>=0?v:undefined;};
  const lim={};
  [["min_risk_reward",0],["min_trade",0],["bounce_alert_pct",0],["min_instrument_value",0],["max_instrument_value",0],["preorder_threshold_pct",0]].forEach(([k])=>{const v=num(k);if(v!==undefined)lim[k]=v;});
  [["min_quality",1],["min_volume_score",1],["min_rvol",0],["max_position_pct",0],["max_open",1],["max_trades_per_instrument_per_day",1],["bounce_lookback_hours",1],["wo_lifespan_days",1],["let_winners_run_trail",1],["let_winners_run_stop",1]].forEach(([k,int])=>{const v=num(k,int);if(v!==undefined)lim[k]=v;});
  lim.require_above_vwap=$("lim-require_above_vwap").checked?1:0;
  lim.require_atr_expanding=$("lim-require_atr_expanding").checked?1:0;
  lim.auto_close_failed_opens=$("lim-auto_close_failed_opens").checked?1:0;
  lim.adaptive_filters=0;   // compatibility only; the unused Adaptive Filters UI has been removed
  lim.let_winners_run=($("lim-let_winners_run")||{}).checked?1:0;   // "Let winners run" report opt-in, default OFF (user 2026-08-02)
  const _er=$("lim-email_recipients"); if(_er)lim.email_recipients=(_er.value||"").split(",").map(x=>x.trim()).filter(Boolean);
  // Show the "Saved." message in whichever remaining panel's save button was clicked.
  const _setMsg=(txt,ok)=>["lim-msg","lim-msg2","lim-msg3","lim-msg4"].forEach(id=>{const el=$(id);if(el){el.style.color=ok?"var(--bull)":"var(--bear)";el.textContent=txt;}});
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({limits:lim})})
    .then(r=>{if(r.ok){_setMsg("Saved.",true);
      MY_LIMITS={...MY_LIMITS,...lim};if(typeof renderPreorders==='function')renderPreorders();   // apply new floors to My Pre-orders now (user 2026-07-24, P-02)
      if(typeof render==='function')render();   // Scanner table/charts also hard-filter on MY_LIMITS (P-01 2026-08-11) — must re-render on save, not just on the next filter change, or a just-saved floor (e.g. "Require ATR expanding") silently leaves stale rows on screen (user 2026-08-11, MARUTI.BO showing ATR ✗ after saving)
      if(typeof _renderPerformance==='function')_renderPerformance();   // Performance respects the personal Volume Score floor now too (user 2026-07-28)
      if(typeof applyWinnersDefaults==='function')applyWinnersDefaults();   // keep Performance's Replay Model in sync with User Configuration
      else if(WIN!==null&&typeof winnersParamsChange==='function')winnersParamsChange();   // re-render the winners tab with the new floor
    }else{_setMsg("Save failed.",false);}})
    .catch(()=>{_setMsg("Save failed.",false);});
}
// Per-webhook Slack send/not-send toggles (user 2026-08-01). SLACK_CHANNELS mirrors the app_config
// slack_ch_<channel> flags; one "send" checkbox sits against each webhook in the Slack card. Unticking a
// channel silences ONLY that webhook (gated per-channel in notify._send + the direct posters).
let SLACK_CHANNELS={};
function _applySlackToggles(){document.querySelectorAll(".slack-ch").forEach(c=>{const ch=c.dataset.ch;c.checked=SLACK_CHANNELS[ch]!==false;});}
function saveSlackChannel(ch,on){SLACK_CHANNELS[ch]=!!on;
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({slack_channel:{name:ch,on:!!on}})})
    .then(r=>{if(!r.ok)throw 0;}).catch(()=>{});}
function saveTradeFilters(){
  // DIRECTION only (user 2026-08-01) — market is governed by Markets (User) now. markets/locations kept
  // empty (= no restriction) for backend compatibility.
  const t={directions:[],markets:[],locations:[]};
  document.querySelectorAll(".cfg-tf:checked").forEach(c=>t[c.dataset.g].push(c.dataset.v));
  // A fully-ticked group = no restriction (send empty so the engine stores ALL).
  const totals={directions:2};
  Object.keys(t).forEach(g=>{if(t[g].length===(totals[g]||0)||t[g].length===0)t[g]=[];});
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({trade:t})})
    .then(r=>{if(!r.ok)throw 0;TRADE_HIDE=t;render();renderPreorders&&renderPreorders();$("cfg-msg").style.color="var(--bull)";$("cfg-msg").textContent="Trade filters saved.";})
    .catch(()=>{$("cfg-msg").style.color="var(--bear)";$("cfg-msg").textContent="Save failed.";});
}
function saveExec(){
  const ex={};document.querySelectorAll(".cfg-ex").forEach(c=>ex[c.dataset.s]=c.checked);
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({exec:ex})})
    .then(r=>{if(!r.ok)throw 0;$("cfg-msg").style.color="var(--bull)";$("cfg-msg").textContent="Execution switches saved.";})
    .catch(()=>{$("cfg-msg").style.color="var(--bear)";$("cfg-msg").textContent="Save failed.";});
}
// Generic column sort for the simple tables (user 2026-06-30: "allow click on title ... any table"):
// nulls sink, numbers compare numerically, everything else as strings.
// Sort-direction arrow on the active column header (user 2026-07-11). attr = the header's data-* name.
function _sortArrows(attr,key,dir){
  document.querySelectorAll('th['+attr+']').forEach(th=>{
    const old=th.querySelector('.sarr'); if(old)old.remove();
    if(th.getAttribute(attr)===String(key)) th.insertAdjacentHTML('beforeend',` <span class="sarr" style="opacity:.65;font-size:10px">${dir<0?'▼':'▲'}</span>`);
  });
}
function genSort(rows,k,dir){
  return rows.slice().sort((a,b)=>{const x=a[k],y=b[k];
    if(x==null&&y==null)return 0; if(x==null||x==="")return 1; if(y==null||y==="")return -1;
    if(typeof x==="number"&&typeof y==="number")return (x-y)*dir;
    return String(x).localeCompare(String(y))*dir;});
}
// Data tables with a specialised data-* header already rerender from their canonical row model. Every
// other heading uses this DOM sorter, including transaction evidence generated after a card is selected.
// It intentionally moves only tbody rows, leaving totals/footers and underlying calculations untouched.
function _genericTableNumber(value){
  // Tables display £, %, the Unicode minus sign and multiplier values (for example 0.8×).
  // Treat those as numbers rather than alphabetic labels; do not guess at mixed prose values.
  const text=String(value||'').trim().replace(/^−/,'-').replace(/[£,$,\s]/g,'');
  const match=text.match(/^([-+]?\d+(?:\.\d+)?)(?:%|×|x)?$/i);
  return match?Number(match[1]):null;
}
function _genericTableSort(th){
  const table=th.closest('table'), head=th.parentElement, body=table&&table.tBodies&&table.tBodies[0];
  if(!table||!head||!body)return;
  const column=[...head.children].indexOf(th); if(column<0)return;
  const direction=th.dataset.genericSortDir==='asc'?'desc':'asc';
  const value=row=>(row.children[column]?.textContent||'').trim();
  const compare=(a,b)=>{
    const left=value(a), right=value(b);
    if(!left||left==='—')return (!right||right==='—')?0:1; // blanks always remain at the end
    if(!right||right==='—')return -1;
    const leftNumber=_genericTableNumber(left), rightNumber=_genericTableNumber(right);
    if(leftNumber!=null&&rightNumber!=null)return leftNumber-rightNumber;
    const leftDate=Date.parse(left), rightDate=Date.parse(right);
    if(/^\d{4}-\d\d-\d\d/.test(left)&&/^\d{4}-\d\d-\d\d/.test(right)&&!isNaN(leftDate)&&!isNaN(rightDate))return leftDate-rightDate;
    return left.localeCompare(right,undefined,{numeric:true,sensitivity:'base'});
  };
  [...head.children].forEach(header=>{
    delete header.dataset.genericSortDir;
    header.removeAttribute('aria-sort');
    header.querySelector('.garr')?.remove();
  });
  th.dataset.genericSortDir=direction;
  th.setAttribute('aria-sort',direction==='asc'?'ascending':'descending');
  th.insertAdjacentHTML('beforeend',` <span class="garr" aria-hidden="true" style="opacity:.65;font-size:10px">${direction==='asc'?'▲':'▼'}</span>`);
  [...body.rows].sort((a,b)=>compare(a,b)*(direction==='asc'?1:-1)).forEach(row=>body.append(row));
}
document.addEventListener('click',ev=>{
  const th=ev.target.closest('th');
  if(!th||th.dataset.nosort||Object.keys(th.dataset).length)return;
  _genericTableSort(th);
});
const _mw=d=>{if(!d)return'—';const day=+String(d).slice(8,10)||1;return String(d).slice(0,7)+' W'+Math.min(5,Math.ceil(day/7));};   // Month-Week bucket
let OO_ROWS=[], ooSortK="updated_at", ooSortDir=-1;
function paintOrderOps(){
  renderIgCredWarn("oo-igwarn");   // no-IG-credentials warning + Open IG settings button (P-10 L225 / P-30 L226)
  const st=s=>s==="PENDING"?"var(--accent)":s==="FILLED"?"var(--bull)":s==="WATCHING"?"#d29922":"var(--muted)";
  // Chart-click filters (user 2026-07-03) apply to the table; the charts show the full mix.
  let rows=OO_ROWS;
  const _statusPicked=setOf("oof_status");   // did the user click a Status on the chart?
  [["oof_status","status"],["oof_direction","direction"],["oof_session","session"],["oof_ticker","ticker"]].forEach(([id,k])=>{
    rows=rows.filter(r=>inSet(id,r[k]));});
  // Default view (user 2026-07-11): hide only the DEAD states (deleted/expired/cancelled) — everything
  // live (PENDING/WATCHING/FILLED/…) stays visible. UNLESS a Status is clicked on the chart, or
  // "Show closed" is ticked (the chart filter can then reach the closed states too).
  if(!_statusPicked && ((document.querySelector('input[name="oo-closed-view"]:checked')||{}).value==='hide')){const _closed=new Set(["DELETED","EXPIRED","CANCELLED","CANCELED"]);
    rows=rows.filter(r=>!_closed.has(String(r.status||"").toUpperCase()));}
  rows=applyDateFilter("oo",rows,r=>r.updated_at||r.placed_at);   // date filter on Updated
  // Free-text search by name / ticker (user 2026-07-10).
  const _ooq=(($("oo-search")||{}).value||"").trim().toLowerCase();
  if(_ooq){const _snm=t=>{const r=DATA.find(x=>x.ticker===t||disp(x.ticker)===t);return r?(r.name||''):'';};
    rows=rows.filter(r=>(_snm(r.ticker)+' '+disp(r.ticker||'')+' '+(r.ticker||'')).toLowerCase().includes(_ooq));}
  if($("oo-datebar")&&!$("oo-datebar").innerHTML)$("oo-datebar").innerHTML=dateFilterBar("oo","paintOrderOps()");
  $("oo-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> order operation records${rows.length!==OO_ROWS.length?` <span class="muted">of ${OO_ROWS.length}</span>`:""}`;
  $("ootab-count").textContent="("+rows.length+")";   // VISIBLE (filtered) count, not the total (user 2026-08-01)
  const oby=k=>OO_ROWS.reduce((m,r)=>{const v=r[k]||"—";m[v]=(m[v]||0)+1;return m;},{});
  // Ticker chart sits far RIGHT wherever it appears (user 2026-07-17, P-11a — reverses P-11).
  $("oo-viz").innerHTML=
    `<div class="vizsector">`+barChart("Status",oby("status"),"oof_status",k=>k==="PENDING"?"var(--accent)":k==="FILLED"?"var(--bull)":k==="WATCHING"?"#d29922":"var(--bear)")+`</div>`+
    `<div class="vizsector">`+barChart("Direction",oby("direction"),"oof_direction",k=>k==="BUY"?"var(--bull)":"var(--bear)")+`</div>`+
    `<div class="vizsector">`+barChart("Source",oby("session"),"oof_session")+`</div>`+
    `<div class="vizsector">`+barChart("Ticker",oby("ticker"),"oof_ticker")+`</div>`;
  // Enrich operational rows from the current scanner snapshot so Name, VolumeScore, R:R and Quality
  // are displayed together and sort on their actual values (user 2026-08-04, P-07).
  const _ooRec=t=>DATA.find(x=>x.ticker===t||disp(x.ticker)===t);
  // Distance to price % (user 2026-08-01): how far the live price is from the order's entry level,
  // as a % of the live price. Live price comes from the loaded snapshot for this ticker.
  // The SERVER value wins (2026-08-17). /api/order-ops resolves RVOL/VolumeScore/R:R/Quality from the
  // squeeze_history trigger that CAUSED each order, which is point-in-time correct. This block used to
  // assign d?.x ?? null unconditionally and so overwrote all four with today's snapshot -- and the
  // snapshot only carries rvol for instruments that triggered TODAY (_snapshot_rvol is triggered-only),
  // so for a historical order the record was found (the Name rendered) but rvol/volume_score came back
  // null and blanked the correct figure. That is the "many rows without RVOL" the user reported: the
  // data was right in the response and thrown away in the browser. Snapshot is now a FALLBACK only.
  // Name and dist_pct still come from the snapshot -- neither is returned by /api/order-ops.
  rows.forEach(r=>{const d=_ooRec(r.ticker),p=d&&d.current_price;
    r.name=d?.name||r.name||'';
    r.rvol=r.rvol??d?.rvol??null;r.volume_score=r.volume_score??d?.volume_score??null;
    // working_orders has no column for any of these, so the snapshot is the ONLY source; without them
    // Market rendered blank on every row (user 2026-08-28: "New orders still has empty data e.g.
    // MARKET"). Same ?? precedence as the fields above, so a server value always wins where one exists.
    r.market=r.market??d?.market??null;r.sector=r.sector??d?.sector??null;
    r.mcap=r.mcap??d?.mcap??null;
    r.above_vwap=r.above_vwap??d?.above_vwap??null;r.atr_expanding=r.atr_expanding??d?.atr_expanding??null;
    r.rr=r.rr??d?.rr??null;r.quality=r.quality??d?.quality??null;
    r.dist_pct=(r.entry!=null&&p)?+(((r.entry-p)/p)*100).toFixed(2):null;});
  rows.forEach(r=>r._fav=FAVS.has(disp(r.ticker))?1:0);   // favourite column sortable (user 2026-07-11)
  $("oo-rows").innerHTML=genSort(rows,ooSortK,ooSortDir).map(r=>`<tr>
    ${_favCell(r.ticker)}<td>${r.placed_at||''}</td><td>${r.updated_at||''}</td><td>${nm40(r.name)}</td>
    <td>${_mcapFmt(r.mcap)}</td><td>${rvolCell(r.rvol)}</td><td>${_tickCross(r.above_vwap)}</td><td>${_tickCross(r.atr_expanding)}</td><td>${volScoreCell(r.volume_score)}</td><td>${r.rr!=null?(+r.rr).toFixed(1):'<span class="muted">—</span>'}</td>
    <td>${r.quality!=null?`<b style="color:${qcol(r.quality)}">${r.quality}</b>`:'<span class="muted">—</span>'}</td>
    <td><span class="tag ${r.direction==='BUY'?'bull':'bear'}">${r.direction||''}</span></td>
    <td>${r.entry??''}</td><td>${r.stop??''}</td><td>${r.target??''}</td>
    <td>${r.dist_pct!=null?`<span style="color:${r.dist_pct>=0?'var(--muted)':'var(--bear)'}">${r.dist_pct>0?'+':''}${r.dist_pct}%</span>`:'<span class="muted">—</span>'}</td>
    <td>${r.size??''}</td>
    <td><b style="color:${st(r.status)}">${r.status||''}</b></td><td>${r.session||''}</td>
    <td class="muted" style="white-space:normal;max-width:460px">${(r.notes||'').replace(/</g,'&lt;')}</td>
    <td><b>${disp(r.ticker||'')}</b></td></tr>`).join("")
  || `<tr><td colspan="21" class="empty">No order operations recorded yet — the bridge runs every 2 hours.</td></tr>`;
}
// Bridge on/off state (user 2026-08-02, P-06) — whether the DB→IG bridge may place orders. Loaded from
// /api/config (j.bridge). The badge next to the Pre-orders title makes it unmistakable.
let BRIDGE_ON=null;   // unknown until /api/config returns; never present a guessed OFF state
function paintBridgeBadge(){
  const el=$("oo-bridge-badge"); if(!el)return;
  if(BRIDGE_ON===null){el.innerHTML="";return;}
  el.innerHTML=BRIDGE_ON
    ? `<span title="The bridge places qualifying READY setups on IG as working orders every 2 hours." style="display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;color:var(--bull);border:1px solid var(--bull);background:color-mix(in srgb,var(--bull) 12%,transparent);border-radius:999px;padding:5px 12px">🟢 Bridge ON — placing orders on IG</span>`
    : `<span title="The bridge is scanning and reporting but is NOT placing any orders on IG. Enable it in Configuration → Trading (Squeeze)." style="display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;color:#d29922;border:1px solid #d29922;background:color-mix(in srgb,#d29922 12%,transparent);border-radius:999px;padding:5px 12px">⏸ Bridge OFF — no orders placed on IG</span>`;
}
function renderOrderOps(){
  paintBridgeBadge();
  _rowsLoading("oo-rows","renderOrderOps()");
  fetch("/api/order-ops",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{_rowsLoaded("oo-rows");OO_ROWS=j.rows||[];paintOrderOps();})
    .catch(()=>_rowsFault("oo-rows","Try signing in again.","renderOrderOps()"));
}
let AC_ROWS=[], acSortK="ts", acSortDir=-1;
// Category derived from the event text (user 2026-07-03) so the log is easy to filter.
function actCat(ev){
  ev=(ev||"").toLowerCase();
  if(ev.startsWith("logged in"))return "Login";
  if(ev.includes("password"))return "Password";
  if(ev.includes("pre-order"))return "Pre-orders";
  if(ev.includes("data refresh"))return "Data";
  if(ev.includes("config")||ev.includes("filter defaults")||ev.includes("execution")||ev.includes("switched"))return "Configuration";
  if(ev.includes("secure setting")||ev.includes("credential"))return "Security";
  return "Other";
}
function paintActivity(){
  AC_ROWS.forEach(e=>e.cat=e.cat||actCat(e.event));
  // Category bar chart (user 2026-07-03) — a sorted bar per Zebra BI guide; click a bar to filter.
  const counts=AC_ROWS.reduce((m,e)=>{m[e.cat]=(m[e.cat]||0)+1;return m;},{});
  const _aby=fn=>AC_ROWS.reduce((m,e)=>{const v=fn(e)||'—';m[v]=(m[v]||0)+1;return m;},{});
  $("act-viz").innerHTML=`<div class="vizsector">`+barChart("Activity by category",counts,"acf_cat")+`</div>`+
    `<div class="vizsector">`+barChart("Month",_aby(e=>(e.ts||'').slice(0,7)),"acf_month",null,true)+`</div>`+
    `<div class="vizsector">`+barChart("Month-Week",_aby(e=>_mw(e.ts)),"acf_mweek",null,true)+`</div>`;
  if($("act-datebar")&&!$("act-datebar").innerHTML)$("act-datebar").innerHTML=dateFilterBar("act","paintActivity()");
  let rows=AC_ROWS.filter(e=>inSet("acf_cat",e.cat)&&inSet("acf_month",(e.ts||'').slice(0,7))&&inSet("acf_mweek",_mw(e.ts)));
  rows=applyDateFilter("act",rows,e=>e.ts);
  rows=genSort(rows,acSortK,acSortDir);
  const nsel=(setOf("acf_cat")||{}).size||0;
  $("actab-count").textContent=`(${AC_ROWS.length})`;
  $("act-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> activity records${(nsel||dateActive("act"))?` <span class="muted">of ${AC_ROWS.length}</span>`:""}`;
  $("act-rows").innerHTML=rows.map(e=>`<tr><td>${e.ts||''}</td><td><span class="tag" style="background:var(--chip);color:var(--fg)">${e.cat}</span></td><td>${(e.event||"").replace(/</g,"&lt;")}</td></tr>`).join("")
    || `<tr><td colspan="3" class="empty">No activity recorded yet.</td></tr>`;
}
function renderActivity(){
  _rowsLoading("act-rows","renderActivity()");
  fetch("/api/userlog",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{_rowsLoaded("act-rows");AC_ROWS=j.log||[];paintActivity();})
    .catch(()=>_rowsFault("act-rows","Try signing in again.","renderActivity()"));
}
// ── Markets tab (user 2026-07-10): live per-market coverage computed from the loaded snapshot ──
// "Included?" = whether any instrument in that market is visible to you (passes your market filter).
const _mkIncl=b=>b?'<span style="color:var(--bull);font-weight:700" title="Included in your processing">🟢 Included</span>':'<span class="muted" title="Switched off in Markets (User) — hidden from your lists and not traded on your behalf">⚪ Excluded</span>';
function _mkAgg(){
  const g={};
  DATA.forEach(r=>{const m=r.market||"—";const o=(g[m]=g[m]||{market:m,total:0,signal:0,triggered:0,ready:0,developing:0,qsum:0,qn:0,incl:false});
    o.total++; if(r.has_signal)o.signal++; if(tradeVisible(r))o.incl=true;
    if(r.status==="TRIGGERED")o.triggered++; else if(r.status==="READY")o.ready++; else if(r.status==="DEVELOPING")o.developing++;
    if(r.quality!=null){o.qsum+=r.quality;o.qn++;}});
  return Object.values(g).map(o=>({...o,avgq:o.qn?Math.round(o.qsum/o.qn):null,incl:o.incl?1:0}));
}
const _mkSort=(rows,k,dir)=>rows.sort((a,b)=>{const x=a[k],y=b[k];const av=x==null?-1e9:x,bv=y==null?-1e9:y;const c=(av<bv?-1:av>bv?1:0)*dir;return c||String(a.market||'').localeCompare(String(b.market||''));});   // Market tiebreak (user 2026-07-11)
const _mkCells=o=>`<td>${o.total}</td><td>${o.signal}</td>
    <td>${o.triggered?`<b style="color:var(--bull)">${o.triggered}</b>`:'0'}</td><td>${o.ready?`<b style="color:var(--accent)">${o.ready}</b>`:'0'}</td><td>${o.developing||0}</td>
    <td>${o.avgq!=null?`<b style="color:${qcol(o.avgq)}">${o.avgq}</b>`:''}</td>`;
// Per-market on/off switch (user 2026-07-11). scope 'app' = admin (everyone); 'user' = this login only.
function mkToggle(market,scope,enabled){
  const set=scope==='app'?MARKETS_DISABLED:MARKETS_OFF;
  if(enabled)set.delete(market);else set.add(market);
  const key=scope==='app'?'markets_disabled':'markets_off';
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({[key]:[...set]})})
    .then(r=>{if(!r.ok)throw 0;if(typeof render==='function')render();if(typeof renderPreorders==='function')renderPreorders();renderMarkets();if(typeof renderMarketsAdmin==='function')renderMarketsAdmin();})
    .catch(()=>alert("Could not save the market switch — try again."));
}
const _mkUserSwitch=m=>{const admDis=MARKETS_DISABLED.has(m),on=!MARKETS_OFF.has(m)&&!admDis;
  return `<input type="checkbox" ${on?'checked':''} ${admDis?'disabled title="Disabled by admin"':''} onchange="mkToggle('${(m||'').replace(/'/g,'')}','user',this.checked)" style="cursor:pointer;transform:scale(1.3)">`;};
const _mkAdminSwitch=m=>`<input type="checkbox" ${MARKETS_DISABLED.has(m)?'':'checked'} onchange="mkToggle('${(m||'').replace(/'/g,'')}','app',this.checked)" style="cursor:pointer;transform:scale(1.3)">`;
let mkSortK="market", mkSortDir=1;   // default: Market A→Z (user 2026-07-11)
// A tab can depend on data it does not fetch itself. Markets, Markets (Admin) and My Pre-orders all
// derive from the shared DATA snapshot, which /api/records loads asynchronously and which took 14.4 s on
// 2026-08-24. Until it arrives they rendered an empty table with no indication, which during a slow load
// is indistinguishable from "there is nothing here" (user: "We are still missing Data loading messages
// e.g. Markets (Admin)"). The first audit missed these because it only looked for fetch() in the
// renderer -- depending on async data needs a loading state just as much as fetching it does.
function _awaitingData(tbodyId){
  if(typeof DATA!=="undefined"&&DATA&&DATA.length)return false;
  _rowsLoading(tbodyId);
  return true;
}
function renderMarkets(){
  if(_awaitingData("mk-rows"))return;
  const rows=_mkSort(_mkAgg(),mkSortK,mkSortDir);
  $("mk-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> markets · <span class="muted">${DATA.length} instruments scanned</span>`;
  $("mktab-count")&&($("mktab-count").textContent=`(${rows.length})`);
  // No click-through to the Scanner (user 2026-07-11) — this is a coverage view.
  $("mk-rows").innerHTML=rows.map(o=>`<tr><td><b>${o.market}</b></td><td>${_mkUserSwitch(o.market)}</td>${_mkCells(o)}</tr>`).join("")
    ||`<tr><td colspan="8" class="empty">No market data yet — open the Scanner first.</td></tr>`;
}
// Markets (User) Refresh button (user 2026-07-17, P-07). It used to call renderMarkets() directly, which
// only repaints the in-memory snapshot — no data was re-read and nothing said the click had registered.
// Now it re-reads /api/records and shows an hourglass on the button until the rows repaint.
function refreshMarkets(ev){
  const btn=ev&&ev.target?ev.target:null, was=btn?btn.textContent:"";
  if(btn){btn.disabled=true;btn.textContent="⏳ Data loading…";}
  $("mk-count").innerHTML='<span class="sqh-loading">⏳ Data loading…</span>';
  fetch("/api/records",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{DATA=j.records||[];DATA_LOADED=true;if(j.markets&&j.markets.length)REFRESH_MKT_LIST=j.markets;DATA.forEach(augment);renderMarkets();})
    .catch(()=>{$("mk-rows").innerHTML=`<tr><td colspan="8" class="empty">Could not refresh — try again.</td></tr>`;})
    .finally(()=>{if(btn){btn.disabled=false;btn.textContent=was;}});
}
document.querySelectorAll("th[data-mk]").forEach(th=>th.onclick=()=>{const k=th.dataset.mk;mkSortDir=(mkSortK===k)?-mkSortDir:-1;mkSortK=k;renderMarkets();_sortArrows("data-mk",mkSortK,mkSortDir);});
// ── Markets (Admin) tab (admin, user 2026-07-10): per-market coverage + full data-import rebuild ──
let maSortK="total", maSortDir=-1;
function renderMarketsAdmin(){
  if(_awaitingData("ma-rows"))return;
  const rows=_mkSort(_mkAgg(),maSortK,maSortDir);
  $("ma-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> markets · <span class="muted">${DATA.length} instruments</span>`;
  $("ma-rows").innerHTML=rows.map(o=>`<tr><td><b>${o.market}</b></td><td>${_mkAdminSwitch(o.market)}</td>${_mkCells(o)}</tr>`).join("")||`<tr><td colspan="8" class="empty">No market data.</td></tr>`;
}
document.querySelectorAll("th[data-ma]").forEach(th=>th.onclick=()=>{const k=th.dataset.ma;maSortDir=(maSortK===k)?-maSortDir:-1;maSortK=k;renderMarketsAdmin();_sortArrows("data-ma",maSortK,maSortDir);});
// ── IG Account tab (user 2026-07-10): the acting user's own IG open positions + working orders ──
let IG_POS=[], IG_ORD=[], IG_CLOSE_SELECTION=new Set(), igOpenMonthInitialised=false;
function updateIgCloseButton(){const b=$("ig-close-selected");if(!b)return;const n=IG_CLOSE_SELECTION.size;b.disabled=!n;b.textContent=`Close selected (${n})`;}
function igCloseToggle(encoded,checked){decodeURIComponent(encoded).split(',').filter(Boolean).forEach(id=>checked?IG_CLOSE_SELECTION.add(id):IG_CLOSE_SELECTION.delete(id));updateIgCloseButton();}
function showIgCloseOutcome(results,namesByDeal){const target=$("ig-close-result");if(!target)return;target.replaceChildren();target.style.display="block";const closed=results.filter(x=>x.closed).length;target.style.borderColor=closed===results.length?"var(--bull)":"var(--bear)";const headline=document.createElement("b");headline.textContent=`IG close outcome: ${closed}/${results.length} position${results.length===1?'':'s'} confirmed closed`;target.appendChild(headline);const list=document.createElement("ul");list.style.cssText="margin:6px 0 0;padding-left:20px";results.forEach(x=>{const li=document.createElement("li"),name=namesByDeal[x.deal_id]||x.deal_id;li.textContent=x.closed?`${name}: IG confirmed closed.`:`${name}: NOT closed — ${x.error||'IG gave no confirmation.'}`;li.style.color=x.closed?"var(--bull)":"var(--bear)";list.appendChild(li);});target.appendChild(list);}
// Close history (user 2026-08-22, deferred to 2026-08-23). _append_ig_close_audit has written a durable
// host-side record of every attempt since 2026-08-21, but nothing read it back, so the evidence lived only
// on disk. Lazy-loaded on the reveal, like Closed trades, because it is not needed for the default view.
let IG_CLOSE_HISTORY_LOADING=false;
const _IG_PHASE={
  confirmed:         ["Closed — IG confirmed",        "var(--bull)"],
  submitted:         ["Sent to IG",                   "var(--warn)"],
  not_closed:        ["NOT closed",                   "var(--bear)"],
  rejected_preflight:["Rejected before sending",      "var(--muted)"]};
function loadIgCloseHistory(){
  const box=$("ig-close-history"); if(!box||IG_CLOSE_HISTORY_LOADING)return;
  IG_CLOSE_HISTORY_LOADING=true;
  box.innerHTML='<span class="sqh-loading">⏳ Data loading…</span>';
  fetch("/api/ig-close-audit",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    IG_CLOSE_HISTORY_LOADING=false;
    if(!j||j.error){box.innerHTML=`<span class="muted" style="font-size:12px">${(j&&j.error)||"The close history could not be read."} <button class="btn" onclick="loadIgCloseHistory()">↻ Retry</button></span>`;return;}
    const rows=(j.entries||[]).map(e=>{
      const [label,colour]=_IG_PHASE[e.phase]||[e.phase||"—","var(--muted)"];
      const nm=(IG_POS.find(p=>String(p.deal_id||"")===String(e.deal_id))||{});
      return `<tr><td class="muted">${(e.at||"").replace("T"," ").slice(0,19)}</td>`+
             `<td>${nm.name||nm.ticker||""}</td><td class="muted">${e.deal_id||""}</td>`+
             `<td><b style="color:${colour}">${label}</b></td>`+
             `<td class="muted">${String(e.detail||"").replace(/</g,"&lt;")}</td>`+
             (j.scope==="all"?`<td class="muted">${e.user||""}</td>`:"")+`</tr>`;}).join("");
    box.innerHTML=`<div class="tablewrap"><table><thead><tr><th>When (UTC)</th><th>Name</th><th>Deal ID</th><th>Outcome</th><th>What IG reported</th>${j.scope==="all"?"<th>User</th>":""}</tr></thead>`+
      `<tbody>${rows||`<tr><td colspan="${j.scope==="all"?6:5}" class="empty">No close has been requested from here yet.</td></tr>`}</tbody></table></div>`+
      `<div class="muted" style="font-size:11px;margin-top:6px">Recorded on the web host as each attempt was made, independently of Supabase. Showing ${(j.entries||[]).length} of ${j.total||0}.</div>`;
  }).catch(()=>{IG_CLOSE_HISTORY_LOADING=false;box.innerHTML='<span class="muted" style="font-size:12px">The close history could not be read. <button class="btn" onclick="loadIgCloseHistory()">↻ Retry</button></span>';});
}
async function requestCloseSelected(){const ids=[...IG_CLOSE_SELECTION];if(!ids.length)return;const namesByDeal=Object.fromEntries(IG_POS.filter(p=>ids.includes(String(p.deal_id||''))).map(p=>[String(p.deal_id),p.name||p.ticker||p.deal_id]));const names=ids.map(id=>namesByDeal[id]||id);if(!await appConfirm(`This sends market-close requests for ${ids.length} open position${ids.length===1?'':'s'}: ${names.join(', ')}. This cannot be undone.`,{title:'Close selected IG positions?',ok:'Close positions'}))return;let r,j;try{r=await fetch('/api/ig-close-positions',{method:'POST',headers:{'Content-Type':'application/json','X-Auth':AUTH},body:JSON.stringify({deal_ids:ids,confirmed:true})});j=await r.json().catch(()=>({}));}catch(e){showIgCloseOutcome(ids.map(deal_id=>({deal_id,closed:false,error:'Network request failed; refresh before retrying.'})),namesByDeal);return;}const results=Array.isArray(j.results)?j.results:ids.map(deal_id=>({deal_id,closed:false,error:j.error||'Close request failed; refresh before retrying.'}));IG_CLOSE_SELECTION.clear();updateIgCloseButton();showIgCloseOutcome(results,namesByDeal);
  // Reveal and refresh the durable record straight away, so the outcome outlives this dialog.
  const _hw=$("ig-close-history-wrap");if(_hw)_hw.open=true;IG_CLOSE_HISTORY_LOADING=false;loadIgCloseHistory();const allClosed=results.length&&results.every(x=>x.closed);if(await appConfirm(allClosed?'IG confirmed every requested close. Refresh the account to verify the live list.':'One or more positions remain open. The detailed outcome is shown above. Refresh the account before considering any retry.',{title:'IG close result',ok:'Refresh account',cancel:'Keep results',rows:results.map(x=>[namesByDeal[x.deal_id]||x.deal_id,x.closed?'Closed — IG confirmed':`Open — ${x.error||'not confirmed'}`])}))await renderIgAccount();}
let igpSortK="activity_date", igpSortDir=-1, igoSortK="good_till", igoSortDir=-1;   // IG Account defaults: newest opened/closed event first; orders by good-till desc
const _igDtag=d=>d?`<span class="tag ${d==='BUY'?'bull':'bear'}">${d}</span>`:'';
// Shared formatters: closed-trade painting runs independently of paintIgAccount.
// Keeping these at module scope prevents real closed rows throwing ReferenceError (_sz/_pf).
const _igSz=v=>v==null||v===''?'':(Math.round(+v*100)/100);
const _igPf=v=>v==null?'<span class="muted">—</span>':`<span style="color:${v>=0?'var(--bull)':'var(--bear)'}">${v>=0?'+':'−'}${Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:2})}</span>`;
const _igPfp=v=>v==null?'<span class="muted">—</span>':`<span style="color:${v>=0?'var(--bull)':'var(--bear)'}">${v>=0?'+':''}${v}%</span>`;
const _igDateTime=s=>s?String(s).slice(0,19).replace('T',' '):'';
const _igCurrency=c=>{const v=String(c||'').trim().toUpperCase();return ({'£':'GBP','$':'USD','US$':'USD','€':'EUR'}[v]||v||'Unspecified');};
// Friendly order-source labels (user 2026-07-13): WEB_BRIDGE is the 2-hour Squeeze-scanner bridge.
const _igSrc=s=>({WEB_BRIDGE:"Squeeze (bridge)",WEB_MANUAL:"Manual (web)"}[s]||s||'—');
// Paint from the last fetch, honouring the tab's name/ticker search (user 2026-07-17, P-17). Split out
// of renderIgAccount so typing filters in place instead of re-hitting the IG API on every keystroke —
// IG has a rate-limited allowance, so re-fetching per character is not an option.
function paintIgAccount(){
  const q=(($("ig-search")||{}).value||"").trim().toLowerCase();
  const hit=r=>!q||(((r.name||'')+' '+(r.ticker||r.epic||'')).toLowerCase().includes(q));
  const _snap={}, _snapByName={};
  const _companySuffix=new Set(['INC','INCORPORATED','CORP','CORPORATION','PLC','LTD','LIMITED','CO','COMPANY','HOLDING','HOLDINGS']);
  const _igNameKey=n=>String(n||'').toUpperCase().replace(/\s*\(24 HOURS\)\s*$/,'')
    .replace(/[^A-Z0-9]+/g,' ').split(/\s+/).filter(x=>x&&!_companySuffix.has(x)).join('');
  DATA.forEach(r=>{if(r.ticker)_snap[r.ticker]=r;if(r.name){const k=_igNameKey(r.name);
    if(!(k in _snapByName))_snapByName[k]=r;else _snapByName[k]=null;}});   // null = ambiguous; never guess
  const _enrCommon=o=>{const s=_snap[o.ticker]||_snap[o.epic]||{};
    return {location:s.location||"—",market:s.market||"—",sector:s.sector||"—",direction:o.direction||"—"};};
  const cnt=(n,tot,word)=>`<b style="font-size:15px;color:var(--fg)">${n}</b> ${word}${n===1?'':'s'}${tot!==n?` <span class="muted">of ${tot}</span>`:''}`;

  // ── OPEN POSITIONS (own strip + aggregate toggle + sortable table, user 2026-08-01) ──
  if(!igOpenMonthInitialised){
    // Load the complete returned history first. A month is a chart filter, not an implicit default;
    // defaulting to the newest month hid older closed transactions from the main table.
    if($("igp_open_month"))$("igp_open_month").value="";
    igOpenMonthInitialised=true;
  }
  const posRaw=IG_POS.filter(hit).map(p=>Object.assign({},p,_enrCommon(p),{open_month:(p.opened||'').slice(0,7)||'—'}));
  let posBase=posRaw.slice();
  const showClosed=((document.querySelector('input[name="ig-closed-view"]:checked')||{}).value!=='hide');
  const closedBase=(showClosed?(IGCLOSED||[]):[]).map(c=>{const s=_snapByName[_igNameKey(c.instrument)]||{};
    const ol=+c.open_level,cl=+c.close_level,dir=c.direction;
    const pct=(Number.isFinite(ol)&&ol!==0&&Number.isFinite(cl))?+(((dir==='SELL'?ol-cl:cl-ol)/ol)*100).toFixed(2):null;
    return Object.assign({},c,{status:'Closed',name:c.instrument||'',size:Math.abs(+c.size||0),level:c.open_level,current:c.close_level,
      profit:c.pnl,profit_pct:pct,closed:c.date||'',open_month:(c.date||'').slice(0,7)||'—',
      location:s.location||'—',market:s.market||'—',sector:s.sector||'—',ticker:s.ticker||''});});
  const _agg=((document.querySelector('input[name="ig-pos-view"]:checked')||{}).value==='agg');
  if(_agg){   // one row per instrument+direction: sum size, size-weighted average level, earliest opened
    const g={};
    posBase.forEach(p=>{const key=(p.ticker||p.epic||p.name)+'|'+p.direction+'|'+p.open_month;
      const a=g[key]||(g[key]=Object.assign({},p,{size:0,_wsum:0,_n:0,_deal_ids:[],stop:null,limit:null,current:null,_currentW:0,_currentWeight:0,profit:0,_ppw:0,_pptot:0}));
      const sz=+p.size||0; a.size+=sz; a._wsum+=(+p.level||0)*sz; a._n++;
      if(p.deal_id)a._deal_ids.push(String(p.deal_id));
      if(p.current!=null&&Number.isFinite(+p.current)){a._currentW+=(+p.current)*sz;a._currentWeight+=sz;}
      a.profit+=(+p.profit||0);
      if(p.profit_pct!=null){a._ppw+=(+p.profit_pct)*sz; a._pptot+=sz;}
      if(p.opened&&(!a.opened||p.opened<a.opened))a.opened=p.opened;});
    posBase=Object.values(g).map(a=>Object.assign(a,{level:a.size?+(a._wsum/a.size).toFixed(4):a.level,
      current:a._currentWeight?+(a._currentW/a._currentWeight).toFixed(4):null,
      profit:+a.profit.toFixed(2), profit_pct:a._pptot?+(a._ppw/a._pptot).toFixed(2):null}));
  }
  const IGPF=[["igp_location","location"],["igp_market","market"],["igp_sector","sector"],["igp_direction","direction"],["igp_open_month","open_month"]];
  const chartBase=posBase.concat(closedBase);
  const pby=(field,exceptId)=>{const m={};
    chartBase.forEach(p=>{const v=p[field]||"—";if(!(v in m))m[v]=0;});
    chartBase.forEach(p=>{if(!IGPF.every(([id,k])=>id===exceptId||inSet(id,p[k])))return;const v=p[field]||"—";m[v]++;});return m;};
  const profitBy=(field,exceptId)=>{const m={};
    chartBase.forEach(p=>{const v=p[field]||"—";if(!(v in m))m[v]=0;});
    chartBase.forEach(p=>{if(!IGPF.every(([id,k])=>id===exceptId||inSet(id,p[k])))return;const v=p[field]||"—";m[v]+=+p.profit||0;});
    Object.keys(m).forEach(k=>m[k]=+m[k].toFixed(2));return m;};
  const profitCurrencies=new Set(chartBase.filter(p=>p.profit!=null).map(p=>_igCurrency(p.currency)));
  const profitCurrency=profitCurrencies.size===1?[...profitCurrencies][0]:'';
  // Month counts represent actual visible transactions, not the fewer rows produced by Aggregate view.
  const openMonthCounts={};
  posRaw.forEach(p=>{const v=p.open_month||"—";if(!(v in openMonthCounts))openMonthCounts[v]=0;});
  posRaw.forEach(p=>{if(IGPF.every(([id,k])=>id==="igp_open_month"||inSet(id,p[k])))openMonthCounts[p.open_month||"—"]++;});
  if(showClosed)(IGCLOSED||[]).forEach(c=>{const v=(c.date||'').slice(0,7)||"—";openMonthCounts[v]=(openMonthCounts[v]||0)+1;});
  $("ig-pos-viz").innerHTML=(posBase.length||(showClosed&&(IGCLOSED||[]).length))?(
    `<div class="vizsector">`+barChart("Location",profitBy("location","igp_location"),"igp_location",null,false,{profit:true,currency:profitCurrency})+`</div>`+
    `<div class="vizsector">`+barChart("Market",profitBy("market","igp_market"),"igp_market",null,false,{profit:true,currency:profitCurrency})+`</div>`+
    `<div class="vizsector">`+barChart("Sector",profitBy("sector","igp_sector"),"igp_sector",null,false,{profit:true,currency:profitCurrency})+`</div>`+
    `<div class="vizsector">`+barChart("Direction",pby("direction","igp_direction"),"igp_direction",k=>k==="BUY"?"var(--bull)":"var(--bear)")+`</div>`+
    `<div class="vizsector">`+barChart("Open / Closed Month",openMonthCounts,"igp_open_month",null,true)+`</div>`):"";
  packViz("ig-pos-viz");
  const pos=posBase.filter(p=>IGPF.every(([id,k])=>inSet(id,p[k])));
  let closed=closedBase.filter(c=>IGPF.every(([id,k])=>inSet(id,c[k])));
  if(_agg){   // IG can close one logical position as several fills; combine fills from the same close event.
    const g={};
    closed.forEach(c=>{const key=[c.name,c.direction,_igCurrency(c.currency)].join('|');
      const a=g[key]||(g[key]=Object.assign({},c,{size:0,profit:0,_openW:0,_closeW:0,_weight:0,_n:0,_reasons:new Set()}));
      const sz=+c.size||0;a.size+=sz;a.profit+=(+c.profit||0);a._openW+=(+c.level||0)*sz;
      a._closeW+=(+c.current||0)*sz;a._weight+=sz;a._n++;a._reasons.add(c.reason||'UNKNOWN');
      if(c.closed&&(!a.closed||c.closed>a.closed))a.closed=c.closed;});
    closed=Object.values(g).map(a=>{a.level=a._weight?+(a._openW/a._weight).toFixed(4):a.level;
      a.current=a._weight?+(a._closeW/a._weight).toFixed(4):a.current;a.size=+a.size.toFixed(4);
      a.profit=+a.profit.toFixed(2);a.profit_pct=(a.level&&a.current!=null)
        ?+(((a.direction==='SELL'?a.level-a.current:a.current-a.level)/a.level)*100).toFixed(2):null;
      a.reason=a._reasons.size===1?[...a._reasons][0]:'MIXED';return a;});
  }
  const tableRows=genSort(
    pos.map(p=>Object.assign({},p,{_rowKind:'open',status:'Open',activity_date:p.opened||''}))
      .concat(closed.map(c=>Object.assign({},c,{_rowKind:'closed',activity_date:c.closed||''}))),
    igpSortK,igpSortDir);
  const renderedRows=tableRows.map(r=>r._rowKind==='open'
    ? (()=>{const ids=_agg?(r._deal_ids||[]):[r.deal_id],encoded=encodeURIComponent(ids.filter(Boolean).join(','));return `<tr><td><input type="checkbox" ${ids.length?'':'disabled'} ${ids.length&&ids.every(id=>IG_CLOSE_SELECTION.has(id))?'checked':''} onchange="igCloseToggle('${encoded}',this.checked)" title="Select this open position for a confirmed market close"></td><td><span class="tag bull">Open</span></td><td>${nm40(r.name)}${_agg&&r._n>1?` <span class="muted" style="font-size:11px">×${r._n}</span>`:''}</td><td>${r.market||'<span class="muted">—</span>'}</td><td>${_mcapFmt(r.mcap)}</td><td>${_igDtag(r.direction)}</td><td>${_igSz(r.size)}</td><td>${r.level??''}</td><td>${r.current??'<span class="muted">—</span>'}</td><td>${_igPf(r.profit)}</td><td>${_igPfp(r.profit_pct)}</td><td>${_agg?'<span class="muted">—</span>':(r.stop??'')}</td><td>${_agg?'<span class="muted">—</span>':(r.limit??'')}</td><td>${r.currency||''}</td><td>${r.opened||''}</td><td></td><td></td><td><b>${r.ticker||r.epic||''}</b></td></tr>`})()
    : `<tr><td></td><td><span class="tag" style="color:var(--muted)">Closed</span></td><td>${nm40(r.name)}${_agg&&r._n>1?` <span class="muted" style="font-size:11px">×${r._n}</span>`:''}</td><td>${r.market||'<span class="muted">—</span>'}</td><td>${_mcapFmt(r.mcap)}</td><td>${_igDtag(r.direction)}</td><td>${_igSz(r.size)}</td><td>${r.level??''}</td><td>${r.current??''}</td><td>${_igPf(r.profit)}</td><td>${_igPfp(r.profit_pct)}</td><td></td><td></td><td>${r.currency||''}</td><td></td><td>${_igDateTime(r.closed)}</td><td>${_igReason(r.reason)}</td><td><b>${r.ticker||''}</b></td></tr>`);
  if(showClosed&&IGCLOSED===null)renderedRows.push(`<tr><td colspan="17" class="empty refreshing">⏳ Data loading…</td></tr>`);
  else if(showClosed&&_igClosedNote)renderedRows.push(`<tr><td colspan="17" class="empty">${_esc(_igClosedNote)}</td></tr>`);
  const profitTotals={};
  pos.concat(closed).forEach(r=>{if(r.profit==null)return;const c=_igCurrency(r.currency);profitTotals[c]=(profitTotals[c]||0)+(+r.profit||0);});
  const totalProfit=Object.entries(profitTotals).map(([c,v])=>`${_igPf(+v.toFixed(2))} <span class="muted">${c}</span>`).join(' · ');
  const actualRows=pos.length+closed.length;
  const totalRow=actualRows?`<tr style="border-top:2px solid var(--line)"><td></td><td></td><td colspan="7"><b>Total visible profit (${actualRows} rows)</b></td><td><b>${totalProfit||'<span class="muted">—</span>'}</b></td><td colspan="8"></td></tr>`:'';
  $("ig-pos-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${actualRows}</b> transactions <span class="muted">· ${pos.length} open · ${closed.length} closed</span>${showClosed&&IGCLOSED===null?' <span class="refreshing">· ⏳ Data loading…</span>':_igClosedNote?' <span class="muted">· '+_esc(_igClosedNote)+'</span>':''}`;
  $("ig-pos-rows").innerHTML=renderedRows.join("")+totalRow||`<tr><td colspan="18" class="empty ${showClosed&&IGCLOSED===null?'refreshing':''}">${q?'No transactions match that search.':_igNoCreds?'No IG account data — add credentials in Configuration → IG.':'⏳ Data loading…'}</td></tr>`;

  // ── WORKING ORDERS (own strip + sortable table) ──
  const ordBase=IG_ORD.filter(hit).map(o=>Object.assign({},o,_enrCommon(o),{gtd:(o.good_till||"").slice(0,10)||"—"}));
  const IGF=[["igo_location","location"],["igo_market","market"],["igo_sector","sector"],["igo_direction","direction"],["igo_gtd","gtd"]];
  const iby=(field,exceptId)=>{const m={};
    ordBase.forEach(o=>{const v=o[field]||"—";if(!(v in m))m[v]=0;});
    ordBase.forEach(o=>{if(!IGF.every(([id,k])=>id===exceptId||inSet(id,o[k])))return;const v=o[field]||"—";m[v]++;});return m;};
  $("ig-ord-viz").innerHTML=ordBase.length?(
    `<div class="vizsector">`+barChart("Location",iby("location","igo_location"),"igo_location")+`</div>`+
    `<div class="vizsector">`+barChart("Market",iby("market","igo_market"),"igo_market")+`</div>`+
    `<div class="vizsector">`+pieChart("Sector",iby("sector","igo_sector"),"igo_sector")+`</div>`+
    `<div class="vizsector">`+barChart("Direction",iby("direction","igo_direction"),"igo_direction",k=>k==="BUY"?"var(--bull)":"var(--bear)")+`</div>`+
    `<div class="vizsector">`+barChart("Good till",iby("gtd","igo_gtd"),"igo_gtd",null,true)+`</div>`):"";
  packViz("ig-ord-viz");
  const ord=genSort(ordBase.filter(o=>IGF.every(([id,k])=>inSet(id,o[k]))),igoSortK,igoSortDir);
  $("ig-ord-count").innerHTML=cnt(ord.length,IG_ORD.length,"working order");
  // MCap / RVOL / VWAP / ATR / VolumeScore / R:R / Quality (user 2026-09-03) come from the server's
  // _attach_setup_metrics, the same resolver behind Pre-orders to my IG, and render through the same
  // shared cell formatters -- so an order shows identical figures on both screens. A missing value is a
  // muted dash and never a cross: we did not measure a failure, we measured nothing.
  IGORD=ord;
  $("ig-ord-rows").innerHTML=ord.map(o=>`<tr><td>${nm40(o.name)}</td><td>${o.market||'<span class="muted">—</span>'}</td><td>${_mcapFmt(o.mcap)}</td><td>${rvolCell(o.rvol)}</td><td>${_tickCross(o.above_vwap)}</td><td>${_tickCross(o.atr_expanding)}</td><td>${volScoreCell(o.volume_score)}</td><td>${o.rr!=null?(+o.rr).toFixed(1):'<span class="muted">—</span>'}</td><td>${o.quality!=null?`<b style="color:${qcol(o.quality)}">${o.quality}</b>`:'<span class="muted">—</span>'}</td><td>${_igDtag(o.direction)}</td><td>${_igSz(o.size)}</td><td>${o.level??''}</td><td>${o.type||''}</td><td>${_igSrc(o.source)}</td><td>${o.good_till||''}</td><td><b>${o.ticker||o.epic||''}</b></td></tr>`).join("")||`<tr><td colspan="16" class="empty">${q?'No working orders match that search.':'No working orders.'}</td></tr>`;
}
// Closed trades are loaded into the main transactions table (user 2026-08-04); no duplicate table below.
let IGCLOSED=null, _igClosedNote="", _igNoCreds=false;
function loadIgClosed(force){
  if(IGCLOSED!==null&&!force){paintIgAccount();return;}
  IGCLOSED=null;_igClosedNote="Closed trades are still loading from IG history.";paintIgAccount();
  fetch("/api/ig-closed",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{trades:[]})
    .then(j=>{IGCLOSED=j.trades||[];_igClosedNote=j.note||"";igOpenMonthInitialised=false;paintIgAccount();})
    .catch(()=>{IGCLOSED=[];_igClosedNote="Could not load closed trades.";paintIgAccount();});
}
function _igReason(r){
  const m={STOP_HIT:['🛑 Stop','var(--bear)'],TARGET_HIT:['🎯 Target','var(--bull)'],MANUAL:['👤 Manual','var(--fg)'],SYSTEM:['⚙️ System','var(--muted)'],MIXED:['Mixed','var(--muted)']}[r]||['—','var(--muted)'];
  return `<span style="color:${m[1]};font-weight:600">${m[0]}</span>`;
}
function renderIgAccount(ev){
  const btn=ev&&ev.target?ev.target:$("ig-refresh"), was=btn?btn.textContent:"";
  if(btn){btn.disabled=true;btn.className="sqh-loading";btn.textContent="⏳ Data loading…";}
  IGCLOSED=null;_igClosedNote="";_igNoCreds=false;
  $("ig-note").textContent="";$("ig-acct").innerHTML="";
  $("ig-pos-count").innerHTML=`<span class="refreshing">⏳ Data loading…</span>`;
  $("ig-pos-rows").innerHTML=`<tr><td colspan="18" class="empty refreshing">⏳ Data loading…</td></tr>`;
  $("ig-ord-rows").innerHTML=`<tr><td colspan="16" class="empty refreshing">⏳ Data loading…</td></tr>`;
  const loadAccount=(attempt=0)=>fetch("/api/ig-account",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(j=>{
      IG_POS=j.positions||[]; IG_ORD=j.orders||[]; IG_CLOSE_SELECTION.clear(); updateIgCloseButton(); igOpenMonthInitialised=false;
      // Account name + obfuscated number above the positions (user 2026-07-20). Number is already masked
      // server-side (last 3 chars only).
      $("ig-acct").innerHTML=(j.account_name||j.account_masked)
        ?`🏦 <b style="color:var(--fg)">${_esc(j.account_name)||'IG account'}</b>${j.account_masked?` <span class="muted" style="font-weight:400">· acct ${_esc(j.account_masked)}</span>`:''}`:"";
      // No IG credentials (user 2026-07-26, P-07 #91/#92): show a prominent warning AND a button that
      // jumps straight to Configuration → IG, instead of only a muted "set them in Configuration" note.
      const noCreds=j.no_creds||((j.note||"").toLowerCase().indexOf("no ig credentials")===0);
      _igNoCreds=!!noCreds;if(noCreds)IGCLOSED=[];
      $("ig-note").innerHTML=noCreds
        ? `<div class="card" style="border-color:#d29922;background:color-mix(in srgb,#d29922 12%,transparent);display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 6px"><span style="font-size:13px">⚠️ <b style="color:var(--fg)">No IG credentials set.</b> <span class="muted">Add your IG login to see your account and place orders.</span></span><span class="grow" style="flex:1"></span><button class="btn" style="border-color:#d29922" onclick="openIgSettings()">🔑 Open IG settings</button></div>`
        : (j.note?`<span class="muted">🔒 ${j.note}</span>`:"");
      paintIgAccount();
      // Re-check the live working orders against the CURRENT settings (user 2026-09-03). Must follow
      // paintIgAccount, which is what populates IGORD -- the panel joins the audit to a real deal id and
      // can only offer an order IG is still holding.
      if(!noCreds)loadOrderFilterAudit();
      // Load closed trades directly into the main transaction table (user 2026-08-04).
      if(!noCreds)loadIgClosed(true);
    })
    .catch(err=>{if(attempt<3){$("ig-pos-count").innerHTML=`<span class="refreshing">⏳ Data loading… retry ${attempt+1} of 3.</span>`;setTimeout(()=>loadAccount(attempt+1),1500*(attempt+1));return;}
      $("ig-note").innerHTML=`<span style="color:var(--bear)">IG account read failed: ${_esc(err.message||"unknown error")}.</span>`;
      $("ig-pos-count").innerHTML=`<span style="color:var(--bear)">Open positions could not be loaded.</span>`;
      $("ig-pos-rows").innerHTML=`<tr><td colspan="18" class="empty" style="color:var(--bear)">IG did not return open positions. Use Refresh to retry.</td></tr>`;
      $("ig-ord-rows").innerHTML=`<tr><td colspan="16" class="empty" style="color:var(--bear)">IG did not return working orders.</td></tr>`;})
    .finally(()=>{if(btn){btn.disabled=false;btn.className="btn";btn.textContent=was;}});
  loadAccount();
}
// Jump from the IG Account warning straight to Configuration → IG (user 2026-07-26, P-07 #91). The IG
// credential panel is built ASYNCHRONOUSLY by renderCredentials(), so poll for it before confShow('IG').
function openIgSettings(){
  showTab('config');
  if(typeof renderCredentials==="function")renderCredentials();   // ensure the credential panels get built
  let n=25; const tick=()=>{ if(document.querySelector('#view-config .confpanel[data-panel="IG"]')){confShow('IG');}
    else if(--n>0){setTimeout(tick,120);} };
  tick();
}
// No-IG-credentials warning + "Open IG settings" button on My Pre-orders and Pre-orders-to-my-IG
// (user 2026-07-27, P-10 L218/L225 + P-25/30 L219/L226). Same amber card + button as the IG Account
// page (P-07 #91/#92). /api/ig-status is a CHEAP check (no live IG login); cached so switching between
// the two pages only calls it once. Pass force=true after saving credentials to re-check.
let _IG_NOCREDS=null, _IG_STATUS_P=null;
function _igStatus(force){
  if(force){_IG_NOCREDS=null;_IG_STATUS_P=null;}
  if(_IG_STATUS_P)return _IG_STATUS_P;
  _IG_STATUS_P=fetch("/api/ig-status",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{no_creds:false})
    .then(j=>{_IG_NOCREDS=!!j.no_creds;return _IG_NOCREDS;}).catch(()=>{_IG_NOCREDS=false;return false;});
  return _IG_STATUS_P;
}
function renderIgCredWarn(id){
  const el=$(id); if(!el)return;
  if(!AUTH){el.innerHTML="";return;}                    // logged-out: no warning (can't set creds anyway)
  const paint=()=>{ el.innerHTML=_IG_NOCREDS
    ? `<div class="card" style="border-color:#d29922;background:color-mix(in srgb,#d29922 12%,transparent);display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 10px"><span style="font-size:13px">⚠️ <b style="color:var(--fg)">No IG credentials set.</b> <span class="muted">Add your IG login to place these orders on your account.</span></span><span class="grow" style="flex:1"></span><button class="btn" style="border-color:#d29922" onclick="openIgSettings()">🔑 Open IG settings</button></div>`
    : ""; };
  if(_IG_NOCREDS===null){_igStatus().then(paint);}else{paint();}
}
async function maRebuild(){
  if(!await appConfirm("This rescans the whole universe (a few minutes) and refreshes the data for everyone.",{title:"Rebuild the snapshot now?",ok:"↻ Rebuild"}))return;
  const b=$("ma-rebuild"),m=$("ma-msg");b.disabled=true;m.style.color="var(--muted)";m.textContent="Rebuild starting…";
  fetch("/api/refresh",{method:"POST",headers:{"X-Auth":AUTH}}).then(r=>r.json().then(j=>({ok:r.ok,j})))
    .then(({ok,j})=>{if(ok&&j.started){m.style.color="var(--bull)";m.textContent="Rebuild running in the background — reload in a few minutes to see fresh data.";}
      else if(ok&&j.busy){m.style.color="#d29922";m.textContent="A rebuild is already running — try again shortly.";}
      else{m.style.color="var(--bear)";m.textContent=(j&&j.error)||"Could not start the rebuild.";}b.disabled=false;})
    .catch(()=>{m.style.color="var(--bear)";m.textContent="Could not start the rebuild.";b.disabled=false;});
}
// ── Performance tab (user 2026-07-13): every RECORDED trigger (hvf_triggers) with its committed
//    entry/stop/target/date, classified STOPPED / TARGET / OPEN from price history since the trigger.
//    Served pre-computed by /api/performance (source of truth = the recorded events, not the snapshot). ──
let pfSortK="trig_date", pfSortDir=1, PERF_DATA=null, PERF_GEN="", PF_LOC_FILTER="", PF_INITIALISED=false;   // default: Triggered date ascending / oldest first (user 2026-08-01)
let PF_COMBO=null;   // clicked "Best Quality / R:R combination" card → filter the Results table to that band (user 2026-07-27, P-10 L292)
let PF_VS_FLOOR=0;   // active personal Minimum Volume Score floor applied to the Performance population (user 2026-07-28); 0 = off
// Quality / R:R / Volume-Score band click-to-filter (user 2026-07-27 P-10 L292; volume added 2026-08-01).
// vlo/vhi bound the volume band ('—' band = unscored rows, vlo/vhi null).
function pfComboFilter(qb,rb,vlo,vhi,vb){
  const q=+qb,r=+rb,lo=(vlo==null||vlo==='null')?null:+vlo,hi=(vhi==null||vhi==='null')?null:+vhi,b=vb||"—";
  PF_COMBO=(PF_COMBO&&PF_COMBO.qb===q&&PF_COMBO.rb===r&&PF_COMBO.vb===b)?null:{qb:q,rb:r,vlo:lo,vhi:hi,vb:b};
  _renderPerformance();
}
// Does a row fall in the currently-picked Quality/R:R/Volume combo band?
function _pfInCombo(r){
  if(!PF_COMBO||r.quality==null||r.rr==null)return false;
  if(Math.floor(r.quality/10)*10!==PF_COMBO.qb||Math.floor(+r.rr)!==PF_COMBO.rb)return false;
  const vs=r.volume_score;
  if(PF_COMBO.vb==="—")return vs==null;
  return vs!=null&&vs>=PF_COMBO.vlo&&vs<=PF_COMBO.vhi;
}
const _stcol=s=>s==="TARGET"?"var(--bull)":s==="STOPPED"?"var(--bear)":"#d29922";   // OPEN = amber
function pfLocFilter(loc){PF_LOC_FILTER=(PF_LOC_FILTER===loc)?"":loc;PF_RENDER_LIMIT=PF_RENDER_STEP;_renderPerformance();}   // toggle the Summary-title location filter
// Date filter (user 2026-07-18, P-01) — one trigger-date window drives BOTH Performance sub-tabs. A preset
// dropdown (Last 3/6/9/12 months) sets it quickly; the window label feeds the "Trades" card title (P-01).
let PF_DATE_FROM="", PF_DATE_TO="", PF_WINDOW_LABEL="last 12 months";
const PF_RENDER_STEP=500;                 // reduce paging while keeping the 23-column Back Test DOM responsive
let PF_RENDER_LIMIT=PF_RENDER_STEP, PF_SEARCH_TIMER=null;
function pfSearchChange(){clearTimeout(PF_SEARCH_TIMER);PF_SEARCH_TIMER=setTimeout(()=>{PF_RENDER_LIMIT=PF_RENDER_STEP;_renderPerformance();},150);}
function pfShowMore(){PF_RENDER_LIMIT+=PF_RENDER_STEP;_renderPerformance();}
// Date window, DISABLED 2026-08-28 (user: "Date filter should be hidden - it is of little value
// currently - Also, must not have any values in it that affects results").
//
// Hiding the control alone would not satisfy the second half. pfDateOk runs FIRST in the Performance
// pipeline — `all = PERF_DATA.filter(r=>pfDateOk(r.trig_date))` — so any lingering From/To value silently
// shrinks every number on the tab: row count, win:loss, wallet, the lot. The control also shipped
// contradicting itself: the select displayed "Last 12 months" while PF_DATE_FROM was "", because
// pfDatePreset only ever ran on change, so the screen claimed a window it was not applying.
//
// So the filter is switched off explicitly rather than merely made unreachable. Flip this to true and
// un-hide .pf-date-group in index.html to restore it; the logic below is intact and still tested.
const PF_DATE_FILTER_ENABLED=false;
function pfDateOk(d){if(!PF_DATE_FILTER_ENABLED)return true;
  if(!d)return true;if(PF_DATE_FROM&&d<PF_DATE_FROM)return false;if(PF_DATE_TO&&d>PF_DATE_TO)return false;return true;}
function _pfMonthsAgo(n){const d=new Date();d.setMonth(d.getMonth()-n);return d.toISOString().slice(0,10);}
const _ymdLocal=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;  // local Y-M-D (avoids the UTC off-by-one from toISOString at a BST midnight)
function pfDatePreset(v){
  const fr=$("pf-date-from"), to=$("pf-date-to");
  if(!v){if(fr)fr.value="";if(to)to.value="";PF_WINDOW_LABEL="all 12 months";}
  else if(v==="tm"){const d=new Date(),f=new Date(d.getFullYear(),d.getMonth(),1);   // This month → 1st of the current month
    if(fr)fr.value=_ymdLocal(f);if(to)to.value="";PF_WINDOW_LABEL="this month";}
  else if(v==="4w"){const d=new Date();d.setDate(d.getDate()-28);                     // Last 4 weeks → today − 28 days
    if(fr)fr.value=_ymdLocal(d);if(to)to.value="";PF_WINDOW_LABEL="last 4 weeks";}
  else{if(fr)fr.value=_pfMonthsAgo(+v);if(to)to.value="";PF_WINDOW_LABEL="last "+v+" months";}
  pfDateChange();
}
function pfDateChange(manual){
  PF_DATE_FROM=(($("pf-date-from")||{}).value)||"";PF_DATE_TO=(($("pf-date-to")||{}).value)||"";
  if(manual){const ps=$("pf-date-preset");if(ps)ps.value="";
    PF_WINDOW_LABEL=(PF_DATE_FROM||PF_DATE_TO)?((PF_DATE_FROM||'…')+' → '+(PF_DATE_TO||'now')):"all 12 months";}
  PF_RENDER_LIMIT=PF_RENDER_STEP;_renderPerformance();if(typeof WIN!=='undefined'&&WIN!==null)paintOrdersPerf();
}
function renderPerformance(){
  // "What separates the winners" is ADMIN ONLY (user 2026-07-18). For non-admins there is only the
  // Results panel, so a lone "Results" pill reads oddly (P-01a) — hide the whole sub-nav bar for them,
  // and never leave a non-admin sitting on the analysis panel.
  // Summary + Results are the core client views. Specialist analysis is grouped behind one control
  // and restricted to Silver/Gold administrators.
  const _pills=$("pf-pills");
  if(_pills)_pills.style.display='flex';
  const _ag=$("pf-advanced-group");
  if(_ag)_ag.style.display='none';
  const _rg=$("pf-run-group");   // "Let winners run" pill (Silver/Gold admin, same gate as Advanced analysis)
  if(_rg)_rg.style.display=advancedPfAllowed()?'':'none';
  const _lg=document.querySelector(".pf-loc-group");
  if(_lg)_lg.style.display=document.querySelector("#pf-panel-summary:not(.hidden)")?"":"none";
  if(!PF_INITIALISED){PF_INITIALISED=true;pfPanel("settings");}
  if(!advancedPfAllowed() && (!$("pf-panel-analysis").classList.contains("hidden")||!$("pf-panel-run").classList.contains("hidden")))pfPanel('results');
  const _pfl=$("pf-loading");
  if(PERF_DATA===null){                       // fetch the recorded-trigger report once, then render
    PERF_DATA=[];                             // in-flight guard
    if(_pfl)_pfl.innerHTML=`<span class="sqh-loading" style="display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;border:1px solid #d29922;background:color-mix(in srgb,#d29922 12%,transparent);border-radius:999px;padding:4px 12px">⏳ Data loading…</span>`;
    // X-Auth required from 2026-08-28: /api/performance now refuses anonymous callers, because its rows
    // are only ever narrowed by the VIEWER'S OWN saved limits, so a logged-out visitor was receiving the
    // unfiltered superset. Logged-in users need the token here or they lose the tab along with them.
    fetch("/api/performance",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{rows:[]})
      .then(j=>{
        // Server is still building the 12-month replay (cold cache, first load after a restart, user
        // 2026-08-03 P-01): it returns {warming:true} instantly instead of blocking ~40s. Keep the badge
        // and poll until the rows are ready, so the tab never hangs.
        if(j.warming){PERF_DATA=null;if($("pf-loading"))$("pf-loading").innerHTML=`<span class="sqh-loading" style="display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;border:1px solid #d29922;background:color-mix(in srgb,#d29922 12%,transparent);border-radius:999px;padding:4px 12px">⏳ Data loading…</span>`;setTimeout(renderPerformance,3000);return;}
        PERF_DATA=_dedupeSameDayRows(j.rows||[]);PERF_GEN=j.generated||"";if($("pf-loading"))$("pf-loading").innerHTML="";_renderPerformance();})
      .catch(()=>{PERF_DATA=null;if($("pf-loading"))$("pf-loading").innerHTML=`<span style="color:var(--bear);font-size:12.5px">Performance data did not load. <button class="subpill" onclick="renderPerformance()">Retry</button></span>`;});
    // The analysis loads lazily when its sub-tab is opened (pfPanel), not here.
  } else if(_pfl && PERF_DATA.length){_pfl.innerHTML="";}
  _renderPerformance();
}
// Performance sub-tabs (user 2026-07-17): Results vs the 15-month analysis. The analysis fetches lazily
// on first open, so the Results view is never slowed by it.
function pfPanel(which){
  if(which==="summary")which="settings";
  if((which==="analysis"||which==="run")&&!advancedPfAllowed())which="results";
  document.querySelectorAll("#pf-pills .pill,#pf-advanced-nav .pill").forEach(b=>b.classList.toggle("active",b.dataset.pfpanel===which));
  const advNav=$("pf-advanced-nav");
  if(advNav&&advancedPfAllowed()&&(which==="analysis"||which==="run"))advNav.classList.remove("hidden");
  if(advNav&&which!=="analysis"&&which!=="run")advNav.classList.add("hidden");
  const sm=$("pf-panel-summary"), res=$("pf-panel-results"), an=$("pf-panel-analysis"), run=$("pf-panel-run"), settings=$("pf-panel-settings");
  if(sm)sm.classList.toggle("hidden",which!=="summary");   // Summary sub-tab (user 2026-07-20)
  if(res)res.classList.toggle("hidden",which!=="results");
  if(an)an.classList.toggle("hidden",which!=="analysis");
  if(run)run.classList.toggle("hidden",which!=="run");
  if(settings)settings.classList.toggle("hidden",which!=="settings");
  const model=$("pf-shared-model");if(model)model.style.display=which==="results"?"none":"flex";   // shared by Summary / analysis / run; Results has its own recorded-trigger wallet control (P-07)
  const filters=$("pf-subnav");if(filters)filters.classList.toggle("hidden",which==="run"||which==="settings");   // these filters feed neither annual settings nor /api/winners-run
  const locGroup=document.querySelector(".pf-loc-group");
  if(locGroup)locGroup.style.display=which==="summary"?"":"none";
  // Date windows filter the Results/Summary evidence but do not recalculate Best Settings.  Hide them
  // on that resource-heavy analysis surface rather than implying a change to its recommendation cards.
  const dateGroup=document.querySelector(".pf-date-group");
  if(dateGroup)dateGroup.style.display=which==="analysis"?"none":"";
  if(which==="results"&&PF_LOC_FILTER){PF_LOC_FILTER="";if(typeof _renderPerformance==="function")_renderPerformance();}
  const wh=$("pf-winners-head");if(wh)wh.classList.toggle("hidden",which!=="analysis");   // winners title/lede above the filters (P-07 L289)
  if(which==="analysis"||which==="settings")renderSqueezeAnalysis();
  if(which==="run")winnersRunChange('pf');
  if(which==="results"&&typeof _renderPerformance==='function')_renderPerformance();
  if(which==="summary"&&typeof _renderPerformance==="function")_renderPerformance();   // rebuild the "Settings used" card for this sub-tab (user 2026-07-24, P-02)
}
// ── 15-month squeeze analysis (user 2026-07-17, P-21b) ───────────────────────────────────────────────
// Cherry-pick presets, derived from the 15-month data (user 2026-07-18): RVOL lifts win RATE; Quality /
// R:R lift PROFIT per trade. Each drives both the analysis (server filter) and the Scanner (setCherry).
const CHERRY_PRESETS=[
  {id:"all",      label:"All setups",            f:{}},
  {id:"win",      label:"High win · RVOL>1.8",   f:{rvmin:1.8}},
  {id:"profit",   label:"High profit · R:R≥8",   f:{rrmin:8}},
  {id:"quality",  label:"High quality · Q≥70",   f:{qmin:70}},
  {id:"balanced", label:"Balanced · 1.8/60/5",   f:{rvmin:1.8,qmin:60,rrmin:5}},
];
const _cherryEq=(a,b)=>JSON.stringify(a||{})===JSON.stringify(b||{});
// "What separates the winners" (admin) is built from the SAME 369 recorded trades as the Results tab
// (user 2026-07-18: "369 rows in main performance tab can be used"), expressed in £ as a fixed 2%-of-wallet
// stake (£200 on £10k) × each trade's actual return%. So it reconciles with Results by construction — no
// separate population, no R-multiple, no compounding-to-millions.
let WINNERS_WALLET=10000, WINNERS_STAKE=0.05, WINNERS_MAXOPEN=20;   // £ wallet, stake fraction and explicit concurrency cap
// Transaction-evidence render cap (user 2026-08-22: "This page isn't responding").
//
// paintOrdersPerf built EVERY ledger entry into one innerHTML assignment. On the three-year window that
// is 11,669 rows x 18 cells -- measured at 7.5 MB of HTML and roughly 233,000 DOM elements -- parsed
// synchronously on the main thread, which is what produced Chrome's "This page isn't responding" dialog.
// Building the string costs only ~16 ms; the DOM parse and layout are the whole cost, so the fix is to
// stop materialising rows nobody has scrolled to, not to build them faster.
//
// This is a RENDER cap only. The ledger itself is unchanged and still computed over every row, so the
// trade count, wallet, net £ and every dimension chart are identical to before -- they read the ledger,
// never the DOM.
// 250, not 1500 (user 2026-08-25: "clicking on best settings when the data is not loaded seems to freeze
// the whole site"). 1,500 rows of this 18-column table is about 27,000 elements -- the same order as the
// Instruments table that measurably froze the tab -- so the original cap was far too high to fix
// anything. 250 matches the limit Instruments settled on.
//
// A NOTE ON THE NUMBERS: timings gathered through the automation harness came from a BACKGROUND tab, and
// Chrome throttles rendering and layout there while leaving raw JS alone. A pure CPU loop measured a
// steady 17-21 ms in the same page where 250 DOM rows appeared to take 6,682 ms and 1,500 rows 3,392 ms
// -- smaller work "slower" than larger. Those figures are scheduling noise and must not be quoted. The
// freeze itself is real and user-reported twice; the remedy chosen here is the structural one, fewer DOM
// nodes, which is sound independently of any stopwatch.
//
// The ledger behind the table is still computed over EVERY row, so the trade count, wallet and net gain
// are unchanged; only the rows drawn are limited.
let WIN=null, WIN_GENERATED="", WIN_3Y=null;
let WIN_LOADING=false, WIN_3Y_LOADING=false;
// Three-year evidence failure, kept distinct from "not loaded yet". Without it a failed fetch left
// WIN_3Y at null for ever and the card sat on "Evidence loading separately..." with no Apply button
// and no error -- a refusal impersonating work in progress (user 2026-08-28, and the same shape as
// the Best settings history spinner fixed the same day).
let WIN_3Y_ERROR="";
// Memo for the three-year grid search — see renderBestCombo. Cleared implicitly when WIN_3Y is replaced.
let _3Y_MEMO={rows:null,wallet:null,minTrade:null,best:null};
// Wallet £ (L211), Max position size % (L200), Max open positions (L199) are variables on the winners tab.
let MIN_TRADE=25;   // User-configured minimum trade (£), default 25 (user 2026-07-18, P-05 L253)
// _fundedMaxOpen MOVED to hvf_web/best_settings.js (2026-09-03); it is a global there, as it was here.
function paintBestSettingsPersonalisation(){
  const el=$("best-settings-personalisation-values");if(!el)return;
  const money=value=>`£${Number(value||0).toLocaleString(undefined,{maximumFractionDigits:2})}`;
  const pct=(WINNERS_STAKE*100).toLocaleString(undefined,{maximumFractionDigits:2});
  el.textContent=`${money(WINNERS_WALLET)} wallet · ${money(MIN_TRADE)} minimum trade · ${pct}% position size · ${WINNERS_MAXOPEN} maximum open positions`;
}
function winnersParamsChange(){
  const wEl=$("ordp-wallet"), moEl=$("ordp-maxopen");
  let w=+((wEl||{}).value||0), s=+(($("ordp-stake")||{}).value||0), mo=+((moEl||{}).value||0);
  const fmt=v=>"£"+Math.round(v).toLocaleString();   // money() is local to paintOrdersPerf, so format here
  let warn="";
  // Validation (user 2026-07-18, P-05 L257): keep the Wallet sane — blank / non-positive / absurd values
  // are clamped so the derived figures never break.
  if(!(w>0)){w=1000; if(wEl)wEl.value=w; warn=`Wallet must be a positive number — reset to £1,000.`;}
  else if(w>1e8){w=1e8; if(wEl)wEl.value=w; warn=`Wallet capped at £100,000,000.`;}
  WINNERS_WALLET=w;
  if(s>0)WINNERS_STAKE=s/100;
  const autoMax=_fundedMaxOpen(WINNERS_STAKE);
  if(!(mo>0)){mo=autoMax;if(moEl)moEl.value=mo;}
  WINNERS_MAXOPEN=Math.min(mo,autoMax);
  paintBestSettingsPersonalisation();
  // This is the notional allocated to one position at the starting wallet.  It must not be
  // presented as Wallet ÷ max-open: a user may deliberately choose a lower open-position cap.
  const openingStake=w*WINNERS_STAKE;
  const mt=$("ordp-maxtrade"); if(mt)mt.textContent=`${fmt(openingStake)} (${(WINNERS_STAKE*100).toFixed(1)}%)`;
  const mn=$("ordp-mintrade"); if(mn)mn.textContent=MIN_TRADE;
  // The replay's position size is NOTIONAL exposure, not margin.  Make the distinction
  // quantitative: broker leverage changes reserved margin, not P&L, unless the user explicitly
  // chooses a leveraged-notional strategy model (which this display-only change does not do).
  const grossExposure=openingStake*WINNERS_MAXOPEN, marginAt10x=grossExposure/10,
        freeMargin=Math.max(0,w-marginAt10x), move4PerPosition=openingStake*.04,
        move4AtCapacity=grossExposure*.04, exposurePct=grossExposure/w*100;
  const exposure=$("ordp-exposure");if(exposure)exposure.innerHTML=`<b style="color:var(--fg)">How this replay uses your £:</b> one opening position is <b>${fmt(openingStake)}</b> notional (${(WINNERS_STAKE*100).toFixed(1)}% of the ${fmt(w)} wallet). At the ${WINNERS_MAXOPEN}-position cap, gross market exposure is <b>${fmt(grossExposure)}</b> (${exposurePct.toFixed(0)}% of the starting wallet). At an illustrative <b>10×</b> broker leverage, that needs <b>${fmt(marginAt10x)}</b> margin and leaves <b>${fmt(freeMargin)}</b> free margin. <br><span class="muted">A 4% move in one ${fmt(openingStake)} position is ${fmt(move4PerPosition)} (${(WINNERS_STAKE*4).toFixed(2)}% of the wallet). If every position moved 4% together, it is ${fmt(move4AtCapacity)} (${(exposurePct*.04).toFixed(2)}% of the wallet). <b style="color:var(--fg)">10× reduces the margin needed; it does not make the replay return 10×.</b> Modelling 10× larger positions would be a separate leveraged-notional strategy change, not a display setting.</span>`;
  // If the wallet can't fund that many positions at the IG minimum, warn (L257).
  if(openingStake<MIN_TRADE && !warn)
    warn=`Max position size gives an opening stake of ${fmt(openingStake)}, below the £${MIN_TRADE} minimum — raise the Wallet or Max position size.`;
  if(mo>autoMax && !warn)
    warn=`Max open positions capped at ${autoMax} because ${(WINNERS_STAKE*100).toFixed(1)}% size can fund at most ${autoMax} positions.`;
  const msg=$("ordp-model-msg"); if(msg){msg.textContent=warn; msg.style.color=warn?"var(--bear)":"";}
  paintOrdersPerf(); winnersSLChange(); winnersRunChange('ordp');   // re-render the plain view + SL + let-winners-run illustrations
}
// Save the winners Model variables as the user's persistent defaults (user 2026-07-28, P-10 L158) — stored
// on the user record via /api/config limits, and re-applied to the Model inputs on the next login (loadLimits).
function winnersSaveDefaults(){
  const wallet=Math.max(1,+(($("ordp-wallet")||{}).value)||1000);
  const stake=Math.max(0.1,+(($("ordp-stake")||{}).value)||5);
  const mo=Math.max(1,Math.floor(+(($("ordp-maxopen")||{}).value)||_fundedMaxOpen(stake/100)));
  const msg=$("ordp-save-msg");
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({limits:{wallet,max_position_pct:stake,max_open:mo}})})
    .then(r=>{if(!r.ok)throw 0;MY_LIMITS={...MY_LIMITS,wallet,max_position_pct:stake,max_open:mo};if(msg){msg.style.color="var(--bull)";msg.textContent="✓ Saved as your defaults.";setTimeout(()=>{if(msg)msg.textContent="";},4000);}})
    .catch(()=>{if(msg){msg.style.color="var(--bear)";msg.textContent="Save failed.";}});
}
// Apply saved winners-Model defaults to the Model inputs (user 2026-07-28, P-10 L158) — called from loadLimits.
function applyWinnersDefaults(){
  MIN_TRADE=Math.max(0,Number(MY_LIMITS.min_trade??25)||0);
  const set=(id,v)=>{const e=$(id);if(e&&v!=null&&v!=="")e.value=v;};
  if(MY_LIMITS.wallet!=null)set("ordp-wallet",MY_LIMITS.wallet);
  if(MY_LIMITS.max_position_pct!=null)set("ordp-stake",MY_LIMITS.max_position_pct);
  const derivedMax=Math.max(1,Math.floor(100/Math.max(.1,+MY_LIMITS.max_position_pct||5)));
  const moEl=$("ordp-maxopen"); if(moEl&&MY_LIMITS.max_open!=null)moEl.value=MY_LIMITS.max_open>0?MY_LIMITS.max_open:derivedMax;
  if(MY_LIMITS.wallet!=null)set("pfw-wallet",MY_LIMITS.wallet);
  if(MY_LIMITS.max_position_pct!=null)set("pfw-stake",MY_LIMITS.max_position_pct);
  const pfMo=$("pfw-maxopen");if(pfMo&&MY_LIMITS.max_open!=null)pfMo.value=MY_LIMITS.max_open>0?MY_LIMITS.max_open:derivedMax;
  if(typeof winnersParamsChange==="function")winnersParamsChange();
}
// Shared same-ticker/same-day dedup (user 2026-08-11, P-03 "reconcile all reports on all the tabs to make
// sure they back each other up for evidence"): the scanner runs multiple independent lookback windows
// (daily-30/60/90/180/240 + weekly), and more than one can trigger the same ticker on the same day,
// double-counting it in any report built from raw trigger rows (e.g. "Domino's Pizza Enterprises Limited",
// ChangeRequest P-04). That was fixed for Best Settings' own LOCAL population on 2026-08-07, but WIN and
// PERF_DATA themselves — the shared arrays EVERY Performance report reads from (Best Settings, Back Test,
// Summary, Let winners run) — stayed raw/undeduped, so those other reports could silently disagree with
// Best Settings' already-deduped counts/win-loss ratios for what should be the same underlying trades.
// Deduping ONCE here, at the shared source, means every report reads the same consistent population
// instead of each one needing to remember to dedup itself (renderBestCombo's own local dedup below is now
// redundant against this but kept — deduping already-deduped rows is a harmless no-op, not a risk).
function _dedupeSameDayRows(rows){
  const byKey={};
  (rows||[]).forEach(r=>{const k=(r.ticker||'')+'|'+String(r.trig_date||'').slice(0,10);
    const cur=byKey[k]; if(!cur||(+r.perf||-Infinity)>(+cur.perf||-Infinity))byKey[k]=r;});
  return Object.values(byKey);
}
function renderSqueezeAnalysis(){
  loadBestSettingsHistory();
  renderVolScoreReport();   // VolumeScore impact report (user 2026-07-24, P-02) — loads once, server-cached
  renderBestSettings();     // best settings by quarter (user 2026-07-25, P-04 L59) — loads once, server-cached
  if(WIN!==null)return winnersParamsChange();   // route through winnersParamsChange so the derived Min/Max Trade populate (P-05)
  if(WIN_LOADING)return;
  WIN_LOADING=true; const s=$("ordp-summary"); if(s)s.innerHTML='<div class="sqh-loading" style="font-size:13px;padding:10px">⏳ Data loading…</div>';
  // Load the annual decision surface first.  Starting the large three-year evidence request in parallel
  // made the browser wait on two expensive server builds and could leave the tab unresponsive; it is
  // now deferred and only refreshes the cards when it is ready.
  const loadThreeYear=(attempt=0)=>{if(WIN_3Y_LOADING||WIN_3Y!==null)return;WIN_3Y_LOADING=true;fetch("/api/winners?years=3",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();}).then(j3=>{if(j3.error)throw new Error(j3.error);WIN_3Y=_dedupeSameDayRows(j3.rows||[]);WIN_3Y_LOADING=false;winnersParamsChange();}).catch(err=>{if(attempt<2){WIN_3Y_LOADING=false;setTimeout(()=>loadThreeYear(attempt+1),2000*(attempt+1));return;}WIN_3Y_LOADING=false;WIN_3Y_ERROR=String((err&&err.message)||err||"unavailable");console.warn("Three-year evidence unavailable",err);if(typeof winnersParamsChange==="function")winnersParamsChange();});};
  window.retryThreeYear=()=>{WIN_3Y_ERROR="";if(typeof winnersParamsChange==="function")winnersParamsChange();loadThreeYear();};
  const loadWinners=(attempt=0)=>fetch("/api/winners",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(j=>{if(j.error)throw new Error(j.error);WIN_LOADING=false;WIN_GENERATED=j.generated||"";WIN=_dedupeSameDayRows(j.rows||[]);winnersParamsChange();setTimeout(()=>loadThreeYear(),250);})
    .catch(err=>{if(attempt<3){if(s)s.innerHTML=`<div class="sqh-loading" style="font-size:13px;padding:10px">⏳ Data loading… retry ${attempt+1} of 3.</div>`;setTimeout(()=>loadWinners(attempt+1),1500*(attempt+1));return;}
      WIN_LOADING=false;WIN=null;const b=$("ordp-bestcombo");if(b)b.innerHTML=`<div class="empty">Annual settings could not be loaded: ${_esc(err.message||'unknown error')}. <button class="btn" onclick="renderSqueezeAnalysis()">↻ Retry</button></div>`;});
  loadWinners();
}
// VolumeScore impact report (user 2026-07-24, P-02 L55): admin-only; fetched once (server-cached). Shows
// whole book vs the ≥threshold subset (win rate, avg return, compounded £) and per-band profitability.
let BEST_LOADED=false;
function renderBestSettings(){
  const box=$("best-settings-report"); if(!box||BEST_LOADED)return;
  BEST_LOADED=true;
  const ret=v=>v==null?'—':`<span style="color:${v>=0?'var(--bull)':'var(--bear)'}">${v>0?'+':''}${v}%</span>`;
  const b=x=>x?`<b>${x.value}</b> <span class="muted">(${ret(x.avg)}, ${x.n})</span>`:'—';
  fetch("/api/best-settings",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    box.classList.remove("sqh-loading");
    if(!j||j.error){box.innerHTML=`<span class="muted" style="font-size:12px">${(j&&j.error)||'Best-settings report unavailable.'}</span>`;BEST_LOADED=false;return;}
    if($("best-span"))$("best-span").textContent=j.span?`· ${j.span}`:'';
    const rows=(j.quarters||[]).map(q=>`<tr><td><b>${q.quarter}</b></td><td>${q.trades}</td><td>${q.win_pct==null?'—':q.win_pct+'%'}</td><td>${ret(q.avg_return)}</td><td>${b(q.best_market)}</td><td>${b(q.best_quality)}</td><td>${b(q.best_rr)}</td></tr>`).join('');
    box.innerHTML=`<div class="tablewrap"><table><thead><tr><th>Quarter</th><th>Trades</th><th>Win %</th><th>Avg return</th><th title="Market with the highest avg return that quarter">Best Market</th><th title="Quality band (10-wide) with the highest avg return">Best Quality</th><th title="R:R band with the highest avg return">Best R:R</th></tr></thead><tbody>${rows||'<tr><td colspan="7" class="empty">No data.</td></tr>'}</tbody></table></div>`+
      `<div class="muted" style="font-size:11px;margin-top:6px">Best = highest average return, min 3 trades per bucket (shown as value (avg return, n)). ${j.note||''} · generated ${j.generated||''}</div>`;
  }).catch(()=>{box.classList.remove("sqh-loading");box.innerHTML='<span class="muted" style="font-size:12px">Best-settings report unavailable.</span>';BEST_LOADED=false;});
}
let VSR_LOADED=false;
function renderVolScoreReport(){
  const box=$("volscore-report"); if(!box||VSR_LOADED)return;
  VSR_LOADED=true;
  const pct=v=>v==null?'—':v+'%', gbp=v=>v==null?'—':'£'+Math.round(v).toLocaleString();
  const ret=v=>v==null?'—':`<span style="color:${v>=0?'var(--bull)':'var(--bear)'}">${v>0?'+':''}${v}%</span>`;
  fetch("/api/volscore-report",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    box.classList.remove("sqh-loading");
    if(!j||j.error){box.innerHTML=`<span class="muted" style="font-size:12px">${(j&&j.error)||'VolumeScore report unavailable.'}</span>`;VSR_LOADED=false;return;}
    const seg=(t,ic,s)=>`<div class="fcard"><div class="ic">${ic}</div><h3>${t}</h3><div class="body"><b style="font-size:16px;color:var(--fg)">${s.avail||0}</b> trades · <b style="color:var(--bull)">${pct(s.win_pct)}</b> win · avg ${ret(s.avg_return)}</div></div>`;
    const comp=(t,c)=>c?`<div class="fcard"><div class="ic">£</div><h3>${t}</h3><div class="body"><b style="font-size:16px;color:${c.gain>=0?'var(--bull)':'var(--bear)'}">${gbp(c.final)}</b> from ${gbp(c.start)} <span class="muted">(${c.trades} trades)</span></div></div>`:'';
    const rowc=b=>`<tr><td><b>${b.bucket}</b></td><td>${b.resolved}</td><td>${pct(b.win_pct)}</td><td>${ret(b.avg_return)}</td><td>${b.pnl_per_10k==null?'—':gbp(b.pnl_per_10k)}</td></tr>`;
    box.innerHTML=
      `<div class="fgrid pf-cardstrip" style="margin-bottom:8px">${seg('All trades','∑',j.all)}${seg('VolumeScore ≥ '+j.threshold,'✓',j.passing)}${comp('All book',j.compound_all)}${comp('≥'+j.threshold+' book',j.compound_passing)}</div>`+
      `<div class="tablewrap"><table><thead><tr><th>VolumeScore band</th><th>Trades</th><th>Win %</th><th>Avg return</th><th title="£ P&L on a £10k wallet, one trade at 2% risk">£/10k per trade</th></tr></thead><tbody>${(j.buckets||[]).map(rowc).join('')||'<tr><td colspan="5" class="empty">No data.</td></tr>'}</tbody></table></div>`+
      ((j.advice||[]).length?`<ul class="muted" style="font-size:12px;margin:8px 0 0;padding-left:18px">${j.advice.map(a=>`<li>${a.replace(/</g,'&lt;')}</li>`).join('')}</ul>`:'')+
      `<div class="muted" style="font-size:11px;margin-top:6px">Same 12-month replay population as the tabs above · generated ${j.generated||''}</div>`;
  }).catch(()=>{box.classList.remove("sqh-loading");box.innerHTML='<span class="muted" style="font-size:12px">VolumeScore report unavailable.</span>';VSR_LOADED=false;});
}
// Trailing-stop illustration (user 2026-07-18): re-backtest every trade with a stop that trails by
// entry×gain×threshold on the close, and show the impact vs the plain result. ILLUSTRATION ONLY — the
// server never touches live IG stops.
function winnersSLChange(){
  const v=+(($("ordp-sl-in")||{}).value||0), box=$("ordp-sl"), busy=$("ordp-sl-busy");
  if(!(v>0)){if(box)box.innerHTML="";return;}
  if(busy){busy.className="sqh-loading";busy.textContent="⏳ Data loading…";}
  fetch("/api/winners-sl?sl="+v,{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    if(busy){busy.className="muted";busy.textContent="";}
    const rows=((j&&j.rows)||[]).filter(r=>r.perf!=null);
    if(!rows.length){if(box)box.innerHTML='<div class="muted" style="font-size:13px">No data to re-backtest.</div>';return;}
    const comp=key=>{let w=WINNERS_WALLET;for(const r of rows){const p=r[key];if(p!=null)w+=w*WINNERS_STAKE*p/100;}return w;};
    const plainFinal=comp("perf"), slFinal=comp("sl_perf"), delta=slFinal-plainFinal;
    const improved=rows.filter(r=>r.sl_perf!=null&&r.sl_perf>r.perf).length;
    const cut=rows.filter(r=>r.sl_perf!=null&&r.sl_perf<r.perf).length;
    const money=x=>`£${Math.round(x).toLocaleString()}`, col=delta>=0?'var(--bull)':'var(--bear)';
    box.innerHTML=`<div class="card" style="border:1px solid ${col};background:color-mix(in srgb,${col} 8%,transparent)">
      <h4 style="margin:0 0 6px">🎯 Trailing stop @ ${(j.threshold_pct||v)}% — illustration (live stops unaffected)</h4>
      <div class="muted" style="font-size:13px">£10,000 compounded ends at <b>${money(slFinal)}</b> <b>with</b> the trailing stop vs <b>${money(plainFinal)}</b> <b>without</b> —
      a <b style="color:${col}">${delta>=0?'+':'−'}${money(Math.abs(delta))}</b> difference over ${rows.length.toLocaleString()} trades.
      <b>${improved.toLocaleString()}</b> trades came out better (loss cut or gain locked in), <b>${cut.toLocaleString()}</b> were cut short of a bigger win.
      ${delta>=0?'Here the trailing stop would have helped.':'Here it would have hurt — it cuts winners short more than it saves losers.'}</div></div>`;
  }).catch(()=>{if(busy){busy.className="muted";busy.textContent="";}if(box)box.innerHTML='<div class="muted" style="font-size:13px">Re-backtest failed.</div>';});
}
// Let winners run (user 2026-08-01/02, ToDo P-08): re-backtest each trade with the target exit REMOVED — once
// a trade hits target the stop ratchets UP TO the target (so it can't give back below the target gain, e.g. 17%
// stays >=17%), then trails above it by the Run-trail %; before target it trails by the Stop-trail %. Per-user
// opt-in: hidden unless MY_LIMITS.let_winners_run is on; inputs default from the user's saved config.
// Pure verdict helper: returns improve/equal/worse from the two like-for-like wallet replays. Equality is
// deliberately NOT treated as an improvement (user 2026-08-12, P-10: require clear evidence it improves).
function _winnerRunComparison(plainReplay,runReplay,wallet){
  const start=Math.max(0,Number(wallet)||0), plainReturn=Number((plainReplay||{}).ret)||0,
        runReturn=Number((runReplay||{}).ret)||0, returnDelta=runReturn-plainReturn,
        tolerance=start>0?.005/start:1e-12;   // half a penny: never label a displayed £0.00 delta as better
  return {plainFinal:start*(1+plainReturn),runFinal:start*(1+runReturn),returnDelta,
    drawdownDelta:(Number((runReplay||{}).dd)||0)-(Number((plainReplay||{}).dd)||0),
    verdict:returnDelta>tolerance?"improved":returnDelta< -tolerance?"worse":"equal"};
}
function _winnerRunAttribution(plainReplay,runReplay,wallet){
  // Attribute the exact fixed-stake wallet delta. Shared row objects identify the identical eligible trade
  // in each proof: trades funded in both isolate exit-method impact; one-sided funding isolates capacity.
  const p=new Map(((plainReplay||{}).proof||[]).filter(x=>x.placed).map(x=>[x.r,x]));
  const q=new Map(((runReplay||{}).proof||[]).filter(x=>x.placed).map(x=>[x.r,x]));
  let commonCount=0,plainOnlyCount=0,runOnlyCount=0,commonExitDelta=0,capacityDelta=0;
  p.forEach((x,r)=>{const y=q.get(r);if(y){commonCount++;commonExitDelta+=wallet*((y.stake*(+r.run_perf||0))-(x.stake*(+r.perf||0)))/100;}
    else{plainOnlyCount++;capacityDelta-=wallet*x.stake*(+r.perf||0)/100;}});
  q.forEach((x,r)=>{if(!p.has(r)){runOnlyCount++;capacityDelta+=wallet*x.stake*(+r.run_perf||0)/100;}});
  const totalDelta=wallet*((Number((runReplay||{}).ret)||0)-(Number((plainReplay||{}).ret)||0));
  const reconciliation=totalDelta-commonExitDelta-capacityDelta;
  return {commonCount,plainOnlyCount,runOnlyCount,commonExitDelta,capacityDelta,totalDelta,reconciliation,
          reconciled:Math.abs(reconciliation)<.005};
}
// The card list is built by renderBestCombo, which may not have run yet and whose cards vary with the
// data (the >125/>250 bands only appear when the evidence supports them). Rebuild the options each time
// rather than wiring them once, and keep the current selection if it still exists.
function _syncRunScopeOptions(prefix){
  const sel=$(prefix+"-run-scope"); if(!sel)return;
  const labels=(BEST_CHOICES||[]).map(c=>c[0]);
  const want="All resolved trades|"+labels.join("|");
  if(sel.dataset.built===want)return;
  const keep=sel.value;
  sel.innerHTML='<option value="">All resolved trades</option>'
    +labels.map(l=>`<option value="${_esc(l)}">${_esc(l)}</option>`).join("");
  sel.value=labels.includes(keep)?keep:"";
  sel.dataset.built=want;
}
function winnersRunChange(surface='pf'){
  // The report is shown on TWO surfaces: the "What separates the winners" tab (#ordp-run, with its own
  // trail inputs) and the dedicated Performance sub-tab (#pf-run, editable what-if values seeded from config).
  const prefix=surface==='ordp'?'ordp':'pf', wrap=$(prefix+"-run-wrap"), box=$(prefix+"-run"), busy=$(prefix+"-run-busy");
  const paint=html=>{if(box)box.innerHTML=html;};
  // Keep the legacy admin card conditional on the live option, but the dedicated Performance report is
  // always available: its purpose is to test settings before enabling or saving them.
  if(prefix==='ordp'&&!(MY_LIMITS&&+MY_LIMITS.let_winners_run)){if(wrap)wrap.style.display="none";return;}
  if(wrap)wrap.style.display="";
  // Seed each surface from saved config once; subsequent edits remain unsaved what-if values.
  const inT=$(prefix+"-run-in"), inS=$(prefix+"-run-stop");
  if(inT&&!inT.dataset.seed){inT.value=(MY_LIMITS.let_winners_run_trail??4);inT.dataset.seed="1";}
  if(inS&&!inS.dataset.seed){inS.value=(MY_LIMITS.let_winners_run_stop??0);inS.dataset.seed="1";}
  const v=+((inT||{}).value||(MY_LIMITS.let_winners_run_trail??4)), sv=+((inS||{}).value||(MY_LIMITS.let_winners_run_stop??0));
  if(busy){busy.className="sqh-loading";busy.textContent="⏳ Data loading…";}
  const evidenceQuery=`&wallet=${encodeURIComponent(WINNERS_WALLET)}&position_pct=${encodeURIComponent(WINNERS_STAKE*100)}&max_open=${encodeURIComponent(WINNERS_MAXOPEN)}&min_trade=${encodeURIComponent(MIN_TRADE)}`;
  fetch("/api/winners-run?thr="+v+"&stop="+sv+evidenceQuery,{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    if(busy){busy.className="muted";busy.textContent="";}
    const rows=_dedupeSameDayRows(((j&&j.rows)||[]).filter(r=>r.perf!=null));
    if(!rows.length){paint('<div class="muted" style="font-size:13px">No data to re-backtest.</div>');return;}
    // One common eligible population, identical ordering and identical wallet/funding settings. The only
    // variable is the exit method (and therefore its exit date/capacity cost). Both proofs are retained so
    // a longer-running position that causes a later opportunity to be missed is visible, not averaged away.
    // RESOLVED trades only (user 2026-08-16). An unresolved position's baseline "return" is a
    // mark-to-market on money still at risk, not a banked result, so setting it against a simulated
    // realised exit is not a comparison -- it is two different quantities. It also breaks the invariant
    // the whole feature rests on: a trade that banked its target can never do worse by running (the stop
    // is floored at the target), but a position still open at +31% can perfectly well show a runner that
    // took +28% and read as a loss. That is where "how was this loss possible" came from on 600048.SS.
    const openRows=rows.filter(r=>r.run_perf!=null&&r.outcome==='OPEN').length;
    // Scope to a Best Settings recommendation's own population (user 2026-08-16: "we are not using that
    // many trades so we must think about what we test this theory on e.g. each of the 6 cards"). Testing
    // the exit theory across the whole tradeable universe answers a question nobody asked -- most of
    // those setups would never have been taken under any configuration you would actually run. Matched on
    // ticker+trigger date because the two datasets come from different endpoints and share no row object.
    _syncRunScopeOptions(prefix);
    const scopeSel=$(prefix+"-run-scope"), scopeLabel=(scopeSel||{}).value||"";
    const scoped=BEST_CHOICES.find(c=>c[0]===scopeLabel);
    const scopeKeys=scoped?new Set((scoped[1].seq||[]).map(r=>(r.ticker||'')+'|'+String(r.trig_date||'').slice(0,10))):null;
    const commonRows=rows.filter(r=>r.run_perf!=null&&r.outcome!=='OPEN'
        &&(!scopeKeys||scopeKeys.has((r.ticker||'')+'|'+String(r.trig_date||'').slice(0,10))))
      .sort((a,b)=>(a.trig_date||'').localeCompare(b.trig_date||'')||(a.ticker||'').localeCompare(b.ticker||''));
    if(!commonRows.length){paint('<div class="muted" style="font-size:13px">No resolved trades have both baseline and trailing-stop results.</div>');return;}
    const plainReplay=_combReplay(commonRows,WINNERS_STAKE,WINNERS_MAXOPEN,true,"perf",false);
    const runReplay=_combReplay(commonRows,WINNERS_STAKE,WINNERS_MAXOPEN,true,"run_perf",false);
    const comparison=_winnerRunComparison(plainReplay,runReplay,WINNERS_WALLET),
          attribution=_winnerRunAttribution(plainReplay,runReplay,WINNERS_WALLET),
          {plainFinal,runFinal,returnDelta,drawdownDelta,verdict}=comparison, delta=runFinal-plainFinal;
    const tgt=commonRows.filter(r=>r.outcome==='TARGET');
    const ran=tgt.filter(r=>r.run_perf>r.perf+0.01).length;
    // The invariant, stated by the user 2026-08-16: "the return should not be any lower than prior to the
    // change and the gains beyond the original target should add to the net gain". Now checked across
    // EVERY resolved trade, not just target-hitters -- a stopped trade must come out identical (nothing
    // trails below target) and a target-hitter can only improve. Any row that goes backwards is a fault
    // in the exit model and withholds the verdict.
    const back=commonRows.filter(r=>r.run_perf<r.perf-0.01).length;
    const improvedTrades=commonRows.filter(r=>r.run_perf>r.perf+0.01).length,
          worseTrades=commonRows.filter(r=>r.run_perf<r.perf-0.01).length,
          sameTrades=commonRows.length-improvedTrades-worseTrades;
    const serverEvidence=(j&&j.evidence)||null,
          serverMatches=!serverEvidence||(Math.abs(serverEvidence.baseline.end_wallet-plainFinal)<.005&&Math.abs(serverEvidence.runner.end_wallet-runFinal)<.005),
          targetLockValid=back===0&&(!serverEvidence||serverEvidence.target_lock.breaches===0),
          evidenceValid=targetLockValid&&attribution.reconciled&&serverMatches,
          money=x=>`£${Number(x||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`,
          signedMoney=x=>`${x>0?'+':x<0?'−':''}£${Math.abs(x).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`,
          pct=x=>`${x>0?'+':''}${(x*100).toFixed(2)}%`, pp=x=>`${x>0?'+':''}${(x*100).toFixed(2)} pp`,
          col=!evidenceValid?'var(--bear)':verdict==='improved'?'var(--bull)':verdict==='worse'?'var(--bear)':'#d29922', fixedStake=WINNERS_WALLET*WINNERS_STAKE,
          firstDate=String((commonRows[0]||{}).trig_date||'').slice(0,10),lastDate=String((commonRows[commonRows.length-1]||{}).trig_date||'').slice(0,10),
          verdictTitle=!evidenceValid?'Evidence invalid — integrity check failed':verdict==='improved'?'Improved historical return':verdict==='worse'?'Reduced historical return':'No historical return improvement',
          verdictText=!targetLockValid
            ?`${back.toLocaleString()} resolved trade(s) finished BELOW their original result. Letting a winner run must never return less than selling at target, so the portfolio verdict is withheld until that is restored.`
            :!attribution.reconciled
              ?`The exit/capacity attribution differs from the wallet result by ${signedMoney(attribution.reconciliation)}. The portfolio verdict is withheld until the replay reconciles.`
            :!serverMatches
              ?`The server and browser portfolio replays do not agree within half a penny. The verdict is withheld until they reconcile.`
            :verdict==='improved'
            ?`The trailing-stop replay finished ${signedMoney(delta)} ahead of selling at target.`
            :verdict==='worse'
              ?`The trailing-stop replay finished ${money(Math.abs(delta))} behind selling at target.`
              :`The trailing-stop replay produced the same return as selling at target; this is not evidence of improvement.`;
    const attributionText=`On ${attribution.commonCount.toLocaleString()} trades funded in both replays, changing only the exit produced ${signedMoney(attribution.commonExitDelta)}. Different exit dates changed which later trades could be funded (${attribution.plainOnlyCount.toLocaleString()} baseline-only; ${attribution.runOnlyCount.toLocaleString()} runner-only), producing ${signedMoney(attribution.capacityDelta)}. ${attribution.reconciled?`Together these reconcile to ${signedMoney(attribution.totalDelta)}.`:`These components do not yet reconcile to the observed ${signedMoney(attribution.totalDelta)} wallet difference.`}`;
    const big=tgt.filter(r=>r.run_perf>r.perf+0.01).sort((a,b)=>(b.run_perf-b.perf)-(a.run_perf-a.perf)).slice(0,5);
    const stopTxt=sv>0?` (stop trailing ${j.stop_pct||sv}% before target)`:``;
    paint(`<div class="card" style="border:1px solid ${col};background:color-mix(in srgb,${col} 8%,transparent)">
      <h4 style="margin:0 0 6px">🏃 Let winners run @ ${(j.threshold_pct||v)}% run-trail${stopTxt} — illustration (live orders unaffected)</h4>
      <div style="border-left:4px solid ${col};padding:8px 10px;margin:8px 0;background:color-mix(in srgb,${col} 10%,transparent)">
        <b style="color:${col}">Evidence verdict: ${verdictTitle}</b><div class="muted" style="font-size:12px;margin-top:3px">${verdictText} ${attributionText} Historical evidence only; it does not guarantee a future improvement.</div>
      </div>
      <div class="muted" style="font-size:12px;margin-bottom:8px"><b>Like-for-like basis:</b> ${scopeLabel?`the <b>${_esc(scopeLabel)}</b> recommendation's own population — `:``}the same <b>${commonRows.length.toLocaleString()}</b> RESOLVED trades (${firstDate||'—'} to ${lastDate||'—'}), <b>${money(WINNERS_WALLET)}</b> starting wallet, fixed <b>${money(fixedStake)}</b> stake (${(WINNERS_STAKE*100).toFixed(2)}%), Max open <b>${WINNERS_MAXOPEN}</b>, minimum-trade, leverage and margin rules. This comparison does not compound returns. Funded counts may differ because a trailing exit can keep capital occupied for longer; that capacity cost is part of the evidence.</div>
      <div class="tablewrap"><table><thead><tr><th>Evidence</th><th>Sell at target</th><th>Let winners run</th><th>Difference</th></tr></thead><tbody>
        <tr><td>End wallet</td><td><b>${money(plainFinal)}</b></td><td><b>${money(runFinal)}</b></td><td><b style="color:${col}">${signedMoney(delta)}</b></td></tr>
        <tr><td>Model return</td><td>${pct(plainReplay.ret)}</td><td>${pct(runReplay.ret)}</td><td><b style="color:${col}">${pp(returnDelta)}</b></td></tr>
        <tr><td>Maximum drawdown</td><td>${(plainReplay.dd*100).toFixed(2)}%</td><td>${(runReplay.dd*100).toFixed(2)}%</td><td><b style="color:${drawdownDelta<0?'var(--bull)':drawdownDelta>0?'var(--bear)':'var(--muted)'}">${pp(drawdownDelta)}</b></td></tr>
        <tr><td>Funded / eligible trades</td><td>${plainReplay.n.toLocaleString()} / ${commonRows.length.toLocaleString()}</td><td>${runReplay.n.toLocaleString()} / ${commonRows.length.toLocaleString()}</td><td>${runReplay.n-plainReplay.n>0?'+':''}${(runReplay.n-plainReplay.n).toLocaleString()} funded</td></tr>
        <tr><td>Missed through constraints</td><td>${(commonRows.length-plainReplay.n).toLocaleString()}</td><td>${(commonRows.length-runReplay.n).toLocaleString()}</td><td>${(plainReplay.n-runReplay.n)>0?'+':''}${(plainReplay.n-runReplay.n).toLocaleString()} missed</td></tr>
        <tr><td>Peak open positions</td><td>${plainReplay.cap}</td><td>${runReplay.cap}</td><td>${runReplay.cap-plainReplay.cap>0?'+':''}${runReplay.cap-plainReplay.cap}</td></tr>
        <tr><td>Exit-method impact · funded in both</td><td colspan="2">${attribution.commonCount.toLocaleString()} common funded trades</td><td><b style="color:${_pnlc(attribution.commonExitDelta)}">${signedMoney(attribution.commonExitDelta)}</b></td></tr>
        <tr><td>Capacity impact · funded in only one</td><td>${attribution.plainOnlyCount.toLocaleString()} baseline-only</td><td>${attribution.runOnlyCount.toLocaleString()} runner-only</td><td><b style="color:${_pnlc(attribution.capacityDelta)}">${signedMoney(attribution.capacityDelta)}</b></td></tr>
        <tr><td>Never worse than selling at target</td><td>${commonRows.length.toLocaleString()} resolved trades</td><td>${(commonRows.length-back).toLocaleString()} at or above their original result</td><td><b style="color:${targetLockValid?'var(--bull)':'var(--bear)'}">${back.toLocaleString()} worse</b></td></tr>
        <tr><td>Unresolved positions set aside</td><td colspan="2">Still open, so their baseline is a mark-to-market rather than a banked return -- not comparable with a simulated exit</td><td><b>${openRows.toLocaleString()} excluded</b></td></tr>
        <tr><td>Attribution reconciliation</td><td colspan="2">End-wallet difference minus exit and capacity impacts</td><td><b style="color:${attribution.reconciled?'var(--bull)':'var(--bear)'}">${signedMoney(attribution.reconciliation)} unexplained</b></td></tr>
        <tr><td>Independent replay cross-check</td><td colspan="2">Supabase/server evidence vs browser calculation</td><td><b style="color:${serverMatches?'var(--bull)':'var(--bear)'}">${serverMatches?'Matched within £0.005':'Mismatch — verdict withheld'}</b></td></tr>
      </tbody></table></div>
      <div class="muted" style="font-size:12px;margin-top:8px">Across all eligible trades, the trailing result was better on <b>${improvedTrades.toLocaleString()}</b>, equal on <b>${sameTrades.toLocaleString()}</b>, and worse on <b>${worseTrades.toLocaleString()}</b>. Of <b>${tgt.length.toLocaleString()}</b> normal target hits, <b>${ran.toLocaleString()}</b> ran further and <b>${back.toLocaleString()}</b> finished below the normal target result.</div>
      ${big.length?`<div class="muted" style="font-size:12px;margin-top:6px"><b>Largest per-trade improvements (not the portfolio verdict):</b> ${big.map(r=>`${_esc(r.name||disp(r.ticker))} ${r.perf>0?'+':''}${r.perf}%→${r.run_perf>0?'+':''}${r.run_perf}%`).join(' · ')}</div>`:''}
      <div id="${prefix}-baseline-proof"></div>
      <div id="${prefix}-run-proof"></div>
    </div>`);
    renderDecisionProof(prefix+'-baseline-proof',plainReplay.proof,{run:false,evidenceTitle:'Baseline transaction evidence — sell at target',evidenceNote:'Baseline funding decisions using the normal exit dates. Compare this with the trailing-stop evidence below.'});
    renderDecisionProof(prefix+'-run-proof',runReplay.proof,{run:true,evidenceTitle:'Trailing-stop transaction evidence — let winners run',evidenceNote:'The identical eligible population and funding rules, with trailing exit dates. Sell-at-target and trailing returns are shown side by side for every decision.'});
  }).catch(()=>{if(busy){busy.className="muted";busy.textContent="";}paint('<div class="muted" style="font-size:13px">Re-backtest failed.</div>');});
}
// Compact market-cap format (user 2026-08-01): 1.23T / 340B / 12M. No currency symbol (values are in each
// instrument's own currency). Blank when absent — mcap is not captured in the pipeline yet, so this reads
// "—" until the market-cap backfill lands (normalised for pence-quoted markets).
// Market cap. The £ is new on 2026-09-04 and is only honest BECAUSE of that day's change: the server used
// to hand these over in the instrument's own currency, so any symbol here would have been wrong for four
// rows in five. _mcap_map now converts every value to GBP (user: "MCAP is expected to be in GBP in our
// system"), so the column can finally say which currency it is quoting.
function _mcapFmt(v){if(v==null||!isFinite(+v))return'—';v=+v;const a=Math.abs(v);
  if(a>=1e12)return'£'+(v/1e12).toFixed(2)+'T';if(a>=1e9)return'£'+(v/1e9).toFixed(1)+'B';
  if(a>=1e6)return'£'+(v/1e6).toFixed(0)+'M';return'£'+Math.round(v).toLocaleString();}
// Colour-coded tick/cross for boolean columns (2026-08-07, ChangeRequest P-09 — Back Test's VWAP/ATR ticks
// were plain uncoloured text; every other BULL/BEAR-style indicator on the site is green/red).
// Cherry-pick presets (user 2026-07-18): set the RVOL / Quality / R:R thresholds the 15-month analysis
// says separate the best setups. Moved from the Scanner to Squeeze History on 2026-08-16 with the filters
// they drive — the presets ARE range filters, so they belong wherever the ranges live, and they are more
// useful against finished trades than against a live list. A preset field not in the preset is cleared.
function setCherry(id){
  const p=CHERRY_PRESETS.find(x=>x.id===id); if(!p)return;
  const set=(el,v)=>{const n=$(el);if(n)n.value=v??'';};
  set("sqfr_rvmin",p.f.rvmin); set("sqfr_qmin",p.f.qmin); set("sqfr_rrmin",p.f.rrmin);
  document.querySelectorAll('#sqh-presets .pill').forEach(b=>b.classList.toggle('active',b.dataset.p===id));
  paintSqueezeHist();
}
function paintScannerPresets(){
  const n=$("sqh-presets"); if(!n)return;
  n.innerHTML=CHERRY_PRESETS.map(p=>`<button class="pill" data-p="${p.id}" onclick="setCherry('${p.id}')" style="font-size:11px;padding:4px 9px">${p.label}</button>`).join("");
}
paintScannerPresets();   // render now — CHERRY_PRESETS is defined above (user 2026-07-18)
const _pnlc=v=>(v||0)>0?'var(--bull)':(v||0)<0?'var(--bear)':'var(--muted)';
const _gbp2=v=>v==null?'—':`${v<0?'−':'+'}£${Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
// Best-combination optimiser (user 2026-07-26, P-05 L132/L133) — grid-searches Max position size %, Max
// open positions and R:R floor over the winners trades and recommends the mix with the best RISK-ADJUSTED
// return: total compounded return % ÷ worst peak-to-trough drawdown %. Ranking on the ratio (not raw
// return) stops it railing to the biggest stake, which always compounds hardest on a positive edge but
// courts ruin. The optimum is wallet-independent under %-of-wallet compounding, so the £1k…£10k examples
// apply the SAME combo and just scale the £ outcome, flagging any wallet whose stake is below the £25 IG
// minimum. Uses startWallet=1 so ret/dd come out as fractions.
// Canonical "when does this trade release its slot" rule, shared by EVERY wallet replay on the page
// (user 2026-08-14, P-03 "apply configuration ... PROOF BACK TEST DOES NOT SHOW THE RESULTS EXPECTED").
// The replays disagreed on unresolved trades: Back Test's own ledger held them to the window end, while
// _combReplay fell back to _pfAddDays(trig_date, days_open||0) - and /api/winners + /api/winners-run rows
// carry no days_open - so every still-open trade settled on its own TRIGGER day, instantly releasing its
// capital and booking its mark-to-market gain. Best Settings therefore recommended configurations Back
// Test could not reproduce. One rule now: a closed trade frees its slot on its close date, an unresolved
// one never frees inside the window. The runner arm only takes run_exit_date when the RUNNER itself
// closed, so "let winners run" compares two like-for-like arms instead of a baseline that settles for
// free (user 2026-08-14, P-05 "should not see 33.52% up go to -3.58%").
function _pfExitDate(r,runner){
  r=r||{};
  const state=String((runner?r.run_outcome:null)||r.state||r.outcome||"");
  if(state==="OPEN")return "9999-99-99";                     // unresolved: still holding its slot
  const closed=(runner&&r.run_exit_date)||r.exit_date;
  if(closed)return String(closed).slice(0,10);
  // Same derivation Back Test's "Closed" column uses for a resolved row that lost its outcome_date.
  if(r.trig_date&&r.days_open!=null&&(state==="TARGET"||state==="STOPPED"))return _pfAddDays(r.trig_date,r.days_open);
  return "9999-99-99";
}
// The replay itself MOVED to hvf_web/best_settings.js (2026-09-03) so the server can execute the same
// code under Node. It used to read MIN_TRADE / WINNERS_WALLET / levOf / _pfExitDate as globals; those
// four are passed in here as live getters, so changing the Wallet box still changes the replay exactly
// as before. Everything that called _combReplay still calls _combReplay.
const _combReplay=makeCombReplay({wallet:()=>WINNERS_WALLET,minTrade:()=>MIN_TRADE,
  leverage:r=>levOf(r),exitDate:(r,runner)=>_pfExitDate(r,runner)});
const DECISION_PROOFS={};
function decisionProofFilter(id,dim,value){
  const s=DECISION_PROOFS[id];if(!s)return;const filters={...(s.opts.filters||{})};
  if(filters[dim]===value)delete filters[dim];else filters[dim]=value;
  renderDecisionProof(id,s.proof,{...s.opts,filters});
}
// The evidence slicers start CLOSED so the transactions are what you see first (user 2026-08-30).
let DECISION_SLICERS_OPEN=false;
function decisionSlicersToggle(id){DECISION_SLICERS_OPEN=!DECISION_SLICERS_OPEN;
  const d=DECISION_PROOFS[id]; if(d)renderDecisionProof(id,d.proof,d.opts);}
function renderDecisionProof(id,proof,opts={}){
  const target=$(id);if(!target)return;
  if(LIMITED){target.style.display="none";target.innerHTML="";return;}
  target.style.display="";DECISION_PROOFS[id]={proof,opts};
  // `missed` is deliberately NOT computed here any more; see below, next to `filtered`.
  const show=!!opts.showMissed, run=opts.run, filters=opts.filters||{};
  const evidenceTitle=opts.evidenceTitle||"Transaction evidence",
        evidenceNote=opts.evidenceNote||"Chronological decisions prove which opportunities the settings selected and whether the wallet could fund them. Positive missed rows expose profit the proposed setup could not actually capture.";
  const pct=v=>v==null?'—':`${+v>=0?'+':''}${(+v).toFixed(2)}%`, num=v=>v==null?'—':(+v).toFixed(1);
  const gbp=v=>'£'+Math.round(v).toLocaleString();   // proof rows carry fractional wallet/stake (×WINNERS_WALLET); real £ for Wallet/Net gain columns (2026-08-07)
  // RVOL bands match the canonical rvolCell() thresholds used everywhere else (2026-08-07, ChangeRequest
  // P-04): ≥2 = real participation spike, ≥1 = above normal, <1 = thin. VWAP/ATR get readable labels
  // instead of raw true/false/Unknown.
  const bucket=(r,d)=>d==='quality'?(r.quality==null?'Unknown':r.quality>=75?'75+':r.quality>=50?'50–74':'<50')
    :d==='rvol'?(r.rvol==null?'Unknown':r.rvol>=2?'RVOL 2+':r.rvol>=1?'RVOL 1–2':'RVOL <1')
    :d==='above_vwap'?(r.above_vwap==null?'Unknown':r.above_vwap?'Above VWAP':'Below VWAP')
    :d==='atr_expanding'?(r.atr_expanding==null?'Unknown':r.atr_expanding?'ATR expanding':'ATR not expanding')
    // Back Test's own chart strip, added here too (2026-08-07, ChangeRequest P-07 — "all the charts used in
    // Back Test... location through to month"): Location/Direction/Outcome/Win-Loss/Days-open/Month, using
    // the same field names and bands Back Test itself uses (locName, _doBand, PF_BE win/loss split).
    :d==='location'?(locName(r.location)||'Unknown')
    :d==='wl'?(v=>v==null||!isFinite(v)?'Unknown':v>PF_BE?'Win':v<-PF_BE?'Loss':'Break-even')(+r[run?'run_perf':'perf'])
    :d==='days_open'?_doBand(r)
    :d==='month'?((r.trig_date||'').slice(0,7)||'Unknown')
    :String(r[d]||'Unknown');
  const filtered=proof.filter(x=>Object.entries(filters).every(([d,v])=>bucket(x.r,d)===v));
  // Counts describe the rows ON SCREEN, so they are taken from `filtered`, not from `proof`.
  // `missed` used to come from the unfiltered proof while the table rendered `filtered`, so clicking any
  // chart left the header quoting a population the table was no longer showing (user 2026-08-30: "50
  // rows in evidence, told 50 at the top, and the card said 17 missed"). Same defect as the "All
  // markets" card label: a number that is right about a set nobody can see.
  const missed=filtered.filter(x=>!x.placed).length, placed=filtered.length-missed,
        narrowed=filtered.length!==proof.length;
  // Reconcile the card's compounded return to the evidence: each row's stake is the wallet at its
  // actual decision, so summing funded row P&L equals the final wallet change.  When cross-filtered,
  // never label the visible subset as the complete card return.
  const netGain=xs=>xs.filter(x=>x.placed).reduce((sum,x)=>sum+x.stake*WINNERS_WALLET*(+x.r[run?'run_perf':'perf']||0)/100,0),
        visibleNetGain=netGain(filtered), totalNetGain=netGain(proof),
        netGainLabel=Object.keys(filters).length?"Visible Net gain subtotal":"Net gain total";
  // The slicers use the SAME barChart the Scanner and Back Test strips use (user 2026-08-30: "the
  // slicers above the table are so ugly - either make them look good or remove them"). They were twelve
  // hand-rolled cards of bespoke markup -- a 75px/bar/54px grid with ellipsised labels, no hover state
  // and flex:1 1 auto, so twelve of them fought over one row and got crushed. barChart already had
  // profit mode, the 8-bar cap and the clear badge; it only lacked a way to hook a click to something
  // other than the shared data-fk dispatcher, which this panel does not use. That is now an option on
  // barChart rather than a second bar renderer living here.
  const chart=(dim,title)=>{
    const grouped={};
    proof.filter(x=>x.placed).forEach(x=>{const key=bucket(x.r,dim),ret=+(x.r[run?'run_perf':'perf']||0);
      grouped[key]=(grouped[key]||0)+x.stake*ret/100*WINNERS_WALLET;});
    const q=v=>String(v).replace(/&/g,'&amp;').replace(/'/g,"\'").replace(/"/g,'&quot;');
    return `<div class="vizsector">`+barChart(title,grouped,`dp_${dim}`,null,false,{
      profit:true, currency:'£',
      selectedValue:filters[dim]||"",
      onclickFor:key=>`decisionProofFilter('${q(id)}','${q(dim)}','${q(key)}')`,
      clearOnclick:`decisionProofFilter('${q(id)}','${q(dim)}','')`
    })+`</div>`;};
  const rows=filtered.filter(x=>show||x.placed).map(x=>{const r=x.r, good=+(r[run?'run_perf':'perf'])>0;
    return `<tr style="${x.placed?'':good?'background:color-mix(in srgb,#d29922 12%,transparent)':'opacity:.7'}">
      <td>${_esc(String(r.trig_date||'').replace('T',' ').slice(0,16))}</td><td>${_esc(r.name||disp(r.ticker))}</td>
      <td>${x.placed?'<b style="color:var(--bull)">Placed</b>':`<b style="color:#d29922">Missed</b><div class="muted" style="font-size:10px">${_esc(x.reason)}</div>`}</td>
      <td>${x.open}</td><td>${gbp(x.w*WINNERS_WALLET)}</td><td>${pct(r.perf)}</td>${run?`<td><b>${pct(r.run_perf)}</b></td><td>${(r.perf!=null&&r.run_perf!=null)?`<b style="color:${(r.run_perf-r.perf)>=0?'var(--bull)':'var(--bear)'}">${pct(r.run_perf-r.perf)}</b>`:'—'}</td>`:''}
      <td>${x.placed?(()=>{const g=x.stake*WINNERS_WALLET*(+r[run?'run_perf':'perf']||0)/100;return `<b style="color:${g>=0?'var(--bull)':'var(--bear)'}">${g>=0?'+':'−'}${gbp(Math.abs(g))}</b>`;})():'—'}</td>
      <td>${num(r.rr)}</td><td>${num(r.quality)}</td><td>${num(r.volume_score)}</td><td>${num(r.rvol)}</td>
      <td>${r.above_vwap==null?'—':r.above_vwap?'Above':'Below'}</td><td>${r.atr_expanding==null?'—':r.atr_expanding?'Expanding':'Not expanding'}</td>
      <td>${_esc(r.market||'—')}</td><td>${_esc(r.sector||'—')}</td><td>${_esc(r.ticker||'—')}</td></tr>`;}).join('');
  // Decision and capacity explain why a row was taken or missed; keep that repeat evidence at the far
  // right so the trading outcome and signal fields remain readable without horizontal scrolling first.
  queueMicrotask(()=>{const t=target.querySelector('table');if(!t)return;[...t.querySelectorAll('thead tr,tbody tr')].forEach(row=>{const cells=[...row.children];if(cells.length>=4){row.append(cells[2],cells[3]);}});});
  target.innerHTML=`<div style="margin-top:14px"><button class="subpill" onclick="decisionSlicersToggle('${id}')" title="Cross-filter the transactions below by location, market, sector and the rest">${DECISION_SLICERS_OPEN?'▾':'▸'} Filter transactions</button><div class="viz" style="margin-top:8px;padding:0;border-bottom:none"${DECISION_SLICERS_OPEN?'':' hidden'}><div class="muted" style="font-size:11px;margin:0 0 6px;width:100%">Bars show <b style="color:var(--fg)">achievable P&amp;L</b> for the transactions actually placed. Click a bar to cross-filter the table below.</div>${chart('location','Location')}${chart('market','Market')}${chart('sector','Sector')}${chart('direction','Direction')}${chart('outcome','Outcome')}${chart('wl','Win / Loss')}${chart('days_open','Days Open')}${chart('month','Month')}${chart('quality','Quality')}${chart('rvol','RVOL')}${chart('above_vwap','VWAP')}${chart('atr_expanding','ATR')}</div></div>
    ${Object.keys(filters).length?`<div class="muted" style="font-size:11px;margin-top:6px">Cross-filter: ${Object.entries(filters).map(([d,v])=>`${d} = ${_esc(v)}`).join(' · ')} <button class="btn" style="padding:2px 7px" onclick="renderDecisionProof('${id}',DECISION_PROOFS['${id}'].proof,{...DECISION_PROOFS['${id}'].opts,filters:{}})">Clear</button></div>`:''}
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:end;margin:14px 0 6px;flex-wrap:wrap">
    <!-- flex:1 1 320px + min-width:0 so the long evidenceNote wraps its own text rather than making
         this block max-content wide and pushing the controls onto a line of their own. -->
    <div style="flex:1 1 320px;min-width:0"><h4 style="margin:0">${_esc(evidenceTitle)}</h4>
    <div style="font-size:12px;margin:3px 0 2px"><b>${filtered.length.toLocaleString()}</b> trigger${filtered.length===1?'':'s'} —
      <b style="color:var(--bull)">${placed.toLocaleString()} placed</b>, <b class="muted">${missed.toLocaleString()} missed</b>${narrowed?` <span class="muted">(narrowed from ${proof.length.toLocaleString()} by the chart filters)</span>`:''}</div>
    <div class="muted" style="font-size:11px">${_esc(evidenceNote)}</div></div>
    <!-- margin-left:auto, NOT justify-content on the parent. Under space-between a line holding a
         SINGLE flex item places it at flex-start, so when this block wrapped it rendered on the LEFT
         (user 2026-08-31: "hide / show is still on the LHS"). An auto left margin absorbs the free
         space on whatever line it lands on, so it is right-aligned either way. -->
    <div style="white-space:nowrap;text-align:right;margin-left:auto">
      <div style="font-size:11px;font-weight:600;margin-bottom:4px">Missed transactions (${missed})</div>
      <div style="display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden">
        <button type="button" onclick="renderDecisionProof('${id}',DECISION_PROOFS['${id}'].proof,{...DECISION_PROOFS['${id}'].opts,showMissed:false})"
          style="padding:5px 14px;font-size:12px;font-weight:600;border:0;cursor:pointer;${!show?'background:var(--accent);color:#fff':'background:transparent;color:var(--fg)'}">Hide</button>
        <button type="button" onclick="renderDecisionProof('${id}',DECISION_PROOFS['${id}'].proof,{...DECISION_PROOFS['${id}'].opts,showMissed:true})"
          style="padding:5px 14px;font-size:12px;font-weight:600;border:0;border-left:1px solid var(--line);cursor:pointer;${show?'background:var(--accent);color:#fff':'background:transparent;color:var(--fg)'}">Show</button>
      </div></div></div>
    <div class="tablewrap"><table><thead><tr><th>Triggered</th><th>Name</th><th>Decision / constraint</th><th>Open after decision</th><th title="Settled model wallet immediately before this decision; it excludes unrealised P&amp;L.">Settled wallet</th><th>Sell at target</th>${run?'<th>Let run</th><th>Difference</th>':''}<th title="£ profit/loss from this trade alone — blank for missed trades">Net gain</th><th>R:R</th><th>Quality</th><th>VolumeScore</th><th>RVOL</th><th>VWAP</th><th>ATR</th><th>Market</th><th>Sector</th><th>Ticker</th></tr></thead><tbody>${rows||`<tr><td colspan="${run?18:16}" class="muted">No transactions match this view.</td></tr>`}</tbody><tfoot><tr><td colspan="${run?6:4}" style="text-align:right"><b>${netGainLabel}</b></td><td><b style="color:${visibleNetGain>=0?'var(--bull)':'var(--bear)'}">${visibleNetGain>=0?'+':'−'}${gbp(Math.abs(visibleNetGain))}</b>${Object.keys(filters).length?`<div class="muted" style="font-size:10px">All funded: ${totalNetGain>=0?'+':'−'}${gbp(Math.abs(totalNetGain))}</div>`:''}</td><td colspan="${run?11:11}"></td></tr></tfoot></table></div>`;
}

let BEST_HISTORY_REQUESTED=false, BEST_HISTORY_POST_KEY="", BEST_HISTORY_ROWS=[];
const BEST_HISTORY_FIELDS=[
  ["scope","Scope"],["min_rr","R:R"],["min_quality","Quality"],
  ["min_volume_score","VolumeScore"],["min_rvol","RVOL"],
  ["require_above_vwap","VWAP required"],["require_atr_expanding","ATR expanding"],
  ["max_position_pct","Position size"],["max_open","Max open"],
];
function _bestHistoryValue(key,value){
  if(key==="require_above_vwap"||key==="require_atr_expanding")return value?"Yes":"No";
  if(key==="max_position_pct")return `${value}%`;
  if((key==="min_quality"||key==="min_volume_score"||key==="min_rvol")&&+value===0)return "Any";
  return String(value??"—");
}
function _bestHistoryPct(value){
  const v=+value||0;
  return v>=9?`×${(1+v).toLocaleString(undefined,{maximumFractionDigits:(1+v)>=100?0:1})}`:`${v>=0?'+':''}${(v*100).toFixed(1)}%`;
}
function _bestHistoryConfig(settings){
  return BEST_HISTORY_FIELDS.map(([key,label])=>`${label}: ${_bestHistoryValue(key,(settings||{})[key])}`).join(" · ");
}
// Identifies the replay maths a snapshot was produced by. Until 2026-08-15 an unresolved trade
// settled on its own trigger day, so returns were inflated and funded counts unachievable; every
// snapshot recorded before then is a record of numbers that were wrong. Bump this whenever a change
// moves the figures, so history stays comparable only within a model (user 2026-08-15: "best settings
// history of little relevance if the calculations during that period were wrong").
const BEST_CALC_MODEL="2026-08-15-exit-rule";
const _bestCalcModel=row=>String((row&&row.model||{}).calc_model||"pre-2026-08-15");
function _bestHistoryChanges(current,previous){
  if(!previous)return "Baseline snapshot";
  // Comparing across a change in the replay maths would report a methodology correction as though the
  // strategy had deteriorated - the corrected model returns far less than the inflated one on identical
  // data. Refuse the comparison rather than draw a misleading delta.
  if(_bestCalcModel(current)!==_bestCalcModel(previous))
    return `Not comparable - calculation model changed (${_bestCalcModel(previous)} → ${_bestCalcModel(current)}). `+
           `Earlier returns were inflated by open positions releasing their capital on the trigger day.`;
  const changes=[];
  const cm=current.model||{},pm=previous.model||{};
  [["wallet","Model wallet","£",""],["minimum_trade","Minimum trade","£",""],["position_pct","Model position size","","%"],["max_open","Model max open","",""]].forEach(([key,label,prefix,suffix])=>{
    if(String(cm[key])!==String(pm[key]))changes.push(`${label}: ${prefix}${pm[key]}${suffix} → ${prefix}${cm[key]}${suffix}`);
  });
  const old=Object.fromEntries((previous.options||[]).map(o=>[o.label,o]));
  (current.options||[]).forEach(option=>{
    const prior=old[option.label];
    if(!prior){changes.push(`${option.label}: added`);return;}
    const diffs=BEST_HISTORY_FIELDS.filter(([key])=>String((option.settings||{})[key])!==String((prior.settings||{})[key]))
      .map(([key,label])=>`${label} ${_bestHistoryValue(key,prior.settings[key])} → ${_bestHistoryValue(key,option.settings[key])}`);
    if(diffs.length)changes.push(`${option.label}: ${diffs.join(", ")}`);
    delete old[option.label];
  });
  Object.keys(old).forEach(label=>changes.push(`${label}: removed`));
  return changes.join(" · ")||"No settings change";
}
function paintBestSettingsHistory(history){
  const box=$("best-settings-history"),count=$("best-history-count");if(!box)return;
  if(Array.isArray(history))BEST_HISTORY_ROWS=history;
  const all=BEST_HISTORY_ROWS||[],query=((($("best-history-search")||{}).value)||"").trim().toLowerCase();
  const rows=query?all.filter((row,index)=>_bestHistoryChanges(row,all[index+1]).toLowerCase().includes(query)):all;
  box.classList.remove("sqh-loading");if(count)count.textContent=query?`${rows.length} shown of ${all.length} daily snapshots`:`${all.length} daily snapshot${all.length===1?'':'s'}`;
  if(!all.length){box.innerHTML='<span class="muted">No history yet. Today’s recommendation will be recorded when calculation completes.</span>';return;}
  if(!rows.length){box.innerHTML='<span class="muted">No settings changes match that search.</span>';return;}
  const body=rows.map((row,index)=>{
    const balanced=(row.options||[]).find(o=>o.label==="Balanced"),result=(balanced||{}).results||{};
    const full=(row.options||[]).map(o=>`<div><b>${_esc(o.label)}</b>: ${_esc(_bestHistoryConfig(o.settings))}</div>`).join("");
    return `<tr><td><b>${_esc(row.snapshot_day||"")}</b><div class="muted">${_esc(String(row.recorded_at||"").slice(11,19))} UTC</div></td>`+
      `<td>${row.data_through?`through <b>${_esc(row.data_through)}</b>`:'—'}<div class="muted">built ${_esc(row.dataset_generated||'—')}</div></td>`+
      `<td>£${(+((row.model||{}).wallet)||0).toLocaleString()} · ${_bestHistoryValue('position_pct',(row.model||{}).position_pct)} · ${(row.model||{}).max_open} max</td>`+
      `<td>${balanced?`<b style="color:${result.annual_return>=0?'var(--bull)':'var(--bear)'}">${_bestHistoryPct(result.annual_return)}</b><div class="muted">${((+result.max_drawdown||0)*100).toFixed(1)}% drawdown · ${result.funded_trades} funded</div>`:'—'}</td>`+
      `<td class="best-history-change">${_esc(_bestHistoryChanges(row,all[all.indexOf(row)+1]))}</td>`+
      `<td class="best-history-config"><details><summary>Full settings</summary>${full}</details></td></tr>`;
  }).join("");
  box.innerHTML=`<div class="tablewrap"><table><thead><tr><th>Snapshot</th><th>Dataset</th><th>Model</th><th>Balanced result</th><th>Changes from previous snapshot</th><th>Configuration</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
function loadBestSettingsHistory(){
  if(BEST_HISTORY_REQUESTED)return;
  // Logged out this used to `return` silently, which left index.html's STATIC "⏳ Data loading…"
  // placeholder on screen for ever — the spinner could never resolve, because nothing was ever going to
  // replace it (user 2026-08-28: "Best settings history … NEVER COMPLETES - when user not logged in").
  // Same shape as the /api/rules defect: a refusal that impersonates work still in progress. A guard
  // that suppresses a fetch MUST also resolve the loading state it leaves behind. The sibling pf-combos
  // panel already does exactly this, so match it rather than inventing a second treatment.
  if(!AUTH){
    const box=$("best-settings-history");
    if(box){box.classList.remove("sqh-loading");
      box.innerHTML='<p class="muted" style="margin:0">🔒 <a href="#" onclick="showLogin();return false">Log in</a> to see the Best settings history.</p>';}
    const count=$("best-history-count"); if(count)count.textContent="";
    return;
  }
  BEST_HISTORY_REQUESTED=true;
  fetch("/api/best-settings-history",{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>paintBestSettingsHistory(j.history||[])).catch(()=>{const box=$("best-settings-history");if(box){box.classList.remove("sqh-loading");box.textContent="Best settings history could not be loaded.";}});
}
function recordBestSettingsSnapshot(snapshot){
  if(!AUTH)return;const key=JSON.stringify(snapshot);if(key===BEST_HISTORY_POST_KEY)return;BEST_HISTORY_POST_KEY=key;
  fetch("/api/best-settings-history",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:key})
    .then(r=>{if(!r.ok)throw 0;return r.json();}).then(j=>paintBestSettingsHistory(j.history||[]))
    .catch(()=>{BEST_HISTORY_POST_KEY="";const box=$("best-settings-history");if(box){box.classList.remove("sqh-loading");box.textContent="Best settings history could not be saved.";}});
}
// Return the strongest risk-adjusted option whose replay funded STRICTLY more than the requested number
// of trades, and no more than `max` (omit / 0 = no ceiling). Kept as a small pure helper so the
// >125-150 / >250-300 card boundaries and ranking are executable in the regression suite rather than
// only asserted as source text (user 2026-08-12, P-10; banded 2026-08-14, P-04).
// _bestSettingsByFundedTrades MOVED to hvf_web/best_settings.js (2026-09-03) with the rest of the
// search, so the browser and the Node precompute rank the funded-trade bands identically.
let _bestDecisionRows=null, _bestCardCapacity=null, _bestSettingsResizeTimer=null;
// A compact iPad-mini decision surface can show nine cards; phones stop at six.  A laptop may show up
// to eight, but only if the actual grid can keep them within two rows (trimmed after layout below).
//
// The tablet band runs to 1024px, not 850 (user 2026-08-23: "ipad mini is meant to show 9 cards, not 8").
// An iPad mini is 768 wide in PORTRAIT but 1024 in LANDSCAPE, so at 850 the landscape orientation fell
// into the laptop band and showed eight. One constant, used by BOTH the count and the row cap below:
// they were separate literals, which is how the two halves of this rule could disagree in the first place.
const BEST_TABLET_MAX=1024;
const bestCardCapacity=()=>innerWidth<=600?6:(innerWidth<=BEST_TABLET_MAX?9:8);
// Rows the grid may occupy. On a LAPTOP the row cap is the real constraint: eight cards are only worth
// showing if they fit two rows, so the post-layout loop trims to that.
//
// On a phone or tablet the CARD COUNT is the constraint and rows must follow it (user 2026-08-28: "iPad
// mini not seeing 9 cards (typically 6,7, or 8)"). The two rules previously disagreed: bestCardCapacity
// allowed nine while this capped the grid at three rows, and nine cards at min-width 240px need FOUR rows
// at an iPad mini's 768px, so the trimming loop deleted cards until they fitted -- silently overruling
// the count and landing on the six-to-eight actually seen. A tablet scrolls; a fourth row costs nothing.
const bestCardMaxRows=()=>innerWidth>BEST_TABLET_MAX?2:Infinity;
// The SEARCH now lives in hvf_web/best_settings.js so the server can run the identical code under Node
// and precompute the summaries a logged-out visitor is allowed to see. What stays here is everything
// that needs the page: the user's own configuration, the Apply control, the three-year status card and
// the post-layout trimming. See best_settings.js for why the split exists.
function renderBestCombo(all,{recordSnapshot=true}={}){
  _bestDecisionRows=all;
  _bestCardCapacity=bestCardCapacity();
  const box=$("ordp-bestcombo"); if(!box)return;
  // Logged out, the cards arrive from the server as SUMMARIES and are rendered by the same template.
  // They cannot be computed here: computing them needs the per-trade rows, and those are exactly what a
  // logged-out visitor must not receive (user 2026-09-01, restated 2026-09-03 — "logged out users should
  // see cards BUT NOT THE UNDERLYING EVIDENCE TABLE").
  if(LIMITED){renderPublicBestCombo();return;}
  // Derived from the ROWS actually replayed and from tradeVisible itself, not from a second reading of
  // MARKETS_OFF/MARKETS_DISABLED. A market excluded for any reason tradeVisible knows about is reported,
  // including rules added later.
  const marketsOff=tradeExcludedValues("market",(WIN||[]).concat(WIN_3Y||[]).map(r=>r&&r.market));
  const res=computeBestSettings({rows:all||[],rows3y:WIN_3Y,wallet:WINNERS_WALLET,minTrade:MIN_TRADE,
    stake:WINNERS_STAKE,maxOpen:WINNERS_MAXOPEN,replay:_combReplay,marketsOff,memo:_3Y_MEMO});
  _3Y_MEMO=res.memo;
  if(res.insufficient){
    box.innerHTML=res.eligibleRows<10
      ?'<div class="muted" style="font-size:13px">Not enough usable trades to recommend settings (minimum 10).</div>':"";
    return;
  }
  const {threeYear,bestThreeYear}=res;
  // Balanced is the card shown first, and the three-year card is the other one a user opens immediately.
  // Computing their funded-decision proof here keeps first paint of the detail panel exactly as it was
  // before the search moved out; every other card still builds its proof lazily in selectBestChoice.
  [["Balanced"],["Best over 3 years"]].forEach(([label])=>{
    const x=(res.choices.find(c=>c[0]===label)||[])[1];
    if(x&&!x.proof)x.proof=_combReplay(x.seq,x.st/100,x.mo,true).proof;});
  const w=Math.max(1,WINNERS_WALLET||1000);
  // Does this card already describe what the user is running? (user 2026-08-28: "If one of the card
  // configurations matches user configuration e.g. Capital Efficient - make it very clear".)
  const current={rr:Number(MY_LIMITS.min_risk_reward??3),q:Number(MY_LIMITS.min_quality??25),vs:Number(MY_LIMITS.min_volume_score??1),rv:+MY_LIMITS.min_rvol||0,
    vw:!!+MY_LIMITS.require_above_vwap,atr:!!+MY_LIMITS.require_atr_expanding,st:+MY_LIMITS.max_position_pct||WINNERS_STAKE*100,mo:+MY_LIMITS.max_open||WINNERS_MAXOPEN,
    scope:_pfSavedScope("market")[0]?`Market: ${_pfSavedScope("market").join(", ")}`:_pfSavedScope("sector")[0]?`Sector: ${_pfSavedScope("sector").join(", ")}`:"All markets"};
  const trimmed=trimBestSettingsCards(res.cards,res.unsupported,!threeYear,_bestCardCapacity);
  const shown=new Set(trimmed.cards.map(c=>c.label));
  const choices=res.choices.filter(c=>shown.has(c[0]));
  const choiceCards=trimmed.cards.map(c=>bestSettingsCardHTML(c,{current,selected:BEST_SELECTED,
    onSelect:"selectBestChoice('LABEL')",apply:_bestApplyRow(c.cfg,bestSettingsMatchesCurrent(c,current))})).join('');
  const unsupportedCards=trimmed.unsupported.map(bestSettingsUnsupportedCardHTML).join('');
  // A three-year option is a recommendation only when it meets its stated evidence threshold. If it does
  // not, omit it rather than occupying a trader-facing card with a non-actionable rejection message.
  const threeYearInfo=WIN_3Y===null
    ?`<div class="muted" style="margin:0 0 10px;font-size:12px">Three-year evidence is loading separately; it is not included in the card list until verified.</div>`
    :!threeYear?`<div class="muted" style="margin:0 0 10px;padding:8px 10px;border-left:3px solid #d29922;background:color-mix(in srgb,#d29922 9%,transparent);font-size:12px"><b>Three-year evidence:</b> no supported recommendation. The strongest reviewed configuration funded <b>${bestThreeYear?bestThreeYear.n.toLocaleString():"0"}</b> trades; the evidence rule requires <b>more than 125</b> and at least 80% of the best annual card’s return.${bestThreeYear?` Its replayed return was <b>${_bsPct(bestThreeYear.ret)}</b>.`:""}</div>`
    :"";
  const threeYearStatusCard=!threeYear?_bestThreeYearStatusCard({
    loaded:WIN_3Y!==null,error:WIN_3Y_ERROR,
    best:bestThreeYear?{ret:bestThreeYear.ret,dd:bestThreeYear.dd,n:bestThreeYear.n,
      settings:{rr:bestThreeYear.rr,q:bestThreeYear.q,vs:bestThreeYear.vs,rv:bestThreeYear.rv,vw:!!bestThreeYear.vw,
        atr:!!bestThreeYear.atr,st:bestThreeYear.st,mo:bestThreeYear.mo,scope:bestThreeYear.scope.label},
      cfg:res.threeYearCard?res.threeYearCard.cfg:null}:null,current}):"";
  box.innerHTML=`<div class="fgrid" style="margin:0 0 10px">${choiceCards}${unsupportedCards}${threeYearStatusCard}</div>
  <div class="card" style="margin:0" id="best-detail"></div>`;
  _bestGridPostLayout(box);
  BEST_CHOICES=choices; BEST_MODEL_W=w;
  // Detail card follows whichever choice is clicked (user 2026-08-07, ChangeRequest P-06); defaults to
  // Balanced on first render, and stays on the same choice across data refreshes where still present.
  selectBestChoice(choices.some(([l])=>l===BEST_SELECTED)?BEST_SELECTED:"Balanced");
  if(recordSnapshot)recordBestSettingsSnapshot({
    dataset_generated:WIN_GENERATED,
    data_through:res.dataThrough,
    model:{wallet:w,minimum_trade:MIN_TRADE,position_pct:WINNERS_STAKE*100,max_open:WINNERS_MAXOPEN,
           calc_model:BEST_CALC_MODEL},
    options:choices.map(([label,x])=>({label,
      settings:{scope:x.scope.label,min_rr:x.rr,min_quality:x.q,min_volume_score:x.vs,min_rvol:x.rv,
        require_above_vwap:!!x.vw,require_atr_expanding:!!x.atr,max_position_pct:x.st,max_open:x.mo},
      results:{annual_return:x.ret,max_drawdown:x.dd,funded_trades:x.n,eligible_trades:x.seq.length,
        positive_quarters:x.posPeriods,quarters:x.periods}})),
  });
}
// There is no User Configuration to write to when nobody is signed in, and /api/config rejects the POST,
// so the control could only ever end at "Save failed" (user 2026-08-22). Offer the sign-in that makes it
// work instead of an action that cannot.
function _bestApplyRow(cfg,matches,extra){
  if(!AUTH)return `<div class="fcard-apply"><span class="muted" style="font-size:11px">Log in to apply this configuration.</span></div>`;
  const args=extra?`,${JSON.stringify(extra)}`:"";
  return `<div class="fcard-apply"><button class="btn" onclick='event.stopPropagation();applyConfigFromReport(${JSON.stringify(cfg)},this${args})' title="${matches?'These values already match your User Configuration - applying would change nothing':'Review and apply these values to User Configuration'}">${matches?'Already applied':'Apply this configuration'}</button></div>`;
}
// The three-year card when its evidence rule is NOT met: information, not a recommendation. Kept in the
// page rather than in best_settings.js because it reports the LOAD state of WIN_3Y, which only exists in
// a browser. `state.best` is a plain summary, so the logged-out path renders the identical card.
function _bestThreeYearStatusCard(state){
  const b=state.best, matches=b?bestSettingsMatchesCurrent({...b.settings,scope:{label:b.settings.scope}},state.current):false;
  // The apply row is computed here so it can be emitted as a DIRECT child of .fcard rather than nested
  // inside .body. .fcard-apply carries margin-top:auto, which only pushes to the bottom for a flex item
  // of a column flex container: .fcard is one, .body is a plain block (index.html:311). Nested inside
  // .body it was inert, and this card - whose body differs from the choice cards - sat at a different
  // height (user 2026-08-30, reported twice).
  const apply=(state.loaded&&b&&b.cfg)?_bestApplyRow(b.cfg,matches,{belowEvidence:b.n}):"";
  return `<div class="fcard" data-choice-unavailable="Best over 3 years" style="min-width:240px;flex:1;border-top:3px solid #00a8a8;opacity:.9" title="Three-year evidence; shown for comparison, not as an applicable recommendation below the evidence threshold">
    <h3 style="color:#00a8a8">Best over 3 years</h3>${matches?`<div class="fcard-current" title="Every setting on this card already matches your saved User Configuration, so applying it would change nothing">✓ This is your current configuration</div>`:''}<div class="muted" style="font-size:11px;min-height:30px">${!state.loaded?(state.error?"Evidence could not be loaded":"Evidence loading separately…"):"Strong return; smaller trade sample."}</div>
    <div class="body">${!state.loaded?(state.error?`<span style="color:var(--bear)">Three-year evidence could not be loaded (${_esc(state.error)}).</span> <button class="subpill" onclick="event.stopPropagation();retryThreeYear()">Retry</button>`:"This card remains visible while the complete three-year evidence loads."):b?`<div><b style="font-size:17px;color:var(--bull)">${_bsPct(b.ret)}</b> return · <b>${(b.dd*100).toFixed(1)}%</b> max drawdown</div><div class="muted" style="font-size:11px;margin-top:5px"><b>${b.n.toLocaleString()}</b> funded trades. The high-confidence evidence rule is more than <b>125</b> funded trades; this is information, not a recommendation.</div>
    `:"No usable three-year evidence is currently available."}</div>
    ${apply}
  </div>`;
}
// Post-layout trimming. Shared by both render paths so the logged-out grid behaves identically.
function _bestGridPostLayout(box){
  // Defensive is intentionally last. If responsive wrapping leaves it alone on a final row, remove it
  // rather than spending an entire row on the least-useful alternative (user 2026-08-18).
  requestAnimationFrame(()=>{const grid=box.querySelector('.fgrid'),def=grid&&grid.querySelector('[data-choice="Defensive"]');if(!grid)return;if(def){const rowPeers=[...grid.children].filter(c=>c!==def&&c.offsetTop===def.offsetTop);if(!rowPeers.length){def.remove();BEST_CHOICES=BEST_CHOICES.filter(c=>c[0]!=="Defensive");if(BEST_SELECTED==="Defensive")selectBestChoice("Balanced");}}
    // Laptop: up to eight only when they physically fit in two rows.  Remove the weakest-return card
    // repeatedly until they do; a one-off trim could still leave a third or fourth row at narrower widths.
    const maxRows=bestCardMaxRows();while(new Set([...grid.children].map(c=>c.offsetTop)).size>maxRows){const weakest=[...grid.querySelectorAll('[data-choice-return]')].sort((a,b)=>Number(a.dataset.choiceReturn)-Number(b.dataset.choiceReturn))[0];if(!weakest)break;const label=weakest.dataset.choice;weakest.remove();BEST_CHOICES=BEST_CHOICES.filter(c=>c[0]!==label);if(BEST_SELECTED===label)selectBestChoice("Balanced");}});
}
// ------------------------------------------------------------------------------------------------------
// The logged-out cards.
//
// /api/best-settings-cards serves AGGREGATES ONLY — return, drawdown, funded/eligible counts, positive
// quarters, win:loss and the three rolling-year returns — precomputed by run_best_settings_cards.py from
// this page's own search (hvf_web/best_settings.js) running under Node. No per-trade row is in that
// payload, so there is nothing here that could reconstruct the Transaction evidence, which stays hidden
// exactly as before. The detail panel is not rendered at all: it exists only to hold that evidence.
// ------------------------------------------------------------------------------------------------------
let PUBLIC_BEST=null, PUBLIC_BEST_LOADING=false, PUBLIC_BEST_ERROR="";
function renderPublicBestCombo(){
  const box=$("ordp-bestcombo"); if(!box)return;
  if(PUBLIC_BEST){paintPublicBestCombo(PUBLIC_BEST);return;}
  if(PUBLIC_BEST_ERROR){
    box.innerHTML=`<div class="muted" style="font-size:13px">Best Settings cards could not be loaded (${_esc(PUBLIC_BEST_ERROR)}). <button class="subpill" onclick="retryPublicBestCombo()">Retry</button></div>`;
    return;
  }
  box.innerHTML=`<div class="refreshing" role="status" aria-live="polite" style="padding:18px 0"><b class="sqh-loading">⏳ Data loading…</b></div>`;
  if(PUBLIC_BEST_LOADING)return;
  PUBLIC_BEST_LOADING=true;
  fetch("/api/best-settings-cards").then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(j=>{PUBLIC_BEST_LOADING=false;
      if(j.error)throw new Error(j.error);
      PUBLIC_BEST=j; renderPublicBestCombo();})
    .catch(err=>{PUBLIC_BEST_LOADING=false;PUBLIC_BEST_ERROR=String((err&&err.message)||err||"unavailable");renderPublicBestCombo();});
}
function retryPublicBestCombo(){PUBLIC_BEST_ERROR="";PUBLIC_BEST=null;renderPublicBestCombo();}
function paintPublicBestCombo(payload){
  const box=$("ordp-bestcombo"); if(!box)return;
  const cards=payload.cards||[];
  if(!cards.length){
    box.innerHTML=`<div class="muted" style="font-size:13px">${payload.pending
      ?"The Best Settings cards have not been built yet — the first full-grid audit has not run against any scan."
      :"No supported recommendation is available from the current dataset."}</div>`;
    return;
  }
  const trimmed=trimBestSettingsCards(cards,payload.unsupported||[],!payload.recommended3y,bestCardCapacity());
  // current:null — nobody is signed in, so there is no saved configuration to compare against and the
  // "matches yours" tick and Changes line are correctly absent rather than guessed at.
  const choiceCards=trimmed.cards.map(c=>bestSettingsCardHTML(c,{current:null,
    apply:`<div class="fcard-apply"><span class="muted" style="font-size:11px">Log in to apply this configuration.</span></div>`})).join('');
  const unsupportedCards=trimmed.unsupported.map(bestSettingsUnsupportedCardHTML).join('');
  const t=payload.threeYear||null;
  const threeYearStatusCard=payload.recommended3y?"":_bestThreeYearStatusCard({loaded:true,error:"",
    best:t?{ret:t.ret,dd:t.dd,n:t.n,settings:t.settings,cfg:null}:null,current:null});
  const model=payload.model||{};
  // Dated rather than hidden. The cards used to disappear entirely whenever a new scan published before
  // the audit had rebuilt them (user 2026-09-05: "has AGAIN stopped showing cards when user not logged
  // in"). These are 12-month figures; an hour-old scan does not move them, and a blank panel tells the
  // reader less than dated numbers do. Saying WHICH scan they came from is what keeps that honest.
  const staleNote=payload.stale_dataset
    ? `<div class="muted" style="margin:0 0 8px;font-size:12px;padding:6px 9px;border-left:3px solid #d29922;background:color-mix(in srgb,#d29922 8%,transparent)">Calculated from the scan of <b>${_esc(String(payload.stale_dataset).replace("T"," "))}</b>; a newer scan has since published and these are being recalculated against it.</div>`
    : "";
  box.innerHTML=staleNote+`<div class="muted" style="margin:0 0 8px;font-size:12px">These recommendations are calculated from the full replayed population on a <b>${model.wallet?'£'+Number(model.wallet).toLocaleString():'£10,000'}</b> model wallet at <b>${model.position_pct??5}%</b> position size${payload.data_through?`, using data through <b>${_esc(payload.data_through)}</b>`:""}. <a href="#" onclick="showLogin();return false">Log in</a> to model your own wallet, see the transaction evidence behind each card, and apply a configuration.</div>
  <div class="fgrid" style="margin:0 0 10px">${choiceCards}${unsupportedCards}${threeYearStatusCard}</div>`;
  _bestGridPostLayout(box);
  BEST_CHOICES=[];
}
window.addEventListener("resize",()=>{
  clearTimeout(_bestSettingsResizeTimer);
  _bestSettingsResizeTimer=setTimeout(()=>{
    const capacity=bestCardCapacity();
    if(_bestDecisionRows&&capacity!==_bestCardCapacity)renderBestCombo(_bestDecisionRows,{recordSnapshot:false});
  },150);
});
// Best Settings choice cards (Balanced/Growth/Defensive/Broad evidence/>125 trades/>250 trades): clicking one swaps the detail
// card + Transaction evidence below to that option (user 2026-08-07, ChangeRequest P-06 — previously only
// Balanced ever got a detail card, which is why it "looked lost" next to the other three).
let BEST_CHOICES=[], BEST_SELECTED="Balanced", BEST_MODEL_W=1000;
function selectBestChoice(label){
  const found=BEST_CHOICES.find(c=>c[0]===label); if(!found)return;
  const [lbl,x]=found;
  BEST_SELECTED=lbl;
  const detail=$("best-detail"); if(!detail)return;
  if(LIMITED){detail.style.display="none";return;}
  detail.style.display="";
  // The funded replay proof is only computed for whichever choice has actually been viewed (lazy) — Best
  // Settings already evaluates thousands of configurations per render, so we don't pay for a full
  // chronological replay on the 3 cards a user never clicks.
  // An EMPTY proof is not a computed proof. _combReplay pushes one row per eligible trade, so a
  // non-empty seq can never legitimately yield zero rows -- but `[]` is TRUTHY, so a proof computed
  // before seq was populated was cached forever and the evidence table rendered empty with no way back
  // (user 2026-08-23: "Capital efficient card selected but nothing in evidence table"). Observed live:
  // seq 64 rows, 45 funded, 42.4% return, stored proof 0 rows, while a fresh replay of the SAME inputs
  // returned all 64. Retry when the stored proof disagrees with the population it came from; a genuinely
  // empty seq still falls through to the table's own "No transactions match this view" state.
  const _proofStale=x.proof&&!x.proof.length&&(x.seq||[]).length>0&&(x.proofAttempts||0)<3;
  if(_proofStale)x.proof=null;
  if(!x.proof){
    document.querySelectorAll('#ordp-bestcombo .fcard-choice').forEach(el=>el.classList.toggle('fcard-selected',el.dataset.choice===lbl));
    if(x.proofError){
      detail.innerHTML=`<div class="muted" role="alert" style="padding:18px 0;color:var(--bear)"><b>Transaction evidence could not be calculated.</b> Select the card again to retry. No settings or orders were changed.</div>`;
      delete x.proofError;
      return;
    }
    // House wording first (user 2026-08-23: "Transaction evidence must have Data loading message if that
    // is what is happening"), with the reassurance that item 12 asked for kept underneath it.
    detail.innerHTML=`<div class="refreshing" role="status" aria-live="polite" style="padding:18px 0"><b class="sqh-loading">⏳ Data loading…</b><div class="muted" style="font-size:12px;margin-top:4px">Transaction evidence for <b>${_esc(lbl)}</b> — replaying that configuration's chronological funding decisions. This does not fetch data, change settings, or place orders.</div></div>`;
    if(!x.proofLoading){
      x.proofLoading=true;
      // Let the browser paint the loading state before the potentially large chronological replay starts.
      x.proofAttempts=(x.proofAttempts||0)+1;
      requestAnimationFrame(()=>setTimeout(()=>{
        try{x.proof=_combReplay(x.seq,x.st/100,x.mo,true).proof;}
        catch(error){console.error('Best Settings transaction-evidence replay failed',error);x.proofError=true;}
        finally{x.proofLoading=false;}
        // A user may have selected a different card while this calculation was queued: never switch it back.
        if(BEST_SELECTED===lbl)selectBestChoice(lbl);
      },0));
    }
    return;
  }
  document.querySelectorAll('#ordp-bestcombo .fcard-choice').forEach(el=>el.classList.toggle('fcard-selected',el.dataset.choice===lbl));
  const pct=v=>v>=9?'×'+(1+v).toLocaleString(undefined,{maximumFractionDigits:(1+v)>=100?0:1}):(v>=0?'+':'')+(v*100).toFixed(1)+'%';
  const gbp=v=>'£'+Math.round(v).toLocaleString();
  const seq=x.seq, w=BEST_MODEL_W;
  const wn=seq.filter(r=>r.perf>0).length, ln=seq.filter(r=>r.perf<0).length, wl=(wn+ln)?Math.round(wn/(wn+ln)*100):null;
  // The FUNDED split alongside the eligible one (user 2026-08-28). x.wins/x.losses are counted inside
  // _combReplay over the trades it actually placed, so this matches the funded count in the heading.
  const awn=+x.wins||0, aln=+x.losses||0, awl=(awn+aln)?Math.round(awn/(awn+aln)*100):null;
  detail.innerHTML=`<h4 style="margin:0 0 4px">${_esc(lbl)} recommendation detail <span class="muted" style="font-weight:400;font-size:11px">— ranked by return per unit of drawdown, over ${x.n.toLocaleString()} funded trades</span></h4>
    <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:6px 0 8px">
      <div><div class="muted" style="font-size:11px">Max position size</div><b style="font-size:16px;color:var(--fg)">${x.st}%</b></div>
      <div><div class="muted" style="font-size:11px">Max open positions</div><b style="font-size:16px;color:var(--fg)" title="Numeric cap ${x.mo}, capped by stake exposure where lower; peak concurrent positions actually funded ${x.cap}.">${x.mo} <span class="muted" style="font-size:11px;font-weight:400">(${x.cap} peak funded)</span></b></div>
      <div><div class="muted" style="font-size:11px">R:R floor</div><b style="font-size:16px;color:var(--fg)">≥ ${x.rr}</b></div>
      <div><div class="muted" style="font-size:11px">Quality floor</div><b style="font-size:16px;color:var(--fg)">${x.q>0?'≥ '+x.q:'any'}</b></div>
      <div><div class="muted" style="font-size:11px">VolumeScore floor</div><b style="font-size:16px;color:var(--fg)">${x.vs>0?'≥ '+x.vs:'any'}</b></div>
      <div><div class="muted" style="font-size:11px">Market / Sector / MCap scope</div><b style="font-size:16px;color:var(--fg)">${x.scope.label}</b></div>
      <div><div class="muted" style="font-size:11px">RVOL floor</div><b style="font-size:16px;color:var(--fg)">${x.rv>0?'≥ '+x.rv:'any'}</b></div>
      <div><div class="muted" style="font-size:11px">VWAP / ATR</div><b style="font-size:16px;color:var(--fg)">${x.vw?'favourable VWAP':'any VWAP'} · ${x.atr?'ATR expanding':'any ATR'}</b></div>
      <div style="border-left:1px solid var(--line);padding-left:18px"><div class="muted" style="font-size:11px">Total return</div><b style="font-size:16px;color:${x.ret>=0?'var(--bull)':'var(--bear)'}">${pct(x.ret)}</b></div>
      <div><div class="muted" style="font-size:11px">Max drawdown</div><b style="font-size:16px;color:var(--bear)">−${(x.dd*100).toFixed(1)}%</b></div>
      <div><div class="muted" style="font-size:11px">Return ÷ drawdown</div><b style="font-size:16px;color:var(--fg)">${x.dd>0?(x.ret/x.dd).toFixed(2):'∞'}</b></div>
      <div><div class="muted" style="font-size:11px">Win : Loss (eligible)</div><b style="font-size:16px;color:var(--fg)" title="Wins vs losses among EVERY trade matching this configuration, whether or not the wallet could fund it">${wn} : ${ln}${wl!=null?` <span class="muted" style="font-weight:400;font-size:12px">(${wl}%)</span>`:''}</b></div>
      <div><div class="muted" style="font-size:11px">Win : Loss (actual)</div><b style="font-size:16px;color:var(--fg)" title="Wins vs losses among only the trades this configuration actually FUNDED — the same population as the funded count in the heading">${awn} : ${aln}${awl!=null?` <span class="muted" style="font-weight:400;font-size:12px">(${awl}%)</span>`:''}</b></div>
      <div><div class="muted" style="font-size:11px">Positive quarters</div><b style="font-size:16px;color:${x.consistency>=.75?'var(--bull)':'#d29922'}">${x.posPeriods} / ${x.periods}</b></div>
    </div>
    <div class="muted" style="font-size:11px;margin-top:6px">On your <b>${gbp(w)}</b> Model wallet. Ranking rewards annual return per unit of drawdown, requires at least 20 funded trades, and discounts settings that were inconsistent across quarters. All recommendation cards use an explicit numeric Max open value. This is historical evidence, not a guaranteed return.</div>
    <div id="best-proof"></div>`;
  renderDecisionProof('best-proof',x.proof);
}
// Copy a winning configuration (from the Best-combination card or a combos row) into the user's personal
// trading limits (user 2026-08-02, ToDo P-08) — "replicate those results in our trading". Confirms first,
// POSTs only the provided keys to /api/config, refreshes MY_LIMITS + the config inputs, and re-renders the
// dependent views. Values map: min_risk_reward, min_quality, min_volume_score, max_position_pct, max_open.
// The four visible states of an "Apply this configuration" button. Amber = working, green = applied,
// red = failed and clickable again (user 2026-08-23). Colours come from the theme tokens so this reads
// correctly in both light and dark; "idle" clears them rather than leaving the last result's tint behind.
// A persistent, screen-reader-announced record of the last apply. Lives above the card grid rather than
// on the card, because applying re-renders the grid and a message attached to a card does not survive it.
function _applyBanner(text,colour){
  let el=$("best-apply-banner");
  if(!el){
    const host=$("ordp-bestcombo"); if(!host)return;
    el=document.createElement("div"); el.id="best-apply-banner";
    el.setAttribute("role","status"); el.setAttribute("aria-live","polite");
    el.style.cssText="margin:8px 0;padding:9px 12px;border:1px solid var(--line);border-left-width:4px;"+
                     "border-radius:7px;font-size:12.5px;background:var(--chip)";
    host.parentNode.insertBefore(el,host);
  }
  el.style.borderLeftColor=colour||"var(--line)";
  el.innerHTML=`<b style="color:${colour||"var(--fg)"}">${text}</b>`;
}
const _APPLY_BTN={
  idle:  {text:"Apply this configuration",      bg:"",              fg:"",              disabled:false},
  saving:{text:"⏳ Saving…",                     bg:"var(--warn)",   fg:"#1a1a1a",       disabled:true},
  done:  {text:"✓ Applied — Back Test updated", bg:"var(--bull)",   fg:"#0b1f14",       disabled:true},
  failed:{text:"⚠ Save failed — retry",         bg:"var(--bear)",   fg:"#fff",          disabled:false}};
function _applyBtnState(btn,state){
  const s=_APPLY_BTN[state]||_APPLY_BTN.idle;
  btn.textContent=s.text; btn.disabled=s.disabled;
  btn.style.background=s.bg; btn.style.color=s.fg; btn.style.borderColor=s.bg||"";
  btn.setAttribute("aria-busy",state==="saving"?"true":"false");
}
async function applyConfigFromReport(cfg, btn, opts){
  // Fail closed rather than at the POST: without a session there is no User Configuration to write to,
  // and /api/config rejects it. The card renderer already withholds the button when logged out; this
  // guard covers every other caller (user 2026-08-22).
  if(!AUTH){await appConfirm("Log in to apply a configuration to your account.",{title:"Not logged in",ok:"OK"});return;}
  cfg=cfg||{}; const limits=cfg.limits||cfg, filters=cfg.filters||{};
  const labels={min_risk_reward:"R:R floor",min_quality:"Quality floor",min_volume_score:"VolumeScore floor",min_rvol:"RVOL floor",require_above_vwap:"Require above VWAP",require_atr_expanding:"Require ATR expanding",max_position_pct:"Max position size %",max_open:"Max open positions",min_instrument_value:"Minimum instrument value",max_instrument_value:"Maximum instrument value"};
  // Booleans and money read as raw 0/1/numbers otherwise, and f_mkt/pof_market (and f_sec/pof_sector)
  // are the same decision stored twice - listing all four padded the dialog for no information.
  const shown=v=>v===1||v===true?"Yes":v===0||v===false?"No":(v===""||v==null?"Any":String(v));
  const money=v=>+v>0?(+v).toLocaleString():"Any";
  const rows=Object.keys(limits).map(k=>[labels[k]||k,
    (k==="require_above_vwap"||k==="require_atr_expanding")?shown(limits[k])
    :(k==="min_instrument_value"||k==="max_instrument_value")?money(limits[k])
    :(k==="max_position_pct")?limits[k]+"%":shown(limits[k])]);
  if("f_mkt" in filters)rows.push(["Market scope",filters.f_mkt||"All markets"]);
  if("f_sec" in filters)rows.push(["Sector scope",filters.f_sec||"All sectors"]);
  // A configuration below the evidence rule may still be applied, but the confirmation must SAY so --
  // the whole reason the button was previously withheld (user 2026-08-28).
  const below=opts&&opts.belowEvidence;
  if(below)rows.unshift(["⚠ Evidence",`${below} funded trades - below the >125 rule, so this is information rather than a recommendation`]);
  if(!await appConfirm("This updates your User Configuration only. It does not place or change any orders."
      +(below?" This configuration is BELOW the high-confidence evidence rule.":""),
      {title:"Apply this configuration",ok:"Apply",rows})) return;
  // In-flight, applied and failed are three different states and must not all look the same (user
  // 2026-08-23: "there is a message of 'Saving' - this should be clear to user e.g. with AMBER colour").
  // The save is a round trip to /api/config, so "Saving…" can sit there for a moment; amber says work is
  // in progress, green says it landed, red says it did not. _applyBtnState also restores the button's own
  // colours afterwards, so a retry does not stay tinted from the previous attempt.
  if(btn)_applyBtnState(btn,"saving");
  _applyBanner("Saving your configuration… the page may pause briefly while the Back Test is rebuilt.",
               "var(--warn)");
  const nextFilters={...USER_FILTERS,...filters};
  fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({limits,filters:nextFilters})})
    .then(r=>{if(!r.ok)throw 0;
      // Confirm the SAVE the moment the server confirms it, BEFORE any re-render (user 2026-08-28:
      // "not showing Saving (AMBER) whilst running or Saved (GREEN) once complete"). These two lines
      // used to sit AFTER applyWinnersDefaults(), which re-runs the Best Settings render -- measured at
      // 52-61 s before the three-year search was memoised. So the amber state was correct and then the
      // page froze, and green only appeared once rendering finished, long after the save had landed.
      // The POST has succeeded here, so "Saved" is already true; making the user wait for a repaint to
      // be told so is what made a working save look broken.
      if(btn){_applyBtnState(btn,"done");setTimeout(()=>_applyBtnState(btn,"idle"),4000);}
      _applyBanner(`Saved at ${new Date().toLocaleTimeString()}. Your User Configuration now uses these
        values; the Back Test and Scanner below have been updated to match.`,"var(--bull)");
      MY_LIMITS={...MY_LIMITS,...limits}; USER_FILTERS=nextFilters; applyUserDefaults();
      // applyUserDefaults() seeds defaults and deliberately skips empty values, so on its own it can never
      // CLEAR a filter and never repaints the P-08 multi-select buttons. That is why an applied card looked
      // like it "does not do all the config e.g. markets" (user 2026-08-14, P-03): a "Market: US" card left
      // the dropdown still reading "All", and an "All markets" card left the previous market selected on the
      // Scanner, which filters from the DOM rather than USER_FILTERS. Write every applied key explicitly.
      Object.entries(filters).forEach(([k,v])=>{const el=$(k);if(!el)return;
        const want=new Set(String(v==null?"":v).split(SEP).filter(Boolean));
        if(el.multiple)[...el.options].forEach(o=>{o.selected=want.has(o.value);});
        else el.value=v==null?"":v;});
      if(typeof msyncAll==='function')msyncAll();
      Object.entries(limits).forEach(([k,v])=>{const el=$("lim-"+k);if(el){if(el.type==='checkbox')el.checked=!!v;else el.value=v;}});
      if(typeof applyWinnersDefaults==='function')applyWinnersDefaults();
      // The save is confirmed in a place that does NOT disappear. The button reverted to idle after four
      // seconds, and applying triggers several heavy re-renders which can stall the tab for longer than
      // that -- so by the time the page responded again the only evidence it had worked was already gone
      // (user 2026-08-23: "after that it is not clear if settings Saved. or not"). The banner persists
      // until the next apply, and carries the time it saved.
      if(typeof renderPreorders==='function')renderPreorders();
      if(typeof render==='function')render();   // Scanner table hard-filters on MY_LIMITS too (P-01 2026-08-11) — same gap as saveLimits(), same fix
      if(typeof _renderPerformance==='function')_renderPerformance();})
    .catch(()=>{if(btn)_applyBtnState(btn,"failed");
      _applyBanner("NOT saved. Your configuration is unchanged and no orders were placed. "+
                   "Check you are still signed in, then press Apply again.","var(--bear)");});
}
// One compounding pass over a set of winners trades (user 2026-07-27, P-10 L122/L123 refactor): chronological
// by trigger date, applies the Max-open-positions cap (a squeeze that triggers while the book is full is a
// MISSED row), and stakes WINNERS_STAKE % of the running wallet on each taken trade. Returns the ledger
// (taken + missed rows, in order) and the final wallet. Extracted so the brushed per-chart net-£ attribution
// can re-run it over each chart's filtered subset (compounding is path-dependent → one pass per subset).
function _winLedger(rows){
  // DO NOT "optimise" the sort below. localeCompare looks like an easy win over a plain < comparison, and
  // it is measurably faster -- but it ORDERS TICKERS DIFFERENTLY, and this replay compounds, so the order
  // is the answer. Benchmarked against the real 11,682-row three-year payload on 2026-08-23: swapping it
  // ran 1.11x faster and moved funded trades 263 -> 251 and the ending wallet £10,906.85 -> £11,943.66.
  // A £1,037 error for 11%.
  //
  // Hoisting the sort out of settle() IS answer-preserving (verified by fingerprint) and worth only 1.06x,
  // which does not justify touching a replay that produces trader-facing numbers. Seven passes over the
  // three-year window cost about 850 ms total and are not the bottleneck; the render was (see
  // the removed winners ledger table) and the server build was (see run_winners_precompute.py).
  const stopPct=r=>Math.abs(r.entry-r.stop)/r.entry;
  const seq=rows.filter(r=>r.perf!=null).slice().sort((a,b)=>(a.trig_date||'').localeCompare(b.trig_date||'')||(a.ticker||'').localeCompare(b.ticker||''));
  // Same affordability model as _combReplay: settle P&L when exits occur, reserve broker margin
  // (position size / leverage) while positions are open, enforce Max open separately, and skip below the
  // broker's minimum trade size.
  const cap=WINNERS_MAXOPEN>0?WINNERS_MAXOPEN:_fundedMaxOpen(WINNERS_STAKE);
  let w=WINNERS_WALLET,reserved=0;const ledger=[];const _openEx=[];
  const exitOf=r=>_pfExitDate(r,false);   // one shared slot-release rule (2026-08-14, P-03)
  const gbpAbs=v=>'£'+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const settle=td=>{_openEx.sort((a,b)=>a.exit.localeCompare(b.exit));
    while(_openEx.length&&_openEx[0].exit<=td){const x=_openEx.shift();w+=x.net;reserved=Math.max(0,reserved-x.margin);}};
  for(const r of seq){
    const td=r.trig_date||'';
    settle(td);
    const stake=w*WINNERS_STAKE,lev=Math.max(1,+(levOf(r)||1)),margin=stake/lev,available=Math.max(0,w-reserved);
    if(stake<MIN_TRADE){ledger.push({r,missed:true,reason:`Below minimum trade — ${gbpAbs(stake)} < £${MIN_TRADE}`,open:_openEx.length});continue;}
    if(cap>0&&_openEx.length>=cap){ledger.push({r,missed:true,reason:`Book full — ${cap} open`,open:_openEx.length});continue;}
    if(margin>available+1e-9){ledger.push({r,missed:true,reason:`Wallet / margin full — ${gbpAbs(available)} available`,open:_openEx.length});continue;}
    const net=stake*r.perf/100,exit=exitOf(r);
    reserved+=margin;_openEx.push({exit,margin,net});
    ledger.push({r,stake,margin,available:Math.max(0,w-reserved),net,maxloss:stake*stopPct(r),cum:w+net,open:_openEx.length,missed:false});}
  settle('9999-99-99');
  return {ledger,endWallet:w};
}
// Winners-chart filter dims (user 2026-07-27, P-10 L122/L123): clicking a Location/Market/Sector/Direction/
// Outcome bar narrows the summary cards + ledger, like the Results tab. locName keeps FTSE 100 under UK/Europe.
// Quality (10-wide) and R:R (1-wide) bands — clickable dims so the winners cards can be filtered by
// R:R / Quality too (user 2026-07-27, P-10 L300), matching the "Best Quality / R:R" theme.
const _qBand=r=>r.quality!=null?`${Math.floor(r.quality/10)*10}–${Math.floor(r.quality/10)*10+9}`:"—";
const _rrBand=r=>r.rr!=null?`R:R ${Math.floor(+r.rr)}–${Math.floor(+r.rr)+1}`:"—";
const _owDims=[["ordpf_market",r=>r.market],["ordpf_location",r=>locName(r.location)],["ordpf_sector",r=>r.sector],["ordpf_direction",r=>r.direction],["ordpf_outcome",r=>r.outcome],["ordpf_quality",_qBand],["ordpf_rr",_rrBand]];
const _owPass=(r,except)=>_owDims.every(([id,fn])=>id===except||inSet(id,fn(r)||"—"));
function paintOrdersPerf(){
  const box=$("ordp-summary"), dims=$("ordp-dims");
  if(!box)return;
  box.classList.remove("sqh-loading");   // the outer wrapper carries the class in static markup (2026-08-07 fix)
  // Best settings is an annual decision model. Do not silently train it on the descriptive Results tab's
  // date, saved-setting or chart selections: those would bias the recommendation toward today's setup.
  // tradeVisible(): Back Test replays only what this user may actually trade (_pfMatchesCurrentConfig
  // ends on the same call), so Best Settings must train on the same population - otherwise it recommends a
  // market the Markets (User)/(Admin) switches have turned off and Back Test then replays a different book
  // (user 2026-08-14, P-03 "does not seem to do all the config e.g. markets").
  const decisionRows=(WIN||[]).filter(r=>r&&r.entry&&r.stop&&r.perf!=null&&r.trig_date&&tradeVisible(r));
  renderBestCombo(decisionRows);
  // Personal Minimum Volume Score floor filters the winners population too (user 2026-07-28), keeping it on
  // ONE dataset with the Results tab; unscored rows (null) pass, matching My Pre-orders.
  const _vsF=(typeof num==='function')?num(MY_LIMITS.min_volume_score):(+MY_LIMITS.min_volume_score||null);
  const _vsFloor=(_vsF!=null&&_vsF>0)?_vsF:0;
  const all=(WIN||[]).filter(r=>r&&r.entry&&r.stop&&pfDateOk(r.trig_date)&&(!_vsFloor||r.volume_score==null||r.volume_score>=_vsFloor)&&tradeVisible(r));   // shared date-window + Volume Score floor (P-01 / 2026-07-28) + user direction/market filter (2026-08-01)
  if(!all.length){
    box.innerHTML=`<div class="muted" style="font-size:13px">No trades in this date window — widen the date filter above (or clear it for the full 12 months).</div>`;
    if(dims)dims.innerHTML="";return;}
  // Apply the winners-chart click-to-filter selection (L122/L123) — the cards + ledger below reflect it.
  const fsel=all.filter(r=>_owPass(r,null)), _filtered=fsel.length!==all.length;
  paintWinnersDims(dims,all);   // brushed, clickable net-£ attribution charts (built from `all`, own filter excluded)
  if(!fsel.length){
    box.innerHTML=`<div class="muted" style="font-size:13px">No trades match the selected chart filters. Clear a filter (the ▶ ✕ on a chart header) to widen.</div>`;
    return;}
  const money=v=>v==null?'—':`£${Math.round(v).toLocaleString()}`;
  const wp=fsel.filter(r=>r.perf!=null);                       // returns available (incl. open, marked-to-market)
  const wins=wp.filter(r=>r.perf>PF_BE),losses=wp.filter(r=>r.perf<-PF_BE),be=wp.filter(r=>Math.abs(r.perf)<=PF_BE);
  const winpct=wp.length?Math.round(wins.length/wp.length*1000)/10:null;
  const losspct=wp.length?Math.round(losses.length/wp.length*1000)/10:null;
  const avgRet=wp.length?wp.reduce((a,r)=>a+r.perf,0)/wp.length:0;
  // THE model (user 2026-07-18): chronological by TRIGGER DATE, stake = Max-position-size % of the wallet
  // AT THAT POINT IN TIME (compounding). net = stake × return%. Now over the filtered selection (L122).
  const stakePct=+(WINNERS_STAKE*100).toFixed(2);
  const {ledger,endWallet:w}=_winLedger(fsel);
  const takenRows=ledger.filter(x=>!x.missed), missedRows=ledger.filter(x=>x.missed);
  const skipped=missedRows.length, taken=takenRows;   // `taken` kept as the taken-trades array used below
  const gain=w-WINNERS_WALLET;
  const mls=takenRows.map(x=>x.maxloss).sort((a,b)=>a-b);const medML=mls.length?mls[Math.floor(mls.length/2)]:0;
  const span=taken.length?`${taken[0].r.trig_date} → ${taken[taken.length-1].r.trig_date}`:'';
  const capNote=` · capped at ${WINNERS_MAXOPEN} open → <b>${taken.length}</b> taken, ${skipped} skipped`;
  const openStake=money(WINNERS_WALLET*WINNERS_STAKE);
  const card=(ic,t,v,sub)=>`<div class="fcard"><div class="ic">${ic}</div><h3>${t}</h3><div class="body"><b style="font-size:17px;color:var(--fg)">${v}</b><br><span class="muted">${sub}</span></div></div>`;
  box.innerHTML=
    card("📊","Trades placed · "+(typeof PF_WINDOW_LABEL!=='undefined'?PF_WINDOW_LABEL:'12 months'),taken.length.toLocaleString(),`<b>${taken.length.toLocaleString()}</b> actually placed of <b>${all.length.toLocaleString()}</b> tradeable${skipped?` · <b>${skipped.toLocaleString()}</b> skipped (book/wallet full)`:''}${_filtered?` · ${fsel.length.toLocaleString()} after chart filters`:''} · ${span}${_filtered?' · filtered by the charts (click a bar to change)':' · tradeable universe (R:R≥3, FX/Crypto excluded)'}`)+
    card("🎯","Win vs loss",`${winpct==null?'—':winpct+'%'} / ${losspct==null?'':losspct+'%'}`,`a win is <b>any gain</b>, a loss is <b>any loss</b> (open trades included) — same definition as the Results report · avg return ${avgRet>=0?'+':''}${avgRet.toFixed(1)}%`)+
    card("💷","Net P&L",`<span style="color:${_pnlc(gain)}">${_gbp2(gain)}</span>`,`from staking <b>${stakePct}% of the wallet</b> on every trade (compounding). Stake starts at ${openStake}; typical max loss <b>£${medML.toFixed(2)}</b> (= stake × the stop distance %).`)+
    card("📈",money(WINNERS_WALLET)+" compounded",`<span style="color:${w>=WINNERS_WALLET?'var(--bull)':'var(--bear)'}">${money(w)}</span> <span class="muted" style="font-size:12px">total</span>`,`each stake = ${stakePct}% of the wallet <i>at that moment</i>, so winners raise the next stake. ${money(gain)} gain over ${taken.length.toLocaleString()} trades taken — every one in the ledger below, oldest first.`);
  // (Monthly %-growth chart now lives on the Report tab — _pfMonthly, driven by _renderPerformance.)
  // The net-£ attribution charts (which attributes separated the winners) are painted by paintWinnersDims()
  // ABOVE (they're click-to-filter now, L122/L123, so they drive this very render and must be built first).
  // The winners ledger TABLE used to be rendered here. Its markup (#ordp-table, #ordp-count and the
  // hide-missed control) was removed deliberately on 2026-08-04 in d686084 "Simplify winners analysis
  // presentation", a commit that added tests asserting it stays gone -- but the builder was left
  // behind, guarded by if(tb)/if(tc), silently doing nothing on every render. Reading it on
  // 2026-08-30 cost half an hour and produced a bug report for a defect that did not exist, so it is
  // removed rather than left as a trap. The counts it used to show now live on the Transaction
  // evidence header (renderDecisionProof).
}
// Brushed, click-to-filter net-£ attribution charts for the winners tab (user 2026-07-27, P-10 L122/L123).
// Each chart is built over the trades passing every OTHER winners filter (so picking one narrows the others
// but never hides a bar — house L31), with net £ attributed from a ledger re-run over that brushed subset
// (compounding is path-dependent). Bars carry data-fk/data-fv (RAW value, like barChart, so inSet matches)
// so the shared [data-fk] click dispatcher toggles the ordpf_* set and re-renders paintOrdersPerf. Location
// grouping via locName keeps FTSE 100 under UK/Europe (answers the "Oceania contains FTSE 100" report).
function paintWinnersDims(dims, all){
  if(!dims)return;
  const groups=[["Market","ordpf_market",r=>r.market],["Location","ordpf_location",r=>locName(r.location)],
                ["Sector","ordpf_sector",r=>r.sector],
                ["Outcome","ordpf_outcome",r=>r.outcome],
                ["Quality","ordpf_quality",_qBand],["R:R","ordpf_rr",_rrBand]];   // Direction card removed (user 2026-08-01); click-to-filter by R:R / Quality (P-10 L300)
  dims.innerHTML=groups.map(([name,fk,fn])=>{
    const base=all.filter(r=>_owPass(r,fk));           // brushing: every OTHER winners filter applied, not this one
    const {ledger:lg}=_winLedger(base);
    const m={}; lg.filter(x=>!x.missed).forEach(x=>{const k=fn(x.r)||'—';(m[k]=m[k]||{net:0,n:0,w:0});m[k].net+=x.net;m[k].n++;if(x.r.perf>PF_BE)m[k].w++;});
    all.forEach(r=>{const k=fn(r)||'—'; if(!(k in m))m[k]={net:0,n:0,w:0};});   // seed every value so bars persist as 0-stubs
    const sel=setOf(fk), nsel=sel?sel.size:0, has=k=>!!(sel&&sel.has(String(k)));
    let bs=Object.entries(m).sort((a,b)=>b[1].net-a[1].net);
    if(name==="R:R")bs=bs.slice(0,10);   // R:R card shows the top 10 bands by net £ (user 2026-08-01)
    if(!bs.length)return"";
    const mx=Math.max(1,...bs.map(([k,v])=>Math.abs(v.net)));
    return `<div class="vizsector"><div class="vizbox${nsel?' filtered':''}"><h5>${name} — net £${nsel?` <span class="afilt clk" data-fk="${fk}" data-fv="" title="clear filter">▶ ${nsel} ✕</span>`:''}</h5><div class="bars">`+
      bs.map(([k,v])=>`<div class="bar clk${has(k)?' active':''}" data-fk="${fk}" data-fv="${k}" title="${k}: ${v.n} trades · ${v.n?Math.round(v.w/v.n*100):0}% win · net ${_gbp2(v.net)} — click to filter">
        <span class="tk" style="max-width:130px"><span class="selmk">${has(k)?'●':''}</span>${k}</span>
        <span class="track"><span class="fill" style="width:${Math.max(2,Math.round(Math.abs(v.net)/mx*100))}%;background:${_pnlc(v.net)}"></span></span>
        <span class="n" style="min-width:60px;color:${_pnlc(v.net)}">${_gbp2(v.net)}</span>
        <span class="n muted" style="min-width:30px">${v.n}</span></div>`).join("")+
      `</div></div></div>`;
  }).join("");
}
// ── Instruments (public) (user 2026-08-07, ChangeRequest P-08) ─────────────────────────────────────────
// Reuses the SAME global DATA array the Scanner already loads via /api/records (now enriched with
// wk52_low/wk52_high, see server.py api_records()) — no extra fetch for the main table. The funnel-history
// mini-table below it lazy-loads the authenticated /api/squeeze-history once and is cached in INSTR_FUNNEL.
let INSTR_FUNNEL=null, instrSorts=[];
function renderInstruments(){
  if($("instr-loginhint"))$("instr-loginhint").style.display=LIMITED?"":"none";
  if($("instr-funnel-wrap"))$("instr-funnel-wrap").style.display=AUTH?"":"none";
  if(AUTH&&INSTR_FUNNEL===null){
    INSTR_FUNNEL=[];
    fetch("/api/squeeze-history",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{rows:[]}).then(j=>{INSTR_FUNNEL=j.rows||[];paintInstrFunnel();})
      .catch(()=>{INSTR_FUNNEL=[];paintInstrFunnel();});
  }
  paintInstruments();
}
function instrSort(k,add=false){
  const at=instrSorts.findIndex(s=>s.k===k),next=at>=0?{k,d:-instrSorts[at].d}:{k,d:1};
  instrSorts=add?instrSorts.filter(s=>s.k!==k):[];instrSorts.push(next);paintInstruments();
  document.querySelectorAll("th[data-instrk]").forEach(th=>{th.querySelector(".sarr")?.remove();const i=instrSorts.findIndex(s=>s.k===th.dataset.instrk);
    if(i>=0)th.insertAdjacentHTML("beforeend",` <span class="sarr">${instrSorts[i].d>0?"▲":"▼"}${instrSorts.length>1?i+1:""}</span>`);});
}
function _instrSortValue(row,key){return key==="rvol"?row.current_rvol:key==="above_vwap"?row.current_above_vwap:key==="atr_expanding"?row.current_atr_expanding:row[key];}
function instrRvolCell(row){
  if(row.current_rvol!=null){const fallback=row.current_metric_status==="complete_nse_fallback"?" NSE-listed equivalent used for this BSE instrument.":row.current_metric_status==="complete_ticker_successor"?" Current successor ticker used for this retained instrument.":"";const date=(row.current_rvol_date?`RVOL from latest usable volume bar: ${row.current_rvol_date}`:"Current RVOL")+fallback;return `<span title="${_esc(date)}">${rvolCell(row.current_rvol)}</span>`;}
  const reason=row.current_metric_status==="delisted"?(row.current_metric_reason||"Instrument is delisted (not applicable)"):{no_price_history:"Price history unavailable — requires data repair",no_reported_volume:"No reported daily volume (not applicable)",insufficient_volume_history:"Insufficient volume history — requires data repair",not_calculated:"Current RVOL has not been calculated"}[row.current_metric_status]||"Current RVOL unavailable — requires data repair";
  return `<span class="muted" title="${_esc(reason)}">Data issue</span>`;
}
// Cross-filter cards: Location/Market/Sector/Status are public; Direction is a 4th "worthwhile" card but
// only once logged in — it's one of the columns hidden pre-login (same rule as the table below).
// ======================================================================================================
// Virtual table rows (user 2026-08-25: "not all rows are visible at once - why not deal with what is
// visible first?").
//
// Only the rows actually on screen exist in the DOM. Everything above and below is represented by two
// spacer rows of the right height, so the scrollbar stays honest and the page scrolls exactly as it did.
// A 1,773-row table costs about 40 rows of DOM instead of 25,000, and stays that way however far you
// scroll -- which a render cap or an infinite-scroll batch cannot do.
//
// WHAT THIS DOES NOT CHANGE. Sorting, filtering and search all operate on the DATA array before it gets
// here, so the rows you see are the correct ones in the correct order, and the counts above the table
// still report the true totals.
//
// KNOWN TRADE-OFF, accepted deliberately: the browser's own Ctrl+F cannot find text in rows that are not
// currently rendered. The in-page search box searches the full data set and is the right tool for that.
//
// Works whether the .tablewrap scrolls or the whole page does: the visible window is computed from the
// tbody's position relative to the viewport, which is true in both cases.
// ======================================================================================================
const VTABLE={};                       // tbody id -> {rows, renderRow, cols, rowH, raf}
const VTABLE_OVERSCAN=12;              // rows drawn beyond each edge, so a fast scroll shows content

function _vtableWindow(st, body){
  const rect=body.getBoundingClientRect();
  const viewTop=0, viewBottom=window.innerHeight||document.documentElement.clientHeight;
  const first=Math.max(0, Math.floor((viewTop-rect.top)/st.rowH)-VTABLE_OVERSCAN);
  const visible=Math.ceil((viewBottom-viewTop)/st.rowH)+VTABLE_OVERSCAN*2;
  return [first, Math.min(st.rows.length, first+visible)];
}

function _vtablePaint(id){
  const st=VTABLE[id], body=$(id); if(!st||!body)return;
  if(!st.rows.length){
    body.innerHTML=`<tr><td colspan="${st.cols}" class="empty">${st.empty||"No rows."}</td></tr>`;
    return;
  }
  // Measure a real row once: heights differ by theme and zoom, and a wrong guess mis-sizes the scrollbar.
  if(!st.rowH){
    body.innerHTML=st.renderRow(st.rows[0],0);
    const r=body.firstElementChild;
    st.rowH=(r&&r.getBoundingClientRect().height)||28;
  }
  const [from,to]=_vtableWindow(st,body);
  if(st.from===from&&st.to===to)return;             // nothing moved; do not touch the DOM
  st.from=from; st.to=to;
  const above=from*st.rowH, below=(st.rows.length-to)*st.rowH;
  const pad=h=>h>0?`<tr aria-hidden="true"><td colspan="${st.cols}" style="padding:0;border:0;height:${h}px"></td></tr>`:"";
  body.innerHTML=pad(above)+st.rows.slice(from,to).map((r,i)=>st.renderRow(r,from+i)).join("")+pad(below);
}

function vtable(id, rows, renderRow, cols, empty){
  const body=$(id); if(!body)return;
  const first=!VTABLE[id];
  VTABLE[id]={rows:rows||[], renderRow, cols, empty,
              rowH:(VTABLE[id]||{}).rowH||0, from:-1, to:-1};
  if(first){
    // One listener per table, coalesced to a frame: scroll fires far faster than the screen refreshes.
    const onScroll=()=>{const st=VTABLE[id]; if(!st||st.raf)return;
      st.raf=requestAnimationFrame(()=>{st.raf=0;_vtablePaint(id);});};
    window.addEventListener("scroll",onScroll,{passive:true});
    window.addEventListener("resize",onScroll,{passive:true});
    const wrap=body.closest(".tablewrap");
    if(wrap)wrap.addEventListener("scroll",onScroll,{passive:true});
  }
  _vtablePaint(id);
}
function paintInstruments(){
  // This tab can be selected before the shared /api/records request completes.  Do not replace the
  // initial loading row with a misleading "No instruments match" result while DATA is still empty.
  if(!DATA_LOADED){
    if($("instr-count"))$("instr-count").innerHTML='<span class="muted">⏳ Data loading…</span>';
    if($("instr-viz"))$("instr-viz").innerHTML='';
    if($("instr-rows"))$("instr-rows").innerHTML='<tr><td colspan="14" class="empty sqh-loading">⏳ Data loading…</td></tr>';
    return;
  }
  const rows=DATA||[], q=(($("instr-search")||{}).value||"").trim().toLowerCase();
  const base=q?rows.filter(r=>((r.name||"")+" "+disp(r.ticker)+" "+(r.ticker||"")).toLowerCase().includes(q)):rows;
  const _idims=[["inf_location",r=>locName(r.location)],["inf_market",r=>r.market],["inf_sector",r=>r.sector],
                ["inf_status",r=>r.has_signal?(r.status||"ACTIVE"):"—"],["inf_direction",r=>r.direction||"—"]];
  const byX=exceptId=>{const m={};base.forEach(r=>{
    for(const [id,fn] of _idims){if(id!==exceptId&&!inSet(id,fn(r)||"—"))return;}
    const v=_idims.find(d=>d[0]===exceptId)[1](r)||"—"; m[v]=(m[v]||0)+1;});return m;};
  let viz=`<div class="vizsector">`+barChart("Location",byX("inf_location"),"inf_location")+`</div>`+
    `<div class="vizsector">`+barChart("Market",byX("inf_market"),"inf_market")+`</div>`+
    `<div class="vizsector">`+pieChart("Sector",byX("inf_sector"),"inf_sector")+`</div>`+
    `<div class="vizsector">`+barChart("Status",byX("inf_status"),"inf_status",_stcol)+`</div>`;
  if(!LIMITED)viz+=`<div class="vizsector">`+barChart("Direction",byX("inf_direction"),"inf_direction",k=>k==="BULL"?"var(--bull)":k==="BEAR"?"var(--bear)":"var(--muted)")+`</div>`;
  if($("instr-viz")){$("instr-viz").innerHTML=viz;packViz("instr-viz");}
  let shown=base.filter(r=>_idims.every(([id,fn])=>inSet(id,fn(r)||"—")));
  if(instrSorts.length)shown=shown.slice().sort((a,b)=>{for(const s of instrSorts){const x=_instrSortValue(a,s.k),y=_instrSortValue(b,s.k);let cmp=0;
    if(x==null||x==="")cmp=(y==null||y==="")?0:1;else if(y==null||y==="")cmp=-1;
    else cmp=(typeof x==="number"&&typeof y==="number")?x-y:String(x).localeCompare(String(y));
    if(cmp)return cmp*s.d;}return 0;});
  if($("instr-count"))$("instr-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${shown.length.toLocaleString()}</b> instruments${rows.length!==shown.length?` <span class="muted">of ${rows.length.toLocaleString()}</span>`:""}`;
  if($("instrtab-count"))$("instrtab-count").textContent=`(${shown.length.toLocaleString()})`;
  // Virtualised (user 2026-08-25: "not all rows are visible at once - why not deal with what is visible
  // first?"). Only the rows on screen exist in the DOM -- about 40 instead of 25,000 for the full
  // universe -- and that stays true however far you scroll. `shown` is already sorted, filtered and
  // searched, so the window drawn is the correct slice of the correct list, and the counts above report
  // the true totals.
  vtable("instr-rows", shown, r=>{
    const tk=(r.ticker||"").replace(/'/g,"");
    return `<tr>
    <td class="clk" style="cursor:pointer;color:var(--accent)" title="${AUTH?"Open the detail report":"Log in to open the detail report"}" onclick="${AUTH?`openDetailFrom('instruments','${tk}')`:"showLogin()"}">${nm40(r.name)}</td>
    <td>${locName(r.location)||""}</td><td>${r.market||""}</td><td>${r.sector||""}</td>
    <td>${ob(r.direction?`<span class="tag ${r.direction==="BULL"?"bull":"bear"}">${r.direction}</span>`:"")}</td>
    <td>${ob(r.quality!=null?`<b style="color:${qcol(r.quality)}">${r.quality}</b>`:"")}</td>
    <td>${r.wk52_low!=null?f2(r.wk52_low):"—"}</td><td>${r.wk52_high!=null?f2(r.wk52_high):"—"}</td>
    <td>${ob(r.rr!=null?(+r.rr).toFixed(1):"")}</td>
    <td>${ob(instrRvolCell(r))}</td>
    <td>${ob(_tickCross(r.current_above_vwap))}</td>
    <td>${ob(_tickCross(r.current_atr_expanding))}</td>
    <td>${ob(volScoreCell(r.volume_score))}</td>
    <td><b>${disp(r.ticker)}</b></td></tr>`;}, 14, "No instruments match.");
  paintInstrFunnel();
}
document.querySelectorAll("th[data-instrk]").forEach(th=>th.onclick=e=>instrSort(th.dataset.instrk,e.shiftKey));
// "Provide a history of the funnels" (P-08) — reuses the authenticated /api/squeeze-history endpoint,
// filtered by the SAME Location/Market/Sector/Direction cards + name search as the table above. Direction
// and Return stay behind the same ob() teaser as the main table.
function paintInstrFunnel(){
  const box=$("instr-funnel-rows"); if(!box)return;
  if(!AUTH)return;
  if(INSTR_FUNNEL===null){box.innerHTML='<tr><td colspan="12" class="empty sqh-loading">⏳ Data loading…</td></tr>';return;}
  const q=(($("instr-search")||{}).value||"").trim().toLowerCase();
  let shown=(INSTR_FUNNEL||[]).filter(r=>inSet("inf_location",locName(r.location)||"—")&&inSet("inf_market",r.market||"—")
    &&inSet("inf_sector",r.sector||"—")&&inSet("inf_direction",r.direction||"—"));
  if(q)shown=shown.filter(r=>((r.name||"")+" "+disp(r.ticker)+" "+(r.ticker||"")).toLowerCase().includes(q));
  shown.sort((a,b)=>String(b.triggered_date||"").localeCompare(String(a.triggered_date||"")));
  box.innerHTML=shown.slice(0,30).map(r=>`<tr>
    <td>${ob(r.direction?`<span class="tag ${r.direction==="BULL"?"bull":"bear"}">${r.direction}</span>`:"")}</td>
    <td>${r.ready_date||"—"}</td><td>${r.triggered_date||"—"}</td>
    <td>${_tickCross(r.atr_expanding)}</td><td>${_tickCross(r.above_vwap)}</td>
    <td>${r.quality??"—"}</td><td>${r.rr??"—"}</td><td>${rvolCell(r.rvol)}</td><td>${volScoreCell(r.volume_score)}</td>
    <td><b style="color:${_stcol(r.outcome)}">${r.outcome||""}</b></td><td>${r.outcome_date||"—"}</td>
    <td>${ob(r.return_pct!=null?`<b style="color:${r.return_pct>0?'var(--bull)':r.return_pct<0?'var(--bear)':'var(--muted)'}">${r.return_pct>0?"+":""}${r.return_pct}%</b>`:"—")}</td></tr>`).join("")
    ||`<tr><td colspan="12" class="empty">No funnel history for this selection.</td></tr>`;
}
// ── Squeeze History (Admin) (user 2026-07-18) — squeeze lifecycle table ───────────────────────────────
let SQH=null, sqhSortK="triggered_date", sqhSortDir=-1;   // default: Triggered date descending (user 2026-08-01); header click changes it
function renderSqueezeHist(){
  if(SQH===null){SQH=[];$("sqh-rows").innerHTML='<tr><td colspan="14" class="empty sqh-loading">⏳ Data loading…</td></tr>';}
  fetch("/api/squeeze-history",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{rows:[]}).then(j=>{
    // The endpoint is background-warmed from 2026-08-25 (user: "squeeze history is still very slow to
    // load"). A cold cache answers {warming:true} INSTANTLY instead of blocking — one such request was
    // measured outstanding for over ten minutes. Keep the loading state and poll, exactly as the
    // Performance tab does; without this branch a warming reply would paint an empty history.
    if(j.warming){SQH=null;$("sqh-rows").innerHTML='<tr><td colspan="14" class="empty sqh-loading">⏳ Data loading…</td></tr>';setTimeout(renderSqueezeHist,3000);return;}
    SQH=j.rows||[];const f=$("sqh-freshness");if(f)f.textContent=j.data_through?`data through ${j.data_through}${j.refreshed_at?` · refreshed ${String(j.refreshed_at).replace('T',' ').slice(0,16)} UTC`:''}`:'';fillSqhFilterOptions();paintSqueezeHist();})
    .catch(()=>{SQH=[];paintSqueezeHist();});
}
// Header sort (user 2026-07-18, P-01): click toggles asc/desc on that column; "" restores server order.
function sqhSort(k){sqhSortDir=(sqhSortK===k)?-sqhSortDir:-1;sqhSortK=k;paintSqueezeHist();_sortArrows("data-sqh",sqhSortK,sqhSortDir);}
function _sqYearOf(r){return String(r.triggered_date||r.ready_date||r.first_seen||'').slice(0,4)||'—';}
function _sqQBand(r){const q=r.quality;if(q==null)return '—';if(q>=75)return '75–100';if(q>=50)return '50–74';if(q>=25)return '25–49';return '0–24';}
const _sqQCol=k=>k==='75–100'?'var(--bull)':k==='0–24'?'var(--bear)':'var(--accent)';
let _sqYearSeeded=false;
let _sqDvSrc=null,_sqDvQ=null,_sqDv=null,_sqDvSig=null;   // cached precomputed dim values (P-06 perf)
// Squeeze History filter controls (user 2026-08-16). Ranges are kept separate from the chart-brushing
// sqf_* sets because they behave differently: a set is "one of these", a range is "between these", and
// only the sets participate in the per-chart `except` brushing.
const SQH_MSELS=["sqf_dir","sqf_loc","sqf_mkt","sqf_sec","sqf_tf","sqf_out"];
const SQH_RANGES=[["sqfr_qmin","sqfr_qmax",r=>r.quality],["sqfr_rrmin","sqfr_rrmax",r=>r.rr],
  ["sqfr_rvmin","sqfr_rvmax",r=>r.rvol],["sqfr_retmin","sqfr_retmax",r=>r.return_pct],
  ["sqfr_dmin","sqfr_dmax",r=>_sqDaysHeld(r)],["sqfr_vsmin","sqfr_vsmax",r=>r.volume_score]];
const SQH_TRI=[["sqf_vwap",r=>r.above_vwap],["sqf_atr",r=>r.atr_expanding]];
const SQH_FILTER_IDS=()=>[...SQH_MSELS,...SQH_RANGES.flatMap(([a,b])=>[a,b]),...SQH_TRI.map(([id])=>id)];
// Calendar days from Triggered to Closed; an unresolved trade counts to today, matching how Back Test's
// days_open is derived server-side (hvf_web/server.py _days_open).
function _sqDaysHeld(r){
  if(!r.triggered_date)return null;
  const a=new Date(String(r.triggered_date).slice(0,10)+"T00:00:00Z");
  const b=r.outcome_date?new Date(String(r.outcome_date).slice(0,10)+"T00:00:00Z"):new Date();
  return (isNaN(a)||isNaN(b))?null:Math.max(0,Math.round((b-a)/86400000));
}
// Options come from SQH itself, not /api/records: the history spans instruments and markets the current
// snapshot no longer contains, and offering a value that can never match is the fault we are avoiding.
function fillSqhFilterOptions(){
  const opts=(get)=>[...new Set((SQH||[]).map(get).filter(v=>v!=null&&v!==""))].sort();
  [["sqf_loc",r=>locName(r.location)],["sqf_mkt",r=>r.market],
   ["sqf_sec",r=>r.sector],["sqf_tf",r=>r.timeframe]].forEach(([id,get])=>{
    const el=$(id); if(!el)return;
    const keep=new Set([...el.selectedOptions].map(o=>o.value));
    el.innerHTML=opts(get).map(v=>`<option${keep.has(v)?" selected":""}>${_esc(v)}</option>`).join("");
  });
  if(typeof msyncAll==="function")msyncAll();
}
function sqhReset(){
  SQH_FILTER_IDS().forEach(id=>{const el=$(id);if(!el)return;
    if(el.multiple)[...el.options].forEach(o=>{o.selected=false;}); else el.value="";});
  // The chart-brushing sets are part of "what is filtered", so Reset clears them too.
  ["sqf_market","sqf_location","sqf_sector","sqf_direction","sqf_status","sqf_year","sqf_quality"]
    .forEach(id=>{const el=$(id);if(el)el.value="";});
  _sqYearSeeded=true;                      // do not re-seed the two-most-recent-years default on reset
  if(typeof msyncAll==="function")msyncAll();
  paintSqueezeHist();
}
function toggleSqhFilters(){
  const a=$("sqh-filters"); if(!a)return;
  const hidden=a.classList.toggle("hidden");
  const b=$("togglefilters");
  if(b)b.innerHTML="Show Filters "+(hidden?'<span style="color:var(--bear)">✗</span>':'<span style="color:var(--bull)">✓</span>');
}
function paintSqueezeHist(){
  const rows=SQH||[], q=(($("sqh-search")||{}).value||"").trim().toLowerCase();
  // Default the year filter to the most recent TWO years, once, when the data first loads (user 2026-08-02,
  // P-06) — this also keeps the (now 32k+ row) tab fast by default.
  if(!_sqYearSeeded && rows.length){
    _sqYearSeeded=true;
    const yrs=[...new Set(rows.map(_sqYearOf).filter(y=>y&&y!=='—'))].sort();
    const recent=yrs.slice(-2);
    if(recent.length && $("sqf_year") && !$("sqf_year").value) $("sqf_year").value=recent.join(SEP);
  }
  // Sidebar filters narrow the population BEFORE the chart strip is built, so the charts describe what
  // the table is actually showing rather than rows it has just excluded.
  const _sqSel=id=>{const el=$(id);if(!el||!el.multiple)return null;
    const v=[...el.selectedOptions].map(o=>o.value).filter(x=>x!=="");return v.length?new Set(v):null;};
  const _sqRng=(lo,hi)=>{const a=$(lo),b=$(hi);
    const mn=(a&&a.value!=="")?+a.value:null, mx=(b&&b.value!=="")?+b.value:null;
    return (mn==null&&mx==null)?null:[mn,mx];};
  const _msel={dir:_sqSel("sqf_dir"),loc:_sqSel("sqf_loc"),mkt:_sqSel("sqf_mkt"),
               sec:_sqSel("sqf_sec"),tf:_sqSel("sqf_tf"),out:_sqSel("sqf_out")};
  const _rngs=SQH_RANGES.map(([lo,hi,get])=>[_sqRng(lo,hi),get]).filter(([r])=>r);
  const _tris=SQH_TRI.map(([id,get])=>[(($(id)||{}).value)||"",get]).filter(([v])=>v!=="");
  const _sqSig=SQH_FILTER_IDS().map(id=>{const el=$(id);
    return el?(el.multiple?[...el.selectedOptions].map(o=>o.value).join(","):el.value):"";}).join("|");
  const sideOn=Object.values(_msel).some(Boolean)||_rngs.length>0||_tris.length>0;
  const _sideKeep=r=>{
    if(_msel.dir&&!_msel.dir.has(r.direction||"—"))return false;
    if(_msel.loc&&!_msel.loc.has(locName(r.location)||"—"))return false;
    if(_msel.mkt&&!_msel.mkt.has(r.market||"—"))return false;
    if(_msel.sec&&!_msel.sec.has(r.sector||"—"))return false;
    if(_msel.tf&&!_msel.tf.has(r.timeframe||"—"))return false;
    if(_msel.out&&!_msel.out.has(r.outcome||"—"))return false;
    // A missing value expresses no opinion and passes, exactly as the Scanner's rng() does — otherwise
    // typing one bound silently deletes every row that has never been scored.
    for(const [rg,get] of _rngs){const v=get(r); if(v==null||!isFinite(+v))continue;
      if(rg[0]!=null&&+v<rg[0])return false; if(rg[1]!=null&&+v>rg[1])return false;}
    for(const [want,get] of _tris){const v=get(r); if(v==null)continue;
      if(String(v?1:0)!==want)return false;}
    return true;};
  const _searched=q?rows.filter(r=>((r.name||'')+' '+disp(r.ticker)+' '+(r.ticker||'')).toLowerCase().includes(q)):rows;
  const base=sideOn?_searched.filter(_sideKeep):_searched;
  // Chart strip with BRUSHING (user 2026-08-01): each chart counts over rows passing every OTHER sqf_ filter
  // but not its own. Year + Quality are now dims too, and Direction drives the compact title chart.
  const _sqDims=[["sqf_market",r=>r.market],["sqf_location",r=>locName(r.location)],["sqf_sector",r=>r.sector],["sqf_direction",r=>r.direction],["sqf_status",r=>r.outcome],["sqf_year",_sqYearOf],["sqf_quality",_sqQBand]];
  // Precompute each row's dim values ONCE (user 2026-08-02, P-06) — the old byX recomputed every dim for
  // every chart, which was slow on 32k+ rows (esp. re-clicking Sector/Status).
  // Precomputed dim values are cached across paints (user 2026-08-02, P-06): they depend only on the data +
  // search, NOT the filter selections, so clicking a filter reuses them instead of rebuilding 32k×7.
  let dv;
  // _sqSig is part of the key: the cache is indexed positionally against `base`, and the sidebar
  // changes what `base` contains, so keying on (data, search) alone would hand back dim values for a
  // different set of rows.
  if(_sqDvSrc===SQH && _sqDvQ===q && _sqDvSig===_sqSig && _sqDv){dv=_sqDv;}
  else{dv=base.map(r=>{const o={};_sqDims.forEach(([id,f])=>{const v=f(r);o[id]=(v==null||v==='')?'—':String(v);});return o;});_sqDvSrc=SQH;_sqDvQ=q;_sqDvSig=_sqSig;_sqDv=dv;}
  // Hoist each dim's selected-set ONCE per paint (user 2026-08-02, P-06) — inSet() rebuilt a Set from a
  // string split on every call, which over 32k rows × 7 charts was ~1.8M splits (the real slowness).
  const _sel={}; _sqDims.forEach(([id])=>_sel[id]=setOf(id));
  const _pass=(id,v)=>{const s=_sel[id];return !s||s.has(v);};   // v is already the normalised dim string
  const byX=id=>{const m={};dv.forEach(o=>{if(!(o[id] in m))m[o[id]]=0;});
    dv.forEach(o=>{for(const [d] of _sqDims){if(d!==id&&!_pass(d,o[d]))return;}m[o[id]]++;});return m;};
  // Year buttons (multi-select) — brushed counts respecting every OTHER filter (user 2026-08-02, P-06).
  const yc=byX("sqf_year"), ysel=setOf("sqf_year");
  const yrsAll=[...new Set(rows.map(_sqYearOf).filter(y=>y&&y!=='—'))].sort().reverse();
  if($("sqh-years"))$("sqh-years").innerHTML=`<span class="muted" style="font-size:11px">Year</span>`+
    yrsAll.map(y=>{const on=ysel&&ysel.has(y);return `<button class="btn clk" data-fk="sqf_year" data-fv="${y}" style="padding:3px 9px;font-size:12px;${on?'border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--fg)':''}" title="${yc[y]||0} squeezes">${y}</button>`;}).join("")+
    (ysel&&ysel.size?` <span class="afilt clk" data-fk="sqf_year" data-fv="" title="clear year filter" style="font-size:12px">✕</span>`:'');
  // Compact Direction chart next to the title (click-to-filter) — freed the strip for a Quality chart.
  const dc=byX("sqf_direction"), dsel=setOf("sqf_direction");
  if($("sqh-dir-mini"))$("sqh-dir-mini").innerHTML=["BULL","BEAR"].filter(d=>d in dc).map(d=>{const on=dsel&&dsel.has(d);const col=d==="BULL"?"var(--bull)":"var(--bear)";
    return `<button class="btn clk" data-fk="sqf_direction" data-fv="${d}" style="padding:3px 9px;font-size:12px;margin-right:4px;${on?`border-color:${col};background:color-mix(in srgb,${col} 16%,transparent)`:''}"><span style="color:${col}">${d}</span> <b>${dc[d]||0}</b></button>`;}).join("");
  if($("sqh-viz"))$("sqh-viz").innerHTML=
    `<div class="vizsector">`+barChart("Location",byX("sqf_location"),"sqf_location")+`</div>`+   /* Location first (P-05 L340) */
    `<div class="vizsector">`+barChart("Market",byX("sqf_market"),"sqf_market")+`</div>`+
    `<div class="vizsector">`+pieChart("Sector",byX("sqf_sector"),"sqf_sector")+`</div>`+
    `<div class="vizsector">`+barChart("Quality",byX("sqf_quality"),"sqf_quality",_sqQCol)+`</div>`+
    `<div class="vizsector">`+barChart("Status",byX("sqf_status"),"sqf_status",_stcol)+`</div>`;
  packViz("sqh-viz");
  let shown=base.filter((r,i)=>{const o=dv[i];for(const [id] of _sqDims){if(!_pass(id,o[id]))return false;}return true;});
  if(sqhSortK)shown=genSort(shown,sqhSortK,sqhSortDir);
  // Cap the rendered rows (user 2026-08-02, P-06) — the store is now 32k+ squeezes; drawing 12k <tr> each
  // repaint made clicking Sector/Status take ~2s. The charts + count still cover the FULL filtered set; only
  // the table display is capped (narrow the filters — or the year — to see more specific rows).
  const SQH_CAP=1500;
  const capped=shown.length>SQH_CAP;
  const view=capped?shown.slice(0,SQH_CAP):shown;
  if($("sqh-count"))$("sqh-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${shown.length.toLocaleString()}</b> squeezes${rows.length!==shown.length?` of ${rows.length.toLocaleString()}`:''}${capped?` <span class="muted" style="font-size:11px">· showing first ${SQH_CAP.toLocaleString()} — filter to narrow</span>`:''}`;
  if($("sqhtab-count"))$("sqhtab-count").textContent=`(${shown.length.toLocaleString()})`;   // row count in the tab name (user 2026-07-20, P-01)
  if($("sqh-rows"))$("sqh-rows").innerHTML=view.map(r=>`<tr>
    <td class="clk" style="cursor:pointer;color:var(--accent)" title="Open the detail report" onclick="openDetailFrom('squeezehist','${(r.ticker||'').replace(/'/g,'')}')">${nm40(r.name)}</td><td>${r.direction?`<span class="tag ${r.direction==='BULL'?'bull':'bear'}">${r.direction}</span>`:''}</td><td>${r.market||''}</td>
    <td>${r.first_seen||'—'}</td><td>${r.ready_date||'—'}</td><td>${r.triggered_date||'—'}</td>
    <td><b style="color:${_stcol(r.outcome)}">${r.outcome||''}</b></td><td>${r.outcome_date||'—'}</td>
    <td style="color:${(r.return_pct||0)>=0?'var(--bull)':'var(--bear)'}">${r.return_pct!=null?(r.return_pct>0?'+':'')+r.return_pct+'%':'—'}</td>
    <td>${r.quality!=null?r.quality:''}</td><td>${r.rr!=null?(+r.rr).toFixed(1):''}</td><td>${(r.timeframe||'').replace('daily-','D')}</td><td>${r.sector||''}</td><td><b>${disp(r.ticker)}</b></td></tr>`).join("")
    ||`<tr><td colspan="14" class="empty">No squeeze history yet.</td></tr>`;
}
// Wire the sortable Squeeze-History headers (mirrors the Performance th[data-pf] wiring).
document.querySelectorAll("th[data-sqh]").forEach(th=>th.onclick=()=>sqhSort(th.dataset.sqh));
// Repaint on any sidebar change. "input" covers typing in a range; the msel wrapper dispatches
// "input" on the underlying <select> too (see _mselBuild), so one listener serves both kinds.
SQH_FILTER_IDS().forEach(id=>{const el=$(id);if(el)el.addEventListener("input",()=>paintSqueezeHist());});
// ── Fees (Admin) (user 2026-07-18) — management + performance fees ─────────────────────────────────────
// Two selectable periods (user 2026-07-31, P-05): "Last month" (billed) + "This month (so far)"; each
// shows the fee example AND the underlying transactions (trade_log) that explain the realised profit.
let FEES=null, FEES_PER='this';
function renderFees(ev){
  const btn=ev&&ev.target?ev.target:null, was=btn?btn.textContent:"";
  if(btn){btn.disabled=true;btn.textContent="⏳ Data loading…";}
  FEES=null;
  const ex=$("fees-example"), tx=$("fees-txns"), title=$("fees-txn-title");
  if(ex)ex.innerHTML='<p class="refreshing" style="font-size:12px;padding:8px 0">⏳ Data loading…</p>';
  if(tx)tx.innerHTML='<p class="refreshing" style="font-size:12px;padding:8px 0">⏳ Data loading…</p>';
  if(title)title.innerHTML='<span class="refreshing">⏳ Data loading…</span>';
  fetch("/api/fees",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    FEES=j||{};
    // Seed the AUM box from the REAL account equity when available (user 2026-08-02, P-12), so the
    // management fee isn't implied to be a tidy £100 on a round £10,000. Seed ONCE so a manual edit sticks.
    const aumEl=$("fees-aum");
    if(aumEl&&FEES.real_aum!=null&&!aumEl.dataset.seeded){aumEl.value=Math.round(FEES.real_aum);aumEl.dataset.seeded="1";}
    const hint=$("fees-aum-hint");
    if(hint)hint.textContent=(FEES.real_aum!=null)?`live IG account value${FEES.aum_currency?' ('+FEES.aum_currency+')':''}`:`no live balance — enter your account value`;
    // Period toggle buttons show the actual YEAR + MONTH (user 2026-08-02, P-06), e.g. "July 2026".
    const pb=document.querySelector('#fees-period .fees-per[data-per="prev"]');   // two months ago (2026-08-07, ChangeRequest P-09)
    const lb=document.querySelector('#fees-period .fees-per[data-per="last"]');
    const tb=document.querySelector('#fees-period .fees-per[data-per="this"]');
    if(pb&&FEES.prev_month&&FEES.prev_month.label)pb.textContent=FEES.prev_month.label;
    if(lb&&FEES.last_month&&FEES.last_month.label)lb.textContent=FEES.last_month.label;
    if(tb&&FEES.this_month&&FEES.this_month.label)tb.textContent=FEES.this_month.label;
    paintFees();
  }).catch(()=>{FEES={note:"Could not load fee data or IG transaction history."};paintFees();})
    .finally(()=>{if(btn){btn.disabled=false;btn.className="btn";btn.textContent=was;}});
}
function feesPeriod(p){
  FEES_PER=p;
  document.querySelectorAll('#fees-period .fees-per').forEach(b=>b.classList.toggle('on', b.dataset.per===p));
  paintFees();
}
function _feesGbp(v){return `£${(v||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;}
function paintFees(){
  const box=$("fees-example"); if(!box)return;
  // Three periods (2026-08-07, ChangeRequest P-09): 'prev' (two months ago), 'last', 'this' (so far).
  const f=FEES||{}, seg=({prev:f.prev_month,last:f.last_month,this:f.this_month}[FEES_PER])||null;
  const warning=f.data_warning?`<div class="card" style="border-color:#d29922;background:color-mix(in srgb,#d29922 12%,transparent);color:var(--fg);font-size:12px;padding:8px 10px;margin:0 0 8px">⚠️ <b>IG data warning:</b> ${_esc(f.data_warning)}</div>`:'';
  if(f.note&&!seg){
    box.innerHTML=warning+`<p style="color:var(--bear);font-size:12px;padding:8px 0">${_esc(f.note)}</p>`;
    const tx=$("fees-txns");if(tx)tx.innerHTML=`<p style="color:var(--bear);font-size:12px;padding:8px 0">${_esc(f.note)}</p>`;
    return;
  }
  const aum=+(($("fees-aum")||{}).value||0)||10000;
  // Rates are PER ANNUM (user 2026-08-02, P-06 — fees were 12× too high). The MONTHLY charge = rate ÷ 12.
  const mgmtRate=(f.mgmt_pct!=null?f.mgmt_pct:1), perfRate=(f.perf_pct!=null?f.perf_pct:10);
  const mgmtGross=aum*mgmtRate/100/12;
  const profit=seg?seg.pnl:0, perfGross=profit>0?profit*perfRate/100/12:0;
  // Per-user fee discount (user 2026-08-02, P-20) — reduces each fee by its discount % when active today.
  const disc=(f.discount||{}), dm=+(disc.mgmt_pct||0), dp=+(disc.perf_pct||0), hasDisc=(disc.active&&(dm>0||dp>0));
  const mgmt=mgmtGross*(1-dm/100), perf=perfGross*(1-dp/100);
  const isThis=(FEES_PER==='this');
  const gbp=_feesGbp;
  const _perFallback={prev:'two months ago',last:'last month',this:'this month (so far)'}[FEES_PER]||'last month';
  const ti=$("fees-title"); if(ti)ti.textContent=`Worked example — ${seg?seg.label:_perFallback}`;
  const row=(k,v,note)=>`<tr><td>${k}</td><td style="text-align:right"><b>${v}</b></td><td class="muted">${note||''}</td></tr>`;
  const isIg=(seg&&seg.source==='ig');
  const colr=v=>`<span style="color:${v>=0?'var(--bull)':'var(--bear)'}">${gbp(v)}</span>`;
  // When sourced from real IG history (P-06), show the P&L breakdown: trading P&L, IG charges, net.
  const pnlRows = isIg
    ? row(`Realised trading P&L`, colr(seg.trade_pnl||0), `${seg.trades} closed trades (net of spread)`)+
      row(`IG charges`, colr(seg.charges_total||0), `overnight funding / interest / fees${seg.charges&&seg.charges.length?` · ${seg.charges.length} item${seg.charges.length>1?'s':''}`:''}`)+
      row(`Net realised profit`, `<b>${colr(profit)}</b>`, `trading P&L + IG charges — the fee basis`)
    : row(`Realised profit`, colr(profit), isThis?`this month's total_pnl to date`:`${_perFallback}'s total_pnl`);
  box.innerHTML=warning+`<table><thead><tr><th>Item</th><th style="text-align:right">Amount</th><th>Basis</th></tr></thead><tbody>`+
    row(`Period`, seg?seg.label:'—', seg?`${seg.trades} trades · ${seg.wins} win / ${seg.losses} loss${isIg?' · from your IG history':''}`:'no data recorded')+
    pnlRows+
    row(`Management fee (${mgmtRate}% p.a.)`, gbp(mgmt), dm>0?`(${mgmtRate}% ÷ 12) × AUM ${gbp(aum)}, less ${dm}% discount`:`(${mgmtRate}% ÷ 12) × AUM ${gbp(aum)}`)+
    row(`Performance fee (${perfRate}% p.a.)`, gbp(perf), profit>0?(dp>0?`(${perfRate}% ÷ 12) × profit, less ${dp}% discount`:`(${perfRate}% ÷ 12) × profit`):`0 — no profit ${isThis?'yet this month':'this month'}`)+
    (hasDisc?row(`Discount applied`, `−${gbp((mgmtGross-mgmt)+(perfGross-perf))}`, `${dm>0?`mgmt −${dm}%`:''}${dm>0&&dp>0?' · ':''}${dp>0?`perf −${dp}%`:''}${(disc.start||disc.end)?` · ${disc.start||'—'}→${disc.end||'—'}`:''}`):'')+
    `<tr style="border-top:2px solid var(--line)"><td><b>${isThis?'Fees so far':'Total fees'}</b></td><td style="text-align:right"><b>${gbp(mgmt+perf)}</b></td><td class="muted">management + performance${isThis?' — accrues until month end':''}${hasDisc?' (after discount)':''}</td></tr>`+
    `</tbody></table>`;
  paintFeesTxns(seg);
}
function paintFeesTxns(seg){
  const box=$("fees-txns"); if(!box)return;
  const ti=$("fees-txn-title");
  const txns=(seg&&seg.txns)||[], charges=(seg&&seg.charges)||[];
  const aggregate=((document.querySelector('input[name="fees-txn-view"]:checked')||{}).value==='agg');
  // API page order is not guaranteed; render by the actual close event, newest first.
  let viewTxns=txns.slice().sort((a,b)=>{
    const ad=String(a.closed_at||a.opened_at||""), bd=String(b.closed_at||b.opened_at||"");
    return bd.localeCompare(ad)||String(a.ticker||"").localeCompare(String(b.ticker||""));
  });
  if(aggregate){
    const grouped={};
    txns.forEach(t=>{const key=[t.ticker,t.direction].join('|');
      const a=grouped[key]||(grouped[key]=Object.assign({},t,{size:0,pnl:0,_openW:0,_closeW:0,_weight:0,_n:0}));
      const sz=+t.size||0;a.size+=sz;a.pnl+=(+t.pnl||0);a._openW+=(+t.open||0)*sz;
      a._closeW+=(+t.close||0)*sz;a._weight+=sz;a._n++;
      if(t.opened_at&&(!a.opened_at||t.opened_at<a.opened_at))a.opened_at=t.opened_at;
      if(t.closed_at&&(!a.closed_at||t.closed_at>a.closed_at))a.closed_at=t.closed_at;});
    viewTxns=Object.values(grouped).map(a=>{a.open=a._weight?+(a._openW/a._weight).toFixed(4):a.open;
      a.close=a._weight?+(a._closeW/a._weight).toFixed(4):a.close;a.size=+a.size.toFixed(4);a.pnl=+a.pnl.toFixed(2);
      a.pnl_pct=(a.open&&a.close!=null)?+(((a.direction==='SELL'?a.open-a.close:a.close-a.open)/a.open)*100).toFixed(2):null;return a;});
    viewTxns.sort((a,b)=>String(b.closed_at||b.opened_at||"").localeCompare(String(a.closed_at||a.opened_at||""))||String(a.ticker||"").localeCompare(String(b.ticker||"")));
  }
  if(ti)ti.textContent=`Transactions — ${seg?seg.label:'—'}`;
  const gbp=_feesGbp;
  const fdate=s=>{if(!s)return '';const d=new Date(s);return isNaN(d)?String(s).slice(0,19).replace('T',' '):d.toLocaleDateString(undefined,{day:'2-digit',month:'short',year:'numeric'})+' '+d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'});};
  const num=(v,dp)=>v==null?'—':(+v).toLocaleString(undefined,{minimumFractionDigits:dp,maximumFractionDigits:dp});
  let html=FEES&&FEES.data_warning?`<div class="card" style="border-color:#d29922;background:color-mix(in srgb,#d29922 12%,transparent);color:var(--fg);font-size:12px;padding:8px 10px;margin:0 0 8px">⚠️ <b>IG data warning:</b> ${_esc(FEES.data_warning)}</div>`:'';
  if(!viewTxns.length){
    const sourceNote=seg&&seg.source==='app_fallback'?'IG history returned no trades during refresh; showing the application trade ledger instead.':'No closed trades were returned for this period.';
    html+=`<p class="muted" style="font-size:12px;padding:6px 0">${sourceNote}</p>`;
  }else{
    let rows='', tot=0;
    viewTxns.forEach((t,i)=>{
      tot+=(t.pnl||0);
      const col=(t.pnl||0)>=0?'var(--bull)':'var(--bear)';
      const market=t.market||((DATA||[]).find(r=>r.ticker===t.ticker||r.name===t.ticker)||{}).market||'—';
      rows+=`<tr>`+
        `<td class="muted" style="text-align:right">${i+1}</td>`+
        `<td>${t.ticker||''}${aggregate&&t._n>1?` <span class="muted" style="font-size:11px">×${t._n}</span>`:''}</td>`+
        `<td>${market}</td>`+
        `<td><span style="color:${t.direction==='BUY'?'var(--bull)':'var(--bear)'}">${t.direction||''}</span></td>`+
        `<td style="text-align:right">${num(t.size,2)}</td>`+
        `<td style="text-align:right">${num(t.open,2)}</td>`+
        `<td style="text-align:right">${num(t.close,2)}</td>`+
        `<td style="text-align:right;color:${col}"><b>${gbp(t.pnl)}</b></td>`+
        `<td style="text-align:right;color:${col}">${t.pnl_pct==null?'—':(t.pnl_pct>=0?'+':'')+(+t.pnl_pct).toFixed(2)+'%'}</td>`+
        `<td>${fdate(t.opened_at)}</td>`+
        `<td>${fdate(t.closed_at)}</td>`+
        `</tr>`;
    });
    html+=`<table><thead><tr><th style="text-align:right">#</th><th>Instrument</th><th>Market</th><th>Dir</th><th style="text-align:right">Size</th><th style="text-align:right">Open</th><th style="text-align:right">Close</th><th style="text-align:right">P&amp;L</th><th style="text-align:right">P&amp;L %</th><th>Opened</th><th>Closed date/time</th></tr></thead><tbody>`+
      rows+
      `<tr style="border-top:2px solid var(--line)"><td></td><td colspan="6"><b>Total trading P&amp;L (${txns.length} trades${aggregate?` · ${viewTxns.length} rows`:''})</b></td><td style="text-align:right;color:${tot>=0?'var(--bull)':'var(--bear)'}"><b>${gbp(tot)}</b></td><td colspan="3"></td></tr>`+
      `</tbody></table>`;
  }
  // IG charges behind the period (user 2026-08-02, P-06) — overnight funding / interest / fees that also
  // hit the net P&L, straight from the account's transaction history.
  if(charges.length){
    let crows='', ctot=0;
    charges.forEach((c,i)=>{ctot+=(c.pnl||0);
      crows+=`<tr><td class="muted" style="text-align:right">${i+1}</td><td>${(c.instrument||'').replace(/</g,'&lt;')}</td><td class="muted">${c.type||''}</td><td>${fdate(c.date)}</td><td style="text-align:right;color:${(c.pnl||0)>=0?'var(--bull)':'var(--bear)'}">${gbp(c.pnl)}</td></tr>`;});
    html+=`<h3 class="sec" style="font-size:14px">IG charges — ${seg.label}</h3>`+
      `<p class="muted" style="font-size:11.5px;margin:2px 0 6px">Overnight funding, interest and fees IG applied in this period — these reduce the net realised profit above.</p>`+
      `<div class="tablewrap fees-charges-wrap"><table class="fees-charges"><thead><tr><th style="text-align:right">#</th><th>Charge</th><th>Type</th><th>Date</th><th style="text-align:right">Amount</th></tr></thead><tbody>`+
      crows+
      `<tr style="border-top:2px solid var(--line)"><td></td><td colspan="3"><b>Total IG charges (${charges.length})</b></td><td style="text-align:right;color:${ctot>=0?'var(--bull)':'var(--bear)'}"><b>${gbp(ctot)}</b></td></tr>`+
      `</tbody></table></div>`;
  }
  box.innerHTML=html;
}
// Monthly growth of the compounded wallet (user 2026-07-18, P-01) — on the Summary sub-tab, full width.
// Compounds a 2%-of-wallet stake across the date-filtered population in trigger-date order, then shows the
// month-on-month %. Shown to EVERYONE incl. logged-out visitors (user 2026-07-20) — it sells the product;
// /api/performance is public, so the data is available without login.
// "2026-01" → "2026 January" (user 2026-08-01) — clearer than "26-01".
const _MONTH_NAMES=['January','February','March','April','May','June','July','August','September','October','November','December'];
function _monLabel(ym){if(!ym||ym.length<7)return ym||'';const y=ym.slice(0,4),mo=+ym.slice(5,7);return y+' '+(_MONTH_NAMES[mo-1]||ym.slice(5,7));}
function _pfMonthly(rows){
  const div=$("pf-monthly"); if(!div)return;
  const rs=(rows||[]).filter(r=>r.perf!=null&&r.trig_date).slice().sort((a,b)=>(a.trig_date<b.trig_date?-1:a.trig_date>b.trig_date?1:0));
  const byMonth={}; let w=WINNERS_WALLET;
  rs.forEach(r=>{w+=w*WINNERS_STAKE*r.perf/100; byMonth[r.trig_date.slice(0,7)]=w;});
  const months=Object.keys(byMonth).sort(); let prev=WINNERS_WALLET;
  const mg=months.map(m=>{const end=byMonth[m], g=prev>0?(end-prev)/prev*100:0; prev=end; return {m,g};});
  if(!mg.length){div.innerHTML="";return;}
  const mx=Math.max(1,...mg.map(x=>Math.abs(x.g)));
  const msel=setOf("pff_month");
  // Each bar is click-to-filter by month (user 2026-08-01) — data-fk/data-fv drive the shared chart-click
  // handler, exactly like the Month bar chart, so clicking a month narrows every figure to it.
  div.innerHTML=`<div class="vizbox" style="max-width:none;width:100%"><h5>Monthly growth of the compounded wallet (%)${msel&&msel.size?` <span class="afilt clk" data-fk="pff_month" data-fv="" title="clear filter">▶ ${msel.size} ✕</span>`:''}</h5>
    <div style="display:flex;align-items:flex-end;gap:4px;height:205px;flex:none;padding-top:6px;width:100%">`+
    mg.map(x=>{const on=msel&&msel.has(x.m);
      return `<div class="clk" data-fk="pff_month" data-fv="${x.m}" title="${_monLabel(x.m)}: ${x.g>=0?'+':''}${x.g.toFixed(1)}% — click to filter" style="flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;cursor:pointer;${on?'background:color-mix(in srgb,var(--accent) 12%,transparent);border-radius:4px':''}">
      <span style="font-size:10px;font-weight:600;color:${x.g>=0?'var(--bull)':'var(--bear)'}">${on?'● ':''}${x.g>=0?'+':''}${x.g.toFixed(0)}%</span>
      <div style="width:90%;max-width:68px;background:${x.g>=0?'var(--bull)':'var(--bear)'};height:${Math.max(2,Math.abs(x.g)/mx*138)}px;border-radius:3px 3px 0 0;opacity:${msel&&msel.size&&!on?0.45:1}"></div>
      <span style="font-size:9px;color:var(--muted);margin-top:3px;line-height:1.1;text-align:center">${_monLabel(x.m)}</span>
    </div>`;}).join("")+`</div></div>`;
}
// Wallet model on Results (user 2026-07-26, P-05 L98/L271) — the SAME formula as the winners ledger
// (paintOrdersPerf): chronological by trigger date, stake = Max-position-size % of the RUNNING wallet
// (compounding), net = stake × return%, with an optional Max-open-positions cap. Applied to the recorded
// triggers shown on Results. Attaches _stake / _net / _cum to each row (null for rows without a return).
function _pfAddDays(d,n){if(!d)return'9999-99-99';const t=new Date(d+'T00:00:00Z');if(isNaN(t))return'9999-99-99';t.setUTCDate(t.getUTCDate()+(+n||0));return t.toISOString().slice(0,10);}
function _pfWalletLedger(sel){
  const wallet=Math.max(1,+(($("pfw-wallet")||{}).value)||1000);
  const stakeFrac=(Math.max(0.01,+(($("pfw-stake")||{}).value)||2))/100;
  const requestedMax=Math.max(0,Math.floor(+(($("pfw-maxopen")||{}).value)||0));
  const maxopen=requestedMax>0?Math.min(requestedMax,_fundedMaxOpen(stakeFrac)):_fundedMaxOpen(stakeFrac);
  sel.forEach(r=>{r._stake=r._net=r._cum=r._open=null;});
  const wp=sel.filter(r=>r.perf!=null);
  const seq=wp.slice().sort((a,b)=>(a.trig_date||'').localeCompare(b.trig_date||'')||(a.ticker||'').localeCompare(b.ticker||''));
  const exitOf=r=>_pfExitDate(r,false);   // when the slot frees - one shared rule (2026-08-14, P-03)
  let w=wallet,reserved=0,taken=0,skipped=0,netTotal=0;const open=[];
  const settle=td=>{open.sort((a,b)=>a.exit.localeCompare(b.exit));
    while(open.length&&open[0].exit<=td){const x=open.shift();w+=x.net;reserved=Math.max(0,reserved-x.margin);}};
  for(const r of seq){const td=r.trig_date||'';settle(td);
    const stake=w*stakeFrac,lev=Math.max(1,+(levOf(r)||1)),margin=stake/lev,available=Math.max(0,w-reserved);
    if(stake<MIN_TRADE||(maxopen>0&&open.length>=maxopen)||margin>available+1e-9){
      // Record the book state even when the trade is NOT funded. Leaving Equity and Open blank on every
      // skipped row made the columns unreadable -- on a typical run more than half the rows are skipped,
      // and they are overwhelmingly "book full" (user 2026-08-15: "many EQUITY values ... have no setting
      // e.g. STOPPED and OPEN transactions"). The wallet really was worth `w` at that moment and the book
      // really did hold `open.length` positions, which is precisely what explains the skip. Stake and Net
      // stay blank because nothing was staked and nothing was won or lost.
      r._cum=w; r._open=open.length; skipped++; continue;}
    const net=stake*r.perf/100;reserved+=margin;open.push({exit:exitOf(r),margin,net});
    taken++;netTotal+=net;r._open=open.length;r._stake=stake;r._net=net;r._cum=w+net;}
  settle('9999-99-99');
  return {wallet,endWallet:w,taken,skipped,netTotal,maxopen,requestedMax};
}
// The Back Test headline is valid only when it reconciles to the exact rows carrying its evidence.
// Keep this pure so a regression test can exercise it without the page DOM.
function pfLedgerReconciliation(rows,led){
  const evidence=(rows||[]).filter(r=>r&&r.perf!=null);
  const funded=evidence.filter(r=>r._stake!=null), skipped=evidence.filter(r=>r._stake==null);
  const netTotal=funded.reduce((sum,r)=>sum+(Number(r._net)||0),0);
  const expectedEnd=(Number(led&&led.wallet)||0)+netTotal;
  const epsilon=.005;
  const ok=!!led&&funded.length===led.taken&&skipped.length===led.skipped&&
    Math.abs(netTotal-(Number(led.netTotal)||0))<epsilon&&Math.abs(expectedEnd-(Number(led.endWallet)||0))<epsilon;
  return {ok,evidence:evidence.length,funded:funded.length,skipped:skipped.length,netTotal,expectedEnd};
}
function _pfSavedScope(kind){
  const keys=kind==="market"?["pof_market","f_mkt"]:["pof_sector","f_sec"];
  for(const key of keys){
    const raw=USER_FILTERS&&USER_FILTERS[key];if(raw==null||raw==="")continue;
    const values=(Array.isArray(raw)?raw:String(raw).split(SEP)).map(v=>String(v).trim()).filter(Boolean);
    if(values.length)return [...new Set(values)];
  }
  return [];
}
function _pfMatchesCurrentConfig(r){
  const floor=(key,value)=>{const n=Number(MY_LIMITS&&MY_LIMITS[key]);return !isFinite(n)||n<=0||(value!=null&&isFinite(+value)&&+value>=n);};
  if(!floor("min_risk_reward",r.rr)||!floor("min_quality",r.quality)||!floor("min_volume_score",r.volume_score)||!floor("min_rvol",r.rvol))return false;
  if(+MY_LIMITS.require_above_vwap&&r.above_vwap!==true)return false;
  if(+MY_LIMITS.require_atr_expanding&&r.atr_expanding!==true)return false;
  const minValue=+MY_LIMITS.min_instrument_value||0,maxValue=+MY_LIMITS.max_instrument_value||0;
  if((minValue>0||maxValue>0)&&(r.mcap==null||!isFinite(+r.mcap)))return false;
  if(minValue>0&&+r.mcap<minValue)return false;
  if(maxValue>0&&+r.mcap>maxValue)return false;
  const markets=_pfSavedScope("market"),sectors=_pfSavedScope("sector");
  if(markets.length&&!markets.includes(String(r.market||"")))return false;
  if(sectors.length&&!sectors.includes(String(r.sector||"")))return false;
  return tradeVisible(r);
}
function _renderPerformance(){
  // Back Test applies the complete saved configuration. A configured floor requires a scored value,
  // matching the annual optimiser; otherwise an unscored row could enter a replay it never qualified for.
  const _vsFloor=(typeof num==='function')?num(MY_LIMITS.min_volume_score):(+MY_LIMITS.min_volume_score||null);
  PF_VS_FLOOR=(_vsFloor!=null&&_vsFloor>0)?_vsFloor:0;
  let all=(PERF_DATA||[]).filter(r=>pfDateOk(r.trig_date));   // date-window filter (P-01) drives everything below
  all=all.filter(_pfMatchesCurrentConfig);
  const _wlCol=k=>k==="Win"?"var(--bull)":k==="Loss"?"var(--bear)":"#d29922";
  // Cross-filter (user 2026-07-24, P-04 L107/L109): each chart counts the population filtered by the OTHER
  // charts' selections (plus the Location button + search), so selecting e.g. Location=US narrows the
  // Market/Sector/… charts to US — while each chart still shows its OWN full set so the selection can be
  // changed (clear via each chart's ✕). Search term is computed here (moved up from below) so the charts
  // react to it too.
  const _pfq=(($("pf-search")||{}).value||"").trim().toLowerCase();
  const _pfDims=[["pff_market",r=>r.market],["pff_location",r=>locName(r.location)],["pff_sector",r=>r.sector],
                 ["pff_direction",r=>r.direction],["pff_status",r=>r.state],["pff_wl",r=>_pfwl(r)],
                 ["pff_daysopen",r=>_doBand(r)],   // Days-open range chart (user 2026-08-01)
                 ["pff_month",r=>(r.trig_date||"").slice(0,7)],["pff_mweek",r=>_mw(r.trig_date)]];   // date-filtered screen → Month + Month-Week (P-03 L24)
  const _cross=exceptKey=>all.filter(r=>
    _pfDims.every(([id,fn])=>id===exceptKey||inSet(id,fn(r)))
    &&(!PF_LOC_FILTER||locName(r.location)===PF_LOC_FILTER)
    &&(!_pfq||((r.name||'')+' '+disp(r.ticker)+' '+(r.ticker||'')).toLowerCase().includes(_pfq)));
  const byX=(fn,exceptKey)=>_cross(exceptKey).reduce((m,r)=>{const v=(typeof fn==='function'?fn(r):r[fn])||'—';m[v]=(m[v]||0)+1;return m;},{});
  // L31 (user 2026-07-25): show the selection's IMPACT on the other charts WITHOUT hiding bars — keep every
  // category present in the full (date-windowed) data as a bar; its value is the count within the current
  // selection (0 → a dimmed stub, not removed). Reconciles L31 with the L109 cross-filter: bars stay put,
  // the numbers move. (barChart still caps the display at its top 8 by count, as everywhere.)
  const byXFull=(fn,exceptKey)=>{const sel=byX(fn,exceptKey);
    all.forEach(r=>{const v=(typeof fn==='function'?fn(r):r[fn])||'—'; if(!(v in sel))sel[v]=0;}); return sel;};
  // Average direction-aware return per group, over the same cross-filtered rows (user 2026-07-26, P-05
  // L281) — drives the Market & Location charts' colour (green good / red bad) and their bar order.
  const avgX=(fn,exceptKey)=>{const acc={};_cross(exceptKey).forEach(r=>{if(r.perf==null)return;const v=(typeof fn==='function'?fn(r):r[fn])||'—';(acc[v]=acc[v]||[]).push(r.perf);});
    const out={};for(const k in acc)out[k]=acc[k].reduce((a,b)=>a+b,0)/acc[k].length;return out;};
  $("pf-viz").innerHTML=LIMITED?"":
    `<div class="vizsector">`+barChart("Location",byXFull(r=>locName(r.location),"pff_location"),"pff_location",null,false,{metric:avgX(r=>locName(r.location),"pff_location")})+`</div>`+   /* Location first (P-05 L182); colour+order by avg return (P-05 L281) */
    `<div class="vizsector">`+barChart("Market",byXFull("market","pff_market"),"pff_market",null,false,{metric:avgX("market","pff_market")})+`</div>`+
    `<div class="vizsector">`+pieChart("Sector",byXFull("sector","pff_sector"),"pff_sector")+`</div>`+
    `<div class="vizsector">`+barChart("Direction",byXFull("direction","pff_direction"),"pff_direction",k=>k==="BULL"?"var(--bull)":"var(--bear)")+`</div>`+   /* P-10 */
    `<div class="vizsector">`+barChart("Outcome",byXFull("state","pff_status"),"pff_status",_stcol)+`</div>`+
    `<div class="vizsector">`+barChart("Win / Loss",byXFull(_pfwl,"pff_wl"),"pff_wl",_wlCol)+`</div>`+         /* wins / losses / break-even (P-10) */
    `<div class="vizsector">`+barChart("Days Open",byXFull(_doBand,"pff_daysopen"),"pff_daysopen")+`</div>`+   /* days-open range distribution (user 2026-08-01) */
    `<div class="vizsector">`+barChart("Month",byXFull(r=>(r.trig_date||"").slice(0,7),"pff_month"),"pff_month",null,true)+`</div>`;   /* Month-Week removed from the Back Test strip (user 2026-08-01) */
  packViz("pf-viz");   // P-15
  // Location quick-filter buttons (user 2026-07-18/20): one per location, plus "All". The SELECTED one is
  // green. They drive the summary table AND the monthly-growth chart (both via `sel`, below).
  const _locs=[...new Set(all.map(r=>locName(r.location)).filter(v=>v&&v!=="—"))].sort();
  const _lb=$("pf-loc-btns");
  const _locBtn=(label,val,on)=>`<button class="subpill${on?' active':''}" style="${on?'background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600':''}" onclick="pfLocFilter('${val.replace(/'/g,"\\'")}')">${label}</button>`;   /* grey inactive, accent when active (user 2026-07-25, P-02 L79/L252) */
  if(_lb)_lb.innerHTML=LIMITED?"":([_locBtn('All','',!PF_LOC_FILTER)].concat(_locs.map(l=>_locBtn(l,l,PF_LOC_FILTER===l)))).join("");
  {const _ll=$("pf-loc-lbl"); if(_ll)_ll.style.display=(LIMITED||!_locs.length)?"none":"";}   // hide the "Location:" label when there are no buttons (user 2026-07-24, P-04 L82)
  // Name/ticker search (user 2026-07-17, P-17): _pfq is computed above (moved up for the charts). The
  // summary, Quality/R:R buckets and row count all reflect it — same contract as the other tabs.
  let sel=all.filter(r=>inSet("pff_sector",r.sector)&&inSet("pff_market",r.market)&&inSet("pff_status",r.state)
    &&inSet("pff_location",locName(r.location))&&inSet("pff_direction",r.direction)&&inSet("pff_wl",_pfwl(r))
    &&inSet("pff_daysopen",_doBand(r))
    &&inSet("pff_month",(r.trig_date||"").slice(0,7))&&inSet("pff_mweek",_mw(r.trig_date)));
  if(PF_LOC_FILTER)sel=sel.filter(r=>locName(r.location)===PF_LOC_FILTER);   // Summary-title quick location filter (P-10)
  if(_pfq)sel=sel.filter(r=>((r.name||'')+' '+disp(r.ticker)+' '+(r.ticker||'')).toLowerCase().includes(_pfq));
  _renderPfSummary(sel);
  _pfMonthly(sel);   // monthly-growth chart reacts to the location filter too (user 2026-07-20)
  _pfSettingsCard(sel,all);   // "Settings used" card, Summary sub-tab only (user 2026-07-24, P-02)
  // Top Quality/R:R buckets by average return across the (filtered) recorded triggers.
  const N=innerWidth>=900?4:3;   // never more than 4 cards; 3 on a mini iPad (<900px) (P-25)
  // Buckets now include a VolumeScore band (user 2026-08-01): Quality (10-wide) x R:R (1-wide) x Vol band
  // (8+ / 4–7 / 0–3 / — unscored), ranked by average return. So the "best combination" reflects volume too.
  const _vsB=vs=>vs==null?{k:"—",lo:null,hi:null}:vs>=8?{k:"Vol 8+",lo:8,hi:99}:vs>=4?{k:"Vol 4–7",lo:4,hi:7}:{k:"Vol 0–3",lo:0,hi:3};
  const buckets={};
  sel.forEach(r=>{if(r.quality==null||r.rr==null||r.perf==null)return;const qb=Math.floor(r.quality/10)*10,rb=Math.floor(+r.rr);const vb=_vsB(r.volume_score);
    const key=qb+"|"+rb+"|"+vb.k;
    const o=(buckets[key]=buckets[key]||{qb,rb,vb:vb.k,vlo:vb.lo,vhi:vb.hi,n:0,sum:0});o.n++;o.sum+=r.perf;});
  const combos=Object.values(buckets).filter(b=>b.n>=3).map(b=>({...b,avg:b.sum/b.n})).sort((a,b)=>b.avg-a.avg).slice(0,N);
  $("pf-combos").classList.remove("sqh-loading");
  $("pf-combos").innerHTML=LIMITED?`<p class="muted">🔒 <a href="#" onclick="showLogin();return false">Log in</a> to see the best Quality / R:R / Volume combinations.</p>`
    :combos.length?combos.map((b,i)=>{const on=PF_COMBO&&PF_COMBO.qb===b.qb&&PF_COMBO.rb===b.rb&&PF_COMBO.vb===b.vb;
      return `<div class="fcard clk${on?' active':''}" style="cursor:pointer${on?';border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)':''}" onclick="pfComboFilter(${b.qb},${b.rb},${b.vlo==null?'null':b.vlo},${b.vhi==null?'null':b.vhi},'${b.vb}')" title="Click to filter the table below to Quality ${b.qb}–${b.qb+10} · R:R ${b.rb.toFixed(1)}–${(b.rb+1).toFixed(1)} · ${b.vb}${on?' — click again to clear':''}"><div class="ic">${on?'✓':i+1}</div>
      <h3>Quality ${b.qb}–${b.qb+10} · R:R ${b.rb.toFixed(1)}–${(b.rb+1).toFixed(1)} · ${b.vb}</h3>
      <div class="body"><b style="color:${b.avg>=0?'var(--bull)':'var(--bear)'};font-size:18px">${b.avg>0?'+':''}${b.avg.toFixed(1)}%</b> average return across <b>${b.n}</b> triggers.${on?' <span style="color:var(--accent);font-weight:600">· filtering the table ✕</span>':''}
        <div style="margin-top:6px"><a href="#" onclick='event.stopPropagation();applyConfigFromReport(${JSON.stringify({min_quality:b.qb,min_risk_reward:b.rb,min_volume_score:b.vlo==null?0:b.vlo})});return false' style="font-size:11px;color:var(--accent)" title="Copy Quality ≥ ${b.qb}, R:R ≥ ${b.rb}, VolumeScore ≥ ${b.vlo==null?0:b.vlo} into your personal trading settings">⬇ copy to my settings</a></div></div></div>`;}).join("")
    :`<p class="muted">Not enough recorded triggers yet to rank Quality/R:R/Volume buckets (need at least 3 per bucket).</p>`;
  // Best-combination card click (user 2026-07-27, P-10 L292): filter the TABLE (and its count + wallet) to
  // the picked Quality/R:R/Volume band, while the cards above still show every bucket so you can re-pick.
  const selT = PF_COMBO ? sel.filter(_pfInCombo) : sel;
  // Wallet model (P-05 L98/L271) is always on; it needs strict trigger-date order internally to compound
  // correctly, but that no longer has to be the DISPLAY order. _pfWalletLedger sorts its own internal copy
  // chronologically regardless of selT's order, so it's safe to compute unconditionally and separately sort
  // the DISPLAYED rows by whichever column header the user clicked (2026-08-07, ChangeRequest P-09 — header
  // clicks used to update pfSortK/pfSortDir but the old hardcoded chronological branch below ignored them,
  // so "allow sort by column header" silently did nothing). Default stays Triggered ascending (pfSortK).
  const pfwOn=$("pfw-on")?$("pfw-on").checked:true;   // wallet model is always on now — the toggle was removed (user 2026-08-01)
  const led=_pfWalletLedger(selT);
  const rows=genSort(selT,pfSortK,pfSortDir);
  $("pf-table")&&$("pf-table").classList.toggle("wallet-on",pfwOn);
  if($("pfw-summary"))$("pfw-summary").innerHTML=led?`<b style="color:var(--fg)">£${Math.round(led.wallet).toLocaleString()}</b> → <b style="color:${led.endWallet>=led.wallet?'var(--bull)':'var(--bear)'}">£${Math.round(led.endWallet).toLocaleString()}</b> over ${led.taken.toLocaleString()} trade${led.taken===1?'':'s'} · ${led.requestedMax>0&&led.requestedMax!==led.maxopen?`requested ${led.requestedMax}, capped at ${led.maxopen}`:led.requestedMax>0?`cap ${led.maxopen}`:`Auto cap ${led.maxopen}`} open → ${led.skipped.toLocaleString()} skipped <span class="muted">· wallet computed oldest first (same model as “What separates the winners”); table sorted by your chosen column, Triggered ascending by default</span>`:"";
  const nT=selT.filter(r=>r.state==="TARGET").length, nS=selT.filter(r=>r.state==="STOPPED").length, nO=selT.filter(r=>r.state==="OPEN").length;
  $("pf-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${selT.length}</b> recorded triggers <span class="muted">(${nT} hit target · ${nS} stopped · ${nO} open)${selT.length!==all.length?` of ${all.length}`:''}${PF_COMBO?` · Quality ${PF_COMBO.qb}–${PF_COMBO.qb+10} · R:R ${PF_COMBO.rb.toFixed(1)}–${(PF_COMBO.rb+1).toFixed(1)} · ${PF_COMBO.vb} <a href="#" onclick="pfComboFilter(${PF_COMBO.qb},${PF_COMBO.rb},${PF_COMBO.vlo==null?'null':PF_COMBO.vlo},${PF_COMBO.vhi==null?'null':PF_COMBO.vhi},'${PF_COMBO.vb}');return false" style="color:var(--accent)">✕ clear</a>`:''}${PF_VS_FLOOR?` · <b style="color:var(--fg)">Volume Score ≥ ${PF_VS_FLOOR}</b> (your floor)`:''}${PERF_GEN?` · as at ${PERF_GEN}`:''}</span>`+(LIMITED?` <b style="color:#d29922">· <a href="#" onclick="showLogin();return false" style="color:#d29922;text-decoration:underline">log in to unlock the full data</a></b>`:"");
  _pfBacktestSettingsCard(selT,all,led,pfLedgerReconciliation(selT,led));
  // Row count withheld when logged out (user 2026-08-28: "remove the evidence tab and row count if user
  // not logged in"). The TAB itself stays public -- removing it was a misreading and was reported back as
  // a bug the same day.
  $("pftab-count").textContent=AUTH?`(${selT.length})`:"";
  // ...and the evidence table itself goes with it. The server already withholds every per-trade field
  // from a logged-out visitor (_public_perf_response sends dated returns only), so there is nothing to
  // put in these columns; drawing an obfuscated skeleton of 4,000 empty rows would be worse than saying
  // plainly that it needs a login. The monthly compounding chart above is unaffected and still public.
  const _pfWrap=$("pf-table")&&$("pf-table").closest(".tablewrap");
  if(!AUTH){
    if(_pfWrap)_pfWrap.style.display="none";
    $("pf-rows").innerHTML="";
    const _more=$("pf-showmore"); if(_more)_more.style.display="none";
    return;
  }
  if(_pfWrap)_pfWrap.style.display="";
  const visibleRows=rows.slice(0,PF_RENDER_LIMIT);   // summaries/wallet/counts above still use ALL selected rows
  $("pf-rows").innerHTML=visibleRows.map((r,__i)=>`<tr class="clk" onclick="${LIMITED?'showLogin()':`openDetailFrom('performance','${r.ticker}')`}">
    <td class="muted">${__i+1}</td>
    <td>${nm40(r.name)}</td>
    <td>${ob(r.direction?`<span class="tag ${r.direction==='BULL'?'bull':'bear'}">${r.direction}</span>`:'')}</td>
    <td>${ob(rvolCell(r.rvol))}</td><td>${_tickCross(r.above_vwap)}</td><td>${_tickCross(r.atr_expanding)}</td><td>${ob(volScoreCell(r.volume_score))}</td>
    <td>${ob(r.rr!=null?(+r.rr).toFixed(1):'')}</td><td>${ob(r.quality!=null?`<b style="color:${qcol(r.quality)}">${r.quality}</b>`:'')}</td>
    <td>${ob(`<b style="color:${_stcol(r.state)}">${r.state||''}</b>`)}</td>
    <!-- Market and Triggered stay visible logged-out (user 2026-07-17, P-26 and P-17b); the rest of the
         row is still obfuscated by ob(). /api/performance is public and already returns both. -->
    <td>${r.market||''}</td><td>${r.trig_date||'—'}</td>
    <td>${r.perf!=null?`<b style="color:${r.perf>=0?'var(--bull)':'var(--bear)'}">${r.perf>0?'+':''}${r.perf.toFixed(1)}%</b>`:'<span class="muted">—</span>'}</td>
    <td class="wcol muted">${r._stake!=null?'£'+Math.round(r._stake).toLocaleString():'—'}</td>
    <td class="wcol">${r._net!=null?`<span style="color:${r._net>=0?'var(--bull)':'var(--bear)'}">${r._net>=0?'+':'−'}£${Math.abs(r._net).toFixed(2)}</span>`:'—'}</td>
    <td class="wcol"><b>${r._cum!=null?'£'+Math.round(r._cum).toLocaleString():'—'}</b></td>
    <td class="wcol">${r._open!=null?r._open:'—'}</td>
    <td>${r.days_open!=null?r.days_open:'—'}</td>
    <td>${(()=>{let cd=r.exit_date?String(r.exit_date).slice(0,10):null;
      if(!cd&&(r.state==='TARGET'||r.state==='STOPPED')&&r.trig_date!=null&&r.days_open!=null){cd=_pfAddDays(r.trig_date,r.days_open);if(cd==='9999-99-99')cd=null;}
      return cd?cd:(r.state==='OPEN'?'<span class="muted">open</span>':'—');})()}</td>
    <td style="color:var(--bear)">${ob(f2(r.stop))}</td><td>${ob(f2(r.entry))}</td><td style="color:var(--bull)">${ob(f2(r.target))}</td>
    <td>${ob(f2(r.current_price))}</td>
    <td>${ob(_mcapFmt(r.mcap))}</td>
    <td>${r.sector||''}</td><td><b>${disp(r.ticker)}</b></td></tr>`).join("")+
    (rows.length>visibleRows.length?`<tr><td colspan="26" class="empty"><button class="subpill" onclick="pfShowMore()">Show ${Math.min(PF_RENDER_STEP,rows.length-visibleRows.length)} more</button> <span class="muted">Showing ${visibleRows.length.toLocaleString()} of ${rows.length.toLocaleString()} filtered triggers</span></td></tr>`:"")
    ||`<tr><td colspan="${pfwOn?24:20}" class="empty">No recorded triggers yet.</td></tr>`;
}
function _pfBacktestSettingsCard(sel,all,led,reconciliation){
  const card=$("pf-backtest-settings");if(!card)return;
  if(LIMITED){card.style.display="none";return;}
  const _num=v=>(typeof num==='function')?num(v):(v==null||v===''?null:+v);
  const list=id=>{const s=setOf(id);return s?[...s]:null;};
  const nice=a=>!a||!a.length?null:(a.length<=3?a.join(", "):a.length+" selected");
  const marketScope=chartSelection=>{
    const uni=(typeof uniq==='function')?uniq("market"):[];
    if(!uni.length)return chartSelection||"All configured markets";
    // Asked of tradeVisible, not re-derived from MARKETS_OFF/MARKETS_DISABLED. This helper had the rule
    // right all along; it was simply a THIRD copy of it, and the copies are what let the card scope and
    // the location line drift (user 2026-08-30).
    const removed=tradeExcludedValues("market",uni), _off=new Set(removed);
    const kept=uni.filter(m=>!_off.has(m));
    const configured=removed.length===0
      ?`all ${uni.length} configured markets`
      :(kept.length<=8?`${kept.join(", ")} (${kept.length} of ${uni.length} available)`:`${kept.length} configured markets (${kept.length} of ${uni.length} available)`);
    return chartSelection?`${chartSelection} within ${configured}`:configured;
  };
  const esc=v=>String(v).replace(/</g,"&lt;");
  const rr=_num(MY_LIMITS.min_risk_reward), ql=_num(MY_LIMITS.min_quality), vs=_num(MY_LIMITS.min_volume_score),rv=_num(MY_LIMITS.min_rvol);
  const chartMarket=nice(list("pff_market"));
  const savedMarkets=nice(_pfSavedScope("market")),savedSectors=nice(_pfSavedScope("sector"));
  const minValue=_num(MY_LIMITS.min_instrument_value),maxValue=_num(MY_LIMITS.max_instrument_value);
  const items=[
    ["R:R floor","≥ "+(rr!=null?rr:3)],
    ["Quality floor","≥ "+(ql!=null?ql:25)],
    ["Volume Score floor","≥ "+(vs!=null?vs:1)],
    ["RVOL floor",rv>0?"≥ "+rv:"Any"],
    ["Require above VWAP",+MY_LIMITS.require_above_vwap?"Yes":"No"],
    ["Require ATR expanding",+MY_LIMITS.require_atr_expanding?"Yes":"No"],
    ["Instrument value",minValue>0||maxValue>0?`${minValue>0?"≥ "+Math.round(minValue).toLocaleString():"no minimum"} · ${maxValue>0?"≤ "+Math.round(maxValue).toLocaleString():"no maximum"}`:"Any"],
    ["Date window",PF_WINDOW_LABEL||"all 12 months"],
    ["Location",PF_LOC_FILTER||nice(list("pff_location"))||_locScopeLabel()],
    ["Saved market scope",savedMarkets||"All"],
    ["Saved sector scope",savedSectors||"All"],
    ["Chart market filter",marketScope(chartMarket)],
    ["Wallet model",led?`£${Math.round(led.wallet).toLocaleString()}, ${(Math.max(0.01,+(($("pfw-stake")||{}).value)||2)).toFixed(1)}% position, ${led.requestedMax>0&&led.requestedMax!==led.maxopen?`requested ${led.requestedMax} capped to ${led.maxopen}`:led.requestedMax>0?`${led.maxopen} max open`:`Auto ${led.maxopen} max open`}`:"—"],
  ];
  const dirs=(TRADE_HIDE&&TRADE_HIDE.directions)||[];if(dirs.length)items.push(["Direction filter",dirs.join(", ")]);
  const opt=(lab,id)=>{const v=nice(list(id));if(v)items.push([lab,v]);};
  opt("Sector","pff_sector");opt("Outcome","pff_status");opt("Win/Loss","pff_wl");opt("Days open","pff_daysopen");opt("Month","pff_month");
  if(PF_COMBO)items.push(["Combo filter",`Quality ${PF_COMBO.qb}-${PF_COMBO.qb+10}, R:R ${PF_COMBO.rb.toFixed(1)}-${(PF_COMBO.rb+1).toFixed(1)}, ${PF_COMBO.vb}`]);
  const q=(($("pf-search")||{}).value||"").trim();if(q)items.push(["Search",'"'+q+'"']);
  const funded=sel.filter(r=>r._stake!=null&&r.perf!=null),wins=funded.filter(r=>r.perf>0).length,
        losses=funded.filter(r=>r.perf<0).length,breakEven=funded.length-wins-losses,
        modelReturn=led&&led.wallet?((led.endWallet/led.wallet)-1)*100:null;
  const returnText=modelReturn==null?"—":`${modelReturn>=0?"+":""}${modelReturn.toFixed(2)}%`;
  card.style.display="";
  const verified=!!(reconciliation&&reconciliation.ok);
  const fundedText=verified
    ?`<b>${led.taken}</b> trades · ${led.skipped} skipped by capacity`
    :`— · ${reconciliation?`${reconciliation.funded} evidence rows funded, ${reconciliation.skipped} skipped`:'replay unavailable'}`;
  card.innerHTML=`<div class="pf-setcard-h">Back Test summary ${verified?'<span style="color:var(--bull);font-size:11px">✓ reconciled to transaction evidence</span>':'<span style="color:var(--bear);font-size:11px">⚠ withheld — evidence mismatch</span>'}</div>
    <div class="pf-setcard-grid" style="margin-bottom:7px">
      <div class="pf-setcard-row"><span class="pf-setcard-k">Actual Win : Loss</span><span class="pf-setcard-v"><b>${verified?`${wins} : ${losses}`:'—'}</b>${verified&&breakEven?` · ${breakEven} break-even`:""}</span></div>
      <div class="pf-setcard-row"><span class="pf-setcard-k">Model return</span><span class="pf-setcard-v"><b style="color:${modelReturn>=0?'var(--bull)':'var(--bear)'}">${verified?returnText:'—'}</b>${verified?` · £${Math.round(led.endWallet).toLocaleString()} ending wallet`:''}</span></div>
      <div class="pf-setcard-row"><span class="pf-setcard-k">Funded</span><span class="pf-setcard-v">${fundedText}</span></div>
    </div><div class="pf-setcard-h">Back Test settings used</div><div class="pf-setcard-grid">`+
    items.map(([k,v])=>`<div class="pf-setcard-row"><span class="pf-setcard-k">${k}</span><span class="pf-setcard-v">${esc(v)}</span></div>`).join("")+
    `</div><div class="pf-setcard-row" style="border-top:1px solid var(--line);margin-top:4px;padding-top:4px"><span class="pf-setcard-k">Triggers</span><span class="pf-setcard-v"><b>${sel.length}</b>${sel.length!==all.length?` of ${all.length}`:""}</span></div>`;
}
// "Settings used" card (user 2026-07-24, P-02): a provenance card, left of the filters on the Summary
// sub-tab, stating the settings behind the monthly chart + summary table — the date window, location, the
// market scope, plus any chart cross-filters carried over from Results, and the resulting trigger count.
function _pfSettingsCard(sel,all){
  const card=$("pf-settings-card"); if(!card)return;
  const onSummary=$("pf-panel-summary")&&!$("pf-panel-summary").classList.contains("hidden");
  if(!onSummary||LIMITED){card.style.display="none";return;}
  const list=id=>{const s=setOf(id);return s?[...s]:null;};
  const nice=a=>!a||!a.length?null:(a.length<=3?a.join(", "):a.length+" selected");
  const marketScope=chartSelection=>{
    const uni=(typeof uniq==='function')?uniq("market"):[];
    if(!uni.length)return chartSelection||"All configured markets";
    // Asked of tradeVisible, not re-derived from MARKETS_OFF/MARKETS_DISABLED. This helper had the rule
    // right all along; it was simply a THIRD copy of it, and the copies are what let the card scope and
    // the location line drift (user 2026-08-30).
    const removed=tradeExcludedValues("market",uni), _off=new Set(removed);
    const kept=uni.filter(m=>!_off.has(m));
    const configured=removed.length===0
      ?`all ${uni.length} configured markets`
      :(kept.length<=8?`${kept.join(", ")} (${kept.length} of ${uni.length} available)`:`${kept.length} configured markets (${kept.length} of ${uni.length} available)`);
    return chartSelection?`${chartSelection} within ${configured}`:configured;
  };
  const esc=v=>String(v).replace(/</g,"&lt;");
  const chartMarket=nice(list("pff_market"));
  const items=[["Date window",PF_WINDOW_LABEL||"all 12 months"],
    ["Location",PF_LOC_FILTER||nice(list("pff_location"))||_locScopeLabel()],
    ["Market scope",marketScope(chartMarket)]];
  const opt=(lab,id)=>{const v=nice(list(id));if(v)items.push([lab,v]);};
  opt("Sector","pff_sector"); opt("Direction","pff_direction"); opt("Outcome","pff_status"); opt("Win/Loss","pff_wl");
  // Your CONFIGURED floors + filters that shape the back test (user 2026-08-01): R:R / Quality / Volume Score
  // from My trading limits, the Trading (Squeeze) direction filter, and the Markets (User) selection.
  const _num=v=>(typeof num==='function')?num(v):(v==null||v===''?null:+v);
  // ALWAYS show the three floors (user 2026-08-01) — fall back to the config baseline when the user
  // hasn't overridden them, so they never vanish just because MY_LIMITS wasn't loaded/set.
  const rr=_num(MY_LIMITS.min_risk_reward), ql=_num(MY_LIMITS.min_quality), vs=_num(MY_LIMITS.min_volume_score);
  items.push(["R:R floor","≥ "+(rr!=null?rr:3)]);
  items.push(["Quality floor","≥ "+(ql!=null?ql:25)]);
  items.push(["Volume Score floor","≥ "+(vs!=null?vs:1)]);
  const dirs=(TRADE_HIDE&&TRADE_HIDE.directions)||[];
  if(dirs.length===1)items.push(["Direction (yours)",dirs.join(", ")]);
  const q=(($("pf-search")||{}).value||"").trim(); if(q)items.push(["Search",'"'+q+'"']);
  card.style.display="";
  card.innerHTML=`<div class="pf-setcard-h">⚙️ Settings used</div>`+
    `<div class="pf-setcard-grid">`+
    items.map(([k,v])=>`<div class="pf-setcard-row"><span class="pf-setcard-k">${k}</span><span class="pf-setcard-v">${esc(v)}</span></div>`).join("")+
    `</div>`+
    `<div class="pf-setcard-row" style="border-top:1px solid var(--line);margin-top:4px;padding-top:4px"><span class="pf-setcard-k">Triggers</span><span class="pf-setcard-v"><b>${sel.length}</b>${sel.length!==all.length?` of ${all.length}`:""}</span></div>`;
}
// Summary table (user 2026-07-13): Overall / Bull / Bear split of the recorded triggers, with
// coloured gradients to draw the eye. Returns Available = every trigger with a return figure —
// including OPEN trades marked to the latest price — so Gains, Losses, Break-even, Win %, Loss %,
// Avg Return and Max/Min all take the open trades into account. Open is an informational subset.
const PF_BE=0;   // a WIN is any gain (>0), a LOSS is any loss (<0); only exactly 0 is break-even (user 2026-07-18)
// Win / Loss / Break-even category for a recorded trigger, from its direction-aware return (matches _pfSeg).
function _pfwl(r){if(r.perf==null)return"—";return r.perf>PF_BE?"Win":r.perf<-PF_BE?"Loss":"Break-even";}
// Days-open range bucket for the Back Test chart strip (user 2026-08-01) — how long trades stay live.
function _doBand(r){const d=r.days_open;if(d==null)return"—";if(d<=2)return"0–2d";if(d<=5)return"3–5d";if(d<=10)return"6–10d";if(d<=20)return"11–20d";if(d<=40)return"21–40d";return"40d+";}
function _pfSeg(rows){
  const ps=rows.map(r=>r.perf).filter(p=>p!=null);
  const g=ps.filter(p=>p>PF_BE),l=ps.filter(p=>p<-PF_BE),be=ps.filter(p=>Math.abs(p)<=PF_BE);
  const av=ps.length?ps.reduce((a,b)=>a+b,0)/ps.length:null;
  return{trades:rows.length,avail:ps.length,gains:g.length,losses:l.length,be:be.length,
    gl:l.length?g.length/l.length:(g.length?Infinity:null),
    win:ps.length?g.length/ps.length*100:null,loss:ps.length?l.length/ps.length*100:null,
    avg:av,max:ps.length?Math.max(...ps):null,min:ps.length?Math.min(...ps):null};
}
// Gradient background scaled from 0..max of |v|, green for good / red for bad.
const _pfBg=(v,max,good)=>{if(v==null||!max)return"";const a=Math.min(Math.abs(v)/max,1)*0.55;
  const c=(good?(v>=0):(v<0))?`22,163,74`:`220,38,38`;return`background:rgba(${c},${a.toFixed(2)})`;};
function _renderPfSummary(sel){
  // Mirror the summary table to BOTH the Summary sub-tab and the Back Test sub-tab (user 2026-08-01):
  // The split belongs only on the dedicated Summary sub-tab (removed from Back Test, user 2026-08-04).
  const _put=html=>{const a=$("pf-summary");if(a)a.innerHTML=html;};
  if(LIMITED){_put(`<p class="muted">🔒 <a href="#" onclick="showLogin();return false">Log in</a> to see the performance summary.</p>`);return;}
  const segs=[["Overall",sel],["Bull",sel.filter(r=>r.direction==="BULL")],["Bear",sel.filter(r=>r.direction==="BEAR")]];
  const rows=segs.map(([name,rs])=>{const s=_pfSeg(rs);
    const pc=v=>v==null?'<span class="muted">—</span>':v.toFixed(1)+'%';
    return `<tr>
      <td><b>${name}</b></td>
      <td style="text-align:right">${s.trades}</td>
      <td style="text-align:right">${s.avail}</td>
      <td style="text-align:right;${_pfBg(s.gains,s.avail||1,true)}">${s.gains}</td>
      <td style="text-align:right;${_pfBg(s.losses,s.avail||1,false)}">${s.losses}</td>
      <td style="text-align:right">${s.be}</td>
      <td style="text-align:right"><b>${s.gl==null?'—':s.gl===Infinity?'∞':s.gl.toFixed(2)+'x'}</b></td>
      <td style="text-align:right;${_pfBg(s.win,100,true)}">${pc(s.win)}</td>
      <td style="text-align:right;${_pfBg(s.loss,100,false)}">${pc(s.loss)}</td>
      <td style="text-align:right;${_pfBg(s.avg,Math.max(Math.abs(s.max||0),Math.abs(s.min||0))||1,true)}"><b>${s.avg==null?'—':(s.avg>0?'+':'')+s.avg.toFixed(1)+'%'}</b></td>
      <td style="text-align:right;color:var(--bull)">${s.max==null?'—':'+'+s.max.toFixed(1)+'%'}</td>
      <td style="text-align:right;color:var(--bear)">${s.min==null?'—':s.min.toFixed(1)+'%'}</td>
    </tr>`;}).join("");
  _put(`<table><thead><tr>
    <th>Segment</th><th style="text-align:right">Trades</th><th style="text-align:right">Returns Available</th>
    <th style="text-align:right">Gains</th><th style="text-align:right">Losses</th><th style="text-align:right">Break-even</th>
    <th style="text-align:right">Gain:Loss Count</th><th style="text-align:right">Win %</th><th style="text-align:right">Loss %</th>
    <th style="text-align:right">Avg Return</th><th style="text-align:right">Max Gain</th><th style="text-align:right" title="The worst single-trade return in the segment">Max Drawdown</th>
  </tr></thead><tbody>${rows}</tbody></table>`);
}
document.querySelectorAll("th[data-pf]").forEach(th=>th.onclick=()=>{const k=th.dataset.pf;pfSortDir=(pfSortK===k)?-pfSortDir:-1;pfSortK=k;renderPerformance();_sortArrows("data-pf",pfSortK,pfSortDir);});
// ── Change Requests tab (admin, user 2026-07-10): parses ChangeRequests/*.txt via /api/change-requests ──
let CR_FILES=[], crSortK="created", crSortDir=-1, CR_SEL=null, CR_DETAIL=null, crDetailStatus="All", crDetailPrange="All";
let crdSortK="row", crdSortDir=1;   // detail-table sort (user 2026-07-24, P-03): default #row ascending
function renderCR(ev){
  // Visible feedback on refresh (user 2026-07-11): spin the button + show a loading row.
  const btn=ev&&ev.target?ev.target:null, was=btn?btn.textContent:"";
  if(btn){btn.disabled=true;btn.textContent="⏳ Data loading…";}
  $("cr-count").innerHTML='<span class="sqh-loading">⏳ Data loading…</span>';
  $("cr-rows").innerHTML=`<tr><td colspan="14" class="empty"><span class="sqh-loading">⏳ Data loading…</span></td></tr>`;
  fetch("/api/change-requests?refresh="+Date.now(),{headers:{"X-Auth":AUTH},cache:"no-store"}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{CR_FILES=j.files||[];paintCR();})
    .catch(()=>{$("cr-rows").innerHTML=`<tr><td colspan="14" class="empty">Could not load (admin only).</td></tr>`;})
    .finally(()=>{if(btn){btn.disabled=false;btn.className="btn";btn.textContent=was;}});
}
function paintCR(){
  // Flatten the per-status counts onto each row so EVERY column sorts (user 2026-07-24, P-02): the count
  // columns read o.counts[status], but genSort compares top-level keys — project them up first.
  const flat=CR_FILES.map(o=>{const c=o.counts||{},p=o.pranges||{};return {...o,"Completed":c["Completed"]||0,"In Progress":c["In Progress"]||0,"Not Started":c["Not Started"]||0,"Cancelled":c["Cancelled"]||0,"Deferred":c["Deferred"]||0,"P01-05":p["P01-05"]||0,"P06-10":p["P06-10"]||0,"P11-25":p["P11-25"]||0,"P26+":p["P26+"]||0};});
  const rows=genSort(flat,crSortK,crSortDir);
  $("cr-count").innerHTML=`<b style="font-size:15px;color:var(--fg)">${CR_FILES.length}</b> change-request files`;
  $("crtab-count")&&($("crtab-count").textContent=`(${CR_FILES.length})`);
  const c=(o,k,col)=>{const n=(o.counts||{})[k]||0;return n?`<b style="color:${col}">${n}</b>`:'0';};
  const pc=(o,k)=>{const n=(o.pranges||{})[k]||0;return n?`<b style="color:#d29922">${n}</b>`:'<span class="muted">0</span>';};   // priority-range count (P-05 L311)
  $("cr-rows").innerHTML=rows.map(o=>`<tr class="clk ${CR_SEL===o.file?'sel':''}" onclick="crOpen('${o.file}')">
    <td><b>${o.name}</b></td><td>${o.created||''}</td><td>${o.updated||''}</td><td><b>${o.total}</b></td>
    <td>${o.prioritised?`<b style="color:#d29922">${o.prioritised}</b>`:'0'}</td>
    <td>${c(o,'Completed','var(--bull)')}</td><td>${c(o,'In Progress','var(--accent)')}</td><td>${c(o,'Not Started','#d29922')}</td><td>${c(o,'Cancelled','var(--bear)')}</td><td>${c(o,'Deferred','var(--muted)')}</td>
    <td>${pc(o,'P01-05')}</td><td>${pc(o,'P06-10')}</td><td>${pc(o,'P11-25')}</td><td>${pc(o,'P26+')}</td></tr>`).join("")
    ||`<tr><td colspan="14" class="empty">No change-request files found.</td></tr>`;
}
function crOpen(file){
  CR_SEL=file;paintCR();
  fetch("/api/change-requests?file="+encodeURIComponent(file),{headers:{"X-Auth":AUTH}}).then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(d=>{CR_DETAIL=d;crDetailStatus="All";crDetailPrange="All";$("cr-detail").classList.remove("hidden");paintCRDetail();$("cr-detail").scrollIntoView({behavior:"smooth",block:"nearest"});})
    .catch(()=>{$("cr-detail").classList.remove("hidden");$("cr-detail").innerHTML='<p class="empty">Could not load file.</p>';});
}
function crStatusFilter(s){crDetailStatus=s;paintCRDetail();}
function crPrangeFilter(p){crDetailPrange=p;paintCRDetail();}   // priority-group filter (user 2026-07-26, P-07/L317)
function paintCRDetail(){
  const d=CR_DETAIL; if(!d)return;
  const sc={Completed:'var(--bull)','In Progress':'var(--accent)','Not Started':'#d29922',Cancelled:'var(--bear)',Requested:'#d29922',Deferred:'var(--muted)'};
  const all=d.requirements||[];
  // Two filter axes (user 2026-07-11 status; user 2026-07-26 P-07/L317 priority group): click a status
  // AND/OR a priority band to narrow. The pill counts are CROSS-FILTERED — each status pill counts within
  // the active priority band and vice-versa — so "P01-05" then "Not Started" shows exactly what's left in
  // that band, and each count reflects what clicking will actually reveal.
  const byStatus=crDetailStatus==="All"?all:all.filter(q=>q.status===crDetailStatus);
  const byPrange=crDetailPrange==="All"?all:all.filter(q=>q.prange===crDetailPrange);
  const sCounts=byPrange.reduce((m,q)=>{m[q.status]=(m[q.status]||0)+1;return m;},{});
  const pCounts=byStatus.reduce((m,q)=>{m[q.prange]=(m[q.prange]||0)+1;return m;},{});
  const pills=["All","Completed","In Progress","Not Started","Cancelled","Requested","Deferred"].map(s=>{
    const n=s==="All"?byPrange.length:(sCounts[s]||0);
    return `<button class="subpill${crDetailStatus===s?' active':''}" onclick="crStatusFilter('${s}')" style="font-size:11px">${s} (${n})</button>`;}).join("");
  const ppills=["All","P01-05","P06-10","P11-25","P26+"].map(p=>{
    const n=p==="All"?byStatus.length:(pCounts[p]||0);
    return `<button class="subpill${crDetailPrange===p?' active':''}" onclick="crPrangeFilter('${p}')" style="font-size:11px">${p} (${n})</button>`;}).join("");
  const shown=all.filter(q=>(crDetailStatus==="All"||q.status===crDetailStatus)&&(crDetailPrange==="All"||q.prange===crDetailPrange));
  // Status must never wrap: the Requirement column is white-space:normal and takes what it likes, which
  // squeezed Status until "Completed" broke mid-word as "Complet/ed" (user 2026-07-17). width:1% +
  // nowrap sizes the column to its longest label ("Not Started") and leaves the rest to Requirement.
  // Prioritised Y/N (user 2026-07-17, P-22) — Y when the requirement carries a P-number or sits under an
  // "Explicitly prioritised work" heading. Same width:1%+nowrap treatment as Status so the Requirement
  // column keeps the width.
  // Every detail column sorts too (user 2026-07-24, P-03) — the file-level table already did; this is the
  // table opened by clicking a filename. Uses the shared genSort/_sortArrows house pattern.
  const rows=genSort(shown,crdSortK,crdSortDir);
  const reqs=rows.map(q=>`<tr><td style="white-space:nowrap;width:1%;text-align:right;color:var(--muted)"><b>#${q.row}</b></td><td style="white-space:nowrap">${q.working_area||'—'}</td><td style="white-space:nowrap">${q.scope||''}</td><td style="white-space:normal">${(q.text||'').replace(/</g,'&lt;')}</td><td style="white-space:nowrap;width:1%;text-align:center"><b style="color:${q.prioritised?'#d29922':'var(--muted)'}">${q.prioritised?'Y':'N'}</b></td><td style="white-space:nowrap;width:1%;text-align:center;color:var(--muted)">${q.prange||'—'}</td><td style="white-space:nowrap;width:1%"><b style="color:${sc[q.status]||'var(--muted)'}">${q.status}</b></td><td style="white-space:normal;color:var(--muted);font-size:12px">${(q.delivery_notes||'').replace(/</g,'&lt;')}</td></tr>`).join("");
  $("cr-detail").innerHTML=`<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><h3 style="margin:0">${d.name}</h3><span class="grow"></span><span class="muted" style="font-size:12px">Created ${d.created||'?'} · Updated ${d.updated||'?'} · ${d.total} actions</span><button class="btn" onclick="$('cr-detail').classList.add('hidden');CR_SEL=null;paintCR()">✕ Close</button></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px">${pills}</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px;align-items:center"><span class="muted" style="font-size:11px;margin-right:2px">Priority:</span>${ppills}</div>
    <div class="tablewrap"><table><thead><tr><th class="clk" data-crd="row" title="Stable row number — use this when referring to a requirement">#</th><th class="clk" data-crd="working_area">Working Area</th><th class="clk" data-crd="scope" title="The requirement's category (Data / Format / Content / BUG / Default / Filter), or &quot;NEW DELIVERY&quot; when it sits under a new-delivery heading (user 2026-07-25, P-05 L310).">Scope</th><th class="clk" data-crd="text">Requirement</th><th class="clk" data-crd="prioritised" title="Carries a P-number, or sits under an &quot;Explicitly prioritised work&quot; heading">Prioritised</th><th class="clk" data-crd="prange" title="Priority band from the requirement's P-number: P01-05 (highest) → P26+ (user 2026-07-26, P-07/L317)">Priority</th><th class="clk" data-crd="status">Status</th><th class="clk" data-crd="delivery_notes" title="What Claude did / decided for this requirement (split out from the requirement text, user 2026-07-25 P-02 L305)">Delivery notes</th></tr></thead><tbody>${reqs||'<tr><td colspan="8" class="empty">No requirements match these filters.</td></tr>'}</tbody></table></div>`;
  // Re-wire on every paint — the header row is rebuilt each render, so a one-time binding would go stale.
  $("cr-detail").querySelectorAll("th[data-crd]").forEach(th=>th.onclick=()=>{const k=th.dataset.crd;crdSortDir=(crdSortK===k)?-crdSortDir:1;crdSortK=k;paintCRDetail();});
  _sortArrows("data-crd",crdSortK,crdSortDir);
}
document.querySelectorAll("th[data-crk]").forEach(th=>th.onclick=()=>{const k=th.dataset.crk;crSortDir=(crSortK===k)?-crSortDir:-1;crSortK=k;paintCR();_sortArrows("data-crk",crSortK,crSortDir);});
function doLogin(){
  fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:$("li_name").value,pwd:$("li_pwd").value})})
    .then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(j=>{localStorage.setItem("sq_auth",j.token);location.reload();})
    .catch(()=>{$("li_err").textContent="Wrong name or password.";});
}
function doLogout(){localStorage.removeItem("sq_auth");location.reload();}
// Open the login panel from the "log in to unlock" teaser link (user 2026-07-10).
function showLogin(){$("loginpanel").classList.remove("hidden");$("view-scanner").classList.add("hidden");window.scrollTo(0,0);const n=$("li_name");if(n)n.focus();}
function doRequestAccount(){
  const m=$("rq_msg");
  if(!($("rq_name").value||"").trim()){m.style.color="var(--bear)";m.textContent="Please enter your name.";return;}
  if(!validEmail($("rq_email").value)){m.style.color="var(--bear)";m.textContent="Enter a valid email address (e.g. name@example.com).";return;}
  fetch("/api/request-account",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:$("rq_name").value,email:$("rq_email").value,note:$("rq_note").value})})
    .then(r=>{if(r.ok){m.style.color="var(--bull)";m.textContent="Request sent — an administrator will review it.";$("rq_name").value=$("rq_email").value=$("rq_note").value="";}
      else{m.style.color="var(--bear)";m.textContent="Could not send — check the details (name may be taken or already requested).";}});
}
function doRequestCode(){
  const el=$("rp_msg");
  fetch("/api/request-reset-code",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:$("rp_name").value,email:$("rp_email").value})})
    .then(()=>{el.style.color="var(--muted)";el.textContent="If those details match an account, a code has been emailed. It's valid for 10 minutes.";});
}
function doReset(){
  fetch("/api/reset-password",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:$("rp_name").value,code:$("rp_code").value,new_pwd:$("rp_pwd").value})})
    .then(r=>r.json().then(j=>({ok:r.ok,j})))
    .then(({ok,j})=>{const el=$("rp_msg");
      if(ok){el.style.color="var(--bull)";el.textContent="Password updated — log in above with the new password.";}
      else{el.style.color="var(--bear)";el.textContent=j.error||"Reset failed.";}});
}
let poSortK="dist_entry", poSortDir=1;   // default sort: Dist->Entry, closest first (user 2026-06-30)
// Show/hide a left-hand filter panel and reflect the state on its button, as the Scanner does
// (user 2026-07-17, P-18/P-19).
function _toggleSideFilters(panelId,btnId){const p=$(panelId);if(!p)return;
  const shown=!p.classList.toggle("hidden");
  const b=$(btnId); if(b)b.innerHTML=`Show Filters <span style="color:var(--${shown?'bull':'bear'})">${shown?'✓':'✗'}</span>`;}
function togglePoFilters(){_toggleSideFilters("po-filters","togglefilters");}   // reflect state on the header button (P-03)
function toggleOoFilters(){_toggleSideFilters("oo-filters","togglefilters");}
function ooClearFilters(){["oo-from","oo-to"].forEach(id=>{const el=$(id);if(el)el.value="";});
  // Clearing filters restores the default, which is HIDE closed orders (user 2026-08-30: the toggle
  // is now the same Show/Hide radio pair IG Account uses, on the right of the count row).
  const sc=document.querySelector('input[name="oo-closed-view"][value="hide"]'); if(sc)sc.checked=true; paintOrderOps();}
function poClearFilters(){["po_qmin","po_qmax","po_rrmin","po_rrmax","po_demin","po_demax"].forEach(id=>{const el=$(id);if(el)el.value="";});renderPreorders();}
function renderPreorders(){
  if(_awaitingData("po-rows"))return;
  renderIgCredWarn("po-igwarn");   // no-IG-credentials warning + Open IG settings button (P-10 L218 / P-25 L219)
  // Say "loading" rather than showing an empty table. DATA is [] until /api/records returns, and an
  // empty Pre-orders table is indistinguishable from one where nothing qualified (user 2026-08-18).
  if(!DATA_LOADED){
    const _b=$("po-rows"); if(_b)_b.innerHTML='<tr><td colspan="24" class="muted" style="padding:18px;text-align:center">Loading pre-orders…</td></tr>';
    const _c=$("po-count-text"); if(_c)_c.textContent="Loading…";
    return;
  }
  let po=DATA.filter(r=>isPreorder(r)&&tradeVisible(r));   // hide the user's excluded markets (user 2026-07-06)
  // Do not publish stale snapshot rows whose geometry is now rejected by the engine.
  // This keeps the browser view consistent before the next full data refresh.
  po=po.filter(r=>r.rr==null||r.rr<=10);
  {const _seen=new Set();po=po.filter(r=>{const k=disp(r.ticker);if(_seen.has(k))return false;_seen.add(k);return true;});}   // no duplicate rows (user 2026-07-10)
  // Respect the user's personal R:R / Quality floors (user 2026-07-24, P-02 BUG): My Pre-orders must not
  // list setups below the floors in Configuration → My trading limits. isPreorder() auto-qualifies any
  // READY/DEVELOPING setup at the system baseline (Q≥25), so without this a Q<40 or R:R<5 row still showed.
  // null rr/quality passes (nothing to compare against).
  {const rrMin=num(MY_LIMITS.min_risk_reward), qMin=num(MY_LIMITS.min_quality), vsMin=num(MY_LIMITS.min_volume_score), rvMin=num(MY_LIMITS.min_rvol);
   // Instrument-value band (user 2026-07-27, P-07) — MCAP for equities; only bites when the row carries an
   // `mcap` value AND the bound is set (0 = off), so it's a no-op until that data lands (like the server gate).
   const ivMin=num(MY_LIMITS.min_instrument_value), ivMax=num(MY_LIMITS.max_instrument_value);
   po=po.filter(r=>!(rrMin!=null&&r.rr!=null&&r.rr<rrMin) && !(qMin!=null&&r.quality!=null&&r.quality<qMin)
                && !(vsMin!=null&&r.volume_score!=null&&r.volume_score<vsMin)
                && !(rvMin!=null&&rvMin>0&&r.rvol!=null&&r.rvol<rvMin)
                && !(+MY_LIMITS.require_above_vwap&&r.above_vwap===false)
                && !(+MY_LIMITS.require_atr_expanding&&r.atr_expanding===false)
                && !(r.mcap!=null&&ivMin!=null&&ivMin>0&&r.mcap<ivMin) && !(r.mcap!=null&&ivMax!=null&&ivMax>0&&r.mcap>ivMax));}   // personal VolumeScore floor + instrument-value band (P-03/P-07), like Quality
  // Chart-click filters (user 2026-07-03): each pof_* holds a SET of selected values (multi-select).
  // Applied to the TABLE (poRows below); the CHARTS brush — each is counted over all OTHER pof_* but not
  // its own — so every option-bar stays visible with the selected value(s) highlighted rather than the
  // strip collapsing to just the picked option (user 2026-07-26, P-03 L31). `po` stays the brushing base.
  const POF=[["pof_direction","direction"],["pof_status","status"],["pof_location","location"],
             ["pof_market","market"],["pof_timeframe","timeframe"],["pof_sector","sector"]];
  // Free-text search by name / ticker (user 2026-07-10).
  const _poq=(($("po-search")||{}).value||"").trim().toLowerCase();
  if(_poq)po=po.filter(r=>((r.name||'')+' '+disp(r.ticker)+' '+(r.ticker||'')).toLowerCase().includes(_poq));
  // Numeric show/hide filters (user 2026-07-13) — same method as the Scanner: null value always passes.
  {const g=id=>(($(id)||{}).value),rng=(v,lo,hi)=>{const a=num(g(lo)),b=num(g(hi));if(v==null)return true;if(a!=null&&v<a)return false;if(b!=null&&v>b)return false;return true;};
   po=po.filter(r=>rng(r.quality,"po_qmin","po_qmax")&&rng(r.rr,"po_rrmin","po_rrmax")&&rng(r.dist_entry,"po_demin","po_demax"));}
  // Click a column header to sort (user 2026-06-30). Dist->Entry sorts on |distance| (closest first);
  // other columns sort on their raw value; nulls always sink to the bottom.
  // Table honours EVERY chart filter (all pof_*); the charts (pby) brush by skipping their own.
  let poRows=po.filter(r=>POF.every(([id,k])=>inSet(id,r[k])));
  const val=r=>{if(poSortK==="_fav")return FAVS.has(disp(r.ticker))?1:0;const v=r[poSortK];if(v==null)return null;return poSortK==="dist_entry"?Math.abs(v):v;};
  poRows.sort((a,b)=>{const x=val(a),y=val(b);
    if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
    return (x<y?-1:x>y?1:0)*poSortDir;});
  const nR=poRows.filter(r=>r.status==="READY").length;
  $("po-count-text").innerHTML=`<b style="font-size:15px;color:var(--fg)">${poRows.length}</b> instruments with a pre-order — waiting for the entry to trigger <span class="muted">(${nR} READY · ${poRows.length-nR} DEVELOPING). Click a row for detail; tick + Delete selected to dismiss.</span>`;
  // Chart visuals, same set as the Scanner (user 2026-07-03) — brushed: count over po with every OTHER
  // pof_* applied but not `exceptId`, so all option-bars stay visible (user 2026-07-26, P-03 L31).
  const pby=(field,exceptId)=>{const m={};
    // Seed EVERY option value (count 0 if none) from the chart-independent base `po`, so a selection never
    // drops a bar — the bar SET stays constant, so packViz packs identically and card size/position don't
    // change on selection (user 2026-07-26, P-04 #66 / house L31+L33). Mirrors the Performance byXFull seed.
    po.forEach(r=>{const v=r[field]||"—";if(!(v in m))m[v]=0;});
    po.forEach(r=>{if(!POF.every(([id,k])=>id===exceptId||inSet(id,r[k])))return;const v=r[field]||"—";m[v]++;});
    return m;};
  // Location, Market, Sector on LHS (user 2026-07-24/25, P-03 L29 / P-05 L182 — supersedes P-12a "Market first").
  $("po-viz").innerHTML=
    `<div class="vizsector">`+barChart("Location",pby("location","pof_location"),"pof_location")+`</div>`+
    `<div class="vizsector">`+barChart("Market",pby("market","pof_market"),"pof_market")+`</div>`+
    `<div class="vizsector">`+pieChart("Sector",pby("sector","pof_sector"),"pof_sector")+`</div>`+
    `<div class="vizbars">`+
      barChart("Direction",pby("direction","pof_direction"),"pof_direction",k=>k==="BULL"?"var(--bull)":"var(--bear)")+
      barChart("Status",pby("status","pof_status"),"pof_status",k=>k==="READY"?"var(--accent)":"#d29922")+
      barChart("Timeframe",pby("timeframe","pof_timeframe"),"pof_timeframe")+
    `</div>`;
  packViz("po-viz");   // P-15
  // MCap / VWAP / ATR (2026-09-04). The HEADINGS for these three were added and the CELLS never were, so
  // this table rendered 20 cells under 23 headings: MCap titled RVOL, RVOL titled Vol, and every column
  // right of it shifted, leaving Source, Sector and Ticker with no data beneath them. Found by sweeping
  // every table after the same class of fault was reported on the Scanner. Same shared formatters the
  // Scanner and IG tables use, and poRows is filtered from DATA, which is where the Scanner reads these
  // very fields -- so one instrument reads identically wherever it appears.
  //
  // This comment lives OUT here on purpose. It was first written inside the template literal below, and
  // the pair of backticks in it closed the literal and broke the whole file: every tab on the site
  // stopped responding. test_the_client_javascript_parses now runs node --check over app.js.
  $("po-rows").innerHTML=poRows.map(r=>{const d=r.dist_entry;
    return `<tr data-t="${r.ticker}"><td><input type="checkbox" class="po-sel" data-t="${r.ticker}" onclick="event.stopPropagation();poUpdateBtn()"></td>
      ${_favCell(r.ticker)}<td>${nm40(r.name)}</td>
      <td>${r.direction?`<span class="tag ${r.direction==='BULL'?'bull':'bear'}">${r.direction}</span>`:''}</td>
      <td>${_mcapFmt(r.mcap)}</td>
      <td>${rvolCell(r.rvol)}</td><td>${_tickCross(r.above_vwap)}</td><td>${_tickCross(r.atr_expanding)}</td><td>${volScoreCell(r.volume_score)}</td>
      <td>${r.status}</td><td>${r.market||''}</td>
      <td>${f2(r.current_price)}</td><td><b>${f2((OVERRIDES[r.ticker]||{}).entry??r.entry)}${(OVERRIDES[r.ticker]||{}).entry!=null&&OVERRIDES[r.ticker].entry!==r.entry?' ✎':''}</b></td><td style="color:var(--bear)">${f2((OVERRIDES[r.ticker]||{}).stop??r.stop)}</td><td style="color:var(--bull)">${f2((OVERRIDES[r.ticker]||{}).target??r.target)}</td>
      <td>${d!=null?(d>0?'+':'')+d+'%':''}</td>
      <td>${r.rr!=null?r.rr.toFixed(1):''}</td>
      <td>${r.quality!=null?`<b style="color:${qcol(r.quality)}">${r.quality}</b>`:''}</td>
      <td>${(r.timeframe||'').replace('daily-','D')}</td>
      <td>${r.added||''}</td><td>${r.source||''}</td><td>${r.sector||''}</td><td><b>${disp(r.ticker)}</b></td></tr>`;}).join("")
    || `<tr><td colspan="23" class="empty">No pre-orders right now.</td></tr>`;
  document.querySelectorAll("#po-rows tr[data-t]").forEach(tr=>tr.onclick=e=>{if(e.target.type==="checkbox")return;openDetailFrom('preorders',tr.dataset.t);});   // return here on close (P-29)
  $("potab-count").textContent="("+DATA.filter(r=>isPreorder(r)&&tradeVisible(r)).length+")";   // match the per-user hidden view (user 2026-07-06)   // keep tab count in sync after delete (user 2026-07-06)
  poUpdateBtn();
}
function poSelected(){return [...document.querySelectorAll(".po-sel:checked")].map(c=>c.dataset.t);}
function poUpdateBtn(){const n=poSelected().length;const b=$("po-delete");b.disabled=!n;b.textContent=n?`Delete selected (${n})`:"Delete selected";}
function poToggleAll(cb){document.querySelectorAll(".po-sel").forEach(c=>c.checked=cb.checked);poUpdateBtn();}
async function deletePreorders(){
  const tks=poSelected(); if(!tks.length)return;
  if(!await appConfirm(`They move to Order (Operations) as DELETED and stay hidden from this list for 30 days.`,{title:`Delete ${tks.length} pre-order(s)?`,ok:"Delete"}))return;
  fetch("/api/preorder-delete",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({tickers:tks})})
    .then(r=>{if(!r.ok)throw 0;return r.json();})
    .then(()=>{tks.forEach(t=>{IGWO.add(t);PINNED.delete(t);PINNED.delete(disp(t));});$("po-all").checked=false;renderPreorders();})
    .catch(()=>alert("Delete failed — try logging in again."));
}
// Push a Scanner instrument into My Pre-orders (pin), or unpin it (user 2026-07-03).
function pushToPreorder(tk){
  const on=!(PINNED.has(tk)||PINNED.has(disp(tk)));
  let levels=null;
  if(on){const r=DATA.find(x=>x.ticker===tk)||{};
    const inp=prompt("Entry, Stop, Target — edit any value before it goes to Pre-orders (changes are recorded in My Activity):",
                     `${r.entry??''}, ${r.stop??''}, ${r.target??''}`);
    if(inp===null)return;
    const parts=inp.split(",").map(x=>parseFloat(x));
    if(parts.length===3&&parts.every(v=>isFinite(v)&&v>0))levels={entry:parts[0],stop:parts[1],target:parts[2]};
  }
  fetch("/api/preorder-pin",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({ticker:tk,on,levels})})
    .then(r=>{if(!r.ok)return r.json().then(j=>{throw j.error||"failed";});return r.json();})
    .then(j=>{PINNED=new Set(j.pinned||[]);OVERRIDES=j.overrides||{};render();renderPreorders&&renderPreorders();if(SEL===tk)showDetail(tk);})
    .catch(e=>alert("Could not update pre-orders: "+e));
}
// Place a pre-order as a live IG working order now (money path) — confirm first.
async function placeOnIG(tk){
  if(!await appConfirm(`Places it now using your own IG account. This is a real order.`,{title:`Place a LIVE IG working order for ${disp(tk)}?`,ok:"Place order"}))return;
  fetch("/api/place-order",{method:"POST",headers:{"Content-Type":"application/json","X-Auth":AUTH},body:JSON.stringify({ticker:tk})})
    .then(r=>r.json().then(j=>({ok:r.ok,j})))
    .then(({ok,j})=>{if(!ok||!j.ok){alert("Not placed: "+(j.error||j.status||"unknown"));return;}
      alert(`${disp(tk)} order placed (${j.status||'ok'}). It will appear in Order (Operations).`);
      IGWO.add(tk);renderPreorders();})
    .catch(()=>alert("Placement failed — try again."));
}

// Resolve role first (user 2026-07-03) so tab visibility is correct on first paint.
fetch("/api/me",{headers:{"X-Auth":AUTH}}).then(r=>r.json()).then(m=>{
  ROLE=m.subscription||"guest"; IS_ADMIN=!!m.is_admin; IS_SUPPORT=!!m.is_support;
  if(m.name)$("logout").textContent=`${m.name}${m.is_admin?" (admin)":m.is_support?" (support)":""} · Log out`;   // who's logged in
  return AUTH?fetch("/api/config",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{}).catch(()=>({})):{};
}).then(cfg=>{HIDDEN_TABS=(cfg&&cfg.hidden_tabs)||[]; SHOWN_TABS=(cfg&&cfg.shown_tabs)||[]; if(cfg&&cfg.leverage)LEVERAGE=cfg.leverage; PINNED=new Set((cfg&&cfg.pinned_preorders)||[]); OVERRIDES=(cfg&&cfg.pinned_overrides)||{}; if(cfg&&cfg.features)FEATURES=cfg.features;})
  .catch(()=>{}).finally(()=>{applyTabVisibility();
    // Return to the tab we were on before a reload (e.g. after a data refresh); fresh visits open Introduction.
    let _t=null; try{_t=sessionStorage.getItem("sq_tab");}catch(e){}
    showTab(_t&&tabAllowed(_t)?_t:"welcome");});
if(AUTH){$("logout").style.display="";$("loginbtn").style.display="none";}   // logged in: swap Log in → Log out (user 2026-08-08)
// FAIL CLOSED. This used to default to false, i.e. "treat an unknown visitor as entitled until the
// server says otherwise" -- and the server only says so inside the .then() of a Promise.all that
// includes /api/records, MEASURED at 25-35s logged out. So a logged-out visitor was unrestricted for
// that whole window, and PERMANENTLY unrestricted if that promise rejected (the 401 path throws
// "auth", so the .then never runs and LIMITED never gets set at all -- observed live 2026-09-01,
// LIMITED still false after a complete load with AUTH false).
//
// Everything gated on LIMITED was therefore open during that window, including the Transaction
// evidence panel (user 2026-09-01: "transaction evidence is visible in performance - best settings -
// when no one is logged in - this is taboo ... there long enough to capture the data").
//
// Deriving it from AUTH means an anonymous visitor is restricted from the FIRST paint, before any
// fetch resolves and whether or not one ever does. The server response still refines it below.
// NOTE: this is the browser being honest, not a security boundary -- the endpoints must stop
// serving the rows too. See the register entry for the /api/winners exposure.
let LIMITED=!AUTH;
const ob=h=>LIMITED?'<span class="muted">•••</span>':h;   // obfuscated cell for logged-out visitors
Promise.all([fetch("/api/records",{headers:{"X-Auth":AUTH}}).then(r=>{if(r.status===401)throw "auth";return r.json();}),
             // X-Auth required from 2026-08-25: /api/positions returned the account's REAL open book to
             // anyone, with no auth check at all. It now answers 401 with an empty map, so a logged-out
             // visitor still renders the page — just without position indicators, which they should
             // never have had. Logged-in users need the token here or they lose the indicators too.
             fetch("/api/positions",{headers:{"X-Auth":AUTH}}).then(r=>r.json()).catch(()=>({positions:{}})),
             fetch("/api/pubcounts").then(r=>r.json()).catch(()=>({pubcounts:{}})),
             fetch("/api/working-orders").then(r=>r.json()).catch(()=>({tickers:[]})),
             fetch("/api/config",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():{}).catch(()=>({}))])
  .then(([j,p,pc,wo,cfg])=>{
    POS=p.positions||{}; PUB=pc.pubcounts||{}; IGWO=new Set(wo.tickers||[]);
    LIMITED=!!j.limited;   // logged out: only the first 5 columns are real; the rest render obfuscated
    USER_FILTERS=(cfg&&cfg.filters)||{}; applyUserDefaults(); _seedStatusDefaults();   // user saved defaults, then built-in default Status selections (P-05)
    TRADE_HIDE=(cfg&&cfg.trade)||{};   // per-user Trading (Squeeze) filter -> hides excluded markets (user 2026-07-06)
    MARKETS_DISABLED=new Set((cfg&&cfg.markets_disabled)||[]); MARKETS_OFF=new Set((cfg&&cfg.markets_off)||[]);   // market on/off switches (user 2026-07-11)
    // My Trading Filters (user 2026-08-11): MY_LIMITS previously stayed {} for the WHOLE session until the
    // user happened to open the Configuration tab (renderConfig() was the only place that ever read
    // cfg.limits) — so the Scanner's first render(), which runs a few lines below, hard-filtered against
    // an empty MY_LIMITS even though the user's floors (R:R/Quality/RVOL/VWAP/ATR/instrument value) were
    // already saved server-side from a previous session. Symptom: freshly-loading the Scanner showed rows
    // that violated a saved "Require ATR expanding"/"Require above VWAP" floor, with no Save click involved
    // at all — a bigger version of the same-day Save-time gap fixed alongside this. Load it here too, from
    // the SAME /api/config response already being fetched for filters/trade/markets, so the very first
    // Scanner render is correctly filtered without needing a visit to Configuration first.
    if(cfg&&cfg.limits){MY_LIMITS=cfg.limits;Object.entries(cfg.limits).forEach(([k,v])=>{const el=$("lim-"+k);if(el){if(el.type==='checkbox')el.checked=!!v;else el.value=Array.isArray(v)?v.join(", "):v;}});}
    DATA=j.records||[]; DATA_LOADED=true; DATA.forEach(augment);
    if(j.markets&&j.markets.length)REFRESH_MKT_LIST=j.markets;   // canonical market list for the Refresh picker (P-15)
    $("gen").textContent=j.generated_utc?("snapshot "+new Date(j.generated_utc).toLocaleString()):"no snapshot — run build_snapshot.py";
    // f_loc/f_tf went to Squeeze History with the filters (2026-08-16) and fillSel dereferences $(id)
    // unguarded. f_mkt/f_sec are still populated: they are hidden now, but applyConfigFromReport selects
    // options on them to carry the saved market/sector scope, which needs the options to exist.
    fillSel("f_mkt","market");fillSel("f_sec","sector");
    msyncAll();   // options only exist now — repaint the P-08 dropdown buttons (incl. any user defaults)
    render();
    document.body.classList.remove("app-loading");
    if($("view-instruments"))paintInstruments();   // Instruments reuses this same DATA load (user 2026-08-07, ChangeRequest P-08) — repaint if it's already the active/cached tab
    $("potab-count").textContent="("+DATA.filter(r=>isPreorder(r)&&tradeVisible(r)).length+")";   // match the per-user hidden view (user 2026-07-06)
    // The Introduction example is now a STATIC file (src set in index.html), so nothing is assigned here.
    // It used to be "/api/card/"+ticker, chosen from DATA with the Howden-else-best-triggered-equity rule
    // (user 2026-07-03). MEASURED on the host 2026-08-30: that endpoint answers HTTP 500 after ~120 s for
    // every ticker tried, because it downloads from yfinance and renders matplotlib on the request thread.
    // The same rule now picks the ticker when the PNG is pre-rendered, so the picture is unchanged; only
    // the delivery moved off the request path. See hvf_web/intro_card.png.
    // If a refresh is already running (started elsewhere), disable the button and show its progress.
    fetch("/api/status").then(x=>x.json()).then(s=>{if(s.refreshing){$("refresh").disabled=true;_refStart=Date.now();pollRefresh();}}).catch(()=>{});
  })
  .catch(e=>{ // 401 = no/stale login: clear the token; the login panel appears when a data tab is opened.
    if(e==="auth"){AUTH="";localStorage.removeItem("sq_auth");$("logout").style.display="none";$("loginbtn").style.display="";}
    const _count=$("count"),_rows=$("rows");
    if(_count){_count.classList.remove("sqh-loading");_count.innerHTML='<span style="color:var(--bear)">Scanner Report data did not load. Please retry.</span>';}
    if(_rows)_rows.innerHTML='<tr><td colspan="27" class="empty">Scanner Report data did not load. Please refresh or log in again.</td></tr>';
    document.body.classList.remove("app-loading");
  });
// Default-sort arrow on first paint (user 2026-07-25, P-03 L39): the click handlers stamp the ▲/▼ on
// click, but a DEFAULT-sorted column had no arrow until first clicked. Stamp each table's default now —
// theads are static HTML so the marker persists across tbody repaints. (CR detail rebuilds its thead and
// re-stamps itself in paintCRDetail.) sortK="" means natural order → no arrow.
function _stampSortDefaults(){
  const pairs=[["data-k",()=>[sortK,sortDir]],["data-pk",()=>[poSortK,poSortDir]],["data-ok",()=>[ooSortK,ooSortDir]],
   ["data-pf",()=>[pfSortK,pfSortDir]],["data-crk",()=>[crSortK,crSortDir]],
   // admin tables (var names from their th[data-*] click wiring)
   ["data-xk",()=>[xSortK,xSortDir]],["data-jk",()=>[jSortK,jSortDir]],["data-sk",()=>[slSortK,slSortDir]],
   ["data-vk",()=>[verSortK,verSortDir]],["data-bk",()=>[batchSortK,batchSortDir]],["data-ak",()=>[acSortK,acSortDir]],
   ["data-sqh",()=>[sqhSortK,sqhSortDir]],
   ["data-igp",()=>[igpSortK,igpSortDir]],["data-igo",()=>[igoSortK,igoSortDir]]];   // IG Account tables (user 2026-08-01)
  pairs.forEach(([a,get])=>{try{const [k,d]=get();_sortArrows(a,k,d);}catch(e){}});   // per-pair guard: a missing var just skips that table
}
_stampSortDefaults();

// ------------------------------------------------------------------------------------------------------
// Orders that no longer meet the saved trading filters (user 2026-09-03).
//
// THE RULE, as the requester finally settled it. Their first wording -- "rvol decay is acceptable - R:R
// below the floor is not ok" -- produced a three-way split, and this panel was written to it. They then
// sharpened it to "RVOL, ATR and VWAP are only relevant at the break (so stale should not be an issue)",
// which is stronger and removes a whole verdict: a pending order has not broken, so its break-bar
// measures are not stale, not decayed and not breaching -- they are NOT APPLICABLE, and since then
// /api/order-filter-audit does not judge them at all. There is no STALE row left for this panel to show.
// See docs/ORDER_TIMING_AND_RVOL.md and order_filter_audit.BREAK_BAR_LABELS.
//
// What survives is durable: R:R, Quality, instrument value, and the direction/location/market gate. So
// every flagged order either fails on something that cannot improve, or cannot be judged at all -- "if 5
// can't be judged - they cannot be confirmed - they need to go" -- and every flagged order is pre-ticked.
// Nothing is ever cancelled without the confirmation dialog and the server re-reading the account itself.
let IG_AUDIT=null, IGORD=[], igbSortK="verdict", igbSortDir=-1;
function loadOrderFilterAudit(){
  const panel=$("ig-breach-panel"); if(!panel)return;
  fetch("/api/order-filter-audit",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    if(!j||j.error){IG_AUDIT=null;panel.style.display="none";return;}
    IG_AUDIT=j; paintOrderFilterAudit();
  }).catch(()=>{IG_AUDIT=null;panel.style.display="none";});
}
function paintOrderFilterAudit(){
  const panel=$("ig-breach-panel"), body=$("ig-breach-rows"), count=$("ig-breach-count");
  const note=$("ig-breach-note"), act=$("ig-breach-actions");
  if(!panel||!body||!IG_AUDIT)return;
  // Only orders IG is actually holding: the audit walks working_orders, which can lag a cancellation.
  const live=new Map((IGORD||[]).filter(o=>o.deal_id).map(o=>[o.ticker,o]));
  const rows=(IG_AUDIT.rows||[]).filter(r=>r.verdict!=="OK"&&live.has(r.ticker)).map(r=>{
    const o=live.get(r.ticker)||{};
    return {...r, name:o.name, direction:o.direction, size:o.size, deal_id:o.deal_id,
            _why:((r.breaches&&r.breaches.length?r.breaches:r.unknown||[])).join("; "), _why1:_whyHead(r)};});
  if(!rows.length){panel.style.display="none";_breachViz("ig-breach-viz",[],"igb");return;}
  panel.style.display="";
  // BREACH and UNKNOWN are the only verdicts this endpoint can now return, and the requester has ruled on
  // both. Anything else would be a verdict added server-side after this was written: show it, so a new
  // kind of failure is never silently dropped, but do not pre-tick a judgement nobody has ruled on.
  const must=rows.filter(r=>r.verdict==="BREACH"||r.verdict==="UNKNOWN");
  const other=rows.filter(r=>r.verdict!=="BREACH"&&r.verdict!=="UNKNOWN");
  count.innerHTML=`<b style="font-size:15px;color:var(--fg)">${must.length}</b> working order${must.length===1?"":"s"} no longer meet your settings`
    +(other.length?` <span class="muted">· ${other.length} flagged under a rule this screen has no ruling for</span>`:"");
  _breachViz("ig-breach-viz", rows, "igb");
  note.innerHTML=`<div class="muted" style="font-size:12px;margin:0 0 8px">A filter is checked when an order is PLACED and never again. <b>Judged:</b> R:R, Quality, instrument value and the direction/location/market gate — properties of the setup, which cannot improve while the order sits there. <b>Not judged:</b> RVOL, VolumeScore, VWAP and ATR, which measure the breakout bar; these orders have not broken, so there is nothing to measure.</div>`;
  const sorted=genSort(rows, igbSortK, igbSortDir);
  body.innerHTML=sorted.map(r=>`<tr>
    <td><input type="checkbox" data-cancel="${_esc(r.deal_id)}" ${(r.verdict==="BREACH"||r.verdict==="UNKNOWN")?"checked":""}></td>
    <td>${nm40(r.name||disp(r.ticker))}</td>
    <td>${_igDtag(r.direction)}</td>
    <td>${_igSz(r.size)}</td>
    <td><b style="color:${r.verdict==="BREACH"?"var(--bear)":r.verdict==="UNKNOWN"?"#d29922":"var(--muted)"}">${_esc(r.verdict)}</b></td>
    <td class="muted" style="white-space:normal">${_esc(r._why)}</td>
    <td><b>${_esc(disp(r.ticker))}</b></td></tr>`).join("")
    || `<tr><td colspan="7" class="empty">Every working order meets your settings.</td></tr>`;
  act.innerHTML=`<button class="btn" id="ig-cancel-btn" onclick="cancelBreachingOrders(this)" style="margin-top:10px;border-color:var(--bear);background:color-mix(in srgb,var(--bear) 14%,transparent)">Cancel the ticked orders at IG</button>`;
  _sortArrows("data-igb", igbSortK, igbSortDir);
}
async function cancelBreachingOrders(btn){
  const picked=[...document.querySelectorAll('#ig-breach-rows input[data-cancel]:checked')].map(el=>el.dataset.cancel);
  if(!picked.length){alert("Nothing is ticked.");return;}
  const live=new Map((IGORD||[]).map(o=>[o.deal_id,o]));
  const rows=picked.map(id=>{const o=live.get(id)||{};return [disp(o.ticker||id), `${o.direction||""} ${o.size??""} @ ${o.level??""}`];});
  // The names are listed in the dialog, not just the count: "cancel 21 orders" is not informed consent.
  const ok=await appConfirm(`This cancels ${picked.length} working order${picked.length===1?"":"s"} at IG. It cannot be undone from here, though the same setups can be re-ordered later.`,
    {title:"Cancel working orders at IG",ok:`Cancel ${picked.length} order${picked.length===1?"":"s"}`,rows});
  if(!ok)return;
  btn.disabled=true; btn.textContent="⏳ Cancelling…";
  try{
    const r=await fetch("/api/ig-cancel-orders",{method:"POST",
      headers:{"Content-Type":"application/json","X-Auth":AUTH},
      body:JSON.stringify({confirmed:true,deal_ids:picked,reason:"WEB_USER_SETTINGS_BREACH"})});
    const j=await r.json();
    if(j.error){btn.textContent="Cancel failed";alert(j.error);btn.disabled=false;return;}
    const done=(j.results||[]).filter(x=>x.cancelled).length;
    btn.textContent=`${done} of ${picked.length} cancelled`;
    const failed=(j.results||[]).filter(x=>!x.cancelled);
    if(failed.length)alert("IG did not confirm these:\n"+failed.map(f=>`${f.deal_id}: ${f.error}`).join("\n"));
    paintIgAccount&&renderIgAccount();     // re-read the account so the table shows what is actually left
  }catch(e){btn.textContent="Cancel failed";btn.disabled=false;}
}

// ------------------------------------------------------------------------------------------------------
// IG Account sub-tabs (user 2026-09-04: "consider within IG accounts use of 3 tab buttons - open tx,
// auto closed tx, open orders").
//
// The panels are plain show/hide over markup that was already there, so the positions and orders tables
// keep their own paint paths, sort state and chart strips untouched. Only the auto-closed table is new,
// and it is READ-ONLY: nothing on this screen closes anything. auto_close_failed_opens.py is the only
// writer, it acts only on the day a position opened, and it is off unless the account owner switches it
// on. See docs and the module header for why same-day is the whole safety property.
// ------------------------------------------------------------------------------------------------------
let IG_PANEL = "open", IG_AUTO = null, igaSortK = "closed_at", igaSortDir = -1;

function showIgPanel(which){
  IG_PANEL = which;
  document.querySelectorAll("#ig-pills .pill").forEach(b=>b.classList.toggle("active", b.dataset.igpanel===which));
  [["open","igpanel-open"],["autoclosed","igpanel-autoclosed"],["orders","igpanel-orders"],
   ["txbreach","igpanel-txbreach"],["ordbreach","igpanel-ordbreach"]].forEach(([k,id])=>{
    const el=$(id); if(el)el.classList.toggle("hidden", k!==which);
  });
  if(which==="autoclosed"&&IG_AUTO===null)loadAutoClosed();
  if(which==="txbreach"&&IG_TXAUDIT===null)loadPositionFilterAudit();
}

// ------------------------------------------------------------------------------------------------------
// OPEN POSITIONS that no longer meet the criteria.
//
// Judged at the bar each position OPENED on -- the opposite basis to the orders panel. A pending order
// has not broken, so RVOL/VWAP/ATR/VolumeScore are not applicable to it; a position HAS broken, because
// that is what filled it, so those are precisely what to test. The break-bar figures are read from
// instrument_metrics_daily, never recomputed.
//
// CLOSING REALISES PROFIT OR LOSS, which cancelling a working order does not. So nothing here is closed
// without the confirmation dialog naming every position, and the server re-reads the account for itself.
// ------------------------------------------------------------------------------------------------------
let IG_TXAUDIT = null, igtSortK = "verdict", igtSortDir = -1;

function loadPositionFilterAudit(){
  // -rows, not -body: aa68f64 rebuilt this panel as a real table and renamed the container. This line kept
  // the old id, so the guard fired on every load and the fetch below never ran -- the tab was empty no
  // matter how many positions breached (user 2026-09-06).
  const body=$("ig-txbreach-rows"); if(!body)return;
  body.innerHTML='<span class="muted">⏳ Checking your open positions…</span>';
  fetch("/api/position-filter-audit",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    IG_TXAUDIT=(j&&!j.error)?j:{rows:[]};
    paintPositionBreach();
  }).catch(()=>{IG_TXAUDIT={rows:[]};paintPositionBreach();});
}

function _breachViz(boxId, rows, prefix){
  // The SAME chart strip the Open transactions and Open orders tabs carry, built from the same
  // barChart/pieChart helpers (user 2026-09-05: "get a standard approach to similar pages so UI and UX
  // is as good as it can be"). Charts here are presentational only -- they summarise what is in the
  // table below rather than filtering it, so they take no data-fk hook.
  const box=$(boxId); if(!box)return;
  if(!rows.length){box.innerHTML="";return;}
  const by=f=>{const m={};rows.forEach(r=>{const v=r[f]||"—";m[v]=(m[v]||0)+1;});return m;};
  box.innerHTML=
    `<div class="vizsector">`+barChart("Verdict",by("verdict"),prefix+"_verdict",k=>k==="BREACH"?"var(--bear)":"#d29922")+`</div>`+
    `<div class="vizsector">`+barChart("Direction",by("direction"),prefix+"_direction",k=>k==="BUY"?"var(--bull)":"var(--bear)")+`</div>`+
    `<div class="vizsector">`+barChart("Reason",by("_why1"),prefix+"_why")+`</div>`;
  packViz(boxId);
}
function _whyHead(r){
  const list=(r.breaches&&r.breaches.length?r.breaches:r.unknown||[]);
  return (list[0]||"—").split(" ").slice(0,2).join(" ");     // "RVOL 1.02 < 1.5" -> "RVOL 1.02" -> group label
}
function paintPositionBreach(){
  const count=$("ig-txbreach-count"), body=$("ig-txbreach-rows"), note=$("ig-txbreach-note"), act=$("ig-txbreach-actions");
  if(!body||!IG_TXAUDIT)return;
  const rows=(IG_TXAUDIT.rows||[]).filter(r=>r.verdict!=="OK"&&r.deal_id)
    .map(r=>({...r,_why:( (r.breaches&&r.breaches.length?r.breaches:r.unknown||[]) ).join("; "),_why1:_whyHead(r)}));
  const ok=(IG_TXAUDIT.rows||[]).length-rows.length;
  count.innerHTML=`<b style="font-size:15px;color:var(--fg)">${rows.length}</b> open position${rows.length===1?"":"s"} no longer meet your criteria`
    +(ok?` <span class="muted">· ${ok} still do</span>`:"");
  _breachViz("ig-txbreach-viz", rows, "igt");
  note.innerHTML=rows.length?`<div class="muted" style="font-size:12px;margin:0 0 8px">Each position is judged against the bar it OPENED on, read from the stored daily metrics — not against today. <b>Closing realises profit or loss</b>, so nothing is closed until you confirm it, and only BREACH rows are ticked: a position that cannot be judged is listed but never pre-selected.</div>`:"";
  const sorted=genSort(rows, igtSortK, igtSortDir);
  body.innerHTML=sorted.map(r=>`<tr>
    <td><input type="checkbox" data-close="${_esc(r.deal_id)}" ${r.verdict==="BREACH"?"checked":""}></td>
    <td>${nm40(r.name||disp(r.ticker))}</td>
    <td>${_igDtag(r.direction)}</td>
    <td>${_igSz(r.size)}</td>
    <td>${_esc(r.opened||"")}</td>
    <td><b style="color:${r.verdict==="BREACH"?"var(--bear)":"#d29922"}">${_esc(r.verdict)}</b></td>
    <td class="muted" style="white-space:normal">${_esc(r._why)}</td>
    <td><b>${_esc(disp(r.ticker))}</b></td></tr>`).join("")
    || `<tr><td colspan="8" class="empty">Every open position meets your criteria.</td></tr>`;
  act.innerHTML=rows.length?`<button class="btn" id="ig-txclose-btn" onclick="closeBreachingPositions(this)" style="margin-top:10px;border-color:var(--bear);background:color-mix(in srgb,var(--bear) 14%,transparent)">Close the ticked positions at IG</button>`:"";
  _sortArrows("data-igt", igtSortK, igtSortDir);
}
async function closeBreachingPositions(btn){
  const picked=[...document.querySelectorAll('#ig-txbreach-rows input[data-close]:checked')].map(el=>el.dataset.close);
  if(!picked.length){await appConfirm("Nothing is ticked.",{title:"No positions selected",ok:"OK"});return;}
  const by={}; (IG_TXAUDIT.rows||[]).forEach(r=>{if(r.deal_id)by[r.deal_id]=r;});
  const rows=picked.map(id=>{const r=by[id]||{};return [disp(r.ticker||id), `${r.direction||""} ${r.size??""} — ${(r.breaches||[]).join("; ")}`];});
  const ok=await appConfirm(`This closes ${picked.length} open position${picked.length===1?"":"s"} at IG and REALISES their profit or loss. It cannot be undone.`,
    {title:"Close positions at IG",ok:`Close ${picked.length} position${picked.length===1?"":"s"}`,rows});
  if(!ok)return;
  btn.disabled=true; btn.textContent="⏳ Closing…";
  try{
    const r=await fetch("/api/ig-close-positions",{method:"POST",
      headers:{"Content-Type":"application/json","X-Auth":AUTH},
      body:JSON.stringify({confirmed:true,deal_ids:picked})});
    const j=await r.json();
    if(j.error){btn.textContent="Close failed";await appConfirm(j.error,{title:"Close failed",ok:"OK"});btn.disabled=false;return;}
    const done=(j.results||[]).filter(x=>x.closed).length;
    btn.textContent=`${done} of ${picked.length} closed`;
    IG_TXAUDIT=null; renderIgAccount();
  }catch(e){btn.textContent="Close failed";btn.disabled=false;}
}

function loadAutoClosed(){
  const body=$("ig-auto-rows"); if(!body)return;
  fetch("/api/auto-closed",{headers:{"X-Auth":AUTH}}).then(r=>r.ok?r.json():null).then(j=>{
    IG_AUTO=(j&&j.rows)||[];
    paintAutoClosed();
  }).catch(()=>{IG_AUTO=[];paintAutoClosed();});
}

function paintAutoClosed(){
  const body=$("ig-auto-rows"), count=$("ig-auto-count");
  if(!body)return;
  const rows=genSort((IG_AUTO||[]).slice(), igaSortK, igaSortDir);
  if(count)count.innerHTML=rows.length
    ? `<b style="font-size:15px;color:var(--fg)">${rows.length}</b> auto-closed position${rows.length===1?"":"s"} <span class="muted">— closed on their opening day for failing the break-bar volume tests</span>`
    : `<b style="font-size:15px;color:var(--fg)">0</b> auto-closed positions <span class="muted">— nothing has been closed this way</span>`;
  body.innerHTML=rows.map(r=>`<tr>
    <td>${nm40(r.name||disp(r.ticker||""))}</td>
    <td>${_esc(String(r.closed_at||"").slice(0,19).replace("T"," "))}</td>
    <td>${_esc(String(r.opened_on||"").slice(0,10))}</td>
    <td>${_igDtag(r.direction)}</td>
    <td>${_igSz(r.size)}</td>
    <td>${_igPf(r.profit)}</td>
    <td>${_esc(r.currency||"")}</td>
    <td style="white-space:normal">${_esc(r.volume_breaches||"")}</td>
    <td style="white-space:normal" class="muted">${_esc(r.durable_breaches||"")||'<span class="muted">—</span>'}</td>
    <td>${_esc(r.outcome||"")}</td>
    <td><b>${_esc(disp(r.ticker||""))}</b></td></tr>`).join("")
    || `<tr><td colspan="11" class="empty">Nothing has been auto-closed.</td></tr>`;
  _sortArrows("data-iga", igaSortK, igaSortDir);
}

document.querySelectorAll("th[data-iga]").forEach(th=>th.onclick=()=>{
  const k=th.dataset.iga; igaSortDir=(igaSortK===k)?-igaSortDir:-1; igaSortK=k; paintAutoClosed();});

document.querySelectorAll("th[data-igt]").forEach(th=>th.onclick=()=>{
  const k=th.dataset.igt; igtSortDir=(igtSortK===k)?-igtSortDir:-1; igtSortK=k; paintPositionBreach();});
document.querySelectorAll("th[data-igb]").forEach(th=>th.onclick=()=>{
  const k=th.dataset.igb; igbSortDir=(igbSortK===k)?-igbSortDir:-1; igbSortK=k; paintOrderFilterAudit();});
