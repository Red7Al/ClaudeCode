// ======================================================================================================
// Best Settings — the search, and the card template, in ONE place.
//
// WHY THIS FILE EXISTS. The Best Settings cards must be visible to a logged-out visitor while the
// per-trade Transaction evidence must not (user 2026-09-01, restated 2026-09-03: "logged out users
// should see cards BUT NOT THE UNDERLYING EVIDENCE TABLE"). The cards were computed in the browser FROM
// those per-trade rows, so the only way to show a card without shipping the evidence is to compute the
// summary somewhere else and serve the summary alone.
//
// "Somewhere else" is deliberately NOT a fourth Python replay. Three wallet replays already exist in this
// repository and the third is the leading suspect in an unresolved 89.8% vs 109.2% divergence; putting a
// fourth on the PUBLIC page would publish that divergence. Instead this module holds the one definition,
// the browser loads it with a <script> tag, and run_best_settings_cards.py executes the SAME code under
// Node to precompute the public summaries. One search, one card template, two callers.
//
// The module is dependency-free and reads no globals: everything it needs arrives in `env`. That is what
// makes it runnable under Node, and it is also why the browser must pass WINNERS_WALLET, MIN_TRADE and
// friends in explicitly rather than letting the functions reach for them.
//
// Author: Alex Hind   Created: 2026-09-03
// ======================================================================================================

// Concurrency a stake can actually FUND: at a 10% position size only ten trades can be open, whatever
// number the user asked for. Shared with app.js, which clamps the same way before quoting a max-open.
const _fundedMaxOpen=stakeFrac=>Math.max(1,Math.floor(1/Math.max(0.000001,+stakeFrac||0.000001)));

const _bsEsc=s=>String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

// Very large compounded returns read better as a growth MULTIPLE than a 5-digit percentage (user
// 2026-07-27, P-03 D — "+46747.6% looks ridiculous"): >= +900% -> "x468"; below that, the usual "+12.3%".
const _bsPct=v=>v>=9?'×'+(1+v).toLocaleString(undefined,{maximumFractionDigits:(1+v)>=100?0:1})
  :(v>=0?'+':'')+(v*100).toFixed(1)+'%';

// ------------------------------------------------------------------------------------------------------
// The wallet replay.
//
// MOVED here from app.js unchanged (2026-09-03) so the browser and the Node precompute cannot drift.
// It used to read MIN_TRADE / WINNERS_WALLET / levOf / _pfExitDate as globals; those four are now the
// `env` this factory closes over. app.js builds its _combReplay from this factory with live getters, so
// changing the Wallet box still changes the replay exactly as before.
// ------------------------------------------------------------------------------------------------------
function makeCombReplay(env){
  const walletOf=()=>Math.max(1,(env.wallet?+env.wallet():0)||1);
  const minTradeOf=()=>{const v=env.minTrade?+env.minTrade():0;return typeof v==="number"&&isFinite(v)?v:0;};
  const levOf=env.leverage||(()=>1);
  const exitOf=env.exitDate||(r=>String(r&&r.exit_date||"9999-99-99").slice(0,10));
  return function _combReplay(seq,stakeFrac,maxopen,withProof=false,perfKey="perf",compound=true){
    // Event-based wallet replay: settle P&L on EXIT (not prematurely at trigger), reserve the broker
    // margin stake/leverage while each trade is open, and separately enforce the user's numeric max-open
    // cap. Blank/0 auto-derives the count cap from stake exposure: floor(100 / Max position size %).
    let w=1,peak=1,maxdd=0,peakOpen=0;const taken=[],open=[],proof=[];
    let wins=0,losses=0;   // funded-only split, reported alongside the eligible one
    const wallet=walletOf(),minTrade=minTradeOf();
    const minStake=minTrade/wallet;
    const effectiveMax=maxopen>0?Math.min(maxopen,_fundedMaxOpen(stakeFrac)):_fundedMaxOpen(stakeFrac);
    const settle=until=>{open.sort((a,b)=>a.exit.localeCompare(b.exit));
      while(open.length&&open[0].exit<=until){const x=open.shift();w+=x.net;if(w>peak)peak=w;const dd=peak>0?(peak-w)/peak:0;if(dd>maxdd)maxdd=dd;}};
    for(const r of seq){const td=r.trig_date||"";settle(td);
      const stake=(compound?w:1)*stakeFrac,lev=Math.max(1,+(levOf(r)||1)),margin=stake/lev,used=open.reduce((a,x)=>a+x.margin,0);
      if(stake+1e-12<minStake){if(withProof)proof.push({r,placed:false,open:open.length,w,stake,margin,available:Math.max(0,w-used),reason:`Below minimum trade — £${Math.round(stake*wallet)} < £${minTrade}`});continue;}
      if(open.length>=effectiveMax){if(withProof)proof.push({r,placed:false,open:open.length,w,stake,margin,reason:`Max open cap — ${effectiveMax}`});continue;}
      if(used+margin>w+1e-9){if(withProof)proof.push({r,placed:false,open:open.length,w,stake,margin,available:Math.max(0,w-used),reason:"Wallet / margin full"});continue;}
      const exit=exitOf(r,perfKey==="run_perf");
      const result=+r[perfKey];
      // Win/loss among the trades actually FUNDED (user 2026-08-28: "Win:Loss is for Eligible - show this
      // ratio for Actual also"). Counted here rather than derived afterwards because `taken` is not
      // retained per candidate — the search runs ~1M replays, so holding each funded row would be costly.
      if(result>0)wins++; else if(result<0)losses++;
      open.push({exit,margin,net:stake*result/100});taken.push(r);peakOpen=Math.max(peakOpen,open.length);
      if(withProof)proof.push({r,placed:true,open:open.length,w,stake,margin,available:Math.max(0,w-used-margin),reason:"Placed"});}
    settle("9999-99-99");
    return {ret:w-1,dd:maxdd,n:taken.length,wins,losses,cap:peakOpen,proof};
  };
}

// Return the strongest risk-adjusted option whose replay funded STRICTLY more than the requested number
// of trades, and no more than `max` (omit / 0 = no ceiling). Kept as a small pure helper so the
// >125-150 / >250-300 card boundaries and ranking are executable in the regression suite rather than
// only asserted as source text (user 2026-08-12, P-10; banded 2026-08-14, P-04).
function _bestSettingsByFundedTrades(pool,min,max){
  return [...(pool||[])].filter(x=>Number(x.n)>min&&(!max||Number(x.n)<=max))
    .sort((a,b)=>b.score-a.score||b.ret-a.ret)[0]||null;
}

// ------------------------------------------------------------------------------------------------------
// The search.
//
// env:
//   rows        annual decision rows (already filtered by whatever the caller considers tradeable)
//   rows3y      three-year rows, or null while they are still loading
//   wallet      £ wallet in the model            minTrade  £ minimum trade
//   stake       fraction (0.05 = 5%)             maxOpen   explicit concurrency cap
//   replay      the function makeCombReplay returned
//   marketsOff  markets excluded upstream, named on the "All markets" card
//   memo        {rows,wallet,minTrade,best} — the three-year memo, carried by the caller
//
// Returns everything a renderer needs and nothing it does not: `cards` are plain serialisable summaries
// (this is what the public endpoint stores), while `choices` keeps the live option objects the signed-in
// page needs for the detail panel and its funded-decision proof.
// ------------------------------------------------------------------------------------------------------
// THE SEARCHED GRID, declared once and exported.
//
// The Methodology page (user 2026-09-06: "explains how this analysis is done - I can then double check it
// with my quant resource") states these values to an external reviewer. A page that RE-TYPES them is a
// page that will eventually describe a search the code no longer performs, and a reviewer cannot tell the
// difference. So the page reads BEST_GRID and the search uses BEST_GRID -- there is one copy.
const BEST_GRID={STAKES:[1,2,3,5,7.5,10], OPENS:[3,5,8,12,20,25,50],
                 RRS:[3,5,8], QUALS:[0,50,75], VSCORES:[0,4,8], RVOLS:[0,1.5,1.8]};

function computeBestSettings(env){
  const rows=env.rows||[], rows3y=Array.isArray(env.rows3y)?env.rows3y:null;
  const wallet=Math.max(1,+env.wallet||1000), minTrade=Math.max(0,+env.minTrade||0);
  const stake=+env.stake||0.05, maxOpen=+env.maxOpen||20;
  const replay=env.replay, marketsOff=env.marketsOff||[];
  const memoIn=env.memo||{rows:null,wallet:null,minTrade:null,best:null};

  const wpRaw=rows.filter(r=>r.perf!=null&&r.trig_date);
  // Dedupe same-instrument/same-day rows (user 2026-08-07, ChangeRequest P-04, e.g. "Domino's Pizza
  // Enterprises Limited"): the scanner runs multiple independent lookback windows (daily-30/60/90/180/240
  // + weekly), and more than one can trigger the same ticker on the same day, double-counting it in the
  // annual replay. Collapse to one row per ticker/day, keeping whichever had the best recorded return.
  const _dedupeKey=r=>(r.ticker||'')+'|'+String(r.trig_date||'').slice(0,10);
  const _bestByKey={};
  wpRaw.forEach(r=>{const k=_dedupeKey(r), cur=_bestByKey[k];
    if(!cur||(+r.perf||-Infinity)>(+cur.perf||-Infinity))_bestByKey[k]=r;});
  const wp=Object.values(_bestByKey);
  if(wp.length<10)return {insufficient:true,eligibleRows:wp.length,cards:[],choices:[],unsupported:[],
                          threeYear:null,bestThreeYear:null,memo:memoIn};

  // Two-stage annual search (P-01, user 2026-08-05): first rank metric/filter candidates using the
  // current Model, then replay only the strongest candidates across stake/open grids. This covers the
  // collected decision metrics without freezing the browser under a combinatorial full cross-product.
  const STAKES=BEST_GRID.STAKES, OPENS=BEST_GRID.OPENS, RRS=BEST_GRID.RRS, QUALS=BEST_GRID.QUALS,
        VSCORES=BEST_GRID.VSCORES, RVOLS=BEST_GRID.RVOLS, BOOLS=[false,true];
  const byDate=wp.slice().sort((a,b)=>(a.trig_date||'').localeCompare(b.trig_date||'')||(a.ticker||'').localeCompare(b.ticker||''));
  const topScopes=(key,label)=>Object.entries(byDate.reduce((m,r)=>{const v=r[key];if(v)m[v]=(m[v]||0)+1;return m;},{}))
    .filter(([,n])=>n>=30).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([v])=>({kind:key,label:`${label}: ${v}`,test:r=>r[key]===v,value:v}));
  // Sector is deliberately NOT a scope (user 2026-08-14, P-05 "We cannot filter by SECTOR so little point
  // in these settings"): the live trade gate (_user_trade_allows) enforces direction, location and the
  // Markets on/off switches only, so a sector-scoped recommendation can never be applied to real trading.
  // Market and MCap bands ARE enforceable, so those stay.
  //
  // "All markets" overstated what was replayed (user 2026-08-28: the Balanced card said All markets while
  // Shanghai was switched off in settings). `label` stays as the comparison key used by changedFor /
  // matchesCurrent; `display` is what the card shows.
  const _allLabel=marketsOff.length?`All enabled markets (${marketsOff.length} off)`:"All markets";
  const scopes=[{kind:"all",label:"All markets",display:_allLabel,offList:marketsOff,test:()=>true},...topScopes("market","Market"),
    {kind:"mcap",label:"MCap < 2bn",min:0,max:2e9,test:r=>r.mcap!=null&&r.mcap<2e9},
    {kind:"mcap",label:"MCap 2–10bn",min:2e9,max:1e10,test:r=>r.mcap>=2e9&&r.mcap<1e10},
    {kind:"mcap",label:"MCap 10–100bn",min:1e10,max:1e11,test:r=>r.mcap>=1e10&&r.mcap<1e11},
    {kind:"mcap",label:"MCap 100bn+",min:1e11,max:0,test:r=>r.mcap>=1e11}];
  const robust=(seq,st,mo)=>{const x=replay(seq,st,mo), periods={};
    seq.forEach(r=>{const d=String(r.trig_date||"");if(d.length>=7){const p=d.slice(0,4)+" Q"+(Math.floor((+d.slice(5,7)-1)/3)+1);(periods[p]||(periods[p]=[])).push(r);}});
    const prs=Object.values(periods).map(s=>replay(s,st,mo).ret),pos=prs.filter(v=>v>0).length,cons=prs.length?pos/prs.length:0;
    return {...x,periods:prs.length,posPeriods:pos,consistency:cons,score:(x.ret/(x.dd+.02))*(.5+.5*cons)*Math.min(1,x.n/40)};};
  const candidates=[];
  for(const scope of scopes)for(const rr of RRS)for(const q of QUALS)for(const vs of VSCORES)for(const rv of RVOLS)for(const vw of BOOLS)for(const atr of BOOLS){
    const seq=byDate.filter(r=>scope.test(r)&&r.rr!=null&&r.rr>=rr&&(!q||(r.quality!=null&&r.quality>=q))&&
      (!vs||(r.volume_score!=null&&r.volume_score>=vs))&&(!rv||(r.rvol!=null&&r.rvol>=rv))&&(!vw||r.above_vwap===true)&&(!atr||r.atr_expanding===true));
    if(seq.length<20)continue;
    // Cheap pre-ranking across the complete configuration grid. The funded replay is much more
    // expensive; only the strongest candidates reach it below.
    const mean=seq.reduce((sum,r)=>sum+(+r.perf||0),0)/seq.length;
    const quick=mean*Math.min(1,seq.length/40);
    candidates.push({scope,rr,q,vs,rv,vw,atr,seq,quick});
  }
  candidates.sort((a,b)=>b.quick-a.quick);
  const shortlist=candidates.slice(0,40);
  shortlist.forEach(c=>{c.pre=robust(c.seq,Math.max(.01,stake),maxOpen);});
  shortlist.sort((a,b)=>b.pre.score-a.pre.score);
  let best=null, evaluated=[];
  // replay clamps Max open to what the stake can actually fund (its effectiveMax), so a requested 12 at a
  // 10% position runs as 10. Recording the REQUEST produced two faults: the card advertised "10% position
  // - 12 max open", a 120%-of-wallet setup that never existed and was never tested (user 2026-08-15 "how
  // is that possible?"), and Apply wrote that untested 12 into the user's config. Normalise to the
  // effective cap here, once, and skip the duplicates.
  const _seenMo=new Set();
  for(const c of shortlist.slice(0,12))for(const mo of OPENS)for(const st of STAKES){
    const eff=Math.min(mo,_fundedMaxOpen(st/100));
    const dedupe=c.scope.label+"|"+c.rr+"|"+c.q+"|"+c.vs+"|"+c.rv+"|"+c.vw+"|"+c.atr+"|"+st+"|"+eff;
    if(_seenMo.has(dedupe))continue; _seenMo.add(dedupe);
    const z=robust(c.seq,st/100,eff);if(z.n<20)continue;
    const option={...c,st,mo:eff,...z}; evaluated.push(option);
    if(!best||z.score>best.score)best=option;
  }
  if(!best)return {insufficient:true,eligibleRows:wp.length,cards:[],choices:[],unsupported:[],
                   threeYear:null,bestThreeYear:null,memo:memoIn};

  const cfgFor=x=>{const limits={min_risk_reward:x.rr,min_quality:x.q,min_volume_score:x.vs,min_rvol:x.rv,max_position_pct:x.st,max_open:x.mo,
      require_above_vwap:x.vw?1:0,require_atr_expanding:x.atr?1:0,min_instrument_value:0,max_instrument_value:0};
    if(x.scope.kind==="mcap"){limits.min_instrument_value=x.scope.min||0;limits.max_instrument_value=x.scope.max||0;}
    const market=x.scope.kind==="market"?x.scope.value:"", sector="";   // never a recommended scope (P-05); applying a card also CLEARS any saved sector restriction
    const filters={f_mkt:market,f_sec:sector,pof_market:market,pof_sector:sector};
    return {limits,filters};};
  const eligible=evaluated.filter(x=>x.n>=20), positive=eligible.filter(x=>x.ret>0);
  // The ordinary recommendation search intentionally concentrates on the strongest risk-adjusted
  // finalists, which can be narrow populations. That is the wrong search for the explicit >125/>250
  // evidence cards, so run a bounded, dedicated large-sample search across both high-quality and
  // widest-population candidates.
  const minLargeStakePct=Math.max(.1,minTrade/wallet*100),
        largeStakes=[...new Set([minLargeStakePct,.25,.5,1,2].filter(v=>v>=minLargeStakePct&&v<=100).map(v=>+v.toFixed(4)))],
        // Smaller concurrency caps matter now the cards are BANDS: landing inside 126-150 funded trades
        // usually means holding fewer positions at once, not more.
        largeOpens=[10,20,35,50,100,250,400],
        robustFor=(c,st,mo)=>{const cache=c._lz||(c._lz={}),k=st+"|"+mo;return cache[k]||(cache[k]=robust(c.seq,st/100,mo));},
        // Funded-sample BAND search (user 2026-08-14, P-04): "> 125 Trades (but do not exceed 150)" and
        // "> 250 Trades (but do not exceed 300)". An open-ended floor kept returning whichever setting
        // simply traded the most, which is a different question from "the best setting at this sample
        // size"; capping the band answers the question that was actually asked.
        largeSampleOptions=(min,max)=>{
          const pool=candidates.filter(c=>c.seq.length>min),
                finalists=[...pool.slice().sort((a,b)=>b.quick-a.quick).slice(0,10),
                           ...pool.slice().sort((a,b)=>b.seq.length-a.seq.length||b.quick-a.quick).slice(0,10)],
                unique=[...new Set(finalists)], options=[];
          const seenMo=new Set();
          for(const c of unique)for(const st of largeStakes)for(const mo of largeOpens){
            const eff=Math.min(mo,_fundedMaxOpen(st/100));          // same clamp as the main grid above
            const key=unique.indexOf(c)+"|"+st+"|"+eff;
            if(seenMo.has(key))continue; seenMo.add(key);
            const z=robustFor(c,st,eff);if(z.n>min&&(!max||z.n<=max))options.push({...c,st,mo:eff,...z});
          }
          return options;
        },
        large125=largeSampleOptions(125,150),large250=largeSampleOptions(250,300);
  // Options must represent different decisions, not cosmetic variations. Prefer at least two changed
  // dimensions; if the tested population cannot support that, allow one difference but never duplicate.
  const configDistance=(a,b)=>['rr','q','vs','rv','vw','atr','st','mo'].reduce((n,k)=>n+(a[k]!==b[k]?1:0),0)+
    (a.scope.label===b.scope.label?0:1);
  const basePool=positive.length?positive:eligible;
  const choose=(pool,chosen,compare)=>{
    const ranked=[...pool].sort((a,b)=>compare(a,b)||b.score-a.score);
    // Prefer a candidate whose SCOPE differs from every already-chosen card, on top of the existing
    // "materially different overall configuration" bar (user 2026-08-11, P-03: "3 of the 4 cards are
    // using the same sector"). A PREFERENCE, not a requirement: it only ever picks a scope-diverse
    // candidate that already clears the same evidence bar, and falls through to the original two tiers.
    return ranked.find(x=>chosen.every(y=>configDistance(x,y)>=2 && x.scope.label!==y.scope.label))
        ||ranked.find(x=>chosen.every(y=>configDistance(x,y)>=2))
        ||ranked.find(x=>chosen.every(y=>configDistance(x,y)>=1))||null;
  };
  // Win:loss and capital efficiency, measured over each candidate's own eligible population.
  // Ratio, not win rate: the user asked for "best win:loss ratio". Break-even trades (|perf| <= 0.5)
  // count as neither, and a candidate with no losses at all sorts above one with any — Infinity is the
  // honest ranking there, not a divide-by-zero guard.
  const _wl=x=>{const w=x.seq.filter(r=>+r.perf>0.5).length,l=x.seq.filter(r=>+r.perf<-0.5).length;
    return l?w/l:(w?Infinity:0);};
  // Return per DAY of capital committed. A configuration earning 20% while holding positions for three
  // weeks is doing more with the book than one earning 25% over six months: unresolved positions hold
  // their slot, the book saturates, and every later setup is refused on the max-open cap.
  const _days=x=>{const d=x.seq.map(r=>{if(!r.exit_date||!r.trig_date)return null;
      const a=new Date(r.trig_date+"T00:00:00Z"),b=new Date(String(r.exit_date).slice(0,10)+"T00:00:00Z");
      return isNaN(a)||isNaN(b)?null:Math.max(1,(b-a)/86400000);}).filter(v=>v!=null);
    return d.length?d.reduce((s,v)=>s+v,0)/d.length:null;};
  const _perDay=x=>{const d=_days(x);return d?x.ret/d:-Infinity;};
  // Win:loss shown ON a card (user 2026-08-23). DECLARED HERE, above the card selection, because the
  // Best win:loss card is now RANKED on it -- and `const` has a temporal dead zone, so leaving it
  // further down threw a ReferenceError that node --check cannot see.
  //
  // Deliberately the SAME split the detail panel uses (user 2026-08-23). Deliberately the SAME split the detail panel uses —
  // perf > 0 and perf < 0 over the eligible trades — because clicking the card opens that panel directly
  // beneath it, and two different counts for one configuration would read as a bug.
  //
  // NOT the same as _wl() above, which RANKS candidates and applies a +/-0.5% break-even dead-band. That
  // is a selection heuristic; this is a displayed count.
  //
  // Two populations, deliberately both shown (user 2026-08-28: "Win:Loss is for Eligible - show this
  // ratio for Actual also"). ELIGIBLE is every trade matching the configuration's filters; ACTUAL is only
  // those the wallet could fund.
  const _cardWL=x=>{const s=x.seq||[],w=s.filter(r=>r.perf>0).length,l=s.filter(r=>r.perf<0).length;
    return {w,l,pct:(w+l)?Math.round(w/(w+l)*100):null};};
  const _cardWLActual=x=>{const w=+x.wins||0,l=+x.losses||0;
    return {w,l,pct:(w+l)?Math.round(w/(w+l)*100):null};};
  const chosen=[best];
  const growth=choose(basePool,chosen,(a,b)=>b.ret-a.ret);if(growth)chosen.push(growth);
  const defensive=choose(basePool,chosen,(a,b)=>a.dd-b.dd);if(defensive)chosen.push(defensive);
  const broad=choose(basePool,chosen,(a,b)=>b.consistency-a.consistency||b.n-a.n||b.ret-a.ret);if(broad)chosen.push(broad);
  // BEST WIN:LOSS -- ranked on the number the card DISPLAYS, and not put through choose().
  //
  // BEST WIN:LOSS is chosen LAST, and only if it is genuinely the best on the page.
  //
  // First reported 2026-09-06 ("growth card with better win:loss ratio than the card illustrating best
  // win:loss"), and reported AGAIN after the first attempt: "still nonsense as it is not the best on the
  // page". That verdict was right, and the first fix deserved it. It ranked on the eligible ratio and
  // explained the discrepancy in the subtitle -- but every card prints TWO win:loss rows, and on the
  // ACTUAL (funded) row three siblings still beat it: measured on the live 4,168-row annual set, this
  // card showed 1.067 while Balanced showed 1.412, Short Duration 1.200 and Defensive 1.154. A caption
  // cannot rescue a card whose own printed number contradicts its title.
  //
  // So the claim is now enforced rather than described. The card is selected after every other card is
  // known, and the candidate must beat all of them on BOTH printed ratios. Eligible is a property of the
  // configuration; actual depends on what the wallet could fund. A card titled "best" must lead on the
  // number the reader is looking at, whichever of the two that is.
  //
  // If nothing clears both, the card is NOT SHOWN. That is the honest outcome: some populations have no
  // configuration that is best on both, and publishing a false superlative is worse than publishing one
  // card fewer. The other cards already cover the ground.
  const _cardWLRatio=x=>{const c=_cardWL(x);return c.l?c.w/c.l:(c.w?Infinity:0);};
  const _cardWLActualRatio=x=>{const c=_cardWLActual(x);return c.l?c.w/c.l:(c.w?Infinity:0);};
  const efficient=choose(basePool,chosen,(a,b)=>_perDay(b)-_perDay(a));if(efficient)chosen.push(efficient);
  const shortDuration=choose(basePool,chosen,(a,b)=>(_days(a)??Infinity)-(_days(b)??Infinity)||b.ret-a.ret);if(shortDuration)chosen.push(shortDuration);
  // Best settings at a given funded-trade sample BAND. Deliberately NOT run through choose()'s difference
  // bar: the DEFINING property here is the sample size, not being unlike the other cards.
  const trades125=_bestSettingsByFundedTrades(large125,125,150), trades250=_bestSettingsByFundedTrades(large250,250,300);

  // Every annual card that will appear, so the win:loss claim can be tested against all of them.
  const _annual=[best,growth,defensive,broad,efficient,shortDuration,trades125,trades250].filter(Boolean);
  const _beatsAll=x=>_annual.every(o=>o===x||
    (_cardWLRatio(x)>=_cardWLRatio(o)-1e-9 && _cardWLActualRatio(x)>=_cardWLActualRatio(o)-1e-9));
  const winloss=[...basePool,...large125,...large250]
    .filter(x=>!_annual.includes(x))
    .sort((a,b)=>(_cardWLRatio(b)+_cardWLActualRatio(b))-(_cardWLRatio(a)+_cardWLActualRatio(a))
                 ||b.ret-a.ret||b.score-a.score)
    .find(_beatsAll)||null;
  if(winloss)chosen.push(winloss);

  // Three-year evidence card (ChangeRequests/20260818, clarified 2026-08-20).
  //
  // MEMOISED 2026-08-29 (user: "'Apply this configuration' is timing out"). The three-year search is the
  // freeze: MEASURED at 962,010 replays over 44.8 million row-visits, 52-61 s of blocked main thread,
  // about 90% of the total. It was recomputed on EVERY re-render — and Apply triggers one.
  //
  // The key is the complete set of inputs the expensive part actually reads: the three-year rows
  // themselves, plus the wallet and minimum trade, which the replay uses for its minimum-stake floor.
  // Max position size and Max open are NOT in the key because the grid searches those itself, which is
  // exactly why changing them can reuse this.
  let bestThreeYear=null, memoOut=memoIn;
  if(memoIn.rows===env.rows3y&&memoIn.wallet===wallet&&memoIn.minTrade===minTrade){
    bestThreeYear=memoIn.best;
  }else{
    const threeYearRaw=rows3y||[], threeYearByKey={};
    threeYearRaw.filter(r=>r.perf!=null&&r.trig_date).forEach(r=>{const k=_dedupeKey(r),cur=threeYearByKey[k];if(!cur||(+r.perf||-Infinity)>(+cur.perf||-Infinity))threeYearByKey[k]=r;});
    const threeYearRows=Object.values(threeYearByKey), threeYearCandidates=[];
    // This is a genuine three-year optimisation: it searches the complete retained three-year population
    // AND replays every generated, enforceable configuration. Do not reuse an annual finalist shortlist
    // or a quick-score prune here: either can exclude the true three-year optimum (user 2026-08-20).
    const threeMarkets=Object.entries(threeYearRows.reduce((m,r)=>{const v=r.market;if(v)m[v]=(m[v]||0)+1;return m;},{}))
      .filter(([,n])=>n>=30).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([v])=>({kind:"market",label:`Market: ${v}`,test:r=>r.market===v,value:v}));
    const threeScopes=[{kind:"all",label:"All markets",display:_allLabel,offList:marketsOff,test:()=>true},...threeMarkets,...scopes.filter(s=>s.kind==="mcap")];
    for(const scope of threeScopes)for(const rr of RRS)for(const q of QUALS)for(const vs of VSCORES)for(const rv of RVOLS)for(const vw of BOOLS)for(const atr of BOOLS){
      const seq=threeYearRows.filter(r=>scope.test(r)&&r.rr!=null&&r.rr>=rr&&(!q||(r.quality!=null&&r.quality>=q))&&
        (!vs||(r.volume_score!=null&&r.volume_score>=vs))&&(!rv||(r.rvol!=null&&r.rvol>=rv))&&(!vw||r.above_vwap===true)&&(!atr||r.atr_expanding===true));
      if(seq.length<20)continue;
      const mean=seq.reduce((sum,r)=>sum+(+r.perf||0),0)/seq.length;
      threeYearCandidates.push({scope,rr,q,vs,rv,vw,atr,seq,quick:mean*Math.min(1,seq.length/40)});
    }
    const threeSeen=new Set(), threeEvaluated=[];
    for(const c of threeYearCandidates)for(const mo of OPENS)for(const st of STAKES){
      const eff=Math.min(mo,_fundedMaxOpen(st/100)), key=c.scope.label+"|"+c.rr+"|"+c.q+"|"+c.vs+"|"+c.rv+"|"+c.vw+"|"+c.atr+"|"+st+"|"+eff;
      if(threeSeen.has(key))continue; threeSeen.add(key);
      const z=robust(c.seq,st/100,eff);if(z.n>=20)threeEvaluated.push({...c,st,mo:eff,...z});
    }
    // RANKED BY RETURN, not by risk-adjusted score (user 2026-09-01: "the cards are for return").
    // MEASURED on the 2026-08-31 payload before this change shipped: score-ranked gave 109.2% (dd 2.9%,
    // 532 trades, 3% stake, 33 open); return-ranked gives 161.0% (dd 5.1%, 139 trades, 10% stake, 10
    // open). Ties on return fall back to score, so the safer of two equal-return configurations wins.
    bestThreeYear=threeEvaluated.sort((a,b)=>b.ret-a.ret||b.score-a.score)[0]||null;
    memoOut={rows:env.rows3y,wallet,minTrade,best:bestThreeYear};
  }
  // The evidence rule — more than 125 funded trades AND within 20% of the best card's return — decides
  // whether this is a RECOMMENDATION. It no longer decides whether the configuration can be APPLIED
  // (user 2026-08-28, raised twice: a card showing +183.6% on 122 funded trades still had no button).
  const threeYearSupported=!!(bestThreeYear&&bestThreeYear.n>125&&bestThreeYear.ret>=best.ret*.8);
  const threeYear=threeYearSupported?bestThreeYear:null;

  const choices=[
    ["Balanced",best,"Best return relative to drawdown, with quarterly consistency included.","var(--bull)"],
    growth&&["Growth",growth,"Highest-return alternative with a materially different configuration.","var(--accent)"],
    defensive&&["Defensive",defensive,"Lowest historical peak-to-trough drawdown while retaining a positive return.","#d29922"],
    broad&&["Broad evidence",broad,"Favours positive quarters and a larger funded trade sample over a narrow winner.","var(--muted)"],
    trades125&&[">125 trades",trades125,"The strongest risk-adjusted configuration among those that funded 125-150 trades — more selective than the band below it, over the same full 12 months.","#1f6feb"],
    trades250&&["Most consistent returns",trades250,"The strongest risk-adjusted configuration among those that funded 250-300 trades. Every card is replayed over the SAME full 12 months — this one is picked from the configurations that put money to work most often, so its return rests on more trades than any other card here.","#a371f7"],
    // The subtitle names the POPULATION the superlative is measured over, because the card prints two
    // win:loss rows and they can disagree (user 2026-09-06: "seeing growth card with better win:loss
    // ratio than the card illustrating best win:loss"). Measured on the live 4,168-row annual set that
    // day: this card led on ELIGIBLE at 1.600, while Balanced (1.412), Short Duration (1.200) and
    // Defensive (1.154) all beat its ACTUAL 1.067.
    //
    // That is not an error in either number. ELIGIBLE is every trade matching the configuration and is
    // a property of the configuration alone; ACTUAL is only the subset the wallet could fund, so it
    // moves with wallet, position size and the max-open cap, and differs between two people reading the
    // same card. A superlative can only be claimed over the population that does not depend on the
    // reader, so the title means ELIGIBLE and now says so.
    winloss&&["Best win : loss",winloss,`Most winners per loser among ELIGIBLE trades — ${_cardWLRatio(winloss)===Infinity?"no losing trades":_cardWLRatio(winloss).toFixed(2)+" wins per loss"}. The funded row below can differ: it counts only the trades this wallet could pay for, so it moves with your position size and open-position cap.`,"#3fb950"],
    efficient&&["Capital efficient",efficient,`Most return per day of capital committed — ${_days(efficient)?Math.round(_days(efficient))+" days average hold":"hold unknown"}. Keeps the book turning instead of tying slots up.`,"#f778ba"]
  ].filter(Boolean);
  if(threeYear)choices.push(["Best over 3 years",threeYear,"Three-year replay with more than 125 funded trades and at least 80% of the best card’s return (20% relative tolerance).","#00a8a8"]);
  if(shortDuration)choices.push(["Short Duration",shortDuration,`Shortest average completed holding period — ${_days(shortDuration)?Math.round(_days(shortDuration))+" days average hold":"hold unknown"}.`,"#58a6ff"]);
  // Requested display order. Sorting presentation does not change any candidate's calculation.
  const cardOrder=["Best win : loss","Capital efficient","Balanced","Growth","Short Duration","Broad evidence","Most consistent returns",">125 trades","Best over 3 years","Defensive"];
  choices.sort((a,b)=>cardOrder.indexOf(a[0])-cardOrder.indexOf(b[0]));

  // Three ROLLING 365-day windows for a card's configuration (user 2026-09-01: "I want to see if these
  // settings are only good for this year or all years. A year in this card is not a calendar year - it is
  // the previous 365 days").
  //
  // The annual cards are SELECTED on the last 365 days, so those earlier windows have to come from the
  // three-year population. The configuration is held FIXED and only the period changes — otherwise this
  // would be three separate optimisations and would prove nothing about overfitting.
  const _yrEdges=(()=>{const rs=rows3y||[];
    if(!rs.length)return null;
    const latest=rs.reduce((m,r)=>{const d=String(r&&r.trig_date||"").slice(0,10);return d>m?d:m;},"");
    if(!latest)return null;
    const back=n=>{const d=new Date(latest+"T00:00:00Z");d.setUTCDate(d.getUTCDate()-n);return d.toISOString().slice(0,10);};
    return [latest,back(365),back(730),back(1095)];})();
  const _cardYears=x=>{
    if(!_yrEdges||!rows3y||!rows3y.length)return null;
    return [0,1,2].map(i=>{const to=_yrEdges[i],from=_yrEdges[i+1];
      const seq=rows3y.filter(r=>{const d=String(r&&r.trig_date||"").slice(0,10);
        return d>from&&d<=to&&r.perf!=null&&x.scope.test(r)&&r.rr!=null&&r.rr>=x.rr&&
          (!x.q||(r.quality!=null&&r.quality>=x.q))&&(!x.vs||(r.volume_score!=null&&r.volume_score>=x.vs))&&
          (!x.rv||(r.rvol!=null&&r.rvol>=x.rv))&&(!x.vw||r.above_vwap===true)&&(!x.atr||r.atr_expanding===true);});
      if(!seq.length)return {from,to,ret:null,n:0};
      const z=replay(seq,x.st/100,x.mo);
      return {from,to,ret:z.ret,n:z.n};});};

  // The serialisable summary. THIS is what /api/best-settings-cards stores and serves: aggregates only,
  // never a per-trade row, so a logged-out visitor can see the card and not the evidence behind it.
  const summarise=([label,x,why,colour])=>({
    label,why,colour,ret:x.ret,dd:x.dd,n:x.n,eligible:x.seq.length,
    posPeriods:x.posPeriods,periods:x.periods,
    wlEligible:_cardWL(x),wlActual:_cardWLActual(x),
    scope:{kind:x.scope.kind,label:x.scope.label,display:x.scope.display||x.scope.label,
           offList:x.scope.offList||[],value:x.scope.value||"",min:x.scope.min||0,max:x.scope.max||0},
    rr:x.rr,q:x.q,vs:x.vs,rv:x.rv,vw:!!x.vw,atr:!!x.atr,st:x.st,mo:x.mo,
    // EVERY card gets the split, including this one (user 2026-09-06: "on the best 3 years card - show
    // the split of the 3 years within the card - just like all the other cards"). It was excluded on the
    // reasoning that the card IS the three-year replay, so a three-year line adds nothing -- but the
    // TOTAL is not the split, and the split is the whole question: a +158% that is one good year and two
    // flat ones is a different proposition from three even ones, and only the breakdown shows which.
    years:_cardYears(x),
    cfg:cfgFor(x)});

  const unsupported=[];
  if(!trades250)unsupported.push({label:"Most consistent returns",min:250,max:300,colour:"#a371f7"});
  if(!trades125)unsupported.push({label:">125 trades",min:125,max:150,colour:"#1f6feb"});

  return {
    insufficient:false,
    choices,                                    // live objects: detail panel + funded-decision proof
    cards:choices.map(summarise),               // serialisable summaries: the public payload
    unsupported,
    threeYear,bestThreeYear,
    threeYearCard:bestThreeYear?{ret:bestThreeYear.ret,dd:bestThreeYear.dd,n:bestThreeYear.n,
      supported:threeYearSupported,cfg:cfgFor(bestThreeYear),
      settings:{rr:bestThreeYear.rr,q:bestThreeYear.q,vs:bestThreeYear.vs,rv:bestThreeYear.rv,
                vw:!!bestThreeYear.vw,atr:!!bestThreeYear.atr,st:bestThreeYear.st,mo:bestThreeYear.mo,
                scope:bestThreeYear.scope.label}}:null,
    loading3y:rows3y===null,
    model:{wallet,minimum_trade:minTrade,position_pct:stake*100,max_open:maxOpen},
    dataThrough:String((byDate[byDate.length-1]||{}).trig_date||"").slice(0,10),
    eligibleRows:wp.length,
    memo:memoOut};
}

// ------------------------------------------------------------------------------------------------------
// The card template. One definition, three callers: the signed-in page, the logged-out page, and the
// regression suite. `opts.current` is the user's saved configuration (null when nobody is signed in, in
// which case the "matches yours" tick and the Changes line are simply absent — there is nothing to
// compare against). `opts.apply` is the markup for the bottom row, supplied by the caller because only
// the signed-in page has a User Configuration to write to.
// ------------------------------------------------------------------------------------------------------
const _bsDisplayValue=(key,v)=>key==="vw"||key==="atr"?(v?"Yes":"No"):key==="st"?v+"%":key==="mo"?String(v):key==="scope"?v:String(v||"Any");

function bestSettingsMatchesCurrent(c,current){
  if(!current)return false;
  const target={rr:c.rr,q:c.q,vs:c.vs,rv:c.rv,vw:c.vw,atr:c.atr,st:c.st,mo:c.mo,scope:c.scope.label};
  return Object.keys(target).every(k=>String(current[k])===String(target[k]));
}
function bestSettingsChangedFor(c,current){
  if(!current)return "";
  const target={rr:c.rr,q:c.q,vs:c.vs,rv:c.rv,vw:c.vw,atr:c.atr,st:c.st,mo:c.mo,scope:c.scope.label};
  const labels={rr:"R:R",q:"Quality",vs:"VolumeScore",rv:"RVOL",vw:"VWAP required",atr:"ATR expanding",st:"Position size",mo:"Max open",scope:"Scope"};
  return Object.keys(target).filter(k=>String(current[k])!==String(target[k]))
    .map(k=>`${labels[k]}: ${_bsDisplayValue(k,current[k])} → ${_bsDisplayValue(k,target[k])}`).join(" · ")||"No change from your current configuration";
}

const _bsWlLine=(label,d,title)=>`<div style="font-size:11px;margin-top:4px" title="${title}">${label} <b style="color:var(--fg)">${d.w} : ${d.l}</b>${d.pct!=null?` <span class="muted">(${d.pct}%)</span>`:''}</div>`;

function _bsYearsLine(c){
  const ys=c.years;
  if(!ys)return `<div class="muted" style="font-size:10px;margin-top:6px">Yearly breakdown loads with the three-year evidence.</div>`;
  const cell=y=>y.ret==null?`<span class="muted">n/a</span>`
    :`<b style="color:${y.ret>=0?'var(--bull)':'var(--bear)'}">${_bsPct(y.ret)}</b>`;
  return `<div style="font-size:10px;margin-top:6px;line-height:1.5" title="${c.label==='Best over 3 years'?'The SAME configuration replayed over each of the three rolling 365-day periods, newest first. This card was selected on all three together, so none of them is out-of-sample.':'The SAME configuration replayed over three rolling 365-day periods, newest first. The first is the window the card was chosen on (in-sample); the other two are out-of-sample.'}">
      <b style="color:var(--fg)">Each of the last 3 years:</b> ` +
    ys.map((y,i)=>`<span title="${y.from} to ${y.to} — ${y.n} funded trade${y.n===1?"":"s"}">${cell(y)}${i===0&&c.label!=="Best over 3 years"?' <span class="muted">(in-sample)</span>':''}</span>`).join(' <span class="muted">·</span> ')
    +`</div>`;
}

function bestSettingsCardHTML(c,opts){
  opts=opts||{};
  const current=opts.current||null, selected=opts.selected===c.label, matches=bestSettingsMatchesCurrent(c,current);
  const changes=current?`<div class="muted" style="font-size:10px;margin-top:7px;line-height:1.4"><b style="color:var(--fg)">Changes:</b> ${bestSettingsChangedFor(c,current)}</div>`:"";
  const onclick=opts.onSelect?` onclick="${opts.onSelect.replace(/LABEL/g,c.label.replace(/'/g,"\\'"))}" title="Show this option's detail and Transaction evidence below"`:"";
  return `<div class="fcard fcard-choice${selected?' fcard-selected':''}" data-choice="${_bsEsc(c.label)}" data-choice-return="${c.ret}" style="min-width:240px;flex:1;border-top:3px solid ${c.colour}"${onclick}>
    <h3 style="color:${c.colour}">${_bsEsc(c.label)}</h3>${matches?`<div class="fcard-current" title="Every setting on this card already matches your saved User Configuration, so applying it would change nothing">✓ This is your current configuration</div>`:''}<div class="muted" style="font-size:11px;min-height:30px">${c.why}</div>
    <div class="body"><div><b style="font-size:17px;color:${c.ret>=0?'var(--bull)':'var(--bear)'}">${_bsPct(c.ret)}</b> return · <b>${(c.dd*100).toFixed(1)}%</b> max drawdown</div>
      <div class="muted" style="font-size:11px;margin-top:4px">${c.n.toLocaleString()} funded of ${c.eligible.toLocaleString()} eligible · ${c.posPeriods}/${c.periods} positive quarters</div>
      ${_bsWlLine("Win : Loss <span class='muted'>(eligible)</span>",c.wlEligible,"Wins vs losses among every trade matching this configuration, whether or not the wallet could fund it — the same split the detail panel below reports")}
      ${_bsWlLine("Win : Loss <span class='muted'>(actual)</span>",c.wlActual,"Wins vs losses among only the trades this configuration actually FUNDED — the same population as the funded count above")}
      <div style="font-size:11px;margin-top:6px"${c.scope.offList&&c.scope.offList.length?` title="These markets are switched off in your settings and were never in this replay: ${_bsEsc(c.scope.offList.join(', '))}"`:''}>${_bsEsc(c.scope.display||c.scope.label)} · R:R ≥ ${c.rr} · Q ≥ ${c.q||'any'} · Vol ≥ ${c.vs||'any'} · RVOL ≥ ${c.rv||'any'} · ${c.vw?'VWAP required':'any VWAP'} · ${c.atr?'ATR expanding':'any ATR'} · ${c.st}% position · ${c.mo} max open</div>
      ${_bsYearsLine(c)}
      ${changes}
    </div>
      <!-- The apply row is a DIRECT child of .fcard, NOT of .body. .fcard-apply carries margin-top:auto,
           which only pushes to the bottom for a flex item of a column flex container; .fcard is one,
           .body is a plain block (index.html:311). Nested inside .body it was inert, and the choice cards
           only LOOKED aligned because their bodies hold identical rows (user 2026-08-30). -->
      ${opts.apply||""}
    </div>`;
}

function bestSettingsUnsupportedCardHTML(u){
  return `<div class="fcard" data-choice-unavailable="${_bsEsc(u.label)}" style="min-width:240px;flex:1;border-top:3px solid ${u.colour};opacity:.8" title="No tested annual configuration funded more than ${u.min} and no more than ${u.max} trades">
    <h3 style="color:${u.colour}">${_bsEsc(u.label)}</h3><div class="muted" style="font-size:11px;min-height:30px">Evidence threshold not met by the current annual dataset.</div>
    <div class="body"><b style="font-size:15px;color:#d29922">No supported recommendation</b><div class="muted" style="font-size:11px;margin-top:5px">None of the tested configurations funded more than ${u.min} and no more than ${u.max} trades. The card remains visible so an evidence shortfall is not mistaken for a missing feature.</div></div>
  </div>`;
}

// Keep the decision surface compact. The permanent three-year evidence card counts towards the screen
// limit even below its recommendation threshold, so it is never silently pushed off the page.
// Shared by both render paths so the logged-out grid trims exactly as the signed-in one does.
function trimBestSettingsCards(cards,unsupported,hasThreeYearCard,capacity){
  const out=[...(cards||[])], un=[...(unsupported||[])];
  const trimCard=label=>{const i=out.findIndex(x=>x.label===label);if(i>=0){out.splice(i,1);return true;}
    const j=un.findIndex(x=>x.label===label);if(label===">125 trades"&&j>=0){un.splice(j,1);return true;}return false;};
  while(out.length+un.length+Number(!!hasThreeYearCard)>capacity){
    if(trimCard("Defensive")||trimCard(">125 trades")||trimCard("Broad evidence"))continue;
    break;
  }
  return {cards:out,unsupported:un};
}

if(typeof module==="object"&&module.exports){
  module.exports={_fundedMaxOpen,makeCombReplay,computeBestSettings,bestSettingsCardHTML,
                  bestSettingsUnsupportedCardHTML,bestSettingsMatchesCurrent,bestSettingsChangedFor,
                  trimBestSettingsCards,_bestSettingsByFundedTrades,_bsPct,BEST_GRID};
}
