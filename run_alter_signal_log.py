"""Add ADX, OBV, and volume columns to signal_log. Safe to re-run."""
import os, pg8000.native

conn = pg8000.native.Connection(
    host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
    database="postgres",
    user=os.environ["SUPABASE_USER"],
    password=os.environ["SUPABASE_DB_PASSWORD"],
    ssl_context=True
)

for sql in [
    "alter table signal_log add column if not exists adx_signal    text",
    "alter table signal_log add column if not exists adx            numeric",
    "alter table signal_log add column if not exists di_plus        numeric",
    "alter table signal_log add column if not exists di_minus       numeric",
    "alter table signal_log add column if not exists obv_signal     text",
    "alter table signal_log add column if not exists obv_trend      text",
    "alter table signal_log add column if not exists volume_signal  text",
    "alter table signal_log add column if not exists volume_ratio   numeric",
]:
    conn.run(sql)
    col = sql.split("column if not exists")[1].strip().split()[0]
    print(f"OK: {col}")

conn.close()
print("Done")
