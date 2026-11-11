"""The benchmark matrix: which (branch, config, size) blocks run, and in what order."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ..config import CORE_SIZE
from ..util import slug

CORE_CONFIGS = ("open", "scroll", "search")
SCALING_CONFIGS = ("open",)


@dataclass(frozen=True)
class Block:
    branch: str
    config: str
    size: int
    samples: int
    warmup: bool
    smoke: bool = False

    @property
    def run_id(self) -> str:
        suffix = "__smoke" if self.smoke else ""
        return f"{slug(self.branch)}__{self.config}__N{self.size}{suffix}"

    @property
    def iterations(self) -> int:
        return self.samples + (1 if self.warmup else 0)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "run_id": self.run_id, "iterations": self.iterations}


def build_plan(
    branches: Sequence[str],
    sizes: Sequence[int],
    *,
    core_samples: int,
    scaling_samples: int,
    warmup: bool,
    seed: int,
) -> list[Block]:
    blocks: list[Block] = []
    for branch in branches:
        blocks += [Block(branch, config, CORE_SIZE, core_samples, warmup) for config in CORE_CONFIGS]
        blocks += [
            Block(branch, config, size, scaling_samples, warmup)
            for size in sizes
            if size != CORE_SIZE
            for config in SCALING_CONFIGS
        ]
    random.Random(seed).shuffle(blocks)
    return blocks


def smoke_plan(warmup: bool) -> list[Block]:
    return [Block(branch, "all", CORE_SIZE, 2, warmup, smoke=True) for branch in ("main", "feat/lazy-clipboard-items")]


def filter_blocks(blocks: Iterable[Block], needles: Sequence[str]) -> list[Block]:
    return [block for block in blocks if any(needle in block.run_id for needle in needles)]
