# =============================================================================
# File:         trade_email.py
# Author:       Alex Hind
# Created:      2026-06-09
#
# Description:
# -----------------------------------------------------------------------------
# Email the investment case + charts to a configurable recipient list whenever a
# trade is opened (user directive 2026-06-09). The investment case explains WHY:
# each fired primary/confirmation is shown with its supporting detail — Options
# flow (call/put + IV rank + GEX), Director buys (insider count + window + names)
# and COT positioning (score, extremes, OI, net + WoW change). Charts: price
# history with the HVF funnel overlaid on the real timeline, volume history, and a
# standalone HVF funnel schematic (entry/stop/target).
#
# Sender:     Resend HTTP API (RESEND_API_KEY) preferred; Yahoo SMTP (465/SSL,
#             YAHOO_USER + YAHOO_APP_PASSWORD) as fallback.
# Recipients: config.EMAIL_RECIPIENTS (default ["eahind@yahoo.co.uk"]).
#
# FAIL-SAFE: send_trade_email() never raises — a mail/chart failure must NEVER
# break trade placement. All work is wrapped; failures are logged (and the caller
# is unaffected).
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-09  Alex Hind   Initial build — Yahoo SMTP, investment-case body,
#                                 inline price / volume / HVF-funnel charts.
# 1.1.0   2026-06-10  Alex Hind   Show supporting detail for Options flow, Director
#                                 buys and COT positioning in the investment case;
#                                 overlay the HVF funnel on the price-history chart
#                                 (real timeline, via pivot dates). User 2026-06-10.
# =============================================================================

import os
import io
import ssl
import smtplib
import logging
import requests
from email.message import EmailMessage
from datetime import datetime, timezone

log = logging.getLogger("trade_email")

YAHOO_SMTP_HOST = "smtp.mail.yahoo.com"
YAHOO_SMTP_PORT = 465   # implicit SSL — Yahoo drops 587/STARTTLS ("Connection unexpectedly closed")


# ---------------------------------------------------------------------------
# Charts (best-effort; never raises)
# ---------------------------------------------------------------------------

def _fig_png(fig) -> bytes:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _num(v):
    try:
        f = float(v)
        return f if f else None
    except (TypeError, ValueError):
        return None


def _overlay_hvf_funnel(ax, hist, levels, date_map):
    """
    Overlay the HVF funnel on a price chart's REAL date axis: the descending
    lower-highs line (H1→H3) and the ascending higher-lows line (L1→L3), pivots
    marked. Uses the pivot DATES when the sig carries them (exact placement on the
    timeline); otherwise approximates the funnel across the most recent ~40 bars so
    a funnel is always shown. Best-effort — never raises (a failure just omits the
    overlay; the price line still renders).
    """
    try:
        import pandas as pd
        h1, h2, h3, l1, l2, l3 = levels
        if not all((h1, h3, l1, l3)) or len(hist.index) < 3:
            return
        idx = hist.index

        def _xpos(date_str):
            """Snap a 'YYYY-MM-DD' pivot date to the nearest bar on the price axis."""
            if not date_str:
                return None
            try:
                ts = pd.Timestamp(date_str)
                if idx.tz is not None and ts.tzinfo is None:
                    ts = ts.tz_localize(idx.tz)
                elif idx.tz is None and ts.tzinfo is not None:
                    ts = ts.tz_localize(None)
                return idx[idx.get_indexer([ts], method="nearest")[0]]
            except Exception:
                return None

        highs = [(_xpos(date_map.get("hvf_h1_date")), h1),
                 (_xpos(date_map.get("hvf_h2_date")), h2),
                 (_xpos(date_map.get("hvf_h3_date")), h3)]
        lows  = [(_xpos(date_map.get("hvf_l1_date")), l1),
                 (_xpos(date_map.get("hvf_l2_date")), l2),
                 (_xpos(date_map.get("hvf_l3_date")), l3)]
        highs = [(x, y) for x, y in highs if x is not None and y]
        lows  = [(x, y) for x, y in lows  if x is not None and y]

        if len(highs) >= 2 and len(lows) >= 2:
            h_labels = ["H1", "H2", "H3"][:len(highs)]
            l_labels = ["L1", "L2", "L3"][:len(lows)]
        else:
            # Fallback: draw the converging funnel over the last ~40 bars (apex = latest bar).
            n = len(idx)
            span = min(40, max(10, n // 4))
            x0, x1 = idx[max(0, n - span)], idx[-1]
            highs = [(x0, h1), (x1, h3)]
            lows  = [(x0, l1), (x1, l3)]
            h_labels, l_labels = ["H1", "H3"], ["L1", "L3"]

        hx = [x for x, _ in highs]; hy = [y for _, y in highs]
        lx = [x for x, _ in lows];  ly = [y for _, y in lows]
        ax.plot(hx, hy, "r--o", lw=1.4, ms=5, label="HVF lower highs (H1>H2>H3)")
        ax.plot(lx, ly, "g--o", lw=1.4, ms=5, label="HVF higher lows (L1<L2<L3)")
        try:
            ax.fill_between([hx[0], hx[-1]], [hy[0], hy[-1]], [ly[0], ly[-1]],
                            color="grey", alpha=0.10, zorder=0)
        except Exception:
            pass
        for (x, y), lab in zip(highs, h_labels):
            ax.annotate(lab, (x, y), textcoords="offset points", xytext=(0, 6),
                        fontsize=7, color="red", ha="center")
        for (x, y), lab in zip(lows, l_labels):
            ax.annotate(lab, (x, y), textcoords="offset points", xytext=(0, -12),
                        fontsize=7, color="green", ha="center")
    except Exception:
        return


def build_charts(ticker: str, sig: dict, trade: dict) -> list:
    """
    Return [(cid, filename, png_bytes), ...] for the trade-open email:
      1. Price history (6mo) + entry/stop/target AND the HVF funnel overlaid on the
         REAL price timeline (lower-highs H1→H3, higher-lows L1→L3).
      2. Volume history (6mo).
      3. HVF funnel schematic (clean close-up with R:R / quality).
    Best-effort: any failure yields fewer (or no) charts, never an exception.
    """
    charts = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import yfinance as yf
        from config import YAHOO_MAP

        entry = _num(trade.get("level"))
        stop  = _num(trade.get("stop_level"))
        targ  = _num(trade.get("limit_level"))

        # ── Gather the HVF funnel ONCE (levels + pivot dates) — used by BOTH the
        #    price-chart overlay (#1) and the standalone schematic (#3). ──────────
        def _lvl(*keys):
            for k in keys:
                v = _num(sig.get(k))
                if v:
                    return v
            return None
        h1 = _lvl("hvf_h1_level", "h1_level"); h2 = _lvl("hvf_h2_level", "h2_level"); h3 = _lvl("hvf_h3_level", "h3_level")
        l1 = _lvl("hvf_l1_level", "l1_level"); l2 = _lvl("hvf_l2_level", "l2_level"); l3 = _lvl("hvf_l3_level", "l3_level")
        dates = {k: sig.get(k) for k in (
            "hvf_h1_date", "hvf_h2_date", "hvf_h3_date", "hvf_l1_date", "hvf_l2_date", "hvf_l3_date")}
        if not all((h1, h3, l1, l3)):
            # Re-derive the funnel (levels + dates) from price_action if sig lacked it.
            try:
                from price_action import get_hvf_signal_mtf, get_trend_structure
                hv = get_hvf_signal_mtf(ticker, trend_hint=get_trend_structure(ticker))
                h1 = h1 or _num(hv.get("h1_level")); h2 = h2 or _num(hv.get("h2_level")); h3 = h3 or _num(hv.get("h3_level"))
                l1 = l1 or _num(hv.get("l1_level")); l2 = l2 or _num(hv.get("l2_level")); l3 = l3 or _num(hv.get("l3_level"))
                stop = stop or _num(hv.get("stop_level")); targ = targ or _num(hv.get("target"))
                if not any(dates.values()):
                    dates = {f"hvf_{p}_date": hv.get(f"{p}_date") for p in ("h1", "h2", "h3", "l1", "l2", "l3")}
            except Exception:
                pass

        # Widen the chart window if the funnel's oldest pivot predates 6 months
        # (weekly funnels can span >1 year) so the entire funnel is visible rather
        # than clamped to the chart's left edge. Defaults to 6 months.
        period, plabel = "6mo", "6 months"
        try:
            import pandas as pd
            _ds = [pd.Timestamp(d) for d in dates.values() if d]
            if _ds:
                _months = (pd.Timestamp.now() - min(_ds)).days / 30.4
                if   _months > 22:  period, plabel = "5y", "5 years"
                elif _months > 11:  period, plabel = "2y", "2 years"
                elif _months > 5.5: period, plabel = "1y", "1 year"
        except Exception:
            period, plabel = "6mo", "6 months"

        yt = YAHOO_MAP.get(ticker, ticker)
        try:
            hist = yf.Ticker(yt).history(period=period, interval="1d")
        except Exception:
            hist = None

        if hist is not None and not hist.empty:
            # 1) Price history + entry/stop/target + HVF funnel overlay on the real axis
            fig, ax = plt.subplots(figsize=(9, 4.5))
            ax.plot(hist.index, hist["Close"], color="#1f77b4", lw=1.3, label="Close")
            _overlay_hvf_funnel(ax, hist, (h1, h2, h3, l1, l2, l3), dates)
            for lvl, lab, col in ((entry, "Entry", "green"), (stop, "Stop", "red"), (targ, "Target", "orange")):
                if lvl:
                    ax.axhline(lvl, color=col, ls="--", lw=1, label=f"{lab} {lvl:g}")
            ax.set_title(f"{ticker} — Price history ({plabel}) with HVF funnel")
            ax.legend(fontsize=7, loc="best")
            ax.grid(alpha=0.3)
            charts.append(("price", "price.png", _fig_png(fig)))

            # 2) Volume history
            fig, ax = plt.subplots(figsize=(9, 3))
            ax.bar(hist.index, hist["Volume"], color="#888888", width=1.0)
            ax.set_title(f"{ticker} — Volume history ({plabel})")
            ax.grid(alpha=0.3)
            charts.append(("volume", "volume.png", _fig_png(fig)))

        # 3) HVF funnel schematic — three descending lower-highs (H1>H2>H3) and three
        # ascending higher-lows (L1<L2<L3) converging into the apex, with entry (H3) /
        # stop / target. The visual definition of the Hunt Volatility Funnel.
        if all((h1, h3, l1, l3)):
            fig, ax = plt.subplots(figsize=(8.5, 4.5))
            # Plot the three highs and three lows at pivot positions 1, 2, 3 (H2/L2
            # included when available, else the line spans 1st→3rd).
            hx = [0, 1, 2] if h2 else [0, 2]
            hy = [h1, h2, h3] if h2 else [h1, h3]
            lx = [0, 1, 2] if l2 else [0, 2]
            ly = [l1, l2, l3] if l2 else [l1, l3]
            ax.plot(hx, hy, "r-o", lw=1.8, label="Lower highs  H1 > H2 > H3")
            ax.plot(lx, ly, "g-o", lw=1.8, label="Higher lows  L1 < L2 < L3")
            ax.fill_between([0, 2], [h1, h3], [l1, l3], color="grey", alpha=0.12)
            for xx, yy, lab, dy, col in [
                (0, h1, "H1", 7, "red"), (1, h2, "H2", 7, "red"), (2, h3, "H3", 7, "red"),
                (0, l1, "L1", -13, "green"), (1, l2, "L2", -13, "green"), (2, l3, "L3", -13, "green")]:
                if yy:
                    ax.annotate(f"{lab} {yy:g}", (xx, yy), textcoords="offset points",
                                xytext=(0, dy), fontsize=7, color=col, ha="center")
            if entry: ax.axhline(entry, color="blue",   ls="--", lw=1.1, label=f"Entry (H3) {entry:g}")
            if stop:  ax.axhline(stop,  color="red",    ls=":",  lw=1,   label=f"Stop {stop:g}")
            if targ:  ax.axhline(targ,  color="orange", ls=":",  lw=1,   label=f"Target {targ:g}")
            rr = sig.get("hvf_risk_reward")
            q  = sig.get("hvf_quality")
            ax.set_title(f"{ticker} — HVF funnel ({sig.get('hvf_type','')} {sig.get('hvf_signal','')}"
                         + (f", R:R {rr}:1" if rr else "") + (f", quality {q}" if q else "") + ")")
            ax.set_xticks([0, 1, 2] if h2 else [0, 2])
            ax.set_xticklabels(["1st pivot", "2nd pivot", "3rd (entry)"] if h2 else ["1st pivot", "3rd (entry)"])
            ax.legend(fontsize=7, loc="best")
            ax.grid(alpha=0.3)
            charts.append(("hvf", "hvf.png", _fig_png(fig)))
    except Exception as e:
        log.warning(f"trade-email chart generation failed for {ticker}: {e}")
    return charts


# ---------------------------------------------------------------------------
# Investment-case body
# ---------------------------------------------------------------------------

def _investment_case(ticker: str, direction: str, size, session_name: str,
                     sig: dict, trade: dict) -> tuple:
    try:
        from notify import fmt
        name = fmt(ticker)
    except Exception:
        name = ticker

    entry = trade.get("level", "—")
    stop  = trade.get("stop_level", "—")
    targ  = trade.get("limit_level", "—")
    rr    = sig.get("hvf_risk_reward")
    rr_s  = f"{rr:.1f}:1" if isinstance(rr, (int, float)) and rr else "—"

    rows = [
        ("Instrument", name), ("Direction", direction), ("Size", size),
        ("Session", session_name), ("Entry", entry), ("Stop", stop),
        ("Target", targ), ("R:R", rr_s),
    ]
    # Named signals that fired (from scan_instrument). Explains WHY, not just counts.
    primaries     = list(sig.get("primaries_fired") or [])
    confirmations = list(sig.get("confirmations_fired") or [])
    pc = sig.get("primary_count", len(primaries))
    cc = sig.get("confirmation_count", len(confirmations))

    # Fallback for sigs without the named lists — derive from raw fields, carrying
    # the same supporting detail the source builds (options call/put + IV rank; COT
    # score/extremes/OI; director count + names).
    if not primaries:
        if sig.get("options_bias") in ("BULLISH", "BEARISH"):
            _ob = []
            if sig.get("call_put_ratio") is not None: _ob.append(f"call/put {sig['call_put_ratio']:.2f}")
            if sig.get("iv_rank") is not None:         _ob.append(f"IV rank {sig['iv_rank']}%")
            primaries.append(f"Options flow {sig['options_bias']}" + (f" — {', '.join(_ob)}" if _ob else ""))
        if sig.get("bb_breakout_dir") in ("BULLISH", "BEARISH"):
            primaries.append(f"BB breakout {sig['bb_breakout_dir']}")
        if sig.get("hvf_type"):
            primaries.append(f"HVF {sig.get('hvf_type')} {sig.get('hvf_signal','')}".strip())
    if not confirmations:
        if sig.get("director_signal"):
            confirmations.append("Director buys — " + (sig.get("director_detail") or "insider cluster (Form 4)"))
        if sig.get("cot_bias") in ("BULLISH", "BEARISH"):
            _cb = []
            if sig.get("cot_score"): _cb.append(f"score {sig['cot_score']:+.0f}")
            if sig.get("cot_comm_extreme") and sig["cot_comm_extreme"] != "NORMAL":
                _cb.append(f"commercials {sig['cot_comm_extreme']}")
            if sig.get("cot_oi_signal") and sig["cot_oi_signal"] != "NEUTRAL":
                _cb.append(f"OI {sig['cot_oi_signal']}")
            confirmations.append(f"COT positioning {sig['cot_bias']}" + (f" — {', '.join(_cb)}" if _cb else ""))

    rationale = (f"{pc} primary signal{'s' if pc != 1 else ''} pointed {direction}"
                 + (f", backed by {cc} confirmation{'s' if cc != 1 else ''}" if cc else ""))

    text = [f"Trade opened: {name} {direction}", "=" * 44]
    text += [f"{k:14}: {v}" for k, v in rows]
    text += ["", f"WHY THIS IS A {direction}: {rationale}.", "", f"Primary signals ({pc}):"]
    text += ([f"  • {p}" for p in primaries] or ["  • (none recorded)"])
    text += ["", f"Confirmations ({cc}):"]
    text += ([f"  • {c}" for c in confirmations] or ["  • (none recorded)"])
    if sig.get("pa_verdict"):
        text += ["", f"Price-action verdict: {sig.get('pa_verdict')} (score {sig.get('pa_score', '—')})"]
    text += ["", "Charts attached: price history, volume history, HVF funnel.",
             f"\nGenerated {datetime.now(timezone.utc):%d %b %Y %H:%M UTC} by EndToEndTrading."]
    text = "\n".join(str(t) for t in text)

    def _tr(k, v):
        return f"<tr><td style='padding:2px 10px;color:#555'>{k}</td><td style='padding:2px 10px'><b>{v}</b></td></tr>"
    def _li(items):
        return "".join(f"<li>{i}</li>" for i in items) or "<li><i>none recorded</i></li>"
    html = (
        f"<h2>Trade opened — {name} {direction}</h2>"
        f"<table>{''.join(_tr(k, v) for k, v in rows)}</table>"
        f"<p><b>Why this is a {direction}:</b> {rationale}.</p>"
        f"<h3>Primary signals ({pc})</h3><ul>{_li(primaries)}</ul>"
        f"<h3>Confirmations ({cc})</h3><ul>{_li(confirmations)}</ul>"
        + (f"<p><b>Price-action verdict:</b> {sig.get('pa_verdict')} "
           f"(score {sig.get('pa_score','—')})</p>" if sig.get('pa_verdict') else "")
    )
    return text, html


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def _html_with_inline(html: str, charts: list) -> str:
    return html + "".join(
        f'<br><img src="cid:{cid}" style="max-width:720px;border:1px solid #ddd">'
        for cid, _, _ in charts)


def _send_via_resend(subject, text, html, charts, rcpts) -> bool:
    """Send via the Resend HTTP API (no SMTP, no app password). Charts attached inline."""
    import base64
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        return False
    # Resend test mode (no verified domain) sends FROM onboarding@resend.dev and only
    # delivers to the account-owner address. Set RESEND_FROM once a domain is verified.
    sender = os.environ.get("RESEND_FROM", "onboarding@resend.dev")
    payload = {
        "from": sender, "to": rcpts, "subject": subject,
        "text": text, "html": _html_with_inline(html, charts),
        "attachments": [
            {"filename": fname, "content": base64.b64encode(png).decode(), "content_id": cid}
            for cid, fname, png in charts
        ],
    }
    r = requests.post("https://api.resend.com/emails",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json=payload, timeout=30)
    if r.status_code >= 300:
        log.error(f"Resend API {r.status_code}: {r.text[:300]}")
        return False
    log.info(f"Trade email sent via Resend to {rcpts} ({len(charts)} charts)")
    return True


def _send_via_yahoo(subject, text, html, charts, rcpts) -> bool:
    """Fallback: Yahoo SMTP (465/SSL) with a 16-char app password (spaces stripped)."""
    user = os.environ.get("YAHOO_USER", "").strip()
    pw   = os.environ.get("YAHOO_APP_PASSWORD", "").replace(" ", "").strip()
    if not (user and pw):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = ", ".join(rcpts)
    msg.set_content(text)
    msg.add_alternative(_html_with_inline(html, charts), subtype="html")
    html_part = msg.get_payload()[-1]
    for cid, fname, png in charts:
        html_part.add_related(png, maintype="image", subtype="png", cid=f"<{cid}>", filename=fname)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(YAHOO_SMTP_HOST, YAHOO_SMTP_PORT, context=ctx, timeout=30) as s:
        if os.environ.get("EMAIL_DEBUG"):
            s.set_debuglevel(1)
        s.ehlo()
        s.login(user, pw)
        s.send_message(msg)
    log.info(f"Trade email sent via Yahoo SMTP to {rcpts} ({len(charts)} charts)")
    return True


def send_trade_email(ticker: str, direction: str, sig: dict, trade: dict,
                     size=None, session_name: str = "", recipients=None) -> bool:
    """
    Email the investment case + charts for a newly opened trade. Prefers the Resend
    HTTP API (RESEND_API_KEY); falls back to Yahoo SMTP. FAIL-SAFE: returns False and
    logs on any problem; never raises into the trade-placement path.
    """
    try:
        try:
            from config import EMAIL_RECIPIENTS
        except Exception:
            EMAIL_RECIPIENTS = ["eahind@yahoo.co.uk"]
        rcpts = recipients or EMAIL_RECIPIENTS
        if not rcpts:
            return False

        subject = f"Trade opened: {ticker} {direction} @ {trade.get('level', '?')}"
        text, html = _investment_case(ticker, direction, size, session_name, sig, trade)
        charts = build_charts(ticker, sig, trade)

        if os.environ.get("RESEND_API_KEY"):
            return _send_via_resend(subject, text, html, charts, rcpts)
        if os.environ.get("YAHOO_USER") and os.environ.get("YAHOO_APP_PASSWORD"):
            return _send_via_yahoo(subject, text, html, charts, rcpts)
        log.warning("No email sender configured (RESEND_API_KEY or YAHOO_*) — trade email skipped")
        return False
    except Exception as e:
        log.error(f"Trade email failed for {ticker}: {type(e).__name__}: {e!r}")
        return False


if __name__ == "__main__":
    # Smoke test: build charts (no send). With --send, send a REAL sample email via
    # Yahoo SMTP (needs YAHOO_USER/YAHOO_APP_PASSWORD) to verify end-to-end delivery.
    import sys
    logging.basicConfig(level=logging.INFO)
    _sig = {"hvf_type": "BULLISH", "hvf_signal": "TRIGGERED", "hvf_risk_reward": 5.75,
            "hvf_quality": 82, "hvf_h1_level": 268.0, "hvf_h2_level": 262.0, "hvf_h3_level": 256.6,
            "hvf_l1_level": 238.0, "hvf_l2_level": 243.0, "hvf_l3_level": 248.0, "hvf_stop_level": 246.0,
            "hvf_target": 300.0, "pa_verdict": "CONFIRM_LONG", "pa_score": 45,
            # Pivot dates — exercise the funnel-on-price overlay (within the 6mo window).
            "hvf_h1_date": "2026-03-02", "hvf_h2_date": "2026-04-06", "hvf_h3_date": "2026-05-18",
            "hvf_l1_date": "2026-03-16", "hvf_l2_date": "2026-04-20", "hvf_l3_date": "2026-05-26",
            # Structured fields (exercise the fallback detail path too).
            "call_put_ratio": 1.85, "iv_rank": 72, "options_bias": "BULLISH",
            "cot_bias": "BULLISH", "cot_score": 42.0, "cot_comm_extreme": "EXTREME_LONG",
            "cot_oi_signal": "rising", "cot_comm_net": 12340, "cot_comm_net_change": 2100,
            "director_signal": True, "director_count": 3,
            "director_detail": "3 insiders bought in last 30d (Form 4): A. Krishna, J. Kavanaugh [STRONG CLUSTER]",
            "primary_count": 3, "confirmation_count": 3,
            "primaries_fired": ["HVF BULLISH TRIGGERED (R:R 5.75, quality 82)",
                                "Options flow BULLISH — call/put 1.85, IV rank 72%, GEX BULLISH",
                                "ADX directional BULLISH (+DI 30 / -DI 18, ADX 35)"],
            "confirmations_fired": ["Director buys — 3 insiders bought in last 30d (Form 4): "
                                    "A. Krishna, J. Kavanaugh [STRONG CLUSTER]",
                                    "COT positioning BULLISH — score +42, commercials EXTREME_LONG, "
                                    "OI rising, commercials net +12,340 (+2,100 WoW)",
                                    "Sector ETF XLK aligned"]}
    _trade = {"level": 256.6, "stop_level": 246.0, "limit_level": 300.0, "deal_id": "TEST"}
    if "--send" in sys.argv:
        ok = send_trade_email("IBM", "BUY", _sig, _trade, size=1.0, session_name="EMAIL_TEST")
        print(f"send_trade_email returned: {ok}")
    else:
        cs = build_charts("IBM", _sig, _trade)
        print(f"charts built: {[(c[0], len(c[2])) for c in cs]}")
