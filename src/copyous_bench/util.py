"""Small helpers shared by every module (JSON I/O, timestamps, slugs)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)


def read_text(path: Path) -> str:
    """Read a text file, returning an empty string when it is unreadable."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def json_load(path: Path) -> Any:
    return json.loads(path.read_text())


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
