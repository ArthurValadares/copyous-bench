"""Ingest every block directory of a workdir into the results database."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .extract import load_block
from .repository import ResultsDatabase


@dataclass(frozen=True)
class IngestStats:
    campaign_id: str | None
    dataset_sizes: list[int]
    samples: int
    blocks: int
    db_path: Path


def ingest(workdir: Path, db_path: Path) -> IngestStats:
    workdir, db_path = workdir.resolve(), db_path.resolve()
    runs_dir = workdir / "runs"
    block_dirs = sorted(path for path in runs_dir.iterdir() if path.is_dir()) if runs_dir.exists() else []

    # Parsing JSON is the slow part and is independent per block; writes stay in one thread.
    with ThreadPoolExecutor() as pool:
        loaded = list(pool.map(load_block, block_dirs))

    with ResultsDatabase(db_path) as database:
        campaign_id = database.insert_campaign(workdir)
        sizes = database.insert_datasets(workdir)
        total = blocks = 0
        for block in filter(None, loaded):
            count = database.insert_block(block, campaign_id)
            if count:
                blocks += 1
                total += count
        database.commit()
    return IngestStats(campaign_id, sizes, total, blocks, db_path)
