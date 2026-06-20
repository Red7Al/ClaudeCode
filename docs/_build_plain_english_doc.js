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
      p("The method we use is called the Hunt Volatility Funnel. The idea is simple to picture. After a market " +
        "has had a strong run in one direction, it often pauses and “coils” — the price swings get " +
        "smaller and smaller, squeezing into a tightening funnel, like a spring being wound up. When the spring " +
        "finally releases, the price often moves quickly in the direction of the original trend."),
      img("hvf_funnel.png", 560, 304, "The funnel pattern"),
      caption("The funnel: the ceiling (red) drifts down while the floor (green) rises, squeezing price to a point. " +
              "The system marks where to get in, where the safety exit (stop) sits, and a realistic target."),
      p("Three things matter: the highs keep getting lower, the lows keep getting higher, and the gap between " +
        "them shrinks. That squeeze is the “wound spring.” The tighter it gets, the more meaningful the " +
        "eventual breakout."),

      // ── 3 ──
      h1("3. What has to be true before a setup is flagged"),
      p("Not every wobble counts. A setup must pass five plain checks before the system treats it as real:"),
      bullet("A clear prior trend — the market must have been moving decisively first. No trend, no setup."),
      bullet("Falling highs — each peak is lower than the last (the ceiling is coming down)."),
      bullet("Rising lows — each dip is higher than the last (the floor is coming up)."),
      bullet("A real squeeze — the funnel must have tightened by at least a third from where it began."),
      bullet("Freshness — the breakout point must be recent, not weeks stale."),
      p("Only when all five are true does the system measure up the trade. There is also a final money check: the " +
        "potential reward must be at least three times the amount being risked (more on that next). If it is not, " +
        "the idea goes on a watch-list rather than being recommended."),

      // ── 4 ──
      h1("4. What goes into the decision"),
      p("The headline number is the reward-to-risk ratio (often written “R:R”). If a trade risks £1 " +
        "to potentially make £3, that is 3:1. The system leads with this — the better the reward for the " +
        "risk, the higher the idea ranks."),
      p("Reward-to-risk comes first, then the readiness of the signal, then a quality score for how clean the " +
        "pattern is. The picture below shows that order of priority:"),
      img("weighting.png", 520, 259, "How setups are ranked"),
      caption("Reward-for-risk is the main driver; the signal’s readiness and the pattern’s quality settle ties."),
      p("Alongside the pattern itself, the system gathers supporting evidence to add confidence — for example:"),
      bullet("Analyst views — what professional analysts rate the share and their price targets."),
      bullet("Insider and large-holder ownership — e.g. if a major investor such as Berkshire Hathaway holds a " +
             "large stake, and whether they are adding to it or trimming."),
      bullet("Market positioning — what the big “smart money” players are doing (the COT report)."),
      bullet("Options activity, valuation (P/E), volume and trend strength."),
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

function glossary() {
  const rows = [
    ["Reward-to-risk (R:R)", "How much you could make versus how much you risk. 3:1 means three times the reward for the risk."],
    ["Entry / Stop / Target", "Where you get in, where you get out if it goes wrong (the safety exit), and where you aim to take profit."],
    ["Support / Resistance", "Price levels where a market has tended to stop falling (support) or stop rising (resistance)."],
    ["The funnel (HVF)", "The coiling-spring pattern: smaller and smaller price swings after a strong trend, then a breakout."],
    ["Smart money (COT)", "What large professional hedgers are doing — often a useful tell on direction."],
    ["Quality score", "A 0–100 mark for how clean and textbook the pattern looks."],
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
