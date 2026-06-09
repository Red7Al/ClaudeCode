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

        # 3) HVF funnel — lower-highs H1→H3 and higher-lows L1→L3 converging.
        h1 = _num(sig.get("hvf_h1_level") or sig.get("h1_level"))
        h3 = _num(sig.get("hvf_h3_level") or sig.get("h3_level"))
        l1 = _num(sig.get("hvf_l1_level") or sig.get("l1_level"))
        l3 = _num(sig.get("hvf_l3_level") or sig.get("l3_level"))
        if not all((h1, h3, l1, l3)):
            # Re-derive the funnel levels from price_action if the sig didn't carry them.
            try:
                from price_action import get_hvf_signal_mtf, get_trend_structure
                hv = get_hvf_signal_mtf(ticker, trend_hint=get_trend_structure(ticker))
                h1 = h1 or _num(hv.get("h1_level")); h3 = h3 or _num(hv.get("h3_level"))
                l1 = l1 or _num(hv.get("l1_level")); l3 = l3 or _num(hv.get("l3_level"))
                stop = stop or _num(hv.get("stop_level")); targ = targ or _num(hv.get("target"))
            except Exception:
                pass
        if all((h1, h3, l1, l3)):
            fig, ax = plt.subplots(figsize=(8, 4))
            x = [0, 1]
            ax.plot(x, [h1, h3], "r-o", lw=1.6, label="Lower highs (H1→H3)")
            ax.plot(x, [l1, l3], "g-o", lw=1.6, label="Higher lows (L1→L3)")
            ax.fill_between(x, [h1, h3], [l1, l3], color="grey", alpha=0.15)
            if entry: ax.axhline(entry, color="green",  ls="--", lw=1, label=f"Entry {entry:g}")
            if stop:  ax.axhline(stop,  color="red",    ls=":",  lw=1, label=f"Stop {stop:g}")
            if targ:  ax.axhline(targ,  color="orange", ls=":",  lw=1, label=f"Target {targ:g}")
            ax.set_title(f"{ticker} — HVF funnel ({sig.get('hvf_type', '')} {sig.get('hvf_signal', '')})")
            ax.set_xticks([0, 1]); ax.set_xticklabels(["earlier", "now"])
            ax.legend(fontsize=8, loc="best")
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
    signals = [
        ("Options bias",  sig.get("options_bias")),
        ("BB breakout",   sig.get("bb_breakout_dir")),
        ("HVF",           f"{sig.get('hvf_type','')} {sig.get('hvf_signal','')}".strip() or None),
        ("COT bias",      sig.get("cot_bias")),
        ("Price action",  sig.get("pa_verdict")),
        ("ADX",           sig.get("adx_signal")),
        ("Director buys", sig.get("director_signal")),
        ("Senate",        sig.get("senate_signal")),
        ("Notable inv.",  sig.get("notable_investor")),
        ("Social",        sig.get("social_mention")),
        ("Primaries",     sig.get("primary_count")),
        ("Confirmations", sig.get("confirmation_count")),
    ]
    sig_lines = [(k, v) for k, v in signals if v not in (None, "", "—", "NONE")]

    text = [f"Trade opened: {name} {direction}", "=" * 44]
    text += [f"{k:14}: {v}" for k, v in rows]
    text += ["", "Why this trade (signals that fired):"]
    text += [f"  • {k}: {v}" for k, v in sig_lines]
    text += ["", "Charts attached: price history, volume history, HVF funnel.",
             f"\nGenerated {datetime.now(timezone.utc):%d %b %Y %H:%M UTC} by EndToEndTrading."]
    text = "\n".join(str(t) for t in text)

    def _tr(k, v):
        return f"<tr><td style='padding:2px 10px;color:#555'>{k}</td><td style='padding:2px 10px'><b>{v}</b></td></tr>"
    html = (
        f"<h2>Trade opened — {name} {direction}</h2>"
        f"<table>{''.join(_tr(k, v) for k, v in rows)}</table>"
        f"<h3>Why this trade</h3><table>{''.join(_tr(k, v) for k, v in sig_lines)}</table>"
    )
    return text, html


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_trade_email(ticker: str, direction: str, sig: dict, trade: dict,
                     size=None, session_name: str = "", recipients=None) -> bool:
    """
    Email the investment case + charts for a newly opened trade. FAIL-SAFE: returns
    False and logs on any problem; never raises into the trade-placement path.
    """
    try:
        user = os.environ.get("YAHOO_USER", "").strip()
        # Yahoo shows app passwords as 4×4 groups ("abcd efgh ijkl mnop") but the
        # real password is the 16 chars with NO spaces — strip them defensively
        # (a space in the secret is the #1 cause of Yahoo 535 auth failures).
        pw   = os.environ.get("YAHOO_APP_PASSWORD", "").replace(" ", "").strip()
        if not (user and pw):
            log.warning("YAHOO_USER / YAHOO_APP_PASSWORD not set — trade email skipped")
            return False
        try:
            from config import EMAIL_RECIPIENTS
        except Exception:
            EMAIL_RECIPIENTS = ["eahind@yahoo.co.uk"]
        rcpts = recipients or EMAIL_RECIPIENTS
        if not rcpts:
            return False

        text, html = _investment_case(ticker, direction, size, session_name, sig, trade)

        msg = EmailMessage()
        msg["Subject"] = f"Trade opened: {ticker} {direction} @ {trade.get('level', '?')}"
        msg["From"]    = user
        msg["To"]      = ", ".join(rcpts)
        msg.set_content(text)

        charts = build_charts(ticker, sig, trade)
        html_imgs = "".join(f'<br><img src="cid:{cid}" style="max-width:720px;border:1px solid #ddd">'
                            for cid, _, _ in charts)
        msg.add_alternative(html + html_imgs, subtype="html")
        html_part = msg.get_payload()[-1]
        for cid, fname, png in charts:
            html_part.add_related(png, maintype="image", subtype="png",
                                  cid=f"<{cid}>", filename=fname)

        ctx = ssl.create_default_context()
        # Implicit SSL on 465 (Yahoo intermittently drops 587/STARTTLS).
        with smtplib.SMTP_SSL(YAHOO_SMTP_HOST, YAHOO_SMTP_PORT, context=ctx, timeout=30) as s:
            if os.environ.get("EMAIL_DEBUG"):
                s.set_debuglevel(1)
            s.ehlo()
            s.login(user, pw)
            s.send_message(msg)
        log.info(f"Trade email sent to {rcpts} for {ticker} ({len(charts)} charts)")
        return True
    except Exception as e:
        # Log the exception TYPE + repr — "Connection unexpectedly closed" alone hides
        # whether it's auth, IP-reputation, or handshake. user must be the FULL Yahoo
        # address and the app password must have no spaces.
        log.error(f"Trade email failed for {ticker}: {type(e).__name__}: {e!r} "
                  f"(from='{user[:3]}…@…', recipients={rcpts})")
        return False


if __name__ == "__main__":
    # Smoke test: build charts (no send). With --send, send a REAL sample email via
    # Yahoo SMTP (needs YAHOO_USER/YAHOO_APP_PASSWORD) to verify end-to-end delivery.
    import sys
    logging.basicConfig(level=logging.INFO)
    _sig = {"hvf_type": "BULLISH", "hvf_signal": "TRIGGERED", "hvf_risk_reward": 5.75,
            "options_bias": "BULLISH", "bb_breakout_dir": "UP", "pa_verdict": "CONFIRM_LONG",
            "primary_count": 3, "confirmation_count": 3}
    _trade = {"level": 256.6, "stop_level": 242.2, "limit_level": 339.3, "deal_id": "TEST"}
    if "--send" in sys.argv:
        ok = send_trade_email("IBM", "BUY", _sig, _trade, size=1.0, session_name="EMAIL_TEST")
        print(f"send_trade_email returned: {ok}")
    else:
        cs = build_charts("IBM", _sig, _trade)
        print(f"charts built: {[(c[0], len(c[2])) for c in cs]}")
