"""Descriptive statistics and number formatting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float | None = None
    std: float | None = None
    median: float | None = None
    p95: float | None = None
    min: float | None = None
    max: float | None = None


def summarize(values: Iterable[Any]) -> Summary:
    """Summarise numeric values, ignoring ``None``/NaN (sample standard deviation)."""
    array = pd.Series(list(values), dtype="Float64").dropna().to_numpy(dtype=float)
    if array.size == 0:
        return Summary(0)
    return Summary(
        n=int(array.size),
        mean=float(array.mean()),
        std=float(array.std(ddof=1)) if array.size > 1 else 0.0,
        median=float(np.median(array)),
        p95=float(np.percentile(array, 95)),
        min=float(array.min()),
        max=float(array.max()),
    )


def fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return f"{value:,}"


def fmt_pm(summary: Summary, digits: int = 1) -> str:
    if summary.n == 0:
        return "—"
    if summary.n == 1:
        return fmt(summary.mean, digits)
    return f"{fmt(summary.mean, digits)} ± {fmt(summary.std, digits)}"


def percent_delta(value: float | None, base: float | None) -> str:
    if value is None or base in (None, 0):
        return "—"
    return f"{(value - base) / base * 100:+.1f}%"
