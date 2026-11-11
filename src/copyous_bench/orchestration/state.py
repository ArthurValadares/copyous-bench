"""Append-only JSONL log of finished blocks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def completed(self) -> set[str]:
        if not self.path.exists():
            return set()
        done: set[str] = set()
        for line in self.path.read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") == "ok":
                done.add(record["run_id"])
        return done
