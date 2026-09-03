"""Shared candlestick chart scaffolding."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import matplotlib
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from quant_trade import config as app_config

SIGNAL_SHEET_COLUMNS = app_config.SIGNAL_SHEET_COLUMNS


def configure_matplotlib_cjk() -> None:
    """Apply CJK-capable font defaults for chart titles and labels."""
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


configure_matplotlib_cjk()


def make_price_rsi_figure() -> tuple[Figure, Axes, Axes]:
    """Create the shared price + RSI subplot layout."""
    figure, (price_axis, rsi_axis) = plt.subplots(
        2,
        1,
        figsize=(9, 6),
        height_ratios=(3, 1),
        sharex=True,
        constrained_layout=True,
    )
    return figure, price_axis, rsi_axis


def draw_candlesticks(price_axis: Axes, frame: pd.DataFrame) -> list[int]:
    """Draw OHLC candles and return the x-axis indices."""
    x_values = list(range(len(frame)))
    for x_value, (_, row) in zip(x_values, frame.iterrows(), strict=True):
        rising = float(row["close"]) >= float(row["open"])
        color = "#d94b55" if rising else "#218c5b"
        price_axis.vlines(x_value, row["low"], row["high"], color=color, linewidth=1)
        body_low = min(float(row["open"]), float(row["close"]))
        body_height = max(abs(float(row["close"]) - float(row["open"])), 0.001)
        price_axis.add_patch(
            Rectangle(
                (x_value - 0.32, body_low),
                0.64,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
            )
        )
    return x_values


def plot_moving_averages(
    price_axis: Axes,
    x_values: Sequence[int],
    fast_ma: Sequence[float | None],
    slow_ma: Sequence[float | None],
) -> None:
    """Plot MA20/MA30 observation overlays."""
    price_axis.plot(x_values, fast_ma, color="#d18b1f", linewidth=1.2, label="MA20")
    price_axis.plot(x_values, slow_ma, color="#2f66d0", linewidth=1.2, label="MA30")


def plot_rsi(
    rsi_axis: Axes,
    x_values: Sequence[int],
    rsi_values: Sequence[float | None],
    *,
    levels: Sequence[float] = (45, 50, 55),
    label: str | None = None,
) -> None:
    """Plot RSI with reference levels."""
    rsi_axis.plot(
        x_values,
        rsi_values,
        color="#7a4cc2",
        linewidth=1.2,
        label=label,
    )
    for level in levels:
        rsi_axis.axhline(level, color="#888888", linestyle=":", linewidth=0.8)
    rsi_axis.set_ylim(0, 100)
    rsi_axis.set_ylabel("RSI(14)")
    rsi_axis.grid(alpha=0.18)


def mark_entry(
    price_axis: Axes,
    trigger_x: int,
    *,
    color: str,
) -> None:
    """Mark the signal candle with a bottom-aligned B label."""
    price_axis.text(
        trigger_x,
        0.01,
        "B",
        transform=price_axis.get_xaxis_transform(),
        color=color,
        ha="center",
        va="bottom",
        fontweight="bold",
    )


def finalize_date_axis(rsi_axis: Axes, frame: pd.DataFrame) -> None:
    """Apply shared date tick formatting on the RSI axis."""
    dates = [timestamp.strftime("%Y-%m-%d") for timestamp in frame.index]
    step = max(1, len(dates) // 10)
    ticks = list(range(0, len(dates), step))
    rsi_axis.set_xticks(
        ticks, [dates[index] for index in ticks], rotation=30, ha="right"
    )


def save_figure(figure: Figure, output: str | Path) -> Path:
    """Save and close a matplotlib figure."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=100)
    plt.close(figure)
    return destination


def render_signal_sheet(
    image_paths: list[Path],
    output: str | Path,
) -> Path:
    """Combine individual signal charts into one QQ delivery image."""
    if not image_paths:
        raise ValueError("image_paths must not be empty")
    columns = min(SIGNAL_SHEET_COLUMNS, len(image_paths))
    rows = math.ceil(len(image_paths) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(9 * columns, 6 * rows),
        squeeze=False,
    )
    for axis, image_path in zip(axes.flat, image_paths, strict=False):
        axis.imshow(plt.imread(image_path))
        axis.axis("off")
    for axis in axes.flat[len(image_paths) :]:
        axis.axis("off")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.subplots_adjust(left=0, right=1, top=1, bottom=0, wspace=0, hspace=0)
    figure.savefig(destination, dpi=100)
    plt.close(figure)
    return destination
