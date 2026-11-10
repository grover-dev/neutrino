# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib"]
# ///
"""Plot a log written by scripts/jetson_temps.sh.

Usage:
    uv run scripts/plot_jetson_temps.py jetson_temps_XXXX.txt [-o out.png]

One small panel per thermal zone, all sharing the same time and temperature axes.
"""

import argparse
import math
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

LINE = "#2a78d6"
PEAK = "#eb6834"
INK = "#3a3a38"
MUTED = "#8a8980"
GRID = "#e4e3de"


def parse(path):
    names, times, rows = None, [], []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "#":  # header: "# timestamp <zone> <zone> ..."
                names = parts[2:]
                continue
            try:
                t = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S")
                vals = [int(v) / 1000.0 for v in parts[2:]]
            except (ValueError, IndexError):
                continue  # partial line from Ctrl-C, etc.
            # Some zones report sentinel values (e.g. -256 C) when disabled.
            vals = [v if -40 < v < 150 else math.nan for v in vals]
            times.append(t)
            rows.append(vals)
    if not rows:
        raise SystemExit(f"no samples found in {path}")
    # Drop truncated lines (e.g. cut off by Ctrl-C) that have fewer zones.
    n = max(len(r) for r in rows)
    keep = [i for i, r in enumerate(rows) if len(r) == n]
    times = [times[i] for i in keep]
    rows = [rows[i] for i in keep]
    if not names or len(names) != n:
        names = [f"zone{i}" for i in range(n)]
    return names, times, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("-o", "--out", help="save PNG instead of opening a window")
    args = ap.parse_args()

    names, times, rows = parse(args.logfile)
    series = list(zip(*rows))

    # Drop zones that never produced a valid reading.
    keep = [i for i, s in enumerate(series) if any(not math.isnan(v) for v in s)]
    names = [names[i] for i in keep]
    series = [series[i] for i in keep]

    n = len(series)
    cols = min(3, n)
    nrows = math.ceil(n / cols)

    plt.rcParams.update({
        "font.size": 9,
        "axes.edgecolor": GRID,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    fig, axes = plt.subplots(
        nrows, cols, figsize=(4 * cols, 2.4 * nrows),
        sharex=True, sharey=True, squeeze=False, layout="constrained",
    )

    for ax, name, s in zip(axes.flat, names, series):
        ax.plot(times, s, color=LINE, linewidth=2, solid_capstyle="round")
        valid = [(t, v) for t, v in zip(times, s) if not math.isnan(v)]
        tmax, vmax = max(valid, key=lambda p: p[1])
        last = valid[-1][1]
        ax.plot([tmax], [vmax], "o", color=PEAK, markersize=6,
                markeredgecolor="white", markeredgewidth=1.5, zorder=3)
        ax.set_title(name, loc="left", color=INK, fontsize=10, fontweight="bold")
        ax.text(0.99, 0.95, f"now {last:.1f}°C · peak {vmax:.1f}°C",
                transform=ax.transAxes, ha="right", va="top", color=INK, fontsize=8)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)

    for ax in axes.flat[n:]:
        ax.set_visible(False)

    locator = mdates.AutoDateLocator()
    for ax in axes[-1]:
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    for ax in axes[:, 0]:
        ax.set_ylabel("°C")

    span = times[-1] - times[0]
    fig.suptitle(
        f"Jetson thermal zones — {len(times)} samples over {span}",
        x=0.01, ha="left", color=INK, fontsize=12,
    )

    if args.out:
        fig.savefig(args.out, dpi=150)
        print(f"saved {args.out}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
