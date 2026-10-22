"""Guide screenshots (owner 2026-10-22: "Add screen shots to documentation where it can be helpful e.g. Adding IG
credentials"). The images are the real screens with placeholder data (docs/_capture_guide_images.py)."""
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = open(os.path.join(ROOT, "docs", "_guides_content.js"), encoding="utf-8").read()


def _html(slug):
    return open(os.path.join(ROOT, "docs", "guides", slug + ".html"), encoding="utf-8").read()


def test_every_screenshot_a_guide_names_exists():
    named = re.findall(r'\["img",\s*"([^"]+)"', CONTENT)
    assert named, "no guide names a screenshot"
    missing = [n for n in named if not os.path.isfile(os.path.join(ROOT, "docs", "guide_images", n))]
    assert missing == [], missing


def test_the_ig_credentials_guide_shows_the_screens_it_describes():
    html = _html("user-guide-adding-ig-credentials")
    assert html.count('<figure class="guide-fig"><img src="data:image/png;base64,') == 4
    # The panel has four fields and no demo/live choice; the platform connects to IG's live API only.
    for field in ("IG API key", "IG username", "IG password", "IG account ID"):
        assert field in html
    assert "select demo or live" not in html and "LIVE service only" in html


def test_the_built_guides_match_their_source():
    """Each screenshot named in the source is embedded in its built guide -- the build was run."""
    for slug, files in (("user-guide-configuration", ["config-nav.png"]),
                        ("user-guide-tab-visibility", ["tab-visibility.png"]),
                        ("user-guide-getting-around", ["top-navigation.png"])):
        assert _html(slug).count('class="guide-fig"') == len(files), slug


def test_screenshots_carry_nothing_personal():
    """The stub that serves them answers with placeholders only and never imports the database or IG."""
    stub = open(os.path.join(ROOT, "docs", "_guide_stub_server.py"), encoding="utf-8").read()
    assert "Demo User" in stub and "WXYZ" in stub
    assert not re.search(r"^\s*(import|from)\s+(db_pool|ig_shim|web_users|web_store|dotenv)", stub, re.M)
