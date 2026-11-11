"""Generate the dataset files for every requested size and describe them."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util import json_dump, json_load
from .builders import TOKEN
from .generator import BASE_TIME, MASTER_SIZE, NO_MATCH_TOKEN, ClipboardEntry, MasterListGenerator
from .sqlite_writer import write_database


@dataclass(frozen=True)
class DatasetInfo:
    size: int
    path: Path
    sha256: str
    type_counts: dict[str, int]
    token_matches: int
    newest_datetime: str
    oldest_datetime: str

    def to_manifest(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "entries": self.size,
            "type_counts": self.type_counts,
            "token": TOKEN,
            "token_matches": self.token_matches,
            "no_match_token": NO_MATCH_TOKEN,
            "newest_datetime": self.newest_datetime,
            "oldest_datetime": self.oldest_datetime,
        }

    def summary(self) -> str:
        return (
            f"N={self.size}: {self.path} entries={self.size} types={self.type_counts} "
            f"token_matches={self.token_matches} sha256={self.sha256[:12]}"
        )


def sha256_of(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _materialize(size: int, master: Sequence[ClipboardEntry], out_dir: Path, force: bool) -> DatasetInfo:
    entries = master[:size]
    path = out_dir / f"N{size}.db"
    if force or not path.exists():
        write_database(path, entries)
    return DatasetInfo(
        size=size,
        path=path.resolve(),
        sha256=sha256_of(path),
        type_counts=dict(sorted(Counter(entry.type for entry in entries).items())),
        token_matches=sum(entry.matches(TOKEN) for entry in entries),
        newest_datetime=entries[0].datetime,
        oldest_datetime=entries[-1].datetime,
    )


def generate_datasets(
    out_dir: Path, sizes: Sequence[int], *, force: bool = False
) -> tuple[dict[str, Any], list[DatasetInfo]]:
    """Write ``N<size>.db`` for every size (in parallel) plus ``datasets.json``."""
    for size in sizes:
        if size > MASTER_SIZE:
            raise ValueError(f"size {size} exceeds master size {MASTER_SIZE}")

    out_dir.mkdir(parents=True, exist_ok=True)
    master = MasterListGenerator().generate()

    with ThreadPoolExecutor(max_workers=max(1, len(sizes))) as pool:
        infos = list(pool.map(lambda size: _materialize(size, master, out_dir, force), sizes))

    manifest: dict[str, Any] = {
        "seed": MasterListGenerator().seed,
        "base_time": BASE_TIME.isoformat(),
        "token": TOKEN,
        "no_match_token": NO_MATCH_TOKEN,
        "master_size": MASTER_SIZE,
        "sizes": {str(info.size): info.to_manifest() for info in infos},
    }
    json_dump(out_dir / "datasets.json", manifest)
    return manifest, infos


def ensure_datasets(out_dir: Path, sizes: Sequence[int], *, force: bool = False) -> dict[str, Any]:
    """Return the manifest, generating whatever is missing (or everything with ``force``)."""
    manifest_path = out_dir / "datasets.json"
    if not force and manifest_path.exists():
        manifest = json_load(manifest_path)
        sizes_manifest = manifest.get("sizes", {})
        if all(str(size) in sizes_manifest and Path(sizes_manifest[str(size)]["path"]).exists() for size in sizes):
            return manifest
    manifest, _ = generate_datasets(out_dir, sizes, force=force)
    return manifest
