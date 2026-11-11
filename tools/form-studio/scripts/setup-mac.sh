#!/usr/bin/env bash
# Form Studio one-time setup for macOS (Apple Silicon or Intel).
#
#   ./scripts/setup-mac.sh                 everything: local AI (free) + the app
#   ./scripts/setup-mac.sh --cloud-only    skip the big local models (use fal.ai + offline engines)
#   ./scripts/setup-mac.sh --with-hunyuan  also install Hunyuan3D-2 mini (better local 3D)
#   ./scripts/setup-mac.sh --with-step     also install STEP export
#   ./scripts/setup-mac.sh --reinstall-ai  reinstall the AI libraries and TripoSR's code from scratch
#
# Safe to run again: it updates what's there. Nothing here costs money.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
FS_HOME="${FORMSTUDIO_HOME:-$HOME/FormStudio}"
VENV="$ROOT/.venv"

LOCAL_AI=1; HUNYUAN=0; STEP=0; REINSTALL_AI=0
for arg in "$@"; do
  case "$arg" in
    --cloud-only) LOCAL_AI=0 ;;
    --with-hunyuan) HUNYUAN=1 ;;
    --with-step) STEP=1 ;;
    --reinstall-ai) REINSTALL_AI=1 ;;
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
if ! xcode-select -p >/dev/null 2>&1 || ! command -v swiftc >/dev/null 2>&1; then
  echo "Apple's Command Line Tools are needed to build the app window (free)."
  echo "Run:  xcode-select --install   then run this script again."
  exit 1
fi
PY="$(brew --prefix python@3.11)/bin/python3.11"

say "Tidying up files from older versions"
# An earlier version kept its code in tools/form-studio/formstudio/. Folders like that, which git
# no longer tracks, would be loaded instead of backend/formstudio. Move them aside (never delete).
LEFTOVERS="$FS_HOME/old-version-leftovers/$(date +%Y%m%d-%H%M%S)"
MOVED=0
for item in formstudio tests pyproject.toml setup.py setup.cfg build dist ./*.egg-info; do
  item="${item#./}"
  [ -e "$item" ] || continue
  if [ -z "$(git ls-files -- "$item" 2>/dev/null)" ]; then
    mkdir -p "$LEFTOVERS"
    mv "$item" "$LEFTOVERS/"
    echo "  moved $item → $LEFTOVERS/"
    MOVED=1
  fi
done
if [ "$MOVED" = 0 ]; then echo "  nothing to tidy"; fi

say "Python environment ($VENV)"
# an environment made by an older Python (or moved folder) is rebuilt
if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -c "import sys" >/dev/null 2>&1; then rm -rf "$VENV"; fi
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip wheel
EXTRAS="matting,heic"
[ "$LOCAL_AI" = 1 ] && EXTRAS="local-ai,heic"
[ "$STEP" = 1 ] && EXTRAS="$EXTRAS,step"
"$VENV/bin/pip" install --quiet -e "backend[$EXTRAS]"

if [ "$REINSTALL_AI" = 1 ]; then
  say "Reinstalling the AI libraries from scratch"
  osascript -e 'tell application id "in.alwi.formstudio" to quit' >/dev/null 2>&1 || true
  pkill -f "formstudio.desktop" >/dev/null 2>&1 || true
  "$VENV/bin/pip" install --force-reinstall --no-cache-dir --no-deps \
    torch torchvision diffusers transformers accelerate safetensors huggingface-hub peft rembg onnxruntime
  "$VENV/bin/pip" install --quiet -e "backend[$EXTRAS]"   # put back anything those need
  rm -rf "$FS_HOME/vendor/TripoSR"
fi

say "Removing old installs from the Python environment"
"$VENV/bin/python" -I "$ROOT/scripts/cleanup_old_install.py" "$ROOT"
if ! (cd "$ROOT/backend" && "$VENV/bin/python" -P -c "import formstudio, sys; sys.exit(0 if formstudio.__file__ and formstudio.__file__.endswith('backend/formstudio/__init__.py') else 1)") 2>/dev/null; then
  echo "!! Python still can't find Form Studio's code in backend/formstudio."
  echo "   Delete the environment and run this script again:  rm -rf \"$VENV\" && ./scripts/setup-mac.sh"
  exit 1
fi
echo "  import check passed"
if [ "$LOCAL_AI" = 1 ] && ! "$VENV/bin/python" -c "import peft" >/dev/null 2>&1; then
  echo "  note: peft didn't install, so Quick restyle quality won't be available (Best still works)"
fi

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

Local engines need AI models (SDXL 12 GB, TripoSR 1.9 GB, Quick quality add-on 0.4 GB), downloaded
once. In the app, open Settings → Local AI to download them with progress and time left.
To see how fast it runs on this Mac, use Settings → Speed test.
Your objects are saved in $FS_HOME/jobs.
MSG
