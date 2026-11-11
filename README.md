# copyous-bench

Reproducible performance harness for [Copyous](https://github.com/boerdereinar/copyous). It measures clipboard-list startup, opening, scrolling and search on one or more branches, using fresh GNOME Shell launches and deterministic datasets.

The harness is **branch-independent**: it lives outside the checkout it tests, creates a detached `git worktree` per branch, and writes results outside that checkout. Switching branches in the Copyous checkout never removes the harness or the results.

## Requirements

- Linux with GNOME Shell and `gnome-shell-test-tool`
- Node.js and pnpm (the same versions the extension expects)
- [`uv`](https://docs.astral.sh/uv/) — Python 3.13 is resolved automatically
- A Copyous checkout with `node_modules` and `submodules/gnome-shell` already set up

The harness is managed entirely by `uv`. `uv sync` creates `.venv` and installs
the runtime dependencies (`matplotlib`, `numpy`) declared in `pyproject.toml`
and pinned in `uv.lock`.

## Layout

```
copyous-bench/              this repository
├── pyproject.toml          uv project, dependencies and tool config
├── uv.lock                 resolved dependency lockfile
├── src/copyous_bench/      the Python package
├── automation/             in-shell measurement script (bench.js)
├── fixtures/               highlight.js asset builder
└── wrap/                   GNOME Shell test wrapper and settings
copyous/                    the extension checkout to benchmark
```

The default `--repo` is `../copyous` relative to this repository. You can override it with `--repo` or `COPYOUS_REPO`.

## Quick start

```sh
# Install the harness and its dependencies into .venv
uv sync

# Run the full matrix (fresh GNOME Shell per sample)
uv run copyous-bench \
  --branches main \
             feat/clipboard-item-placeholders \
             feat/lazy-clipboard-items \
             perf/cheaper-cached-async-items \
             merge/clipboard-item-placeholders-cached-async-items \
  --sizes 50 200 500

# Rebuild the database, CSV, Markdown and plots from raw runs
uv run copyous-bench-ingest
uv run copyous-bench-analyze
```

The package also exposes `python -m copyous_bench.orchestrator`,
`copyous_bench.ingest`, `copyous_bench.analyze` and `copyous_bench.dataset`.

Outputs go to `./results/` by default (`results.db`, `summary.md`, `summary.csv`, `plots/*.png`). The directory is git-ignored; attach or publish it separately.

## Options

| Option | Default | Purpose |
| --- | --- | --- |
| `--repo` | `$COPYOUS_REPO` or `../copyous` | Copyous checkout to benchmark |
| `--workdir` | `$COPYOUS_BENCH_WORKDIR` or `<tmp>/copyous-bench` | Worktrees and raw runs |
| `--results-dir` | `$COPYOUS_BENCH_RESULTS` or `./results` | Database, summaries and plots |
| `--branches` | the five benchmark branches | Branches to test |
| `--sizes` | `50 200 500` | Dataset sizes (must include 500) |
| `--core-samples` | `6` | Samples for the core matrix |
| `--scaling-samples` | `4` | Samples for the scaling matrix |
| `--no-post` | off | Skip ingest/analyze after the run |
| `--resume` | off | Skip blocks already marked ok |
| `--skip-build` | off | Reuse existing extension zips |

`--workdir` and `--results-dir` are rejected if they are inside `--repo`, so the tested checkout stays clean.

## How a run works

1. Each branch is checked out into a detached worktree under `--workdir`.
2. Every worktree shares the main checkout's `node_modules` and `submodules/gnome-shell`.
3. The extension is built once per branch with identical flags.
4. `gnome-shell-test-tool` runs in a private D-Bus session, one fresh shell per sample, with the benchmark settings in `wrap/settings.ini`.
5. Raw sidecars, reports and logs are written per iteration under `--workdir`.
6. `copyous_bench.ingest` rebuilds `results.db` from the raw artifacts; `copyous_bench.analyze` regenerates the CSV, Markdown and plots.

The default benchmark uses a horizontal, fixed-size layout (`250 x 170`), with link preview images disabled. Image entries and dynamic-height vertical are not covered by default.

## Files

- `src/copyous_bench/orchestrator.py` — matrix runner, worktrees, builds, shell launches
- `src/copyous_bench/ingest.py` — raw artifacts to SQLite
- `src/copyous_bench/analyze.py` — SQLite to CSV, Markdown and plots
- `src/copyous_bench/dataset.py` — deterministic dataset generator
- `src/copyous_bench/paths.py` — shared repository paths
- `automation/bench.js` — in-shell measurement script
- `fixtures/build_hljs.sh` — highlight.js asset builder
- `wrap/` — GNOME Shell test wrapper and benchmark settings

## Development

Quality tooling is wired through `pre-commit` and configured in `pyproject.toml`:

```sh
uv sync                       # create .venv from uv.lock
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
uv run pre-commit run --all-files
uv run pytest
```

- **ruff** — linting (`E`, `F`, `W`, `I`, `UP`, `B`, `C4`, `SIM`, `RUF`) and formatting.
- **mypy** — static typing, run against `src/` inside the `uv` environment.
- **pytest** — unit tests under `tests/`.
- **pre-commit-hooks** — trailing whitespace, end-of-file, YAML/TOML, large files, merge conflicts.

Run the tools directly with `uv run ruff check --fix .`, `uv run ruff format .`,
`uv run mypy` and `uv run pytest`.

### Commit messages

This repository follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <subject>
```

Use `uv run cz commit` for a guided prompt, or `uv run cz check --rev-range
origin/main..HEAD` to validate a range. The `commit-msg` hook rejects messages
that do not match the convention.

## License

GPL-3.0, same as Copyous. See `LICENSE`.
