#!/bin/sh
# Per-iteration setup for gnome-shell-test-tool.
#
# The test tool runs this script once per iteration, immediately before
# gnome-shell starts, with the iteration's private XDG directories exported.
# It seeds the database, installs the offline highlight.js assets, appends the
# benchmark settings to the GSettings keyfile and records the iteration index
# for the automation script.  Finally it execs the shell command it was given.
set -eu

: "${BENCH_OUTDIR:?BENCH_OUTDIR is required}"
: "${BENCH_DATASET:?BENCH_DATASET is required}"
: "${BENCH_HLJS_DIR:?BENCH_HLJS_DIR is required}"
: "${BENCH_CONTEXT_FILE:?BENCH_CONTEXT_FILE is required}"
: "${BENCH_SETTINGS_FILE:?BENCH_SETTINGS_FILE is required}"
: "${BENCH_SIZE:?BENCH_SIZE is required}"
: "${XDG_DATA_HOME:?XDG_DATA_HOME is required}"
: "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"
: "${XDG_CACHE_HOME:?XDG_CACHE_HOME is required}"

UUID="${BENCH_EXT_UUID:-copyous@boerdereinar.dev}"

mkdir -p "$BENCH_OUTDIR" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME/glib-2.0/settings" "$XDG_CACHE_HOME"

# ---------------------------------------------------------------------------
# Iteration counter
# ---------------------------------------------------------------------------
COUNTER="$BENCH_OUTDIR/.counter"
if [ -f "$COUNTER" ]; then
	INDEX=$(cat "$COUNTER")
else
	INDEX=-1
fi
INDEX=$((INDEX + 1))
printf '%s\n' "$INDEX" > "$COUNTER"

SIDECAR="$BENCH_OUTDIR/sidecar-$(printf '%03d' "$INDEX").json"

# The automation script reads this from GLib.get_user_config_dir().
jq --argjson index "$INDEX" \
	--arg sidecar "$SIDECAR" \
	'. + {iteration: $index, warmup: ($index == 0), sidecar_path: $sidecar}' \
	"$BENCH_CONTEXT_FILE" > "$XDG_CONFIG_HOME/bench-iter.json"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_DIR="$XDG_DATA_HOME/copyous-bench"
mkdir -p "$DB_DIR"
cp "$BENCH_DATASET" "$DB_DIR/clipboard.db"
export DEBUG_COPYOUS_DBPATH="$DB_DIR/clipboard.db"

# ---------------------------------------------------------------------------
# Offline highlight.js assets
# ---------------------------------------------------------------------------
EXT_DATA="$XDG_DATA_HOME/$UUID"
mkdir -p "$EXT_DATA/languages"
cp "$BENCH_HLJS_DIR/highlight.min.js" "$EXT_DATA/highlight.min.js"
for language_file in "$BENCH_HLJS_DIR"/languages/*.min.js; do
	[ -e "$language_file" ] || continue
	cp "$language_file" "$EXT_DATA/languages/"
done

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
KEYFILE="$XDG_CONFIG_HOME/glib-2.0/settings/keyfile"
touch "$KEYFILE"
{
	printf '\n'
	sed "s/@SIZE@/$BENCH_SIZE/g" "$BENCH_SETTINGS_FILE"
} >> "$KEYFILE"

exec "$@"
