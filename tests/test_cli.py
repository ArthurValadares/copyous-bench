import subprocess

from click.testing import CliRunner

from copyous_bench.cli import main


def _fake_repo(path):
    (path / "resources").mkdir(parents=True)
    (path / "resources" / "metadata.json").write_text("{}")
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_dry_run_prints_plan_without_touching_workdir(tmp_path):
    repo, work = tmp_path / "repo", tmp_path / "work"
    _fake_repo(repo)
    result = CliRunner().invoke(
        main, ["run", "--dry-run", "--repo", str(repo), "--workdir", str(work), "--results-dir", str(tmp_path / "res")]
    )
    assert result.exit_code == 0, result.output
    assert "total full launches: 155" in result.output
    assert not work.exists()


def test_results_dir_inside_repo_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    _fake_repo(repo)
    result = CliRunner().invoke(main, ["run", "--dry-run", "--repo", str(repo), "--results-dir", str(repo / "r")])
    assert result.exit_code != 0
    assert "outside --repo" in result.output


def test_sizes_must_include_core(tmp_path):
    repo = tmp_path / "repo"
    _fake_repo(repo)
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--dry-run",
            "--repo",
            str(repo),
            "--workdir",
            str(tmp_path / "w"),
            "--results-dir",
            str(tmp_path / "r"),
            "--sizes",
            "50",
        ],
    )
    assert result.exit_code != 0 and "must include 500" in result.output
