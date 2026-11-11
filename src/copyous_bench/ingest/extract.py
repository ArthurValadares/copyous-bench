"""Read the raw artifacts of one block and flatten sidecars into metric maps."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import json_load

SKIPPED_KEYS = frozenset({"frames_ms"})


def flatten(prefix: str, value: Any, out: dict[str, float]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in SKIPPED_KEYS:
                flatten(f"{prefix}.{key}" if prefix else str(key), child, out)
    elif isinstance(value, bool):
        out[prefix] = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        out[prefix] = float(value)


def sidecar_metrics(sidecar: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    flatten("", sidecar.get("phases", {}), metrics)
    final = sidecar.get("final")
    if isinstance(final, dict):
        flatten("final", final, metrics)
    return metrics


def iter_frame_phases(phases: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(phase_name, phase)`` pairs that may carry frames; search is split per step."""
    for phase_name, phase in phases.items():
        if not isinstance(phase, dict):
            continue
        if phase_name == "search":
            for step_name, step in phase.items():
                if isinstance(step, dict):
                    yield f"search.{step_name}", step
        else:
            yield phase_name, phase


@dataclass
class BlockData:
    outdir: Path
    meta: dict[str, Any]
    report: dict[str, Any] | None
    sidecars: list[tuple[Path, dict[str, Any]]] = field(default_factory=list)

    @property
    def meta_path(self) -> Path:
        return self.outdir / "meta.json"

    @property
    def report_path(self) -> Path:
        return self.outdir / "report.json"


def load_block(outdir: Path) -> BlockData | None:
    """Parse a block directory; returns ``None`` when it has no readable ``meta.json``."""
    meta_path = outdir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json_load(meta_path)
    except (OSError, json.JSONDecodeError):
        return None

    report = None
    if (outdir / "report.json").exists():
        try:
            report = json_load(outdir / "report.json")
        except (OSError, json.JSONDecodeError):
            report = None

    sidecars: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(outdir.glob("sidecar-*.json")):
        try:
            sidecars.append((path, json_load(path)))
        except (OSError, json.JSONDecodeError):
            continue
    return BlockData(outdir, meta, report, sidecars)
