"""Snapshot of the machine and toolchain the campaign runs on."""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..util import now_iso, read_text
from .dbus import DbusRuntime
from .process import run_command
from .toolchain import Toolchain

TEST_TOOL = "/usr/bin/gnome-shell-test-tool"


def collect_thermal(root: Path = Path("/sys/class/thermal")) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for zone in sorted(root.glob("thermal_zone*")):
        temp_path = zone / "temp"
        if not temp_path.exists():
            continue
        try:
            value = int(temp_path.read_text().strip())
        except (OSError, ValueError):
            continue
        zones.append({"zone": zone.name, "type": read_text(zone / "type").strip(), "temp_c": value / 1000.0})
    return zones


def collect_governors(root: Path = Path("/sys/devices/system/cpu")) -> dict[str, str]:
    governors: dict[str, str] = {}
    for cpu in sorted(root.glob("cpu[0-9]*")):
        path = cpu / "cpufreq/scaling_governor"
        if path.exists():
            governors[cpu.name] = read_text(path).strip()
    return governors


def cpu_model(cpuinfo: Path = Path("/proc/cpuinfo")) -> str:
    for line in read_text(cpuinfo).splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


class EnvironmentCollector:
    def __init__(self, toolchain: Toolchain) -> None:
        self.toolchain = toolchain
        self._env = {**os.environ, "PATH": toolchain.prepend_to(os.environ.get("PATH", ""))}

    def _version(self, cmd: Sequence[str]) -> str | None:
        try:
            proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=20, env=self._env)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        lines = (proc.stdout or proc.stderr).strip().splitlines()
        return lines[0] if lines else None

    @staticmethod
    def _commit(repo: Path, branch: str) -> str:
        try:
            proc = run_command(["git", "rev-parse", branch], cwd=repo, timeout=30)
        except subprocess.SubprocessError:
            return "unknown"
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"

    def collect(self, repo: Path, branches: Sequence[str], workdir: Path, dbus: DbusRuntime) -> dict[str, Any]:
        tools = {
            "gnome_shell": ["gnome-shell", "--version"],
            "test_tool": [TEST_TOOL, "--version"],
            "gjs": ["gjs", "--version"],
            "node": [str(self.toolchain.node), "--version"],
            "pnpm": [str(self.toolchain.pnpm), "--version"],
            "git": ["git", "--version"],
        }
        with ThreadPoolExecutor(max_workers=8) as pool:
            version_futures = {name: pool.submit(self._version, cmd) for name, cmd in tools.items()}
            commit_futures = {branch: pool.submit(self._commit, repo, branch) for branch in branches}
            status_future = pool.submit(run_command, ["git", "status", "--porcelain"], cwd=repo)

            versions = {name: future.result() for name, future in version_futures.items()}
            commits = {branch: future.result() for branch, future in commit_futures.items()}
            git_status = status_future.result().stdout.strip()

        return {
            "captured_at": now_iso(),
            "hostname": platform.node(),
            "platform": platform.platform(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_model": cpu_model(),
            "cpu_logical_count": os.cpu_count(),
            **versions,
            "governors": collect_governors(),
            "thermal": collect_thermal(),
            "dbus": dbus.to_dict(),
            "commits": commits,
            "repo": str(repo),
            "workdir": str(workdir),
            "git_status": git_status,
            "locale": {name: os.environ.get(name) for name in ("LANG", "LC_ALL", "TZ")},
        }
