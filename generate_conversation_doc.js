const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, LevelFormat, PageBreak
} = require('docx');
const fs = require('fs');

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const noBorders = {
  top:    { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  left:   { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  right:  { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
};

function spacer() {
  return new Paragraph({ children: [new TextRun("")], spacing: { before: 40, after: 40 } });
}

function userBubble(lines) {
  const children = [];
  for (const line of lines) {
    children.push(new TableRow({
      children: [new TableCell({
        borders: noBorders,
        shading: { fill: "D6E4F0", type: ShadingType.CLEAR },
        width: { size: 9026, type: WidthType.DXA },
        margins: { top: 60, bottom: 40, left: 160, right: 160 },
        children: [new Paragraph({
          children: [new TextRun({ text: line, font: "Arial", size: 20 })],
          spacing: { before: 30, after: 30 }
        })]
      })]
    }));
  }
  return new Table({
    width: { size: 9026, type: WidthType.DXA },
    columnWidths: [9026],
    borders: noBorders,
    rows: [
      new TableRow({
        children: [new TableCell({
          borders: noBorders,
          shading: { fill: "1F6B9A", type: ShadingType.CLEAR },
          width: { size: 9026, type: WidthType.DXA },
          margins: { top: 60, bottom: 40, left: 160, right: 160 },
          children: [new Paragraph({
            children: [new TextRun({ text: "YOU", font: "Arial", size: 18, bold: true, color: "FFFFFF" })]
          })]
        })]
      }),
      ...children
    ]
  });
}

function claudeLabel() {
  return new Table({
    width: { size: 9026, type: WidthType.DXA },
    columnWidths: [9026],
    borders: noBorders,
    rows: [new TableRow({
      children: [new TableCell({
        borders: noBorders,
        shading: { fill: "2D6A4F", type: ShadingType.CLEAR },
        width: { size: 9026, type: WidthType.DXA },
        margins: { top: 60, bottom: 40, left: 160, right: 160 },
        children: [new Paragraph({
          children: [new TextRun({ text: "CLAUDE", font: "Arial", size: 18, bold: true, color: "FFFFFF" })]
        })]
      })]
    })]
  });
}

function cPara(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, font: "Arial", size: 20, ...opts })],
    spacing: { before: 40, after: 40 },
    indent: { left: 160 }
  });
}

function cBullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 20 })],
    spacing: { before: 30, after: 30 },
    indent: { left: 880, hanging: 360 }
  });
}

function cHeading(text) {
  return new Paragraph({
    children: [new TextRun({ text, font: "Arial", size: 22, bold: true, color: "1F3864" })],
    spacing: { before: 120, after: 40 },
    indent: { left: 160 }
  });
}

function cCode(text) {
  return new Paragraph({
    children: [new TextRun({ text, font: "Courier New", size: 18, color: "333333" })],
    spacing: { before: 20, after: 20 },
    indent: { left: 400 },
    shading: { fill: "F4F4F4", type: ShadingType.CLEAR }
  });
}

function makeTable(headers, rows, colWidths) {
  const total = colWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: colWidths,
    indent: { size: 160, type: WidthType.DXA },
    rows: [
      new TableRow({
        tableHeader: true,
        children: headers.map((h, i) => new TableCell({
          borders,
          shading: { fill: "1F3864", type: ShadingType.CLEAR },
          width: { size: colWidths[i], type: WidthType.DXA },
          margins: { top: 60, bottom: 60, left: 100, right: 100 },
          children: [new Paragraph({
            children: [new TextRun({ text: h, font: "Arial", size: 18, bold: true, color: "FFFFFF" })]
          })]
        }))
      }),
      ...rows.map((row, ri) => new TableRow({
        children: row.map((cell, ci) => new TableCell({
          borders,
          shading: ri % 2 === 1 ? { fill: "EBF0F7", type: ShadingType.CLEAR } : { fill: "FFFFFF", type: ShadingType.CLEAR },
          width: { size: colWidths[ci], type: WidthType.DXA },
          margins: { top: 60, bottom: 60, left: 100, right: 100 },
          children: [new Paragraph({
            children: [new TextRun({ text: cell, font: "Arial", size: 18 })]
          })]
        }))
      }))
    ]
  });
}

const doc = new Document({
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 880, hanging: 360 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 880, hanging: 360 } } } }] }
    ]
  },
  styles: { default: { document: { run: { font: "Arial", size: 20 } } } },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 }
      }
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        children: [
          new TextRun({ text: "Automated CFD Spread Betting System — Design Conversation", font: "Arial", size: 18, color: "666666" }),
          new TextRun({ text: "\t29 May 2026", font: "Arial", size: 18, color: "666666" })
        ],
        tabStops: [{ type: "right", position: 9026 }],
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } }
      })] })
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        children: [
          new TextRun({ text: "Page ", font: "Arial", size: 18, color: "666666" }),
          new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: "666666" }),
          new TextRun({ text: " of ", font: "Arial", size: 18, color: "666666" }),
          new TextRun({ children: [PageNumber.TOTAL_PAGES], font: "Arial", size: 18, color: "666666" })
        ],
        tabStops: [{ type: "right", position: 9026 }],
        border: { top: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } }
      })] })
    },
    children: [

      // TITLE
      new Paragraph({ children: [new TextRun({ text: "Automated CFD Spread Betting System", font: "Arial", size: 48, bold: true, color: "1F3864" })], alignment: AlignmentType.CENTER, spacing: { before: 0, after: 120 } }),
      new Paragraph({ children: [new TextRun({ text: "Full Design Conversation — 29 May 2026", font: "Arial", size: 28, color: "666666" })], alignment: AlignmentType.CENTER, spacing: { before: 0, after: 480 } }),
      new Paragraph({ children: [new PageBreak()] }),

      // ── EXCHANGE 1 ──────────────────────────────────────────────────────────
      userBubble([
        "Please record this mission statement and determine the actions required",
        "",
        "Aim: I am trying to find the most profitable method(s) to trade CFD (spread betting) financial instruments",
        "",
        "Choice of instrument: Available across all global markets (24x5) - Two days off per week for analysis and downtime",
        "",
        "Method: I plan to use automation so that I do not get emotionally involved.",
        "",
        "Tools I have considered (for decisions and execution): TradingView, Claude Code to write Python Engine, Grok to provide a local endpoint",
        "Tools I have considered (for reporting): Grafana for dashboard and analysis, Microsoft PowerBI for analysis",
        "Tools I have considered for data: SQL Server, MySQL, SQLLite, CSV files, JSON files",
        "Tools I have considered for configuration data: SQL Server, MySQL, SQLLite, CSV files, JSON files",
        "Brokers I have considered: IG UK Broker, Plus500",
        "",
        "I can see multiple methods to trade CFD e.g. XAU, ZAG, MSFT, META e.g.",
        "1. TradingView alerts to a Python engine to Broker (via nGrok local endpoint)",
        "2. TradingView price information to Claude Engine to Broker",
        "3. Data sources e.g. US Senate equity purchases (web page) to Claude Engine to Broker",
        "",
        "Functionality to be incorporated in engine and/or strategy:",
        "1. Circuit breakers e.g. if spread is too big, do not trade",
        "2. Calculate position size",
        "3. Calculate stop loss (trailing) using timeframe and ATR and instrument type",
        "",
        "I am assuming that for each trading period, Claude can dynamically:",
        "1. Choose instruments e.g. META, NVDA that are most likely to be profitable",
        "2. Choose a trading strategy most suited to the instrument and time frame",
        "3. Provide explanation of the above choices",
        "",
        "Other considerations: Financial costs, Bull and Bear markets, GitHub trading strategies, backtesting, location of processing, control of application",
        "",
        "I am curious to know if I can use TradingView price data to supply a Claude Engine with information so Claude engine will choose instruments and strategy for the period",
        "",
        "Finally, please can you tell me other automated ways to trade that I have not mentioned."
      ]),
      spacer(),
      claudeLabel(),
      cHeading("Correcting Your Assumptions First"),
      cPara("Yes — Claude can dynamically choose instruments and strategies, but with important caveats:"),
      cBullet("Claude has no real-time market data unless you feed it. It must receive OHLCV, news, and technicals via an external feed."),
      cBullet("Claude can reason over data you supply and return a structured decision. This is viable."),
      cBullet("Claude is not deterministic — the same inputs can produce different outputs. Guardrails and audit logs are essential."),
      cBullet("Claude has latency (1–5 seconds per API call) — unsuitable for scalping but well-suited to session-based decisions."),
      cBullet("Claude has no memory between calls — each decision call needs relevant history passed explicitly."),
      cPara("Bottom line: Your vision is architecturally sound for medium-frequency trading. It is NOT suitable for tick-by-tick execution.", { italics: true }),
      spacer(),
      cHeading("Recommended Tool Decisions"),
      makeTable(
        ["Component", "Recommendation", "Reason"],
        [
          ["Broker", "IG UK", "Full REST + WebSocket API. Plus500 has no public API — eliminated."],
          ["Data storage", "Supabase (PostgreSQL) + GitHub JSON", "Cloud-accessible from Cloud Routines. SQLite/local DB unreachable from cloud."],
          ["Reporting", "Grafana (live) + Power BI (weekly)", "Grafana connects natively to PostgreSQL."],
          ["Secrets", "Routine environment variables", "Never in files or databases."],
          ["Local LLM (Grok)", "Not recommended initially", "Use Claude API — stronger at multi-factor financial reasoning."],
          ["TradingView", "Not required", "All signals covered by Unusual Whales MCP."]
        ],
        [1400, 2000, 5226]
      ),
      spacer(),
      cHeading("Other Automated Trading Methods Not Mentioned"),
      cBullet("Systematic mean reversion — statistical pairs trading (e.g. MSFT vs GOOGL cointegration)"),
      cBullet("Options flow / dark pool signal following — unusualwhales.com publishes unusual options activity via API"),
      cBullet("Order book imbalance — reading Level 2 data to detect institutional accumulation"),
      cBullet("Earnings drift (PEAD) — buy/sell equities after earnings surprises, hold 5–20 days"),
      cBullet("Macro regime detection — use VIX, yield curve, DXY to select instrument class"),
      cBullet("Reddit/X sentiment scraping — retail sentiment as contrarian or momentum signal"),
      cBullet("ETF flow analysis — track institutional ETF inflows/outflows"),
      cBullet("QuantConnect LEAN engine — free open-source backtesting with live execution support"),
      spacer(), spacer(),

      // ── EXCHANGE 2 ──────────────────────────────────────────────────────────
      userBubble(["I would rather most of the processing and scheduling is done within Claude. e.g. look at https://unusualwhales.com/stock-screener and choose some equities to BUY/SELL based upon CALL, PUT, VOLUME, VOLATILITY etc"]),
      spacer(),
      claudeLabel(),
      cPara("Unusual Whales has a native MCP Server — Claude can connect directly to their data. No scraping, no middleware, no Python parsing layer."),
      spacer(),
      cHeading("Revised Architecture: Claude-Centric"),
      makeTable(
        ["Routine", "Action"],
        [
          ["Market open", "Query Unusual Whales MCP → top options flow → macro regime → pick instruments → calculate size → call IG API → log to Supabase"],
          ["Every hour",  "Check open positions vs trailing stop → check circuit breakers → close/adjust if needed"]
        ],
        [2000, 6626]
      ),
      spacer(),
      cHeading("What Each Data Source Tells Claude"),
      makeTable(
        ["Unusual Whales Endpoint", "Trading Signal"],
        [
          ["flow_alerts (calls vs puts)",      "Directional bias — heavy call buying = bullish"],
          ["iv_rank",                           "Is options premium cheap or expensive — affects strategy choice"],
          ["perc_30_day_total",                 "Which tickers have highest unusual activity relative to normal"],
          ["greek_exposure (GEX)",              "Where price is likely to pin or repel — market maker positioning"],
          ["congress_trades",                   "Smart money / insider-adjacent long-term bias"],
          ["short_screener",                    "High short interest = squeeze candidate or continued weakness"]
        ],
        [3200, 5426]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 3 ──────────────────────────────────────────────────────────
      userBubble(["Happy for Claude to do Macro regime detection — use VIX level, yield curve slope, and DXY trend to select which instrument class to trade (risk-on vs. risk-off). Claude is well-suited to this."]),
      spacer(),
      claudeLabel(),
      cHeading("The Four Regime Types Claude Will Identify"),
      cCode("VIX Low (<20) + Curve Positive + DXY Weak/Neutral  →  RISK-ON"),
      cCode("  Best instruments: Growth equities (NVDA, META, MSFT), Oil, EM"),
      cCode("VIX Low (<20) + Curve Positive + DXY Strong        →  RISK-ON (USD-flavoured)"),
      cCode("  Best instruments: US equities"),
      cCode("VIX High (>25) + Curve Flat/Inverted + DXY Strong  →  RISK-OFF"),
      cCode("  Best instruments: Gold (XAU), short equities"),
      cCode("VIX Spiking (>35) + DXY surging                    →  CRISIS / STAND-ASIDE"),
      cCode("  Circuit breaker fires — no new positions"),
      spacer(),
      cHeading("Data Sources for Macro"),
      makeTable(
        ["Signal", "Source", "Series/Ticker", "Cost"],
        [
          ["VIX",          "FRED API",      "VIXCLS",    "Free"],
          ["Yield curve",  "FRED API",      "T10Y2Y",    "Free"],
          ["10yr yield",   "FRED API",      "DGS10",     "Free"],
          ["DXY",          "Yahoo Finance", "DX-Y.NYB",  "Free"]
        ],
        [2000, 2000, 2000, 2626]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 4 ──────────────────────────────────────────────────────────
      userBubble(["For the solution, is it possible to make it multi user so I can split the transactions between me, wife and son all with separate accounts?"]),
      spacer(),
      claudeLabel(),
      cPara("Yes — and it is a clean design decision to make now rather than retrofit later."),
      cHeading("Recommendation: Single Engine, Per-User Strategy Profiles"),
      makeTable(
        ["Parameter", "Owner", "Wife", "Son"],
        [
          ["max_positions",  "5",       "5",       "5"],
          ["risk_per_trade", "2%",      "1%",      "2%"],
          ["paper_trade",    "false",   "false",   "true"],
          ["sessions",       "All",     "UK + US", "All"]
        ],
        [2400, 2200, 2200, 1826]
      ),
      spacer(),
      cHeading("UK Tax / Regulatory Note"),
      cPara("Each person must hold their own IG spread betting account in their own name. You cannot legally trade on behalf of another person without FCA authorisation. The engine executes trades using each person's own credentials — this keeps you on the right side of FCA rules."),
      spacer(), spacer(),

      // ── EXCHANGE 5 ──────────────────────────────────────────────────────────
      userBubble(["yes (to explore scheduling)"]),
      spacer(),
      claudeLabel(),
      cHeading("Claude Scheduling Options — Three Types"),
      makeTable(
        ["Option", "Runs On", "Machine Required", "Min Interval", "Best For"],
        [
          ["/loop (in-session)",        "Your machine",    "Yes + open session", "1 minute",  "Development / live watching"],
          ["Desktop Scheduled Tasks",   "Your machine",    "Yes",                "1 minute",  "Local automation"],
          ["Cloud Routines",            "Anthropic cloud", "No",                 "1 hour",    "Unattended autonomous trading"]
        ],
        [2000, 1800, 1800, 1400, 1626]
      ),
      spacer(),
      cHeading("Critical Finding: Cloud Routines"),
      cBullet("Runs on Anthropic-managed cloud infrastructure — no local machine required"),
      cBullet("Fully autonomous — no permission prompts during a run"),
      cBullet("Supports MCP connectors (Unusual Whales, Supabase, GitHub)"),
      cBullet("Can be triggered by schedule, API call, or GitHub event"),
      cBullet("Currently in research preview"),
      spacer(), spacer(),

      // ── EXCHANGE 6 ──────────────────────────────────────────────────────────
      userBubble(["please only use claude scheduler"]),
      spacer(),
      claudeLabel(),
      cPara("Noted and saved. All scheduling will use Claude Cloud Routines only."),
      makeTable(
        ["Routine", "Trigger Time (UTC)", "Days", "Purpose"],
        [
          ["AUS/Asia Open",            "00:00", "Mon–Fri", "Macro check → AUS/Asia instrument selection → trade entry"],
          ["AUS/Asia Monitor",         "03:00", "Mon–Fri", "Check open positions, trailing stops"],
          ["UK Open",                  "08:00", "Mon–Fri", "Close overnight → UK instrument selection → entry"],
          ["UK Monitor + US Pre-mkt",  "13:00", "Mon–Fri", "UK positions + read US pre-market signals"],
          ["US Open",                  "15:00", "Mon–Fri", "US instrument selection via options flow → entry"],
          ["Session Close",            "21:00", "Mon–Fri", "Close all positions, log daily P&L"],
          ["Weekend Review",           "09:00", "Saturday",    "Performance analysis, refresh ATR values"]
        ],
        [2400, 1400, 1000, 3826]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 7 ──────────────────────────────────────────────────────────
      userBubble(["yes (to explore data storage)"]),
      spacer(),
      claudeLabel(),
      cHeading("Critical Storage Constraint"),
      cPara("Cloud Routines run on Anthropic-managed infrastructure with a fresh environment on every run. This means: no access to your local machine, no local databases, no local files. Storage must be cloud-hosted and network-accessible."),
      cPara("This eliminates: local SQLite, local SQL Server, local MySQL, CSV files, local JSON files.", { bold: true }),
      spacer(),
      makeTable(
        ["Data", "Storage", "Reason"],
        [
          ["API keys and credentials",                         "Routine environment variables",  "Secure, never in files or DB"],
          ["Trade log, positions, user profiles, epic lookup", "Supabase (PostgreSQL)",           "Free tier, SQL queries, Grafana-compatible"],
          ["Decision logs with rationale and signal values",   "GitHub repo (JSON files)",        "Human-readable, version-controlled, free"]
        ],
        [2800, 2400, 3426]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 8 ──────────────────────────────────────────────────────────
      userBubble(["For the initial load - option 1 and then if instrument is not found use Option 2 (re: epic lookup)"]),
      spacer(),
      claudeLabel(),
      cPara("Saved. The epic lookup flow:"),
      cCode("Claude decides to trade NVDA"),
      cCode("  → Query Supabase: SELECT epic FROM instrument_epics WHERE name = 'NVDA'"),
      cCode("  → Found? Use epic from table → Place trade"),
      cCode("  → Not found? Call IG: GET /markets?searchTerm=NVDA"),
      cCode("  → Parse response → extract epic"),
      cCode("  → Write back to Supabase (self-populating cache)"),
      cCode("  → Use epic → Place trade"),
      spacer(), spacer(),

      // ── EXCHANGE 9 ──────────────────────────────────────────────────────────
      userBubble(["In fact, with the schedule - we will be trading US, UK, ASIA, AUS markets so each may need their own schedule"]),
      spacer(),
      claudeLabel(),
      cHeading("Market Hours (UTC)"),
      makeTable(
        ["Market", "Open (UTC)", "Close (UTC)", "Key Instruments"],
        [
          ["Australia (ASX)",  "00:00", "06:00", "AUS200, AUD pairs, mining stocks"],
          ["Asia (Tokyo)",     "00:00", "06:00", "JPN225, JPY pairs"],
          ["Asia (Hong Kong)", "01:30", "08:00", "HK50, CNH pairs"],
          ["UK (LSE)",         "08:00", "16:30", "UK100, GBP pairs, BP, HSBC"],
          ["US Pre-market",    "13:30", "14:30", "Early signals, futures"],
          ["US (NYSE/NASDAQ)", "14:30", "21:00", "NVDA, META, MSFT, SPX, XAU"],
          ["US/UK Overlap",    "14:30", "16:30", "Highest volatility window"]
        ],
        [2000, 1400, 1400, 3826]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 10 ──────────────────────────────────────────────────────────
      userBubble(["For cloud storage - what do I need to do there or will you handle the storage, performance, monitoring, costs etc?"]),
      spacer(),
      claudeLabel(),
      cHeading("What Claude Handles vs What You Handle"),
      makeTable(
        ["Claude Handles", "You Handle (one-time setup)"],
        [
          ["Writing trade records to the database", "Create free account at supabase.com (2 minutes)"],
          ["Reading positions and config at each run", "Create a new project (2 minutes)"],
          ["Writing decision logs to GitHub", "Run SQL script to create tables (1 minute)"],
          ["Querying P&L history for weekly review", "Copy connection string to Routine environment variables (1 minute)"]
        ],
        [4200, 4426]
      ),
      cPara("After setup, Supabase handles: uptime, backups, performance, security patching. Free tier sufficient for development. Upgrade to Pro (~£20/mo) when going live with real money."),
      spacer(), spacer(),

      // ── EXCHANGE 11 ──────────────────────────────────────────────────────────
      userBubble(["Grafana (Reporting) can wait for now - lets look at MCP. For my notes - Model Context Protocol (MCP) is an open-source standard created by Anthropic. Think of it like a USB-C port for AI: it standardizes how AI assistants securely connect to your local files, databases, and external apps so they can fetch data and execute tasks without needing custom integrations"]),
      spacer(),
      claudeLabel(),
      cHeading("MCP Connectors for This System"),
      makeTable(
        ["Connector", "Data Provided", "Cost"],
        [
          ["Unusual Whales MCP", "Flow alerts, dark pool, GEX, IV rank, VWAP, screener, congress trades, director buys, news, technicals, financials", "~$250/mo"],
          ["Supabase MCP",       "SQL execute, table management, read/write trade log and positions", "Free"],
          ["GitHub MCP",         "Commit decision log JSON files to repository", "Free"],
          ["IG UK (Python shim)","Execute trades via REST API — no MCP server available", "Free (with account)"]
        ],
        [2000, 4600, 1626]
      ),
      spacer(),
      cHeading("Full MCP Stack Per Routine Run"),
      cCode("Cloud Routine fires"),
      cCode("  Unusual Whales MCP  → flow alerts, GEX, IV, congress trades, technicals"),
      cCode("  Supabase MCP        → read open positions, read user risk profiles"),
      cCode("  Claude reasons over all data → produces trade JSON"),
      cCode("  Python shim (shell command) → IG API → place orders"),
      cCode("  Supabase MCP        → write trade records"),
      cCode("  GitHub MCP          → commit decision log JSON"),
      spacer(), spacer(),

      // ── EXCHANGE 12 ──────────────────────────────────────────────────────────
      userBubble(["What is 'shim'?"]),
      spacer(),
      claudeLabel(),
      cPara("A shim is a small piece of code that sits between two systems and translates or bridges them when they cannot talk to each other directly."),
      cPara("In your case: Claude wants to place a trade. IG UK has no MCP server. The Python shim sits in the middle — it receives Claude's decision and forwards it to IG."),
      cPara("Think of it like a travel adapter. Your UK plug (Claude) cannot fit directly into a US socket (IG API), so you use an adapter (shim) that does nothing clever — it just makes the connection possible."),
      cPara("The shim has no intelligence. It does not decide what to trade, when, or how much. It only does:"),
      cCode("1. Read Claude's decision  →  'BUY NVDA, size 10, stop at 118.50'"),
      cCode("2. Call IG API             →  places the order"),
      cCode("3. Return result           →  'Order confirmed, deal ref IG-12345'"),
      spacer(), spacer(),

      // ── EXCHANGE 13 ──────────────────────────────────────────────────────────
      userBubble(["Perfect, let's look at that next (the Python shim for IG)"]),
      spacer(),
      claudeLabel(),
      cHeading("The IG Python Shim — Design"),
      cPara("What It Does (Nothing More): INPUT: JSON from Claude. OUTPUT: Order confirmation back to Claude. SIDE EFFECT: Trade placed on IG account."),
      spacer(),
      cHeading("JSON Claude Produces (input to shim)"),
      cCode('{ "action": "OPEN", "user": "owner", "epic": "UC.D.NVDA.DAILY.IP",'),
      cCode('  "direction": "BUY", "size": 1.0, "order_type": "MARKET",'),
      cCode('  "stop_distance": 45, "limit_distance": 90, "trailing_stop": true,'),
      cCode('  "trailing_stop_increment": 5, "currency_code": "GBP" }'),
      spacer(),
      cHeading("What the Shim Does Step by Step"),
      cCode("1. Read JSON decision file written by Claude"),
      cCode("2. Load IG credentials from environment variables (per user)"),
      cCode("3. Run circuit breaker checks:"),
      cCode("      - Is market open?"),
      cCode("      - Is spread within acceptable range?"),
      cCode("      - Has daily loss limit been breached?"),
      cCode("      - Is there already an open position on this epic?"),
      cCode("4. If all checks pass → call IG API"),
      cCode("5. Confirm order filled → return deal reference"),
      cCode("6. Write confirmation back (Claude logs to Supabase)"),
      spacer(),
      cHeading("Key IG Parameters"),
      makeTable(
        ["Parameter", "What It Is", "Example"],
        [
          ["epic",                   "IG unique instrument identifier",     "IX.D.NASDAQ.DAILY.IP"],
          ["direction",              "BUY or SELL",                         "BUY"],
          ["size",                   "£ per point for spread bet",         "1.0 = £1/point"],
          ["stop_distance",          "Points away for stop loss",           "45 points"],
          ["trailing_stop",          "Whether stop follows price",          "true"],
          ["guaranteed_stop",        "Pay premium for guaranteed fill",     "false (adds cost)"]
        ],
        [2400, 3400, 2826]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 14 ──────────────────────────────────────────────────────────
      userBubble(["For the initial load - option 1 and then if instrument is not found use Option 2 (confirmed for epic lookup)"]),
      spacer(),
      claudeLabel(),
      cPara("Saved. The table self-populates over time. After a few weeks, the lookup covers every instrument Claude is likely to pick — the IG API fallback becomes rarely needed."),
      spacer(), spacer(),

      // ── EXCHANGE 15 ──────────────────────────────────────────────────────────
      userBubble(["Is it also worth considering director buys (not awards) especially sizeable and/or new positions?"]),
      spacer(),
      claudeLabel(),
      cPara("Yes — and it is one of the strongest signals available. Directors buying with their own money is significant because:"),
      cBullet("They have material non-public information about their company's prospects"),
      cBullet("Buying is always voluntary — unlike selling, which has many innocent explanations"),
      cBullet("A new position (first-ever purchase) is the strongest signal of all"),
      cBullet("Academic research shows director buy signals outperform the market by 6–10% over 12 months on average"),
      spacer(),
      cHeading("Filtering Rules Claude Should Apply"),
      makeTable(
        ["Filter", "Strong Signal", "Weak / Ignore"],
        [
          ["Transaction type", "Open market purchase with own cash",    "Award, DRIP, option exercise, gift"],
          ["Size",             "> 1x annual salary",                    "Nominal £5,000 purchase"],
          ["New position",     "First-ever purchase by this director",  "Adding small amount to large existing holding"],
          ["Cluster",          "2+ directors buying within 30 days",   "Single isolated purchase"],
          ["Role",             "CEO, CFO, Chairman",                    "Minor non-exec buying small amount"]
        ],
        [2000, 3200, 3426]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 16 ──────────────────────────────────────────────────────────
      userBubble(["For institutional trades the vwap, vwma, volume and sites like unusualwhales could be very helpful as volume can precede price for the big players. It is also possible now to view TradingView charts live using MCP to make decisions live."]),
      spacer(),
      claudeLabel(),
      cHeading("TradingView MCP — Critical Constraint Discovered"),
      cPara("The TradingView MCP works by connecting to your locally running TradingView Desktop app via Chrome DevTools Protocol. It reads what is on your screen in real time."),
      cPara("This creates a fundamental conflict: TradingView MCP requires your local machine and TradingView Desktop open. Cloud Routines run on Anthropic's servers — no access to your local machine."),
      spacer(),
      cHeading("Two-Path Solution"),
      cBullet("Path A — Use Unusual Whales MCP for institutional volume signals (cloud-compatible): dark pool prints, net premium ticks, GEX, VWAP via technical indicators endpoint. All run natively in Cloud Routines."),
      cBullet("Path B — Use TradingView MCP for your own personal live analysis at your desk (local session only, not autonomous)."),
      spacer(), spacer(),

      // ── EXCHANGE 17 ──────────────────────────────────────────────────────────
      userBubble(["Let's leave Option B for now and leave it as an option to resume/explore later. Ultimately I would like this tool to run with as much evidence based information with little input from me."]),
      spacer(),
      claudeLabel(),
      cPara("Noted and saved. Core design principle: maximum autonomy — system must run with as much evidence-based information as possible and minimal input from the user."),
      spacer(), spacer(),

      // ── EXCHANGE 18 ──────────────────────────────────────────────────────────
      userBubble(["As the US senate appear to be making some very profitable share purchases in recent years, these are thought to be very good for driving our confidence metric"]),
      spacer(),
      claudeLabel(),
      cPara("Agreed — studies show US senators outperform the market by 12–25% above benchmark on disclosed trades."),
      cHeading("Signal Filtering — What Claude Should Act On"),
      makeTable(
        ["Trade Characteristic", "Weight", "Reason"],
        [
          ["Purchase (not sale)",                     "High",      "Buys are intentional; sales have many innocent reasons"],
          ["Senate over House",                        "Higher",    "Senators have longer tenure, better committee access"],
          ["Cluster — 3+ members same ticker, 30 days","Very High", "Coordinated signal, not coincidence"],
          ["Committee relevance",                      "Very High", "e.g. tech committee member buying NVDA"],
          ["Bipartisan — both parties buying same ticker","Very High","Crosses political lines — strong conviction"],
          ["Size > $50,000",                           "Higher",    "Meaningful personal commitment"],
          ["Recency < 30 days",                        "Higher",    "Signal still fresh"]
        ],
        [3000, 1400, 4226]
      ),
      spacer(),
      cHeading("Recommended Data Sources"),
      makeTable(
        ["Source", "Coverage", "Cost"],
        [
          ["Unusual Whales MCP",   "Senate + House — already in stack", "Included"],
          ["Quiverquant API",      "Congress historical excess return vs SPY — identifies best-performing senators", "~$30/mo"],
          ["Finnhub",              "Senate + House STOCK Act — free tier fallback", "Free"]
        ],
        [2400, 4400, 1826]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 19 ──────────────────────────────────────────────────────────
      userBubble(["Is there anything else to consider or could we be foul of too much information?"]),
      spacer(),
      claudeLabel(),
      cPara("Great question — yes, there is a real risk called signal noise or overfitting to data."),
      cHeading("The Risk of Too Much Information"),
      cBullet("Conflicting signals cause paralysis — without a clear hierarchy, the engine freezes or makes arbitrary decisions"),
      cBullet("Spurious correlations — the more signals added, the more likely you find patterns that do not repeat"),
      cBullet("Signal lag mismatch — congress disclosures lag 45 days; dark pool prints lead by hours. Mixing without accounting for time horizons creates confusion"),
      cBullet("Over-confidence on agreement — correlated signals (congress + director buys often move together) give false confidence"),
      spacer(),
      cHeading("Recommended Lean Stack"),
      makeTable(
        ["Layer", "Signals", "Source"],
        [
          ["GATE",          "Macro regime — VIX + yield curve + DXY. Must pass — no trades if fails.", "FRED + Yahoo Finance"],
          ["PRIMARY",       "Options flow (call/put imbalance ≥3:1) + Dark pool prints", "Unusual Whales MCP"],
          ["CONFIRMATION",  "GEX by strike, VWAP position, Director cluster buys, Congress cluster buys", "Unusual Whales MCP + Quiverquant"],
          ["RISK FILTER",   "News/economic calendar — no trades within 30 min of major events", "Unusual Whales MCP"]
        ],
        [1600, 4200, 2826]
      ),
      cPara("Rule of thumb: if removing a signal would not change more than 10% of trade decisions, it is probably noise. Start lean. Add signals back only if backtesting shows improvement."),
      spacer(), spacer(),

      // ── EXCHANGE 20 ──────────────────────────────────────────────────────────
      userBubble(["Let's try the prompt and see how we get on"]),
      spacer(),
      claudeLabel(),
      cHeading("System Prompt (set once in the Routine configuration)"),
      cCode("You are an autonomous trading decision engine for a UK spread betting account."),
      cCode("Your job is to analyse market data and produce structured trade decisions."),
      cCode("You must be evidence-based, conservative, and explain every decision clearly."),
      cCode(""),
      cCode("ACCOUNTS: owner (live), wife (live), son (paper trade only)"),
      cCode(""),
      cCode("GATE — check this first, trade nothing if it fails:"),
      cCode("1. Read macro regime from today's data (VIX, yield curve, DXY)"),
      cCode("2. If VIX>35 OR curve inverted AND DXY surging: CRISIS, place no trades"),
      cCode("3. Classify regime as RISK-ON, RISK-OFF, or NEUTRAL with confidence score"),
      cCode(""),
      cCode("PRIMARY SIGNALS — required for any trade:"),
      cCode("  Options flow: call/put premium imbalance. Minimum 3:1 ratio."),
      cCode("  Dark pool prints: large institutional prints in same direction."),
      cCode("At least one primary signal must be present."),
      cCode(""),
      cCode("CONFIRMATION SIGNALS — scale position size (20% per confirmation, max 2x):"),
      cCode("  GEX: positive GEX = price magnet above, negative = below"),
      cCode("  VWAP: price above VWAP = bullish, below = bearish"),
      cCode("  Director buys: open market >$50k within 30 days, cluster (2+) = high weight"),
      cCode("  Congress buys: senate cluster (3+) or high-track-record senator"),
      cCode(""),
      cCode("RISK FILTER:"),
      cCode("  High-impact event within 30 minutes: skip instrument"),
      cCode("  Daily loss limit breached: halt all trades for that account"),
      cCode("  Already open position on instrument: skip"),
      spacer(),
      cHeading("Session Prompt Output Format (JSON)"),
      cCode('{ "session": "US_OPEN", "timestamp": "2026-05-29T15:00:00Z",'),
      cCode('  "macro_regime": { "classification": "RISK-ON", "confidence": 0.74,'),
      cCode('    "summary": "Plain English explanation" },'),
      cCode('  "trades": [{'),
      cCode('    "user": "owner", "epic": "UC.D.NVDA.DAILY.IP",'),
      cCode('    "direction": "BUY", "size": 1.5, "stop_distance": 45,'),
      cCode('    "signals_fired": { "gate_passed": true,'),
      cCode('      "primary": ["options_flow_4.2:1", "dark_pool_bullish_$3.2M"],'),
      cCode('      "confirmations": ["gex_positive", "vwap_above", "director_cluster"] },'),
      cCode('    "confirmation_count": 3, "position_size_multiplier": 1.6,'),
      cCode('    "rationale": "Plain English explanation" }],'),
      cCode('  "skipped_instruments": [{"instrument": "META", "reason": "Balanced flow 1.2:1"}] }'),
      spacer(), spacer(),

      // ── EXCHANGE 21 ──────────────────────────────────────────────────────────
      userBubble(["Plenty of detail will make analysis better later on - happy with plenty of data"]),
      spacer(),
      claudeLabel(),
      cPara("Saved. Capture as much data as possible in all logs — verbose is better than sparse. Every trade record captures: macro regime + confidence, VIX/curve/DXY at entry, call/put ratio, IV rank, dark pool direction, VWAP position, GEX assessment, director buy count, congress buy count, confirmation count, size multiplier, entry/exit price and time, exit reason, gross/net P&L, max favourable/adverse excursion, full plain-English rationale."),
      spacer(), spacer(),

      // ── EXCHANGE 22 ──────────────────────────────────────────────────────────
      userBubble(["I just wanted to confirm the plan so far - does it need TradingView at all for now?"]),
      spacer(),
      claudeLabel(),
      cPara("No — TradingView is not needed for the autonomous system as currently designed."),
      makeTable(
        ["Original TradingView Role", "Replaced By"],
        [
          ["Live price data",                  "IG UK API (live prices via WebSocket)"],
          ["VWAP / volume indicators",         "Unusual Whales MCP technical indicators endpoint"],
          ["Chart alerts → Python engine",     "Unusual Whales MCP options flow (a better signal)"],
          ["Technical indicators (RSI, MACD)", "Unusual Whales MCP (RSI kept as optional confirmation)"],
          ["Live chart reading via MCP",       "Parked — incompatible with Cloud Routines"]
        ],
        [3600, 5026]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 23 ──────────────────────────────────────────────────────────
      userBubble(["How does the engine know which metrics to get from IG UK API (Live price data)?"]),
      spacer(),
      claudeLabel(),
      cPara("The engine knows which IG epic to query using the epic lookup table in Supabase. The key insight: for the hourly monitor routine, the /positions endpoint from IG includes the current live level and unrealised P&L for every open position in one call — no separate prices endpoint needed."),
      makeTable(
        ["Routine", "IG API Calls"],
        [
          ["Market Open (new trades)", "GET /markets/{epic} — spread check + market status per candidate"],
          ["Hourly Monitor",           "GET /positions — one call covers all open positions with live P&L"],
          ["Session Close",            "DELETE /positions/otc — close each open position"],
          ["Weekend Review",           "GET /markets/{epic} — refresh spread and ATR data for epic table"]
        ],
        [2800, 5826]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 24 ──────────────────────────────────────────────────────────
      userBubble(["And please remind me how the engine knows to look at NVDA and not other e.g. AVCT"]),
      spacer(),
      claudeLabel(),
      cPara("The engine knows because of the instrument scope defined in each routine's system prompt — a static list per session — combined with dynamic discovery from Unusual Whales."),
      cHeading("The Better Approach — Dynamic Candidate Discovery"),
      cPara("At session start, Claude calls the Unusual Whales screener (top 20 by unusual options activity). Then applies filters:"),
      makeTable(
        ["Filter", "Rule", "Reason"],
        [
          ["Minimum market cap",   "> $10 billion",                         "Avoid small/micro caps — low liquidity, wide spreads"],
          ["Minimum options vol",  "> 10,000 contracts/day",                "Enough activity to be meaningful"],
          ["IG tradeable",         "Must exist in epic lookup or findable",  "Cannot trade what IG does not offer"],
          ["No open position",     "Skip if position exists",               "No doubling up"],
          ["Market open",          "Session hours must be active",          "No point analysing a closed market"]
        ],
        [2200, 3200, 3226]
      ),
      cPara("NVDA appears because it consistently shows extraordinary institutional options activity. AVCT would only appear if it had genuinely unusual flow — and would then fail the market cap filter."),
      spacer(), spacer(),

      // ── EXCHANGE 25 ──────────────────────────────────────────────────────────
      userBubble(["max open x5, risk per trade x2 (confirmed position sizing parameters)"]),
      spacer(),
      claudeLabel(),
      cPara("Updated. At 2% risk per trade with 5 simultaneous positions, the theoretical maximum exposure is 10% of account equity at once. The daily loss limit of 3% acts as the hard brake before that scenario plays out fully."),
      makeTable(
        ["Parameter", "Owner", "Wife", "Son"],
        [
          ["risk_per_trade",      "2%",          "1%",          "2% (paper)"],
          ["daily_loss_limit",    "3%",          "2%",          "N/A"],
          ["max_open_positions",  "5",           "5",           "5"],
          ["allowed_sessions",    "All",         "UK + US",     "All"],
          ["paper_trade",         "No",          "No",          "Yes"]
        ],
        [2800, 2000, 2000, 2026]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 26 ──────────────────────────────────────────────────────────
      userBubble(["full system summary"]),
      spacer(),
      claudeLabel(),
      cHeading("Full System Summary"),
      cPara("Mission: Find the most profitable methods to trade CFD spread betting instruments across global markets using full automation — removing emotional bias, running 24×5, requiring minimal human input, with every decision evidence-based and fully logged for retrospective analysis."),
      spacer(),
      cHeading("Complete Signal Stack"),
      makeTable(
        ["Layer", "Signal", "Source", "Weight"],
        [
          ["Macro Gate",       "VIX + yield curve + DXY",                           "FRED + Yahoo Finance",        "High — gates everything"],
          ["Institutional vol","Dark pool prints",                                   "Unusual Whales MCP",          "Very High"],
          ["Institutional vol","Net premium ticks, GEX, VWAP",                      "Unusual Whales MCP",          "High"],
          ["Options flow",     "Call/put imbalance + IV rank",                       "Unusual Whales MCP",          "Very High"],
          ["Smart money",      "Director first-ever / cluster buy (>$50k, OMP)",    "Unusual Whales MCP",          "Very High"],
          ["Smart money",      "Senate cluster buy / bipartisan / committee-relevant","Unusual Whales MCP + Quiverquant","Very High"],
          ["Copy trade",       "Top Darwin alignment",                               "Darwinex API",                "Medium"],
          ["Risk filter",      "Economic calendar — block 30 min around events",    "Unusual Whales MCP",          "Override"]
        ],
        [1800, 3000, 2400, 1426]
      ),
      spacer(),
      cHeading("Monthly Costs"),
      makeTable(
        ["Component", "Cost"],
        [
          ["Unusual Whales MCP",      "~$250/mo"],
          ["Quiverquant API",         "~$30/mo"],
          ["Supabase Pro (when live)", "~£20/mo"],
          ["Claude API usage",        "~$5–20/mo"],
          ["Darwinex / FRED / Yahoo", "Free"],
          ["IG UK account",           "Free (spread is the cost)"],
          ["Total",                   "~$305–320/mo"]
        ],
        [5000, 3626]
      ),
      spacer(), spacer(),

      // ── EXCHANGE 27 ──────────────────────────────────────────────────────────
      userBubble(["Can I share this whole conversation with a colleague?"]),
      spacer(),
      claudeLabel(),
      cPara("Yes. Options: Share via Claude.ai (if session synced), copy and paste, screenshot sections, or use the Word document. The Word document is the cleanest option for a colleague reading it for the first time."),
      spacer(), spacer(),

      // ── EXCHANGE 28 ──────────────────────────────────────────────────────────
      userBubble(["I am using Claude Code on Windows desktop - what are my best options? .docx sounds great"]),
      spacer(),
      claudeLabel(),
      cPara("In Claude Code on Windows desktop, there is no one-click share button. Best option: Word document via the /docx skill — professional formatting, no Claude account needed for your colleague. This conversation is saved locally; the memory files ensure any future Claude session on your machine picks up the full context."),
      spacer(), spacer(),

      // ── EXCHANGE 29 ──────────────────────────────────────────────────────────
      userBubble(["I did not like the format of the word document. Please create a word document with the full conversation from the start of this project earlier today"]),
      spacer(),
      claudeLabel(),
      cPara("This document is the result of that request — the full design conversation from 29 May 2026, formatted as a readable exchange between you and Claude."),
      spacer(), spacer(),

      // ── END ─────────────────────────────────────────────────────────────────
      new Paragraph({
        children: [new TextRun({ text: "— End of Conversation —", font: "Arial", size: 20, italics: true, color: "999999" })],
        alignment: AlignmentType.CENTER, spacing: { before: 480 }
      })
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("TradingSystemConversation.docx", buffer);
  console.log("Conversation document created: TradingSystemConversation.docx");
});
