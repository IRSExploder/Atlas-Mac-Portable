#!/bin/bash
# Double-click to play ATLAS with rule-based bots in every seat: free, offline, no API key.
cd "$(dirname "$0")" || exit 1
ATLAS_MODE=--no-ai exec ./Atlas.command
