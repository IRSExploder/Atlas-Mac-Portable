#!/usr/bin/env bash
# One-shot ATLAS setup for macOS (also works on Linux).
#
#   ./setup_mac.sh            # venv + install + bake all three map packs
#   ./setup_mac.sh --no-bake  # venv + install only
#
# Needs Python 3.12+ (python.org installer or `brew install python@3.12`).
# The venv lives outside the project tree (default ~/.atlas-venv, override with ATLAS_VENV).
# Worlds and map packs go to $LOCALAPPDATA/Atlas, which on a Mac defaults to
# ~/AppData/Local/Atlas -- the same layout the Windows build uses.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for cand in python3.13 python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      PY="$cand"; break
    fi
  done
fi
if [[ -z "$PY" ]]; then
  echo "Python 3.12+ not found. Install it (brew install python@3.12) or set PYTHON=/path/to/python3.12" >&2
  exit 1
fi

VENV="${ATLAS_VENV:-$HOME/.atlas-venv}"
echo "==> Python: $("$PY" --version)  venv: $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -e ".[dev,bake]"

if [[ "${1:-}" != "--no-bake" ]]; then
  # Downloads public-domain Natural Earth data once; admin1-10m is the big one (~25 MB pack).
  for src in admin0-50m marine-10m admin1-10m; do
    echo "==> baking $src"
    "$VENV/bin/atlas" map bake --source "$src"
  done
fi

echo "==> smoke test"
"$VENV/bin/atlas" newgame >/dev/null && echo "engine OK"

cat <<EOF

Done. Start a game with no real model calls:
  $VENV/bin/atlas new --mock

With real models (needs the claude / codex / agy CLIs on PATH):
  $VENV/bin/atlas doctor
  $VENV/bin/atlas new
EOF
