"""Turn the results database into CSV, Markdown and plots."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .data import load_samples, load_smoke, write_csv
from .metrics import HEADLINE_COLUMNS
from .report import build_context, render_markdown


@dataclass(frozen=True)
class AnalysisResult:
    csv: Path
    markdown: Path
    samples: int
    plots: list[Path] = field(default_factory=list)


def analyze(db_path: Path, outdir: Path, *, plots: bool = True) -> AnalysisResult:
    db_path, outdir = db_path.resolve(), outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    with closing(sqlite3.connect(db_path)) as conn:
        samples = load_samples(conn)
        smoke = load_smoke(conn)
        if samples.empty:
            raise SystemExit("No successful samples found in the database")

        csv_path = outdir / "summary.csv"
        markdown_path = outdir / "summary.md"
        write_csv(csv_path, samples, HEADLINE_COLUMNS)
        markdown_path.write_text(render_markdown(build_context(conn, samples, smoke)))

    written: list[Path] = []
    if plots:
        try:
            from .plots import make_plots

            written = make_plots(outdir, samples)
        except ImportError as error:
            print(f"matplotlib unavailable, skipping plots: {error}")
    return AnalysisResult(csv_path, markdown_path, len(samples), written)
