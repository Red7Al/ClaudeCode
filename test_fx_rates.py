"""Currency -> GBP rates (user 2026-09-04: "MCAP is expected to be in GBP in our system").

The rule this file exists to enforce: an unconvertible currency yields None, never a rate of 1.0.
Treating an unknown currency as pounds is the defect fx_rates was written to remove, and it is the one
mistake here that would be invisible -- a wrong number looks exactly like a right one on screen.
"""
import fx_rates

TABLE = {"GBP": 1.0, "USD": 0.73886, "JPY": 0.004722}


# ------------------------------------------------------------------------------------------------------
# The rule that matters most
# ------------------------------------------------------------------------------------------------------

def test_an_unknown_currency_converts_to_none_not_to_itself():
    """THE BUG THIS PREVENTS. Silently treating XYZ as GBP is how JPY 11,573bn passed a 100bn floor."""
    assert fx_rates.to_gbp(5e9, "XYZ", table=TABLE) is None


def test_a_known_currency_is_multiplied_by_its_rate():
    assert fx_rates.to_gbp(1e9, "USD", table=TABLE) == 738_860_000.0
    assert fx_rates.to_gbp(1e9, "GBP", table=TABLE) == 1e9


def test_a_missing_value_stays_missing():
    assert fx_rates.to_gbp(None, "USD", table=TABLE) is None


def test_pence_is_converted_even_without_a_stored_rate():
    """mcap_backfill already relabels GBp as GBP, so this only fires for another producer -- but a pence
    figure read as pounds is 100x wrong, which is worse than reporting nothing."""
    assert fx_rates.to_gbp(1e11, "GBp", table=TABLE) == 1e9


# ------------------------------------------------------------------------------------------------------
# Fetching and storing
# ------------------------------------------------------------------------------------------------------

def test_gbp_needs_no_network_call(monkeypatch):
    """A rate of 1.0 is true by definition; making it depend on a fetch is a needless way to fail."""
    monkeypatch.setattr(fx_rates, "yfinance", None, raising=False)

    assert fx_rates._fetch_to_gbp("GBP") == 1.0


def test_an_implausible_rate_is_rejected(monkeypatch):
    """A yfinance hiccup returning 0, or a quote the wrong way up, would rescale a whole market."""
    class _FI:
        last_price = 0.0

    class _T:
        def __init__(self, sym):
            self.fast_info = _FI()

    monkeypatch.setitem(__import__("sys").modules, "yfinance", type("m", (), {"Ticker": _T}))

    assert fx_rates._fetch_to_gbp("USD") is None


def test_a_currency_that_fails_to_fetch_keeps_its_stored_rate(monkeypatch):
    """Deleting or zeroing it would blank the market cap of every instrument quoted in that currency."""
    written = []

    class _Db:
        def run(self, sql, **k):
            if "insert into fx_rates" in sql:
                written.append(k["c"])
            return []

        def close(self):
            pass

    monkeypatch.setattr(fx_rates, "_fetch_to_gbp", lambda c: None if c == "JPY" else 0.5)

    out = fx_rates.refresh(currencies=["USD", "JPY"], db=_Db())

    assert out == {"USD": 0.5}
    assert written == ["USD"], "the failed currency must not be written at all"


def test_the_refresh_is_actually_invoked_by_the_weekly_backfill():
    """This repository's recurring defect is correct code nothing calls. The rates are only meaningful
    next to the caps they convert, so they ride the job that already refreshes those weekly."""
    import pathlib
    src = pathlib.Path("mcap_backfill.py").read_text(encoding="utf-8", errors="replace")

    assert "fx_rates.refresh()" in src, (
        "nothing would ever refresh the FX rates; market caps would convert on rates frozen at seeding")
    assert src.index("fx_rates.refresh()") < src.index("for i, tk in enumerate(tickers"), (
        "refresh must precede the ticker loop, which has timed out before and would skip it")
