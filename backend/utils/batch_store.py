"""
Batch voice clone task tracking and ZIP storage.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)
_batch_store: dict[str, "BatchCloneState"] = {}
_store_lock = threading.Lock()

MAX_LOG_LINES = 500


@dataclass
class BatchCloneState:
    """State for a batch clone task."""
    batch_id: str
    status: str  # "processing" | "complete" | "error"
    total_sources: int = 0
    total_lines: int = 0
    current_source: int = 0
    current_line: int = 0
    zip_bytes: Optional[bytes] = None
    error: Optional[str] = None
    filenames: list[str] = field(default_factory=list)
    zip_filename: Optional[str] = None  # optional custom name for download
    logs: list[str] = field(default_factory=list)
    worker_stats: Optional[dict[str, Any]] = None


def start_batch(
    batch_id: str,
    total_sources: int,
    total_lines: int,
    zip_filename: Optional[str] = None,
) -> None:
    """Mark batch as started."""
    with _store_lock:
        _batch_store[batch_id] = BatchCloneState(
            batch_id=batch_id,
            status="processing",
            total_sources=total_sources,
            total_lines=total_lines,
            current_source=0,
            current_line=0,
            zip_filename=zip_filename,
            logs=[],
            worker_stats=None,
        )
    logger.info("[BatchStore] Batch %s started: %s sources, %s text lines", batch_id[:8], total_sources, total_lines)


def append_batch_log(batch_id: str, message: str) -> None:
    """Append a log line to the batch state and log to terminal."""
    with _store_lock:
        state = _batch_store.get(batch_id)
        if state is not None:
            state.logs.append(message)
            if len(state.logs) > MAX_LOG_LINES:
                state.logs = state.logs[-MAX_LOG_LINES:]
    logger.info("[Batch %s] %s", batch_id[:8], message)


def update_batch_worker_stats(batch_id: str, **kwargs: Any) -> None:
    """Update worker stats for the batch. Merges with existing stats."""
    with _store_lock:
        state = _batch_store.get(batch_id)
        if state is not None:
            if state.worker_stats is None:
                state.worker_stats = {}
            state.worker_stats.update(kwargs)


_last_progress_log: dict[str, tuple[int, int]] = {}


def update_batch_progress(
    batch_id: str,
    current_source: int,
    current_line: int,
    total_sources: int,
    total_lines: int,
) -> None:
    """Update batch progress."""
    with _store_lock:
        if batch_id in _batch_store:
            s = _batch_store[batch_id]
            s.current_source = current_source
            s.current_line = current_line
            s.total_sources = total_sources
            s.total_lines = total_lines
            # Log when we advance (new source or new line)
            key = (current_source, current_line)
            last = _last_progress_log.get(batch_id, (-1, -1))
            if key != last:
                logger.info("[BatchStore] Batch %s progress: source %s/%s, generating line %s/%s", batch_id[:8], current_source + 1, total_sources, current_line, total_lines)
                _last_progress_log[batch_id] = key


def complete_batch(batch_id: str, zip_bytes: bytes, filenames: list[str]) -> None:
    """Mark batch complete and store ZIP."""
    with _store_lock:
        if batch_id in _batch_store:
            s = _batch_store[batch_id]
            s.status = "complete"
            s.zip_bytes = zip_bytes
            s.filenames = filenames
        else:
            _batch_store[batch_id] = BatchCloneState(
                batch_id=batch_id,
                status="complete",
                zip_bytes=zip_bytes,
                filenames=filenames,
            )
    logger.info("[BatchStore] Batch %s complete: %s files, ZIP ready for download", batch_id[:8], len(filenames))


def error_batch(batch_id: str, error_msg: str) -> None:
    """Mark batch as failed."""
    with _store_lock:
        if batch_id in _batch_store:
            _batch_store[batch_id].status = "error"
            _batch_store[batch_id].error = error_msg
        else:
            _batch_store[batch_id] = BatchCloneState(
                batch_id=batch_id,
                status="error",
                error=error_msg,
            )
    logger.error("[BatchStore] Batch %s error: %s", batch_id[:8], error_msg)


def stopped_batch(
    batch_id: str,
    current_source: int,
    current_line: int,
) -> None:
    """Mark batch as stopped by user (cancelled)."""
    with _store_lock:
        if batch_id in _batch_store:
            s = _batch_store[batch_id]
            s.status = "stopped"
            s.current_source = current_source
            s.current_line = current_line
            s.error = None
            logger.info(
                "[BatchStore] Batch %s stopped by user at source %s/%s, line %s/%s",
                batch_id[:8],
                current_source + 1,
                s.total_sources,
                current_line,
                s.total_lines,
            )
        else:
            _batch_store[batch_id] = BatchCloneState(
                batch_id=batch_id,
                status="stopped",
                current_source=current_source,
                current_line=current_line,
            )


def get_batch_status(batch_id: str) -> Optional[BatchCloneState]:
    """Get batch state, or None if not found. Returns a snapshot; safe to read without lock."""
    with _store_lock:
        state = _batch_store.get(batch_id)
        if state is None:
            return None
        # Return a copy so caller cannot mutate store; copy logs and worker_stats
        return BatchCloneState(
            batch_id=state.batch_id,
            status=state.status,
            total_sources=state.total_sources,
            total_lines=state.total_lines,
            current_source=state.current_source,
            current_line=state.current_line,
            zip_bytes=state.zip_bytes,
            error=state.error,
            filenames=list(state.filenames),
            zip_filename=state.zip_filename,
            logs=list(state.logs),
            worker_stats=dict(state.worker_stats) if state.worker_stats else None,
        )


def get_batch_zip(batch_id: str) -> Optional[bytes]:
    """Get ZIP bytes if batch is complete. Optionally clear after first read to free memory."""
    with _store_lock:
        state = _batch_store.get(batch_id)
        if state and state.status == "complete" and state.zip_bytes is not None:
            return state.zip_bytes
    return None


def clear_batch(batch_id: str) -> None:
    """Remove batch from store (e.g. after download)."""
    with _store_lock:
        if batch_id in _batch_store:
            del _batch_store[batch_id]
            logger.info("[BatchStore] Batch %s cleared from store", batch_id[:8])
