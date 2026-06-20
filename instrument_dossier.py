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
# 1.6.0   2026-06-19  Alex Hind   All HVF comments (user 2026-06-19, Current #5): slack.txt + summary now list EVERY HVF
#                                 confirmation in full wording (from the X-draft collect dict's "justifications"), not just
#                                 the few that fit the 280-char tweet.
# 1.5.0   2026-06-15  Alex Hind   Technical read section (user 2026-06-15): MA10/30/50, RSI14, Stoch(9,6), ATR14, ADX14,
#                                 CCI20 as Buy/Sell/Hold + dividend growth, via technical_summary.py. Supplementary
#                                 context only — does not gate a trade or touch HVF detection.
# 1.4.0   2026-06-15  Alex Hind   --check-slack flag (user 2026-06-15): reports whether SLACK_TWITTER / SLACK_BOT_TOKEN /
#                                 SLACK_TWITTER_CHANNEL_ID are visible to the process (values HIDDEN) so a posting session
#                                 can be confirmed before a real run; loads .env the same way a run does. No run performed.
# 1.3.0   2026-06-15  Alex Hind   When SLACK_TWITTER is present (i.e. running in the Instrument Dossier GitHub Action),
#                                 the dossier now POSTS the tweet + card to #arw-claude-twitter via the same
#                                 _generate_x_drafts path (user 2026-06-15). Local runs stay generate-only (the secret is
#                                 never set locally), so the no-post-from-local rule holds. New workflow + skill updated.
# 1.2.0   2026-06-15  Alex Hind   HVF summary now prints FULL figures (Entry/Stop/Target/R:R/Q) for EVERY timeframe the
#                                 funnel appears on, in weight order (user 2026-06-15), replacing the one-line "Also on".
#                                 Primary row is flagged; non-primary rows show raw detection levels.
# 1.1.0   2026-06-15  Alex Hind   slack.txt now surfaces the tight-stop ⚠️ caution from the X-draft collect dict (#9b), so
#                                 a structurally-untradeable funnel is flagged in the dossier too.
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
    # Full figures for EVERY timeframe the funnel appears on (user 2026-06-15), in
    # weight order. The primary (best) timeframe carries the AMP1-anchored / IG-
    # validated target+R:R; the others show their raw per-timeframe detection levels.
    tfs = r.get("mtf_timeframes") or []
    if tfs:
        lines.append("")
        lines.append("  By date range (each timeframe the funnel appears on):")
        for c in tfs:
            tf_lbl = (c.get("hvf_timeframe", "") or "").replace("daily-", "d")
            rr_c   = c.get("risk_reward")
            rr_cs  = f"{rr_c:.1f}:1" if isinstance(rr_c, (int, float)) and rr_c else "—"
            primary = "  ◄ primary (exhaustion-anchored, IG-validated)" if tf_lbl == tf else ""
            lines.append(f"    [{tf_lbl}] {c.get('hvf_signal', '—')}{primary}")
            lines.append(f"        Entry {_g(c.get('h3_level'))}   Stop {_g(c.get('stop_level'))}   "
                         f"Target {_g(c.get('target'))}   R:R {rr_cs}   Q {c.get('pattern_quality', '—')}/100")
        lines.append("    (Non-primary rows show RAW detection figures — only the primary's target/R:R is "
                     "exhaustion-anchored and IG-validated.)")
    return "\n".join(lines)


def _technical_block(ticker: str) -> str:
    """
    Supplementary technical read (user 2026-06-15) — MA10/30/50, RSI14, Stoch(9,6),
    ATR14, ADX14, CCI20 as Buy/Sell/Hold + dividend growth. CONTEXT only: it does not
    gate a trade or change HVF detection. ATR is volatility (no Buy/Sell).
    """
    from technical_summary import get_technical_summary
    ts = get_technical_summary(ticker)
    lines = ["Technical read (supplementary context — not a trade signal):"]
    if ts.get("error") or not ts.get("indicators"):
        lines.append(f"    unavailable ({ts.get('error') or 'no data'})")
        return "\n".join(lines)
    for name, val, rating in ts["indicators"]:
        tag = f"[{rating}]" if rating != "—" else "[ vol ]"
        lines.append(f"    {name:14} {str(val):18} {tag}")
    dg = ts.get("dividend_growth_pct")
    dg_str = f"{dg:+.1f}% YoY" if dg is not None else "n/a (non-payer)"
    lines.append(f"    {'Dividend growth':14} {dg_str:18}")
    lines.append(f"    → {ts['buy']} Buy / {ts['sell']} Sell / {ts['hold']} Hold")
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
    manifest = [summary, "", _technical_block(ticker), ""]

    has_pattern = bool(r.get("hvf_type"))
    ctx = _latest_signal_log(ticker)

    # ── X (Twitter) — exact production tweet + card, no posting ───────────────────────────────────────────────────────
    if has_pattern:
        try:
            from intraday_signals import _generate_x_drafts
            # Post the tweet + card to #arw-claude-twitter ONLY when SLACK_TWITTER is set —
            # i.e. running in GitHub Actions (user 2026-06-15: "when the dossier runs also
            # run the tweet process to the slack channel"). Locally SLACK_TWITTER is never
            # set (memory: secrets_and_x_delivery — the local machine cannot post), so a
            # local run stays generate-only. Same production path; never an MCP substitute.
            _post_x = bool(os.environ.get("SLACK_TWITTER"))
            drafts = _generate_x_drafts([r], post=_post_x, collect=True) or []
            if drafts:
                d = drafts[0]
                with open(os.path.join(out_dir, "tweet.txt"), "w", encoding="utf-8") as f:
                    f.write(d["tweet"])
                manifest.append(f"X tweet ({len(d['tweet'])} chars) → tweet.txt")
                if d.get("png"):
                    with open(os.path.join(out_dir, "card.png"), "wb") as f:
                        f.write(d["png"])
                    manifest.append(f"X post-card ({len(d['png']):,} bytes) → card.png")
                # All HVF comments (user 2026-06-19, Current #5): the dossier shows EVERY
                # confirmation, not just the few the 280-char tweet fits. Sourced from the
                # collect dict's full-wording justifications (production path — no rebuild).
                _justs = d.get("justifications") or []
                _hvf_comments = ("HVF confirmations (all " + str(len(_justs)) + "):\n"
                                 + "\n".join(f"  • {j}" for j in _justs) + "\n") if _justs else \
                                "HVF confirmations: none recorded for this setup\n"
                # Slack X-draft block layout (what #claude-twitter receives).
                _caution = f"{d.get('caution')}\n" if d.get("caution") else ""
                slack_txt = (
                    f"X Draft — {ticker} ({name})  {d.get('direction','').title()} · "
                    f"{(d.get('sig_desc') or '').title()}\n"
                    f"R:R {d.get('rr_str','—')} | Quality {d.get('quality') or '—'} | {d.get('tf_raw') or '—'}\n"
                    f"{_caution}{_hvf_comments}{'-' * 60}\n{d['tweet']}\n{'-' * 60}\n[card.png attached]\n")
                # Also surface the comments in the dossier manifest/summary.txt.
                manifest.append(_hvf_comments.rstrip())
                with open(os.path.join(out_dir, "slack.txt"), "w", encoding="utf-8") as f:
                    f.write(slack_txt)
                manifest.append("Slack X-draft block → slack.txt"
                                + ("  (POSTED to #arw-claude-twitter)" if _post_x
                                   else "  (not posted — local run, no SLACK_TWITTER)"))
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


def _check_slack() -> None:
    """
    --check-slack: report whether the X-posting keys are visible to THIS process,
    so you can confirm a terminal session is set up BEFORE a real run — WITHOUT ever
    printing the values. Mirrors what a run sees (loads .env the same way the code
    does). SLACK_TWITTER posts the tweet text; the card IMAGE needs BOTH
    SLACK_BOT_TOKEN and SLACK_TWITTER_CHANNEL_ID (a webhook can't upload a PNG).
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass
    keys = [
        ("SLACK_TWITTER",            "tweet text (webhook)"),
        ("SLACK_BOT_TOKEN",          "card image upload"),
        ("SLACK_TWITTER_CHANNEL_ID", "card image target channel"),
    ]
    print("Slack X-posting keys visible to this process (values hidden):")
    present = {}
    for name, role in keys:
        ok = bool(os.environ.get(name))
        present[name] = ok
        print(f"  [{'OK ' if ok else 'MISSING'}] {name:24} — {role}")
    text_ok  = present["SLACK_TWITTER"]
    image_ok = present["SLACK_BOT_TOKEN"] and present["SLACK_TWITTER_CHANNEL_ID"]
    print()
    if text_ok and image_ok:
        print("→ Ready: a dossier run will POST the tweet + card to #arw-claude-twitter.")
    elif text_ok:
        print("→ Partial: the tweet TEXT will post, but NOT the card image — set "
              "SLACK_BOT_TOKEN + SLACK_TWITTER_CHANNEL_ID for the graph.")
    else:
        print("→ Not set: a run stays LOCAL (artifacts on disk, nothing posted). Set the "
              "keys in this terminal session, then re-run --check-slack.")


def main():
    args = sys.argv[1:]
    if "--check-slack" in args:
        _check_slack()
        return
    if not args:
        print("Usage: python instrument_dossier.py <TICKER> [<TICKER> ...]   e.g. RR.L")
        print("       python instrument_dossier.py --check-slack            (verify posting keys, no run)")
        return
    for ticker in args:
        try:
            build_dossier(ticker)
        except Exception as e:
            log.error(f"Dossier failed for {ticker}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
