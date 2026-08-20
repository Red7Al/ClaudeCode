# Repository Working Rules

- Do not delete, remove, or destructively rewrite user data, registers, history, or configuration without explicit confirmation from the user first.
- When a format change is needed, preserve the original information in a compatible field or note unless the user explicitly approves its removal.
- Before any destructive change, state exactly what will be removed or rewritten and wait for confirmation.

## Operational completion

- For operational or deployment work, trace the complete path: build, publish, install, and verify the live consumer.
- Do not treat an uploaded artifact, green intermediate step, or preserved fallback as completion. Verify the final
  destination's generated timestamp or user-visible output.
- If the final handoff depends on an unavailable external secret or service, leave the request blocked and state the
  exact dependency; do not mark it complete or imply that the fallback is active.
- Record the request as in progress before editing and complete it only after end-to-end verification.
