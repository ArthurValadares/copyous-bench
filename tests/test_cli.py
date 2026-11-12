import runpy
import sys

import pytest
from click.testing import CliRunner

from copyous_bench.cli import main


def invoke(*args):
    return CliRunner().invoke(main, [str(a) for a in args])


def test_dry_run_prints_plan_without_touching_workdir(git_repo, tmp_path):
    work = tmp_path / "work"
    result = invoke("run", "--dry-run", "--repo", git_repo, "--workdir", work, "--results-dir", tmp_path / "res")
    assert result.exit_code == 0, result.output
    assert "total full launches: 155" in result.output
    assert not work.exists()


def test_results_dir_inside_repo_is_rejected(git_repo):
    result = invoke("run", "--dry-run", "--repo", git_repo, "--results-dir", git_repo / "r")
    assert result.exit_code != 0 and "outside --repo" in result.output


def test_sizes_must_include_core(git_repo, tmp_path):
    result = invoke("run", "--dry-run", "--repo", git_repo, "--workdir", tmp_path / "w",
                    "--results-dir", tmp_path / "r", "--sizes", "50")  # fmt: skip
    assert result.exit_code != 0 and "must include 500" in result.output


def test_run_forwards_options_and_exit_code(monkeypatch, tmp_path):
    seen = {}

    def fake_run(self):
        seen["options"] = self.options
        return 3

    monkeypatch.setattr("copyous_bench.orchestration.Campaign.run", fake_run)
    result = invoke(
        "run", "--branches", "main", "--sizes", 500, "--no-warmup", "--build-jobs", 2, "--workdir", tmp_path
    )
    assert result.exit_code == 3
    options = seen["options"]
    assert (list(options.branches), list(options.sizes), options.warmup, options.build_jobs) == (
        ["main"],
        [500],
        False,
        2,
    )


def test_dataset_command(tmp_path):
    result = invoke("dataset", "--out-dir", tmp_path, "--sizes", 50, "--sizes", 200)
    assert result.exit_code == 0, result.output
    assert "N=50:" in result.output and "N=200:" in result.output and "manifest:" in result.output
    assert (tmp_path / "N200.db").exists()


def test_dataset_command_rejects_oversized(tmp_path):
    result = invoke("dataset", "--out-dir", tmp_path, "--sizes", 501)
    assert result.exit_code == 2 and "exceeds master size" in result.output


def test_ingest_and_analyze_commands(workdir, tmp_path):
    db = tmp_path / "r" / "results.db"
    result = invoke("ingest", "--workdir", workdir, "--db", db)
    assert result.exit_code == 0 and "campaign: C1" in result.output and "ingested" in result.output

    result = invoke("analyze", "--db", db, "--outdir", tmp_path / "r")
    assert result.exit_code == 0, result.output
    assert "plot:" in result.output and "samples:" in result.output

    result = invoke("analyze", "--db", db, "--outdir", tmp_path / "r2", "--no-plots")
    assert result.exit_code == 0 and "plot:" not in result.output


def test_module_entry_points(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["copyous-bench", "--help"])
    for module in ("copyous_bench", "copyous_bench.cli"):
        warns = pytest.warns(RuntimeWarning) if module.endswith("cli") else _nullcontext()
        with warns, pytest.raises(SystemExit) as exit_info:
            runpy.run_module(module, run_name="__main__")
        assert exit_info.value.code == 0


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
