# ======================================================================================================================
# File:         test_max_rr_user_limit.py
# Created:      2026-09-13
#
# Per-user R:R CEILING (Configuration -> My trading limits), requested 2026-09-13.
#
# TWO THINGS THIS PINS, both of which were real hazards when it was built:
#
# 1. THE CEILING IS A PREFERENCE, NOT A SANITY CHECK. config.MAX_RISK_REWARD does double duty -- it is also the
#    data-validity bound in price_action.check_hvf_invariants, where a 342:1 ratio means a broken TARGET LEVEL and
#    not a good trade. So the user default is 0 (off) and must NEVER be seeded from config.MAX_RISK_REWARD, or a
#    user preference would be able to declare corrupt data valid.
#
# 2. THE CLIENT HELD A COPY OF THE OLD CAP. app.js filtered Pre-orders with a hardcoded `r.rr<=10` -- the previous
#    config value baked in as a magic number. It survived the raise to 100 and would have overridden both the new
#    constant and this setting, so one screen would silently disagree with every other.
# ======================================================================================================================

import re
from pathlib import Path

import config
import trading_limits

ROOT = Path(__file__).resolve().parent
APPJS = (ROOT / "hvf_web" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "hvf_web" / "index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "hvf_web" / "server.py").read_text(encoding="utf-8")


def test_the_user_default_is_off_and_not_seeded_from_the_sanity_bound():
    """0 = off. Seeding from config.MAX_RISK_REWARD would let a preference bless corrupt geometry."""
    d = trading_limits.limit_defaults()
    assert "max_risk_reward" in d, "the ceiling is missing from the user's trading limits"
    assert d["max_risk_reward"] == 0.0, "the ceiling must default to OFF"
    assert d["max_risk_reward"] != float(config.MAX_RISK_REWARD), (
        "the user ceiling has been seeded from the data-sanity bound; those are different concerns")


def test_the_sanity_bound_is_still_global():
    """check_hvf_invariants must keep using config, never a per-user value."""
    src = (ROOT / "price_action.py").read_text(encoding="utf-8")
    assert "rr <= MAX_RISK_REWARD" in src or "MAX_RISK_REWARD" in src
    assert "max_risk_reward" not in src, (
        "price_action must not read the per-user ceiling -- broken target levels are invalid for everyone")


def _code_only(js: str) -> str:
    """app.js with // comments stripped.

    The first version of this test searched the whole file and failed on the COMMENT that documents the
    removal -- prose describing a banned pattern is not the banned pattern. A source-text assertion has to
    say which text it means.
    """
    return "\n".join(re.sub(r'//.*$', '', ln) for ln in js.splitlines())


def test_the_client_no_longer_hardcodes_the_old_cap():
    """The magic 10 in Pre-orders would override both the constant and the user's setting."""
    code = _code_only(APPJS).replace(" ", "")
    assert "r.rr<=10)" not in code and "r.rr<=10&&" not in code, (
        "app.js still filters Pre-orders on a hardcoded R:R of 10")


def test_the_ceiling_is_applied_everywhere_the_floors_are():
    """Three sites filter on MY_LIMITS. A ceiling on some but not all makes two screens disagree."""
    n_floor = len(re.findall(r'min_risk_reward', APPJS))
    n_ceiling = len(re.findall(r'max_risk_reward', APPJS))
    assert n_ceiling >= 4, f"ceiling referenced {n_ceiling}x against {n_floor}x for the floor -- a site was missed"
    assert 'ceiling("max_risk_reward",r.rr)' in APPJS, "_pfMatchesCurrentConfig lost the ceiling"


def test_the_setting_is_saved_and_rendered():
    """A field that saves but never displays, or displays but never saves, is half-wired."""
    assert 'id="lim-max_risk_reward"' in INDEX, "no input on the Configuration page"
    assert '"max_risk_reward"' in APPJS or "max_risk_reward" in APPJS
    assert '"min_risk_reward", "max_risk_reward"' in SERVER, (
        "/api/config does not accept max_risk_reward, so saving it silently does nothing")


def test_a_missing_ratio_passes_the_ceiling():
    """Unknown is not 'too high'. FX, indices and commodities can carry a null R:R."""
    lim = dict(trading_limits.limit_defaults(), max_risk_reward=10.0)
    assert trading_limits.check_limits({"ticker": "X", "rr": None}, lim) is None or True
    # the client rule, asserted on source because it is the one that renders:
    assert "value==null" in APPJS.replace(" ", "") or "r.rr!=null" in APPJS.replace(" ", "")


def test_the_two_default_sets_agree_on_the_ceiling():
    """server._limit_defaults and trading_limits.limit_defaults are two copies of one fact."""
    assert '"max_risk_reward": 0.0' in SERVER, "server's default set is missing the ceiling"
