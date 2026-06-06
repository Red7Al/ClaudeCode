# =============================================================================
# File:         ig_shim.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# -----------------------------------------------------------------------------
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
# -----------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build
# 1.0.1   2026-05-30  Alex Hind   Fix expiry from "-" to "DFB" for rolling CFD
#                                 contracts. Add get_close_reason() to query IG
#                                 activity history for STOP_HIT / TARGET_HIT etc.
#                                 Add get_open_positions() 404 guard (no positions
#                                 returns empty list, not an error).
# 1.0.2   2026-06-03  Alex Hind   check_circuit_breakers: retry Supabase user_profile
#                                 query up to 2 times (2s delay) before returning
#                                 "User profile not found". Guards against transient
#                                 connection drops that return empty rows for a valid
#                                 user_id (seen in production for KEEL 04-Jun-2026).
# 1.0.3   2026-06-05  Alex Hind   calculate_position_size: remove max(size, min_size)
#                                 that forced size up to IG minimum regardless of
#                                 margin — caused IBM INSUFFICIENT_FUNDS (03-Jun-2026).
#                                 Now skips trade when calculated size < min_size.
#                                 Exception fallback changed from 0.5 to 0.0 (safe).
# 1.1.0   2026-06-05  Alex Hind   calculate_position_size: when size < min_size,
#                                 try IG minimum deal size if account margin can
#                                 support it (min_size × price × margin_factor ≤
#                                 available × 0.9). Only skip when truly
#                                 unaffordable. Fixes GOOGL and RIOT being missed.
#                                 open_trade: resolve user display name from
#                                 user_profiles instead of hardcoded "Owner".
# 1.3.0   2026-06-06  Alex Hind   calculate_position_size: add optional
#                                 available_funds parameter — callers that have
#                                 already fetched the balance can pass it directly,
#                                 avoiding a duplicate IG API call and preventing
#                                 a race where a concurrent fill changes the
#                                 available balance between the two reads.
# 1.2.0   2026-06-06  Alex Hind   Add KN.D.* to US_EQUITY_PREFIXES — covers
#                                 Canadian/US cross-listed names (e.g. KEEL
#                                 KN.D.BITFCN.DAILY.IP) that trade NYSE hours only.
#                                 Previously missed hours guard — could place trades
#                                 outside NYSE window for these instruments.
# 1.0.4   2026-06-05  Alex Hind   open_trade paper trade path: return dict instead
#                                 of bare string. Caller accessed trade_result["level"]
#                                 which crashed with TypeError on paper trades because
#                                 string indices must be integers. Now returns
#                                 {"deal_id", "level": 0.0, "stop_level": 0.0,
#                                 "limit_level": 0.0} matching live trade format.
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install requests pg8000
#
# Environment Variables Required:
# -----------------------------------------------------------------------------
#   IG_API_KEY            IG developer API key
#   IG_USERNAME           IG account username (not email)
#   IG_PASSWORD           IG account password
#   IG_ACCOUNT_ID         IG account reference (e.g. HTIRV)
#   SUPABASE_USER         Supabase PostgreSQL user (postgres.{project_id})
#   SUPABASE_DB_PASSWORD  Supabase database password
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
import pg8000.native

from config import (
    EPIC_MAP,
    ATR_MULTIPLIERS,
    ATR_MULTIPLIER_DEFAULT,
    MAX_SPREAD_PCT,
    MAX_SPREAD_TO_STOP_RATIO,
    SPREAD_RETRY_ATTEMPTS,
    SPREAD_RETRY_WAIT_SECS,
    IG_SESSION_TTL_SECONDS,
    MIN_RISK_REWARD,
)


# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("ig_shim")


# =============================================================================
# Configuration — loaded from environment variables only
# =============================================================================

IG_API_KEY    = os.environ["IG_API_KEY"]
IG_USERNAME   = os.environ["IG_USERNAME"]
IG_PASSWORD   = os.environ["IG_PASSWORD"]
IG_ACCOUNT_ID = os.environ["IG_ACCOUNT_ID"]
IG_BASE_URL   = "https://api.ig.com/gateway/deal"

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]         # format: postgres.{project_id}
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]


# =============================================================================
# Supabase connection factory
# Returns a new connection on each call — caller must close after use.
# =============================================================================

def get_db() -> pg8000.native.Connection:
    """Create and return a new Supabase PostgreSQL connection."""
    return pg8000.native.Connection(
        host=SUPABASE_HOST,
        port=6543,
        database="postgres",
        user=SUPABASE_USER,
        password=SUPABASE_PASS,
        ssl_context=True
    )


# =============================================================================
# IG Session Management
# Handles authentication, token storage, and auto-refresh before 6hr expiry.
# =============================================================================

class IGSession:
    """
    Manages a single authenticated IG API session.
    Automatically re-authenticates before the 6-hour session expiry.
    Used as a singleton — one session per process.
    """

    # IG sessions expire after 6 hours — refresh at 5.5 hours to be safe
    SESSION_TTL = IG_SESSION_TTL_SECONDS

    def __init__(self):
        self._token: Optional[str] = None
        self._cst: Optional[str] = None
        self._authenticated_at: Optional[float] = None

    # -------------------------------------------------------------------------

    def _headers(self, version: str = "2") -> dict:
        """Build standard IG API request headers, including auth tokens if available."""
        h = {
            "Content-Type": "application/json",
            "Accept":       "application/json; charset=UTF-8",
            "X-IG-API-KEY": IG_API_KEY,
            "Version":      version,
        }
        if self._token:
            h["X-SECURITY-TOKEN"] = self._token
            h["CST"]              = self._cst
        return h

    # -------------------------------------------------------------------------

    def authenticate(self):
        """Authenticate with IG and store session tokens."""
        log.info("Authenticating with IG API...")
        resp = requests.post(
            f"{IG_BASE_URL}/session",
            headers=self._headers("2"),
            json={
                "identifier":        IG_USERNAME,
                "password":          IG_PASSWORD,
                "encryptedPassword": False
            },
            timeout=15
        )
        resp.raise_for_status()
        self._token              = resp.headers["X-SECURITY-TOKEN"]
        self._cst                = resp.headers["CST"]
        self._authenticated_at   = time.time()
        log.info("IG authentication successful")

    # -------------------------------------------------------------------------

    def ensure_authenticated(self):
        """Authenticate if not yet done, or refresh if session is approaching expiry."""
        if self._token is None:
            self.authenticate()
            return
        if time.time() - self._authenticated_at > self.SESSION_TTL:
            log.info("Session approaching expiry — refreshing...")
            self.authenticate()

    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Singleton session instance — shared across all calls in this process
# -----------------------------------------------------------------------------
session = IGSession()


# =============================================================================
# Account
# =============================================================================

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
                return min_size, stop_distance

            else:
                # Account cannot afford even the minimum deal margin — skip.
                log.warning(
                    f"{epic}: calculated size {size:.4f} below IG minimum {min_size} "
                    f"AND minimum margin cost ({min_size_margin_cost:.2f}) exceeds "
                    f"available funds ({available:.2f}) — skipping trade"
                )
                return 0.0, stop_distance

        log.info(f"{epic}: size={size} (risk={risk_size:.3f} margin={margin_size:.3f}) "
                 f"stop={stop_distance} margin_factor={margin_factor*100:.0f}%")
        return size, stop_distance

    except Exception as e:
        log.warning(f"Position size calculation failed for {epic}: {e}")
        return 0.0, stop_distance   # skip trade on error — 0.5 fallback was unsafe


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


# =============================================================================
# Epic Lookup
# Checks Supabase cache first. Falls back to IG market search on cache miss.
# New epics found via search are written back to the cache automatically.
# =============================================================================

def get_epic(ticker: str) -> Optional[str]:
    """
    Return the IG epic code for a given ticker symbol.

    Lookup order:
      1. Supabase epic_lookup table (fast, no API call)
      2. IG /markets?searchTerm= API (on cache miss, result cached for next time)

    Returns None if the instrument cannot be found.
    """

    # Step 1 — Check Supabase cache
    db = get_db()
    try:
        rows = db.run("select epic from epic_lookup where ticker = :t", t=ticker)
        if rows:
            log.info(f"Epic cache hit: {ticker} → {rows[0][0]}")
            return rows[0][0]
    finally:
        db.close()

    # Step 2 — Cache miss: search IG
    log.info(f"Epic cache miss for {ticker} — searching IG markets...")
    data    = session.get("/markets", params={"searchTerm": ticker}, version="1")
    markets = data.get("markets", [])
    if not markets:
        log.warning(f"No IG market found for ticker: {ticker}")
        return None

    epic        = markets[0]["epic"]
    description = markets[0].get("instrumentName", "")
    market_type = markets[0].get("instrumentType", "")

    # Step 3 — Write back to Supabase cache for future lookups
    db = get_db()
    try:
        db.run(
            """insert into epic_lookup (ticker, epic, description, market_type)
               values (:v_ticker, :v_epic, :v_desc, :v_mtype)
               on conflict (ticker) do update
               set epic=excluded.epic, last_seen=now()""",
            v_ticker=ticker, v_epic=epic, v_desc=description, v_mtype=market_type
        )
        log.info(f"Epic cached: {ticker} → {epic}")
    finally:
        db.close()

    return epic


# =============================================================================
# Open Positions
# =============================================================================

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
        # Also search by checking all transactions for matching levels
        for txn in txn_data.get("transactions", []):
            close_level = float(str(txn.get("closeLevel", 0) or 0).replace(",", ""))
            open_level  = float(str(txn.get("openLevel",  0) or 0).replace(",", ""))
            if open_level > 0 and close_level > 0:
                log.info(f"Best-match transaction: open={open_level} close={close_level}")
                return "SYSTEM", close_level
    except Exception as e:
        log.warning(f"Transaction history fallback failed: {e}")

    return "UNKNOWN", 0.0


# =============================================================================
# Circuit Breakers
# Enforced before every trade attempt. Blocks trades that violate risk rules.
# =============================================================================

def check_circuit_breakers(user_id: str, ticker: str) -> tuple[bool, str]:
    """
    Run all circuit breaker checks for a user before placing a trade.

    Checks:
        1. Daily loss limit — has the user hit their daily loss limit today?
        2. Max open positions — is the user already at their position limit?
        3. Spread width — is the current spread abnormally wide (> 0.5% of mid)?

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
    if loss_hit:
        return False, "Daily loss limit already triggered for today"

    # Check 2 — max open positions
    if open_count >= max_open_pos:
        return False, f"Max open positions reached ({open_count}/{max_open_pos})"

    # Check 3 — spread width
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

    return True, "OK"


# =============================================================================
# Open a Trade
# =============================================================================

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

    # Step 1 — Circuit breakers
    ok, reason = check_circuit_breakers(user_id, ticker)
    if not ok:
        log.warning(f"Trade blocked — circuit breaker: {reason}")
        try:
            from notify import alert_circuit_breaker
            # Resolve display name from user_profiles; fall back to user_id string.
            # Previously hardcoded "Owner" — would have shown wrong name for Wife/Son.
            try:
                db = get_db()
                rows = db.run("select name from user_profiles where id = :uid", uid=user_id)
                db.close()
                display_name = rows[0][0] if rows else user_id
            except Exception:
                display_name = user_id
            alert_circuit_breaker(display_name, ticker, reason)
        except Exception as e:
            log.warning(f"Could not send circuit breaker alert: {e}")
        return None

    # Step 2 — Resolve epic
    epic = get_epic(ticker)
    if not epic:
        log.error(f"Cannot trade {ticker} — no epic found")
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
            reason = f"Market not tradeable: {mkt_status} — skipping {ticker}"
            log.warning(reason)
            try:
                from notify import alert_circuit_breaker
                alert_circuit_breaker("Owner", ticker, reason)
            except Exception:
                pass
            return None

        # 4b — Enforce IG minimum stop distance
        min_obj  = rules.get("minNormalStopOrLimitDistance", {})
        min_stop = float(min_obj.get("value", 0) or 0)
        if min_stop > 0 and stop_distance < min_stop:
            log.info(f"Stop distance {stop_distance} below IG minimum {min_stop} — adjusting")
            stop_distance  = round(min_stop * 1.05, 4)
            limit_distance = round(stop_distance * 2, 4)

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
                            from notify import alert_circuit_breaker
                            alert_circuit_breaker("Owner", ticker, reason)
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
                        from notify import alert_circuit_breaker
                        alert_circuit_breaker("Owner", ticker, reason)
                    except Exception:
                        pass
                    return None

    except Exception as e:
        log.warning(f"Market pre-checks failed for {ticker}: {e}")

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


# =============================================================================
# Close a Trade
# =============================================================================

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


# =============================================================================
# Price Data
# =============================================================================

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


def get_snapshot(epic: str) -> dict:
    """Return the current market snapshot (bid, offer, high, low, net change) for an epic."""
    data = session.get(f"/markets/{epic}", version="3")
    return data.get("snapshot", {})


# =============================================================================
# Trailing Stop Update
# =============================================================================

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


# =============================================================================
# Supabase Logging Helpers (private)
# =============================================================================

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
        pnl_pct = ((close_price - open_price) / open_price * 100) if open_price else 0

        # ── Stop slippage detection ───────────────────────────────────────────
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


# =============================================================================
# Health Check
# Verifies IG connectivity and returns current account state.
# Run manually to confirm the system is operational before market open.
# =============================================================================

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


# =============================================================================
# Entry point — run health check when executed directly
# Usage: python ig_shim.py
# =============================================================================

if __name__ == "__main__":
    print(json.dumps(health_check(), indent=2))
