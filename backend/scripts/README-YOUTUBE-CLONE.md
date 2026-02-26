# YouTube Voice Clone Script – Setup & Usage

Clone voices from YouTube video segments and generate audio for a fixed set of questions. No APIs required; everything runs locally.

## 1. Prerequisites

- **Python 3.9+** with the project dependencies installed.
- **FFmpeg** installed and on your PATH (used by yt-dlp for audio extraction).

### Install dependencies

From the **repo root**:

```bash
# If you use the project venv (recommended)
.venv/bin/pip install -r backend/requirements.txt

# Or system/user Python
pip install -r backend/requirements.txt
```

### Check FFmpeg

```bash
ffmpeg -version
```

If missing, install:

- **macOS (Homebrew):** `brew install ffmpeg`
- **macOS (no Homebrew / newer OS):** Download static builds from [evermeet.cx/ffmpeg](https://evermeet.cx/ffmpeg/), then run the script with `--ffmpeg-location /path/to/dir` (directory containing `ffmpeg` and `ffprobe`).
- **Ubuntu/Debian:** `sudo apt install ffmpeg`
- **Windows:** Download from https://ffmpeg.org/download.html and add to PATH.

If FFmpeg is installed but not on PATH, use `--ffmpeg-location` (path to the `ffmpeg` binary or to a directory containing both `ffmpeg` and `ffprobe`).

## 2. Excel file

Create an Excel file (`.xlsx`) with these columns (header names case-insensitive):

| YouTube URL | Start | Duration |
|-------------|--------|----------|
| https://youtube.com/watch?v=... | 65 or 1:05 | 15 or 0:15 |

- **YouTube URL:** Full video URL.
- **Start:** Start time in seconds (e.g. `65`) or `M:SS` / `MM:SS` (e.g. `1:05`).
- **Duration:** Length of the clip in seconds or `M:SS` (reference audio must be 2–30 seconds).

## 3. Run the script

From the **repo root** you can run it in either of these ways:

### Option A – Python runner (works on all platforms)

```bash
python run_youtube_voice_clone.py --excel path/to/videos.xlsx -o my_output_folder
```

With optional arguments:

```bash
python run_youtube_voice_clone.py --excel videos.xlsx -o out \
  --language en \
  --stt-model base \
  --questions-file questions.txt
```

### Option B – Shell script (Unix/macOS; uses .venv if present)

```bash
chmod +x run_youtube_voice_clone.sh
./run_youtube_voice_clone.sh --excel path/to/videos.xlsx -o my_output_folder
```

### Option C – As a module (from repo root)

```bash
python -m backend.scripts.youtube_voice_clone --excel path/to/videos.xlsx -o my_output_folder
```

## 4. Options

| Option | Description |
|--------|-------------|
| `--excel` | Path to Excel file (required). |
| `-o`, `--output-folder` | Output folder for generated WAV files (required). |
| `--language` | Language code for TTS/STT (default: `en`). |
| `--stt-model` | Whisper size: tiny, base, small, medium, large (default: `base`). |
| `--questions-file` | Text file with one question per line (optional; otherwise built-in questions are used). |
| `--ffmpeg-location` | Path to `ffmpeg` executable or directory containing `ffmpeg` and `ffprobe` (if not on PATH). |
| `--tts-model` | TTS model size: `0.6B` (less VRAM) or `1.7B` (default). Use `0.6B` if you hit GPU OOM. |

## 5. Output

For each video row, the script generates one WAV per question, e.g.:

- `my_output_folder/video_00_q00.wav`, `video_00_q01.wav`, … (first video)
- `my_output_folder/video_01_q00.wav`, … (second video)

## 6. Questions

- **Default:** If you don’t pass `--questions-file`, the script uses built-in questions (e.g. “What is your name?”, “Where are you from?”, “What do you do for a living?”). You can change these in `backend/scripts/youtube_voice_clone.py` in `DEFAULT_QUESTIONS`.
- **From file:** Put one question per line in a text file and pass it with `--questions-file path/to/questions.txt`.

## 7. GPU out of memory?

If you see `CUDA out of memory` (other processes using the GPU), you can:

- Use the smaller TTS model: `--tts-model 0.6B`
- Force CPU: `CUDA_VISIBLE_DEVICES="" python run_youtube_voice_clone.py --excel ... -o ...` (slower but works without GPU).
