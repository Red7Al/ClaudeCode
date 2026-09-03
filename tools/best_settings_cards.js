// ======================================================================================================
// Precompute the PUBLIC Best Settings cards, using the browser's own search.
//
// Reads a JSON job on argv[2] -- {rows, rows3y, wallet, minTrade, stake, maxOpen} -- runs
// hvf_web/best_settings.js over it, and prints the summaries a logged-out visitor is allowed to see.
// run_best_settings_cards.py drives this; nothing else should.
//
// WHY NODE AND NOT PYTHON. Three wallet replays already exist in this repository and the third is the
// leading suspect in an unresolved return divergence. A fourth, written in Python for the public page,
// would publish that divergence on the one surface anonymous visitors see. Running the page's own code
// is the only way to guarantee the logged-out card and the signed-in card are the same calculation.
//
// The two helpers the replay needs that are NOT in best_settings.js -- broker leverage, and the rule for
// when a trade releases its slot -- are EXTRACTED from hvf_web/app.js rather than copied here, for the
// same reason. A failed extraction is fatal: a silently-defaulted leverage of 1x would produce numbers
// that look plausible and are wrong.
//
// Author: Alex Hind   Created: 2026-09-03
// ======================================================================================================
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const BS = require(path.join(ROOT, "hvf_web", "best_settings.js"));
const APP = fs.readFileSync(path.join(ROOT, "hvf_web", "app.js"), "utf8");

function extract(pattern, what) {
  const m = APP.match(pattern);
  if (!m) {
    console.error(`FATAL: could not extract ${what} from hvf_web/app.js. The replay would run with a `
      + `wrong default and produce plausible, incorrect cards, so nothing is published.`);
    process.exit(2);
  }
  return m[0];
}

// Broker leverage by instrument type, and the canonical "when does this trade release its slot" rule.
// Both are single-line declarations in app.js; the patterns are anchored to the start of the line so a
// mention inside a comment cannot match.
const pieces = [
  extract(/^let LEVERAGE=\{[^}]*\};/m, "LEVERAGE"),
  extract(/^function levType\(r\)\{.*$/m, "levType"),
  extract(/^const levOf=.*$/m, "levOf"),
  extract(/^function _pfAddDays\(d,n\)\{.*$/m, "_pfAddDays"),
  extract(/^function _pfExitDate\(r,runner\)\{[\s\S]*?\n\}/m, "_pfExitDate"),
];
const helpers = new Function(`${pieces.join("\n")}\nreturn {levOf,_pfExitDate};`)();

const jobPath = process.argv[2];
if (!jobPath) { console.error("usage: node tools/best_settings_cards.js <job.json>"); process.exit(2); }
const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));

const wallet = +job.wallet || 10000, minTrade = +job.minTrade || 25;
const stake = +job.stake || 0.05, maxOpen = +job.maxOpen || 20;
const replay = BS.makeCombReplay({
  wallet: () => wallet, minTrade: () => minTrade,
  leverage: r => helpers.levOf(r), exitDate: (r, runner) => helpers._pfExitDate(r, runner),
});

const started = Date.now();
const res = BS.computeBestSettings({
  rows: job.rows || [], rows3y: job.rows3y || [],
  wallet, minTrade, stake, maxOpen, replay,
  // No user is signed in, so no market is switched off: this is the unrestricted replay, and the card
  // says "All markets" because that is what it searched.
  marketsOff: [],
});

if (res.insufficient) {
  console.error(`no cards: only ${res.eligibleRows} usable trades after dedupe`);
  process.stdout.write(JSON.stringify({ cards: [], unsupported: [], insufficient: true }));
  process.exit(0);
}

// SUMMARIES ONLY. `cfg` is dropped because the logged-out page has no User Configuration to apply it to,
// and `choices` -- which carries every per-trade row -- is never touched. This is the whole point of the
// file: what leaves here cannot reconstruct the Transaction evidence.
const t = res.threeYear || res.bestThreeYear;
const payload = {
  cards: res.cards.map(c => { const { cfg, ...rest } = c; return rest; }),
  unsupported: res.unsupported,
  recommended3y: !!res.threeYear,
  threeYear: t ? { ret: t.ret, dd: t.dd, n: t.n, settings: res.threeYearCard.settings } : null,
  model: res.model,
  data_through: res.dataThrough,
  eligible_rows: res.eligibleRows,
  compute_seconds: Math.round((Date.now() - started) / 100) / 10,
};
console.error(`${payload.cards.length} cards from ${res.eligibleRows} annual rows in ${payload.compute_seconds}s`);
process.stdout.write(JSON.stringify(payload));
