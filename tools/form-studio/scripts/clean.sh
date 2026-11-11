#!/usr/bin/env bash
# Remove cache files from the Form Studio project folder. Your objects, styles, models and the app stay.
#
#   ./scripts/clean.sh                 Python caches (__pycache__, .pytest_cache, .mypy_cache, .ruff_cache)
#   ./scripts/clean.sh --build-tools   also web/node_modules (setup-mac.sh puts it back when it rebuilds)
#
# Written for the bash 3.2 that macOS ships (no mapfile, no empty arrays).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_TOOLS=0
for arg in "$@"; do
  case "$arg" in
    --build-tools) BUILD_TOOLS=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
# never look inside the Python environment, git, node_modules or the built interface
find "$ROOT" \( -name .venv -o -name .git -o -name node_modules -o -name web_dist \) -prune -o \
  -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -print > "$LIST"
if [ "$BUILD_TOOLS" = 1 ] && [ -d "$ROOT/web/node_modules" ]; then
  echo "$ROOT/web/node_modules" >> "$LIST"
fi

COUNT=0
FREED_KB=0
while IFS= read -r dir; do
  [ -d "$dir" ] || continue
  kb=$(du -sk "$dir" 2>/dev/null | awk '{print $1}')
  rm -rf "$dir"
  COUNT=$((COUNT + 1))
  FREED_KB=$((FREED_KB + ${kb:-0}))
done < "$LIST"

if [ "$COUNT" = 0 ]; then
  echo "Nothing to clean: no cache folders in $ROOT"
else
  echo "Removed $COUNT cache folder(s), freed $((FREED_KB / 1024)) MB."
fi
if [ "$BUILD_TOOLS" = 1 ]; then
  echo "web/node_modules comes back the next time you run ./scripts/setup-mac.sh."
fi
