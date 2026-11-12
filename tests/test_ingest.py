import json
import sqlite3
from contextlib import closing

from copyous_bench.ingest import ingest
from copyous_bench.ingest.extract import flatten, iter_frame_phases, load_block, sidecar_metrics
from copyous_bench.ingest.repository import ResultsDatabase
from copyous_bench.ingest.schema import METRIC_COLUMNS, MetricColumn, schema_sql


def query(db, sql):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchall()


def test_ingest_full_workdir(workdir, tmp_path):
    db = tmp_path / "out" / "results.db"
    stats = ingest(workdir, db)
    assert (stats.campaign_id, stats.dataset_sizes) == ("C1", [50, 200, 500])
    # 5 branches x 5 blocks, minus the missing merge/N50, plus the unlisted branch and 2 smoke blocks.
    assert stats.blocks == 27 and stats.samples == query(db, "select count(*) from run")[0][0]
    assert query(db, "select count(*) from dataset") == [(3,)]
    # The frame-summary fallback fills every explicit gap except the one block with no frame data at all.
    assert query(db, "select count(*) from run where open_first_frame_ms is null and config='open'") == [(1,)]
    assert query(db, "select count(*) from run where warmup=1")[0][0] == stats.blocks
    assert query(db, "select count(*) from report_metric")[0][0] > 0
    assert query(db, "select typeof(scroll_passes) from run where config='scroll' limit 1") == [("integer",)]


def test_ingest_is_rebuilt_from_scratch(workdir, tmp_path):
    db = tmp_path / "results.db"
    first = ingest(workdir, db)
    again = ingest(workdir, db)
    assert first.samples == again.samples
    assert query(db, "select count(*) from run")[0][0] == again.samples


def test_ingest_without_runs_dir(tmp_path):
    stats = ingest(tmp_path / "empty", tmp_path / "r.db")
    assert (stats.campaign_id, stats.dataset_sizes, stats.samples, stats.blocks) == (None, [], 0, 0)


def test_metric_column_extraction():
    real = MetricColumn("a", "REAL", "x", "y")
    integer = MetricColumn("b", "INTEGER", "x")
    assert real.extract({"x": 1.5}) == 1.5
    assert real.extract({"y": 2.5}) == 2.5  # fallback
    assert real.extract({}) is None
    assert integer.extract({"x": 2.6}) == 3
    assert len({c.name for c in METRIC_COLUMNS}) == len(METRIC_COLUMNS)
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(schema_sql())


def test_flatten_and_helpers():
    out: dict[str, float] = {}
    flatten("", {"a": {"b": True, "c": 2, "frames_ms": [1], "d": "text"}, "e": 1.5}, out)
    assert out == {"a.b": 1.0, "a.c": 2.0, "e": 1.5}
    assert sidecar_metrics({"phases": {"p": {"x": 1}}, "final": {"y": 2}}) == {"p.x": 1.0, "final.y": 2.0}
    assert sidecar_metrics({"phases": {}, "final": "oops"}) == {}
    phases = {"search": {"clear": {"k": 1}, "note": 3}, "open": {"k": 2}, "bad": 5}
    assert dict(iter_frame_phases(phases)) == {"search.clear": {"k": 1}, "open": {"k": 2}}


def test_load_block_tolerates_damage(tmp_path):
    assert load_block(tmp_path) is None  # no meta.json
    (tmp_path / "meta.json").write_text("{broken")
    assert load_block(tmp_path) is None

    (tmp_path / "meta.json").write_text(json.dumps({"status": "ok"}))
    (tmp_path / "report.json").write_text("{broken")
    (tmp_path / "sidecar-00.json").write_text("{broken")
    (tmp_path / "sidecar-01.json").write_text(json.dumps({"iteration": 1}))
    block = load_block(tmp_path)
    assert block.report is None and len(block.sidecars) == 1
    assert block.meta_path.name == "meta.json" and block.report_path.name == "report.json"


def test_database_campaign_variants(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    with ResultsDatabase(tmp_path / "a.db") as database:
        assert database.insert_campaign(work) is None  # nothing on disk
        assert database.insert_datasets(work) == []
        (work / "environment.json").write_text(json.dumps({"x": 1}))
        assert database.insert_campaign(work) is None  # environment without a campaign id
        (work / "campaign.json").write_text(json.dumps({"campaign_id": "Z", "environment": {"x": 1}}))
        assert database.insert_campaign(work) == "Z"
        database.commit()
    assert query(tmp_path / "a.db", "select campaign_id from campaign") == [("Z",)]


def test_database_block_edge_cases(tmp_path):
    outdir = tmp_path / "blk"
    outdir.mkdir()
    sidecars = {
        "sidecar-00.json": {"iteration": 0, "warmup": True, "phases": {}},
        "sidecar-01.json": {"phases": {}},  # no iteration: skipped
        "sidecar-02.json": {"iteration": 2, "error": "boom", "phases": {"open": {"opened": True}}},
    }
    for name, content in sidecars.items():
        (outdir / name).write_text(json.dumps(content))
    meta = {"status": "ok", "block": {"warmup": True, "branch": "b", "config": "open", "size": 5}}
    (outdir / "meta.json").write_text(json.dumps(meta))
    report = {"metrics": {"m": {"values": [1.0], "description": "d", "units": "u"}}}
    (outdir / "report.json").write_text(json.dumps(report))

    block = load_block(outdir)
    with ResultsDatabase(tmp_path / "b.db") as database:
        assert database.insert_block(block, None) == 2
        database.commit()
    rows = query(tmp_path / "b.db", "select run_id, status, error, branch from run order by iteration")
    assert rows == [("blk#i00", "ok", None, "b"), ("blk#i02", "failed", "boom", "b")]
    # Warmup sample (index -1) has no report value; iteration 2 maps to sample 1, out of range.
    assert query(tmp_path / "b.db", "select count(*) from report_metric") == [(0,)]


def test_report_metrics_follow_the_sample_index(tmp_path):
    outdir = tmp_path / "blk"
    outdir.mkdir()
    (outdir / "sidecar-01.json").write_text(json.dumps({"iteration": 1, "phases": {}}))
    (outdir / "meta.json").write_text(json.dumps({"status": "ok", "block": {"warmup": True}}))
    report = {"metrics": {"m": {"values": [7.0, 8.0], "description": "d", "units": "u"}}}
    (outdir / "report.json").write_text(json.dumps(report))
    with ResultsDatabase(tmp_path / "c.db") as database:
        database.insert_block(load_block(outdir), None)
        database.commit()
    assert query(tmp_path / "c.db", "select value from report_metric") == [(7.0,)]
