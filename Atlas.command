#!/bin/bash
# Double-click to play ATLAS on a Mac.
#
# The first time, this installs the game (a few minutes; needs internet for Qt and the map
# data). After that it opens straight away. It reinstalls by itself when this folder holds
# a newer build than the one installed.
#
# It starts in Claude API mode: add your Anthropic API key under Settings > AI providers.
# For no AI at all, use "Atlas (no AI).command" instead.
cd "$(dirname "$0")" || exit 1
MODE="${ATLAS_MODE:---api}"
VENV="${ATLAS_VENV:-$HOME/.atlas-venv}"
# What build this folder holds: a BUILD_ID file when the package has one, otherwise a
# fingerprint of the game's own files, so a GitHub download or a clone is recognised too.
BUILD="$(cat BUILD_ID 2>/dev/null ||
  find pyproject.toml src -type f ! -path '*__pycache__*' ! -path '*.egg-info*' -print0 | sort -z |
  xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-16)"
STAMP="$VENV/.atlas-build"

pause_on_error() {
  echo
  read -r -p "Something went wrong (see above). Press Return to close this window. "
  exit 1
}

if [[ ! -x "$VENV/bin/atlas" || "$(cat "$STAMP" 2>/dev/null)" != "$BUILD" ]]; then
  echo "Setting up ATLAS (first run, or a new build) -- this takes a few minutes."
  ./setup_mac.sh || pause_on_error
  echo "$BUILD" > "$STAMP"
fi

"$VENV/bin/atlas" start "$MODE" || pause_on_error
