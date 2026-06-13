# ======================================================================================================================
# File:         test_signal_alignment.py
# Author:       Alex Hind
# Created:      2026-06-13
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Regression test for signals.bias_aligned — the canonical "does this signal agree with the trade side?" rule
# (user 2026-06-11: "Bearish is not confirmation for a buy"). Consolidated from 4 inline copies in the code-review
# 2026-06-13; this test pins the truth table so the rule can never drift. Offline, deterministic.
#
# Usage:   python test_signal_alignment.py     (exit 0 = pass, 1 = fail)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-13  Alex Hind   Initial build — bias_aligned truth table incl. the "bearish ≠ buy" rule.
# ======================================================================================================================

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from signals import bias_aligned

CASES = [
    # (bias, direction, expected)
    ("BULLISH", "BUY",     True),    # long-side, BUY/SELL caller (signals.py, emails)
    ("BULLISH", "BULLISH", True),    # long-side, BULLISH/BEARISH caller (intraday X drafts)
    ("BEARISH", "SELL",    True),
    ("BEARISH", "BEARISH", True),
    ("BEARISH", "BUY",     False),   # THE house rule — bearish never confirms a buy
    ("BULLISH", "SELL",    False),
    ("BULLISH", "BEARISH", False),
    ("BEARISH", "BULLISH", False),
    ("NEUTRAL", "BUY",     False),   # neutral is never a confirmation
    ("NEUTRAL", "SELL",    False),
    (None,      "BUY",     False),   # missing bias
    ("BULLISH", None,      False),   # missing direction
    ("",        "BUY",     False),
]

fails = 0
for bias, direction, expected in CASES:
    got = bias_aligned(bias, direction)
    ok = (got == expected)
    if not ok:
        fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  bias_aligned({bias!r}, {direction!r}) = {got} "
          f"(expected {expected})")

print(f"\n{len(CASES) - fails} passed, {fails} failed")
if fails:
    raise SystemExit(1)
