"""Shared fixtures: synthetic benchmark artifacts and throw-away Git repositories."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from copyous_bench.dataset import generate_datasets

BRANCHES = [
    "main",
    "feat/clipboard-item-placeholders",
    "feat/lazy-clipboard-items",
    "perf/cheaper-cached-async-items",
    "merge/clipboard-item-placeholders-cached-async-items",
]


@pytest.fixture(autouse=True)
def _isolate_git_environment(monkeypatch):
    """Git exports GIT_DIR/GIT_INDEX_FILE to hooks; these tests use throw-away repositories."""
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)


def frame_summary(rnd: random.Random, n: int) -> dict:
    return {
        "frames": n, "duration_ms": rnd.random() * 900, "first_ms": rnd.random() * 40, "last_ms": 900,
        "mean_interval_ms": 16.7, "median_interval_ms": 16, "p95_interval_ms": 30, "max_interval_ms": 80,
        "long_50": rnd.randint(0, 4), "long_100": rnd.randint(0, 2), "fps": 50 + rnd.random() * 10,
    }  # fmt: skip


def search_step(rnd: random.Random, first: bool) -> dict:
    return {
        "apply_ms": rnd.random() * 30, "ready_ms": rnd.random() * 120,
        "result_count": 7, "expected": 7, "ready_ok": True,
        "frames_ms": [1.0, 2.5, 4.0], "frame_summary": frame_summary(rnd, 3),
        "counts": {"items": rnd.randint(10, 30), "placeholders": rnd.randint(0, 400)},
        "first_frame_ms": rnd.random() * 50 if first else None,
    }  # fmt: skip


def make_sidecar(rnd, branch, config, size, iteration, warmup, *, first_frame=True, bare=False):
    """``first_frame=False`` exercises the frame-summary fallback; ``bare`` leaves no first frame at all."""
    open_phase = {
        "opened": True, "grabbed": True,
        "first_frame_ms": rnd.random() * 80 if first_frame else None,
        "animation_ms": rnd.random() * 200, "settle_ms": rnd.random() * 300,
        "frames_ms": [1, 2, 3],
    }  # fmt: skip
    if not bare:
        open_phase["frame_summary"] = frame_summary(rnd, 3)
    phases = {
        "startup": {
            "ready_ms": rnd.random() * 500, "init_to_run_ms": rnd.random() * 80,
            "data_ok": True, "data_count": size,
            "checkpoint": {"rss_kb": 300000 + rnd.randint(0, 9999), "actors": rnd.randint(500, 900),
                           "items": rnd.randint(10, 30), "placeholders": rnd.randint(0, 400)},
        },
        "open": open_phase,
    }  # fmt: skip
    if config in ("scroll", "all"):
        phases["scroll"] = {
            "first_frame_ms": rnd.random() * 60, "settle_ms": rnd.random() * 800, "passes": 3,
            "counts": {"items": 20, "placeholders": 300}, "frames_ms": [1, 2], "frame_summary": frame_summary(rnd, 2),
        }  # fmt: skip
    if config in ("search", "all"):
        phases["search"] = {name: search_step(rnd, first_frame) for name in ("nomatch", "match", "clear")}
    return {
        "iteration": iteration, "warmup": warmup, "branch": branch, "config": config, "size": size,
        "commit": "abc123", "started_at": "2026-11-10T10:00:00Z", "phases": phases,
        "final": {"rss_kb": 320000 + rnd.randint(0, 9999), "hwm_kb": 340000, "cpu_total_ms": rnd.random() * 9000,
                  "actors": 700, "counts": {"items": 25, "placeholders": 350}},
        "checkpoints": [{"name": "ready", "t_ms": 10.0, "rss_kb": 300000, "hwm_kb": 310000, "threads": 9,
                         "cpu_ms": 100.0, "actors": 600, "children": 40, "items": 20, "placeholders": 300,
                         "status_items": 1, "result_count": 500, "data_count": size}],
    }  # fmt: skip


def write_block(workdir, rnd, branch, config, size, samples, *, smoke=False, **sidecar_options):
    run_id = f"{re.sub(r'[^A-Za-z0-9._-]+', '-', branch)}__{config}__N{size}" + ("__smoke" if smoke else "")
    outdir = workdir / "runs" / run_id
    outdir.mkdir(parents=True)
    for i in range(samples + 1):
        sidecar = make_sidecar(rnd, branch, config, size, i, i == 0, first_frame=(i != 2), **sidecar_options)
        (outdir / f"sidecar-{i:02d}.json").write_text(json.dumps(sidecar))
    report = {"metrics": {
        "copyousError": {"values": [0] * samples, "description": "errors", "units": "n"},
        "frames": {"values": [rnd.random() for _ in range(samples)], "description": "f", "units": "ms"},
    }}  # fmt: skip
    (outdir / "report.json").write_text(json.dumps(report))
    meta = {"run_id": run_id, "status": "ok", "commit": "abc", "started_at": "2026-11-10T10:00:00Z",
            "block": {"run_id": run_id, "branch": branch, "config": config, "size": size, "warmup": True}}  # fmt: skip
    (outdir / "meta.json").write_text(json.dumps(meta))
    return outdir


@pytest.fixture
def workdir(tmp_path):
    """A campaign workdir holding every kind of block the harness produces."""
    work = tmp_path / "work"
    work.mkdir()
    rnd = random.Random(1)
    for branch in BRANCHES:
        for config in ("open", "scroll", "search"):
            write_block(work, rnd, branch, config, 500, 5)
        write_block(work, rnd, branch, "open", 200, 4)
        if branch != "merge/clipboard-item-placeholders-cached-async-items":
            write_block(work, rnd, branch, "open", 50, 4)
    # One branch whose N=200 open runs never recorded a first frame anywhere.
    write_block(work, rnd, "unlisted/branch", "open", 200, 3, bare=True)
    for branch in ("main", "feat/lazy-clipboard-items"):
        write_block(work, rnd, branch, "all", 500, 2, smoke=True)

    generate_datasets(work / "datasets", [50, 200, 500])
    environment = {
        "hostname": "host", "platform": "plat", "kernel": "6.x", "cpu_model": "cpu", "cpu_logical_count": 8,
        "gnome_shell": "GNOME Shell 49", "gjs": "1.86", "node": "v26", "pnpm": "12", "python": "3.13",
        "governors": {"cpu0": "performance"}, "thermal": [{"zone": "z", "type": "x86_pkg", "temp_c": 41.5}],
        "commits": dict.fromkeys(BRANCHES, "a" * 40),
    }  # fmt: skip
    campaign = {"campaign_id": "C1", "started_at": "s", "finished_at": "f", "environment": environment,
                "datasets": {"a": 1}, "settings": "[x]"}  # fmt: skip
    (work / "campaign.json").write_text(json.dumps(campaign))
    return work


def git(repo: Path, *args: str) -> str:
    env_args = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *env_args, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def git_repo(tmp_path):
    """A repository with a ``main`` commit, an extra branch and the Copyous marker files."""
    repo = tmp_path / "repo"
    (repo / "resources").mkdir(parents=True)
    (repo / "resources" / "metadata.json").write_text("{}")
    (repo / "node_modules").mkdir()
    (repo / "submodules" / "gnome-shell").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "add", "resources")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "branch", "feature")
    return repo


# A stand-in for ``dbus-run-session -- gnome-shell-test-tool ...``: it writes the sidecars and report the real
# benchmark would. FAKE_MODE selects: ok | bad (invalid data) | fail (exit 3) | hang (never finishes).
FAKE_SESSION_BODY = """
import json, os, sys, time
from pathlib import Path

mode = os.environ.get("FAKE_MODE", "ok")
args = sys.argv[1:]
if mode == "fail":
    sys.exit(3)
if mode == "hang":
    time.sleep(60)
warmup = "--perf-warmup" in args
iters = int(args[args.index("--test-iters") + 1]) + (1 if warmup else 0)
out = Path(os.environ["BENCH_OUTDIR"])
ctx = json.loads(Path(os.environ["BENCH_CONTEXT_FILE"]).read_text())
config, size = ctx["config"], ctx["expected_entries"]
good = mode != "bad"
for i in range(iters):
    phases = {
        "startup": {"data_ok": good, "data_count": size},
        "open": {"opened": good, "grabbed": good, "first_frame_ms": 1.0 if good else None},
    }
    if config in ("scroll", "all"):
        phases["scroll"] = {"first_frame_ms": 1.0 if good else None}
    if config in ("search", "all"):
        phases["search"] = {
            step: {"result_count": 1, "expected": 1 if good else 2, "ready_ok": good}
            for step in ("nomatch", "match", "clear")
        }
    sidecar = {"iteration": i, "warmup": warmup and i == 0, "phases": phases}
    (out / f"sidecar-{i:02d}.json").write_text(json.dumps(sidecar))
(out / "report.json").write_text(json.dumps({"metrics": {"copyousError": {"values": [0] * iters}}}))
"""


@pytest.fixture
def fake_session(tmp_path):
    path = tmp_path / "bin" / "dbus-run-session"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{FAKE_SESSION_BODY}")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_make(tmp_path, monkeypatch):
    """A ``make`` on PATH. FAKE_MAKE_FAIL fails the build, FAKE_MAKE_NOZIP builds nothing."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    make = bin_dir / "make"
    make.write_text(
        "#!/bin/sh\n"
        'if [ -n "$FAKE_MAKE_FAIL" ]; then echo boom; exit 1; fi\n'
        'if [ -n "$FAKE_MAKE_NOZIP" ]; then exit 0; fi\n'
        "mkdir -p dist && : > dist/copyous@boerdereinar.dev.zip\n"
    )
    make.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return make
