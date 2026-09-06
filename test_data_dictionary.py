# ======================================================================================================
# The data-dictionary skill (P-25, requested 2026-08-29, built 2026-09-06).
#
# "Create a data dictionary for the database tables, as a skill."
#
# It is GENERATED from the live schema rather than written, because a hand-written dictionary is accurate
# on the day it is written and wrong from the first migration afterwards -- with nothing telling the
# reader which they are looking at. These tests cover the two ways that promise could quietly fail: a
# table appearing in the database and not in the document, and a table appearing in the document with a
# column list and no explanation, which reads as documented and is not.
# ======================================================================================================

import pathlib

import pytest

import build_data_dictionary as dd

SKILL = pathlib.Path("skills_src/ah-data-dictionary/SKILL.md")


def test_the_skill_exists_and_is_a_skill():
    assert SKILL.is_file(), "run: python build_data_dictionary.py"
    head = SKILL.read_text(encoding="utf-8")[:400]
    assert head.startswith("---"), "a skill needs its frontmatter"
    assert "name: ah-data-dictionary" in head
    assert "description:" in head, "the description is how the skill gets selected; it cannot be omitted"


def test_the_traps_that_cost_money_are_stated_up_front():
    """A dictionary is read when someone is already confused. The three that have actually caused defects
    in this repository belong above the table list, not buried in it."""
    body = SKILL.read_text(encoding="utf-8")
    top = body[:body.index("## Tables")]
    assert "no `direction` column" in top, "squeeze_history direction is hvf_type; this has bitten"
    assert "`instrument_mcap.mcap`" in top, "the column is mcap, not market_cap"
    assert "as_of` is not the bar it describes" in top.replace("**", ""), \
        "the metrics as_of/bar_date gap is the one with real money attached"


def test_every_documented_table_says_what_writes_it():
    """'What writes this, and how would I know if it stopped' is the question this repository's recurring
    defect is made of. A table entry without it is half an entry."""
    for name, (holds, writer, _trap) in dd.NOTES.items():
        assert holds.strip(), f"{name} has no description"
        assert writer.strip(), f"{name} does not say which job writes it"


def test_an_undocumented_table_is_reported_as_a_gap(monkeypatch):
    """The failure mode this guards: a new table gets a column list from the generator and therefore looks
    documented, when nobody has said what it is for."""
    monkeypatch.setitem(dd.NOTES, "__probe__", None)
    monkeypatch.delitem(dd.NOTES, "__probe__")
    schema = {"tables": [("mystery_table", "8 kB", 3, 8192)],
              "columns": {"mystery_table": [("id", "integer", False)]},
              "total": "8 kB"}
    body = dd.render(schema)
    assert "UNDOCUMENTED" in body, "an unexplained table must be flagged, not padded with a column list"
    assert "## Undocumented tables" in body and "- `mystery_table`" in body


def test_a_documented_table_renders_its_notes_and_columns():
    schema = {"tables": [("fx_rates", "32 kB", 9, 32768)],
              "columns": {"fx_rates": [("currency", "text", False), ("rate", "double precision", True)]},
              "total": "32 kB"}
    body = dd.render(schema)
    assert "UNDOCUMENTED" not in body
    assert "never 1.0" in body, "the fx trap must survive into the rendered page"
    assert "| `currency` | text | no |" in body
    assert "| `rate` | double precision | yes |" in body


def test_the_generator_reports_the_total_against_the_free_tier():
    """397 MB of 500 MB measured 2026-09-06. Whoever reads this needs to know how much room is left."""
    schema = {"tables": [], "columns": {}, "total": "397 MB"}
    body = dd.render(schema)
    assert "397 MB" in body and "500 MB free tier" in body


@pytest.mark.live_state
def test_the_committed_skill_matches_the_live_schema():
    """--check is the guard against the document drifting after a migration."""
    assert dd.main.__doc__ is None or True
    import sys
    argv = sys.argv[:]
    sys.argv = ["build_data_dictionary.py", "--check"]
    try:
        assert dd.main() == 0, "the data dictionary is stale; run python build_data_dictionary.py"
    finally:
        sys.argv = argv
