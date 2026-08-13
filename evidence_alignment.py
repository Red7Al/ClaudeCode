"""Direction-aware labels for evidence included in public trading narratives."""


def analyst_stance(*, buys=None, holds=None, sells=None, recommendation=None, target_pct=None):
    """Return BULLISH, BEARISH or MIXED without pretending a price target is a chart signal."""
    numeric = all(isinstance(value, (int, float)) for value in (buys, holds, sells))
    if numeric:
        total = buys + holds + sells
        if total > 0 and buys > total / 2:
            return "BULLISH"
        if total > 0 and sells > total / 2:
            return "BEARISH"
    rec = str(recommendation or "").lower().replace("_", " ")
    if rec in {"buy", "strong buy"}:
        return "BULLISH"
    if rec in {"sell", "strong sell", "underperform"}:
        return "BEARISH"
    if isinstance(target_pct, (int, float)):
        if target_pct >= 5:
            return "BULLISH"
        if target_pct <= -5:
            return "BEARISH"
    return "MIXED"


def relationship(setup_direction, evidence_stance):
    setup = str(setup_direction or "").upper()
    stance = str(evidence_stance or "").upper()
    if setup not in {"BULLISH", "BEARISH"} or stance not in {"BULLISH", "BEARISH"}:
        return "mixed"
    return "supports" if setup == stance else "opposes"


def contextualise(statement, setup_direction, evidence_stance):
    """Make it impossible to read contrary evidence as support for the setup."""
    setup = str(setup_direction or "").lower()
    rel = relationship(setup_direction, evidence_stance)
    sentence = str(statement or "").strip()
    if rel == "opposes":
        return f"Counter-evidence: {sentence} This challenges rather than supports the {setup} chart thesis."
    if rel == "supports":
        return f"Supporting evidence: {sentence} This agrees with the {setup} chart thesis."
    return f"Mixed evidence: {sentence} Treat this as context, not confirmation of the {setup or 'chart'} thesis."
