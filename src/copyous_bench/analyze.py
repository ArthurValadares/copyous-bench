"""Analyse the benchmark database and write CSV, Markdown and plots."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import statistics
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT

DEFAULT_RESULTS = Path(os.environ.get("COPYOUS_BENCH_RESULTS", PROJECT_ROOT / "results")).expanduser()
DEFAULT_DB = DEFAULT_RESULTS / "results.db"
DEFAULT_OUTDIR = DEFAULT_RESULTS

BRANCH_LABELS = {
    "main": "main",
    "feat/clipboard-item-placeholders": "placeholders",
    "feat/lazy-clipboard-items": "lazy",
    "perf/cheaper-cached-async-items": "cached-async",
    "merge/clipboard-item-placeholders-cached-async-items": "merge",
}
BRANCH_ORDER = [
    "main",
    "feat/clipboard-item-placeholders",
    "feat/lazy-clipboard-items",
    "perf/cheaper-cached-async-items",
    "merge/clipboard-item-placeholders-cached-async-items",
]

HEADLINE_COLUMNS = [
    "run_id",
    "branch",
    "config",
    "size",
    "iteration",
    "startup_ready_ms",
    "startup_to_run_ms",
    "open_first_frame_ms",
    "open_animation_ms",
    "open_settle_ms",
    "scroll_first_frame_ms",
    "scroll_settle_ms",
    "scroll_passes",
    "scroll_long_frames",
    "scroll_fps",
    "search_nomatch_apply_ms",
    "search_match_apply_ms",
    "search_clear_apply_ms",
    "search_clear_first_frame_ms",
    "search_clear_ready_ms",
    "search_match_results",
    "search_clear_results",
    "rss_ready_kb",
    "rss_end_kb",
    "hwm_end_kb",
    "cpu_total_ms",
    "actors_ready",
    "actors_end",
    "data_entries",
    "items_ready",
    "placeholders_ready",
    "items_end",
    "placeholders_end",
    "items_after_scroll",
    "placeholders_after_scroll",
    "items_after_search",
    "placeholders_after_search",
]

METRIC_SECTIONS = {
    "Startup & open": [
        ("startup_ready_ms", "startup ready (ms)"),
        ("startup_to_run_ms", "init→run (ms)"),
        ("open_first_frame_ms", "open first frame (ms)"),
        ("open_animation_ms", "open animation (ms)"),
        ("open_settle_ms", "open settle (ms)"),
    ],
    "Scroll": [
        ("scroll_first_frame_ms", "scroll first frame (ms)"),
        ("scroll_settle_ms", "scroll settle (ms)"),
        ("scroll_passes", "scroll passes"),
        ("scroll_long_frames", ">50ms frames"),
        ("scroll_fps", "scroll fps"),
    ],
    "Search": [
        ("search_nomatch_apply_ms", "no-match apply (ms)"),
        ("search_match_apply_ms", "match apply (ms)"),
        ("search_clear_apply_ms", "clear apply (ms)"),
        ("search_clear_first_frame_ms", "clear first frame (ms)"),
        ("search_clear_ready_ms", "clear ready (ms)"),
    ],
    "Memory & CPU": [
        ("rss_ready_kb", "RSS ready (kB)"),
        ("rss_end_kb", "RSS end (kB)"),
        ("hwm_end_kb", "RSS peak (kB)"),
        ("cpu_total_ms", "CPU total (ms)"),
        ("actors_ready", "actors ready"),
        ("actors_end", "actors end"),
    ],
    "Widgets": [
        ("items_ready", "items ready"),
        ("placeholders_ready", "placeholders ready"),
        ("items_after_scroll", "items after scroll"),
        ("placeholders_after_scroll", "placeholders after scroll"),
        ("items_after_search", "items after search"),
        ("placeholders_after_search", "placeholders after search"),
    ],
}

CONFIG_SECTIONS = {
    "open": ["Startup & open", "Memory & CPU"],
    "scroll": ["Startup & open", "Scroll", "Widgets", "Memory & CPU"],
    "search": ["Startup & open", "Search", "Widgets", "Memory & CPU"],
}


def json_load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def summarize(values: Iterable[float | int | None]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"n": 0, "mean": None, "std": None, "median": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(clean),
        "mean": statistics.fmean(clean),
        "std": statistics.stdev(clean) if len(clean) > 1 else 0.0,
        "median": statistics.median(clean),
        "p95": percentile(clean, 0.95),
        "min": min(clean),
        "max": max(clean),
    }


def fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return f"{value:,}"


def fmt_pm(summary: dict[str, Any], digits: int = 1) -> str:
    if summary["n"] == 0:
        return "—"
    if summary["n"] == 1:
        return fmt(summary["mean"], digits)
    return f"{fmt(summary['mean'], digits)} ± {fmt(summary['std'], digits)}"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_samples(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
		SELECT * FROM run
		WHERE warmup = 0 AND status = 'ok' AND config IN ('open', 'scroll', 'search')
		ORDER BY config, size, branch, iteration
		"""
    ).fetchall()
    return [dict(row) for row in rows]


def load_smoke(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM run WHERE warmup = 0 AND config = 'all' ORDER BY branch, iteration").fetchall()
    return [dict(row) for row in rows]


def branch_sort(branch: str) -> int:
    try:
        return BRANCH_ORDER.index(branch)
    except ValueError:
        return len(BRANCH_ORDER)


def group_samples(samples: list[dict[str, Any]], config: str, size: int) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        if sample["config"] != config or sample["size"] != size:
            continue
        grouped.setdefault(sample["branch"], []).append(sample)
    return dict(sorted(grouped.items(), key=lambda item: branch_sort(item[0])))


def write_csv(path: Path, samples: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADLINE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for sample in samples:
            writer.writerow(sample)


def dataset_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM dataset ORDER BY size").fetchall()]


def campaign_row(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM campaign LIMIT 1").fetchone()
    return dict(row) if row else None


def render_markdown(
    path: Path,
    conn: sqlite3.Connection,
    samples: list[dict[str, Any]],
    smoke: list[dict[str, Any]],
) -> None:
    lines: list[str] = []
    add = lines.append

    campaign = campaign_row(conn)
    environment = json_load(campaign.get("environment")) if campaign else None
    datasets = dataset_rows(conn)

    add("# Copyous branch benchmark")
    add("")
    add(f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')}")
    add("")

    add("## Environment")
    add("")
    if environment:
        add("| Item | Value |")
        add("| --- | --- |")
        for label, key in [
            ("Host", "hostname"),
            ("Platform", "platform"),
            ("Kernel", "kernel"),
            ("CPU", "cpu_model"),
            ("Logical CPUs", "cpu_logical_count"),
            ("GNOME Shell", "gnome_shell"),
            ("GJS", "gjs"),
            ("Node", "node"),
            ("pnpm", "pnpm"),
            ("Python", "python"),
        ]:
            add(f"| {label} | {environment.get(key, '—')} |")
        governors = environment.get("governors") or {}
        governor_values = sorted(set(governors.values()))
        add(f"| CPU governor | {', '.join(governor_values) if governor_values else '—'} |")
        thermal = environment.get("thermal") or []
        if thermal:
            temps = ", ".join(f"{zone['type']}={zone['temp_c']:.1f}°C" for zone in thermal[:6])
            add(f"| Thermals at start | {temps} |")
        commits = environment.get("commits") or {}
        for branch in BRANCH_ORDER:
            if branch in commits:
                add(f"| `{branch}` | `{commits[branch][:12]}` |")
    else:
        add("No campaign metadata found.")
    add("")

    if datasets:
        add("## Datasets")
        add("")
        add("| N | Entries | Token matches | Types | SHA-256 |")
        add("| --- | --- | --- | --- | --- |")
        for dataset in datasets:
            types = json_load(dataset["type_counts"]) or {}
            type_text = ", ".join(f"{name} {count}" for name, count in types.items())
            add(
                f"| {dataset['size']} | {dataset['entries']} | {dataset['token_matches']} | "
                f"{type_text} | `{(dataset['sha256'] or '')[:12]}` |"
            )
        add("")

    add("## Results")
    add("")
    add("All values are mean ± standard deviation over successful samples (warmup excluded).")
    add("")

    for size in sorted({sample["size"] for sample in samples}):
        for config in ["open", "scroll", "search"]:
            grouped = group_samples(samples, config, size)
            if not grouped:
                continue
            add(f"### N={size} · {config}")
            add("")
            for section_name in CONFIG_SECTIONS[config]:
                metrics = METRIC_SECTIONS[section_name]
                add(f"**{section_name}**")
                add("")
                header = "| Branch | n | " + " | ".join(label for _, label in metrics) + " |"
                separator = "| --- | ---: | " + " | ".join("---:" for _ in metrics) + " |"
                add(header)
                add(separator)
                for branch, branch_samples in grouped.items():
                    cells = [f"`{BRANCH_LABELS.get(branch, branch)}`", str(len(branch_samples))]
                    for column, _ in metrics:
                        cells.append(fmt_pm(summarize(sample.get(column) for sample in branch_samples)))
                    add("| " + " | ".join(cells) + " |")
                add("")

            # Deltas versus main for the section's headline metrics.
            main_samples = grouped.get("main", [])
            if main_samples:
                add("**Δ versus main (mean)**")
                add("")
                delta_section = METRIC_SECTIONS[CONFIG_SECTIONS[config][0]]
                delta_columns = [column for column, _ in delta_section]
                add("| Branch | " + " | ".join(label for _, label in delta_section) + " |")
                add("| --- | " + " | ".join("---:" for _ in delta_columns) + " |")
                main_means = {
                    column: summarize(sample.get(column) for sample in main_samples)["mean"] for column in delta_columns
                }
                for branch, branch_samples in grouped.items():
                    cells = [f"`{BRANCH_LABELS.get(branch, branch)}`"]
                    for column in delta_columns:
                        mean = summarize(sample.get(column) for sample in branch_samples)["mean"]
                        base = main_means.get(column)
                        if mean is None or base in (None, 0):
                            cells.append("—")
                        else:
                            cells.append(f"{(mean - base) / base * 100:+.1f}%")
                    add("| " + " | ".join(cells) + " |")
                add("")

    if smoke:
        add("### Smoke gate")
        add("")
        add("| Branch | Samples | Error | Open first frame (ms) | Scroll settle (ms) | Clear ready (ms) |")
        add("| --- | ---: | ---: | ---: | ---: | ---: |")
        for branch in sorted({sample["branch"] for sample in smoke}, key=branch_sort):
            branch_samples = [sample for sample in smoke if sample["branch"] == branch]
            errors = sum(1 for sample in branch_samples if sample["status"] != "ok")
            add(
                f"| `{BRANCH_LABELS.get(branch, branch)}` | {len(branch_samples)} | {errors} | "
                f"{fmt_pm(summarize(sample.get('open_first_frame_ms') for sample in branch_samples))} | "
                f"{fmt_pm(summarize(sample.get('scroll_settle_ms') for sample in branch_samples))} | "
                f"{fmt_pm(summarize(sample.get('search_clear_ready_ms') for sample in branch_samples))} |"
            )
        add("")

    path.write_text("\n".join(lines) + "\n")


def plot_branch_box(
    ax: Any,
    grouped: dict[str, list[dict[str, Any]]],
    metric: str,
    title: str,
    ylabel: str,
) -> None:
    labels: list[str] = []
    data: list[list[float]] = []
    for branch, branch_samples in grouped.items():
        values = [float(sample[metric]) for sample in branch_samples if sample.get(metric) is not None]
        if not values:
            continue
        labels.append(BRANCH_LABELS.get(branch, branch))
        data.append(values)
    if not data:
        ax.set_visible(False)
        return
    ax.boxplot(data, tick_labels=labels, showmeans=True)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", alpha=0.3)
    ax.tick_params(axis="x", rotation=20)


def make_plots(outdir: Path, samples: list[dict[str, Any]]) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as error:  # pragma: no cover - optional dependency
        print(f"matplotlib unavailable, skipping plots: {error}")
        return []

    plot_dir = outdir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def save(fig: Any, name: str) -> None:
        path = plot_dir / name
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
        written.append(path)

    # Open metrics at N=500.
    grouped_open = group_samples(samples, "open", 500)
    if grouped_open:
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
        plot_branch_box(axes[0], grouped_open, "open_first_frame_ms", "Open first frame", "ms")
        plot_branch_box(axes[1], grouped_open, "open_animation_ms", "Open animation", "ms")
        plot_branch_box(axes[2], grouped_open, "open_settle_ms", "Open settle", "ms")
        save(fig, "open.png")

    # Scroll metrics.
    grouped_scroll = group_samples(samples, "scroll", 500)
    if grouped_scroll:
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
        plot_branch_box(axes[0], grouped_scroll, "scroll_first_frame_ms", "Scroll first frame", "ms")
        plot_branch_box(axes[1], grouped_scroll, "scroll_settle_ms", "Scroll settle", "ms")
        plot_branch_box(axes[2], grouped_scroll, "scroll_long_frames", "Scroll >50ms frames", "count")
        save(fig, "scroll.png")

    # Search metrics.
    grouped_search = group_samples(samples, "search", 500)
    if grouped_search:
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
        plot_branch_box(axes[0], grouped_search, "search_nomatch_apply_ms", "No-match apply", "ms")
        plot_branch_box(axes[1], grouped_search, "search_match_apply_ms", "Match apply", "ms")
        plot_branch_box(axes[2], grouped_search, "search_clear_apply_ms", "Clear apply", "ms")
        save(fig, "search_apply.png")

        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
        plot_branch_box(axes[0], grouped_search, "search_clear_first_frame_ms", "Clear first frame", "ms")
        plot_branch_box(axes[1], grouped_search, "search_clear_ready_ms", "Clear ready", "ms")
        save(fig, "search_ready.png")

    # Memory and CPU on the full-lifecycle (search) configuration.
    if grouped_search:
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
        plot_branch_box(axes[0], grouped_search, "rss_ready_kb", "RSS after history load", "kB")
        plot_branch_box(axes[1], grouped_search, "rss_end_kb", "RSS at end", "kB")
        plot_branch_box(axes[2], grouped_search, "cpu_total_ms", "CPU used", "ms")
        save(fig, "memory_cpu.png")

        # Widget composition after the search lifecycle.
        labels: list[str] = []
        items: list[float] = []
        placeholders: list[float] = []
        for branch, branch_samples in grouped_search.items():
            item_values = [
                float(s["items_after_search"]) for s in branch_samples if s.get("items_after_search") is not None
            ]
            placeholder_values = [
                float(s["placeholders_after_search"])
                for s in branch_samples
                if s.get("placeholders_after_search") is not None
            ]
            if not item_values and not placeholder_values:
                continue
            labels.append(BRANCH_LABELS.get(branch, branch))
            items.append(statistics.fmean(item_values) if item_values else 0.0)
            placeholders.append(statistics.fmean(placeholder_values) if placeholder_values else 0.0)

        if labels:
            fig, ax = plt.subplots(figsize=(9, 4.5))
            x = range(len(labels))
            ax.bar(x, items, label="real items", color="#4c72b0")
            ax.bar(x, placeholders, bottom=items, label="placeholders", color="#dd8452")
            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, rotation=20)
            ax.set_ylabel("widgets")
            ax.set_title("Widget composition after search (N=500)")
            ax.legend()
            ax.grid(True, axis="y", alpha=0.3)
            save(fig, "widgets_after_search.png")

    # Open scaling across N.
    sizes = sorted({sample["size"] for sample in samples if sample["config"] == "open"})
    if len(sizes) > 1:
        fig, ax = plt.subplots(figsize=(9, 5))
        for branch in BRANCH_ORDER:
            points: list[tuple[int, float, float]] = []
            for size in sizes:
                grouped = group_samples(samples, "open", size)
                branch_samples = grouped.get(branch, [])
                values = [
                    float(s["open_first_frame_ms"]) for s in branch_samples if s.get("open_first_frame_ms") is not None
                ]
                if not values:
                    continue
                mean = statistics.fmean(values)
                std = statistics.stdev(values) if len(values) > 1 else 0.0
                points.append((size, mean, std))
            if not points:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            errors = [point[2] for point in points]
            ax.errorbar(xs, ys, yerr=errors, marker="o", capsize=4, label=BRANCH_LABELS.get(branch, branch))
        ax.set_xscale("log")
        ax.set_xticks(sizes)
        ax.set_xticklabels([str(size) for size in sizes])
        ax.set_xlabel("entries")
        ax.set_ylabel("open first frame (ms)")
        ax.set_title("Open latency scaling")
        ax.legend()
        ax.grid(True, alpha=0.3)
        save(fig, "scaling_open.png")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    db_path = args.db.resolve()
    outdir = args.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    conn = connect(db_path)
    try:
        samples = load_samples(conn)
        smoke = load_smoke(conn)
        if not samples:
            raise SystemExit("No successful samples found in the database")

        write_csv(outdir / "summary.csv", samples)
        render_markdown(outdir / "summary.md", conn, samples, smoke)

        if not args.no_plots:
            plots = make_plots(outdir, samples)
            for path in plots:
                print(f"plot: {path}")

        print(f"csv: {outdir / 'summary.csv'}")
        print(f"markdown: {outdir / 'summary.md'}")
        print(f"samples: {len(samples)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
