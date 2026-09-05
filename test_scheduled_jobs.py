

# ── The run stats are fetched in PARALLEL (user 2026-09-05: "still very slow to render") ───────────────
#
# MEASURED that day: one GitHub API call per unique workflow, 34 of them, each 1.1-2.3s and about
# 1,076 KB (per_page=100 is needed for the failure count). Serially that is 37.5 seconds of pure
# waiting before the page can render; with a bounded thread pool it is 4.2. Nothing about the calls is
# ordered or dependent -- they were serial only because a for-loop is the obvious way to write it.

def test_the_workflow_stats_are_fetched_concurrently(monkeypatch):
    """DETERMINISTIC, not timing-based. Each stub call waits on a barrier that only releases when two
    are in flight at once. Serial execution cannot satisfy it and the barrier breaks."""
    import threading
    from hvf_web import scheduled_jobs as sj

    barrier = threading.Barrier(2, timeout=5)
    seen = []

    def _stub(session, wf):
        seen.append(wf)
        barrier.wait()                      # raises BrokenBarrierError if nothing else arrives
        return {"executions": 1, "failures": 0, "failures_window": 1,
                "last_status": "success", "last_time": None, "last_duration_s": 1}

    monkeypatch.setattr(sj, "_gh_token", lambda: "token")
    monkeypatch.setattr(sj, "_fetch_runs", _stub)
    monkeypatch.setattr(sj, "_load_jobs", lambda: [("A", "0 1 * * *", "a.yml"), ("B", "0 2 * * *", "b.yml")])
    monkeypatch.setitem(sj._cache, "ts", 0.0)
    monkeypatch.setitem(sj._cache, "data", None)

    data = sj.get_jobs(force=True)

    assert len(seen) == 2, f"both workflows must be fetched: {seen}"
    assert all(j.get("executions") == 1 for j in data["jobs"]), "every job must carry its stats"


def test_the_pool_is_bounded():
    """Unbounded parallelism against one host is how an API starts 403ing."""
    import re, pathlib
    src = pathlib.Path("hvf_web/scheduled_jobs.py").read_text(encoding="utf-8")
    m = re.search(r"max_workers=(\d+)", src)

    assert m, "the thread pool must declare an explicit bound"
    assert 1 < int(m.group(1)) <= 32, f"max_workers={m.group(1)} is not a sane bound"


def test_a_failing_workflow_does_not_lose_the_others(monkeypatch):
    """One workflow erroring must not blank the whole page."""
    from hvf_web import scheduled_jobs as sj

    def _stub(session, wf):
        if wf == "bad.yml":
            raise RuntimeError("boom")
        return {"executions": 5, "failures": 0, "failures_window": 5,
                "last_status": "success", "last_time": None, "last_duration_s": 1}

    monkeypatch.setattr(sj, "_gh_token", lambda: "token")
    monkeypatch.setattr(sj, "_fetch_runs", _stub)
    monkeypatch.setattr(sj, "_load_jobs", lambda: [("A", "0 1 * * *", "ok.yml"), ("B", "0 2 * * *", "bad.yml")])
    monkeypatch.setitem(sj._cache, "ts", 0.0)
    monkeypatch.setitem(sj._cache, "data", None)

    jobs = {j["raw_title"]: j for j in sj.get_jobs(force=True)["jobs"]}

    assert jobs["A"]["executions"] == 5
    assert "error" in jobs["B"], "the failing one reports its error rather than vanishing"
