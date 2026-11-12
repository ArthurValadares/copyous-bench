#!/usr/bin/env bash
# Build the offline highlight.js assets used by the benchmark.
#
# The extension loads highlight.js and its language modules from the XDG data
# directory when the system install does not provide them.  We bundle the same
# modules with esbuild from the Copyous checkout's node_modules (the harness
# itself ships no JavaScript dependencies) so every benchmark iteration uses
# identical, network-free assets.
#
# Usage: build_hljs.sh <copyous-checkout> [output-dir]
set -euo pipefail

if [[ $# -lt 1 ]]; then
	echo "usage: $(basename "$0") <copyous-checkout> [output-dir]" >&2
	exit 2
fi

REPO_ROOT="$1"
OUT_DIR="${2:-/tmp/opencode/copyous-bench/assets/hljs}"
NODE_MODULES="$REPO_ROOT/node_modules"

if [[ ! -d "$NODE_MODULES" ]]; then
	echo "node_modules not found under $REPO_ROOT (run pnpm install there first)" >&2
	exit 1
fi

ESBUILD="$NODE_MODULES/.bin/esbuild"
if [[ ! -x "$ESBUILD" ]]; then
	ESBUILD="$(find "$NODE_MODULES/.pnpm" -path '*/esbuild/bin/esbuild' -type f -print -quit 2>/dev/null || true)"
fi
if [[ -z "${ESBUILD:-}" || ! -x "$ESBUILD" ]]; then
	echo "esbuild binary not found under $NODE_MODULES" >&2
	exit 1
fi

mkdir -p "$OUT_DIR/languages"

"$ESBUILD" "$NODE_MODULES/highlight.js/es/core.js" \
	--bundle --format=esm --minify --outfile="$OUT_DIR/highlight.min.js"

for language in typescript python rust json bash; do
	"$ESBUILD" "$NODE_MODULES/highlight.js/es/languages/$language.js" \
		--bundle --format=esm --minify --outfile="$OUT_DIR/languages/$language.min.js"
done

echo "highlight.js assets: $OUT_DIR"
