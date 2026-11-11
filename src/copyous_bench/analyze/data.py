"""Load results into pandas and group them by branch."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from ..branches import order_key

CONFIGS = ("open", "scroll", "search")


def json_load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _query(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    # Nullable dtypes keep integer columns as integers even when some values are NULL.
    return pd.read_sql_query(sql, conn).convert_dtypes()


def load_samples(conn: sqlite3.Connection) -> pd.DataFrame:
    return _query(
        conn,
        """SELECT * FROM run
           WHERE warmup = 0 AND status = 'ok' AND config IN ('open', 'scroll', 'search')
           ORDER BY config, size, branch, iteration""",
    )


def load_smoke(conn: sqlite3.Connection) -> pd.DataFrame:
    return _query(conn, "SELECT * FROM run WHERE warmup = 0 AND config = 'all' ORDER BY branch, iteration")


def dataset_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = conn.execute("SELECT * FROM dataset ORDER BY size")
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def campaign_environment(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT environment FROM campaign LIMIT 1").fetchone()
    return json_load(row[0]) if row else None


def group_samples(samples: pd.DataFrame, config: str, size: int) -> dict[str, pd.DataFrame]:
    """Samples of one (config, size), keyed by branch in catalogue order."""
    subset = samples[(samples["config"] == config) & (samples["size"] == size)]
    branches = sorted(subset["branch"].dropna().unique(), key=order_key)
    return {branch: subset[subset["branch"] == branch] for branch in branches}


def write_csv(path: Path, samples: pd.DataFrame, columns: list[str]) -> None:
    samples.reindex(columns=columns).to_csv(path, index=False)
