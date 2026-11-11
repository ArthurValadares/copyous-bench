"""Detached Git worktrees, one per branch, sharing the main checkout's dependencies."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from ..util import slug
from .process import run_command


class WorktreeManager:
    def __init__(self, repo: Path, workdir: Path) -> None:
        self.repo = repo
        self.root = workdir / "worktrees"

    def path_for(self, branch: str) -> Path:
        return self.root / slug(branch)

    def _git(self, *args: str, check: bool = False) -> str:
        return run_command(["git", *args], cwd=self.repo, check=check).stdout

    def ensure(self, branch: str, *, recreate: bool = False) -> Path:
        path = self.path_for(branch)
        # Drop registrations whose directory disappeared (e.g. /tmp wiped by a reboot).
        self._git("worktree", "prune")
        registered = f"worktree {path}" in self._git("worktree", "list", "--porcelain") and path.exists()

        if recreate and path.exists():
            self._git("worktree", "remove", "--force", str(path))
            registered = False
            shutil.rmtree(path, ignore_errors=True)

        if not registered and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git("worktree", "add", "--force", "--detach", str(path), branch, check=True)

        if not path.exists():
            raise RuntimeError(f"Worktree for {branch} was not created: {path}")

        self._link_node_modules(path)
        self._link_gnome_shell_sources(path)
        return path

    def prepare_all(self, branches: Sequence[str], *, recreate: bool = False) -> dict[str, Path]:
        # Sequential on purpose: concurrent `git worktree add` calls contend on repository locks.
        return {branch: self.ensure(branch, recreate=recreate) for branch in branches}

    def _link_node_modules(self, path: Path) -> None:
        node_modules = path / "node_modules"
        if not node_modules.is_symlink() and not node_modules.exists():
            node_modules.symlink_to(self.repo / "node_modules", target_is_directory=True)

    def _link_gnome_shell_sources(self, path: Path) -> None:
        # Git leaves empty directories for uninitialised submodules, so only keep
        # a directory that really holds the theme sources.
        submodules = path / "submodules"
        submodules.mkdir(parents=True, exist_ok=True)
        gnome_shell = submodules / "gnome-shell"
        if gnome_shell.is_dir() and (gnome_shell / "data" / "theme" / "gnome-shell-sass").is_dir():
            return
        if gnome_shell.is_symlink():
            gnome_shell.unlink()
        elif gnome_shell.exists():
            shutil.rmtree(gnome_shell) if gnome_shell.is_dir() else gnome_shell.unlink()
        gnome_shell.symlink_to(self.repo / "submodules" / "gnome-shell", target_is_directory=True)
