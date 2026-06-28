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
# 1.2.0   2026-06-27  Alex Hind   (user 2026-06-27) /api/thread (ALL publication pages — lead + numbered long report),
#                                 /api/rules (Rolls-Royce-style per-rule justification with the numbers), /api/positions
#                                 (live open IG positions per instrument via epic_lookup). Supports the rebuilt UI.
# 1.1.0   2026-06-27  Alex Hind   (user 2026-06-27) /api/links/<ticker> — OUR latest X publication (x_publications) + every
#                                 tracked account that posted the instrument (notable_investors.post_url), fetched at
#                                 selection time. 12-hour snapshot auto-refresh thread (rebuild + cache clear); threaded serve.
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
_X_HANDLE = "SqueezeSignals"   # our X account (config.py / publish_one_to_x X_HANDLE)


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
    # no-store so the browser never serves a stale image — UK cards rendered broken (16KB) while the
    # host disk was full and browsers cached that; without this they keep showing the empty one
    # (user 2026-06-27 "X post card still empty" even after the disk was freed).
    resp = send_file(io.BytesIO(png), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


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


@app.route("/api/links/<ticker>")
def api_links(ticker):
    """On-demand X links for an instrument (user 2026-06-27): OUR latest publication (x_publications)
    and every tracked account we follow that posted about it (notable_investors.post_url). Queried
    live at selection time so the links are always current; never raises."""
    ours, mentions = None, []
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run("select tweet_id from x_publications where ticker = :t and tweet_id is not null "
                          "order by published_at desc limit 1", t=ticker)
            if rows:
                tid = rows[0][0]
                ours = {"tweet_id": str(tid), "url": f"https://x.com/{_X_HANDLE}/status/{tid}"}
            mrows = db.run("select investor_name, post_url, disclosed_at from notable_investors "
                           "where ticker = :t and post_url is not null order by disclosed_at desc limit 15", t=ticker)
            seen = set()
            for inv, url, dt in (mrows or []):
                if not url or url in seen:
                    continue
                seen.add(url)
                mentions.append({"account": inv, "url": url, "date": str(dt) if dt else None})
        finally:
            db.close()
    except Exception as e:
        log.warning(f"links lookup failed for {ticker}: {e}")
    return jsonify({"ticker": ticker, "ours": ours, "mentions": mentions})


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


@app.route("/api/thread/<ticker>")
def api_thread(ticker):
    """ALL pages of the X publication for one instrument (user 2026-06-27 'not showing all pages
    6/7'): the lead short tweet + every numbered long-report part (1/n..n/n). One render = low mem."""
    key = f"thread:{ticker}"
    if key not in _PNG_CACHE:
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name"); card["index"] = rec.get("market")
        parts = []
        try:
            from intraday_signals import _generate_x_drafts
            drafts = _generate_x_drafts([card], post=False, collect=True)
            if drafts and drafts[0].get("tweet"):
                parts.append(drafts[0]["tweet"])
        except Exception as e:
            log.warning(f"thread lead failed for {ticker}: {e}")
        try:
            from quality_report import publish_long_report_for
            parts += [p for p in (publish_long_report_for(card, post=False) or []) if p]
        except Exception as e:
            log.warning(f"thread report failed for {ticker}: {e}")
        _PNG_CACHE[key] = parts
    return jsonify({"ticker": ticker, "parts": _PNG_CACHE[key]})


def _rule_detail(rec: dict) -> list:
    """Rolls-Royce-style per-rule justification (user 2026-06-27) with the actual numbers, so each
    of the 5 RW rules is explained, not just PASS/FAIL."""
    c = rec.get("_card") or {}
    bull = rec.get("direction") == "BULL"
    g = lambda k: c.get(k)
    h1, h2, h3 = g("h1_level"), g("h2_level"), g("h3_level")
    l1, l2, l3 = g("l1_level"), g("l2_level"), g("l3_level")
    entry, stop, target, rr = rec.get("entry"), rec.get("stop"), rec.get("target"), rec.get("rr")
    out, base = [], {u["n"]: u for u in (rec.get("rules") or [])}

    def add(n, name, verdict, detail):
        out.append({"n": n, "name": name, "verdict": (base.get(n) or {}).get("verdict", verdict), "detail": detail})

    add(1, "Prior trend", "PASS",
        f"The funnel trades in the direction of the prior trend ({'BULLISH long' if bull else 'BEARISH short'}). "
        f"RW Rule 1 needs a clear, recent move of the same direction before the coil forms.")
    if None not in (h1, h2, h3, l1, l2, l3):
        add(2, "Three swings", "PASS",
            f"Lower highs  H1 {h1:g} ({g('h1_date')}) > H2 {h2:g} > H3 {h3:g} ({g('h3_date')}); "
            f"higher lows  L1 {l1:g} ({g('l1_date')}) < L2 {l2:g} < L3 {l3:g} ({g('l3_date')}). "
            f"Three real, alternating candle swings converging — the HVF pattern.")
    else:
        add(2, "Three swings", "DEVELOPING", "Not all three alternating swings are confirmed yet.")
    if None not in (h1, h3, l1, l3) and (h1 - l1):
        amp1 = h1 - l1; tight = (h3 - l3) / amp1 * 100
        add(3, "Tightness ≤35%", "PASS" if tight <= 35 else "FAIL",
            f"Current funnel range (H3−L3 = {h3 - l3:g}) vs the first amplitude (AMP1 = H1−L1 = {amp1:g}) "
            f"= {tight:.0f}%. RW compresses to ≤35% — tighter coil, tighter stop.")
        mid = (h3 + l3) / 2
        add(4, "Levels & target", "PASS" if (target and target > 0) else "FAIL",
            f"AMP1 = {amp1:g}; midpoint (H3+L3)/2 = {mid:g}. Entry = {entry:g} (break of the 3rd "
            f"{'high' if bull else 'low'}); stop beyond the opposite pivot = {stop:g}; "
            f"target = mid {'+' if bull else '−'} AMP1 = {target:g}.")
    if isinstance(rr, (int, float)):
        risk = abs((entry or 0) - (stop or 0)); reward = abs((target or 0) - (entry or 0))
        add(5, "R:R ≥ 3:1", "PASS" if rr >= 3 else "DEVELOPING",
            f"Reward {reward:g} ÷ risk {risk:g} = {rr:.2f}:1 (RW floor 3:1).")
    return out


@app.route("/api/rules/<ticker>")
def api_rules(ticker):
    return jsonify({"ticker": ticker, "rules": _rule_detail(_record(ticker))})


@app.route("/api/positions")
def api_positions():
    """Live count of OPEN IG positions per instrument (user 2026-06-27). Best-effort: needs IG env +
    epic_lookup; returns {} on any failure so the page still loads."""
    counts = {}
    try:
        import ig_shim
        epic2tk = {}
        try:
            from db_pool import get_db
            db = get_db()
            try:
                for row in (db.run("select ticker, epic from epic_lookup") or []):
                    if row[1]:
                        epic2tk[str(row[1])] = row[0]
            finally:
                db.close()
        except Exception:
            pass
        for pos in (ig_shim.get_open_positions() or []):
            mk = pos.get("market", {}) or {}
            tk = epic2tk.get(str(mk.get("epic"))) or mk.get("instrumentName") or mk.get("epic")
            if tk:
                counts[tk] = counts.get(tk, 0) + 1
    except Exception as e:
        log.warning(f"positions lookup failed: {e}")
    return jsonify({"positions": counts})


_REFRESHING = {"on": False}


def _do_rebuild() -> bool:
    """Rebuild the snapshot (shared by the 12h loop + the manual refresh button). Guards against a
    concurrent rebuild and clears the PNG/tweet/links caches afterwards."""
    if _REFRESHING["on"]:
        return False
    _REFRESHING["on"] = True
    try:
        from hvf_web.build_snapshot import build
        build()
        _PNG_CACHE.clear()
        log.info("snapshot rebuilt; caches cleared")
        return True
    except Exception as e:
        log.error(f"snapshot rebuild failed: {e}")
        return False
    finally:
        _REFRESHING["on"] = False


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    """Trigger an on-demand snapshot rebuild in a background thread (user 2026-06-28)."""
    if _REFRESHING["on"]:
        return jsonify({"started": False, "busy": True})
    import threading
    threading.Thread(target=_do_rebuild, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/status")
def api_status():
    snap = _load_snapshot()
    return jsonify({"refreshing": _REFRESHING["on"], "generated_utc": snap.get("generated_utc"),
                    "count": snap.get("count")})


@app.route("/api/broker/<ticker>")
def api_broker(ticker):
    """Change in broker coverage over the last 6 and 12 months (user 2026-06-27): net analyst
    upgrades vs downgrades from yfinance upgrades_downgrades. Live per-ticker; graceful if Yahoo is
    unreachable (available=False)."""
    res = {"up6": 0, "down6": 0, "up12": 0, "down12": 0, "available": False}
    try:
        import yfinance as yf, pandas as pd
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        ud = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).upgrades_downgrades
        if ud is not None and not ud.empty:
            res["available"] = True
            now = pd.Timestamp.now(tz="UTC")
            for dt, row in ud.iterrows():
                try:
                    d = pd.Timestamp(dt)
                    d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
                except Exception:
                    continue
                months = (now - d).days / 30.44
                act = str(row.get("Action", "")).lower()
                if 0 <= months <= 12:
                    if act == "up":
                        res["up12"] += 1; res["up6"] += (months <= 6)
                    elif act == "down":
                        res["down12"] += 1; res["down6"] += (months <= 6)
    except Exception as e:
        log.warning(f"broker history failed for {ticker}: {e}")
    return jsonify({"ticker": ticker, **{k: int(v) if isinstance(v, bool) else v for k, v in res.items()}})


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


def _refresh_loop():
    """Rebuild the snapshot every 12h (user 2026-06-27) — and once on startup if it's missing or
    already older than 12h. The build is light (no PNG rendering — those are lazy), so running it
    in-process is fine; the PNG/tweet/links caches are cleared after each rebuild. Checks every 6h."""
    import time as _t
    from datetime import datetime, timezone
    while True:
        try:
            need = True
            snap = _load_snapshot()
            gen = snap.get("generated_utc")
            if gen:
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds()
                    need = age >= 12 * 3600
                except Exception:
                    need = True
            if need:
                log.info("snapshot refresh (12h): building ...")
                _do_rebuild()
        except Exception as e:
            log.warning(f"snapshot refresh failed (will retry): {e}")
        _t.sleep(6 * 3600)


if __name__ == "__main__":
    import threading
    threading.Thread(target=_refresh_loop, daemon=True).start()
    log.info("HVF site on http://127.0.0.1:5057  (ngrok http 5057 to share)")
    app.run(host="0.0.0.0", port=5057, debug=False, threaded=True)
