

# ── A "positive" must actually be positive (user 2026-09-05) ───────────────────────────────────────────
#
# THE PUBLISHED DEFECT. A tweet read: "Returns are high: 0% on shareholders' money." VOD's ROE is
# 0.00109 -- 0.1%. It passed the sanity filter (0 < roe <= 0.60), which only proves the number is not
# absurd, rounded to "0" at :.0f, and was then described as high. Every _P_ROE phrasing asserts strength,
# so a weak return produces a statement that is the opposite of the truth, on a public account.

def test_a_weak_return_is_never_described_as_high():
    import quality_report as qr

    assert qr._ROE_STRONG >= 0.10, "a return called 'high' must clear a floor that justifies the word"
    for phrase in qr._P_ROE:
        formatted = phrase.format(roe=f"{qr._ROE_STRONG * 100:.0f}")
        assert " 0%" not in formatted, f"the floor still allows a 0% claim: {formatted}"


def test_every_roe_phrasing_asserts_strength_so_the_floor_is_load_bearing():
    """If a neutral phrasing were added, the floor could be relaxed. While they all claim strength, it
    cannot be -- which is why the floor and the wording are pinned together in one test."""
    import quality_report as qr
    strong = ("high", "strong", "well")

    for phrase in qr._P_ROE:
        assert any(w in phrase.lower() for w in strong), (
            f"this phrasing makes no strength claim, so the floor may be wrong for it: {phrase}")


def test_a_weak_return_produces_no_returns_sentence_at_all(monkeypatch):
    """BEHAVIOURAL, not a source match. Drives build_report with VOD's real ROE of 0.00109 and asserts
    the published prose makes no claim about returns -- the two tests above only pin the wording and the
    floor, and neither would have caught the emit site still using `if f.get("roe")`."""
    import quality_report as qr
    monkeypatch.setattr(qr, "fundamentals", lambda tk: {"roe": 0.00109, "financial": False})
    monkeypatch.setattr("intraday_signals._resolve_name", lambda tk: "Vodafone Group PLC", raising=False)

    _, body = qr.build_report({"ticker": "VOD.L", "name": "Vodafone Group PLC", "hvf_type": "BULLISH"})

    assert "Returns are high" not in body
    assert "shareholders' money" not in body
    assert "return on equity" not in body.lower()


def test_a_strong_return_still_gets_its_sentence(monkeypatch):
    """The inverse, so the fix cannot be 'delete the feature'."""
    import quality_report as qr
    monkeypatch.setattr(qr, "fundamentals", lambda tk: {"roe": 0.24, "financial": False})
    monkeypatch.setattr("intraday_signals._resolve_name", lambda tk: "Strong Co", raising=False)

    _, body = qr.build_report({"ticker": "STRONG", "name": "Strong Co", "hvf_type": "BULLISH"})

    assert "24%" in body, f"a 24% return should be reported: {body[:300]}"
