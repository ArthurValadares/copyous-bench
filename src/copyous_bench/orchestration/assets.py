"""highlight.js assets the extension needs at runtime."""

from __future__ import annotations

from pathlib import Path

from ..paths import FIXTURES_DIR
from .process import run_command

HLJS_LANGUAGES = ("typescript", "python", "rust", "json", "bash")


def _expected_files(out_dir: Path) -> list[Path]:
    return [out_dir / "highlight.min.js", *(out_dir / "languages" / f"{lang}.min.js" for lang in HLJS_LANGUAGES)]


def ensure_hljs(workdir: Path, *, force: bool = False) -> Path:
    out_dir = workdir / "assets" / "hljs"
    if force or not all(path.exists() for path in _expected_files(out_dir)):
        run_command(["bash", str(FIXTURES_DIR / "build_hljs.sh"), str(out_dir)], check=True)
    if not all(path.exists() for path in _expected_files(out_dir)):
        raise RuntimeError(f"highlight.js assets incomplete in {out_dir}")
    return out_dir
