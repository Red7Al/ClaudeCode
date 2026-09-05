"""The client scripts must be versioned by the build, or a deploy never reaches the browser.

THE BUG THIS PREVENTS, found live on 2026-09-05. index.html loaded best_settings.js and app.js by bare
filename. The browser kept a stale best_settings.js that defined neither of its functions, so app.js died
on "ReferenceError: makeCombReplay is not defined" and the Scanner sat on "Data loading..." indefinitely.
Ctrl+Shift+R fixed it instantly, which is what a caching fault looks like -- and it explains a series of
reports that something had "stopped working AGAIN" while the host was serving a perfectly good file.
"""
import build_ionos_package as bip

_HTML = b'<html><body>x</body>\n<script src="best_settings.js"></script>\n<script src="app.js"></script>\n'


def test_both_client_scripts_are_stamped_with_the_build():
    out = bip.cache_bust("hvf_web/index.html", _HTML, "abc123def456").decode("utf-8")

    assert 'src="best_settings.js?v=abc123def456"' in out
    assert 'src="app.js?v=abc123def456"' in out
    assert 'src="app.js"' not in out, "an unversioned tag left behind keeps the stale copy alive"


def test_only_the_page_is_rewritten():
    """Rewriting the scripts themselves would corrupt them."""
    for other in ("hvf_web/app.js", "hvf_web/best_settings.js", "cgi-bin/app.py"):
        assert bip.cache_bust(other, _HTML, "abc123") == _HTML


def test_no_stamp_means_no_rewrite():
    """A build that cannot identify itself must ship the page unchanged rather than a broken URL."""
    assert bip.cache_bust("hvf_web/index.html", _HTML, "") == _HTML


def test_the_stamp_matches_the_build_identity_the_api_reports():
    """The version in the URL and the fingerprint /api/build reports come from one source, so "is the
    browser on this build" is answerable the same way as "is the worker on this build"."""
    ident = bip.build_identity()

    assert ident.get("fingerprint"), "the build must be able to identify itself"
    stamp = str(ident["fingerprint"])[:12]
    out = bip.cache_bust("hvf_web/index.html", _HTML, stamp).decode("utf-8")
    assert f'?v={stamp}' in out
