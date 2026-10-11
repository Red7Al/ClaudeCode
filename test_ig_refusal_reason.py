"""IG refusals log IG's own errorCode (owner 2026-10-11): history calls failed with a bare "403 Client Error:
Forbidden", so the reason could not be proved."""
import logging

import pytest
import requests

import ig_shim


class _Resp:
    status_code = 403
    url = "https://api.ig.com/gateway/deal/history/transactions"

    def json(self):
        return {"errorCode": "error.example.not-a-measured-code"}

    def raise_for_status(self):
        raise requests.HTTPError("403 Client Error: Forbidden", response=self)


def test_a_refusal_logs_igs_error_code_and_still_raises(monkeypatch, caplog):
    s = ig_shim.IGSession()
    monkeypatch.setattr(s, "ensure_authenticated", lambda: None)
    monkeypatch.setattr(s, "_headers", lambda v: {})
    monkeypatch.setattr(ig_shim.requests, "get", lambda *a, **k: _Resp())
    with caplog.at_level(logging.WARNING), pytest.raises(requests.HTTPError):
        s.get("/history/transactions", version="2")
    assert "HTTP 403 errorCode=error.example.not-a-measured-code" in caplog.text
