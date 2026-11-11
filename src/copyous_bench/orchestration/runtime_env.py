"""Environment for the GNOME Shell test tool."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .dbus import DbusRuntime
from .toolchain import Toolchain

# Variables that would leak the host session into the isolated shell.
SCRUBBED_VARIABLES = (
    "GSETTINGS_SCHEMA_DIR",
    "DEBUG_COPYOUS_DBPATH",
    "DEBUG_COPYOUS_SCHEMA",
    "DEBUG_COPYOUS_GDA_VERSION",
    "MUTTER_DEBUG_DUMMY_MODE_SPECS",
    "CLUTTER_TEXT_DIRECTION",
    "DISPLAY",
    "WAYLAND_DISPLAY",
)

PERF_HELPER = "/usr/libexec/gnome-shell-perf-helper"


def ensure_perf_helper(workdir: Path) -> Path:
    """Create a ``gnome-shell-perf-helper`` wrapper with a long idle timeout.

    The helper exits after 30 s by default; a block takes longer, so the shell
    could no longer call Exit() and would die before the report is written.
    The shell spawns ``$GNOME_SHELL_BUILDDIR/gnome-shell-perf-helper``.
    """
    helper_dir = workdir / "perf-helper"
    helper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = helper_dir / "gnome-shell-perf-helper"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf "%s %s %s\\n" "$(date -Is)" "$$" "$*" >> "{helper_dir}/spawn.log"\n'
        f'exec {PERF_HELPER} --idle-timeout=7200 "$@"\n'
    )
    wrapper.chmod(0o755)
    return helper_dir


def build_run_env(
    workdir: Path,
    dbus: DbusRuntime,
    toolchain: Toolchain,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(workdir / "dbus" / "bin"), dbus.runtime_bin, *toolchain.entries(), env.get("PATH", "")]
    env["PATH"] = ":".join(part for part in parts if part)

    for key in SCRUBBED_VARIABLES:
        env.pop(key, None)

    env.update(
        LANG="C.utf8",
        LC_ALL="C.utf8",
        TZ="UTC",
        GNOME_SHELL_BUILDDIR=str(ensure_perf_helper(workdir)),
    )
    if extra:
        env.update(extra)
    return env
