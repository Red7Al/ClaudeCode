# Account isolation — target architecture

**Status:** proposed design, 2026-09-12. Nothing here is built.
**Requirement owner:** the account owner. **Author:** this session.

> "Users' transactions and monies must NEVER be mixed up." — and, stated 2026-09-12:
> **a person may hold more than one account**, and the reported symptom is that
> **an IG account's data is visible across other logins**.

This document exists because the previous remedy (`account_scope.py`, 2026-09-12) is a *correct
workaround for the wrong model*. It reduces nine interpreters of identity to one, which is a real
improvement, but it leaves authorization as something every query must remember to apply. This
design removes that obligation.

---

## 1. What is actually wrong

Measured in the code and schema on 2026-09-12. Facts marked *(measured)* were read directly;
*(inferred)* means reasoned from those facts.

All database facts below were **measured against the live Supabase instance on 2026-09-12** by an
independent audit that was briefed from the requirement and explicitly denied sight of the prior
session's handover.

| # | Finding | Evidence |
|---|---|---|
| 1 | **Entitlement is a column, not a relation, and the two registries cannot be joined.** Web logins live in a **JSON document** (`web_json_store` key `web_users`, rev 141) keyed by login *name* with no id at all; trading profiles are a *table*. No foreign key between them is possible in the current shape. | live store + `user_profiles` *(measured)* |
| 2 | **REGRESSION RISK to already-settled work.** Multi-account binding was resolved by the owner several sessions ago and is working: the live table has PK on `id` and UNIQUE on `ig_account_id` only, with **no** unique index on `login`. But `run_schema.py:308-310` still *declares* one, and `run_schema.py` is invoked by seven workflows. The next run creates it and re-imposes the single-account constraint that was deliberately removed. **Delete that migration block.** Nothing else about binding is open — do not re-open it. | *(measured live + `run_schema.py:308-310`)* |
| 3 | **THREE identity namespaces, not two.** (A) login name, (B) `user_profiles.id` uuid, (C) `user_profiles.name` — `Owner`/`Wife`/`Son`. Namespace C is undocumented: `working_orders.lwr_owner_login` is *named* for a login but *holds a profile name* (49 rows = `Owner`), while its own parameter doc calls it a "web-login binding". Column name and contents disagree. | `ig_shim.py:3295` vs `:2245` *(measured)* |
| 4 | **`working_orders.user_id` has no FK and no CHECK**, is `text`, and holds 5 distinct values across two namespaces: Owner uuid 263, `Alex` 178, `Rich` 38, Wife uuid 2, `Red7dp` 1 (482 total). | *(measured)* |
| 5 | **The namespace split is exactly the "reached the broker" split.** All 265 uuid rows carry a `deal_id`; all 217 login-name rows carry none — zero exceptions. The column is silently encoding a **lifecycle stage**, not just an owner. This is the same row set as CLAUDE.md's "deal_id NULL on 45%" (217/482 = 45.02%). | *(measured)* |
| 6 | **Revocation silently does nothing.** `account_scope._FLOOR` hard-codes `Alex → Owner profile` and merges database rows *over* it. Rebinding the Owner profile to another login yields **both** bound; deleting every binding still yields Alex bound. An administrator revoking access would believe it worked. | `account_scope.py:55,68-81` *(measured by exercising the merge)* |
| 7 | **Unconstrained text identity columns already drift.** `web_activity_log.user_id` (text, no FK) contains `Cameron Watson` and `pytest-throttle2` alongside real logins — proof, not theory, that these columns accept anything. | *(measured)* |
| 8 | **Two rows are owned by nobody reachable.** `working_orders` id 1–2 carry the Wife uuid, and Wife's login is NULL, so `owns_row()` is false for all five logins and every scheduled job. | *(measured)* |
| 9 | **Money tables are sound where FKs exist.** `positions`/`trade_log`/`daily_pnl.user_id` are `uuid` **with real foreign keys** to `user_profiles`, and hold only the Owner uuid (9/42/15 rows). Unbound logins are structurally unable to appear there — not merely absent today. This is the model the rest of the system should copy. | *(measured)* |
| 10 | **Exactly one account can trade.** Only `Alex` holds IG credentials; all four other logins resolve to none. A `None` login falls back to the owner's environment credentials. Three separate constants independently pin the job identity to `"Alex"`. | `ig_shim._resolve_ig_creds` *(measured live)* |
| 11 | **Isolation is enforced by remembering.** Every read and write must apply the predicate itself; a query that omits it returns **everything**. | *(inferred from 1–8)* |
| 12 | **`working_orders` holds four different kinds of thing** — broker orders, watch entries, UI dismissals, other users' rows — so a cache-refresh operation can destroy primary data. | prior handover §5.2 *(reported, not re-verified)* |

**Finding 9 is the important one for the design.** Where this system uses a uuid with a real foreign
key, isolation already holds *by construction* and cannot be got wrong. Where it uses unconstrained
text, it has already drifted (finding 7). The architecture below is largely "make the rest of the
system look like `positions`".

**Finding 6 is the most urgent defect** and is independent of everything else: access cannot currently
be revoked. It is a five-line fix and should not wait for the migration.

**The structural defect is #6.** Everything else is a symptom of it. When the safe behaviour depends on
each author remembering a predicate, the system's correctness is a function of attention, and attention
is exactly what a cold session does not bring. Finding #2 additionally encodes a requirement that is
**the opposite of the stated one**.

### Why the current fix cannot hold

`account_scope` makes the *right* answer available. It does not make the *wrong* answer impossible.
A new endpoint written in three months that says `select ... from positions` — with no predicate —
returns every account's rows and no test fails, because no test knows that endpoint exists.

That is the difference between a rule and a constraint, and it is the whole argument of this document.

---

## 2. Design principles

1. **Fail closed, not open.** A forgotten predicate must yield *nothing*, never *everything*.
2. **Entitlement is data, not code.** Adding a user, or granting someone a second account, is an
   `INSERT` — never an edit to Python and never a migration.
3. **One identity namespace.** A row's owner is referenced, never matched by string.
4. **The database is the last line.** Postgres does not start cold and cannot forget. Anything that
   can be expressed as a constraint belongs there rather than in a convention.
5. **Multi-account is a supported case, not an anomaly.** A person may hold several accounts; totals
   are reported *per account* rather than merged, and never forbidden.
6. **Broker state and user intent are different data** with different owners and lifecycles, and do not
   belong in one table.

---

## 3. The model

Three entities, and the relationship between them is explicit.

```
  logins  ──< account_grants >──  accounts  ──<  orders / positions / trade_log / daily_pnl
 (subject)      (entitlement)     (resource)                (owned rows)
```

- **`accounts`** — the money-bearing entity: one row per IG account. The *resource*.
- **`logins`** — who authenticates. The *subject*.
- **`account_grants`** — many-to-many, with a role. **This is the authorization**, and it is the only
  place authorization exists.

```sql
create table accounts (
    id            uuid primary key default gen_random_uuid(),
    ig_account_id text        not null unique,
    label         text        not null,              -- 'Owner', 'Wife', 'Son'
    active        boolean     not null default true,
    created_at    timestamptz not null default now()
);

create table logins (
    id       uuid primary key default gen_random_uuid(),
    username text not null unique,                   -- mirrors hvf_web/web_users
    active   boolean not null default true
);

-- The entitlement. A person with two accounts has two rows. Nothing forbids it.
create table account_grants (
    login_id   uuid not null references logins(id)   on delete cascade,
    account_id uuid not null references accounts(id) on delete restrict,
    role       text not null check (role in ('trader','viewer')),
    granted_at timestamptz not null default now(),
    granted_by text,
    primary key (login_id, account_id)
);
```

Every owned row then carries **one** owning reference, mandatory:

```sql
alter table positions   add column account_id uuid not null references accounts(id);
alter table trade_log   add column account_id uuid not null references accounts(id);
alter table daily_pnl   add column account_id uuid not null references accounts(id);
-- and the order tables of §5
```

`NOT NULL` + foreign key retires finding #5 permanently: a money row owned by nobody becomes
unrepresentable rather than merely absent today.

---

## 4. The mechanism that makes leaks impossible

**Postgres row-level security.** The application declares *who is asking*, once per transaction; the
database filters every query against every table, whether or not the query author remembered.

```sql
alter table positions enable row level security;

create policy positions_visible_by_grant on positions
using (
    account_id in (
        select g.account_id
          from account_grants g
          join logins l on l.id = g.login_id
         where l.username = current_setting('app.current_login', true)
           and l.active
    )
);
```

Applied to `positions`, `trade_log`, `daily_pnl` and the order tables. The application sets the caller
once, inside the transaction:

```sql
set local app.current_login = 'Alex';
```

**What this changes.** The forgotten-predicate query from §1 now returns zero rows instead of every
account's. The failure mode inverts from *silent leak* to *visible emptiness* — a bug someone reports
in an hour rather than one that hides for two months.

### Three implementation details that will bite if missed

1. **`SET LOCAL` is transaction-scoped, and this codebase pools connections** (`db_pool.py`, imported by
   65 modules). It must be set inside each transaction, never once per connection — a pooled connection
   carrying the previous borrower's identity is the same leak in a new costume. The correct shape is a
   single helper that opens a transaction, sets the identity, and yields; nothing else may obtain a
   connection for a request path.
2. **RLS does not apply to the table owner or to superusers** unless forced. The application must connect
   as a role that is *not* the owner, and the tables need `force row level security` so that even the
   owner is filtered. Getting this wrong makes every policy silently inert — and the system would look
   perfectly correct while enforcing nothing, which is the most dangerous possible outcome. There is a
   test for exactly this in §6.
3. **The duplicate-order guard must not become fail-closed.** For money, seeing too few rows is safe;
   for that guard, seeing too few rows means it misses an existing order and places a **second real
   order**. It must run *within its own account's* context, where RLS shows it every order for that
   account — and it must treat an empty identity as an error that refuses to trade, never as "no orders
   exist". This asymmetry is the single subtlest thing in this design.

---

## 5. Splitting the overloaded table

`working_orders` currently holds four kinds of thing (finding #7). Mixing a rebuildable *cache* of
broker state with *primary data* that exists nowhere else is what allowed a cache-refresh operation to
expire 12 filled and 19 live rows on 2026-09-04.

| New table | Contents | Owner of truth | May a broker-driven job delete it? |
|---|---|---|---|
| `ig_working_orders` | mirror of IG's book | IG — rebuildable at any time | yes, that is its purpose |
| `order_intents` | what the user wants to happen | us — the only copy | **never** |
| `preorder_dismissals` | per-login UI suppression | us | never |

The synthetic `WATCH-…` identifier disappears with this split: it exists only because rows with no
broker order were forced into a table whose key is a broker identifier.

---

## 6. How this gets proved — not asserted

Isolation is verified by properties that hold over the whole system, not by examples.

**A. The exhaustive entitlement matrix.** For every `(login × account)` pair with no grant, every
surface must return zero rows of that account's data. This is a cross product, enumerated from the live
registries — not a spot check on the accounts that happen to exist today. It is the direct executable
form of the stated requirement.

**B. The negative-space test.** A login with no grants sees nothing, anywhere, on every route. If this
passes while (A) fails, the policies are inert — see §4 detail 2.

**C. RLS is actually in force.** Assert `relrowsecurity` and `relforcerowsecurity` on every protected
table, and that the application's role is not the table owner. A policy that exists but does not apply
is worse than no policy, because it manufactures confidence.

**D. Mutation as a CI gate, not a practice.** For each guard: break it, prove a **named** test fails,
restore. A mutation that survives is an unprotected path and fails the build. This is the only check
that detects a test which re-implements production logic instead of calling it — the failure that let
the September defect through a green suite.

**E. Separation of duties.** The tests for an isolation guard are written from *the requirement*, by a
party that did not write the implementation and cannot see its internals. Where that party is an AI
agent it must be a **cold** one: an agent carrying the implementer's context inherits the implementer's
blind spots and will confirm rather than test.

**F. No orphans.** `select count(*) ... where account_id is null` must be structurally impossible —
enforced by `NOT NULL`, and asserted as a live test so a future migration cannot quietly relax it.

---

## 7. Migration — safe for a live money system

Expand → migrate → contract. Every stage is reversible, and no stage has a window in which orders or
money are unreadable. **No row is deleted at any point**; the old columns are dropped only in stage 6,
only after a verification window, and only with explicit sign-off.

| Stage | Action | Reversible? | Gate before proceeding |
|---|---|---|---|
| 1 | Create `accounts`, `logins`, `account_grants`. Populate from the existing registries. | yes — additive only | grants reproduce today's visibility exactly, proven by diff |
| 2 | Add nullable `account_id` to owned tables. Backfill from **both** namespaces. | yes | 100% of rows resolve; any unresolved row is listed and decided by hand, never guessed |
| 3 | Dual-write `account_id` alongside the existing column. | yes | the two agree on every new row for a full trading week |
| 4 | Enable RLS in **permissive report-only mode**: log what a policy *would* have blocked, block nothing. | yes — no behaviour change | the log shows zero legitimate accesses would break |
| 5 | Enforce RLS. Switch reads to `account_id`. Set `NOT NULL`. | yes — policies can be dropped | tests A–F all green |
| 6 | Drop `user_profiles.login`, the unique index of finding #2, and the dual-namespace column. | **no** | owner's explicit sign-off (`AGENTS.md`: state exactly what will be removed and wait) |

Stage 4 is the one that makes this safe to do at all: it produces evidence of what enforcement *would*
do before anything is enforced, on live traffic, with no risk.

**Finding #2's unique index is removed in stage 6**, which is what restores the multi-account case.
Two accounts under one login then report as two accounts, side by side — never merged into one total,
which was the legitimate worry behind the index and is properly answered by the reporting layer rather
than by forbidding the data.

---

## 8. What this costs, honestly

- Stages 1–3 are low hazard and mostly mechanical.
- Stage 4 requires the pooled-connection work of §4 detail 1 and is where real design effort sits.
- Stage 5 touches every read path; it is the stage that needs the full verification suite green.
- Stage 6 is irreversible and gated on sign-off.

**This is not a patch and should not be attempted as one.** A partial application — RLS on some tables,
or `account_id` on some rows — produces a system that looks protected and is not. If it is not taken to
stage 5, it is better not to start: finding #2 alone can be reverted independently as a one-line change
to restore multi-account support without any of the rest.

## 9. What is deliberately NOT solved here

- Instrument, snapshot and scanner data remain shared. They are not per-account and making them so
  would be wrong.
- Fee and billing *calculation* is unchanged; only the population it reads becomes correct by construction.
- Nothing here changes trading logic, thresholds, or the method.
