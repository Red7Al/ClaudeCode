# ======================================================================================================================
# File:         config.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Central configuration for the EndToEndTrading system.
# All instrument definitions, epic codes, ATR multipliers, session instrument
# lists, and system constants live here.
#
# This is the file to edit when:
#   - Adding a new instrument to trade
#   - Adjusting ATR stop multipliers
#   - Changing session instrument lists
#   - Updating Yahoo Finance ticker mappings
#   - Modifying trade/risk constants
#
# Epic codes are verified CFD epics from IG UK (account HTIRV).
# Format reference:
#   CS.D.*    Spot / cash CFDs (FX, metals)
#   CC.D.*    Commodity futures CFDs
#   IX.D.*    Index CFDs
#   UA.D.*    US equity CFDs (24hr)
#   UB.D.*    US equity CFDs (24hr, alternate prefix)
#   UD.D.*    US equity CFDs (24hr, alternate prefix)
#   KA.D.*    UK equity CFDs
#   SE.D.*    Extended hours US equity CFDs
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Epics verified against live IG account HTIRV. Seeded into Supabase
#                                 epic_lookup table on 2026-05-30.
# 1.1.0   2026-06-05  Alex Hind   Raised MIN_RISK_REWARD from 2.0 to 2.5. All trades (including HVF) now require minimum
#                                 2.5:1 risk/reward to be placed.
# 1.2.0   2026-06-05  Alex Hind   Removed DAX (Germany) from EPIC_MAP, OPTIONS_PROXY_MAP, YAHOO_MAP, ATR_MULTIPLIERS.
#                                 DAX suspended from trading until further notice.
# 1.3.0   2026-06-05  Alex Hind   Added PREMARKET_BRIEF to SESSION_INSTRUMENTS
# 1.11.0  2026-06-13  Alex Hind   X_DRAFT_TOP_N (count of X drafts per run, default 20) and HVF_LIQUIDITY_TIERS_GBP
#                                 (turnover-based pattern-quality penalty so illiquid names rank lower) — both tunable
#                                 here without touching intraday_signals.py / price_action.py (user 2026-06-13).
# 1.12.0  2026-06-13  Alex Hind   HVF_REPORT_TOP_N — max setups listed in the daily report (weight-ordered); tunable
#                                 here (user 2026-06-13: "too many setups").
# 1.10.0  2026-06-12  Alex Hind   TICKER FIX: "TE" replaced with "TEL" everywhere — Yahoo's TE is T1 Energy Inc, NOT
#                                 TE Connectivity (TEL). All TE signals had been computed on T1 Energy's prices while
#                                 orders would have routed to TE Connectivity's epic; never traded. Also
#                                 MAX_TRADES_PER_INSTRUMENT_PER_DAY 2 → 5 (user 2026-06-12).
# 1.9.0   2026-06-07  Alex Hind   Add XAGUSD (Silver) and OIL (WTI) to PREMARKET_BRIEF so the Sunday pre-open scan
#                                 covers ALL commodities that reopen on the CME Globex Sunday session (~22:00 UTC / 11pm
#                                 UK), not just Gold.
# 1.8.0   2026-06-07  Alex Hind   Add MSCI_EAFE (CFTC 244041) as a COT-only proxy for UK/international-developed index
#                                 positioning — CFTC has no FTSE/UK contract. YAHOO_MAP MSCI_EAFE→EFA for the divergence
#                                 price calc; the weekly COT report pairs it with the FTSE 100 price trend.
# 1.7.0   2026-06-07  Alex Hind   CORRECT the Gold/Silver CFTC codes after verifying every code against the live CFTC
#                                 API. The 1.6.0 diagnosis was wrong on both counts: 084691 is SILVER (not Gold), and
#                                 084251 returns NO DATA (not Silver). Real bug: XAUUSD (Gold) was pulling 084691 =
#                                 Silver's positioning, so Gold COT was always Silver's numbers; and 1.6.0's
#                                 XAGUSD→084251 broke Silver entirely. Now verified: Gold=088691, Silver=084691 ("...-
#                                 COMMODITY EXCHANGE INC."). All other codes (OIL, GBPUSD, AUDUSD, USDJPY, EURUSD,
#                                 SPX500, NASDAQ) confirmed correct.
# 1.6.0   2026-06-06  Alex Hind   Fix XAGUSD CFTC code: was '084691' (Gold's code), corrected to '084251' (Silver). COT
#                                 lookups for XAGUSD were silently returning Gold positioning data. [SUPERSEDED by 1.7.0
#                                 — this diagnosis was incorrect.]
# 1.5.0   2026-06-06  Alex Hind   Lowered crypto PA thresholds from 25 → 20. ETH pa_score=-35 was still WAIT because the
#                                 deployment of the threshold override (v1.4.0) happened after the Jun-05 sessions ran.
#                                 Also: crypto rarely scores -25 without a full range breakout (-30 pts) — the -20 floor
#                                 allows ETHUSD/BTCUSD to confirm on trend+MA alignment alone when other primaries
#                                 align.
# 1.4.0   2026-06-05  Alex Hind   Added PA_CONFIRM_THRESHOLDS and PA_CONFIRM_THRESHOLD_DEFAULT — per-instrument PA gate
#                                 thresholds. Crypto=25, FX/metals=30-35, equities/indices=40 (default). with 24/7
#                                 instruments: crypto, XAUUSD (Spot Gold), and FX pairs active on Sunday evening.
# ======================================================================================================================


# ======================================================================================================================
# IG Epic Codes — Verified CFD Epics
# Maps our ticker names to the exact IG API epic identifiers.
# Expiry: DFB (Daily Funded Bet) for all rolling CFD contracts.
# ======================================================================================================================

EPIC_MAP = {

    # ------------------------------------------------------------------------------------------------------------------
    # Forex — Spot Cash CFDs (TODAY contracts)
    # ------------------------------------------------------------------------------------------------------------------
    "GBPUSD":  "CS.D.GBPUSD.TODAY.IP",     # British Pound / US Dollar
    "AUDUSD":  "CS.D.AUDUSD.TODAY.IP",     # Australian Dollar / US Dollar
    "USDJPY":  "CS.D.USDJPY.TODAY.IP",     # US Dollar / Japanese Yen
    "EURUSD":  "CS.D.EURUSD.TODAY.IP",     # Euro / US Dollar
    "USDCAD":  "CS.D.USDCAD.TODAY.IP",     # US Dollar / Canadian Dollar
    "USDCHF":  "CS.D.USDCHF.TODAY.IP",     # US Dollar / Swiss Franc
    "NZDUSD":  "CS.D.NZDUSD.TODAY.IP",     # New Zealand Dollar / US Dollar

    # ------------------------------------------------------------------------------------------------------------------
    # Commodities — Precious Metals (Spot Cash CFDs)
    # ------------------------------------------------------------------------------------------------------------------
    "XAUUSD":    "CS.D.USCGC.TODAY.IP",    # Spot Gold
    "GOLD":      "CS.D.USCGC.TODAY.IP",    # Spot Gold (alias)
    "SPOTGOLD":  "CS.D.USCGC.TODAY.IP",    # Spot Gold (alias)
    "XAGUSD":    "CS.D.USCSI.TODAY.IP",    # Spot Silver
    "SILVER":    "CS.D.USCSI.TODAY.IP",    # Spot Silver (alias)
    "PLATINUM":  "CS.D.PLAT.TODAY.IP",     # Platinum
    "PALLADIUM": "CS.D.PALL.TODAY.IP",     # Palladium
    "COPPER":    "CS.D.COPPER.TODAY.IP",   # Copper
    "ZINC":      "CS.D.ZINC.TODAY.IP",     # Zinc
    "NICKEL":    "CS.D.NICKEL.TODAY.IP",   # Nickel
    "LEAD":      "CS.D.LEAD.TODAY.IP",     # Lead
    "ALUMINIUM": "CS.D.ALUMINIUM.TODAY.IP",# Aluminium

    # ------------------------------------------------------------------------------------------------------------------
    # Commodities — Energy (Futures CFDs)
    # ------------------------------------------------------------------------------------------------------------------
    "OIL":    "CC.D.CL.USS.IP",            # US Crude Oil (WTI)
    "USOIL":  "CC.D.CL.USS.IP",            # US Crude Oil (alias)
    "CL":     "CC.D.CL.USS.IP",            # US Crude Oil (alias)

    # ------------------------------------------------------------------------------------------------------------------
    # Indices — Major Markets (CFDs)
    # ------------------------------------------------------------------------------------------------------------------
    # S&P 500
    "SPX500": "IX.D.SPTR500.IFD.IP",       # S&P 500
    "SPX":    "IX.D.SPTR500.IFD.IP",       # S&P 500 (alias)
    "US500":  "IX.D.SPTR500.IFD.IP",       # S&P 500 (alias)
    "SP500":  "IX.D.SPTR500.IFD.IP",       # S&P 500 (alias)

    # NASDAQ 100
    "NASDAQ": "IX.D.NASDAQ.IFD.IP",        # NASDAQ 100
    "NAS100": "IX.D.NASDAQ.IFD.IP",        # NASDAQ 100 (alias)
    "NDX":    "IX.D.NASDAQ.IFD.IP",        # NASDAQ 100 (alias)
    "USTEC":  "IX.D.NASDAQ.IFD.IP",        # NASDAQ 100 (alias)
    "US100":  "IX.D.NASDAQ.IFD.IP",        # NASDAQ 100 (alias)

    # Dow Jones 30
    "DOW":    "IX.D.DOW.IFD.IP",           # Dow Jones 30 (verify epic)
    "US30":   "IX.D.DOW.IFD.IP",           # Dow Jones 30 (alias)
    "WALL":   "IX.D.DOW.IFD.IP",           # Dow Jones 30 (alias)

    # UK / Europe / Asia
    "UK100":   "IX.D.FTSE.IFD.IP",         # FTSE 100
    "FTSE100": "IX.D.FTSE.IFD.IP",         # FTSE 100 (alias)
    "JPN225":  "IX.D.NIKKEI.IFD.IP",       # Nikkei 225
    "HK50":    "IX.D.HSIF.IFD.IP",         # Hang Seng

    # ------------------------------------------------------------------------------------------------------------------
    # US Equities — Large Cap Tech (24-hour CFDs)
    # ------------------------------------------------------------------------------------------------------------------
    "AAPL":  "UA.D.AAPL.DAILY.IP",         # Apple Inc
    "MSFT":  "UC.D.MSFT.DAILY.IP",         # Microsoft Corp (epic verified live 2026-06-09; was UA→404)
    "NVDA":  "UC.D.NVDA.DAILY.IP",         # NVIDIA Corp (epic verified live 2026-06-09; was UA→404)
    "AMZN":  "UA.D.AMZN.DAILY.IP",         # Amazon.com Inc
    "GOOGL": "UB.D.GOOGL.DAILY.IP",        # Alphabet Inc Class A
    "GOOG":  "UB.D.GOOGUS.DAILY.IP",       # Alphabet Inc Class C
    "META":  "UB.D.FB.DAILY.IP",           # Meta Platforms (NOTE: IG uses old FB epic)
    "AMD":   "UA.D.AMD.DAILY.IP",          # Advanced Micro Devices
    "AVGO":  "UA.D.AVGO.DAILY.IP",         # Broadcom Inc
    "TSLA":  "UD.D.TSLA.DAILY.IP",         # Tesla Inc
    "PLTR":  "SE.D.PLTRUS.DAILY.IP",       # Palantir Technologies

    # ------------------------------------------------------------------------------------------------------------------
    # US Equities — Semiconductors
    # ------------------------------------------------------------------------------------------------------------------
    "INTC":  "UB.D.INTC.DAILY.IP",         # Intel Corp
    "QCOM":  "UA.D.QCOM.DAILY.IP",         # Qualcomm
    "MU":    "UA.D.MU.DAILY.IP",           # Micron Technology
    "AMAT":  "UA.D.AMAT.DAILY.IP",         # Applied Materials
    "KLAC":  "UA.D.KLAC.DAILY.IP",         # KLA Corp
    "ASML":  "EG.D.ASML.DAILY.IP",         # ASML Holding NV (European listed)

    # ------------------------------------------------------------------------------------------------------------------
    # Aschenbrenner AI Infrastructure / Energy Picks
    # ------------------------------------------------------------------------------------------------------------------
    "NBIS":  "UD.D.YNDX.DAILY.IP",      # Nebius Group (AI cloud) — uses old Yandex epic on IG
    "SNDK":  "UD.D.SNDKUS.DAILY.IP",    # SanDisk
    "BE":    "SA.D.BEUS.DAILY.IP",       # Bloom Energy (power for AI datacentres)
    "CRWV":  "UA.D.CRWVUS.DAILY.IP",    # CoreWeave (AI cloud compute)
    "IREN":  "UB.D.IRENUS.DAILY.IP",    # Iris Energy (AI/crypto compute)
    "APLD":  "UA.D.APLDUS.DAILY.IP",    # Applied Digital (AI datacentres)
    "RIOT":  "UC.D.RIOTUS.DAILY.IP",    # Riot Platforms (crypto mining)
    "CLSK":  "UA.D.CLSKUS.DAILY.IP",    # CleanSpark (Bitcoin mining)
    "BTDR":  "UA.D.BTDRUS.DAILY.IP",    # Bitdeer Technologies
    "TEL":   "SG.D.TELUS.DAILY.IP",     # TE Connectivity (Yahoo ticker TEL — "TE" is T1 Energy, fixed 2026-06-12)
    "SEI":   "UD.D.SEICUS.DAILY.IP",    # SEI Investments

    # ------------------------------------------------------------------------------------------------------------------
    # Trump / Jensen Huang Recommended
    # ------------------------------------------------------------------------------------------------------------------
    "IBM":   "SD.D.IBM.DAILY.IP",       # IBM Corp
    "DELL":  "SB.D.DELLUS.DAILY.IP",   # Dell Technologies
    "NOK":   "SE.D.NOK.DAILY.IP",       # Nokia OYJ ADR
    "NOW":   "SE.D.NOWUS.DAILY.IP",     # ServiceNow (Trump + Jensen Huang) — epic verified live 2026-06-09; was UA.D.NOW→404
    "CRWD":  "UA.D.CRWDUS.DAILY.IP",    # CrowdStrike (Jensen Huang) — epic verified live 2026-06-09; was UA.D.CRWD→404

    # ------------------------------------------------------------------------------------------------------------------
    # UK Equities
    # ------------------------------------------------------------------------------------------------------------------
    "BP":    "KA.D.BP.DAILY.IP",           # BP PLC
    "LLOY":  "KA.D.LLOY.DAILY.IP",         # Lloyds Banking Group
    "BARC":  "KA.D.BARC.DAILY.IP",         # Barclays PLC
    "AZN":   "KA.D.AZN.DAILY.IP",          # AstraZeneca PLC
    "RIO":   "KA.D.RIO.DAILY.IP",          # Rio Tinto PLC
    "GSK":   "KA.D.GSK.DAILY.IP",          # GSK PLC
    "VOD":   "KA.D.VOD.DAILY.IP",          # Vodafone Group PLC
    "RR":    "KA.D.RR.DAILY.IP",           # Rolls-Royce Holdings PLC

    # ------------------------------------------------------------------------------------------------------------------
    # Crypto CFDs — top 5 by market cap
    # All trade 24/7 — no market hours restriction on CS.D.* epics
    # XRP, SOL, BNB epics: IG lookup will find and cache on first scan
    # ------------------------------------------------------------------------------------------------------------------
    "BTCUSD":  "CS.D.BITCOIN.TODAY.IP",    # Bitcoin / USD
    "BITCOIN": "CS.D.BITCOIN.TODAY.IP",    # Bitcoin (alias)
    "ETHUSD":  "CS.D.ETHUSD.TODAY.IP",     # Ethereum / USD
    "XRPUSD":  "CS.D.XRPUSD.TODAY.IP",    # XRP / USD
    "SOLUSD":  "CS.D.SOLUSD.TODAY.IP",     # Solana / USD
    "BNBUSD":  "CS.D.BNBUSD.TODAY.IP",     # BNB (Binance Coin) / USD

}


# ======================================================================================================================
# Options Proxy Map
# Indices and commodities don't have options chains on Yahoo Finance
# (^GSPC, ^FTSE, GC=F etc. return empty option chains).
# Map them to liquid ETF proxies for options/GEX signals ONLY.
# Price data continues to use YAHOO_MAP (correct scale for ATR/stop calcs).
# ======================================================================================================================

OPTIONS_PROXY_MAP = {
    # US indices — ETF proxies have the world's most liquid options markets
    "SPX500":  "SPY",    # S&P 500  → SPDR S&P 500 ETF (most liquid options globally)
    "NASDAQ":  "QQQ",    # Nasdaq   → Invesco QQQ Trust
    "DOW":     "DIA",    # Dow 30   → SPDR Dow Jones Industrial Average ETF

    # International indices — less liquid but better than nothing
    "UK100":   "EWU",    # FTSE 100 → iShares MSCI United Kingdom ETF
    "JPN225":  "EWJ",    # Nikkei   → iShares MSCI Japan ETF
    "HK50":    "EWH",    # Hang Seng → iShares MSCI Hong Kong ETF

    # Commodities — ETF options reflect institutional sentiment well
    "XAUUSD":  "GLD",    # Gold     → SPDR Gold Shares (most liquid gold options)
    "XAGUSD":  "SLV",    # Silver   → iShares Silver Trust
    "OIL":     "USO",    # Oil      → United States Oil Fund
    "USOIL":   "USO",    # Oil alias
    "COPPER":  "COPX",   # Copper   → Global X Copper Miners ETF (proxy)

    # Crypto — ETF proxies for options flow signal
    # IBIT (BlackRock iShares Bitcoin Trust) is the most liquid Bitcoin options market
    # ETHA (iShares Ethereum ETF) — newer, options availability varies
    # XRP/SOL/BNB have no suitable ETF proxies yet — options will return NEUTRAL
    "BTCUSD":  "IBIT",   # Bitcoin  → iShares Bitcoin Trust ETF
    "BITCOIN": "IBIT",   # Bitcoin alias
    "ETHUSD":  "ETHA",   # Ethereum → iShares Ethereum ETF
}


# ======================================================================================================================
# Yahoo Finance Ticker Mapping
# Maps our internal ticker names to Yahoo Finance symbols for price/options data.
# ======================================================================================================================

YAHOO_MAP = {

    # UK Equities — LSE tickers ending in .L
    # Note: tickers with a trailing dot on LSE (e.g. RR., BA.) use single .L on Yahoo
    "RR.L":    "RR.L",     # Rolls-Royce
    "BA..L":   "BA.L",     # BAE Systems (LSE: BA. → Yahoo: BA.L)

    # Indices
    "SPX500":  "^GSPC",
    "NASDAQ":  "^IXIC",
    "UK100":   "^FTSE",
    "MSCI_EAFE": "EFA",     # iShares MSCI EAFE ETF — price proxy for EAFE COT divergence calc
    "JPN225":  "^N225",
    "HK50":    "^HSI",

    # Commodities
    "XAUUSD":  "GC=F",         # Gold futures (closest proxy for spot)
    "XAGUSD":  "SI=F",         # Silver futures
    "OIL":     "CL=F",         # WTI Crude futures

    # FX
    "GBPUSD":  "GBPUSD=X",
    "AUDUSD":  "AUDUSD=X",
    "USDJPY":  "USDJPY=X",
    "EURUSD":  "EURUSD=X",

    # China
    "CN50":    "^FTXIN9",    # FTSE China A50 Index

    # Macro
    "VIX":     "^VIX",
    "DXY":     "DX-Y.NYB",

    # US Equities — use ticker directly (Yahoo matches)
    "NVDA":    "NVDA",
    "AAPL":    "AAPL",
    "MSFT":    "MSFT",
    "META":    "META",
    "AMZN":    "AMZN",
    "GOOGL":   "GOOGL",
    "TSLA":    "TSLA",
    "AMD":     "AMD",
    "PLTR":    "PLTR",
    "AVGO":    "AVGO",

    # Crypto — Yahoo Finance tickers for price, BB, HVF, volume signals
    # Options chains are not available on Yahoo Finance for crypto;
    # BTCUSD uses IBIT (BlackRock Bitcoin ETF) as options flow proxy instead.
    "BTCUSD":  "BTC-USD",
    "BITCOIN": "BTC-USD",
    "ETHUSD":  "ETH-USD",
    "XRPUSD":  "XRP-USD",
    "SOLUSD":  "SOL-USD",
    "BNBUSD":  "BNB-USD",

}


# ======================================================================================================================
# ATR Stop Loss Multipliers
# Applied to the 14-period ATR to compute the stop distance for each instrument.
# Wider multiplier = more breathing room = less likely to be stopped out by noise.
# ======================================================================================================================

ATR_MULTIPLIERS = {

    # Equities and indices — standard
    "NVDA":    1.5,
    "AAPL":    1.5,
    "MSFT":    1.5,
    "META":    1.5,
    "AMZN":    1.5,
    "GOOGL":   1.5,
    "TSLA":    1.5,
    "AMD":     1.5,
    "PLTR":    1.5,
    "SPX500":  1.5,
    "NASDAQ":  1.5,
    "UK100":   1.5,
    "JPN225":  1.5,
    "HK50":    1.5,

    # Gold — standard (strong trending instrument)
    "XAUUSD":  1.5,
    "XAGUSD":  1.5,

    # Oil — wider (high volatility, news-driven spikes)
    "OIL":     2.0,
    "USOIL":   2.0,

    # Crypto — significantly wider to avoid being stopped out by normal daily swings
    # Bitcoin typically moves 3-8% per day; a tight stop gets eaten by noise
    "BTCUSD":  2.5,
    "BITCOIN": 2.5,
    "ETHUSD":  2.5,
    "XRPUSD":  3.0,   # XRP is more volatile relative to its ATR
    "SOLUSD":  3.0,
    "BNBUSD":  2.5,

    # FX — tighter (lower volatility per point)
    "GBPUSD":  1.2,
    "AUDUSD":  1.2,
    "USDJPY":  1.2,
    "EURUSD":  1.2,

    # Volatile small-cap equities (crypto-miners + small-cap AI infra). Their wide
    # spreads vs tight default ATR stops blocked entries (user 2026-06-09 — e.g.
    # RIOT/NBIS). Wider stop lifts the spread-to-stop ratio so they can fill;
    # position sizing shrinks size to keep £-risk constant. Extreme OPENING spreads
    # may still block until they narrow — that protection is intentional.
    "RIOT": 3.0, "CLSK": 3.0, "NBIS": 3.0, "CRWV": 3.0, "IREN": 3.0,
    "APLD": 3.0, "BTDR": 3.0, "HIVE": 3.0, "SEI":  3.0, "BE":   3.0,
    "TEL":  3.0, "SNDK": 3.0, "KEEL": 3.0, "WYFI": 3.0, "USAR": 3.0,
    "PATH": 3.0, "ONDS": 3.0, "OUST": 3.0,
}

# Default ATR multiplier for any instrument not listed above
ATR_MULTIPLIER_DEFAULT = 1.5


# ======================================================================================================================
# Sector ETF Map
# Maps individual equity tickers to their representative sector ETF.
# Used by get_sector_alignment() — if the sector ETF itself is directional
# (above/below VWAP) it adds a primary signal confirming the stock's move.
# FX, indices, crypto, and commodities are omitted — they have their own
# macro/COT signals and don't align to US equity sector ETFs.
# ======================================================================================================================

SECTOR_ETF_MAP = {
    # Technology / Software
    "NVDA":  "XLK",   "AMD":   "XLK",   "MSFT":  "XLK",
    "AAPL":  "XLK",   "META":  "XLK",   "AMZN":  "XLK",
    "GOOGL": "XLK",   "TSLA":  "XLK",   "PLTR":  "XLK",
    "CRWD":  "XLK",   "NOW":   "XLK",   "IBM":   "XLK",
    "DELL":  "XLK",   "NOK":   "XLK",
    # Semiconductors
    "AVGO":  "SOXX",  "MU":    "SOXX",
    # AI / compute / Bitcoin mining (smaller names — use broad tech)
    "NBIS":  "XLK",   "CRWV":  "XLK",   "CLSK":  "XLK",
    "RIOT":  "XLK",   "SNDK":  "XLK",   "HIVE":  "XLK",
    "IREN":  "XLK",   "APLD":  "XLK",   "BTDR":  "XLK",
    # Industrials
    "BE":    "XLI",   "TEL":   "XLI",
    # Financials
    "SEI":   "XLF",
    # Energy
    "OIL":   "XLE",   "USOIL": "XLE",
    # Materials (rare earths)
    "USAR":  "XLB",
}


# ======================================================================================================================
# Session Instrument Lists
# Defines which instruments are evaluated at each session open.
# AUS200 excluded — not traded.
# ======================================================================================================================

SESSION_INSTRUMENTS = {

    "AUS_OPEN": [
        "JPN225",   # Nikkei 225
        "HK50",     # Hang Seng (China H-shares exposure)
        "CN50",     # China A50 — Shanghai/Shenzhen top 50, opens 01:30 UTC
        "XAUUSD",   # Gold (trades 24hrs — always evaluated)
        "AUDUSD",   # AUD/USD
        "USDJPY",   # USD/JPY
        # Crypto — 24/7, active during Asia session
        "BTCUSD",   # Bitcoin
        "ETHUSD",   # Ethereum
        "XRPUSD",   # XRP
        "SOLUSD",   # Solana
        "BNBUSD",   # BNB
    ],

    "AUS_MONITOR": [],   # Monitor only — no new instrument scan

    "UK_OPEN": [
        "UK100",    # FTSE 100
        "UK250",    # FTSE 250 index
        "GBPUSD",   # GBP/USD
        "XAUUSD",   # Gold
        # Add FTSE 250 individual stocks here once IG tickers are verified
        # Crypto — 24/7, active during European session
        "BTCUSD",   # Bitcoin
        "ETHUSD",   # Ethereum
        "XRPUSD",   # XRP
        "SOLUSD",   # Solana
        "BNBUSD",   # BNB
    ],

    "UK_MONITOR": [],    # Monitor only — no new instrument scan

    "US_OPEN": [
        # Core instruments
        "SPX500",   # S&P 500
        "NVDA",     # NVIDIA
        "META",     # Meta Platforms
        "MSFT",     # Microsoft
        "AAPL",     # Apple
        "XAUUSD",   # Gold
        "OIL",      # US Crude Oil
        # Notable investor picks — Aschenbrenner
        "NBIS",     # Nebius Group (AI cloud)
        "CRWV",     # CoreWeave (AI compute)
        "CLSK",     # CleanSpark (Bitcoin mining)
        "RIOT",     # Riot Platforms
        "SNDK",     # SanDisk
        "BE",       # Bloom Energy
        "TEL",      # TE Connectivity (Yahoo ticker corrected from "TE" = T1 Energy, 2026-06-12)
        # Trump recommendations
        "IBM",      # IBM
        "DELL",     # Dell Technologies
        "NOW",      # ServiceNow (also Jensen Huang)
        # Jensen Huang
        "PLTR",     # Palantir
        "CRWD",     # CrowdStrike
        # LeopoldATracker picks
        "HIVE",     # HIVE Blockchain (breakout confirmed)
        "KEEL",     # Keel Infrastructure (strongest PA score)
        "WYFI",     # Whitefiber Inc
        # Additional Aschenbrenner / LeopoldATracker overlap
        "IREN",     # Iris Energy
        "APLD",     # Applied Digital
        "BTDR",     # Bitdeer Technologies
        "SEI",      # SEI Investments
        "NOK",      # Nokia
        # Crypto — 24/7, most liquid during US session
        "BTCUSD",   # Bitcoin
        "ETHUSD",   # Ethereum
        "XRPUSD",   # XRP
        "SOLUSD",   # Solana
        "BNBUSD",   # BNB
        # Multi-source CONFIRM LONG picks (LeopoldATracker + Asklivermore)
        "MU",       # Micron Technology — +75 confirmed by both sources
        "USAR",     # USA Rare Earth — +75 AI infrastructure theme
        "PATH",     # UiPath — +65 AI workflow automation
        "ONDS",     # Ondas Holdings — +45 strong uptrend
        "OUST",     # Ouster — +45 strong uptrend
        "ASX",      # ASX — +45 strong uptrend
        "CRM",      # Salesforce — Asklivermore CONFIRM LONG
        "AMD",      # AMD — multi-source confirmed
    ],

    "SESSION_CLOSE": [],  # Review + close decisions only — no new instrument scan

    "WEEKEND_REVIEW": [], # No instrument scan — scoring and digest only

    # Sunday pre-market brief — scans 24/7 instruments active before Asia open.
    # Crypto markets never close; Gold (XAUUSD) and FX pairs active Sunday evening.
    "PREMARKET_BRIEF": [
        "XAUUSD",   # Spot Gold (XAU/USD) — 24/7, CME Globex reopen Sun 22:00 UTC
        "XAGUSD",   # Spot Silver (XAG/USD) — CME Globex reopen Sun 22:00 UTC
        "OIL",      # US Crude Oil (WTI) — CME Globex reopen Sun 22:00 UTC
        "BTCUSD",   # Bitcoin (BTC/USD) — 24/7
        "ETHUSD",   # Ethereum (ETH/USD) — 24/7
        "XRPUSD",   # XRP (XRP/USD) — 24/7
        "SOLUSD",   # Solana (SOL/USD) — 24/7
        "BNBUSD",   # BNB / Binance Coin (BNB/USD) — 24/7
        "GBPUSD",   # GBP/USD — opens Sunday 22:00 UTC
        "AUDUSD",   # AUD/USD — opens Sunday 22:00 UTC
        "USDJPY",   # USD/JPY — opens Sunday 22:00 UTC
        "EURUSD",   # EUR/USD — opens Sunday 22:00 UTC
    ],

}


# ======================================================================================================================
# CFTC Contract Codes for COT Data
# Used to query the CFTC Commitment of Traders API.
# ======================================================================================================================

CFTC_CODES = {
    "XAUUSD":  "088691",   # Gold   - COMMODITY EXCHANGE INC. (verified vs CFTC API 2026-06-07)
    "XAGUSD":  "084691",   # Silver - COMMODITY EXCHANGE INC. (verified vs CFTC API 2026-06-07)
    "OIL":     "067651",   # Crude Oil WTI
    "GBPUSD":  "096742",   # British Pound
    "AUDUSD":  "232741",   # Australian Dollar
    "USDJPY":  "097741",   # Japanese Yen
    "EURUSD":  "099741",   # Euro
    "SPX500":  "13874+",   # S&P 500
    "NASDAQ":  "20974+",   # NASDAQ 100
    # UK/international-developed COT proxy. CFTC has NO FTSE/UK index contract
    # (verified 2026-06-07), so the weekly COT report pairs MSCI EAFE positioning
    # (developed markets ex-US, which includes the UK) with the FTSE 100 price
    # trend. Not a tradeable instrument — used for COT context only.
    "MSCI_EAFE": "244041", # MSCI EAFE - ICE FUTURES U.S.
}


# ======================================================================================================================
# Risk & Trade Constants
# ======================================================================================================================

# Minimum risk/reward ratio — trades below this are not placed.
# 3:1 (user directive 2026-06-09 — "same as Richie Williams"). Raised from 2.5.
MIN_RISK_REWARD = 3.0

# HVF minimum R:R threshold — patterns below this are DEVELOPING (watchlist only, not traded).
# Intentionally aliased to MIN_RISK_REWARD so the two values are always in sync.
# Import HVF_MIN_RR from config wherever the HVF threshold is needed — do NOT
# define a local copy in price_action.py or run_hvf_report.py.
HVF_MIN_RR = MIN_RISK_REWARD

# Number of HVF X (Twitter) drafts posted per run, best→worst (user 2026-06-13).
# Stored here so the count is changed in ONE place without re-testing intraday_signals.
X_DRAFT_TOP_N = 20

# Max HVF setups listed in the daily report's TRADEABLE / DEVELOPING sections, shown in
# WEIGHT order (TRIGGERED first, then quality) — the full count still shows in the summary
# line. Keeps the report readable (user 2026-06-13: "too many setups, not in weight order").
HVF_REPORT_TOP_N = 20

# HVF liquidity quality penalty (user 2026-06-13): illiquid names must NOT rank high on
# the list. Tiers of recent median DAILY turnover (Close × Volume, in GBP — ".L" prices
# are pence so turnover is ÷100 to pounds) → points subtracted from the pattern-quality
# score. Highest floor first; the first floor the turnover meets wins. Tune here freely —
# it only reorders the list, it never changes detection or the R:R tradeable gate.
HVF_LIQUIDITY_TIERS_GBP = [
    (10_000_000,   0),   # >= £10m/day  — fully liquid, no penalty
    ( 3_000_000, -10),   # £3m – £10m
    ( 1_000_000, -25),   # £1m – £3m
    (         0, -40),   # < £1m        — very thin (e.g. small investment trusts)
]

# Default take-profit distance as a multiple of the stop distance, used for
# NON-HVF trades and as the fallback when a setup has no measured-move target.
# 3:1 to match the R:R policy above (user directive 2026-06-09). The single
# source of truth — import DEFAULT_TARGET_RR; never hardcode "* 2"/"* 3".
DEFAULT_TARGET_RR = 3.0

# Spread width limit — trades blocked if spread exceeds this % of mid price
MAX_SPREAD_PCT = 0.005      # 0.5%

# Spread-to-stop ratio — trades blocked if spread > this multiple of stop distance
# IBM example: spread=65, stop=27.3 → ratio=2.38 (way above 0.5 threshold)
# If spread costs more than 50% of the stop, the trade is negative expectancy
MAX_SPREAD_TO_STOP_RATIO = 0.5

# Spread retry — when market is open but spread is temporarily wide,
# retry this many times with this many seconds between checks before giving up.
# Pre-market/closed markets are never retried.
# 15 × 20s ≈ 5 min of persistence (user 2026-06-09 — missed NBIS after only 3×30s).
# Wait kept at 20s so the worst case stays ~at the */5 monitor cadence and checks
# the (volatile) spread often enough to catch it narrowing. Monitor workflow
# timeouts raised to 15 min to fit. The next monitor run also re-scans + re-tries.
SPREAD_RETRY_ATTEMPTS  = 15
SPREAD_RETRY_WAIT_SECS = 20

# Session token TTL — IG sessions expire after 6 hours
# Refresh at 5.5 hours to avoid mid-session expiry
IG_SESSION_TTL_SECONDS = 5.5 * 3600

# Maximum trades per session (across all users) — legacy global fallback.
MAX_TRADES_PER_SESSION = 3

# Per-SESSION-GROUP daily trade caps (user 2026-06-09) — so one session (e.g. the
# Asia FX session) can't spend the whole day's budget and starve higher-conviction
# later setups. Keys match session_name.split("_")[0]: AUS / UK / US.
SESSION_TRADE_CAPS = {"AUS": 3, "UK": 3, "US": 4}

# Per-INSTRUMENT daily trade cap — stops over-concentration in one name
# (e.g. 3x USDJPY in a single Asia session, which ate the budget on 2026-06-09).
# Raised 2 → 5 (user 2026-06-12 — XAUUSD blocked at 2/2 with valid signals).
MAX_TRADES_PER_INSTRUMENT_PER_DAY = 5

# Trade-open email recipients (user 2026-06-09). Sent via Yahoo SMTP (trade_email.py,
# secrets YAHOO_USER / YAHOO_APP_PASSWORD) with the investment case + price/volume/HVF
# charts. Editable list — add addresses to email more people.
EMAIL_RECIPIENTS = ["eahind@yahoo.co.uk"]

# Economic calendar block window — no new trades within this many minutes
# of a high-impact event
CALENDAR_BLOCK_MINUTES = 30

# Macro gate thresholds
VIX_GATE_THRESHOLD          = 35.0   # VIX above this = gate fails
YIELD_SPREAD_GATE_THRESHOLD = -1.0   # Yield spread below this = gate fails

# Intraday stress gate thresholds (SPX % change from yesterday close)
SPX_HIGH_STRESS_PCT = -2.5   # SPX down >2.5% → gate FAILS (no new entries)
SPX_STRESS_PCT      = -1.0   # SPX down 1–2.5% → STRESS mode (position sizes halved)

# Intraday guard — falling-knife protection
# Blocks a trade if an instrument has already moved more than N × its 14-day ATR
# from today's open. 2.0× = genuinely violent/news-driven move.
# 1.5× was too sensitive (fires on normal strong trending days).
# 2.5× is too loose (NVDA -8% would still pass).
INTRADAY_GUARD_ATR_MULTIPLIER = 2.0

# ======================================================================================================================
# Price Action Confirmation Thresholds
# The PA score must reach ± threshold before a trade fires.
# Higher = more conservative. Lower = more sensitive.
#
# Root cause of missed crypto shorts on 2026-06-05:
#   ETHUSD had pa_score=-35 with 5 primary SELL signals and HVF TRIGGERED.
#   The original fixed ±40 threshold blocked it. Crypto moves violently
#   intraday without breaking weekly chart structure, so a lower threshold
#   is appropriate.
#
# Threshold lowered from 25 → 20 on 2026-06-06 after review:
#   BTCUSD had pa_score=+5 (mixed) when the bear signal fired — correctly WAIT.
#   ETHUSD had pa_score=-35 (strongly bearish) — SHOULD have been CONFIRM_SHORT.
#   The fix code was deployed but sessions this week ran before the deployment.
#   Lowering to 20 gives more headroom on crypto where PA naturally scores lower
#   because BTC/ETH rarely produce clean range breakouts (score +30) at entry time
#   — the trend structure and MA signals (-25 to -25) are the dominant components.
#
# HVF TRIGGERED bypass (in price_action.py):
#   When HVF signal is TRIGGERED in the same direction as the trade, the effective
#   threshold is halved (min 15). Price has already voted via the pattern trigger.
# ======================================================================================================================

PA_CONFIRM_THRESHOLD_DEFAULT = 40   # US/UK equities, indices — standard

PA_CONFIRM_THRESHOLDS = {
    # Crypto — violent intraday moves without weekly structure breaking.
    # Lowered to 20 on 2026-06-06: ETH -35 and BTC bear opportunity missed this
    # week because pa_score rarely reaches -25 on crypto without a range breakout
    # (which scores -30 but requires breaking a 60-day low — uncommon for BTC).
    "BTCUSD": 20,  "BITCOIN": 20,
    "ETHUSD": 20,
    "XRPUSD": 20,
    "SOLUSD": 20,
    "BNBUSD": 20,
    # Precious metals — fast-moving on macro news
    "XAUUSD": 30,  "GOLD": 30,  "SPOTGOLD": 30,
    "XAGUSD": 30,  "SILVER": 30,
    # Energy — macro / geopolitical driven
    "OIL": 35,  "USOIL": 35,  "CL": 35,
    # FX — slightly more sensitive than equities
    "GBPUSD": 35,  "AUDUSD": 35,  "USDJPY": 35,  "EURUSD": 35,
    "USDCAD": 35,  "USDCHF": 35,  "NZDUSD": 35,
    # All unlisted instruments use PA_CONFIRM_THRESHOLD_DEFAULT (40)
}

# Signal thresholds
# Design decision: "trade fires when gate passes + 1 primary + 1 confirmation"
# Original code had 2 — that requires options AND BB simultaneously which almost never aligns.
MIN_PRIMARY_SIGNALS       = 2   # 2 primaries required (HVF alone bypasses — see signals.py)
MIN_CONFIRMATION_SIGNALS  = 1   # Minimum confirmation signals required to trade
MIN_CALL_PUT_RATIO_BULL   = 1.2 # Call/put ratio above this = bullish options signal
MAX_CALL_PUT_RATIO_BEAR   = 0.8 # Call/put ratio below this = bearish options signal

# Senator scoring
MIN_SENATOR_TRADES = 5          # Minimum trades for a senator to qualify

# Superinvestor lookback window (days)
SUPERINVESTOR_LOOKBACK_DAYS = 90

# Director buy minimum cluster size
MIN_DIRECTOR_CLUSTER = 2        # 2+ Form 4 filings = cluster signal

# Social mention lookback (hours)
SOCIAL_MENTION_LOOKBACK_HOURS = 24
