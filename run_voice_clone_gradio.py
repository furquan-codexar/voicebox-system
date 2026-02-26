#!/usr/bin/env python3
"""
Launch the Gradio voice clone UI from the repo root.

Usage:
    python run_voice_clone_gradio.py

Opens a web interface at http://localhost:7860 for cloning voices from
Excel/YouTube or local audio files.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.voice_clone_gradio import main

if __name__ == "__main__":
    main()
