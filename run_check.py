import os, pg8000.native

conn = pg8000.native.Connection(
    host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
    database="postgres",
    user=os.environ["SUPABASE_USER"],
    password=os.environ["SUPABASE_DB_PASSWORD"],
    ssl_context=True
)

rows = conn.run("""
    select
        c.relname                                      as table_name,
        c.oid                                          as oid,
        pg_size_pretty(pg_total_relation_size(c.oid))  as size,
        (select count(*) from information_schema.columns
         where table_name = c.relname
           and table_schema = 'public')                as col_count,
        s.n_live_tup                                   as row_count
    from   pg_class c
    join   pg_namespace n  on n.oid = c.relnamespace
    left   join pg_stat_user_tables s on s.relname = c.relname
    where  n.nspname = 'public'
      and  c.relkind = 'r'
    order  by c.oid
""")
conn.close()

# Markdown for GitHub Actions step summary
print("## Supabase Table Check\n")
print(f"| Table | OID | Rows | Size |")
print(f"|-------|-----|------|------|")
for r in rows:
    print(f"| `{r[0]}` | {r[1]} | {r[4] or 0} | {r[2]} |")
print(f"\n_{len(rows)} tables in public schema — lower OID = created earlier_")
