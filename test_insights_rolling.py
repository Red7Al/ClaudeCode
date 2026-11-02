# ======================================================================================================
# The two ROLLING insights (owner 2026-11-02: "the insights tab is predictable now - same reports each day -
# please add some better insights"): _insight_market_shift and _insight_recent_results.
#
# No database. Every test passes rows= and today= explicitly, with rows shaped exactly like
# _squeeze_history_raw's: 0 ticker, 1 market, 2 timeframe, 3 hvf_type, 4 quality, 5 risk_reward, 6 rvol,
# 7 outcome, 8 return_pct, 9 triggered_date, 10 outcome_date, 11-13 entry/stop/target. Outcome shapes are
# the ones measured live: STOPPED/TARGET (return and outcome_date set), NEVER_TRIGGERED (both None),
# OPEN (return set, outcome_date None).
# ======================================================================================================

import datetime as dt
import re
from statistics import NormalDist

import pytest

from hvf_web import server

TODAY = dt.date(2026, 11, 2)


def row(ticker, market, td, outcome="STOPPED", ret=-1.0, od=None):
    """One squeeze_history row in _squeeze_history_raw's layout."""
    if outcome == "NEVER_TRIGGERED":
        ret, od = None, None
    elif outcome == "OPEN":
        od = None
    return (ticker, market, "1D", "bull", "A", 3.5, 1.2, outcome, ret, td, od, 10.0, 9.0, 13.0)


def ago(days, today=TODAY):
    return today - dt.timedelta(days=days)


# ------------------------------------------------------------------------------------------------------
# market_shift
# ------------------------------------------------------------------------------------------------------

def market_rows(recent_counts, prior_counts, today=TODAY):
    """prior_counts / recent_counts: {market: n}. Each market uses tickers <market>0..<market>9; every
    ticker triggers in the prior window (100 days ago), so every recent row is from a KNOWN instrument."""
    rows = []
    for m, n in prior_counts.items():
        rows += [row(f"{m}{i % 10}", m, ago(100, today)) for i in range(n)]
    for m, n in recent_counts.items():
        rows += [row(f"{m}{i % 10}", m, ago(5, today)) for i in range(n)]
    return rows


def _nums(detail):
    """(recent count of mkt, n_recent, prior count of mkt, n_prior) from the detail sentence."""
    m = re.search(r"([\d,]+) of ([\d,]+) triggers in the last 30 days.*?months before, ([\d,]+) of ([\d,]+)\.",
                  detail)
    assert m, detail
    return tuple(int(x.replace(",", "")) for x in m.groups())


def test_market_shift_counts_only_instruments_known_before_the_recent_window():
    """116 Nikkei names added on 2026-10-18 read as a 6% -> 14% "shift". New instruments must not count."""
    base = market_rows({"A": 15, "B": 15}, {"A": 100, "B": 100})
    new = [row(f"NEW{i}", "C", ago(3)) for i in range(100)]        # never triggered before the window
    got = server._insight_market_shift(rows=base + new, today=TODAY)
    assert got and got["n"] == 30, "rows from instruments with no earlier trigger were counted"
    assert "C" not in {c["label"] for c in got["chart"]}
    assert not got["significant"]
    # Same new rows from a KNOWN ticker of market B DO count -- proving it is the instrument filter at work.
    known_b = [row("B0", "B", ago(3)) for _ in range(100)]
    got2 = server._insight_market_shift(rows=base + known_b, today=TODAY)
    assert got2["n"] == 130


def test_a_ticker_known_only_through_a_never_triggered_row_is_not_known():
    base = market_rows({"A": 15, "B": 15}, {"A": 100, "B": 100})
    ghost = [row("GHOST", "C", ago(100), outcome="NEVER_TRIGGERED")] + [row("GHOST", "C", ago(3))] * 40
    got = server._insight_market_shift(rows=base + ghost, today=TODAY)
    assert got["n"] == 30


def test_never_triggered_rows_are_excluded_from_both_windows():
    base = market_rows({"A": 15, "B": 15}, {"A": 100, "B": 100})
    nt = ([row("A0", "A", ago(5), outcome="NEVER_TRIGGERED")] * 200
          + [row("B0", "B", ago(100), outcome="NEVER_TRIGGERED")] * 500)
    a = server._insight_market_shift(rows=base, today=TODAY)
    b = server._insight_market_shift(rows=base + nt, today=TODAY)
    assert _nums(a["detail"]) == _nums(b["detail"]) and a["n"] == b["n"] == 30
    assert a["significant"] is b["significant"] is False


def _bonf(k):
    return NormalDist().inv_cdf(1 - 0.025 / k)


@pytest.mark.parametrize("x, expect", [(35, False), (37, True)])
def test_market_shift_uses_a_bonferroni_bar_over_the_markets_scored(x, expect):
    """4 scored markets -> bar 2.50. x=35 of 100 gives z ~2.34: clears 1.96 but not the Bonferroni bar.
    Market D (2 prior rows, p0*n_recent ~0.5 < 5) is too small to score and must not raise k."""
    rest = 100 - x
    recent = {"A": x, "B": rest // 3, "C": rest // 3, "E": rest - 2 * (rest // 3)}
    prior = {"A": 100, "B": 100, "C": 100, "E": 100, "D": 2}
    got = server._insight_market_shift(rows=market_rows(recent, prior), today=TODAY)
    assert got["headline"].startswith("A:")
    assert f"needs {_bonf(4):.1f} with 4 markets compared" in got["stat"], got["stat"]
    z = float(re.search(r"z = ([\d.]+)", got["stat"]).group(1))
    assert 1.96 < z, "fixture no longer exercises the gap between 1.96 and the Bonferroni bar"
    assert got["significant"] is expect
    assert isinstance(got["significant"], bool)


def test_market_shift_k_counts_every_scored_market():
    prior = {f"M{i}": 100 for i in range(10)}
    recent = {f"M{i}": 10 for i in range(10)}
    got = server._insight_market_shift(rows=market_rows(recent, prior), today=TODAY)
    assert f"needs {_bonf(10):.1f} with 10 markets compared" in got["stat"]
    assert not got["significant"]


def test_market_shift_picks_the_largest_absolute_z_including_a_fall():
    # B falls from 50% to 20%: the biggest |z|, and it is DOWN.
    got = server._insight_market_shift(rows=market_rows({"A": 40, "B": 20, "C": 40},
                                                        {"A": 100, "B": 200, "C": 100}), today=TODAY)
    assert got["headline"].startswith("B:") and " down from " in got["headline"]
    assert got["significant"] is True


def test_market_shift_is_empty_below_its_minimum_samples():
    assert server._insight_market_shift(rows=market_rows({"A": 10, "B": 9}, {"A": 100, "B": 100}),
                                        today=TODAY) == {}, "19 recent triggers must not publish"
    assert server._insight_market_shift(rows=market_rows({"A": 10, "B": 10}, {"A": 50, "B": 49}),
                                        today=TODAY) == {}, "99 prior triggers must not publish"
    assert server._insight_market_shift(rows=market_rows({"A": 10, "B": 10}, {"A": 50, "B": 50}),
                                        today=TODAY) != {}
    assert server._insight_market_shift(rows=[], today=TODAY) == {}
    # A single market has p0 == 1: nothing to compare, nothing published.
    assert server._insight_market_shift(rows=market_rows({"A": 30}, {"A": 200}), today=TODAY) == {}


def test_market_shift_window_boundaries():
    """recent = (today-30, today]; prior = (today-395, today-30]."""
    base = market_rows({"A": 10, "B": 10}, {"A": 50, "B": 49})            # 99 prior -> {} on its own
    assert server._insight_market_shift(rows=base, today=TODAY) == {}
    at_cut = server._insight_market_shift(rows=base + [row("A0", "A", ago(30))], today=TODAY)
    assert at_cut and at_cut["n"] == 20, "day 30 belongs to the earlier window"
    assert _nums(at_cut["detail"])[3] == 100
    assert server._insight_market_shift(rows=base + [row("A0", "A", ago(395))], today=TODAY) == {}, \
        "day 395 is outside the earlier window"
    assert server._insight_market_shift(rows=base + [row("A0", "A", ago(394))], today=TODAY) != {}
    # Day 29 is recent: 21 recent, still 99 prior -> {}.
    assert server._insight_market_shift(rows=base + [row("A0", "A", ago(29))], today=TODAY) == {}
    # A ticker whose only earlier trigger is on day 395 is not known.
    base2 = market_rows({"A": 10, "B": 10}, {"A": 50, "B": 50})
    old = [row("OLD", "C", ago(395))] + [row("OLD", "C", ago(3))] * 30
    assert server._insight_market_shift(rows=base2 + old, today=TODAY)["n"] == 20


def test_market_shift_card_shape_matches_what_the_client_renders():
    got = server._insight_market_shift(rows=market_rows({"A": 40, "B": 20, "C": 40},
                                                        {"A": 100, "B": 200, "C": 100}), today=TODAY)
    for k in ("id", "title", "headline", "stat", "detail", "caveat", "chart_title"):
        assert isinstance(got[k], str) and got[k], k
    assert got["id"] == "market_shift" and got["n"] == 100
    assert got.get("chart_kind", "") != "time" and "table" not in got
    assert got["chart"] and all(set(c) >= {"label", "value"} and isinstance(c["value"], float)
                                for c in got["chart"])
    assert [c["label"] for c in got["chart"]][:1] in (["A"], ["C"])
    assert abs(sum(c["value"] for c in got["chart"]) - 100) < 0.2


# ------------------------------------------------------------------------------------------------------
# recent_results
# ------------------------------------------------------------------------------------------------------

def trade(td_ago, ret, close_after=10, outcome=None, ticker="T", market="M"):
    td = ago(td_ago)
    outcome = outcome or ("TARGET" if ret > 0 else "STOPPED")
    return row(ticker, market, td, outcome=outcome, ret=ret, od=td + dt.timedelta(days=close_after))


def results_rows(rw=6, rl=4, pw=20, pl=30, rwin=1.0, rloss=-1.0, pwin=1.0, ploss=-1.0):
    """rw/rl recent wins/losses (triggered 90 days ago), pw/pl prior wins/losses (triggered 300 days ago).
    Win sizes alternate +/-10% so returns have a variance."""
    def jitter(v, i):
        return v * (1.1 if i % 2 else 0.9)
    return ([trade(90, jitter(rwin, i)) for i in range(rw)] + [trade(90, jitter(rloss, i)) for i in range(rl)]
            + [trade(300, jitter(pwin, i)) for i in range(pw)] + [trade(300, jitter(ploss, i)) for i in range(pl)])


def _rnums(got):
    m = re.search(r"(\d+) trades triggered 60-120 days ago: (\d+) closed positive and (\d+) negative within "
                  r"60 days, (\d+) still open at day 60\. The 12 months before: ([\d,]+) trades, ([\d,]+) won "
                  r"and ([\d,]+) lost", got["detail"])
    assert m, got["detail"]
    return tuple(int(x.replace(",", "")) for x in m.groups())


def test_results_horizon_day_60_counts_day_61_is_open():
    base = results_rows()
    assert _rnums(server._insight_recent_results(rows=base, today=TODAY))[:4] == (10, 6, 4, 0)
    d60 = server._insight_recent_results(rows=base + [trade(90, 5.0, close_after=60)], today=TODAY)
    assert _rnums(d60)[:4] == (11, 7, 4, 0), "a trade closed on day 60 is within the horizon"
    d61 = server._insight_recent_results(rows=base + [trade(90, 5.0, close_after=61)], today=TODAY)
    assert _rnums(d61)[:4] == (11, 6, 4, 1), "a trade closed on day 61 is still open at day 60"
    # And in the earlier window the same rule applies, so both periods are measured alike.
    p61 = server._insight_recent_results(rows=base + [trade(300, 5.0, close_after=61)], today=TODAY)
    assert _rnums(p61)[4:] == (51, 20, 30)
    assert server._RESULTS_HORIZON == 60


def test_results_open_rows_without_an_outcome_date_count_as_open():
    base = results_rows()
    td = ago(90)
    extra = [row("O", "M", td, outcome="OPEN", ret=3.0), row("O", "M", td, outcome="OPEN", ret=None)]
    assert _rnums(server._insight_recent_results(rows=base + extra, today=TODAY))[:4] == (12, 6, 4, 2)


def test_results_break_even_is_neither_a_win_nor_a_loss():
    base = results_rows()
    got = server._insight_recent_results(rows=base + [trade(90, 0.0, outcome="STOPPED")] * 3, today=TODAY)
    assert _rnums(got)[:4] == (13, 6, 4, 0)
    assert "won 60% within" in got["headline"], got["headline"]


def test_results_never_triggered_is_excluded():
    base = results_rows()
    nt = [row("N", "M", ago(90), outcome="NEVER_TRIGGERED")] * 50 + [row("N", "M", ago(300),
                                                                         outcome="NEVER_TRIGGERED")] * 50
    a = server._insight_recent_results(rows=base, today=TODAY)
    b = server._insight_recent_results(rows=base + nt, today=TODAY)
    assert _rnums(a) == _rnums(b) and a["n"] == b["n"] == 10


def test_results_trades_belong_to_the_window_they_triggered_in():
    """Grouping by CLOSE date was the censoring artefact (16% vs 28%). A trade triggered 150 days ago that
    closed 100 days ago belongs to the earlier group, even though it closed inside the recent window."""
    base = results_rows()
    late = [trade(150, -2.0, close_after=50)] * 5
    got = server._insight_recent_results(rows=base + late, today=TODAY)
    assert _rnums(got)[:4] == (10, 6, 4, 0)
    assert _rnums(got)[4:] == (55, 20, 35)


def test_results_window_boundaries():
    """recent = (today-120, today-60]; prior = (today-485, today-120]; newer than day 60 is not judged."""
    base = results_rows()
    def nums(extra):
        return _rnums(server._insight_recent_results(rows=base + [extra], today=TODAY))
    assert nums(trade(60, 1.0))[:2] == (11, 7), "day 60 is the newest recent trigger"
    assert nums(trade(59, 1.0)) == _rnums(server._insight_recent_results(rows=base, today=TODAY)), \
        "day 59's 60 days are not complete; it must not be counted"
    assert nums(trade(119, 1.0))[:2] == (11, 7)
    assert nums(trade(120, 1.0))[4:6] == (51, 21), "day 120 belongs to the earlier group"
    assert nums(trade(484, 1.0))[4:6] == (51, 21)
    assert nums(trade(485, 1.0))[4:] == (50, 20, 30), "day 485 is outside the earlier group"


def test_results_significant_only_when_win_rate_and_return_agree():
    # Win rate UP (70% vs 35%) but return DOWN (small wins, big losses): both |z| and |t| clear 2, but
    # they point opposite ways -> not significant, and the headline says so.
    mixed = results_rows(rw=70, rl=30, pw=70, pl=130, rwin=1, rloss=-10, pwin=10, ploss=-1)
    got = server._insight_recent_results(rows=mixed, today=TODAY)
    z, t = map(float, re.search(r"z = ([\d.]+), return t = ([\d.]+)", got["stat"]).groups())
    assert z >= 2 and t >= 2, got["stat"]
    assert got["significant"] is False
    assert "mixed: the two measures disagree" in got["headline"]

    agree = results_rows(rw=70, rl=30, pw=70, pl=130, rwin=10, rloss=-1, pwin=10, ploss=-1)
    got = server._insight_recent_results(rows=agree, today=TODAY)
    assert got["significant"] is True and got["headline"].endswith("-- better")

    worse = results_rows(rw=20, rl=80, pw=140, pl=60, rwin=1, rloss=-1, pwin=1, ploss=-1)
    got = server._insight_recent_results(rows=worse, today=TODAY)
    assert got["significant"] is True and got["headline"].endswith("-- worse")


def test_results_agreeing_but_weak_return_test_is_not_significant():
    """Win rate clears z >= 2 and agrees in direction, but one huge winner carries the mean: t < 2."""
    rows = ([trade(90, 500.0)] + [trade(90, 0.01)] * 59 + [trade(90, -0.01)] * 40
            + [trade(300, 0.01)] * 80 + [trade(300, -0.01)] * 120)
    got = server._insight_recent_results(rows=rows, today=TODAY)
    z, t = map(float, re.search(r"z = ([\d.]+), return t = ([\d.]+)", got["stat"]).groups())
    assert z >= 2 and t < 2, got["stat"]
    assert got["significant"] is False


def test_results_agreeing_but_weak_win_rate_test_is_not_significant():
    """Return clears t >= 2 and agrees, win rate barely moves: z < 2."""
    rows = ([trade(90, 5.0)] * 30 + [trade(90, -1.0)] * 70
            + [trade(300, 1.0)] * 56 + [trade(300, -1.0)] * 144)
    got = server._insight_recent_results(rows=rows, today=TODAY)
    z, t = map(float, re.search(r"z = ([\d.]+), return t = ([\d.]+)", got["stat"]).groups())
    assert z < 2 and t >= 2, got["stat"]
    assert got["significant"] is False


def test_results_empty_below_minimum_samples():
    assert server._insight_recent_results(rows=results_rows(rw=5, rl=4), today=TODAY) == {}
    assert server._insight_recent_results(rows=results_rows(pw=20, pl=29), today=TODAY) == {}
    assert server._insight_recent_results(rows=results_rows(rw=10, rl=0, pw=50, pl=0), today=TODAY) != {} \
        or True   # all-wins: p == 1, must not divide by zero
    assert server._insight_recent_results(rows=[], today=TODAY) == {}


def test_results_all_wins_does_not_divide_by_zero():
    got = server._insight_recent_results(rows=results_rows(rw=10, rl=0, pw=50, pl=0), today=TODAY)
    assert got["significant"] is False and "z = 0.0" in got["stat"]


def test_results_card_shape_and_time_chart():
    got = server._insight_recent_results(rows=results_rows(), today=TODAY)
    for k in ("id", "title", "headline", "stat", "detail", "caveat", "chart_title"):
        assert isinstance(got[k], str) and got[k], k
    assert got["id"] == "recent_results" and got["n"] == 10
    assert isinstance(got["significant"], bool)
    assert got["chart_kind"] == "time" and got["chart_unit"] == "%"
    assert got["baseline"] == 40.0, "the baseline is the earlier period's win rate"
    labels = [c["label"] for c in got["chart"]]
    assert labels == sorted(labels) and len(labels) >= 2, "the client draws a time chart only for >= 2 points"
    assert all(re.fullmatch(r"\d{4}-\d{2}", lab) for lab in labels)
    assert labels == [ago(300).strftime("%Y-%m"), ago(90).strftime("%Y-%m")]
    assert [c["value"] for c in got["chart"]] == [40.0, 60.0]


def test_results_chart_keeps_the_last_13_months_and_skips_months_with_nothing_closed():
    rows = results_rows()
    rows += [trade(130 + 30 * i, 1.0) for i in range(12)]          # one trade in many more months
    rows += [row("O", "M", ago(100), outcome="OPEN", ret=1.0)]
    got = server._insight_recent_results(rows=rows, today=TODAY)
    labels = [c["label"] for c in got["chart"]]
    assert len(labels) <= 13 and labels == sorted(labels)


# ------------------------------------------------------------------------------------------------------
# The endpoint: four cards, every one with the keys test_insights requires and the client renders.
# ------------------------------------------------------------------------------------------------------

def test_endpoint_returns_four_insights_including_both_rolling_ones(monkeypatch):
    today = dt.date.today()
    rows = market_rows({"A": 40, "B": 20, "C": 40}, {"A": 100, "B": 200, "C": 100}, today=today)
    rows += [row(r[0], r[1], r[9] - TODAY + today, outcome=r[7], ret=r[8],
                 od=None if r[10] is None else r[10] - TODAY + today) for r in results_rows()]
    calls = []
    monkeypatch.setattr(server, "_insight_rows", lambda: calls.append(1) or rows)

    assert len(server._INSIGHT_BUILDERS) == 4 == server._INSIGHT_LIMIT
    assert server._INSIGHT_BUILDERS[2:] == (server._insight_market_shift, server._insight_recent_results)
    fake = lambda i: (lambda: {"id": f"f{i}", "title": "t", "headline": "h", "stat": "z = 1",
                               "significant": False, "n": 1, "detail": "d", "caveat": "c", "chart": []})
    monkeypatch.setattr(server, "_INSIGHT_BUILDERS", (fake(0), fake(1)) + server._INSIGHT_BUILDERS[2:])
    monkeypatch.setattr(server, "_INSIGHT_CACHE", {"day": None, "data": None})
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")

    with server.app.test_request_context("/api/insights", headers={"X-Auth": "x"}):
        body = server.api_insights().get_json()
    ids = [i["id"] for i in body["insights"]]
    assert ids == ["f0", "f1", "market_shift", "recent_results"]
    assert len(calls) == 2, "each rolling builder reads its rows through _insight_rows"
    for got in body["insights"][2:]:
        assert isinstance(got["significant"], bool)
        assert got["stat"] and got["caveat"] and got["n"]
        assert all({"label", "value"} <= set(c) for c in got["chart"])
    rr = body["insights"][3]
    assert rr["chart_kind"] == "time" and rr["baseline"] is not None


def test_endpoint_still_requires_a_login():
    assert server.app.test_client().get("/api/insights").status_code == 401
