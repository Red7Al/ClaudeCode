# =============================================================================
# File:         run_query.py
# Author:       Alex Hind
# Created:      2026-06-02
#
# Description:
# -----------------------------------------------------------------------------
# Diagnostic query — prints today's trades and open positions to stdout.
# Run via the "Query: Today's Trades" GitHub Actions workflow.
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv()
import logging
from datetime import datetime, timezone

import pg8000.native

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_query")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"


def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        ssl_context=True
    )


def main():
    conn = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"  EndToEndTrading — Diagnostic Query  ({today} UTC)")
    print(f"{'='*60}")

    # ── Open positions ────────────────────────────────────────────────────────
    rows = conn.run(
        """select ticker, direction, size, open_price, stop_loss,
                  take_profit, paper_trade, session, opened_at, signal_summary
           from   positions
           order  by opened_at desc"""
    )
    print(f"\nOPEN POSITIONS ({len(rows)})")
    print("-" * 60)
    if rows:
        for r in rows:
            ticker, direction, size, open_price, stop_loss, take_profit, paper, session, opened_at, sig = r
            tag = "[PAPER]" if paper else "[LIVE] "
            print(f"  {tag} {direction} {size} x {ticker}")
            print(f"         Entry: {open_price}  SL: {stop_loss}  TP: {take_profit}")
            print(f"         Session: {session}  Opened: {opened_at}")
            print(f"         Signals: {sig}")
    else:
        print("  (none)")

    # ── Trades opened today ───────────────────────────────────────────────────
    rows = conn.run(
        """select ticker, direction, size, open_price, stop_loss,
                  paper_trade, session, opened_at, signal_summary
           from   trade_log
           where  date(opened_at at time zone 'UTC') = :d
           order  by opened_at desc""",
        d=today
    )
    print(f"\nTRADES OPENED TODAY ({len(rows)})")
    print("-" * 60)
    if rows:
        for r in rows:
            ticker, direction, size, open_price, stop_loss, paper, session, opened_at, sig = r
            tag = "[PAPER]" if paper else "[LIVE] "
            print(f"  {tag} {direction} {size} x {ticker}  @ {open_price}  SL:{stop_loss}")
            print(f"         Session: {session}  Opened: {opened_at}")
            print(f"         Signals: {sig}")
    else:
        print("  (none)")

    # ── Trades closed today ───────────────────────────────────────────────────
    rows = conn.run(
        """select ticker, direction, size, open_price, close_price,
                  pnl, close_reason, paper_trade, closed_at
           from   trade_log
           where  date(closed_at at time zone 'UTC') = :d
           order  by closed_at desc""",
        d=today
    )
    print(f"\nTRADES CLOSED TODAY ({len(rows)})")
    print("-" * 60)
    if rows:
        for r in rows:
            ticker, direction, size, open_price, close_price, pnl, reason, paper, closed_at = r
            tag    = "[PAPER]" if paper else "[LIVE] "
            pnl_str = f"+£{pnl:.2f}" if pnl >= 0 else f"-£{abs(pnl):.2f}"
            print(f"  {tag} {direction} {size} x {ticker}  {open_price} → {close_price}  P&L: {pnl_str}")
            print(f"         Reason: {reason}  Closed: {closed_at}")
    else:
        print("  (none)")

    # ── Daily P&L summary ─────────────────────────────────────────────────────
    rows = conn.run(
        """select up.name, dp.total_pnl, dp.trade_count,
                  dp.win_count, dp.loss_count, dp.daily_loss_hit
           from   daily_pnl dp
           join   user_profiles up on up.id = dp.user_id
           where  dp.trade_date = :d
           order  by up.name""",
        d=today
    )
    print(f"\nDAILY P&L SUMMARY")
    print("-" * 60)
    if rows:
        for r in rows:
            name, total_pnl, trade_count, win_count, loss_count, loss_hit = r
            pnl_str = f"+£{total_pnl:.2f}" if total_pnl >= 0 else f"-£{abs(total_pnl):.2f}"
            limit   = "⚠️  DAILY LIMIT HIT" if loss_hit else ""
            print(f"  {name}: {pnl_str}  ({trade_count} trades, {win_count}W/{loss_count}L) {limit}")
    else:
        print("  (no activity today)")

    # ── Last macro snapshot ───────────────────────────────────────────────────
    rows = conn.run(
        """select session, vix, dxy, yield_spread, macro_gate_pass, gate_reason, snapshot_time
           from   macro_snapshot
           order  by snapshot_time desc
           limit  3"""
    )
    print(f"\nLAST MACRO SNAPSHOTS")
    print("-" * 60)
    for r in rows:
        session, vix, dxy, spread, gate_pass, reason, snap_time = r
        gate = "✅ PASS" if gate_pass else "❌ FAIL"
        print(f"  {session} {snap_time}  VIX:{vix}  DXY:{dxy}  Spread:{spread}  Gate:{gate}")
        if not gate_pass:
            print(f"    Reason: {reason}")

    # ── signal_log column check ───────────────────────────────────────────────
    col_rows = conn.run(
        """select column_name from information_schema.columns
           where table_name = 'signal_log'
             and column_name in ('call_put_ratio','primary_count','direction','pa_verdict')
           order by column_name"""
    )
    found = [r[0] for r in col_rows]
    print(f"\nSIGNAL_LOG SCHEMA CHECK — new columns present: {found}")

    # ── signal_log row counts by session/date ─────────────────────────────────
    count_rows = conn.run(
        """select date(session_time at time zone 'UTC') as day,
                  session, count(*) as n
           from   signal_log
           group  by 1, 2
           order  by 1 desc, 3 desc
           limit  10"""
    )
    print(f"\nSIGNAL_LOG RECENT ROW COUNTS")
    print("-" * 60)
    for r in count_rows:
        print(f"  {r[0]}  {str(r[1]).ljust(20)}  {r[2]} rows")

    # ── Today's signal log — sorted by signal strength ───────────────────────
    rows = conn.run(
        """select ticker, session, primary_count, confirmation_count,
                  direction, trade_triggered, pa_verdict,
                  options_bias, bb_breakout_dir, cot_bias,
                  director_signal, senate_signal, notable_investor,
                  social_mention, session_time
           from   signal_log
           where  date(session_time at time zone 'UTC') = :d
           order  by primary_count desc nulls last,
                     confirmation_count desc nulls last,
                     session_time desc""",
        d=today
    )
    print(f"\nTODAY'S SIGNAL LOG — {len(rows)} scans (sorted by signal strength)")
    print("-" * 60)
    if rows:
        for r in rows:
            (ticker, session, primaries, confs, direction, triggered,
             pa_verdict, options_bias, bb_dir, cot_bias,
             director, senate, notable_inv, social, scan_time) = r

            primaries = primaries or 0
            confs     = confs or 0
            fired     = "🟢 TRADE" if triggered else ("🟡 CLOSE" if primaries >= 2 else "⚪")
            dir_str   = f" → {direction}" if direction else ""
            pa_str    = f" PA:{pa_verdict}" if pa_verdict else ""

            print(f"  {fired} {ticker:<8} [{session}]{dir_str}  "
                  f"primaries={primaries}  confs={confs}{pa_str}")

            signals = []
            if options_bias and options_bias != "NEUTRAL": signals.append(f"Options:{options_bias}")
            if bb_dir:        signals.append(f"BB:{bb_dir}")
            if cot_bias and cot_bias not in ("NEUTRAL", None): signals.append(f"COT:{cot_bias}")
            if director:      signals.append("DirectorBuy")
            if senate:        signals.append("SenateBuy")
            if notable_inv:   signals.append(f"Investor:{notable_inv[:20]}")
            if social:        signals.append(f"Social:{social[:20]}")
            if signals:
                print(f"           {' | '.join(signals)}")
    else:
        print("  (no signal records for today)")

    # ── Ticker spotlight (comma-separated list supported) ─────────────────────
    spotlight_env = os.environ.get("SPOTLIGHT_TICKER", "")
    spotlights    = [t.strip().upper() for t in spotlight_env.split(",") if t.strip()]

    for spotlight in spotlights:
        print(f"\nSPOTLIGHT: {spotlight}")
        print("-" * 60)

        sig_rows = conn.run(
            """select session, primary_count, confirmation_count, direction,
                      options_bias, bb_breakout_dir, cot_bias,
                      trade_triggered, pa_verdict, session_time
               from   signal_log
               where  ticker = :t
               order  by session_time desc
               limit  10""",
            t=spotlight
        )
        print(f"  Signal log ({len(sig_rows)} recent scans):")
        if sig_rows:
            for r in sig_rows:
                fired = "🟢 TRADE" if r[7] else ("🟡" if (r[1] or 0) >= 2 else "⚪")
                print(f"  {fired} {str(r[9])[:19]}  [{r[0]}]  P:{r[1]} C:{r[2]} "
                      f"dir:{r[3]} opts:{r[4]} bb:{r[5]} cot:{r[6]} pa:{r[8]}")
        else:
            print("  (no signal log entries — not yet scanned or log predates schema fix)")

        ni_rows = conn.run(
            """select investor_name, action, disclosed_at, source, notes
               from   notable_investors
               where  ticker = :t
               order  by disclosed_at desc
               limit  10""",
            t=spotlight
        )
        print(f"\n  Notable investor entries ({len(ni_rows)}):")
        if ni_rows:
            for r in ni_rows:
                print(f"  {r[2]}  {r[0]}  {r[1]}  [{r[3]}]  {str(r[4] or '')[:60]}")
        else:
            print("  (none)")

        sm_rows = conn.run(
            """select author, platform, sentiment, post_time, post_text
               from   social_mentions
               where  :t = any(tickers_found)
               order  by post_time desc
               limit  5""",
            t=spotlight
        )
        print(f"\n  Social mentions ({len(sm_rows)}):")
        if sm_rows:
            for r in sm_rows:
                print(f"  {r[3]}  @{r[0]} ({r[1]})  {r[2]}")
                print(f"    {str(r[4])[:100]}")
        else:
            print("  (none in social_mentions table)")

    print(f"\n{'='*60}\n")
    conn.close()


if __name__ == "__main__":
    main()
