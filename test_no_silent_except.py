"""Ratchet (owner 2026-11-13: every error 100% clear). A broad `except Exception` that neither uses the error, logs it
nor re-raises hides the cause (AAPL was reported 'no IG epic' for a failed step). Every tracked non-test module is
covered; a file's count may only fall (lower its number in BASELINE when you fix handlers), and a file not listed has 0."""
import ast
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).parent


def silent_handlers(path):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ExceptHandler) and (n.type is None or getattr(n.type, "id", "") in ("Exception", "BaseException")):
            body = ast.Module(n.body, [])
            names = {x.id for x in ast.walk(body) if isinstance(x, ast.Name)}
            attrs = {x.attr for x in ast.walk(body) if isinstance(x, ast.Attribute)}
            if not ((n.name and n.name in names) or any(isinstance(x, ast.Raise) for x in ast.walk(body)) or "exception" in attrs):
                out.append(n.lineno)
    return out


def tracked_modules():
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [f for f in files if not pathlib.Path(f).name.startswith(("test_", "conftest")) and not f.startswith("skills_src")]


BASELINE = {
    "analyst_signals.py": 1,
    "build_ionos_package.py": 1,
    "closing_bar_capture.py": 1,
    "commodity_supply_demand.py": 3,
    "config_store.py": 3,
    "cron_spec.py": 1,
    "db_pool.py": 5,
    "docs/_capture_guide_images.py": 1,
    "hot_stock_tweet.py": 7,
    "hvf_clean.py": 1,
    "hvf_web/build_snapshot.py": 9,
    "hvf_web/order_bridge.py": 1,
    "hvf_web/scheduled_jobs.py": 1,
    "hvf_web/server.py": 35,
    "hvf_web/web_users.py": 1,
    "ig_shim.py": 34,
    "instrument_dossier.py": 6,
    "instrument_metrics.py": 2,
    "instrument_name.py": 5,
    "intraday_signals.py": 22,
    "login_throttle.py": 1,
    "market_hours.py": 1,
    "migrate_runtime_state_to_supabase.py": 1,
    "notify.py": 4,
    "order_filter_audit.py": 2,
    "price_action.py": 4,
    "price_audit.py": 5,
    "price_store.py": 2,
    "publish_one_to_x.py": 4,
    "quality_report.py": 16,
    "run_cot_report.py": 3,
    "run_cron_watch.py": 1,
    "run_daily_report.py": 4,
    "run_data_quality_audit.py": 1,
    "run_diagnostics.py": 1,
    "run_fundamentals_precompute.py": 4,
    "run_hvf_orders.py": 1,
    "run_hvf_report.py": 4,
    "run_scanner_report_email.py": 1,
    "run_session.py": 5,
    "run_session_watchdog.py": 1,
    "run_uk_morning_brief.py": 1,
    "scanner_snapshot_store.py": 4,
    "sector_cache.py": 2,
    "setup_cronjobs.py": 3,
    "signals.py": 3,
    "social_monitor.py": 4,
    "squeeze_history.py": 2,
    "technical_summary.py": 1,
    "trade_email.py": 11,
    "web_store.py": 1,
    "working_orders_report.py": 8,
    "x_publish.py": 3,
}


def test_silent_handler_count_never_rises():
    worse = {}
    for f in tracked_modules():
        n = len(silent_handlers(ROOT / f))
        if n > BASELINE.get(f, 0):
            worse[f] = (n, BASELINE.get(f, 0))
    assert not worse, f"silent broad handlers rose (found, allowed): {worse}. Log or use the error."
