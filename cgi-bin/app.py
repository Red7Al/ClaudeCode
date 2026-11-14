"""IONOS Linux Web Hosting CGI/WSGI adapter for the Flask application."""

import os
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
VENV_SITE_PACKAGES = (
    ROOT
    / ".venv_linux"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)

# IONOS invokes the adapter with its system Python. Reuse the application virtual environment while keeping
# all relative application paths rooted in the deployed release.
sys.path.insert(0, str(VENV_SITE_PACKAGES))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# The website, as opposed to a GitHub job: it skips schema DDL, which the jobs own (2026-10-09).
os.environ.setdefault("HVF_WEB_TIER", "1")
from hvf_web.server import app  # noqa: E402


_CGI_PREFIX = "/cgi-bin/app.py"


def application(environ, start_response):
    """Normalise IONOS's CGI path and forward the request to Flask's WSGI application."""
    hosted = environ.copy()
    request_path = hosted.get("REQUEST_URI", hosted.get("PATH_INFO", "/")).split("?", 1)[0]
    if request_path.startswith(_CGI_PREFIX):
        request_path = request_path[len(_CGI_PREFIX):]
    # REQUEST_URI is the raw, still percent-encoded URL; PATH_INFO must be decoded (WSGI). Without this
    # /api/pricebars/%5EGDAXI looked up the ticker "%5EGDAXI" and answered "unknown ticker", so the 18 index
    # tickers (^DJI, ^GDAXI, ...) never showed a price chart (measured live 2026-11-14).
    hosted["SCRIPT_NAME"] = ""
    hosted["PATH_INFO"] = unquote(request_path) or "/"
    return app.wsgi_app(hosted, start_response)
