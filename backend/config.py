"""
Configuration module for voicebox backend.

Handles data directory configuration for production bundling.
"""

import os
from pathlib import Path

# Default data directory (used in development)
_data_dir = Path("data")

def set_data_dir(path: str | Path):
    """
    Set the data directory path.

    Args:
        path: Path to the data directory
    """
    global _data_dir
    _data_dir = Path(path)
    _data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Data directory set to: {_data_dir.absolute()}")

def get_data_dir() -> Path:
    """
    Get the data directory path.

    Returns:
        Path to the data directory
    """
    return _data_dir

def get_db_path() -> Path:
    """Get database file path."""
    return _data_dir / "voicebox.db"

def get_profiles_dir() -> Path:
    """Get profiles directory path."""
    path = _data_dir / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path

def get_generations_dir() -> Path:
    """Get generations directory path."""
    path = _data_dir / "generations"
    path.mkdir(parents=True, exist_ok=True)
    return path

def get_cache_dir() -> Path:
    """Get cache directory path."""
    path = _data_dir / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path

def get_models_dir() -> Path:
    """Get models directory path."""
    path = _data_dir / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_tts_workers() -> int:
    """
    Get number of TTS worker processes for batch voice clone (1-12).
    Read from VOICEBOX_TTS_WORKERS (e.g. in .env); default 4; clamp to 1-12.
    Note: 8-12 workers is appropriate for GPUs with 40GB+ VRAM;
    1-4 workers is recommended for GPUs with 8-16GB VRAM.
    """
    try:
        n = int(os.environ.get("VOICEBOX_TTS_WORKERS", "4"))
    except (TypeError, ValueError):
        n = 4
    return max(1, min(12, n))
