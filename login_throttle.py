# ======================================================================================================================
# File:         login_throttle.py
# Author:       Claude
# Created:      2026-08-18
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Brute-force protection for /api/login (SECURITY_RECOMMENDATIONS HIGH #1, user 2026-08-18: "Throttling is a good
# idea, please proceed").
#
# WHAT IT DEFENDS. api_login accepted unlimited password guesses -- no per-IP cap, no per-account cap, no lockout and
# no alert. Three things make that worth fixing now rather than later:
#   * logins are guessable NAMES (Alex, Owner, Rich), not emails, so half the credential pair is already known;
#   * the site moved from a random tunnel hostname to a stable, discoverable public domain;
#   * a successful guess reaches the ORDER path. This is not a data leak, it is execution access.
# PBKDF2 makes each guess slow to verify, which raises the cost of a campaign; it does not stop one.
#
# WHY IT IS IN THE DATABASE. The original 2026-07-28 advice said an in-memory counter would do "for a single-process
# app". That is void: under CGI every request is a brand-new Python process, so an in-memory counter is created and
# destroyed inside a single attempt. It counts to one forever -- protection in appearance only.
#
# TWO SCOPES, because they catch different attacks:
#   * (ip, name)  one attacker grinding one account.
#   * ip alone    one attacker spraying many usernames. Counted across the window, with a higher ceiling, or
#                 spraying would slip under a per-account cap indefinitely.
#
# FAILS OPEN, DELIBERATELY. If the database is unreachable the login proceeds unthrottled rather than locking
# everybody out. For a private trading system, being unable to reach your own account during a Supabase blip is a
# worse outcome than a brief unthrottled window -- and the blip is itself logged and alerted. Note the trade-off
# rather than assume it: reverse this if the site ever takes public signups.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-18  Claude      Initial build.
# ======================================================================================================================

import logging

log = logging.getLogger("login_throttle")

# Deliberately generous for a human who has forgotten which password they used, and useless for a script.
MAX_PER_ACCOUNT = 5        # failures for one (ip, name) inside the window before that pair is locked
MAX_PER_IP      = 15       # failures from one IP across ALL usernames inside the window (spray defence)
WINDOW_MINUTES  = 15       # failures older than this no longer count
LOCKOUT_MINUTES = 15       # how long a lock lasts


def _cfg(key: str, fallback: int) -> int:
    try:
        import config_store
        return int(config_store.cfg_num(key, fallback) or fallback)
    except Exception:
        return fallback


def settings() -> tuple:
    return (_cfg("login_max_per_account", MAX_PER_ACCOUNT), _cfg("login_max_per_ip", MAX_PER_IP),
            _cfg("login_window_minutes", WINDOW_MINUTES), _cfg("login_lockout_minutes", LOCKOUT_MINUTES))


def check(ip: str, name: str) -> tuple:
    """(allowed, seconds_remaining). Call BEFORE verifying the password.

    Checking first means a locked-out attacker never reaches PBKDF2 -- which also stops the endpoint being
    used as a CPU-burning amplifier, since verification is deliberately expensive.
    """
    ip, name = (ip or "?")[:64], (name or "")[:64]
    _acct, max_ip, window, _lock = settings()
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run("select extract(epoch from (locked_until - now())) from login_attempts "
                          "where ip = :ip and name = :n and locked_until is not null "
                          "and locked_until > now()", ip=ip, n=name) or []
            if rows:
                return False, int(rows[0][0] or 0)
            # Spray check: this IP against every username inside the window.
            tot = db.run(f"select coalesce(sum(attempts), 0) from login_attempts "
                         f"where ip = :ip and last_attempt > now() - interval '{int(window)} minutes'",
                         ip=ip) or [[0]]
            if int(tot[0][0] or 0) >= max_ip:
                return False, int(window) * 60
        finally:
            db.close()
    except Exception as exc:
        log.warning(f"login throttle unavailable, allowing the attempt: {exc}")
        return True, 0
    return True, 0


def record_failure(ip: str, name: str) -> int:
    """Count a failed attempt; lock the pair once it passes the cap. Returns the new count (0 on error)."""
    ip, name = (ip or "?")[:64], (name or "")[:64]
    max_acct, _max_ip, window, lockout = settings()
    try:
        from db_pool import get_db
        db = get_db()
        try:
            # One statement: insert, or bump an existing row -- restarting the count when the previous
            # first_attempt has aged out of the window, so old failures cannot accumulate into a lock
            # weeks later.
            rows = db.run(
                f"""insert into login_attempts (ip, name, attempts, first_attempt, last_attempt)
                    values (:ip, :n, 1, now(), now())
                    on conflict (ip, name) do update set
                        attempts = case
                            when login_attempts.first_attempt < now() - interval '{int(window)} minutes'
                            then 1 else login_attempts.attempts + 1 end,
                        first_attempt = case
                            when login_attempts.first_attempt < now() - interval '{int(window)} minutes'
                            then now() else login_attempts.first_attempt end,
                        last_attempt = now()
                    returning attempts""", ip=ip, n=name) or [[0]]
            count = int(rows[0][0] or 0)
            if count >= max_acct:
                db.run(f"update login_attempts set locked_until = now() + interval '{int(lockout)} minutes' "
                       f"where ip = :ip and name = :n", ip=ip, n=name)
                log.warning(f"login locked: {name} from {ip} after {count} failures")
                _alert(ip, name, count, lockout)
            return count
        finally:
            db.close()
    except Exception as exc:
        log.warning(f"login throttle could not record a failure: {exc}")
        return 0


def record_success(ip: str, name: str) -> None:
    """Clear the counter. A correct password proves this was not an attack."""
    try:
        from db_pool import get_db
        db = get_db()
        try:
            db.run("delete from login_attempts where ip = :ip and name = :n",
                   ip=(ip or "?")[:64], n=(name or "")[:64])
        finally:
            db.close()
    except Exception as exc:
        log.warning(f"login throttle could not clear on success: {exc}")


def _alert(ip: str, name: str, count: int, lockout: int) -> None:
    """Tell somebody. Silent throttling means an attacker can grind for a week unnoticed -- the whole
    point is that the first you hear of it is not the moment they succeed."""
    try:
        import notify
        notify.alert_system_error(
            session="Security",
            component="Login brute-force protection",
            summary=f"{count} failed logins for '{name}' from {ip}. That account/IP pair is locked for "
                    f"{lockout} minutes.",
            detail="If this was not you, the password is being guessed against a public endpoint. "
                   "Consider changing it. Repeated bursts from one address warrant blocking it at the "
                   "host.")
    except Exception as exc:
        log.warning(f"could not post the login-throttle alert: {exc}")
