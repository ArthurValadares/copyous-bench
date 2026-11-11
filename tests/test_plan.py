from copyous_bench.branches import DEFAULT_BRANCH_NAMES
from copyous_bench.orchestration.plan import Block, build_plan, filter_blocks, smoke_plan


def _plan(seed=1):
    return build_plan(DEFAULT_BRANCH_NAMES, [50, 200, 500], core_samples=6, scaling_samples=4, warmup=True, seed=seed)


def test_plan_shape_and_determinism():
    plan = _plan()
    assert len(plan) == 5 * (3 + 2)
    assert plan == _plan()
    assert plan != _plan(seed=2)
    assert sum(b.iterations for b in plan) == 5 * (3 * 7 + 2 * 5)


def test_block_ids_and_filter():
    block = Block("feat/lazy-clipboard-items", "open", 500, 6, True)
    assert block.run_id == "feat-lazy-clipboard-items__open__N500"
    assert smoke_plan(True)[0].run_id.endswith("__smoke")
    assert filter_blocks(_plan(), ["__open__N50"]) and all(
        "__open__N50" in b.run_id for b in filter_blocks(_plan(), ["__open__N50"])
    )
