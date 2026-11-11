"""Subprocess helpers: plain commands and supervised (timeout/stall) runs."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CommandError(RuntimeError):
    pass


def run_command(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    log_path: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command with stderr merged into stdout, optionally logging to a file."""
    options: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "env": dict(env) if env is not None else None,
        "stderr": subprocess.STDOUT,
        "text": True,
        "timeout": timeout,
    }
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as handle:
            proc = subprocess.run(list(cmd), stdout=handle, **options)
    else:
        proc = subprocess.run(list(cmd), stdout=subprocess.PIPE, **options)

    if check and proc.returncode != 0:
        raise CommandError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout or ''}")
    return proc


def kill_process_group(proc: subprocess.Popen[Any], grace: float = 20.0) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()


@dataclass(frozen=True)
class SupervisedResult:
    returncode: int | None
    timed_out: bool = False
    stalled: bool = False


def run_supervised(
    cmd: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    log_path: Path,
    progress: Callable[[], int] | None = None,
    stall_timeout: float | None = None,
    poll_interval: float = 1.0,
) -> SupervisedResult:
    """Run a command in its own process group, killing it on timeout or stall.

    A stall is ``progress()`` not changing for ``stall_timeout`` seconds, which is
    how a GNOME Shell that hangs during startup is detected.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as handle:
        proc = subprocess.Popen(
            list(cmd),
            cwd=str(cwd),
            env=dict(env),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        start = last_change = time.monotonic()
        last_value = progress() if progress else None
        while True:
            try:
                return SupervisedResult(proc.wait(timeout=poll_interval))
            except subprocess.TimeoutExpired:
                pass

            now = time.monotonic()
            if now - start > timeout:
                kill_process_group(proc)
                return SupervisedResult(None, timed_out=True)

            if progress is not None and stall_timeout is not None:
                value = progress()
                if value != last_value:
                    last_value, last_change = value, now
                elif now - last_change > stall_timeout:
                    kill_process_group(proc)
                    return SupervisedResult(None, stalled=True)
