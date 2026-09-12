# Repository Working Rules

- Do not delete, remove, or destructively rewrite user data, registers, history, or configuration without explicit confirmation from the user first.
- When a format change is needed, preserve the original information in a compatible field or note unless the user explicitly approves its removal.
- Before any destructive change, state exactly what will be removed or rewritten and wait for confirmation.

## Before you write code — required, not advisory

The account owner has asked repeatedly for no rushing, thorough testing and periodic review. It kept not
happening because every session starts cold and re-decides how careful to be. These are the mechanical
form of those three asks. Each one is here because skipping it produced a real defect in September 2026.

1. **Enumerate before you touch shared state.** Before writing anything that reads or writes a shared
   table, column or file, list every other reader and writer of it — `Grep` for the table name and for
   `update <table>` / `insert into <table>` — and say in the transcript what you found. `reconcile_fills`
   was written without this; one grep would have found `run_working_order_sweep` already interpreting the
   same column, and the result was one column carrying four contradictory meanings.

2. **Profile the data before designing around it.** Query the real distribution first — NULLs, duplicates,
   empty strings, distinct values — and design for what is actually there. `working_orders.deal_id` is
   NULL on 45% of rows and `good_till` on 39%. Code keyed on `deal_id` and an update built with
   `str(deal_id)` both shipped because nobody ran `select count(deal_id) from working_orders`.

3. **Fixtures come from the table, not from memory.** A test built on an assumed data shape does not fail
   to catch the bug, it *certifies the assumption*. Every fixture in the suite that missed the NULL
   `deal_id` bug had a populated `deal_id`, because they were invented rather than derived.

4. **Mutation-test every guard, and believe the survivors.** Break the guard, prove the named test fails,
   restore. When a mutation SURVIVES, the test is wrong — not the mutation. Two survived on 2026-09-12 and
   both were fixtures that never reached the branch they claimed to cover.

5. **Run it before you call it done, and assert the effect.** Not the exit code, not a green test — the
   rows, the rendered screen, the account. The sweep logged "marked 4 row(s) EXPIRED" while writing none
   of them; the auto-closer ran 300 green passes and closed nothing. Both were found by running them, and
   neither would ever have been found by reading them.

## Operational completion

- For operational or deployment work, trace the complete path: build, publish, install, and verify the live consumer.
- Do not treat an uploaded artifact, green intermediate step, or preserved fallback as completion. Verify the final
  destination's generated timestamp or user-visible output.
- If the final handoff depends on an unavailable external secret or service, leave the request blocked and state the
  exact dependency; do not mark it complete or imply that the fallback is active.
- Record the request as in progress before editing and complete it only after end-to-end verification.
