# =============================================================================
# File:         run_schema.py
# Description:  Run supabase_schema.sql against the Supabase PostgreSQL database.
#               Called once via GitHub Actions to initialise all tables.
# =============================================================================

import os
import pg8000.native
from pathlib import Path

conn = pg8000.native.Connection(
    host="aws-0-eu-west-1.pooler.supabase.com",
    port=6543,
    database="postgres",
    user=os.environ["SUPABASE_USER"],
    password=os.environ["SUPABASE_DB_PASSWORD"],
    ssl_context=True
)

sql = Path("supabase_schema.sql").read_text(encoding="utf-8")

# Split on semicolons, skip blanks and comment-only blocks
statements = [s.strip() for s in sql.split(";") if s.strip()]
ok = failed = skipped = 0
for stmt in statements:
    # Skip pure comment blocks
    lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
    clean = "\n".join(lines).strip()
    if not clean:
        skipped += 1
        continue
    try:
        conn.run(clean)
        ok += 1
    except Exception as e:
        print(f"WARN: {str(e)[:120]}")
        failed += 1

conn.close()
print(f"\nSchema run complete — {ok} ok  {failed} warnings  {skipped} skipped")
