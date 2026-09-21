"""Text helpers for X posts and long reports — deliberately free of numpy, pandas and yfinance.

WHY THIS MODULE EXISTS. These four helpers used to live in intraday_signals, which imports numpy,
pandas and yfinance at module level (lines 336-338). On the IONOS web host `import numpy` is
SIGSYS-killed — the seccomp filter kills any process calling mbind(2), which the bundled OpenBLAS does —
and it kills the PROCESS, so try/except cannot catch it. quality_report.publish_long_report_for imports
those helpers lazily, so /api/thread died with HTTP 500 after ~120s (the gateway timeout) and the whole
X-thread card on the instrument panel rendered as nothing.

They are pure string work and never needed numpy. Moving them here is what lets the web tier use them.

intraday_signals imports FROM this module rather than keeping its own copies, so the text the website
shows and the text posted to X are produced by the SAME code. That is the point: a second
implementation would drift, and the drift would only show up in public.
"""

# ── X character weighting ─────────────────────────────────────────────────────────────────────────


def x_weighted_len(s: str) -> int:
    """Approximate X's weighted character count: supplementary-plane code points
    (emoji hooks, the bold-italic disclaimer) count as 2, everything else as 1 —
    models the 280-char limit far better than len() for our content."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def bold_italic(s: str) -> str:
    """Map ASCII letters to Unicode Mathematical Bold Italic so the text shows as
    bold-italic on X (plain tweets have no markdown). NOTE: these are supplementary-
    plane glyphs — screen readers may skip them and X counts each as 2 chars."""
    out = []
    for c in s:
        o = ord(c)
        if 65 <= o <= 90:      out.append(chr(0x1D468 + o - 65))   # A–Z
        elif 97 <= o <= 122:   out.append(chr(0x1D482 + o - 97))   # a–z
        else:                  out.append(c)
    return "".join(out)


# Disclaimer in bold italic, preceded by a blank line (user 2026-06-13). Plain ASCII
# is kept here for readability; rendered to Unicode bold-italic once at import.
NFA_DISCLAIMER = "\n\n" + bold_italic("Not financial advice.")


# ── Market hashtags ───────────────────────────────────────────────────────────────────────────────


def exchange_tag_by_suffix(ticker: str) -> str:
    """The ticker-suffix fallback: #LSE for .L, else #NYSE.

    intraday_signals has a richer resolver that asks yfinance for the REAL listing exchange
    (#NASDAQ vs #NYSE) and passes it in below. That resolver cannot run on the IONOS web host,
    because reaching yfinance means importing numpy. This is the same fallback its own `except`
    branch already used, so a US ticker's tag on the WEBSITE may say #NYSE where the POSTED tweet
    says #NASDAQ. UK tickers are unaffected — they never reach this path.
    """
    return "#LSE" if (ticker or "").endswith(".L") else "#NYSE"


def market_tags(r: dict, exchange_tag=None) -> str:
    """Market + country hashtags (user 2026-06-13). UK names use their index
    (#FTSE100/#FTSE250); US names use the REAL listing exchange (#NASDAQ/#NYSE), not the
    S&P bucket. Country #UK/#USA.

    `exchange_tag` is the resolver for the US branch. Callers that can reach yfinance pass their
    own; callers that cannot (the web host) leave it None and get the suffix fallback.
    """
    idx = r.get("index") or ""
    if idx in ("FTSE 100", "FTSE 250"):
        return ("#FTSE100" if idx == "FTSE 100" else "#FTSE250") + " #UK"
    if (r.get("ticker") or "").endswith(".L"):
        return "#FTSE #UK"
    resolve = exchange_tag or exchange_tag_by_suffix
    return f"{resolve(r.get('ticker') or '')} #USA"
