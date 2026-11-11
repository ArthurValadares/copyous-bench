"""Command line interface: ``copyous-bench {run,dataset,ingest,analyze}``."""

from __future__ import annotations

from pathlib import Path

import click

from .branches import DEFAULT_BRANCH_NAMES
from .config import DATASET_SIZES, DEFAULT_SEED, default_repo, default_results, default_workdir

PATH = click.Path(path_type=Path)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Reproducible performance harness for the Copyous GNOME Shell extension."""


@main.command()
@click.option("--out-dir", type=PATH, default=lambda: default_results() / "datasets", show_default="results/datasets")
@click.option("--sizes", type=int, multiple=True, default=DATASET_SIZES, show_default=True)
@click.option("--force", is_flag=True)
def dataset(out_dir: Path, sizes: tuple[int, ...], force: bool) -> None:
    """Generate the deterministic clipboard-history databases."""
    from .dataset import generate_datasets

    try:
        _, infos = generate_datasets(out_dir, sizes, force=force)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    for info in infos:
        click.echo(info.summary())
    click.echo(f"manifest: {out_dir / 'datasets.json'}")


@main.command()
@click.option("--workdir", type=PATH, default=default_workdir, show_default="$COPYOUS_BENCH_WORKDIR")
@click.option("--db", type=PATH, default=lambda: default_results() / "results.db", show_default="results/results.db")
def ingest(workdir: Path, db: Path) -> None:
    """Ingest raw runs into a SQLite database (rebuilt from scratch)."""
    from .ingest import ingest as run_ingest

    stats = run_ingest(workdir, db)
    click.echo(f"campaign: {stats.campaign_id}")
    click.echo(f"datasets: {stats.dataset_sizes}")
    click.echo(f"ingested {stats.samples} samples from {stats.blocks} blocks into {stats.db_path}")


@main.command()
@click.option("--db", type=PATH, default=lambda: default_results() / "results.db", show_default="results/results.db")
@click.option("--outdir", type=PATH, default=default_results, show_default="$COPYOUS_BENCH_RESULTS")
@click.option("--no-plots", is_flag=True)
def analyze(db: Path, outdir: Path, no_plots: bool) -> None:
    """Write summary.csv, summary.md and plots."""
    from .analyze import analyze as run_analyze

    result = run_analyze(db, outdir, plots=not no_plots)
    for path in result.plots:
        click.echo(f"plot: {path}")
    click.echo(f"csv: {result.csv}")
    click.echo(f"markdown: {result.markdown}")
    click.echo(f"samples: {result.samples}")


@main.command()
@click.option("--repo", type=PATH, default=default_repo, show_default="$COPYOUS_REPO or ../copyous")
@click.option("--workdir", type=PATH, default=default_workdir, show_default="$COPYOUS_BENCH_WORKDIR")
@click.option("--results-dir", type=PATH, default=default_results, show_default="$COPYOUS_BENCH_RESULTS")
@click.option("--branches", multiple=True, default=DEFAULT_BRANCH_NAMES)
@click.option("--sizes", type=int, multiple=True, default=DATASET_SIZES, show_default=True)
@click.option("--core-samples", type=int, default=6, show_default=True)
@click.option("--scaling-samples", type=int, default=4, show_default=True)
@click.option("--seed", type=int, default=DEFAULT_SEED, show_default=True)
@click.option("--timeout", type=float, default=900.0, show_default=True, help="Timeout per block (s).")
@click.option(
    "--stall-timeout",
    type=float,
    default=150.0,
    show_default=True,
    help="Kill a block with no new sidecar for this long (s).",
)
@click.option("--retries", type=int, default=2, show_default=True)
@click.option("--retry-delay", type=float, default=10.0, show_default=True)
@click.option("--only", default=None, help="Comma-separated run_id substrings to run.")
@click.option("--warmup/--no-warmup", default=True, show_default=True)
@click.option("--resume", is_flag=True, help="Skip blocks whose meta.json is ok.")
@click.option("--recreate-worktrees", is_flag=True)
@click.option("--skip-build", is_flag=True)
@click.option("--build-jobs", type=int, default=4, show_default=True, help="Parallel builds (use 1 if pnpm contends).")
@click.option("--regenerate-datasets", is_flag=True)
@click.option("--rebuild-hljs", is_flag=True)
@click.option("--dbus-run-session", default=None)
@click.option("--skip-smoke", is_flag=True)
@click.option("--smoke-only", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("--no-post", is_flag=True)
@click.option("--campaign-id", default=None)
@click.pass_context
def run(ctx: click.Context, **options: object) -> None:
    """Run the benchmark matrix."""
    from .orchestration import Campaign, CampaignOptions

    ctx.exit(Campaign(CampaignOptions(**options)).run())  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
