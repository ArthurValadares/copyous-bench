#!/usr/bin/env bash
# Build the offline highlight.js assets used by the benchmark.
#
# The extension loads highlight.js and its language modules from the XDG data
# directory when the system install does not provide them.  We bundle the same
# modules with esbuild (already present in node_modules) so every benchmark
# iteration uses identical, network-free assets.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${1:-/tmp/opencode/copyous-bench/assets/hljs}"

ESBUILD="$REPO_ROOT/node_modules/.pnpm/esbuild@0.28.2/node_modules/esbuild/bin/esbuild"
if [[ ! -x "$ESBUILD" ]]; then
	ESBUILD="$(find "$REPO_ROOT/node_modules/.pnpm" -path '*/esbuild/bin/esbuild' -type f -print -quit)"
fi
if [[ -z "${ESBUILD:-}" || ! -x "$ESBUILD" ]]; then
	echo "esbuild binary not found under node_modules" >&2
	exit 1
fi

mkdir -p "$OUT_DIR/languages"

"$ESBUILD" "$REPO_ROOT/node_modules/highlight.js/es/core.js" \
	--bundle --format=esm --minify --outfile="$OUT_DIR/highlight.min.js"

for language in typescript python rust json bash; do
	"$ESBUILD" "$REPO_ROOT/node_modules/highlight.js/es/languages/$language.js" \
		--bundle --format=esm --minify --outfile="$OUT_DIR/languages/$language.min.js"
done

echo "highlight.js assets: $OUT_DIR"
