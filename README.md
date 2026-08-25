# Squeeze Scanner — EndToEndTrading

A squeeze (high-volatility-funnel) continuation scanner and trading system. It screens roughly 1,773
instruments across equities, indices, FX and commodities for a specific continuation pattern, scores and
ranks what it finds, publishes the results to a web application, and can place and manage the resulting
orders against an IG account.

Live at **<https://www.squeezescanner.cloud>**.

## What it does

1. **Scan** — detect squeezes across six timeframes per instrument and keep the best
   (`price_action.py`, `intraday_signals.py`).
2. **Score and gate** — rank by signal state and pattern quality, then apply the tradeability gate
   (`R:R ≥ 3`) and the publication floor.
3. **Publish** — build a snapshot, serve it through the Flask app (`hvf_web/`), and optionally post
   selected setups to X.
4. **Trade** — place and manage orders through IG via the Web Bridge, including a trailing-stop amendment
   and an opt-in "let winners run" model.
5. **Review** — replay historical squeezes to report performance and recommend configurations
   ("Best Settings", "What separates the winners").

## The pattern, in one paragraph

After a confirmed trend, price coils into a tightening range — lower highs pressing down on higher lows —
until it breaks out. A setup must satisfy all five rules (prior trend, lower highs, higher lows, at least
30% convergence, and a fresh breakout pivot within 60 bars). Entry is the third pivot, the stop sits just
beyond the opposite pivot, and the target is re-anchored to the prior trend's true exhaustion extreme.

Full detail, kept in step with the code: **[`docs/SQUEEZE_METHOD.md`](docs/SQUEEZE_METHOD.md)**.

## Documentation

| Document | What it covers |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | **Start here to work on the code** — tests, commits, deploys, and the traps that have actually bitten |
| [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md) | Running the system day to day; health checks, backfills, failures |
| [`docs/SQUEEZE_METHOD.md`](docs/SQUEEZE_METHOD.md) | Detection rules, thresholds, scoring |
| [`docs/DECISIONS_AND_WEIGHTING.md`](docs/DECISIONS_AND_WEIGHTING.md) | How a detected setup is ordered, gated and published |
| [`IONOS_DEPLOYMENT.md`](IONOS_DEPLOYMENT.md) | Hosting and deployment specifics |
| [`AGENTS.md`](AGENTS.md) | Working rules for anyone (human or agent) changing this repository |

## Quick start

```bash
./.venv/Scripts/python.exe -m pytest -q -m "not live_state"   # the offline suite — 466 tests
ASSUME_YES=1 ./deploy_ionos.sh                                # build, upload, verify
curl -s https://www.squeezescanner.cloud/api/build            # which build the API is actually running
```

Two things that surprise everyone once: **a push does not update the website** (deploy is a separate
step), and a deploy updates static files but often leaves the resident Flask module on the previous
build — always check `/api/build`. Both are explained in `CLAUDE.md`.

## Layout

- `price_action.py`, `intraday_signals.py`, `signals.py` — detection and scoring
- `hvf_web/` — the Flask app (`server.py`), its client (`app.js`, `index.html`) and snapshot build
- `ig_shim.py`, `hvf_web/order_bridge.py` — IG account access and order execution
- `squeeze_history.py`, `scanner_snapshot_store.py` — historical replay and snapshot persistence
- `.github/workflows/` — 55 workflows, triggered by cron-job.org via `workflow_dispatch`
  (registry: `setup_cronjobs.py::JOBS`; GitHub-native `schedule:` is banned)
- `ChangeRequests/` — the live worklist behind the admin Change Requests tab (deliberately not in git)
- `skills_src/` — task-specific working procedures, packaged to `*.skill` by `build_skills.py`
