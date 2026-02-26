#!/usr/bin/env python3
"""
Run the voice clone script from the repo root.

Two input modes (use exactly one):
  --excel  : Excel file with YouTube URL, Start, Duration per row
  --folder : Folder containing audio files (.wav, .mp3, .flac, .ogg, .m4a)

Usage:
    python run_youtube_voice_clone.py --excel path/to/videos.xlsx -o output_folder
    python run_youtube_voice_clone.py --folder path/to/audio_files -o output_folder
    python run_youtube_voice_clone.py --excel videos.xlsx -o out --questions-file questions.txt

Or use the shell script: ./run_youtube_voice_clone.sh --excel ... -o ...
"""

import sys
from pathlib import Path

# Run from repo root so backend is importable
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Invoke the script's main (it will parse sys.argv)
from backend.scripts.youtube_voice_clone import main

if __name__ == "__main__":
    main()
