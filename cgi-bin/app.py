"""IONOS Linux Web Hosting CGI/WSGI adapter for the Flask application."""

import os
import sys
from pathlib import Path


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

from hvf_web.server import app  # noqa: E402


_CGI_PREFIX = "/cgi-bin/app.py"


def application(environ, start_response):
    """Normalise IONOS's CGI path and forward the request to Flask's WSGI application."""
    hosted = environ.copy()
    request_path = hosted.get("REQUEST_URI", hosted.get("PATH_INFO", "/")).split("?", 1)[0]
    if request_path.startswith(_CGI_PREFIX):
        request_path = request_path[len(_CGI_PREFIX):]
    hosted["SCRIPT_NAME"] = ""
    hosted["PATH_INFO"] = request_path or "/"
    return app.wsgi_app(hosted, start_response)
