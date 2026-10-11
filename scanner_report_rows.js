// The Scanner Report rows a login sees on first opening the page -- computed by the page's OWN code.
//
// WHY (owner 2026-10-09, P-01 "SCANNER REPORT EMAIL STILL USES THE OLD RULE"): the emailed report kept a
// second, Python copy of the screen's filters, judged VWAP/ATR on the latest bar and had no trigger-date
// rule, so the email and the screen disagreed. This file holds NO filter logic of its own: it lifts
// augment(), tradeVisible(), pass() and their helpers out of the shipped hvf_web/app.js and runs them over
// the login's own /api/records and /api/config payloads, so a change to the page changes the email.
//
// stdin : {"records": <the /api/records JSON>, "config": <the /api/config JSON>}
// stdout: the rows as JSON, in the page's default order (Days since ascending, blanks last -- render()).
const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(path.join(__dirname, "hvf_web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const fn = name => {
  const a = APP.indexOf(`function ${name}(`);
  if (a < 0) throw new Error(`app.js has no function ${name}`);
  return APP.slice(a, APP.indexOf("\n}\n", a) + 3);
};
const line = prefix => {
  const l = APP.split("\n").find(s => s.startsWith(prefix));
  if (!l) throw new Error(`app.js has no line starting ${prefix}`);
  return l;
};
// The page's default sort, read from its own declaration rather than restated here.
const decl = line("let DATA=[]");
const sortK = /sortK="([^"]+)"/.exec(decl)[1], sortDir = +/sortDir=(-?\d+)/.exec(decl)[1];
const signalOnly = /signalOnly=(true|false)/.exec(decl)[1] === "true";

const src = [line("const num="), line("const disp="), line("const SEP="),
             fn("daysSince"), fn("augment"), fn("setOf"), fn("inSet"), fn("tradeVisible"), fn("pass"),
             fn("applyUserDefaults")].join("\n");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const cfg = input.config || {};
// The page's elements, as on first load: empty search, the login's saved filter defaults applied, then the
// built-in Scanner default Status = TRIGGERED unless the login saved a Status of their own
// (_seedStatusDefaults -- its "TRIGGERED" literal is the only value restated here).
const EL = {f_search: {value: ""}, mf_direction: {value: ""}, mf_location: {value: ""}, mf_market: {value: ""},
            mf_sector: {value: ""}, mf_status: {value: ""}, mf_timeframe: {value: ""}};
const run = new Function("EL", "cfg", "records", "signalOnly", "sortK", "sortDir", `
  const $ = id => EL[id] || null;
  const POS = {}, PUB = {};
  let TRADE_HIDE = (cfg && cfg.trade) || {};
  let MARKETS_DISABLED = new Set((cfg && cfg.markets_disabled) || []), MARKETS_OFF = new Set((cfg && cfg.markets_off) || []);
  let MY_LIMITS = (cfg && cfg.limits) || {};
  let USER_FILTERS = (cfg && cfg.filters) || {};
  ${src}
  applyUserDefaults();
  if (!(USER_FILTERS && USER_FILTERS.mf_status) && !EL.mf_status.value) EL.mf_status.value = "TRIGGERED";
  const DATA = (records && records.records) || [];
  DATA.forEach(augment);
  const _sv = r => sortK === "dist_entry" ? (r.dist_entry == null ? 1e9 : Math.abs(r.dist_entry)) : r[sortK];
  return DATA.filter(r => pass(r)).sort((a, b) => {
    const x = _sv(a), y = _sv(b); if (x == null || y == null) return x == null && y == null ? 0 : x == null ? 1 : -1;
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
`);
process.stdout.write(JSON.stringify(run(EL, cfg, input.records, signalOnly, sortK, sortDir)));
