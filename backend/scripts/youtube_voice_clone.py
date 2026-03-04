"""
YouTube Voice Clone Batch Script.

Reads an Excel sheet (YouTube URL, Start, Duration per row), downloads and trims
audio from each video, auto-transcribes for the voice prompt, clones the voice
with Qwen TTS, and generates audio for a list of questions. Saves all outputs
into a user-specified folder.

Usage (from repo root):
    python -m backend.scripts.youtube_voice_clone --excel path/to/videos.xlsx -o my_cloned_voices
    python -m backend.scripts.youtube_voice_clone --excel videos.xlsx -o out --questions-file questions.txt

Requires FFmpeg (used by yt-dlp for audio extraction). Processing is sequential
to avoid GPU/memory issues; large Excel files will take proportionally longer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add repo root to path when run as script (optional; prefer: python -m backend.scripts.youtube_voice_clone)
if __name__ == "__main__" and __package__ is None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

import openpyxl

# Default questions (used when --questions-file is not provided)
DEFAULT_QUESTIONS = [
    "What is your name?",
    "Where are you from?",
    "What do you do for a living?",
]

# Default TTS model size (CLI override available for STT)
TTS_MODEL_SIZE = "1.7B"

# Voice clone reference audio must be 2–30 seconds (matches backend.utils.audio.validate_reference_audio)
REFERENCE_MAX_DURATION = 30.0


def _parse_time_to_seconds(value) -> float | None:
    """Parse Start or Duration from Excel: number (seconds) or M:SS / MM:SS string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # MM:SS or M:SS
    match = re.match(r"^(\d+):(\d{2})$", s)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def _find_column_indices(header_row) -> dict[str, int] | None:
    """Return dict of normalized header name -> 0-based column index. Expects YouTube URL, Start, Duration."""
    indices = {}
    normalized = {
        "youtube url": "url",
        "url": "url",
        "youtube_url": "url",
        "start": "start",
        "duration": "duration",
    }
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        val = getattr(cell, "value", cell) or ""
        key = str(val).strip().lower().replace(" ", "_")
        if key in normalized:
            indices[normalized[key]] = idx
        elif key == "youtubeurl":
            indices["url"] = idx
    if "url" in indices and "start" in indices and "duration" in indices:
        return indices
    return None


def _read_excel_rows(excel_path: Path) -> list[tuple[str, float, float]]:
    """Read Excel file; return list of (youtube_url, start_seconds, duration_seconds)."""
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        raise ValueError("Excel sheet is empty")
    header = all_rows[0]
    col = _find_column_indices(header)
    if not col:
        raise ValueError(
            "Excel must have columns: YouTube URL, Start, Duration (header names case-insensitive)"
        )
    url_col = col["url"]
    start_col = col["start"]
    dur_col = col["duration"]
    result = []
    for row in all_rows[1:]:
        url_val = row[url_col] if url_col < len(row) else None
        url = (str(url_val).strip() if url_val else "") or None
        if not url or ("youtube" not in url.lower() and "youtu.be" not in url.lower()):
            continue
        start_s = _parse_time_to_seconds(row[start_col] if start_col < len(row) else None)
        dur_s = _parse_time_to_seconds(row[dur_col] if dur_col < len(row) else None)
        if start_s is None or dur_s is None or dur_s <= 0:
            logging.warning("Skipping row: invalid start or duration for URL %s", url[:50])
            continue
        result.append((url, start_s, dur_s))
    return result


def _yt_dlp_cmd() -> list[str]:
    """Return command to run yt-dlp (binary if on PATH, else python -m yt_dlp)."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def _download_youtube_audio(
    url: str,
    temp_dir: Path,
    row_index: int,
    ffmpeg_location: str | Path | None = None,
) -> Path | None:
    """
    Download audio from YouTube as WAV using yt-dlp.
    Uses -o template with %(ext)s so yt-dlp controls extension; then glob for the file.
    Returns path to downloaded WAV file, or None on failure.
    """
    template = str(temp_dir / f"row_{row_index}_raw.%(ext)s")
    cmd = _yt_dlp_cmd() + [
        "-x",
        "--audio-format", "wav",
        "--no-playlist",
        "-o", template,
        "--no-warnings",
    ]
    if ffmpeg_location is not None:
        cmd.extend(["--ffmpeg-location", str(ffmpeg_location)])
    cmd.append(url)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            logging.error("yt-dlp failed for %s: %s", url[:50], err or result.returncode)
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logging.error("yt-dlp failed for %s: %s", url[:50], e)
        return None
    # yt-dlp may name the file with .wav or other; find any file matching our prefix
    candidates = list(temp_dir.glob(f"row_{row_index}_raw.*"))
    if not candidates:
        logging.error("yt-dlp did not produce expected file for row %s", row_index)
        return None
    return candidates[0]


def _get_backend_imports():
    """Import backend modules. Relative when run as package; absolute when run as script (repo root on path)."""
    try:
        if __package__:
            from ..tts import get_tts_model
            from ..transcribe import get_whisper_model
            from ..utils.audio import (
                load_audio,
                save_audio,
                validate_reference_audio,
                normalize_audio,
            )
        else:
            from backend.tts import get_tts_model
            from backend.transcribe import get_whisper_model
            from backend.utils.audio import (
                load_audio,
                save_audio,
                validate_reference_audio,
                normalize_audio,
            )
    except ImportError as e:
        raise ImportError(
            "Voicebox backend not found. Run from repo root: "
            "python -m backend.scripts.youtube_voice_clone --excel ... -o ..."
        ) from e
    return get_tts_model, get_whisper_model, load_audio, save_audio, validate_reference_audio, normalize_audio


async def _process_video(
    row_index: int,
    url: str,
    start_s: float,
    duration_s: float,
    output_folder: Path,
    language: str,
    temp_dir: Path,
    questions: list[str],
    stt_model_size: str,
    ffmpeg_location: str | Path | None = None,
) -> None:
    """Download, trim, validate, transcribe, create voice prompt, generate for each question, save."""
    get_tts_model, get_whisper_model, load_audio, save_audio, validate_reference_audio, normalize_audio = _get_backend_imports()

    tts_model = get_tts_model()
    whisper_model = get_whisper_model()

    # Download (yt-dlp uses %(ext)s template; we glob for the resulting file)
    raw_path = _download_youtube_audio(url, temp_dir, row_index, ffmpeg_location)
    if raw_path is None:
        return
    logging.info("Row %s: downloaded", row_index)

    # Trim (cap duration at REFERENCE_MAX_DURATION so validation passes)
    effective_duration = min(duration_s, REFERENCE_MAX_DURATION)
    if duration_s > REFERENCE_MAX_DURATION:
        logging.info(
            "Row %s: duration %.0fs capped to %.0fs for voice reference (max %s seconds)",
            row_index, duration_s, effective_duration, int(REFERENCE_MAX_DURATION),
        )
    audio, sr = load_audio(str(raw_path), sample_rate=24000)
    start_sample = int(start_s * sr)
    end_sample = int((start_s + effective_duration) * sr)
    if start_sample >= len(audio):
        logging.warning("Row %s: start time beyond audio length, skipping", row_index)
        return
    end_sample = min(end_sample, len(audio))
    trimmed = audio[start_sample:end_sample]
    segment_path = temp_dir / f"row_{row_index}_segment.wav"
    save_audio(trimmed, str(segment_path), sr)

    # Normalize if clipping (same as profiles)
    import numpy as np
    if np.abs(trimmed).max() > 0.99:
        trimmed = normalize_audio(trimmed)
        save_audio(trimmed, str(segment_path), sr)

    # Validate
    is_valid, err = validate_reference_audio(str(segment_path))
    if not is_valid:
        logging.warning("Row %s: invalid reference audio: %s", row_index, err)
        return
    logging.info("Row %s: validated segment", row_index)

    # Transcribe
    await whisper_model.load_model_async(stt_model_size)
    transcript = await whisper_model.transcribe(str(segment_path), language=language or None)
    if not (transcript and transcript.strip()):
        logging.warning("Row %s: empty transcript, skipping", row_index)
        return
    logging.info("Row %s: transcribed", row_index)

    # Voice prompt
    await tts_model.load_model_async(TTS_MODEL_SIZE)
    voice_prompt, _ = await tts_model.create_voice_prompt(
        str(segment_path),
        transcript.strip(),
        use_cache=False,
    )
    logging.info("Row %s: voice prompt created", row_index)

    # Generate for each question
    for q_idx, text in enumerate(questions):
        audio_out, sample_rate = await tts_model.generate(
            text,
            voice_prompt,
            language=language,
        )
        out_path = output_folder / f"video_{row_index:02d}_q{q_idx:02d}.wav"
        save_audio(audio_out, str(out_path), sample_rate, leading_silence_seconds=0.5)
    logging.info("Row %s: generated %s files", row_index, len(questions))


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Clone voices from YouTube videos and generate answers to fixed questions."
    )
    parser.add_argument(
        "--excel",
        required=True,
        type=Path,
        help="Path to Excel file with columns: YouTube URL, Start, Duration",
    )
    parser.add_argument(
        "-o",
        "--output-folder",
        required=True,
        type=Path,
        dest="output_folder",
        help="Output folder for generated WAV files",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code for TTS/STT (default: en)",
    )
    parser.add_argument(
        "--stt-model",
        default="base",
        dest="stt_model",
        help="Whisper STT model size: tiny, base, small, medium, large (default: base)",
    )
    parser.add_argument(
        "--questions-file",
        type=Path,
        default=None,
        dest="questions_file",
        help="Text file with one question per line (default: use built-in questions)",
    )
    parser.add_argument(
        "--ffmpeg-location",
        type=Path,
        default=None,
        dest="ffmpeg_location",
        help="Path to ffmpeg executable or directory containing ffmpeg and ffprobe (if not on PATH)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.excel.exists():
        logging.error("Excel file not found: %s", args.excel)
        sys.exit(1)

    rows = _read_excel_rows(args.excel)
    if not rows:
        logging.error("No valid video rows found in Excel")
        sys.exit(1)

    if args.questions_file is not None:
        if not args.questions_file.exists():
            logging.error("Questions file not found: %s", args.questions_file)
            sys.exit(1)
        questions = [
            line.strip() for line in args.questions_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not questions:
            logging.error("No non-empty questions in %s", args.questions_file)
            sys.exit(1)
        logging.info("Loaded %s questions from %s", len(questions), args.questions_file)
    else:
        questions = DEFAULT_QUESTIONS

    args.output_folder.mkdir(parents=True, exist_ok=True)

    # FFmpeg (and ffprobe) are required by yt-dlp for audio extraction
    if args.ffmpeg_location is not None:
        if not args.ffmpeg_location.exists():
            logging.error("--ffmpeg-location path does not exist: %s", args.ffmpeg_location)
            sys.exit(1)
        ffmpeg_location = args.ffmpeg_location
    elif not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        logging.error(
            "FFmpeg is required but not found. Install it and ensure ffmpeg and ffprobe are on your PATH,\n"
            "or pass a path via --ffmpeg-location (path to ffmpeg binary or directory containing ffmpeg and ffprobe).\n"
            "  macOS (Homebrew):  brew install ffmpeg\n"
            "  macOS (no Homebrew): download static build from https://evermeet.cx/ffmpeg/ then use --ffmpeg-location\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )
        sys.exit(1)
    else:
        ffmpeg_location = None

    with tempfile.TemporaryDirectory(prefix="youtube_voice_clone_") as temp_dir:
        temp_path = Path(temp_dir)
        for row_index, (url, start_s, duration_s) in enumerate(rows):
            try:
                await _process_video(
                    row_index,
                    url,
                    start_s,
                    duration_s,
                    args.output_folder,
                    args.language,
                    temp_path,
                    questions,
                    args.stt_model,
                    ffmpeg_location,
                )
            except Exception as e:
                logging.exception("Row %s failed: %s", row_index, e)

    logging.info("Done. Output folder: %s", args.output_folder.resolve())


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
