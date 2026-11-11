import math
import statistics

import pandas as pd

from copyous_bench.analyze.stats import fmt_pm, percent_delta, summarize


def test_summarize_matches_statistics_module():
    values = [1.0, 2.0, 4.0, 8.0, 16.0]
    s = summarize(values)
    assert s.n == 5
    assert math.isclose(s.mean, statistics.fmean(values))
    assert math.isclose(s.std, statistics.stdev(values))
    assert s.median == 4.0
    assert math.isclose(s.p95, 14.4)


def test_summarize_handles_missing_values():
    assert summarize([]).n == 0
    assert fmt_pm(summarize([None, pd.NA])) == "—"
    assert fmt_pm(summarize(pd.Series([5, None], dtype="Int64"))) == "5.0"
    assert fmt_pm(summarize([1.0, 3.0])) == "2.0 ± 1.4"


def test_percent_delta():
    assert percent_delta(110.0, 100.0) == "+10.0%"
    assert percent_delta(1.0, 0) == "—"
    assert percent_delta(None, 5.0) == "—"
