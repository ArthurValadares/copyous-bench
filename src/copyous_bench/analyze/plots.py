"""Figures, rendered in parallel with matplotlib's object-oriented API (no pyplot state)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..branches import BRANCHES, label_of
from ..config import CORE_SIZE
from .data import group_samples


class Plot(ABC):
    name: str

    @abstractmethod
    def render(self, samples: pd.DataFrame) -> Any | None:  # pragma: no cover
        """Return a matplotlib ``Figure`` or ``None`` when there is nothing to draw."""


@dataclass(frozen=True)
class Panel:
    metric: str
    title: str
    ylabel: str


def _box(ax: Any, grouped: dict[str, pd.DataFrame], panel: Panel) -> None:
    labels, data = [], []
    for branch, frame in grouped.items():
        values = frame[panel.metric].dropna().astype(float).tolist()
        if values:
            labels.append(label_of(branch))
            data.append(values)
    if not data:
        ax.set_visible(False)
        return
    ax.boxplot(data, tick_labels=labels, showmeans=True)
    ax.set_title(panel.title)
    ax.set_ylabel(panel.ylabel)
    ax.grid(True, axis="y", alpha=0.3)
    ax.tick_params(axis="x", rotation=20)


@dataclass(frozen=True)
class BoxFigure(Plot):
    """A row of box plots over the branches of one configuration at the core size."""

    name: str
    config: str
    panels: tuple[Panel, ...]
    figsize: tuple[float, float] = (16, 4.5)

    def render(self, samples: pd.DataFrame) -> Any | None:
        from matplotlib.figure import Figure

        grouped = group_samples(samples, self.config, CORE_SIZE)
        if not grouped:
            return None
        fig = Figure(figsize=self.figsize)
        for ax, panel in zip(fig.subplots(1, len(self.panels)).ravel(), self.panels, strict=True):
            _box(ax, grouped, panel)
        return fig


class WidgetComposition(Plot):
    name = "widgets_after_search"

    def render(self, samples: pd.DataFrame) -> Any | None:
        from matplotlib.figure import Figure

        grouped = group_samples(samples, "search", CORE_SIZE)
        rows = []
        for branch, frame in grouped.items():
            items = frame["items_after_search"].dropna().astype(float)
            placeholders = frame["placeholders_after_search"].dropna().astype(float)
            if items.empty and placeholders.empty:
                continue
            rows.append(
                (
                    label_of(branch),
                    items.mean() if len(items) else 0.0,
                    placeholders.mean() if len(placeholders) else 0.0,
                )
            )
        if not rows:
            return None

        labels, item_means, placeholder_means = zip(*rows, strict=True)
        fig = Figure(figsize=(9, 4.5))
        ax = fig.subplots()
        x = range(len(labels))
        ax.bar(x, item_means, label="real items", color="#4c72b0")
        ax.bar(x, placeholder_means, bottom=item_means, label="placeholders", color="#dd8452")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=20)
        ax.set_ylabel("widgets")
        ax.set_title(f"Widget composition after search (N={CORE_SIZE})")
        ax.legend()
        ax.grid(True, axis="y", alpha=0.3)
        return fig


class OpenScaling(Plot):
    name = "scaling_open"

    def render(self, samples: pd.DataFrame) -> Any | None:
        from matplotlib.figure import Figure

        opened = samples[samples["config"] == "open"]
        sizes = sorted(int(size) for size in opened["size"].dropna().unique())
        if len(sizes) < 2:
            return None

        fig = Figure(figsize=(9, 5))
        ax = fig.subplots()
        for branch in BRANCHES:
            points = []
            for size in sizes:
                values = group_samples(samples, "open", size).get(branch.name)
                if values is None:
                    continue
                series = values["open_first_frame_ms"].dropna().astype(float)
                if not series.empty:
                    points.append((size, series.mean(), series.std(ddof=1) if len(series) > 1 else 0.0))
            if points:
                xs, ys, errors = zip(*points, strict=True)
                ax.errorbar(xs, ys, yerr=errors, marker="o", capsize=4, label=branch.label)
        ax.set_xscale("log")
        ax.set_xticks(sizes)
        ax.set_xticklabels([str(size) for size in sizes])
        ax.set_xlabel("entries")
        ax.set_ylabel("open first frame (ms)")
        ax.set_title("Open latency scaling")
        ax.legend()
        ax.grid(True, alpha=0.3)
        return fig


PLOTS: tuple[Plot, ...] = (
    BoxFigure("open", "open", (
        Panel("open_first_frame_ms", "Open first frame", "ms"),
        Panel("open_animation_ms", "Open animation", "ms"),
        Panel("open_settle_ms", "Open settle", "ms"),
    )),
    BoxFigure("scroll", "scroll", (
        Panel("scroll_first_frame_ms", "Scroll first frame", "ms"),
        Panel("scroll_settle_ms", "Scroll settle", "ms"),
        Panel("scroll_long_frames", "Scroll >50ms frames", "count"),
    )),
    BoxFigure("search_apply", "search", (
        Panel("search_nomatch_apply_ms", "No-match apply", "ms"),
        Panel("search_match_apply_ms", "Match apply", "ms"),
        Panel("search_clear_apply_ms", "Clear apply", "ms"),
    )),
    BoxFigure("search_ready", "search", (
        Panel("search_clear_first_frame_ms", "Clear first frame", "ms"),
        Panel("search_clear_ready_ms", "Clear ready", "ms"),
    ), figsize=(12, 4.5)),
    BoxFigure("memory_cpu", "search", (
        Panel("rss_ready_kb", "RSS after history load", "kB"),
        Panel("rss_end_kb", "RSS at end", "kB"),
        Panel("cpu_total_ms", "CPU used", "ms"),
    )),
    WidgetComposition(),
    OpenScaling(),
)  # fmt: skip


def make_plots(outdir: Path, samples: pd.DataFrame, plots: Sequence[Plot] = PLOTS) -> list[Path]:
    plot_dir = outdir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    def draw(plot: Plot) -> Path | None:
        fig = plot.render(samples)
        if fig is None:
            return None
        fig.set_layout_engine("tight")
        path = plot_dir / f"{plot.name}.png"
        fig.savefig(path, dpi=150)
        return path

    with ThreadPoolExecutor() as pool:
        return [path for path in pool.map(draw, plots) if path is not None]
