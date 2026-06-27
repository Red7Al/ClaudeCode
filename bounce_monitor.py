# ======================================================================================================================
# File:         bounce_monitor.py
# Author:       Alex Hind
# Created:      2026-06-26
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Backlog E (user 2026-06-26): if an instrument we SOLD in the last BOUNCE_LOOKBACK_HOURS bounces back UP to
# >= sold_level * (1 + BOUNCE_ALERT_PCT), send an URGENT email (e.g. Japan 225 sold, then reverses up against us /
# back over our exit). Reads the SHARED IG account's /history/activity (decision 2026-06-26: same account as
# TradingViewWebhook), so it catches sells placed by EITHER system. One email per sell per bounce (spam-guarded).
#
# Design: the decision logic (time window, bounce test, spam-guard) is PURE and dependency-injected, so the orchestrator
# check_bounces() is unit-tested without any IG / email side-effects. The live IG-activity shape is mapped in ONE adapter
# (_sold_from_activity) modelled on the fields ig_shim.get_close_reason already reads (direction/level/epic/date);
# validate it against the first live run.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-26  Alex Hind   Initial build (backlog E). Pure recent_sells / is_bounce / spam-guard + injectable
#                                 check_bounces orchestrator; URGENT email via trade_email.send_simple_email.
# ======================================================================================================================

import os
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from config import BOUNCE_ALERT_PCT, BOUNCE_LOOKBACK_HOURS

log = logging.getLogger("bounce_monitor")

_STATE_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_STATE_FILE = os.path.join(_STATE_DIR, "bounce_alerts_state.json")


@dataclass(frozen=True)
class SoldPosition:
    epic: str
    name: str
    sold_level: float
    sold_time: datetime          # tz-aware UTC
    direction: str               # always "SELL" here

    def key(self) -> str:
        """Stable spam-guard key — one alert per sell event per bounce."""
        return f"{self.epic}|{self.sold_time.isoformat()}"


# ── Pure helpers (unit-tested) ────────────────────────────────────────────────────────────────────────────────────────

def parse_ig_time(s: str) -> Optional[datetime]:
    """Parse an IG activity timestamp to a tz-aware UTC datetime. IG returns ISO-8601
    (e.g. '2026-06-26T14:03:11' or '...+00:00'); treat naive values as UTC."""
    if not s:
        return None
    txt = str(s).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(txt) if fmt is None else datetime.strptime(txt, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _sold_from_activity(act: dict) -> Optional[SoldPosition]:
    """Map one IG /history/activity (v3, detailed) record to a SoldPosition, or None if it
    is not a SELL with a usable level/epic/time. A SELL covers BOTH opening a short and
    closing a long — either way we 'sold' at `level`, and a recovery above it is the alert."""
    d = act.get("details") or {}
    if (d.get("direction") or "").upper() != "SELL":
        return None
    try:
        level = float(str(d.get("level") or 0).replace(",", ""))
    except (ValueError, TypeError):
        level = 0.0
    epic = act.get("epic")
    dt   = parse_ig_time(act.get("date") or act.get("dateUTC"))
    if not epic or level <= 0 or dt is None:
        return None
    name = act.get("marketName") or d.get("marketName") or epic
    return SoldPosition(epic=epic, name=name, sold_level=level, sold_time=dt, direction="SELL")


def recent_sells(activities: list, now: datetime, hours: int = BOUNCE_LOOKBACK_HOURS) -> list:
    """All SELL activities within the last `hours`, newest first, deduped to the most
    recent sell per epic (the relevant exit/short level for a bounce)."""
    cutoff = now - timedelta(hours=hours)
    sells = []
    for act in activities or []:
        sp = _sold_from_activity(act)
        if sp is not None and sp.sold_time >= cutoff:
            sells.append(sp)
    sells.sort(key=lambda s: s.sold_time, reverse=True)
    seen, out = set(), []
    for s in sells:
        if s.epic not in seen:
            seen.add(s.epic)
            out.append(s)
    return out


def is_bounce(sold_level: float, current_price: float, pct: float = BOUNCE_ALERT_PCT) -> bool:
    """True when price has recovered to >= sold_level*(1+pct). Guards bad inputs."""
    if not sold_level or current_price is None:
        return False
    if current_price != current_price:        # NaN
        return False
    return current_price >= sold_level * (1 + pct)


# ── Spam-guard state ──────────────────────────────────────────────────────────────────────────────────────────────────

def _load_state(path: str = _STATE_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict, path: str = _STATE_FILE) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        log.warning(f"bounce state save failed (non-critical): {e}")


# ── Orchestrator (live path; fetchers injectable for tests) ───────────────────────────────────────────────────────────

def _live_fetch_activities(now: datetime) -> list:
    import ig_shim
    since = (now - timedelta(hours=BOUNCE_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    data = ig_shim.session.get("/history/activity", version="3",
                               params={"from": since, "detailed": True})
    return data.get("activities", [])


def _live_fetch_price(epic: str) -> Optional[float]:
    import ig_shim
    try:
        snap = ig_shim.session.get(f"/markets/{epic}", version="3").get("snapshot", {})
        bid, offer = snap.get("bid"), snap.get("offer")
        if bid and offer:
            return (float(bid) + float(offer)) / 2.0
        return float(bid or offer) if (bid or offer) else None
    except Exception as e:
        log.warning(f"bounce price fetch failed for {epic}: {e}")
        return None


def _live_send(sp: SoldPosition, current: float) -> bool:
    from trade_email import send_simple_email
    up_pct = (current / sp.sold_level - 1) * 100
    subject = f"URGENT: {sp.name} bounced +{up_pct:.1f}% above our sell ({sp.sold_level:g} -> {current:g})"
    text = (f"An instrument we SOLD in the last {BOUNCE_LOOKBACK_HOURS}h has bounced back up.\n\n"
            f"Instrument : {sp.name} ({sp.epic})\n"
            f"Sold at    : {sp.sold_level:g}  on {sp.sold_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Now        : {current:g}  (+{up_pct:.1f}% above the sell level)\n"
            f"Trigger    : recovery >= {BOUNCE_ALERT_PCT*100:.0f}% above the sell level\n\n"
            f"If this was a short, it is moving against us; if we closed a long, price has run back up.")
    return send_simple_email(subject, text)


def check_bounces(now: datetime = None,
                  fetch_activities: Callable = None,
                  fetch_price: Callable = None,
                  send: Callable = None,
                  state_path: str = _STATE_FILE) -> list:
    """Detect bounces in recently-sold instruments and fire one URGENT email each.
    Returns the list of SoldPositions alerted on this run. Never raises."""
    now = now or datetime.now(timezone.utc)
    fetch_activities = fetch_activities or _live_fetch_activities
    fetch_price      = fetch_price      or _live_fetch_price
    send             = send             or _live_send

    alerted = []
    try:
        sells = recent_sells(fetch_activities(now), now)
    except Exception as e:
        log.error(f"bounce: could not fetch activities: {e}")
        return alerted

    state = _load_state(state_path)
    for sp in sells:
        if sp.key() in state:
            continue                                   # already alerted on this sell's bounce
        try:
            current = fetch_price(sp.epic)
        except Exception as e:
            log.warning(f"bounce: price fetch error for {sp.epic}: {e}")
            continue
        if current is None or not is_bounce(sp.sold_level, current):
            continue
        if send(sp, current):
            state[sp.key()] = now.isoformat()
            alerted.append(sp)
            log.info(f"bounce alert sent: {sp.name} ({sp.epic}) sold {sp.sold_level:g} -> now {current:g}")
        else:
            log.warning(f"bounce alert email failed for {sp.epic} — will retry next run")

    if alerted:
        _save_state(state, state_path)
    return alerted
