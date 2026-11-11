"""Deterministic generation of the master clipboard list."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from ..config import DEFAULT_SEED
from .builders import TOKEN, EntryBuilder, default_builders

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
MASTER_SIZE = 500
NO_MATCH_TOKEN = "zzzznomatch"
TYPE_COUNTS: Mapping[str, int] = {"Text": 250, "Code": 125, "Link": 75, "Color": 25, "Character": 25}

# Every 7th entry (offset 3) carries the search token.
TOKEN_STRIDE = 7
TOKEN_OFFSET = 3


@dataclass(frozen=True)
class ClipboardEntry:
    type: str
    content: str
    datetime: str
    metadata: str | None = None
    pinned: int = 0
    tag: str | None = None
    title: str | None = None

    def matches(self, token: str) -> bool:
        token = token.lower()
        return token in self.content.lower() or bool(self.title and token in self.title.lower())

    def as_row(self) -> dict[str, object]:
        return asdict(self)


def stable_order(seed: int, count: int) -> list[int]:
    """Return 0..count-1 ordered by a stable hash (not Python's PRNG)."""
    return sorted(range(count), key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).digest())


class MasterListGenerator:
    """Builds the master list; every dataset size is a prefix of it."""

    def __init__(
        self,
        *,
        seed: int = DEFAULT_SEED,
        size: int = MASTER_SIZE,
        type_counts: Mapping[str, int] = TYPE_COUNTS,
        base_time: datetime = BASE_TIME,
        token: str = TOKEN,
    ) -> None:
        if sum(type_counts.values()) != size:
            raise ValueError(f"type counts add up to {sum(type_counts.values())}, expected {size}")
        self.seed = seed
        self.size = size
        self.type_counts = type_counts
        self.base_time = base_time
        self.token = token

    def _shuffled_types(self) -> list[str]:
        types = [name for name, count in self.type_counts.items() for _ in range(count)]
        return [types[i] for i in stable_order(self.seed, self.size)]

    def generate(self) -> list[ClipboardEntry]:
        builders: dict[str, EntryBuilder] = default_builders(self.token)
        entries: list[ClipboardEntry] = []

        for index, item_type in enumerate(self._shuffled_types()):
            has_token = index % TOKEN_STRIDE == TOKEN_OFFSET
            built = builders[item_type].build(index, has_token)
            if not has_token and self.token in built.content.lower():
                raise ValueError(f"entry {index} unexpectedly contains the token")
            entries.append(
                ClipboardEntry(
                    type=item_type,
                    content=built.content,
                    metadata=json.dumps(built.metadata, separators=(",", ":")) if built.metadata else None,
                    datetime=(self.base_time - timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M:%S"),
                )
            )

        # One minute between entries keeps the ordering unambiguous.
        if len({entry.datetime for entry in entries}) != len(entries):
            raise ValueError("timestamps are not unique")
        return entries
