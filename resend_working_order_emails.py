# ======================================================================================================================
# File:         resend_working_order_emails.py
# Author:       Alex Hind
# Created:      2026-07-09
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# One-shot script: re-send trade emails for all PENDING working orders.
# Used after a chart-rendering fix to push corrected emails to recipients.
#
# Builds sig + trade dicts from the DB, calls send_trade_email() for each
# order. The chart fix (ig_scale normalisation) is in trade_email.py v1.2.0
# and will be picked up automatically.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-11  Alex Hind   COT confirmation rebuilt direction-aligned only (mirrors signals.py 1.9.0 —
#                                 "Bearish is not confirmation for a buy").
# 1.0.0   2026-07-09  Alex Hind   Initial build — resend after chart scale fix.
# ======================================================================================================================

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("resend_emails")


def _build_sig(row: dict) -> dict:
    """Reconstruct a signal dict from a signal_log row."""
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    sig = {
        "options_bias":    row.get("options_bias"),
        "call_put_ratio":  _f(row.get("call_put_ratio")),
        "iv_rank":         _f(row.get("iv_rank")),
        "bb_breakout_dir": row.get("bb_breakout_dir"),
        "hvf_type":        row.get("hvf_type"),
        "hvf_signal":      row.get("hvf_signal"),
        "hvf_risk_reward": _f(row.get("hvf_risk_reward")),
        "hvf_quality":     row.get("hvf_quality"),
        # h3 level in yfinance units (signal_log stores yf prices, not IG points)
        "hvf_h3_level":    _f(row.get("hvf_h3_level")),
        "hvf_stop_level":  _f(row.get("hvf_stop_level")),
        "hvf_target":      _f(row.get("hvf_target")),
        "cot_bias":        row.get("cot_bias"),
        "director_signal": row.get("director_signal"),
        "pa_verdict":      row.get("pa_verdict"),
        "pa_score":        _f(row.get("pa_score")),
        "primary_count":   row.get("primary_count"),
        "confirmation_count": row.get("confirmation_count"),
        "adx_dir":         row.get("adx_dir"),
        "di_plus":         _f(row.get("di_plus")),
        "di_minus":        _f(row.get("di_minus")),
        "adx":             _f(row.get("adx")),
    }
    # Build primaries_fired so the email rationale is named, not just counts.
    primaries = []
    if sig["options_bias"] in ("BULLISH", "BEARISH"):
        bits = []
        if sig["call_put_ratio"] is not None:
            bits.append(f"call/put {sig['call_put_ratio']:.2f}")
        if sig["iv_rank"] is not None:
            bits.append(f"IV rank {sig['iv_rank']:.0f}%")
        primaries.append(f"Options flow {sig['options_bias']}" +
                         (f" — {', '.join(bits)}" if bits else ""))
    if sig["hvf_type"]:
        rr = sig["hvf_risk_reward"]
        q  = sig["hvf_quality"]
        primaries.append(
            f"HVF {sig['hvf_type']} {sig['hvf_signal'] or ''}"
            + (f" (R:R {rr}, quality {q})" if rr else "").strip())
    if sig["adx_dir"] in ("BULLISH", "BEARISH"):
        dp = sig["di_plus"]; dm = sig["di_minus"]; av = sig["adx"]
        bits = (f" (+DI {dp} / -DI {dm}, ADX {av})" if all(x is not None for x in (dp, dm, av)) else "")
        primaries.append(f"ADX directional {sig['adx_dir']}{bits}")
    sig["primaries_fired"] = primaries

    confirmations = []
    # COT only counts when it agrees with the order side (user 2026-06-11:
    # "Bearish is not confirmation for a buy") — canonical helper in signals.py.
    from signals import bias_aligned
    if bias_aligned(sig.get("cot_bias"), row.get("direction")):
        confirmations.append(f"COT positioning {sig['cot_bias']}")
    if sig["pa_verdict"] and sig["pa_verdict"] not in ("NEUTRAL", "CONFLICTED"):
        confirmations.append(f"Price-action {sig['pa_verdict']} (score {sig['pa_score'] or '—'})")
    sig["confirmations_fired"] = confirmations

    return sig


def _build_trade(row: dict) -> dict:
    """Reconstruct a trade dict from a working_orders row."""
    return {
        "level":       float(row["entry_level"]),
        "stop_level":  float(row["stop_level"]),
        "limit_level": float(row["limit_level"]),
        "deal_id":     row["deal_id"],
        "deal_ref":    row["deal_ref"],
    }


def main():
    from db_pool import get_db
    from trade_email import send_trade_email

    conn = get_db()
    rows = conn.run("""
        SELECT wo.deal_id, wo.deal_ref, wo.ticker, wo.direction, wo.size,
               wo.entry_level, wo.stop_level, wo.limit_level, wo.otype,
               wo.session, wo.signal_summary, wo.placed_at,
               sl.options_bias, sl.call_put_ratio, sl.iv_rank,
               sl.bb_breakout_dir, sl.hvf_type, sl.hvf_signal,
               sl.hvf_risk_reward, sl.hvf_quality, sl.hvf_h3_level,
               sl.hvf_stop_level, sl.hvf_target,
               sl.cot_bias, sl.director_signal,
               sl.pa_verdict, sl.pa_score,
               sl.primary_count, sl.confirmation_count,
               sl.adx_dir, sl.di_plus, sl.di_minus, sl.adx
        FROM working_orders wo
        LEFT JOIN signal_log sl ON sl.ticker = wo.ticker
          AND sl.session_time = (
            SELECT MAX(sl2.session_time) FROM signal_log sl2
            WHERE sl2.ticker = wo.ticker AND sl2.session_time <= wo.placed_at
          )
        WHERE wo.status = 'PENDING'
        ORDER BY wo.placed_at DESC
    """)
    conn.close()

    cols = ["deal_id","deal_ref","ticker","direction","size",
            "entry_level","stop_level","limit_level","otype",
            "session","signal_summary","placed_at",
            "options_bias","call_put_ratio","iv_rank",
            "bb_breakout_dir","hvf_type","hvf_signal",
            "hvf_risk_reward","hvf_quality","hvf_h3_level",
            "hvf_stop_level","hvf_target",
            "cot_bias","director_signal",
            "pa_verdict","pa_score",
            "primary_count","confirmation_count",
            "adx_dir","di_plus","di_minus","adx"]
    orders = [dict(zip(cols, r)) for r in rows]

    if not orders:
        log.info("No PENDING working orders found — nothing to resend.")
        return

    log.info(f"Resending emails for {len(orders)} PENDING working order(s)...")
    ok_count = 0
    for o in orders:
        ticker    = o["ticker"]
        direction = o["direction"]
        size      = float(o["size"])
        session   = o["session"] or ""
        deal_ref  = o["deal_id"]   # use deal_id as the human-visible ref

        sig   = _build_sig(o)
        trade = _build_trade(o)

        log.info(f"  Sending: {ticker} {direction} entry={o['entry_level']} "
                 f"stop={o['stop_level']} target={o['limit_level']}  ref={deal_ref}")
        ok = send_trade_email(
            ticker=ticker,
            direction=direction,
            sig=sig,
            trade=trade,
            size=size,
            session_name=session,
            event="Working order placed",
            deal_ref=deal_ref,
        )
        if ok:
            log.info(f"  ✓ Email sent for {ticker}")
            ok_count += 1
        else:
            log.error(f"  ✗ Email FAILED for {ticker}")

    log.info(f"Done. {ok_count}/{len(orders)} emails sent successfully.")
    if ok_count < len(orders):
        sys.exit(1)


if __name__ == "__main__":
    main()
