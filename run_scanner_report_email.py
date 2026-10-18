# ======================================================================================================================
# File:         run_scanner_report_email.py
# Author:       Claude
# Created:      2026-08-07
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Emails each enabled web-user account holder their own Scanner Report (ChangeRequest P-07, 2026-08-07: "Email the
# scanner report each day to the account holder").
#
# WHAT IT SENDS -- since 2026-10-11 (P-01 "SCANNER REPORT EMAIL STILL USES THE OLD RULE"): EXACTLY the rows that
# login's Scanner Report shows on opening the page, in the same order. screen_rows() builds the login's own
# /api/records and /api/config payloads with the server's handlers (trigger-date rule, 90-day cap, market switches)
# and runs the page's own filter code over them (scanner_report_rows.js lifts pass()/tradeVisible()/augment() out of
# hvf_web/app.js). There is no second copy of the filter here to drift: before this, the email judged VWAP/ATR on
# the latest bar with no trigger-date rule and could differ from the screen.
#
# WHERE THIS RUNS: every morning as job 3 of the Morning Chain (trading-scanner-report-email.yml, a reusable
# workflow), measured 2026-10-09: "Trade email sent via Resend". Best-effort per user: one bad address or send
# failure never blocks the rest.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-07  Claude   Initial build (ChangeRequest P-07).
# 1.1.0   2026-08-11  Claude   Hard-filter by My Trading Filters (trading_limits.check_limits), not just the
#                               separate "Scanner Report filter defaults"; above_vwap/atr_expanding now come from
#                               _live_vwap_atr (every has_signal row, any status) instead of the TRIGGERED-only
#                               volume_score components, matching the same-day Scanner tab fix.
# 2.0.0   2026-10-11  Claude   Send the rows the login's Scanner Report shows, computed by the page's own code
#                               (screen_rows + scanner_report_rows.js); build_rows/user_rows removed.
# ======================================================================================================================

import json
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_scanner_report_email")

MAX_ROWS = 25   # cap the emailed table; the app itself remains the full interactive report


def _fmt1(v):
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "—"


def screen_rows(name: str):
    """(rows, report_unavailable) -- the rows `name`'s Scanner Report shows on opening the page, in its order.

    The payloads come from the server's own handlers called with that login's session, exactly as the page
    fetches them; the row selection is the page's own JavaScript (scanner_report_rows.js). Nothing here
    decides which rows qualify."""
    from hvf_web import server, web_users as wu
    token = wu.token_for(name)
    if not token:
        raise RuntimeError(f"no session for login {name!r}")
    payload = {}
    # /api/config decrypts the login's IG credentials only to answer has_ig_creds / bridge, which the row code
    # (scanner_report_rows.js) never reads. On the GitHub runner there is no key for them, so every send logged
    # "secret decrypt failed" four times (2026-10-13). This job has no business touching IG credentials: the
    # check is skipped for these calls and restored after.
    real_check = server._user_has_ig_creds
    server._user_has_ig_creds = lambda _name: False
    try:
        for path, view in (("/api/records", server.api_records), ("/api/config", server.api_config)):
            with server.app.test_request_context(path, headers={"X-Auth": token}):
                payload[path.rsplit("/", 1)[1]] = server.app.make_response(view()).get_json()
    finally:
        server._user_has_ig_creds = real_check
    here = os.path.dirname(os.path.abspath(__file__))
    p = subprocess.run(["node", os.path.join(here, "scanner_report_rows.js")], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"scanner_report_rows.js failed: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout), bool((payload.get("records") or {}).get("report_unavailable"))


def email_body(name: str, rows: list, generated_utc: str, report_unavailable: bool = False):
    """(subject, text, html) for one user's report. rows come from screen_rows() -- the screen's rows.

    Sorted by trigger date, newest first, with a Triggered column (owner 2026-10-12: "need trigger date
    column and sort by this descending"). Stable, so rows triggered the same day keep the screen's order;
    a row with no trigger date goes last."""
    date_str = (generated_utc or "")[:10] or ""
    total = len(rows)
    trig = lambda r: str(r.get("trig_date") or "")[:10]
    # A finished trade stays on the report showing its outcome (owner 2026-10-18: "it should stay on the
    # report and show it has reached target - not drop off the report").
    def outcome(r):
        oc = r.get("outcome")
        if oc not in ("TARGET", "STOPPED"):
            return "Open"
        ret = r.get("outcome_return")
        return (f"{oc.title()} {str(r.get('outcome_date') or '')[:10]}"
                + (f" ({ret:+.1f}%)" if isinstance(ret, (int, float)) else ""))
    ordered = (sorted([r for r in rows if trig(r)], key=trig, reverse=True)
               + [r for r in rows if not trig(r)])[:MAX_ROWS]
    warn = ("Triggered setups are hidden today: the trigger history was unavailable, so they could not be checked "
            "against your filters." if report_unavailable else "")
    warn_html = f'<p style="color:#b42318;font-weight:600">{warn}</p>' if warn else ""
    subject = f"Squeeze Scanner - Scanner Report{f' for {date_str}' if date_str else ''} ({total} setup{'' if total == 1 else 's'})"

    lines = [f"Hello {name},", "", f"Your Squeeze Scanner report{f' for {date_str}' if date_str else ''}:", ""]
    if warn:
        lines += [warn, ""]
    if not ordered:
        lines.append("No setups are in your Scanner Report today.")
    else:
        lines.append(f"{'Ticker':<14}{'Triggered':<12}{'Outcome':<27}{'Dir':<6}{'Market':<12}{'Quality':<9}{'R:R':<7}RVOL")  # no Status column: every row is TRIGGERED (owner 2026-10-13)
        for r in ordered:
            lines.append(f"{str(r.get('ticker') or ''):<14}{trig(r) or '-':<12}{outcome(r):<27}{str(r.get('direction') or ''):<6}"
                         f"{str(r.get('market') or ''):<12}{_fmt1(r.get('quality')):<9}"
                         f"{_fmt1(r.get('rr')):<7}{_fmt1(r.get('rvol'))}")
        if total > len(ordered):
            lines.append(f"\n… and {total - len(ordered)} more (showing the {len(ordered)} most recently triggered).")
    lines += ["", "Log in to Squeeze Scanner for the full interactive report and every column.",
             "These are the rows your Scanner Report shows when you open it, sorted by trigger date, newest first; "
             "your My Trading Filters and market switches decide them."]
    text = "\n".join(lines)

    def _row_html(r):
        col = "#3fb950" if r.get("direction") == "BULL" else "#f85149" if r.get("direction") == "BEAR" else "#555"
        base = "padding:4px 8px;border-bottom:1px solid #eee"   # each td's shared style; extras appended BEFORE the closing quote (a plain string-concat bug here silently produced invalid style="..."... markup, caught by review 2026-08-07)
        td = lambda extra="": f'<td style="{base}{extra}">'
        return (f'<tr>{td()}{r.get("ticker","")}</td>'
                f'{td(";white-space:nowrap")}{trig(r) or "—"}</td>'
                f'{td(";white-space:nowrap" + (";color:#1a7f37;font-weight:600" if r.get("outcome") == "TARGET" else ";color:#b42318;font-weight:600" if r.get("outcome") == "STOPPED" else ""))}{outcome(r)}</td>'
                f'{td(f";color:{col};font-weight:600")}{r.get("direction","")}</td>'
                f'{td()}{r.get("market","")}</td>'
                f'{td(";text-align:right")}{_fmt1(r.get("quality"))}</td>'
                f'{td(";text-align:right")}{_fmt1(r.get("rr"))}</td>'
                f'{td(";text-align:right")}{_fmt1(r.get("rvol"))}</td></tr>')

    if ordered:
        table = ('<table style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:13px">'
                 '<tr style="background:#f6f8fa"><th style="padding:4px 8px;text-align:left">Ticker</th>'
                 '<th style="padding:4px 8px;text-align:left">Triggered &darr;</th>'
                 '<th style="padding:4px 8px;text-align:left">Outcome</th>'
                 '<th style="padding:4px 8px;text-align:left">Dir</th><th style="padding:4px 8px;text-align:left">Market</th>'
                 '<th style="padding:4px 8px;text-align:right">Quality</th><th style="padding:4px 8px;text-align:right">R:R</th>'
                 '<th style="padding:4px 8px;text-align:right">RVOL</th></tr>'
                 + "".join(_row_html(r) for r in ordered) + "</table>")
        if total > len(ordered):
            table += (f'<p style="margin:6px 0 0;font-size:12px;color:#777">… and {total - len(ordered)} more '
                     f'(showing the {len(ordered)} most recently triggered).</p>')
    else:
        table = '<p>No setups are in your Scanner Report today.</p>'

    html = (f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px">'
           f'<h2 style="margin:0 0 4px">Squeeze Scanner — Scanner Report</h2>'
           f'<p style="margin:0 0 12px;color:#555">Hello {name}, here is your report'
           f'{f" for {date_str}" if date_str else ""}.</p>'
           f'{warn_html}'
           f'{table}'
           f'<p style="margin:12px 0 0;font-size:12px;color:#777">Log in to Squeeze Scanner for the full interactive '
           f'report. These are the rows your Scanner Report shows when you open it, sorted by trigger date, newest '
           f'first; your My Trading Filters and market switches decide them.</p></div>')
    return subject, text, html


def _only_recipients() -> set:
    """Restrict the run to specific addresses (user 2026-08-23: "just for eahind@yahoo.co.uk for now").

    This report has never actually run since it was written on 2026-08-07, so the first live send is also
    the first time anyone sees its output. Going straight to every enabled account holder would make that
    debut public. SCANNER_EMAIL_ONLY limits delivery to a named address; empty means everyone, which is the
    intended end state once the content has been reviewed.

    A user who is skipped by this filter is NOT counted as failed and gets no log_event, because nothing
    went wrong for them -- they simply were not in scope for this run.
    """
    raw = os.environ.get("SCANNER_EMAIL_ONLY", "")
    return {a.strip().lower() for a in raw.replace(";", ",").split(",") if a.strip()}


def main():
    from hvf_web import web_users as wu
    from hvf_web.server import _load_snapshot

    only = _only_recipients()
    if only:
        log.warning("RESTRICTED RUN: delivering only to %s; every other account holder is skipped.",
                    ", ".join(sorted(only)))

    snap = _load_snapshot()
    if not snap.get("records"):
        log.warning("no snapshot available (hvf_web/snapshot.json empty/missing) - scanner report email skipped")
        return

    sent = skipped = failed = 0
    for u in wu.list_users():
        name, email = u["name"], (u.get("email") or "").strip()
        if not u.get("enabled") or not email:
            skipped += 1
            continue
        if only and email.lower() not in only:
            skipped += 1
            continue
        ok = False
        urows = []
        try:
            urows, unavailable = screen_rows(name)
            subject, text, html = email_body(name, urows, snap.get("generated_utc"), unavailable)
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

    scope = f" [restricted to {', '.join(sorted(only))}]" if only else ""
    log.info(f"Scanner report email run complete: {sent} sent, {skipped} skipped "
             f"(disabled/no email/out of scope), {failed} failed{scope}")
    try:
        from web_store import append_batch
        append_batch("cron-job.org",
                     f"Scanner report email — {sent} sent, {skipped} skipped, {failed} failed{scope}", by="cron")
    except Exception as e:
        log.warning(f"batch log skipped: {e}")
    # A failed send must fail the JOB. The first live run reported success while delivering nothing
    # (0 sent, 1 failed) because main() returned None regardless (user 2026-08-23). Skips are fine --
    # a disabled account or one outside a restricted run is not an error -- but a send that was attempted
    # and did not arrive is exactly what this job exists to do.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
