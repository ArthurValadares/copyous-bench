import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from copyous_bench.dataset import MasterListGenerator, ensure_datasets, generate_datasets
from copyous_bench.dataset import generator as generator_module
from copyous_bench.dataset.builders import CharacterBuilder, CodeBuilder, default_builders
from copyous_bench.dataset.service import sha256_of
from copyous_bench.dataset.templates import CHARACTERS, LANGUAGES


def test_master_list_is_deterministic_and_prefix_stable():
    first = MasterListGenerator().generate()
    assert first == MasterListGenerator().generate()
    assert len({e.datetime for e in first}) == len(first) == 500
    assert first[0].datetime > first[-1].datetime  # entry 0 is the newest


def test_token_every_seventh_entry():
    entries = MasterListGenerator().generate()
    assert [i for i, e in enumerate(entries[:20]) if e.matches("benchmark")] == [3, 10, 17]


def test_entry_row_and_title_match():
    entry = MasterListGenerator().generate()[0]
    assert entry.as_row()["type"] == entry.type
    titled = type(entry)(type="Text", content="x", datetime="d", title="A Benchmark")
    assert titled.matches("benchmark")
    assert not type(entry)(type="Text", content="x", datetime="d").matches("benchmark")


def test_generator_rejects_inconsistent_configuration():
    with pytest.raises(ValueError, match="add up"):
        MasterListGenerator(size=10)
    with pytest.raises(ValueError, match="unexpectedly contains the token"):
        MasterListGenerator(token="the").generate()


def test_generator_rejects_duplicate_timestamps(monkeypatch):
    monkeypatch.setattr(generator_module, "timedelta", lambda minutes: timedelta(0))
    with pytest.raises(ValueError, match="not unique"):
        MasterListGenerator().generate()


def test_builders_rotate_through_templates():
    code, chars = CodeBuilder(), CharacterBuilder()
    languages = [code.build(i, False).metadata["language"]["id"] for i in range(len(LANGUAGES) + 1)]
    assert languages[0] == languages[-1] == LANGUAGES[0][0]
    assert [chars.build(i, False).content for i in range(len(CHARACTERS) + 1)][-1] == CHARACTERS[0]
    assert set(default_builders()) == {"Text", "Code", "Link", "Color", "Character"}


def test_generate_and_ensure(tmp_path):
    manifest, infos = generate_datasets(tmp_path, [50, 200])
    assert [i.size for i in infos] == [50, 200]
    assert "N=50:" in infos[0].summary()
    assert infos[0].sha256 == sha256_of(tmp_path / "N50.db")
    with closing(sqlite3.connect(tmp_path / "N50.db")) as conn:
        assert conn.execute("select count(*) from clipboard").fetchone()[0] == 50
        assert conn.execute("select version from clipboard_version").fetchone()[0] == 2
    assert ensure_datasets(tmp_path, [50, 200]) == manifest
    # A missing size is generated without needing --force.
    assert "500" in ensure_datasets(tmp_path, [50, 500])["sizes"]


def test_ensure_regenerates_when_a_database_vanished(tmp_path):
    generate_datasets(tmp_path, [50])
    (tmp_path / "N50.db").unlink()
    ensure_datasets(tmp_path, [50])
    assert (tmp_path / "N50.db").exists()


def test_force_rewrites_and_oversized_is_rejected(tmp_path):
    generate_datasets(tmp_path, [50])
    before = (tmp_path / "N50.db").stat().st_mtime_ns
    generate_datasets(tmp_path, [50], force=True)
    assert (tmp_path / "N50.db").stat().st_mtime_ns >= before
    with pytest.raises(ValueError, match="exceeds master size"):
        generate_datasets(tmp_path, [501])
