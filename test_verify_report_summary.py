"""verify_report_summary must FAIL on a wrong file -- a check that cannot fail proves nothing.

The database rebuild and the stamped-since query are replaced with fixed values; the comparison and the
STALE/DEFECT classing are what is under test. Section names and shapes are the live file's (10-21 05:10 build).
"""
import copy

import pytest

import publish_report_summary
import verify_report_summary as v

SNAP = {"generated_utc": "2026-10-21T05:05:26.438263+00:00"}
SECTIONS = {
    "rvol": {"BP.L": 1.42, "CRDA.L": 0.88},
    "vwap_atr": {"ANTO.L": {"vwap": 2310.5, "atr": 61.2}, "BP.L": {"vwap": 402.1, "atr": 9.7}},
    "trigdates": {"NBPE.L": "2026-10-08", "NCC.L": "2026-09-30"},
}


@pytest.fixture
def db(monkeypatch):
    moved = set()
    monkeypatch.setattr(publish_report_summary, "build",
                        lambda snap: {"generated_utc": snap["generated_utc"], "sections": copy.deepcopy(SECTIONS)})
    monkeypatch.setattr(v, "_stamped_since", lambda built_at: moved)
    return moved


def _file(sections=None, generated=SNAP["generated_utc"]):
    return {"generated_utc": generated, "built_at": "2026-10-21T05:10:10+00:00",
            "sections": copy.deepcopy(SECTIONS if sections is None else sections)}


def test_identical_file_passes(db, capsys):
    assert v.compare_sections(SNAP, _file()) == 0
    assert "RESULT PASS" in capsys.readouterr().out


def test_a_wrong_value_is_a_defect(db, capsys):
    f = _file()
    f["sections"]["vwap_atr"]["BP.L"]["atr"] = 9.8
    assert v.compare_sections(SNAP, f) == 1
    out = capsys.readouterr().out
    assert "DEFECT BP.L" in out and "RESULT FAIL (1 defects)" in out


def test_a_change_explained_by_newer_prices_is_stale_not_a_defect(db, capsys):
    db.add("BP.L")
    f = _file()
    f["sections"]["rvol"]["BP.L"] = 1.40
    assert v.compare_sections(SNAP, f) == 0
    assert "STALE    1" in capsys.readouterr().out


def test_a_missing_key_is_a_defect(db):
    f = _file()
    del f["sections"]["trigdates"]["NCC.L"]
    assert v.compare_sections(SNAP, f) == 1


def test_a_missing_section_fails(db, capsys):
    f = _file()
    del f["sections"]["rvol"]
    assert v.compare_sections(SNAP, f) == 1
    assert "MISSING from the file" in capsys.readouterr().out


def test_a_summary_for_another_snapshot_fails(db):
    assert v.compare_sections(SNAP, _file(generated="2026-10-20T18:31:00+00:00")) == 1


def test_the_rebuild_goes_through_the_builders_own_serialisation():
    # A date in the database result is a string in the file: equal only after the same json default=str.
    import datetime as dt
    assert v._roundtrip({"X": dt.date(2026, 10, 8)}) == {"X": "2026-10-08"}
