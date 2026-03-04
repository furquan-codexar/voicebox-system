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

import asyncio
import io
import logging
import multiprocessing
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np


class BatchCancelled(Exception):
    """Raised when the user requested to stop the batch."""
    def __init__(self, current_source: int, current_line: int) -> None:
        self.current_source = current_source
        self.current_line = current_line
        super().__init__(f"Batch cancelled at source {current_source}, line {current_line}")

from . import config
from .tts import get_tts_model
from .transcribe import get_whisper_model
from .utils.audio import (
    load_audio,
    save_audio,
    validate_reference_audio,
    normalize_audio,
)
from .utils.batch_store import append_batch_log, update_batch_worker_stats

TTS_MODEL_SIZE = "1.7B"
REFERENCE_MAX_DURATION = 30.0
LEADING_SILENCE_SECONDS = 0.5
TRAILING_SILENCE_SECONDS = 2.0
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
# Max concurrent TTS generations per source (avoids GPU/CPU overload)
MAX_CONCURRENT_GENERATIONS = 4

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


async def _generate_one(
    semaphore: asyncio.Semaphore,
    tts_model,
    voice_prompt: dict,
    line_idx: int,
    out_name: str,
    text: str,
    language: str,
    output_folder: Path,
    source_index: int,
    total_sources: int,
    total_entries: int,
    progress_callback: Callable[[int, int, int, int], None] | None,
    leading_silence_seconds: float = LEADING_SILENCE_SECONDS,
    trailing_silence_seconds: float = TRAILING_SILENCE_SECONDS,
) -> tuple[int, Path]:
    """Generate one WAV; used inside semaphore for bounded parallelism."""
    async with semaphore:
        audio_out, sample_rate = await tts_model.generate(
            text,
            voice_prompt,
            language=language,
        )
        if total_sources > 1:
            out_name = f"{source_index:02d}_{out_name}"
        out_path = output_folder / out_name
        save_audio(
            audio_out,
            str(out_path),
            sample_rate,
            leading_silence_seconds=leading_silence_seconds,
            trailing_silence_seconds=trailing_silence_seconds,
        )
        if progress_callback:
            progress_callback(source_index, line_idx + 1, total_sources, total_entries)
        return (line_idx, out_path)


async def _get_voice_prompt_for_segment(
    segment_path: Path,
    source_index: int,
    language: str,
    stt_model: str,
) -> dict:
    """Validate segment, transcribe with Whisper, create TTS voice prompt. Returns voice_prompt dict."""
    is_valid, err = validate_reference_audio(str(segment_path))
    if not is_valid:
        raise ValueError(f"Invalid reference audio for source {source_index}: {err}")
    tts_model = get_tts_model()
    whisper_model = get_whisper_model()
    await whisper_model.load_model_async(stt_model)
    transcript = await whisper_model.transcribe(str(segment_path), language=language or None)
    if not (transcript and transcript.strip()):
        raise ValueError(f"Empty transcript for source {source_index}")
    await tts_model.load_model_async(TTS_MODEL_SIZE)
    voice_prompt, _ = await tts_model.create_voice_prompt(
        str(segment_path),
        transcript.strip(),
        use_cache=False,
    )
    return voice_prompt


async def _process_source_and_generate(
    segment_path: Path,
    source_index: int,
    output_prefix: str,
    output_folder: Path,
    text_entries: list[tuple[str, str]],
    language: str,
    stt_model: str,
    progress_callback: Callable[[int, int, int, int], None] | None,
    total_sources: int,
    batch_id: str | None = None,
    leading_silence_seconds: float = LEADING_SILENCE_SECONDS,
    trailing_silence_seconds: float = TRAILING_SILENCE_SECONDS,
) -> list[Path]:
    """
    Validate, transcribe, create voice prompt, generate for each (out_name, text) entry.
    Returns list of output file paths. Adds 2s trailing silence to each output.
    text_entries: list of (output_filename, tts_text).
    """
    is_valid, err = validate_reference_audio(str(segment_path))
    if not is_valid:
        raise ValueError(f"Invalid reference audio for source {source_index}: {err}")

    logger.info("[Batch] Source %s: validated segment (2-30s, no clipping)", source_index)
    if batch_id:
        append_batch_log(batch_id, f"Source {source_index + 1}/{total_sources} validated.")

    tts_model = get_tts_model()
    whisper_model = get_whisper_model()

    logger.info("[Batch] Source %s: loading Whisper model '%s' for transcription", source_index, stt_model)
    if batch_id:
        append_batch_log(batch_id, f"Transcribing source {source_index + 1}.")
    await whisper_model.load_model_async(stt_model)
    transcript = await whisper_model.transcribe(str(segment_path), language=language or None)
    if not (transcript and transcript.strip()):
        raise ValueError(f"Empty transcript for source {source_index}")

    logger.info("[Batch] Source %s: transcribed (length=%d chars)", source_index, len(transcript.strip()))
    if batch_id:
        append_batch_log(batch_id, f"Source {source_index + 1} transcribed.")

    logger.info("[Batch] Source %s: loading TTS model '%s', creating voice prompt", source_index, TTS_MODEL_SIZE)
    await tts_model.load_model_async(TTS_MODEL_SIZE)
    voice_prompt, _ = await tts_model.create_voice_prompt(
        str(segment_path),
        transcript.strip(),
        use_cache=False,
    )
    if batch_id:
        append_batch_log(batch_id, "Voice prompt created.")
        update_batch_worker_stats(
            batch_id, workers_loaded=1, current_phase="generating"
        )

    total_entries = len(text_entries)
    logger.info("[Batch] Source %s: voice prompt created, generating %s outputs (parallel, max %s)", source_index, total_entries, MAX_CONCURRENT_GENERATIONS)
    if batch_id:
        append_batch_log(batch_id, f"Generating {total_entries} line(s).")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)
    tasks = [
        _generate_one(
            semaphore,
            tts_model,
            voice_prompt,
            line_idx,
            out_name,
            text,
            language,
            output_folder,
            source_index,
            total_sources,
            total_entries,
            progress_callback,
            leading_silence_seconds=leading_silence_seconds,
            trailing_silence_seconds=trailing_silence_seconds,
        )
        for line_idx, (out_name, text) in enumerate(text_entries)
    ]
    results = await asyncio.gather(*tasks)
    output_paths = [out_path for _, out_path in sorted(results, key=lambda x: x[0])]

    logger.info("[Batch] Source %s: generated %s files", source_index, len(output_paths))
    return output_paths


def _worker_process(
    task_queue: multiprocessing.Queue,
    result_queue: multiprocessing.Queue,
    output_folder_str: str,
    model_size: str,
    leading_silence_seconds: float = LEADING_SILENCE_SECONDS,
    trailing_silence_seconds: float = TRAILING_SILENCE_SECONDS,
) -> None:
    """
    Worker process: load TTS model once, then pull tasks and generate.
    Each task is (line_idx, out_name, text, voice_prompt, language).
    Puts (line_idx, path_str) into result_queue for each result.
    Exits when task_queue.get() returns None.
    """
    import os
    worker_pid = os.getpid()
    logger.info("[Batch] Worker (pid %s) started", worker_pid)
    try:
        tts = get_tts_model()
        tts._load_model_sync(model_size)
        logger.info("[Batch] Worker (pid %s) loaded TTS model", worker_pid)
    except Exception as e:
        logger.exception("[Batch] Worker (pid %s) failed to load model: %s", worker_pid, e)
        result_queue.put(("error", str(e)))
        return
    output_folder = Path(output_folder_str)
    while True:
        try:
            task = task_queue.get()
        except Exception:
            break
        if task is None:
            break
        line_idx, out_name, text, voice_prompt, language = task
        logger.info("[Batch] Worker (pid %s) generating line %s", worker_pid, line_idx)
        try:
            audio_out, sample_rate = tts.generate_sync(text, voice_prompt, language=language)
            out_path = output_folder / out_name
            save_audio(
                audio_out,
                str(out_path),
                sample_rate,
                leading_silence_seconds=leading_silence_seconds,
                trailing_silence_seconds=trailing_silence_seconds,
            )
            result_queue.put((line_idx, str(out_path)))
            logger.info("[Batch] Worker (pid %s) completed line %s", worker_pid, line_idx)
        except Exception as e:
            logger.exception("[Batch] Worker (pid %s) failed line %s: %s", worker_pid, line_idx, e)
            result_queue.put(("error", (line_idx, str(e))))


def _run_batch_with_workers_sync(
    batch_id: str,
    output_folder: Path,
    text_entries: list[tuple[str, str]],
    voice_prompt: dict,
    language: str,
    num_workers: int,
    source_index: int,
    total_sources: int,
    progress_callback: Callable[[int, int, int, int], None] | None,
    leading_silence_seconds: float = LEADING_SILENCE_SECONDS,
    trailing_silence_seconds: float = TRAILING_SILENCE_SECONDS,
) -> list[Path]:
    """
    Run TTS generation for one source using N worker processes.
    Returns ordered list of output paths (one per text entry). Blocking.
    """
    X = len(text_entries)
    if batch_id:
        append_batch_log(batch_id, f"Starting {num_workers} worker process(es).")
        update_batch_worker_stats(
            batch_id,
            current_phase="loading_workers",
            tasks_total=X,
            tasks_completed=0,
            tasks_waiting=X,
        )
    out_name_for = (
        (lambda idx, name: f"{source_index:02d}_{name}")
        if total_sources > 1
        else (lambda idx, name: name)
    )
    tasks = [
        (line_idx, out_name_for(line_idx, out_name), text, voice_prompt, language)
        for line_idx, (out_name, text) in enumerate(text_entries)
    ]
    if batch_id:
        append_batch_log(batch_id, f"Task queue filled with {X} task(s).")
    ctx = multiprocessing.get_context("spawn")
    task_queue = ctx.Queue()
    result_queue = ctx.Queue()
    for t in tasks:
        task_queue.put(t)
    for _ in range(num_workers):
        task_queue.put(None)
    processes = [
        ctx.Process(
            target=_worker_process,
            args=(
                task_queue,
                result_queue,
                str(output_folder),
                TTS_MODEL_SIZE,
                leading_silence_seconds,
                trailing_silence_seconds,
            ),
        )
        for _ in range(num_workers)
    ]
    for p in processes:
        p.start()
    if batch_id:
        update_batch_worker_stats(batch_id, workers_loaded=num_workers)
    results: dict[int, Path] = {}
    while len(results) < X:
        try:
            item = result_queue.get(timeout=600)
        except Exception as e:
            for p in processes:
                p.terminate()
            raise RuntimeError(f"Timeout or error waiting for results: {e}") from e
        if isinstance(item, tuple) and len(item) == 2:
            if item[0] == "error":
                err_val = item[1]
                for p in processes:
                    p.terminate()
                if isinstance(err_val, tuple):
                    line_idx, err_msg = err_val
                    raise RuntimeError(f"Worker failed line {line_idx}: {err_msg}") from None
                raise RuntimeError(f"Worker failed to load model: {err_val}") from None
            line_idx, path_str = item
            if not isinstance(line_idx, int) or line_idx < 0 or line_idx >= X:
                for p in processes:
                    p.terminate()
                raise RuntimeError(f"Worker returned invalid line_idx: {line_idx!r}") from None
            results[line_idx] = Path(path_str)
            if batch_id:
                update_batch_worker_stats(
                    batch_id,
                    tasks_completed=len(results),
                    tasks_waiting=X - len(results),
                    current_phase="generating",
                )
            if progress_callback:
                progress_callback(source_index, len(results), total_sources, X)
        else:
            for p in processes:
                p.terminate()
            raise RuntimeError(f"Unexpected result from worker: {type(item).__name__}") from None
    if batch_id:
        append_batch_log(batch_id, f"All {X} output(s) generated for source {source_index + 1}.")
    for p in processes:
        p.join(timeout=10)
        if p.is_alive():
            p.terminate()
    if len(results) != X or set(results.keys()) != set(range(X)):
        raise RuntimeError(f"Expected {X} results, got {len(results)}")
    return [results[i] for i in range(X)]


async def run_batch_voice_clone(
    *,
    mode: str,
    youtube_url: str | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    audio_paths: list[Path] | None = None,
    text_lines: list[str] | None = None,
    text_entries: list[tuple[str, str]] | None = None,
    language: str = "en",
    stt_model: str = "base",
    ffmpeg_location: Path | None = None,
    progress_callback: Callable[[int, int, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    batch_id: str | None = None,
    leading_silence_seconds: float = LEADING_SILENCE_SECONDS,
    trailing_silence_seconds: float = TRAILING_SILENCE_SECONDS,
) -> tuple[bytes, list[str]]:
    """
    Run batch voice clone. Returns (zip_bytes, list of output filenames).

    mode: "youtube" | "upload"
    For YouTube: youtube_url, start_seconds, end_seconds required.
    For upload: audio_paths (list of temp file paths) required.
    Provide text_entries [(wav_filename, text), ...] or text_lines (converted to line_00.wav, etc).
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

    if text_entries is not None:
        if not text_entries:
            raise ValueError("text_entries must not be empty")
    elif text_lines:
        text_entries = [(f"line_{i:02d}.wav", line.strip()) for i, line in enumerate(text_lines) if line.strip()]
        if not text_entries:
            raise ValueError("At least one non-empty text line is required")
    else:
        raise ValueError("Provide text_lines or text_entries")

    total_sources = len(sources)
    num_workers = config.get_tts_workers()
    X = len(text_entries)
    total_tasks = total_sources * X
    logger.info("[Batch] run_batch_voice_clone started: mode=%s, sources=%s, entries=%s, workers=%s", mode, total_sources, X, num_workers)

    if batch_id:
        append_batch_log(batch_id, f"Batch started: {total_sources} source(s), {X} line(s).")
        update_batch_worker_stats(
            batch_id,
            workers_configured=num_workers,
            processes_started=num_workers if num_workers > 1 else 1,
            workers_loaded=0,
            tasks_total=total_tasks,
            tasks_completed=0,
            tasks_waiting=total_tasks,
            current_phase="starting",
        )

    def _progress(s_idx: int, l_idx: int, tot_s: int, tot_l: int) -> None:
        if progress_callback:
            progress_callback(s_idx, l_idx, tot_s, tot_l)
        if batch_id and tot_s and tot_l:
            done = (s_idx) * tot_l + l_idx
            update_batch_worker_stats(
                batch_id,
                tasks_completed=done,
                tasks_waiting=max(0, total_tasks - done),
                current_phase="generating",
            )

    all_output_paths: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="voice_clone_batch_") as temp_dir:
        temp_path = Path(temp_dir)
        output_folder = temp_path / "output"
        output_folder.mkdir()

        for source_index, source_type, item in sources:
            if cancel_check and cancel_check():
                logger.info("[Batch] Cancel requested at source %s", source_index + 1)
                raise BatchCancelled(source_index, 0)
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

                if num_workers > 1:
                    voice_prompt = await _get_voice_prompt_for_segment(
                        segment_path, source_index, language, stt_model
                    )
                    paths = await asyncio.to_thread(
                        _run_batch_with_workers_sync,
                        batch_id or "",
                        output_folder,
                        text_entries,
                        voice_prompt,
                        language,
                        num_workers,
                        source_index,
                        total_sources,
                        _progress,
                        leading_silence_seconds,
                        trailing_silence_seconds,
                    )
                    all_output_paths.extend(paths)
                else:
                    paths = await _process_source_and_generate(
                        segment_path,
                        source_index,
                        output_prefix,
                        output_folder,
                        text_entries,
                        language,
                        stt_model,
                        _progress,
                        total_sources,
                        batch_id=batch_id,
                        leading_silence_seconds=leading_silence_seconds,
                        trailing_silence_seconds=trailing_silence_seconds,
                    )
                    all_output_paths.extend(paths)

            except Exception as e:
                logger.exception("[Batch] Source %s failed: %s", source_index, e)
                raise

        if not all_output_paths:
            raise ValueError("No audio files were generated")

        if batch_id:
            append_batch_log(batch_id, "Building ZIP.")
            update_batch_worker_stats(batch_id, current_phase="zipping")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in all_output_paths:
                zf.write(p, p.name)

        zip_buffer.seek(0)
        zip_bytes = zip_buffer.read()
        filenames = [p.name for p in all_output_paths]

        if batch_id:
            append_batch_log(batch_id, f"Complete. {len(filenames)} file(s) in ZIP.")

    logger.info("[Batch] Batch voice clone complete: %s files in ZIP (%.1f MB)", len(filenames), len(zip_bytes) / (1024 * 1024))
    return zip_bytes, filenames
