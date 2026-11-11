"""Write clipboard entries into a database using the extension's schema (v2)."""

from __future__ import annotations

import sqlite3
import textwrap
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from .generator import ClipboardEntry

SCHEMA_VERSION = 2

# SQLite stores the CREATE text verbatim, so the indentation is part of the file's
# SHA-256; keep it identical to earlier campaigns.
_SCHEMA = f"""
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
INSERT INTO 'clipboard_version' (id, version) VALUES (1, {SCHEMA_VERSION});
"""
SCHEMA_SQL = textwrap.indent(_SCHEMA, " " * 12)

INSERT_SQL = """
INSERT INTO 'clipboard' ('type', 'content', 'pinned', 'tag', 'datetime', 'metadata', 'title')
VALUES (:type, :content, :pinned, :tag, :datetime, :metadata, :title)
"""


def write_database(path: Path, entries: Sequence[ClipboardEntry]) -> None:
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executemany(INSERT_SQL, [entry.as_row() for entry in entries])
        conn.commit()
        conn.execute("VACUUM")
