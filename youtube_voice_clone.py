"""
YouTube Voice Clone Batch Script.

Two input modes:
  - Excel: Reads YouTube URL, Start, Duration per row; downloads and trims audio.
  - Folder: Reads pre-existing audio files from a directory.

For both modes: auto-transcribes for the voice prompt, clones the voice with Qwen
TTS, and generates audio for a list of questions. Saves all outputs into a
user-specified folder.

Usage (from repo root):
    python -m backend.scripts.youtube_voice_clone --excel path/to/videos.xlsx -o my_cloned_voices
    python -m backend.scripts.youtube_voice_clone --folder path/to/audio_files -o my_cloned_voices

Excel mode requires FFmpeg (used by yt-dlp). Folder mode does not require FFmpeg.
Processing is sequential to avoid GPU/memory issues.
"""

from __future__ import annotations

import argparse
from typing import Callable
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

# Audio file extensions supported by librosa for folder mode
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


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


def _read_audio_folder(folder_path: Path) -> list[Path]:
    """List audio files in folder (non-recursive); return sorted paths."""
    if not folder_path.is_dir():
        raise ValueError(f"Not a directory: {folder_path}")
    files = [
        p for p in folder_path.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
    ]
    return sorted(files)


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


async def _process_reference_segment(
    segment_path: Path,
    index: int,
    output_prefix: str,
    output_folder: Path,
    language: str,
    questions: list[str],
    stt_model_size: str,
    progress_callback: Callable[[str, int, int, int, int], None] | None = None,
    audio_index: int = 0,
    total_audios: int = 1,
) -> None:
    """Validate, transcribe, create voice prompt, generate TTS for each question. Shared by video and folder modes."""
    get_tts_model, get_whisper_model, load_audio, save_audio, validate_reference_audio, normalize_audio = _get_backend_imports()

    is_valid, err = validate_reference_audio(str(segment_path))
    if not is_valid:
        logging.warning("%s %s: invalid reference audio: %s", output_prefix, index, err)
        return
    logging.info("%s %s: validated segment", output_prefix, index)

    tts_model = get_tts_model()
    whisper_model = get_whisper_model()

    await whisper_model.load_model_async(stt_model_size)
    transcript = await whisper_model.transcribe(str(segment_path), language=language or None)
    if not (transcript and transcript.strip()):
        logging.warning("%s %s: empty transcript, skipping", output_prefix, index)
        return
    logging.info("%s %s: transcribed", output_prefix, index)

    await tts_model.load_model_async(TTS_MODEL_SIZE)
    voice_prompt, _ = await tts_model.create_voice_prompt(
        str(segment_path),
        transcript.strip(),
        use_cache=False,
    )
    logging.info("%s %s: voice prompt created", output_prefix, index)

    audio_name = f"{output_prefix}_{index:02d}"
    total_questions = len(questions)
    for q_idx, text in enumerate(questions):
        if progress_callback:
            progress_callback(audio_name, q_idx + 1, total_questions, audio_index, total_audios)
        audio_out, sample_rate = await tts_model.generate(
            text,
            voice_prompt,
            language=language,
        )
        out_path = output_folder / f"{output_prefix}_{index:02d}_q{q_idx:02d}.wav"
        save_audio(audio_out, str(out_path), sample_rate, leading_silence_seconds=0.5)
    logging.info("%s %s: generated %s files", output_prefix, index, len(questions))


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
    progress_callback: Callable[[str, int, int, int, int], None] | None = None,
    audio_index: int = 0,
    total_audios: int = 1,
) -> None:
    """Download, trim, validate, transcribe, create voice prompt, generate for each question, save."""
    _, _, load_audio, save_audio, validate_reference_audio, normalize_audio = _get_backend_imports()

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
    # No trailing silence—reference must stay ≤30s for validation
    save_audio(trimmed, str(segment_path), sr, trailing_silence_seconds=0)

    # Normalize if clipping (same as profiles)
    import numpy as np
    if np.abs(trimmed).max() > 0.99:
        trimmed = normalize_audio(trimmed)
        save_audio(trimmed, str(segment_path), sr, trailing_silence_seconds=0)

    await _process_reference_segment(
        segment_path,
        row_index,
        "video",
        output_folder,
        language,
        questions,
        stt_model_size,
        progress_callback,
        audio_index,
        total_audios,
    )


async def _process_audio_file(
    file_index: int,
    audio_path: Path,
    output_folder: Path,
    language: str,
    temp_dir: Path,
    questions: list[str],
    stt_model_size: str,
    progress_callback: Callable[[str, int, int, int, int], None] | None = None,
    audio_index: int = 0,
    total_audios: int = 1,
) -> None:
    """Load audio file, trim if needed, validate, transcribe, create voice prompt, generate for each question."""
    import numpy as np

    _, _, load_audio, save_audio, validate_reference_audio, normalize_audio = _get_backend_imports()

    try:
        audio, sr = load_audio(str(audio_path), sample_rate=24000)
    except Exception as e:
        logging.warning("audio %s: failed to load %s: %s", file_index, audio_path.name, e)
        return

    duration_s = len(audio) / sr
    if duration_s < 2.0:
        logging.warning("audio %s: %s too short (%.1fs, minimum 2s), skipping", file_index, audio_path.name, duration_s)
        return

    effective_duration = min(duration_s, REFERENCE_MAX_DURATION)
    if duration_s > REFERENCE_MAX_DURATION:
        logging.info(
            "audio %s: %s duration %.0fs capped to %.0fs for voice reference",
            file_index, audio_path.name, duration_s, effective_duration,
        )
    end_sample = int(effective_duration * sr)
    trimmed = audio[:end_sample]

    if np.abs(trimmed).max() > 0.99:
        trimmed = normalize_audio(trimmed)

    segment_path = temp_dir / f"audio_{file_index}_segment.wav"
    save_audio(trimmed, str(segment_path), sr, trailing_silence_seconds=0)

    await _process_reference_segment(
        segment_path,
        file_index,
        "audio",
        output_folder,
        language,
        questions,
        stt_model_size,
        progress_callback,
        audio_index,
        total_audios,
    )


async def run_voice_clone(
    *,
    excel_path: Path | None = None,
    folder_path: Path | None = None,
    output_folder: Path,
    language: str = "en",
    stt_model: str = "base",
    questions: list[str] | None = None,
    questions_file: Path | None = None,
    ffmpeg_location: Path | None = None,
    progress_callback: Callable[[str, int, int, int, int], None] | None = None,
) -> Path:
    """
    Run voice cloning. Provide exactly one of excel_path or folder_path.

    Returns the output folder path on success.
    Raises ValueError on invalid input.
    """
    if (excel_path is None) == (folder_path is None):
        raise ValueError("Provide exactly one of excel_path or folder_path")

    if excel_path is not None:
        if not excel_path.exists():
            raise ValueError(f"Excel file not found: {excel_path}")
        rows = _read_excel_rows(excel_path)
        if not rows:
            raise ValueError("No valid video rows found in Excel")
        input_items = [("video", i, r) for i, r in enumerate(rows)]
    else:
        if not folder_path.exists() or not folder_path.is_dir():
            raise ValueError(f"Folder not found or not a directory: {folder_path}")
        try:
            audio_files = _read_audio_folder(folder_path)
        except ValueError as e:
            raise e
        if not audio_files:
            raise ValueError(f"No audio files found in folder (supported: {', '.join(_AUDIO_EXTENSIONS)})")
        logging.info("Found %s audio files in %s", len(audio_files), folder_path)
        input_items = [("audio", i, p) for i, p in enumerate(audio_files)]

    if questions is not None:
        if not questions:
            raise ValueError("Questions list is empty")
        logging.info("Using %s questions from parameter", len(questions))
    elif questions_file is not None:
        if not questions_file.exists():
            raise ValueError(f"Questions file not found: {questions_file}")
        questions = [
            line.strip() for line in questions_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not questions:
            raise ValueError(f"No non-empty questions in {questions_file}")
        logging.info("Loaded %s questions from %s", len(questions), questions_file)
    else:
        questions = DEFAULT_QUESTIONS

    output_folder.mkdir(parents=True, exist_ok=True)

    ffmpeg_loc = None
    if excel_path is not None:
        # FFmpeg (and ffprobe) are required by yt-dlp for audio extraction
        if ffmpeg_location is not None:
            if not ffmpeg_location.exists():
                raise ValueError(f"--ffmpeg-location path does not exist: {ffmpeg_location}")
            ffmpeg_loc = ffmpeg_location
        elif not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise ValueError(
                "FFmpeg is required for Excel/YouTube mode but not found. Install it and ensure ffmpeg and ffprobe are on your PATH.\n"
                "  macOS (Homebrew): brew install ffmpeg\n"
                "  Ubuntu: sudo apt install ffmpeg"
            )

    total_audios = len(input_items)
    with tempfile.TemporaryDirectory(prefix="voice_clone_") as temp_dir:
        temp_path = Path(temp_dir)
        for audio_idx, (mode, index, item) in enumerate(input_items):
            try:
                if mode == "video":
                    url, start_s, duration_s = item
                    await _process_video(
                        index,
                        url,
                        start_s,
                        duration_s,
                        output_folder,
                        language,
                        temp_path,
                        questions,
                        stt_model,
                        ffmpeg_loc,
                        progress_callback,
                        audio_idx,
                        total_audios,
                    )
                else:
                    await _process_audio_file(
                        index,
                        item,
                        output_folder,
                        language,
                        temp_path,
                        questions,
                        stt_model,
                        progress_callback,
                        audio_idx,
                        total_audios,
                    )
            except Exception as e:
                logging.exception("%s %s failed: %s", mode, index, e)

    logging.info("Done. Output folder: %s", output_folder.resolve())
    return output_folder


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clone voices from YouTube videos or local audio files and generate answers to fixed questions."
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--excel",
        type=Path,
        help="Path to Excel file with columns: YouTube URL, Start, Duration",
    )
    input_group.add_argument(
        "--folder",
        type=Path,
        help="Path to folder containing audio files (.wav, .mp3, .flac, .ogg, .m4a)",
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

    try:
        asyncio.run(run_voice_clone(
            excel_path=args.excel,
            folder_path=args.folder,
            output_folder=args.output_folder,
            language=args.language,
            stt_model=args.stt_model,
            questions_file=args.questions_file,
            ffmpeg_location=args.ffmpeg_location,
        ))
    except ValueError as e:
        logging.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
