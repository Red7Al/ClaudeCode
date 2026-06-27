# ======================================================================================================================
# File:         hvf_web/server.py
# Author:       Alex Hind
# Created:      2026-06-27
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Flask server for the HVF website (user 2026-06-27). Serves the single-page UI (index.html), the data snapshot
# (build_snapshot.py output) as JSON, and three PNG visuals per instrument:
#   /api/card/<ticker>            the production X post-card (native funnel window)         -> render_x_post_card
#   /api/pricewin/<ticker>?days=N a DATE-WINDOW-REACTIVE price+funnel chart (filters live)  -> _render_price_window
#   /api/hist3yr/<ticker>         the fixed 3-YEAR price history (always 3y, never filtered) -> render_3yr_history_card
# The pricewin chart is a fresh, website-only renderer so the protected production card is never modified.
# Expose to a colleague with ngrok:  ngrok http 5057
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-27  Alex Hind   Initial build.
# ======================================================================================================================

import os
import io
import json
import logging

from flask import Flask, jsonify, send_file, request, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hvf_web.server")

_HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(_HERE, "snapshot.json")

app = Flask(__name__)
_PNG_CACHE: dict = {}


def _load_snapshot() -> dict:
    try:
        with open(SNAPSHOT, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"generated_utc": None, "count": 0, "records": []}


def _record(ticker: str) -> dict:
    for r in _load_snapshot().get("records", []):
        if r.get("ticker") == ticker:
            return r
    return {}


@app.route("/")
def index():
    with open(os.path.join(_HERE, "index.html"), "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/api/records")
def api_records():
    snap = _load_snapshot()
    # Strip the heavy _card blob from the list payload (only needed for PNG rendering).
    recs = [{k: v for k, v in r.items() if k != "_card"} for r in snap.get("records", [])]
    return jsonify({"generated_utc": snap.get("generated_utc"), "count": len(recs), "records": recs})


def _png_response(png: bytes):
    return send_file(io.BytesIO(png), mimetype="image/png")


@app.route("/api/card/<ticker>")
def api_card(ticker):
    key = f"card:{ticker}"
    if key not in _PNG_CACHE:
        from intraday_signals import render_x_post_card
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name")
        _PNG_CACHE[key] = render_x_post_card(card) or b""
    png = _PNG_CACHE[key]
    return _png_response(png) if png else ("no card", 404)


@app.route("/api/hist3yr/<ticker>")
def api_hist3yr(ticker):
    key = f"hist3yr:{ticker}"
    if key not in _PNG_CACHE:
        from intraday_signals import render_3yr_history_card
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name")
        _PNG_CACHE[key] = render_3yr_history_card(card) or b""
    png = _PNG_CACHE[key]
    return _png_response(png) if png else ("no 3yr chart", 404)


@app.route("/api/tweet/<ticker>")
def api_tweet(ticker):
    """Build the exact X tweet text for ONE instrument on demand (one render = low memory; the build
    deliberately doesn't render all ~150)."""
    key = f"tweet:{ticker}"
    if key not in _PNG_CACHE:
        from intraday_signals import _generate_x_drafts
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name")
        card["index"] = rec.get("market")
        txt = ""
        try:
            drafts = _generate_x_drafts([card], post=False, collect=True)
            if drafts:
                txt = drafts[0].get("tweet") or ""
        except Exception as e:
            log.warning(f"tweet render failed for {ticker}: {e}")
        _PNG_CACHE[key] = txt
    return jsonify({"ticker": ticker, "tweet": _PNG_CACHE[key]})


def _render_price_window(rec: dict, days: int, theme: str) -> bytes:
    """Website-only price+funnel chart for the last `days` sessions — re-rendered as the date-range
    filter changes (does NOT touch the protected production card). Funnel pivots that fall inside the
    window are overlaid; entry/stop/target drawn as horizontal lines."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, yfinance as yf
    from datetime import datetime, timezone, timedelta
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    dark = theme != "light"
    bg, fg, grid = ("#0d1117", "#c9d1d9", "#30363d") if dark else ("#ffffff", "#24292f", "#d0d7de")
    tk = rec.get("ticker", "")
    card = rec.get("_card") or {}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(20, days))
    try:
        hist = yf.download(YAHOO_MAP.get(tk, tk), start=start.strftime("%Y-%m-%d"),
                           end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    except Exception:
        hist = None
    fig = plt.figure(figsize=(9, 4.2)); fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0.08, 0.12, 0.88, 0.80]); ax.set_facecolor(bg)
    if hist is None or hist.empty:
        ax.text(0.5, 0.5, "no price data", color=fg, ha="center");
    else:
        close = hist["Close"].squeeze().dropna()
        ax.plot(close.index, close.values, color="#58a6ff", lw=1.5)
        col = "#3fb950" if card.get("hvf_type") == "BULLISH" else "#f85149"
        for lvl, lab, c in ((card.get("h3_level"), "Entry", "#e3b341"),
                            (card.get("stop_level"), "Stop", "#f85149"),
                            (card.get("target"), "Target", "#3fb950")):
            if isinstance(lvl, (int, float)):
                ax.axhline(lvl, color=c, lw=1.0, ls="--", alpha=0.8)
                ax.text(close.index[-1], lvl, f" {lab}", color=c, fontsize=8, va="center")
        # funnel pivots inside the window
        for dk, lk, c in (("h1_date", "h1_level", col), ("h2_date", "h2_level", col), ("h3_date", "h3_level", col),
                          ("l1_date", "l1_level", "#3fb950"), ("l2_date", "l2_level", "#3fb950"), ("l3_date", "l3_level", "#3fb950")):
            d, l = card.get(dk), card.get(lk)
            if d and isinstance(l, (int, float)):
                try:
                    dt = pd.Timestamp(d)
                    if close.index[0] <= dt.tz_localize(close.index.tz) <= close.index[-1]:
                        ax.scatter([dt], [l], color=c, s=22, zorder=5)
                except Exception:
                    pass
    ax.tick_params(colors=fg, labelsize=8)
    for s in ax.spines.values():
        s.set_color(grid)
    ax.grid(True, color=grid, alpha=0.4, lw=0.5)
    ax.set_title(f"{tk} — last {days}d", color=fg, fontsize=10)
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=bg, dpi=110); plt.close(fig)
    return buf.getvalue()


@app.route("/api/pricewin/<ticker>")
def api_pricewin(ticker):
    days = int(request.args.get("days", "180") or 180)
    theme = request.args.get("theme", "dark")
    rec = _record(ticker)
    if not rec:
        return ("unknown ticker", 404)
    return _png_response(_render_price_window(rec, days, theme))


if __name__ == "__main__":
    log.info("HVF site on http://127.0.0.1:5057  (ngrok http 5057 to share)")
    app.run(host="0.0.0.0", port=5057, debug=False)
