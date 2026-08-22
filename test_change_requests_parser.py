"""Change Requests register parsing — both supported file formats.

The tab is the surface the user watches to see what is in progress, so a register the parser cannot
read is indistinguishable from no work at all. On 2026-08-22 that is exactly what had happened: the
numbered "N. Time: / Request: / Result: / Status:" format adopted by the *-COMPLETE.txt archives on
2026-08-13, and by the ACTIVE 20260820/20260821 registers from 2026-08-20, produced ZERO requirements
because _cr_parse only ever emitted a row for a line starting with "*". 33 of the 34 items across those
four files -- including every in-progress and deferred one -- were invisible.

test_numbered_block_format_is_not_silently_dropped reconstructs that bug directly.
"""

from pathlib import Path

import pytest

from hvf_web import server


def _parse(tmp_path, text, name="20260821.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return server._cr_parse(str(path))


NUMBERED = """Date: 2026-08-21
Timezone: Europe/London

## Website and portal

1. Time: 09:00:00
   Request: Reconcile every Back Test summary figure with its evidence rows.
   Result: Completed and regression-tested.
   Status: complete

## Testing and quality

2. Time: 10:15:00
   Request: Make Let winners run simulation-only until per-user routing is verified.
   Result: In progress. The live path is fail-closed.
   Status: in progress

3. Time: 13:02:00
   Request: Failing cron jobs also.
   Evidence: Supabase Storage returns HTTP 402 while the quota is exhausted.
   Result: Partial builds now seed from IONOS.
   Status: deferred external dependency

4. Time: 14:30:00
   Request: Remove the unused Closed date/time column.
   Result: Withdrawn by the requester before release.
   Status: cancelled

5. Time: 2026-08-20
   Request: Guardian One - cover page.
   Result: Belongs to its separate repository/register.
   Status: out of scope
"""


# --------------------------------------------------------------------------------------------------
# The bug this file exists for
# --------------------------------------------------------------------------------------------------

def test_numbered_block_format_is_not_silently_dropped(tmp_path):
    """THE REGRESSION. Before the fix this returned total=0 and the tab showed an empty register."""
    parsed = _parse(tmp_path, NUMBERED)

    assert parsed["total"] == 5, "numbered blocks must become requirements, not vanish"
    assert [r["text"] for r in parsed["requirements"]][:2] == [
        "Reconcile every Back Test summary figure with its evidence rows.",
        "Make Let winners run simulation-only until per-user routing is verified.",
    ]


def test_every_block_status_word_maps_to_a_tab_status(tmp_path):
    """A status the tab cannot classify defaults to Not Started, which reads as 'never picked up'."""
    parsed = _parse(tmp_path, NUMBERED)

    assert [r["status"] for r in parsed["requirements"]] == [
        "Completed", "In Progress", "Deferred", "Cancelled", "Cancelled"]
    assert parsed["counts"]["In Progress"] == 1
    assert parsed["counts"]["Not Started"] == 0


def test_deferred_external_dependency_is_not_shadowed_by_deferred():
    """Longest-match ordering: the compound word must not fall through to a different status."""
    assert server._cr_word_status("deferred external dependency") == "Deferred"
    assert server._cr_word_status("complete") == "Completed"
    assert server._cr_word_status("completed") == "Completed"
    assert server._cr_word_status("in-progress") == "In Progress"
    assert server._cr_word_status("out of scope") == "Cancelled"
    assert server._cr_word_status("something nobody has used yet") == "Not Started"


def test_markdown_heading_becomes_the_working_area(tmp_path):
    parsed = _parse(tmp_path, NUMBERED)

    assert [r["working_area"] for r in parsed["requirements"]] == [
        "Website and portal", "Testing and quality", "Testing and quality",
        "Testing and quality", "Testing and quality"]


def test_evidence_and_result_both_land_in_the_delivery_note(tmp_path):
    parsed = _parse(tmp_path, NUMBERED)
    cron = parsed["requirements"][2]

    assert "HTTP 402" in cron["delivery_notes"]
    assert "seed from IONOS" in cron["delivery_notes"]


def test_final_block_in_a_file_is_flushed(tmp_path):
    """No successor line follows the last block, so it is only emitted by the end-of-file flush."""
    parsed = _parse(tmp_path, "## Only\n\n1. Time: 09:00:00\n   Request: The last one.\n   Status: complete\n")

    assert [(r["text"], r["status"]) for r in parsed["requirements"]] == [("The last one.", "Completed")]


def test_wrapped_request_and_result_lines_are_joined(tmp_path):
    parsed = _parse(tmp_path, "## A\n\n1. Time: 09:00:00\n   Request: A request that runs\n"
                              "      onto a second line.\n   Result: A result that also\n"
                              "      wraps.\n   Status: complete\n")
    req = parsed["requirements"][0]

    assert req["text"] == "A request that runs onto a second line."
    assert req["delivery_notes"] == "A result that also wraps."


def test_original_request_date_is_not_treated_as_note_or_requirement(tmp_path):
    """The *-COMPLETE.txt archives carry this field; it must not leak into the requirement text."""
    parsed = _parse(tmp_path, "## A\n\n1. Time: 14:47:00\n   Original request date: 2026-08-12\n"
                              "   Request: Make the refresh button work.\n   Status: complete\n"
                              "   Completion: Deployed and verified.\n")
    req = parsed["requirements"][0]

    assert req["text"] == "Make the refresh button work."
    assert req["delivery_notes"] == "Deployed and verified."
    assert req["status"] == "Completed"


# --------------------------------------------------------------------------------------------------
# The pre-existing "*" format must be untouched
# --------------------------------------------------------------------------------------------------

def test_star_format_still_parses_with_its_end_anchored_marker(tmp_path):
    parsed = _parse(tmp_path, "Application Focus - Scanner - NEW DELIVERY\n\n"
                              "* P-01 BUG Fix the thing -- Claude 2026-08-22: done. [Completed]\n"
                              "* P-10 Add the other thing\n", name="20260817-ToDo-Claude.txt")

    assert [r["status"] for r in parsed["requirements"]] == ["Completed", "Not Started"]
    assert parsed["requirements"][0]["working_area"] == "Scanner"
    assert parsed["requirements"][0]["scope"] == "Bug"   # _cr_scope title-cases the category word
    assert parsed["requirements"][0]["delivery_notes"] == "Claude 2026-08-22: done."
    assert parsed["prioritised"] == 2


def test_markdown_heading_does_not_rescope_star_lines(tmp_path):
    """20260818-ToDo-Claude.txt carries BOTH '## Uncategorized' and 'Application Focus' headers.
    '## Heading' scopes numbered blocks only, so those 14 rows keep the working area they had."""
    parsed = _parse(tmp_path, "Application Focus - Scanner\n\n## Uncategorized\n\n"
                              "* P-05 Snapshot date is not updated after a refresh\n",
                    name="20260818-ToDo-Claude.txt")

    assert parsed["requirements"][0]["working_area"] == "Scanner"


def test_a_star_line_closes_an_open_block(tmp_path):
    parsed = _parse(tmp_path, "## A\n\n1. Time: 09:00:00\n   Request: Block item.\n   Status: complete\n"
                              "\n* P-01 Star item [In Progress]\n")

    assert [(r["text"], r["status"]) for r in parsed["requirements"]] == [
        ("Block item.", "Completed"), ("P-01 Star item", "In Progress")]


# --------------------------------------------------------------------------------------------------
# The real registers
# --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("register", ["20260820.txt", "20260821.txt",
                                      "20260820-COMPLETE.txt", "20260813-COMPLETE.txt"])
def test_the_shipped_numbered_registers_are_visible_in_the_tab(register):
    """These four files held 0, 1, 0 and 0 parsed requirements before the fix."""
    path = Path(__file__).parent / "ChangeRequests" / register
    if not path.is_file():
        pytest.skip(f"{register} has been archived")

    parsed = server._cr_parse(str(path))

    assert parsed["total"] > 1, f"{register} still reads as an empty register"
    assert parsed["counts"]["Not Started"] < parsed["total"], (
        f"{register} parsed no statuses — every row defaulted to Not Started")
