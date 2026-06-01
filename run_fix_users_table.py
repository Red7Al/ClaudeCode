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

conn.run("drop table if exists users cascade")
print("Dropped: users")

# Confirm user_profiles is intact
rows = conn.run("select username, display_name, risk_per_trade_pct, paper_trade from user_profiles order by username")
print(f"\nuser_profiles ({len(rows)} rows):")
for r in rows:
    print(f"  {r[0]:<10} {r[1]:<20} risk={r[2]}%  paper={r[3]}")

conn.close()
