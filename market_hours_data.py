# ======================================================================================================================
# File:         market_hours_data.py
# ----------------------------------------------------------------------------------------------------------------------
# GENERATED FILE - DO NOT EDIT BY HAND. Regenerate with `python market_hours.py --refresh`.
#
# Each exchange's regular session, derived from Yahoo's own `currentTradingPeriod.regular` for the sampled instruments
# named beside it. Times are LOCAL to the exchange; the UTC close is computed through `tz` so daylight saving follows
# automatically. A session of 00:00-23:59 means the venue trades continuously and has no closing bar.
#
# Generated: 2026-09-07 18:15 UTC
# Source:    yfinance history_metadata.currentTradingPeriod.regular
# Universe:  run_hvf_report.UNIVERSE (1856 tickers, 43 exchanges)
# ======================================================================================================================

EXCHANGES = {
    'INDEX:^AEX': {"tz": 'Europe/Amsterdam', "open": '09:00', "close": '17:30', "samples": ['^AEX'], "agreement": '1/1'},
    'INDEX:^AXJO': {"tz": 'Australia/Sydney', "open": '10:00', "close": '16:12', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['^AXJO'], "agreement": '1/1'},
    'INDEX:^BSESN': {"tz": 'Asia/Kolkata', "open": '09:15', "close": '15:30', "samples": ['^BSESN'], "agreement": '1/1'},
    'INDEX:^BVSP': {"tz": 'America/Sao_Paulo', "open": '10:00', "close": '17:00', "samples": ['^BVSP'], "agreement": '1/1'},
    'INDEX:^DJI': {"tz": 'America/New_York', "open": '09:30', "close": '16:00', "samples": ['^DJI'], "agreement": '1/1'},
    'INDEX:^FCHI': {"tz": 'Europe/Paris', "open": '09:00', "close": '17:30', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['^FCHI'], "agreement": '1/1'},
    'INDEX:^FTMC': {"tz": 'Europe/London', "open": '08:00', "close": '16:30', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['^FTMC'], "agreement": '1/1'},
    'INDEX:^FTSE': {"tz": 'Europe/London', "open": '08:00', "close": '16:30', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['UK100'], "agreement": '1/1'},
    'INDEX:^GDAXI': {"tz": 'Europe/Berlin', "open": '09:00', "close": '17:30', "samples": ['^GDAXI'], "agreement": '1/1'},
    'INDEX:^GSPC': {"tz": 'America/New_York', "open": '09:30', "close": '16:00', "vol_share_median": 0.798, "vol_share_p05": 0.783, "vol_share_sessions": 23, "samples": ['SPX500'], "agreement": '1/1'},
    'INDEX:^GSPTSE': {"tz": 'America/Toronto', "open": '09:30', "close": '16:00', "samples": ['^GSPTSE'], "agreement": '1/1'},
    'INDEX:^HSI': {"tz": 'Asia/Hong_Kong', "open": '09:30', "close": '16:10', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['HK50'], "agreement": '1/1'},
    'INDEX:^IBEX': {"tz": 'Europe/Madrid', "open": '09:00', "close": '17:30', "samples": ['^IBEX'], "agreement": '1/1'},
    'INDEX:^IXIC': {"tz": 'America/New_York', "open": '09:30', "close": '16:00', "vol_share_median": 0.877, "vol_share_p05": 0.866, "vol_share_sessions": 23, "samples": ['NASDAQ'], "agreement": '1/1'},
    'INDEX:^KS11': {"tz": 'Asia/Seoul', "open": '09:00', "close": '15:00', "samples": ['^KS11'], "agreement": '1/1'},
    'INDEX:^MXX': {"tz": 'America/Mexico_City', "open": '07:30', "close": '14:00', "samples": ['^MXX'], "agreement": '1/1'},
    'INDEX:^N225': {"tz": 'Asia/Tokyo', "open": '09:00', "close": '15:30', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['JPN225'], "agreement": '1/1'},
    'INDEX:^NSEI': {"tz": 'Asia/Kolkata', "open": '09:15', "close": '15:30', "vol_share_p05": None, "vol_share_reason": 'no traded volume reported for this exchange', "samples": ['^NSEI'], "agreement": '1/1'},
    'INDEX:^RUT': {"tz": 'America/New_York', "open": '09:30', "close": '16:00', "samples": ['^RUT'], "agreement": '1/1'},
    'INDEX:^SSMI': {"tz": 'Europe/Zurich', "open": '09:00', "close": '17:30', "samples": ['^SSMI'], "agreement": '1/1'},
    'INDEX:^STI': {"tz": 'Asia/Singapore', "open": '09:00', "close": '17:00', "samples": ['^STI'], "agreement": '1/1'},
    'INDEX:^STOXX50E': {"tz": 'Europe/Zurich', "open": '09:00', "close": '17:30', "samples": ['^STOXX50E'], "agreement": '1/1'},
    'INDEX:^TWII': {"tz": 'Asia/Taipei', "open": '09:00', "close": '13:30', "samples": ['^TWII'], "agreement": '1/1'},
    'SUFFIX:(bare)': {"tz": 'America/New_York', "open": '09:30', "close": '16:00', "vol_share_median": 0.86, "vol_share_p05": 0.755, "vol_share_sessions": 46, "samples": ['AAPL', 'MSFT'], "agreement": '2/2'},
    'SUFFIX:-USD': {"tz": 'UTC', "open": '00:00', "close": '23:59', "samples": ['BTCUSD', 'ETHUSD'], "agreement": '2/2'},
    'SUFFIX:.AS': {"tz": 'Europe/Amsterdam', "open": '09:00', "close": '17:30', "vol_share_median": 0.881, "vol_share_p05": 0.817, "vol_share_sessions": 42, "samples": ['ASML.AS', 'PRX.AS'], "agreement": '2/2'},
    'SUFFIX:.AX': {"tz": 'Australia/Sydney', "open": '10:00', "close": '16:12', "vol_share_median": 0.58, "vol_share_p05": 0.494, "vol_share_sessions": 42, "samples": ['BHP.AX', 'CBA.AX'], "agreement": '2/2'},
    'SUFFIX:.BO': {"tz": 'Asia/Kolkata', "open": '09:15', "close": '15:30', "samples": ['RELIANCE.BO', 'TCS.BO'], "agreement": '2/2'},
    'SUFFIX:.BR': {"tz": 'Europe/Brussels', "open": '09:00', "close": '17:40', "vol_share_median": 0.334, "vol_share_p05": 0.077, "vol_share_sessions": 42, "samples": ['ABI.BR', 'KBC.BR'], "agreement": '2/2'},
    'SUFFIX:.DE': {"tz": 'Europe/Berlin', "open": '09:00', "close": '17:30', "vol_share_median": 0.868, "vol_share_p05": 0.793, "vol_share_sessions": 42, "samples": ['SAP.DE', 'SIE.DE'], "agreement": '2/2'},
    'SUFFIX:.HK': {"tz": 'Asia/Hong_Kong', "open": '09:30', "close": '16:10', "vol_share_median": 0.865, "vol_share_p05": 0.768, "vol_share_sessions": 42, "samples": ['0700.HK', '0941.HK'], "agreement": '2/2'},
    'SUFFIX:.IR': {"tz": 'Europe/Dublin', "open": '08:00', "close": '16:30', "samples": ['RYA.IR', 'KRX.IR'], "agreement": '2/2'},
    'SUFFIX:.L': {"tz": 'Europe/London', "open": '08:00', "close": '16:30', "vol_share_median": 0.922, "vol_share_p05": 0.819, "vol_share_sessions": 40, "samples": ['III.L', 'ADM.L'], "agreement": '2/2'},
    'SUFFIX:.LS': {"tz": 'Europe/Lisbon', "open": '09:00', "close": '17:30', "vol_share_median": 1.0, "vol_share_p05": 1.0, "vol_share_sessions": 42, "samples": ['EDP.LS', 'GALP.LS'], "agreement": '2/2'},
    'SUFFIX:.MI': {"tz": 'Europe/Rome', "open": '09:00', "close": '17:30', "samples": ['ENEL.MI', 'ISP.MI'], "agreement": '2/2'},
    'SUFFIX:.NS': {"tz": 'Asia/Kolkata', "open": '09:15', "close": '15:30', "samples": ['RELIANCE.NS', 'TCS.NS'], "agreement": '2/2'},
    'SUFFIX:.OL': {"tz": 'Europe/Oslo', "open": '09:00', "close": '16:20', "vol_share_median": 0.862, "vol_share_p05": 0.716, "vol_share_sessions": 42, "samples": ['EQNR.OL', 'DNB.OL'], "agreement": '2/2'},
    'SUFFIX:.PA': {"tz": 'Europe/Paris', "open": '09:00', "close": '17:30', "vol_share_median": 0.877, "vol_share_p05": 0.783, "vol_share_sessions": 42, "samples": ['MC.PA', 'OR.PA'], "agreement": '2/2'},
    'SUFFIX:.SS': {"tz": 'Asia/Shanghai', "open": '09:30', "close": '15:00', "samples": ['600519.SS', '601398.SS'], "agreement": '2/2'},
    'SUFFIX:.SZ': {"tz": 'Asia/Shanghai', "open": '09:30', "close": '15:00', "samples": ['000001.SZ', '000002.SZ'], "agreement": '2/2'},
    'SUFFIX:.T': {"tz": 'Asia/Tokyo', "open": '09:00', "close": '15:30', "vol_share_median": 0.911, "vol_share_p05": 0.855, "vol_share_sessions": 40, "samples": ['7203.T', '6758.T'], "agreement": '2/2'},
    'SUFFIX:=F': {"tz": 'America/New_York', "open": '00:00', "close": '23:59', "samples": ['XAUUSD', 'XAGUSD'], "agreement": '2/2'},
    'SUFFIX:=X': {"tz": 'Europe/London', "open": '00:00', "close": '23:59', "samples": ['USDJPY', 'GBPUSD'], "agreement": '2/2'},
}
