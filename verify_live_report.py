#!/usr/bin/env python3
"""Open a login's LIVE Scanner Report as they would see it -- read-only (owner 2026-10-09).

    ./.venv/Scripts/python.exe verify_live_report.py [--login Alex] [--port 5061]

1. Refuses to run unless the live site's /api/build fingerprint equals this checkout's HEAD fingerprint
   (build_ionos_package.py derives it from HEAD), so the page served locally IS the deployed page code.
2. Fetches that login's exact /api/records and /api/config payloads from the LIVE site through
   /api/report-check (REPORT_CHECK_KEY), built server-side with the login's own session.
3. Serves the real page locally with ONLY those two calls answered from the live payload, under a token
   valid in this process alone. Open the printed URL and read the Scanner Report tab: what renders is what
   the login sees. Nothing is placed, changed or deleted; server.py's __main__ (the order bridge) never runs.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

SITE = "https://www.squeezescanner.cloud"
TOKEN = "local-live-report-check"


def _get(path, headers=None):
    req = urllib.request.Request(SITE + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default="Alex")
    ap.add_argument("--port", type=int, default=5061)
    a = ap.parse_args()
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    sys.path.insert(0, root)

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    local_fp = hashlib.sha256(head.encode()).hexdigest()[:12]
    live_fp = _get("/api/build").get("fingerprint")
    if live_fp != local_fp:
        print(f"REFUSED: live build {live_fp} != this checkout {local_fp} ({head[:7]}). Deploy or check out "
              "the deployed commit first, or the page served here would not be the live page.")
        return 2
    print(f"live build {live_fp} == checkout {head[:7]}")

    from dotenv import load_dotenv
    load_dotenv(".env")
    key = os.environ.get("REPORT_CHECK_KEY", "")
    if not key:
        print("REFUSED: REPORT_CHECK_KEY is not in .env")
        return 2
    live = _get(f"/api/report-check?login={a.login}", {"X-Report-Check-Key": key})
    recs, cfg = live["records"], live["config"]
    print(f"live payload for {a.login}: snapshot {recs.get('generated_utc')}, {len(recs.get('records') or [])} records,"
          f" report_unavailable={recs.get('report_unavailable')}")

    os.environ["HVF_DISABLE_WARMERS"] = "1"
    from flask import jsonify, request
    from hvf_web import server
    # READ-ONLY, ENFORCED: the token maps to a real login, so any POST from the page (settings, an order)
    # would act on that real account. Every non-GET request is refused before it reaches a handler.
    @server.app.before_request
    def _read_only():
        if request.method not in ("GET", "HEAD") or request.path.startswith("/api/refresh"):
            return jsonify({"error": "verify_live_report is read-only"}), 403   # /api/refresh is a GET that dispatches a scan
    server.app.before_request_funcs.setdefault(None, []).insert(0, server.app.before_request_funcs[None].pop())
    server.app.view_functions["api_records"] = lambda: jsonify(recs)
    server.app.view_functions["api_config"] = lambda: jsonify(cfg)
    _valid, _name = server._wu.valid_tokens, server._wu.name_for_token
    server._wu.valid_tokens = lambda: set(_valid()) | {TOKEN}
    server._wu.name_for_token = lambda t: a.login if t == TOKEN else _name(t)
    print(f"open http://127.0.0.1:{a.port}/ with localStorage.sq_auth = '{TOKEN}' (valid in this process only)")
    server.app.run(host="127.0.0.1", port=a.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
