# ======================================================================================================================
# File:         docs/_gen_doc_visuals.py
# Author:       Alex Hind
# Created:      2026-06-19
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Regenerates the rendered visuals embedded in the docs (user 2026-06-19: "Markdown + rendered visuals"):
#   docs/img/hvf_funnel.png      — the Hunt Volatility Funnel geometry (H1>H2>H3 / L1<L2<L3, entry/stop/target, AMP1)
#   docs/img/decision_flow.png   — scan -> 5 rules -> publication gates -> weighting -> publish
#   docs/img/weighting.png       — the hvf_weight sort key (R:R primary, then signal, then quality)
#
# Pure matplotlib (no project imports) so it is reproducible anywhere. Re-run after changing any documented numbers:
#   python docs/_gen_doc_visuals.py
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.2.0   2026-06-19  Alex Hind   Decision flow: add the EXECUTION node (user 2026-06-19) — broker execution is a separate
#                                 system (TradingView alert -> TradingViewWebhook -> IG), shown dashed/greyed.
# 1.1.0   2026-06-19  Alex Hind   Decision flow: Slack now precedes X (user 2026-06-19) — Slack publishes first / more
#                                 instruments, X then publishes the top subset.
# 1.0.0   2026-06-19  Alex Hind   Initial build — funnel schematic, decision flow, weighting illustration.
# ======================================================================================================================

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BG, FG, MUTED = "#0d1117", "#c9d1d9", "#8b949e"
RED, GREEN, GOLD, BLUE, PURP = "#f85149", "#3fb950", "#e3b341", "#58a6ff", "#a371f7"
OUT = os.path.join(os.path.dirname(__file__), "img")
os.makedirs(OUT, exist_ok=True)


def _save(fig, name):
    fig.savefig(os.path.join(OUT, name), dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    print("wrote", os.path.join("docs", "img", name))


def funnel():
    fig, ax = plt.subplots(figsize=(11, 6))
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    # converging jaws (left half)
    hx, hy = [0, 2, 4], [9.6, 8.2, 7.0]          # H1>H2>H3 (lower highs)
    lx, ly = [0.6, 2.6, 4.0], [3.2, 4.6, 5.6]    # L1<L2<L3 (higher lows)
    ax.plot(hx, hy, "--o", color=RED, lw=2, label="H1 > H2 > H3 (lower highs)")
    ax.plot(lx, ly, "--o", color=GREEN, lw=2, label="L1 < L2 < L3 (higher lows)")
    for (px, py, lbl) in [(0, 9.6, "H1"), (2, 8.2, "H2"), (4, 7.0, "H3"),
                          (0.6, 3.2, "L1"), (2.6, 4.6, "L2"), (4.0, 5.6, "L3")]:
        ax.text(px - 0.18, py, lbl, color=FG, fontsize=8, va="center", ha="right")
    # level lines emanate from the apex (x=4) and STOP before the label gutter (x=8.4)
    entry, stop, target = 7.0, 5.6, 11.4         # bullish: entry=H3, stop just below L3, target above
    LINE_END, LBL_X = 8.4, 8.6
    ax.plot([4.0, LINE_END], [entry, entry],   color=GOLD,  lw=1.6, ls="--")
    ax.plot([4.0, LINE_END], [stop, stop],     color=RED,   lw=1.2, ls=":")
    ax.plot([4.0, LINE_END], [target, target], color=GREEN, lw=1.2, ls=":")
    ax.text(LBL_X, entry,  "Entry = H3 (ceiling break)", color=GOLD,  va="center", fontsize=9)
    ax.text(LBL_X, stop,   "Stop = just beyond L3",      color=RED,   va="center", fontsize=9)
    ax.text(LBL_X, target, "Target = AMP1 projection",   color=GREEN, va="center", fontsize=9)
    # AMP1 amplitude arrow (between apex and the label gutter, no overlap)
    ax.annotate("", xy=(6.0, target), xytext=(6.0, entry),
                arrowprops=dict(arrowstyle="<->", color=PURP, lw=1.8))
    ax.text(5.0, (target + entry) / 2,
            "AMP1\n(target amplitude re-anchored\nto the prior trend's\nexhaustion extreme)",
            color=PURP, fontsize=8, va="center", ha="center")
    ax.text(2.0, 1.7, "Volatility contracts from BOTH sides — the funnel must tighten >=30%\n"
                      "from H1-L1 to H3-L3, on a confirmed prior trend, with a fresh H3.",
            color=MUTED, fontsize=9, ha="center")
    ax.set_xlim(-0.6, 13.2); ax.set_ylim(1, 12.4)
    ax.set_title("Hunt Volatility Funnel — geometry (bullish example)", color=FG, fontsize=13, weight="bold")
    ax.legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d", labelcolor=FG, fontsize=9)
    for s in ax.spines.values():
        s.set_edgecolor("#30363d")
    ax.tick_params(colors=MUTED); ax.set_xticks([]); ax.set_yticks([])
    _save(fig, "hvf_funnel.png")


def _box(ax, x, y, w, h, text, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc="#161b22", ec=color, lw=1.8))
    ax.text(x + w / 2, y + h / 2, text, color=FG, ha="center", va="center", fontsize=9)


def _arrow(ax, x1, y1, x2, y2, dashed=False, color=MUTED):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=14,
                                 color=color, lw=1.4, linestyle="--" if dashed else "-"))


def decision_flow():
    fig, ax = plt.subplots(figsize=(11, 6.4))
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    ax.set_xlim(0, 12); ax.set_ylim(0, 10)
    _box(ax, 0.4, 7.5, 2.6, 1.4, "Multi-timeframe scan\n(daily 30/60/90/180/240,\nweekly)", BLUE)
    _box(ax, 4.0, 7.5, 3.0, 1.4, "FIVE HVF rules\ntrend - lower highs - higher lows\nconverge >=30% - fresh H3", RED)
    _box(ax, 8.0, 7.5, 3.4, 1.4, "Best-of timeframes + AMP1\ntarget re-anchor + IG validation", PURP)
    _box(ax, 8.0, 4.8, 3.4, 1.4, "Gates\nR:R >= 3 -> tradeable\nquality >= 70 -> publishable", GOLD)
    _box(ax, 4.0, 4.8, 3.0, 1.4, "Weighting / ordering\nR:R -> signal -> quality\n(hvf_weight)", GREEN)
    _box(ax, 0.4, 4.8, 2.6, 1.4, "Per-market grouping\ntop N of M candidates", BLUE)
    # Slack publishes FIRST and shows MORE instruments; X then publishes the top subset
    # (user 2026-06-19). Flow: grouping -> Slack -> X.
    _box(ax, 1.4, 2.0, 3.7, 1.5, "1) PUBLISH to Slack\ndaily report - dossier - alerts\nMORE instruments + time-to-target, % from price", BLUE)
    _box(ax, 6.7, 2.0, 3.4, 1.5, "2) then PUBLISH to X\ncard + short tweet + long thread\nTOP SUBSET (quality >= 70)", GREEN)
    _arrow(ax, 3.0, 8.2, 4.0, 8.2)
    _arrow(ax, 7.0, 8.2, 8.0, 8.2)
    _arrow(ax, 9.7, 7.5, 9.7, 6.2)
    _arrow(ax, 8.0, 5.5, 7.0, 5.5)
    _arrow(ax, 4.0, 5.5, 3.0, 5.5)
    _arrow(ax, 1.7, 4.8, 2.6, 3.55)      # grouping -> Slack (first)
    _arrow(ax, 5.1, 2.75, 6.7, 2.75)     # Slack -> X (X is the narrower, later step)
    # Execution is a SEPARATE system (user 2026-06-19): this repo publishes/analyses only.
    # The actual broker order is placed by TradingViewWebhook -> IG when a TradingView alert
    # fires — shown dashed/greyed to mark the system boundary.
    _box(ax, 3.0, -0.9, 6.0, 1.3,
         "EXECUTION — separate system\nTradingView alert -> TradingViewWebhook -> IG REST API\n(places the actual broker order; not part of this repo)", MUTED)
    _arrow(ax, 3.2, 2.0, 4.6, 0.45, dashed=True)   # Slack/publish -> execution (informational boundary)
    _arrow(ax, 8.4, 2.0, 7.4, 0.45, dashed=True)   # X/publish -> execution
    ax.text(6.0, -1.5, "Same weight order (R:R first) drives every list. Slack publishes first / more instruments; "
                       "X then the top subset (quality>=70). Broker execution is a separate system.",
            color=MUTED, ha="center", fontsize=9)
    ax.set_ylim(-2.0, 10)
    ax.set_title("From scan to publication (and where execution happens)", color=FG, fontsize=13, weight="bold")
    ax.axis("off")
    _save(fig, "decision_flow.png")


def weighting():
    fig, ax = plt.subplots(figsize=(10, 4.6))
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    ax.set_xlim(0, 12); ax.set_ylim(0, 6)
    ax.text(6, 5.4, "hvf_weight  =  ( -R:R ,  signal_rank ,  -quality )", color=FG, ha="center",
            fontsize=15, weight="bold", family="monospace")
    ax.text(6, 4.5, "sorted ascending -> best first", color=MUTED, ha="center", fontsize=10)
    cols = [("1st: R:R (desc)", "higher reward-to-risk wins", GREEN, 2.2),
            ("2nd: signal", "TRIGGERED > READY > DEVELOPING", GOLD, 6.0),
            ("3rd: quality", "higher pattern quality breaks ties", BLUE, 9.8)]
    for title, sub, col, x in cols:
        _box(ax, x - 1.7, 2.2, 3.4, 1.4, f"{title}\n{sub}", col)
        if x < 9:
            _arrow(ax, x + 1.7, 2.9, x + 2.6, 2.9)
    ax.text(6, 0.9, "R:R is the PRIMARY key (user 2026-06-19). Every published list — X drafts, daily report,\n"
                    "quality thread — uses this one ordering, so nothing diverges.", color=MUTED, ha="center", fontsize=9)
    ax.set_title("Weighting / ordering of HVF setups", color=FG, fontsize=13, weight="bold")
    ax.axis("off")
    _save(fig, "weighting.png")


if __name__ == "__main__":
    funnel()
    decision_flow()
    weighting()
    print("done")
