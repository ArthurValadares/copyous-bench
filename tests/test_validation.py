import json

import pytest

from copyous_bench.orchestration.plan import Block
from copyous_bench.orchestration.validation import BlockValidator


def _write(outdir, sidecars, report=None):
    outdir.mkdir(parents=True, exist_ok=True)
    for i, sidecar in enumerate(sidecars):
        (outdir / f"sidecar-{i:02d}.json").write_text(json.dumps(sidecar))
    if report is not False:
        (outdir / "report.json").write_text(json.dumps(report or {"metrics": {"copyousError": {"values": [0]}}}))


def _search(ok=True):
    return {
        step: {"result_count": 1, "expected": 1 if ok else 2, "ready_ok": ok} for step in ("nomatch", "match", "clear")
    }


def _good(warmup=False, **phases):
    base = {
        "startup": {"data_ok": True, "data_count": 50},
        "open": {"opened": True, "grabbed": True, "first_frame_ms": 3.0},
    }
    return {"warmup": warmup, "phases": {**base, **phases}}


def _validate(tmp_path, block, sidecars, report=None):
    _write(tmp_path / "b", sidecars, report)
    return BlockValidator(50, 6).validate(block, tmp_path / "b")


def test_valid_block(tmp_path):
    result = _validate(tmp_path, Block("main", "open", 50, 1, True), [_good(True), _good()])
    assert result.ok and result.problems == []
    assert result.summary["sidecars"] == 2 and result.summary["expected_entries"] == 50
    assert result.summary["token_matches"] == 6


def test_reports_each_problem_once(tmp_path):
    bad = _good()
    bad["phases"]["open"]["grabbed"] = False
    report = {"metrics": {"copyousError": {"values": [1]}}}
    result = _validate(tmp_path, Block("main", "open", 50, 1, True), [_good(True), bad], report)
    assert "sidecar-01.json: dialog did not grab input" in result.problems
    assert sum("copyousError" in p for p in result.problems) == 1


def test_missing_sidecars_and_report(tmp_path):
    (tmp_path / "b").mkdir()
    problems = BlockValidator(50, 6).validate(Block("main", "open", 50, 2, True), tmp_path / "b").problems
    assert "report.json missing" in problems
    assert "expected 3 sidecars, found 0" in problems


def test_unreadable_files(tmp_path):
    _write(tmp_path / "b", [_good()], report=False)
    (tmp_path / "b" / "report.json").write_text("{nope")
    (tmp_path / "b" / "sidecar-01.json").write_text("{nope")
    problems = BlockValidator(50, 6).validate(Block("main", "open", 50, 2, False), tmp_path / "b").problems
    assert any(p.startswith("report.json unreadable") for p in problems)
    assert any(p.startswith("sidecar-01.json unreadable") for p in problems)
    assert "expected 2 sidecars, found 2" not in problems


def test_sidecar_error_short_circuits_phase_checks(tmp_path):
    failed = {"error": "shell crashed", "phases": {}}
    result = _validate(tmp_path, Block("main", "open", 50, 1, False), [failed])
    assert result.problems == ["sidecar-00.json error: shell crashed"]


def test_startup_and_open_problems(tmp_path):
    broken = {"phases": {"startup": {"data_ok": False, "data_count": 7}, "open": {}}}
    problems = _validate(tmp_path, Block("main", "open", 50, 1, False), [broken]).problems
    assert problems == [
        "sidecar-00.json: history did not load",
        "sidecar-00.json: data_count 7 != 50",
        "sidecar-00.json: dialog did not open",
        "sidecar-00.json: dialog did not grab input",
        "sidecar-00.json: no open frame",
    ]


@pytest.mark.parametrize("config", ["scroll", "all"])
def test_scroll_checks(tmp_path, config):
    block = Block("main", config, 50, 1, False)
    extra = {"search": _search()} if config == "all" else {}
    assert _validate(tmp_path / "ok", block, [_good(scroll={"first_frame_ms": 1.0}, **extra)]).ok
    assert "sidecar-00.json: scroll phase missing" in _validate(tmp_path / "missing", block, [_good(**extra)]).problems
    no_frame = _validate(tmp_path / "noframe", block, [_good(scroll={"first_frame_ms": None}, **extra)])
    assert "sidecar-00.json: no scroll frame" in no_frame.problems


@pytest.mark.parametrize("config", ["search", "all"])
def test_search_checks(tmp_path, config):
    block = Block("main", config, 50, 1, False)
    extra = {"scroll": {"first_frame_ms": 1.0}} if config == "all" else {}
    assert _validate(tmp_path / "ok", block, [_good(search=_search(), **extra)]).ok
    assert "sidecar-00.json: search phase missing" in _validate(tmp_path / "missing", block, [_good(**extra)]).problems
    problems = _validate(tmp_path / "bad", block, [_good(search=_search(ok=False), **extra)]).problems
    assert "sidecar-00.json: search match count 1 != 2" in problems
    assert "sidecar-00.json: search clear did not become ready" in problems


def test_phases_of_other_configs_are_not_required(tmp_path):
    assert _validate(tmp_path, Block("main", "open", 50, 1, False), [_good()]).ok


def test_report_is_only_checked_for_measured_sidecars(tmp_path):
    report = {"metrics": {"copyousError": {"values": [1]}}}
    only_warmup = _validate(tmp_path, Block("main", "open", 50, 0, True), [_good(True)], report)
    assert only_warmup.ok
