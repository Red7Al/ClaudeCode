const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, LevelFormat, PageBreak
} = require('docx');
const fs = require('fs');

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const headerShading = { fill: "1F3864", type: ShadingType.CLEAR };
const altShading = { fill: "EBF0F7", type: ShadingType.CLEAR };
const labelShading = { fill: "E8EDF4", type: ShadingType.CLEAR };

function heading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text, font: "Arial", size: 32, bold: true, color: "1F3864" })],
    spacing: { before: 360, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "1F3864", space: 1 } }
  });
}

function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text, font: "Arial", size: 26, bold: true, color: "2E5FA3" })],
    spacing: { before: 240, after: 80 }
  });
}

function para(text, options = {}) {
  return new Paragraph({
    children: [new TextRun({ text, font: "Arial", size: 22, ...options })],
    spacing: { before: 60, after: 60 }
  });
}

function paraItalic(text) {
  return new Paragraph({
    children: [new TextRun({ text, font: "Arial", size: 22, italics: true, color: "444444" })],
    spacing: { before: 60, after: 60 },
    indent: { left: 720 }
  });
}

function bullet(text, bold = false) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 22, bold })],
    spacing: { before: 40, after: 40 }
  });
}

function numbered(text) {
  return new Paragraph({
    numbering: { reference: "numbers", level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 22 })],
    spacing: { before: 40, after: 40 }
  });
}

function spacer() {
  return new Paragraph({ children: [new TextRun("")], spacing: { before: 60, after: 60 } });
}

function makeTable(headers, rows, colWidths) {
  const total = colWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: headers.map((h, i) => new TableCell({
          borders, shading: headerShading,
          width: { size: colWidths[i], type: WidthType.DXA },
          verticalAlign: VerticalAlign.CENTER,
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({
            children: [new TextRun({ text: h, font: "Arial", size: 20, bold: true, color: "FFFFFF" })]
          })]
        }))
      }),
      ...rows.map((row, ri) => new TableRow({
        children: row.map((cell, ci) => new TableCell({
          borders,
          shading: ri % 2 === 1 ? altShading : { fill: "FFFFFF", type: ShadingType.CLEAR },
          width: { size: colWidths[ci], type: WidthType.DXA },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({
            children: [new TextRun({ text: cell, font: "Arial", size: 20 })]
          })]
        }))
      }))
    ]
  });
}

function metaTable(rows) {
  return new Table({
    width: { size: 9026, type: WidthType.DXA },
    columnWidths: [2200, 6826],
    rows: rows.map((row, ri) => new TableRow({
      children: [
        new TableCell({
          borders,
          shading: labelShading,
          width: { size: 2200, type: WidthType.DXA },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({
            children: [new TextRun({ text: row[0], font: "Arial", size: 20, bold: true })]
          })]
        }),
        new TableCell({
          borders,
          shading: { fill: "FFFFFF", type: ShadingType.CLEAR },
          width: { size: 6826, type: WidthType.DXA },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({
            children: [new TextRun({ text: row[1], font: "Arial", size: 20 })]
          })]
        })
      ]
    }))
  });
}

const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }]
      },
      {
        reference: "numbers",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }]
      }
    ]
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: "1F3864" },
        paragraph: { spacing: { before: 360, after: 120 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: "2E5FA3" },
        paragraph: { spacing: { before: 240, after: 80 }, outlineLevel: 1 } }
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          children: [
            new TextRun({ text: "Automated CFD Spread Betting System — Design Document", font: "Arial", size: 18, color: "666666" }),
            new TextRun({ text: "\tConfidential", font: "Arial", size: 18, color: "666666" })
          ],
          tabStops: [{ type: "right", position: 9026 }],
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } }
        })]
      })
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          children: [
            new TextRun({ text: "Page ", font: "Arial", size: 18, color: "666666" }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: "666666" }),
            new TextRun({ text: " of ", font: "Arial", size: 18, color: "666666" }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], font: "Arial", size: 18, color: "666666" }),
            new TextRun({ text: "\tDesign Phase — May 2026", font: "Arial", size: 18, color: "666666" })
          ],
          tabStops: [{ type: "right", position: 9026 }],
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } }
        })]
      })
    },
    children: [

      // ── COVER PAGE ──────────────────────────────────────────────────────────
      spacer(), spacer(), spacer(),
      new Paragraph({
        children: [new TextRun({ text: "Automated CFD Spread Betting System", font: "Arial", size: 56, bold: true, color: "1F3864" })],
        alignment: AlignmentType.CENTER, spacing: { before: 0, after: 120 }
      }),
      new Paragraph({
        children: [new TextRun({ text: "System Design Document", font: "Arial", size: 36, color: "2E5FA3" })],
        alignment: AlignmentType.CENTER, spacing: { before: 0, after: 360 }
      }),
      spacer(),
      metaTable([
        ["Author",      "A. Hind"],
        ["Owner",       "K. Hind"],
        ["Created",     "May 2026"],
        ["Approved by", "E. Hind"],
        ["Status",      "Design Phase — not yet built"],
        ["Version",     "v1.00  Initial design document"],
      ]),
      spacer(), spacer(),
      new Paragraph({
        children: [
          new TextRun({ text: "“Big money is made in big trends. Chasing small moves only gets you small results…", font: "Arial", size: 20, italics: true, color: "555555" }),
        ],
        alignment: AlignmentType.CENTER, spacing: { before: 0, after: 60 }
      }),
      new Paragraph({
        children: [
          new TextRun({ text: "“Let the trend lead and ride it until it proves you wrong.”", font: "Arial", size: 20, italics: true, color: "555555" }),
        ],
        alignment: AlignmentType.CENTER, spacing: { before: 0, after: 60 }
      }),
      new Paragraph({
        children: [new TextRun({ text: "— Dr David Paul", font: "Arial", size: 20, color: "888888" })],
        alignment: AlignmentType.CENTER, spacing: { before: 0, after: 0 }
      }),
      new Paragraph({ children: [new PageBreak()] }),

      // ── 1. EXECUTIVE SUMMARY ────────────────────────────────────────────────
      heading1("1. Executive Summary"),
      spacer(),
      heading2("Mission"),
      para("Design and build a fully automated CFD spread betting system that trades global financial markets 24 hours a day, 5 days a week. The system removes emotional bias from trading decisions by delegating all analysis, instrument selection, and execution to an AI-driven engine backed by multiple independent evidence-based signal sources."),
      spacer(),
      heading2("Core Principles"),
      bullet("Evidence-based decisions — every trade requires multiple independent signals to align before execution"),
      bullet("Minimal human input — the system runs autonomously; human intervention is an exception not the rule"),
      bullet("Maximum transparency — every decision is logged with full rationale for retrospective analysis"),
      bullet("Conservative risk management — position sizing and circuit breakers protect capital at all times"),
      bullet("Emotion-free execution — automated rules remove hesitation, fear, greed, and fatigue"),
      spacer(),
      heading2("Accounts"),
      makeTable(
        ["Account", "Type", "Risk Per Trade", "Daily Loss Limit", "Max Open Positions", "Sessions"],
        [
          ["Owner", "Live", "2%", "3%", "5", "All markets"],
          ["Wife",  "Live", "1%", "2%", "5", "UK + US only"],
          ["Son",   "Paper trade", "2%", "N/A", "5", "All markets"]
        ],
        [1400, 1200, 1400, 1560, 1800, 1666]
      ),
      spacer(),
      heading2("Estimated Monthly Running Cost"),
      para("Approximately $305–320 per month once live, covering data subscriptions, cloud database, and AI API usage."),

      // ── 2. ARCHITECTURE ─────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("2. Architecture Overview"),
      spacer(),
      para("The system is built around three layers: Claude Cloud Routines as the intelligence layer, a lightweight Python shim as the execution relay, and MCP connectors providing all market data."),
      spacer(),
      heading2("Intelligence Layer — Claude Cloud Routines"),
      bullet("Runs on Anthropic-managed cloud infrastructure — no local machine required"),
      bullet("Fully autonomous — no permission prompts, no human approval during a run"),
      bullet("Reads all signal data via MCP connectors, reasons over it, and produces structured JSON trade decisions"),
      bullet("Writes full decision logs (including instruments considered and rejected) to GitHub"),
      bullet("Writes trade records to Supabase database"),
      spacer(),
      heading2("Execution Layer — Python Shim"),
      bullet("Approximately 150–200 lines of Python code with no decision logic"),
      bullet("Reads Claude's JSON decision output"),
      bullet("Runs mechanical circuit breaker checks (spread, daily loss limit, duplicate positions)"),
      bullet("Calls IG UK REST API to place or close orders"),
      bullet("Returns order confirmation back to Claude for logging"),
      para("The shim is a relay only. It does not decide what to trade, when to trade, or how much. All intelligence stays in Claude."),
      spacer(),
      heading2("Data Flow"),
      makeTable(
        ["Step", "Action", "Component"],
        [
          ["1", "Routine fires on schedule", "Claude Cloud Routine"],
          ["2", "Macro regime data fetched", "FRED API + Yahoo Finance"],
          ["3", "Options flow, dark pool, GEX, congress data fetched", "Unusual Whales MCP"],
          ["4", "Open positions and user profiles read", "Supabase MCP"],
          ["5", "Claude reasons over all data and produces trade JSON", "Claude (AI decision engine)"],
          ["6", "Python shim receives JSON, checks circuit breakers", "Python shim"],
          ["7", "Orders placed via IG UK API", "IG UK REST API"],
          ["8", "Trade record written to database", "Supabase MCP"],
          ["9", "Decision log committed to repository", "GitHub MCP"]
        ],
        [600, 4600, 3826]
      ),
      spacer(),
      heading2("Data Sources — MCP Connectors"),
      bullet("Unusual Whales MCP — options flow, dark pool, GEX, IV rank, VWAP, congress trades, director buys, news"),
      bullet("FRED API — VIX, yield curve, treasury yields (free)"),
      bullet("Yahoo Finance — DXY real-time (free)"),
      bullet("Quiverquant API — congress historical excess return vs SPY (~$30/mo)"),
      bullet("Darwinex API — copy trade confirmation layer (free with account)"),
      bullet("IG UK API — live prices, open positions, order execution"),
      spacer(),
      heading2("Storage"),
      bullet("API keys and credentials — Routine environment variables (never stored in files or database)"),
      bullet("Trade log, positions, user profiles, epic lookup — Supabase PostgreSQL"),
      bullet("Decision logs with full rationale and raw signal values — GitHub repository JSON files"),

      // ── 3. BROKER ───────────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("3. Broker"),
      spacer(),
      heading2("IG UK — Selected"),
      bullet("Spread betting account — profits are tax-free in the UK"),
      bullet("Full REST API and WebSocket streaming API"),
      bullet("Multi-account support — separate API credentials per family member"),
      bullet("Demo account available for full API testing before going live"),
      bullet("FCA regulated"),
      spacer(),
      heading2("Plus500 — Eliminated"),
      bullet("No public API — automation is not supported"),
      bullet("Automated trading is prohibited in their Terms of Service"),

      // ── 4. SESSIONS ─────────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("4. Market Sessions — 7 Cloud Routines"),
      spacer(),
      para("Seven Cloud Routines cover all global market sessions. All run on Anthropic’s cloud infrastructure. No local machine is required."),
      spacer(),
      makeTable(
        ["Routine", "Time (UTC)", "Days", "Purpose"],
        [
          ["AUS / Asia Open",            "00:00", "Mon–Fri", "Macro regime check → instrument selection → trade entry"],
          ["AUS / Asia Monitor",         "03:00", "Mon–Fri", "Check open positions, update trailing stops"],
          ["UK Open",                    "08:00", "Mon–Fri", "Close overnight positions → UK instrument selection → entry"],
          ["UK Monitor + US Pre-market", "13:00", "Mon–Fri", "Review UK positions, read US pre-market signals"],
          ["US Open",                    "15:00", "Mon–Fri", "US instrument selection via options flow → trade entry"],
          ["Session Close",              "21:00", "Mon–Fri", "Close all remaining positions, log daily P&L summary"],
          ["Weekend Review",             "09:00", "Saturday",    "Performance analysis, refresh ATR values, strategy review"]
        ],
        [2400, 1200, 1200, 4226]
      ),

      // ── 5. INSTRUMENT SELECTION ─────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("5. Instrument Selection"),
      spacer(),
      heading2("Layer 1 — Static Core Instruments (always evaluated)"),
      makeTable(
        ["Session", "Instruments"],
        [
          ["AUS / Asia", "AUS200, JPN225, HK50, XAUUSD (Gold), AUD/USD, USD/JPY"],
          ["UK",         "UK100, GBP/USD, BP.L, HSBC.L, XAUUSD (Gold)"],
          ["US",         "SPX500, NVDA, META, MSFT, AAPL, XAUUSD (Gold), OIL"]
        ],
        [2000, 7026]
      ),
      spacer(),
      heading2("Layer 2 — Dynamic Discovery via Unusual Whales Screener"),
      bullet("Query top 20 tickers by unusual options activity at session start"),
      bullet("Filter: market cap > $10 billion"),
      bullet("Filter: options volume > 10,000 contracts per day"),
      bullet("Filter: instrument must be tradeable on IG UK"),
      bullet("Filter: no existing open position on this instrument"),
      bullet("Filter: market session must be active"),
      spacer(),
      para("Combined candidate list is typically 5–12 instruments. Maximum 3 trades opened per session."),

      // ── 6. SIGNAL STACK ─────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("6. Signal Stack"),
      spacer(),
      heading2("Gate — Macro Regime (must pass before any trade)"),
      bullet("VIX level and 5-day trend"),
      bullet("Yield curve slope — 10-year minus 2-year Treasury spread"),
      bullet("DXY (US Dollar Index) 5-day trend"),
      bullet("Claude classifies regime as RISK-ON, RISK-OFF, NEUTRAL, or CRISIS"),
      bullet("CRISIS regime (VIX > 35, inverted curve, surging DXY) — all trading halted"),
      spacer(),
      heading2("Primary Signals — at least one required"),
      makeTable(
        ["Signal", "Threshold", "Source"],
        [
          ["Options flow — call/put imbalance", "Minimum 3:1 ratio in trade direction", "Unusual Whales MCP"],
          ["Dark pool institutional prints",      "Large prints confirming same direction", "Unusual Whales MCP"]
        ],
        [3200, 3600, 2226]
      ),
      spacer(),
      heading2("Confirmation Signals — each adds 20% to position size (max 2× base)"),
      makeTable(
        ["Signal", "Condition", "Source"],
        [
          ["GEX by strike",              "Gamma exposure creates price magnet in trade direction", "Unusual Whales MCP"],
          ["VWAP position",              "Price above VWAP for BUY, below for SELL",              "Unusual Whales MCP"],
          ["Director open-market buys",  "Cluster of 2+, > $50,000 each, last 30 days",           "Unusual Whales MCP"],
          ["Congress / Senate buys",     "3+ members or high track-record senator, last 30 days", "Unusual Whales MCP + Quiverquant"]
        ],
        [2400, 4000, 2626]
      ),
      spacer(),
      heading2("Risk Filter — overrides all signals"),
      bullet("Economic calendar — no new positions within 30 minutes of a high-impact event"),
      bullet("Daily loss limit breach — halt all new trades for that account"),
      bullet("Duplicate position — do not open a second position on the same instrument"),

      // ── 7. KNOWLEDGE SOURCES ────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("7. Knowledge Sources"),
      spacer(),
      para("The engine draws on multiple data and intelligence sources. Each is evaluated for its signal quality, latency, and relevance to the current market session."),
      spacer(),
      makeTable(
        ["Source", "Signal Type", "Strength", "Limitation"],
        [
          ["Unusual Whales — Options Flow",    "Directional bias via call/put imbalance",                         "Very High — real-time institutional intent",           "Requires subscription (~$250/mo)"],
          ["Unusual Whales — Dark Pool",       "Large hidden institutional prints preceding public moves",        "Very High — leading indicator",                        "Not always directional without context"],
          ["Unusual Whales — GEX",             "Gamma exposure by strike — price magnet and wall levels",    "High — market maker positioning",                      "Complex to interpret in isolation"],
          ["Unusual Whales — Congress Trades", "Senate and House stock purchases under STOCK Act",               "High — informed insiders, bipartisan buys strongest",  "Up to 45-day disclosure lag"],
          ["Director Transactions",                 "Open-market insider buys — cluster and first-ever buys",    "High — own-money conviction signal",                   "Multiple motivations; not always immediate"],
          ["FRED API — VIX / Yield Curve",     "Macro regime (risk-on vs risk-off)",                             "High — gates all trading decisions",                   "Lagging by nature; daily frequency"],
          ["DXY (Yahoo Finance)",                   "USD strength — commodity and EM instrument bias",            "High — real-time, free",                               "Can reverse quickly on Fed commentary"],
          ["Quiverquant",                           "Congress trade historical excess return vs SPY",                  "Medium-High — identifies best-performing senators",    "~$30/mo; supplements Unusual Whales"],
          ["Darwinex API",                          "Top Darwin instrument alignment — copy trade confirmation",  "Medium — proven track records",                        "Free; CFD only, not spread bet"],
          ["COT Reports (CFTC)",                    "Commercial vs speculative futures positioning",                   "Medium — long-term sentiment and extremes",            "Weekly frequency; not for short-term timing"],
          ["FCA Short Positions",                   "Disclosed bearish institutional positioning in UK equities",     "Medium — squeeze or continuation signal",              "Timing uncertain; crowded shorts can persist"],
          ["Broker Recommendations",                "Analyst upgrades, downgrades, price target changes",             "Medium — short-term momentum catalyst",                "Can lag price action; herd behaviour risk"],
          ["Economic Calendar",                     "Scheduled high-impact events — used as risk filter only",   "High as a risk filter — avoid 30 min either side",    "Not a directional signal"],
          ["News Headlines",                        "Breaking news and macro developments",                           "Medium — event risk awareness",                        "Noise ratio high; used as filter not signal"],
          ["Investing.com Calendar",                "Consensus forecasts and upcoming volatility events",             "Medium — preparation and scenario planning",           "Generic; supplementary only"]
        ],
        [2200, 2800, 2200, 1826]
      ),

      // ── 8. POSITION SIZING ──────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("8. Position Sizing Formula"),
      spacer(),
      makeTable(
        ["Step", "Formula", "Example (Owner, NVDA)"],
        [
          ["1. Base risk",             "Account equity × risk per trade %",                               "£10,000 × 2% = £200"],
          ["2. Stop distance",         "ATR(14 daily) × instrument ATR multiplier",                       "3.20 pts × 1.5 = 4.80 pts"],
          ["3. Base size",             "Base risk ÷ stop distance",                                       "£200 ÷ 4.80 = £41.67 / pt"],
          ["4. Confirmation mult.",    "1.0 + (0.20 × confirmation count), max 2.0×",               "3 confirmations → 1.6×"],
          ["5. Final size",            "Base size × multiplier",                                         "£41.67 × 1.6 = £66.67 / pt"],
          ["6. Limit distance",        "Stop distance × risk/reward ratio (min 2.0×)",               "4.80 × 2.0 = 9.60 pts"]
        ],
        [2200, 3600, 3226]
      ),
      spacer(),
      heading2("ATR Multipliers by Instrument"),
      makeTable(
        ["Instrument Type", "Examples", "ATR Multiplier"],
        [
          ["Equities", "NVDA, META, MSFT, AAPL",             "1.5×"],
          ["Indices",  "SPX500, UK100, AUS200, JPN225",       "1.5×"],
          ["Gold",     "XAUUSD",                              "1.5×"],
          ["Oil",      "OIL (WTI)",                           "2.0×"],
          ["FX pairs", "GBP/USD, AUD/USD, USD/JPY",           "1.2×"]
        ],
        [2800, 3800, 2426]
      ),

      // ── 9. USER PROFILES ────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("9. User Profiles"),
      spacer(),
      makeTable(
        ["Parameter", "Owner", "Wife", "Son"],
        [
          ["Account type",         "Live spread bet",          "Live spread bet",   "Paper trade only"],
          ["Risk per trade",       "2%",                       "1%",                "2% (simulated)"],
          ["Daily loss limit",     "3% of equity",             "2% of equity",      "N/A"],
          ["Max open positions",   "5",                        "5",                 "5"],
          ["Allowed sessions",     "All (AUS/Asia, UK, US)",   "UK + US only",      "All sessions"],
          ["Confirmation step",    "20% per signal",           "20% per signal",    "20% per signal"],
          ["Max size multiplier",  "2.0×",               "2.0×",         "2.0×"],
          ["Min risk/reward",      "2:1",                      "2:1",               "2:1"]
        ],
        [3000, 2000, 2000, 2026]
      ),

      // ── 10. STORAGE ─────────────────────────────────────────────────────────
      heading1("10. Storage"),
      spacer(),
      makeTable(
        ["Data", "Storage Location", "Rationale"],
        [
          ["API keys, passwords, IG credentials",               "Routine environment variables",     "Never stored in files or database — managed securely by Anthropic"],
          ["Trade log, positions, user profiles, epic lookup",  "Supabase PostgreSQL",               "SQL queries, Grafana-compatible, free tier sufficient initially"],
          ["Decision logs with full rationale and signal data", "GitHub repository (JSON files)",    "Human-readable, version-controlled, permanent audit trail"]
        ],
        [2800, 2400, 3826]
      ),
      spacer(),
      heading2("Epic Lookup (IG Instrument Codes)"),
      para("IG UK uses internal instrument codes called ‘epics’ (e.g. NVDA = UC.D.NVDA.DAILY.IP). These are stored in a Supabase lookup table. If an instrument is not found, the IG market search API is called as a fallback and the new epic is automatically written back to the table. The table self-populates over time."),

      // ── 11. AI TOOLS ────────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("11. Why Claude Was Chosen as the Decision Engine"),
      spacer(),
      para("Several AI tools were evaluated for the decision engine role. The choice of Claude as the primary reasoning engine was made after comparing capabilities against the specific requirements of this system."),
      spacer(),
      makeTable(
        ["Tool", "Strengths", "Limitations", "Role in This System"],
        [
          ["Claude (Anthropic)",
           "Long-context reasoning, reviews entire trading systems in one pass. Clean, safe, readable code. Strong conceptual clarity around exposure, margin, and trade lifecycle. Ideal for multi-factor financial reasoning.",
           "Requires explicit direction for code generation. API latency (1–5 seconds) unsuitable for scalping.",
           "Primary decision engine — macro regime, instrument selection, signal analysis, trade sizing, rationale generation"],
          ["ChatGPT (OpenAI)",
           "Strong at generating structured Python code and designing trading architectures. Effective for debugging and documentation.",
           "Occasional overconfidence in API details. All outputs must be validated.",
           "Development support — code generation and documentation during build phase"],
          ["Perplexity",
           "Best for research-driven tasks — gathering market structure information, checking broker API references, validating assumptions.",
           "Not optimised for code generation. Prioritises sourced answers over implementation.",
           "Research tool during design and build phases"],
          ["GitHub Copilot",
           "Effective within the IDE for completing functions, generating boilerplate, and maintaining consistency across modules.",
           "Less effective at designing systems from first principles or reasoning through complex trading logic.",
           "Development acceleration during build phase"]
        ],
        [1400, 2800, 2200, 2626]
      ),

      // ── 12. SECURITY ────────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("12. Security"),
      spacer(),
      heading2("Credentials and Secrets"),
      bullet("All API keys, broker passwords, and database connection strings stored exclusively in Routine environment variables"),
      bullet("No credentials stored in code, configuration files, GitHub repositories, or databases"),
      bullet("Each family member’s IG credentials stored separately — complete isolation between accounts"),
      bullet("Unusual Whales, Quiverquant, and Supabase API keys stored as separate environment variables per routine"),
      spacer(),
      heading2("Request Validation"),
      bullet("All IG API calls authenticated using session tokens with automatic refresh"),
      bullet("IG session tokens expire after approximately 6 hours — the shim handles refresh automatically"),
      bullet("Supabase access scoped to the specific project only — no cross-project access"),
      bullet("GitHub MCP access limited to the decision log branch — no write access to main branch"),
      spacer(),
      heading2("Retry and Failure Policy"),
      bullet("IG API calls: retry up to 3 times with exponential backoff (1s, 2s, 4s)"),
      bullet("Do not retry validation errors or rejected trades — log and move on"),
      bullet("If IG API is unreachable: log the failure, skip that session, alert via notification channel"),
      bullet("Circuit breaker failures logged in full — never silently suppressed"),
      spacer(),
      heading2("Audit Trail"),
      bullet("Every trade decision logged to GitHub with full signal values and rationale"),
      bullet("Every order placement and outcome logged to Supabase with timestamp and deal reference"),
      bullet("Skipped instruments and rejected trades logged with reason — full session audit at all times"),
      bullet("Daily loss limit breaches logged as events — separate from trade log"),

      // ── 13. MONITORING ──────────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("13. Monitoring and Alerting"),
      spacer(),
      para("Monitoring is deferred to the build phase but the following design is confirmed. Grafana will connect directly to Supabase PostgreSQL for real-time dashboards."),
      spacer(),
      heading2("Grafana Dashboard Panels (planned)"),
      makeTable(
        ["Panel", "Data Source", "Purpose"],
        [
          ["Macro regime indicator",         "Supabase — latest routine run",   "Colour-coded tile: RISK-ON (green), RISK-OFF (amber), CRISIS (red)"],
          ["VIX 30-day sparkline",           "Supabase — decision log",         "VIX trend with 20 and 35 threshold lines"],
          ["Yield curve 90-day chart",       "Supabase — decision log",         "10Y–2Y spread over time"],
          ["DXY 20-day chart",               "Supabase — decision log",         "DXY with 5-day moving average"],
          ["Open positions",                 "Supabase — positions table",      "Live positions per user with P&L, stop level, entry price"],
          ["Daily P&L per account",          "Supabase — trade log",            "Today’s realised + unrealised P&L vs daily loss limit"],
          ["Session P&L history",            "Supabase — trade log",            "Bar chart of net P&L per session over last 30 days"],
          ["Signal effectiveness table",     "Supabase — trade log",            "Win rate grouped by signal combination fired"],
          ["Decision log viewer",            "GitHub JSON files",                    "Human-readable rationale for each session’s decisions"]
        ],
        [2400, 2400, 4226]
      ),
      spacer(),
      heading2("Alerting (planned)"),
      bullet("Daily loss limit breach — immediate notification (email or Telegram)"),
      bullet("CRISIS regime detected — immediate notification with macro summary"),
      bullet("IG API unreachable — immediate notification with session skipped flag"),
      bullet("Trade placed — confirmation notification with instrument, direction, size, and rationale summary"),

      // ── 14. DATA CAPTURED PER TRADE ─────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("14. Data Captured Per Trade"),
      spacer(),
      para("Every trade record captures the full context at the moment of decision to enable rich retrospective analysis."),
      spacer(),
      heading2("At Entry"),
      bullet("Macro regime classification and confidence score"),
      bullet("VIX, yield curve spread, and DXY value"),
      bullet("Call/put ratio and IV rank"),
      bullet("Dark pool direction and print size"),
      bullet("VWAP value and price position relative to VWAP"),
      bullet("GEX assessment"),
      bullet("Director buy count and congress buy count (last 30 days)"),
      bullet("Primary signal count and confirmation signal count"),
      bullet("Position size multiplier applied"),
      bullet("Full plain-English rationale from Claude"),
      spacer(),
      heading2("At Exit"),
      bullet("Exit price, time, and reason (trailing stop, limit hit, session close, circuit breaker, manual)"),
      bullet("Gross P&L, spread cost, and net P&L"),
      bullet("Hold duration in minutes"),
      bullet("Maximum favourable excursion (furthest price moved in our favour)"),
      bullet("Maximum adverse excursion (furthest price moved against us)"),
      spacer(),
      heading2("Analysis This Enables"),
      makeTable(
        ["Question", "How to Answer"],
        [
          ["Which signal combinations have the highest win rate?", "GROUP BY primary signals + confirmations fired"],
          ["Does performance differ between RISK-ON and RISK-OFF?", "GROUP BY macro regime classification"],
          ["Which market session is most profitable?",             "GROUP BY session (US_OPEN, UK_OPEN, etc.)"],
          ["Which instruments produce the best returns?",          "GROUP BY instrument"],
          ["Is our stop distance optimal?",                        "Compare max favourable excursion vs stop distance"],
          ["Which senators’ trades are most predictive?",    "Correlate congress buy entries with net P&L"],
          ["Does director buy signal improve win rate?",           "Compare with and without director_buy_count > 0"]
        ],
        [4200, 4826]
      ),

      // ── 15. MONTHLY COSTS ───────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("15. Monthly Running Costs"),
      spacer(),
      makeTable(
        ["Component", "Monthly Cost", "Notes"],
        [
          ["Unusual Whales MCP",     "~$250",    "Primary signal source — options flow, dark pool, GEX, congress"],
          ["Quiverquant API",        "~$30",     "Congress historical performance data"],
          ["Supabase Pro",           "~£20","Free tier sufficient for development; upgrade when live"],
          ["Claude API usage",       "~$5–20", "7 routines per day across 5 trading days"],
          ["Darwinex API",           "Free",     "Account required but no fee"],
          ["FRED API",               "Free",     "Federal Reserve economic data"],
          ["Yahoo Finance (yfinance)","Free",     "DXY real-time data"],
          ["IG UK account",          "Free",     "Spread is the transaction cost"],
          ["Total",                  "~$305–320/mo", ""]
        ],
        [2800, 1800, 4426]
      ),

      // ── 16. PHASED DELIVERY ─────────────────────────────────────────────────
      new Paragraph({ children: [new PageBreak()] }),
      heading1("16. Phased Delivery Plan"),
      spacer(),
      makeTable(
        ["Phase", "Activity", "Status"],
        [
          ["1-A", "System design and architecture document (this document)", "Complete"],
          ["1-B", "Open IG UK demo account and verify API access",           "To do"],
          ["1-C", "Sign up for Unusual Whales API and test MCP connection",  "To do"],
          ["1-D", "Obtain FRED API key (free, instant)",                     "To do"],
          ["1-E", "Create Supabase project and run database schema SQL",     "To do"],
          ["1-F", "Set up GitHub repository for decision logs",              "To do"],
          ["2-A", "Build Python execution shim for IG UK order placement",   "To do"],
          ["2-B", "Build epic lookup and IG market search fallback",         "To do"],
          ["2-C", "Build circuit breakers (spread, daily loss, duplicate)",  "To do"],
          ["3-A", "Configure US Open Cloud Routine and test on demo",        "To do"],
          ["3-B", "Configure remaining 6 Cloud Routines",                    "To do"],
          ["3-C", "Configure Unusual Whales MCP connector",                  "To do"],
          ["3-D", "Configure Supabase MCP connector",                        "To do"],
          ["4-A", "Paper trade all three accounts — minimum 4 weeks",  "To do"],
          ["4-B", "Weekend review prompt and analysis validation",           "To do"],
          ["5-A", "Grafana dashboard — live P&L and regime panels",    "To do"],
          ["5-B", "Alerting — email or Telegram notifications",         "To do"],
          ["6-A", "Go live — owner account first",                      "To do"],
          ["6-B", "Go live — wife account after 2 weeks validation",    "To do"],
          ["6-C", "Son account — move from paper to live when ready",   "To do"]
        ],
        [800, 5600, 2626]
      ),

      // ── 17. WHAT NOT YET DESIGNED ───────────────────────────────────────────
      heading1("17. What Has Not Been Designed Yet"),
      spacer(),
      makeTable(
        ["Area", "Status"],
        [
          ["Weekend review prompt template",                   "Not yet designed"],
          ["Backtesting approach",                             "Not yet explored"],
          ["Grafana dashboard layout",                         "Deferred — design phase only"],
          ["TradingView local MCP (personal chart analysis)", "Parked — optional future addition"],
          ["Notification channel (email or Telegram)",         "Not yet decided"],
          ["Build phase — actual code",                   "Not started — design phase only"]
        ],
        [4000, 5026]
      ),
      spacer(),
      spacer(),
      new Paragraph({
        children: [new TextRun({ text: "End of Design Document", font: "Arial", size: 20, color: "999999", italics: true })],
        alignment: AlignmentType.CENTER,
        spacing: { before: 480 }
      })
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("TradingSystemDesign.docx", buffer);
  console.log("Document created: TradingSystemDesign.docx");
});
