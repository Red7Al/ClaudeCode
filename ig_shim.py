# ======================================================================================================================
# File:         ig_shim.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# IG API execution shim for the EndToEndTrading system.
# Pure execution layer — contains NO trading decision logic.
# All decisions are made upstream by the Claude Cloud Routines.
#
# Responsibilities:
#   - IG API session management (authentication, token refresh)
#   - Opening and closing CFD positions via IG OTC API
#   - Circuit breaker enforcement (daily loss limit, max positions, spread)
#   - Epic lookup with Supabase cache and IG search fallback
#   - Trade and P&L logging to Supabase
#   - Trailing stop updates
#   - Health check / connectivity verification
#
# All credentials are loaded exclusively from environment variables.
# No credentials are ever written to this file or any log.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.23.0  2026-07-03  Alex Hind   (user 2026-07-03, #11) Per-user live routing for the LOCAL web server: acting_session
#                                 (login) context manager swaps the module-global `session` to that login's own session
#                                 under _IG_LOCK for one operation (owner = no-op; non-owner w/o creds RAISES). /api/place-
#                                 order wraps placement in it; /api/positions read serialised under the same lock. Actions
#                                 monitors (separate process) unaffected. Remaining: per-login engine config + a real 2nd-
#                                 account demo test before enabling a second live trader.
# 1.22.0  2026-07-03  Alex Hind   (user 2026-07-03 "each user must use their own IG credentials") IGSession is now
#                                 credential-parameterizable (defaults to env creds — global singleton byte-identical);
#                                 _resolve_ig_creds(login) reads a login's own IG creds from web_users; session_for(login)
#                                 pools a per-login session and returns None for a non-owner with no creds (won't trade on
#                                 another account). Enabling layer only — live bridge/monitor routing + demo test still to do.
# 1.21.0  2026-07-03  Alex Hind   (user 2026-07-03, Config tab) Per-source trade-execution toggles: open_trade and
#                                 place_hvf_order_from_sig check config_store.monitor_enabled(session) — a source
#                                 switched off scans/reports but places nothing. Gate fails OPEN on config errors.
# 1.20.0  2026-06-29  Alex Hind   (user 2026-06-29) Bridge tuning: WO_PROXIMITY_PCT 1.0 -> 1.5 (place the IG working
#                                 order once price is within 1.5% of entry) and a new WO_MIN_QUALITY=50 floor in
#                                 place_hvf_order_from_sig — a setup only becomes a live IG order when Quality > 50.
# 1.19.0  2026-06-26  Alex Hind   (user 2026-06-26) New describe_size_skip() — THE single source for the size<=0 Slack
#                                 wording ("give a better explanation in slack"). Distinguishes a margin DEFICIT (free
#                                 balance < 0 → nothing trades until a position closes / funds added) from an IG-minimum
#                                 unaffordable instrument (needs ~£X free MARGIN — a deposit, not trade value) from a generic
#                                 zero-size (stop/epic). Keeps the needles notify classes on. Working-order path + run_session
#                                 both delegate here.
# 1.18.0  2026-06-26  Alex Hind   (user 2026-06-26) place_hvf_order_from_sig size<=0 (working-order path) now classifies via
#                                 LAST_SIZE_SKIP — a structural ACCOUNT_TOO_SMALL skip (e.g. IREN) reports the funding gap
#                                 and is summarised daily instead of paging #alerts every cycle, matching the run_session
#                                 paths. (Closes the BACKLOG "working-order size=0 still pages individually" item.) epic
#                                 pre-initialised so the lookup is NameError-safe.
# 1.17.0  2026-06-24  Alex Hind   (user 2026-06-24) get_epic Step 0: a ticker in _EPIC_VERIFIED_OVERRIDES now returns its
#                                 epic DIRECTLY (authoritative pin) — bypasses cache/IG-search/identity-check. Pinned 9
#                                 backlog tickers verified from a live IG /markets NAME lookup: PYPL/MSTR/SYM/FLY/FTAI/LUNR/
#                                 QBTS/RGTI/GLD (several can't be auto-resolved — a ticker search returns the wrong YieldMax/
#                                 Defiance ETF). Makes the real US equities tradeable; the 2 prior overrides (ETH/SPCX) now
#                                 also return directly (was: validate-on-cache-hit only).
# 1.16.0  2026-06-24  Alex Hind   (user 2026-06-24) calculate_position_size now records WHY it returned size 0 in the
#                                 module-level LAST_SIZE_SKIP[epic] = (reason, needed_margin, available): ACCOUNT_TOO_SMALL
#                                 (structural — min deal margin > available, e.g. crypto on a small account), or ERROR
#                                 (exception). Lets callers distinguish an EXPECTED unaffordable skip (summarise the funding
#                                 gap, don't page per cycle) from a genuine fixable size=0 (real missed trade -> alert).
#                                 Cleared on a successful size. No change to the size value or any trade decision.
# 1.15.0  2026-06-22  Alex Hind   (user 2026-06-22) (a) check_circuit_breakers(skip_spread=) — place_working_order (HVF path)
#                                 now skips the spread-width check ("an HVF-triggered order has no need to check spread").
#                                 (b) the price-outside-funnel / wrong-epic guards now alert SILENTLY (logged, not Slacked).
# 1.14.0  2026-06-19  Alex Hind   FIX wrong-instrument epic resolution (user 2026-06-19, AXP→AXP Energy). (1) y_name was
#                                 referenced but never assigned, so the identity guard NameError'd and never ran — now
#                                 computed via _yahoo_name (memoised). (2) get_epic now validates identity on the cache HIT
#                                 too (not just on miss): a cached epic whose IG name doesn't match the Yahoo company is
#                                 alerted, purged and re-resolved — never traded. _EPIC_VERIFIED_OVERRIDES whitelists pairs
#                                 the matcher can't confirm (spot ETH, SpaceX). Exchange is carried by the Yahoo ticker
#                                 (AXP=NYSE AmEx, AXP.AX=ASX AXP Energy), so both can be pinned to their own unique epic.
# 1.13.0  2026-06-15  Alex Hind   open_trade market pre-checks (Step 4) now FAIL CLOSED (user 2026-06-15). Previously a
#                                 try/except logged a warning and fell through to place the order with spread/stop/status
#                                 UNVERIFIED — how a 0.098%-stop AMD market order slipped past the 4d tight-stop guard.
#                                 An exception in 4a-4d now blocks the trade + alerts (fail-safe) instead of placing it.
# 1.12.0  2026-06-15  Alex Hind   Backlog #9b (behaviour): place_hvf_order_from_sig now skips a setup flagged
#                                 hvf_tight_stop_intraday SILENTLY (no missed-trade alert) — the funnel is structurally
#                                 untradeable at IG intraday (stop < config.TIGHT_STOP_MIN_PCT of price); the report still
#                                 shows it labelled. open_trade 4d guard repointed at config.TIGHT_STOP_MIN_PCT (single
#                                 source of truth). NB AMD 2026-06-15 was a CONFIRMATION-STACK trade (ATR stop), a separate
#                                 path from this HVF flag — the 4d fail-open weakness that let it through is logged separately.
# 1.0.0   2026-05-30  Alex Hind   Initial build
# 1.0.1   2026-05-30  Alex Hind   Fix expiry from "-" to "DFB" for rolling CFD contracts. Add get_close_reason() to
#                                 query IG activity history for STOP_HIT / TARGET_HIT etc. Add get_open_positions() 404
#                                 guard (no positions returns empty list, not an error).
# 1.0.2   2026-06-03  Alex Hind   check_circuit_breakers: retry Supabase user_profile query up to 2 times (2s delay)
#                                 before returning "User profile not found". Guards against transient connection drops
#                                 that return empty rows for a valid user_id (seen in production for KEEL 04-Jun-2026).
# 1.0.3   2026-06-05  Alex Hind   calculate_position_size: remove max(size, min_size) that forced size up to IG minimum
#                                 regardless of margin — caused IBM INSUFFICIENT_FUNDS (03-Jun-2026). Now skips trade
#                                 when calculated size < min_size. Exception fallback changed from 0.5 to 0.0 (safe).
# 1.1.0   2026-06-05  Alex Hind   calculate_position_size: when size < min_size, try IG minimum deal size if account
#                                 margin can support it (min_size × price × margin_factor ≤ available × 0.9). Only skip
#                                 when truly unaffordable. Fixes GOOGL and RIOT being missed. open_trade: resolve user
#                                 display name from user_profiles instead of hardcoded "Owner".
# 1.6.0   2026-06-10  Alex Hind   HVF setups now placed as IG WORKING ORDERS (user 2026-06-10: "HVF provides STOP, ENTRY
#                                 and EXIT — these should be ORDERS in IG"). New: place_working_order (pending
#                                 STOP/LIMIT entry at H3 with HVF stop+target attached, confirmed via /confirms),
#                                 update_working_order (re-signal = AMEND not duplicate), delete_working_order,
#                                 get_working_orders, reconcile_working_orders (fill → positions row so the monitor
#                                 manages closure; cancelled/ expired surfaced), place_hvf_order_from_sig (routing
#                                 helper). Caps fix: per-instrument/per-session counts now include positions opened
#                                 today (was trade_log only — trades still open did NOT count toward caps) and today's
#                                 PENDING working orders. detect_ig_scale: aligns Yahoo-unit HVF levels to IG points
#                                 (EURUSD 1.1539 → 11538.6 ×10⁴, JPY ×10² — verified live 2026-06-10); non-power-of-ten
#                                 mismatches still refused by the entry-distance guard. Live-tested: EURUSD far-from-
#                                 market LIMIT placed → ACCEPTED → reconciled → deleted.
# 1.11.1  2026-06-13  Alex Hind   calculate_position_size: added a guard comment documenting that spread-bet margin
#                                 has NO FX conversion (size × level × margin_factor = GBP directly), verified vs the
#                                 live account (RR.L £262.06 computed = £261.60 actual deposit). Considered adding
#                                 USD→GBP FX (user request) and REJECTED — would under-reserve ~23% and reintroduce
#                                 INSUFFICIENT_FUNDS. No behaviour change.
# 1.11.0  2026-06-12  Alex Hind   Wrong-instrument hardening after the ASX incident (ticker 'ASX' = ASE Technology on
#                                 Yahoo; best-scored IG match was ASX Ltd, the Australian exchange — wrong company
#                                 queued as WATCHING 54.2% from entry): (a) get_epic now verifies the chosen IG
#                                 instrument NAME shares a significant word with the Yahoo company name; mismatches are
#                                 overridden by a name-matching candidate or refused + alerted. (b) Cache keyed by the
#                                 ORIGINAL ticker — UK rows carry .L (migrated; bare keys collided: TSCO = Tesco AND
#                                 Tractor Supply); .L lookups never fall back to bare keys. (c) WATCHING queue rejects
#                                 setups where price sits beyond 1.2× the pattern's own stop distance — such patterns
#                                 are invalidated or mis-instrumented, nothing valid to watch. Cache audit purged 29
#                                 mismatched rows, migrated 15 UK keys; zero trades/orders were exposed.
# 1.10.0  2026-06-12  Alex Hind   (a) get_prices_df(): IG candles as DataFrame + remaining weekly allowance — IG is
#                                 the arbiter data source for UK pattern levels (user 2026-06-12). (b) get_epic() now
#                                 SCORES search results instead of taking the first: exact ticker in the epic body, DFB
#                                 preferred, .L tickers require the KA.D. UK-share family (search for 'LAND' returned
#                                 Gladstone Land Corp [US] first and Land Securities fifth — a wrong-instrument trade
#                                 risk; 3 bad cached mappings found and purged: LAND/TEM/SPX, none ever traded). A .L
#                                 ticker that cannot resolve to a UK epic is refused and alerted, never cached.
# 1.9.0   2026-06-11  Alex Hind   (Z) get_epic: strip Yahoo .L suffix before DB lookup so LAND.L → LAND matches EPIC_MAP
#                                 seeded entries. Tries normalized key first, then original, searches IG with
#                                 normalized, caches under normalized key. (B) open_trade: INSUFFICIENT_FUNDS retry —
#                                 halve size and resubmit once before alerting as missed. (C) open_trade: tight-stop
#                                 guard — skip trade when stop_distance < 0.5% of price AND price ≥ 500pt (GBX
#                                 equities); alerts as missed with explanation.
# 1.8.0   2026-06-11  Alex Hind   GBX (pence) conversion for US stocks quoted on IG UK. place_working_order now reads
#                                 instrument.currencies[0] .baseExchangeRate (pence per USD) and applies it to
#                                 Yahoo-derived HVF levels before the sanity guard. detect_ig_scale (power-of-ten FX
#                                 logic) unchanged and still handles EURUSD/USDJPY. Fixes RIOT/NVDA etc. rejecting with
#                                 "99.4% from current IG price".
# 1.7.0   2026-06-11  Alex Hind   Proximity band for working orders: orders placed only when price is within
#                                 WO_PROXIMITY_PCT (1%) of entry. Beyond that, logged as WATCHING (no capital
#                                 committed). reconcile_working_orders upgrades WATCHING→PENDING when price enters band;
#                                 cancels PENDING when price drifts beyond WO_CANCEL_BAND_PCT (2.5%) with Slack alert.
#                                 _promote_watching_order() places the live IG order at promotion time.
#                                 _get_pending_working_order now matches WATCHING rows to prevent duplicates.
# 1.6.0   2026-06-11  Alex Hind   Post-trade review: _post_trade_review() called from _log_trade_close_to_db after every
#                                 close. Checks R:R, stop tightness vs spread, and minimum meaningful risk (£). Posts a
#                                 GOOD / MARGINAL / POOR verdict to #alerts with specific flags so bad trade decisions
#                                 surface immediately. R:R now calculated from stored levels and included in the
#                                 trade_closed Slack notification.
# 1.5.0   2026-06-07  Alex Hind   CRITICAL safety fix: enforce the daily loss limit. check_circuit_breakers read
#                                 daily_loss_hit, but that flag was NEVER set anywhere → the daily loss limit was not
#                                 enforced at all. Now compares the day's realised P&L to daily_loss_limit% of account
#                                 balance, blocks the trade and persists daily_loss_hit on breach. (Found in deep-review
#                                 Pass 2. Basis = % of current balance — flag if a different basis is intended.)
# 1.4.0   2026-06-06  Alex Hind   Fix 3 bugs: (a) get_close_reason: removed unsafe fallback that returned the first
#                                 transaction with any non-zero open/close level — had zero deal_id check, so it logged
#                                 the wrong trade's close price; (b) update_stop: add ensure_authenticated() before PUT
#                                 — was using potentially stale token when called between health_check intervals; (c)
#                                 _log_trade_close: pnl_pct sign was wrong for SELL trades (subtracted in wrong
#                                 direction — profitable SELLs showed negative).
# 1.3.0   2026-06-06  Alex Hind   calculate_position_size: add optional available_funds parameter — callers that have
#                                 already fetched the balance can pass it directly, avoiding a duplicate IG API call and
#                                 preventing a race where a concurrent fill changes the available balance between the
#                                 two reads.
# 1.2.0   2026-06-06  Alex Hind   Add KN.D.* to US_EQUITY_PREFIXES — covers Canadian/US cross-listed names (e.g. KEEL
#                                 KN.D.BITFCN.DAILY.IP) that trade NYSE hours only. Previously missed hours guard —
#                                 could place trades outside NYSE window for these instruments.
# 1.0.4   2026-06-05  Alex Hind   open_trade paper trade path: return dict instead of bare string. Caller accessed
#                                 trade_result["level"] which crashed with TypeError on paper trades because string
#                                 indices must be integers. Now returns {"deal_id", "level": 0.0, "stop_level": 0.0,
#                                 "limit_level": 0.0} matching live trade format.
#
# Dependencies:
# ----------------------------------------------------------------------------------------------------------------------
#   pip install requests pg8000
#
# Environment Variables Required:
# ----------------------------------------------------------------------------------------------------------------------
#   IG_API_KEY            IG developer API key
#   IG_USERNAME           IG account username (not email)
#   IG_PASSWORD           IG account password
#   IG_ACCOUNT_ID         IG account reference (e.g. HTIRV)
#   SUPABASE_USER         Supabase PostgreSQL user (postgres.{project_id})
#   SUPABASE_DB_PASSWORD  Supabase database password
# ======================================================================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import math
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import pg8000.native

from config import (
    EPIC_MAP,
    ATR_MULTIPLIERS,
    ATR_MULTIPLIER_DEFAULT,
    MAX_SPREAD_PCT,
    MAX_SPREAD_TO_STOP_RATIO,
    TIGHT_STOP_MIN_PCT,
    SPREAD_RETRY_ATTEMPTS,
    SPREAD_RETRY_WAIT_SECS,
    IG_SESSION_TTL_SECONDS,
    MIN_RISK_REWARD,
    DEFAULT_TARGET_RR,
    SESSION_TRADE_CAPS,
    MAX_TRADES_PER_INSTRUMENT_PER_DAY,
    MAX_TRADES_PER_SESSION,
)


# ======================================================================================================================
# Logging
# ======================================================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("ig_shim")


# ======================================================================================================================
# Configuration — loaded from environment variables only
# ======================================================================================================================

IG_API_KEY    = os.environ["IG_API_KEY"]
IG_USERNAME   = os.environ["IG_USERNAME"]
IG_PASSWORD   = os.environ["IG_PASSWORD"]
IG_ACCOUNT_ID = os.environ["IG_ACCOUNT_ID"]
IG_BASE_URL   = "https://api.ig.com/gateway/deal"

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]         # format: postgres.{project_id}
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]


# ======================================================================================================================
# Supabase connection factory
# Returns a new connection on each call — caller must close after use.
# ======================================================================================================================

def get_db() -> pg8000.native.Connection:
    """Supabase connection via the shared resilient session-pooler helper (timeout + retry)."""
    from db_pool import get_db as _pool_get_db
    return _pool_get_db()


# ======================================================================================================================
# IG Session Management
# Handles authentication, token storage, and auto-refresh before 6hr expiry.
# ======================================================================================================================

class IGSession:
    """
    Manages a single authenticated IG API session.
    Automatically re-authenticates before the 6-hour session expiry.
    Used as a singleton — one session per process.
    """

    # IG sessions expire after 6 hours — refresh at 5.5 hours to be safe
    SESSION_TTL = IG_SESSION_TTL_SECONDS

    def __init__(self, api_key: str = None, username: str = None, password: str = None,
                 account_id: str = None, login: str = None):
        # Per-user IG credentials (user 2026-07-03 — "each user must use their own IG credentials").
        # Defaults to the process env creds, so the existing global singleton is byte-for-byte unchanged.
        self._api_key    = api_key    or IG_API_KEY
        self._username   = username   or IG_USERNAME
        self._password   = password   or IG_PASSWORD
        self._account_id = account_id or IG_ACCOUNT_ID
        self._login      = login                       # which web login this session belongs to (or None=global)
        self._token: Optional[str] = None
        self._cst: Optional[str] = None
        self._authenticated_at: Optional[float] = None

    # ------------------------------------------------------------------------------------------------------------------

    def _headers(self, version: str = "2") -> dict:
        """Build standard IG API request headers, including auth tokens if available."""
        h = {
            "Content-Type": "application/json",
            "Accept":       "application/json; charset=UTF-8",
            "X-IG-API-KEY": self._api_key,
            "Version":      version,
        }
        if self._token:
            h["X-SECURITY-TOKEN"] = self._token
            h["CST"]              = self._cst
        return h

    # ------------------------------------------------------------------------------------------------------------------

    def authenticate(self):
        """Authenticate with IG and store session tokens."""
        log.info(f"Authenticating with IG API...{f' (login={self._login})' if self._login else ''}")
        resp = requests.post(
            f"{IG_BASE_URL}/session",
            headers=self._headers("2"),
            json={
                "identifier":        self._username,
                "password":          self._password,
                "encryptedPassword": False
            },
            timeout=15
        )
        resp.raise_for_status()
        self._token              = resp.headers["X-SECURITY-TOKEN"]
        self._cst                = resp.headers["CST"]
        self._authenticated_at   = time.time()
        log.info("IG authentication successful")

    # ------------------------------------------------------------------------------------------------------------------

    def ensure_authenticated(self):
        """Authenticate if not yet done, or refresh if session is approaching expiry."""
        if self._token is None:
            self.authenticate()
            return
        if time.time() - self._authenticated_at > self.SESSION_TTL:
            log.info("Session approaching expiry — refreshing...")
            self.authenticate()

    # ------------------------------------------------------------------------------------------------------------------

    def get(self, path: str, version: str = "1", params: dict = None) -> dict:
        """Authenticated GET request to the IG API."""
        self.ensure_authenticated()
        resp = requests.get(
            f"{IG_BASE_URL}{path}",
            headers=self._headers(version),
            params=params,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------------------------------------------------------

    def post(self, path: str, body: dict, version: str = "1") -> dict:
        """Authenticated POST request to the IG API."""
        self.ensure_authenticated()
        resp = requests.post(
            f"{IG_BASE_URL}{path}",
            headers=self._headers(version),
            json=body,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------------------------------------------------------

    def delete(self, path: str, body: dict, version: str = "1") -> dict:
        """
        Authenticated DELETE request to the IG API.
        IG does not support HTTP DELETE directly — uses POST with _method header.
        """
        self.ensure_authenticated()
        h = self._headers(version)
        h["_method"] = "DELETE"
        resp = requests.post(
            f"{IG_BASE_URL}{path}",
            headers=h,
            json=body,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------------------------------------------------------
# Singleton session instance — shared across all calls in this process
# ----------------------------------------------------------------------------------------------------------------------
session = IGSession()


# ----------------------------------------------------------------------------------------------------------------------
# Per-user IG credentials (user 2026-07-03 — "each user must use their own IG credentials — imperative")
# ----------------------------------------------------------------------------------------------------------------------
_OWNER_LOGIN = "Alex"          # owner's stored IG creds == process env creds (verified byte-identical)
_SESSION_POOL: dict = {}       # login -> IGSession (lazy, kept for the process lifetime)


def _resolve_ig_creds(login: str):
    """Return that web login's own IG credentials. The app's encrypted store is the source of truth
    (seeded once from GitHub Secrets, then editable); the owner falls back to process env creds when
    the store isn't populated (e.g. inside GitHub Actions, which has no local store). Returns None for
    a non-owner who hasn't supplied their own creds — they must not trade on anyone else's account."""
    try:
        from hvf_web import web_users as _wu
        api = _wu.get_secret(login or _OWNER_LOGIN, "ig_api_key")
        usr = _wu.get_secret(login or _OWNER_LOGIN, "ig_username")
        pwd = _wu.get_secret(login or _OWNER_LOGIN, "ig_password")
        acct = _wu.get_secret(login or _OWNER_LOGIN, "ig_account_id")
        if api and usr and pwd:
            return {"api_key": api, "username": usr, "password": pwd, "account_id": acct or IG_ACCOUNT_ID}
    except Exception as e:
        log.warning(f"IG credential lookup failed for {login}: {e}")
    if not login or login == _OWNER_LOGIN:
        return {"api_key": IG_API_KEY, "username": IG_USERNAME,
                "password": IG_PASSWORD, "account_id": IG_ACCOUNT_ID}   # owner env fallback
    return None


def session_for(login: str = None) -> Optional["IGSession"]:
    """Pooled IGSession authenticated with THAT login's own IG credentials. Returns None when a
    non-owner login has not supplied their own credentials — the caller must then NOT trade on
    anyone else's account (imperative). Owner/None returns the shared global session."""
    if not login or login == _OWNER_LOGIN:
        return session
    if login in _SESSION_POOL:
        return _SESSION_POOL[login]
    creds = _resolve_ig_creds(login)
    if not creds:
        log.warning(f"{login} has no IG credentials of their own — no session created (will not trade).")
        return None
    s = IGSession(api_key=creds["api_key"], username=creds["username"],
                  password=creds["password"], account_id=creds["account_id"], login=login)
    _SESSION_POOL[login] = s
    return s


# All local (web-server) IG access is serialised through this lock so the module-global `session`
# can be temporarily swapped to a specific user's session for the duration of one operation without
# leaking to a concurrent request/thread (user 2026-07-03, per-user live routing). The GitHub-Actions
# monitors run in a separate process with only the global session, so they're unaffected.
import threading as _threading
_IG_LOCK = _threading.RLock()


import contextlib as _contextlib


@_contextlib.contextmanager
def acting_session(login: str = None):
    """Run an IG operation as `login`: swap the module-global `session` to that login's own session
    for the duration, under _IG_LOCK. Owner/None is a no-op swap (already the global). Raises if a
    non-owner has no credentials of their own — so an order is NEVER placed on someone else's account."""
    global session
    s = session_for(login)
    if s is None:
        raise RuntimeError(f"{login} has no IG credentials of their own")
    with _IG_LOCK:
        orig = session
        session = s
        try:
            yield s
        finally:
            session = orig


# ======================================================================================================================
# Account
# ======================================================================================================================

# Records WHY the last size calc returned 0 for an epic, so callers can resolve the right response
# (user 2026-06-24). ACCOUNT_TOO_SMALL = the account cannot meet IG's minimum deal margin for this
# instrument (a STANDING fact for the current balance — e.g. crypto minimums vs a small account), to be
# summarised with its funding gap, not paged every cycle. ERROR = an exception (genuine missed trade).
# Keyed by epic; (reason, needed_margin_gbp, available_gbp). Cleared when a real size is produced.
LAST_SIZE_SKIP: dict = {}


def calculate_position_size(epic: str, stop_distance: float,
                            risk_amount: float,
                            available_funds: float = None) -> tuple[float, float]:
    """
    Calculate position size that satisfies BOTH risk management AND IG margin.

    Returns (size, adjusted_stop_distance).

    Two constraints:
      1. Risk constraint:  size × stop_distance = risk_amount  (2% rule)
      2. Margin constraint: size × price × margin_factor ≤ available_funds

    Uses the tighter of the two. If the resulting size is below IG's minimum
    deal size, the function tries the minimum deal size — but ONLY if the
    account has enough margin to support it. If not, returns (0.0, stop_distance).

    Why not just force min_size always?
      Forcing max(size, min_size) without checking margin caused IBM (IBM Corp)
      to be submitted at size=1.0 when margin only supported ~0.24, producing
      an INSUFFICIENT_FUNDS rejection from IG (2026-06-03). The margin check
      here is what separates a safe fallback from that failure.
    """
    try:
        mkt      = session.get(f"/markets/{epic}", version="3")
        snap     = mkt.get("snapshot", {})
        inst     = mkt.get("instrument", {})
        rules    = mkt.get("dealingRules", {})

        price    = float(snap.get("offer", 0) or snap.get("bid", 0) or 1)
        margin_factor = float(inst.get("marginFactor", 20)) / 100.0
        min_size = float((rules.get("minDealSize") or {}).get("value", 0.04))
        min_stop = float((rules.get("minNormalStopOrLimitDistance") or {}).get("value", 0))

        # Enforce minimum stop distance
        if min_stop > 0 and stop_distance < min_stop:
            stop_distance = round(min_stop * 1.05, 4)

        # Risk-based size
        risk_size = risk_amount / stop_distance if stop_distance > 0 else 0

        # Margin-based size: how much stake can we afford given available funds?
        # Use the caller-supplied balance when available — avoids a redundant
        # IG API call when the caller already fetched the balance to compute
        # risk_amount (e.g. run_monitor rescan calls get_account_balance() once
        # to derive risk_amount; passing that value here saves a second call and
        # prevents a race where a concurrent fill changes available between the
        # two reads, causing the margin check to use a stale balance).
        if available_funds is not None:
            available = available_funds
        else:
            try:
                available = get_account_balance()["available"]
            except Exception:
                available = 0
        # ⚠️ DO NOT add an FX conversion here. SPREAD-BET margin = size × level ×
        # margin_factor, in GBP DIRECTLY — the £/point stake already encodes the
        # GBP exposure, so the underlying currency (USD for DELL, GBX for RR.L)
        # and the pence/cents quoting are all absorbed by the £/point convention.
        # Verified empirically against the live account 2026-06-13: RR.L size 1.0
        # @ level 1310.3 @ 20% → computed £262.06, and IG's actual held margin
        # (account deposit) was £261.60 — a match with no FX. Applying the USD→GBP
        # rate (≈0.77) would UNDER-reserve ~23% and bring INSUFFICIENT_FUNDS
        # rejections / margin-call risk straight back. (Considered + rejected
        # 2026-06-13.)
        margin_size = (available * 0.9) / (price * margin_factor) if price > 0 else 0

        # Use the tighter of the two constraints — do NOT blindly force up to min_size.
        # See docstring for why the naive max(size, min_size) approach was dangerous.
        size = round(min(risk_size, margin_size), 2)

        if size < min_size:
            # Calculated size is below IG's minimum deal size.
            # Before giving up, check whether the account can actually afford the
            # minimum deal size from a margin perspective.
            # Margin cost of minimum deal = min_size × price × margin_factor
            min_size_margin_cost = min_size * price * margin_factor

            if available > 0 and (available * 0.9) >= min_size_margin_cost:
                # Account CAN support the minimum deal — use it.
                # The trade will slightly exceed the configured risk_per_trade
                # (actual risk = min_size × stop_distance in currency terms),
                # but this is preferable to missing a valid signal entirely.
                actual_risk_currency = round(min_size * stop_distance, 2)
                actual_risk_pct      = round(actual_risk_currency / available * 100, 2) if available > 0 else 0
                log.info(
                    f"{epic}: calculated size {size:.4f} below IG minimum {min_size} — "
                    f"using minimum deal size instead. "
                    f"Actual risk: {actual_risk_currency} ({actual_risk_pct:.2f}% of available). "
                    f"Margin cost: {min_size_margin_cost:.2f} vs available: {available:.2f}"
                )
                LAST_SIZE_SKIP.pop(epic, None)
                return min_size, stop_distance

            else:
                # Account cannot afford even the minimum deal margin — skip. STRUCTURAL, not
                # transient: it recurs every cycle until the balance rises or the instrument is
                # removed from the universe. Record the funding gap so the caller summarises it
                # (once/day, with the gap) rather than paging an un-actionable alert each cycle.
                log.warning(
                    f"{epic}: calculated size {size:.4f} below IG minimum {min_size} "
                    f"AND minimum margin cost ({min_size_margin_cost:.2f}) exceeds "
                    f"available funds ({available:.2f}) — skipping trade"
                )
                LAST_SIZE_SKIP[epic] = ("ACCOUNT_TOO_SMALL",
                                        round(min_size_margin_cost, 2), round(available, 2))
                return 0.0, stop_distance

        log.info(f"{epic}: size={size} (risk={risk_size:.3f} margin={margin_size:.3f}) "
                 f"stop={stop_distance} margin_factor={margin_factor*100:.0f}%")
        LAST_SIZE_SKIP.pop(epic, None)
        return size, stop_distance

    except Exception as e:
        log.warning(f"Position size calculation failed for {epic}: {e}")
        LAST_SIZE_SKIP[epic] = ("ERROR", None, None)
        return 0.0, stop_distance   # skip trade on error — 0.5 fallback was unsafe


def describe_size_skip(ticker: str, epic: str) -> str:
    """Plain-English Slack explanation for a size<=0 skip, from LAST_SIZE_SKIP (user 2026-06-26:
    "give a better explanation in slack"). THE single source for the wording, used by every size<=0
    path. Each ACCOUNT_TOO_SMALL branch keeps the literal "account too small" so notify still classes
    it ACCOUNT_TOO_SMALL (silenced -> daily summary); the generic branch keeps "calculated size is 0"
    so it classes SIZE_ZERO (paged). Distinguishes three cases:
      • margin DEFICIT (free balance < 0)  • IG-minimum unaffordable  • generic zero-size."""
    skip = LAST_SIZE_SKIP.get(epic or "")
    if skip and skip[0] == "ACCOUNT_TOO_SMALL":
        needed, have = skip[1], skip[2]
        if isinstance(have, (int, float)) and have < 0:
            return (f"{ticker}: account too small — in fact in MARGIN DEFICIT (only £{have:.2f} free). "
                    f"Open positions are using more margin than the balance, so NO new trade can be "
                    f"placed on ANY instrument until it recovers. Fix: close a position or add funds.")
        _n = f"£{needed:.2f}" if isinstance(needed, (int, float)) else "the IG minimum"
        _h = f"£{have:.2f}"   if isinstance(have, (int, float)) else "the free balance"
        return (f"{ticker}: account too small for IG's minimum deal. Its smallest allowed position "
                f"(0.01) needs ~{_n} of FREE MARGIN (a deposit to hold the trade, not the trade's "
                f"value), but only {_h} is free. This is IG's minimum bet size, not our 2% risk rule, "
                f"and recurs every scan until the balance covers it. Fix: fund above {_n} (+ a "
                f"buffer), or remove {ticker} from the trade universe (publishing is unaffected).")
    return (f"{ticker}: calculated size is 0 — most likely a zero/garbage stop distance or a wrong/404 "
            f"epic (a units mismatch can inflate the stop so the risk-based size collapses to ~0), not "
            f"pure affordability. Review the stop distance and the epic.")


def get_account_balance() -> dict:
    """
    Return balance details for the configured IG account.
    Raises ValueError if the account ID is not found in the response.
    """
    data = session.get("/accounts", version="1")
    for acct in data.get("accounts", []):
        if acct["accountId"] == IG_ACCOUNT_ID:
            return {
                "balance":     acct["balance"]["balance"],
                "available":   acct["balance"]["available"],
                "profit_loss": acct["balance"]["profitLoss"],
                "deposit":     acct["balance"]["deposit"],
                "currency":    acct["currency"],
            }
    raise ValueError(f"Account {IG_ACCOUNT_ID} not found in IG response")


def get_account_info() -> dict:
    """Name + id of the account the CURRENT session is acting on (user 2026-07-20, IG Account tab header).
    Uses the acting session's own account id (so it is correct for non-owner users under acting_session),
    falling back to the 'preferred' account. Best-effort — returns {} on any failure so the page still
    renders. The caller is responsible for obfuscating the id before sending it to the browser."""
    try:
        acct_id = getattr(session, "_account_id", None) or IG_ACCOUNT_ID
        accts = (session.get("/accounts", version="1") or {}).get("accounts", []) or []
        match = next((a for a in accts if str(a.get("accountId")) == str(acct_id)), None)
        if match is None:
            match = next((a for a in accts if a.get("preferred")), accts[0] if accts else None)
        if match:
            return {"account_id":   str(match.get("accountId") or ""),
                    "account_name": match.get("accountName") or ""}
    except Exception:
        pass
    return {}


# ======================================================================================================================
# Epic Lookup
# Checks Supabase cache first. Falls back to IG market search on cache miss.
# New epics found via search are written back to the cache automatically.
# ======================================================================================================================

def instrument_names_match(ticker: str, ig_name: str, yahoo_name: str) -> bool:
    """
    True when an IG instrument name and a Yahoo company name plausibly refer to
    the SAME company for this ticker. Shared by get_epic verification and the
    nightly identity sweep so the rule cannot drift. Matches on:
    - any significant word in common ("Land Securities Group PLC" <-> "Land Securities")
    - the ticker itself appearing as a word in either name ("IBM Corp" <-> ticker IBM)
    - the acronym of either name equalling the ticker ("International Business
      Machines" -> IBM; false-positive fix 2026-06-12)
    """
    _sw = {"plc", "the", "inc", "corp", "corporation", "group", "ltd", "limited",
           "holdings", "holding", "trust", "ord", "and", "of", "co", "24",
           "hours", "adr", "sa", "nv", "se", "ag", "class"}

    def words(s):
        return [w for w in (s or "").lower().replace(".", " ").replace(",", " ")
                .replace("(", " ").replace(")", " ").split() if w not in _sw]

    iw, yw = words(ig_name), words(yahoo_name)
    sig_i = {w for w in iw if len(w) > 2}
    sig_y = {w for w in yw if len(w) > 2}
    if sig_i and sig_y and (sig_i & sig_y):
        return True
    # Ticker/acronym checks use the YAHOO side only — the IG candidate being
    # validated may be literally named after the ticker while being the wrong
    # company (ASX Ltd vs ticker ASX = ASE Technology; caught in unit checks).
    t = ticker.lower().replace(".l", "").replace("-", "")
    if t in yw and t in iw:
        return True   # both names carry the ticker word (IBM Corp <-> IBM ...)
    if len(yw) >= 2 and "".join(w[0] for w in yw) == t and t in iw:
        return True   # Yahoo acronym == ticker and IG carries it (Intl Business Machines -> IBM Corp)
    return False


def _name_search_term(yahoo_name: str) -> str:
    """A clean IG search term from a Yahoo company name — legal-suffix stopwords dropped, first few
    significant words kept ('Astellas Pharma Inc.' -> 'Astellas Pharma'). Used as a fallback when the
    ticker itself can't be resolved at IG (numeric-coded foreign tickers, e.g. Tokyo .T / Shenzhen .SZ,
    which IG lists by NAME, not by their local code). User 2026-08-01."""
    _sw = {"plc", "the", "inc", "corp", "corporation", "group", "ltd", "limited", "holdings", "holding",
           "trust", "ord", "and", "of", "co", "adr", "sa", "nv", "se", "ag", "class", "company", "co.,"}
    ws = [w for w in (yahoo_name or "").lower().replace(".", " ").replace(",", " ").split()
          if w not in _sw and len(w) > 1]
    return " ".join(ws[:3])


# Search-term aliases (user 2026-07-03): some index tickers can't be found by their raw symbol at IG.
# get_epic tries these name terms in order when the ticker matches. e.g. ^NSEI: try "India 50" then "NIFTY".
_SEARCH_TERM_ALIASES = {
    "^NSEI": ["India 50", "NIFTY"],
}

# Heuristic Yahoo->IG epic mapping (user 2026-07-04). FX "<PAIR>=X" maps to CS.D.<PAIR>.CFD.IP;
# indices use IG's known IX.D.* epics. UNLIKE the human-verified pins below, each heuristic pin is
# VALIDATED against IG (/markets/{epic} must answer) the first time it's used — on failure we fall
# through to the normal search, so a wrong guess can never route to the wrong instrument.
def _fx_heuristics():
    m = {}
    for pair in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
                 "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY", "CADJPY", "CHFJPY",
                 "NZDJPY", "EURAUD", "GBPAUD", "EURCAD", "GBPCAD", "AUDNZD", "AUDCAD",
                 "EURNZD", "USDSGD", "USDHKD", "USDNOK", "USDSEK", "USDMXN", "USDZAR",
                 "USDCNY", "USDINR", "USDTRY", "USDPLN"):
        m[pair] = m[pair + "=X"] = f"CS.D.{pair}.CFD.IP"
    return m


_EPIC_HEURISTIC_PINS = {
    **_fx_heuristics(),
    # Indices (user-supplied + IG-standard epics)
    "^GSPC": "IX.D.SPTRD.CASH.IP", "SPX500": "IX.D.SPTRD.CASH.IP",     # S&P 500
    "^DJI": "IX.D.DOW.DAILY.IP",                                        # Dow
    "^IXIC": "IX.D.NASDAQ.CASH.IP", "NASDAQ": "IX.D.NASDAQ.CASH.IP",   # Nasdaq
    "^FTSE": "IX.D.FTSE.CASH.IP", "UK100": "IX.D.FTSE.CASH.IP",        # FTSE 100
    "^GDAXI": "IX.D.DAX.CASH.IP",                                       # DAX
    "^FCHI": "IX.D.CAC.CASH.IP",                                        # CAC 40
    "JPN225": "IX.D.NIKKEI.CASH.IP", "^N225": "IX.D.NIKKEI.CASH.IP",   # Nikkei
    "HK50": "IX.D.HANGSENG.CASH.IP", "^HSI": "IX.D.HANGSENG.CASH.IP",  # Hang Seng
    "^AXJO": "IX.D.ASX.CASH.IP",                                        # ASX 200
    "^STOXX50E": "IX.D.STXE.CASH.IP",                                   # Euro Stoxx 50
}
_HEURISTIC_VALIDATED: dict = {}    # epic -> True/False after the one-time IG check


def _heuristic_epic(ticker: str):
    """Return a VALIDATED heuristic epic for the ticker, or None (then normal search applies)."""
    epic = _EPIC_HEURISTIC_PINS.get(ticker)
    if not epic:
        return None
    if epic in _HEURISTIC_VALIDATED:
        return epic if _HEURISTIC_VALIDATED[epic] else None
    try:
        session.get(f"/markets/{epic}", version="3")
        _HEURISTIC_VALIDATED[epic] = True
        log.info(f"Heuristic epic validated: {ticker} -> {epic}")
        return epic
    except Exception as e:
        _HEURISTIC_VALIDATED[epic] = False
        log.warning(f"Heuristic epic REJECTED by IG ({ticker} -> {epic}): {e} — falling back to search")
        return None


_EPIC_VERIFIED_OVERRIDES = {
    # ticker -> epic that the name-matcher CANNOT confirm but a human has verified correct
    # (user 2026-06-19). get_epic returns these DIRECTLY (Step 0) — authoritative pins.
    "ETH":  "CS.D.ETHUSD.TODAY.IP",   # spot Ether; Yahoo 'ETH' returns a Grayscale ETF name
    "SPCX": "UD.D.SPCXUS.DAILY.IP",   # SpaceX vs 'Space Exploration Technologies Corp.'
    # Pinned 2026-06-24 (user) from a verified IG /markets name lookup. Several CANNOT be
    # auto-resolved — a ticker search returns the wrong instrument (e.g. "PYPL" -> a YieldMax ETF,
    # "QBTS"/"RGTI" -> Defiance 2x-short ETFs), the real equity only surfaces on a NAME search.
    "PYPL": "UC.D.PYPLVUS.DAILY.IP",  # PayPal Holdings Inc (24 Hours)   — NOT the YieldMax PYPL ETF
    "MSTR": "UC.D.MSTR.DAILY.IP",     # Strategy Inc (ex-MicroStrategy)  — NOT the Morningstar AU ETF
    "SYM":  "UD.D.SVFCUS.DAILY.IP",   # Symbotic Inc                     — NOT Symphony/Symrise
    "FLY":  "UB.D.FLYUS.DAILY.IP",    # Firefly Aerospace Inc
    "FTAI": "SC.D.FTAIUS.DAILY.IP",   # FTAI Aviation (IG: Fortress Transportation & Infrastructure)
    "LUNR": "UB.D.LUNRUS.DAILY.IP",   # Intuitive Machines Inc
    "QBTS": "SH.D.XPOAUUS.DAILY.IP",  # D-Wave Quantum Inc               — NOT the Defiance 2x short
    "RGTI": "SG.D.SNIIUS.DAILY.IP",   # Rigetti Computing Inc            — NOT the Defiance 2x short
    "GLD":  "SI.D.GLDUS.DAILY.IP",    # SPDR Gold Shares (US)            — NOT NewGold (JSE)
}


def _is_verified_epic(ticker: str, epic: str) -> bool:
    return _EPIC_VERIFIED_OVERRIDES.get(ticker) == epic


_YNAME_CACHE: dict = {}


def _yahoo_name(ticker: str) -> str:
    """Yahoo company name for the EXACT ticker — used to verify an IG epic is the right
    instrument. Memoised per process (one yfinance call per ticker). '' on any failure, in
    which case callers skip name validation (fail-open to today's behaviour)."""
    if ticker in _YNAME_CACHE:
        return _YNAME_CACHE[ticker]
    name = ""
    try:
        import yfinance as _yf
        info = _yf.Ticker(ticker).info or {}
        name = info.get("longName") or info.get("shortName") or ""
    except Exception:
        name = ""
    _YNAME_CACHE[ticker] = name
    return name


def get_epic(ticker: str) -> Optional[str]:
    """
    Return the IG epic code for a given ticker symbol.

    Lookup order:
      1. Supabase epic_lookup table (fast, no API call)
      2. IG /markets?searchTerm= API (on cache miss, result cached for next time)

    Returns None if the instrument cannot be found.
    """

    # Normalize Yahoo Finance LSE suffix before lookup.
    # Yahoo appends .L for LSE stocks (LAND.L, BA..L, RR.L, BT-A.L).
    # EPIC_MAP and the DB are keyed without .L (LAND, BA., RR, BT-A).
    # Strip the trailing .L so the cache always hits the right key.
    normalized = ticker[:-2] if ticker.endswith('.L') and len(ticker) > 2 else ticker

    # Step 0 — human-verified PIN (user 2026-06-24). A ticker in _EPIC_VERIFIED_OVERRIDES has a
    # hand-verified epic; return it DIRECTLY, bypassing cache/IG-search/identity-check. This is what
    # makes a pin authoritative — essential for tickers the auto-resolver CANNOT find (a "PYPL" search
    # returns a YieldMax ETF, "QBTS"/"RGTI" return Defiance 2x-short ETFs; the real equity only
    # surfaces on a name search). Checked on both the raw and .L-normalised key.
    _pin = _EPIC_VERIFIED_OVERRIDES.get(ticker) or _EPIC_VERIFIED_OVERRIDES.get(normalized)
    if _pin:
        log.info(f"Epic pin (verified override): {ticker} → {_pin}")
        return _pin

    # Step 0b — heuristic Yahoo->IG mapping (FX =X pairs, indices), validated against IG on first use.
    _h = _heuristic_epic(ticker) or _heuristic_epic(normalized)
    if _h:
        return _h

    # Yahoo company name for identity checks. (Bug fix, user 2026-06-19: y_name was referenced
    # below and on the miss path but NEVER assigned — the wrong-instrument guard NameError'd, so
    # it never ran. That is how rows like AXP→AXP Energy persisted.)
    y_name = _yahoo_name(ticker)

    # Step 1 — Check Supabase cache. The cache key IS the original ticker:
    # UK rows are keyed WITH the .L suffix (migrated 2026-06-12) because bare
    # keys collide across markets — 'TSCO' is Tesco PLC in London and Tractor
    # Supply in New York; 'LAND' is Land Securities and Gladstone Land. A .L
    # ticker must therefore NEVER fall back to a bare-key row.
    db = get_db()
    try:
        rows = db.run("select epic, description from epic_lookup where ticker = :t", t=ticker)
    finally:
        db.close()
    if rows:
        cached_epic, cached_desc = rows[0][0], rows[0][1]
        # Validate identity on the cache HIT too (user 2026-06-19). Previously a hit was returned
        # unchecked, so stale wrong rows (AXP→AXP Energy, SPGI→Spain 35, TGT→11880 SA …) were
        # served and could be TRADED. Trust the row only when: Yahoo has no name (FX/index/
        # commodity), it's a verified override, or the IG and Yahoo names match.
        if (not y_name) or _is_verified_epic(ticker, cached_epic) \
                or instrument_names_match(ticker, cached_desc or "", y_name):
            log.info(f"Epic cache hit: {ticker} → {cached_epic}")
            return cached_epic
        # Mismatch → never trade the wrong instrument. Alert, purge the bad row, then re-resolve.
        log.error(f"get_epic {ticker}: cached epic {cached_epic} ({cached_desc!r}) != Yahoo company "
                  f"{y_name!r} — wrong-instrument; purging + re-resolving via IG.")
        try:
            from notify import alert_system_error
            alert_system_error("EPIC_LOOKUP", "get_epic",
                               f"{ticker}: cached epic {cached_epic} ({cached_desc}) does not match "
                               f"'{y_name}' — wrong instrument; purged and re-resolving.")
        except Exception:
            pass
        try:
            dbx = get_db()
            try:
                dbx.run("delete from epic_lookup where ticker = :t", t=ticker)
            finally:
                dbx.close()
        except Exception as e:
            log.warning(f"get_epic {ticker}: could not purge bad cache row: {e}")
        # fall through to Step 2 (IG search, now with a working identity guard)

    # Step 2 — Cache miss: search IG. Try any human-provided name aliases first (user 2026-07-03),
    # then the normalized ticker — the first term that returns markets wins.
    _terms = (_SEARCH_TERM_ALIASES.get(ticker) or _SEARCH_TERM_ALIASES.get(normalized) or []) + [normalized]
    markets = []
    for _term in _terms:
        log.info(f"Epic cache miss for {ticker} — searching IG markets (term='{_term}')...")
        data = session.get("/markets", params={"searchTerm": _term}, version="1")
        markets = data.get("markets", [])
        if markets:
            if _term != normalized:
                log.info(f"{ticker}: found via alias search term '{_term}'")
            break
    if not markets:
        log.warning(f"No IG market found for ticker: {ticker} (tried {_terms})")
        return None

    # ── Pick the RIGHT market, not the first one (user 2026-06-12: search for
    # 'LAND' returned Gladstone Land Corporation [US, UB.D.LANDUS] first, and
    # Land Securities [UK, KA.D.LAND] fifth — a .L ticker mapped to the WRONG
    # COMPANY, which would have traded the wrong instrument).
    # IG epic anatomy: <family>.D.<TICKER>.<expiry>.IP — UK shares are
    # KA.D.<TICKER>., US shares <fam>.D.<TICKER>US. Scoring: exact ticker
    # match in the epic body, DFB (no expiry) preferred; .L tickers strongly
    # prefer KA.D. and penalise US epics.
    is_uk = ticker.endswith(".L")

    def _epic_score(m):
        epic_str = m.get("epic", "")
        parts    = epic_str.split(".")
        mid      = parts[2] if len(parts) > 2 else ""
        s = 0
        if mid == normalized.replace(".", "").replace("-", ""):
            s += 4                      # exact ticker in epic body
        elif mid == normalized.replace(".", "").replace("-", "") + "US":
            s += 2 if not is_uk else -8 # US listing of the same ticker
        if m.get("expiry") == "DFB":
            s += 2                      # rolling daily bet, what we trade
        if is_uk and epic_str.startswith("KA.D."):
            s += 8                      # UK share family
        return s

    best = max(markets, key=_epic_score)

    # ── Identity verification (ASX 2026-06-12: ticker 'ASX' = ASE Technology
    # on Yahoo but the search's best score was ASX Ltd, the Australian
    # exchange — wrong company queued 54% from entry; same class as
    # LAND→Gladstone and SNOW→SnowWorld). The IG instrument NAME must share
    # at least one significant word with the Yahoo company name for this
    # ticker. If the best-scored candidate fails, prefer the best candidate
    # that DOES match; if none match, refuse + alert rather than guess.
    # Skipped when Yahoo has no name (FX/indices/commodities tickers).
    if y_name:
        if not instrument_names_match(ticker, best.get("instrumentName", ""), y_name):
            matching = [m for m in markets
                        if instrument_names_match(ticker, m.get("instrumentName", ""), y_name)]
            if matching:
                best = max(matching, key=_epic_score)
                log.warning(f"get_epic {ticker}: best-scored epic was a different company — "
                            f"overridden by name match → {best.get('epic')} "
                            f"({best.get('instrumentName')})")
            else:
                # Fallback — retry the IG search by COMPANY NAME (user 2026-08-01). The ticker-based
                # search returned no name match; this is normal for numeric-coded foreign tickers (Tokyo
                # .T, Shenzhen .SZ …) that IG doesn't index by their local code but DOES list by name
                # ('4503.T' finds nothing; 'Astellas Pharma' finds the epic). Still gated by
                # instrument_names_match, so there is no wrong-instrument risk.
                name_term = _name_search_term(y_name)
                name_markets = []
                if name_term and name_term.lower() != normalized.lower():
                    try:
                        log.info(f"get_epic {ticker}: no ticker-name match — retrying IG search by name "
                                 f"'{name_term}'")
                        name_markets = session.get("/markets", params={"searchTerm": name_term},
                                                   version="1").get("markets", [])
                    except Exception as e:
                        log.warning(f"get_epic {ticker}: name search '{name_term}' failed: {e}")
                name_match = [m for m in name_markets
                              if instrument_names_match(ticker, m.get("instrumentName", ""), y_name)]
                if name_match:
                    markets = name_markets           # so the downstream UK-epic check sees the right set
                    best = max(name_match, key=_epic_score)
                    log.info(f"get_epic {ticker}: resolved via company-name search '{name_term}' → "
                             f"{best.get('epic')} ({best.get('instrumentName')})")
                else:
                    try:
                        from notify import alert_system_error
                        alert_system_error("EPIC_LOOKUP", "get_epic",
                                           f"{ticker} ({y_name}): no IG search result matches this "
                                           f"company's name (by ticker OR by name) — wrong-instrument "
                                           f"risk, NOT cached, instrument skipped.",
                                           detail=str([(m.get('epic'), m.get('instrumentName'))
                                                       for m in markets[:6]]))
                    except Exception:
                        pass
                    log.error(f"get_epic {ticker}: no candidate matches Yahoo name '{y_name}' "
                              f"(ticker + name search) — refusing")
                    return None

    if is_uk and not best.get("epic", "").startswith("KA.D."):
        # A .L ticker that cannot resolve to a UK share epic is a wrong-
        # instrument risk — surface loudly and refuse to cache it.
        try:
            from notify import alert_system_error
            alert_system_error("EPIC_LOOKUP", "get_epic",
                               f"UK ticker {ticker} resolved to non-UK epic "
                               f"{best.get('epic')} ({best.get('instrumentName')}) — NOT cached, "
                               f"instrument skipped. Verify manually.",
                               detail=str([m.get('epic') for m in markets[:6]]))
        except Exception:
            pass
        log.error(f"get_epic {ticker}: best match {best.get('epic')} is not a UK share epic — refusing")
        return None

    epic        = best["epic"]
    description = best.get("instrumentName", "")
    market_type = best.get("instrumentType", "")

    # Step 3 — Write back to Supabase cache keyed on the normalized ticker
    db = get_db()
    try:
        db.run(
            """insert into epic_lookup (ticker, epic, description, market_type)
               values (:v_ticker, :v_epic, :v_desc, :v_mtype)
               on conflict (ticker) do update
               set epic=excluded.epic, last_seen=now()""",
            v_ticker=ticker, v_epic=epic, v_desc=description, v_mtype=market_type   # key = ORIGINAL ticker (.L kept)
        )
        log.info(f"Epic cached: {ticker} → {epic}")
    finally:
        db.close()

    return epic


# ======================================================================================================================
# Open Positions
# ======================================================================================================================

def get_open_positions() -> list:
    """
    Return all currently open OTC positions for the account.
    Returns an empty list if no positions exist (404 from IG is not an error).
    """
    try:
        data = session.get("/positions/otc", version="2")
        return data.get("positions", [])
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return []   # No open positions — normal condition
        raise


def get_position_by_deal(deal_id: str) -> Optional[dict]:
    """Return a single open position dict by deal ID, or None if not found."""
    for pos in get_open_positions():
        if pos["position"]["dealId"] == deal_id:
            return pos
    return None


def get_close_reason(deal_id: str) -> tuple[str, float]:
    """
    Query the IG activity history to determine why a position was closed.

    Returns (reason, close_level) where:
        reason:      STOP_HIT | TARGET_HIT | MANUAL | SYSTEM | UNKNOWN
        close_level: actual closing price from IG activity (0.0 if not found)
    """
    try:
        data = session.get(
            "/history/activity",
            version="3",
            params={"from": "2020-01-01T00:00:00", "detailed": True, "dealId": deal_id}
        )
        activities = data.get("activities", [])

        for act in activities:
            channel     = act.get("channel", "").upper()
            act_type    = act.get("type", "").upper()
            close_level = float(act.get("level", 0) or 0)
            actions     = act.get("details", {}).get("actions", [])

            # Check action types for stop/limit triggers
            for action in actions:
                action_type = action.get("actionType", "").upper()
                if "STOP" in action_type:
                    return "STOP_HIT", close_level
                if "LIMIT" in action_type or "PROFIT" in action_type:
                    return "TARGET_HIT", close_level

            # Fall back to channel / type
            if channel in ("DEALER", "SYSTEM", "CLOSE"):
                return "SYSTEM", close_level
            if act_type == "CLOSE":
                return "MANUAL", close_level

    except Exception as e:
        log.warning(f"Could not determine close reason for {deal_id}: {e}")

    # Fallback: try transaction history for the close price
    try:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        txn_data = session.get("/history/transactions", version="2",
                               params={"type": "ALL", "from": since})
        for txn in txn_data.get("transactions", []):
            ref = txn.get("reference", "")
            if deal_id in str(txn.get("reference", "")) or deal_id in str(txn.get("dealId", "")):
                close_level = float(str(txn.get("closeLevel", 0) or 0).replace(",", ""))
                log.info(f"Close price from transaction history: {close_level}")
                return "SYSTEM", close_level
        # No fallback by price-level match — that path had zero identity check
        # and would return the first transaction with any non-zero open/close,
        # silently logging the wrong trade's close price.
    except Exception as e:
        log.warning(f"Transaction history fallback failed: {e}")

    return "UNKNOWN", 0.0


# ======================================================================================================================
# Circuit Breakers
# Enforced before every trade attempt. Blocks trades that violate risk rules.
# ======================================================================================================================

def check_circuit_breakers(user_id: str, ticker: str, session_name: str = None,
                           skip_spread: bool = False) -> tuple[bool, str]:
    """
    Run all circuit breaker checks for a user before placing a trade.

    Checks:
        1. Daily loss limit — has the user hit their daily loss limit today?
        2. Max open positions — is the user already at their position limit?
        3. Spread width — is the current spread abnormally wide (> 0.5% of mid)?
           SKIPPED when skip_spread=True (user 2026-06-22: "if an order is placed due to an HVF
           trigger there is no need to check price spread"). HVF working orders are pending STOP
           orders at the pattern's own entry/stop/target, so the spread at scan time doesn't gate
           them — set by place_working_order (the HVF path).

    Returns:
        (True, "OK")           — trade is allowed
        (False, reason_string) — trade is blocked, reason explains why
    """

    # Retry up to 2 times on empty result or exception — guards against transient
    # Supabase connection drops that return no rows for a valid user_id.
    _MAX_RETRIES = 2
    _RETRY_DELAY = 2  # seconds
    rows = None
    for _attempt in range(1, _MAX_RETRIES + 2):  # attempts: 1, 2, 3
        db = get_db()
        try:
            rows = db.run(
                """select up.daily_loss_limit,
                          up.max_open_pos,
                          coalesce(dp.daily_loss_hit, false) as loss_hit,
                          coalesce(dp.total_pnl, 0)          as pnl,
                          (select count(*)
                           from   positions p
                           where  p.user_id    = up.id
                           and    p.paper_trade = up.paper_trade) as open_count
                   from   user_profiles up
                   left   join daily_pnl dp
                          on dp.user_id    = up.id
                          and dp.trade_date = current_date
                   where  up.id = :uid""",
                uid=user_id
            )
            if rows:
                break  # got a result — proceed
            log.warning(f"check_circuit_breakers: empty result for user {user_id} (attempt {_attempt})")
        except Exception as e:
            log.warning(f"check_circuit_breakers: DB error on attempt {_attempt}: {e}")
        finally:
            db.close()
        if _attempt <= _MAX_RETRIES:
            time.sleep(_RETRY_DELAY)

    if not rows:
        return False, "User profile not found in Supabase"

    daily_loss_limit, max_open_pos, loss_hit, total_pnl, open_count = rows[0]

    # Check 1 — daily loss limit
    # The daily_loss_hit flag is read here but was NEVER set anywhere in the
    # codebase, so the limit was effectively UNENFORCED — a user could exceed
    # their daily loss limit and keep trading. Enforce it directly: compare the
    # day's realised P&L to the limit (daily_loss_limit % of account balance) and
    # persist the flag on breach so the daily report + later opens short-circuit.
    if loss_hit:
        return False, "Daily loss limit already triggered for today"
    try:
        balance = float(get_account_balance().get("balance", 0) or 0)
        threshold = (float(daily_loss_limit) / 100.0) * balance   # daily_loss_limit is a %
        if threshold > 0 and float(total_pnl or 0) <= -threshold:
            try:
                db2 = get_db()
                db2.run("update daily_pnl set daily_loss_hit = true "
                        "where user_id = :uid and trade_date = current_date", uid=user_id)
                db2.close()
            except Exception:
                pass
            return False, (f"Daily loss limit reached: day P&L {float(total_pnl):.2f} "
                           f"<= -{threshold:.2f} ({daily_loss_limit}% of balance {balance:.2f})")
    except Exception as e:
        log.warning(f"Daily loss-limit compute failed for {user_id}: {e}")

    # Check 2 — max open positions
    if open_count >= max_open_pos:
        return False, f"Max open positions reached ({open_count}/{max_open_pos})"

    # Check 3 — spread width (skipped for HVF working orders — user 2026-06-22)
    if not skip_spread:
        try:
            epic = get_epic(ticker)
            if epic:
                market = session.get(f"/markets/{epic}", version="3")
                snap   = market.get("snapshot", {})
                bid    = snap.get("bid", 0)
                offer  = snap.get("offer", 0)
                if bid and offer:
                    spread = offer - bid
                    mid    = (bid + offer) / 2
                    if mid > 0 and (spread / mid) > MAX_SPREAD_PCT:
                        return False, f"Spread too wide: {spread:.4f} ({(spread/mid)*100:.2f}% of mid)"
        except Exception as e:
            log.warning(f"Spread check failed for {ticker}: {e}")

    # Checks 4 & 5 — per-instrument and per-session daily trade caps.
    # Stops one instrument (e.g. 3x USDJPY) or one session (Asia) consuming the
    # whole day's budget and starving higher-conviction later setups. One query.
    # Counts ALL of today's trade intents:
    #   trade_log       trades opened today and already closed
    #   positions       trades opened today and still open (was MISSING — caps
    #                   previously undercounted while positions stayed open)
    #   working_orders  pending HVF entry orders placed today (PENDING only —
    #                   FILLED ones already appear as positions/trade_log rows,
    #                   counting both would double-count one trade)
    try:
        grp = session_name.split("_")[0].upper() if session_name else None
        db = get_db()
        try:
            inst_n, sess_n = db.run(
                """select
                     (select count(*) from trade_log
                        where ticker = :t and date(opened_at) = current_date)
                   + (select count(*) from positions
                        where ticker = :t and date(opened_at) = current_date)
                   + (select count(*) from working_orders
                        where ticker = :t and status = 'PENDING'
                          and date(placed_at) = current_date),
                     (select count(*) from trade_log
                        where session like :g and date(opened_at) = current_date)
                   + (select count(*) from positions
                        where session like :g and date(opened_at) = current_date)
                   + (select count(*) from working_orders
                        where session like :g and status = 'PENDING'
                          and date(placed_at) = current_date)""",
                t=ticker, g=(grp + "%") if grp else "%"
            )[0]
        finally:
            db.close()
        if inst_n >= MAX_TRADES_PER_INSTRUMENT_PER_DAY:
            return False, (f"Per-instrument daily cap: {ticker} already traded "
                           f"{inst_n}/{MAX_TRADES_PER_INSTRUMENT_PER_DAY} today")
        if grp:
            cap = SESSION_TRADE_CAPS.get(grp, MAX_TRADES_PER_SESSION)
            if sess_n >= cap:
                return False, (f"Per-session daily cap: {grp} session already placed "
                               f"{sess_n}/{cap} today")
    except Exception as e:
        log.warning(f"Trade-cap check failed for {ticker}/{session_name}: {e}")
        # No silent failures (user directive): a broken cap check means caps are
        # NOT being enforced — surface it. (Trade still proceeds: blocking every
        # trade on a transient DB blip would silently starve valid signals.)
        try:
            from notify import alert_system_error
            alert_system_error(session=session_name or "TRADING", component="check_circuit_breakers",
                               summary=f"Trade-cap check failed for {ticker} — caps not enforced this attempt",
                               detail=str(e))
        except Exception:
            pass

    return True, "OK"


# ======================================================================================================================
# Open a Trade
# ======================================================================================================================

def open_trade(
    user_id:        str,
    ticker:         str,
    direction:      str,        # "BUY" or "SELL"
    size:           float,
    stop_distance:  float,      # distance in points from entry to stop loss
    limit_distance: float,      # distance in points from entry to take profit
    session_name:   str,        # e.g. "US_OPEN", "UK_OPEN"
    signal_summary: str,        # human-readable description of signals that fired
    paper_trade:    bool = False
) -> Optional[str]:
    """
    Open a CFD position on IG.

    Process:
        1. Run circuit breaker checks
        2. Resolve epic from ticker
        3. For paper trades: log to Supabase only, do not call IG
        4. For live trades: place market order, confirm deal, log to Supabase

    Returns:
        deal_id (str) on success
        None on failure or circuit breaker block
    """

    # Step 0 — Per-source execution toggle + trade filters (user 2026-07-03, Config tab). CHECKED
    # LIVE on every trade (fresh DB read — the config can change while a user is online). Fails OPEN
    # on any config error.
    try:
        from config_store import monitor_enabled, trade_allowed, location_of_ticker
        if not monitor_enabled(session_name):
            log.info(f"{ticker}: trade execution for {session_name} is switched OFF (Config) — trade not placed.")
            return None
        _ok, _why = trade_allowed(direction=("BULL" if direction == "BUY" else "BEAR"),
                                  location=location_of_ticker(ticker))
        if not _ok:
            log.info(f"{ticker}: blocked by trade filters (Config) — {_why}.")
            return None
    except Exception:
        pass

    # Step 1 — Circuit breakers (session_name enables per-session + per-instrument caps)
    ok, reason = check_circuit_breakers(user_id, ticker, session_name)
    if not ok:
        log.warning(f"Trade blocked — circuit breaker: {reason}")
        # Clear "tradeable signal not placed" alert — covers per-session/per-instrument
        # caps, daily-loss, max-positions, wide-spread (user 2026-06-09: make cap blocks
        # impossible to miss). Shows the reason + the signals that fired.
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, reason, signal_summary)
        except Exception as e:
            log.warning(f"Could not send missed-trade alert: {e}")
        return None

    # Step 2 — Resolve epic
    epic = get_epic(ticker)
    if not epic:
        log.error(f"Cannot trade {ticker} — no epic found")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction,
                               "No IG epic found — check epic_lookup / config.EPIC_MAP (a wrong "
                               "epic 404s and silently blocks the instrument)", signal_summary)
        except Exception:
            pass
        return None

    # Step 2b — Market hours guard for US equities
    # US equity CFDs (UA/UB/UC/UD/SE/SD/SB/SA/SG/SF/KN prefix) only trade during
    # NYSE hours: 14:30–21:00 UTC. Do not enter pre-market or after-hours —
    # spreads are wider and stops are prone to gap-slippage (proven: IBM today).
    # KN.D.* added 2026-06-06: covers Canadian/US cross-listed names (e.g. KEEL
    # Infrastructure Corp KN.D.BITFCN.DAILY.IP) that trade on NYSE hours only.
    US_EQUITY_PREFIXES = (
        "UA.D.", "UB.D.", "UC.D.", "UD.D.",   # standard US equity CFDs
        "SE.D.", "SD.D.", "SB.D.", "SA.D.",    # extended-hours / alternate prefix
        "SG.D.", "SF.D.", "SC.D.",
        "KN.D.",                               # Canadian/US cross-listed equities
    )
    if epic.startswith(US_EQUITY_PREFIXES):
        now_utc      = datetime.now(timezone.utc)
        market_open  = now_utc.replace(hour=14, minute=30, second=0, microsecond=0)
        market_close = now_utc.replace(hour=21, minute=0,  second=0, microsecond=0)
        if not (market_open <= now_utc <= market_close):
            log.info(
                f"Trade blocked — {ticker} ({epic}) is a US equity and the NYSE is closed "
                f"(current: {now_utc.strftime('%H:%M')} UTC, window: 14:30–21:00 UTC)"
            )
            return None

    # Step 3 — Paper trade: log only, skip IG
    if paper_trade:
        paper_id = f"PAPER-{int(time.time())}"
        log.info(f"[PAPER] {direction} {size} x {ticker} (epic={epic})")
        _log_position_to_db(
            user_id=user_id, epic=epic, ticker=ticker,
            direction=direction, size=size,
            open_price=0, stop_loss=0, take_profit=0,
            deal_id=paper_id,
            paper_trade=True, session_name=session_name,
            signal_summary=signal_summary
        )
        # Return a dict matching the live trade format so callers can treat
        # paper and live results identically. Previously returned a bare string
        # which caused TypeError when caller accessed trade_result["level"].
        return {"deal_id": paper_id, "level": 0.0, "stop_level": 0.0, "limit_level": 0.0}

    # Step 4 — Market checks: status, min stop, spread-to-stop ratio
    try:
        mkt     = session.get(f"/markets/{epic}", version="3")
        snap    = mkt.get("snapshot", {})
        rules   = mkt.get("dealingRules", {})
        inst    = mkt.get("instrument", {})

        # 4a — Market must be TRADEABLE
        mkt_status = snap.get("marketStatus", "TRADEABLE")
        if mkt_status != "TRADEABLE":
            reason = f"Market not tradeable (status: {mkt_status})"
            log.warning(reason)
            try:
                from notify import alert_missed_trade
                alert_missed_trade(ticker, direction, reason, signal_summary)
            except Exception:
                pass
            return None

        # 4b — Enforce IG minimum stop distance
        min_obj  = rules.get("minNormalStopOrLimitDistance", {})
        min_stop = float(min_obj.get("value", 0) or 0)
        if min_stop > 0 and stop_distance < min_stop:
            log.info(f"Stop distance {stop_distance} below IG minimum {min_stop} — adjusting")
            stop_distance  = round(min_stop * 1.05, 4)
            limit_distance = round(stop_distance * DEFAULT_TARGET_RR, 4)

        # 4c — Spread-to-stop ratio with retry
        # Spread must be < 50% of stop distance (negative expectancy otherwise).
        # If the market IS open but spread is temporarily wide (e.g. at the bell,
        # around news) retry up to SPREAD_RETRY_ATTEMPTS times before giving up.
        # Pre-market / closed markets are never retried — the monitor rescan
        # will re-evaluate the signal when the exchange opens.
        bid    = float(snap.get("bid",   0) or 0)
        offer  = float(snap.get("offer", 0) or 0)
        spread = offer - bid
        market_is_open = (mkt_status == "TRADEABLE")

        if spread > 0 and stop_distance > 0:
            ratio = spread / stop_distance
            if ratio > MAX_SPREAD_TO_STOP_RATIO:
                if market_is_open:
                    # Market is open — spread may narrow; retry a few times
                    for attempt in range(1, SPREAD_RETRY_ATTEMPTS + 1):
                        log.info(
                            f"{ticker}: spread {spread:.1f} is {ratio:.1f}x stop "
                            f"({stop_distance:.1f}) — waiting {SPREAD_RETRY_WAIT_SECS}s "
                            f"for spread to narrow (attempt {attempt}/{SPREAD_RETRY_ATTEMPTS})"
                        )
                        time.sleep(SPREAD_RETRY_WAIT_SECS)
                        snap2  = session.get(f"/markets/{epic}", version="3").get("snapshot", {})
                        bid    = float(snap2.get("bid",   0) or 0)
                        offer  = float(snap2.get("offer", 0) or 0)
                        spread = offer - bid
                        ratio  = spread / stop_distance if stop_distance > 0 else 999
                        log.info(f"{ticker}: spread now {spread:.1f} (ratio {ratio:.2f}x)")
                        if ratio <= MAX_SPREAD_TO_STOP_RATIO:
                            log.info(f"{ticker}: spread acceptable after {attempt} attempt(s) — proceeding")
                            break
                    else:
                        # All retries exhausted — spread still too wide
                        reason = (
                            f"Spread ({spread:.1f}) still {ratio:.1f}x stop ({stop_distance:.1f}) "
                            f"after {SPREAD_RETRY_ATTEMPTS} retries × {SPREAD_RETRY_WAIT_SECS}s — "
                            f"signal remains valid; monitor will re-evaluate"
                        )
                        log.warning(reason)
                        try:
                            from notify import alert_missed_trade
                            alert_missed_trade(ticker, direction, reason, signal_summary)
                        except Exception:
                            pass
                        return None
                else:
                    # Market closed / pre-market — no point retrying
                    reason = (
                        f"Spread ({spread:.1f}) is {ratio:.1f}x stop ({stop_distance:.1f}) "
                        f"and market is {mkt_status} — not retrying; "
                        f"monitor will re-evaluate when market opens"
                    )
                    log.warning(reason)
                    try:
                        from notify import alert_missed_trade
                        alert_missed_trade(ticker, direction, reason, signal_summary)
                    except Exception:
                        pass
                    return None

        # 4d — Tight-stop guard for expensive instruments (e.g. GBX-denominated UK equities).
        # A stop tighter than 0.5% of price on a ≥500pt instrument is hit by normal
        # tick movement within minutes (proven: SNDK 176054p stop 623p = 0.35%, 3-min
        # stop-out, -£19.20). The HVF stop may be valid for a daily timeframe but the
        # CFD spread + tick noise kills it intraday. Skip and alert.
        mid_price = (bid + offer) / 2 if bid > 0 and offer > 0 else offer or bid
        if mid_price >= 500 and stop_distance > 0:
            stop_pct = stop_distance / mid_price * 100
            if stop_pct < TIGHT_STOP_MIN_PCT:
                reason = (
                    f"Stop distance {stop_distance:.1f}pt is only {stop_pct:.2f}% of "
                    f"current price {mid_price:.1f}pt — minimum {TIGHT_STOP_MIN_PCT}% for instruments "
                    f"≥500pt. HVF stop too tight; pattern may need a wider L3 or "
                    f"more recent scan."
                )
                log.warning(reason)
                try:
                    from notify import alert_missed_trade
                    alert_missed_trade(ticker, direction, reason, signal_summary)
                except Exception:
                    pass
                return None

    except Exception as e:
        # FAIL CLOSED (was fail-open, user 2026-06-15). The market pre-checks (4a
        # tradeable status, 4b IG min-stop, 4c spread-to-stop, 4d tight-stop) are the
        # safety gates. If they can't COMPLETE, we have NOT verified the trade is safe
        # to place — so we must NOT place it. Previously this only logged a warning and
        # fell through to Step 5, placing an UNVERIFIED order: that is how a 0.098%-stop
        # AMD market order slipped past the 4d guard. Block + alert (visible, not silent).
        reason = (f"Market pre-checks could not complete ({type(e).__name__}: {e}) — "
                  f"trade NOT placed (fail-safe; spread/stop/status unverified)")
        log.error(f"{ticker}: {reason}")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, reason, signal_summary)
        except Exception:
            pass
        return None

    # Step 5 — Live trade: build and submit order
    body = {
        "epic":           epic,
        "direction":      direction,
        "size":           str(size),
        "orderType":      "MARKET",
        "timeInForce":    "FILL_OR_KILL",
        "guaranteedStop": False,
        "stopDistance":   str(stop_distance),
        "limitDistance":  str(limit_distance),
        "currencyCode":   "GBP",
        "expiry":         "DFB",    # Daily Funded Bet — correct expiry for rolling CFD contracts
        "forceOpen":      True,
    }

    log.info(
        f"Placing {direction} {size} x {ticker} (epic={epic}) | "
        f"stop={stop_distance} limit={limit_distance}"
    )

    try:
        # Place order
        resp     = session.post("/positions/otc", body=body, version="2")
        deal_ref = resp.get("dealReference")
        if not deal_ref:
            log.error(f"No deal reference returned by IG: {resp}")
            try:
                from notify import alert_missed_trade
                alert_missed_trade(ticker, direction, "IG returned no deal reference (order not accepted)", signal_summary)
            except Exception:
                pass
            return None

        # Confirm deal (wait briefly for IG to process)
        time.sleep(1)
        confirm    = session.get(f"/confirms/{deal_ref}", version="1")
        status     = confirm.get("dealStatus")
        deal_id    = confirm.get("dealId")
        level      = confirm.get("level", 0)
        stop_level = confirm.get("stopLevel", 0)
        limit_level = confirm.get("limitLevel", 0)

        if status != "ACCEPTED":
            reason_code = confirm.get("reason", "UNKNOWN")
            log.error(f"Deal rejected: {status} — {reason_code}")

            # INSUFFICIENT_FUNDS: retry once at half size before giving up.
            # Account balance may have changed since calculate_position_size ran
            # (e.g. a concurrent fill consumed margin). Halving avoids a hard skip.
            if reason_code == "INSUFFICIENT_FUNDS":
                retry_size = round(size / 2, 2)
                if retry_size >= 0.01:
                    log.warning(
                        f"{ticker}: INSUFFICIENT_FUNDS at size={size} — "
                        f"retrying at size={retry_size}"
                    )
                    body["size"] = str(retry_size)
                    try:
                        resp2     = session.post("/positions/otc", body=body, version="2")
                        deal_ref2 = resp2.get("dealReference")
                        if deal_ref2:
                            time.sleep(1)
                            confirm2   = session.get(f"/confirms/{deal_ref2}", version="1")
                            if confirm2.get("dealStatus") == "ACCEPTED":
                                # Retry succeeded — continue with the new confirm
                                deal_id     = confirm2.get("dealId")
                                level       = confirm2.get("level", 0)
                                stop_level  = confirm2.get("stopLevel", 0)
                                limit_level = confirm2.get("limitLevel", 0)
                                size        = retry_size
                                log.info(
                                    f"{ticker}: retry at size={retry_size} ACCEPTED — "
                                    f"deal={deal_id} level={level}"
                                )
                                # Fall through to the success path below
                                confirm = confirm2
                                status  = "ACCEPTED"
                    except Exception as retry_exc:
                        log.warning(f"{ticker}: INSUFFICIENT_FUNDS retry failed: {retry_exc}")

            if status != "ACCEPTED":
                try:
                    from notify import alert_system_error
                    alert_system_error(
                        session="IG_ORDER",
                        component="open_trade",
                        summary=f"Deal rejected for {ticker} {direction} — {reason_code}",
                        detail=f"epic={epic}  size={size}  stop={stop_distance}  limit={limit_distance}"
                    )
                except Exception as e:
                    log.warning(f"Could not send deal rejection alert: {e}")
                return None

        log.info(f"Deal confirmed: {deal_id} at level {level}  stop={stop_level}  limit={limit_level}")

        # Log confirmed position to Supabase
        _log_position_to_db(
            user_id=user_id, epic=epic, ticker=ticker,
            direction=direction, size=size,
            open_price=level, stop_loss=stop_level, take_profit=limit_level,
            deal_id=deal_id, paper_trade=paper_trade,
            session_name=session_name, signal_summary=signal_summary
        )
        return {
            "deal_id":     deal_id,
            "level":       float(level      or 0),
            "stop_level":  float(stop_level  or 0),
            "limit_level": float(limit_level or 0),
        }

    except requests.HTTPError as e:
        log.error(f"IG API error opening trade: {e.response.status_code} — {e.response.text}")
        return None


# ======================================================================================================================
# Close a Trade
# ======================================================================================================================

def close_trade(deal_id: str, reason: str = "MANUAL") -> bool:
    """
    Close an open CFD position by deal ID.

    If reason is "MANUAL", the actual close reason is determined by querying
    IG activity history (may return STOP_HIT, TARGET_HIT, SYSTEM, etc.).

    Returns True on successful close, False on failure.
    """

    # Find the open position
    pos = get_position_by_deal(deal_id)
    if not pos:
        log.warning(f"Position {deal_id} not found in open positions")
        return False

    epic      = pos["market"]["epic"]
    direction = pos["position"]["direction"]
    size      = pos["position"]["size"]
    close_dir = "SELL" if direction == "BUY" else "BUY"

    body = {
        "dealId":      deal_id,
        "epic":        epic,
        "direction":   close_dir,
        "size":        str(size),
        "orderType":   "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "expiry":      "DFB",
    }

    log.info(f"Closing position {deal_id} ({direction} {size} x {epic})")

    try:
        # Submit close request
        resp     = session.delete("/positions/otc", body=body, version="1")
        deal_ref = resp.get("dealReference")

        # Confirm closure
        time.sleep(1)
        confirm = session.get(f"/confirms/{deal_ref}", version="1")
        status  = confirm.get("dealStatus")

        if status != "ACCEPTED":
            log.error(f"Close rejected: {confirm.get('reason', 'UNKNOWN')}")
            return False

        close_price  = confirm.get("level", 0)
        close_reason = get_close_reason(deal_id)[0] if reason == "MANUAL" else reason
        log.info(f"Position {deal_id} closed at {close_price} — reason: {close_reason}")

        # Log closure to Supabase
        _log_trade_close_to_db(
            deal_id=deal_id,
            close_price=close_price,
            close_reason=close_reason
        )
        return True

    except requests.HTTPError as e:
        log.error(f"IG API error closing trade: {e.response.status_code} — {e.response.text}")
        return False


# ======================================================================================================================
# Price Data
# ======================================================================================================================

def get_prices(epic: str, resolution: str = "HOUR", count: int = 50) -> list:
    """
    Fetch OHLCV candle data for an epic.

    resolution options:
        SECOND, MINUTE, MINUTE_2, MINUTE_3, MINUTE_5, MINUTE_10,
        MINUTE_15, MINUTE_30, HOUR, HOUR_2, HOUR_3, HOUR_4, DAY, WEEK, MONTH
    """
    data = session.get(
        f"/prices/{epic}",
        version="3",
        params={"resolution": resolution, "max": count, "pageSize": count}
    )
    return data.get("prices", [])


def get_prices_df(epic: str, resolution: str = "DAY", count: int = 120):
    """
    Fetch IG candles as a pandas DataFrame (Open/High/Low/Close/Volume, date
    index) plus the remaining weekly historical-price allowance.

    IG broker data is the ARBITER for pattern levels (user 2026-06-12): Yahoo's
    LSE feed contains phantom prints (RR.L fake 1,420 high / 990 low) so UK HVF
    setups are re-validated against these candles before posting/trading.
    The weekly allowance is 10,000 data points (verified live 2026-06-12) —
    callers must budget via the returned remaining_allowance.

    Returns (df, remaining_allowance); (empty DataFrame, None) on failure.
    Prices are bid/ask midpoints, matching the scale Yahoo quotes (GBX for
    UK shares).
    """
    import pandas as pd
    try:
        data = session.get(
            f"/prices/{epic}",
            version="3",
            params={"resolution": resolution, "max": count, "pageSize": count}
        )
        remaining = (data.get("metadata", {}) or {}).get("allowance", {}).get("remainingAllowance")
        rows = []
        for c in data.get("prices", []):
            def _mid(k):
                b = (c.get(k) or {}).get("bid")
                a = (c.get(k) or {}).get("ask")
                if b is not None and a is not None:
                    return (b + a) / 2.0
                return b if b is not None else a
            o, h, l, cl = _mid("openPrice"), _mid("highPrice"), _mid("lowPrice"), _mid("closePrice")
            if None in (o, h, l, cl):
                continue
            rows.append({
                "Date":   pd.Timestamp(c.get("snapshotTime", "").replace("/", "-")[:10]),
                "Open":   o, "High": h, "Low": l, "Close": cl,
                "Volume": c.get("lastTradedVolume") or 0,
            })
        if not rows:
            return pd.DataFrame(), remaining
        df = pd.DataFrame(rows).set_index("Date").sort_index()
        log.info(f"IG prices {epic}: {len(df)} {resolution} candles "
                 f"(weekly allowance remaining: {remaining})")
        return df, remaining
    except Exception as e:
        log.warning(f"IG price fetch failed for {epic}: {e}")
        return pd.DataFrame(), None


def get_snapshot(epic: str) -> dict:
    """Return the current market snapshot (bid, offer, high, low, net change) for an epic."""
    data = session.get(f"/markets/{epic}", version="3")
    return data.get("snapshot", {})


# ======================================================================================================================
# Working Orders — HVF pending entries (user 2026-06-10)
#
# An HVF setup pre-defines ENTRY (H3 breakout), STOP and TARGET. Instead of a
# market order at whatever price the scan happens to see, we place a PENDING
# working order at the exact entry level with the stop and target attached, so
# the trade triggers precisely at the breakout.
#
# Lifecycle:  place_working_order → IG holds the order → reconcile_working_orders
#             (called by the monitors) detects FILLED (→ row inserted into the
#             positions table so the monitor manages closure normally) or
#             CANCELLED / EXPIRED (surfaced to Slack — nothing ends silently).
#
# CRITICAL:   a pending order is NOT a position. It must NEVER be written to the
#             positions table while pending — run_monitor would see it missing
#             from IG /positions and falsely record it as a closed trade. All
#             pending state lives in the dedicated working_orders table.
#
# Re-signals: the same funnel fires on every scan while it remains valid. A new
#             signal for a ticker with a PENDING order is treated as an UPDATE:
#             levels moved materially → amend the IG order in place (never a
#             second order); levels unchanged → skip silently.
# ======================================================================================================================

# Amend (rather than skip) a pending order when any of entry/stop/target moved
# by more than this percentage — re-scans recompute HVF pivots slightly as new
# bars arrive; sub-threshold jitter is noise, beyond it the setup truly moved.
WO_UPDATE_THRESHOLD_PCT = 0.25

# Working-order proximity band (2026-06-11):
#   WO_PROXIMITY_PCT   — only place a live IG order when price is within this %
#                        of the entry level. Further away → logged as WATCHING.
#   WO_CANCEL_BAND_PCT — cancel an existing PENDING order when price has drifted
#                        beyond this % from entry (capital no longer committed
#                        to a setup that is now remote).
WO_PROXIMITY_PCT   = 1.5    # user 2026-06-29: place the IG order once price is within 1.5% of entry
WO_CANCEL_BAND_PCT = 2.5
# Only promote a scanned setup to a live IG working order when its pattern Quality exceeds this
# (user 2026-06-29: "ONLY if Q is over 50"). Below the floor the funnel is shown in the report but
# never becomes an order.
WO_MIN_QUALITY = 50


def _round_level(value, decimals: int):
    """Round a price level to the market's quoted decimal places (IG rejects
    levels with more precision than the instrument's step, e.g. 149.089996)."""
    try:
        return round(float(value), max(0, int(decimals)))
    except (TypeError, ValueError):
        return value


def detect_ig_scale(current: float, level: float) -> float:
    """
    Factor mapping signal units (Yahoo) → IG units. Returns 1.0 when already aligned.

    Two cases:
      FX (EURUSD/USDJPY): IG quotes in points — clean power-of-ten ratio (±25%).
        EURUSD Yahoo 1.1539 vs IG 11538.6 (×10⁴), USDJPY 155.1 vs IG 15512 (×10²).
      US equities on IG UK: quoted in USD cents OR GBX (pence). Both produce a ratio
        in the 50–250 range (cents=100 exactly; pence≈78–130 depending on GBP/USD).
        A dedicated GBX path in place_working_order reads baseExchangeRate from the IG
        market snapshot when available; this fallback handles the cents case and acts
        as a safety net for GBX when baseExchangeRate is absent/zero.
    """
    try:
        if current <= 0 or level <= 0:
            return 1.0
        ratio = current / level
        if 0.2 < ratio < 5:
            return 1.0
        # US equities (cents) and GBX equities (pence): ratio 50–250 → scale ×100.
        # Tolerance spans USD cents (ratio=100) through GBX pence at any FX rate (≈78–130).
        if 50 < ratio < 250:
            return 100.0
        # FX/other: strict power-of-ten detection (±25% of 10^n).
        power = round(math.log10(ratio))
        scale = 10.0 ** power
        return scale if abs(ratio / scale - 1.0) <= 0.25 else 1.0
    except Exception:
        return 1.0


def get_working_orders() -> list:
    """Return all working orders on the account (GET /workingorders v2)."""
    try:
        data = session.get("/workingorders", version="2")
        return data.get("workingOrders", [])
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return []   # no working orders — normal condition
        raise


def _log_working_order_to_db(deal_ref, deal_id, user_id, ticker, epic, direction,
                             size, entry_level, stop_level, limit_level, otype,
                             hvf_type, good_till, paper_trade, session_name,
                             signal_summary, status="PENDING"):
    """Insert a new working-order record. status defaults to PENDING; pass WATCHING
    when price is not yet in range and no capital is committed. Never raises."""
    try:
        db = get_db()
        try:
            db.run(
                """insert into working_orders
                   (deal_ref, deal_id, user_id, ticker, epic, direction, size,
                    entry_level, stop_level, limit_level, otype, hvf_type, good_till,
                    status, paper_trade, session, signal_summary)
                   values (:v_ref, :v_deal, :v_uid, :v_ticker, :v_epic, :v_dir, :v_size,
                           :v_entry, :v_stop, :v_limit, :v_otype, :v_hvf, :v_till,
                           :v_status, :v_paper, :v_session, :v_signal)""",
                v_ref=deal_ref, v_deal=deal_id, v_uid=user_id, v_ticker=ticker,
                v_epic=epic, v_dir=direction, v_size=size, v_entry=entry_level,
                v_stop=stop_level, v_limit=limit_level, v_otype=otype, v_hvf=hvf_type,
                v_till=good_till, v_status=status, v_paper=paper_trade,
                v_session=session_name, v_signal=signal_summary
            )
            log.info(f"Working order logged to Supabase: {deal_id} ({ticker} {direction} @ {entry_level}) [{status}]")
        finally:
            db.close()
    except Exception as ex:
        log.error(f"Failed to log working order to Supabase: {ex}")


def _set_working_order_status(deal_id: str, status: str, fill_deal_id: str = None,
                              notes: str = None):
    """Update a working-order row's lifecycle status. Never raises."""
    try:
        db = get_db()
        try:
            db.run(
                """update working_orders
                   set status = :v_status, updated_at = now(),
                       filled_at    = case when :v_status = 'FILLED' then now() else filled_at end,
                       fill_deal_id = coalesce(:v_fill, fill_deal_id),
                       notes        = coalesce(:v_notes, notes)
                   where deal_id = :v_deal""",
                v_status=status, v_fill=fill_deal_id, v_notes=notes, v_deal=deal_id
            )
        finally:
            db.close()
    except Exception as ex:
        log.error(f"Failed to update working order {deal_id} → {status}: {ex}")


def _get_pending_working_order(ticker: str, user_id: str):
    """Return the most recent PENDING or WATCHING working-order row for ticker+user, or None.
    WATCHING rows have no capital committed but still block duplicate entries."""
    try:
        db = get_db()
        try:
            rows = db.run(
                """select deal_id, entry_level, stop_level, limit_level, direction,
                          otype, good_till, size, status
                   from   working_orders
                   where  ticker = :t and user_id = :u and status in ('PENDING','WATCHING')
                   order  by placed_at desc limit 1""",
                t=ticker, u=user_id
            )
        finally:
            db.close()
        if not rows:
            return None
        return {"deal_id": rows[0][0], "entry_level": float(rows[0][1] or 0),
                "stop_level": float(rows[0][2] or 0), "limit_level": float(rows[0][3] or 0),
                "direction": rows[0][4], "otype": rows[0][5], "good_till": rows[0][6],
                "size": float(rows[0][7] or 0), "status": rows[0][8]}
    except Exception as ex:
        log.warning(f"Pending working-order lookup failed for {ticker}: {ex}")
        return None


def _good_till_str(dt) -> str:
    """Format a datetime as IG's goodTillDate string (yyyy/MM/dd HH:mm:ss, UTC)."""
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def place_working_order(
    user_id:        str,
    ticker:         str,
    direction:      str,            # "BUY" or "SELL"
    size:           float,
    entry_level:    float,          # HVF H3 breakout level (bearish: L3)
    stop_level:     float,          # HVF stop (just beyond L3/H3)
    limit_level:    float,          # HVF target
    session_name:   str,
    signal_summary: str,
    paper_trade:    bool = False,
    good_till_days: int = None,     # None -> configured lifespan (config_store wo_lifespan_days, default 28)
    hvf_type:       str = None,
    max_entry_distance_pct: float = 0.90,   # sanity guard: entry vs current price (after unit conversion)
) -> Optional[dict]:
    """
    Place (or amend) a PENDING entry order on IG at the HVF level.

    Order type (IG decides fill behaviour by which side of market the level is):
        BUY  → "STOP"  if entry >= current offer (breakout buy-stop above market)
               "LIMIT" if entry <  current offer (price already past H3 — wait for
                        the pullback to the planned entry; never chase at market)
        SELL → mirrored.

    UPDATE-not-duplicate: if a PENDING order already exists for this ticker+user,
    the new signal AMENDS it when any level moved > WO_UPDATE_THRESHOLD_PCT
    (otherwise skips). Never places a second order for the same ticker.

    Returns dict {deal_id, deal_ref, level, stop_level, limit_level, otype,
    working_order: True, updated: bool} on success, None when blocked/rejected/skipped.
    """
    # Working-order lifespan (user 2026-07-03): configurable via config_store (default 28 days),
    # so it's the same for the local bridge and the GitHub-Actions monitors. Fails safe to 28.
    if good_till_days is None:
        try:
            from config_store import cfg_num
            good_till_days = int(cfg_num("wo_lifespan_days", 28))
        except Exception:
            good_till_days = 28

    # Step 0a — an open position on this ticker already carries the exposure;
    # do not stack a pending order on top of it.
    try:
        db = get_db()
        try:
            pos_rows = db.run(
                "select deal_id from positions where ticker = :t and user_id = :u limit 1",
                t=ticker, u=user_id)
        finally:
            db.close()
        if pos_rows:
            log.info(f"{ticker}: open position exists ({pos_rows[0][0]}) — not placing a working order")
            return None
    except Exception as e:
        log.warning(f"{ticker}: open-position dedup check failed: {e}")

    # Step 0b — UPDATE-not-duplicate (user 2026-06-10): re-signal for a ticker
    # with a PENDING order amends it (levels moved) or skips (unchanged). Runs
    # BEFORE circuit breakers — an amend adds no new exposure, so a full session
    # cap must not freeze an existing order on stale levels.
    existing = _get_pending_working_order(ticker, user_id)
    if existing:
        if existing["direction"] != direction:
            # Direction flipped (e.g. funnel re-classified) — replace the order.
            log.info(f"{ticker}: pending {existing['direction']} order exists but new signal is "
                     f"{direction} — deleting old order and placing fresh")
            delete_working_order(existing["deal_id"], reason=f"direction changed to {direction}")
        else:
            moved = max(
                abs(entry_level - existing["entry_level"]) / max(existing["entry_level"], 1e-9),
                abs((stop_level or 0) - existing["stop_level"]) / max(existing["stop_level"], 1e-9)
                    if existing["stop_level"] else 0,
                abs((limit_level or 0) - existing["limit_level"]) / max(existing["limit_level"], 1e-9)
                    if existing["limit_level"] else 0,
            ) * 100.0
            if moved <= WO_UPDATE_THRESHOLD_PCT:
                log.info(f"{ticker}: PENDING working order already at these levels "
                         f"(max move {moved:.3f}% <= {WO_UPDATE_THRESHOLD_PCT}%) — skipping (no duplicate)")
                return None
            log.info(f"{ticker}: HVF levels moved {moved:.2f}% — amending existing order "
                     f"{existing['deal_id']} instead of placing a new one")
            return update_working_order(
                existing["deal_id"], ticker, direction, entry_level, stop_level,
                limit_level, existing, session_name, user_id, paper_trade=paper_trade)

    # Step 1 — circuit breakers. NOT APPLIED to the HVF working-order path (user 2026-07-11):
    # circuit breakers gate the Multi-Factor Momentum trading only; HVF pending orders (a STOP at the
    # pattern's own entry/stop/target) are not gated by daily-loss/max-position/daily-cap checks.
    # (Was: check_circuit_breakers(user_id, ticker, session_name, skip_spread=True).)
    ok, reason = True, ""
    if not ok:
        log.warning(f"Working order blocked — circuit breaker: {reason}")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, f"[working order] {reason}", signal_summary)
        except Exception as e:
            log.warning(f"Could not send missed-trade alert: {e}")
        return None

    # Step 2 — resolve epic
    epic = get_epic(ticker)
    if not epic:
        log.error(f"Cannot place working order for {ticker} — no epic found")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction,
                               "[working order] No IG epic found — check epic_lookup / config.EPIC_MAP",
                               signal_summary)
        except Exception:
            pass
        return None

    # Step 3 — paper trades: log the pending order only, never call IG
    if paper_trade:
        paper_id  = f"PAPER-WO-{int(time.time())}"
        good_till = datetime.now(timezone.utc) + timedelta(days=good_till_days)
        log.info(f"[PAPER] working order {direction} {size} x {ticker} @ {entry_level} (epic={epic})")
        _log_working_order_to_db(paper_id, paper_id, user_id, ticker, epic, direction,
                                 size, entry_level, stop_level, limit_level, "STOP",
                                 hvf_type, good_till, True, session_name, signal_summary)
        return {"deal_id": paper_id, "deal_ref": paper_id, "level": entry_level,
                "stop_level": stop_level, "limit_level": limit_level, "otype": "STOP",
                "working_order": True, "updated": False}

    # Step 4 — market snapshot: current price, decimal precision, order-type choice
    try:
        mkt      = session.get(f"/markets/{epic}", version="3")
        snap     = mkt.get("snapshot", {})
        bid      = float(snap.get("bid", 0) or 0)
        offer    = float(snap.get("offer", 0) or 0)
        decimals = int(snap.get("decimalPlacesFactor", 2) or 2)
    except Exception as e:
        log.error(f"{ticker}: market snapshot failed — cannot place working order: {e}")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, f"[working order] market snapshot failed: {e}",
                               signal_summary)
        except Exception:
            pass
        return None

    current = offer if direction == "BUY" else bid
    if not current:
        current = (bid + offer) / 2 if (bid or offer) else 0
    if not current:
        log.error(f"{ticker}: no current price in snapshot — skipping working order")
        return None

    # Align signal units → IG units.
    # Two cases handled:
    #   1. FX power-of-ten (EURUSD Yahoo 1.1539 vs IG 11538.6 ×10⁴): detect_ig_scale
    #   2. USD stock quoted on IG in GBX (pence): IG returns instrument.currencies[].
    #      baseExchangeRate = pence per USD. Yahoo levels are USD; multiply by that
    #      rate. detect_ig_scale fails here because the ratio (≈78–170) is not a
    #      clean power-of-ten (it includes the live GBP/USD FX factor).
    ig_ccy = ""
    usd_to_ig = 1.0
    try:
        currencies = mkt.get("instrument", {}).get("currencies", [])
        if currencies:
            ig_ccy = currencies[0].get("code", "")
            if ig_ccy == "GBX":
                # baseExchangeRate: pence per 1 USD (e.g. ~127.x at GBP/USD 1.27)
                base_rate = float(currencies[0].get("baseExchangeRate", 0) or 0)
                if base_rate > 0:
                    usd_to_ig = base_rate
    except Exception:
        pass

    if usd_to_ig != 1.0:
        log.warning(f"{ticker}: GBX instrument — converting Yahoo USD levels ×{usd_to_ig:.2f} "
                    f"(IG price {current} vs raw entry {entry_level})")
        entry_level *= usd_to_ig
        stop_level  *= usd_to_ig
        limit_level *= usd_to_ig
    else:
        # FX instruments (EURUSD, USDJPY, etc.) — power-of-ten alignment
        scale = detect_ig_scale(current, entry_level)
        if scale != 1.0:
            log.warning(f"{ticker}: scaling HVF levels ×{scale:g} into IG units "
                        f"(IG price {current} vs signal entry {entry_level})")
            entry_level *= scale
            stop_level  *= scale
            limit_level *= scale

    entry_level = _round_level(entry_level, decimals)
    stop_level  = _round_level(stop_level,  decimals)
    limit_level = _round_level(limit_level, decimals)

    # Distance from current price — drives both the sanity guard and proximity band.
    dist_pct = abs(entry_level - current) / current * 100.0

    # Sanity guard — only fires if entry is >90% from live IG price AFTER unit
    # conversion. This catches a genuinely wrong epic (wrong instrument entirely)
    # or completely uncorrectable scale. Stale-but-valid setups (entry 10-90% from
    # current because the stock has moved since the HVF was computed) fall through
    # to the proximity band below, which queues them as WATCHING — they'll be
    # promoted when/if price returns to the entry. (Tests may override threshold.)
    if dist_pct > max_entry_distance_pct * 100.0:
        msg = (f"Entry {entry_level} is {dist_pct:.0f}% from live IG price {current} "
               f"after unit conversion — epic may be wrong or instrument has been renamed. "
               f"Order NOT placed.")
        log.error(f"{ticker}: {msg}")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, msg, signal_summary, silent=True)   # noise — log, don't Slack (user 2026-06-22)
        except Exception:
            pass
        return None

    # Pattern-invalidation guard (ASX 2026-06-12: a wrong-epic order queued
    # 54.2% from entry). A valid HVF has price INSIDE the funnel — never
    # further from the entry than the pattern's own stop distance. If price
    # sits beyond ~1.2× the stop distance, the pattern is already invalidated
    # (or the levels belong to a different instrument): nothing valid to watch.
    stop_dist_pct = abs(entry_level - stop_level) / current * 100.0 if current else 0.0
    max_watch_pct = max(WO_PROXIMITY_PCT, stop_dist_pct * 1.2)
    if dist_pct > max_watch_pct:
        msg = (f"Price is {dist_pct:.1f}% from entry {entry_level} but the pattern's own "
               f"stop is only {stop_dist_pct:.1f}% away — the setup is already invalidated "
               f"(price outside the funnel) or the levels belong to a different instrument. "
               f"NOT queued as WATCHING.")
        log.error(f"{ticker}: {msg}")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, msg, signal_summary, silent=True)   # noise — log, don't Slack (user 2026-06-22)
        except Exception:
            pass
        return None

    # Proximity band — only commit capital when price is close to the entry.
    # Beyond WO_PROXIMITY_PCT, log as WATCHING (no IG order placed, no margin
    # committed) and post a Slack alert. reconcile_working_orders will upgrade
    # the WATCHING row to PENDING once price enters the band.
    if dist_pct > WO_PROXIMITY_PCT:
        watch_id  = f"WATCH-{ticker}-{int(time.time())}"
        good_till = datetime.now(timezone.utc) + timedelta(days=good_till_days)
        _log_working_order_to_db(
            watch_id, watch_id, user_id, ticker, epic, direction,
            size, entry_level, stop_level, limit_level,
            "STOP" if direction == "BUY" else "STOP",   # placeholder — set at placement time
            hvf_type, good_till, paper_trade, session_name, signal_summary,
            status="WATCHING"
        )
        try:
            from notify import working_order_watching
            working_order_watching(ticker, direction, entry_level, stop_level,
                                   limit_level, dist_pct, WO_PROXIMITY_PCT, session_name)
        except Exception as ne:
            log.warning(f"Watching notification failed for {ticker}: {ne}")
        log.info(f"{ticker}: entry {entry_level} is {dist_pct:.2f}% away — logged as WATCHING "
                 f"(will place order when within {WO_PROXIMITY_PCT}%)")
        return {"deal_id": watch_id, "watching": True, "level": entry_level,
                "stop_level": stop_level, "limit_level": limit_level,
                "current_price": current, "working_order": True}

    # Level geometry must match the direction or IG will reject the order.
    if direction == "BUY" and not (stop_level < entry_level < limit_level):
        log.error(f"{ticker}: invalid BUY levels stop={stop_level} entry={entry_level} "
                  f"limit={limit_level} — skipping")
        return None
    if direction == "SELL" and not (limit_level < entry_level < stop_level):
        log.error(f"{ticker}: invalid SELL levels limit={limit_level} entry={entry_level} "
                  f"stop={stop_level} — skipping")
        return None

    if direction == "BUY":
        otype = "STOP" if entry_level >= current else "LIMIT"
    else:
        otype = "STOP" if entry_level <= current else "LIMIT"

    good_till = datetime.now(timezone.utc) + timedelta(days=good_till_days)

    body = {
        "epic":           epic,
        "direction":      direction,
        "size":           str(size),
        "level":          str(entry_level),
        "type":           otype,
        "timeInForce":    "GOOD_TILL_DATE",
        "goodTillDate":   _good_till_str(good_till),
        "guaranteedStop": False,
        "stopLevel":      str(stop_level),
        "limitLevel":     str(limit_level),
        "currencyCode":   "GBP",
        "expiry":         "DFB",
        "forceOpen":      True,
    }

    log.info(f"Placing working order: {direction} {otype} {size} x {ticker} (epic={epic}) | "
             f"entry={entry_level} stop={stop_level} target={limit_level} "
             f"current={current} goodTill={body['goodTillDate']}")

    try:
        resp     = session.post("/workingorders/otc", body=body, version="2")
        deal_ref = resp.get("dealReference")
        if not deal_ref:
            log.error(f"No deal reference returned for working order: {resp}")
            try:
                from notify import alert_missed_trade
                alert_missed_trade(ticker, direction,
                                   "[working order] IG returned no deal reference", signal_summary)
            except Exception:
                pass
            return None

        time.sleep(1)
        confirm = session.get(f"/confirms/{deal_ref}", version="1")
        status  = confirm.get("dealStatus")
        deal_id = confirm.get("dealId")

        if status != "ACCEPTED":
            reason_code = confirm.get("reason", "UNKNOWN")
            log.error(f"Working order rejected: {status} — {reason_code}")
            try:
                from notify import alert_missed_trade
                alert_missed_trade(ticker, direction,
                                   f"[working order] IG rejected the pending order: {reason_code}",
                                   signal_summary)
            except Exception:
                pass
            return None

        log.info(f"Working order confirmed: {deal_id} ({ticker} {direction} {otype} @ {entry_level})")

        _log_working_order_to_db(deal_ref, deal_id, user_id, ticker, epic, direction,
                                 size, entry_level, stop_level, limit_level, otype,
                                 hvf_type, good_till, False, session_name, signal_summary)

        return {"deal_id": deal_id, "deal_ref": deal_ref, "level": entry_level,
                "stop_level": stop_level, "limit_level": limit_level, "otype": otype,
                "good_till": body["goodTillDate"], "working_order": True, "updated": False,
                "current_price": current}

    except requests.HTTPError as e:
        log.error(f"IG API error placing working order: {e.response.status_code} — {e.response.text}")
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction,
                               f"[working order] IG API error {e.response.status_code}: "
                               f"{e.response.text[:160]}", signal_summary)
        except Exception:
            pass
        return None


def update_working_order(deal_id: str, ticker: str, direction: str,
                         entry_level: float, stop_level: float, limit_level: float,
                         existing: dict, session_name: str, user_id: str,
                         paper_trade: bool = False) -> Optional[dict]:
    """
    Amend an existing pending order to fresh HVF levels (PUT /workingorders/otc v2).
    Keeps the original good-till date. Returns a result dict on success, None on failure.
    """
    # Paper rows: update the DB record only.
    if paper_trade or str(deal_id).startswith("PAPER-"):
        try:
            db = get_db()
            try:
                db.run("""update working_orders set entry_level=:e, stop_level=:s,
                          limit_level=:l, updated_at=now() where deal_id=:d""",
                       e=entry_level, s=stop_level, l=limit_level, d=deal_id)
            finally:
                db.close()
        except Exception as ex:
            log.error(f"Paper working-order update failed for {deal_id}: {ex}")
        return {"deal_id": deal_id, "level": entry_level, "stop_level": stop_level,
                "limit_level": limit_level, "otype": existing.get("otype", "STOP"),
                "working_order": True, "updated": True}

    try:
        # Re-derive order type against the live price (entry may have crossed it).
        epic = get_epic(ticker)
        snap = get_snapshot(epic) if epic else {}
        bid, offer = float(snap.get("bid", 0) or 0), float(snap.get("offer", 0) or 0)
        decimals   = int(snap.get("decimalPlacesFactor", 2) or 2)
        current    = offer if direction == "BUY" else bid
        # Same unit alignment as placement (FX Yahoo→IG points) — no-op when aligned.
        scale = detect_ig_scale(current, entry_level) if current else 1.0
        if scale != 1.0:
            log.warning(f"{ticker}: amend levels scaled ×{scale:g} into IG units")
            entry_level *= scale
            stop_level  *= scale
            limit_level *= scale
        entry_level = _round_level(entry_level, decimals)
        stop_level  = _round_level(stop_level,  decimals)
        limit_level = _round_level(limit_level, decimals)
        if direction == "BUY":
            otype = "STOP" if (not current) or entry_level >= current else "LIMIT"
        else:
            otype = "STOP" if (not current) or entry_level <= current else "LIMIT"

        good_till = existing.get("good_till")
        body = {
            "level":          str(entry_level),
            "type":           otype,
            "timeInForce":    "GOOD_TILL_DATE" if good_till else "GOOD_TILL_CANCELLED",
            "guaranteedStop": False,
            "stopLevel":      str(stop_level),
            "limitLevel":     str(limit_level),
        }
        if good_till:
            body["goodTillDate"] = _good_till_str(good_till)

        session.ensure_authenticated()
        resp = requests.put(f"{IG_BASE_URL}/workingorders/otc/{deal_id}",
                            headers=session._headers("2"), json=body, timeout=15)
        resp.raise_for_status()
        deal_ref = resp.json().get("dealReference")
        time.sleep(1)
        confirm = session.get(f"/confirms/{deal_ref}", version="1")
        if confirm.get("dealStatus") != "ACCEPTED":
            log.error(f"Working-order amend rejected for {ticker}: {confirm.get('reason')}")
            return None

        new_deal_id = confirm.get("dealId") or deal_id
        try:
            db = get_db()
            try:
                db.run("""update working_orders set entry_level=:e, stop_level=:s,
                          limit_level=:l, otype=:o, deal_id=:nd, updated_at=now()
                          where deal_id=:d""",
                       e=entry_level, s=stop_level, l=limit_level, o=otype,
                       nd=new_deal_id, d=deal_id)
            finally:
                db.close()
        except Exception as ex:
            log.error(f"Working-order DB update failed for {deal_id}: {ex}")

        log.info(f"Working order amended: {ticker} {direction} entry "
                 f"{existing['entry_level']}→{entry_level} stop {existing['stop_level']}→{stop_level} "
                 f"target {existing['limit_level']}→{limit_level}")
        try:
            from notify import working_order_updated
            working_order_updated(ticker, direction,
                                  existing["entry_level"], entry_level,
                                  existing["stop_level"], stop_level,
                                  existing["limit_level"], limit_level, session_name,
                                  deal_ref=new_deal_id)
        except Exception as e:
            log.warning(f"Could not send working-order-updated notification: {e}")

        return {"deal_id": new_deal_id, "level": entry_level, "stop_level": stop_level,
                "limit_level": limit_level, "otype": otype,
                "working_order": True, "updated": True}

    except requests.HTTPError as e:
        log.error(f"IG API error amending working order {deal_id}: "
                  f"{e.response.status_code} — {e.response.text}")
        return None
    except Exception as e:
        log.error(f"Working-order amend failed for {deal_id}: {e}")
        return None


def delete_working_order(deal_id: str, reason: str = "") -> bool:
    """
    Cancel a pending working order (DELETE /workingorders/otc/{dealId} v2).
    Marks the DB row CANCELLED. Returns True on success.
    """
    if str(deal_id).startswith("PAPER-"):
        _set_working_order_status(deal_id, "CANCELLED", notes=reason or "paper cancel")
        return True
    try:
        resp     = session.delete(f"/workingorders/otc/{deal_id}", body={}, version="2")
        deal_ref = resp.get("dealReference")
        if deal_ref:
            time.sleep(1)
            confirm = session.get(f"/confirms/{deal_ref}", version="1")
            if confirm.get("dealStatus") != "ACCEPTED":
                log.error(f"Working-order delete rejected for {deal_id}: {confirm.get('reason')}")
                return False
        _set_working_order_status(deal_id, "CANCELLED", notes=reason or "deleted via API")
        log.info(f"Working order deleted: {deal_id}" + (f" ({reason})" if reason else ""))
        return True
    except requests.HTTPError as e:
        log.error(f"IG API error deleting working order {deal_id}: "
                  f"{e.response.status_code} — {e.response.text}")
        return False


def _promote_watching_order(row) -> bool:
    """
    Place a live IG order for a WATCHING row that has entered the proximity band.
    Updates the row in-place (deal_id, deal_ref, status → PENDING).
    Returns True on success, False on failure (row stays WATCHING for next cycle).
    """
    (deal_id, deal_ref, user_id, ticker, epic, direction, size, entry,
     stop, limit, otype, sess, sig_sum, good_till, hvf_type, paper) = row

    if paper:
        _set_working_order_status(deal_id, "PENDING",
                                  notes="WATCHING promoted to PENDING (paper)")
        return True

    try:
        mkt      = session.get(f"/markets/{epic}", version="3")
        snap     = mkt.get("snapshot", {})
        bid      = float(snap.get("bid", 0) or 0)
        offer    = float(snap.get("offer", 0) or 0)
        decimals = int(snap.get("decimalPlacesFactor", 2) or 2)
        current  = offer if direction == "BUY" else bid
        if not current:
            current = (bid + offer) / 2 if (bid or offer) else float(entry)
    except Exception as e:
        log.warning(f"{ticker}: market snapshot failed in WATCHING promote: {e}")
        return False

    real_otype = "STOP" if (direction == "BUY" and float(entry) >= current) \
                 or (direction == "SELL" and float(entry) <= current) else "LIMIT"
    good_till_dt = good_till or datetime.now(timezone.utc) + timedelta(days=3)

    body = {
        "epic":           epic,
        "direction":      direction,
        "size":           str(float(size)),
        "level":          str(_round_level(float(entry), decimals)),
        "type":           real_otype,
        "timeInForce":    "GOOD_TILL_DATE",
        "goodTillDate":   _good_till_str(good_till_dt),
        "guaranteedStop": False,
        "stopLevel":      str(_round_level(float(stop),  decimals)),
        "limitLevel":     str(_round_level(float(limit), decimals)),
        "currencyCode":   "GBP",
        "expiry":         "DFB",
        "forceOpen":      True,
    }
    try:
        resp     = session.post("/workingorders/otc", body=body, version="2")
        new_ref  = resp.get("dealReference")
        if not new_ref:
            log.error(f"{ticker}: WATCHING promote — no deal reference returned: {resp}")
            return False
        time.sleep(1)
        confirm  = session.get(f"/confirms/{new_ref}", version="1")
        status_  = confirm.get("dealStatus")
        new_id   = confirm.get("dealId")
        if status_ != "ACCEPTED":
            log.error(f"{ticker}: WATCHING promote rejected: {confirm.get('reason')}")
            return False

        # Update the existing WATCHING row to PENDING with the real IG IDs
        db = get_db()
        try:
            db.run(
                """update working_orders
                   set deal_id = :v_new_id, deal_ref = :v_new_ref,
                       status = 'PENDING', otype = :v_otype, updated_at = now()
                   where deal_id = :v_old_id""",
                v_new_id=new_id, v_new_ref=new_ref, v_otype=real_otype, v_old_id=deal_id
            )
        finally:
            db.close()

        log.info(f"{ticker}: WATCHING → PENDING — order placed {new_id} @ {entry}")
        try:
            from notify import working_order_watching_promoted
            working_order_watching_promoted(ticker, direction, float(entry), float(stop),
                                            float(limit), new_id, sess or "MONITOR")
        except Exception as ne:
            log.warning(f"Promote notification failed for {ticker}: {ne}")
        return True

    except Exception as e:
        log.error(f"{ticker}: WATCHING promote failed: {e}")
        return False


def reconcile_working_orders() -> dict:
    """
    Sync PENDING working orders against IG. Called by the session monitors.

    For each PENDING row:
      still in IG /workingorders → leave as is
      gone + matching NEW position in /positions → FILLED: insert into the
          positions table (monitor then manages closure normally), announce via
          notify.trade_opened + trade email
      gone + no matching position → CANCELLED (or EXPIRED when good-till passed),
          announce via notify.working_order_outcome — nothing ends silently

    Returns {"pending": n, "filled": [...], "cancelled": [...], "expired": [...]}.
    Never raises — monitors must not crash on a reconcile failure.
    """
    summary = {"pending": 0, "watching": 0, "filled": [], "cancelled": [], "expired": [],
               "promoted": []}
    try:
        db = get_db()
        try:
            rows = db.run(
                """select deal_id, deal_ref, user_id, ticker, epic, direction, size,
                          entry_level, stop_level, limit_level, otype, session,
                          signal_summary, good_till, hvf_type, paper_trade
                   from   working_orders where status in ('PENDING','WATCHING')""")
        finally:
            db.close()
        if not rows:
            return summary

        now_utc = datetime.now(timezone.utc)

        # Separate WATCHING from PENDING. Query includes both statuses.
        watching_rows = []
        pending_rows  = []
        for r in rows:
            # Need status — fetch it (column not in original select; use deal_id prefix heuristic)
            deal_id_val = str(r[0])
            db2 = get_db()
            try:
                st_rows = db2.run("select status from working_orders where deal_id=:d", d=deal_id_val)
                row_status = st_rows[0][0] if st_rows else "PENDING"
            except Exception:
                row_status = "PENDING"
            finally:
                db2.close()
            if row_status == "WATCHING":
                watching_rows.append(r)
            else:
                pending_rows.append(r)

        # ── Pass 0: WATCHING → PENDING upgrade ────────────────────────────────────────────────────────────────────────
        # For each WATCHING row, check if price has entered the proximity band.
        for r in watching_rows:
            deal_id, _, _, ticker, epic, direction, size, entry, stop, limit, _, sess, _, good_till, _, paper = r
            if good_till and good_till < now_utc:
                _set_working_order_status(deal_id, "EXPIRED",
                                          notes="watching order expired without reaching entry range")
                summary["expired"].append(ticker)
                log.info(f"WATCHING order expired (never reached range): {ticker} @ {entry}")
                continue
            try:
                snap    = session.get(f"/markets/{epic}", version="3").get("snapshot", {})
                bid     = float(snap.get("bid", 0) or 0)
                offer   = float(snap.get("offer", 0) or 0)
                current = offer if direction == "BUY" else bid
                if not current:
                    current = (bid + offer) / 2 if (bid or offer) else 0
                if not current:
                    summary["watching"] += 1
                    continue
                dist_pct = abs(float(entry) - current) / current * 100.0
                if dist_pct <= WO_PROXIMITY_PCT:
                    ok = _promote_watching_order(r)
                    if ok:
                        summary["promoted"].append(ticker)
                    else:
                        summary["watching"] += 1
                else:
                    summary["watching"] += 1
                    log.debug(f"{ticker}: still WATCHING — {dist_pct:.2f}% from entry {entry}")
            except Exception as we:
                log.warning(f"WATCHING check failed for {ticker}: {we}")
                summary["watching"] += 1

        # Paper rows can't be reconciled against IG — just expire stale ones.
        live_rows = []
        for r in pending_rows:
            if str(r[0]).startswith("PAPER-"):
                good_till = r[13]
                if good_till and good_till < now_utc:
                    _set_working_order_status(r[0], "EXPIRED", notes="paper order expired")
                    summary["expired"].append(r[3])
                else:
                    summary["pending"] += 1
                continue
            live_rows.append(r)
        if not live_rows:
            return summary

        ig_pending = set()
        for wo in get_working_orders():
            wod = wo.get("workingOrderData", {}) or {}
            if wod.get("dealId"):
                ig_pending.add(wod["dealId"])

        ig_positions = get_open_positions()
        db = get_db()
        try:
            tracked = {r[0] for r in db.run("select deal_id from positions")}
        finally:
            db.close()

        for (deal_id, deal_ref, user_id, ticker, epic, direction, size, entry,
             stop, limit, otype, sess, sig_sum, good_till, hvf_type, paper) in live_rows:

            if deal_id in ig_pending:
                # Still in IG — check if price has moved outside the cancel band.
                # If so, delete the order and alert: no point committing margin to
                # a setup the market has moved away from.
                try:
                    snap_r  = session.get(f"/markets/{epic}", version="3").get("snapshot", {})
                    bid_r   = float(snap_r.get("bid", 0) or 0)
                    offer_r = float(snap_r.get("offer", 0) or 0)
                    cur_r   = offer_r if direction == "BUY" else bid_r
                    if not cur_r:
                        cur_r = (bid_r + offer_r) / 2 if (bid_r or offer_r) else 0
                    if cur_r:
                        dist_r = abs(float(entry) - cur_r) / cur_r * 100.0
                        if dist_r > WO_CANCEL_BAND_PCT:
                            cancel_reason = (
                                f"price moved {dist_r:.1f}% from entry {entry} "
                                f"(threshold {WO_CANCEL_BAND_PCT}%)"
                            )
                            log.info(f"{ticker}: cancelling PENDING order — {cancel_reason}")
                            delete_working_order(deal_id, reason=cancel_reason)
                            try:
                                from notify import working_order_cancelled_proximity
                                working_order_cancelled_proximity(
                                    ticker, direction, float(entry), cur_r,
                                    dist_r, WO_CANCEL_BAND_PCT, deal_id)
                            except Exception as ne:
                                log.warning(f"Cancel-proximity notification failed for {ticker}: {ne}")
                            summary["cancelled"].append(ticker)
                            continue
                except Exception as ce:
                    log.debug(f"Out-of-band check failed for {ticker}: {ce}")

                summary["pending"] += 1
                continue

            # Gone from /workingorders — find the position it became, if any.
            match = None
            for p in ig_positions:
                pos  = p.get("position", {}) or {}
                pmkt = p.get("market", {}) or {}
                if pmkt.get("epic") != epic or pos.get("direction") != direction:
                    continue
                if pos.get("dealId") in tracked:
                    continue   # already a tracked position (opened by open_trade)
                p_size = float(pos.get("size") or pos.get("dealSize") or 0)
                if abs(p_size - float(size)) <= max(0.011, float(size) * 0.05):
                    match = p
                    break

            if match:
                pos        = match["position"]
                fill_deal  = pos.get("dealId")
                fill_level = float(pos.get("level") or pos.get("openLevel") or entry or 0)
                stop_lvl   = float(pos.get("stopLevel")  or stop  or 0)
                limit_lvl  = float(pos.get("limitLevel") or limit or 0)
                log.info(f"Working order FILLED: {ticker} {direction} {size} @ {fill_level} "
                         f"(order {deal_id} → position {fill_deal})")
                _log_position_to_db(user_id, epic, ticker, direction, float(size),
                                    fill_level, stop_lvl, limit_lvl, fill_deal,
                                    False, sess, sig_sum)
                _set_working_order_status(deal_id, "FILLED", fill_deal_id=fill_deal)
                tracked.add(fill_deal)
                summary["filled"].append(ticker)
                try:
                    from notify import trade_opened
                    trade_opened(ticker, direction, float(size), fill_level, stop_lvl,
                                 limit_lvl, sess or "WORKING_ORDER",
                                 f"HVF pending order filled — {sig_sum}")
                except Exception as e:
                    log.warning(f"Fill notification failed for {ticker}: {e}")
                try:
                    from trade_email import send_trade_email
                    send_trade_email(
                        ticker, direction,
                        {"hvf_type": hvf_type, "hvf_signal": "TRIGGERED",
                         "primaries_fired": [f"HVF pending order filled at {fill_level}"],
                         "confirmations_fired": [sig_sum] if sig_sum else []},
                        {"level": fill_level, "stop_level": stop_lvl, "limit_level": limit_lvl,
                         "deal_id": fill_deal},
                        size=size, session_name=sess or "WORKING_ORDER",
                        event="Working order FILLED — trade opened")
                except Exception as e:
                    log.warning(f"Fill email failed for {ticker}: {e}")
            else:
                expired = bool(good_till and good_till < now_utc)
                outcome = "EXPIRED" if expired else "CANCELLED"
                _set_working_order_status(deal_id, outcome,
                                          notes="good-till passed" if expired
                                          else "removed from IG without fill")
                summary["expired" if expired else "cancelled"].append(ticker)
                log.info(f"Working order {outcome}: {ticker} {direction} @ {entry} ({deal_id})")
                try:
                    from notify import working_order_outcome
                    working_order_outcome(ticker, direction, float(entry or 0), outcome,
                                          detail=f"Session {sess}.", deal_ref=deal_id)
                except Exception as e:
                    log.warning(f"Outcome notification failed for {ticker}: {e}")

    except Exception as e:
        log.error(f"reconcile_working_orders failed: {e}")
    return summary


def place_hvf_order_from_sig(sig: dict, profile: dict, session_name: str,
                             stress_mult: float = 1.0) -> Optional[dict]:
    """
    Route a scanned HVF signal to a pending working order (the entry/stop/target
    come from the pattern itself). Sizes from |entry − stop| — the actual risk
    of the pending order — using the same margin-aware sizing as market trades.
    Sends the working-order Slack notification + investment-case email on success.

    Returns the place/update result dict, or None when skipped/blocked.
    """
    ticker    = sig["ticker"]
    direction = sig.get("direction")
    entry     = sig.get("hvf_h3_level")     # entry level for BOTH directions (bearish = L3)
    stop      = sig.get("hvf_stop_level")
    target    = sig.get("hvf_target")
    if not all((ticker, direction, entry, stop, target)):
        return None

    # Per-source execution toggle + trade filters (user 2026-07-03, Config tab): a source switched
    # off, or a direction/market/location outside the configured allow-lists, places NO orders.
    try:
        from config_store import monitor_enabled, trade_allowed, location_of_ticker
        if not monitor_enabled(session_name):
            log.info(f"{ticker}: trade execution for {session_name} is switched OFF (Config) — no working order.")
            return None
        _dir = "BULL" if sig.get("hvf_type") == "BULLISH" else "BEAR"
        _ok, _why = trade_allowed(direction=_dir, market=sig.get("index"),
                                  location=sig.get("location") or location_of_ticker(ticker))
        if not _ok:
            log.info(f"{ticker}: blocked by trade filters (Config) — {_why}.")
            return None
    except Exception:
        pass   # gate fails OPEN — a config error must never stop trading

    # Quality floor (user 2026-06-29): only route a setup to a LIVE IG working order when its pattern
    # Quality is over WO_MIN_QUALITY (50). Below that it stays report-only — never becomes an order.
    _q = sig.get("hvf_quality")
    if _q is None:
        _q = sig.get("pattern_quality")
    try:
        from config_store import cfg_num
        _minq = float(cfg_num("bridge_min_quality", WO_MIN_QUALITY))
    except Exception:
        _minq = WO_MIN_QUALITY
    if not isinstance(_q, (int, float)) or _q <= _minq:
        log.info(f"{ticker}: HVF quality {_q} not > {_minq} — no working order (below quality floor).")
        return None

    # Tight-stop skip (backlog #9b): a funnel whose stop is closer than
    # TIGHT_STOP_MIN_PCT of price is structurally untradeable at IG intraday — spread
    # + tick noise stop it out for pennies (SNDK 0.35%, AMD 0.098%). The flag is set
    # at pattern evaluation (price_action.get_hvf_signal_mtf). Skip SILENTLY here at
    # the decision point — no missed-trade alert (the daily SNDK/AMD-class noise this
    # flag exists to remove); the HVF report still shows the funnel, labelled.
    if sig.get("hvf_tight_stop_intraday"):
        log.info(f"{ticker}: HVF stop too tight for IG intraday "
                 f"({sig.get('hvf_stop_pct')}% of price < {TIGHT_STOP_MIN_PCT}%) — no working order "
                 f"(structurally untradeable; shown in report).")
        return None

    # The pending order is directional — the funnel type must agree with the
    # signal-consensus direction (a BULLISH funnel can't back a SELL order).
    hvf_type = sig.get("hvf_type")
    if (hvf_type == "BULLISH" and direction != "BUY") or \
       (hvf_type == "BEARISH" and direction != "SELL"):
        log.info(f"{ticker}: HVF {hvf_type} conflicts with consensus {direction} — "
                 f"falling back to market-order path")
        return None

    entry, stop, target = float(entry), float(stop), float(target)

    size = 0.0
    epic = None   # defined before the try so the size<=0 reason lookup is NameError-safe
    try:
        epic = get_epic(ticker)
        if not epic:
            raise ValueError("no IG epic")
        # Align signal units → IG units BEFORE sizing: FX HVF levels come from
        # Yahoo (EURUSD 1.1539) while IG quotes points (11538.6). Sizing from the
        # unscaled distance would compute risk in the wrong units entirely.
        snap    = get_snapshot(epic)
        bid     = float(snap.get("bid", 0) or 0)
        offer   = float(snap.get("offer", 0) or 0)
        current = offer if direction == "BUY" else bid
        scale   = detect_ig_scale(current, entry) if current else 1.0
        if scale != 1.0:
            log.warning(f"{ticker}: HVF levels scaled ×{scale:g} into IG units for sizing/orders")
            entry, stop, target = entry * scale, stop * scale, target * scale

        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return None

        bal         = get_account_balance()
        available   = bal["available"]
        risk_amount = available * float(profile.get("risk_per_trade", 0.02)) * stress_mult
        size, adj_stop_distance = calculate_position_size(
            epic, stop_distance, risk_amount, available_funds=available)
        # IG minimum stop distance may widen the stop — keep it anchored to ENTRY.
        # Guard: never accept a widening that dwarfs the entry price (that means
        # a unit mismatch slipped through, not a real minimum-distance rule).
        if adj_stop_distance > stop_distance and (adj_stop_distance / max(entry, 1e-9)) < 0.5:
            stop = entry - adj_stop_distance if direction == "BUY" else entry + adj_stop_distance
            log.info(f"{ticker}: working-order stop widened to IG minimum "
                     f"(distance {adj_stop_distance}) → stop level {stop}")
    except Exception as e:
        log.warning(f"Working-order size calc failed for {ticker}: {e}")

    if size <= 0:
        # Plain-English, context-aware reason from the single source (user 2026-06-26). A structural
        # ACCOUNT_TOO_SMALL / margin-deficit skip is summarised daily; a generic zero-size pages.
        _reason = describe_size_skip(ticker, epic)
        try:
            from notify import alert_missed_trade
            alert_missed_trade(ticker, direction, _reason, str(sig.get("primaries_fired") or ""))
        except Exception:
            pass
        return None

    signal_str = (f"HVF {hvf_type} {sig.get('hvf_signal','')} "
                  f"R:R {sig.get('hvf_risk_reward','—')} quality {sig.get('hvf_quality','—')} | "
                  f"P:{sig.get('primary_count',0)} C:{sig.get('confirmation_count',0)} "
                  f"[{session_name}]")

    result = place_working_order(
        user_id=profile["user_id"], ticker=ticker, direction=direction, size=size,
        entry_level=entry, stop_level=stop, limit_level=target,
        session_name=session_name, signal_summary=signal_str,
        paper_trade=profile.get("paper_trade", False), hvf_type=hvf_type)

    if result and not result.get("updated") and not result.get("watching"):
        try:
            from notify import working_order_placed
            working_order_placed(ticker, direction, size, result["level"],
                                 result["stop_level"], result["limit_level"],
                                 result.get("otype", "STOP"), result.get("good_till", "—"),
                                 session_name, signal_str, user=profile.get("name", "Owner"),
                                 deal_ref=result.get("deal_id", ""))
        except Exception as e:
            log.warning(f"Working-order notification failed for {ticker}: {e}")
        try:
            from trade_email import send_trade_email
            send_trade_email(ticker, direction, sig, result, size=size,
                             session_name=session_name, event="Working order placed",
                             deal_ref=result.get("deal_id", ""))
        except Exception as e:
            log.warning(f"Working-order email failed for {ticker}: {e}")
    return result


# ======================================================================================================================
# Trailing Stop Update
# ======================================================================================================================

def update_stop(deal_id: str, new_stop_level: float) -> bool:
    """
    Move the stop loss to a new absolute price level for an open position.
    Used by the monitor routine to implement trailing stop logic.
    Note: Only call this to move the stop in the profitable direction —
    never widen a stop.

    Returns True on success, False on failure.
    """
    body = {
        "stopLevel":    new_stop_level,
        "limitLevel":   None,
        "trailingStop": False,
    }
    try:
        session.ensure_authenticated()   # refresh token before PUT — stop updates run outside health_check cadence
        resp = requests.put(
            f"{IG_BASE_URL}/positions/otc/{deal_id}",
            headers=session._headers("2"),
            json=body,
            timeout=15
        )
        resp.raise_for_status()
        log.info(f"Stop updated for {deal_id} → new stop level: {new_stop_level}")
        return True
    except requests.HTTPError as e:
        log.error(f"Failed to update stop: {e.response.status_code} — {e.response.text}")
        return False


# ======================================================================================================================
# Automated Stop-Loss Amendment (user 2026-07-18)
# ----------------------------------------------------------------------------------------------------------------------
# As a position moves into profit, lock some of that gain in by trailing the stop up (a long) / down (a short).
#   gain      = favourable % move from entry
#   increment = entry * gain * threshold          (threshold is a fraction; 0.5 = keep half the run)
#   new_stop  = current_stop + increment          (BUY; minus for SELL — i.e. move the stop toward price)
# The stop is only ever TIGHTENED, never widened. OFF unless the `stop_amend_threshold` config value > 0.
# Examples (user, all BUY): stop100/entry105/price120/thr50% -> +7.5 -> 107.5 ; thr0% -> none ; price85 -> none.
# ======================================================================================================================

def compute_trailing_stop(direction, entry, current_stop, price, threshold, min_move_pct: float = 0.0):
    """New absolute stop level per the rule above, or None if no amendment applies. `threshold` is a
    fraction (0.5 = 50%). `min_move_pct` (a %, default 0 = off) optionally requires the move to exceed that
    % of the current stop before amending — the user's ">1% of the stop price" guard, wired off for now."""
    try:
        entry = float(entry); current_stop = float(current_stop); threshold = float(threshold)
        price = float(price)
    except (TypeError, ValueError):
        return None
    if not entry or not current_stop or threshold <= 0 or price is None:
        return None
    buy = str(direction).upper() in ("BUY", "BULL", "BULLISH")
    gain = (price - entry) / entry if buy else (entry - price) / entry
    if gain <= 0:                                   # only trail once in profit
        return None
    increment = entry * gain * threshold
    new_stop = current_stop + increment if buy else current_stop - increment
    better = new_stop > current_stop if buy else new_stop < current_stop
    if not better:                                  # never widen
        return None
    if min_move_pct and abs(new_stop - current_stop) < abs(current_stop) * (min_move_pct / 100.0):
        return None                                 # move too small to bother
    return round(new_stop, 2)


def stop_amend_threshold() -> float:
    """Configured Automated Stop-Loss Amendment threshold as a FRACTION (0 = OFF). Stored in config as a
    percent (0-100, e.g. 50 = keep half the run), so divide by 100 here."""
    try:
        import config_store
        return float(config_store.get_value("stop_amend_threshold", "0") or 0) / 100.0
    except Exception:
        return 0.0


def amend_open_stops(min_move_pct: float = 0.0) -> dict:
    """Trail the stop on every open position by the configured threshold. Never widens a stop.

    NOT WIRED TO THE LIVE MONITOR (user 2026-07-18): the stop-amendment skill is illustration-only in the
    Performance tab for now — update_stop must not move real IG stops until the user explicitly enables it.
    Kept here (uncalled) for when it does go live. The reusable formula is compute_trailing_stop()."""
    threshold = stop_amend_threshold()
    out = {"threshold": threshold, "amended": [], "skipped": 0, "checked": 0}
    if threshold <= 0:
        return out
    for p in get_open_positions():
        out["checked"] += 1
        pos = p.get("position", {}) or {}
        mkt = p.get("market", {}) or {}
        deal_id = pos.get("dealId")
        direction = pos.get("direction")
        try:
            entry = float(pos.get("openLevel") or pos.get("level") or 0)
            stop = float(pos.get("stopLevel") or 0)
            bid = float(mkt.get("bid") or 0); offer = float(mkt.get("offer") or 0)
        except (TypeError, ValueError):
            out["skipped"] += 1; continue
        buy = str(direction).upper() == "BUY"
        price = (bid if buy else offer) or ((bid + offer) / 2 if (bid or offer) else 0)   # exit price side
        if not (deal_id and stop and entry and price):
            out["skipped"] += 1; continue
        new_stop = compute_trailing_stop(direction, entry, stop, price, threshold, min_move_pct)
        if new_stop is None:
            out["skipped"] += 1; continue
        if update_stop(deal_id, new_stop):
            log.info(f"Stop trailed: {mkt.get('epic')} {direction} {stop} -> {new_stop} "
                     f"(entry {entry}, price {price}, threshold {threshold})")
            out["amended"].append({"epic": mkt.get("epic"), "deal_id": deal_id,
                                   "old_stop": stop, "new_stop": new_stop})
        else:
            out["skipped"] += 1
    return out


# ======================================================================================================================
# Supabase Logging Helpers (private)
# ======================================================================================================================

def _log_position_to_db(
    user_id, epic, ticker, direction, size,
    open_price, stop_loss, take_profit, deal_id,
    paper_trade, session_name, signal_summary
):
    """Insert a new open position record into the Supabase positions table."""
    db = get_db()
    try:
        db.run(
            """insert into positions
               (user_id, epic, ticker, direction, size, open_price,
                stop_loss, take_profit, deal_id, paper_trade, session, signal_summary)
               values (:v_uid, :v_epic, :v_ticker, :v_dir, :v_size, :v_open,
                       :v_stop, :v_tp, :v_deal, :v_paper, :v_session, :v_signal)""",
            v_uid=user_id, v_epic=epic, v_ticker=ticker, v_dir=direction, v_size=size,
            v_open=open_price, v_stop=stop_loss, v_tp=take_profit,
            v_deal=deal_id, v_paper=paper_trade, v_session=session_name, v_signal=signal_summary
        )
        log.info(f"Position logged to Supabase: {deal_id}")
    except Exception as ex:
        log.error(f"Failed to log position to Supabase: {ex}")
    finally:
        db.close()


def _post_trade_review(pos: dict, pnl: float, close_reason: str):
    """
    Post a structured post-trade verdict to #alerts after every close.

    Checks three things in priority order:
      1. R:R — was the reward worth the risk on paper?
      2. Stop tightness — was the stop wider than the bid-ask spread?
      3. Minimum meaningful risk — was the £ at risk worth placing the trade?

    Verdict is GOOD / MARGINAL / POOR with specific flags so bad entries are
    surfaced immediately and can inform sizing/stop adjustments.
    """
    try:
        from notify import _send

        open_price  = float(pos["open_price"])
        stop_loss   = float(pos["stop_loss"]) if pos.get("stop_loss") else None
        take_profit = float(pos["take_profit"]) if pos.get("take_profit") else None
        size        = float(pos["size"])
        direction   = pos["direction"]
        ticker      = pos["ticker"]
        epic        = pos["epic"]

        flags   = []
        verdict = "GOOD"

        # ── R:R check ─────────────────────────────────────────────────────────────────────────────────────────────────
        rr = None
        if stop_loss and take_profit and open_price:
            stop_dist   = abs(open_price - stop_loss)
            target_dist = abs(take_profit - open_price)
            if stop_dist > 0:
                rr = round(target_dist / stop_dist, 2)

        if rr is None:
            flags.append("No R:R — take_profit or stop not set")
            verdict = "MARGINAL"
        elif rr < 1.5:
            flags.append(f"Low R:R {rr:.1f}:1 (target {target_dist:.1f} pts vs stop {stop_dist:.1f} pts)")
            verdict = "POOR"
        elif rr < 2.0:
            flags.append(f"Marginal R:R {rr:.1f}:1")
            if verdict == "GOOD":
                verdict = "MARGINAL"

        # ── Stop tightness: fetch live spread and compare ─────────────────────────────────────────────────────────────
        try:
            mkt  = session.get(f"/markets/{epic}", version="3")
            snap = mkt.get("snapshot", {})
            bid  = float(snap.get("bid") or 0)
            ask  = float(snap.get("offer") or snap.get("ask") or 0)
            spread = round(ask - bid, 4) if bid and ask else None
            if stop_loss and spread and spread > 0:
                stop_dist_chk = abs(open_price - stop_loss)
                if stop_dist_chk < spread:
                    flags.append(
                        f"Stop ({stop_dist_chk:.2f} pts) LESS than spread ({spread:.2f} pts) "
                        f"— stop was inside the spread, fill impossible at intended level"
                    )
                    verdict = "POOR"
                elif stop_dist_chk < 2 * spread:
                    flags.append(
                        f"Stop ({stop_dist_chk:.2f} pts) < 2× spread ({spread:.2f} pts) "
                        f"— very likely to be clipped by normal bid/ask movement"
                    )
                    if verdict == "GOOD":
                        verdict = "MARGINAL"
        except Exception as se:
            log.debug(f"Post-trade review: spread check failed for {epic}: {se}")

        # ── Minimum meaningful risk ───────────────────────────────────────────────────────────────────────────────────
        if stop_loss:
            risk_gbp = round(abs(open_price - stop_loss) * size, 2)
            if risk_gbp < 3.0:
                flags.append(
                    f"Tiny risk £{risk_gbp:.2f} — position too small to be meaningful. "
                    f"Review position sizing or minimum account balance."
                )
                if verdict == "GOOD":
                    verdict = "MARGINAL"

        # ── Actual outcome vs expectation ─────────────────────────────────────────────────────────────────────────────
        outcome = "profit" if pnl >= 0 else "loss"
        if close_reason == "STOP_HIT" and rr and rr >= 2.0 and verdict == "GOOD":
            pass   # stopped out on a good setup — no additional flag needed

        # ── Build Slack message ───────────────────────────────────────────────────────────────────────────────────────
        verdict_emoji = {"GOOD": "✅", "MARGINAL": "⚠️", "POOR": "❌"}.get(verdict, "ℹ️")
        rr_str = f"{rr:.1f}:1" if rr else "—"
        pnl_str = f"£{pnl:+.2f}"

        flag_text = "\n".join(f"• {f}" for f in flags) if flags else "No issues identified."

        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"{verdict_emoji} Post-trade review — {ticker} ({direction}) — {verdict}"}},
            {"type": "section",
             "fields": [
                 {"type": "mrkdwn", "text": f"*R:R:*\n{rr_str}"},
                 {"type": "mrkdwn", "text": f"*P&L:*\n{pnl_str}"},
                 {"type": "mrkdwn", "text": f"*Close reason:*\n{close_reason}"},
                 {"type": "mrkdwn", "text": f"*Session:*\n{pos.get('session', '—')}"},
             ]},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": f"*Flags:*\n{flag_text}"}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                           "text": (f"Entry {open_price} | Stop {stop_loss or '—'} | "
                                    f"Target {take_profit or '—'} | Size {size} | "
                                    f"Deal {pos.get('deal_id', '—')}")}]},
        ]
        _send("alerts", blocks)
        log.info(f"Post-trade review posted for {ticker}: {verdict} | R:R {rr_str} | P&L {pnl_str}")

    except Exception as e:
        log.warning(f"Post-trade review failed for {pos.get('deal_id', '?')}: {e}")


def _log_trade_close_to_db(deal_id: str, close_price: float, close_reason: str):
    """
    Move a closed position from the positions table to the trade_log table.
    Computes P&L and updates the daily_pnl summary for the user.
    """
    db = get_db()
    try:
        # Fetch original position record
        rows = db.run("select * from positions where deal_id = :d", d=deal_id)
        if not rows:
            log.warning(f"Position {deal_id} not found in Supabase for close logging")
            return

        # Map row tuple to named dict
        cols = [
            "id", "user_id", "epic", "ticker", "direction", "size",
            "open_price", "stop_loss", "take_profit", "atr_multiplier",
            "deal_id", "paper_trade", "opened_at", "session", "signal_summary"
        ]
        pos        = dict(zip(cols, rows[0]))
        open_price = float(pos["open_price"])
        size       = float(pos["size"])
        direction  = pos["direction"]

        # Compute P&L
        pnl     = (close_price - open_price) * size if direction == "BUY" \
                  else (open_price - close_price) * size
        pnl_pct = (
            (close_price - open_price) / open_price * 100 if direction == "BUY"
            else (open_price - close_price) / open_price * 100
        ) if open_price else 0

        # ── Stop slippage detection ───────────────────────────────────────────────────────────────────────────────────
        # Was the position closed significantly worse than the stop level?
        # If so, override the close_reason and alert — this is actionable information.
        stop_loss = float(pos["stop_loss"]) if pos["stop_loss"] else None
        if stop_loss and stop_loss > 0:
            stop_dist = abs(open_price - stop_loss)
            if direction == "BUY" and close_price < stop_loss and stop_dist > 0:
                # Closed below where the stop was set
                slippage_pts   = stop_loss - close_price
                slippage_ratio = slippage_pts / stop_dist
                if slippage_ratio > 1.5:   # actual close > 1.5× further from entry than stop
                    expected_loss = round(stop_dist * size, 2)
                    log.warning(
                        f"Stop slippage on {deal_id}: closed at {close_price} "
                        f"vs stop {stop_loss} — {slippage_ratio:.1f}× stop distance. "
                        f"Expected loss £{expected_loss:.2f}, actual £{abs(pnl):.2f}"
                    )
                    close_reason = f"STOP_SLIPPAGE_{slippage_ratio:.1f}x"
                    try:
                        from notify import alert_stop_slippage
                        alert_stop_slippage(
                            ticker=pos["ticker"], direction=direction,
                            open_price=open_price, stop_level=stop_loss,
                            close_price=close_price, size=size,
                            expected_loss=expected_loss, actual_loss=round(abs(pnl), 2),
                            slippage_ratio=slippage_ratio, original_reason=close_reason
                        )
                    except Exception as ae:
                        log.warning(f"Could not send slippage alert: {ae}")
            elif direction == "SELL" and close_price > stop_loss and stop_dist > 0:
                slippage_pts   = close_price - stop_loss
                slippage_ratio = slippage_pts / stop_dist
                if slippage_ratio > 1.5:
                    expected_loss = round(stop_dist * size, 2)
                    log.warning(
                        f"Stop slippage on {deal_id}: closed at {close_price} "
                        f"vs stop {stop_loss} — {slippage_ratio:.1f}× stop distance. "
                        f"Expected loss £{expected_loss:.2f}, actual £{abs(pnl):.2f}"
                    )
                    close_reason = f"STOP_SLIPPAGE_{slippage_ratio:.1f}x"
                    try:
                        from notify import alert_stop_slippage
                        alert_stop_slippage(
                            ticker=pos["ticker"], direction=direction,
                            open_price=open_price, stop_level=stop_loss,
                            close_price=close_price, size=size,
                            expected_loss=expected_loss, actual_loss=round(abs(pnl), 2),
                            slippage_ratio=slippage_ratio, original_reason=close_reason
                        )
                    except Exception as ae:
                        log.warning(f"Could not send slippage alert: {ae}")

        db.run(
            """insert into trade_log
               (user_id, epic, ticker, direction, size, open_price, close_price,
                stop_loss, pnl, pnl_pct, paper_trade, opened_at, closed_at,
                session, close_reason, signal_summary)
               values
               (:v_uid, :v_epic, :v_ticker, :v_dir, :v_size, :v_open, :v_close,
                :v_stop, :v_pnl, :v_pnl_pct, :v_paper, :v_opened, now(),
                :v_session, :v_reason, :v_signal)""",
            v_uid=pos["user_id"], v_epic=pos["epic"], v_ticker=pos["ticker"],
            v_dir=direction, v_size=size, v_open=open_price, v_close=close_price,
            v_stop=pos["stop_loss"], v_pnl=round(pnl, 2), v_pnl_pct=round(pnl_pct, 4),
            v_paper=pos["paper_trade"], v_opened=pos["opened_at"],
            v_session=pos["session"], v_reason=close_reason, v_signal=pos["signal_summary"]
        )

        # Remove from open positions
        db.run("delete from positions where deal_id = :d", d=deal_id)
        log.info(f"Trade closed and logged: {deal_id} | P&L = £{pnl:.2f}")

        # Update daily P&L summary (upsert)
        win  = 1 if pnl > 0 else 0
        loss = 1 if pnl < 0 else 0
        db.run(
            """insert into daily_pnl
               (user_id, trade_date, total_pnl, trade_count, win_count, loss_count)
               values (:v_uid, current_date, :v_pnl, 1, :v_win, :v_loss)
               on conflict (user_id, trade_date) do update
               set total_pnl   = daily_pnl.total_pnl   + excluded.total_pnl,
                   trade_count = daily_pnl.trade_count  + 1,
                   win_count   = daily_pnl.win_count    + excluded.win_count,
                   loss_count  = daily_pnl.loss_count   + excluded.loss_count""",
            v_uid=pos["user_id"], v_pnl=round(pnl, 2), v_win=win, v_loss=loss
        )

    except Exception as ex:
        log.error(f"Failed to log trade close to Supabase: {ex}")
    finally:
        db.close()

    # Post-trade review — runs outside the DB transaction so a review failure
    # never prevents the close from being recorded.
    try:
        _post_trade_review(pos, pnl, close_reason)
    except Exception as re:
        log.warning(f"Post-trade review raised unexpected error for {deal_id}: {re}")


# ======================================================================================================================
# Health Check
# Verifies IG connectivity and returns current account state.
# Run manually to confirm the system is operational before market open.
# ======================================================================================================================

def health_check() -> dict:
    """
    Verify IG API connectivity and return a summary of account state.
    Safe to run at any time — read-only, no trades placed.
    """
    try:
        balance   = get_account_balance()
        positions = get_open_positions()
        return {
            "status":         "OK",
            "account_id":     IG_ACCOUNT_ID,
            "balance":        balance["balance"],
            "available":      balance["available"],
            "currency":       balance["currency"],
            "open_positions": len(positions),
            "timestamp":      datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


# ======================================================================================================================
# Entry point — run health check when executed directly
# Usage: python ig_shim.py
# ======================================================================================================================

if __name__ == "__main__":
    print(json.dumps(health_check(), indent=2))
