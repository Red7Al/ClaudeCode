# =============================================================================
# File:         trade_email.py
# Author:       Alex Hind
# Created:      2026-06-09
#
# Description:
# -----------------------------------------------------------------------------
# Email the investment case + charts to a configurable recipient list whenever a
# trade is opened (user directive 2026-06-09). Charts: price history, volume
# history, and the HVF funnel (lower-highs / higher-lows converging, with
# entry/stop/target).
#
# Sender:     Yahoo SMTP (smtp.mail.yahoo.com:587, STARTTLS) authenticated with
#             YAHOO_USER + YAHOO_APP_PASSWORD (GitHub secrets / app password).
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


def build_charts(ticker: str, sig: dict, trade: dict) -> list:
    """
    Return [(cid, filename, png_bytes), ...] for price, volume and HVF-funnel charts.
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

        yt = YAHOO_MAP.get(ticker, ticker)
        try:
            hist = yf.Ticker(yt).history(period="6mo", interval="1d")
        except Exception:
            hist = None

        if hist is not None and not hist.empty:
            # 1) Price history with entry/stop/target
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(hist.index, hist["Close"], color="#1f77b4", lw=1.3, label="Close")
            for lvl, lab, col in ((entry, "Entry", "green"), (stop, "Stop", "red"), (targ, "Target", "orange")):
                if lvl:
                    ax.axhline(lvl, color=col, ls="--", lw=1, label=f"{lab} {lvl:g}")
            ax.set_title(f"{ticker} — Price history (6 months)")
            ax.legend(fontsize=8, loc="best")
            ax.grid(alpha=0.3)
            charts.append(("price", "price.png", _fig_png(fig)))

            # 2) Volume history
            fig, ax = plt.subplots(figsize=(9, 3))
            ax.bar(hist.index, hist["Volume"], color="#888888", width=1.0)
            ax.set_title(f"{ticker} — Volume history (6 months)")
            ax.grid(alpha=0.3)
            charts.append(("volume", "volume.png", _fig_png(fig)))

        # 3) HVF funnel — three descending lower-highs (H1>H2>H3) and three ascending
        # higher-lows (L1<L2<L3) converging into the apex, with entry (H3) / stop /
        # target. This is the visual definition of the Hunt Volatility Funnel.
        h1 = _num(sig.get("hvf_h1_level") or sig.get("h1_level"))
        h2 = _num(sig.get("hvf_h2_level") or sig.get("h2_level"))
        h3 = _num(sig.get("hvf_h3_level") or sig.get("h3_level"))
        l1 = _num(sig.get("hvf_l1_level") or sig.get("l1_level"))
        l2 = _num(sig.get("hvf_l2_level") or sig.get("l2_level"))
        l3 = _num(sig.get("hvf_l3_level") or sig.get("l3_level"))
        if not all((h1, h3, l1, l3)):
            # Re-derive the funnel levels from price_action if the sig didn't carry them.
            try:
                from price_action import get_hvf_signal_mtf, get_trend_structure
                hv = get_hvf_signal_mtf(ticker, trend_hint=get_trend_structure(ticker))
                h1 = h1 or _num(hv.get("h1_level")); h2 = h2 or _num(hv.get("h2_level")); h3 = h3 or _num(hv.get("h3_level"))
                l1 = l1 or _num(hv.get("l1_level")); l2 = l2 or _num(hv.get("l2_level")); l3 = l3 or _num(hv.get("l3_level"))
                stop = stop or _num(hv.get("stop_level")); targ = targ or _num(hv.get("target"))
            except Exception:
                pass
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

    # Fallback for sigs without the named lists — derive primaries from raw fields.
    if not primaries:
        if sig.get("options_bias") in ("BULLISH", "BEARISH"):
            primaries.append(f"Options flow {sig['options_bias']}")
        if sig.get("bb_breakout_dir") in ("BULLISH", "BEARISH"):
            primaries.append(f"BB breakout {sig['bb_breakout_dir']}")
        if sig.get("hvf_type"):
            primaries.append(f"HVF {sig.get('hvf_type')} {sig.get('hvf_signal','')}".strip())

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
            "primary_count": 3, "confirmation_count": 3,
            "primaries_fired": ["HVF BULLISH TRIGGERED (R:R 5.75, quality 82)",
                                "Options flow BULLISH",
                                "ADX directional BULLISH (+DI 30 / -DI 18, ADX 35)"],
            "confirmations_fired": ["Director buys — 3 insiders in 30 days",
                                    "COT positioning BULLISH", "Sector ETF XLK aligned"]}
    _trade = {"level": 256.6, "stop_level": 246.0, "limit_level": 300.0, "deal_id": "TEST"}
    if "--send" in sys.argv:
        ok = send_trade_email("IBM", "BUY", _sig, _trade, size=1.0, session_name="EMAIL_TEST")
        print(f"send_trade_email returned: {ok}")
    else:
        cs = build_charts("IBM", _sig, _trade)
        print(f"charts built: {[(c[0], len(c[2])) for c in cs]}")
