"""Toolchain, state log, runtime environment and environment snapshot."""

import json
import os
import subprocess
import sys

from copyous_bench.orchestration import environment
from copyous_bench.orchestration.dbus import DbusRuntime
from copyous_bench.orchestration.environment import (
    EnvironmentCollector,
    collect_governors,
    collect_thermal,
    cpu_model,
)
from copyous_bench.orchestration.runtime_env import SCRUBBED_VARIABLES, build_run_env, ensure_perf_helper
from copyous_bench.orchestration.state import StateLog
from copyous_bench.orchestration.toolchain import Toolchain


# -- toolchain ---------------------------------------------------------------
def test_toolchain_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("COPYOUS_NODE_BIN", str(tmp_path / "node"))
    monkeypatch.setenv("COPYOUS_PNPM_DIR", str(tmp_path / "pnpm"))
    toolchain = Toolchain.from_env()
    assert toolchain.node == tmp_path / "node" / "node"
    assert toolchain.pnpm == tmp_path / "pnpm" / "pnpm"
    assert toolchain.entries() == [str(tmp_path / "node"), str(tmp_path / "pnpm")]
    assert toolchain.prepend_to("/usr/bin") == f"{tmp_path / 'node'}:{tmp_path / 'pnpm'}:/usr/bin"


def test_toolchain_defaults_to_mise(monkeypatch):
    monkeypatch.delenv("COPYOUS_NODE_BIN", raising=False)
    monkeypatch.delenv("COPYOUS_PNPM_DIR", raising=False)
    assert "mise" in str(Toolchain.from_env().node_bin)


# -- state log ---------------------------------------------------------------
def test_state_log_roundtrip(tmp_path):
    log = StateLog(tmp_path / "deep" / "state.jsonl")
    assert log.completed() == set()
    log.append({"run_id": "a", "status": "ok"})
    log.append({"run_id": "b", "status": "failed"})
    with log.path.open("a") as handle:
        handle.write("not json\n")
    log.append({"run_id": "c", "status": "ok"})
    assert log.completed() == {"a", "c"}


# -- runtime environment -------------------------------------------------------
def test_perf_helper_wrapper(tmp_path):
    helper_dir = ensure_perf_helper(tmp_path)
    wrapper = helper_dir / "gnome-shell-perf-helper"
    assert os.access(wrapper, os.X_OK)
    assert "--idle-timeout=7200" in wrapper.read_text()


def test_build_run_env_scrubs_and_overrides(tmp_path, monkeypatch):
    for name in SCRUBBED_VARIABLES:
        monkeypatch.setenv(name, "leak")
    dbus = DbusRuntime("host", "/x/dbus-run-session", "/x/dbus-daemon", "/x")
    toolchain = Toolchain(tmp_path / "n", tmp_path / "p")
    env = build_run_env(tmp_path, dbus, toolchain, {"BENCH_SIZE": "50"})
    assert not any(name in env for name in SCRUBBED_VARIABLES)
    assert (env["LANG"], env["LC_ALL"], env["TZ"]) == ("C.utf8", "C.utf8", "UTC")
    assert env["BENCH_SIZE"] == "50"
    assert env["GNOME_SHELL_BUILDDIR"] == str(tmp_path / "perf-helper")
    assert env["PATH"].split(":")[:3] == [str(tmp_path / "dbus" / "bin"), "/x", str(tmp_path / "n")]


def test_build_run_env_without_extras_or_runtime_bin(tmp_path):
    dbus = DbusRuntime("host", "r", "d", "")
    env = build_run_env(tmp_path, dbus, Toolchain(tmp_path / "n", tmp_path / "p"))
    assert "" not in env["PATH"].split(":")[:3]


# -- machine snapshot ----------------------------------------------------------
def test_collect_thermal(tmp_path):
    (tmp_path / "thermal_zone0").mkdir()
    (tmp_path / "thermal_zone0" / "temp").write_text("42500\n")
    (tmp_path / "thermal_zone0" / "type").write_text("x86_pkg_temp\n")
    (tmp_path / "thermal_zone1").mkdir()  # no temp file
    (tmp_path / "thermal_zone2").mkdir()
    (tmp_path / "thermal_zone2" / "temp").write_text("garbage")
    assert collect_thermal(tmp_path) == [{"zone": "thermal_zone0", "type": "x86_pkg_temp", "temp_c": 42.5}]
    assert collect_thermal(tmp_path / "missing") == []


def test_collect_governors(tmp_path):
    (tmp_path / "cpu0" / "cpufreq").mkdir(parents=True)
    (tmp_path / "cpu0" / "cpufreq" / "scaling_governor").write_text("performance\n")
    (tmp_path / "cpu1").mkdir()
    (tmp_path / "cpufreq").mkdir()  # not a cpuN directory
    assert collect_governors(tmp_path) == {"cpu0": "performance"}


def test_cpu_model(tmp_path, monkeypatch):
    info = tmp_path / "cpuinfo"
    info.write_text("processor : 0\nModel Name\t: Test CPU @ 3GHz\n")
    assert cpu_model(info) == "Test CPU @ 3GHz"
    info.write_text("processor : 0\n")
    monkeypatch.setattr(environment.platform, "processor", lambda: "")
    assert cpu_model(info) == "unknown"
    monkeypatch.setattr(environment.platform, "processor", lambda: "arm")
    assert cpu_model(tmp_path / "missing") == "arm"


def _collector(tmp_path):
    return EnvironmentCollector(Toolchain(tmp_path / "n", tmp_path / "p"))


def test_version_probe(tmp_path):
    collector = _collector(tmp_path)
    assert collector._version([sys.executable, "-c", "print('v1\\nv2')"]) == "v1"
    assert collector._version([sys.executable, "-c", "import sys; print('on-stderr', file=sys.stderr)"]) == "on-stderr"
    assert collector._version([sys.executable, "-c", "pass"]) is None  # no output
    assert collector._version([sys.executable, "-c", "raise SystemExit(1)"]) is None
    assert collector._version([str(tmp_path / "missing")]) is None


def test_version_probe_timeout(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(environment.subprocess, "run", boom)
    assert _collector(tmp_path)._version(["anything"]) is None


def test_commit_lookup(git_repo, monkeypatch):
    commit = EnvironmentCollector._commit(git_repo, "main")
    assert len(commit) == 40
    assert EnvironmentCollector._commit(git_repo, "no-such-branch") == "unknown"

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 30)

    monkeypatch.setattr(environment, "run_command", boom)
    assert EnvironmentCollector._commit(git_repo, "main") == "unknown"


def test_collect_snapshot(git_repo, tmp_path):
    dbus = DbusRuntime("host", "/x/run", "/x/daemon", "/x")
    snapshot = _collector(tmp_path).collect(git_repo, ["main", "feature"], tmp_path / "work", dbus)
    assert set(snapshot["commits"]) == {"main", "feature"}
    assert snapshot["commits"]["main"] == snapshot["commits"]["feature"] != "unknown"
    assert snapshot["dbus"]["kind"] == "host"
    assert snapshot["git"].startswith("git version")
    assert snapshot["node"] is None  # the toolchain does not exist in the sandbox
    assert snapshot["repo"] == str(git_repo)
    assert snapshot["git_status"] == ""  # a clean checkout
    assert set(snapshot["locale"]) == {"LANG", "LC_ALL", "TZ"}
    json.dumps(snapshot)  # the snapshot must be serialisable
