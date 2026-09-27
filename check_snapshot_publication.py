#!/usr/bin/env python
"""Fail loudly when the Supabase snapshot copy has fallen behind the one the site is serving.

WHY THIS EXISTS. MEASURED 2026-09-27: scanner_snapshot_store.current_metadata() reported the remote
snapshot as generated 2026-08-16T19:04Z with 1,421 records, while the live site was serving one generated
that day with 1,773. The Supabase copy was SIX WEEKS stale.

NOTHING SAID SO, and that is the defect being fixed. The publish step in trading-scanner-snapshot.yml is
`continue-on-error: true` -- correctly, because the static release on IONOS is live whether or not Supabase
took a copy -- so every one of those runs reported success. The workflow's own comment records "fourteen
consecutive runs from 2026-08-16". The outage was six weeks old when someone thought to ask.

The actual cause is not a code or credentials fault: the publish raises
`SnapshotStoreError: could not inspect Storage bucket (402)`. HTTP 402 is Payment Required -- Supabase
STORAGE is refusing every request, while the Postgres database it bills separately works fine. That needs
an action on the account, which is exactly why it must be reported rather than retried.

WHY THIS READS THE DATABASE AND NOT STORAGE. current_metadata() reads the metadata table in Postgres, not
the bucket -- verified by it answering while Storage was returning 402. So this check still works during
precisely the outage it exists to report.

WHY A LAG THRESHOLD RATHER THAN "DID THE LAST PUBLISH FAIL". A single failed publish is not worth a red
run: IONOS is serving and `load_snapshot` refuses a remote older than the local copy, so the site cannot
regress. A backup that has been dead for days IS worth one. The threshold is the line between a transient
miss and no second copy at all.
"""

import argparse
import datetime as dt
import json
import logging
import os

log = logging.getLogger("snapshot_publication")

DEFAULT_MAX_LAG_DAYS = 3.0


def _parse(value):
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def local_generated(path=None):
    """generated_utc of the snapshot on disk -- the one the site actually serves."""
    import scanner_snapshot_store as store
    p = path or store.DEFAULT_SNAPSHOT
    try:
        with open(p, encoding="utf-8") as fh:
            return _parse(json.load(fh).get("generated_utc"))
    except Exception as exc:
        log.warning("could not read the local snapshot at %s (%s)", p, exc)
        return None


def remote_generated():
    """generated_utc of the published Supabase copy, or None when it cannot be established.

    None is NOT treated as fresh: a store we cannot ask about is a store we cannot rely on.
    """
    try:
        import scanner_snapshot_store as store
        meta = store.current_metadata() or {}
        return _parse(meta.get("generated_utc")), meta
    except Exception as exc:
        log.warning("could not read the published snapshot metadata (%s)", exc)
        return None, {}


STATE_KEY = "snapshot_publication_state"


def _was_stale() -> bool:
    try:
        import web_store
        return bool((web_store.load_json_store(STATE_KEY) or {}).get("stale"))
    except Exception as exc:
        log.warning("could not read %s (%s); treating this as the first look", STATE_KEY, exc)
        return False


def _remember(stale: bool, remote_iso: str = "") -> None:
    try:
        import web_store
        web_store.save_json_store(STATE_KEY, {"stale": bool(stale), "remote": remote_iso,
                                              "checked_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    except Exception as exc:
        log.error("could not save %s: %s", STATE_KEY, exc)


def check(max_lag_days=DEFAULT_MAX_LAG_DAYS, alert=True, path=None, fail_on_stale=False) -> int:
    """0 when the published copy is current enough, 1 when it is not. Never raises.

    ALERTS ON CHANGE, NOT ON STATE. The publish step is continue-on-error by a deliberate decision
    recorded in trading-scanner-snapshot.yml: while Supabase Storage is over quota, the IONOS fallback
    keeping the site current IS the system working, and a red run every day becomes noise that gets muted.
    That decision was right and is kept. What it relied on was a warning written to the run summary -- and
    MEASURED 2026-09-27, that was not enough: the outage ran six weeks before anyone asked. A run summary
    is a page you have to visit, which is the "nobody reads a green cron page" failure this repository
    already records against the GH_PAT that expired unnoticed for eight weeks.

    So this tells somebody, once, when the backup dies -- and once again when it comes back.
    """
    local = local_generated(path)
    remote, meta = remote_generated()

    if local is None:
        # Without a local reference there is nothing to compare against, and inventing one would either
        # cry wolf or go quiet. Say so and stay green: the snapshot build itself reports its own failures.
        log.warning("no local snapshot generated_utc; nothing to compare against")
        return 0

    if remote is None:
        detail = ("The Supabase snapshot copy has no readable generated_utc, so the site has no verified "
                  f"second copy. The local snapshot is dated {local.isoformat()}.")
        log.error("published snapshot unreadable; local is %s", local.isoformat())
        _tell("Scanner snapshot: no readable published copy", detail, alert)
        return 1

    # AGE FROM NOW, not distance from whatever snapshot happens to be on this disk. Measured while writing
    # this: run from a developer checkout the local file was the stale 2026-08-12 boot copy, so
    # local-minus-remote came out NEGATIVE and the check passed while the backup was six weeks dead. The
    # snapshot is rebuilt daily, so "how old is the published copy" is the question, and its answer does not
    # depend on where the check runs. The local date is still reported, as context.
    now = dt.datetime.now(dt.timezone.utc)
    lag_days = (now - remote).total_seconds() / 86400.0
    log.info("snapshot publication: published %s (%.2f days old, limit %.2f), local %s, records %s",
             remote.isoformat(), lag_days, max_lag_days, local.isoformat(), meta.get("record_count"))

    was = _was_stale()
    if lag_days <= max_lag_days:
        if was:
            # RECOVERY IS REPORTED TOO. Without it, the only way to learn the backup came back is to ask,
            # which is the same gap that let the outage run six weeks.
            _tell("Scanner snapshot backup is publishing again",
                  f"The Supabase copy is current again: published {remote.isoformat()}, "
                  f"{lag_days:.1f} days old, {meta.get('record_count')} records.", alert)
        if alert:            # a dry run must not consume the transition and silence the real alert
            _remember(False, remote.isoformat())
        return 0

    detail = (
        f"The published Supabase copy is {lag_days:.1f} days old, and the snapshot is rebuilt daily.\n\n"
        f"  local copy seen : {local.isoformat()}\n"
        f"  published       : {remote.isoformat()} ({meta.get('record_count')} records, "
        f"version {meta.get('version_id')})\n\n"
        "The site is NOT degraded: IONOS serves the local copy and scanner_snapshot_store.load_snapshot "
        "refuses a remote older than it, so a stale remote cannot overwrite a newer local one. What is "
        "missing is the second copy.\n\n"
        "The publish step is continue-on-error, so the Scanner Snapshot Publish run reports success even "
        "when it fails. Check that step's log for the reason. When this was first measured, on 2026-09-27, "
        "it was 'SnapshotStoreError: could not inspect Storage bucket (402)' -- HTTP 402 is Payment "
        "Required, and Supabase Storage was refusing every request while the Postgres database was fine. "
        "That needs an account action, not a retry.")
    log.error("published snapshot is %.1f days old", lag_days)
    if was:
        log.error("already reported; not alerting again (the site is not degraded by this)")
    else:
        _tell(f"Scanner snapshot backup is {lag_days:.0f} days stale", detail, alert)
    if alert:
        _remember(True, remote.isoformat())
    # Exit 0 by default. The publish run going red every day during a known quota outage is the noise the
    # workflow's own comment argues against, and the site is genuinely fine -- IONOS serves the current
    # snapshot and load_snapshot refuses an older remote. --fail-on-stale is there for a caller that wants
    # the exit code, such as a future readiness check.
    return 1 if fail_on_stale else 0


def _tell(subject: str, detail: str, alert: bool) -> None:
    if not alert:
        return
    try:
        import notify
        notify.alert_system_error(session="Scheduled", component="scanner_snapshot_store / Supabase Storage",
                                 summary=subject, detail=detail)
    except Exception as exc:
        log.error("Slack alert failed: %s", exc)
    try:
        from trade_email import send_simple_email
        if not send_simple_email(subject=subject, text=detail):
            log.error("email alert returned False")
    except Exception as exc:
        log.error("email alert failed: %s", exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-lag-days", type=float,
                    default=float(os.environ.get("SNAPSHOT_MAX_LAG_DAYS") or DEFAULT_MAX_LAG_DAYS),
                    help=f"how far the published copy may fall behind (default {DEFAULT_MAX_LAG_DAYS})")
    ap.add_argument("--snapshot", default=None, help="path to the local snapshot (default: the site's)")
    ap.add_argument("--no-alert", action="store_true", help="report only; send nothing, remember nothing")
    ap.add_argument("--fail-on-stale", action="store_true",
                    help="exit non-zero when the published copy is stale (default: alert but exit 0, "
                         "because the site is not degraded by a dead backup)")
    a = ap.parse_args()
    return check(max_lag_days=a.max_lag_days, alert=not a.no_alert, path=a.snapshot,
                 fail_on_stale=a.fail_on_stale)


if __name__ == "__main__":
    raise SystemExit(main())
