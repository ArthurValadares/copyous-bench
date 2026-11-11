"""Catalogue of the branches under test: canonical order and short labels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Branch:
    name: str
    label: str


BRANCHES: tuple[Branch, ...] = (
    Branch("main", "main"),
    Branch("feat/clipboard-item-placeholders", "placeholders"),
    Branch("feat/lazy-clipboard-items", "lazy"),
    Branch("perf/cheaper-cached-async-items", "cached-async"),
    Branch("merge/clipboard-item-placeholders-cached-async-items", "merge"),
)

DEFAULT_BRANCH_NAMES: tuple[str, ...] = tuple(branch.name for branch in BRANCHES)
_LABELS = {branch.name: branch.label for branch in BRANCHES}
_ORDER = {branch.name: index for index, branch in enumerate(BRANCHES)}


def label_of(name: str) -> str:
    return _LABELS.get(name, name)


def order_key(name: str) -> int:
    """Sort key: known branches in catalogue order, unknown ones last."""
    return _ORDER.get(name, len(BRANCHES))
