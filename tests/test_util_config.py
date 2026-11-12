from pathlib import Path

from copyous_bench import config
from copyous_bench.branches import label_of, order_key
from copyous_bench.util import json_dump, json_load, now_iso, read_text, slug


def test_util(tmp_path):
    assert slug("feat/some branch!") == "feat-some-branch-"
    assert now_iso().endswith("Z")
    assert read_text(tmp_path / "missing") == ""
    (tmp_path / "t.txt").write_text("hi")
    assert read_text(tmp_path / "t.txt") == "hi"
    json_dump(tmp_path / "a" / "b.json", {"b": 1, "a": 2})
    assert json_load(tmp_path / "a" / "b.json") == {"a": 2, "b": 1}
    assert (tmp_path / "a" / "b.json").read_text().endswith("}\n")


def test_branches():
    assert label_of("main") == "main"
    assert label_of("feat/lazy-clipboard-items") == "lazy"
    assert label_of("other") == "other"
    assert order_key("main") < order_key("feat/lazy-clipboard-items") < order_key("other")


def test_config_defaults_and_overrides(monkeypatch, tmp_path):
    for name in ("COPYOUS_REPO", "COPYOUS_BENCH_WORKDIR", "COPYOUS_BENCH_RESULTS"):
        monkeypatch.delenv(name, raising=False)
    assert config.default_repo().name == "copyous"
    assert config.default_workdir().name == "copyous-bench"
    assert config.default_results().name == "results"

    monkeypatch.setenv("COPYOUS_REPO", str(tmp_path / "r"))
    monkeypatch.setenv("COPYOUS_BENCH_WORKDIR", str(tmp_path / "w"))
    monkeypatch.setenv("COPYOUS_BENCH_RESULTS", str(tmp_path / "x"))
    assert config.default_repo() == tmp_path / "r"
    assert config.default_workdir() == tmp_path / "w"
    assert config.default_results() == tmp_path / "x"
    assert isinstance(config.default_repo(), Path)
