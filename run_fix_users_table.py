"""Drop the incorrectly-named 'users' table created by run_schema.py.
The correct table from the previous session is 'user_profiles'."""
import os, pg8000.native

conn = pg8000.native.Connection(
    host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
    database="postgres",
    user=os.environ["SUPABASE_USER"],
    password=os.environ["SUPABASE_DB_PASSWORD"],
    ssl_context=True
)

import datetime
today = datetime.date.today().isoformat()

# Check what's in signal_log for today
rows = conn.run(
    "select ticker, session, options_bias, bb_breakout_dir, trade_triggered "
    "from signal_log where date(session_time) = :d order by session, ticker",
    d=today
)
print(f"\n## signal_log for {today} ({len(rows)} rows)")
for r in rows:
    print(f"  {r[1]:<12} {r[0]:<10} options={r[2]:<10} bb={str(r[3]):<10} traded={r[4]}")

if not rows:
    print("  (empty — no scans logged today)")

conn.close()
raise SystemExit(0)

# Show ALL tables with ALL columns
tables = conn.run("""
    select table_name from information_schema.tables
    where table_schema = 'public' and table_type = 'BASE TABLE'
    order by table_name
""")

for (tbl,) in tables:
    cols = conn.run("""
        select column_name, data_type, is_nullable, column_default
        from information_schema.columns
        where table_schema = 'public' and table_name = :t
        order by ordinal_position
    """, t=tbl)
    count = conn.run(f"select count(*) from {tbl}")
    print(f"\n## {tbl}  ({count[0][0]} rows)")
    for c in cols:
        nullable = "" if c[2] == "YES" else " NOT NULL"
        default  = f"  default={c[3]}" if c[3] else ""
        print(f"  {c[0]}  {c[1]}{nullable}{default}")

conn.close()
