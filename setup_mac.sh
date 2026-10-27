#!/usr/bin/env bash
# One-shot ATLAS setup for macOS (also works on Linux). `Atlas.command` runs this for you.
#
#   ./setup_mac.sh            # install into a venv and bake the map packs
#   ./setup_mac.sh --dev      # editable install plus the test tools, for working on ATLAS
#   ./setup_mac.sh --no-bake  # skip the map packs
#
# Needs Python 3.12+ (python.org installer or `brew install python@3.12`).
# The venv lives outside this folder (default ~/.atlas-venv, override with ATLAS_VENV). A
# normal install copies the game into it, so this folder can be moved or deleted afterwards.
# Worlds and map packs go to $LOCALAPPDATA/Atlas, which on a Mac defaults to
# ~/AppData/Local/Atlas -- the same layout the Windows build uses.
set -euo pipefail
cd "$(dirname "$0")"

DEV=0
BAKE=1
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --no-bake) BAKE=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for cand in python3.14 python3.13 python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      PY="$cand"; break
    fi
  done
fi
if [[ -z "$PY" ]]; then
  echo "ATLAS needs Python 3.12 or newer, and this Mac does not have it yet." >&2
  echo "Install it from python.org (the page is opening now), then run this again." >&2
  if [[ "$(uname)" == "Darwin" ]]; then open "https://www.python.org/downloads/macos/"; fi
  exit 1
fi

VENV="${ATLAS_VENV:-$HOME/.atlas-venv}"
echo "==> Python: $("$PY" --version)  venv: $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
echo "==> installing ATLAS (the first time downloads Qt, about 150 MB)"
if [[ $DEV == 1 ]]; then
  "$VENV/bin/python" -m pip install -q -e ".[dev,bake,api]"
else
  "$VENV/bin/python" -m pip install -q --force-reinstall --no-deps .
  "$VENV/bin/python" -m pip install -q ".[bake,api]"
fi

if [[ $BAKE == 1 ]]; then
  PACKS="$("$VENV/bin/python" -c 'from atlas.config.paths import packs_root; print(packs_root())')"
  # Downloads public-domain Natural Earth data once; admin1-10m is the big one (~25 MB pack).
  for src in admin0-50m marine-10m admin1-10m; do
    if [[ -f "$PACKS/$src.pack" ]]; then
      echo "==> map $src already baked"
    else
      echo "==> baking map $src"
      "$VENV/bin/atlas" map bake --source "$src"
    fi
  done
fi

echo "==> smoke test"
"$VENV/bin/atlas" newgame --force >/dev/null 2>&1 || "$VENV/bin/atlas" newgame >/dev/null
echo "engine OK"

cat <<EOF

Done. Double-click Atlas.command to play, or from a terminal:

  Claude API writes the world and talks, bots play the rest (add your key in Settings):
    $VENV/bin/atlas start --api
    $VENV/bin/atlas probe --provider api     # optional check, a fraction of a cent

  No AI at all, rule-based bots in every seat (free):
    $VENV/bin/atlas start --no-ai

  The original setup, with the claude / codex / agy CLIs on PATH:
    $VENV/bin/atlas doctor
    $VENV/bin/atlas start
EOF
