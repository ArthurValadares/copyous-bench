"""Sanity checks on the raw artifacts a block leaves behind."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import json_load
from .plan import Block

Phases = dict[str, Any]
PhaseCheck = Callable[["BlockValidator", Block, str, Phases], list[str]]


@dataclass
class ValidationResult:
    problems: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


class BlockValidator:
    def __init__(self, expected_entries: int, token_matches: int) -> None:
        self.expected_entries = expected_entries
        self.token_matches = token_matches

    # -- phase checks -------------------------------------------------------
    def _check_startup(self, block: Block, name: str, phases: Phases) -> list[str]:
        startup = phases.get("startup", {})
        problems = []
        if not startup.get("data_ok"):
            problems.append(f"{name}: history did not load")
        if startup.get("data_count") != self.expected_entries:
            problems.append(f"{name}: data_count {startup.get('data_count')} != {self.expected_entries}")
        return problems

    def _check_open(self, block: Block, name: str, phases: Phases) -> list[str]:
        opened = phases.get("open", {})
        checks = (
            (opened.get("opened"), "dialog did not open"),
            (opened.get("grabbed"), "dialog did not grab input"),
            (opened.get("first_frame_ms") is not None, "no open frame"),
        )
        return [f"{name}: {message}" for ok, message in checks if not ok]

    def _check_scroll(self, block: Block, name: str, phases: Phases) -> list[str]:
        if block.config not in ("scroll", "all"):
            return []
        scroll = phases.get("scroll", {})
        if not scroll:
            return [f"{name}: scroll phase missing"]
        if scroll.get("first_frame_ms") is None:
            return [f"{name}: no scroll frame"]
        return []

    def _check_search(self, block: Block, name: str, phases: Phases) -> list[str]:
        if block.config not in ("search", "all"):
            return []
        search = phases.get("search", {})
        if not search:
            return [f"{name}: search phase missing"]
        problems = []
        for step_name in ("nomatch", "match", "clear"):
            step = search.get(step_name, {})
            if step.get("result_count") != step.get("expected"):
                problems.append(
                    f"{name}: search {step_name} count {step.get('result_count')} != {step.get('expected')}"
                )
            if not step.get("ready_ok"):
                problems.append(f"{name}: search {step_name} did not become ready")
        return problems

    CHECKS: tuple[PhaseCheck, ...] = (_check_startup, _check_open, _check_scroll, _check_search)

    # -- orchestration ------------------------------------------------------
    def _check_sidecar(self, block: Block, name: str, sidecar: dict[str, Any]) -> list[str]:
        if sidecar.get("error"):
            return [f"{name} error: {sidecar['error']}"]
        phases = sidecar.get("phases", {})
        return [problem for check in self.CHECKS for problem in check(self, block, name, phases)]

    @staticmethod
    def _check_report(report: dict[str, Any]) -> list[str]:
        values = report.get("metrics", {}).get("copyousError", {}).get("values", [])
        return [f"report copyousError values: {values}"] if values and any(v != 0 for v in values) else []

    def validate(self, block: Block, outdir: Path) -> ValidationResult:
        report_path = outdir / "report.json"
        sidecar_paths = sorted(outdir.glob("sidecar-*.json"))
        problems: list[str] = []
        report: dict[str, Any] | None = None

        if not report_path.exists():
            problems.append("report.json missing")
        else:
            try:
                report = json_load(report_path)
            except (OSError, json.JSONDecodeError) as error:
                problems.append(f"report.json unreadable: {error}")

        if len(sidecar_paths) < block.iterations:
            problems.append(f"expected {block.iterations} sidecars, found {len(sidecar_paths)}")

        measured = False
        for path in sidecar_paths:
            try:
                sidecar = json_load(path)
            except (OSError, json.JSONDecodeError) as error:
                problems.append(f"{path.name} unreadable: {error}")
                continue
            found = self._check_sidecar(block, path.name, sidecar)
            problems += found
            measured = measured or (not sidecar.get("warmup") and not sidecar.get("error"))

        if measured and report is not None:
            problems += self._check_report(report)

        return ValidationResult(
            problems=problems,
            summary={
                "report": report_path.exists(),
                "sidecars": len(sidecar_paths),
                "expected_sidecars": block.iterations,
                "errors": list(problems),
                "expected_entries": self.expected_entries,
                "token_matches": self.token_matches,
            },
        )
