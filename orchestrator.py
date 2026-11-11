#!/usr/bin/env python3
"""Run the Copyous branch benchmark matrix.

The script prepares one detached Git worktree per branch, builds every branch
with identical flags, runs gnome-shell-test-tool in a private D-Bus session,
and records raw sidecar files per iteration.  Ingestion and analysis are
separate scripts (``ingest.py`` and ``analyze.py``) so raw runs can be replayed
without rerunning the shell.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BENCH_DIR = Path(__file__).resolve().parent
UUID = "copyous@boerdereinar.dev"

# The harness is branch-independent: it lives outside the checkout it tests and
# reaches it through --repo (or COPYOUS_REPO).  Results and worktrees also live
# outside that checkout so switching branches never hides or invalidates them.
DEFAULT_REPO = Path(os.environ.get("COPYOUS_REPO", BENCH_DIR.parent / "copyous")).expanduser()
DEFAULT_WORKDIR = Path(
	os.environ.get("COPYOUS_BENCH_WORKDIR", Path(tempfile.gettempdir()) / "copyous-bench")
).expanduser()
DEFAULT_RESULTS = Path(os.environ.get("COPYOUS_BENCH_RESULTS", BENCH_DIR / "results")).expanduser()

DEFAULT_BRANCHES = [
    "main",
    "feat/clipboard-item-placeholders",
    "feat/lazy-clipboard-items",
    "perf/cheaper-cached-async-items",
    "merge/clipboard-item-placeholders-cached-async-items",
]

DEFAULT_WORKDIR = Path("/tmp/opencode/copyous-bench")

CORE_CONFIGS = ["open", "scroll", "search"]
SCALING_CONFIGS = ["open"]

DATASET_SIZES = [50, 200, 500]

MISE_NODE_BIN = Path.home() / ".local/share/mise/installs/node/26.13.0/bin"
MISE_PNPM_DIR = Path.home() / ".local/share/mise/installs/pnpm/12.15.0"


def now_iso() -> str:
	return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(branch: str) -> str:
	return re.sub(r"[^A-Za-z0-9._-]+", "-", branch)


def which_or_none(name: str, path: str | None = None) -> str | None:
	return shutil.which(name, path=path)


def read_text(path: Path) -> str:
	try:
		return path.read_text(errors="replace")
	except OSError:
		return ""


def run(
	cmd: list[str],
	*,
	cwd: Path | None = None,
	env: dict[str, str] | None = None,
	timeout: float | None = None,
	log_path: Path | None = None,
	check: bool = False,
) -> subprocess.CompletedProcess[str]:
	if log_path is not None:
		log_path.parent.mkdir(parents=True, exist_ok=True)
		with log_path.open("w") as handle:
			proc = subprocess.run(
				cmd,
				cwd=str(cwd) if cwd else None,
				env=env,
				stdout=handle,
				stderr=subprocess.STDOUT,
				text=True,
				timeout=timeout,
			)
	else:
		proc = subprocess.run(
			cmd,
			cwd=str(cwd) if cwd else None,
			env=env,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			timeout=timeout,
		)

	if check and proc.returncode != 0:
		raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout or ''}")
	return proc


def _kill_process_group(proc: subprocess.Popen[Any]) -> None:
	try:
		os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
	except ProcessLookupError:
		pass
	try:
		proc.wait(timeout=20)
	except subprocess.TimeoutExpired:
		try:
			os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
		except ProcessLookupError:
			pass
		proc.wait()


def run_shell(
	cmd: list[str],
	*,
	cwd: Path,
	env: dict[str, str],
	timeout: float,
	log_path: Path,
	progress: Callable[[], int] | None = None,
	stall_timeout: float | None = None,
) -> tuple[int | None, bool, bool]:
	"""Run a command in its own process group.

	Returns ``(returncode, timed_out, stalled)``.  A stalled run is one where
	``progress()`` stopped increasing for ``stall_timeout`` seconds; this is how
	we detect a GNOME Shell that hangs during startup before it ever reaches the
	automation script's ``run()`` function.
	"""
	log_path.parent.mkdir(parents=True, exist_ok=True)
	with log_path.open("wb") as handle:
		proc = subprocess.Popen(
			cmd,
			cwd=str(cwd),
			env=env,
			stdout=handle,
			stderr=subprocess.STDOUT,
			start_new_session=True,
		)
		start = time.monotonic()
		last_progress = start
		last_value = progress() if progress else None
		while True:
			try:
				return proc.wait(timeout=1.0), False, False
			except subprocess.TimeoutExpired:
				pass

			now = time.monotonic()
			if now - start > timeout:
				_kill_process_group(proc)
				return None, True, False

			if progress is not None and stall_timeout is not None:
				value = progress()
				if value != last_value:
					last_value = value
					last_progress = now
				elif now - last_progress > stall_timeout:
					_kill_process_group(proc)
					return None, False, True


def json_dump(path: Path, value: Any) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def json_load(path: Path) -> Any:
	return json.loads(path.read_text())


def collect_thermal() -> list[dict[str, Any]]:
	zones: list[dict[str, Any]] = []
	for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
		temp_path = zone / "temp"
		if not temp_path.exists():
			continue
		try:
			value = int(temp_path.read_text().strip())
		except (OSError, ValueError):
			continue
		zones.append({"zone": zone.name, "type": read_text(zone / "type").strip(), "temp_c": value / 1000.0})
	return zones


def collect_governors() -> dict[str, str]:
	governors: dict[str, str] = {}
	for cpu in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*")):
		path = cpu / "cpufreq/scaling_governor"
		if path.exists():
			governors[cpu.name] = read_text(path).strip()
	return governors


def cpu_model() -> str:
	for line in read_text(Path("/proc/cpuinfo")).splitlines():
		if line.lower().startswith("model name"):
			return line.split(":", 1)[1].strip()
	return platform.processor() or "unknown"


def collect_environment(repo: Path, branches: list[str], workdir: Path, dbus_info: dict[str, Any]) -> dict[str, Any]:
	path = os.environ.get("PATH", "")
	node_bin = str(MISE_NODE_BIN)
	pnpm_dir = str(MISE_PNPM_DIR)
	path_with_tools = f"{node_bin}:{pnpm_dir}:{path}"

	def version(cmd: list[str]) -> str | None:
		try:
			proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env={**os.environ, "PATH": path_with_tools})
			if proc.returncode == 0:
				return (proc.stdout or proc.stderr).strip().splitlines()[0]
		except (OSError, subprocess.SubprocessError):
			pass
		return None

	commits: dict[str, str] = {}
	for branch in branches:
		try:
			commits[branch] = run(["git", "rev-parse", branch], cwd=repo).stdout.strip()
		except subprocess.SubprocessError:
			commits[branch] = "unknown"

	return {
		"captured_at": now_iso(),
		"hostname": platform.node(),
		"platform": platform.platform(),
		"kernel": platform.release(),
		"machine": platform.machine(),
		"python": platform.python_version(),
		"cpu_model": cpu_model(),
		"cpu_logical_count": os.cpu_count(),
		"gnome_shell": version(["gnome-shell", "--version"]),
		"test_tool": version(["/usr/bin/gnome-shell-test-tool", "--version"]),
		"gjs": version(["gjs", "--version"]),
		"node": version([str(MISE_NODE_BIN / "node"), "--version"]),
		"pnpm": version([str(MISE_PNPM_DIR / "pnpm"), "--version"]),
		"git": version(["git", "--version"]),
		"governors": collect_governors(),
		"thermal": collect_thermal(),
		"dbus": dbus_info,
		"commits": commits,
		"repo": str(repo),
		"workdir": str(workdir),
		"git_status": run(["git", "status", "--porcelain"], cwd=repo).stdout.strip(),
		"locale": {
			"LANG": os.environ.get("LANG"),
			"LC_ALL": os.environ.get("LC_ALL"),
			"TZ": os.environ.get("TZ"),
		},
	}


def ensure_dataset(workdir: Path, sizes: list[int], force: bool) -> dict[str, Any]:
	out_dir = workdir / "datasets"
	manifest_path = out_dir / "datasets.json"
	if force or not manifest_path.exists():
		cmd = [
			sys.executable,
			str(BENCH_DIR / "fixtures" / "generate_dataset.py"),
			"--out-dir",
			str(out_dir),
			"--sizes",
			*[str(size) for size in sizes],
		]
		if force:
			cmd.append("--force")
		run(cmd, check=True)
	manifest = json_load(manifest_path)
	for size in sizes:
		entry = manifest["sizes"].get(str(size))
		if entry is None:
			raise RuntimeError(f"Dataset manifest does not contain N={size}")
		path = Path(entry["path"])
		if not path.exists():
			raise RuntimeError(f"Dataset file missing: {path}")
	return manifest


def ensure_hljs(workdir: Path, force: bool) -> Path:
	out_dir = workdir / "assets" / "hljs"
	expected = [out_dir / "highlight.min.js"] + [
		out_dir / "languages" / f"{language}.min.js" for language in ["typescript", "python", "rust", "json", "bash"]
	]
	if force or not all(path.exists() for path in expected):
		run(["bash", str(BENCH_DIR / "fixtures" / "build_hljs.sh"), str(out_dir)], check=True)
	if not all(path.exists() for path in expected):
		raise RuntimeError(f"highlight.js assets incomplete in {out_dir}")
	return out_dir


def find_dbus_runtime(workdir: Path, override: str | None) -> dict[str, Any]:
	if override:
		dbus_run_session = Path(override)
		if not dbus_run_session.exists():
			raise RuntimeError(f"--dbus-run-session not found: {dbus_run_session}")
		runtime_bin = dbus_run_session.parent
		dbus_daemon = runtime_bin / "dbus-daemon"
		libapparmor = None
	else:
		host_run_session = which_or_none("dbus-run-session")
		host_daemon = which_or_none("dbus-daemon")
		if host_run_session and host_daemon:
			return {
				"kind": "host",
				"run_session": host_run_session,
				"daemon": host_daemon,
				"runtime_bin": str(Path(host_run_session).parent),
				"shim": None,
			}

		patterns = [
			"/var/lib/flatpak/runtime/org.gnome.Platform/x86_64/*/*/files/bin/dbus-run-session",
			"/var/lib/flatpak/runtime/org.gnome.Platform/*/*/*/files/bin/dbus-run-session",
			"/var/lib/flatpak/runtime/org.gnome.Sdk/x86_64/*/*/files/bin/dbus-run-session",
		]
		candidates: list[Path] = []
		for pattern in patterns:
			candidates.extend(Path("/").glob(pattern.lstrip("/")))
		candidates = [path for path in candidates if path.exists()]
		if not candidates:
			raise RuntimeError("Could not find dbus-run-session on the host or in a Flatpak runtime")
		# Prefer the newest runtime (numeric version component before the hash).
		def version_key(path: Path) -> tuple[int, str]:
			parts = path.parts
			for part in parts:
				if part.isdigit():
					return (int(part), str(path))
			return (0, str(path))
		candidates.sort(key=version_key, reverse=True)
		dbus_run_session = candidates[0]
		runtime_bin = dbus_run_session.parent
		dbus_daemon = runtime_bin / "dbus-daemon"
		libapparmor = None
		# runtime_bin is <runtime>/files/bin; the libraries live in
		# <runtime>/files/lib/<triplet>/.
		runtime_files = runtime_bin.parent
		for candidate in runtime_files.glob("lib/*/libapparmor.so.1"):
			libapparmor = candidate
			break

	shim_dir = workdir / "dbus"
	shim_bin = shim_dir / "bin"
	shim_lib = shim_dir / "lib"
	shim_bin.mkdir(parents=True, exist_ok=True)
	shim_lib.mkdir(parents=True, exist_ok=True)

	if libapparmor is not None:
		link = shim_lib / "libapparmor.so.1"
		if link.is_symlink() or link.exists():
			link.unlink()
		link.symlink_to(libapparmor)

	shim = shim_bin / "dbus-daemon"
	shim.write_text(
		"#!/bin/sh\n"
		f'LD_LIBRARY_PATH="{shim_lib}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}" '
		f'exec "{dbus_daemon}" "$@"\n'
	)
	shim.chmod(0o755)

	return {
		"kind": "flatpak-shim",
		"run_session": str(dbus_run_session),
		"daemon": str(dbus_daemon),
		"runtime_bin": str(runtime_bin),
		"shim": str(shim),
		"libapparmor": str(libapparmor) if libapparmor else None,
	}


def worktree_path(workdir: Path, branch: str) -> Path:
	return workdir / "worktrees" / slug(branch)


def ensure_worktree(repo: Path, workdir: Path, branch: str, recreate: bool) -> Path:
	path = worktree_path(workdir, branch)
	# Drop registrations whose directory disappeared (e.g. after a reboot wiped
	# /tmp); otherwise `git worktree add` refuses to recreate them.
	run(["git", "worktree", "prune"], cwd=repo)
	existing = run(["git", "worktree", "list", "--porcelain"], cwd=repo).stdout
	registered = f"worktree {path}" in existing and path.exists()

	if recreate and path.exists():
		run(["git", "worktree", "remove", "--force", str(path)], cwd=repo)
		registered = False
		if path.exists():
			shutil.rmtree(path)

	if not registered and not path.exists():
		path.parent.mkdir(parents=True, exist_ok=True)
		run(["git", "worktree", "add", "--force", "--detach", str(path), branch], cwd=repo, check=True)

	if not path.exists():
		raise RuntimeError(f"Worktree for {branch} was not created: {path}")

	# Share the main checkout's dependencies and GNOME Shell sources.  Git
	# creates empty directories for uninitialised submodules in a new worktree,
	# so only reuse a directory that actually contains the theme sources.
	node_modules = path / "node_modules"
	if not node_modules.is_symlink() and not node_modules.exists():
		node_modules.symlink_to(repo / "node_modules", target_is_directory=True)

	submodules = path / "submodules"
	submodules.mkdir(parents=True, exist_ok=True)
	gnome_shell = submodules / "gnome-shell"
	gnome_shell_ok = gnome_shell.is_dir() and (gnome_shell / "data" / "theme" / "gnome-shell-sass").is_dir()
	if not gnome_shell_ok:
		if gnome_shell.is_symlink() or gnome_shell.exists():
			if gnome_shell.is_dir() and not gnome_shell.is_symlink():
				shutil.rmtree(gnome_shell)
			else:
				gnome_shell.unlink()
		gnome_shell.symlink_to(repo / "submodules" / "gnome-shell", target_is_directory=True)

	return path


def build_worktree(path: Path, log_path: Path, skip: bool) -> Path:
	zip_path = path / "dist" / f"{UUID}.zip"
	if skip and zip_path.exists():
		return zip_path

	env = os.environ.copy()
	env["PATH"] = f"{MISE_NODE_BIN}:{MISE_PNPM_DIR}:{env.get('PATH', '')}"
	env.pop("RELEASE", None)
	# The worktree shares the main checkout's node_modules.  Stop pnpm from
	# running the package's `install` lifecycle script (which would try to
	# install the extension into the host session) when it verifies deps.
	env["npm_config_verify_deps_before_run"] = "false"
	proc = run(["make", "build"], cwd=path, env=env, timeout=1800, log_path=log_path)
	if proc.returncode != 0:
		raise RuntimeError(f"Build failed for {path.name}; see {log_path}")
	if not zip_path.exists():
		raise RuntimeError(f"Build did not produce {zip_path}")
	return zip_path


@dataclass
class Block:
	branch: str
	config: str
	size: int
	samples: int
	warmup: bool
	smoke: bool = False

	@property
	def run_id(self) -> str:
		suffix = "__smoke" if self.smoke else ""
		return f"{slug(self.branch)}__{self.config}__N{self.size}{suffix}"

	@property
	def iterations(self) -> int:
		return self.samples + (1 if self.warmup else 0)

	def as_dict(self) -> dict[str, Any]:
		return {
			"run_id": self.run_id,
			"branch": self.branch,
			"config": self.config,
			"size": self.size,
			"samples": self.samples,
			"warmup": self.warmup,
			"iterations": self.iterations,
			"smoke": self.smoke,
		}


def build_plan(
	branches: list[str],
	sizes: list[int],
	core_samples: int,
	scaling_samples: int,
	warmup: bool,
	seed: int,
) -> list[Block]:
	blocks: list[Block] = []
	for branch in branches:
		for config in CORE_CONFIGS:
			blocks.append(Block(branch=branch, config=config, size=500, samples=core_samples, warmup=warmup))
		for size in sizes:
			if size == 500:
				continue
			for config in SCALING_CONFIGS:
				blocks.append(Block(branch=branch, config=config, size=size, samples=scaling_samples, warmup=warmup))

	random.Random(seed).shuffle(blocks)
	return blocks


def smoke_plan(warmup: bool) -> list[Block]:
	return [
		Block(branch="main", config="all", size=500, samples=2, warmup=warmup, smoke=True),
		Block(branch="feat/lazy-clipboard-items", config="all", size=500, samples=2, warmup=warmup, smoke=True),
	]


def ensure_perf_helper(workdir: Path) -> Path:
	"""Create a wrapper for gnome-shell-perf-helper with a long idle timeout.

	The helper exits after 30 seconds by default.  A benchmark block takes
	longer than that, after which gnome-shell cannot call Exit() and exits
	with an error before gnome-shell-test-tool writes its report.  The shell
	spawns ``$GNOME_SHELL_BUILDDIR/gnome-shell-perf-helper``, so point that at
	a wrapper that raises the timeout.
	"""
	helper_dir = workdir / "perf-helper"
	helper_dir.mkdir(parents=True, exist_ok=True)
	wrapper = helper_dir / "gnome-shell-perf-helper"
	wrapper.write_text(
		"#!/bin/sh\n"
		f'printf "%s %s %s\\n" "$(date -Is)" "$$" "$*" >> "{helper_dir}/spawn.log"\n'
		'exec /usr/libexec/gnome-shell-perf-helper --idle-timeout=7200 "$@"\n'
	)
	wrapper.chmod(0o755)
	return helper_dir


def base_env(workdir: Path, dbus_info: dict[str, Any], extra: dict[str, str] | None = None) -> dict[str, str]:
	env = os.environ.copy()
	path_parts = [
		str(workdir / "dbus" / "bin"),
		dbus_info.get("runtime_bin", ""),
		str(MISE_NODE_BIN),
		str(MISE_PNPM_DIR),
		env.get("PATH", ""),
	]
	env["PATH"] = ":".join(part for part in path_parts if part)

	for key in [
		"GSETTINGS_SCHEMA_DIR",
		"DEBUG_COPYOUS_DBPATH",
		"DEBUG_COPYOUS_SCHEMA",
		"DEBUG_COPYOUS_GDA_VERSION",
		"MUTTER_DEBUG_DUMMY_MODE_SPECS",
		"CLUTTER_TEXT_DIRECTION",
		"DISPLAY",
		"WAYLAND_DISPLAY",
	]:
		env.pop(key, None)

	env["LANG"] = "C.utf8"
	env["LC_ALL"] = "C.utf8"
	env["TZ"] = "UTC"
	env["GNOME_SHELL_BUILDDIR"] = str(ensure_perf_helper(workdir))

	if extra:
		env.update(extra)
	return env


def validate_block(
	block: Block,
	outdir: Path,
	expected_entries: int,
	token_matches: int,
) -> tuple[bool, list[str], dict[str, Any]]:
	problems: list[str] = []
	report_path = outdir / "report.json"
	sidecars = sorted(outdir.glob("sidecar-*.json"))
	summary: dict[str, Any] = {
		"report": report_path.exists(),
		"sidecars": len(sidecars),
		"expected_sidecars": block.iterations,
		"errors": [],
	}

	if not report_path.exists():
		problems.append("report.json missing")
	else:
		try:
			json_load(report_path)
		except (OSError, json.JSONDecodeError) as error:
			problems.append(f"report.json unreadable: {error}")

	if len(sidecars) < block.iterations:
		problems.append(f"expected {block.iterations} sidecars, found {len(sidecars)}")

	for sidecar_path in sidecars:
		try:
			sidecar = json_load(sidecar_path)
		except (OSError, json.JSONDecodeError) as error:
			problems.append(f"{sidecar_path.name} unreadable: {error}")
			continue

		if sidecar.get("error"):
			problems.append(f"{sidecar_path.name} error: {sidecar['error']}")
			continue

		phases = sidecar.get("phases", {})
		startup = phases.get("startup", {})
		if not startup.get("data_ok"):
			problems.append(f"{sidecar_path.name}: history did not load")
		if startup.get("data_count") != expected_entries:
			problems.append(
				f"{sidecar_path.name}: data_count {startup.get('data_count')} != {expected_entries}"
			)

		open_phase = phases.get("open", {})
		if not open_phase.get("opened"):
			problems.append(f"{sidecar_path.name}: dialog did not open")
		if not open_phase.get("grabbed"):
			problems.append(f"{sidecar_path.name}: dialog did not grab input")
		if open_phase.get("first_frame_ms") is None:
			problems.append(f"{sidecar_path.name}: no open frame")

		if block.config in ("scroll", "all"):
			scroll = phases.get("scroll", {})
			if not scroll:
				problems.append(f"{sidecar_path.name}: scroll phase missing")
			elif scroll.get("first_frame_ms") is None:
				problems.append(f"{sidecar_path.name}: no scroll frame")

		if block.config in ("search", "all"):
			search = phases.get("search", {})
			if not search:
				problems.append(f"{sidecar_path.name}: search phase missing")
			else:
				for name in ("nomatch", "match", "clear"):
					step = search.get(name, {})
					if step.get("result_count") != step.get("expected"):
						problems.append(
							f"{sidecar_path.name}: search {name} count {step.get('result_count')} "
							f"!= {step.get('expected')}"
						)
					if not step.get("ready_ok"):
						problems.append(f"{sidecar_path.name}: search {name} did not become ready")

		if sidecar.get("warmup"):
			continue

		# Sanity-check the report's headline metric as well.
		if report_path.exists():
			try:
				report = json_load(report_path)
				metric = report.get("metrics", {}).get("copyousError", {})
				values = metric.get("values", [])
				if values and any(value != 0 for value in values):
					problems.append(f"report copyousError values: {values}")
			except (OSError, json.JSONDecodeError):
				pass

	summary["errors"] = problems
	summary["expected_entries"] = expected_entries
	summary["token_matches"] = token_matches
	return not problems, problems, summary


def write_block_meta(
	outdir: Path,
	block: Block,
	*,
	status: str,
	started_at: str,
	finished_at: str,
	duration_s: float,
	command: list[str],
	returncode: int | None,
	timed_out: bool,
	stalled: bool,
	validation: dict[str, Any],
	commit: str,
	zip_path: Path,
) -> None:
	json_dump(
		outdir / "meta.json",
		{
			"run_id": block.run_id,
			"block": block.as_dict(),
			"status": status,
			"started_at": started_at,
			"finished_at": finished_at,
			"duration_s": duration_s,
			"command": command,
			"returncode": returncode,
			"timed_out": timed_out,
			"stalled": stalled,
			"commit": commit,
			"extension_zip": str(zip_path),
			"validation": validation,
		},
	)


def append_state(workdir: Path, record: dict[str, Any]) -> None:
	path = workdir / "state.jsonl"
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("a") as handle:
		handle.write(json.dumps(record, sort_keys=True) + "\n")


def completed_runs(workdir: Path) -> set[str]:
	done: set[str] = set()
	path = workdir / "state.jsonl"
	if not path.exists():
		return done
	for line in path.read_text().splitlines():
		try:
			record = json.loads(line)
		except json.JSONDecodeError:
			continue
		if record.get("status") == "ok":
			done.add(record.get("run_id"))
	return done


def run_block(
	block: Block,
	*,
	workdir: Path,
	worktrees: dict[str, Path],
	zips: dict[str, Path],
	datasets: dict[str, Any],
	dbus_info: dict[str, Any],
	settings_file: Path,
	timeout: float,
	stall_timeout: float,
	resume: bool,
) -> tuple[bool, dict[str, Any]]:
	outdir = workdir / "runs" / block.run_id
	dataset = datasets["sizes"][str(block.size)]
	commit = datasets.get("commits", {}).get(block.branch, "unknown")

	if resume and (outdir / "meta.json").exists():
		try:
			meta = json_load(outdir / "meta.json")
			if meta.get("status") == "ok":
				print(f"  skip {block.run_id} (already ok)")
				return True, meta
		except (OSError, json.JSONDecodeError):
			pass

	if outdir.exists():
		shutil.rmtree(outdir)
	outdir.mkdir(parents=True, exist_ok=True)

	context = {
		"campaign_id": datasets.get("campaign_id"),
		"run_id": block.run_id,
		"branch": block.branch,
		"commit": commit,
		"config": block.config,
		"size": block.size,
		"expected_entries": dataset["entries"],
		"token": dataset["token"],
		"token_matches": dataset["token_matches"],
		"no_match_token": dataset["no_match_token"],
		"sample_count": block.samples,
		"warmup": block.warmup,
	}
	context_path = outdir / "context.json"
	json_dump(context_path, context)

	env = base_env(
		workdir,
		dbus_info,
		{
			"BENCH_OUTDIR": str(outdir),
			"BENCH_DATASET": dataset["path"],
			"BENCH_HLJS_DIR": str(workdir / "assets" / "hljs"),
			"BENCH_CONTEXT_FILE": str(context_path),
			"BENCH_SETTINGS_FILE": str(settings_file),
			"BENCH_SIZE": str(block.size),
			"BENCH_EXT_UUID": UUID,
			"BENCH_RUN_ID": block.run_id,
		},
	)

	report_path = outdir / "report.json"
	log_path = outdir / "run.log"
	command = [
		dbus_info["run_session"],
		"--",
		"/usr/bin/gnome-shell-test-tool",
		str(BENCH_DIR / "automation" / "bench.js"),
		"--headless",
		"--disable-animations",
		"--test-iters",
		str(block.samples),
		"--perf-output",
		str(report_path),
		"--extension",
		str(zips[block.branch]),
		"--wrap",
		str(BENCH_DIR / "wrap" / "wrap.sh"),
	]
	if block.warmup:
		command.append("--perf-warmup")

	started_at = now_iso()
	start = time.monotonic()
	print(f"  run {block.run_id} ({block.iterations} launches)")

	def sidecar_count() -> int:
		return len(list(outdir.glob("sidecar-*.json")))

	returncode, timed_out, stalled = run_shell(
		command,
		cwd=worktrees[block.branch],
		env=env,
		timeout=timeout,
		log_path=log_path,
		progress=sidecar_count,
		stall_timeout=stall_timeout,
	)
	duration_s = time.monotonic() - start
	finished_at = now_iso()

	ok, problems, validation = validate_block(
		block,
		outdir,
		expected_entries=dataset["entries"],
		token_matches=dataset["token_matches"],
	)
	if timed_out:
		problems = [*problems, f"timed out after {timeout:.0f}s"]
		ok = False
	if stalled:
		problems = [
			*problems,
			f"stalled: no new sidecar for {stall_timeout:.0f}s (shell hung during startup)",
		]
		ok = False
	if returncode not in (0, None):
		problems = [*problems, f"exit code {returncode}"]
		ok = False
	if problems:
		ok = False
		validation["errors"] = problems

	status = "ok" if ok else "failed"
	write_block_meta(
		outdir,
		block,
		status=status,
		started_at=started_at,
		finished_at=finished_at,
		duration_s=duration_s,
		command=command,
		returncode=returncode,
		timed_out=timed_out,
		stalled=stalled,
		validation=validation,
		commit=commit,
		zip_path=zips[block.branch],
	)

	record = {
		"run_id": block.run_id,
		"status": status,
		"started_at": started_at,
		"finished_at": finished_at,
		"duration_s": duration_s,
		"returncode": returncode,
		"timed_out": timed_out,
		"stalled": stalled,
		"errors": validation.get("errors", []),
	}
	append_state(workdir, record)

	if ok:
		print(f"    ok in {duration_s:.1f}s")
	else:
		print(f"    FAILED in {duration_s:.1f}s")
		for problem in validation.get("errors", [])[:8]:
			print(f"      - {problem}")
		if len(validation.get("errors", [])) > 8:
			print(f"      ... {len(validation['errors']) - 8} more")

	return ok, record


def validate_repo(repo: Path) -> None:
	if not (repo / ".git").exists():
		raise SystemExit(f"--repo is not a Git checkout: {repo}")
	metadata = repo / "resources" / "metadata.json"
	if not metadata.is_file():
		raise SystemExit(f"--repo does not look like the Copyous extension (missing {metadata})")


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--repo",
		type=Path,
		default=DEFAULT_REPO,
		help="Copyous checkout to benchmark (default: $COPYOUS_REPO or ../copyous)",
	)
	parser.add_argument(
		"--workdir",
		type=Path,
		default=DEFAULT_WORKDIR,
		help="Scratch directory for worktrees and raw runs (default: $COPYOUS_BENCH_WORKDIR or <tmp>/copyous-bench)",
	)
	parser.add_argument(
		"--results-dir",
		type=Path,
		default=DEFAULT_RESULTS,
		help="Output directory for results.db, summary and plots (default: $COPYOUS_BENCH_RESULTS or <bench>/results)",
	)
	parser.add_argument("--branches", nargs="+", default=DEFAULT_BRANCHES)
	parser.add_argument("--sizes", nargs="+", type=int, default=DATASET_SIZES)
	parser.add_argument("--core-samples", type=int, default=6)
	parser.add_argument("--scaling-samples", type=int, default=4)
	parser.add_argument("--seed", type=int, default=20261108)
	parser.add_argument("--timeout", type=float, default=900.0, help="Timeout per block in seconds")
	parser.add_argument(
		"--stall-timeout",
		type=float,
		default=150.0,
		help="Kill a block when no new sidecar appears for this many seconds",
	)
	parser.add_argument("--retries", type=int, default=2, help="Retries per failed block")
	parser.add_argument("--retry-delay", type=float, default=10.0, help="Seconds between block retries")
	parser.add_argument("--only", default=None, help="Comma-separated run_id substrings to run")
	parser.add_argument("--warmup", dest="warmup", action="store_true", default=True)
	parser.add_argument("--no-warmup", dest="warmup", action="store_false")
	parser.add_argument("--resume", action="store_true", help="Skip blocks whose meta.json is ok")
	parser.add_argument("--recreate-worktrees", action="store_true")
	parser.add_argument("--skip-build", action="store_true")
	parser.add_argument("--regenerate-datasets", action="store_true")
	parser.add_argument("--rebuild-hljs", action="store_true")
	parser.add_argument("--dbus-run-session", default=None)
	parser.add_argument("--skip-smoke", action="store_true")
	parser.add_argument("--smoke-only", action="store_true")
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--no-post", action="store_true")
	parser.add_argument("--campaign-id", default=None)
	args = parser.parse_args()

	repo: Path = args.repo.expanduser().resolve()
	validate_repo(repo)
	workdir: Path = args.workdir.expanduser().resolve()
	workdir.mkdir(parents=True, exist_ok=True)

	results_dir: Path = args.results_dir.expanduser().resolve()
	if results_dir.is_relative_to(repo):
		raise SystemExit(f"--results-dir must be outside --repo so results survive branch switches: {results_dir}")
	if workdir.is_relative_to(repo):
		raise SystemExit(f"--workdir must be outside --repo so worktrees do not pollute the checkout: {workdir}")

	branches: list[str] = args.branches
	sizes: list[int] = args.sizes
	if 500 not in sizes:
		raise SystemExit("--sizes must include 500 for the core matrix")

	print("== Environment ==")
	dbus_info = find_dbus_runtime(workdir, args.dbus_run_session)
	print(f"dbus: {dbus_info['kind']} ({dbus_info['run_session']})")

	environment = collect_environment(repo, branches, workdir, dbus_info)
	json_dump(workdir / "environment.json", environment)

	datasets = ensure_dataset(workdir, sizes, args.regenerate_datasets)
	campaign_id = args.campaign_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
	datasets["campaign_id"] = campaign_id
	datasets["commits"] = environment["commits"]
	datasets["seed"] = args.seed
	datasets["core_samples"] = args.core_samples
	datasets["scaling_samples"] = args.scaling_samples
	datasets["warmup"] = args.warmup
	datasets["branches"] = branches
	json_dump(workdir / "datasets" / "datasets.json", datasets)

	hljs_dir = ensure_hljs(workdir, args.rebuild_hljs)
	print(f"highlight.js: {hljs_dir}")

	settings_file = BENCH_DIR / "wrap" / "settings.ini"
	if not settings_file.exists():
		raise SystemExit(f"Settings file missing: {settings_file}")

	print("== Worktrees ==")
	worktrees: dict[str, Path] = {}
	zips: dict[str, Path] = {}
	for branch in branches:
		path = ensure_worktree(repo, workdir, branch, args.recreate_worktrees)
		worktrees[branch] = path
		print(f"{branch} -> {path}")

	print("== Builds ==")
	for branch in branches:
		log_path = workdir / "build" / f"{slug(branch)}.log"
		print(f"build {branch}")
		zips[branch] = build_worktree(worktrees[branch], log_path, args.skip_build)
		print(f"  {zips[branch]}")

	smoke_blocks = smoke_plan(args.warmup)
	full_blocks = build_plan(
		branches,
		sizes,
		args.core_samples,
		args.scaling_samples,
		args.warmup,
		args.seed,
	)
	if args.only:
		needles = [item.strip() for item in args.only.split(",") if item.strip()]
		smoke_blocks = [
			block for block in smoke_blocks if any(needle in block.run_id for needle in needles)
		]
		full_blocks = [
			block for block in full_blocks if any(needle in block.run_id for needle in needles)
		]
		if not smoke_blocks and not full_blocks:
			raise SystemExit(f"--only matched no blocks: {args.only}")

	if args.dry_run:
		print("== Plan ==")
		for block in smoke_blocks:
			print(f"smoke {block.as_dict()}")
		for block in full_blocks:
			print(f"full  {block.as_dict()}")
		print(f"total full launches: {sum(block.iterations for block in full_blocks)}")
		return 0

	campaign = {
		"campaign_id": campaign_id,
		"started_at": now_iso(),
		"finished_at": None,
		"environment": environment,
		"datasets": datasets,
		"settings_file": str(settings_file),
		"settings": read_text(settings_file),
		"smoke": [block.as_dict() for block in smoke_blocks],
		"plan": [block.as_dict() for block in full_blocks],
	}
	json_dump(workdir / "campaign.json", campaign)

	def run_blocks(blocks: list[Block], label: str) -> bool:
		total = len(blocks)
		all_ok = True
		for index, block in enumerate(blocks, start=1):
			print(f"== {label} {index}/{total}: {block.run_id} ==")
			ok = False
			for attempt in range(1, args.retries + 2):
				ok, _ = run_block(
					block,
					workdir=workdir,
					worktrees=worktrees,
					zips=zips,
					datasets=datasets,
					dbus_info=dbus_info,
					settings_file=settings_file,
					timeout=args.timeout,
					stall_timeout=args.stall_timeout,
					resume=args.resume,
				)
				if ok:
					break
				if attempt <= args.retries:
					print(
						f"    retry {attempt}/{args.retries} in {args.retry_delay:.0f}s "
						f"(the shell sometimes hangs during startup)"
					)
					time.sleep(args.retry_delay)
			all_ok = all_ok and ok
		return all_ok

	if not args.skip_smoke:
		print("== Smoke gate ==")
		if not run_blocks(smoke_blocks, "smoke"):
			print("Smoke gate failed; aborting before the full matrix.", file=sys.stderr)
			campaign["finished_at"] = now_iso()
			json_dump(workdir / "campaign.json", campaign)
			return 2

	if args.smoke_only:
		campaign["finished_at"] = now_iso()
		json_dump(workdir / "campaign.json", campaign)
		print("Smoke-only run finished.")
		return 0

	print("== Full matrix ==")
	all_ok = run_blocks(full_blocks, "matrix")

	campaign["finished_at"] = now_iso()
	campaign["thermal_end"] = collect_thermal()
	json_dump(workdir / "campaign.json", campaign)

	if not args.no_post:
		results_dir.mkdir(parents=True, exist_ok=True)
		db_path = results_dir / "results.db"
		print("== Ingest ==")
		run(
			[sys.executable, str(BENCH_DIR / "ingest.py"), "--workdir", str(workdir), "--db", str(db_path)],
			check=False,
		)
		print("== Analyze ==")
		analyze_cmd = [sys.executable, str(BENCH_DIR / "analyze.py"), "--db", str(db_path), "--outdir", str(results_dir)]
		uv = which_or_none("uv")
		if uv:
			uv_cmd = [
				uv,
				"run",
				"--no-project",
				"--with",
				"matplotlib",
				"--with",
				"numpy",
				sys.executable,
				*analyze_cmd[1:],
			]
			proc = run(uv_cmd, check=False, timeout=1800)
			if proc.returncode != 0:
				print("uv-based analysis failed; retrying with plain Python (plots may be skipped)")
				run(analyze_cmd, check=False)
		else:
			run(analyze_cmd, check=False)

	if not all_ok:
		print("Some matrix blocks failed; see state.jsonl and runs/*/meta.json", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
