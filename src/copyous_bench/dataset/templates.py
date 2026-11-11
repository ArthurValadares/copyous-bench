"""Static sample content used to build the clipboard entries."""

from __future__ import annotations

LANGUAGES = [
    ("typescript", "TypeScript"),
    ("python", "Python"),
    ("rust", "Rust"),
    ("json", "JSON"),
    ("bash", "Bash"),
]

TEXT_TEMPLATES = [
    "Meeting notes: the release is on track and the remaining work is documentation.",
    "Remember to renew the domain certificate before the end of the month.",
    "A short reminder.",
    "The quick brown fox jumps over the lazy dog while the build finishes.",
    "Configuration values for the staging environment live in the shared vault.",
    "This is a longer paragraph that is meant to exercise line wrapping in the clipboard item. "
    "It contains several sentences so that the text preview has to wrap across multiple lines "
    "and the layout engine has real work to do.",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH7examplekeycopyoussample user@host",
    "https://example.com/docs/getting-started?section=installation",
    "TODO: remove the temporary workaround once the upstream fix is released.",
    "Line one\nLine two\nLine three\nLine four",
    "A list of values: alpha, beta, gamma, delta, epsilon, zeta, eta, theta.",
    "The database migration completed successfully after the service restarted.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt "
    "ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation.",
    "Note: keep the clipboard history size bounded to avoid unbounded memory growth.",
    "Recipe: flour, water, salt, yeast. Mix, rest, fold, bake.",
]

CODE_TEMPLATES = {
    "typescript": """interface SampleOptions {
    iterations: number;
    warmup: boolean;
}

export function runSample(options: SampleOptions): number {
    let total = 0;
    for (let i = 0; i < options.iterations; i++) {
        total += i * i;
    }
    return total;
}""",
    "python": """from dataclasses import dataclass


@dataclass
class SampleResult:
    name: str
    duration_ms: float


def run_sample(iterations: int) -> SampleResult:
    total = sum(i * i for i in range(iterations))
    return SampleResult("sum", total / 1000.0)""",
    "rust": """use std::time::Instant;

pub struct SampleResult {
    pub duration_ms: f64,
    pub samples: usize,
}

pub fn run(iterations: usize) -> SampleResult {
    let start = Instant::now();
    let mut total = 0usize;
    for i in 0..iterations {
        total = total.wrapping_add(i * i);
    }
    let _ = total;
    SampleResult {
        duration_ms: start.elapsed().as_secs_f64() * 1000.0,
        samples: iterations,
    }
}""",
    "json": """{
    "name": "copyous-sample",
    "iterations": 500,
    "settings": {
        "orientation": "horizontal",
        "itemWidth": 250,
        "itemHeight": 170
    },
    "enabled": true
}""",
    "bash": """#!/usr/bin/env bash
set -euo pipefail

iterations=${1:-500}
total=0

for ((i = 0; i < iterations; i++)); do
    total=$((total + i * i))
done

printf 'total=%s\\n' "$total\"""",
}

CHARACTERS = [
    "🫃", "👨‍👨‍👶‍👧", "😀", "😃", "😄", "😁", "😆", "😅", "🤣", "😂",
    "🙂", "🙃", "😉", "😊", "😇", "🥰", "😍", "🤩", "😘", "😗",
    "😚", "😙", "🥲", "😋", "😛", "😜", "🤪", "😝", "🤑", "🤗",
    "🤭", "🤫", "🤔", "🤐", "🤨", "😐", "😑", "😶", "😏", "😒",
    "🙄", "😬", "🤥", "😌", "😔", "😪", "🤤", "😴", "😷", "🤒",
]  # fmt: skip
