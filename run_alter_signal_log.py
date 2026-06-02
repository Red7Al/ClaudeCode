"""
Add 4 columns to signal_log that signals.py now tries to insert.
Without these, every scan throws a DB warning and skips logging.
Safe to re-run — uses ADD COLUMN IF NOT EXISTS.
"""
import os, pg8000.native

conn = pg8000.native.Connection(
    host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
    database="postgres",
    user=os.environ["SUPABASE_USER"],
    password=os.environ["SUPABASE_DB_PASSWORD"],
    ssl_context=True
)

alterations = [
    "alter table signal_log add column if not exists call_put_ratio numeric",
    "alter table signal_log add column if not exists primary_count   integer default 0",
    "alter table signal_log add column if not exists direction        text",
    "alter table signal_log add column if not exists pa_verdict       text",
]

for sql in alterations:
    conn.run(sql)
    col = sql.split("column if not exists")[1].strip().split()[0]
    print(f"OK: {col}")

# Confirm
cols = conn.run("""
    select column_name, data_type
    from information_schema.columns
    where table_schema='public' and table_name='signal_log'
    order by ordinal_position
""")
print(f"\nsignal_log now has {len(cols)} columns:")
for c in cols:
    print(f"  {c[0]}  ({c[1]})")

conn.close()
