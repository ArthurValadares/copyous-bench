"""Generate deterministic clipboard-history databases for the benchmark.

The databases use the same schema as ``resources/database/database.sql``
(schema version 2) so the extension can open them through Gda without a
migration.

Every size is a prefix of the same master list: entry ``i`` has the same type,
content and timestamp in N=50, N=200 and N=500.  Entry 0 is the newest entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT

SEED = 20261108
BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
MASTER_SIZE = 500
TOKEN = "benchmark"
NO_MATCH_TOKEN = "zzzznomatch"

TYPE_COUNTS = {
    "Text": 250,
    "Code": 125,
    "Link": 75,
    "Color": 25,
    "Character": 25,
}

LANGUAGES = [
    ("typescript", "TypeScript"),
    ("python", "Python"),
    ("rust", "Rust"),
    ("json", "JSON"),
    ("bash", "Bash"),
]

TEXT_TEMPLATES = [
    "Meeting notes: the release is on track and the remaining work is documentation.",
    "Remember to renew the domain certificate before the end of the month.",
    "A short reminder.",
    "The quick brown fox jumps over the lazy dog while the build finishes.",
    "Configuration values for the staging environment live in the shared vault.",
    "This is a longer paragraph that is meant to exercise line wrapping in the clipboard item. "
    "It contains several sentences so that the text preview has to wrap across multiple lines "
    "and the layout engine has real work to do.",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH7examplekeycopyoussample user@host",
    "https://example.com/docs/getting-started?section=installation",
    "TODO: remove the temporary workaround once the upstream fix is released.",
    "Line one\nLine two\nLine three\nLine four",
    "A list of values: alpha, beta, gamma, delta, epsilon, zeta, eta, theta.",
    "The database migration completed successfully after the service restarted.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt "
    "ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation.",
    "Note: keep the clipboard history size bounded to avoid unbounded memory growth.",
    "Recipe: flour, water, salt, yeast. Mix, rest, fold, bake.",
]

CODE_TEMPLATES = {
    "typescript": """interface SampleOptions {
    iterations: number;
    warmup: boolean;
}

export function runSample(options: SampleOptions): number {
    let total = 0;
    for (let i = 0; i < options.iterations; i++) {
        total += i * i;
    }
    return total;
}""",
    "python": """from dataclasses import dataclass


@dataclass
class SampleResult:
    name: str
    duration_ms: float


def run_sample(iterations: int) -> SampleResult:
    total = sum(i * i for i in range(iterations))
    return SampleResult("sum", total / 1000.0)""",
    "rust": """use std::time::Instant;

pub struct SampleResult {
    pub duration_ms: f64,
    pub samples: usize,
}

pub fn run(iterations: usize) -> SampleResult {
    let start = Instant::now();
    let mut total = 0usize;
    for i in 0..iterations {
        total = total.wrapping_add(i * i);
    }
    let _ = total;
    SampleResult {
        duration_ms: start.elapsed().as_secs_f64() * 1000.0,
        samples: iterations,
    }
}""",
    "json": """{
    "name": "copyous-sample",
    "iterations": 500,
    "settings": {
        "orientation": "horizontal",
        "itemWidth": 250,
        "itemHeight": 170
    },
    "enabled": true
}""",
    "bash": """#!/usr/bin/env bash
set -euo pipefail

iterations=${1:-500}
total=0

for ((i = 0; i < iterations; i++)); do
    total=$((total + i * i))
done

printf 'total=%s\\n' "$total\"""",
}

CHARACTERS = [
    "🫃",
    "👨‍👨‍👶‍👧",
    "😀",
    "😃",
    "😄",
    "😁",
    "😆",
    "😅",
    "🤣",
    "😂",
    "🙂",
    "🙃",
    "😉",
    "😊",
    "😇",
    "🥰",
    "😍",
    "🤩",
    "😘",
    "😗",
    "😚",
    "😙",
    "🥲",
    "😋",
    "😛",
    "😜",
    "🤪",
    "😝",
    "🤑",
    "🤗",
    "🤭",
    "🤫",
    "🤔",
    "🤐",
    "🤨",
    "😐",
    "😑",
    "😶",
    "😏",
    "😒",
    "🙄",
    "😬",
    "🤥",
    "😌",
    "😔",
    "😪",
    "🤤",
    "😴",
    "😷",
    "🤒",
]


def stable_order(seed: int, count: int) -> list[int]:
    """Return 0..count-1 ordered by a stable hash (not Python's PRNG)."""
    return sorted(range(count), key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).digest())


def build_master() -> list[dict]:
    types: list[str] = []
    for item_type, count in TYPE_COUNTS.items():
        types.extend([item_type] * count)
    assert len(types) == MASTER_SIZE

    order = stable_order(SEED, MASTER_SIZE)
    types = [types[i] for i in order]

    entries: list[dict] = []
    language_index = 0
    character_index = 0

    for i, item_type in enumerate(types):
        token = i % 7 == 3
        metadata: dict[str, Any] | None = None
        title = None

        if item_type == "Text":
            template = TEXT_TEMPLATES[i % len(TEXT_TEMPLATES)]
            content = f"{template}\n[#{i:04d}]"
            if token:
                content = f"{TOKEN} note {i:04d}: {content}"
        elif item_type == "Code":
            language_id, language_name = LANGUAGES[language_index % len(LANGUAGES)]
            language_index += 1
            template = CODE_TEMPLATES[language_id]
            content = template
            marker = f"{TOKEN} " if token else ""
            content = f"// {marker}sample {i:04d}\n{content}"
            metadata = {"language": {"id": language_id, "name": language_name}}
        elif item_type == "Link":
            slug = TOKEN if token else "article"
            content = f"https://example.com/{slug}/{i:04d}"
            metadata = {
                "title": f"{'Benchmark' if token else 'Article'} notes {i:04d}",
                "description": (f"A short description for link {i:04d} used by the clipboard dataset."),
                "image": None,
            }
        elif item_type == "Color":
            value = (i * 2654435761) & 0xFFFFFF
            content = f"#{value:06x}"
        elif item_type == "Character":
            content = CHARACTERS[character_index % len(CHARACTERS)]
            character_index += 1
        else:  # pragma: no cover - defensive
            raise AssertionError(item_type)

        # Keep the content of the entries that do not carry the token free of it.
        if not token:
            assert TOKEN not in content.lower()

        entries.append(
            {
                "type": item_type,
                "content": content,
                "pinned": 0,
                "tag": None,
                "metadata": json.dumps(metadata, separators=(",", ":")) if metadata else None,
                "title": title,
                "datetime": (BASE_TIME - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    # Deterministic jitter in the timestamps keeps ordering realistic without
    # changing the order.  Keep one minute between entries so ordering is
    # unambiguous.
    assert len({entry["datetime"] for entry in entries}) == len(entries)
    return entries


def write_db(path: Path, entries: list[dict]) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;

            DROP TABLE IF EXISTS 'clipboard';
            CREATE TABLE 'clipboard' (
                'id'       integer   NOT NULL UNIQUE PRIMARY KEY AUTOINCREMENT,
                'type'     text      NOT NULL,
                'content'  text      NOT NULL,
                'pinned'   boolean   NOT NULL,
                'tag'      text,
                'datetime' timestamp NOT NULL,
                'metadata' text,
                'title'    text,
                UNIQUE ('type', 'content')
            );

            DROP TABLE IF EXISTS 'clipboard_version';
            CREATE TABLE 'clipboard_version' (
                'id'      integer PRIMARY KEY CHECK (id = 1),
                'version' integer
            );
            INSERT INTO 'clipboard_version' (id, version) VALUES (1, 2);
            """
        )
        conn.executemany(
            """
            INSERT INTO 'clipboard' ('type', 'content', 'pinned', 'tag', 'datetime', 'metadata', 'title')
            VALUES (:type, :content, :pinned, :tag, :datetime, :metadata, :title)
            """,
            entries,
        )
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "results" / "datasets")
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 200, 500])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    master = build_master()

    manifest: dict[str, Any] = {
        "seed": SEED,
        "base_time": BASE_TIME.isoformat(),
        "token": TOKEN,
        "no_match_token": NO_MATCH_TOKEN,
        "master_size": MASTER_SIZE,
        "sizes": {},
    }

    for size in args.sizes:
        if size > MASTER_SIZE:
            raise SystemExit(f"size {size} exceeds master size {MASTER_SIZE}")

        entries = master[:size]
        path = args.out_dir / f"N{size}.db"
        if args.force or not path.exists():
            write_db(path, entries)

        type_counts = Counter(entry["type"] for entry in entries)
        token_matches = sum(
            1
            for entry in entries
            if TOKEN in entry["content"].lower() or (entry["title"] and TOKEN in entry["title"].lower())
        )
        manifest["sizes"][str(size)] = {
            "path": str(path.resolve()),
            "sha256": sha256(path),
            "entries": size,
            "type_counts": dict(sorted(type_counts.items())),
            "token": TOKEN,
            "token_matches": token_matches,
            "no_match_token": NO_MATCH_TOKEN,
            "newest_datetime": entries[0]["datetime"],
            "oldest_datetime": entries[-1]["datetime"],
        }
        print(
            f"N={size}: {path} entries={size} types={dict(sorted(type_counts.items()))} "
            f"token_matches={token_matches} sha256={manifest['sizes'][str(size)]['sha256'][:12]}"
        )

    manifest_path = args.out_dir / "datasets.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
