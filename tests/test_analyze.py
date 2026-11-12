import sqlite3
import sys
from contextlib import closing

import pandas as pd
import pytest

from copyous_bench.analyze import analyze
from copyous_bench.analyze import plots as plots_module
from copyous_bench.analyze.data import (
    campaign_environment,
    group_samples,
    json_load,
    load_samples,
    load_smoke,
    write_csv,
)
from copyous_bench.analyze.metrics import HEADLINE_COLUMNS
from copyous_bench.analyze.report import (
    DatasetsSection,
    EnvironmentSection,
    ReportContext,
    ResultsSection,
    SmokeSection,
    md_table,
    render_markdown,
)
from copyous_bench.ingest import ingest


@pytest.fixture
def database(workdir, tmp_path):
    db = tmp_path / "results.db"
    ingest(workdir, db)
    return db


def frame(rows, columns):
    return pd.DataFrame(rows, columns=columns).convert_dtypes()


def test_json_load_and_group(database):
    assert json_load(None) is None
    assert json_load("{bad") is None
    assert json_load('{"a": 1}') == {"a": 1}
    with closing(sqlite3.connect(database)) as conn:
        samples, smoke = load_samples(conn), load_smoke(conn)
        assert campaign_environment(conn)["hostname"] == "host"
        conn.execute("delete from campaign")
        assert campaign_environment(conn) is None
    assert set(samples["config"]) == {"open", "scroll", "search"} and set(smoke["config"]) == {"all"}
    grouped = group_samples(samples, "open", 200)
    assert next(iter(grouped)) == "main" and list(grouped)[-1] == "unlisted/branch"
    assert group_samples(samples, "scroll", 50) == {}


def test_write_csv_uses_headline_columns(database, tmp_path):
    with closing(sqlite3.connect(database)) as conn:
        samples = load_samples(conn)
    write_csv(tmp_path / "s.csv", samples, [*HEADLINE_COLUMNS, "nonexistent"])
    header = (tmp_path / "s.csv").read_text().splitlines()[0].split(",")
    assert header[: len(HEADLINE_COLUMNS)] == HEADLINE_COLUMNS and header[-1] == "nonexistent"


def test_analyze_end_to_end(database, tmp_path):
    out = tmp_path / "out"
    result = analyze(database, out)
    assert result.samples > 0 and result.csv.exists()
    assert {p.name for p in result.plots} == {
        "open.png", "scroll.png", "search_apply.png", "search_ready.png",
        "memory_cpu.png", "widgets_after_search.png", "scaling_open.png",
    }  # fmt: skip
    text = result.markdown.read_text()
    for heading in (
        "## Environment",
        "## Datasets",
        "## Results",
        "### Smoke gate",
        "### N=500 · search",
        "Δ versus main",
    ):
        assert heading in text
    assert "`lazy`" in text and "`unlisted/branch`" in text and "x86_pkg=41.5°C" in text


def test_analyze_without_plots_and_without_matplotlib(database, tmp_path, monkeypatch, capsys):
    assert analyze(database, tmp_path / "a", plots=False).plots == []
    monkeypatch.setitem(sys.modules, "copyous_bench.analyze.plots", None)
    assert analyze(database, tmp_path / "b").plots == []
    assert "matplotlib unavailable" in capsys.readouterr().out


def test_analyze_errors(tmp_path):
    with pytest.raises(SystemExit, match="Database not found"):
        analyze(tmp_path / "missing.db", tmp_path)
    empty = tmp_path / "empty.db"
    ingest(tmp_path / "nothing", empty)
    with pytest.raises(SystemExit, match="No successful samples"):
        analyze(empty, tmp_path / "out")


def test_report_sections_without_data():
    empty = frame([], ["config", "size", "branch", "status"])
    context = ReportContext(None, [], empty, empty)
    assert "No campaign metadata found." in EnvironmentSection().render(context)
    assert DatasetsSection().render(context) == []
    assert SmokeSection().render(context) == []
    assert ResultsSection().render(context)[0] == "## Results"
    assert "Thermals at start" not in "\n".join(
        EnvironmentSection().render(ReportContext({"governors": {}}, [], empty, empty))
    )
    assert "| CPU governor | — |" in EnvironmentSection().render(ReportContext({"governors": None}, [], empty, empty))
    assert render_markdown(context).startswith("# Copyous branch benchmark")


def test_md_table_alignment():
    assert md_table(["a", "b"], [[1, 2]]) == ["| a | b |", "| --- | --- |", "| 1 | 2 |", ""]
    assert md_table(["a", "b"], [], right_from=1)[1] == "| --- | ---: |"


def test_delta_is_omitted_without_main_branch():
    columns = ["config", "size", "branch", "startup_ready_ms", "startup_to_run_ms", "open_first_frame_ms",
               "open_animation_ms", "open_settle_ms", "rss_ready_kb", "rss_end_kb", "hwm_end_kb",
               "cpu_total_ms", "actors_ready", "actors_end"]  # fmt: skip
    samples = frame([["open", 500, "feat/lazy-clipboard-items", *range(11)]], columns)
    lines = ResultsSection().render(ReportContext(None, [], samples, samples))
    assert "### N=500 · open" in lines and "**Δ versus main (mean)**" not in lines


# -- plots ---------------------------------------------------------------------------------------------------------
def plot_frame(rows):
    columns = ["config", "size", "branch", "open_first_frame_ms", "items_after_search", "placeholders_after_search"]
    df = frame(rows, columns)
    for extra in ("open_animation_ms", "open_settle_ms"):
        df[extra] = pd.array([pd.NA] * len(df), dtype="Float64")
    return df


def test_plots_return_none_when_there_is_nothing_to_draw(tmp_path):
    empty = plot_frame([])
    for plot in plots_module.PLOTS:
        assert plot.render(empty) is None
    assert plots_module.make_plots(tmp_path, empty) == []
    assert (tmp_path / "plots").is_dir()


def test_scaling_needs_two_sizes_and_skips_missing_values():
    one_size = plot_frame([["open", 500, "main", 1.0, None, None]])
    assert plots_module.OpenScaling().render(one_size) is None
    rows = [
        ["open", 50, "main", 1.0, None, None],
        ["open", 500, "main", 2.0, None, None],
        ["open", 500, "feat/lazy-clipboard-items", None, None, None],  # only NaN at this size
    ]
    assert plots_module.OpenScaling().render(plot_frame(rows)) is not None


def test_widget_composition_skips_branches_without_counts_and_handles_partial_data():
    rows = [
        ["search", 500, "main", None, None, None],  # nothing recorded: skipped
        ["search", 500, "feat/lazy-clipboard-items", None, 10, None],  # items only
        ["search", 500, "feat/clipboard-item-placeholders", None, None, 5],  # placeholders only
    ]
    assert plots_module.WidgetComposition().render(plot_frame(rows)) is not None
    assert plots_module.WidgetComposition().render(plot_frame(rows[:1])) is None


def test_box_panel_is_hidden_without_values():
    rows = [["open", 500, "main", None, None, None]]
    figure = plots_module.PLOTS[0].render(plot_frame(rows))
    assert figure is not None and not figure.axes[0].get_visible()
