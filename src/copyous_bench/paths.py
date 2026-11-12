"""Shared filesystem locations for the benchmark harness.

The harness assets (``automation/``, ``fixtures/`` and ``wrap/``) are shipped
inside the installed package so they are available in wheels and virtualenvs.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# Harness assets shipped with the package.
AUTOMATION_DIR = PACKAGE_DIR / "automation"
FIXTURES_DIR = PACKAGE_DIR / "fixtures"
WRAP_DIR = PACKAGE_DIR / "wrap"

# Default location for databases, summaries and plots.
DEFAULT_RESULTS = Path.cwd() / "results"
