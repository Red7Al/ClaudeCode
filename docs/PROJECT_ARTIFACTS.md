# Project Artifacts — catalogue & candidates

_Created 2026-07-24 (ToDo P-03 L43). A single index of the artifacts that support the
EndToEndTrading project — what exists today, and the candidates worth adding — so the growing
set of skills, memory items and documents stays discoverable._

The system in one line: a **Squeeze Scanner engine** scans 1,400+ instruments across every asset
type, records Squeeze setups with entry/stop/target, tracks them to outcome, and (per user)
places them as IG working orders — surfaced through a web app, Slack reports and X publications,
driven by scheduled jobs.

---

## 1. Existing artifacts

### 1.1 Skills (`skills_src/<name>/SKILL.md`, packaged as `*.skill`)
| Skill | Purpose |
|---|---|
| `ah-hvf-analysis` | The Squeeze squeeze method — detection rules, parameters, pipeline reference. |
| `ah-signal-stack` | Multi-factor / decision reference and troubleshooting. |
| `ah-hvf-orders` | Turning setups into actionable orders. |
| `ah-hvf-report` | The daily Squeeze report format. |
| `ah-quality-report` | The long per-instrument quality report. |
| `ah-working-orders` | IG working-order lifecycle. |
| `ah-instrument-dossier` | Per-instrument dossier generation. |
| `ah-x-publications` / `ah-x-writing-style` | X (Twitter) publication pipeline + house style. |
| `ah-web-formatting` | Layout/formatting rules + verification recipe for `hvf_web/index.html`. |
| `ah-deploy` | Ship a change live (branch→commit→merge→trigger workflow→verify/rollback). |
| `ah-change-control` | How to work the `ChangeRequests/*.txt` lists + the CR-tab parser contract. |

### 1.2 Memory (`.claude/.../memory/`, indexed in `MEMORY.md`)
`commit-workflow` · `cr-status-live` · `deploy-cron-tasks` · `equities-scan-only` ·
`qa-reconcile-existing` · `results-winners-same-dataset` · `stop-loss-trailing` ·
`table-name-search` · `web-formatting-skill`.

### 1.3 Documents (`docs/`)
- `SQUEEZE_METHOD.md` — the five rules + thresholds.
- `DECISIONS_AND_WEIGHTING.md` — scoring/weighting decisions.
- `price_action_framework.md` · `commodity_fundamentals.md` — analytical frameworks.
- `weekly_checklist.md` — operating checklist.
- `grafana_setup.md` + `grafana_dashboard.json` — monitoring dashboard.
- `How_The_Trading_System_Works_PlainEnglish.docx` (+ `_build_plain_english_doc.js`, `_gen_doc_visuals.py`).
- Root: `EndToEndTrading_SystemDoc_v1.7.docx`, `TradingSystemDesign.docx`, `README.md`, `BACKLOG.md`.

### 1.4 Operational artifacts
- **Web app** — `hvf_web/` (single-page `index.html` + Flask `server.py`), the primary UI.
- **Routines** — `routines/routine_*.md` (aus/uk/us open, monitor, session close, daily report, weekend review).
- **Scheduled jobs** — authoritative registry in `setup_cronjobs.py::JOBS` (cron-job.org → GitHub Actions),
  surfaced read-only in the app's Scheduled Jobs tab.
- **Tests** — `test_hvf_method.py`, `test_volume_score.py`, `test_bounce_monitor.py` + the pre-commit Squeeze suite.

---

## 2. Candidate artifacts to add (prioritised)

These would materially help onboarding, ops and change safety. None exist yet.

1. **Architecture diagram** — components + boundaries: scan engine, Supabase (system of record),
   cron-job.org + GitHub Actions (scheduling/compute), the Flask web app, and external services
   (Yahoo Finance prices, IG for orders/positions, Slack, X). A Mermaid diagram checked into `docs/`.
2. **Data-flow diagram** — price fetch → `price_history` (golden set) → scan → `snapshot.json` (cache)
   + `squeeze_history` / `hvf_triggers` → web app, reports, and IG working orders. Shows where each
   number originates (ties to the `results-winners-same-dataset` invariant).
3. **Database ERD** — the Supabase schema (`supabase_schema.sql`): `price_history`, `squeeze_history`,
   `hvf_triggers`, `web_users`, `app_config`, `working_orders`, `epic_lookup`, `x_publications`,
   `price_audit_log`, plus keys/relationships.
4. **Ops runbook** — deploy (see `ah-deploy`), restart the web server, run/scope a backfill, rotate
   secrets, and incident response (Yahoo 404s, IG session failures, snapshot staleness).
5. **Scheduled-jobs timeline** — a one-page daily UTC timeline of every cron job and why it runs when
   (e.g. price refresh 05:00 before the 05:30 Squeeze report), derived from `setup_cronjobs.JOBS`.
6. **Security model** — auth/roles (admin/gold/silver/guest), secret storage (GitHub Secrets, not local
   `.env`), IG-credential encryption, and the instruction/data trust boundary.
7. **Glossary** — consolidate the in-app Appendix terms into a referenceable doc.
8. **Test strategy / coverage map** — what the pre-commit suite gates, what each test covers, and gaps.

> Keep this index current: when a skill, memory item or doc is added or retired, update the relevant
> table here (a small P-20 documentation task).
