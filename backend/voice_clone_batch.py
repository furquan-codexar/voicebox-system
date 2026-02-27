"""
Bulk voice clone module for the voicebox API.

Supports two modes:
  - YouTube: Single URL + start/end timestamps
  - Upload: Multiple audio files

For each source: validate, transcribe (Whisper), create voice prompt, generate TTS
for each text line. Outputs are saved with 2 seconds trailing silence and returned
as a ZIP archive.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np

from .tts import get_tts_model
from .transcribe import get_whisper_model
from .utils.audio import (
    load_audio,
    save_audio,
    validate_reference_audio,
    normalize_audio,
)

TTS_MODEL_SIZE = "1.7B"
REFERENCE_MAX_DURATION = 30.0
TRAILING_SILENCE_SECONDS = 2.0
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}

logger = logging.getLogger(__name__)


def _parse_time_to_seconds(value) -> float | None:
    """Parse time: number (seconds) or M:SS / MM:SS string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    match = re.match(r"^(\d+):(\d{2})$", s)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def _yt_dlp_cmd() -> list[str]:
    """Return command to run yt-dlp."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def _download_youtube_audio(
    url: str,
    temp_dir: Path,
    prefix: str,
    ffmpeg_location: Path | None = None,
) -> Path | None:
    """
    Download audio from YouTube as WAV using yt-dlp.
    Returns path to downloaded file, or None on failure.
    """
    template = str(temp_dir / f"{prefix}_raw.%(ext)s")
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
            logger.error("yt-dlp failed for %s: %s", url[:50], err or result.returncode)
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("yt-dlp failed for %s: %s", url[:50], e)
        return None
    candidates = list(temp_dir.glob(f"{prefix}_raw.*"))
    if not candidates:
        logger.error("yt-dlp did not produce expected file for %s", prefix)
        return None
    logger.info("[Batch] YouTube download complete: %s", candidates[0].name)
    return candidates[0]


async def _process_source_and_generate(
    segment_path: Path,
    source_index: int,
    output_prefix: str,
    output_folder: Path,
    text_lines: list[str],
    language: str,
    stt_model: str,
    progress_callback: Callable[[int, int, int, int], None] | None,
    total_sources: int,
) -> list[Path]:
    """
    Validate, transcribe, create voice prompt, generate for each text line.
    Returns list of output file paths. Adds 2s trailing silence to each output.
    """
    is_valid, err = validate_reference_audio(str(segment_path))
    if not is_valid:
        raise ValueError(f"Invalid reference audio for source {source_index}: {err}")

    logger.info("[Batch] Source %s: validated segment (2-30s, no clipping)", source_index)

    tts_model = get_tts_model()
    whisper_model = get_whisper_model()

    logger.info("[Batch] Source %s: loading Whisper model '%s' for transcription", source_index, stt_model)
    await whisper_model.load_model_async(stt_model)
    transcript = await whisper_model.transcribe(str(segment_path), language=language or None)
    if not (transcript and transcript.strip()):
        raise ValueError(f"Empty transcript for source {source_index}")

    logger.info("[Batch] Source %s: transcribed (length=%d chars)", source_index, len(transcript.strip()))

    logger.info("[Batch] Source %s: loading TTS model '%s', creating voice prompt", source_index, TTS_MODEL_SIZE)
    await tts_model.load_model_async(TTS_MODEL_SIZE)
    voice_prompt, _ = await tts_model.create_voice_prompt(
        str(segment_path),
        transcript.strip(),
        use_cache=False,
    )

    logger.info("[Batch] Source %s: voice prompt created, generating %s output lines", source_index, len(text_lines))

    output_paths = []
    for line_idx, text in enumerate(text_lines):
        if progress_callback:
            progress_callback(source_index, line_idx + 1, total_sources, len(text_lines))
        logger.info("[Batch] Source %s: generating line %s/%s: %s", source_index, line_idx + 1, len(text_lines), text[:50] + "..." if len(text) > 50 else text)
        audio_out, sample_rate = await tts_model.generate(
            text,
            voice_prompt,
            language=language,
        )
        out_path = output_folder / f"{output_prefix}_{source_index:02d}_line_{line_idx:02d}.wav"
        save_audio(
            audio_out,
            str(out_path),
            sample_rate,
            trailing_silence_seconds=TRAILING_SILENCE_SECONDS,
        )
        output_paths.append(out_path)

    logger.info("[Batch] Source %s: generated %s files", source_index, len(output_paths))
    return output_paths


async def run_batch_voice_clone(
    *,
    mode: str,
    youtube_url: str | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    audio_paths: list[Path] | None = None,
    text_lines: list[str],
    language: str = "en",
    stt_model: str = "base",
    ffmpeg_location: Path | None = None,
    progress_callback: Callable[[int, int, int, int], None] | None = None,
) -> tuple[bytes, list[str]]:
    """
    Run batch voice clone. Returns (zip_bytes, list of output filenames).

    mode: "youtube" | "upload"
    For YouTube: youtube_url, start_seconds, end_seconds required.
    For upload: audio_paths (list of temp file paths) required.
    """
    if mode == "youtube":
        if not youtube_url or start_seconds is None or end_seconds is None:
            raise ValueError("YouTube mode requires youtube_url, start_seconds, end_seconds")
        url_lower = youtube_url.strip().lower()
        if "youtube" not in url_lower and "youtu.be" not in url_lower:
            raise ValueError("Invalid YouTube URL")
        duration_s = end_seconds - start_seconds
        if duration_s < 2.0 or duration_s > REFERENCE_MAX_DURATION:
            raise ValueError(
                f"Duration must be 2–{int(REFERENCE_MAX_DURATION)} seconds, got {duration_s:.1f}"
            )
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            if ffmpeg_location is None or not ffmpeg_location.exists():
                raise ValueError(
                    "FFmpeg is required for YouTube mode. Install it and ensure ffmpeg and ffprobe are on PATH."
                )
        sources: list[tuple[int, str, Path | None]] = [(0, "youtube", None)]
    elif mode == "upload":
        if not audio_paths or len(audio_paths) == 0:
            raise ValueError("Upload mode requires at least one audio file")
        sources = [(i, "upload", p) for i, p in enumerate(audio_paths)]
    else:
        raise ValueError("mode must be 'youtube' or 'upload'")

    if not text_lines or all(not line.strip() for line in text_lines):
        raise ValueError("At least one non-empty text line is required")

    text_lines = [line.strip() for line in text_lines if line.strip()]
    total_sources = len(sources)
    logger.info("[Batch] run_batch_voice_clone started: mode=%s, sources=%s, text_lines=%s", mode, total_sources, len(text_lines))

    def _progress(s_idx: int, l_idx: int, tot_s: int, tot_l: int) -> None:
        if progress_callback:
            progress_callback(s_idx, l_idx, tot_s, tot_l)

    all_output_paths: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="voice_clone_batch_") as temp_dir:
        temp_path = Path(temp_dir)
        output_folder = temp_path / "output"
        output_folder.mkdir()

        for source_index, source_type, item in sources:
            try:
                logger.info("[Batch] Processing source %s/%s (type=%s)", source_index + 1, total_sources, source_type)
                if source_type == "youtube":
                    logger.info("[Batch] Downloading YouTube audio from %s (start=%.1fs, end=%.1fs)", youtube_url[:60], start_seconds, end_seconds)
                    raw_path = _download_youtube_audio(
                        youtube_url,
                        temp_path,
                        "youtube_0",
                        ffmpeg_location,
                    )
                    if raw_path is None:
                        raise ValueError("Failed to download YouTube audio")

                    effective_duration = min(duration_s, REFERENCE_MAX_DURATION)
                    audio, sr = load_audio(str(raw_path), sample_rate=24000)
                    start_sample = int(start_seconds * sr)
                    end_sample = int((start_seconds + effective_duration) * sr)
                    if start_sample >= len(audio):
                        raise ValueError("Start time is beyond audio length")
                    end_sample = min(end_sample, len(audio))
                    trimmed = audio[start_sample:end_sample]
                    if np.abs(trimmed).max() > 0.99:
                        trimmed = normalize_audio(trimmed)
                    segment_path = temp_path / "youtube_0_segment.wav"
                    save_audio(trimmed, str(segment_path), sr, trailing_silence_seconds=0)
                    logger.info("[Batch] YouTube segment trimmed and saved (%.1fs)", len(trimmed) / sr)
                    output_prefix = "source"
                else:
                    audio_path = item
                    if not audio_path or not audio_path.exists():
                        logger.warning("[Batch] Skipping missing audio file at index %s", source_index)
                        continue
                    logger.info("[Batch] Loading uploaded file: %s", audio_path.name)
                    try:
                        audio, sr = load_audio(str(audio_path), sample_rate=24000)
                    except Exception as e:
                        logger.warning("[Batch] Failed to load %s: %s", audio_path.name, e)
                        continue

                    duration_s = len(audio) / sr
                    if duration_s < 2.0:
                        logger.warning("[Batch] Skipping %s: too short (%.1fs, min 2s)", audio_path.name, duration_s)
                        continue

                    effective_duration = min(duration_s, REFERENCE_MAX_DURATION)
                    end_sample = int(effective_duration * sr)
                    trimmed = audio[:end_sample]
                    if np.abs(trimmed).max() > 0.99:
                        trimmed = normalize_audio(trimmed)
                    segment_path = temp_path / f"upload_{source_index}_segment.wav"
                    save_audio(trimmed, str(segment_path), sr, trailing_silence_seconds=0)
                    logger.info("[Batch] Upload segment prepared: %s (%.1fs)", audio_path.name, len(trimmed) / sr)
                    output_prefix = "source"

                paths = await _process_source_and_generate(
                    segment_path,
                    source_index,
                    output_prefix,
                    output_folder,
                    text_lines,
                    language,
                    stt_model,
                    _progress,
                    total_sources,
                )
                all_output_paths.extend(paths)

            except Exception as e:
                logger.exception("[Batch] Source %s failed: %s", source_index, e)
                raise

        if not all_output_paths:
            raise ValueError("No audio files were generated")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in all_output_paths:
                zf.write(p, p.name)

        zip_buffer.seek(0)
        zip_bytes = zip_buffer.read()
        filenames = [p.name for p in all_output_paths]

    logger.info("[Batch] Batch voice clone complete: %s files in ZIP (%.1f MB)", len(filenames), len(zip_bytes) / (1024 * 1024))
    return zip_bytes, filenames
