"""Charts: hourly dispatch with prices, and price comparison across scenarios."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # draw to files, no window needed (works in Docker and CI)
import matplotlib.pyplot as plt  # noqa: E402

from .model import UNSERVED, Result  # noqa: E402

COLORS = {
    "hydro": "#2b6cb0",
    "solar": "#f6ad1b",
    "new_solar": "#fbd46d",
    "gas_ccgt": "#8d6e63",
    "new_ccgt": "#bcaaa4",
    "gas_peaker": "#e07b39",
    "diesel": "#4a4a4a",
    UNSERVED: "#d62728",
}
# Stack order: cheapest and most "always on" at the bottom.
ORDER = ["hydro", "solar", "new_solar", "gas_ccgt", "new_ccgt", "gas_peaker", "diesel", UNSERVED]


def plot_dispatch(result: Result, title: str, path: Path | str) -> Path:
    dispatch = result.dispatch.loc[:, (result.dispatch > 1e-6).any()]  # hide plants that never run
    columns = sorted(dispatch.columns, key=lambda c: ORDER.index(c) if c in ORDER else len(ORDER))
    hours = dispatch.index

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.stackplot(
        hours,
        [dispatch[c] for c in columns],
        labels=columns,
        colors=[COLORS.get(c, "#999999") for c in columns],
        alpha=0.9,
    )
    ax.plot(hours, result.demand, color="black", linewidth=2, label="demand")
    ax.set_xlabel("Hour of the day")
    ax.set_ylabel("Generation (MW)")
    ax.set_xlim(hours.min(), hours.max())
    ax.grid(alpha=0.3)

    price_ax = ax.twinx()
    price_ax.step(hours, result.prices, where="mid", color="crimson", linestyle="--", linewidth=1.8, label="price")
    price_ax.set_ylabel("Price (R$/MWh)", color="crimson")
    price_ax.set_ylim(0, max(100.0, result.prices.max() * 1.25))

    handles, labels = ax.get_legend_handles_labels()
    price_handles, price_labels = price_ax.get_legend_handles_labels()
    ax.legend(handles + price_handles, labels + price_labels, loc="upper left", fontsize=8, ncol=2)
    ax.set_title(title)
    fig.tight_layout()

    path = Path(path)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_price_comparison(results: dict[str, Result], path: Path | str) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for name, result in results.items():
        ax.step(result.prices.index, result.prices, where="mid", linewidth=2, label=name)
    ax.set_xlabel("Hour of the day")
    ax.set_ylabel("Price (R$/MWh)")
    ax.set_title("Hourly electricity price by scenario")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()

    path = Path(path)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
