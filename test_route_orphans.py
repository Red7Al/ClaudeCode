"""No /api route may exist that nothing calls.

THIS REPOSITORY'S RECURRING DEFECT is correct, tested code that nothing ever invokes, and routes are its
favourite hiding place: they answer when you curl them, so they look alive. Three have now been found by
hand rather than by a test -- /api/squeeze-analysis (deleted), and /api/hist3yr and /api/tweet, both of
which lost their detail-panel cards in ONE owner commit on 2026-06-27 (8786202) and then sat unreferenced
for two and a half months until a bug report happened to surface them.

The by-hand sweep that found them is cheap and quiet enough to be a test: 60 routes, 2 unreferenced. A
detector that cries wolf is one nobody trusts, so if this starts flagging routes that ARE used, fix the
detector or add an allow-list entry WITH A REASON -- do not delete a working route to make it pass.
"""

import re
from pathlib import Path

from hvf_web import server

ROOT = Path(__file__).parent

# Routes that are knowingly unreferenced, each with the reason and who has to decide. An allow-list with
# no reasons is how a detector rots into a formality, so a new entry needs one.
_KNOWN_ORPHAN_ROUTES: dict[str, str] = {}

# Everything that could legitimately call a route: the client, the deploy script, the scheduled workflows
# and every other module. server.py is excluded because that is where routes are DEFINED -- including it
# would make every route reference itself and the detector would find nothing, ever.
_SEARCH_GLOBS = ("*.py", "*.sh", "hvf_web/*.js", "hvf_web/*.html", ".github/workflows/*.yml")


def _reference_blob() -> str:
    """Every file that could CALL a route, excluding server.py and excluding the tests.

    EXCLUDING THE TESTS IS NOT TIDINESS, it is the difference between a detector and a formality. The
    first version of this file globbed *.py, which swept in the tests -- including this one -- so a route
    named anywhere in a test counted as a reference and the sweep reported zero orphans forever. Its own
    self-check caught it: a route invented purely as bait was reported as referenced, because the bait
    string sat in this very file. A route exercised only by its own test is still a route nothing uses.
    """
    parts = []
    for pattern in _SEARCH_GLOBS:
        for path in ROOT.glob(pattern):
            if path.name == "server.py" or path.name.startswith("test_") or path.name == "conftest.py":
                continue
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)


def _unreferenced(rules, blob, allow) -> list[str]:
    """Routes whose literal path never appears outside server.py.

    The <converter> segments are stripped so "/api/card/<ticker>" is looked for as "/api/card/", which is
    how the client actually writes it -- `/api/card/${r.ticker}`.
    """
    out = []
    for rule in sorted(set(rules)):
        base = re.sub(r"<[^>]+>", "", rule).rstrip("/")
        if not base or base in blob or rule in allow:
            continue
        out.append(rule)
    return out


def _api_rules() -> list[str]:
    return [r.rule for r in server.app.url_map.iter_rules() if r.rule.startswith("/api/")]


def test_no_api_route_is_unreferenced():
    orphans = _unreferenced(_api_rules(), _reference_blob(), _KNOWN_ORPHAN_ROUTES)
    assert not orphans, (
        "these /api routes are defined but nothing calls them: " + ", ".join(orphans) +
        " — either wire them up, delete them, or add them to _KNOWN_ORPHAN_ROUTES with a reason")


def test_the_detector_can_actually_detect_one():
    """A sweep that cannot fail is worse than no sweep: it occupies the place where a real guard would go.

    /api/squeeze-analysis, /api/hist3yr and /api/tweet were all found by hand while this file did not
    exist, so the detector is asserted against a route that is genuinely absent from the codebase.
    """
    blob = _reference_blob()
    assert _unreferenced(["/api/definitely-not-called-anywhere"], blob, {}) == \
        ["/api/definitely-not-called-anywhere"]


def test_a_referenced_route_is_not_flagged():
    """The other half: it must not flag a route that IS called. /api/pricebars is fetched by app.js."""
    assert _unreferenced(["/api/pricebars/<ticker>"], _reference_blob(), {}) == []


def test_the_allow_list_suppresses_but_demands_a_reason():
    route = "/api/definitely-not-called-anywhere"
    assert _unreferenced([route], _reference_blob(), {route: "a reason"}) == []
    for name, reason in _KNOWN_ORPHAN_ROUTES.items():
        assert reason and len(reason) > 20, f"{name} is allow-listed without a real reason"


def test_the_two_routes_deleted_today_have_not_come_back():
    """Both lost their cards in commit 8786202 on 2026-06-27 and were left behind. Their RENDERERS are
    still live and must not be deleted with them -- render_3yr_history_card is called by publish_one_to_x
    and intraday_signals, both of which run where numpy works."""
    rules = _api_rules()
    assert not [r for r in rules if r.startswith("/api/hist3yr")]
    assert not [r for r in rules if r.startswith("/api/tweet")]

    import intraday_signals
    assert callable(intraday_signals.render_3yr_history_card)
    assert callable(intraday_signals._generate_x_drafts)
