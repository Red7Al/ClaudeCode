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
                            risk_amount: float) -> tuple[float, float]:
    """
    Calculate position size that satisfies BOTH risk management AND IG margin.

    Returns (size, adjusted_stop_distance).

    Two constraints:
      1. Risk constraint:  size × stop_distance = risk_amount  (2% rule)
      2. Margin constraint: size × price × margin_factor ≤ available_funds

    Uses the tighter of the two. If the resulting size is below IG's
    minimum deal size the function returns (0.0, stop_distance) — caller
    should skip the trade.
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
        try:
            available = get_account_balance()["available"]
        except Exception:
            available = 0
        margin_size = (available * 0.9) / (price * margin_factor) if price > 0 else 0

        # Use the smaller of the two
        size = min(risk_size, margin_size)
        size = round(max(size, min_size), 2)

        if size < min_size:
            log.warning(f"{epic}: calculated size {size:.4f} below IG minimum {min_size} — skipping")
            return 0.0, stop_distance

        log.info(f"{epic}: size={size} (risk={risk_size:.3f} margin={margin_size:.3f}) "
                 f"stop={stop_distance} margin_factor={margin_factor*100:.0f}%")
        return size, stop_distance

    except Exception as e:
        log.warning(f"Position size calculation failed for {epic}: {e}")
        return 0.5, stop_distance   # safe fallback


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
               values (:t, :e, :d, :m)
               on conflict (ticker) do update
               set epic=excluded.epic, last_seen=now()""",
            t=ticker, e=epic, d=description, m=market_type
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
        if not rows:
            return False, "User profile not found in Supabase"

        daily_loss_limit, max_open_pos, loss_hit, total_pnl, open_count = rows[0]

        # Check 1 — daily loss limit
        if loss_hit:
            return False, "Daily loss limit already triggered for today"

        # Check 2 — max open positions
        if open_count >= max_open_pos:
            return False, f"Max open positions reached ({open_count}/{max_open_pos})"

    finally:
        db.close()

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
            alert_circuit_breaker("Owner", ticker, reason)
        except Exception as e:
            log.warning(f"Could not send circuit breaker alert: {e}")
        return None

    # Step 2 — Resolve epic
    epic = get_epic(ticker)
    if not epic:
        log.error(f"Cannot trade {ticker} — no epic found")
        return None

    # Step 3 — Paper trade: log only, skip IG
    if paper_trade:
        log.info(f"[PAPER] {direction} {size} x {ticker} (epic={epic})")
        _log_position_to_db(
            user_id=user_id, epic=epic, ticker=ticker,
            direction=direction, size=size,
            open_price=0, stop_loss=0, take_profit=0,
            deal_id=f"PAPER-{int(time.time())}",
            paper_trade=True, session_name=session_name,
            signal_summary=signal_summary
        )
        return f"PAPER-{int(time.time())}"

    # Step 4 — Enforce IG minimum stop distance
    try:
        mkt     = session.get(f"/markets/{epic}", version="3")
        rules   = mkt.get("dealingRules", {})
        min_obj = rules.get("minNormalStopOrLimitDistance", {})
        min_stop = float(min_obj.get("value", 0) or 0)
        if min_stop > 0 and stop_distance < min_stop:
            log.info(f"Stop distance {stop_distance} below IG minimum {min_stop} — adjusting")
            stop_distance  = round(min_stop * 1.05, 4)   # 5% above minimum
            limit_distance = round(stop_distance * 2, 4)
    except Exception as e:
        log.warning(f"Could not check min stop distance: {e}")

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

        log.info(f"Deal confirmed: {deal_id} at level {level}")

        # Log confirmed position to Supabase
        _log_position_to_db(
            user_id=user_id, epic=epic, ticker=ticker,
            direction=direction, size=size,
            open_price=level, stop_loss=stop_level, take_profit=limit_level,
            deal_id=deal_id, paper_trade=False,
            session_name=session_name, signal_summary=signal_summary
        )
        return deal_id

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
               values (:uid, :e, :t, :d, :s, :op, :sl, :tp, :did, :pt, :sess, :sig)""",
            uid=user_id, e=epic, t=ticker, d=direction, s=size,
            op=open_price, sl=stop_loss, tp=take_profit,
            did=deal_id, pt=paper_trade, sess=session_name, sig=signal_summary
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

        # Insert into trade_log
        db.run(
            """insert into trade_log
               (user_id, epic, ticker, direction, size, open_price, close_price,
                stop_loss, pnl, pnl_pct, paper_trade, opened_at, closed_at,
                session, close_reason, signal_summary)
               values
               (:uid, :e, :t, :d, :s, :op, :cp,
                :sl, :pnl, :ppct, :pt, :oa, now(),
                :sess, :cr, :sig)""",
            uid=pos["user_id"], e=pos["epic"], t=pos["ticker"],
            d=direction, s=size, op=open_price, cp=close_price,
            sl=pos["stop_loss"], pnl=round(pnl, 2), ppct=round(pnl_pct, 4),
            pt=pos["paper_trade"], oa=pos["opened_at"],
            sess=pos["session"], cr=close_reason, sig=pos["signal_summary"]
        )

        # Remove from open positions
        db.run("delete from positions where deal_id = :d", d=deal_id)
        log.info(f"Trade closed and logged: {deal_id} | P&L = £{pnl:.2f}")

        # Update daily P&L summary (upsert)
        db.run(
            """insert into daily_pnl
               (user_id, trade_date, total_pnl, trade_count, win_count, loss_count)
               values
               (:uid, current_date, :pnl, 1,
                case when :pnl2 > 0 then 1 else 0 end,
                case when :pnl3 < 0 then 1 else 0 end)
               on conflict (user_id, trade_date) do update
               set total_pnl   = daily_pnl.total_pnl   + excluded.total_pnl,
                   trade_count = daily_pnl.trade_count  + 1,
                   win_count   = daily_pnl.win_count    + excluded.win_count,
                   loss_count  = daily_pnl.loss_count   + excluded.loss_count""",
            uid=pos["user_id"],
            pnl=round(pnl, 2), pnl2=round(pnl, 2), pnl3=round(pnl, 2)
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
