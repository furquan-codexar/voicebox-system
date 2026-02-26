# Voicebox (Backend)

The open-source voice synthesis backend. Clone voices, generate speech, and build voice-powered apps — all running locally on your machine.

## Quick Start (serve.sh)

One script to set up and run the voice system on any machine:

```bash
# First-time setup (venv, deps, FFmpeg, SoX)
./serve.sh setup

# Setup with PyTorch CUDA (Linux NVIDIA GPU)
./serve.sh setup --cuda

# Start the API server
./serve.sh

# Run YouTube voice clone (CLI)
./serve.sh youtube-clone --excel path/to/videos.xlsx -o output_folder

# Run Gradio voice clone UI (web interface at http://localhost:7860)
./serve.sh gradio
```

**Requirements:** Python 3.10+, curl, unzip. Supports macOS (Intel/Apple Silicon) and Ubuntu/Debian Linux.

---

## What is Voicebox?

Voicebox is a **local-first voice cloning backend** powered by Qwen3-TTS. Clone any voice from a few seconds of audio and generate speech via REST API or CLI scripts.

- **Complete privacy** — models and voice data stay on your machine
- **API-first** — integrate voice synthesis into your own projects
- **Super fast on Mac** — MLX backend with native Metal acceleration for 4-5x faster inference on Apple Silicon

---

## Running backend/scripts

| Script | How to run |
|--------|------------|
| API server | `./serve.sh` or `./serve.sh serve` |
| YouTube voice clone (CLI) | `./serve.sh youtube-clone --excel path/to.xlsx -o out` or `python run_youtube_voice_clone.py --excel ... -o out` |
| Gradio voice clone UI | `./serve.sh gradio` or `python run_voice_clone_gradio.py` |

See [backend/scripts/README-YOUTUBE-CLONE.md](backend/scripts/README-YOUTUBE-CLONE.md) for detailed YouTube voice clone usage.

---

## API

Voicebox exposes a full REST API at `http://localhost:8000` when running:

```bash
# Generate speech
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "profile_id": "abc123", "language": "en"}'

# List voice profiles
curl http://localhost:8000/profiles

# Create a profile
curl -X POST http://localhost:8000/profiles \
  -H "Content-Type: application/json" \
  -d '{"name": "My Voice", "language": "en"}'
```

Full API documentation: `http://localhost:8000/docs` when the server is running.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python) |
| Voice Model | Qwen3-TTS (PyTorch or MLX) |
| Transcription | Whisper (PyTorch or MLX) |
| Inference Engine | MLX (Apple Silicon) / PyTorch (Windows/Linux/Intel) |
| Database | SQLite |

---

## Project Structure

```
voicebox/
├── backend/                 # Python FastAPI server + scripts
│   ├── scripts/             # youtube_voice_clone.py, voice_clone_gradio.py
│   ├── main.py
│   ├── requirements.txt
│   └── ...
├── run_voice_clone_gradio.py
├── run_youtube_voice_clone.py
├── run_youtube_voice_clone.sh
└── serve.sh
```

---

## Development

**Prerequisites:** [Python 3.10+](https://python.org)

```bash
# Setup
./serve.sh setup

# Run API server (development with uvicorn reload, if using npm/bun)
# Or use: ./serve.sh serve
uvicorn backend.main:app --reload --port 17493
```

**Performance:**
- **Apple Silicon (M1/M2/M3)**: MLX backend with Metal acceleration
- **Windows/Linux/Intel Mac**: PyTorch backend (CUDA GPU recommended)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
