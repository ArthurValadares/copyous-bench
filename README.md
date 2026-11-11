# copyous-bench

Reproducible performance harness for [Copyous](https://github.com/boerdereinar/copyous). It measures clipboard-list startup, opening, scrolling and search on one or more branches, using fresh GNOME Shell launches and deterministic datasets.

The harness is **branch-independent**: it lives outside the checkout it tests, creates a detached `git worktree` per branch, and writes results outside that checkout. Switching branches in the Copyous checkout never removes the harness or the results.

## Requirements

- Linux with GNOME Shell and `gnome-shell-test-tool`
- Node.js and pnpm (the same versions the extension expects)
- Python 3.11+
- `uv` (optional, used to run `analyze.py` with `matplotlib`/`numpy`)
- A Copyous checkout with `node_modules` and `submodules/gnome-shell` already set up

`analyze.py` needs `matplotlib` and `numpy`; install them with `pip install -r requirements.txt` or let the orchestrator use `uv run --with`.

## Layout

```
copyous-bench/          this repository
copyous/                the extension checkout to benchmark
```

The default `--repo` is `../copyous` relative to this repository. You can override it with `--repo` or `COPYOUS_REPO`.

## Quick start

```sh
# Run the full matrix (fresh GNOME Shell per sample)
python3 orchestrator.py \
  --branches main \
             feat/clipboard-item-placeholders \
             feat/lazy-clipboard-items \
             perf/cheaper-cached-async-items \
             merge/clipboard-item-placeholders-cached-async-items \
  --sizes 50 200 500

# Rebuild the database, CSV, Markdown and plots from raw runs
python3 ingest.py
python3 analyze.py
```

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
6. `ingest.py` rebuilds `results.db` from the raw artifacts; `analyze.py` regenerates the CSV, Markdown and plots.

The default benchmark uses a horizontal, fixed-size layout (`250 x 170`), with link preview images disabled. Image entries and dynamic-height vertical are not covered by default.

## Files

- `orchestrator.py` — matrix runner, worktrees, builds, shell launches
- `ingest.py` — raw artifacts to SQLite
- `analyze.py` — SQLite to CSV, Markdown and plots
- `automation/bench.js` — in-shell measurement script
- `fixtures/` — deterministic dataset generator and highlight.js assets
- `wrap/` — GNOME Shell test wrapper and benchmark settings

## License

GPL-3.0, same as Copyous. See `LICENSE`.
