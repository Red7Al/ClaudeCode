"""One place to read the browser client's source.

The page's JavaScript was extracted from ``hvf_web/index.html`` into ``hvf_web/app.js`` on 2026-08-23,
because a 616 KB single file meant every behaviour change was regex surgery on a blob — which is exactly
how a template expression got cut in half and shipped rendering literally on screen that morning.

Tests that search the client for a function, a string or a template must not care where it now lives.
``client_source()`` returns the markup and the script together, so assertions written against the old
single file keep working and keep meaning the same thing. ``client_js()`` returns just the script, for
the checks that parse or execute it.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "hvf_web" / "index.html"
APP_JS = ROOT / "hvf_web" / "app.js"
# The Best Settings search moved out of app.js on 2026-09-03 so the server could run it under Node.
# It is still page JavaScript and every source-text and behavioural assertion must keep seeing it.
BEST_SETTINGS_JS = ROOT / "hvf_web" / "best_settings.js"


def client_html() -> str:
    """The page markup alone."""
    return INDEX.read_text(encoding="utf-8")


def client_js() -> str:
    """The page's JavaScript alone — what Node parses and what the behavioural harness executes."""
    return BEST_SETTINGS_JS.read_text(encoding="utf-8") + chr(10) + APP_JS.read_text(encoding="utf-8")


def client_source() -> str:
    """Markup + script, as the single document these assertions were originally written against."""
    return client_html() + "\n" + client_js()
