"""Make the README figures from the saved evaluation results.

    python3 scripts/make_figures.py

Reads ONLY results/results.json (seeds 0-9) and
results/seeds-10-19/results.json, and writes PNGs to docs/img/.
Nothing here re-runs the evaluation, and no number is typed in by hand.
Needs matplotlib (the lab itself is standard library only).
"""
import json
import statistics
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
RUNS = {
    "seeds 0-9": ROOT / "results" / "results.json",
    "seeds 10-19": ROOT / "results" / "seeds-10-19" / "results.json",
}

# Palette: categorical slots 1-2 + neutral chrome (light mode).
GUIDED = "#2a78d6"     # slot 1, blue
UNIFORM = "#eb6834"    # slot 2, orange
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#ffffff"
BLUE_RAMP = ["#f0efec", "#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab"]

STRATEGIES = [
    ("baseline", "baseline\n(happy path)"),
    ("enumeration", "enumeration\n(every single request)"),
    ("stateful_uniform", "stateful\nuniform"),
    ("stateful_guided", "stateful\nguided"),
]

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 11,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK2,
    "axes.titlecolor": INK,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "xtick.color": INK2,
    "ytick.color": INK2,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def load():
    return {k: json.loads(p.read_text()) for k, p in RUNS.items()}


def clean_axes(ax, grid_axis="x"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=150, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def fig_class_grid(runs):
    """Strategy x bug-class grid: caught / total, shaded by fraction caught."""
    d = runs["seeds 0-9"]
    by_class = d["by_class"]
    classes = sorted(by_class, key=lambda c: (-by_class[c]["enumeration"] / by_class[c]["total"], c))
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)

    fig, ax = plt.subplots(figsize=(10, 6.4))
    n_rows = len(classes) + 1
    for j, (key, _) in enumerate(STRATEGIES):
        for i, cls in enumerate(classes + ["all"]):
            if cls == "all":
                caught, total = d["summary"][key]["caught"], d["summary"][key]["total"]
            else:
                caught, total = by_class[cls][key], by_class[cls]["total"]
            frac = caught / total
            y = i + (0.35 if cls == "all" else 0)
            ax.add_patch(plt.Rectangle((j + 0.04, y + 0.06), 0.92, 0.88,
                                       facecolor=cmap(frac), edgecolor=SURFACE, linewidth=2))
            ink = "#ffffff" if frac > 0.75 else INK
            weight = "bold" if cls == "all" else "normal"
            ax.text(j + 0.5, y + 0.5, f"{caught}/{total}", ha="center", va="center",
                    color=ink, fontsize=12, fontweight=weight)
    ax.set_xlim(0, len(STRATEGIES))
    ax.set_ylim(n_rows + 0.35, 0)
    ax.set_xticks([j + 0.5 for j in range(len(STRATEGIES))])
    ax.set_xticklabels([label for _, label in STRATEGIES], color=INK)
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(classes))] + [len(classes) + 0.85])
    ax.set_yticklabels(classes + ["all 9 classes"], color=INK)
    ax.get_yticklabels()[-1].set_fontweight("bold")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    seeds = d["config"]["seeds"]
    ax.set_title(f"Seeded bugs caught, by bug class and strategy (out of {d['summary']['caught_by_any']})",
                 pad=48)
    fig.text(0.0, -0.02,
             f"Stateful columns: caught by at least one of {len(seeds)} seeds "
             f"({seeds[0]}-{seeds[-1]}), {d['config']['budget']:,} requests per seed. "
             "Totals for seeds 10-19 are identical. Source: results/results.json",
             color=MUTED, fontsize=9.5, ha="left")
    save(fig, "bugs_by_class.png")


def fig_per_seed(runs):
    """Dot plot: bugs caught by ONE seed, uniform vs guided, all 20 seeds."""
    scores = {"stateful_uniform": [], "stateful_guided": []}
    for d in runs.values():
        for key in scores:
            scores[key].extend(d["summary"][key]["per_seed"])
    total = runs["seeds 0-9"]["summary"]["stateful_guided"]["total"]
    rows = [("stateful_uniform", "uniform", UNIFORM), ("stateful_guided", "guided", GUIDED)]

    fig, ax = plt.subplots(figsize=(10, 4.2))
    lo = min(min(v) for v in scores.values()) - 1
    for r, (key, label, color) in enumerate(rows):
        counts = Counter(scores[key])
        for x, n in counts.items():
            for k in range(n):
                y = r + (k - (n - 1) / 2) * 0.06
                ax.scatter(x, y, s=70, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=3)
        med = statistics.median(scores[key])
        ax.scatter(med, r - 0.4, marker="v", s=60, color=INK, zorder=3)
        ax.text(med, r - 0.46, f"median {med:g}", ha="center", va="bottom", color=INK, fontsize=10)
        ax.text(lo + 0.05, r, f"{label}\n{len(scores[key])} seeds", ha="left", va="center",
                color=INK, fontsize=11, fontweight="bold")
    ax.set_yticks([])
    ax.set_ylim(1.6, -0.75)
    ax.set_xlim(lo, total + 0.5)
    ax.set_xticks(range(lo + 1, total + 1))
    ax.set_xlabel(f"bugs caught by a single seed (out of {total})")
    clean_axes(ax, "x")
    ax.spines["left"].set_visible(False)
    budget = runs["seeds 0-9"]["config"]["budget"]
    ax.set_title(f"One seed of {budget:,} requests: how many of the {total} bugs it catches")
    fig.text(0.0, -0.04, "Each dot is one seed (seeds 0-19 pooled). "
             "Source: results/results.json + results/seeds-10-19/results.json",
             color=MUTED, fontsize=9.5, ha="left")
    save(fig, "per_seed_scores.png")


def fig_missed(runs):
    """The bugs enumeration missed: seeds (of 20) that caught each, uniform vs guided."""
    base = runs["seeds 0-9"]
    missed = [m["name"] for m in base["mutants"] if not m["enumeration"]["detected"]]
    pooled = {n: {"stateful_uniform": [0, 0], "stateful_guided": [0, 0]} for n in missed}
    for d in runs.values():
        for m in d["mutants"]:
            if m["name"] in pooled:
                for key in ("stateful_uniform", "stateful_guided"):
                    pooled[m["name"]][key][0] += m[key]["seeds_detected"]
                    pooled[m["name"]][key][1] += m[key]["seeds"]
    missed.sort(key=lambda n: (pooled[n]["stateful_guided"][0], pooled[n]["stateful_uniform"][0]),
                reverse=True)
    n_seeds = pooled[missed[0]]["stateful_guided"][1]

    fig, ax = plt.subplots(figsize=(10, 5.2))
    h = 0.36
    for i, name in enumerate(missed):
        for off, key, color in ((-h / 2, "stateful_guided", GUIDED), (h / 2, "stateful_uniform", UNIFORM)):
            v = pooled[name][key][0]
            y = i + off
            ax.barh(y, v, height=h - 0.06, color=color, edgecolor=SURFACE, linewidth=0)
            ax.text(v + 0.25, y, f"{v}/{n_seeds}", va="center", ha="left", color=INK2, fontsize=10)
    ax.set_yticks(range(len(missed)))
    ax.set_yticklabels(missed, color=INK)
    ax.set_ylim(len(missed) - 0.5, -0.5)
    ax.set_xlim(0, n_seeds + 2)
    ax.set_xticks(range(0, n_seeds + 1, 5))
    ax.set_xlabel(f"seeds that caught the bug (out of {n_seeds})")
    clean_axes(ax, "x")
    ax.spines["left"].set_visible(False)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=GUIDED, label="stateful guided"),
                       Patch(color=UNIFORM, label="stateful uniform")],
              loc="lower right", frameon=False, labelcolor=INK)
    ax.set_title(f"The {len(missed)} bugs enumeration missed (0 of {len(missed)} caught by enumeration)")
    fig.text(0.0, -0.04, "Seeds 0-19 pooled. Source: results/results.json + results/seeds-10-19/results.json",
             color=MUTED, fontsize=9.5, ha="left")
    save(fig, "missed_by_enumeration.png")


def fig_shrink(runs):
    """Dumbbell: ops in the first failing sequence -> ops after shrinking, per bug (seeds 0-9)."""
    d = runs["seeds 0-9"]
    muts = sorted(d["mutants"], key=lambda m: (m["shrink"]["shrunk_ops"], m["shrink"]["original_ops"]))
    fig, ax = plt.subplots(figsize=(10, 9))
    for i, m in enumerate(muts):
        a, b = m["shrink"]["original_ops"], m["shrink"]["shrunk_ops"]
        ax.plot([b, a], [i, i], color=AXIS, linewidth=2, zorder=1)
        ax.scatter(a, i, s=55, color=SURFACE, edgecolor=INK2, linewidth=1.5, zorder=2)
        ax.scatter(b, i, s=70, color=GUIDED, edgecolor=SURFACE, linewidth=1.5, zorder=3)
    ax.set_yticks(range(len(muts)))
    ax.set_yticklabels([m["name"] for m in muts], color=INK, fontsize=10)
    ax.set_ylim(len(muts) - 0.5, -0.5)
    ax.set_xlim(0, max(m["shrink"]["original_ops"] for m in muts) + 1)
    ax.set_xlabel("ops in the repro")
    clean_axes(ax, "x")
    ax.spines["left"].set_visible(False)
    s = d["summary"]["shrink"]
    ax.scatter([], [], s=55, color=SURFACE, edgecolor=INK2, linewidth=1.5, label="first failing sequence")
    ax.scatter([], [], s=70, color=GUIDED, label="after shrinking")
    ax.legend(loc="upper right", frameon=False, labelcolor=INK)
    ax.set_title(f"Shrinking: median {s['median_original_ops']:g} ops down to "
                 f"{s['median_shrunk_ops']:g} (max {s['max_shrunk_ops']})")
    fig.text(0.0, 0.02, "One row per seeded bug, first detection in seeds 0-9. Source: results/results.json",
             color=MUTED, fontsize=9.5, ha="left")
    save(fig, "shrinking.png")


def main():
    runs = load()
    fig_class_grid(runs)
    fig_per_seed(runs)
    fig_missed(runs)
    fig_shrink(runs)


if __name__ == "__main__":
    main()
