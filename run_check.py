import os, requests, pg8000.native

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

lines = ["*Supabase Table Check*", f"{'Table':<30} {'OID':>10} {'Rows':>8} {'Size':>10}", "─" * 62]
for r in rows:
    lines.append(f"`{r[0]:<30}` {r[1]:>10} {str(r[4] or 0):>8} {r[2]:>10}")
lines.append(f"\n_{len(rows)} tables in public schema. Lower OID = created earlier._")

msg = "\n".join(lines)
print(msg)

slack = os.environ.get("SLACK_ALERTS", "")
if slack:
    requests.post(slack, json={"text": msg}, timeout=10)
