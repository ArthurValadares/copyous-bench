"""The whole campaign: prepare, smoke gate, matrix, ingest, analyse."""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..branches import DEFAULT_BRANCH_NAMES
from ..config import (
    CORE_SIZE,
    DATASET_SIZES,
    DEFAULT_SEED,
    default_repo,
    default_results,
    default_workdir,
)
from ..dataset import ensure_datasets
from ..paths import WRAP_DIR
from ..util import json_dump, now_iso, read_text
from .assets import ensure_hljs
from .build import ExtensionBuilder
from .dbus import discover_dbus_runtime
from .environment import EnvironmentCollector, collect_thermal
from .plan import Block, build_plan, filter_blocks, smoke_plan
from .runner import BlockRunner, RunContext
from .state import StateLog
from .toolchain import Toolchain
from .worktrees import WorktreeManager


@dataclass
class CampaignOptions:
    repo: Path = field(default_factory=default_repo)
    workdir: Path = field(default_factory=default_workdir)
    results_dir: Path = field(default_factory=default_results)
    branches: Sequence[str] = DEFAULT_BRANCH_NAMES
    sizes: Sequence[int] = DATASET_SIZES
    core_samples: int = 6
    scaling_samples: int = 4
    seed: int = DEFAULT_SEED
    timeout: float = 900.0
    stall_timeout: float = 150.0
    retries: int = 2
    retry_delay: float = 10.0
    only: str | None = None
    warmup: bool = True
    resume: bool = False
    recreate_worktrees: bool = False
    skip_build: bool = False
    build_jobs: int = 4
    regenerate_datasets: bool = False
    rebuild_hljs: bool = False
    dbus_run_session: str | None = None
    skip_smoke: bool = False
    smoke_only: bool = False
    dry_run: bool = False
    no_post: bool = False
    campaign_id: str | None = None


class CampaignError(SystemExit):
    """Invalid configuration; exits with a message."""


class Campaign:
    def __init__(self, options: CampaignOptions) -> None:
        self.options = options
        self.toolchain = Toolchain.from_env()
        self.repo = options.repo.expanduser().resolve()
        self.workdir = options.workdir.expanduser().resolve()
        self.results_dir = options.results_dir.expanduser().resolve()

    # -- validation and planning -------------------------------------------
    def _validate(self) -> None:
        if not (self.repo / ".git").exists():
            raise CampaignError(f"--repo is not a Git checkout: {self.repo}")
        if not (self.repo / "resources" / "metadata.json").is_file():
            raise CampaignError("--repo does not look like the Copyous extension (missing resources/metadata.json)")
        if self.results_dir.is_relative_to(self.repo):
            raise CampaignError(
                f"--results-dir must be outside --repo so results survive branch switches: {self.results_dir}"
            )
        if self.workdir.is_relative_to(self.repo):
            raise CampaignError(
                f"--workdir must be outside --repo so worktrees do not pollute the checkout: {self.workdir}"
            )
        if CORE_SIZE not in self.options.sizes:
            raise CampaignError(f"--sizes must include {CORE_SIZE} for the core matrix")

    def _plan(self) -> tuple[list[Block], list[Block]]:
        o = self.options
        smoke = smoke_plan(o.warmup)
        full = build_plan(
            o.branches,
            o.sizes,
            core_samples=o.core_samples,
            scaling_samples=o.scaling_samples,
            warmup=o.warmup,
            seed=o.seed,
        )
        if o.only:
            needles = [item.strip() for item in o.only.split(",") if item.strip()]
            smoke, full = filter_blocks(smoke, needles), filter_blocks(full, needles)
            if not smoke and not full:
                raise CampaignError(f"--only matched no blocks: {o.only}")
        return smoke, full

    # -- preparation (independent steps run concurrently) -------------------
    def _prepare_extensions(self) -> tuple[dict[str, Path], dict[str, Path]]:
        o = self.options
        manager = WorktreeManager(self.repo, self.workdir)
        worktrees = manager.prepare_all(o.branches, recreate=o.recreate_worktrees)
        builder = ExtensionBuilder(self.toolchain, self.workdir / "build")
        return worktrees, builder.build_all(worktrees, skip=o.skip_build, jobs=o.build_jobs)

    # -- execution ------------------------------------------------------------
    def _run_blocks(self, runner: BlockRunner, blocks: list[Block], label: str) -> bool:
        o = self.options
        all_ok = True
        for index, block in enumerate(blocks, start=1):
            print(f"== {label} {index}/{len(blocks)}: {block.run_id} ==")
            ok = False
            for attempt in range(1, o.retries + 2):
                ok = runner.run(block).ok
                if ok:
                    break
                if attempt <= o.retries:
                    print(
                        f"    retry {attempt}/{o.retries} in {o.retry_delay:.0f}s "
                        "(the shell sometimes hangs during startup)"
                    )
                    time.sleep(o.retry_delay)
            all_ok = all_ok and ok
        return all_ok

    def _post_process(self) -> None:
        from ..analyze import analyze
        from ..ingest import ingest

        self.results_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.results_dir / "results.db"
        for label, step in (
            ("Ingest", lambda: ingest(self.workdir, db_path)),
            ("Analyze", lambda: analyze(db_path, self.results_dir)),
        ):
            print(f"== {label} ==")
            try:
                step()
            except (Exception, SystemExit) as error:  # analyze exits when nothing succeeded; replay from raw runs
                print(f"{label.lower()} failed: {error}")

    def run(self) -> int:
        o = self.options
        self._validate()
        smoke_blocks, full_blocks = self._plan()

        if o.dry_run:
            print("== Plan ==")
            for block in smoke_blocks:
                print(f"smoke {block.as_dict()}")
            for block in full_blocks:
                print(f"full  {block.as_dict()}")
            print(f"total full launches: {sum(block.iterations for block in full_blocks)}")
            return 0

        self.workdir.mkdir(parents=True, exist_ok=True)

        # Captured before any build so the thermal snapshot reflects an idle machine.
        print("== Environment ==")
        dbus = discover_dbus_runtime(self.workdir, o.dbus_run_session)
        print(f"dbus: {dbus.kind} ({dbus.run_session})")
        environment = EnvironmentCollector(self.toolchain).collect(self.repo, o.branches, self.workdir, dbus)
        json_dump(self.workdir / "environment.json", environment)

        settings_file = WRAP_DIR / "settings.ini"
        if not settings_file.exists():
            raise CampaignError(f"Settings file missing: {settings_file}")

        print("== Datasets, assets, worktrees and builds ==")
        with ThreadPoolExecutor(max_workers=3) as pool:
            datasets_future = pool.submit(
                ensure_datasets, self.workdir / "datasets", o.sizes, force=o.regenerate_datasets
            )
            hljs_future = pool.submit(ensure_hljs, self.workdir, force=o.rebuild_hljs)
            build_future = pool.submit(self._prepare_extensions)
            datasets = datasets_future.result()
            print(f"highlight.js: {hljs_future.result()}")
            worktrees, zips = build_future.result()
        for branch in o.branches:
            print(f"{branch} -> {worktrees[branch]}\n  {zips[branch]}")

        campaign_id = o.campaign_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        datasets.update(
            campaign_id=campaign_id,
            commits=environment["commits"],
            seed=o.seed,
            core_samples=o.core_samples,
            scaling_samples=o.scaling_samples,
            warmup=o.warmup,
            branches=list(o.branches),
        )
        json_dump(self.workdir / "datasets" / "datasets.json", datasets)

        campaign: dict[str, Any] = {
            "campaign_id": campaign_id,
            "started_at": now_iso(),
            "finished_at": None,
            "environment": environment,
            "datasets": datasets,
            "settings_file": str(settings_file),
            "settings": read_text(settings_file),
            "smoke": [block.as_dict() for block in smoke_blocks],
            "plan": [block.as_dict() for block in full_blocks],
        }
        campaign_path = self.workdir / "campaign.json"
        json_dump(campaign_path, campaign)

        runner = BlockRunner(
            RunContext(
                workdir=self.workdir,
                worktrees=worktrees,
                zips=zips,
                datasets=datasets,
                dbus=dbus,
                toolchain=self.toolchain,
                settings_file=settings_file,
                timeout=o.timeout,
                stall_timeout=o.stall_timeout,
                resume=o.resume,
            ),
            StateLog(self.workdir / "state.jsonl"),
        )

        # Benchmark blocks are deliberately sequential: overlapping runs would corrupt the measurements.
        if not o.skip_smoke:
            print("== Smoke gate ==")
            if not self._run_blocks(runner, smoke_blocks, "smoke"):
                import sys

                print("Smoke gate failed; aborting before the full matrix.", file=sys.stderr)
                campaign["finished_at"] = now_iso()
                json_dump(campaign_path, campaign)
                return 2

        if o.smoke_only:
            campaign["finished_at"] = now_iso()
            json_dump(campaign_path, campaign)
            print("Smoke-only run finished.")
            return 0

        print("== Full matrix ==")
        all_ok = self._run_blocks(runner, full_blocks, "matrix")

        campaign["finished_at"] = now_iso()
        campaign["thermal_end"] = collect_thermal()
        json_dump(campaign_path, campaign)

        if not o.no_post:
            self._post_process()

        if not all_ok:
            import sys

            print("Some matrix blocks failed; see state.jsonl and runs/*/meta.json", file=sys.stderr)
            return 1
        return 0
