"""Which metrics appear in which report section."""

from __future__ import annotations

from typing import NamedTuple


class Metric(NamedTuple):
    column: str
    label: str


HEADLINE_COLUMNS = [
    "run_id", "branch", "config", "size", "iteration",
    "startup_ready_ms", "startup_to_run_ms",
    "open_first_frame_ms", "open_animation_ms", "open_settle_ms",
    "scroll_first_frame_ms", "scroll_settle_ms", "scroll_passes", "scroll_long_frames", "scroll_fps",
    "search_nomatch_apply_ms", "search_match_apply_ms", "search_clear_apply_ms",
    "search_clear_first_frame_ms", "search_clear_ready_ms", "search_match_results", "search_clear_results",
    "rss_ready_kb", "rss_end_kb", "hwm_end_kb", "cpu_total_ms", "actors_ready", "actors_end",
    "data_entries", "items_ready", "placeholders_ready", "items_end", "placeholders_end",
    "items_after_scroll", "placeholders_after_scroll", "items_after_search", "placeholders_after_search",
]  # fmt: skip

METRIC_SECTIONS: dict[str, list[Metric]] = {
    "Startup & open": [
        Metric("startup_ready_ms", "startup ready (ms)"),
        Metric("startup_to_run_ms", "init→run (ms)"),
        Metric("open_first_frame_ms", "open first frame (ms)"),
        Metric("open_animation_ms", "open animation (ms)"),
        Metric("open_settle_ms", "open settle (ms)"),
    ],
    "Scroll": [
        Metric("scroll_first_frame_ms", "scroll first frame (ms)"),
        Metric("scroll_settle_ms", "scroll settle (ms)"),
        Metric("scroll_passes", "scroll passes"),
        Metric("scroll_long_frames", ">50ms frames"),
        Metric("scroll_fps", "scroll fps"),
    ],
    "Search": [
        Metric("search_nomatch_apply_ms", "no-match apply (ms)"),
        Metric("search_match_apply_ms", "match apply (ms)"),
        Metric("search_clear_apply_ms", "clear apply (ms)"),
        Metric("search_clear_first_frame_ms", "clear first frame (ms)"),
        Metric("search_clear_ready_ms", "clear ready (ms)"),
    ],
    "Memory & CPU": [
        Metric("rss_ready_kb", "RSS ready (kB)"),
        Metric("rss_end_kb", "RSS end (kB)"),
        Metric("hwm_end_kb", "RSS peak (kB)"),
        Metric("cpu_total_ms", "CPU total (ms)"),
        Metric("actors_ready", "actors ready"),
        Metric("actors_end", "actors end"),
    ],
    "Widgets": [
        Metric("items_ready", "items ready"),
        Metric("placeholders_ready", "placeholders ready"),
        Metric("items_after_scroll", "items after scroll"),
        Metric("placeholders_after_scroll", "placeholders after scroll"),
        Metric("items_after_search", "items after search"),
        Metric("placeholders_after_search", "placeholders after search"),
    ],
}

CONFIG_SECTIONS: dict[str, list[str]] = {
    "open": ["Startup & open", "Memory & CPU"],
    "scroll": ["Startup & open", "Scroll", "Widgets", "Memory & CPU"],
    "search": ["Startup & open", "Search", "Widgets", "Memory & CPU"],
}
