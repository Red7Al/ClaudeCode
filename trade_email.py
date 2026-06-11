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
# 1.2.0   2026-07-09  Alex Hind   Trade reference (IG deal_id) added to email subject
#                                 and investment-case table. Corporate HTML redesign:
#                                 dark header band, structured two-column layout,
#                                 professional footer — IBM-style presentation.
#                                 Opposing primary signals tagged in rationale and
#                                 list (e.g. ADX BEARISH in a BUY trade): rationale
#                                 now reads "2 of 3 aligned BUY (1 opposed)";
#                                 opposing items highlighted [Opposing] in dark red.
#                                 Fix: IG trade levels (entry/stop/target) rescaled to
#                                 match yfinance price units before plotting. IG may
#                                 quote in pence or US cents (~100x yfinance); if
#                                 entry/yfinance_median > 5, all IG levels are divided
#                                 by the ratio so price history and trade levels share
#                                 one axis. Legend still shows the original IG values.
# 1.3.0   2026-07-09  Alex Hind   Remove HVF funnel schematic (chart #3) — redundant
#                                 now the funnel is overlaid on the price history chart.
# 1.4.0   2026-06-10  Alex Hind   Director buys: render Form 4 transaction details as
#                                 a structured HTML mini-table (name, date, shares,
#                                 price, amount) when director_transactions list is
#                                 present in the signal dict. Uses data fetched by
#                                 signals.py v1.6.0 _fetch_form4_transactions().
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
        # ig_scale is computed once the yfinance history is available (see below);
        # initialise here so the schematic block can also use it.
        ig_scale = 1.0

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
            # Normalise IG trade levels to yfinance price scale.
            # IG confirms deals in pence (UK stocks) or US cents (US stocks), so the
            # entry level can be ~100x the yfinance close. Detect by comparing the IG
            # entry to the median yfinance close; if the ratio is > 5, divide IG levels
            # by that ratio so everything lands on the same axis.
            if entry:
                yf_med = float(hist["Close"].median())
                if yf_med > 0:
                    raw_ratio = entry / yf_med
                    if raw_ratio > 5:
                        ig_scale = raw_ratio
            entry_p = entry / ig_scale if entry else None
            stop_p  = stop  / ig_scale if stop  else None
            targ_p  = targ  / ig_scale if targ  else None

            # 1) Price history + entry/stop/target + HVF funnel overlay on the real axis
            fig, ax = plt.subplots(figsize=(9, 4.5))
            ax.plot(hist.index, hist["Close"], color="#1f77b4", lw=1.3, label="Close")
            _overlay_hvf_funnel(ax, hist, (h1, h2, h3, l1, l2, l3), dates)
            for lvl, lvl_ig, lab, col in (
                (entry_p, entry, "Entry", "green"),
                (stop_p,  stop,  "Stop",  "red"),
                (targ_p,  targ,  "Target","orange"),
            ):
                if lvl:
                    label = f"{lab} {lvl_ig:g}" if ig_scale != 1.0 else f"{lab} {lvl:g}"
                    ax.axhline(lvl, color=col, ls="--", lw=1, label=label)
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

    except Exception as e:
        log.warning(f"trade-email chart generation failed for {ticker}: {e}")
    return charts


# ---------------------------------------------------------------------------
# Investment-case body
# ---------------------------------------------------------------------------

def _investment_case(ticker: str, direction: str, size, session_name: str,
                     sig: dict, trade: dict, event: str = "Trade opened",
                     deal_ref: str = "") -> tuple:
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
    ref   = deal_ref or trade.get("deal_id", "") or trade.get("deal_ref", "")

    rows = [
        ("Instrument", name), ("Direction", direction), ("Size", size),
        ("Session",    session_name), ("Entry", entry), ("Stop", stop),
        ("Target",     targ), ("R:R", rr_s),
    ]

    # For working orders, show live price and how far away the entry is.
    if event == "Working order placed":
        cur = trade.get("current_price")
        if cur and entry and entry != "—":
            try:
                dist = abs(float(entry) - float(cur)) / float(cur) * 100.0
                rows.append(("Current Price", f"{cur}  ({dist:.1f}% from entry)"))
            except Exception:
                pass
        rows.append(("Note", "NOT yet visible in IG platform — placed automatically when price reaches entry"))

    if ref:
        rows.append(("Trade Ref", ref))

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
            dir_txs = sig.get("director_transactions") or []
            if dir_txs:
                # Build one line per transaction: name, date, shares, price, amount
                tx_lines = "; ".join(
                    f"{t['name']} {t['date']}: {t['shares']:,} sh @ ${t['price_per_share']:,.2f} (${t['amount']:,.0f})"
                    for t in dir_txs[:5]
                )
                confirmations.append(f"Director buys ({len(dir_txs)}) — {tx_lines}")
            else:
                confirmations.append("Director buys — " + (sig.get("director_detail") or "insider cluster (Form 4)"))
        if sig.get("cot_bias") in ("BULLISH", "BEARISH"):
            _cb = []
            if sig.get("cot_score"): _cb.append(f"score {sig['cot_score']:+.0f}")
            if sig.get("cot_comm_extreme") and sig["cot_comm_extreme"] != "NORMAL":
                _cb.append(f"commercials {sig['cot_comm_extreme']}")
            if sig.get("cot_oi_signal") and sig["cot_oi_signal"] != "NEUTRAL":
                _cb.append(f"OI {sig['cot_oi_signal']}")
            confirmations.append(f"COT positioning {sig['cot_bias']}" + (f" — {', '.join(_cb)}" if _cb else ""))

    # Work out which primaries aligned with the trade direction vs opposed it.
    # A primary "aligns" if it contains the expected bias word; otherwise it opposed
    # but was outvoted. We tag opposing entries so the reader understands the split.
    aligned_kw = "BULLISH" if direction == "BUY" else "BEARISH"
    opposed_kw = "BEARISH" if direction == "BUY" else "BULLISH"

    def _is_opposing(label: str) -> bool:
        return opposed_kw in label.upper() and aligned_kw not in label.upper()

    aligned_count = sum(1 for p in primaries if not _is_opposing(p))
    opposed_count = pc - aligned_count

    if opposed_count > 0:
        rationale = (f"{aligned_count} of {pc} primary signals aligned {direction} "
                     f"({opposed_count} opposed — majority vote)")
    else:
        rationale = f"{pc} primary signal{'s' if pc != 1 else ''} pointed {direction}"
    if cc:
        rationale += f", backed by {cc} confirmation{'s' if cc != 1 else ''}"

    # Plain-text version
    text = [f"{event}: {name} {direction}", "=" * 44]
    text += [f"{k:14}: {v}" for k, v in rows]
    text += ["", f"WHY THIS IS A {direction}: {rationale}.", "", f"Primary signals ({pc}):"]
    text += ([f"  • {'[OPPOSING] ' if _is_opposing(p) else ''}{p}" for p in primaries]
             or ["  • (none recorded)"])
    text += ["", f"Confirmations ({cc}):"]
    text += ([f"  • {c}" for c in confirmations] or ["  • (none recorded)"])
    if sig.get("pa_verdict"):
        text += ["", f"Price-action verdict: {sig.get('pa_verdict')} (score {sig.get('pa_score', '—')})"]
    text += ["", "Charts attached: price history (with HVF funnel overlay), volume history.",
             f"\nGenerated {datetime.now(timezone.utc):%d %b %Y %H:%M UTC} by EndToEndTrading."]
    text = "\n".join(str(t) for t in text)

    # Corporate HTML — IBM-style: dark header, two-column data table, structured sections
    dir_color  = "#006400" if direction == "BUY" else "#8B0000"
    dir_label  = f'<span style="color:{dir_color};font-weight:700">{direction}</span>'
    ts_str     = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")

    def _row(k, v, highlight=False):
        bg   = "#f0f4f8" if highlight else "#ffffff"
        val  = f'<span style="font-family:monospace;font-size:13px">{v}</span>' if k == "Trade Ref" else f'<b>{v}</b>'
        return (f'<tr style="background:{bg}">'
                f'<td style="padding:7px 16px;color:#555;font-size:13px;white-space:nowrap;'
                f'border-bottom:1px solid #e8ecf0">{k}</td>'
                f'<td style="padding:7px 16px;font-size:13px;border-bottom:1px solid #e8ecf0">{val}</td>'
                f'</tr>')

    def _li(items, tag_opposing=False):
        if not items:
            return '<li style="color:#888;font-style:italic">none recorded</li>'
        parts = []
        for i in items:
            if tag_opposing and _is_opposing(i):
                parts.append(
                    f'<li style="margin:4px 0;font-size:13px;color:#8B0000">'
                    f'<b>[Opposing]</b> {i}</li>')
            else:
                parts.append(f'<li style="margin:4px 0;font-size:13px;color:#333">{i}</li>')
        return "".join(parts)

    table_rows = "".join(_row(k, v, highlight=(k == "Trade Ref")) for k, v in rows)

    # Director transactions block — shown only when structured data is available
    dir_txs = sig.get("director_transactions") or []
    dir_block = ""
    if dir_txs:
        header = (f'<tr style="background:#f0f4f8">'
                  f'<th style="padding:5px 10px;font-size:11px;text-align:left;color:#555;'
                  f'border-bottom:1px solid #d0d7de">Name</th>'
                  f'<th style="padding:5px 10px;font-size:11px;text-align:left;color:#555;'
                  f'border-bottom:1px solid #d0d7de">Date</th>'
                  f'<th style="padding:5px 10px;font-size:11px;text-align:right;color:#555;'
                  f'border-bottom:1px solid #d0d7de">Shares</th>'
                  f'<th style="padding:5px 10px;font-size:11px;text-align:right;color:#555;'
                  f'border-bottom:1px solid #d0d7de">Price</th>'
                  f'<th style="padding:5px 10px;font-size:11px;text-align:right;color:#555;'
                  f'border-bottom:1px solid #d0d7de">Amount</th>'
                  f'</tr>')
        rows_html = ""
        for t in dir_txs[:6]:
            rows_html += (
                f'<tr>'
                f'<td style="padding:5px 10px;font-size:12px;border-bottom:1px solid #f0f0f0">{t["name"]}</td>'
                f'<td style="padding:5px 10px;font-size:12px;border-bottom:1px solid #f0f0f0">{t["date"]}</td>'
                f'<td style="padding:5px 10px;font-size:12px;text-align:right;border-bottom:1px solid #f0f0f0">{t["shares"]:,}</td>'
                f'<td style="padding:5px 10px;font-size:12px;text-align:right;border-bottom:1px solid #f0f0f0">${t["price_per_share"]:,.2f}</td>'
                f'<td style="padding:5px 10px;font-size:12px;text-align:right;border-bottom:1px solid #f0f0f0;font-weight:600">${t["amount"]:,.0f}</td>'
                f'</tr>'
            )
        dir_block = (
            f'<p style="margin:12px 0 4px 0;font-size:12px;font-weight:700;color:#555;'
            f'text-transform:uppercase;letter-spacing:1px">Insider Transactions (Form 4)</p>'
            f'<table style="width:100%;border-collapse:collapse;border:1px solid #d0d7de;'
            f'border-radius:3px;margin-bottom:10px">{header}{rows_html}</table>'
        )

    pa_block = ""
    if sig.get("pa_verdict"):
        pa_block = (f'<p style="margin:8px 0;font-size:13px">'
                    f'<b>Price-action verdict:</b> {sig["pa_verdict"]} '
                    f'(score {sig.get("pa_score","—")})</p>')

    ref_line = (f'<p style="margin:0;font-size:11px;color:#777">'
                f'Trade reference: <code style="font-family:monospace">{ref}</code></p>') if ref else ""

    html = f"""
<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:0 auto;
            background:#ffffff;border:1px solid #d0d7de;border-radius:4px;overflow:hidden">

  <!-- Header band -->
  <div style="background:#1a1a2e;padding:20px 24px;display:flex;align-items:center">
    <div>
      <div style="color:#ffffff;font-size:11px;letter-spacing:2px;text-transform:uppercase;
                  font-weight:600;margin-bottom:4px">EndToEndTrading</div>
      <div style="color:#e0e6f0;font-size:20px;font-weight:700">{event}</div>
      <div style="color:#a0b0c8;font-size:14px;margin-top:4px">{name} &mdash; {dir_label}</div>
    </div>
  </div>

  <!-- Trade details table -->
  <div style="padding:0">
    <table style="width:100%;border-collapse:collapse">
      {table_rows}
    </table>
  </div>

  <!-- Investment rationale -->
  <div style="padding:16px 24px;border-top:2px solid #1a1a2e">
    <p style="margin:0 0 10px 0;font-size:13px;font-weight:700;color:#1a1a2e;
              text-transform:uppercase;letter-spacing:1px">Investment Rationale</p>
    <p style="margin:0 0 12px 0;font-size:13px;color:#333">{rationale}.</p>

    <p style="margin:0 0 6px 0;font-size:12px;font-weight:700;color:#555;
              text-transform:uppercase;letter-spacing:1px">Primary Signals ({pc})</p>
    <ul style="margin:0 0 14px 16px;padding:0">{_li(primaries, tag_opposing=True)}</ul>

    <p style="margin:0 0 6px 0;font-size:12px;font-weight:700;color:#555;
              text-transform:uppercase;letter-spacing:1px">Confirmations ({cc})</p>
    <ul style="margin:0 0 14px 16px;padding:0">{_li(confirmations)}</ul>

    {dir_block}
    {pa_block}
  </div>

  <!-- Charts note -->
  <div style="padding:12px 24px;background:#f6f8fa;border-top:1px solid #e8ecf0">
    <p style="margin:0;font-size:12px;color:#555">
      Charts attached: price history with HVF funnel overlay (6 months), volume history.
    </p>
  </div>

  <!-- Footer -->
  <div style="padding:12px 24px;background:#1a1a2e">
    <p style="margin:0;font-size:11px;color:#8899aa">
      Generated {ts_str} &nbsp;|&nbsp; EndToEndTrading automated system
    </p>
    {ref_line.replace('color:#777', 'color:#8899aa').replace('style="margin:0', 'style="margin:4px 0 0 0')}
  </div>

</div>
"""
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
                     size=None, session_name: str = "", recipients=None,
                     event: str = "Trade opened", deal_ref: str = "") -> bool:
    """
    Email the investment case + charts for a newly opened trade (or, with
    event="Working order placed", for a pending HVF entry order). Prefers the Resend
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

        ref = deal_ref or trade.get("deal_id", "") or trade.get("deal_ref", "")
        ref_suffix = f" [{ref}]" if ref else ""
        subject = f"{event}: {ticker} {direction} @ {trade.get('level', '?')}{ref_suffix}"
        text, html = _investment_case(ticker, direction, size, session_name, sig, trade,
                                      event=event, deal_ref=ref)
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
    _trade = {"level": 256.6, "stop_level": 246.0, "limit_level": 300.0, "deal_id": "DIAAAABBBCDE"}
    if "--send" in sys.argv:
        ok = send_trade_email("IBM", "BUY", _sig, _trade, size=1.0, session_name="EMAIL_TEST",
                              deal_ref=_trade["deal_id"])
        print(f"send_trade_email returned: {ok}")
    else:
        cs = build_charts("IBM", _sig, _trade)
        print(f"charts built: {[(c[0], len(c[2])) for c in cs]}")
