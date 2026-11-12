"""BlockRunner against a fake ``dbus-run-session`` (see conftest.FAKE_SESSION_BODY)."""

import dataclasses
import json

import pytest

from copyous_bench.dataset import generate_datasets
from copyous_bench.orchestration.dbus import DbusRuntime
from copyous_bench.orchestration.plan import Block
from copyous_bench.orchestration.runner import BlockRunner, RunContext
from copyous_bench.orchestration.state import StateLog
from copyous_bench.orchestration.toolchain import Toolchain


@pytest.fixture
def kit(tmp_path, fake_session):
    work = tmp_path / "work"
    work.mkdir()
    manifest, _ = generate_datasets(work / "datasets", [50, 500])
    manifest.update(campaign_id="C1", commits={"main": "deadbeef"})
    worktree = tmp_path / "wt"
    worktree.mkdir()
    ext = tmp_path / "ext.zip"
    ext.write_text("")
    settings = tmp_path / "settings.ini"
    settings.write_text("[x]")
    context = RunContext(
        workdir=work,
        worktrees={"main": worktree},
        zips={"main": ext},
        datasets=manifest,
        dbus=DbusRuntime("host", str(fake_session), "dbus-daemon", str(fake_session.parent)),
        toolchain=Toolchain(tmp_path / "n", tmp_path / "p"),
        settings_file=settings,
        timeout=60.0,
        stall_timeout=60.0,
        resume=False,
    )

    def make(**overrides):
        ctx = dataclasses.replace(context, **overrides)
        return BlockRunner(ctx, StateLog(work / "state.jsonl")), ctx

    return make


def _meta(runner, block):
    return json.loads((runner.outdir(block) / "meta.json").read_text())


def test_successful_block(kit, capsys):
    runner, ctx = kit()
    block = Block("main", "open", 50, 2, True)
    result = runner.run(block)
    assert result.ok and result.record["status"] == "ok" and result.record["errors"] == []
    assert "run main__open__N50 (3 launches)" in capsys.readouterr().out

    outdir = runner.outdir(block)
    assert len(list(outdir.glob("sidecar-*.json"))) == 3
    context = json.loads((outdir / "context.json").read_text())
    assert (context["campaign_id"], context["commit"], context["expected_entries"]) == ("C1", "deadbeef", 50)
    meta = _meta(runner, block)
    assert meta["status"] == "ok" and meta["block"]["iterations"] == 3
    assert meta["command"][0] == ctx.dbus.run_session and "--perf-warmup" in meta["command"]
    assert meta["extension_zip"] == str(ctx.zips["main"])
    assert runner.state.completed() == {block.run_id}


def test_every_phase_is_exercised_by_the_all_config(kit):
    runner, _ = kit()
    assert runner.run(Block("main", "all", 500, 1, False, smoke=True)).ok


def test_unknown_commit_and_no_warmup(kit):
    manifest_without_commits = {k: v for k, v in kit()[1].datasets.items() if k != "commits"}
    runner, _ = kit(datasets=manifest_without_commits)
    block = Block("main", "scroll", 50, 1, False)
    assert runner.run(block).ok
    assert _meta(runner, block)["commit"] == "unknown"
    assert "--perf-warmup" not in _meta(runner, block)["command"]


def test_resume_skips_blocks_that_are_already_ok(kit, capsys):
    runner, _ = kit(resume=True)
    block = Block("main", "open", 50, 1, True)
    assert runner.run(block).ok  # no meta yet: runs
    capsys.readouterr()
    again = runner.run(block)
    assert again.ok and again.record["status"] == "ok"
    assert "skip main__open__N50 (already ok)" in capsys.readouterr().out
    assert len(runner.state.path.read_text().splitlines()) == 1


@pytest.mark.parametrize("damage", ["corrupt", "failed"])
def test_resume_reruns_unusable_results(kit, capsys, damage):
    runner, _ = kit(resume=True)
    block = Block("main", "open", 50, 1, True)
    assert runner.run(block).ok
    meta_path = runner.outdir(block) / "meta.json"
    if damage == "corrupt":
        meta_path.write_text("{nope")
    else:
        meta_path.write_text(json.dumps({**_meta(runner, block), "status": "failed"}))
    capsys.readouterr()
    assert runner.run(block).ok
    assert "skip" not in capsys.readouterr().out
    assert len(runner.state.path.read_text().splitlines()) == 2


def test_nonzero_exit_fails_the_block(kit, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "fail")
    runner, _ = kit()
    block = Block("main", "open", 50, 1, True)
    result = runner.run(block)
    assert not result.ok and result.record["returncode"] == 3
    assert "exit code 3" in result.record["errors"]
    assert "report.json missing" in result.record["errors"]
    out = capsys.readouterr().out
    assert "FAILED" in out and "- exit code 3" in out
    assert _meta(runner, block)["status"] == "failed"
    assert runner.state.completed() == set()


def test_many_problems_are_truncated_in_the_log(kit, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "bad")
    runner, _ = kit()
    result = runner.run(Block("main", "all", 500, 2, True, smoke=True))
    assert not result.ok and len(result.record["errors"]) > 8
    assert f"... {len(result.record['errors']) - 8} more" in capsys.readouterr().out
    assert (
        _meta(runner, Block("main", "all", 500, 2, True, smoke=True))["validation"]["errors"] == result.record["errors"]
    )


def test_timeout(kit, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    runner, _ = kit(timeout=0.3)
    result = runner.run(Block("main", "open", 50, 1, False))
    assert not result.ok and result.record["timed_out"] and result.record["returncode"] is None
    assert "timed out after 0s" in result.record["errors"]


def test_stall(kit, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    runner, _ = kit(stall_timeout=0.2)
    result = runner.run(Block("main", "open", 50, 1, False))
    assert not result.ok and result.record["stalled"]
    assert any(error.startswith("stalled: no new sidecar for 0s") for error in result.record["errors"])
