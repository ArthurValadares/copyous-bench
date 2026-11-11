"""Defaults and environment-variable overrides, resolved in one place."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .paths import PROJECT_ROOT

EXTENSION_UUID = "copyous@boerdereinar.dev"
DEFAULT_SEED = 20261108
DATASET_SIZES: tuple[int, ...] = (50, 200, 500)
CORE_SIZE = 500


def default_repo() -> Path:
    return Path(os.environ.get("COPYOUS_REPO", PROJECT_ROOT.parent / "copyous")).expanduser()


def default_workdir() -> Path:
    fallback = Path(tempfile.gettempdir()) / "copyous-bench"
    return Path(os.environ.get("COPYOUS_BENCH_WORKDIR", fallback)).expanduser()


def default_results() -> Path:
    return Path(os.environ.get("COPYOUS_BENCH_RESULTS", PROJECT_ROOT / "results")).expanduser()
