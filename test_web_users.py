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
