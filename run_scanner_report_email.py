# ======================================================================================================================
# File:         run_scanner_report_email.py
# Author:       Claude
# Created:      2026-08-07
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Emails each enabled web-user account holder their own Scanner Report (ChangeRequest P-07, 2026-08-07: "Email the
# scanner report each day to the account holder"). Sends today's Squeeze-only setups (has_signal == True) from the
# SAME snapshot the Scanner tab reads, enriched with RVOL/VolumeScore/above_vwap/atr_expanding exactly like
# /api/records, filtered by BOTH each user's saved Quality / R:R / RVOL display floors (Configuration -> Preferences
# -> "Scanner Report filter defaults") AND — since 2026-08-11 — their personal "My Trading Filters" (R:R, Quality,
# VolumeScore, RVOL, require-above-VWAP, require-ATR-expanding, instrument value), the SAME hard filter the Scanner
# tab itself now applies. User instruction (2026-08-11): "The scanner report MUST also match the user trading filter
# settings" -> "Hard filter (Recommended)": a setup failing your own floors is excluded from the email entirely, not
# just flagged. Best-effort per user: one bad email address or send failure never blocks the rest.
#
# IMPORTANT — where this runs: hvf_web/snapshot.json and data/web_users.json are LOCAL, gitignored files that only
# exist on the machine running the Flask server (see hvf_web/build_snapshot.py's own header: "the Flask server reads
# that file"; hvf_web/scheduled_jobs.py: "the web server runs on the operator's laptop"). A GitHub Actions runner has
# neither file, so — unlike the other run_*.py jobs — this CANNOT be wired into the cron-job.org -> GitHub Actions
# pipeline; it must be scheduled locally (e.g. Windows Task Scheduler) on the same machine as the web server, after
# the day's snapshot has been (re)built. Tested by running directly, not assumed.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-07  Claude   Initial build (ChangeRequest P-07).
# 1.1.0   2026-08-11  Claude   Hard-filter by My Trading Filters (trading_limits.check_limits), not just the
#                               separate "Scanner Report filter defaults"; above_vwap/atr_expanding now come from
#                               _live_vwap_atr (every has_signal row, any status) instead of the TRIGGERED-only
#                               volume_score components, matching the same-day Scanner tab fix.
# ======================================================================================================================

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_scanner_report_email")

MAX_ROWS = 25   # cap the emailed table; the app itself remains the full interactive report


def _fmt1(v):
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "—"


def build_rows(snap: dict) -> list:
    """Every snapshot record enriched with rvol/volume_score/above_vwap/atr_expanding — the SAME enrichment
    hvf_web.server.api_records() applies for /api/records, reusing its helpers so the emailed report can
    never quietly drift from what the Scanner tab itself shows. above_vwap/atr_expanding come from
    _live_vwap_atr (2026-08-11) — computed for every has_signal row regardless of status, not just
    TRIGGERED, same as the Scanner tab; RVOL/VolumeScore remain TRIGGERED-only (inherently about the break
    bar, see _snapshot_rvol/_snapshot_volscore's own docstrings)."""
    from hvf_web.server import _snapshot_rvol, _snapshot_volscore, _live_vwap_atr
    rvol = _snapshot_rvol(snap)
    vscore = _snapshot_volscore(snap)
    vwap_atr = _live_vwap_atr(snap)

    rows = []
    for r in snap.get("records", []):
        result = vscore.get(r.get("ticker")) or {}
        av, ae = vwap_atr.get(r.get("ticker"), (None, None))
        rows.append(dict({k: v for k, v in r.items() if k != "_card"},
                         rvol=rvol.get(r.get("ticker")),
                         volume_score=result.get("score"),
                         above_vwap=av,
                         atr_expanding=ae))
    return rows


def user_rows(rows: list, filters: dict, username: str = None) -> list:
    """Squeeze-only rows (has_signal, matching the Scanner's own default "Show Squeeze Only" view) passing
    BOTH: (1) the user's saved Quality / R:R / RVOL display floors — the 3 numeric fields the Preferences
    card persists as 'Scanner Report filter defaults' — and (2) since 2026-08-11, their personal "My
    Trading Filters" (trading_limits.check_limits — R:R, Quality, VolumeScore, RVOL, above-VWAP,
    ATR-expanding, instrument value), the SAME hard filter the Scanner tab itself applies. `username` is
    optional so build_rows()/user_rows() stay testable/usable without a real login (falls back to code
    defaults via trading_limits.user_limits(None) — see its own docstring). Anything finer than these two
    filter sets (search, market/sector picks, PE/insider ranges, ...) stays a login-only refinement, kept
    out of the email so this stays small, testable and unambiguous."""
    filters = filters or {}
    qmin, rrmin, rvmin = filters.get("f_qmin"), filters.get("f_rrmin"), filters.get("f_rvmin")

    def _floor_ok(value, floor):
        if floor in (None, ""):
            return True
        try:
            floor = float(floor)
        except (TypeError, ValueError):
            return True
        return value is not None and float(value) >= floor

    import trading_limits

    out = []
    for r in rows:
        if not r.get("has_signal"):
            continue
        if not _floor_ok(r.get("quality"), qmin):
            continue
        if not _floor_ok(r.get("rr"), rrmin):
            continue
        if not _floor_ok(r.get("rvol"), rvmin):
            continue
        reason = trading_limits.check_limits(
            username, r.get("ticker"), quality=r.get("quality"), rr=r.get("rr"),
            volume_score=r.get("volume_score"), rvol=r.get("rvol"),
            above_vwap=r.get("above_vwap"), atr_expanding=r.get("atr_expanding"), mcap=r.get("mcap"))
        if reason:
            continue
        out.append(r)
    return out


def email_body(name: str, rows: list, generated_utc: str):
    """(subject, text, html) for one user's report. rows should already be filtered via user_rows()."""
    date_str = (generated_utc or "")[:10] or ""
    total = len(rows)
    ordered = sorted(rows, key=lambda r: (r.get("quality") if r.get("quality") is not None else -1),
                     reverse=True)[:MAX_ROWS]
    subject = f"Squeeze Scanner - Scanner Report{f' for {date_str}' if date_str else ''} ({total} setup{'' if total == 1 else 's'})"

    lines = [f"Hello {name},", "", f"Your Squeeze Scanner report{f' for {date_str}' if date_str else ''}:", ""]
    if not ordered:
        lines.append("No setups matched your saved filters and My Trading Filters floors today.")
    else:
        lines.append(f"{'Ticker':<10}{'Dir':<6}{'Market':<12}{'Quality':<9}{'R:R':<7}{'RVOL':<7}Status")
        for r in ordered:
            lines.append(f"{str(r.get('ticker') or ''):<10}{str(r.get('direction') or ''):<6}"
                         f"{str(r.get('market') or ''):<12}{_fmt1(r.get('quality')):<9}"
                         f"{_fmt1(r.get('rr')):<7}{_fmt1(r.get('rvol')):<7}{r.get('status') or ''}")
        if total > len(ordered):
            lines.append(f"\n… and {total - len(ordered)} more (showing the top {len(ordered)} by Quality).")
    lines += ["", "Log in to Squeeze Scanner for the full interactive report and every column.",
             "This reflects your saved 'Scanner Report filter defaults' (Configuration -> Preferences) AND your "
             "'My Trading Filters' (Configuration -> My Trading Filters) applied to today's Squeeze-only setups; "
             "change either there to change what this email includes."]
    text = "\n".join(lines)

    def _row_html(r):
        col = "#3fb950" if r.get("direction") == "BULL" else "#f85149" if r.get("direction") == "BEAR" else "#555"
        base = "padding:4px 8px;border-bottom:1px solid #eee"   # each td's shared style; extras appended BEFORE the closing quote (a plain string-concat bug here silently produced invalid style="..."... markup, caught by review 2026-08-07)
        td = lambda extra="": f'<td style="{base}{extra}">'
        return (f'<tr>{td()}{r.get("ticker","")}</td>'
                f'{td(f";color:{col};font-weight:600")}{r.get("direction","")}</td>'
                f'{td()}{r.get("market","")}</td>'
                f'{td(";text-align:right")}{_fmt1(r.get("quality"))}</td>'
                f'{td(";text-align:right")}{_fmt1(r.get("rr"))}</td>'
                f'{td(";text-align:right")}{_fmt1(r.get("rvol"))}</td>'
                f'{td()}{r.get("status","")}</td></tr>')

    if ordered:
        table = ('<table style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:13px">'
                 '<tr style="background:#f6f8fa"><th style="padding:4px 8px;text-align:left">Ticker</th>'
                 '<th style="padding:4px 8px;text-align:left">Dir</th><th style="padding:4px 8px;text-align:left">Market</th>'
                 '<th style="padding:4px 8px;text-align:right">Quality</th><th style="padding:4px 8px;text-align:right">R:R</th>'
                 '<th style="padding:4px 8px;text-align:right">RVOL</th><th style="padding:4px 8px;text-align:left">Status</th></tr>'
                 + "".join(_row_html(r) for r in ordered) + "</table>")
        if total > len(ordered):
            table += (f'<p style="margin:6px 0 0;font-size:12px;color:#777">… and {total - len(ordered)} more '
                     f'(showing the top {len(ordered)} by Quality).</p>')
    else:
        table = '<p>No setups matched your saved filters and My Trading Filters floors today.</p>'

    html = (f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px">'
           f'<h2 style="margin:0 0 4px">Squeeze Scanner — Scanner Report</h2>'
           f'<p style="margin:0 0 12px;color:#555">Hello {name}, here is your report'
           f'{f" for {date_str}" if date_str else ""}.</p>'
           f'{table}'
           f'<p style="margin:12px 0 0;font-size:12px;color:#777">Log in to Squeeze Scanner for the full interactive '
           f'report. This reflects your saved Scanner Report filter defaults (Configuration &rarr; Preferences) AND '
           f'your My Trading Filters (Configuration &rarr; My Trading Filters).</p></div>')
    return subject, text, html


def main():
    from hvf_web import web_users as wu
    from hvf_web.server import _load_snapshot

    snap = _load_snapshot()
    if not snap.get("records"):
        log.warning("no snapshot available (hvf_web/snapshot.json empty/missing) - scanner report email skipped")
        return

    rows = build_rows(snap)
    sent = skipped = failed = 0
    for u in wu.list_users():
        name, email = u["name"], (u.get("email") or "").strip()
        if not u.get("enabled") or not email:
            skipped += 1
            continue
        filters = wu.get_settings(name).get("filters", {})
        urows = user_rows(rows, filters, name)
        subject, text, html = email_body(name, urows, snap.get("generated_utc"))
        ok = False
        try:
            from trade_email import send_simple_email
            ok = send_simple_email(subject, text, html=html, recipients=[email])
        except Exception as e:
            log.error(f"scanner report email failed for {name}: {type(e).__name__}: {e!r}")
        if ok:
            sent += 1
            try:
                wu.log_event(name, f"Scanner report emailed ({len(urows)} setups)")
            except Exception:
                pass
        else:
            failed += 1
            log.warning(f"scanner report email NOT sent to {name} <{email}>")

    log.info(f"Scanner report email run complete: {sent} sent, {skipped} skipped (disabled/no email), {failed} failed")
    try:
        from web_store import append_batch
        append_batch("cron-job.org", f"Scanner report email — {sent} sent, {skipped} skipped, {failed} failed", by="cron")
    except Exception as e:
        log.warning(f"batch log skipped: {e}")


if __name__ == "__main__":
    main()
