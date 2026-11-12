"""SQLite repository for benchmark results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from ..util import json_load
from . import schema
from .extract import BlockData, iter_frame_phases, sidecar_metrics


class ResultsDatabase:
    """Rebuilds the database from scratch; use as a context manager."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(schema.schema_sql())

    def __enter__(self) -> ResultsDatabase:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def _insert(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
        placeholders = ", ".join("?" * len(columns))
        self.conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", list(rows)
        )

    # -- campaign and datasets --------------------------------------------
    def insert_campaign(self, workdir: Path) -> str | None:
        campaign_path = workdir / "campaign.json"
        environment_path = workdir / "environment.json"
        campaign: dict[str, Any] = {}
        if campaign_path.exists():
            campaign = json_load(campaign_path)
        elif environment_path.exists():
            campaign = {"environment": json_load(environment_path)}

        campaign_id = campaign.get("campaign_id")
        if not campaign_id:
            return None
        self._insert(
            "campaign",
            schema.CAMPAIGN_COLUMNS,
            [
                (
                    campaign_id,
                    campaign.get("started_at"),
                    campaign.get("finished_at"),
                    json.dumps(campaign.get("environment"), sort_keys=True),
                    json.dumps(campaign.get("datasets"), sort_keys=True),
                    campaign.get("settings"),
                )
            ],
        )
        return campaign_id

    def insert_datasets(self, workdir: Path) -> list[int]:
        path = workdir / "datasets" / "datasets.json"
        if not path.exists():
            return []
        sizes = json_load(path).get("sizes", {})
        rows = []
        for size_text, entry in sizes.items():
            rows.append(
                (
                    int(size_text),
                    entry.get("path"),
                    entry.get("sha256"),
                    entry.get("entries"),
                    json.dumps(entry.get("type_counts"), sort_keys=True),
                    entry.get("token"),
                    entry.get("token_matches"),
                    entry.get("no_match_token"),
                    entry.get("newest_datetime"),
                    entry.get("oldest_datetime"),
                )
            )
        self._insert("dataset", schema.DATASET_COLUMNS, rows)
        return sorted(int(size) for size in sizes)

    # -- runs ---------------------------------------------------------------
    def insert_block(self, block: BlockData, campaign_id: str | None) -> int:
        meta = block.meta
        block_info = meta.get("block", {})
        block_id = meta.get("run_id") or block_info.get("run_id") or block.outdir.name
        block_warmup = bool(block_info.get("warmup"))
        inserted = 0

        for sidecar_path, sidecar in block.sidecars:
            iteration = sidecar.get("iteration")
            if iteration is None:
                continue
            run_id = f"{block_id}#i{int(iteration):02d}"
            error = sidecar.get("error")
            identity = {
                "run_id": run_id,
                "block_id": block_id,
                "campaign_id": campaign_id,
                "branch": sidecar.get("branch") or block_info.get("branch"),
                "commit_hash": sidecar.get("commit") or meta.get("commit"),
                "config": sidecar.get("config") or block_info.get("config"),
                "size": sidecar.get("size") or block_info.get("size"),
                "iteration": iteration,
                "warmup": 1 if sidecar.get("warmup") else 0,
                "started_at": sidecar.get("started_at") or meta.get("started_at"),
                "status": "ok" if not error and meta.get("status") == "ok" else "failed",
                "error": error,
                "sidecar_path": str(sidecar_path),
                "report_path": str(block.report_path),
                "meta_path": str(block.meta_path),
            }
            metrics = sidecar_metrics(sidecar)
            columns = [name for name, _ in schema.IDENTITY_COLUMNS] + [c.name for c in schema.METRIC_COLUMNS]
            values = [identity[name] for name, _ in schema.IDENTITY_COLUMNS]
            values += [column.extract(metrics) for column in schema.METRIC_COLUMNS]
            self._insert("run", columns, [values])

            phases = sidecar.get("phases", {})
            self._insert_phase_metrics(run_id, phases)
            self._insert_checkpoints(run_id, sidecar.get("checkpoints", []))
            self._insert_frames(run_id, phases)

            sample_index = int(iteration) - (1 if block_warmup else 0)
            self._insert_report_metrics(run_id, block.report, sample_index if sample_index >= 0 else None)
            inserted += 1
        return inserted

    def _insert_phase_metrics(self, run_id: str, phases: dict[str, Any]) -> None:
        from .extract import flatten

        rows: list[tuple[str, str, str, float]] = []
        for phase_name, phase in phases.items():
            values: dict[str, float] = {}
            flatten("", phase, values)
            rows.extend((run_id, phase_name, name, value) for name, value in values.items())
        self._insert("metric", ("run_id", "phase", "name", "value"), rows)

    def _insert_checkpoints(self, run_id: str, checkpoints: list[dict[str, Any]]) -> None:
        columns = ("run_id", *schema.CHECKPOINT_COLUMNS)
        self._insert(
            "checkpoint", columns, [(run_id, *(item.get(c) for c in schema.CHECKPOINT_COLUMNS)) for item in checkpoints]
        )

    def _insert_frames(self, run_id: str, phases: dict[str, Any]) -> None:
        frame_rows: list[tuple[Any, ...]] = []
        summary_rows: list[tuple[Any, ...]] = []
        for phase_name, phase in iter_frame_phases(phases):
            frames = phase.get("frames_ms")
            if isinstance(frames, list):
                frame_rows.extend((run_id, phase_name, index, value) for index, value in enumerate(frames))
            summary = phase.get("frame_summary")
            if isinstance(summary, dict) and summary:
                summary_rows.append((run_id, phase_name, *(summary.get(c) for c in schema.FRAME_SUMMARY_COLUMNS)))
        self._insert("frame", ("run_id", "phase", "seq", "t_ms"), frame_rows)
        self._insert("frame_summary", ("run_id", "phase", *schema.FRAME_SUMMARY_COLUMNS), summary_rows)

    def _insert_report_metrics(self, run_id: str, report: dict[str, Any] | None, sample_index: int | None) -> None:
        if not report or sample_index is None:
            return
        rows = []
        for name, metric in report.get("metrics", {}).items():
            values = metric.get("values", [])
            if 0 <= sample_index < len(values):
                rows.append((run_id, name, metric.get("description"), metric.get("units"), values[sample_index]))
        self._insert("report_metric", ("run_id", "name", "description", "units", "value"), rows)
