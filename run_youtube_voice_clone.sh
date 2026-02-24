#!/usr/bin/env bash
# Run YouTube voice clone script from repo root.
# Usage: ./run_youtube_voice_clone.sh --excel path/to/videos.xlsx -o output_folder

set -e
cd "$(dirname "$0")"

if [ -d ".venv" ] && [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python run_youtube_voice_clone.py "$@"
else
  exec python3 run_youtube_voice_clone.py "$@"
fi
