#!/usr/bin/env python3
"""Regenerate the README result figures.

The benchmark numbers live in the RESULTS dict below — edit them there.
Outputs SVG (embedded in README.md, text baked to paths) and PNG (preview)
into assets/figures/.

Usage: python scripts/plot_readme_figures.py
Requires: matplotlib
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "figures"

# Benchmark results embedded from the evaluation records: overall pass rate (%)
# for SkillLift and baselines on WildClawBench (60 tasks) and SkillsBench
# (87 tasks), 3 rollout seeds, mean per-task best evolved-skill score. Evolving
# baselines share the same seed skills and receive 2x SkillLift's token budget.
# WildClawBench uses GPT-5.4 and SkillsBench uses GPT-5.4-mini, aligning with
# the official implementations.
RESULTS = {
    "methods": [
        "No skill",
        "+ Human-written",
        "+ LLM-written",
        "+ SkillOpt",
        "+ CoEvoSkills",
        "+ SkillLift (ours)",
    ],
    "main_results": {
        "WildClawBench": {
            "GPT-5.4":         [50.3, 56.9, 55.7, 64.3, 62.2, 68.4],
            "GLM-5.1":         [48.1, 55.2, 52.2, 62.0, 55.9, 66.5],
            "DeepSeek-V4-Pro": [44.4, 53.6, 48.3, 59.5, 54.7, 62.4],
        },
        "SkillsBench": {
            "GPT-5.4-mini":    [29.9, 41.4, 39.5, 58.2, 52.5, 62.5],
            "GLM-5.1":         [32.7, 58.4, 57.5, 70.5, 66.6, 75.1],
            "DeepSeek-V4-Pro": [26.9, 50.1, 47.9, 69.0, 66.3, 74.3],
        },
    },
    "token_efficiency": {
        # Cumulative tokens to first reach the 0.9-quantile pass rate on
        # SkillsBench, as a multiple of SkillLift's cost (SkillLift = 1.0x).
        "models": ["GPT-5.4-mini", "GLM-5.1", "DeepSeek-V4"],
        "SkillOpt":    [2.74, 2.22, 2.62],
        "CoEvoSkills": [2.68, 2.17, 2.13],
    },
    "ablation": {
        # Ablation on SkillsBench (overall pass rate, %).
        "variants": ["w/o rubricator", "w/o seed skills", "w/ score regression", "SkillLift (full)"],
        "models": ["GPT-5.4-mini", "GLM-5.1", "DeepSeek-V4"],
        "GPT-5.4-mini": [42.5, 57.5, 59.8, 62.5],
        "GLM-5.1":      [59.8, 71.3, 72.4, 75.1],
        "DeepSeek-V4":  [65.5, 67.8, 69.0, 74.3],
    },
}

# NPG-inspired scientific palette, colorblind-considered.
C_OURS = "#E64B35"      # vermillion — SkillLift
C_NAVY = "#3C5488"      # dark navy  — SkillOpt / GPT
C_CYAN = "#4DBBD5"      # cyan       — CoEvoSkills / GLM
C_GREEN = "#00A087"     # teal       — DeepSeek
C_STATIC = ["#C9CDD3", "#A9B4C0", "#8494A7"]  # static-skill gray ramp
C_TEXT = "#2B2B2B"
C_MUTED = "#6B6B6B"

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "svg.fonttype": "path",
    "axes.edgecolor": "#B0B4BA",
    "axes.linewidth": 0.8,
    "axes.labelcolor": C_TEXT,
    "text.color": C_TEXT,
    "xtick.color": C_MUTED,
    "ytick.color": C_MUTED,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "axes.titlesize": 10.5,
    "axes.labelsize": 9.5,
})


def _style_axis(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linewidth=0.6, alpha=0.35, color="#9AA0A6", zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(length=3, width=0.8)


def save(fig, name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(OUT_DIR / f"{name}.{ext}", bbox_inches="tight",
                    facecolor="white", dpi=200 if ext == "png" else None)
    plt.close(fig)
    print(f"wrote {OUT_DIR / name}.svg")


def plot_main_results(data):
    methods = data["methods"]
    results = data["main_results"]
    n = len(methods)
    colors = C_STATIC + [C_NAVY, C_CYAN, C_OURS]

    fig, axes = plt.subplots(2, 3, figsize=(13.2, 7.4))
    handles = [
        plt.Rectangle((0, 0), 1, 1, fc=c, ec="none")
        for c in colors
    ]
    for i, (bench, models) in enumerate(results.items()):
        for j, (model, values) in enumerate(models.items()):
            ax = axes[i][j]
            _style_axis(ax)
            x = np.arange(n)
            vals = np.asarray(values)
            ax.bar(x, vals, width=0.62, color=colors, zorder=3)
            base = vals[0]
            for xi, v in zip(x, vals):
                ax.text(xi, v + 0.9, f"{v:.1f}", ha="center", va="bottom",
                        fontsize=7.8,
                        fontweight="bold" if xi == n - 1 else "normal",
                        color=C_OURS if xi == n - 1 else C_MUTED)
            delta = vals[-1] - base
            ax.text(n - 1, vals[-1] + 8, f"+{delta:.1f} pp", ha="center",
                    va="bottom", fontsize=8.2, fontweight="bold", color=C_OURS)
            ax.set_xticks(x)
            ax.set_xticklabels([m.replace(" (ours)", "") for m in methods],
                               rotation=24, ha="right")
            ax.set_ylim(0, 100)
            ax.set_yticks([0, 25, 50, 75, 100])
            if j == 0:
                ax.set_ylabel(f"{bench}\n\nPass rate (%)")
            ax.set_title(model, pad=6)
    fig.legend(handles, methods, loc="upper center", ncol=n, frameon=False,
               fontsize=9, bbox_to_anchor=(0.5, 1.0), handlelength=1.2,
               handleheight=1.0, columnspacing=1.6)
    fig.suptitle("Overall pass rate by model and benchmark", y=1.10,
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save(fig, "main_results")


def plot_token_efficiency(data):
    eff = data["token_efficiency"]
    models = eff["models"]
    skillopt = np.asarray(eff["SkillOpt"])
    coevo = np.asarray(eff["CoEvoSkills"])

    fig, ax = plt.subplots(figsize=(9.6, 4.0))
    _style_axis(ax)
    ax.grid(axis="x", linewidth=0.6, alpha=0.35, color="#9AA0A6", zorder=0)
    ax.grid(axis="y", visible=False)

    y = np.arange(len(models))
    h = 0.32
    bars1 = ax.barh(y - h / 2 - 0.02, skillopt, height=h, color=C_NAVY,
                    zorder=3, label="SkillOpt")
    bars2 = ax.barh(y + h / 2 + 0.02, coevo, height=h, color=C_CYAN,
                    zorder=3, label="CoEvoSkills")
    for bars in (bars1, bars2):
        for b in bars:
            ax.text(b.get_width() + 0.04, b.get_y() + b.get_height() / 2,
                    f"{b.get_width():.2f}×", va="center", ha="left",
                    fontsize=9, fontweight="bold", color=C_TEXT)

    ax.axvline(1.0, color=C_OURS, linewidth=1.6, linestyle="--", zorder=4,
               label="SkillLift = 1.0×")

    ax.set_yticks(y)
    ax.set_yticklabels(models, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, 3.2)
    ax.set_xlabel("Cumulative tokens to reach 0.9-quantile accuracy on SkillsBench (× relative to SkillLift)")
    ax.set_title("SkillLift reaches the same accuracy with 40–70% fewer tokens",
                 fontsize=12.5, fontweight="bold", pad=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=3,
              frameon=False, fontsize=9, columnspacing=1.8)
    fig.tight_layout()
    save(fig, "token_efficiency")


def plot_ablation(data):
    ab = data["ablation"]
    variants = ab["variants"]
    models = ab["models"]
    model_colors = {"GPT-5.4-mini": C_NAVY, "GLM-5.1": C_CYAN, "DeepSeek-V4": C_GREEN}

    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    _style_axis(ax)
    n = len(models)
    x = np.arange(len(variants))
    w = 0.24
    full_x = len(variants) - 1
    ax.axvspan(full_x - 0.47, full_x + 0.47, color=C_OURS, alpha=0.07, zorder=0)

    for k, model in enumerate(models):
        vals = np.asarray(ab[model])
        offset = (k - (n - 1) / 2) * w
        bars = ax.bar(x + offset, vals, width=w * 0.92,
                      color=model_colors[model], zorder=3, label=model)
        for xi, v in zip(x + offset, vals):
            emphasize = xi > full_x + 0.4
            ax.text(xi, v + 0.9, f"{v:.1f}", ha="center", va="bottom",
                    fontsize=8,
                    fontweight="bold" if emphasize else "normal",
                    color=C_OURS if emphasize else C_MUTED)

    ax.set_xticks(x)
    ax.set_xticklabels(variants, fontsize=9.5)
    ax.set_ylabel("Overall pass rate on SkillsBench (%)")
    ax.set_ylim(0, 88)
    ax.set_yticks([0, 20, 40, 60, 80])
    ax.set_title("Ablation: oracle-aligned rubric revision is the core driver",
                 fontsize=12.5, fontweight="bold", pad=10)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
              columnspacing=1.4)
    fig.tight_layout()
    save(fig, "ablation")


def main():
    plot_main_results(RESULTS)
    plot_token_efficiency(RESULTS)
    plot_ablation(RESULTS)


if __name__ == "__main__":
    main()
