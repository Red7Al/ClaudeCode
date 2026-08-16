# ======================================================================================================================
# File:         run_data_quality_audit.py
# Author:       Alex Hind
# Created:      2026-06-12
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Nightly Yahoo-vs-IG price data audit (user 2026-06-12: "Consider using IG to review yahoo data on a regular basis
# each evening"). Yahoo's LSE feed contains phantom prints (RR.L fake 1,420 high vs IG's real 1,345.9 — caused a
# genuine HVF to be missed) and the epic search once mapped LAND.L to the wrong company entirely. This audit is the
# regression net for both failure classes:
#
#   1. CLOSE deviation  — Yahoo close vs IG close per day. A large deviation (>2%) means the ticker is mapped to the
#                         WRONG INSTRUMENT or wrong currency — the most dangerous data failure possible.
#   2. Phantom wicks    — Yahoo High above IG High (or Low below IG Low) by >1%: bad exchange prints that poison
#                         pivot detection. Counted per ticker over the comparison window.
#
# Coverage & budget: rotates through the UK universe (FTSE 100 + 250 — Yahoo's US feed is clean), auditing the
# least-recently-audited tickers first, ~30 daily candles each. The IG historical-price allowance is 10,000
# points/week shared with HVF validation, so the audit self-throttles: it only spends allowance above a 5,000-point
# reserve and caps each night's batch. Full UK universe coverage takes ~2 weeks per rotation.
#
# Output: rows upserted to data_quality_log; Slack summary in WEIGHT order (CRITICAL mismatches first, then by
# phantom-wick count) — CRITICAL findings go to #alerts, otherwise a digest goes to #signals.
#
# Usage:
#   python run_data_quality_audit.py            # auto batch size from allowance
#   python run_data_quality_audit.py 10         # cap batch at 10 tickers
#   python run_data_quality_audit.py RR.L BP.L  # audit specific tickers
#
# Environment Variables Required:
#   IG_API_KEY / IG_USERNAME / IG_PASSWORD / IG_ACCOUNT_ID
#   SUPABASE_USER / SUPABASE_DB_PASSWORD
#   SLACK_ALERTS, SLACK_SIGNALS
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.4.0   2026-06-24  Alex Hind   (user 2026-06-24) Added read-only `--verify-epic TICKER...`: resolves each ticker via
#                                 get_epic() and prints the IG instrument NAME at that epic — confirms a pin maps to the
#                                 intended company end-to-end. Wired into trading-data-quality.yml (verify_epics input).
# 1.3.0   2026-06-24  Alex Hind   (user 2026-06-24) Added a read-only `--lookup-epic TICKER...` diagnostic: prints the IG
#                                 /markets search candidates (epic, expiry, type, name) for tickers whose epic mapping needs
#                                 a human pin decision (backlog: PYPL/MSTR/GLD/DJT + unmapped names). No pin, no trade — just
#                                 surfaces the options so the correct epic can be added to ig_shim._EPIC_VERIFIED_OVERRIDES.
#                                 Wired into trading-data-quality.yml via the lookup_epics input.
# 1.2.0   2026-06-12  Alex Hind   Phase 2: full-cache identity sweep EVERY night — every cached epic (all markets,
#                                 zero IG allowance) verified via ig_shim.instrument_names_match (shared with get_epic
#                                 so the rule cannot drift). Closes the ASX gap: the UK-only identity check left US/AU
#                                 tickers unguarded and the wrong-company class recurred within hours. First run caught
#                                 BNO → Bionomics Ltd (Yahoo BNO = Brent Oil Fund) — purged, never traded.
# 1.1.0   2026-06-12  Alex Hind   Identity reconciliation for audited tickers (IDENTITY_MISMATCH → #alerts).
# 1.0.0   2026-06-12  Alex Hind   Initial build — nightly rotation audit, allowance-aware, CRITICAL → #alerts.
# ======================================================================================================================

import io
import os
import sys
import logging
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("data_quality_audit")

COMPARE_DAYS      = 30      # daily candles compared per ticker
ALLOWANCE_RESERVE = 5000    # never audit below this remaining IG allowance
MAX_BATCH         = 25      # hard cap per night
CLOSE_CRITICAL    = 0.02    # >2% close deviation = wrong instrument / currency
WICK_TOLERANCE    = 0.01    # Yahoo extreme beyond IG extreme by >1% = phantom


def _uk_universe() -> list:
    from run_hvf_report import FTSE100, FTSE250
    return FTSE100 + FTSE250


def _pick_batch(limit: int) -> list:
    """Least-recently-audited UK tickers first (never-audited before all others)."""
    from db_pool import get_db
    universe = _uk_universe()
    db = get_db()
    try:
        rows = db.run("select ticker, max(audit_date) from data_quality_log group by ticker")
    finally:
        db.close()
    last = {r[0]: r[1] for r in rows}
    ordered = sorted(universe, key=lambda t: (t in last, last.get(t) or ""))
    return ordered[:limit]


def _audit_ticker(ticker: str) -> dict:
    """Compare Yahoo vs IG daily candles for one ticker. Returns the audit row dict."""
    import yfinance as yf
    from ig_shim import get_epic, get_prices_df

    row = {"ticker": ticker, "days_compared": 0, "close_max_dev_pct": None,
           "phantom_high_wicks": 0, "phantom_low_wicks": 0,
           "verdict": "NO_IG_DATA", "detail": "", "remaining": None}

    epic = get_epic(ticker)
    if not epic:
        row["verdict"] = "NO_EPIC"
        row["detail"]  = "ticker has no IG epic (not tradeable on IG, or UK epic refused)"
        return row

    # ── Identity reconciliation (recon register #4): the IG instrument NAME
    # must match the Yahoo company name for this ticker — catches the
    # LAND→Gladstone Land class (wrong company mapped) continuously, not just
    # at lookup time. Word-overlap check: at least one significant word of one
    # name must appear in the other.
    try:
        from db_pool import get_db as _gdb
        _db = _gdb()
        try:
            _r = _db.run("select description from epic_lookup where epic = :e limit 1", e=epic)
        finally:
            _db.close()
        ig_name = (_r[0][0] or "") if _r else ""
        y_name  = (yf.Ticker(ticker).info or {}).get("shortName") or ""
        if ig_name and y_name:
            _stop  = {"plc", "the", "inc", "corp", "corporation", "group", "ltd",
                      "limited", "holdings", "trust", "ord", "and", "of", "co"}
            ig_w = {w for w in ig_name.lower().replace(".", " ").split() if w not in _stop and len(w) > 2}
            y_w  = {w for w in y_name.lower().replace(".", " ").split() if w not in _stop and len(w) > 2}
            if ig_w and y_w and not (ig_w & y_w):
                row["verdict"] = "IDENTITY_MISMATCH"
                row["detail"]  = (f"IG instrument '{ig_name}' vs Yahoo company '{y_name}' share no "
                                  f"significant words — epic {epic} may map to the WRONG COMPANY")
                return row
    except Exception as e:
        log.debug(f"{ticker}: identity check skipped ({e})")

    ig_df, remaining = get_prices_df(epic, resolution="DAY", count=COMPARE_DAYS)
    row["remaining"] = remaining
    if ig_df.empty:
        return row

    y_df = yf.Ticker(ticker).history(period=f"{COMPARE_DAYS + 10}d", interval="1d")
    if y_df is None or y_df.empty:
        row["verdict"] = "NO_YAHOO_DATA"
        return row
    y_df = y_df.copy()
    y_df.index = y_df.index.tz_localize(None).normalize()

    # ── Scale normalisation: IG quotes US shares in CENTS and some instruments
    # in different units (NVDA: IG 20,100 vs Yahoo $201 → a fake "99% deviation").
    # Snap the median close ratio to the nearest power of 10 and rescale Yahoo
    # before comparing; only residual deviation is a real data problem.
    scale_note = ""
    overlap = [d for d in ig_df.index if d in y_df.index]
    if overlap:
        ig_med = float(ig_df.loc[overlap, "Close"].median())
        y_med  = float(y_df.loc[overlap, "Close"].median())
        if ig_med > 0 and y_med > 0:
            import math
            ratio = ig_med / y_med
            snapped = 10 ** round(math.log10(ratio))
            if snapped != 1:
                y_df[["Open", "High", "Low", "Close"]] *= snapped
                scale_note = f"(Yahoo rescaled ×{snapped:g} — IG quotes different units) "

    worst_close, worst_close_day = 0.0, None
    examples = []
    n = 0
    for dt, ig in ig_df.iterrows():
        if dt not in y_df.index:
            continue
        y = y_df.loc[dt]
        n += 1
        if ig["Close"] > 0:
            dev = abs(float(y["Close"]) - float(ig["Close"])) / float(ig["Close"])
            if dev > worst_close:
                worst_close, worst_close_day = dev, dt.date()
        if float(ig["High"]) > 0 and float(y["High"]) > float(ig["High"]) * (1 + WICK_TOLERANCE):
            row["phantom_high_wicks"] += 1
            examples.append(f"{dt.date()} high {float(y['High']):.1f} (Yahoo) vs {float(ig['High']):.1f} (IG)")
        if float(ig["Low"]) > 0 and float(y["Low"]) < float(ig["Low"]) * (1 - WICK_TOLERANCE):
            row["phantom_low_wicks"] += 1
            examples.append(f"{dt.date()} low {float(y['Low']):.1f} (Yahoo) vs {float(ig['Low']):.1f} (IG)")

    row["days_compared"]     = n
    row["close_max_dev_pct"] = round(worst_close * 100, 3)

    if n == 0:
        row["verdict"] = "NO_OVERLAP"
    elif worst_close > CLOSE_CRITICAL:
        row["verdict"] = "CRITICAL_MISMATCH"
        row["detail"]  = scale_note + (f"closes deviate up to {worst_close*100:.1f}% (worst {worst_close_day}) — "
                                       f"Yahoo and IG may be quoting DIFFERENT INSTRUMENTS or currencies")
    elif row["phantom_high_wicks"] + row["phantom_low_wicks"] > 0:
        row["verdict"] = "PHANTOM_WICKS"
        row["detail"]  = scale_note + "; ".join(examples[:4])
    else:
        row["verdict"] = "OK"
        row["detail"]  = scale_note.strip()
    return row


def _save(rows: list):
    from db_pool import get_db
    db = get_db()
    try:
        for r in rows:
            db.run(
                """insert into data_quality_log
                       (audit_date, ticker, days_compared, close_max_dev_pct,
                        phantom_high_wicks, phantom_low_wicks, verdict, detail)
                   values (current_date, :t, :n, :c, :ph, :pl, :v, :d)
                   on conflict (audit_date, ticker) do update
                   set days_compared = excluded.days_compared,
                       close_max_dev_pct = excluded.close_max_dev_pct,
                       phantom_high_wicks = excluded.phantom_high_wicks,
                       phantom_low_wicks = excluded.phantom_low_wicks,
                       verdict = excluded.verdict, detail = excluded.detail""",
                t=r["ticker"], n=r["days_compared"], c=r["close_max_dev_pct"],
                ph=r["phantom_high_wicks"], pl=r["phantom_low_wicks"],
                v=r["verdict"], d=r["detail"][:500])
    finally:
        db.close()


def _post_slack(rows: list, remaining):
    """Weight order: CRITICAL first, then by phantom count desc. CRITICAL → #alerts, else digest → #signals."""
    import requests
    from notify import fmt, slack_enabled
    if not slack_enabled("signals"):
        return   # Slack #signals channel disabled (user 2026-08-01)

    rank = {"IDENTITY_MISMATCH": 0, "CRITICAL_MISMATCH": 0, "PHANTOM_WICKS": 1, "NO_OVERLAP": 2,
            "NO_IG_DATA": 3, "NO_YAHOO_DATA": 3, "NO_EPIC": 4, "OK": 5}
    rows = sorted(rows, key=lambda r: (rank.get(r["verdict"], 9),
                                       -(r["phantom_high_wicks"] + r["phantom_low_wicks"])))
    critical = [r for r in rows if r["verdict"] in ("CRITICAL_MISMATCH", "IDENTITY_MISMATCH")]
    wicky    = [r for r in rows if r["verdict"] == "PHANTOM_WICKS"]
    ok_n     = sum(1 for r in rows if r["verdict"] == "OK")

    lines = []
    for r in rows:
        if r["verdict"] == "OK":
            continue
        lines.append(f"• {fmt(r['ticker'])} — *{r['verdict']}* "
                     f"(close dev {r['close_max_dev_pct'] or 0:.2f}%, "
                     f"{r['phantom_high_wicks']}+{r['phantom_low_wicks']} phantom wicks)\n"
                     f"    _{(r['detail'] or '—')[:180]}_")

    header = (f"🔍 Data Quality Audit — Yahoo vs IG broker data: "
              f"{len(rows)} tickers, {len(critical)} critical, {len(wicky)} with phantom wicks, {ok_n} clean")
    body = "\n".join(lines) if lines else "_All audited tickers matched IG broker data — no issues found._"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": (f"Phantom wick = Yahoo extreme beyond IG's by >1% (bad exchange print). "
                                f"Critical = closes deviate >2% (possible wrong instrument). "
                                f"IG allowance remaining: {remaining} | "
                                + datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"))}]},
    ]
    url = os.environ.get("SLACK_ALERTS" if critical else "SLACK_SIGNALS", "")
    if url:
        try:
            requests.post(url, json={"blocks": blocks}, timeout=10)
            log.info(f"Audit summary posted to {'#alerts' if critical else '#signals'}")
        except Exception as e:
            log.error(f"Audit Slack post failed: {e}")


def _identity_sweep_all_cached() -> list:
    """
    Recon register #4, FULL scope (ASX 2026-06-12: the UK-only identity check
    left US/AU tickers unguarded and the wrong-company class recurred within
    hours). Every cached equity epic, every night: the IG instrument name must
    share a significant word with the Yahoo company name. Costs ZERO IG
    allowance — name comparisons only. Mismatches are CRITICAL (wrong company).
    """
    import yfinance as yf
    from db_pool import get_db
    from ig_shim import instrument_names_match

    db = get_db()
    try:
        cached = db.run("select ticker, epic, description from epic_lookup "
                        "where epic like '%.D.%' and description is not null")
    finally:
        db.close()
    findings = []
    for t, e, d in cached:
        try:
            y = (yf.Ticker(t).info or {}).get("shortName") or ""
        except Exception:
            continue   # FX/index/commodity tickers have no Yahoo equity name
        if y and not instrument_names_match(t, d, y):
            findings.append({"ticker": t, "days_compared": 0, "close_max_dev_pct": None,
                             "phantom_high_wicks": 0, "phantom_low_wicks": 0,
                             "verdict": "IDENTITY_MISMATCH",
                             "detail": f"epic {e} is '{d}' but Yahoo says {t} is '{y}' — "
                                       f"WRONG COMPANY mapped"})
            log.error(f"Identity sweep: {t} → {e} ('{d}') vs Yahoo '{y}' — MISMATCH")
    log.info(f"Identity sweep: {len(cached)} cached epics checked, {len(findings)} mismatches")
    return findings


def _lookup_epics(tickers: list):
    """Read-only IG /markets search (user 2026-06-24): print every candidate market for each ticker
    so the correct epic can be picked for a human pin (ig_shim._EPIC_VERIFIED_OVERRIDES). Never trades,
    never writes. The same scoring get_epic uses is shown so the auto-pick is visible alongside."""
    from ig_shim import session
    for t in tickers:
        try:
            data = session.get("/markets", params={"searchTerm": t}, version="1")
            mkts = data.get("markets", [])
            log.info(f"=== {t}: {len(mkts)} IG candidate(s) (showing up to 15) ===")
            for m in mkts[:15]:
                print(f"  {t:<8} epic={str(m.get('epic')):<30} expiry={str(m.get('expiry','?')):<6} "
                      f"type={str(m.get('instrumentType','?')):<12} "
                      f"status={str(m.get('marketStatus','?')):<10} {m.get('instrumentName','')}")
        except Exception as e:
            log.error(f"{t}: IG /markets search failed — {e}")


def _verify_epics(tickers: list):
    """Read-only end-to-end pin check (user 2026-06-24): for each ticker, run get_epic() then read
    the IG instrument NAME at that epic, so a pin is confirmed to resolve to the intended company.
    No trade, no write."""
    from ig_shim import get_epic, session
    for t in tickers:
        try:
            epic = get_epic(t)
            if not epic:
                print(f"  {t:<8} -> (no epic resolved)")
                continue
            mkt  = session.get(f"/markets/{epic}", version="3")
            name = (mkt.get("instrument", {}) or {}).get("name", "?")
            status = (mkt.get("snapshot", {}) or {}).get("marketStatus", "?")
            print(f"  {t:<8} -> {epic:<28} [{status}] {name}")
        except Exception as e:
            log.error(f"{t}: verify failed — {e}")


# Absurd-outcome sweep (user 2026-08-16: "the status of 9844% growth is nonsense ... continue with
# price checks each night to flush these out"). Costs no IG allowance -- pure SQL over squeeze_history.
#
# The existing audit compares our prices against IG tick by tick, which catches a wrong CANDLE. It cannot
# catch a setup whose GEOMETRY is impossible, and that is what produced 9844%: a stop placed a fraction
# of a percent from entry inflates R:R, inflates the recorded return against a trivial risk, and then
# compounds. Those are now excluded from the analysis population at read time (server.py _sqa_all_rows),
# but exclusion is a bandage -- the rows are still being WRITTEN, so they are still worth reporting.
_ABSURD_RETURN_PCT = 300.0        # a single squeeze returning more than this is a data fault, not a win
_MIN_STOP_DISTANCE_PCT = 0.5      # matches ig_shim's live tight-stop guard and server._MIN_STOP_DISTANCE


def _absurd_outcomes(days: int = 400) -> list:
    """Rows in squeeze_history whose numbers cannot be true. Read-only; returns dicts for the digest."""
    try:
        from config import MAX_RISK_REWARD as _max_rr
    except Exception:
        _max_rr = 10.0
    out = []
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run(
                "select ticker, market, triggered_date, outcome, return_pct, risk_reward, "
                "       entry_level, stop_level, target_level, hvf_type "
                "  from squeeze_history "
                " where triggered_date >= (current_date - make_interval(days => :d)) "
                "   and entry_level is not null and stop_level is not null and entry_level <> 0",
                d=days) or []
        finally:
            db.close()
    except Exception as exc:
        log.warning(f"absurd-outcome sweep skipped: {exc}")
        return out

    for tk, mkt, td, oc, ret, rr, entry, stop, target, side in rows:
        faults = []
        entry, stop = float(entry), float(stop)
        stop_pct = abs(entry - stop) / abs(entry) * 100.0
        bull = str(side or "").upper().startswith("BULL")
        if ret is not None and abs(float(ret)) > _ABSURD_RETURN_PCT:
            faults.append(f"return {float(ret):+.0f}%")
        if rr is not None and float(rr) > _max_rr:
            faults.append(f"R:R {float(rr):.1f} > {_max_rr:g}")
        if stop_pct < _MIN_STOP_DISTANCE_PCT:
            faults.append(f"stop {stop_pct:.2f}% from entry")
        # Geometry that cannot be right whatever the prices did: a long stopped above its entry, or
        # targeting below it (and the mirror for a short). Cheap, and it catches a whole class the
        # numeric bounds never would.
        if bull and stop > entry:
            faults.append("BULL stop above entry")
        if (not bull) and stop < entry:
            faults.append("BEAR stop below entry")
        if target is not None:
            target = float(target)
            if bull and target < entry:
                faults.append("BULL target below entry")
            if (not bull) and target > entry:
                faults.append("BEAR target above entry")
        if faults:
            out.append({"ticker": tk, "market": mkt or "", "triggered_date": str(td or ""),
                        "outcome": oc or "", "faults": faults})
    out.sort(key=lambda r: (-len(r["faults"]), r["ticker"]))
    return out


def _post_absurd_slack(fresh: list, backlog: int, window_days: int) -> None:
    from notify import slack_enabled                      # every direct poster must ask (memory rule)
    if not slack_enabled("alerts"):
        return
    import os
    import requests
    url = os.environ.get("SLACK_ALERTS", "")
    if not url:
        return
    top = fresh[:15]
    lines = [f"*Absurd squeeze outcomes* — {len(fresh)} NEW row(s) in the last {window_days} days "
             f"whose numbers cannot be true."]
    lines += [f"• `{r['ticker']}` {r['triggered_date']} {r['outcome']} — {', '.join(r['faults'])}"
              for r in top]
    if len(fresh) > len(top):
        lines.append(f"…and {len(fresh) - len(top)} more new.")
    lines.append(f"_{backlog} such rows exist in the last 400 days. They are excluded from Best Settings "
                 "and Back Test at read time, but are still being written._")
    try:
        requests.post(url, json={"text": "\n".join(lines)}, timeout=10)
    except Exception as exc:
        log.warning(f"absurd-outcome Slack post failed: {exc}")


def main():
    args = sys.argv[1:]
    # Epic-lookup diagnostic — run BEFORE the audit-batch parsing so it never triggers a price audit.
    if args and args[0] == "--lookup-epic":
        _lookup_epics(args[1:])
        return
    if args and args[0] == "--verify-epic":
        _verify_epics(args[1:])
        return
    if args and not args[0].isdigit():
        batch = args
    else:
        cap = int(args[0]) if args else MAX_BATCH
        batch = _pick_batch(cap)

    log.info(f"Auditing {len(batch)} tickers (30 daily candles each vs IG)...")
    rows, remaining = [], None
    for ticker in batch:
        try:
            r = _audit_ticker(ticker)
            remaining = r.pop("remaining", None) or remaining
            rows.append(r)
            log.info(f"  {ticker:<8} {r['verdict']:<18} close_dev={r['close_max_dev_pct']}% "
                     f"wicks={r['phantom_high_wicks']}+{r['phantom_low_wicks']}")
            if remaining is not None and remaining < ALLOWANCE_RESERVE:
                log.warning(f"IG allowance {remaining} below {ALLOWANCE_RESERVE} reserve — stopping batch early")
                break
        except Exception as e:
            log.warning(f"  {ticker}: audit failed — {e}")

    # Phase 2 — full-cache identity sweep (every cached epic, every market;
    # zero IG allowance). Runs after the price rotation so its findings join
    # the same weight-ordered digest.
    rows.extend(_identity_sweep_all_cached())

    if rows:
        _save(rows)
        _post_slack(rows, remaining)

    # Absurd-outcome sweep — no IG allowance, so it runs even when the price batch stopped early.
    # Alert on what is NEW. The backlog is ~1,800 rows and re-listing it nightly would be noise that
    # trains everyone to ignore the channel; it is carried as a single number so a rising trend is still
    # visible. Roughly 1-2 genuinely new rows appear per day, which is an alert worth reading.
    import datetime as _dt
    _WINDOW_DAYS = 2
    bad = _absurd_outcomes()
    cutoff = _dt.date.today() - _dt.timedelta(days=_WINDOW_DAYS)
    fresh = [r for r in bad if r["triggered_date"]
             and _dt.date.fromisoformat(r["triggered_date"][:10]) >= cutoff]
    if bad:
        log.warning(f"Absurd outcomes: {len(fresh)} new in {_WINDOW_DAYS}d, {len(bad)} in 400d")
        for r in fresh[:20]:
            log.warning(f"  {r['ticker']:<10} {r['triggered_date']} {r['outcome']:<10} "
                        f"{', '.join(r['faults'])}")
    else:
        log.info("Absurd outcomes: none")
    if fresh:
        _post_absurd_slack(fresh, len(bad), _WINDOW_DAYS)

    log.info(f"Audit complete: {len(rows)} tickers, allowance remaining {remaining}")
    try:   # record this run in the web app's Batch Activity (user 2026-08-11, P-12)
        from web_store import append_batch
        append_batch("cron-job.org", f"Data quality audit — {len(rows)} ticker(s)", by="cron")
    except Exception:
        pass


if __name__ == "__main__":
    main()
