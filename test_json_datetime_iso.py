"""Every date and time leaving the API is ISO-8601, because that is what the client parses.

WHY (owner 2026-09-27: the Closed (UTC) column is truncating its values).

The auto-closed table renders `String(r.closed_at).slice(0,19).replace("T"," ")`. Flask's own JSON
provider serialises a datetime with http_date(), and MEASURED against the installed flask 3.1.3 that
produces:

    "Fri, 25 Sep 2026 06:21:14 GMT"        29 characters, no 'T', month as a word

so .slice(0,19) yields "Fri, 25 Sep 2026 06" -- cut mid-value -- and the .replace("T"," ") does nothing
because there is no 'T'. The column was not mis-styled; the wire format was wrong.

WHY THE FIX IS IN _json_safe AND NOT IN THAT COLUMN. hvf_web/app.js slices dates as ISO in 22 places, and
_StrictJSONProvider.dumps runs _json_safe over EVERY JSON response. One definition makes the whole wire
format match what the client already assumes, instead of leaving the same mismatch latent in every other
endpoint that happens to return a raw datetime.
"""

import datetime as dt
import json
import os

import pytest

os.environ.setdefault("CRONJOB_API_KEY", "test-placeholder")

from hvf_web import server


def _dumps(obj):
    """Serialise through the app's real JSON provider, the way a response does."""
    with server.app.app_context():
        return json.loads(server.app.json.dumps(obj))


AWARE = dt.datetime(2026, 9, 25, 6, 21, 14, 144135, tzinfo=dt.timezone.utc)
NAIVE = dt.datetime(2026, 9, 25, 6, 21, 14)


def test_a_datetime_is_iso_not_an_http_date():
    """THE DEFECT. 'Fri, 25 Sep 2026 06:21:14 GMT' is what Flask sends by default."""
    got = _dumps({"closed_at": AWARE})["closed_at"]
    assert got.startswith("2026-09-25T06:21:14"), got
    assert "GMT" not in got and "Sep" not in got


def test_the_clients_slice_now_produces_a_whole_datetime():
    """The exact expression in app.js for that column, applied to what the server now sends."""
    got = _dumps({"closed_at": AWARE})["closed_at"]
    rendered = got[:19].replace("T", " ")
    assert rendered == "2026-09-25 06:21:14", rendered
    assert len(rendered) == 19


def test_the_old_format_would_have_been_truncated():
    """Proof the bug was real rather than theoretical: the same slice on an HTTP date loses the value."""
    from werkzeug.http import http_date
    assert http_date(AWARE)[:19].replace("T", " ") == "Fri, 25 Sep 2026 06"


def test_a_naive_datetime_is_also_iso():
    got = _dumps({"t": NAIVE})["t"]
    assert got.startswith("2026-09-25T06:21:14"), got


def test_a_plain_date_survives_a_ten_character_slice():
    """opened_on and bar_date are dates, not datetimes, and the client slices them with .slice(0,10)."""
    got = _dumps({"opened_on": dt.date(2026, 9, 25)})["opened_on"]
    assert got == "2026-09-25"
    assert got[:10] == "2026-09-25"


def test_datetimes_nested_in_rows_are_converted():
    """The auto-closed endpoint returns {"rows": [ {...}, ... ]}, so the conversion must recurse."""
    got = _dumps({"rows": [{"closed_at": AWARE}, {"closed_at": None}]})
    assert got["rows"][0]["closed_at"].startswith("2026-09-25T")
    assert got["rows"][1]["closed_at"] is None


def test_the_non_finite_contract_is_unchanged():
    """_json_safe's original job. Adding dates must not weaken it -- allow_nan=False would otherwise make
    the whole response raise rather than send null."""
    got = _dumps({"a": float("nan"), "b": float("inf"), "c": 1.5})
    assert got["a"] is None and got["b"] is None and got["c"] == 1.5


def test_strings_are_left_alone():
    """Most date fields already arrive as strings from the database driver; they must pass through."""
    got = _dumps({"d": "2026-09-25T06:21:14+00:00", "s": "not a date"})
    assert got["d"] == "2026-09-25T06:21:14+00:00"
    assert got["s"] == "not a date"
