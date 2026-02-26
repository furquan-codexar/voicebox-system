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

try:
    import colorama
    colorama.init(autoreset=True)
    _C = colorama.Fore
    _S = colorama.Style
except ImportError:
    class _DummyColors:
        __getattr__ = lambda self, _: ""
    _C = _DummyColors()
    _S = _DummyColors()


def _log_section(msg: str) -> None:
    """Section header (e.g. '=== Processing row 2 ===')."""
    print(f"{_S.BRIGHT}{_C.CYAN}{msg}{_S.RESET_ALL}")


def _log_ok(msg: str) -> None:
    """Success / completion message."""
    print(f"{_C.GREEN}{msg}{_S.RESET_ALL}")


def _log_detail(label: str, value: str | int | float) -> None:
    """Label: value (for URLs, paths, numbers)."""
    print(f"  {_C.CYAN}{label}:{_S.RESET_ALL} {_C.WHITE}{value}{_S.RESET_ALL}")


def _log_transcript(text: str) -> None:
    """Whisper transcription output (highlighted)."""
    print(f"  {_C.MAGENTA}[Whisper transcript]{_S.RESET_ALL} {_C.YELLOW}{text!r}{_S.RESET_ALL}")


def _log_question(idx: int, text: str, out_path: str) -> None:
    """Question index, text, and output file."""
    print(f"    {_C.BLUE}Q{idx}{_S.RESET_ALL} {_C.WHITE}{text!r}{_S.RESET_ALL} -> {_C.CYAN}{out_path}{_S.RESET_ALL}")


def _log_warn(msg: str) -> None:
    """Warning message."""
    print(f"{_C.YELLOW}{msg}{_S.RESET_ALL}")


def _log_error(msg: str) -> None:
    """Error message."""
    print(f"{_C.RED}{msg}{_S.RESET_ALL}")

# Default questions (used when --questions-file is not provided)
DEFAULT_QUESTIONS = [
    "Where is the red atrium?",
    "Show me the superior venacava",
    "Where is the blackflow?",
    "What is the black flow?",
    "What does the blad flow do?",
    "How does the blood flow through the flood flow?",
    "Tell me about the blood floor",
    "Show me the blodd",
    "Point to the blod",
    "Explain the blud",
    "Where is the bloed?",
    "What is the a orta?",
    "What does the ayorta do?",
    "How does the blood flow through the eorta?",
    "Tell me about the aorta",
    "Show me the ay orta",
    "Point to the a orter",
    "Explain the aortic valve",
    "Where is the a ortic valve?",
    "What is the ayortic valve?",
    "What does the aortic valv do?",
    "How does the blood flow through the a ortic valv?",
    "Tell me about the aortic value",
    "Show me the ortic valve",
    "Point to the atrioventricular av node",
    "Explain the atrial ventricular node",
    "Where is the atrio ventricular node?",
    "What is the av node?"
]

# Default TTS model size (overridden by --tts-model)
TTS_MODEL_SIZE = "1.7B"

# Voice clone reference audio must be 2–30 seconds (matches backend.utils.audio.validate_reference_audio)
REFERENCE_MAX_DURATION = 30.0

# Approximate VRAM (MiB) needed to load TTS so we can decide whether to keep Whisper in memory
TTS_NEEDED_MB = {"1.7B": 6000, "0.6B": 4000}


def _get_cuda_free_mb() -> int | None:
    """Return current free GPU memory in MiB, or None if CUDA unavailable or query failed."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                return int(out.stdout.strip().split()[0])
        except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
            pass
        return (
            torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated(0)
        ) // (1024 * 1024)
    except Exception:
        return None


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
    _log_detail("Downloading (yt-dlp)", f"row {row_index}")
    _log_detail("  URL", url)
    _log_detail("  output template", template)
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
            _log_error(f"yt-dlp failed for row {row_index}: {err or result.returncode}")
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _log_error(f"yt-dlp failed for row {row_index}: {e}")
        return None
    # yt-dlp may name the file with .wav or other; find any file matching our prefix
    candidates = list(temp_dir.glob(f"row_{row_index}_raw.*"))
    if not candidates:
        _log_error(f"yt-dlp did not produce expected file for row {row_index}")
        return None
    _log_ok(f"Downloaded audio -> {candidates[0]}")
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

    _log_section(f"=== Processing row {row_index} ===")
    _log_detail("YouTube URL", url)
    _log_detail("Start (s)", start_s)
    _log_detail("Duration (s)", duration_s)
    _log_detail("Language", language or "auto")

    tts_model = get_tts_model()
    whisper_model = get_whisper_model()

    # Download (yt-dlp uses %(ext)s template; we glob for the resulting file)
    raw_path = _download_youtube_audio(url, temp_dir, row_index, ffmpeg_location)
    if raw_path is None:
        return
    _log_detail("Raw audio file", str(raw_path))

    # Trim (cap duration at REFERENCE_MAX_DURATION so validation passes)
    effective_duration = min(duration_s, REFERENCE_MAX_DURATION)
    if duration_s > REFERENCE_MAX_DURATION:
        _log_warn(
            f"Duration {duration_s:.0f}s capped to {effective_duration:.0f}s for voice reference (max {int(REFERENCE_MAX_DURATION)}s)"
        )
    audio, sr = load_audio(str(raw_path), sample_rate=24000)
    start_sample = int(start_s * sr)
    end_sample = int((start_s + effective_duration) * sr)
    if start_sample >= len(audio):
        _log_error(f"Row {row_index}: start time beyond audio length (len={len(audio)/sr:.1f}s), skipping")
        return
    end_sample = min(end_sample, len(audio))
    trimmed = audio[start_sample:end_sample]
    segment_path = temp_dir / f"row_{row_index}_segment.wav"
    save_audio(trimmed, str(segment_path), sr)
    _log_ok("Trimmed segment saved")
    _log_detail("Segment path", str(segment_path))
    _log_detail("Segment length (samples)", len(trimmed))
    _log_detail("Segment duration (s)", f"{len(trimmed) / sr:.2f}")
    _log_detail("Sample rate", sr)

    # Normalize if clipping (same as profiles)
    import numpy as np
    if np.abs(trimmed).max() > 0.99:
        trimmed = normalize_audio(trimmed)
        save_audio(trimmed, str(segment_path), sr)
        _log_ok("Segment normalized (was clipping)")

    # Validate
    is_valid, err = validate_reference_audio(str(segment_path))
    if not is_valid:
        _log_error(f"Row {row_index}: invalid reference audio: {err}")
        return
    _log_ok("Reference audio validated")

    # Transcribe
    _log_detail("Transcribing with Whisper", f"model={stt_model_size}")
    await whisper_model.load_model_async(stt_model_size)
    transcript = await whisper_model.transcribe(str(segment_path), language=language or None)
    if not (transcript and transcript.strip()):
        _log_error(f"Row {row_index}: empty transcript, skipping")
        return
    _log_ok("Transcription done")
    _log_transcript(transcript.strip())

    # Unload Whisper only when GPU free memory is too low for TTS (avoids OOM; keeps Whisper loaded when VRAM allows)
    tts_needed_mb = TTS_NEEDED_MB.get(TTS_MODEL_SIZE, 6000)
    free_mb = _get_cuda_free_mb()
    if free_mb is not None and free_mb < tts_needed_mb:
        _log_detail("GPU free (MiB)", f"{free_mb} < {tts_needed_mb} -> unloading Whisper before TTS")
        whisper_model.unload_model()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    else:
        _log_detail("GPU free (MiB)", f"{free_mb or 'N/A'} -> keeping Whisper loaded")

    # Voice prompt
    _log_detail("Creating voice prompt", f"TTS model={TTS_MODEL_SIZE}")
    await tts_model.load_model_async(TTS_MODEL_SIZE)
    voice_prompt, _ = await tts_model.create_voice_prompt(
        str(segment_path),
        transcript.strip(),
        use_cache=False,
    )
    _log_ok("Voice prompt created")

    # Generate for each question
    _log_section(f"--- Generating {len(questions)} answer(s) for row {row_index} ---")
    for q_idx, text in enumerate(questions):
        out_path = output_folder / f"video_{row_index:02d}_q{q_idx:02d}.wav"
        _log_question(q_idx, text, str(out_path))
        audio_out, sample_rate = await tts_model.generate(
            text,
            voice_prompt,
            language=language,
        )
        save_audio(audio_out, str(out_path), sample_rate)
    _log_ok(f"Row {row_index}: generated {len(questions)} file(s)")


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
    parser.add_argument(
        "--tts-model",
        default="1.7B",
        dest="tts_model",
        choices=("0.6B", "1.7B"),
        help="TTS model size: 0.6B (less VRAM) or 1.7B (default)",
    )
    args = parser.parse_args()

    # Apply TTS model size for this run
    global TTS_MODEL_SIZE
    TTS_MODEL_SIZE = args.tts_model

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    _log_section("========== YouTube Voice Clone ==========")
    _log_detail("Excel file", str(args.excel))
    _log_detail("Output folder", str(args.output_folder))
    _log_detail("Language", args.language)
    _log_detail("STT model (Whisper)", args.stt_model)
    _log_detail("TTS model", TTS_MODEL_SIZE)

    # Log GPU status when using CUDA (script uses GPU by default when available)
    try:
        import torch
        if torch.cuda.is_available():
            free_mb = _get_cuda_free_mb()
            needed = TTS_NEEDED_MB.get(TTS_MODEL_SIZE, 6000)
            if free_mb is not None:
                if free_mb < needed:
                    _log_warn(
                        f"GPU has ~{free_mb} MiB free; {TTS_MODEL_SIZE} needs ~{needed} MiB. "
                        "You may get CUDA OOM. Free GPU memory or use --tts-model 0.6B / CPU (CUDA_VISIBLE_DEVICES=\"\")."
                    )
                else:
                    _log_ok(f"Using GPU (CUDA); ~{free_mb} MiB free.")
        else:
            _log_detail("Device", "CUDA not available; using CPU (slower).")
    except Exception:
        pass

    if not args.excel.exists():
        _log_error(f"Excel file not found: {args.excel}")
        sys.exit(1)

    rows = _read_excel_rows(args.excel)
    if not rows:
        _log_error("No valid video rows found in Excel")
        sys.exit(1)

    _log_ok(f"Loaded {len(rows)} video row(s) from Excel")
    for i, (u, start, dur) in enumerate(rows):
        url_short = u[:70] + "..." if len(u) > 70 else u
        _log_detail(f"  Row {i}", f"URL={url_short}  Start={start}s  Duration={dur}s")

    if args.questions_file is not None:
        if not args.questions_file.exists():
            logging.error("Questions file not found: %s", args.questions_file)
            sys.exit(1)
        questions = [
            line.strip() for line in args.questions_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not questions:
            _log_error(f"No non-empty questions in {args.questions_file}")
            sys.exit(1)
        _log_ok(f"Loaded {len(questions)} question(s) from {args.questions_file}")
    else:
        questions = DEFAULT_QUESTIONS
        _log_detail("Questions (default)", f"{len(questions)} built-in")

    for i, q in enumerate(questions):
        _log_detail(f"  Q{i}", q[:80] + ("..." if len(q) > 80 else ""))

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

    # Pre-load models once so the loop only does per-video work (download, trim, transcribe, voice prompt, generate)
    _log_section("---------- Pre-loading models ----------")
    get_tts_model, get_whisper_model, _, _, _, _ = _get_backend_imports()
    _log_detail("Pre-loading Whisper", args.stt_model)
    await get_whisper_model().load_model_async(args.stt_model)
    _log_ok("Whisper ready")
    tts_needed_mb = TTS_NEEDED_MB.get(TTS_MODEL_SIZE, 6000)
    whisper_approx_mb = 2000  # base ~1.5–2 GiB
    free_mb = _get_cuda_free_mb()
    if free_mb is not None and free_mb >= whisper_approx_mb + tts_needed_mb:
        _log_detail("Pre-loading TTS", TTS_MODEL_SIZE)
        await get_tts_model().load_model_async(TTS_MODEL_SIZE)
        _log_ok("TTS ready")
    else:
        _log_detail("TTS", "will load on first use (VRAM check)")

    _log_section("---------- Processing videos ----------")
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
                _log_error(f"Row {row_index} failed: {e}")
                logging.exception("Row %s failed: %s", row_index, e)

    _log_section("========== Done ==========")
    _log_ok(f"Output folder: {args.output_folder.resolve()}")


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print()
        _log_warn("Stopped by user (keyboard interrupt). Exiting without finishing remaining videos.")
        sys.exit(130)


if __name__ == "__main__":
    main()
