"""
Gradio UI for the voice clone script.

Provides a web interface for cloning voices from Excel/YouTube or local audio files.
Run from repo root: python -m backend.scripts.voice_clone_gradio
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tempfile
from io import StringIO
from pathlib import Path

# Add repo root to path
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import gradio as gr

from backend.scripts.youtube_voice_clone import (
    DEFAULT_QUESTIONS,
    _AUDIO_EXTENSIONS,
    run_voice_clone,
)


def _run_voice_clone_sync(
    input_mode: str,
    excel_file: tuple[str, str] | None,
    audio_files: list[tuple[str, str]] | None,
    output_folder: str,
    language: str,
    stt_model: str,
    questions_text: str,
    questions_file: tuple[str, str] | None,
    ffmpeg_location: str,
    progress: gr.Progress = gr.Progress(),
) -> str:
    """
    Synchronous wrapper for run_voice_clone. Captures logs and returns output message.
    """
    # Capture logging to string
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        out_path = Path(output_folder or "out").resolve()
        out_path.mkdir(parents=True, exist_ok=True)

        excel_path = None
        folder_path = None
        questions = None
        questions_file_path = None
        used_temp_folder = False

        if input_mode == "Excel (YouTube)":
            if not excel_file:
                return "Error: Please upload an Excel file."
            excel_path = Path(excel_file[0])
            if not excel_path.exists():
                return f"Error: Excel file not found: {excel_path}"

        else:  # Folder (local audio)
            if not audio_files or len(audio_files) == 0:
                return "Error: Please upload at least one audio file (.wav, .mp3, .flac, .ogg, .m4a)."
            # Create temp dir and copy uploaded files
            temp_dir = Path(tempfile.mkdtemp(prefix="gradio_audio_"))
            used_temp_folder = True
            try:
                for i, f in enumerate(audio_files):
                    path = f[0] if isinstance(f, (list, tuple)) else f
                    if path and Path(path).exists():
                        p = Path(path)
                        if p.suffix.lower() in _AUDIO_EXTENSIONS:
                            shutil.copy2(str(path), temp_dir / f"audio_{i:03d}{p.suffix}")
                folder_path = temp_dir
            except Exception as e:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return f"Error preparing audio files: {e}"

        if questions_text and questions_text.strip():
            questions = [q.strip() for q in questions_text.strip().splitlines() if q.strip()]
            questions_file_path = None
        elif questions_file and len(questions_file) > 0:
            qf0 = questions_file[0]
            questions_file_path = Path(qf0[0] if isinstance(qf0, (tuple, list)) else qf0)
            questions = None
        else:
            questions = DEFAULT_QUESTIONS
            questions_file_path = None

        ffmpeg_path = Path(ffmpeg_location).resolve() if ffmpeg_location.strip() else None
        if ffmpeg_path is not None and not ffmpeg_path.exists():
            return f"Error: FFmpeg path does not exist: {ffmpeg_path}"

        def _progress_callback(
            audio_name: str,
            current: int,
            total: int,
            audio_index: int,
            total_audios: int,
        ) -> None:
            overall_current = audio_index * total + current
            overall_total = total_audios * total
            progress(
                overall_current / overall_total if overall_total else 0,
                desc=f"For {audio_name}: generating {current} of {total}",
            )

        async def _run():
            await run_voice_clone(
                excel_path=excel_path,
                folder_path=folder_path,
                output_folder=out_path,
                language=language.strip() or "en",
                stt_model=stt_model,
                questions=questions if questions else None,
                questions_file=questions_file_path,
                ffmpeg_location=ffmpeg_path,
                progress_callback=_progress_callback,
            )

        try:
            asyncio.run(_run())
        except ValueError as e:
            return f"Error: {e}\n\n{log_stream.getvalue()}"
        except Exception as e:
            return f"Error: {e}\n\n{log_stream.getvalue()}"
        finally:
            if used_temp_folder and folder_path and folder_path.exists():
                shutil.rmtree(folder_path, ignore_errors=True)

        output = log_stream.getvalue()
        return f"{output}\n\nDone! Output folder: {out_path}"

    finally:
        root.removeHandler(handler)


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Voice Cloning Tool") as demo:
        gr.Markdown("# Voice Cloning Tool ")
        gr.Markdown(
            "Clone voices from **Excel/YouTube** (YouTube URL, Start, Duration per row) or from **local audio files**."
        )

        with gr.Row():
            input_mode = gr.Radio(
                ["Excel (YouTube)", "Folder (local audio)"],
                value="Excel (YouTube)",
                label="Input mode",
            )

        with gr.Row():
            excel_file = gr.File(
                label="Excel file (.xlsx)",
                file_types=[".xlsx"],
                visible=True,
            )
            audio_files = gr.File(
                label="Audio files",
                file_count="multiple",
                file_types=[".wav", ".mp3", ".flac", ".ogg", ".m4a"],
                visible=False,
            )

        def _toggle_input(mode):
            return (
                gr.update(visible=(mode == "Excel (YouTube)")),
                gr.update(visible=(mode == "Folder (local audio)")),
            )

        input_mode.change(
            _toggle_input,
            inputs=[input_mode],
            outputs=[excel_file, audio_files],
        )

        with gr.Row():
            output_folder = gr.Textbox(
                label="Output folder",
                value="out",
                placeholder="out",
            )
            language = gr.Textbox(
                label="Language code",
                value="en",
                placeholder="en",
            )

        stt_model = gr.Dropdown(
            choices=["tiny", "base", "small", "medium", "large"],
            value="base",
            label="Whisper STT model",
        )

        questions_text = gr.Textbox(
            label="Questions (one per line, optional)",
            placeholder="What is your name?\nWhere are you from?\nWhat do you do for a living?",
            lines=5,
        )

        questions_file = gr.File(
            label="Or upload questions file (.txt)",
            file_types=[".txt"],
        )

        ffmpeg_location = gr.Textbox(
            label="FFmpeg path (optional, for Excel/YouTube mode)",
            placeholder="/usr/bin/ffmpeg or /path/to/ffmpeg",
        )

        run_btn = gr.Button("Run voice clone", variant="primary")

        output_log = gr.Textbox(
            label="Output",
            lines=15,
            max_lines=30,
            interactive=False,
        )

        run_btn.click(
            _run_voice_clone_sync,
            inputs=[
                input_mode,
                excel_file,
                audio_files,
                output_folder,
                language,
                stt_model,
                questions_text,
                questions_file,
                ffmpeg_location,
            ],
            outputs=[output_log],
        )

        gr.Markdown(
            "**Excel format:** YouTube URL | Start (seconds or M:SS) | Duration (2–30 sec). "
            "**Folder:** Upload audio files (2–30 sec each; longer files are trimmed)."
        )

    return demo


def main() -> None:
    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
