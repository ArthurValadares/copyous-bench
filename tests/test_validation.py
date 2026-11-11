import json

from copyous_bench.orchestration.plan import Block
from copyous_bench.orchestration.validation import BlockValidator


def _write(outdir, sidecars, report=None):
    outdir.mkdir(parents=True)
    for i, sidecar in enumerate(sidecars):
        (outdir / f"sidecar-{i:02d}.json").write_text(json.dumps(sidecar))
    (outdir / "report.json").write_text(json.dumps(report or {"metrics": {"copyousError": {"values": [0]}}}))


def _good(warmup=False):
    return {
        "warmup": warmup,
        "phases": {
            "startup": {"data_ok": True, "data_count": 50},
            "open": {"opened": True, "grabbed": True, "first_frame_ms": 3.0},
        },
    }


def test_valid_block(tmp_path):
    block = Block("main", "open", 50, 1, True)
    _write(tmp_path / "b", [_good(True), _good()])
    assert BlockValidator(50, 6).validate(block, tmp_path / "b").ok


def test_reports_each_problem_once(tmp_path):
    block = Block("main", "open", 50, 1, True)
    bad = _good()
    bad["phases"]["open"]["grabbed"] = False
    report = {"metrics": {"copyousError": {"values": [1]}}}
    _write(tmp_path / "b", [_good(True), bad], report)
    problems = BlockValidator(50, 6).validate(block, tmp_path / "b").problems
    assert "sidecar-01.json: dialog did not grab input" in problems
    assert sum("copyousError" in p for p in problems) == 1


def test_missing_sidecars_and_report(tmp_path):
    block = Block("main", "open", 50, 2, True)
    (tmp_path / "b").mkdir()
    problems = BlockValidator(50, 6).validate(block, tmp_path / "b").problems
    assert "report.json missing" in problems
    assert "expected 3 sidecars, found 0" in problems
