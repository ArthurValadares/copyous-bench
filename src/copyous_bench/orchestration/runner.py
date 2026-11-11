"""Run one benchmark block through ``gnome-shell-test-tool`` and validate it."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import EXTENSION_UUID
from ..paths import AUTOMATION_DIR, WRAP_DIR
from ..util import json_dump, json_load, now_iso
from .dbus import DbusRuntime
from .environment import TEST_TOOL
from .plan import Block
from .process import run_supervised
from .runtime_env import build_run_env
from .state import StateLog
from .toolchain import Toolchain
from .validation import BlockValidator

MAX_PRINTED_PROBLEMS = 8


@dataclass(frozen=True)
class RunContext:
    workdir: Path
    worktrees: Mapping[str, Path]
    zips: Mapping[str, Path]
    datasets: Mapping[str, Any]
    dbus: DbusRuntime
    toolchain: Toolchain
    settings_file: Path
    timeout: float
    stall_timeout: float
    resume: bool


@dataclass(frozen=True)
class BlockResult:
    ok: bool
    record: dict[str, Any]


class BlockRunner:
    def __init__(self, context: RunContext, state: StateLog) -> None:
        self.ctx = context
        self.state = state

    def outdir(self, block: Block) -> Path:
        return self.ctx.workdir / "runs" / block.run_id

    def _already_ok(self, block: Block) -> dict[str, Any] | None:
        meta_path = self.outdir(block) / "meta.json"
        if not (self.ctx.resume and meta_path.exists()):
            return None
        try:
            meta = json_load(meta_path)
        except (OSError, json.JSONDecodeError):
            return None
        return meta if meta.get("status") == "ok" else None

    def _write_context(self, block: Block, outdir: Path) -> Path:
        dataset = self.ctx.datasets["sizes"][str(block.size)]
        path = outdir / "context.json"
        json_dump(
            path,
            {
                "campaign_id": self.ctx.datasets.get("campaign_id"),
                "run_id": block.run_id,
                "branch": block.branch,
                "commit": self.ctx.datasets.get("commits", {}).get(block.branch, "unknown"),
                "config": block.config,
                "size": block.size,
                "expected_entries": dataset["entries"],
                "token": dataset["token"],
                "token_matches": dataset["token_matches"],
                "no_match_token": dataset["no_match_token"],
                "sample_count": block.samples,
                "warmup": block.warmup,
            },
        )
        return path

    def _command(self, block: Block, report_path: Path) -> list[str]:
        command = [
            self.ctx.dbus.run_session,
            "--",
            TEST_TOOL,
            str(AUTOMATION_DIR / "bench.js"),
            "--headless",
            "--disable-animations",
            "--test-iters",
            str(block.samples),
            "--perf-output",
            str(report_path),
            "--extension",
            str(self.ctx.zips[block.branch]),
            "--wrap",
            str(WRAP_DIR / "wrap.sh"),
        ]
        if block.warmup:
            command.append("--perf-warmup")
        return command

    def run(self, block: Block) -> BlockResult:
        if (meta := self._already_ok(block)) is not None:
            print(f"  skip {block.run_id} (already ok)")
            return BlockResult(True, meta)

        outdir = self.outdir(block)
        shutil.rmtree(outdir, ignore_errors=True)
        outdir.mkdir(parents=True, exist_ok=True)

        dataset = self.ctx.datasets["sizes"][str(block.size)]
        env = build_run_env(
            self.ctx.workdir,
            self.ctx.dbus,
            self.ctx.toolchain,
            {
                "BENCH_OUTDIR": str(outdir),
                "BENCH_DATASET": dataset["path"],
                "BENCH_HLJS_DIR": str(self.ctx.workdir / "assets" / "hljs"),
                "BENCH_CONTEXT_FILE": str(self._write_context(block, outdir)),
                "BENCH_SETTINGS_FILE": str(self.ctx.settings_file),
                "BENCH_SIZE": str(block.size),
                "BENCH_EXT_UUID": EXTENSION_UUID,
                "BENCH_RUN_ID": block.run_id,
            },
        )
        command = self._command(block, outdir / "report.json")

        print(f"  run {block.run_id} ({block.iterations} launches)")
        started_at = now_iso()
        start = time.monotonic()
        outcome = run_supervised(
            command,
            cwd=self.ctx.worktrees[block.branch],
            env=env,
            timeout=self.ctx.timeout,
            log_path=outdir / "run.log",
            progress=lambda: len(list(outdir.glob("sidecar-*.json"))),
            stall_timeout=self.ctx.stall_timeout,
        )
        duration_s = time.monotonic() - start

        validation = BlockValidator(dataset["entries"], dataset["token_matches"]).validate(block, outdir)
        problems = list(validation.problems)
        if outcome.timed_out:
            problems.append(f"timed out after {self.ctx.timeout:.0f}s")
        if outcome.stalled:
            problems.append(f"stalled: no new sidecar for {self.ctx.stall_timeout:.0f}s (shell hung during startup)")
        if outcome.returncode not in (0, None):
            problems.append(f"exit code {outcome.returncode}")
        validation.summary["errors"] = problems

        status = "ok" if not problems else "failed"
        finished_at = now_iso()
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
                "returncode": outcome.returncode,
                "timed_out": outcome.timed_out,
                "stalled": outcome.stalled,
                "commit": self.ctx.datasets.get("commits", {}).get(block.branch, "unknown"),
                "extension_zip": str(self.ctx.zips[block.branch]),
                "validation": validation.summary,
            },
        )
        record = {
            "run_id": block.run_id,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
            "returncode": outcome.returncode,
            "timed_out": outcome.timed_out,
            "stalled": outcome.stalled,
            "errors": problems,
        }
        self.state.append(record)
        self._report(status, duration_s, problems)
        return BlockResult(status == "ok", record)

    @staticmethod
    def _report(status: str, duration_s: float, problems: list[str]) -> None:
        if status == "ok":
            print(f"    ok in {duration_s:.1f}s")
            return
        print(f"    FAILED in {duration_s:.1f}s")
        for problem in problems[:MAX_PRINTED_PROBLEMS]:
            print(f"      - {problem}")
        if len(problems) > MAX_PRINTED_PROBLEMS:
            print(f"      ... {len(problems) - MAX_PRINTED_PROBLEMS} more")
