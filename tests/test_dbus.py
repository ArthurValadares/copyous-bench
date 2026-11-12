import pytest

from copyous_bench.orchestration import dbus
from copyous_bench.orchestration.dbus import (
    DbusRuntime,
    _find_libapparmor,
    _install_shim,
    _newest_flatpak_session,
    discover_dbus_runtime,
)


def _flatpak(root, version, *, apparmor=False, base="org.gnome.Platform/x86_64"):
    files = root / "var/lib/flatpak/runtime" / base / version / "hash" / "files"
    (files / "bin").mkdir(parents=True)
    session = files / "bin" / "dbus-run-session"
    session.write_text("#!/bin/sh\n")
    if apparmor:
        (files / "lib" / "x86_64-linux-gnu").mkdir(parents=True)
        (files / "lib" / "x86_64-linux-gnu" / "libapparmor.so.1").write_text("")
    return session


def test_newest_flatpak_runtime_wins(tmp_path):
    _flatpak(tmp_path, "47")
    newest = _flatpak(tmp_path, "48")
    _flatpak(tmp_path, "stable")  # no numeric component sorts last
    assert _newest_flatpak_session(tmp_path) == newest


def test_sdk_runtime_is_considered(tmp_path):
    session = _flatpak(tmp_path, "46", base="org.gnome.Sdk/x86_64")
    assert _newest_flatpak_session(tmp_path) == session


def test_no_flatpak_runtime(tmp_path):
    with pytest.raises(RuntimeError, match="Could not find dbus-run-session"):
        _newest_flatpak_session(tmp_path)


def test_find_libapparmor(tmp_path):
    with_lib = _flatpak(tmp_path / "a", "48", apparmor=True)
    without = _flatpak(tmp_path / "b", "48")
    assert _find_libapparmor(with_lib.parent).name == "libapparmor.so.1"
    assert _find_libapparmor(without.parent) is None


def test_install_shim_links_libapparmor_and_is_repeatable(tmp_path):
    session = _flatpak(tmp_path / "rt", "48", apparmor=True)
    work = tmp_path / "work"
    first = _install_shim(work, session, search_libs=True)
    second = _install_shim(work, session, search_libs=True)  # replaces the existing link
    assert first == second
    assert first.kind == "flatpak-shim"
    assert (work / "dbus" / "lib" / "libapparmor.so.1").is_symlink()
    script = (work / "dbus" / "bin" / "dbus-daemon").read_text()
    assert str(session.parent / "dbus-daemon") in script and "LD_LIBRARY_PATH" in script
    assert (work / "dbus" / "bin" / "dbus-daemon").stat().st_mode & 0o100


def test_install_shim_without_library_search(tmp_path):
    session = _flatpak(tmp_path / "rt", "48", apparmor=True)
    runtime = _install_shim(tmp_path / "work", session, search_libs=False)
    assert runtime.libapparmor is None
    assert not (tmp_path / "work" / "dbus" / "lib" / "libapparmor.so.1").exists()


def test_override(tmp_path):
    session = tmp_path / "custom" / "dbus-run-session"
    session.parent.mkdir()
    session.write_text("")
    runtime = discover_dbus_runtime(tmp_path / "work", str(session))
    assert runtime.kind == "flatpak-shim" and runtime.run_session == str(session)
    with pytest.raises(RuntimeError, match="not found"):
        discover_dbus_runtime(tmp_path / "work", str(tmp_path / "missing"))


def test_host_runtime_is_preferred(tmp_path, monkeypatch):
    monkeypatch.setattr(dbus.shutil, "which", lambda name: f"/opt/bin/{name}")
    runtime = discover_dbus_runtime(tmp_path)
    assert runtime == DbusRuntime("host", "/opt/bin/dbus-run-session", "/opt/bin/dbus-daemon", "/opt/bin")
    assert runtime.to_dict()["kind"] == "host"
    assert not (tmp_path / "dbus").exists()


@pytest.mark.parametrize("missing", ["dbus-run-session", "dbus-daemon"])
def test_falls_back_to_flatpak_when_host_is_incomplete(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(dbus.shutil, "which", lambda name: None if name == missing else f"/opt/bin/{name}")
    session = _flatpak(tmp_path / "rt", "48", apparmor=True)
    monkeypatch.setattr(dbus, "_newest_flatpak_session", lambda: session)
    runtime = discover_dbus_runtime(tmp_path / "work")
    assert runtime.kind == "flatpak-shim" and runtime.libapparmor
