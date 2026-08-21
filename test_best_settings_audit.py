import datetime as dt
import math

import run_best_settings_audit as audit
from hvf_web import server
import web_store


def test_replay_holds_an_open_trade_until_the_review_end():
    rows = [
        {"ticker": "A", "trig_date": "2026-01-01", "exit_date": None, "outcome": "OPEN",
         "market": "Equities", "return_pct": 10},
        {"ticker": "B", "trig_date": "2026-01-02", "exit_date": "2026-01-03", "outcome": "TARGET",
         "market": "Equities", "return_pct": 10},
    ]

    result = audit._replay(rows, .5, 1)

    # The first trade retains its slot so the second cannot be funded; its recorded mark is applied only
    # when the reporting window closes, matching the browser's canonical exit rule.
    assert result["funded_trades"] == 1
    assert math.isclose(result["return"], .05)


def test_missing_trigger_feature_blocks_persisted_recommendation(monkeypatch):
    row = {"ticker": "MISSING", "trig_date": dt.date.today().isoformat(), "return_pct": 2.0,
           "outcome": "TARGET", "exit_date": dt.date.today().isoformat(), "market": "Equities",
           "quality": 50, "rr": 3, "rvol": 1.5}
    saved = []
    monkeypatch.setattr(server, "_sqa_all_rows", lambda: [row])
    monkeypatch.setattr(server, "_volscore_trigger_feature_map", lambda years: {})
    monkeypatch.setattr(web_store, "save_json_store", lambda key, report: saved.append((key, report)) or True)
    monkeypatch.setattr(audit, "_full_grid", lambda rows: (_ for _ in ()).throw(AssertionError("must fail closed")))

    report = audit.run()

    assert report["status"] == "blocked_data_quality"
    assert report["missing_trigger_features"] == {"volume_score": 1, "above_vwap": 1, "atr_expanding": 1}
    assert saved[0][0] == "best_settings_full_grid_audit"
