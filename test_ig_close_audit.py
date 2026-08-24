"""A user must never be left unsure whether a position they asked to close actually closed.

_append_ig_close_audit has written a durable, host-side, append-only record of every close attempt since
2026-08-21 -- deliberately independent of Supabase, because an audit trail for a live broker action must
survive the outage that might have caused the problem. But nothing ever read it back. The evidence existed
only on disk: once the result dialog was dismissed or the page reloaded, the user had no way to see what
IG had actually said (user 2026-08-22, deferred by the requester to 2026-08-23).

These tests cover the read path and, most importantly, its authorisation boundary: a close audit names
which user closed which broker position, so one user must never see another's.
"""

import json

import pytest

from hvf_web import server


@pytest.fixture
def audit(tmp_path, monkeypatch):
    path = tmp_path / "ig_close_audit.jsonl"
    monkeypatch.setattr(server, "_IG_CLOSE_AUDIT_FILE", str(path))
    monkeypatch.setattr(server, "_DATA_DIR", str(tmp_path))
    return path


def _identity(monkeypatch, name, *, admin=False):
    monkeypatch.setattr(server._wu, "name_for_token", lambda tok: name if tok == "token" else "")
    monkeypatch.setattr(server._wu, "is_admin", lambda who: admin and who == name)


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


ROWS = [
    {"at": "2026-08-23T09:00:00+00:00", "user": "Alex", "deal_id": "D1", "phase": "submitted", "detail": "matched"},
    {"at": "2026-08-23T09:00:01+00:00", "user": "Alex", "deal_id": "D1", "phase": "confirmed", "detail": "ACCEPTED"},
    {"at": "2026-08-23T09:05:00+00:00", "user": "Sam",  "deal_id": "D9", "phase": "not_closed", "detail": "REJECTED"},
]


def test_login_is_required(audit):
    assert server.app.test_client().get("/api/ig-close-audit").status_code == 401


def test_a_user_sees_only_their_own_attempts(audit, monkeypatch):
    """THE BOUNDARY. This trail names which user closed which live broker position."""
    _write(audit, ROWS)
    _identity(monkeypatch, "Alex")

    body = server.app.test_client().get("/api/ig-close-audit", headers={"X-Auth": "token"}).get_json()

    assert body["scope"] == "mine"
    assert {e["user"] for e in body["entries"]} == {"Alex"}
    assert all(e["deal_id"] != "D9" for e in body["entries"]), "another user's deal id leaked"


def test_an_admin_sees_every_users_attempts(audit, monkeypatch):
    _write(audit, ROWS)
    _identity(monkeypatch, "Alex", admin=True)

    body = server.app.test_client().get("/api/ig-close-audit", headers={"X-Auth": "token"}).get_json()

    assert body["scope"] == "all"
    assert {e["user"] for e in body["entries"]} == {"Alex", "Sam"}


def test_newest_first_so_the_last_attempt_is_the_first_row(audit, monkeypatch):
    _write(audit, ROWS)
    _identity(monkeypatch, "Alex")

    entries = server.app.test_client().get("/api/ig-close-audit", headers={"X-Auth": "token"}).get_json()["entries"]

    assert [e["phase"] for e in entries] == ["confirmed", "submitted"]


def test_a_torn_final_write_does_not_hide_the_rest(audit, monkeypatch):
    """An audit that vanishes when its last line is half-written is not an audit."""
    audit.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n" + '{"at":"2026-08-23T09:06', encoding="utf-8")
    _identity(monkeypatch, "Alex")

    body = server.app.test_client().get("/api/ig-close-audit", headers={"X-Auth": "token"}).get_json()

    assert len(body["entries"]) == 2


def test_a_missing_file_is_an_empty_history_not_an_error(audit, monkeypatch):
    _identity(monkeypatch, "Alex")

    response = server.app.test_client().get("/api/ig-close-audit", headers={"X-Auth": "token"})

    assert response.status_code == 200
    assert response.get_json() == {"entries": [], "total": 0, "scope": "mine"}


def test_the_limit_is_bounded(audit, monkeypatch):
    _write(audit, ROWS * 400)
    _identity(monkeypatch, "Alex")
    c = server.app.test_client()

    assert len(c.get("/api/ig-close-audit?limit=1", headers={"X-Auth": "token"}).get_json()["entries"]) == 1
    assert len(c.get("/api/ig-close-audit?limit=99999", headers={"X-Auth": "token"}).get_json()["entries"]) == 500
    assert len(c.get("/api/ig-close-audit?limit=junk", headers={"X-Auth": "token"}).get_json()["entries"]) == 200


def test_every_phase_the_close_path_writes_has_a_label():
    """An unlabelled phase would render as a raw token where the user needs a plain answer."""
    from pathlib import Path
    html = __import__("client_source").client_source()
    src = Path(__file__).parent / "hvf_web" / "server.py"
    written = set(__import__("re").findall(r'_append_ig_close_audit\([^,]+,[^,]+,\s*"([a-z_]+)"',
                                           src.read_text(encoding="utf-8")))
    written |= {"confirmed", "not_closed"}      # written via a conditional expression

    for phase in written:
        assert f"{phase}:" in html or f'"{phase}"' in html, f"phase {phase!r} has no GUI label"


def test_the_history_is_revealed_after_a_close():
    """The outcome must outlive the dialog that reported it."""
    from pathlib import Path
    html = __import__("client_source").client_source()

    assert "loadIgCloseHistory" in html
    assert 'fetch("/api/ig-close-audit"' in html
    assert "_hw.open=true" in html, "a completed close must open the history rather than hide it"
