// Builds the User Guide / Support / Operations guides shown in the web app's "Documentation" tab.
// Each guide is emitted as a .docx into docs/guides/. Run:
//   NODE_PATH=$(npm root -g) node docs/_build_guides.js
// House style matches docs/_build_plain_english_doc.js (Arial, blue accent, page footer).
// Terminology rule (ChangeRequest P-02): the method is always "The Squeeze" — never
// "Hunt Volatility Funnel", "HVF" or "funnel".
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  LevelFormat, BorderStyle, PageNumber, Footer, Table, TableRow, TableCell,
  WidthType, ShadingType,
} = require("docx");

const ACCENT = "2E5BBA";
const OUT_DIR = path.join(__dirname, "guides");

// ── block helpers ───────────────────────────────────────────────────────────
function p(text)   { return new Paragraph({ spacing: { after: 140 }, children: [new TextRun({ text, size: 22 })] }); }
function h1(text)  { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] }); }
function h2(text)  { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] }); }
function bullet(t) { return new Paragraph({ numbering: { reference: "g-bullets", level: 0 }, spacing: { after: 70 }, children: [new TextRun({ text: t, size: 22 })] }); }
function step(t)   { return new Paragraph({ numbering: { reference: "g-steps", level: 0 }, spacing: { after: 70 }, children: [new TextRun({ text: t, size: 22 })] }); }
function note(t) {
  return new Paragraph({
    spacing: { before: 100, after: 160 }, shading: { fill: "F2F6FC", type: ShadingType.CLEAR },
    border: { left: { style: BorderStyle.SINGLE, size: 18, color: ACCENT, space: 10 } },
    children: [new TextRun({ text: "Note  ", bold: true, size: 20, color: ACCENT }), new TextRun({ text: t, size: 20 })],
  });
}
function twoCol(rows, lHdr, rHdr, lw = 2900, rw = 6460) {
  const bd = { style: BorderStyle.SINGLE, size: 1, color: "DDDDDD" };
  const borders = { top: bd, bottom: bd, left: bd, right: bd };
  const cell = (text, opts, i, w) => new TableCell({
    borders, width: { size: w, type: WidthType.DXA },
    shading: { fill: opts.head ? ACCENT : (i % 2 ? "FFFFFF" : "F2F6FC"), type: ShadingType.CLEAR },
    margins: { top: 70, bottom: 70, left: 120, right: 120 },
    children: [new Paragraph({ children: [new TextRun({ text, bold: opts.head || opts.bold, size: 20, color: opts.head ? "FFFFFF" : "000000" })] })],
  });
  const head = new TableRow({ tableHeader: true, children: [cell(lHdr, { head: true }, 0, lw), cell(rHdr, { head: true }, 0, rw)] });
  const body = rows.map(([a, b], i) => new TableRow({ children: [cell(a, { bold: true }, i, lw), cell(b, {}, i, rw)] }));
  return new Table({ width: { size: lw + rw, type: WidthType.DXA }, columnWidths: [lw, rw], rows: [head, ...body] });
}

// A tiny DSL so each guide reads as data. Each block: [kind, ...args]
function render(block) {
  const [kind, a, b, c, d] = block;
  switch (kind) {
    case "h1": return h1(a);
    case "h2": return h2(a);
    case "p": return p(a);
    case "b": return bullet(a);
    case "s": return step(a);
    case "note": return note(a);
    case "table": return twoCol(a, b, c, d);
    default: throw new Error("unknown block " + kind);
  }
}

function buildDoc(title, subtitle, blocks) {
  return new Document({
    creator: "A&A Trading", title,
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 28, bold: true, font: "Arial", color: ACCENT },
          paragraph: { spacing: { before: 260, after: 140 }, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 23, bold: true, font: "Arial", color: "333333" },
          paragraph: { spacing: { before: 160, after: 90 }, outlineLevel: 1 } },
      ],
    },
    numbering: { config: [
      { reference: "g-bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•",
        alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 260 } } } }] },
      { reference: "g-steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.",
        alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
    ] },
    sections: [{
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
        new TextRun({ text: "The Squeeze — " + title + "  ·  Page ", size: 16, color: "888888" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "888888" }),
      ] })] }) },
      children: [
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 900, after: 100 },
          children: [new TextRun({ text: title, bold: true, size: 40, color: ACCENT })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
          children: [new TextRun({ text: subtitle, size: 24, color: "555555" })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 700 },
          children: [new TextRun({ text: "The Squeeze — trading signal platform", size: 20, color: "888888" })] }),
        ...blocks.map(render),
        new Paragraph({ spacing: { before: 260 }, border: { top: { style: BorderStyle.SINGLE, size: 6, color: "CCCCCC", space: 8 } },
          children: [new TextRun({ text: "Not financial advice. This guide explains how to use the platform; it is not a recommendation to buy or sell anything.", italics: true, size: 18, color: "888888" })] }),
      ],
    }],
  });
}

// ── HTML rendering (user 2026-08-08) ─────────────────────────────────────────
// The web app shows guides as HTML in-app (instant, no download) rather than serving .docx. Each guide is
// emitted as a self-contained fragment styled by the app's own CSS (.guide-doc / .guide-note / .guide-table).
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function tableHtml(rows, lHdr, rHdr) {
  return `<table class="guide-table"><thead><tr><th>${esc(lHdr)}</th><th>${esc(rHdr)}</th></tr></thead><tbody>`
    + rows.map(([a, b]) => `<tr><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join("")
    + `</tbody></table>`;
}

function blocksToHtml(blocks) {
  // Clear-documentation layout: each top-level "h1" becomes a NUMBERED section (01, 02, …) with the
  // heading, and its content sits in the section body — matching the guardian-one risk-management style.
  let out = "", listType = null, buf = [], secOpen = false, secN = 0;
  const flushList = () => {
    if (!buf.length) return;
    out += `<${listType}>` + buf.map((x) => `<li>${esc(x)}</li>`).join("") + `</${listType}>`;
    buf = []; listType = null;
  };
  const closeSec = () => { if (secOpen) { out += `</div></section>`; secOpen = false; } };
  for (const bl of blocks) {
    const kind = bl[0];
    if (kind === "b" || kind === "s") {           // group consecutive bullets/steps into one ul/ol
      const t = kind === "b" ? "ul" : "ol";
      if (listType && listType !== t) flushList();
      listType = t; buf.push(bl[1]); continue;
    }
    flushList();
    if (kind === "h1") {
      closeSec(); secN += 1;
      out += `<section class="guide-sec"><div class="guide-sec-head"><span class="num">`
        + String(secN).padStart(2, "0") + `</span><h2>${esc(bl[1])}</h2></div><div class="guide-sec-body">`;
      secOpen = true;
    } else if (kind === "h2") out += `<h3>${esc(bl[1])}</h3>`;
    else if (kind === "p") out += `<p>${esc(bl[1])}</p>`;
    else if (kind === "note") out += `<div class="guide-note">${esc(bl[1])}</div>`;
    else if (kind === "table") out += tableHtml(bl[1], bl[2], bl[3]);
  }
  flushList(); closeSec();
  return out;
}

function guideHtml(g) {
  return `<article class="guide-doc"><header class="guide-hero"><p class="eyebrow">${esc(g.category)}</p>`
    + `<h1>${esc(g.title)}</h1>`
    + (g.subtitle ? `<p class="guide-lead">${esc(g.subtitle)}</p>` : "")
    + `</header>`
    + blocksToHtml(g.blocks)
    + `<p class="guide-foot">Not financial advice. This guide explains how to use the platform; it is not a recommendation to buy or sell anything.</p></article>`;
}

// ── the guides (slug drives the URL and the filename) ────────────────────────
const GUIDES = require("./_guides_content.js");

fs.mkdirSync(OUT_DIR, { recursive: true });
(async () => {
  const manifest = [];
  for (const g of GUIDES) {
    const doc = buildDoc(g.title, g.subtitle, g.blocks);
    const buf = await Packer.toBuffer(doc);
    fs.writeFileSync(path.join(OUT_DIR, g.slug + ".docx"), buf);   // .docx retained for offline use
    fs.writeFileSync(path.join(OUT_DIR, g.slug + ".html"), guideHtml(g), "utf8");   // in-app HTML view
    manifest.push({ slug: g.slug, title: g.title, category: g.category, subtitle: g.subtitle || "",
                    access: g.access, file: g.slug + ".html" });
    console.log("WROTE guides/" + g.slug + ".html  (+.docx)");
  }
  fs.writeFileSync(path.join(OUT_DIR, "_manifest.json"), JSON.stringify(manifest, null, 2));
  console.log("WROTE guides/_manifest.json (" + manifest.length + " guides)");
})();
