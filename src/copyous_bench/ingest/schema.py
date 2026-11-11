"""Declarative description of the results database."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricColumn:
    """A ``run`` column filled from a flattened sidecar metric."""

    name: str
    sql_type: str
    key: str
    fallback: str | None = None  # older sidecars only carry the first frame in the frame summary

    def extract(self, metrics: dict[str, float]) -> float | int | None:
        value = metrics.get(self.key)
        if value is None and self.fallback:
            value = metrics.get(self.fallback)
        if value is None:
            return None
        return round(value) if self.sql_type == "INTEGER" else value


def _real(name: str, key: str, fallback: str | None = None) -> MetricColumn:
    return MetricColumn(name, "REAL", key, fallback)


def _int(name: str, key: str) -> MetricColumn:
    return MetricColumn(name, "INTEGER", key)


IDENTITY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "TEXT PRIMARY KEY"),
    ("block_id", "TEXT"),
    ("campaign_id", "TEXT"),
    ("branch", "TEXT"),
    ("commit_hash", "TEXT"),
    ("config", "TEXT"),
    ("size", "INTEGER"),
    ("iteration", "INTEGER"),
    ("warmup", "INTEGER"),
    ("started_at", "TEXT"),
    ("status", "TEXT"),
    ("error", "TEXT"),
    ("sidecar_path", "TEXT"),
    ("report_path", "TEXT"),
    ("meta_path", "TEXT"),
)

METRIC_COLUMNS: tuple[MetricColumn, ...] = (
    _real("startup_ready_ms", "startup.ready_ms"),
    _real("startup_to_run_ms", "startup.init_to_run_ms"),
    _real("open_first_frame_ms", "open.first_frame_ms", "open.frame_summary.first_ms"),
    _real("open_animation_ms", "open.animation_ms"),
    _real("open_settle_ms", "open.settle_ms"),
    _real("scroll_first_frame_ms", "scroll.first_frame_ms", "scroll.frame_summary.first_ms"),
    _real("scroll_settle_ms", "scroll.settle_ms"),
    _int("scroll_passes", "scroll.passes"),
    _int("scroll_long_frames", "scroll.frame_summary.long_50"),
    _real("scroll_fps", "scroll.frame_summary.fps"),
    _real("search_nomatch_apply_ms", "search.nomatch.apply_ms"),
    _real("search_match_apply_ms", "search.match.apply_ms"),
    _real("search_clear_apply_ms", "search.clear.apply_ms"),
    _real("search_clear_first_frame_ms", "search.clear.first_frame_ms", "search.clear.frame_summary.first_ms"),
    _real("search_clear_ready_ms", "search.clear.ready_ms"),
    _int("search_match_results", "search.match.result_count"),
    _int("search_clear_results", "search.clear.result_count"),
    _int("rss_ready_kb", "startup.checkpoint.rss_kb"),
    _int("rss_end_kb", "final.rss_kb"),
    _int("hwm_end_kb", "final.hwm_kb"),
    _real("cpu_total_ms", "final.cpu_total_ms"),
    _int("actors_ready", "startup.checkpoint.actors"),
    _int("actors_end", "final.actors"),
    _int("data_entries", "startup.data_count"),
    _int("items_ready", "startup.checkpoint.items"),
    _int("placeholders_ready", "startup.checkpoint.placeholders"),
    _int("items_end", "final.counts.items"),
    _int("placeholders_end", "final.counts.placeholders"),
    _int("items_after_scroll", "scroll.counts.items"),
    _int("placeholders_after_scroll", "scroll.counts.placeholders"),
    _int("items_after_search", "search.clear.counts.items"),
    _int("placeholders_after_search", "search.clear.counts.placeholders"),
)

CAMPAIGN_COLUMNS = ("campaign_id", "started_at", "finished_at", "environment", "datasets", "settings")
DATASET_COLUMNS = (
    "size", "path", "sha256", "entries", "type_counts", "token", "token_matches",
    "no_match_token", "newest_datetime", "oldest_datetime",
)  # fmt: skip
FRAME_SUMMARY_COLUMNS = (
    "frames", "duration_ms", "first_ms", "last_ms", "mean_interval_ms", "median_interval_ms",
    "p95_interval_ms", "max_interval_ms", "long_50", "long_100", "fps",
)  # fmt: skip
CHECKPOINT_COLUMNS = (
    "name", "t_ms", "rss_kb", "hwm_kb", "threads", "cpu_ms", "actors", "children",
    "items", "placeholders", "status_items", "result_count", "data_count",
)  # fmt: skip

_RUN_DDL = ",\n\t".join(
    [f"{name} {sql}" for name, sql in IDENTITY_COLUMNS]
    + [f"{column.name} {column.sql_type}" for column in METRIC_COLUMNS]
)

TABLE_DDL: dict[str, str] = {
    "campaign": """CREATE TABLE campaign (
	campaign_id   TEXT PRIMARY KEY,
	started_at    TEXT,
	finished_at   TEXT,
	environment   TEXT,
	datasets      TEXT,
	settings      TEXT
)""",
    "dataset": """CREATE TABLE dataset (
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
)""",
    "run": f"CREATE TABLE run (\n\t{_RUN_DDL}\n)",
    "metric": """CREATE TABLE metric (
	run_id  TEXT,
	phase   TEXT,
	name    TEXT,
	value   REAL,
	PRIMARY KEY (run_id, phase, name)
)""",
    "frame": """CREATE TABLE frame (
	run_id  TEXT,
	phase   TEXT,
	seq     INTEGER,
	t_ms    REAL,
	PRIMARY KEY (run_id, phase, seq)
)""",
    "frame_summary": """CREATE TABLE frame_summary (
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
)""",
    "checkpoint": """CREATE TABLE checkpoint (
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
)""",
    "report_metric": """CREATE TABLE report_metric (
	run_id      TEXT,
	name        TEXT,
	description TEXT,
	units       TEXT,
	value       REAL,
	PRIMARY KEY (run_id, name)
)""",
}


def schema_sql() -> str:
    """The full script: drop and recreate every table."""
    drops = "\n".join(f"DROP TABLE IF EXISTS {name};" for name in TABLE_DDL)
    creates = ";\n\n".join(TABLE_DDL.values()) + ";"
    return f"PRAGMA journal_mode = DELETE;\nPRAGMA foreign_keys = OFF;\n\n{drops}\n\n{creates}\n"
