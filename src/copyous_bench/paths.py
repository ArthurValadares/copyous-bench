"""Shared filesystem locations for the benchmark harness.

The Python code lives in ``src/copyous_bench`` while the harness assets
(``automation/``, ``fixtures/`` and ``wrap/``) stay at the repository root, so
every module resolves them through :data:`PROJECT_ROOT` instead of guessing
from ``__file__``.
"""

from __future__ import annotations

from pathlib import Path

# ``src/copyous_bench/paths.py`` -> ``parents[1]`` is the repository root.
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]

# Harness assets kept at the repository root.
AUTOMATION_DIR = PROJECT_ROOT / "automation"
FIXTURES_DIR = PROJECT_ROOT / "fixtures"
WRAP_DIR = PROJECT_ROOT / "wrap"

# Default location for databases, summaries and plots.
DEFAULT_RESULTS = PROJECT_ROOT / "results"
