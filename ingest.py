#!/usr/bin/env python3
"""Ingest benchmark raw artifacts into a SQLite database.

Raw data lives in ``<workdir>/runs/<run-id>/``: one ``meta.json`` per block,
one ``context.json``, one ``report.json`` from gnome-shell-test-tool and one
``sidecar-*.json`` per shell launch (including the warmup launch).

The database is rebuilt from scratch on every invocation so it always matches
the raw artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_WORKDIR = Path(
	os.environ.get("COPYOUS_BENCH_WORKDIR", Path(tempfile.gettempdir()) / "copyous-bench")
).expanduser()
DEFAULT_RESULTS = Path(os.environ.get("COPYOUS_BENCH_RESULTS", BENCH_DIR / "results")).expanduser()
DEFAULT_DB = DEFAULT_RESULTS / "results.db"


def json_load(path: Path) -> Any:
	return json.loads(path.read_text())


def flatten(prefix: str, value: Any, out: dict[str, float]) -> None:
	if isinstance(value, dict):
		for key, child in value.items():
			if key in ("frames_ms",):
				continue
			flatten(f"{prefix}.{key}" if prefix else str(key), child, out)
	elif isinstance(value, bool):
		out[prefix] = 1.0 if value else 0.0
	elif isinstance(value, (int, float)):
		out[prefix] = float(value)


SCHEMA = """
PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS campaign;
DROP TABLE IF EXISTS dataset;
DROP TABLE IF EXISTS run;
DROP TABLE IF EXISTS metric;
DROP TABLE IF EXISTS frame;
DROP TABLE IF EXISTS frame_summary;
DROP TABLE IF EXISTS checkpoint;
DROP TABLE IF EXISTS report_metric;

CREATE TABLE campaign (
	campaign_id   TEXT PRIMARY KEY,
	started_at    TEXT,
	finished_at   TEXT,
	environment   TEXT,
	datasets      TEXT,
	settings      TEXT
);

CREATE TABLE dataset (
	size          INTEGER PRIMARY KEY,
	path          TEXT,
	sha256        TEXT,
	entries       INTEGER,
	type_counts   TEXT,
	token         TEXT,
	token_matches INTEGER,
	no_match_token TEXT,
	newest_datetime TEXT,
	oldest_datetime TEXT
);

CREATE TABLE run (
	run_id        TEXT PRIMARY KEY,
	block_id      TEXT,
	campaign_id   TEXT,
	branch        TEXT,
	commit_hash   TEXT,
	config        TEXT,
	size          INTEGER,
	iteration     INTEGER,
	warmup        INTEGER,
	started_at    TEXT,
	status        TEXT,
	error         TEXT,
	sidecar_path  TEXT,
	report_path   TEXT,
	meta_path     TEXT,

	startup_ready_ms          REAL,
	startup_to_run_ms         REAL,
	open_first_frame_ms       REAL,
	open_animation_ms         REAL,
	open_settle_ms            REAL,
	scroll_first_frame_ms     REAL,
	scroll_settle_ms          REAL,
	scroll_passes             INTEGER,
	scroll_long_frames        INTEGER,
	scroll_fps                REAL,
	search_nomatch_apply_ms   REAL,
	search_match_apply_ms     REAL,
	search_clear_apply_ms     REAL,
	search_clear_first_frame_ms REAL,
	search_clear_ready_ms     REAL,
	search_match_results      INTEGER,
	search_clear_results      INTEGER,
	rss_ready_kb              INTEGER,
	rss_end_kb                INTEGER,
	hwm_end_kb                INTEGER,
	cpu_total_ms              REAL,
	actors_ready              INTEGER,
	actors_end                INTEGER,
	data_entries              INTEGER,
	items_ready               INTEGER,
	placeholders_ready        INTEGER,
	items_end                 INTEGER,
	placeholders_end          INTEGER,
	items_after_scroll        INTEGER,
	placeholders_after_scroll INTEGER,
	items_after_search        INTEGER,
	placeholders_after_search INTEGER
);

CREATE TABLE metric (
	run_id  TEXT,
	phase   TEXT,
	name    TEXT,
	value   REAL,
	PRIMARY KEY (run_id, phase, name)
);

CREATE TABLE frame (
	run_id  TEXT,
	phase   TEXT,
	seq     INTEGER,
	t_ms    REAL,
	PRIMARY KEY (run_id, phase, seq)
);

CREATE TABLE frame_summary (
	run_id             TEXT,
	phase              TEXT,
	frames             INTEGER,
	duration_ms        REAL,
	first_ms           REAL,
	last_ms            REAL,
	mean_interval_ms   REAL,
	median_interval_ms REAL,
	p95_interval_ms    REAL,
	max_interval_ms    REAL,
	long_50            INTEGER,
	long_100           INTEGER,
	fps                REAL,
	PRIMARY KEY (run_id, phase)
);

CREATE TABLE checkpoint (
	run_id        TEXT,
	name          TEXT,
	t_ms          REAL,
	rss_kb        INTEGER,
	hwm_kb        INTEGER,
	threads       INTEGER,
	cpu_ms        REAL,
	actors        INTEGER,
	children      INTEGER,
	items         INTEGER,
	placeholders  INTEGER,
	status_items  INTEGER,
	result_count  INTEGER,
	data_count    INTEGER,
	PRIMARY KEY (run_id, name)
);

CREATE TABLE report_metric (
	run_id      TEXT,
	name        TEXT,
	description TEXT,
	units       TEXT,
	value       REAL,
	PRIMARY KEY (run_id, name)
);
"""


def get(mapping: dict[str, float], key: str) -> float | None:
	return mapping.get(key)


def first_frame(mapping: dict[str, float], prefix: str) -> float | None:
	"""Return a phase's first-frame time, falling back to its frame summary.

	Older sidecars (recorded before the automation script waited for the first
	frame) have a null ``first_frame_ms`` but still carry the measured first
	frame in ``frame_summary.first_ms``.
	"""
	value = mapping.get(f"{prefix}.first_frame_ms")
	if value is None:
		value = mapping.get(f"{prefix}.frame_summary.first_ms")
	return value


def as_int(value: float | None) -> int | None:
	if value is None:
		return None
	return int(round(value))


def insert_campaign(conn: sqlite3.Connection, workdir: Path) -> str | None:
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

	conn.execute(
		"INSERT INTO campaign (campaign_id, started_at, finished_at, environment, datasets, settings) VALUES (?, ?, ?, ?, ?, ?)",
		(
			campaign_id,
			campaign.get("started_at"),
			campaign.get("finished_at"),
			json.dumps(campaign.get("environment"), sort_keys=True),
			json.dumps(campaign.get("datasets"), sort_keys=True),
			campaign.get("settings"),
		),
	)
	return campaign_id


def insert_datasets(conn: sqlite3.Connection, workdir: Path) -> dict[int, dict[str, Any]]:
	path = workdir / "datasets" / "datasets.json"
	if not path.exists():
		return {}
	manifest = json_load(path)
	result: dict[int, dict[str, Any]] = {}
	for size_text, entry in manifest.get("sizes", {}).items():
		size = int(size_text)
		result[size] = entry
		conn.execute(
			"""
			INSERT INTO dataset (size, path, sha256, entries, type_counts, token, token_matches,
				no_match_token, newest_datetime, oldest_datetime)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				size,
				entry.get("path"),
				entry.get("sha256"),
				entry.get("entries"),
				json.dumps(entry.get("type_counts"), sort_keys=True),
				entry.get("token"),
				entry.get("token_matches"),
				entry.get("no_match_token"),
				entry.get("newest_datetime"),
				entry.get("oldest_datetime"),
			),
		)
	return result


def insert_frames(conn: sqlite3.Connection, run_id: str, phase: str, frames: list[float]) -> None:
	conn.executemany(
		"INSERT INTO frame (run_id, phase, seq, t_ms) VALUES (?, ?, ?, ?)",
		[(run_id, phase, index, value) for index, value in enumerate(frames)],
	)


def insert_frame_summary(conn: sqlite3.Connection, run_id: str, phase: str, summary: dict[str, Any]) -> None:
	if not summary:
		return
	conn.execute(
		"""
		INSERT INTO frame_summary (run_id, phase, frames, duration_ms, first_ms, last_ms,
			mean_interval_ms, median_interval_ms, p95_interval_ms, max_interval_ms,
			long_50, long_100, fps)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			run_id,
			phase,
			summary.get("frames"),
			summary.get("duration_ms"),
			summary.get("first_ms"),
			summary.get("last_ms"),
			summary.get("mean_interval_ms"),
			summary.get("median_interval_ms"),
			summary.get("p95_interval_ms"),
			summary.get("max_interval_ms"),
			summary.get("long_50"),
			summary.get("long_100"),
			summary.get("fps"),
		),
	)


def insert_metrics(conn: sqlite3.Connection, run_id: str, phases: dict[str, Any]) -> None:
	rows: list[tuple[str, str, str, float]] = []
	for phase_name, phase in phases.items():
		values: dict[str, float] = {}
		flatten("", phase, values)
		for name, value in values.items():
			rows.append((run_id, phase_name, name, value))
	conn.executemany("INSERT OR REPLACE INTO metric (run_id, phase, name, value) VALUES (?, ?, ?, ?)", rows)


def insert_checkpoints(conn: sqlite3.Connection, run_id: str, checkpoints: list[dict[str, Any]]) -> None:
	for item in checkpoints:
		conn.execute(
			"""
			INSERT OR REPLACE INTO checkpoint (run_id, name, t_ms, rss_kb, hwm_kb, threads, cpu_ms,
				actors, children, items, placeholders, status_items, result_count, data_count)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				run_id,
				item.get("name"),
				item.get("t_ms"),
				item.get("rss_kb"),
				item.get("hwm_kb"),
				item.get("threads"),
				item.get("cpu_ms"),
				item.get("actors"),
				item.get("children"),
				item.get("items"),
				item.get("placeholders"),
				item.get("status_items"),
				item.get("result_count"),
				item.get("data_count"),
			),
		)


def insert_report_metrics(
	conn: sqlite3.Connection,
	run_id: str,
	report: dict[str, Any] | None,
	sample_index: int | None,
) -> None:
	if not report or sample_index is None:
		return
	for name, metric in report.get("metrics", {}).items():
		values = metric.get("values", [])
		if sample_index < 0 or sample_index >= len(values):
			continue
		conn.execute(
			"INSERT OR REPLACE INTO report_metric (run_id, name, description, units, value) VALUES (?, ?, ?, ?, ?)",
			(run_id, name, metric.get("description"), metric.get("units"), values[sample_index]),
		)


def ingest_run(conn: sqlite3.Connection, outdir: Path, campaign_id: str | None) -> int:
	meta_path = outdir / "meta.json"
	if not meta_path.exists():
		return 0
	try:
		meta = json_load(meta_path)
	except (OSError, json.JSONDecodeError):
		return 0

	block = meta.get("block", {})
	block_id = meta.get("run_id") or block.get("run_id") or outdir.name
	block_warmup = bool(block.get("warmup"))
	config = block.get("config")
	size = block.get("size")

	report_path = outdir / "report.json"
	report = None
	if report_path.exists():
		try:
			report = json_load(report_path)
		except (OSError, json.JSONDecodeError):
			report = None

	sidecars = sorted(outdir.glob("sidecar-*.json"))
	inserted = 0

	for sidecar_path in sidecars:
		try:
			sidecar = json_load(sidecar_path)
		except (OSError, json.JSONDecodeError):
			continue

		iteration = sidecar.get("iteration")
		if iteration is None:
			continue

		run_id = f"{block_id}#i{int(iteration):02d}"
		phases = sidecar.get("phases", {})
		metrics: dict[str, float] = {}
		flatten("", phases, metrics)
		final = sidecar.get("final")
		if isinstance(final, dict):
			flatten("final", final, metrics)

		error = sidecar.get("error")
		status = "ok" if not error and meta.get("status") == "ok" else "failed"

		checkpoints = sidecar.get("checkpoints", [])
		checkpoint_by_name = {item.get("name"): item for item in checkpoints if item.get("name")}
		ready = checkpoint_by_name.get("ready", {})
		end = checkpoint_by_name.get("end", {})
		after_scroll = checkpoint_by_name.get("after_scroll", {})
		after_search = checkpoint_by_name.get("after_search", {})

		search = phases.get("search", {}) if isinstance(phases, dict) else {}
		scroll = phases.get("scroll", {}) if isinstance(phases, dict) else {}
		open_phase = phases.get("open", {}) if isinstance(phases, dict) else {}
		startup = phases.get("startup", {}) if isinstance(phases, dict) else {}

		conn.execute(
			"""
			INSERT OR REPLACE INTO run (
				run_id, block_id, campaign_id, branch, commit_hash, config, size, iteration, warmup,
				started_at, status, error, sidecar_path, report_path, meta_path,
				startup_ready_ms, startup_to_run_ms,
				open_first_frame_ms, open_animation_ms, open_settle_ms,
				scroll_first_frame_ms, scroll_settle_ms, scroll_passes, scroll_long_frames, scroll_fps,
				search_nomatch_apply_ms, search_match_apply_ms, search_clear_apply_ms,
				search_clear_first_frame_ms, search_clear_ready_ms,
				search_match_results, search_clear_results,
				rss_ready_kb, rss_end_kb, hwm_end_kb, cpu_total_ms,
				actors_ready, actors_end, data_entries,
				items_ready, placeholders_ready, items_end, placeholders_end,
				items_after_scroll, placeholders_after_scroll,
				items_after_search, placeholders_after_search
			)
			VALUES (
				?, ?, ?, ?, ?, ?, ?, ?, ?,
				?, ?, ?, ?, ?, ?,
				?, ?,
				?, ?, ?,
				?, ?, ?, ?, ?,
				?, ?, ?,
				?, ?,
				?, ?,
				?, ?, ?, ?,
				?, ?, ?,
				?, ?, ?, ?,
				?, ?,
				?, ?
			)
			""",
			(
				run_id,
				block_id,
				campaign_id,
				sidecar.get("branch") or block.get("branch"),
				sidecar.get("commit") or meta.get("commit"),
				sidecar.get("config") or config,
				sidecar.get("size") or size,
				iteration,
				1 if sidecar.get("warmup") else 0,
				sidecar.get("started_at") or meta.get("started_at"),
				status,
				error,
				str(sidecar_path),
				str(report_path),
				str(meta_path),
				get(metrics, "startup.ready_ms"),
				get(metrics, "startup.init_to_run_ms"),
				first_frame(metrics, "open"),
				get(metrics, "open.animation_ms"),
				get(metrics, "open.settle_ms"),
				first_frame(metrics, "scroll"),
				get(metrics, "scroll.settle_ms"),
				as_int(get(metrics, "scroll.passes")),
				as_int(get(metrics, "scroll.frame_summary.long_50")),
				get(metrics, "scroll.frame_summary.fps"),
				get(metrics, "search.nomatch.apply_ms"),
				get(metrics, "search.match.apply_ms"),
				get(metrics, "search.clear.apply_ms"),
				first_frame(metrics, "search.clear"),
				get(metrics, "search.clear.ready_ms"),
				as_int(get(metrics, "search.match.result_count")),
				as_int(get(metrics, "search.clear.result_count")),
				as_int(get(metrics, "startup.checkpoint.rss_kb")),
				as_int(get(metrics, "final.rss_kb")),
				as_int(get(metrics, "final.hwm_kb")),
				get(metrics, "final.cpu_total_ms"),
				as_int(get(metrics, "startup.checkpoint.actors")),
				as_int(get(metrics, "final.actors")),
				as_int(get(metrics, "startup.data_count")),
				as_int(get(metrics, "startup.checkpoint.items")),
				as_int(get(metrics, "startup.checkpoint.placeholders")),
				as_int(get(metrics, "final.counts.items")),
				as_int(get(metrics, "final.counts.placeholders")),
				as_int(get(metrics, "scroll.counts.items")),
				as_int(get(metrics, "scroll.counts.placeholders")),
				as_int(get(metrics, "search.clear.counts.items")),
				as_int(get(metrics, "search.clear.counts.placeholders")),
			),
		)

		insert_metrics(conn, run_id, phases)
		insert_checkpoints(conn, run_id, checkpoints)

		for phase_name, phase in phases.items():
			if not isinstance(phase, dict):
				continue
			if phase_name == "search":
				for step_name, step in phase.items():
					if not isinstance(step, dict):
						continue
					frames = step.get("frames_ms")
					if isinstance(frames, list):
						insert_frames(conn, run_id, f"search.{step_name}", frames)
					summary = step.get("frame_summary")
					if isinstance(summary, dict):
						insert_frame_summary(conn, run_id, f"search.{step_name}", summary)
				continue

			frames = phase.get("frames_ms")
			if isinstance(frames, list):
				insert_frames(conn, run_id, phase_name, frames)
			summary = phase.get("frame_summary")
			if isinstance(summary, dict):
				insert_frame_summary(conn, run_id, phase_name, summary)

		sample_index = int(iteration) - (1 if block_warmup else 0)
		insert_report_metrics(conn, run_id, report, sample_index if sample_index >= 0 else None)
		inserted += 1

	return inserted


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
	parser.add_argument("--db", type=Path, default=DEFAULT_DB)
	args = parser.parse_args()

	workdir: Path = args.workdir.resolve()
	db_path: Path = args.db.resolve()
	db_path.parent.mkdir(parents=True, exist_ok=True)
	if db_path.exists():
		db_path.unlink()

	conn = sqlite3.connect(db_path)
	try:
		conn.executescript(SCHEMA)
		campaign_id = insert_campaign(conn, workdir)
		datasets = insert_datasets(conn, workdir)
		print(f"campaign: {campaign_id}")
		print(f"datasets: {sorted(datasets)}")

		runs_dir = workdir / "runs"
		total = 0
		blocks = 0
		if runs_dir.exists():
			for outdir in sorted(runs_dir.iterdir()):
				if not outdir.is_dir():
					continue
				count = ingest_run(conn, outdir, campaign_id)
				if count:
					blocks += 1
					total += count
		conn.commit()

		print(f"ingested {total} samples from {blocks} blocks into {db_path}")
	finally:
		conn.close()
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
