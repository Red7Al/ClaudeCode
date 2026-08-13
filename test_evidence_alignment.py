import evidence_alignment
import intraday_signals
import quality_report


def _tsco_facts():
    return {
        "financial": False, "analyst_rec": "Buy", "analyst_buys": 12,
        "analyst_holds": 3, "analyst_sells": 0, "analyst_rated": 15,
        "analyst_trend": ("strengthening", 10, 12), "target_pct": 14.0,
    }


def test_bullish_analyst_case_is_counter_evidence_for_bearish_setup():
    stance = evidence_alignment.analyst_stance(
        buys=12, holds=3, sells=0, recommendation="Buy", target_pct=14)
    text = evidence_alignment.contextualise(
        "Of 15 analysts, 12 say Buy.", "BEARISH", stance)

    assert stance == "BULLISH"
    assert text.startswith("Counter-evidence:")
    assert "challenges rather than supports the bearish chart thesis" in text


def test_quality_thread_labels_tsco_divergence_and_strengthening(monkeypatch):
    monkeypatch.setattr(quality_report, "fundamentals", lambda ticker: _tsco_facts())
    monkeypatch.setattr(quality_report, "_chart_story", lambda *args: "The chart setup is bearish.")
    monkeypatch.setattr(quality_report, "_kpi_block", lambda *args: "")
    row = {"ticker": "TSCO.L", "name": "Tesco", "hvf_type": "BEARISH"}

    _title, body = quality_report.build_report(row)
    tweet = quality_report.build_tweet(row)

    assert "Counter-evidence:" in body
    assert "challenges rather than supports the bearish chart thesis" in body
    assert "That opposing analyst view is building" in body
    assert "conflict with the bearish chart thesis is therefore getting stronger" in body
    assert "bearish chart conflicts with analysts' ~14% upside" in tweet.lower()


def test_short_x_analyst_angle_calls_opposing_stance_a_counter_signal():
    full, short = intraday_signals._format_analyst_angle(
        "BEARISH", buys=12, holds=3, sells=0, old_buys=10, target_pct=14)

    assert full.startswith("Counter-signal:")
    assert "challenges the bearish setup" in full
    assert short == "Counter-signal: analysts bullish"
