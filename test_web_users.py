"""Offline authentication tests for the Squeeze web user store."""

from hvf_web import web_users as wu


def _users():
    salt = "00" * 16
    return {
        "Enabled": {"salt": salt, "pwd_hash": wu._hash_pwd("enabled-password", salt),
                    "enabled": True},
        "Disabled": {"salt": salt, "pwd_hash": wu._hash_pwd("disabled-password", salt),
                     "enabled": False},
    }


def test_disabled_account_token_is_immediately_revoked(monkeypatch):
    users = _users()
    monkeypatch.setattr(wu, "_ensure_seeded", lambda: users)
    disabled_token = wu.token_for("Disabled")

    assert disabled_token
    assert wu.name_for_token(disabled_token) == ""
    assert disabled_token not in wu.valid_tokens()


def test_enabled_account_token_remains_valid(monkeypatch):
    users = _users()
    monkeypatch.setattr(wu, "_ensure_seeded", lambda: users)
    enabled_token = wu.token_for("Enabled")

    assert wu.name_for_token(enabled_token) == "Enabled"
    assert enabled_token in wu.valid_tokens()


def _store(monkeypatch, initial=None):
    """In-memory replacement for the on-disk user store (User Management "Add user", 2026-08-07)."""
    store = {"users": dict(initial or {})}
    monkeypatch.setattr(wu, "_load", lambda: store["users"])
    monkeypatch.setattr(wu, "_save", lambda u: store.__setitem__("users", u))
    return store


def test_admin_create_user_creates_locked_enabled_account(monkeypatch):
    store = _store(monkeypatch)

    ok = wu.admin_create_user("NewPerson", "new@example.com", subscription="silver", admin=True)

    assert ok is True
    row = store["users"]["NewPerson"]
    assert row["enabled"] is True
    assert row["admin"] is True
    assert row["subscription"] == "silver"
    assert row["email"] == "new@example.com"
    # Locked: a random, unknowable password was hashed — not left blank/predictable.
    assert row["pwd_hash"] and row["pwd_hash"] != wu._hash_pwd("NewPerson", row["salt"])


def test_admin_create_user_rejects_duplicate_name(monkeypatch):
    salt = "00" * 16
    store = _store(monkeypatch, {"Existing": {"salt": salt, "pwd_hash": wu._hash_pwd("x", salt),
                                               "enabled": True}})

    assert wu.admin_create_user("Existing", "dup@example.com") is False
    assert len(store["users"]) == 1


def test_admin_create_user_rejects_invalid_email(monkeypatch):
    store = _store(monkeypatch)

    assert wu.admin_create_user("Someone", "not-an-email") is False
    assert "Someone" not in store["users"]


def test_support_flag_is_independent_of_admin(monkeypatch):
    """Support role (2026-08-07): a narrow read-only tier, independent of the admin flag."""
    salt = "00" * 16
    users = {
        "Ops": {"salt": salt, "pwd_hash": wu._hash_pwd("x", salt), "enabled": True,
                "admin": False, "support": True},
        "Boss": {"salt": salt, "pwd_hash": wu._hash_pwd("x", salt), "enabled": True,
                 "admin": True, "support": False},
        "Guest": {"salt": salt, "pwd_hash": wu._hash_pwd("x", salt), "enabled": True},
    }
    monkeypatch.setattr(wu, "_ensure_seeded", lambda: users)

    assert wu.is_support("Ops") is True and wu.is_admin("Ops") is False
    assert wu.is_admin("Boss") is True and wu.is_support("Boss") is False
    assert wu.is_support("Guest") is False   # missing "support" key defaults False, no migration needed


def test_set_support_updates_only_the_target(monkeypatch):
    store = _store(monkeypatch, {"Ops": {"salt": "00" * 16, "pwd_hash": "x", "enabled": True}})

    assert wu.set_support("Ops", True) is True
    assert store["users"]["Ops"]["support"] is True
    assert wu.set_support("Nobody", True) is False   # unknown account
