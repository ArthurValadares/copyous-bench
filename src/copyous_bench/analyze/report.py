"""Markdown report assembled from independent sections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..branches import BRANCHES, label_of, order_key
from .data import CONFIGS, dataset_rows, group_samples, json_load
from .metrics import CONFIG_SECTIONS, METRIC_SECTIONS
from .stats import fmt_pm, percent_delta, summarize

ENVIRONMENT_FIELDS = (
    ("Host", "hostname"), ("Platform", "platform"), ("Kernel", "kernel"), ("CPU", "cpu_model"),
    ("Logical CPUs", "cpu_logical_count"), ("GNOME Shell", "gnome_shell"), ("GJS", "gjs"),
    ("Node", "node"), ("pnpm", "pnpm"), ("Python", "python"),
)  # fmt: skip


def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], *, right_from: int | None = None) -> list[str]:
    """A Markdown table; columns from ``right_from`` onwards are right-aligned."""
    aligns = ["---" if right_from is None or i < right_from else "---:" for i in range(len(headers))]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(aligns) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return [*lines, ""]


@dataclass
class ReportContext:
    environment: dict[str, Any] | None
    datasets: list[dict[str, Any]]
    samples: pd.DataFrame
    smoke: pd.DataFrame


class Section(ABC):
    @abstractmethod
    def render(self, ctx: ReportContext) -> list[str]: ...


class EnvironmentSection(Section):
    def render(self, ctx: ReportContext) -> list[str]:
        lines = ["## Environment", ""]
        env = ctx.environment
        if not env:
            return [*lines, "No campaign metadata found.", ""]

        rows: list[tuple[str, Any]] = [(label, env.get(key, "—")) for label, key in ENVIRONMENT_FIELDS]
        governors = sorted(set((env.get("governors") or {}).values()))
        rows.append(("CPU governor", ", ".join(governors) if governors else "—"))
        if thermal := env.get("thermal") or []:
            rows.append(("Thermals at start", ", ".join(f"{z['type']}={z['temp_c']:.1f}°C" for z in thermal[:6])))
        commits = env.get("commits") or {}
        rows += [(f"`{b.name}`", f"`{commits[b.name][:12]}`") for b in BRANCHES if b.name in commits]
        return [*lines, *md_table(["Item", "Value"], rows)]


class DatasetsSection(Section):
    def render(self, ctx: ReportContext) -> list[str]:
        if not ctx.datasets:
            return []
        rows = []
        for dataset in ctx.datasets:
            types = json_load(dataset["type_counts"]) or {}
            rows.append(
                (
                    dataset["size"],
                    dataset["entries"],
                    dataset["token_matches"],
                    ", ".join(f"{name} {count}" for name, count in types.items()),
                    f"`{(dataset['sha256'] or '')[:12]}`",
                )
            )
        return ["## Datasets", "", *md_table(["N", "Entries", "Token matches", "Types", "SHA-256"], rows)]


class ResultsSection(Section):
    def render(self, ctx: ReportContext) -> list[str]:
        lines = [
            "## Results", "",
            "All values are mean ± standard deviation over successful samples (warmup excluded).", "",
        ]  # fmt: skip
        for size in sorted(ctx.samples["size"].dropna().unique()):
            for config in CONFIGS:
                grouped = group_samples(ctx.samples, config, size)
                if grouped:
                    lines += self._block(config, int(size), grouped)
        return lines

    @staticmethod
    def _block(config: str, size: int, grouped: dict[str, pd.DataFrame]) -> list[str]:
        lines = [f"### N={size} · {config}", ""]
        for section_name in CONFIG_SECTIONS[config]:
            metrics = METRIC_SECTIONS[section_name]
            rows = [
                [f"`{label_of(branch)}`", len(frame), *(fmt_pm(summarize(frame[m.column])) for m in metrics)]
                for branch, frame in grouped.items()
            ]
            lines += [f"**{section_name}**", ""]
            lines += md_table(["Branch", "n", *(m.label for m in metrics)], rows, right_from=1)

        if "main" in grouped:
            metrics = METRIC_SECTIONS[CONFIG_SECTIONS[config][0]]
            base = {m.column: summarize(grouped["main"][m.column]).mean for m in metrics}
            rows = [
                [
                    f"`{label_of(branch)}`",
                    *(percent_delta(summarize(frame[m.column]).mean, base[m.column]) for m in metrics),
                ]
                for branch, frame in grouped.items()
            ]
            lines += ["**Δ versus main (mean)**", ""]
            lines += md_table(["Branch", *(m.label for m in metrics)], rows, right_from=1)
        return lines


class SmokeSection(Section):
    def render(self, ctx: ReportContext) -> list[str]:
        smoke = ctx.smoke
        if smoke.empty:
            return []
        rows = []
        for branch in sorted(smoke["branch"].dropna().unique(), key=order_key):
            frame = smoke[smoke["branch"] == branch]
            rows.append(
                (
                    f"`{label_of(branch)}`",
                    len(frame),
                    int((frame["status"] != "ok").sum()),
                    fmt_pm(summarize(frame["open_first_frame_ms"])),
                    fmt_pm(summarize(frame["scroll_settle_ms"])),
                    fmt_pm(summarize(frame["search_clear_ready_ms"])),
                )
            )
        headers = ["Branch", "Samples", "Error", "Open first frame (ms)", "Scroll settle (ms)", "Clear ready (ms)"]
        return ["### Smoke gate", "", *md_table(headers, rows, right_from=1)]


SECTIONS: tuple[Section, ...] = (EnvironmentSection(), DatasetsSection(), ResultsSection(), SmokeSection())


def render_markdown(ctx: ReportContext) -> str:
    lines = ["# Copyous branch benchmark", "", f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')}", ""]
    for section in SECTIONS:
        lines += section.render(ctx)
    return "\n".join(lines) + "\n"


def build_context(conn: Any, samples: pd.DataFrame, smoke: pd.DataFrame) -> ReportContext:
    from .data import campaign_environment

    return ReportContext(campaign_environment(conn), dataset_rows(conn), samples, smoke)
