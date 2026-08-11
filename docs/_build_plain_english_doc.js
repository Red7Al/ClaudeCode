// Builds docs/How_The_Trading_System_Works_PlainEnglish.docx — a non-technical guide.
// Run: NODE_PATH=$(npm root -g) node docs/_build_plain_english_doc.js
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, HeadingLevel, AlignmentType,
  LevelFormat, BorderStyle, PageNumber, Header, Footer, Table, TableRow, TableCell,
  WidthType, ShadingType
} = require("docx");

const IMG = (f) => fs.readFileSync(path.join(__dirname, "img", f));
const ACCENT = "2E5BBA";

function img(file, w, h, title) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 120, after: 60 },
    children: [new ImageRun({ type: "png", data: IMG(file),
      transformation: { width: w, height: h },
      altText: { title, description: title, name: file } })]
  });
}
function caption(t) {
  return new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: t, italics: true, size: 18, color: "666666" })] });
}
function p(text, opts = {}) {
  return new Paragraph({ spacing: { after: 140 }, ...opts,
    children: [new TextRun({ text, size: 22 })] });
}
function bullet(text) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 },
    children: [new TextRun({ text, size: 22 })] });
}
function h1(text) { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] }); }
function h2(text) { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] }); }

const doc = new Document({
  creator: "A&A Trading",
  title: "How the Trading Signal System Works",
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "333333" },
        paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 1 } },
    ]
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•",
      alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 260 } } } }] },
  ]},
  sections: [{
    properties: { page: {
      size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ text: "A plain-English guide  ·  Page ", size: 16, color: "888888" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "888888" }),
      ] })] }) },
    children: [
      // ── Title ──
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 1200, after: 100 },
        children: [new TextRun({ text: "How the Trading Signal System Works", bold: true, size: 48, color: ACCENT })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
        children: [new TextRun({ text: "A plain-English guide — no jargon required", size: 26, color: "555555" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 1200 },
        children: [new TextRun({ text: "June 2026", size: 22, color: "888888" })] }),

      p("This guide explains, in everyday language, how our system spots promising trading opportunities, " +
        "how it decides which ones are worth flagging, and where the results end up. You do not need any " +
        "trading or technical background to follow it."),
      new Paragraph({ children: [] }),

      // ── 1 ──
      h1("1. The big picture"),
      p("Every day the system automatically looks at hundreds of markets — shares, indices, gold, oil and " +
        "currencies — and asks one question for each: “is this setting up for a worthwhile move?”"),
      p("It is looking for a very specific, repeatable pattern (explained next). When it finds one, it works out " +
        "where you would get in, where you would get out if wrong, and where it could reasonably go if right. " +
        "It then ranks the best opportunities and shares them. It does this the same way every time, with no " +
        "emotion and no guesswork."),

      // ── 2 ──
      h1("2. The core idea — the “coiling spring”"),
      p("The method we use is called The Squeeze. The idea is simple to picture. After a market " +
        "has had a strong run in one direction, it often pauses and “coils” — the price swings get " +
        "smaller and smaller, squeezing into a tighter and tighter range, like a spring being wound up. When the spring " +
        "finally releases, the price often moves quickly in the direction of the original trend."),
      img("hvf_funnel.png", 560, 304, "The squeeze pattern"),
      caption("The squeeze: the ceiling (red) drifts down while the floor (green) rises, squeezing price to a point. " +
              "The system marks where to get in, where the safety exit (stop) sits, and a realistic target."),
      p("Three things matter: the highs keep getting lower, the lows keep getting higher, and the gap between " +
        "them shrinks. That squeeze is the “wound spring.” The tighter it gets, the more meaningful the " +
        "eventual breakout."),
      p("This works both ways. After a strong rise, a coil that breaks upward is a “bullish” setup; after a strong " +
        "fall, the same coil breaking downward is a “bearish” one. The system only ever calls it in the direction " +
        "the market is actually trending — it will not flag a bullish idea on a market that has been falling."),
      h2("How far it could go — the target (AMP1)"),
      p("Every squeeze has a height: the distance from the first high to the first low of the coil. The method " +
        "calls this the amplitude, or AMP1 (you can see it marked on the picture above). When the spring releases, " +
        "the system projects that FULL height from the middle of the squeeze, in the breakout direction, to set the " +
        "target. It uses the full amplitude — not a watered-down version — which is deliberate, and it is why some " +
        "setups show a large reward-to-risk."),
      p("So the three trade levels come straight from the squeeze: the entry is the breakout level itself (the third " +
        "pivot); the safety exit (stop) sits just beyond the opposite side of the coil; and the target is one full " +
        "AMP1 away from the middle. Reward-to-risk is then simply the distance to the target divided by the distance " +
        "to the stop."),

      // ── 3 ──
      h1("3. The rules and their thresholds"),
      p("Not every wobble counts. A setup must pass five checks before the system treats it as real, then clear two " +
        "more gates to be traded and published. The exact thresholds are below — these are the actual numbers the " +
        "system uses, not approximations:"),
      rulesTable(),
      p("Only when all five rules pass does the system measure up the trade; the two gates then decide whether it is " +
        "tradeable and whether it is published. Everything is checked the same way every time.", { spacing: { before: 160, after: 140 } }),

      // ── 4 ──
      h1("4. What goes into the decision"),
      p("The headline number is the reward-to-risk ratio (often written “R:R”). If a trade risks £1 " +
        "to potentially make £3, that is 3:1. The system leads with this — the better the reward for the " +
        "risk, the higher the idea ranks."),
      p("Reward-to-risk comes first, then the readiness of the signal, then a quality score for how clean the " +
        "pattern is. The picture below shows that order of priority:"),
      img("weighting.png", 520, 259, "How setups are ranked"),
      caption("Reward-for-risk is the main driver; the signal’s readiness and the pattern’s quality settle ties."),
      h2("How the ranking is calculated"),
      p("The ranking is a simple, fixed recipe — every idea is scored on three things, compared in this exact order:"),
      bullet("Reward-to-risk, highest first — the single biggest driver."),
      bullet("Signal readiness — already breaking out (“triggered”) ranks above armed-and-ready, which ranks above " +
             "still-developing."),
      bullet("Pattern quality (0–100), highest first — used to settle any remaining ties."),
      p("In other words: sort everything by reward-to-risk; where two ideas are level, the more ready one wins; and " +
        "where they are still level, the cleaner pattern wins. The same recipe orders every list — the daily Slack " +
        "report, the drafts, and the public picks — so nothing is ranked differently in different places."),
      p("Alongside the pattern itself, the system gathers supporting evidence to add confidence — for example:"),
      bullet("Analyst views — what professional analysts rate the share and their price targets."),
      bullet("How it stacks up against its main rival — e.g. Nike versus Lululemon: which is outperforming over " +
             "the last few months, plus recent news on that competition (why a rival may be taking market share)."),
      bullet("Company insiders, and the largest institutional holder — e.g. a fund like BlackRock, or a major " +
             "investor such as Berkshire Hathaway holding a large stake — and whether they are adding or trimming. " +
             "(Insiders are the company’s own officers/directors; an institutional holder is an outside fund — two " +
             "different things.)"),
      bullet("Market positioning — what the big “smart money” players are doing (the COT report)."),
      bullet("Valuation — the price-to-earnings (P/E) ratio, shown against the wider market so you can see at a " +
             "glance whether it looks cheap or expensive."),
      bullet("Options activity, volume, trend strength — and a three-year price history so the long-term backdrop " +
             "is obvious (a name can be bouncing this month yet falling for years)."),
      bullet("Support and resistance — the price floors and ceilings traders watch; if price is sitting right on " +
             "one, the system gives that extra thought, because price can bounce or stall there."),
      p("These do not override the pattern — they add or subtract confidence around it. Where a figure comes from " +
        "a trusted provider (Bloomberg, S&P Global, Morningstar, FactSet, Investing.com), that figure takes priority."),

      // ── 5 ──
      h1("5. Where the results go — and what happens next"),
      p("Once the best ideas are ranked, they are shared in two places, in this order:"),
      bullet("Slack (internal) first — the full picture for the team: more instruments, with the live price, how " +
             "far each level is from it, the reward-to-risk and the expected time to target."),
      bullet("X / Twitter second — a curated, public-facing selection of only the very best, higher-quality ideas."),
      p("Two filters keep the published list trustworthy. First, no chasing: if the price has already run too far " +
        "past the entry, the idea is dropped — there is no point flagging a move you have missed. Second, a quality " +
        "floor: only genuinely clean, high-scoring patterns make the headline list; weaker ones stay on the internal " +
        "watch-list rather than being published."),
      img("decision_flow.png", 575, 300, "From scan to publication"),
      caption("The journey: scan every market → apply the five checks → rank by reward-for-risk → publish " +
              "to Slack (more) then X (the top few)."),
      p("One important point: this system finds and publishes ideas — it does not place the trades itself. " +
        "Actually buying or selling is handled by a separate system, only when its own conditions are met. The " +
        "published lists are candidates to consider, not orders that have been placed."),

      // ── Glossary ──
      h1("A few terms, in plain words"),
      ...glossary(),

      new Paragraph({ spacing: { before: 240 }, border: { top: { style: BorderStyle.SINGLE, size: 6, color: "CCCCCC", space: 8 } },
        children: [new TextRun({ text: "Not financial advice. This document explains how the system works; it is " +
          "not a recommendation to buy or sell anything.", italics: true, size: 18, color: "888888" })] }),
    ]
  }]
});

function twoColTable(rows, leftHdr, rightHdr, lw, rw) {
  const border = { style: BorderStyle.SINGLE, size: 1, color: "DDDDDD" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const head = new TableRow({ tableHeader: true, children: [
    new TableCell({ borders, width: { size: lw, type: WidthType.DXA },
      shading: { fill: ACCENT, type: ShadingType.CLEAR },
      margins: { top: 70, bottom: 70, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: leftHdr, bold: true, size: 20, color: "FFFFFF" })] })] }),
    new TableCell({ borders, width: { size: rw, type: WidthType.DXA },
      shading: { fill: ACCENT, type: ShadingType.CLEAR },
      margins: { top: 70, bottom: 70, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: rightHdr, bold: true, size: 20, color: "FFFFFF" })] })] }),
  ]});
  const body = rows.map(([a, b], i) => new TableRow({ children: [
    new TableCell({ borders, width: { size: lw, type: WidthType.DXA },
      shading: { fill: i % 2 ? "FFFFFF" : "F2F6FC", type: ShadingType.CLEAR },
      margins: { top: 70, bottom: 70, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: a, bold: true, size: 20 })] })] }),
    new TableCell({ borders, width: { size: rw, type: WidthType.DXA },
      shading: { fill: i % 2 ? "FFFFFF" : "F2F6FC", type: ShadingType.CLEAR },
      margins: { top: 70, bottom: 70, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: b, size: 20 })] })] }),
  ]}));
  return new Table({ width: { size: 9360, type: WidthType.DXA }, columnWidths: [lw, rw], rows: [head, ...body] });
}

function rulesTable() {
  return twoColTable([
    ["1. Prior trend", "A confirmed weekly trend in the trade's direction (up for a bullish squeeze, down for bearish). Choppy/flat markets are rejected."],
    ["2. Lower highs", "Three peaks, each lower than the last (H1 > H2 > H3) — the ceiling coming down."],
    ["3. Higher lows", "Three dips, each higher than the last (L1 < L2 < L3) — the floor coming up."],
    ["4. Real squeeze", "The coil must tighten by at least 30% — its mouth shrinks to under 70% of its starting width."],
    ["5. Fresh breakout", "The breakout pivot (H3 / L3) must have formed within the last 60 bars, not weeks ago."],
    ["Tradeable gate", "Reward-to-risk must be at least 3 : 1, or the idea goes on the watch-list, not the trade list."],
    ["Publish gate", "Pattern quality must be at least 70 / 100 to be published; weaker patterns stay internal."],
  ], "Rule", "Threshold — what must be true", 2400, 6960);
}

function glossary() {
  const rows = [
    ["Reward-to-risk (R:R)", "How much you could make versus how much you risk. 3:1 means three times the reward for the risk."],
    ["Entry / Stop / Target", "Where you get in, where you get out if it goes wrong (the safety exit), and where you aim to take profit."],
    ["Support / Resistance", "Price levels where a market has tended to stop falling (support) or stop rising (resistance)."],
    ["The Squeeze", "The coiling-spring pattern: smaller and smaller price swings after a strong trend, then a breakout."],
    ["Smart money (COT)", "What large professional hedgers are doing — often a useful tell on direction."],
    ["Quality score", "A 0–100 mark for how clean and textbook the pattern looks (weak ones aren’t published)."],
    ["Peer / competitor", "The main rival (e.g. Lululemon for Nike) — how the two compare, and related news."],
    ["Institutional holder", "An outside fund (e.g. BlackRock, Berkshire) that owns shares — not a company insider."],
    ["P/E ratio", "Price versus earnings — a rough gauge of cheap vs expensive, shown against the wider market."],
    ["3-year history", "A long-term price chart so you can see the multi-year trend behind a short-term move."],
  ];
  const border = { style: BorderStyle.SINGLE, size: 1, color: "DDDDDD" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const table = new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2700, 6660],
    rows: rows.map(([term, def], i) => new TableRow({ children: [
      new TableCell({ borders, width: { size: 2700, type: WidthType.DXA },
        shading: { fill: i % 2 ? "FFFFFF" : "F2F6FC", type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: term, bold: true, size: 20 })] })] }),
      new TableCell({ borders, width: { size: 6660, type: WidthType.DXA },
        shading: { fill: i % 2 ? "FFFFFF" : "F2F6FC", type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: def, size: 20 })] })] }),
    ] }))
  });
  return [table];
}

Packer.toBuffer(doc).then(buf => {
  const out = path.join(__dirname, "How_The_Trading_System_Works_PlainEnglish.docx");
  fs.writeFileSync(out, buf);
  console.log("WROTE", out, buf.length, "bytes");
});
