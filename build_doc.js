// =============================================================================
// File:         build_doc.js
// Description:  Generates the EndToEndTrading System Design & Operations
//               Reference document as a .docx file.
// Run:          node build_doc.js
// Output:       EndToEndTrading_SystemDoc.docx
// =============================================================================

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, TableOfContents,
  LevelFormat, ExternalHyperlink, Bookmark
} = require("docx");
const fs = require("fs");

// =============================================================================
// Colour palette
// =============================================================================
const C = {
  navy:      "1F3864",
  blue:      "2E75B6",
  lightBlue: "D5E8F0",
  midBlue:   "BDD7EE",
  darkGrey:  "404040",
  midGrey:   "595959",
  lightGrey: "F2F2F2",
  white:     "FFFFFF",
  red:       "C00000",
  green:     "375623",
  amber:     "7F6000",
};

// =============================================================================
// Shared helpers
// =============================================================================

const border1 = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders  = { top: border1, bottom: border1, left: border1, right: border1 };
const cellPad  = { top: 100, bottom: 100, left: 140, right: 140 };

function hdrCell(text, shade = C.navy, color = C.white, colSpan) {
  const opts = {
    borders,
    shading: { fill: shade, type: ShadingType.CLEAR },
    margins: cellPad,
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      children: [new TextRun({ text, bold: true, color, font: "Arial", size: 20 })],
    })],
  };
  if (colSpan) opts.columnSpan = colSpan;
  return new TableCell(opts);
}

function dataCell(text, shade, bold = false, color = C.darkGrey) {
  return new TableCell({
    borders,
    shading: { fill: shade || C.white, type: ShadingType.CLEAR },
    margins: cellPad,
    children: [new Paragraph({
      children: [new TextRun({ text: String(text || ""), bold, color, font: "Arial", size: 20 })],
    })],
  });
}

function para(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 100 },
    children: [new TextRun({ text, font: "Arial", size: 22, color: C.darkGrey, ...opts })],
  });
}

function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, font: "Arial", size: 20, color: C.darkGrey })],
  });
}

function h1(text, bookmarkId) {
  const children = bookmarkId
    ? [new Bookmark({ id: bookmarkId, children: [new TextRun({ text, font: "Arial", size: 32, bold: true, color: C.navy })] })]
    : [new TextRun({ text, font: "Arial", size: 32, bold: true, color: C.navy })];
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 180 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: C.blue, space: 6 } },
    children,
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, font: "Arial", size: 26, bold: true, color: C.blue })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 180, after: 80 },
    children: [new TextRun({ text, font: "Arial", size: 22, bold: true, color: C.midGrey })],
  });
}

function spacer(lines = 1) {
  return Array.from({ length: lines }, () =>
    new Paragraph({ children: [new TextRun("")], spacing: { before: 0, after: 120 } })
  );
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

function twoColTable(rows, col1W = 2800, col2W = 6560) {
  const totalW = col1W + col2W;
  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: [col1W, col2W],
    rows: rows.map(([label, value], i) =>
      new TableRow({ children: [
        dataCell(label, i % 2 === 0 ? C.lightBlue : C.white, true),
        dataCell(value, i % 2 === 0 ? C.lightGrey : C.white),
      ]})
    ),
  });
}

function threeColTable(headers, rows) {
  const W = [2000, 4000, 3360];
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: W,
    rows: [
      new TableRow({ children: headers.map((h, i) => hdrCell(h)) }),
      ...rows.map((r, ri) =>
        new TableRow({ children: r.map((v, ci) => dataCell(v, ri % 2 === 0 ? C.lightGrey : C.white)) })
      ),
    ],
  });
}

// =============================================================================
// Visual Diagrams
// =============================================================================

const nb = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: nb, bottom: nb, left: nb, right: nb };

function captionPara(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 60, after: 180 },
    children: [new TextRun({ text, font: "Arial", size: 18, italic: true, color: C.midGrey })],
  });
}

// ── Architecture U-shaped flow diagram ──────────────────────────────────────
function archDiagramFlow() {
  const boxW = 3000, arrW = 180;
  const totalW = boxW * 3 + arrW * 2; // 9360

  function box(lines, fill, textCol) {
    return new TableCell({
      borders,
      shading: { fill, type: ShadingType.CLEAR },
      margins: { top: 140, bottom: 140, left: 160, right: 160 },
      width: { size: boxW, type: WidthType.DXA },
      verticalAlign: VerticalAlign.CENTER,
      children: lines.map((ln, i) => new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: i < lines.length - 1 ? 40 : 0 },
        children: [new TextRun({ text: ln, font: "Arial", size: i === 0 ? 20 : 18, bold: i === 0, color: textCol || C.white })],
      })),
    });
  }

  function arrow(sym, transparent) {
    return new TableCell({
      borders: noBorders,
      shading: { fill: C.white, type: ShadingType.CLEAR },
      width: { size: arrW, type: WidthType.DXA },
      verticalAlign: VerticalAlign.CENTER,
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: sym, font: "Arial", size: 28, bold: true, color: C.midGrey })],
      })],
    });
  }

  function emptyCell(w) {
    return new TableCell({
      borders: noBorders,
      shading: { fill: C.white, type: ShadingType.CLEAR },
      width: { size: w, type: WidthType.DXA },
      children: [new Paragraph({ children: [new TextRun("  ")] })],
    });
  }

  return [
    new Table({
      width: { size: totalW, type: WidthType.DXA },
      columnWidths: [boxW, arrW, boxW, arrW, boxW],
      rows: [
        // Row 0 — left to right: Scheduler → GitHub → Runner
        new TableRow({ children: [
          box(["cron-job.org", "External Scheduler"], C.navy),
          arrow("→"),
          box(["GitHub API", "workflow_dispatch trigger"], "2E75B6"),
          arrow("→"),
          box(["GitHub Actions", "Ubuntu runner (cloud)"], "4472C4"),
        ]}),
        // Row 1 — spacer, down-arrow only on right
        new TableRow({ children: [
          emptyCell(boxW), emptyCell(arrW), emptyCell(boxW), emptyCell(arrW),
          new TableCell({
            borders: noBorders,
            shading: { fill: C.white, type: ShadingType.CLEAR },
            width: { size: boxW, type: WidthType.DXA },
            children: [new Paragraph({
              alignment: AlignmentType.CENTER,
              children: [new TextRun({ text: "↓", font: "Arial", size: 32, bold: true, color: C.midGrey })],
            })],
          }),
        ]}),
        // Row 2 — right to left: Supabase+Slack ← ig_shim ← signals+Claude
        new TableRow({ children: [
          box(["Supabase DB + Slack", "Persistence & Alerts"], "375623"),
          arrow("←"),
          box(["ig_shim.py", "IG UK REST API execution"], "C00000"),
          arrow("←"),
          box(["signals.py + Claude AI", "Signal engine + Decisions"], "7F6000"),
        ]}),
      ],
    }),
    captionPara("Figure 1 — End-to-end system architecture flow"),
  ];
}

// ── Signal stack funnel ──────────────────────────────────────────────────────
function signalFunnelDiagram() {
  const colCount = 12;
  const colW = 780; // 9360 / 12
  const allCols = Array(colCount).fill(colW);

  function emptyPad() {
    return new TableCell({
      borders: noBorders,
      shading: { fill: C.white, type: ShadingType.CLEAR },
      width: { size: colW, type: WidthType.DXA },
      children: [new Paragraph({ children: [new TextRun("  ")] })],
    });
  }

  function contentCell(span, fill, lines, textCol) {
    return new TableCell({
      columnSpan: span,
      borders,
      shading: { fill, type: ShadingType.CLEAR },
      margins: { top: 120, bottom: 120, left: 160, right: 160 },
      width: { size: colW * span, type: WidthType.DXA },
      verticalAlign: VerticalAlign.CENTER,
      children: (Array.isArray(lines) ? lines : [lines]).map((ln, i) =>
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: i < lines.length - 1 ? 30 : 0 },
          children: [new TextRun({ text: ln, font: "Arial", size: i === 0 ? 19 : 17, bold: i === 0, color: textCol || C.white })],
        })
      ),
    });
  }

  function arrowCell(span) {
    return new TableCell({
      columnSpan: span,
      borders: noBorders,
      shading: { fill: C.white, type: ShadingType.CLEAR },
      width: { size: colW * span, type: WidthType.DXA },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "▼", font: "Arial", size: 20, color: C.midGrey })],
      })],
    });
  }

  function funnelRow(padCols, fill, lines, textCol) {
    const span = colCount - 2 * padCols;
    const cells = [];
    for (let i = 0; i < padCols; i++) cells.push(emptyPad());
    cells.push(contentCell(span, fill, lines, textCol));
    for (let i = 0; i < padCols; i++) cells.push(emptyPad());
    return new TableRow({ children: cells });
  }

  function arrowRow(padCols) {
    const span = colCount - 2 * padCols;
    const cells = [];
    for (let i = 0; i < padCols; i++) cells.push(emptyPad());
    cells.push(arrowCell(span));
    for (let i = 0; i < padCols; i++) cells.push(emptyPad());
    return new TableRow({ children: cells });
  }

  return [
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: allCols,
      rows: [
        funnelRow(0, C.navy,   ["MACRO GATE", "VIX < 35  |  Yield Spread > -1%  |  SPX not in high stress"]),
        arrowRow(1),
        funnelRow(1, "2E75B6", ["PRIMARY SIGNALS", "Options Flow (Call/Put Ratio)  |  Bollinger Band Breakout  |  HVF Pattern (>= 2.5:1 R:R)"]),
        arrowRow(2),
        funnelRow(2, "4472C4", ["CONFIRMATION SIGNALS", "GEX  |  VWAP  |  COT  |  Director Buys  |  Senate  |  Superinvestor  |  Social Mention"]),
        arrowRow(3),
        funnelRow(3, "7F6000", ["RISK FILTER", "Economic Calendar Block  |  Intraday Guard (2x ATR)  |  Spread Check"]),
        arrowRow(4),
        funnelRow(4, "375623", ["TRADE FIRES", "ATR Position Sizing  |  Min 2.5:1 R:R  |  Max 3 trades per session"]),
      ],
    }),
    captionPara("Figure 2 — Signal stack funnel: all layers must pass in sequence before a trade executes"),
  ];
}

// ── 24-hour session timeline ─────────────────────────────────────────────────
function sessionTimelineDiagram() {
  const cellW = 390; // 9360 / 24

  const COLORS = {
    off:     "E8E8E8",
    aus:     "1F3864",
    uk:      "375623",
    us:      "C05A17",
    overlap: "843C0C",
  };

  function hourFill(h) {
    if (h >= 1  && h <= 6)  return COLORS.aus;
    if (h >= 9  && h <= 14) return COLORS.uk;
    if (h === 15)            return COLORS.overlap; // 15:30 transition
    if (h >= 16 && h <= 20) return COLORS.us;
    return COLORS.off;
  }

  function hourTextCol(h) {
    return (h >= 1 && h <= 6) || (h >= 9 && h <= 20) ? C.white : C.midGrey;
  }

  const hourRow = new TableRow({
    children: Array.from({ length: 24 }, (_, h) =>
      new TableCell({
        borders: { top: border1, bottom: border1, left: border1, right: border1 },
        shading: { fill: COLORS.off, type: ShadingType.CLEAR },
        width: { size: cellW, type: WidthType.DXA },
        margins: { top: 60, bottom: 60, left: 0, right: 0 },
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: String(h).padStart(2, "0"), font: "Arial", size: 15, color: C.midGrey })],
        })],
      })
    ),
  });

  const colorRow = new TableRow({
    children: Array.from({ length: 24 }, (_, h) =>
      new TableCell({
        borders,
        shading: { fill: hourFill(h), type: ShadingType.CLEAR },
        width: { size: cellW, type: WidthType.DXA },
        margins: { top: 120, bottom: 120, left: 0, right: 0 },
        children: [new Paragraph({ children: [new TextRun("")] })],
      })
    ),
  });

  // Legend row
  function legendCell(fill, label, colSpan) {
    return new TableCell({
      columnSpan: colSpan,
      borders,
      shading: { fill, type: ShadingType.CLEAR },
      margins: { top: 80, bottom: 80, left: 100, right: 100 },
      width: { size: cellW * colSpan, type: WidthType.DXA },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: label, font: "Arial", size: 16, bold: true, color: C.white })],
      })],
    });
  }

  function offCell(colSpan) {
    return new TableCell({
      columnSpan: colSpan,
      borders,
      shading: { fill: COLORS.off, type: ShadingType.CLEAR },
      margins: { top: 80, bottom: 80, left: 100, right: 100 },
      width: { size: cellW * colSpan, type: WidthType.DXA },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Market Closed", font: "Arial", size: 16, color: C.midGrey })],
      })],
    });
  }

  const legendRow = new TableRow({ children: [
    offCell(1),                                // 00:00
    legendCell(COLORS.aus,     "AUS / Asia  01:00–07:00", 6),   // 01-06
    offCell(2),                                // 07-08
    legendCell(COLORS.uk,      "UK / Europe  09:00–15:29", 6),  // 09-14
    legendCell(COLORS.overlap, "Overlap", 1),  // 15
    legendCell(COLORS.us,      "US  15:30–21:00", 5),           // 16-20
    offCell(3),                                // 21-23
  ]});

  return [
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: Array(24).fill(cellW),
      rows: [hourRow, colorRow, legendRow],
    }),
    captionPara("Figure 3 — Daily trading session coverage (UTC). Overlap period 15:00–15:30 UTC when UK and US markets are both open."),
  ];
}

// ── HVF pattern schematic ────────────────────────────────────────────────────
function hvfDiagram() {
  const lines = [
    ["          H1                                                         ", false],
    ["          /\\             H2                                          ", false],
    ["         /  \\            /\\            H3  <-- ENTRY (BUY above H3) ", false],
    ["        /    \\          /  \\           /\\                            ", false],
    ["       /      \\        /    \\         /  \\  ──────────────────────► Target", false],
    ["      /        \\      /      \\       /    \\   (H1-L1 range from H3) ", false],
    ["     /          \\    /        \\     /      \\                         ", false],
    ["    /            \\  /          \\   /        L3 <-- STOP LOSS         ", false],
    ["   /              \\/            \\ /                                  ", false],
    ["  /              L1             L2                                   ", false],
    [" /                                                                   ", false],
    ["──────────────────────────────────────────────────────────────────►  ", false],
    ["                           Time                                      ", false],
    ["                                                                      ", false],
    [" Pattern: Higher lows (L1→L2→L3) + Lower highs (H1→H2→H3)           ", true ],
    [" = Volatility compressing = Breakout imminent at H3                  ", true ],
    [" HVF fires when price breaks above H3 with volume confirmation        ", true ],
  ];

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({ children: [new TableCell({
      borders,
      margins: { top: 140, bottom: 140, left: 240, right: 240 },
      shading: { fill: "F8F8F8", type: ShadingType.CLEAR },
      children: lines.map(([text, isAnnotation]) =>
        new Paragraph({
          children: [new TextRun({
            text,
            font: "Courier New",
            size: 17,
            color: isAnnotation ? "2E75B6" : C.darkGrey,
          })],
        })
      ),
    })]})],
  });
}

// ── Process step boxes (numbered flow) ───────────────────────────────────────
function processStepFlow(steps) {
  // Renders a horizontal sequence of numbered boxes with arrows
  // steps: array of {num, label, sublabel, color}
  const perStep = Math.floor(9360 / steps.length);
  const arrW    = 220;
  const boxW    = Math.floor((9360 - arrW * (steps.length - 1)) / steps.length);

  const cols = [];
  steps.forEach((_, i) => { cols.push(boxW); if (i < steps.length - 1) cols.push(arrW); });

  const cells = [];
  steps.forEach((s, i) => {
    cells.push(new TableCell({
      borders,
      shading: { fill: s.color || C.navy, type: ShadingType.CLEAR },
      margins: { top: 120, bottom: 120, left: 100, right: 100 },
      width: { size: boxW, type: WidthType.DXA },
      verticalAlign: VerticalAlign.CENTER,
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: s.num, font: "Arial", size: 28, bold: true, color: C.white })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 40, after: 0 },
          children: [new TextRun({ text: s.label, font: "Arial", size: 18, bold: true, color: C.white })],
        }),
        ...(s.sublabel ? [new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 20, after: 0 },
          children: [new TextRun({ text: s.sublabel, font: "Arial", size: 16, color: "DDDDDD" })],
        })] : []),
      ],
    }));
    if (i < steps.length - 1) {
      cells.push(new TableCell({
        borders: noBorders,
        shading: { fill: C.white, type: ShadingType.CLEAR },
        width: { size: arrW, type: WidthType.DXA },
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "→", font: "Arial", size: 28, bold: true, color: C.midGrey })],
        })],
      }));
    }
  });

  return [
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: cols,
      rows: [new TableRow({ children: cells })],
    }),
  ];
}

// =============================================================================
// Visual step-panel helpers — for deployment guide (non-tech-savvy users)
// =============================================================================

/**
 * stepPanel — renders a full-width annotated "how to do it" panel.
 *
 * header  : e.g. "GitHub — Step 2: Create a Repository"
 * url     : the URL the user should open (shown as a clickable-looking bar)
 * steps   : array of strings — numbered steps
 * tips    : array of {label, text} — yellow callout tips / warnings
 */
function stepPanel(header, url, steps, tips = []) {
  const rows = [];

  // ── Header bar (dark navy) ────────────────────────────────────────────────
  rows.push(new TableRow({ children: [new TableCell({
    columnSpan: 1,
    borders,
    shading: { fill: C.navy, type: ShadingType.CLEAR },
    margins: { top: 120, bottom: 120, left: 200, right: 200 },
    children: [new Paragraph({
      children: [new TextRun({ text: header, font: "Arial", size: 22, bold: true, color: C.white })],
    })],
  })] }));

  // ── URL bar (light grey, simulates browser address bar) ───────────────────
  if (url) {
    rows.push(new TableRow({ children: [new TableCell({
      borders: { top: border1, bottom: border1, left: border1, right: border1 },
      shading: { fill: "EFEFEF", type: ShadingType.CLEAR },
      margins: { top: 80, bottom: 80, left: 200, right: 200 },
      children: [new Paragraph({
        children: [
          new TextRun({ text: "🌐  ", font: "Arial", size: 19, color: C.midGrey }),
          new TextRun({ text: url, font: "Courier New", size: 19, color: "1155CC" }),
        ],
      })],
    })] }));
  }

  // ── Numbered steps ────────────────────────────────────────────────────────
  steps.forEach((step, i) => {
    const isEven = i % 2 === 0;
    rows.push(new TableRow({ children: [new TableCell({
      borders,
      shading: { fill: isEven ? "F8FAFE" : C.white, type: ShadingType.CLEAR },
      margins: { top: 100, bottom: 100, left: 200, right: 200 },
      children: [new Paragraph({
        children: [
          new TextRun({ text: `${i + 1}.  `, font: "Arial", size: 20, bold: true, color: C.navy }),
          new TextRun({ text: step, font: "Arial", size: 20, color: C.darkGrey }),
        ],
      })],
    })] }));
  });

  // ── Tips / warnings ───────────────────────────────────────────────────────
  tips.forEach(({ label, text, color }) => {
    const bg   = color === "warn"  ? "FFF9E6" :
                 color === "error" ? "FDECEA" : "E8F4FD";
    const icon = color === "warn"  ? "⚠️  " :
                 color === "error" ? "❌  " : "💡  ";
    const labelColor = color === "warn" ? "7F6000" : color === "error" ? "C00000" : "1F3864";
    rows.push(new TableRow({ children: [new TableCell({
      borders: { top: border1, bottom: border1,
                 left: { style: BorderStyle.SINGLE, size: 12, color: labelColor },
                 right: border1 },
      shading: { fill: bg, type: ShadingType.CLEAR },
      margins: { top: 100, bottom: 100, left: 200, right: 200 },
      children: [
        new Paragraph({ children: [
          new TextRun({ text: `${icon}${label}: `, font: "Arial", size: 20, bold: true, color: labelColor }),
          new TextRun({ text, font: "Arial", size: 20, color: C.darkGrey }),
        ]}),
      ],
    })] }));
  });

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows,
  });
}

/**
 * clickTarget — renders a blue "click / look for this" callout.
 * Use to point out where exactly to click on a page.
 */
function clickTarget(location, action) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 6960],
    rows: [new TableRow({ children: [
      new TableCell({
        borders,
        shading: { fill: "D5E8F0", type: ShadingType.CLEAR },
        margins: cellPad,
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: `📍 ${location}`, font: "Arial", size: 19, bold: true, color: C.navy })],
        })],
      }),
      new TableCell({
        borders,
        shading: { fill: C.white, type: ShadingType.CLEAR },
        margins: cellPad,
        children: [new Paragraph({
          children: [new TextRun({ text: action, font: "Arial", size: 20, color: C.darkGrey })],
        })],
      }),
    ]})],
  });
}

// =============================================================================
// Cover page section
// =============================================================================

function buildCover() {
  const centerBig = (text, size, bold, color) =>
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 140 },
      children: [new TextRun({ text, font: "Arial", size, bold: bold || false, color: color || C.navy })],
    });

  return [
    ...spacer(4),
    centerBig("EndToEndTrading", 64, true, C.navy),
    centerBig("Automated CFD Trading System", 40, false, C.blue),
    ...spacer(1),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: C.blue, space: 4 } },
      children: [],
      spacing: { before: 0, after: 240 },
    }),
    ...spacer(1),
    centerBig("System Design & Operations Reference", 34, true, C.navy),
    ...spacer(3),

    // Approval table
    new Table({
      width: { size: 7200, type: WidthType.DXA },
      columnWidths: [2400, 4800],
      rows: [
        new TableRow({ children: [
          hdrCell("Document Title", C.navy),
          dataCell("EndToEndTrading — System Design & Operations Reference"),
        ]}),
        new TableRow({ children: [
          hdrCell("Author", C.navy),
          dataCell("Alex Hind"),
        ]}),
        new TableRow({ children: [
          hdrCell("Date", C.navy),
          dataCell("5 June 2026"),
        ]}),
        new TableRow({ children: [
          hdrCell("Version", C.navy),
          dataCell("1.0"),
        ]}),
        new TableRow({ children: [
          hdrCell("Status", C.navy),
          dataCell("Draft — For Review"),
        ]}),
        new TableRow({ children: [
          hdrCell("Classification", C.navy),
          dataCell("Confidential — Internal Use Only"),
        ]}),
        new TableRow({ children: [
          hdrCell("Approved By", C.navy),
          dataCell(""),
        ]}),
        new TableRow({ children: [
          hdrCell("Approval Date", C.navy),
          dataCell(""),
        ]}),
      ],
    }),

    ...spacer(2),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({
        text: "CONFIDENTIAL — NOT FOR EXTERNAL DISTRIBUTION",
        font: "Arial", size: 18, bold: true, color: C.red,
      })],
    }),
    pageBreak(),
  ];
}

// =============================================================================
// Table of Contents page
// =============================================================================

function buildTOC() {
  return [
    h1("Table of Contents"),
    new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
    pageBreak(),
  ];
}

// =============================================================================
// 1. System Overview
// =============================================================================

function buildOverview() {
  return [
    h1("1. System Overview", "s1"),
    h2("1.1 Mission"),
    para("EndToEndTrading is a fully automated CFD and spread betting trading system built on Claude AI. Its primary goal is to find and execute profitable trades in global financial markets using data-driven signals — removing emotional bias and human latency from the decision-making process."),
    para("The system operates 24 hours a day, 5 days a week across equities, indices, FX, commodities and crypto. It integrates free-tier external data sources with IG UK's REST API, using Claude Cloud Routines as the intelligence layer and GitHub Actions as the execution scheduler."),
    ...spacer(1),

    h2("1.2 Core Design Principles"),
    bullet("Maximum autonomy — system runs with minimal human input once deployed."),
    bullet("Evidence-based decisions — every trade is backed by a stack of independently sourced signals."),
    bullet("Separation of concerns — Claude handles intelligence; Python handles execution only."),
    bullet("Silent failure is not acceptable — all errors surface to Slack #alerts immediately."),
    bullet("Idempotency — all database migrations and schema scripts are safe to re-run."),
    bullet("Auditability — every trade decision is logged with full rationale."),
    ...spacer(1),

    h2("1.3 High-Level Architecture"),
    para("The system has four logical layers:"),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2200, 7160],
      rows: [
        new TableRow({ children: [hdrCell("Layer"), hdrCell("Responsibility")] }),
        new TableRow({ children: [dataCell("Scheduler", C.lightBlue, true), dataCell("cron-job.org fires GitHub workflow_dispatch. GitHub Actions runs the Python session script.")] }),
        new TableRow({ children: [dataCell("Intelligence", C.white, true), dataCell("Claude AI (via Cloud Routines) reads signal data, makes trade decisions, and calls ig_shim.py to execute.")] }),
        new TableRow({ children: [dataCell("Signal Engine", C.lightBlue, true), dataCell("signals.py queries all external data sources (Yahoo Finance, FRED, CFTC, SEC, Capitol Trades, ForexFactory) and returns structured signal data.")] }),
        new TableRow({ children: [dataCell("Execution", C.white, true), dataCell("ig_shim.py handles all IG UK REST API calls — authentication, order placement, trailing stops, P&L logging. No decision logic lives here.")] }),
      ],
    }),
    ...spacer(1),
    ...archDiagramFlow(),
    ...spacer(1),
    pageBreak(),
  ];
}

// =============================================================================
// 2. Background: AI, Large Language Models & the MVP
// =============================================================================

function buildBackground() {
  return [
    h1("2. Background: AI, Large Language Models & the MVP", "s2"),

    h2("2.1 What is Artificial Intelligence?"),
    para("Artificial Intelligence (AI) is software that can perform tasks that would normally require human intelligence — such as understanding language, making decisions, recognising patterns, and generating creative output. AI is not magic: it is the result of training mathematical models on large amounts of data until they become very good at specific tasks."),
    para("Modern AI relevant to this project falls into two broad categories:"),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 6560],
      rows: [
        new TableRow({ children: [hdrCell("Type"), hdrCell("What it does — plain English")] }),
        new TableRow({ children: [dataCell("Narrow AI", C.lightGrey), dataCell("Trained for one specific task — e.g. spam filtering, price prediction, image recognition. Cannot reason outside its narrow domain.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("General-purpose AI (LLM)"), dataCell("Trained on vast amounts of text and code. Can reason over complex data, write code, summarise, make structured decisions, explain its thinking. This is the type powering EndToEndTrading.")] }),
      ],
    }),
    ...spacer(1),

    h2("2.2 What is a Large Language Model (LLM)?"),
    para("A Large Language Model (LLM) is an AI trained on enormous quantities of text — books, articles, websites, scientific papers, and code. By learning patterns across billions of examples, the model becomes able to understand context and generate highly relevant responses. The word 'large' refers to the number of internal parameters (numerical weights) — modern LLMs have hundreds of billions."),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 6560],
      rows: [
        new TableRow({ children: [hdrCell("Concept"), hdrCell("Plain English Explanation")] }),
        new TableRow({ children: [dataCell("Token", C.lightGrey), dataCell("The smallest unit the model processes — roughly a word or word-fragment. The text 'NVIDIA Corp' is about 3 tokens. A session scan prompt is roughly 5,000–15,000 tokens.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Context Window"), dataCell("Everything the model can read at once — its working memory. Modern LLMs handle 100,000–200,000 tokens. An entire trading session's worth of signal data fits comfortably inside one call.")] }),
        new TableRow({ children: [dataCell("Prompt", C.lightGrey), dataCell("The input given to the model — instructions, data, rules, and questions. In this system the prompt contains: all signal data, the user's risk profile, current open positions, and trading rules.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Temperature"), dataCell("A knob controlling how creative vs deterministic the output is. Set low for trading — we want consistent, rules-based responses, not creative variation.")] }),
        new TableRow({ children: [dataCell("Tool Use / Function Calling", C.lightGrey), dataCell("The model can call external code functions rather than just generating text. This is how Claude places trades — it decides to call open_trade() with specific parameters, which runs ig_shim.py.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Inference"), dataCell("Running the model to generate a response. Each trading session triggers one Claude inference — it reads the signals and produces a structured decision.")] }),
      ],
    }),
    ...spacer(1),

    h2("2.3 What is Claude?"),
    para("Claude is the family of large language models built by Anthropic, an AI safety company founded in 2021. Claude is used as the core intelligence layer of EndToEndTrading — it reads signal data from all sources, reasons about which trades to place, and calls Python functions to execute them."),
    ...spacer(1),
    twoColTable([
      ["Model in use",    "Claude Sonnet 4.x — balances strong reasoning capability with fast response time"],
      ["Context window",  "Up to 200,000 tokens — holds an entire session's signal data in one call"],
      ["Tool use",        "Claude can call ig_shim.py functions directly to place, modify, and close trades"],
      ["Explainability",  "Claude explains every decision in plain English — logged with each trade"],
      ["Provider",        "Anthropic (anthropic.com) — billed per token (input + output separately)"],
      ["Safety focus",    "Anthropic builds Claude with strong safety principles — relevant when it controls real money"],
    ]),
    ...spacer(1),

    h2("2.4 How Claude Makes a Trade Decision"),
    para("Each time a session runs, signals.py collects data from all sources and builds a structured prompt. Claude reads this prompt in full, reasons over the signal layers, and either places trades via tool calls or skips with an explanation."),
    ...spacer(1),
    ...processStepFlow([
      { num: "1", label: "Collect",      sublabel: "signals.py fetches all data", color: "4472C4" },
      { num: "2", label: "Format",       sublabel: "Build Claude prompt",          color: "2E75B6" },
      { num: "3", label: "Read",         sublabel: "Claude sees full context",     color: C.navy   },
      { num: "4", label: "Reason",       sublabel: "Applies signal rules",         color: "7F6000" },
      { num: "5", label: "Act or Skip",  sublabel: "Tool call or log skip",        color: "375623" },
    ]),
    captionPara("Figure 6 — How Claude processes signals and produces a structured trade decision via tool use"),
    ...spacer(1),
    para("Every decision is logged with Claude's full reasoning. If asked 'why did you buy NVDA (NVIDIA Corp)?', the system can explain exactly which signals fired, which were primary vs confirmation, and what R:R ratio was calculated."),
    ...spacer(1),

    h2("2.5 AI Tools Used in This Project"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 6560],
      rows: [
        new TableRow({ children: [hdrCell("Tool"), hdrCell("Role in This Project")] }),
        new TableRow({ children: [
          dataCell("Claude Code Desktop", C.lightGrey),
          dataCell("The IDE used to build and maintain the trading system. You are using it in this session. It embeds Claude directly — it can read, write, and explain the entire codebase, run scripts, manage git, and interact with external services via MCP.", C.lightGrey),
        ]}),
        new TableRow({ children: [
          dataCell("Claude AI (Anthropic API)"),
          dataCell("The decision engine inside every trading session. Called once per session with all signal data. Returns structured decisions and calls ig_shim.py functions to execute trades."),
        ]}),
        new TableRow({ children: [
          dataCell("Claude Scheduled Tasks", C.lightGrey),
          dataCell("Autonomous Claude sessions that run on a cron schedule. Each task has a pre-written prompt that instructs Claude to run a Python script. They are independent agents — each starts fresh with no memory of previous runs.", C.lightGrey),
        ]}),
        new TableRow({ children: [
          dataCell("Claude in Chrome"),
          dataCell("A Chrome browser extension giving Claude DOM-aware access to web pages. Used for web-based tasks: reading dashboards, filling forms, navigating SPAs. More precise and faster than pixel-level screen control."),
        ]}),
        new TableRow({ children: [
          dataCell("MCP (Model Context Protocol)", C.lightGrey),
          dataCell("Anthropic's open standard — like a USB-C port for AI. Allows Claude to connect securely to Supabase, GitHub, and Slack without custom API code. Active connectors: Supabase MCP, GitHub MCP, Slack MCP, Scheduled Tasks MCP.", C.lightGrey),
        ]}),
      ],
    }),
    ...spacer(1),

    h2("2.6 What is an MVP?"),
    para("MVP stands for Minimum Viable Product — the smallest version of a system that delivers real, working value. An MVP is not a demo or prototype: it does real things. The philosophy is to ship something that works end-to-end as quickly as possible, validate that it's worth building further, and expand from there."),
    para("The alternative — trying to build the complete, perfect system before using it — is a well-known failure mode in software. MVPs force you to be ruthless about what is truly essential vs nice-to-have."),
    ...spacer(1),

    h2("2.7 The EndToEndTrading MVP — What It Does Today"),
    para("The MVP is a single-user, fully autonomous CFD trading system. It requires zero human intervention during normal operation."),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 6560],
      rows: [
        new TableRow({ children: [hdrCell("Capability"), hdrCell("Detail")] }),
        new TableRow({ children: [dataCell("User", C.lightGrey), dataCell("Owner account — live, real money. Wife and Son are configured in the database but deferred.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Instruments"), dataCell("FX, indices (SPX500 / S&P 500, UK100 / FTSE 100, JPN225 / Nikkei 225), commodities (XAUUSD / Spot Gold, OIL / WTI Crude), US equities, UK equities, and crypto — all as CFDs via IG UK.")] }),
        new TableRow({ children: [dataCell("Signal stack", C.lightGrey), dataCell("All 10 signal layers active: macro gate, options flow, Bollinger Bands, HVF patterns, GEX, VWAP, COT, director buys, senate trades, superinvestor positions, social mentions.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Sessions"), dataCell("AUS/Asia open (01:00 UTC), UK open (09:00 UTC), UK monitor (every 15 min), US open (15:30 UTC), session close (22:00 UTC), weekend review (Saturday), Sunday pre-market brief.")] }),
        new TableRow({ children: [dataCell("Autonomy", C.lightGrey), dataCell("System runs, trades, monitors positions, updates trailing stops, and posts full reports to Slack — entirely without human input.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Risk management"), dataCell("Per-trade ATR stops, daily loss limit (3%), maximum 5 open positions, minimum 2.5:1 R:R, economic calendar blocking, spread checks, intraday guard.")] }),
        new TableRow({ children: [dataCell("Reporting", C.lightGrey), dataCell("Every trade posted to Slack (#claude-trading-trades). Session scans posted to #claude-trading-signals. All errors posted to #claude-trading-alerts. Weekly digest to #claude-trading-weekly.", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),

    h2("2.8 What is Deferred — Future Phases"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 6560],
      rows: [
        new TableRow({ children: [hdrCell("Feature"), hdrCell("Current Status & Plan")] }),
        new TableRow({ children: [dataCell("Wife + Son accounts", C.lightGrey), dataCell("User profiles exist in Supabase. Multi-user session loop not yet active — Owner only. Will be wired in when ready to expand.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("DAX 40 (Germany index)"), dataCell("Suspended 2026-06-05. Removed from all config maps. Reinstate by restoring DAX entries in config.py.")] }),
        new TableRow({ children: [dataCell("Unusual Whales MCP", C.lightGrey), dataCell("Premium real-time institutional options flow data (~$250/mo). Currently using Yahoo Finance as a free alternative. Unusual Whales would significantly improve signal quality.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("TradingView integration"), dataCell("Requires the local machine to be running. Incompatible with cloud-only architecture. Parked for potential manual-oversight use case only.")] }),
        new TableRow({ children: [dataCell("Backtesting", C.lightGrey), dataCell("No historical backtesting framework yet. The signal stack is forward-looking. Backtesting against tick data is a future phase.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Copy trading / strategy reverse-engineering"), dataCell("Using eToro Popular Investors or Darwinex performance data to mirror strategies. Parked — too complex for MVP. Superinvestor 13F positions partially cover this use case.")] }),
      ],
    }),
    ...spacer(1),

    // ── 2.9 Alternative AI Models ─────────────────────────────────────────────
    h2("2.9 Other AI Models — Used and Considered"),
    para("Claude (Anthropic) was selected as the intelligence layer for this system. Other LLMs were considered and may be relevant for specific use cases in future phases."),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2400, 2560, 4400],
      rows: [
        new TableRow({ children: [hdrCell("Model"), hdrCell("Provider"), hdrCell("Notes for This Project")] }),
        new TableRow({ children: [dataCell("Claude Sonnet / Opus", C.lightBlue, true), dataCell("Anthropic", C.lightGrey), dataCell("Selected. Strong reasoning, tool use, and explainability. Safety-first design reduces risk of erratic trade decisions.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("GPT-4o / o1"), dataCell("OpenAI"), dataCell("Strong alternative. OpenAI has a more mature function-calling ecosystem. Higher token costs at equivalent capability. Could be a drop-in replacement via the same prompt architecture.")] }),
        new TableRow({ children: [dataCell("Grok 2 / 3", C.lightGrey), dataCell("xAI (Elon Musk)", C.lightGrey), dataCell("Real-time X/Twitter data access is a significant advantage for social mention signals. Could replace or supplement the social_monitor.py layer. Less proven for structured financial reasoning.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Gemini 1.5 Pro"), dataCell("Google DeepMind"), dataCell("2M-token context window useful for large historical batch analysis. Strong at structured data extraction. Less mature tool-use vs Claude/GPT-4o.")] }),
        new TableRow({ children: [dataCell("Llama 3.3 70B / 405B", C.lightGrey), dataCell("Meta (open source)", C.lightGrey), dataCell("Could run locally via Ollama — zero API cost. Viable for offline backtesting or signal pre-processing. Not suitable as primary decision engine without significant fine-tuning.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Mistral Large"), dataCell("Mistral AI"), dataCell("European provider — potential relevance for GDPR-sensitive deployments. Lower cost than GPT-4o but less capable at complex multi-step reasoning.")] }),
      ],
    }),
    ...spacer(1),

    // ── 2.10 Connectors — Used, Considered, and Future ────────────────────────
    h2("2.10 Connectors — Used, Considered, and Future"),
    para("A connector is any external service integrated into the system — brokers, data providers, notification channels, scheduling services, or AI tools. The table below documents what has been evaluated and why each decision was made."),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2400, 1600, 1200, 4160],
      rows: [
        new TableRow({ children: [hdrCell("Connector"), hdrCell("Category"), hdrCell("Status"), hdrCell("Notes")] }),

        // ── Brokers ──
        new TableRow({ children: [dataCell("IG UK", C.lightBlue, true), dataCell("Broker", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("Full REST API, CFD + spread betting, verified UK account HTIRV. Only broker in current MVP.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Plus500"), dataCell("Broker"), dataCell("Rejected"), dataCell("No public API. Terms of service prohibit automation.")] }),
        new TableRow({ children: [dataCell("Interactive Brokers (IBKR)", C.lightGrey), dataCell("Broker", C.lightGrey), dataCell("Future", C.lightGrey), dataCell("Professional-grade API (TWS / IBKR Gateway). Supports global markets, options, and futures. Higher account minimum. Best option for US equity options execution.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Alpaca"), dataCell("Broker"), dataCell("Future"), dataCell("Commission-free US stocks and options API. No UK presence. Would suit a US-only sub-strategy running alongside IG.")] }),

        // ── Data: Market ──
        new TableRow({ children: [dataCell("Yahoo Finance (yfinance)", C.lightBlue, true), dataCell("Market Data", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("Price data, options chains, GEX, VWAP, IV rank. Free. Sufficient for MVP signal quality.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Unusual Whales"), dataCell("Market Data"), dataCell("Planned"), dataCell("Real-time institutional options flow, dark pool prints, GEX by strike. ~$250/mo. Highest-value single upgrade to signal quality.")] }),
        new TableRow({ children: [dataCell("Bloomberg Terminal", C.lightGrey), dataCell("Market Data", C.lightGrey), dataCell("Not viable", C.lightGrey), dataCell("Industry standard. ~$24,000/yr. Not justifiable for MVP. Revisit if system scales to institutional level.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Refinitiv (LSEG)"), dataCell("Market Data"), dataCell("Not viable"), dataCell("Similar to Bloomberg. Enterprise pricing. Not in scope.")] }),

        // ── Data: Macro / Alt ──
        new TableRow({ children: [dataCell("FRED API (St. Louis Fed)", C.lightBlue, true), dataCell("Macro Data", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("Yield curve, real yields, inflation breakevens. Free. Essential for macro gate.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("EIA API"), dataCell("Macro Data"), dataCell("ACTIVE"), dataCell("US oil inventory data (weekly EIA report). Free API key.")] }),
        new TableRow({ children: [dataCell("CFTC API", C.lightGrey), dataCell("Macro Data", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("Commitment of Traders (COT) data. Fully free REST API.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("SEC EDGAR"), dataCell("Alt Data"), dataCell("ACTIVE"), dataCell("Form 4 (director buys), Schedule 13D (activist), 13F (institutions). Free.")] }),
        new TableRow({ children: [dataCell("Capitol Trades", C.lightGrey), dataCell("Alt Data", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("US Senate STOCK Act disclosures. Free tier. Used as fallback to Quiver Quant.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Quiver Quant"), dataCell("Alt Data"), dataCell("ACTIVE"), dataCell("Congressional trading + senator scoring (win rate × excess return). Free tier adequate for weekly refresh.")] }),
        new TableRow({ children: [dataCell("Dataroma", C.lightGrey), dataCell("Alt Data", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("Superinvestor 13F holdings — Buffett (Berkshire Hathaway), Ackman (Pershing Square), Burry (Scion). 81 investors. Free.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("ForexFactory"), dataCell("Calendar"), dataCell("ACTIVE"), dataCell("Economic calendar for event-based trade blocking. Scraped — no official API.")] }),
        new TableRow({ children: [dataCell("Finnhub", C.lightGrey), dataCell("Alt Data", C.lightGrey), dataCell("Considered", C.lightGrey), dataCell("Congressional trading + insider filings. Free tier available. Similar coverage to Quiver Quant. Could add redundancy.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Darwinex"), dataCell("Copy Trading"), dataCell("Considered"), dataCell("Darwin API provides performance stats for signal provider strategies. Could enable strategy reverse-engineering. Requires Darwinex account.")] }),

        // ── Infrastructure ──
        new TableRow({ children: [dataCell("Supabase", C.lightBlue, true), dataCell("Database", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("PostgreSQL via pooler. Trades, positions, signals, user profiles. MCP connector active.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("GitHub Actions"), dataCell("Scheduler"), dataCell("ACTIVE"), dataCell("Session workflow runner. workflow_dispatch triggered by cron-job.org.")] }),
        new TableRow({ children: [dataCell("cron-job.org", C.lightGrey), dataCell("Scheduler", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("External cron — fires GitHub workflow_dispatch at precise UTC times. Free tier sufficient.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Slack"), dataCell("Notifications"), dataCell("ACTIVE"), dataCell("5 channels (trades, signals, alerts, weekly, daily). Block Kit formatting.")] }),
        new TableRow({ children: [dataCell("TradingView MCP", C.lightGrey), dataCell("Charting", C.lightGrey), dataCell("Parked", C.lightGrey), dataCell("Live chart data via Chrome DevTools Protocol. Requires local machine running. Incompatible with cloud-only architecture.", C.lightGrey)] }),

        // ── AI / MCP ──
        new TableRow({ children: [dataCell("Anthropic API (Claude)"), dataCell("AI Engine"), dataCell("ACTIVE"), dataCell("Core intelligence layer. Claude Sonnet 4.x. Tool use for trade execution.")] }),
        new TableRow({ children: [dataCell("Supabase MCP", C.lightGrey), dataCell("MCP Connector", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("SQL queries, table management, project URLs, logs. Used by Claude Code in this session.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("GitHub MCP"), dataCell("MCP Connector"), dataCell("ACTIVE"), dataCell("Repository management, PRs, secrets. Used by Claude Code.")] }),
        new TableRow({ children: [dataCell("Slack MCP", C.lightGrey), dataCell("MCP Connector", C.lightGrey), dataCell("ACTIVE", C.lightGrey), dataCell("Send messages, read channels. Used by Claude Code for monitoring.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Unusual Whales MCP"), dataCell("MCP Connector"), dataCell("Planned"), dataCell("Native MCP at unusualwhales.com/public-api/mcp. 20+ tools. Needs API token. Highest-priority MCP upgrade.")] }),
      ],
    }),
    ...spacer(1),
    pageBreak(),
  ];
}

// =============================================================================
// 3. Design Decisions
// =============================================================================

function buildDesignDecisions() {
  return [
    h1("3. Design Decisions", "s3"),

    h2("3.1 Broker Selection"),
    twoColTable([
      ["Decision", "IG UK only"],
      ["Rationale", "Full REST API with CFD and spread betting support. Plus500 eliminated — no API and prohibits automation. Live account from day one (no demo phase)."],
      ["Account", "HTIRV (IG UK)"],
      ["Products traded", "CFDs and Daily Funded Bets (DFB) — rolling contracts with no expiry"],
    ]),
    ...spacer(1),

    h2("3.2 Intelligence Layer"),
    twoColTable([
      ["Decision", "Claude AI (Anthropic) via Cloud Routines"],
      ["Rationale", "All trading logic lives in Claude prompt/context. The Python layer is a thin execution shim (~200 lines). This makes the system trivial to update — changing trading logic requires no code deployment."],
      ["Python role", "ig_shim.py: IG API calls only. No trading decisions, no signal logic."],
    ]),
    ...spacer(1),

    h2("3.3 Scheduling"),
    twoColTable([
      ["Decision", "cron-job.org → GitHub workflow_dispatch → GitHub Actions"],
      ["Rationale", "Local machine must be switchable off at any time. Cloud Routines (local Bash) are disabled. GitHub Actions native cron was unreliable — caused 4-hour delays. cron-job.org fires workflow_dispatch via GitHub API for precise, reliable timing."],
      ["Watchdog fallback", "trading-watchdog.yml retains GitHub native cron as a 30-minute safety net only."],
    ]),
    ...spacer(1),

    h2("3.4 Storage"),
    twoColTable([
      ["Trade data", "Supabase (PostgreSQL) — signal_log, positions, macro_snapshot, user_profiles"],
      ["Secrets", "GitHub Actions Secrets (13 secrets) — never written to disk or logs"],
      ["Decision rationale", "GitHub repository — JSON audit files per session"],
      ["Schema management", "run_schema.py — idempotent, safe to re-run at any time"],
    ]),
    ...spacer(1),

    h2("3.5 Signal Philosophy"),
    para("A trade fires when all three conditions are met:"),
    bullet("Macro gate passes (VIX + yield curve regime check)"),
    bullet("Minimum 2 primary signals are aligned (directional)"),
    bullet("Minimum 1 confirmation signal is present"),
    para("Removed as noise: standalone RSI/MACD, single congress trade under $50k, net premium ticks, generic insider transactions. Only filtered, high-conviction signals remain."),
    ...spacer(1),

    h2("3.6 Multi-User Design"),
    twoColTable([
      ["Architecture", "Single engine, per-user profiles in Supabase"],
      ["Owner", "2% risk/trade · 5 max positions · 3% daily loss limit · Live"],
      ["Wife", "1% risk/trade · 5 max positions · 2% daily loss limit · Live"],
      ["Son", "2% risk/trade · 5 max positions · Paper trade mode"],
      ["Status", "Multi-user loop deferred — Owner only active. Wired in when ready."],
    ]),
    ...spacer(1),

    h2("3.7 Instrument Discovery"),
    para("Two-layer instrument selection per session:"),
    bullet("Layer 1 — Static core instruments (always evaluated, verified IG-tradeable epics)"),
    bullet("Layer 2 — Dynamic discovery via unusual options activity screener (Yahoo Finance)"),
    para("Filters applied before evaluation: market cap > $10B, options volume > 10,000 contracts/day, IG-tradeable (epic lookup or IG search fallback), no existing open position, market hours active. Engine places 0–3 trades per session maximum."),
    ...spacer(1),

    h2("3.8 Position Sizing"),
    twoColTable([
      ["Method", "ATR-based stop distance × risk % of account balance"],
      ["ATR multipliers", "Equities/Indices: 1.5× · Gold/Silver: 1.5× · Oil: 2.0× · FX: 1.2× · Crypto: 2.5–3.0×"],
      ["Minimum R:R", "2.5:1 (raised from 2.0:1 on 2026-06-05)"],
      ["Scale-up", "Position size increases 20% per additional confirmation signal aligned, up to 2× base"],
      ["Stress mode", "SPX down 1–2.5%: position sizes halved. SPX down >2.5%: no new entries."],
    ]),
    pageBreak(),
  ];
}

// =============================================================================
// 3. Tooling Decisions
// =============================================================================

function buildTooling() {
  return [
    h1("4. Tooling Decisions", "s4"),

    h2("4.1 Data Sources"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2600, 3200, 3560],
      rows: [
        new TableRow({ children: [hdrCell("Source"), hdrCell("Used For"), hdrCell("Cost")] }),
        new TableRow({ children: [dataCell("Yahoo Finance (yfinance)", C.lightGrey), dataCell("Price data, BB, options chains, GEX, VWAP, IV rank", C.lightGrey), dataCell("Free", C.lightGrey)] }),
        new TableRow({ children: [dataCell("FRED API"), dataCell("Yield curve (2Y/10Y), real yields, TIPS"), dataCell("Free (API key required)")] }),
        new TableRow({ children: [dataCell("CFTC API", C.lightGrey), dataCell("Commitment of Traders (COT) — commercial net positions", C.lightGrey), dataCell("Free", C.lightGrey)] }),
        new TableRow({ children: [dataCell("SEC EDGAR"), dataCell("Form 4 (director buys), Schedule 13D (activist), 13F (institutions)"), dataCell("Free")] }),
        new TableRow({ children: [dataCell("Capitol Trades", C.lightGrey), dataCell("US Senate equity purchase disclosures", C.lightGrey), dataCell("Free tier", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Quiver Quant"), dataCell("Senator scoring — win rate × excess return"), dataCell("Free tier")] }),
        new TableRow({ children: [dataCell("EIA API", C.lightGrey), dataCell("Oil inventory data (weekly EIA report)", C.lightGrey), dataCell("Free (API key required)", C.lightGrey)] }),
        new TableRow({ children: [dataCell("ForexFactory"), dataCell("Economic calendar — blocks trades 30 min before high-impact events"), dataCell("Free")] }),
        new TableRow({ children: [dataCell("Dataroma", C.lightGrey), dataCell("Superinvestor 13F holdings (Buffett, Ackman, Burry, Tepper etc.)", C.lightGrey), dataCell("Free", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),

    h2("4.2 Infrastructure"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2600, 6760],
      rows: [
        new TableRow({ children: [hdrCell("Tool"), hdrCell("Role")] }),
        new TableRow({ children: [dataCell("GitHub Actions", C.lightGrey), dataCell("Session workflow runner. workflow_dispatch triggered by cron-job.org.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("cron-job.org"), dataCell("External cron service — fires GitHub workflow_dispatch via API at precise times.")] }),
        new TableRow({ children: [dataCell("Supabase", C.lightGrey), dataCell("PostgreSQL database — trades, positions, signals, user profiles, HVF scan log.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("IG UK REST API"), dataCell("Broker execution — session auth, position open/close, trailing stops, activity history.")] }),
        new TableRow({ children: [dataCell("Slack", C.lightGrey), dataCell("4-channel notification system — trades, signals, alerts, weekly digest.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Claude AI"), dataCell("Anthropic API — intelligence layer running inside Cloud Routines (scheduled agents).")] }),
      ],
    }),
    ...spacer(1),

    h2("4.3 Python Libraries"),
    threeColTable(
      ["Library", "Purpose", "Install"],
      [
        ["yfinance", "Market data, options chains", "pip install yfinance"],
        ["pg8000", "Supabase PostgreSQL driver", "pip install pg8000"],
        ["requests", "HTTP calls (IG API, Slack, external APIs)", "pip install requests"],
        ["numpy / pandas", "Numerical and data processing", "pip install numpy pandas"],
        ["python-dotenv", "Local .env loading (dev only)", "pip install python-dotenv"],
      ]
    ),
    pageBreak(),
  ];
}

// =============================================================================
// 4. Process Flow
// =============================================================================

function buildProcessFlow() {
  return [
    h1("5. Process Flow", "s5"),

    h2("5.1 Session Lifecycle"),
    para("Each session follows this fixed sequence:"),
    ...spacer(1),
    ...processStepFlow([
      { num: "1", label: "Trigger",    sublabel: "cron-job.org",   color: C.navy    },
      { num: "2", label: "Guard",      sublabel: "Dup check",      color: "2E75B6"  },
      { num: "3", label: "Health",     sublabel: "IG check",       color: "4472C4"  },
      { num: "4", label: "Macro Gate", sublabel: "VIX/Yield/SPX",  color: "7F6000"  },
      { num: "5", label: "Calendar",   sublabel: "ForexFactory",   color: "C05A17"  },
      { num: "6", label: "Scan",       sublabel: "All instruments",color: "C00000"  },
      { num: "7", label: "Signals",    sublabel: "Full stack",     color: "843C0C"  },
      { num: "8", label: "Execute",    sublabel: "ig_shim.py",     color: "375623"  },
    ]),
    captionPara("Figure 4 — Session lifecycle: every step must succeed before proceeding to the next"),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [600, 2400, 6360],
      rows: [
        new TableRow({ children: [hdrCell("#"), hdrCell("Step"), hdrCell("Detail")] }),
        new TableRow({ children: [dataCell("1", C.lightGrey), dataCell("Trigger", C.lightGrey), dataCell("cron-job.org fires GitHub workflow_dispatch at scheduled time.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("2"), dataCell("Duplicate guard"), dataCell("Check macro_snapshot table — skip if session already ran today. Override with FORCE_RUN=true.")] }),
        new TableRow({ children: [dataCell("3", C.lightGrey), dataCell("Health check", C.lightGrey), dataCell("Verify IG API connectivity and account balance before proceeding.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("4"), dataCell("Macro gate"), dataCell("VIX level · 2Y/10Y yield spread · SPX intraday stress. All must pass before scanning instruments.")] }),
        new TableRow({ children: [dataCell("5", C.lightGrey), dataCell("Calendar block", C.lightGrey), dataCell("Check ForexFactory for high-impact events within 30 minutes. Block if found.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("6"), dataCell("Instrument scan"), dataCell("Run full signal stack against session instrument list (static core + dynamic discovery).")] }),
        new TableRow({ children: [dataCell("7", C.lightGrey), dataCell("Signal evaluation", C.lightGrey), dataCell("For each instrument: primary signals scored, confirmations counted, trade decision made.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("8"), dataCell("Trade execution"), dataCell("Up to 3 trades per session. Position size = (balance × risk%) / stop_distance × stress_mult.")] }),
        new TableRow({ children: [dataCell("9", C.lightGrey), dataCell("Notification", C.lightGrey), dataCell("Session summary → #signals. Each trade opened → #trades. Errors → #alerts.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("10"), dataCell("DB logging"), dataCell("signal_log, positions, macro_snapshot written to Supabase.")] }),
      ],
    }),
    ...spacer(1),

    h2("5.2 Signal Stack"),
    para("Signals are evaluated in layers. Each layer is independent — a failure in one does not prevent others from running."),
    ...spacer(1),
    ...signalFunnelDiagram(),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [1800, 2000, 5560],
      rows: [
        new TableRow({ children: [hdrCell("Layer"), hdrCell("Type"), hdrCell("Sources")] }),
        new TableRow({ children: [dataCell("Macro Gate", C.lightBlue, true), dataCell("GATE (must pass)", C.lightBlue), dataCell("VIX > 35 → FAIL · Yield spread < -1% → FAIL · SPX down > 2.5% → FAIL", C.lightBlue)] }),
        new TableRow({ children: [dataCell("Options Flow", C.white, true), dataCell("Primary"), dataCell("Yahoo Finance options chains — call/put ratio + IV rank. Bull: C/P > 1.2. Bear: C/P < 0.8.")] }),
        new TableRow({ children: [dataCell("Bollinger Bands", C.lightGrey, true), dataCell("Primary", C.lightGrey), dataCell("BB squeeze detection + directional breakout. 20-period, 2σ bands.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("HVF Pattern", C.white, true), dataCell("Primary (bypass)"), dataCell("Hunt Volatility Funnel — H1/L1/H2/L2/H3/L3 pattern. If fired with >=2.5:1 R:R, bypasses 2-primary requirement.")] }),
        new TableRow({ children: [dataCell("GEX", C.lightGrey, true), dataCell("Confirmation", C.lightGrey), dataCell("Gamma Exposure by strike. Computed from Yahoo Finance chains. Directional pin/repel signal.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("VWAP", C.white, true), dataCell("Confirmation"), dataCell("Intraday position relative to VWAP. Above = bullish bias, Below = bearish bias.")] }),
        new TableRow({ children: [dataCell("COT", C.lightGrey, true), dataCell("Confirmation", C.lightGrey), dataCell("CFTC Commitment of Traders — commercial net position and weekly change.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Director Buys", C.white, true), dataCell("Confirmation"), dataCell("SEC Form 4 — open-market purchases only. >$50k, cluster of 2+ within 30 days weighted highest.")] }),
        new TableRow({ children: [dataCell("Senate Signal", C.lightGrey, true), dataCell("Confirmation", C.lightGrey), dataCell("Capitol Trades — equity buys from senators with score ≥ threshold (win rate × excess return).", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Superinvestor", C.white, true), dataCell("Confirmation"), dataCell("Dataroma 13F — new buys/adds from top 10 tracked investors within past 90 days.")] }),
        new TableRow({ children: [dataCell("Social Mention", C.lightGrey, true), dataCell("Confirmation", C.lightGrey), dataCell("Trump (Truth Social), Musk (X), Aschenbrenner, Asklivermore — mention in last 24h.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Intraday Guard", C.white, true), dataCell("Risk Block"), dataCell("Blocks trade if instrument moved > 2.0× ATR from today's open (falling-knife protection).")] }),
      ],
    }),
    ...spacer(1),

    h2("5.3 Monitor Sessions"),
    para("UK_MONITOR and AUS_MONITOR run on a scheduled interval (UK_MONITOR: every 15 minutes, 09:00–16:45 Mon–Fri). They do not scan for new entry signals — they check:"),
    bullet("Trailing stop updates for open positions"),
    bullet("Intraday deterioration (instrument now below key support or above key resistance)"),
    bullet("Whether any open positions should be exited early"),
    ...spacer(1),

    h2("5.4 HVF (Hunt Volatility Funnel) Daily Report"),
    para("The Hunt Volatility Funnel (HVF) is a price action pattern where successive highs are lower and successive lows are higher — creating a converging price channel (funnel). When volatility compresses to this degree, a sharp directional breakout typically follows."),
    ...spacer(1),
    hvfDiagram(),
    captionPara("Figure 5 — HVF pattern: three converging highs and lows. Entry above H3, stop below L3, target = H1–L1 range projected from H3."),
    ...spacer(1),
    para("run_hvf_report.py runs independently of session scripts. It scans FTSE 100, FTSE 250, and S&P 500 constituents across five timeframes (daily-30, daily-60, daily-90, daily-220, weekly) for HVF patterns and posts a structured report to #claude-trading-signals. Results are logged to the hvf_scan_log table."),
    para("Only patterns with ≥ 2.5:1 R:R appear in the TRADEABLE section. Patterns with valid structure but R:R < 2.5:1 appear in DEVELOPING (watch list)."),
    pageBreak(),
  ];
}

// =============================================================================
// 5. Scheduling
// =============================================================================

function buildScheduling() {
  return [
    h1("6. Scheduling", "s6"),

    h2("6.1 Session Schedule (UTC)"),
    ...sessionTimelineDiagram(),
    ...spacer(1),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2200, 1800, 2360, 2600, 400],
      rows: [
        new TableRow({ children: [hdrCell("Session"), hdrCell("UTC Time"), hdrCell("Days"), hdrCell("Purpose"), hdrCell("")] }),
        new TableRow({ children: [dataCell("AUS_OPEN", C.lightGrey), dataCell("01:00", C.lightGrey), dataCell("Mon–Fri", C.lightGrey), dataCell("Asia/Pacific session open — scan + trade", C.lightGrey), dataCell("", C.lightGrey)] }),
        new TableRow({ children: [dataCell("AUS_MONITOR"), dataCell("03:00"), dataCell("Mon–Fri"), dataCell("Asia position monitor — trailing stops"), dataCell("")] }),
        new TableRow({ children: [dataCell("UK_OPEN", C.lightGrey), dataCell("09:00", C.lightGrey), dataCell("Mon–Fri", C.lightGrey), dataCell("UK/Europe session open — scan + trade", C.lightGrey), dataCell("", C.lightGrey)] }),
        new TableRow({ children: [dataCell("UK_MONITOR"), dataCell("Every 15 min 09:00–16:45"), dataCell("Mon–Fri"), dataCell("UK position monitor + intraday checks"), dataCell("")] }),
        new TableRow({ children: [dataCell("US_OPEN", C.lightGrey), dataCell("15:30", C.lightGrey), dataCell("Mon–Fri", C.lightGrey), dataCell("US session open — scan + trade", C.lightGrey), dataCell("", C.lightGrey)] }),
        new TableRow({ children: [dataCell("SESSION_CLOSE"), dataCell("21:00"), dataCell("Mon–Fri"), dataCell("End-of-day position review + report"), dataCell("")] }),
        new TableRow({ children: [dataCell("WEEKEND_REVIEW", C.lightGrey), dataCell("10:00", C.lightGrey), dataCell("Saturday", C.lightGrey), dataCell("HVF report, COT refresh, senator scoring, weekly digest", C.lightGrey), dataCell("", C.lightGrey)] }),
        new TableRow({ children: [dataCell("PREMARKET_BRIEF"), dataCell("21:00"), dataCell("Sunday"), dataCell("Pre-week briefing before Asia open"), dataCell("")] }),
      ],
    }),
    ...spacer(1),

    h2("6.2 Scheduling Architecture"),
    para("Each session uses this chain:"),
    bullet("cron-job.org (external cron) fires an HTTP POST to GitHub API at the scheduled UTC time"),
    bullet("GitHub receives the workflow_dispatch event and queues the Actions runner"),
    bullet("GitHub Actions runner executes: python run_session.py <SESSION_NAME>"),
    bullet("run_session.py checks the duplicate-run guard, then runs the session handler"),
    ...spacer(1),

    h2("6.3 Watchdog"),
    para("trading-watchdog.yml runs every 30 minutes via GitHub native cron (00:00–21:59 UTC, Mon–Fri). It checks that expected sessions produced signal_log rows within their expected window. If a session is overdue or produced no signals despite a macro_snapshot existing, it alerts to #claude-trading-alerts."),
    ...spacer(1),

    h2("6.4 FORCE_RUN Override"),
    para("Set FORCE_RUN=true in the GitHub Actions workflow environment to bypass the duplicate-run guard. Use this when deliberately re-scanning mid-session after a code change."),
    pageBreak(),
  ];
}

// =============================================================================
// 6. Instruments
// =============================================================================

function buildInstruments() {
  return [
    h1("7. Instrument Configuration", "s7"),

    h2("7.1 Instrument Classes"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2000, 5000, 2360],
      rows: [
        new TableRow({ children: [hdrCell("Class"), hdrCell("Instruments"), hdrCell("IG Epic Prefix")] }),
        new TableRow({ children: [dataCell("FX", C.lightGrey), dataCell("GBPUSD (GBP/USD)  ·  AUDUSD (AUD/USD)  ·  USDJPY (USD/JPY)  ·  EURUSD (EUR/USD)  ·  USDCAD (USD/CAD)  ·  USDCHF (USD/CHF)  ·  NZDUSD (NZD/USD)", C.lightGrey), dataCell("CS.D.*", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Precious Metals"), dataCell("XAUUSD (Spot Gold)  ·  XAGUSD (Spot Silver)  ·  PLATINUM  ·  PALLADIUM  ·  COPPER  ·  ZINC  ·  NICKEL  ·  LEAD  ·  ALUMINIUM"), dataCell("CS.D.*")] }),
        new TableRow({ children: [dataCell("Energy", C.lightGrey), dataCell("OIL / USOIL (US Crude Oil WTI)", C.lightGrey), dataCell("CC.D.*", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Indices"), dataCell("SPX500 (S&P 500)  ·  NASDAQ (NASDAQ 100)  ·  DOW (Dow Jones 30)  ·  UK100 (FTSE 100)  ·  JPN225 (Nikkei 225)  ·  HK50 (Hang Seng)"), dataCell("IX.D.*")] }),
        new TableRow({ children: [dataCell("US Equities — Core", C.lightGrey), dataCell("AAPL (Apple)  ·  MSFT (Microsoft)  ·  NVDA (NVIDIA)  ·  AMZN (Amazon)  ·  GOOGL (Alphabet)  ·  META (Meta Platforms)  ·  AMD (Advanced Micro Devices)  ·  AVGO (Broadcom)  ·  TSLA (Tesla)  ·  PLTR (Palantir)", C.lightGrey), dataCell("UA/UB/UD.D.*", C.lightGrey)] }),
        new TableRow({ children: [dataCell("US Equities — Semis"), dataCell("INTC (Intel)  ·  QCOM (Qualcomm)  ·  MU (Micron Technology)  ·  AMAT (Applied Materials)  ·  KLAC (KLA Corp)  ·  ASML (ASML Holding NV)"), dataCell("UA/EG.D.*")] }),
        new TableRow({ children: [dataCell("US Equities — AI/Tech Picks", C.lightGrey), dataCell("NBIS (Nebius Group)  ·  CRWV (CoreWeave)  ·  CLSK (CleanSpark)  ·  RIOT (Riot Platforms)  ·  SNDK (SanDisk)  ·  BE (Bloom Energy)  ·  IREN (Iris Energy)  ·  APLD (Applied Digital)  ·  BTDR (Bitdeer Technologies)  ·  IBM (IBM Corp)  ·  DELL (Dell Technologies)  ·  NOW (ServiceNow)  ·  CRWD (CrowdStrike)  ·  NOK (Nokia OYJ ADR)  ·  TE (TE Connectivity)  ·  SEI (SEI Investments)", C.lightGrey), dataCell("Various", C.lightGrey)] }),
        new TableRow({ children: [dataCell("UK Equities"), dataCell("BP (BP PLC)  ·  LLOY (Lloyds Banking Group)  ·  BARC (Barclays PLC)  ·  AZN (AstraZeneca PLC)  ·  RIO (Rio Tinto PLC)  ·  GSK (GSK PLC)  ·  VOD (Vodafone Group PLC)  ·  RR (Rolls-Royce Holdings PLC)"), dataCell("KA.D.*")] }),
        new TableRow({ children: [dataCell("Crypto", C.lightGrey), dataCell("BTCUSD (Bitcoin)  ·  ETHUSD (Ethereum)  ·  XRPUSD (XRP)  ·  SOLUSD (Solana)  ·  BNBUSD (BNB / Binance Coin)", C.lightGrey), dataCell("CS.D.*", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),
    para("Note: DAX 40 has been suspended as of 2026-06-05 and removed from all instrument maps. Re-add by restoring the DAX entries in config.py when reinstated."),
    ...spacer(1),

    h2("7.2 Session Instrument Lists"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2000, 7360],
      rows: [
        new TableRow({ children: [hdrCell("Session"), hdrCell("Core Instruments")] }),
        new TableRow({ children: [
          dataCell("AUS_OPEN", C.lightBlue, true),
          dataCell("JPN225 (Nikkei 225)  ·  HK50 (Hang Seng)  ·  CN50 (FTSE China A50)  ·  XAUUSD (Spot Gold)  ·  AUDUSD (AUD/USD)  ·  USDJPY (USD/JPY)  ·  BTCUSD (Bitcoin)  ·  ETHUSD (Ethereum)  ·  XRPUSD (XRP)  ·  SOLUSD (Solana)  ·  BNBUSD (BNB)", C.lightGrey),
        ]}),
        new TableRow({ children: [
          dataCell("UK_OPEN", C.lightBlue, true),
          dataCell("UK100 (FTSE 100)  ·  GBPUSD (GBP/USD)  ·  XAUUSD (Spot Gold)  ·  BTCUSD (Bitcoin)  ·  ETHUSD (Ethereum)  ·  XRPUSD (XRP)  ·  SOLUSD (Solana)  ·  BNBUSD (BNB)"),
        ]}),
        new TableRow({ children: [
          dataCell("US_OPEN", C.lightBlue, true),
          dataCell("SPX500 (S&P 500)  ·  NVDA (NVIDIA)  ·  META (Meta Platforms)  ·  MSFT (Microsoft)  ·  AAPL (Apple)  ·  XAUUSD (Spot Gold)  ·  OIL (WTI Crude)  ·  NBIS (Nebius Group)  ·  CRWV (CoreWeave)  ·  CLSK (CleanSpark)  ·  RIOT (Riot Platforms)  ·  SNDK (SanDisk)  ·  BE (Bloom Energy)  ·  TE (TE Connectivity)  ·  IBM (IBM Corp)  ·  DELL (Dell Technologies)  ·  NOW (ServiceNow)  ·  PLTR (Palantir)  ·  CRWD (CrowdStrike)  ·  MU (Micron Technology)  ·  IREN (Iris Energy)  ·  APLD (Applied Digital)  ·  BTDR (Bitdeer Technologies)  ·  SEI (SEI Investments)  ·  NOK (Nokia OYJ ADR)  ·  BTCUSD (Bitcoin)  ·  ETHUSD (Ethereum)  ·  XRPUSD (XRP)  ·  SOLUSD (Solana)  ·  BNBUSD (BNB)", C.lightGrey),
        ]}),
      ],
    }),
    pageBreak(),
  ];
}

// =============================================================================
// 7. Configuration
// =============================================================================

function buildConfiguration() {
  return [
    h1("8. Configuration", "s8"),

    h2("8.1 Risk Constants (config.py)"),
    twoColTable([
      ["MIN_RISK_REWARD", "2.5 — minimum R:R for any trade to be placed"],
      ["MAX_SPREAD_PCT", "0.5% — spread above this blocks the trade"],
      ["MAX_SPREAD_TO_STOP_RATIO", "0.5 — spread must be < 50% of stop distance"],
      ["SPREAD_RETRY_ATTEMPTS", "3 retries at 30-second intervals when spread is temporarily wide"],
      ["MAX_TRADES_PER_SESSION", "3 trades maximum per session (all users combined)"],
      ["CALENDAR_BLOCK_MINUTES", "30 — no new trades within 30 minutes of a high-impact event"],
      ["VIX_GATE_THRESHOLD", "35.0 — VIX above this fails the macro gate"],
      ["YIELD_SPREAD_GATE_THRESHOLD", "-1.0% — yield spread below this fails the macro gate"],
      ["SPX_HIGH_STRESS_PCT", "-2.5% — SPX down more than this: no new entries"],
      ["SPX_STRESS_PCT", "-1.0% — SPX down 1–2.5%: position sizes halved"],
      ["INTRADAY_GUARD_ATR_MULTIPLIER", "2.0× ATR — instrument moved more than this from open: trade blocked"],
      ["MIN_PRIMARY_SIGNALS", "2 — minimum primary signals to trigger a trade (HVF bypasses this)"],
      ["MIN_CONFIRMATION_SIGNALS", "1 — minimum confirmation signals required"],
    ]),
    ...spacer(1),

    h2("8.2 ATR Multipliers"),
    para("ATR multipliers control stop distance. A wider multiplier gives the position more room to breathe and reduces noise-based stop-outs."),
    threeColTable(
      ["Instrument Class", "Multiplier", "Rationale"],
      [
        ["Equities, Indices", "1.5×", "Standard — liquid, predictable volatility"],
        ["Gold, Silver", "1.5×", "Trending instrument — tight stop adequate"],
        ["Oil (WTI)", "2.0×", "High volatility, news-driven spikes"],
        ["FX pairs", "1.2×", "Lower volatility per point"],
        ["BTC, ETH, BNB", "2.5×", "Crypto noise — standard daily moves 3–8%"],
        ["XRP, SOL", "3.0×", "Higher relative volatility vs ATR"],
      ]
    ),
    pageBreak(),
  ];
}

// =============================================================================
// 8. Messaging, Reporting & Alerting
// =============================================================================

function buildMessaging() {
  return [
    h1("9. Messaging, Reporting & Alerting", "s9"),

    h2("9.1 Slack Channels"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 3760, 2800],
      rows: [
        new TableRow({ children: [hdrCell("Channel"), hdrCell("Purpose"), hdrCell("Env Var")] }),
        new TableRow({ children: [dataCell("#claude-trading-trades", C.lightGrey), dataCell("Trade opened / closed — entry, stop, target, R:R, P&L", C.lightGrey), dataCell("SLACK_TRADES", C.lightGrey)] }),
        new TableRow({ children: [dataCell("#claude-trading-signals"), dataCell("Session scan summaries, signal breakdowns, HVF daily report"), dataCell("SLACK_SIGNALS")] }),
        new TableRow({ children: [dataCell("#claude-trading-alerts", C.lightGrey), dataCell("Circuit breakers, macro gate failures, system errors, watchdog alerts", C.lightGrey), dataCell("SLACK_ALERTS", C.lightGrey)] }),
        new TableRow({ children: [dataCell("#claude-trading-weekly"), dataCell("Weekend digest: P&L, senator scores, superinvestor positions"), dataCell("SLACK_WEEKLY")] }),
        new TableRow({ children: [dataCell("#claude-trading-daily", C.lightGrey), dataCell("End-of-day executive report, UK morning brief (run_daily_report.py, run_uk_morning_brief.py)", C.lightGrey), dataCell("SLACK_DAILY", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),

    h2("9.2 Trade Notifications (#trades)"),
    para("Every trade opened sends a Block Kit message including: instrument name, direction (BUY/SELL), entry price, stop level, target, R:R ratio, position size, and session name. Closed trades include P&L, close reason (STOP_HIT / TARGET_HIT / MANUAL), and running daily P&L."),
    ...spacer(1),

    h2("9.3 Signal Notifications (#signals)"),
    para("At the end of each session open, a session summary is posted showing: macro conditions, number of instruments scanned, list of trade candidates, and for each candidate: options flow, BB breakout, HVF pattern (type, signal, R:R), GEX, VWAP, COT, director buys, senate signal, superinvestor, and social mentions."),
    ...spacer(1),

    h2("9.4 HVF Report (#signals)"),
    para("The daily HVF report posts to #claude-trading-signals with three sections:"),
    bullet("READY / TRIGGERED — patterns with ≥ 2.5:1 R:R, grouped by index"),
    bullet("DEVELOPING — valid pattern structure, R:R < 2.5:1 (watch list)"),
    bullet("Footer — scan coverage, threshold, and generation time"),
    ...spacer(1),

    h2("9.5 Alert Types (#alerts)"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [3000, 6360],
      rows: [
        new TableRow({ children: [hdrCell("Alert"), hdrCell("Trigger")] }),
        new TableRow({ children: [dataCell("Macro gate failed", C.lightGrey), dataCell("VIX > 35, yield spread < -1%, or SPX down > 2.5%", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Circuit breaker"), dataCell("IG unreachable, daily loss limit hit, spread too wide after retries, session skipped")] }),
        new TableRow({ children: [dataCell("Calendar block", C.lightGrey), dataCell("High-impact economic event within 30 minutes", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Stop slippage"), dataCell("Position closed significantly worse than stop level — reports expected vs actual loss")] }),
        new TableRow({ children: [dataCell("Session watchdog", C.lightGrey), dataCell("Session ran (macro_snapshot exists) but produced no signal_log rows, or session overdue", C.lightGrey)] }),
        new TableRow({ children: [dataCell("System error"), dataCell("Any unhandled exception in any script — full traceback posted")] }),
      ],
    }),
    ...spacer(1),

    h2("9.6 End-of-Day Report (#daily)"),
    para("run_daily_report.py runs after SESSION_CLOSE. It posts a single executive summary to #claude-trading-daily covering: macro conditions for the day, all trades opened with signal reasoning, all trades closed with P&L and close reason, overnight positions, notable market moves > 3% that were not traded (with signal gap explained), daily P&L per user, and tomorrow's high-impact economic events."),
    para("The UK morning brief (run_uk_morning_brief.py) also posts to #claude-trading-daily — it covers the 07:00–09:00 UTC window before UK_OPEN and summarises macro, signals fired, and any early trades placed."),
    pageBreak(),
  ];
}

// =============================================================================
// 9. Secrets & Security
// =============================================================================

function buildSecurity() {
  return [
    h1("10. Secrets & Security", "s10"),

    h2("10.1 Secret Store"),
    para("All credentials are stored exclusively as GitHub Actions Secrets. They are never written to source code, log files, or the Supabase database. The Python layer loads them at runtime via os.environ."),
    ...spacer(1),

    h2("10.2 Complete Secrets Register"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [3400, 3160, 2800],
      rows: [
        new TableRow({ children: [hdrCell("Secret Name"), hdrCell("Purpose"), hdrCell("Provider")] }),
        new TableRow({ children: [dataCell("IG_API_KEY", C.lightGrey), dataCell("IG developer API key", C.lightGrey), dataCell("IG UK Developer Portal", C.lightGrey)] }),
        new TableRow({ children: [dataCell("IG_USERNAME"), dataCell("IG account username (not email)"), dataCell("IG UK")] }),
        new TableRow({ children: [dataCell("IG_PASSWORD", C.lightGrey), dataCell("IG account password", C.lightGrey), dataCell("IG UK", C.lightGrey)] }),
        new TableRow({ children: [dataCell("IG_ACCOUNT_ID"), dataCell("IG account reference (e.g. HTIRV)"), dataCell("IG UK")] }),
        new TableRow({ children: [dataCell("SUPABASE_USER", C.lightGrey), dataCell("Supabase PostgreSQL user (postgres.{project_id})", C.lightGrey), dataCell("Supabase", C.lightGrey)] }),
        new TableRow({ children: [dataCell("SUPABASE_DB_PASSWORD"), dataCell("Supabase database password"), dataCell("Supabase")] }),
        new TableRow({ children: [dataCell("FRED_API_KEY", C.lightGrey), dataCell("FRED API — yield curve, TIPS, real yields", C.lightGrey), dataCell("Federal Reserve (FRED)", C.lightGrey)] }),
        new TableRow({ children: [dataCell("EIA_API_KEY"), dataCell("EIA API — oil inventory data"), dataCell("US EIA")] }),
        new TableRow({ children: [dataCell("SLACK_TRADES", C.lightGrey), dataCell("Webhook — #claude-trading-trades", C.lightGrey), dataCell("Slack", C.lightGrey)] }),
        new TableRow({ children: [dataCell("SLACK_SIGNALS"), dataCell("Webhook — #claude-trading-signals"), dataCell("Slack")] }),
        new TableRow({ children: [dataCell("SLACK_ALERTS", C.lightGrey), dataCell("Webhook — #claude-trading-alerts", C.lightGrey), dataCell("Slack", C.lightGrey)] }),
        new TableRow({ children: [dataCell("SLACK_WEEKLY"), dataCell("Webhook — #claude-trading-weekly"), dataCell("Slack")] }),
        new TableRow({ children: [dataCell("SLACK_DAILY", C.lightGrey), dataCell("Webhook — #claude-trading-daily (end-of-day reports, morning brief)", C.lightGrey), dataCell("Slack", C.lightGrey)] }),
        new TableRow({ children: [dataCell("QUIVER_QUANT_API_KEY"), dataCell("Quiver Quant — senator scoring data"), dataCell("Quiver Quant")] }),
      ],
    }),
    ...spacer(1),

    h2("10.3 Security Principles"),
    bullet("No credentials ever written to logs, files, or database rows."),
    bullet("Python code loads credentials only from os.environ — never from file paths."),
    bullet("All Supabase connections use SSL (ssl_context=True)."),
    bullet("GitHub Secrets are repository-scoped — not accessible outside Actions workflows."),
    bullet("Gitleaks pre-commit hook runs on every commit — blocks accidental secret commits."),
    bullet("IG API session tokens refreshed before 6-hour TTL expires (at 5.5 hours)."),
    bullet("Paper trade mode per-user — Son's account never sends real orders to IG."),
    ...spacer(1),

    h2("10.4 Data Classification"),
    twoColTable([
      ["Trade data (Supabase)", "Confidential — contains P&L, position sizes, account balance references"],
      ["Signal data (Supabase)", "Internal — raw signal scores, macro snapshots"],
      ["Source code (GitHub)", "Confidential — contains system architecture and strategy logic"],
      ["Slack messages", "Internal — trade notifications contain real P&L figures"],
    ]),
    pageBreak(),
  ];
}

// =============================================================================
// Shared code-block helper
// =============================================================================

function codeBlock(lines) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({ children: [new TableCell({
      borders,
      margins: { top: 120, bottom: 120, left: 200, right: 200 },
      shading: { fill: "1F1F1F", type: ShadingType.CLEAR },
      children: lines.map(([text, isComment]) =>
        new Paragraph({ children: [new TextRun({
          text, font: "Courier New", size: 20,
          color: isComment ? "888888" : "00FF00",
        })] })
      ),
    })]})],
  });
}

function noteBox(text) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({ children: [new TableCell({
      borders: { top: border1, bottom: border1, left: { style: BorderStyle.SINGLE, size: 12, color: C.blue }, right: border1 },
      shading: { fill: C.lightBlue, type: ShadingType.CLEAR },
      margins: cellPad,
      children: [new Paragraph({ children: [new TextRun({ text, font: "Arial", size: 20, color: C.navy })] })],
    })]})],
  });
}

// =============================================================================
// 10. Deployment Guide
// =============================================================================

function buildDeployment() {
  return [
    h1("11. Deployment Guide — New Installation", "s11"),
    para("This section provides step-by-step installation instructions for each component of the EndToEndTrading system — including the non-obvious steps that are not covered in standard quick-start guides."),
    ...spacer(1),

    h2("11.1 Prerequisites — Account Creation"),
    para("You will need to create accounts on several different services before the system can run. Each service provides one piece of the puzzle. Create them in the order shown — some accounts are needed before others."),
    ...spacer(1),
    stepPanel(
      "IG UK — Get Your API Key",
      "ig.com → sign in → My IG (top right) → Settings → API",
      [
        "Log in to your IG account at ig.com",
        "Click 'My IG' (top right of the page) → 'Settings'",
        "Scroll down and find the 'API access' or 'My API' section",
        "Click 'Manage API access' or 'Generate API key'",
        "Enable API access — there may be a terms and conditions checkbox",
        "Your API key will appear as a long string of letters and numbers — copy it",
        "This is your IG_API_KEY secret",
        "Your IG_USERNAME is your IG login username (not your email)",
        "Your IG_ACCOUNT_ID is the account reference shown in your IG dashboard (e.g. HTIRV)",
      ],
      [
        { label: "What is this for?", text: "The IG API key allows the trading system to place orders on your behalf. Think of it like giving the system a key to the trading room, but you keep the master key — you can revoke it at any time.", color: "info" },
        { label: "Never share this", text: "Your IG API key + username + password together can place real trades with real money. Treat them like your online banking password. Store them only in GitHub Secrets — never in a text file or email.", color: "error" },
      ]
    ),
    ...spacer(1),
    para("Summary of all accounts needed:"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2400, 4560, 2400],
      rows: [
        new TableRow({ children: [hdrCell("Service"), hdrCell("URL / Notes"), hdrCell("What You Need")] }),
        new TableRow({ children: [dataCell("IG UK", C.lightGrey), dataCell("ig.com — live CFD account. Apply for API access via My IG → API.", C.lightGrey), dataCell("API key, username, password, account ID", C.lightGrey)] }),
        new TableRow({ children: [dataCell("GitHub"), dataCell("github.com — create a private repository (e.g. Red7Al/ClaudeCode)."), dataCell("Personal access token with workflow scope")] }),
        new TableRow({ children: [dataCell("Supabase", C.lightGrey), dataCell("supabase.com — create a project in the eu-west-1 region.", C.lightGrey), dataCell("Project URL, user, DB password", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Slack"), dataCell("slack.com — create a workspace, 4 channels, 4 incoming webhooks."), dataCell("4 webhook URLs")] }),
        new TableRow({ children: [dataCell("cron-job.org", C.lightGrey), dataCell("cron-job.org — free external cron. Create jobs for each session.", C.lightGrey), dataCell("GitHub PAT for workflow_dispatch", C.lightGrey)] }),
        new TableRow({ children: [dataCell("FRED"), dataCell("fred.stlouisfed.org/docs/api — register for a free API key."), dataCell("API key")] }),
        new TableRow({ children: [dataCell("EIA", C.lightGrey), dataCell("eia.gov/opendata — register for a free API key.", C.lightGrey), dataCell("API key", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Quiver Quant"), dataCell("quiverquant.com — free tier sufficient for senator scoring weekly batch."), dataCell("API key")] }),
      ],
    }),
    ...spacer(1),

    // -----------------------------------------------------------------------
    // 10.2  Claude Code + Claude in Chrome
    // -----------------------------------------------------------------------
    h2("11.2 Claude Code & Claude in Chrome"),

    stepPanel(
      "Step 1 of 2 — Install Claude Code Desktop",
      "claude.ai/download",
      [
        "Open your browser and go to  claude.ai/download",
        "Click the big blue 'Download for Windows' button — this downloads a .exe installer file",
        "Open your Downloads folder and double-click the installer file (e.g. ClaudeSetup.exe)",
        "If Windows shows a 'Windows protected your PC' warning, click 'More info' then 'Run anyway' — this is normal for new software",
        "Follow the installer steps (Next → Next → Install) — defaults are fine",
        "When it opens, sign in with your Anthropic account (the same account used to pay for Claude)",
        "You should see a home screen with a text box — Claude Code is ready",
      ],
      [
        { label: "What is Claude Code?", text: "Think of it like Microsoft Word but for building software with AI. You talk to it in plain English and it writes and edits code for you.", color: "info" },
        { label: "Warning", text: "Do NOT download from anywhere other than claude.ai/download — only get it from the official Anthropic website.", color: "warn" },
      ]
    ),
    ...spacer(1),

    stepPanel(
      "Step 2 of 2 — Install Claude in Chrome Extension",
      "chromewebstore.google.com  (search: Claude for Chrome)",
      [
        "Open Google Chrome (if you don't have it, download it from google.com/chrome first)",
        "In Chrome, go to the Chrome Web Store — search for 'Claude for Chrome'",
        "Click 'Add to Chrome' → click 'Add extension' in the popup",
        "You'll see a small Claude icon appear in the top-right of Chrome (near the address bar)",
        "Click that icon and sign in with the same Anthropic account",
        "Now open Claude Code desktop — you should see 'Claude in Chrome: Connected' in Settings",
      ],
      [
        { label: "Important", text: "Claude Code desktop must be open and running for the Chrome extension to work. The extension talks to the desktop app — not directly to the internet.", color: "warn" },
        { label: "What does this do?", text: "It lets Claude read and interact with web pages in Chrome — useful for checking dashboards, filling in forms, and navigating websites automatically.", color: "info" },
      ]
    ),
    ...spacer(1),

    h3("Claude Code CLI (optional — for launching from terminal)"),
    codeBlock([
      ["# Requires Node.js — install from nodejs.org first if not installed", true],
      ["npm install -g @anthropic-ai/claude-code --foreground-scripts", false],
      ["", false],
      ["# Open the project from PowerShell:", true],
      ["cd \"C:\\Users\\eahin\\OneDrive\\ClaudeCode\\EndToEndTrading\"", false],
      ["claude", false],
    ]),
    noteBox("If you type 'claude' and get 'not recognised', close PowerShell and open a new one. If it still fails, the install path needs to be added to your PATH environment variable — search 'environment variables' in the Windows Start menu."),
    ...spacer(1),
    pageBreak(),

    // -----------------------------------------------------------------------
    // 10.3  GitHub
    // -----------------------------------------------------------------------
    h2("11.3 GitHub — Repository, Secrets & Actions"),

    stepPanel(
      "Step 1 of 4 — Create a GitHub Account and Repository",
      "github.com",
      [
        "Go to github.com and click 'Sign up' (top right). Enter your email, create a password, choose a username",
        "Verify your email address — GitHub will send you a code",
        "Once logged in, click the '+' icon (top right) → 'New repository'",
        "Repository name: ClaudeCode",
        "Set it to 'Private' — this hides your trading code from the public",
        "Do NOT tick 'Add a README file' — leave all the boxes empty",
        "Click the green 'Create repository' button",
      ],
      [
        { label: "What is GitHub?", text: "Think of GitHub as a cloud backup + version history system for your code. Every change is saved with a note explaining what changed and why. It also runs your trading sessions automatically.", color: "info" },
        { label: "Important", text: "Set to Private. Your trading code contains strategy logic — never leave it Public.", color: "warn" },
      ]
    ),
    ...spacer(1),

    stepPanel(
      "Step 2 of 4 — Create a Personal Access Token (PAT)",
      "github.com → top-right avatar → Settings → Developer settings",
      [
        "Click your profile picture (top right) → 'Settings'",
        "Scroll all the way down the left sidebar → click 'Developer settings' (very bottom)",
        "Click 'Personal access tokens' → 'Fine-grained tokens'",
        "Click the green 'Generate new token' button",
        "Token name: EndToEndTrading-cron",
        "Expiration: 1 year",
        "Repository access: select 'Only selected repositories' → pick 'ClaudeCode'",
        "Under 'Permissions' → 'Repository permissions' → find 'Actions' → set to 'Read and write'",
        "Scroll down → click 'Generate token'",
        "IMMEDIATELY copy the token (starts with 'github_pat_'). Save it somewhere safe — GitHub only shows it ONCE",
      ],
      [
        { label: "What is a PAT?", text: "A Personal Access Token is like a password that only allows one specific action. The cron-job.org scheduler uses it to trigger your trading sessions without having full access to your GitHub account.", color: "info" },
        { label: "You only see it once!", text: "If you lose the token before saving it, just delete it and generate a new one. No harm done.", color: "warn" },
      ]
    ),
    ...spacer(1),

    stepPanel(
      "Step 3 of 4 — Add Secrets to GitHub",
      "github.com/Red7Al/ClaudeCode/settings/secrets/actions",
      [
        "Open your ClaudeCode repository on GitHub",
        "Click 'Settings' tab (along the top of the repo page)",
        "In the left sidebar: click 'Secrets and variables' → 'Actions'",
        "Click the green 'New repository secret' button",
        "Name: type the secret name exactly as shown (e.g. IG_API_KEY) — capitals matter",
        "Secret: paste your value",
        "Click 'Add secret'",
        "Repeat for all 14 secrets listed in Section 10.2",
      ],
      [
        { label: "Tip", text: "Use the GitHub CLI command 'gh secret set SECRET_NAME' from a terminal — it prompts you to paste the value. Much faster than clicking through the web UI 14 times.", color: "info" },
        { label: "Check names carefully", text: "Secret names are case-sensitive. IG_API_KEY and ig_api_key are different. Always use capitals exactly as shown in the secrets table.", color: "warn" },
      ]
    ),
    ...spacer(1),

    stepPanel(
      "Step 4 of 4 — Verify GitHub Actions is Running",
      "github.com/Red7Al/ClaudeCode/actions",
      [
        "Click the 'Actions' tab in your repository",
        "You should see a list of workflow files (e.g. 'UK Session Open', 'US Session Open')",
        "To test manually: click a workflow → click 'Run workflow' button → click 'Run workflow' again",
        "Watch the run appear in the list — a yellow circle means running, green tick means success, red X means failed",
        "Click the run → click the job name → read the logs to see exactly what happened",
      ],
      [
        { label: "Nothing showing?", text: "Workflows only appear after the first push to the main branch. If the Actions tab is empty, push your code first.", color: "warn" },
        { label: "Red X?", text: "Click the failed run → click the job → scroll to the bottom of the logs. The error message tells you exactly what went wrong. Common causes: missing secret, typo in secret name, Python error.", color: "error" },
      ]
    ),
    ...spacer(1),
    pageBreak(),

    // -----------------------------------------------------------------------
    // 10.4  Slack
    // -----------------------------------------------------------------------
    h2("11.4 Slack — Workspace, Channels & Incoming Webhooks"),
    h3("Create the workspace and channels"),
    bullet("Go to slack.com → Create a new workspace → follow the wizard"),
    bullet("Create 5 channels (plus icon in sidebar → Create a channel): #claude-trading-trades, #claude-trading-signals, #claude-trading-alerts, #claude-trading-weekly, #claude-trading-daily"),
    bullet("Set all channels to Private — only the bot should post here"),
    ...spacer(1),

    stepPanel(
      "Step 1 of 3 — Create a Slack Workspace",
      "slack.com/create",
      [
        "Go to slack.com and click 'Get started for free' or 'Create a workspace'",
        "Enter your email address → click 'Continue'",
        "Check your email and enter the 6-digit confirmation code",
        "Enter a workspace name, e.g. 'My Trading Bot'",
        "Skip the 'invite teammates' step — click 'Skip for now'",
        "You now have a Slack workspace. You'll see a left sidebar with channels",
      ],
      [{ label: "What is Slack?", text: "Slack is a messaging app. The trading system sends you messages here — trade alerts, daily reports, error notifications. Think of it like a dedicated WhatsApp group for your trading bot.", color: "info" }]
    ),
    ...spacer(1),

    stepPanel(
      "Step 2 of 3 — Create the 5 Channels",
      "In your Slack workspace — left sidebar",
      [
        "In the left sidebar, click the '+' next to 'Channels'",
        "Click 'Create a channel'",
        "Name it exactly: claude-trading-trades (lowercase, hyphens) → click 'Create'",
        "Repeat to create 4 more channels:",
        "    Channel 2: claude-trading-signals",
        "    Channel 3: claude-trading-alerts",
        "    Channel 4: claude-trading-weekly",
        "    Channel 5: claude-trading-daily",
        "You should now have 5 channels in your sidebar",
      ],
      [
        { label: "Names matter", text: "The channel names must be typed exactly as above. The system's code looks for these specific names.", color: "warn" },
        { label: "What goes where?", text: "#trades = each trade opened/closed. #signals = scan summaries. #alerts = errors and warnings. #weekly = weekend digest. #daily = morning brief and end-of-day report.", color: "info" },
      ]
    ),
    ...spacer(1),

    stepPanel(
      "Step 3 of 3 — Create Incoming Webhooks (one per channel)",
      "api.slack.com/apps",
      [
        "Go to api.slack.com/apps (this is the Slack developer portal — separate from the Slack messaging app)",
        "Click 'Create New App' → 'From scratch'",
        "App Name: EndToEndTrading Bot → select your workspace → 'Create App'",
        "You'll land on the app settings page. In the left sidebar, click 'Incoming Webhooks'",
        "Click the toggle to turn 'Activate Incoming Webhooks' ON (it turns green)",
        "Scroll down and click 'Add New Webhook to Workspace'",
        "A dropdown appears — choose '#claude-trading-trades' → click 'Allow'",
        "You'll see a webhook URL appear — it looks like: https://hooks.slack.com/services/T.../B.../...",
        "Click 'Copy' to copy it — save it somewhere (this is your SLACK_TRADES secret value)",
        "Click 'Add New Webhook to Workspace' again — repeat for the remaining 4 channels",
        "After all 5 webhooks are created, save each URL as a GitHub Secret",
      ],
      [
        { label: "One URL per channel", text: "Each of the 5 channels gets its OWN webhook URL. Do not reuse the same URL for multiple channels — messages will all go to one place.", color: "warn" },
        { label: "Lost the URL?", text: "Go back to api.slack.com/apps → your app → Incoming Webhooks. All the webhook URLs are listed there permanently.", color: "info" },
      ]
    ),
    ...spacer(1),
    noteBox("To test a webhook: open PowerShell and run:   curl -Uri 'https://hooks.slack.com/services/YOUR/URL' -Method POST -Body '{\"text\":\"test\"}' -ContentType 'application/json'   You should see 'ok' in the terminal and a message appear in Slack."),
    ...spacer(1),
    pageBreak(),

    // -----------------------------------------------------------------------
    // 10.5  Supabase
    // -----------------------------------------------------------------------
    h2("11.5 Supabase — Project Setup & Database Connection"),

    stepPanel(
      "Step 1 of 3 — Create a Supabase Account and Project",
      "supabase.com",
      [
        "Go to supabase.com → click 'Start your project' → sign up (GitHub login is easiest)",
        "Click 'New project'",
        "Organisation: leave as your personal org (created automatically)",
        "Project name: EndToEndTrading",
        "Database Password: click 'Generate a password' — IMMEDIATELY copy and save this password. This is your SUPABASE_DB_PASSWORD secret. You CANNOT see it again after leaving this page",
        "Region: select 'EU West 1 (Ireland)' — this is the closest to UK and reduces lag",
        "Click 'Create new project'",
        "Wait about 2 minutes. You'll see a green 'Project is ready' notice when done",
      ],
      [
        { label: "What is Supabase?", text: "Think of it as a cloud spreadsheet / database that stores all your trade history, signals, and account settings. The trading system reads and writes to it every time it runs.", color: "info" },
        { label: "Save the password NOW", text: "Supabase shows you the database password only once — when you create the project. If you miss it, you must reset it in Settings, which requires updating all your GitHub Secrets.", color: "error" },
      ]
    ),
    ...spacer(1),

    stepPanel(
      "Step 2 of 3 — Find Your Connection Details",
      "supabase.com → your project → Settings → Database",
      [
        "In your project dashboard, click 'Settings' (gear icon, bottom of left sidebar)",
        "Click 'Database' in the settings menu",
        "Scroll down to 'Connection string'",
        "Click the 'Transaction' tab (NOT 'Session' or 'Direct connection')",
        "You'll see a connection string. Find these specific values:",
        "    Host: looks like  aws-0-eu-west-1.pooler.supabase.com",
        "    Port: 6543  (important — NOT 5432)",
        "    User: looks like  postgres.abcdefghijklmnop  (there is a dot, then a long ID)",
        "The User value (postgres.xxxxx) = your SUPABASE_USER secret",
        "Now go to Settings → General to find your 'Reference ID' — a short code like 'hbzmpxbnknofxkmpoyno'",
      ],
      [
        { label: "The most common mistake", text: "People set SUPABASE_USER to just 'postgres'. It must be 'postgres.{your_reference_id}' — with the dot and the ID after it. The connection will silently fail without it.", color: "error" },
        { label: "Transaction mode", text: "Always use the Transaction connection string (port 6543). The Direct connection (port 5432) is blocked by Supabase's firewall for cloud environments like GitHub Actions.", color: "warn" },
      ]
    ),
    ...spacer(1),

    h3("Correct connection format"),
    ...spacer(1),
    codeBlock([
      ["# The connection parameters used in ig_shim.py and signals.py:", true],
      ["host     = \"aws-0-eu-west-1.pooler.supabase.com\"", false],
      ["port     = 6543", false],
      ["database = \"postgres\"", false],
      ["user     = \"postgres.{project_ref}\"  # from SUPABASE_USER secret", false],
      ["password = \"{db_password}\"           # from SUPABASE_DB_PASSWORD secret", false],
      ["ssl      = True                        # always required", false],
    ]),
    ...spacer(1),

    h3("Row Level Security"),
    para("Supabase enables Row Level Security (RLS) by default on new tables. For this system, RLS is disabled on all tables — the Python backend authenticates as the postgres superuser and manages its own access control. If you create tables via the Supabase web UI, you must disable RLS manually: Table Editor → select table → RLS disabled."),
    noteBox("run_schema.py creates all tables with RLS disabled. Only tables created manually via the Supabase UI need this step."),
    ...spacer(1),

    h3("Initialise the schema"),
    codeBlock([
      ["# Set environment variables first (or use .env file)", true],
      ["set SUPABASE_USER=postgres.your_project_ref", false],
      ["set SUPABASE_DB_PASSWORD=your_db_password", false],
      ["", false],
      ["# Run the schema migration", true],
      ["python run_schema.py", false],
      ["# Safe to re-run at any time — all statements use IF NOT EXISTS", true],
    ]),
    para("Tables created: user_profiles, epic_lookup, positions, trade_log, daily_pnl, macro_snapshot, signal_log, cot_snapshot, senator_scores, notable_investors, social_mentions, geopolitical_risk, hvf_scan_log."),
    ...spacer(1),

    stepPanel(
      "Step 3 of 3 — Run the Schema Script",
      "PowerShell — in your EndToEndTrading project folder",
      [
        "Open PowerShell and navigate to the project: cd \"C:\\Users\\eahin\\OneDrive\\ClaudeCode\\EndToEndTrading\"",
        "Set the two required variables (paste your actual values):",
        "    $env:SUPABASE_USER = 'postgres.your_reference_id'",
        "    $env:SUPABASE_DB_PASSWORD = 'your_database_password'",
        "Run the script:  python run_schema.py",
        "You should see lines like: [INFO] Migration OK: signal_log: add hvf_type",
        "If you see errors about 'could not connect', check your SUPABASE_USER format (must have postgres. prefix)",
      ],
      [
        { label: "Safe to re-run", text: "The schema script uses 'IF NOT EXISTS' everywhere — you can run it as many times as you want without breaking anything or losing data.", color: "info" },
      ]
    ),
    ...spacer(1),
    pageBreak(),

    // -----------------------------------------------------------------------
    // 10.6  Quiver Quant
    // -----------------------------------------------------------------------
    h2("11.6 Quiver Quant — Senator Scoring API"),

    stepPanel(
      "How to get your Quiver Quant API key",
      "quiverquant.com",
      [
        "Go to quiverquant.com and click 'Sign Up' (top right)",
        "Register with your email address — free account, no credit card needed",
        "After logging in, click your name or avatar (top right) → 'Dashboard' or 'Account'",
        "Look for an 'API' section or 'API Key' — click to reveal or generate your key",
        "Copy the key and save it as GitHub Secret: QUIVER_QUANT_API_KEY",
      ],
      [
        { label: "What does this data do?", text: "US Senators are legally required to report their stock trades within 45 days. Quiver Quant scores each senator by how profitable their trades historically were. The system only acts on signals from senators with a strong track record.", color: "info" },
        { label: "45-day lag", text: "This signal tells you what senators BOUGHT up to 45 days ago — it's a confirmation signal, not a prediction. Use it alongside other signals, not alone.", color: "warn" },
      ]
    ),
    ...spacer(1),
    pageBreak(),

    // -----------------------------------------------------------------------
    // 10.7  cron-job.org
    // -----------------------------------------------------------------------
    h2("11.7 cron-job.org — External Scheduler"),

    stepPanel(
      "Why cron-job.org? — The problem with GitHub's built-in scheduler",
      "",
      [
        "GitHub has its own built-in scheduler (called 'cron') but it uses a shared queue with millions of other repositories worldwide",
        "During busy times (like 00:00 UTC on weekday mornings when everyone's workflows start), your job can be delayed by HOURS",
        "This actually happened: the UK_OPEN session was delayed 4 hours — the market was half-way through before the system even started",
        "cron-job.org solves this by sending a direct 'trigger' message to GitHub the moment your scheduled time arrives, bypassing the shared queue",
        "Result: your sessions start on time, every time",
      ],
      [{ label: "Cost", text: "cron-job.org free plan allows up to 5-minute intervals and unlimited jobs. The trading system needs jobs every 15 minutes at minimum — free tier is sufficient.", color: "info" }]
    ),
    ...spacer(1),

    stepPanel(
      "Step 1 of 2 — Create a cron-job.org Account",
      "cron-job.org",
      [
        "Go to cron-job.org → click 'Sign up for free'",
        "Register with your email → verify your email",
        "Log in — you'll see a dashboard with a 'Create cronjob' button",
      ],
      []
    ),
    ...spacer(1),

    stepPanel(
      "Step 2 of 2 — Create a Cron Job for Each Session",
      "cron-job.org → dashboard → Create cronjob",
      [
        "Click 'Create cronjob'",
        "Title: EndToEndTrading UK_OPEN  (use a clear name so you know what it does)",
        "URL: https://api.github.com/repos/Red7Al/ClaudeCode/actions/workflows/trading-uk-open.yml/dispatches",
        "Request method: POST",
        "Click 'Headers' → Add 3 headers:",
        "    Header 1: Key = Authorization,  Value = Bearer YOUR_GITHUB_PAT  (paste the token you created in GitHub Step 2)",
        "    Header 2: Key = Accept,  Value = application/vnd.github+json",
        "    Header 3: Key = X-GitHub-Api-Version,  Value = 2022-11-28",
        "Click 'Body' → Request body: {\"ref\":\"main\"}",
        "Click 'Schedule' → choose 'Custom cron expression' → enter the cron for this session",
        "Click 'Create' to save",
        "Repeat for each of the 8 session workflows using the schedule table below",
      ],
      [
        { label: "Test it first!", text: "After creating each job, click 'Run now' to test it manually. Check GitHub Actions to see if the workflow triggered. If not, the most common issue is a wrong header or expired GitHub PAT.", color: "warn" },
        { label: "Cron time = UTC", text: "All times in cron-job.org must be in UTC (Coordinated Universal Time). UK in summer is UTC+1. So UK market open at 09:00 BST = 08:00 UTC.", color: "warn" },
      ]
    ),
    ...spacer(1),
    threeColTable(
      ["Session", "Cron (UTC)", "Workflow File"],
      [
        ["AUS_OPEN",       "0 1 * * 1-5",    "trading-aus-open.yml"],
        ["AUS_MONITOR",    "0 3 * * 1-5",    "trading-aus-monitor.yml"],
        ["UK_OPEN",        "0 9 * * 1-5",    "trading-uk-open.yml"],
        ["UK_MONITOR",     "*/15 9-16 * * 1-5", "trading-uk-monitor.yml"],
        ["US_OPEN",        "30 15 * * 1-5",  "trading-us-open.yml"],
        ["SESSION_CLOSE",  "0 21 * * 1-5",   "trading-session-close.yml"],
        ["WEEKEND_REVIEW", "0 10 * * 6",     "trading-weekend-review.yml"],
        ["PREMARKET_BRIEF","0 21 * * 0",     "trading-sunday-premarket.yml"],
      ]
    ),
    noteBox("Non-obvious: cron-job.org expressions run in UTC. The UK market closes at 16:30 BST = 15:30 UTC in summer. Always express schedule times in UTC regardless of local time."),
    ...spacer(1),
    pageBreak(),

    // -----------------------------------------------------------------------
    // 10.8  Verification & Go-Live
    // -----------------------------------------------------------------------
    h2("11.8 Verification & Go-Live Checklist"),
    h3("Run diagnostics"),
    codeBlock([
      ["python run_diagnostics.py", false],
      ["# Tests: macro gate, options, GEX, price action, signal stack, DB writes, IG", true],
      ["# Posts ✅/❌/⚠ report to #claude-trading-alerts", true],
      ["# NEVER places real trades — safe to run at any time", true],
    ]),
    ...spacer(1),

    h3("Go-live checklist"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [600, 7560, 1200],
      rows: [
        new TableRow({ children: [hdrCell(""), hdrCell("Check"), hdrCell("Status")] }),
        new TableRow({ children: [dataCell("1", C.lightGrey), dataCell("All 13 GitHub Secrets set (verify via Settings → Secrets)", C.lightGrey), dataCell("[ ]", C.lightGrey)] }),
        new TableRow({ children: [dataCell("2"), dataCell("run_schema.py completed without errors"), dataCell("[ ]")] }),
        new TableRow({ children: [dataCell("3", C.lightGrey), dataCell("4 Slack webhooks confirmed working (curl test)", C.lightGrey), dataCell("[ ]", C.lightGrey)] }),
        new TableRow({ children: [dataCell("4"), dataCell("run_diagnostics.py shows all ✅"), dataCell("[ ]")] }),
        new TableRow({ children: [dataCell("5", C.lightGrey), dataCell("cron-job.org jobs created and test-fired successfully", C.lightGrey), dataCell("[ ]", C.lightGrey)] }),
        new TableRow({ children: [dataCell("6"), dataCell("User profile row for Owner exists in user_profiles table"), dataCell("[ ]")] }),
        new TableRow({ children: [dataCell("7", C.lightGrey), dataCell("IG account has API access enabled (My IG → Settings → API)", C.lightGrey), dataCell("[ ]", C.lightGrey)] }),
        new TableRow({ children: [dataCell("8"), dataCell("Manual FORCE_RUN=true test run completed without errors"), dataCell("[ ]")] }),
      ],
    }),
    ...spacer(1),
    pageBreak(),
  ];
}

// =============================================================================
// Appendices — Memory File Content
// =============================================================================

function buildAppendices() {
  return [
    h1("Appendix A: Project Decisions Reference", "appA"),
    para("The following decisions were made and confirmed during the design phase. They represent the source of truth for all architecture choices in this system."),
    ...spacer(1),

    h2("A.1 Broker"),
    twoColTable([
      ["Decision", "IG UK only"],
      ["Rejected", "Plus500 — no API, prohibits automation"],
      ["Account", "HTIRV — live account from day one (no demo phase)"],
    ]),
    ...spacer(1),

    h2("A.2 Scheduling Architecture"),
    twoColTable([
      ["Confirmed", "cron-job.org → GitHub workflow_dispatch → GitHub Actions runner"],
      ["Rejected: Cloud Routines", "Use local Bash tool — fails if machine is off. All trading Routines DISABLED."],
      ["Rejected: GitHub cron", "4-hour delays on shared queue confirmed. Removed from session workflows."],
      ["Rejected: Windows Task Scheduler", "Local machine dependency — never use."],
      ["Watchdog exception", "trading-watchdog.yml retains 30-min GitHub cron as safety net only."],
    ]),
    ...spacer(1),

    h2("A.3 Processing Architecture"),
    twoColTable([
      ["Intelligence", "Claude AI handles all trading decisions via Cloud Routines"],
      ["Execution", "Python shim (ig_shim.py) handles IG API only — no decision logic"],
      ["Design principle", "Changing trading strategy = update Claude prompt. No code deployment required."],
    ]),
    ...spacer(1),

    h2("A.4 Storage"),
    twoColTable([
      ["Trade data", "Supabase (PostgreSQL) — signal_log, positions, trade_log, macro_snapshot, user_profiles"],
      ["Secrets", "GitHub Actions Secrets only — never on disk or in code"],
      ["Audit trail", "GitHub repository — JSON files per session for decision rationale"],
    ]),
    ...spacer(1),

    h2("A.5 Multi-User Parameters"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2400, 2000, 2000, 1600, 1360],
      rows: [
        new TableRow({ children: [hdrCell("User"), hdrCell("Risk/Trade"), hdrCell("Max Positions"), hdrCell("Daily Loss"), hdrCell("Mode")] }),
        new TableRow({ children: [dataCell("Owner", C.lightGrey), dataCell("2%", C.lightGrey), dataCell("5", C.lightGrey), dataCell("3%", C.lightGrey), dataCell("Live", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Wife"), dataCell("1%"), dataCell("5"), dataCell("2%"), dataCell("Live")] }),
        new TableRow({ children: [dataCell("Son", C.lightGrey), dataCell("2%", C.lightGrey), dataCell("5", C.lightGrey), dataCell("3%", C.lightGrey), dataCell("Paper", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),

    h2("A.6 Signal Stack"),
    twoColTable([
      ["Gate", "VIX + yield curve (DXY secondary). Must pass before any trade."],
      ["Primary signals", "Options flow (call/put + IV rank), Bollinger Band breakout, HVF pattern"],
      ["Confirmation signals", "GEX, VWAP, COT, director cluster buys, senate signal, superinvestor, social mention"],
      ["Risk filter", "ForexFactory economic calendar — no new positions within 30 min of major events"],
      ["Trade fires when", "Gate passes + 2 primaries + 1 confirmation (or HVF alone if R:R >= 2.5:1)"],
      ["Removed as noise", "Standalone RSI/MACD/BBANDS, single congress buy < $50k, generic insider transactions"],
    ]),
    ...spacer(1),

    h2("A.7 Notable Investors Tracked"),
    bullet("Donald Trump — OGE presidential disclosures, Truth Social mentions"),
    bullet("Nancy Pelosi — STOCK Act / Capitol Trades"),
    bullet("Warren Buffett (Berkshire Hathaway) — SEC 13F quarterly filings"),
    bullet("Michael Burry (Scion Asset Management) — SEC 13F quarterly filings"),
    bullet("Ray Dalio (Bridgewater Associates) — SEC 13F quarterly filings"),
    bullet("George Soros (Soros Fund Management) — SEC 13F quarterly filings"),
    bullet("Elon Musk — social mentions on X only (no equity disclosures required)"),
    bullet("Leopold Aschenbrenner (@leopoldasch) — X/Twitter AI infrastructure picks: NBIS (Nebius Group), CRWV (CoreWeave), BE (Bloom Energy), IREN (Iris Energy), APLD (Applied Digital), RIOT (Riot Platforms), CLSK (CleanSpark), BTDR (Bitdeer Technologies), TE (TE Connectivity), SEI (SEI Investments), SNDK (SanDisk)"),
    bullet("@LeopoldATracker — dedicated tracker account amplifying Aschenbrenner picks"),
    bullet("@Asklivermore — AI infrastructure and equity picks"),
    bullet("Jensen Huang (NVIDIA Corp CEO) — public statements on NOW (ServiceNow), PLTR (Palantir), CRWD (CrowdStrike)"),
    bullet("Donald Trump recommendations — IBM (IBM Corp), DELL (Dell Technologies), NOK (Nokia OYJ ADR), NOW (ServiceNow)"),
    ...spacer(1),
    pageBreak(),

    h1("Appendix B: Scheduling Architecture Detail", "appB"),
    para("Full scheduling specification including all cron expressions, workflow files, and the rationale for each decision."),
    ...spacer(1),

    h2("B.1 Confirmed Architecture"),
    para("cron-job.org (external) → GitHub workflow_dispatch → GitHub Actions runner (Ubuntu cloud)"),
    bullet("Step 1: cron-job.org fires at exact UTC time — no queue delays"),
    bullet("Step 2: HTTP POST to GitHub API workflow_dispatch endpoint"),
    bullet("Step 3: GitHub Actions runner starts fresh Ubuntu VM, checks out code, runs Python"),
    bullet("Step 4: Python script loads secrets from env, runs session, posts results to Slack + Supabase"),
    ...spacer(1),

    h2("B.2 Full cron-job.org Schedule"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [3000, 1600, 2360, 2400],
      rows: [
        new TableRow({ children: [hdrCell("Workflow"), hdrCell("Cron (UTC)"), hdrCell("Days"), hdrCell("Notes")] }),
        new TableRow({ children: [dataCell("trading-aus-open.yml", C.lightGrey), dataCell("0 1 * * 1-5", C.lightGrey), dataCell("Mon-Fri", C.lightGrey), dataCell("Once per day (duplicate guard)", C.lightGrey)] }),
        new TableRow({ children: [dataCell("trading-aus-monitor.yml"), dataCell("*/20 0-6 * * 1-5"), dataCell("Mon-Fri"), dataCell("Every 20 min during Asia hours")] }),
        new TableRow({ children: [dataCell("trading-uk-open.yml", C.lightGrey), dataCell("0 8 * * 1-5", C.lightGrey), dataCell("Mon-Fri", C.lightGrey), dataCell("Once per day (duplicate guard)", C.lightGrey)] }),
        new TableRow({ children: [dataCell("trading-uk-monitor.yml"), dataCell("*/15 8-16 * * 1-5"), dataCell("Mon-Fri"), dataCell("Every 15 min — no guard")] }),
        new TableRow({ children: [dataCell("trading-us-open.yml", C.lightGrey), dataCell("30 14 * * 1-5", C.lightGrey), dataCell("Mon-Fri", C.lightGrey), dataCell("Once per day (duplicate guard)", C.lightGrey)] }),
        new TableRow({ children: [dataCell("trading-session-close.yml"), dataCell("0 21 * * 1-5"), dataCell("Mon-Fri"), dataCell("Once per day")] }),
        new TableRow({ children: [dataCell("trading-daily-report.yml", C.lightGrey), dataCell("30 21 * * 1-5", C.lightGrey), dataCell("Mon-Fri", C.lightGrey), dataCell("30 min after close", C.lightGrey)] }),
        new TableRow({ children: [dataCell("trading-weekend-review.yml"), dataCell("0 9 * * 6"), dataCell("Saturday"), dataCell("Once — senator refresh + HVF")] }),
        new TableRow({ children: [dataCell("trading-watchdog.yml", C.lightGrey), dataCell("*/30 0-21 * * 1-5", C.lightGrey), dataCell("Mon-Fri", C.lightGrey), dataCell("GitHub cron only — safety net", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),

    h2("B.3 Duplicate-Run Guard"),
    para("Open sessions (AUS_OPEN, UK_OPEN, US_OPEN) use a duplicate-run guard: check macro_snapshot table for today's date + session name. If a row exists, skip. This prevents double-execution if cron-job.org misfires or a manual dispatch overlaps. Override with FORCE_RUN=true environment variable."),
    para("Monitor sessions (AUS_MONITOR, UK_MONITOR) have NO guard — they must run repeatedly throughout the session."),
    ...spacer(1),
    pageBreak(),

    h1("Appendix C: Supabase Database Schema", "appC"),
    para("Exact table schemas as verified by querying information_schema on 2026-06-01. Use these as source of truth — never assume column names."),
    para("Host: aws-0-eu-west-1.pooler.supabase.com  |  Port: 6543  |  DB: postgres  |  Schema: public"),
    ...spacer(1),

    h2("C.1 user_profiles (3 rows)"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [3000, 2400, 1800, 2160],
      rows: [
        new TableRow({ children: [hdrCell("Column"), hdrCell("Type"), hdrCell("Nullable"), hdrCell("Default")] }),
        ...[ ["id","uuid","NOT NULL","gen_random_uuid()"], ["name","text","NOT NULL",""], ["ig_account_id","text","NOT NULL",""], ["risk_per_trade","numeric","NOT NULL",""], ["max_open_pos","integer","NOT NULL","5"], ["daily_loss_limit","numeric","NOT NULL",""], ["paper_trade","boolean","NOT NULL","false"], ["active","boolean","NOT NULL","true"], ["created_at","timestamptz","","now()"] ]
          .map(([c,t,n,d],i) => new TableRow({ children: [dataCell(c,i%2===0?C.lightGrey:C.white), dataCell(t,i%2===0?C.lightGrey:C.white), dataCell(n,i%2===0?C.lightGrey:C.white), dataCell(d,i%2===0?C.lightGrey:C.white)] })),
      ],
    }),
    ...spacer(1),

    h2("C.2 signal_log (key columns)"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [3000, 2400, 3960],
      rows: [
        new TableRow({ children: [hdrCell("Column"), hdrCell("Type"), hdrCell("Notes")] }),
        ...[ ["session_time","timestamptz","default now()"], ["session","text","AUS_OPEN, UK_OPEN etc"], ["ticker","text","instrument symbol"], ["options_bias","text","BULLISH/BEARISH/NEUTRAL"], ["bb_squeeze","boolean",""], ["bb_breakout_dir","text",""], ["gex_bias","text",""], ["vwap_position","text","ABOVE/BELOW/AT"], ["cot_bias","text",""], ["director_signal","boolean",""], ["senate_signal","boolean",""], ["senate_senator","text","senator name if fired"], ["notable_investor","text","investor name if fired"], ["social_mention","text","source if found"], ["trade_triggered","boolean","default false"], ["primary_count","integer",""], ["confirmation_count","integer",""], ["direction","text","BUY/SELL"] ]
          .map(([c,t,n],i) => new TableRow({ children: [dataCell(c,i%2===0?C.lightGrey:C.white), dataCell(t,i%2===0?C.lightGrey:C.white), dataCell(n,i%2===0?C.lightGrey:C.white)] })),
      ],
    }),
    ...spacer(1),

    h2("C.3 positions"),
    twoColTable([
      ["id, user_id", "UUID primary key, FK to user_profiles"],
      ["epic, ticker", "IG epic code and our internal ticker symbol"],
      ["direction", "BUY or SELL"],
      ["size, open_price, stop_loss, take_profit", "Trade parameters"],
      ["atr_multiplier", "ATR multiplier used at trade open"],
      ["deal_id", "IG deal reference — used to close/modify the position"],
      ["paper_trade", "true = no real IG order was placed"],
      ["session, signal_summary", "Which session opened it, what signals fired"],
    ]),
    ...spacer(1),
    pageBreak(),

    h1("Appendix D: Reference URLs & Data Sources", "appD"),
    para("All external URLs and APIs researched and used during system development."),
    ...spacer(1),

    h2("D.1 Data Source APIs"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [3400, 5960],
      rows: [
        new TableRow({ children: [hdrCell("Source / URL"), hdrCell("Notes")] }),
        new TableRow({ children: [dataCell("Yahoo Finance (yfinance pip)", C.lightGrey), dataCell("Price data, options chains, GEX, VWAP, IV rank. No API key required. Free.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("fred.stlouisfed.org/docs/api"), dataCell("FRED API — yield curve, real yields, TIPS. Free API key. Key series: T10Y2Y, DGS10, DGS2, DFII10, T5YIE, T5YIFR.")] }),
        new TableRow({ children: [dataCell("publicreporting.cftc.gov", C.lightGrey), dataCell("CFTC COT data — commercial net positions. Fully free REST API. No key required.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("efts.sec.gov / EDGAR"), dataCell("Form 4 (director buys), Schedule 13D (activist), 13F (institutions). Free.")] }),
        new TableRow({ children: [dataCell("capitoltrades.com", C.lightGrey), dataCell("US Senate STOCK Act disclosures. Free tier — used as fallback to Quiver Quant.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("api.quiverquant.com"), dataCell("Congressional trading + senator scoring. Free tier adequate for weekly batch. Bearer token auth.")] }),
        new TableRow({ children: [dataCell("api.eia.gov/v2", C.lightGrey), dataCell("US Energy Information Administration — oil inventory (PET.WCRSTUS1.W). Free API key.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("forexfactory.com/calendar"), dataCell("Economic calendar. Scraped — no official API. Used for high-impact event blocking.")] }),
        new TableRow({ children: [dataCell("dataroma.com", C.lightGrey), dataCell("Superinvestor 13F holdings. 81 investors tracked. Free. Scraped weekly.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("labs.ig.com/rest-trading-api-reference"), dataCell("IG official REST API reference. Requires IG account login — blocked to WebFetch. python-igmarkets library wraps this.")] }),
      ],
    }),
    ...spacer(1),

    h2("D.2 Key FRED Series IDs"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2400, 4560, 2400],
      rows: [
        new TableRow({ children: [hdrCell("Series ID"), hdrCell("Data"), hdrCell("Used For")] }),
        ...[ ["T10Y2Y","10yr minus 2yr Treasury spread","Yield curve slope — recession signal"], ["DGS10","10yr Treasury yield","Rate direction signal"], ["DGS2","2yr Treasury yield","Short-end rate signal"], ["DFII10","10yr TIPS yield (real yield)","Real yield signal for commodities"], ["T5YIE","5yr inflation breakeven","Near-term inflation expectation"], ["T5YIFR","5yr/5yr forward breakeven","Structural inflation view (central bank proxy)"] ]
          .map(([s,d,u],i) => new TableRow({ children: [dataCell(s,i%2===0?C.lightGrey:C.white), dataCell(d,i%2===0?C.lightGrey:C.white), dataCell(u,i%2===0?C.lightGrey:C.white)] })),
      ],
    }),
    ...spacer(1),
    pageBreak(),

    h1("Appendix E: Code Standards", "appE"),
    para("All Python scripts and cmd/bat files in this project must follow these standards. They apply whenever creating or modifying any file."),
    ...spacer(1),

    h2("E.1 File Header Block"),
    para("Every script must open with this header:"),
    codeBlock([
      ["# =============================================================================", false],
      ["# File:         filename.py", false],
      ["# Author:       Alex Hind", false],
      ["# Created:      YYYY-MM-DD", false],
      ["#", false],
      ["# Description:", false],
      ["# -----------------------------------------------------------------------------", false],
      ["# What this script does and why it exists.", false],
      ["#", false],
      ["# Usage:", false],
      ["#   python filename.py [args]", false],
      ["#", false],
      ["# Environment Variables Required:", false],
      ["#   VAR_NAME   What it is used for", false],
      ["#", false],
      ["# Version History:", false],
      ["# -----------------------------------------------------------------------------", false],
      ["# 1.0.0   YYYY-MM-DD  Alex Hind   Initial build.", false],
      ["# =============================================================================", false],
    ]),
    ...spacer(1),

    h2("E.2 Version History — Update on Every Change"),
    para("Add a new entry every time a file is modified. Format: Major.Minor.Patch"),
    bullet("Major (1.x.x → 2.x.x): breaking change to interface or behaviour"),
    bullet("Minor (1.0.x → 1.1.x): new feature or meaningful logic change"),
    bullet("Patch (1.0.0 → 1.0.1): bug fix, threshold tweak, label update"),
    ...spacer(1),

    h2("E.3 Section Breaks & Legibility"),
    bullet("Separate logical sections with # === or # --- banner comments"),
    bullet("Blank line before and after every function/class definition"),
    bullet("Inline comments on non-obvious logic — especially signal thresholds and magic numbers"),
    bullet("Import blocks grouped: stdlib → third-party → local, separated by blank lines"),
    ...spacer(1),
    pageBreak(),

    h1("Appendix F: Error Alerting Principles", "appF"),
    noteBox("Any system error — DB write failures, scan exceptions, zero-result sessions, schema mismatches — must be surfaced to Slack (#claude-trading-alerts) immediately. Silent logging to console/Actions log only is not acceptable."),
    ...spacer(1),
    para("Why this principle exists: The signal_log INSERT was silently failing for an entire US_OPEN session (missing columns). The macro gate passed, the session appeared to run, but no signal data was written. This was only discovered by manually querying the DB."),
    ...spacer(1),
    twoColTable([
      ["Rule 1", "Any exception in a data-writing path must fire notify.alert_system_error() — not just log.warning()"],
      ["Rule 2", "Aggregate per-session rather than per-ticker to avoid spam — one alert per session, not one per instrument"],
      ["Rule 3", "Include the first error message and affected ticker count in the alert detail"],
      ["Rule 4", "Pattern: flag failures on returned dict, aggregate in session loop, fire once at end"],
      ["Rule 5", "When adding new DB writes or external API calls, always include a Slack alert path for failures"],
    ]),
    ...spacer(1),
    pageBreak(),

    h1("Appendix G: Naming Convention", "appG"),
    para("Every time a GitHub Actions workflow is modified, update the name: field to include the date so it is immediately obvious in the Actions UI that the new version is running."),
    ...spacer(1),
    twoColTable([
      ["Format", "Name (YYYY-MM-DD)"],
      ["Example before", "name: UK Session Open"],
      ["Example after", "name: UK Session Open (2026-06-05)"],
      ["Same-day revision", "name: UK Session Open (2026-06-05b)"],
      ["Why", "Eliminates confusion about whether GitHub ran old or new code — the date is visible in the Actions run history"],
    ]),
    ...spacer(1),
    pageBreak(),

    h1("Appendix H: MCP Reference", "appH"),
    para("Model Context Protocol (MCP) is an open-source standard created by Anthropic. Think of it like a USB-C port for AI: it standardises how AI assistants securely connect to local files, databases, and external apps so they can fetch data and execute tasks without custom integrations."),
    ...spacer(1),

    h2("H.1 MCP Connectors in Use"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 3960, 2600],
      rows: [
        new TableRow({ children: [hdrCell("Connector"), hdrCell("Purpose"), hdrCell("Status")] }),
        new TableRow({ children: [dataCell("Supabase MCP", C.lightGrey), dataCell("SQL query/execute, table management, project URLs, logs", C.lightGrey), dataCell("Active — connected", C.lightGrey)] }),
        new TableRow({ children: [dataCell("GitHub MCP"), dataCell("Repository management, issue tracking, PR creation"), dataCell("Active — connected")] }),
        new TableRow({ children: [dataCell("Slack MCP", C.lightGrey), dataCell("Send messages, read channels, search", C.lightGrey), dataCell("Active — connected", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Claude in Chrome"), dataCell("DOM-aware browser control for web apps"), dataCell("Active — extension installed")] }),
        new TableRow({ children: [dataCell("Scheduled Tasks MCP", C.lightGrey), dataCell("Create/update/list Cloud Routine scheduled tasks", C.lightGrey), dataCell("Active", C.lightGrey)] }),
        new TableRow({ children: [dataCell("Unusual Whales MCP"), dataCell("Options flow, GEX, congress trades, IV rank"), dataCell("Parked — needs API token")] }),
        new TableRow({ children: [dataCell("TradingView MCP", C.lightGrey), dataCell("Live chart data via Chrome DevTools Protocol", C.lightGrey), dataCell("Parked — requires local Desktop app", C.lightGrey)] }),
      ],
    }),
    ...spacer(1),

    h2("H.2 Unusual Whales MCP — Setup When Ready"),
    bullet("MCP server URL: https://unusualwhales.com/public-api/mcp"),
    bullet("Requires Unusual Whales API token (~$250/mo for full historical data)"),
    bullet("Tools available: options flow alerts, dark pool prints, GEX by strike, IV rank, technicals, congress trades, news"),
    bullet("Add to Claude Code: Settings → MCP Servers → Add server → paste URL + Bearer token"),
    para("Note: Unusual Whales would replace the Yahoo Finance options chain scraping with higher-quality real-time institutional flow data. It is the highest-value upgrade available to the signal stack."),
  ];
}

// =============================================================================
// 11. Key Files Reference
// =============================================================================

function buildFileReference() {
  return [
    h1("12. Key Files Reference", "s12"),

    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [2800, 6560],
      rows: [
        new TableRow({ children: [hdrCell("File"), hdrCell("Purpose")] }),
        new TableRow({ children: [dataCell("config.py", C.lightGrey), dataCell("Central configuration — all instrument maps, ATR multipliers, risk constants, session lists.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("signals.py"), dataCell("Signal computation — queries all external data sources, returns structured signal dict per instrument.")] }),
        new TableRow({ children: [dataCell("ig_shim.py", C.lightGrey), dataCell("IG UK execution layer — session auth, open/close trades, trailing stops, epic lookup, P&L logging.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("notify.py"), dataCell("Slack notification module — Block Kit messages to all 4 channels.")] }),
        new TableRow({ children: [dataCell("price_action.py", C.lightGrey), dataCell("Price action analysis — HVF pattern detection (multi-timeframe), Bollinger Bands, trend structure.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("run_session.py"), dataCell("Session entry point — routes session name to handler, duplicate-run guard, user profile loading.")] }),
        new TableRow({ children: [dataCell("run_hvf_report.py", C.lightGrey), dataCell("Daily HVF scanner — scans FTSE 100, FTSE 250, S&P 500 constituents, posts to Slack.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("run_diagnostics.py"), dataCell("Post-deployment verification — tests all signal layers and posts ✅/❌ report to Slack.")] }),
        new TableRow({ children: [dataCell("run_schema.py", C.lightGrey), dataCell("Idempotent schema migration — creates/updates all Supabase tables.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("run_daily_report.py"), dataCell("End-of-day executive report — all trades, P&L, macro summary, tomorrow's calendar.")] }),
        new TableRow({ children: [dataCell("run_session_watchdog.py", C.lightGrey), dataCell("Watchdog — checks sessions ran and produced signals within expected windows.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("intraday_signals.py"), dataCell("Intraday signal layer — volume analysis, sector alignment, ORB detection.")] }),
        new TableRow({ children: [dataCell("commodity_macro.py", C.lightGrey), dataCell("Commodity macro analysis — DXY, real yields, breakevens, PMI proxy.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("analyst_signals.py"), dataCell("Analyst consensus — mean price target, upside %, recent upgrades/downgrades.")] }),
        new TableRow({ children: [dataCell("cot_analysis.py", C.lightGrey), dataCell("COT (Commitment of Traders) signal computation from CFTC API.", C.lightGrey)] }),
        new TableRow({ children: [dataCell("social_monitor.py"), dataCell("Social mention monitor — Trump (Truth Social), Musk and investor mentions (X/Twitter).")] }),
      ],
    }),
    ...spacer(2),
    pageBreak(),
  ];
}

// =============================================================================
// 12. Version History
// =============================================================================

function buildVersionHistory() {
  return [
    h1("13. Document Version History", "s13"),
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: [1200, 2200, 3560, 2400],
      rows: [
        new TableRow({ children: [hdrCell("Version"), hdrCell("Date"), hdrCell("Changes"), hdrCell("Author")] }),
        new TableRow({ children: [dataCell("1.0", C.lightGrey), dataCell("5 June 2026", C.lightGrey), dataCell("Initial document — covers full system as built.", C.lightGrey), dataCell("Alex Hind", C.lightGrey)] }),
        new TableRow({ children: [dataCell("1.1"), dataCell("5 June 2026"), dataCell("Expanded deployment section with per-service deep-dive (Claude, GitHub, Slack, Quiver Quant, Supabase, cron-job.org). Added Appendices A–H with full memory file content."), dataCell("Alex Hind")] }),
        new TableRow({ children: [dataCell("1.2", C.lightGrey), dataCell("5 June 2026", C.lightGrey), dataCell("Added Section 2 — AI, LLM & MVP background for new readers. Added visual diagrams (architecture flow, signal funnel, session timeline, HVF pattern, process step flow). All instrument tickers updated to include full names throughout.", C.lightGrey), dataCell("Alex Hind", C.lightGrey)] }),
        new TableRow({ children: [dataCell("1.3"), dataCell("5 June 2026"), dataCell("Added 5th Slack channel (#claude-trading-daily / SLACK_DAILY) throughout. Added Section 2.9 — alternative AI models comparison. Added Section 2.10 — full connectors table (used, considered, future). HVF_MIN_RR now sourced from config.py globally."), dataCell("Alex Hind")] }),
      ],
    }),
  ];
}

// =============================================================================
// Document Assembly
// =============================================================================

const doc = new Document({
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{
        level: 0, format: LevelFormat.BULLET, text: "•",
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } },
      }, {
        level: 1, format: LevelFormat.BULLET, text: "◦",
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 1080, hanging: 360 } } },
      }],
    }],
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: C.navy },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: C.blue },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Arial", color: C.midGrey },
        paragraph: { spacing: { before: 180, after: 80 }, outlineLevel: 2 },
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.blue, space: 4 } },
            spacing: { after: 120 },
            children: [
              new TextRun({ text: "EndToEndTrading — System Design & Operations Reference", font: "Arial", size: 18, color: C.midGrey }),
              new TextRun({ text: "\t\tCONFIDENTIAL", font: "Arial", size: 18, color: C.red, bold: true }),
            ],
            tabStops: [{ type: "right", position: 9026 }],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.blue, space: 4 } },
            spacing: { before: 120 },
            children: [
              new TextRun({ text: "Version 1.3 | 5 June 2026", font: "Arial", size: 18, color: C.midGrey }),
              new TextRun({ text: "\t\tPage ", font: "Arial", size: 18, color: C.midGrey }),
              new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: C.midGrey }),
            ],
            tabStops: [{ type: "right", position: 9026 }],
          })],
        }),
      },
      children: [
        ...buildCover(),
        ...buildTOC(),
        ...buildOverview(),
        ...buildBackground(),
        ...buildDesignDecisions(),
        ...buildTooling(),
        ...buildProcessFlow(),
        ...buildScheduling(),
        ...buildInstruments(),
        ...buildConfiguration(),
        ...buildMessaging(),
        ...buildSecurity(),
        ...buildDeployment(),
        ...buildFileReference(),
        ...buildVersionHistory(),
        ...buildAppendices(),
      ],
    },
  ],
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = "C:\\Users\\eahin\\OneDrive\\ClaudeCode\\EndToEndTrading\\EndToEndTrading_SystemDoc.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("✅ Document written to: " + outPath);
}).catch(err => {
  console.error("❌ Error:", err);
  process.exit(1);
});
