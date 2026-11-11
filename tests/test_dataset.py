import sqlite3

from copyous_bench.dataset import MasterListGenerator, ensure_datasets, generate_datasets


def test_master_list_is_deterministic_and_prefix_stable():
    first = MasterListGenerator().generate()
    second = MasterListGenerator().generate()
    assert first == second
    assert len({e.datetime for e in first}) == len(first) == 500
    assert first[0].datetime > first[-1].datetime  # entry 0 is the newest


def test_token_every_seventh_entry():
    entries = MasterListGenerator().generate()
    assert [i for i, e in enumerate(entries[:20]) if e.matches("benchmark")] == [3, 10, 17]


def test_generate_and_ensure(tmp_path):
    manifest, infos = generate_datasets(tmp_path, [50, 200])
    assert [i.size for i in infos] == [50, 200]
    with sqlite3.connect(tmp_path / "N50.db") as conn:
        assert conn.execute("select count(*) from clipboard").fetchone()[0] == 50
        assert conn.execute("select version from clipboard_version").fetchone()[0] == 2
    assert ensure_datasets(tmp_path, [50, 200]) == manifest
    # A missing size is generated without needing --force.
    assert "500" in ensure_datasets(tmp_path, [50, 500])["sizes"]
