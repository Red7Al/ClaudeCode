import pytest

import run_data_quality_audit as audit


def test_current_metric_audit_rejects_an_empty_snapshot(monkeypatch):
    from hvf_web import server
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"records": []})

    with pytest.raises(RuntimeError, match="requires the daily scanner snapshot"):
        audit._audit_current_instrument_metrics()


def test_current_metric_audit_persists_real_coverage(monkeypatch):
    from hvf_web import server
    import price_store
    import web_store
    snapshot = {"records": [{"ticker": "OK", "name": "Okay", "market": "Test"}]}
    saved = []
    monkeypatch.setattr(server, "_load_snapshot", lambda: snapshot)
    monkeypatch.setattr(server, "_live_instrument_metrics",
                        lambda snap: {"OK": {"status": "complete", "above_vwap": True, "atr_expanding": False}})
    monkeypatch.setattr(price_store, "get_bars_or_fetch", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_store, "save_json_store", lambda key, report: saved.append((key, report)) or True)

    report = audit._audit_current_instrument_metrics()

    assert report["rows"] == 1
    assert report["metric_statuses"] == {"complete": 1}
    assert report["missing_vwap"] == 0 and report["missing_atr"] == 0
    assert saved[0][0] == "current_instrument_metric_audit"
