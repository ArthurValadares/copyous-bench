"""Git worktrees, extension builds and highlight.js assets."""

import pytest

from copyous_bench.orchestration import assets
from copyous_bench.orchestration.build import ExtensionBuilder
from copyous_bench.orchestration.process import CommandError
from copyous_bench.orchestration.toolchain import Toolchain
from copyous_bench.orchestration.worktrees import WorktreeManager

ZIP = "copyous@boerdereinar.dev.zip"


# -- worktrees -----------------------------------------------------------------
def test_ensure_creates_a_worktree_with_shared_dependencies(git_repo, tmp_path):
    manager = WorktreeManager(git_repo, tmp_path / "work")
    path = manager.ensure("feature")
    assert path == tmp_path / "work" / "worktrees" / "feature"
    assert (path / "resources" / "metadata.json").exists()
    assert (path / "node_modules").is_symlink()
    assert (path / "submodules" / "gnome-shell").resolve() == (git_repo / "submodules" / "gnome-shell").resolve()


def test_ensure_is_idempotent_and_recreate_resets(git_repo, tmp_path):
    manager = WorktreeManager(git_repo, tmp_path / "work")
    path = manager.ensure("feature")
    (path / "marker").write_text("x")
    assert manager.ensure("feature") == path
    assert (path / "marker").exists()
    manager.ensure("feature", recreate=True)
    assert not (path / "marker").exists() and (path / "resources").exists()


def test_ensure_adopts_an_existing_directory(git_repo, tmp_path):
    manager = WorktreeManager(git_repo, tmp_path / "work")
    manager.path_for("feature").mkdir(parents=True)
    assert manager.ensure("feature") == manager.path_for("feature")


def test_ensure_reports_a_missing_worktree(git_repo, tmp_path, monkeypatch):
    manager = WorktreeManager(git_repo, tmp_path / "work")
    monkeypatch.setattr(manager, "_git", lambda *args, **kwargs: "")
    with pytest.raises(RuntimeError, match="was not created"):
        manager.ensure("feature")


def test_ensure_rejects_unknown_branches(git_repo, tmp_path):
    with pytest.raises(CommandError):
        WorktreeManager(git_repo, tmp_path / "work").ensure("no-such-branch")


def test_prepare_all(git_repo, tmp_path):
    manager = WorktreeManager(git_repo, tmp_path / "work")
    paths = manager.prepare_all(["main", "feature"], recreate=True)
    assert list(paths) == ["main", "feature"] and all(p.is_dir() for p in paths.values())


def _gnome_shell(tmp_path):
    root = tmp_path / "repo"
    (root / "submodules" / "gnome-shell").mkdir(parents=True)
    (tmp_path / "wt").mkdir()
    return WorktreeManager(root, tmp_path), tmp_path / "wt" / "submodules" / "gnome-shell"


def test_gnome_shell_sources_keep_a_populated_directory(tmp_path):
    manager, target = _gnome_shell(tmp_path)
    (target / "data" / "theme" / "gnome-shell-sass").mkdir(parents=True)
    manager._link_gnome_shell_sources(target.parents[1])
    assert target.is_dir() and not target.is_symlink()


def test_gnome_shell_sources_replace_stale_entries(tmp_path):
    manager, target = _gnome_shell(tmp_path)
    worktree = target.parents[1]

    target.mkdir(parents=True)  # empty directory created for an uninitialised submodule
    manager._link_gnome_shell_sources(worktree)
    assert target.is_symlink()

    target.unlink()
    target.symlink_to(tmp_path / "nowhere")  # dangling link
    manager._link_gnome_shell_sources(worktree)
    assert target.resolve() == (tmp_path / "repo" / "submodules" / "gnome-shell").resolve()

    target.unlink()
    target.write_text("a file")
    manager._link_gnome_shell_sources(worktree)
    assert target.is_symlink()


# -- builds ----------------------------------------------------------------------
@pytest.fixture
def builder(tmp_path, fake_make):
    return ExtensionBuilder(Toolchain(tmp_path / "n", tmp_path / "p"), tmp_path / "logs")


def _worktree(tmp_path, name="wt"):
    path = tmp_path / name
    path.mkdir()
    return path


def test_build_produces_the_zip_and_a_log(builder, tmp_path):
    worktree = _worktree(tmp_path)
    zip_path = builder.build("feat/x", worktree)
    assert zip_path == worktree / "dist" / ZIP and zip_path.exists()
    assert (tmp_path / "logs" / "feat-x.log").exists()


def test_build_can_reuse_an_existing_zip(builder, tmp_path, monkeypatch):
    worktree = _worktree(tmp_path)
    monkeypatch.setenv("FAKE_MAKE_FAIL", "1")  # proves make is not invoked
    (worktree / "dist").mkdir()
    (worktree / "dist" / ZIP).write_text("")
    assert builder.build("b", worktree, skip=True).exists()
    with pytest.raises(RuntimeError, match="Build failed"):  # skip without a zip still builds
        builder.build("b", _worktree(tmp_path, "other"), skip=True)


def test_build_failures(builder, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MAKE_FAIL", "1")
    with pytest.raises(RuntimeError, match=r"Build failed for b; see .*b\.log"):
        builder.build("b", _worktree(tmp_path))
    monkeypatch.delenv("FAKE_MAKE_FAIL")
    monkeypatch.setenv("FAKE_MAKE_NOZIP", "1")
    with pytest.raises(RuntimeError, match="did not produce"):
        builder.build("b", _worktree(tmp_path, "other"))


def test_build_environment(builder, monkeypatch):
    monkeypatch.setenv("RELEASE", "1")
    env = builder._env()
    assert "RELEASE" not in env
    assert env["npm_config_verify_deps_before_run"] == "false"
    assert env["PATH"].startswith(str(builder.toolchain.node_bin))


def test_build_all_keeps_the_branch_order(builder, tmp_path):
    worktrees = {name: _worktree(tmp_path, name) for name in ("c", "a", "b")}
    zips = builder.build_all(worktrees, jobs=3)
    assert list(zips) == ["c", "a", "b"] and all(path.exists() for path in zips.values())


def test_build_all_aggregates_failures(builder, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MAKE_FAIL", "1")
    worktrees = {name: _worktree(tmp_path, name) for name in ("a", "b")}
    with pytest.raises(RuntimeError, match="Build failures") as error:
        builder.build_all(worktrees, jobs=2)
    assert "a: Build failed" in str(error.value) and "b: Build failed" in str(error.value)


# -- highlight.js assets -------------------------------------------------------------
@pytest.fixture
def hljs_script(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    script = fixtures / "build_hljs.sh"
    script.write_text(
        "#!/bin/bash\nset -e\n"
        'mkdir -p "$(dirname "$1")" "$1/languages"\n'
        'echo run >> "$(dirname "$1")/runs.log"\n'
        '[ -n "$HLJS_BROKEN" ] && exit 0\n'
        'touch "$1/highlight.min.js"\n'
        'for l in typescript python rust json bash; do touch "$1/languages/$l.min.js"; done\n'
    )
    monkeypatch.setattr(assets, "FIXTURES_DIR", fixtures)
    return script


def test_ensure_hljs_builds_once_unless_forced(tmp_path, hljs_script):
    out = assets.ensure_hljs(tmp_path / "work")
    assert out == tmp_path / "work" / "assets" / "hljs"
    runs = tmp_path / "work" / "assets" / "runs.log"
    assets.ensure_hljs(tmp_path / "work")
    assert runs.read_text().count("run") == 1
    assets.ensure_hljs(tmp_path / "work", force=True)
    assert runs.read_text().count("run") == 2


def test_ensure_hljs_detects_incomplete_assets(tmp_path, hljs_script, monkeypatch):
    monkeypatch.setenv("HLJS_BROKEN", "1")
    with pytest.raises(RuntimeError, match="incomplete"):
        assets.ensure_hljs(tmp_path / "work")


def test_ensure_hljs_propagates_script_failures(tmp_path, hljs_script):
    hljs_script.write_text("#!/bin/bash\nexit 7\n")
    hljs_script.chmod(0o755)
    with pytest.raises(CommandError):
        assets.ensure_hljs(tmp_path / "work")
