#!/usr/bin/env bash
# Form Studio one-time setup for macOS (Apple Silicon or Intel).
#
#   ./scripts/setup-mac.sh                 everything: local AI (free) + the app
#   ./scripts/setup-mac.sh --cloud-only    skip the big local models (use fal.ai + offline engines)
#   ./scripts/setup-mac.sh --with-hunyuan  also install Hunyuan3D-2 mini (better local 3D)
#   ./scripts/setup-mac.sh --with-step     also install STEP export
#
# Safe to run again: it updates what's there. Nothing here costs money.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
FS_HOME="${FORMSTUDIO_HOME:-$HOME/FormStudio}"
VENV="$ROOT/.venv"

LOCAL_AI=1; HUNYUAN=0; STEP=0
for arg in "$@"; do
  case "$arg" in
    --cloud-only) LOCAL_AI=0 ;;
    --with-hunyuan) HUNYUAN=1 ;;
    --with-step) STEP=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

say() { printf "\n\033[1m▸ %s\033[0m\n" "$1"; }

say "Checking tools"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is needed (free). Install it from https://brew.sh, then run this script again."
  exit 1
fi
brew list python@3.11 >/dev/null 2>&1 || brew install python@3.11
command -v node >/dev/null 2>&1 || brew install node
command -v git >/dev/null 2>&1 || brew install git
PY="$(brew --prefix python@3.11)/bin/python3.11"

say "Python environment ($VENV)"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip wheel
EXTRAS="matting,heic"
[ "$LOCAL_AI" = 1 ] && EXTRAS="local-ai,heic"
[ "$STEP" = 1 ] && EXTRAS="$EXTRAS,step"
"$VENV/bin/pip" install --quiet -e "backend[$EXTRAS]"

if [ "$LOCAL_AI" = 1 ]; then
  say "TripoSR (local image → 3D)"
  mkdir -p "$FS_HOME/vendor"
  if [ -d "$FS_HOME/vendor/TripoSR/.git" ]; then
    git -C "$FS_HOME/vendor/TripoSR" pull --quiet --ff-only || true
  else
    git clone --quiet --depth 1 https://github.com/VAST-AI-Research/TripoSR.git "$FS_HOME/vendor/TripoSR"
  fi
fi
if [ "$HUNYUAN" = 1 ]; then
  say "Hunyuan3D-2 mini (local image → 3D, Tencent community licence)"
  "$VENV/bin/pip" install --quiet -e "backend[hunyuan]"
fi

say "Building the app's interface"
(cd web && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)

say "Creating Form Studio.app"
"$ROOT/scripts/build-app.sh"

say "Done"
cat <<MSG
Open Form Studio from Launchpad, Spotlight, or ~/Applications, and drag it to your Dock.

The first local run downloads the AI models (about 10 GB, once) into ~/.cache/huggingface.
Your objects are saved in $FS_HOME/jobs.
MSG
