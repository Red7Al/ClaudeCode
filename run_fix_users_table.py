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
print("Dropped: users (or did not exist)")

# Show actual columns in user_profiles
cols = conn.run("""
    select column_name, data_type
    from information_schema.columns
    where table_schema = 'public' and table_name = 'user_profiles'
    order by ordinal_position
""")
print(f"\nuser_profiles columns:")
for c in cols:
    print(f"  {c[0]} ({c[1]})")

# Show row count
count = conn.run("select count(*) from user_profiles")
print(f"\nuser_profiles row count: {count[0][0]}")

conn.close()
