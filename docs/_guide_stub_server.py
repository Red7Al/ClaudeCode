"""Serves the web app's real page code (hvf_web/) with PLACEHOLDER answers to every /api/ call, so the guide
screenshots (docs/_capture_guide_images.py) show the real screens and nothing personal: no account, no
credentials, no scanner data. Local only (127.0.0.1); it never reads the database, IG or any secret.

    ./.venv/Scripts/python.exe docs/_guide_stub_server.py [port]
"""
import os
import sys

from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "hvf_web")
app = Flask(__name__)
STATE = {"ig_set": False}   # flipped by POST /stub/state so one run can show "Not set" and "Set"

IG_FIELDS = [("ig_api_key", "IG API key"), ("ig_username", "IG username"),
             ("ig_password", "IG password"), ("ig_account_id", "IG account ID")]


@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/<path:name>")
def static_file(name):
    return send_from_directory(WEB, name)


@app.route("/stub/state", methods=["POST"])
def stub_state():
    STATE.update(request.get_json(silent=True) or {})
    return jsonify(STATE)


@app.route("/api/me")
def me():
    return jsonify({"name": "Demo User", "subscription": "premium", "is_admin": False, "is_support": False})


@app.route("/api/credentials", methods=["GET", "POST"])
def credentials():
    if request.method == "POST":
        return jsonify({"saved": [k for k, _ in IG_FIELDS]})
    s = STATE["ig_set"]
    return jsonify({"sections": [{
        "id": "IG", "scope": "ig", "admin_only": False, "editable": True,
        "note": "Your own IG account — each user trades their own account.",
        "fields": [{"key": k, "label": label, "set": s, "masked": "••••" + "WXYZ"[:4] if s else ""}
                   for k, label in IG_FIELDS]}]})


@app.route("/api/ig-status")
def ig_status():
    if STATE["ig_set"]:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "no_creds": True, "note": "No IG credentials set"})


@app.route("/api/guides")
def guides():
    import json
    with open(os.path.join(HERE, "guides", "_manifest.json"), encoding="utf-8") as fh:
        return jsonify({"guides": json.load(fh)})


@app.route("/api/guides/<slug>")
def guide(slug):
    return send_from_directory(os.path.join(HERE, "guides"), os.path.basename(slug) + ".html", mimetype="text/html")


@app.route("/api/<path:rest>", methods=["GET", "POST"])
def anything(rest):
    return jsonify({})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 5071, debug=False)
