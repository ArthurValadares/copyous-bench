"""Locate a usable ``dbus-run-session`` (host or Flatpak runtime) and, if needed, a shim."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FLATPAK_PATTERNS = (
    "var/lib/flatpak/runtime/org.gnome.Platform/x86_64/*/*/files/bin/dbus-run-session",
    "var/lib/flatpak/runtime/org.gnome.Platform/*/*/*/files/bin/dbus-run-session",
    "var/lib/flatpak/runtime/org.gnome.Sdk/x86_64/*/*/files/bin/dbus-run-session",
)


@dataclass(frozen=True)
class DbusRuntime:
    kind: str
    run_session: str
    daemon: str
    runtime_bin: str
    shim: str | None = None
    libapparmor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _host_runtime() -> DbusRuntime | None:
    run_session = shutil.which("dbus-run-session")
    daemon = shutil.which("dbus-daemon")
    if not (run_session and daemon):
        return None
    return DbusRuntime("host", run_session, daemon, str(Path(run_session).parent))


def _newest_flatpak_session(root: Path = Path("/")) -> Path:
    candidates = [path for pattern in FLATPAK_PATTERNS for path in root.glob(pattern) if path.exists()]
    if not candidates:
        raise RuntimeError("Could not find dbus-run-session on the host or in a Flatpak runtime")

    def version_key(path: Path) -> tuple[int, str]:
        numeric = next((int(part) for part in path.parts if part.isdigit()), 0)
        return numeric, str(path)

    return max(candidates, key=version_key)


def _find_libapparmor(runtime_bin: Path) -> Path | None:
    # runtime_bin is <runtime>/files/bin; libraries live in <runtime>/files/lib/<triplet>/.
    return next(runtime_bin.parent.glob("lib/*/libapparmor.so.1"), None)


def _install_shim(workdir: Path, run_session: Path, *, search_libs: bool) -> DbusRuntime:
    runtime_bin = run_session.parent
    daemon = runtime_bin / "dbus-daemon"
    libapparmor = _find_libapparmor(runtime_bin) if search_libs else None

    shim_bin = workdir / "dbus" / "bin"
    shim_lib = workdir / "dbus" / "lib"
    shim_bin.mkdir(parents=True, exist_ok=True)
    shim_lib.mkdir(parents=True, exist_ok=True)

    if libapparmor is not None:
        link = shim_lib / "libapparmor.so.1"
        link.unlink(missing_ok=True)
        link.symlink_to(libapparmor)

    shim = shim_bin / "dbus-daemon"
    shim.write_text(
        f'#!/bin/sh\nLD_LIBRARY_PATH="{shim_lib}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}" exec "{daemon}" "$@"\n'
    )
    shim.chmod(0o755)

    return DbusRuntime(
        kind="flatpak-shim",
        run_session=str(run_session),
        daemon=str(daemon),
        runtime_bin=str(runtime_bin),
        shim=str(shim),
        libapparmor=str(libapparmor) if libapparmor else None,
    )


def discover_dbus_runtime(workdir: Path, override: str | None = None) -> DbusRuntime:
    if override:
        run_session = Path(override)
        if not run_session.exists():
            raise RuntimeError(f"--dbus-run-session not found: {run_session}")
        return _install_shim(workdir, run_session, search_libs=False)

    host = _host_runtime()
    if host is not None:
        return host
    return _install_shim(workdir, _newest_flatpak_session(), search_libs=True)
