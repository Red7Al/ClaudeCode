# ======================================================================================================================
# File:         instrument_dossier.py
# Author:       Alex Hind
# Created:      2026-06-15
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Single-instrument DOSSIER — pass in one ticker (e.g. RR.L) and get EVERYTHING the system would publish for it:
#   • HVF analysis  — direction, signal state, every timeframe the funnel appears on, entry/stop/target, R:R, quality
#   • X (Twitter)   — the exact tweet text + the post-card PNG (the production renderer, not a rebuild)
#   • Slack         — the X-draft Slack block as it is posted to #claude-twitter (tweet + card)
#   • Email         — the trade-open investment-case HTML + chart PNGs (the production email builders)
#
# Everything is rendered through the SAME production code paths the live system uses (intraday_signals._generate_x_drafts
# in collect mode, intraday_signals.render_x_post_card, trade_email._investment_case / build_charts) so the dossier never
# drifts from what Slack/email/X actually receive.
#
# IMPORTANT — this NEVER posts or sends anything. It writes artifacts to a local folder for review / manual use:
#       dossier\<TICKER>_<UTC-stamp>\
#           summary.txt      — HVF analysis + manifest (also printed to console)
#           tweet.txt        — the X tweet text (copy-paste ready)
#           card.png         — the X post-card image
#           slack.txt        — the X-draft Slack block layout (tweet + card reference)
#           email.html       — the investment-case email body (PREVIEW — no trade placed)
#           email_chart_N.png— the email's chart attachment(s)
#
# The email/Slack TRADE confirmations (options flow, COT, directors, …) are taken from the latest signal_log row for the
# ticker (what the last session computed) — running them live needs the full signal stack (scan_instrument) and the API
# keys that live in GitHub Secrets, so live re-computation is an Actions job, not a local one. HVF + X are fully live.
#
# Usage:
#   python instrument_dossier.py RR.L
#   python instrument_dossier.py NVDA
#
# Environment Variables Required:
#   SUPABASE_USER / SUPABASE_DB_PASSWORD   (optional — only to enrich with the latest signal_log confirmations)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-15  Alex Hind   Initial build (user 2026-06-15): one ticker in → all Slack/email/X artifacts + PNGs out,
#                                 rendered via the production code paths. No posting/sending.
# ======================================================================================================================

import io
import os
import sys
import json
import logging
from datetime import datetime, timezone

# Emoji-safe stdout (the cards/tweets contain ⚡📈 etc.) — mirrors generate_x_cards.py.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("instrument_dossier")

OUT_ROOT = "dossier"


# ----------------------------------------------------------------------------------------------------------------------
# Confirmation context — the last computed signals for this ticker (used by the email/Slack preview)
# ----------------------------------------------------------------------------------------------------------------------

def _latest_signal_log(ticker: str) -> dict:
    """
    Most recent signal_log row for the ticker → the confirmation fields the email/X
    builders read (options flow, COT, directors, VWAP, …). Returns {} if the DB is
    unavailable or the ticker has never been scanned — every consumer treats absent
    confirmations as simply not shown.
    """
    try:
        from db_pool import get_db
        db = get_db()
        try:
            # Same column set the X-draft path selects (the proven-correct list);
            # cot_score lives in cot_snapshot, NOT signal_log, so it is not selected.
            rows = db.run(
                """select options_bias, call_put_ratio, iv_rank, director_signal,
                          cot_bias, adx_signal, obv_signal, sector_etf,
                          sector_dir, senate_signal, senate_senator, vwap_position, vwap_pct
                     from signal_log
                    where ticker = :t
                    order by session_time desc
                    limit 1""", t=ticker)
        finally:
            db.close()
    except Exception as e:
        log.info(f"signal_log enrichment unavailable ({e}) — proceeding without confirmations")
        return {}
    if not rows:
        return {}
    k = ("options_bias", "call_put_ratio", "iv_rank", "director_signal", "cot_bias",
         "adx_signal", "obv_signal", "sector_etf", "sector_dir",
         "senate_signal", "senate_senator", "vwap_position", "vwap_pct")
    return dict(zip(k, rows[0]))


def _sig_for_email(ticker: str, r: dict, ctx: dict) -> dict:
    """
    Map the HVF scanner result (h3_level, stop_level, …) + the latest signal_log
    confirmations onto the hvf_-prefixed `sig` dict that trade_email expects. This is
    the same shape signals.scan_instrument builds, so the email renders identically.
    """
    sig = {
        "direction":        "BUY" if r.get("hvf_type") == "BULLISH" else "SELL",
        "hvf_type":         r.get("hvf_type"),
        "hvf_signal":       r.get("hvf_signal"),
        "hvf_h3_level":     r.get("h3_level"),
        "hvf_stop_level":   r.get("stop_level"),
        "hvf_target":       r.get("target"),
        "hvf_risk_reward":  r.get("risk_reward"),
        "hvf_quality":      r.get("pattern_quality"),
        "hvf_h1_level":     r.get("h1_level"),
        "hvf_h2_level":     r.get("h2_level"),
        "hvf_l1_level":     r.get("l1_level"),
        "hvf_l2_level":     r.get("l2_level"),
        "hvf_l3_level":     r.get("l3_level"),
        "hvf_h1_date":      r.get("h1_date"),
        "hvf_h2_date":      r.get("h2_date"),
        "hvf_h3_date":      r.get("h3_date"),
        "hvf_l1_date":      r.get("l1_date"),
        "hvf_l2_date":      r.get("l2_date"),
        "hvf_l3_date":      r.get("l3_date"),
        "current_price":    r.get("current_price"),
    }
    # Confirmations from the last session (shown when present; the builder skips blanks).
    for key in ("options_bias", "call_put_ratio", "iv_rank", "director_signal",
                "cot_bias", "adx_signal", "obv_signal", "sector_etf",
                "sector_dir", "senate_signal", "senate_senator", "vwap_position", "vwap_pct"):
        if ctx.get(key) is not None:
            sig[key] = ctx[key]
    return sig


# ----------------------------------------------------------------------------------------------------------------------
# HVF analysis summary (plain English, all timeframes)
# ----------------------------------------------------------------------------------------------------------------------

def _hvf_summary(ticker: str, name: str, r: dict) -> str:
    """Plain-English HVF read for the instrument — mirrors the HVF report line, in full."""
    def _g(v):
        return f"{v:g}" if isinstance(v, (int, float)) else "—"

    if not r.get("hvf_type"):
        return f"{ticker} ({name})\n  No qualifying HVF funnel on any timeframe right now."

    direction = "BULLISH (long)" if r.get("hvf_type") == "BULLISH" else "BEARISH (short)"
    rr   = r.get("risk_reward")
    rr_s = f"{rr:.1f}:1" if isinstance(rr, (int, float)) and rr else "—"
    tf   = (r.get("hvf_timeframe", "") or "").replace("daily-", "d")
    lines = [
        f"{ticker} ({name})",
        f"  Direction : {direction}",
        f"  Signal    : {r.get('hvf_signal', '—')}  (best timeframe {tf or '—'})",
        f"  Entry     : {_g(r.get('h3_level'))}   (break of H3)",
        f"  Stop      : {_g(r.get('stop_level'))}",
        f"  Target    : {_g(r.get('target'))}",
        f"  R:R       : {rr_s}",
        f"  Quality   : {r.get('pattern_quality', '—')}/100",
        f"  Now       : {_g(r.get('current_price'))}",
    ]
    others = r.get("mtf_timeframes") or []
    if others:
        tfs = ", ".join(
            f"{(c.get('hvf_timeframe','') or '').replace('daily-','d')}={c.get('hvf_signal','')}"
            for c in others)
        lines.append(f"  Also on   : {tfs}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------------
# Build & write the dossier
# ----------------------------------------------------------------------------------------------------------------------

def build_dossier(ticker: str) -> str:
    """Run the full single-instrument dossier; returns the output directory path."""
    from price_action import get_hvf_signal_mtf, get_trend_structure
    from intraday_signals import _resolve_name

    ticker = ticker.strip().upper()
    name   = _resolve_name(ticker)
    log.info(f"Building dossier for {ticker} ({name})…")

    # ── HVF (live) ────────────────────────────────────────────────────────────────────────────────────────────────────
    trend = get_trend_structure(ticker)
    r = get_hvf_signal_mtf(ticker, trend_hint=trend)
    r["ticker"] = ticker

    stamp   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_dir = os.path.join(OUT_ROOT, f"{ticker.replace('.', '_')}_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    summary = _hvf_summary(ticker, name, r)
    manifest = [summary, ""]

    has_pattern = bool(r.get("hvf_type"))
    ctx = _latest_signal_log(ticker)

    # ── X (Twitter) — exact production tweet + card, no posting ───────────────────────────────────────────────────────
    if has_pattern:
        try:
            from intraday_signals import _generate_x_drafts
            drafts = _generate_x_drafts([r], post=False, collect=True) or []
            if drafts:
                d = drafts[0]
                with open(os.path.join(out_dir, "tweet.txt"), "w", encoding="utf-8") as f:
                    f.write(d["tweet"])
                manifest.append(f"X tweet ({len(d['tweet'])} chars) → tweet.txt")
                if d.get("png"):
                    with open(os.path.join(out_dir, "card.png"), "wb") as f:
                        f.write(d["png"])
                    manifest.append(f"X post-card ({len(d['png']):,} bytes) → card.png")
                # Slack X-draft block layout (what #claude-twitter receives).
                slack_txt = (
                    f"X Draft — {ticker} ({name})  {d.get('direction','').title()} · "
                    f"{(d.get('sig_desc') or '').title()}\n"
                    f"R:R {d.get('rr_str','—')} | Quality {d.get('quality') or '—'} | {d.get('tf_raw') or '—'}\n"
                    f"{'-' * 60}\n{d['tweet']}\n{'-' * 60}\n[card.png attached]\n")
                with open(os.path.join(out_dir, "slack.txt"), "w", encoding="utf-8") as f:
                    f.write(slack_txt)
                manifest.append("Slack X-draft block → slack.txt")
            else:
                manifest.append("X tweet: not generated (no draft produced)")
        except Exception as e:
            log.warning(f"X artifacts failed: {e}")
            manifest.append(f"X tweet/card: FAILED — {e}")
    else:
        manifest.append("X tweet/card: skipped (no HVF pattern)")

    # ── Email — investment-case HTML + chart PNGs (PREVIEW, no send) ──────────────────────────────────────────────────
    if has_pattern:
        try:
            import base64 as _b64
            from trade_email import _investment_case, build_charts
            direction = "BUY" if r.get("hvf_type") == "BULLISH" else "SELL"
            sig   = _sig_for_email(ticker, r, ctx)
            trade = {"level": r.get("h3_level"), "stop_level": r.get("stop_level"),
                     "limit_level": r.get("target"), "deal_id": "PREVIEW",
                     "current_price": r.get("current_price")}
            text, html = _investment_case(ticker, direction, "(preview)", "DOSSIER",
                                          sig, trade, event="HVF setup (preview — no trade placed)")
            charts = build_charts(ticker, sig, trade)   # [(cid, filename, png_bytes), …]
            # Inline charts as base64 data URIs so email.html renders standalone in a
            # browser (the live email uses cid: attachments — those need a mail client).
            html_full = html + "".join(
                f'<br><img src="data:image/png;base64,{_b64.b64encode(png).decode()}" '
                f'style="max-width:720px;border:1px solid #ddd">'
                for _, _, png in charts)
            with open(os.path.join(out_dir, "email.html"), "w", encoding="utf-8") as f:
                f.write(html_full)
            manifest.append("Email investment case → email.html")
            for i, (_cid, _fname, png) in enumerate(charts, 1):
                if isinstance(png, (bytes, bytearray)):
                    with open(os.path.join(out_dir, f"email_chart_{i}.png"), "wb") as f:
                        f.write(png)
                    manifest.append(f"Email chart {i} → email_chart_{i}.png")
        except Exception as e:
            log.warning(f"Email artifacts failed: {e}")
            manifest.append(f"Email: FAILED — {e}")
    else:
        manifest.append("Email: skipped (no HVF pattern)")

    # ── summary.txt + console ─────────────────────────────────────────────────────────────────────────────────────────
    header = (f"INSTRUMENT DOSSIER — {ticker} ({name})\n"
              f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  "
              f"(no Slack/email/X sent — artifacts only)\n"
              f"{'=' * 80}\n\n")
    body = header + "\n".join(manifest) + "\n"
    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(body)
    print("\n" + body)
    log.info(f"Dossier written to {out_dir}\\")
    return out_dir


def main():
    if len(sys.argv) < 2:
        print("Usage: python instrument_dossier.py <TICKER>   e.g. RR.L")
        return
    for ticker in sys.argv[1:]:
        try:
            build_dossier(ticker)
        except Exception as e:
            log.error(f"Dossier failed for {ticker}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
