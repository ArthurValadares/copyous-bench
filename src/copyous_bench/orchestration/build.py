"""Build the extension zip for every worktree (in parallel)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..config import EXTENSION_UUID
from ..util import slug
from .process import run_command
from .toolchain import Toolchain


class ExtensionBuilder:
    def __init__(self, toolchain: Toolchain, log_dir: Path, *, timeout: float = 1800.0) -> None:
        self.toolchain = toolchain
        self.log_dir = log_dir
        self.timeout = timeout

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = self.toolchain.prepend_to(env.get("PATH", ""))
        env.pop("RELEASE", None)
        # Worktrees share node_modules; stop pnpm from running the package's
        # `install` lifecycle script (which would install into the host session).
        env["npm_config_verify_deps_before_run"] = "false"
        return env

    def build(self, branch: str, worktree: Path, *, skip: bool = False) -> Path:
        zip_path = worktree / "dist" / f"{EXTENSION_UUID}.zip"
        if skip and zip_path.exists():
            return zip_path

        log_path = self.log_dir / f"{slug(branch)}.log"
        proc = run_command(["make", "build"], cwd=worktree, env=self._env(), timeout=self.timeout, log_path=log_path)
        if proc.returncode != 0:
            raise RuntimeError(f"Build failed for {branch}; see {log_path}")
        if not zip_path.exists():
            raise RuntimeError(f"Build did not produce {zip_path}")
        return zip_path

    def build_all(self, worktrees: Mapping[str, Path], *, skip: bool = False, jobs: int = 4) -> dict[str, Path]:
        """Build every branch concurrently; builds never overlap benchmark runs."""
        zips: dict[str, Path] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max(1, min(jobs, len(worktrees) or 1))) as pool:
            futures = {pool.submit(self.build, branch, path, skip=skip): branch for branch, path in worktrees.items()}
            for future in as_completed(futures):
                branch = futures[future]
                try:
                    zips[branch] = future.result()
                except Exception as error:
                    errors.append(f"{branch}: {error}")
        if errors:
            raise RuntimeError("Build failures:\n" + "\n".join(errors))
        return {branch: zips[branch] for branch in worktrees}
