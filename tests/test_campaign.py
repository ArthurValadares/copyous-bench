"""Whole campaigns: real Git worktrees, a fake ``make`` and a fake ``dbus-run-session``."""

import json

import pytest
from conftest import git

import copyous_bench.ingest
from copyous_bench.orchestration import Campaign, CampaignOptions, campaign
from copyous_bench.orchestration.dbus import DbusRuntime

BRANCHES = ["main", "feat/lazy-clipboard-items"]


@pytest.fixture
def options(tmp_path, monkeypatch, git_repo, fake_session, fake_make):
    git(git_repo, "branch", "feat/lazy-clipboard-items")
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    (wrap / "settings.ini").write_text("[s]\n")
    runtime = DbusRuntime("host", str(fake_session), "dbus-daemon", str(fake_session.parent))
    monkeypatch.setattr(campaign, "WRAP_DIR", wrap)
    monkeypatch.setattr(campaign, "ensure_hljs", lambda repo, workdir, force=False: workdir / "assets" / "hljs")
    monkeypatch.setattr(campaign, "discover_dbus_runtime", lambda workdir, override=None: runtime)

    def make(**overrides):
        defaults = {
            "repo": git_repo,
            "workdir": tmp_path / "work",
            "results_dir": tmp_path / "results",
            "branches": BRANCHES,
            "sizes": [50, 500],
            "core_samples": 2,
            "scaling_samples": 2,
            "retries": 0,
            "retry_delay": 0.0,
            "build_jobs": 2,
            "timeout": 30.0,
            "stall_timeout": 30.0,
        }
        return CampaignOptions(**{**defaults, **overrides})

    return make


def _state(tmp_path):
    return [json.loads(line) for line in (tmp_path / "work" / "state.jsonl").read_text().splitlines()]


def test_full_campaign(options, tmp_path, capsys):
    assert Campaign(options(campaign_id="CAMP")).run() == 0
    out = capsys.readouterr().out
    for heading in ("== Environment ==", "== Smoke gate ==", "== Full matrix ==", "== Ingest ==", "== Analyze =="):
        assert heading in out

    records = _state(tmp_path)
    assert len(records) == 2 + 2 * (3 + 1) and all(r["status"] == "ok" for r in records)

    work, results = tmp_path / "work", tmp_path / "results"
    meta = json.loads((work / "campaign.json").read_text())
    assert meta["campaign_id"] == "CAMP" and meta["finished_at"] and "thermal_end" in meta
    assert meta["settings"] == "[s]\n" and len(meta["plan"]) == 8 and len(meta["smoke"]) == 2
    manifest = json.loads((work / "datasets" / "datasets.json").read_text())
    assert manifest["campaign_id"] == "CAMP" and set(manifest["commits"]) == set(BRANCHES)
    assert (work / "environment.json").exists()
    assert (results / "results.db").exists() and (results / "summary.md").exists()


def test_default_campaign_id_is_a_timestamp(options, tmp_path):
    assert Campaign(options(smoke_only=True)).run() == 0
    campaign_id = json.loads((tmp_path / "work" / "campaign.json").read_text())["campaign_id"]
    assert len(campaign_id) == 16 and campaign_id.endswith("Z")


def test_smoke_only(options, tmp_path, capsys):
    assert Campaign(options(smoke_only=True)).run() == 0
    assert "Smoke-only run finished." in capsys.readouterr().out
    assert len(_state(tmp_path)) == 2
    assert not (tmp_path / "results").exists()
    assert json.loads((tmp_path / "work" / "campaign.json").read_text())["finished_at"]


def test_failing_smoke_gate_aborts(options, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "fail")
    assert Campaign(options()).run() == 2
    captured = capsys.readouterr()
    assert "Smoke gate failed" in captured.err and "== Full matrix ==" not in captured.out
    assert json.loads((tmp_path / "work" / "campaign.json").read_text())["finished_at"]


def test_failing_blocks_are_retried_and_reported(options, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "fail")
    opts = options(skip_smoke=True, retries=1, only="main__open__N500")
    assert Campaign(opts).run() == 1
    captured = capsys.readouterr()
    assert "retry 1/1" in captured.out
    assert "Some matrix blocks failed" in captured.err
    assert len(_state(tmp_path)) == 2  # one attempt plus one retry
    # Nothing succeeded, so the analysis has nothing to report; the campaign still finishes.
    assert "analyze failed: No successful samples found" in captured.out


def test_post_processing_can_be_skipped(options, tmp_path):
    assert Campaign(options(no_post=True, only="main__open__N500")).run() == 0
    assert not (tmp_path / "results").exists()


def test_post_processing_errors_do_not_hide_the_result(options, tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(copyous_bench.ingest, "ingest", boom)
    assert Campaign(options(skip_smoke=True, only="main__open__N500")).run() == 0
    out = capsys.readouterr().out
    assert "ingest failed: boom" in out and "analyze failed: Database not found" in out


def test_resume_skips_finished_blocks(options, tmp_path, capsys):
    assert Campaign(options(smoke_only=True, resume=True)).run() == 0
    capsys.readouterr()
    assert Campaign(options(smoke_only=True, resume=True)).run() == 0
    assert capsys.readouterr().out.count("(already ok)") == 2
    assert len(_state(tmp_path)) == 2


def test_dry_run_prints_the_plan_only(options, tmp_path, capsys):
    # --only matches substrings, so "__open__N50" selects N50 and N500: 2 branches x 2 sizes x 3 launches.
    assert Campaign(options(dry_run=True, only="__open__N50")).run() == 0
    out = capsys.readouterr().out
    assert "full  {" in out and "total full launches: 12" in out
    assert not (tmp_path / "work").exists()


def test_dry_run_lists_smoke_blocks(options, capsys):
    assert Campaign(options(dry_run=True)).run() == 0
    assert capsys.readouterr().out.count("smoke {") == 2


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sizes": [50]}, "must include 500"),
        ({"only": "no-such-block"}, "matched no blocks"),
    ],
)
def test_invalid_options(options, overrides, message):
    with pytest.raises(SystemExit, match=message):
        Campaign(options(**overrides)).run()


def test_repo_must_be_a_git_checkout(options, tmp_path):
    with pytest.raises(SystemExit, match="not a Git checkout"):
        Campaign(options(repo=tmp_path)).run()


def test_repo_must_be_the_extension(options, git_repo):
    (git_repo / "resources" / "metadata.json").unlink()
    with pytest.raises(SystemExit, match="does not look like the Copyous extension"):
        Campaign(options()).run()


def test_results_and_workdir_must_live_outside_the_repo(options, git_repo):
    with pytest.raises(SystemExit, match="--results-dir must be outside"):
        Campaign(options(results_dir=git_repo / "results")).run()
    with pytest.raises(SystemExit, match="--workdir must be outside"):
        Campaign(options(workdir=git_repo / "work")).run()


def test_missing_settings_file(options, tmp_path, monkeypatch):
    empty = tmp_path / "empty-wrap"
    empty.mkdir()
    monkeypatch.setattr(campaign, "WRAP_DIR", empty)
    with pytest.raises(SystemExit, match="Settings file missing"):
        Campaign(options()).run()
