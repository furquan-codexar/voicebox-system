#!/usr/bin/env bash
#
# voicebox serve.sh - One script to set up and run the voice system on any machine.
#
# Usage:
#   ./serve.sh setup          # First-time setup (venv, deps, FFmpeg, SoX)
#   ./serve.sh setup --cuda   # Setup with PyTorch CUDA (Linux NVIDIA GPU)
#   ./serve.sh                # Start the API server (default)
#   ./serve.sh serve          # Same as above
#   ./serve.sh youtube-clone --excel path/to/videos.xlsx -o out   # Run YouTube voice clone
#   ./serve.sh gradio           # Run Gradio voice clone UI (http://localhost:7860)
#
# Requirements: Python 3.10+, curl, unzip
# Supports: macOS (Intel/Apple Silicon), Ubuntu/Debian Linux
#

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
FFMPEG_DIR="$REPO_ROOT/tools/ffmpeg"
FFMPEG_STATIC_URL="https://evermeet.cx/ffmpeg/ffmpeg-122942-gc7b5f1537d.zip"
FFPROBE_STATIC_URL="https://evermeet.cx/ffmpeg/ffprobe-122942-gc7b5f1537d.zip"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "[serve.sh] $*"; }
warn() { echo "[serve.sh] WARNING: $*" >&2; }
die() { echo "[serve.sh] ERROR: $*" >&2; exit 1; }

detect_os() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)  echo "linux" ;;
    *)      echo "unknown" ;;
  esac
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Setup: Virtual environment
# ---------------------------------------------------------------------------
setup_venv() {
  if [ -x "$VENV_PYTHON" ]; then
    log "Virtual environment already exists at $VENV_DIR"
    return 0
  fi
  log "Creating virtual environment..."
  python3 -m venv "$VENV_DIR" || die "Failed to create venv. Install Python 3.10+ with venv."
  log "Virtual environment created."
}

# ---------------------------------------------------------------------------
# Setup: Python dependencies
# ---------------------------------------------------------------------------
setup_pip_deps() {
  log "Installing Python dependencies..."
  "$VENV_PIP" install -r "$REPO_ROOT/backend/requirements.txt" -q
  log "Python dependencies installed."
}

# ---------------------------------------------------------------------------
# Setup: PyTorch with CUDA (Linux only)
# ---------------------------------------------------------------------------
setup_pytorch_cuda() {
  if [ "$(detect_os)" != "linux" ]; then
    warn "PyTorch CUDA setup is for Linux only. Skipping."
    return 0
  fi
  if ! has_cmd nvidia-smi; then
    warn "nvidia-smi not found. Install NVIDIA drivers first. Skipping CUDA."
    return 0
  fi
  log "Installing PyTorch with CUDA 12.x..."
  "$VENV_PIP" install torch torchvision --index-url https://download.pytorch.org/whl/cu121 -q
  log "PyTorch with CUDA installed."
}

# ---------------------------------------------------------------------------
# Setup: FFmpeg
# ---------------------------------------------------------------------------
setup_ffmpeg() {
  # Already have working ffmpeg + ffprobe on PATH?
  if has_cmd ffmpeg && has_cmd ffprobe; then
    if ffmpeg -version >/dev/null 2>&1; then
      log "FFmpeg already available on PATH."
      return 0
    fi
  fi

  # Try package manager
  OS="$(detect_os)"
  if [ "$OS" = "macos" ]; then
    for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$brew" ]; then
        log "Installing FFmpeg via Homebrew..."
        if "$brew" install ffmpeg 2>/dev/null; then
          log "FFmpeg installed via Homebrew."
          return 0
        fi
      fi
    done
  elif [ "$OS" = "linux" ]; then
    if has_cmd apt-get; then
      log "Installing FFmpeg via apt..."
      sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg && {
        log "FFmpeg installed via apt."
        return 0
      }
    fi
  fi

  # Fallback: download static build
  log "Downloading FFmpeg static build..."
  mkdir -p "$FFMPEG_DIR"
  cd "$FFMPEG_DIR"
  if [ ! -x "./ffmpeg" ] || [ ! -x "./ffprobe" ]; then
    curl -sL -o ffmpeg.zip "$FFMPEG_STATIC_URL" || die "Failed to download ffmpeg"
    curl -sL -o ffprobe.zip "$FFPROBE_STATIC_URL" || die "Failed to download ffprobe"
    unzip -o -q ffmpeg.zip 2>/dev/null || true
    unzip -o -q ffprobe.zip 2>/dev/null || true
    chmod +x ffmpeg ffprobe 2>/dev/null || true
    rm -f ffmpeg.zip ffprobe.zip
  fi
  cd "$REPO_ROOT"
  if [ -x "$FFMPEG_DIR/ffmpeg" ] && [ -x "$FFMPEG_DIR/ffprobe" ]; then
    log "FFmpeg static build installed at $FFMPEG_DIR"
  else
    die "FFmpeg installation failed. Install manually: https://ffmpeg.org/download.html"
  fi
}

# ---------------------------------------------------------------------------
# Setup: SoX (optional, for qwen-tts)
# ---------------------------------------------------------------------------
setup_sox() {
  OS="$(detect_os)"
  if [ "$OS" = "macos" ]; then
    for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$brew" ]; then
        if "$brew" list sox >/dev/null 2>&1; then
          log "SoX already installed."
          return 0
        fi
        log "Installing SoX via Homebrew..."
        "$brew" install sox 2>/dev/null && { log "SoX installed."; return 0; }
        warn "SoX install failed (optional). Voice clone will still work."
        return 0
      fi
    done
  elif [ "$OS" = "linux" ]; then
    if has_cmd apt-get; then
      if dpkg -l sox >/dev/null 2>&1; then
        log "SoX already installed."
        return 0
      fi
      log "Installing SoX via apt..."
      sudo apt-get install -y -qq sox libsox-fmt-all 2>/dev/null && { log "SoX installed."; return 0; }
      warn "SoX install failed (optional). Voice clone will still work."
    fi
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Full setup
# ---------------------------------------------------------------------------
run_setup() {
  USE_CUDA=false
  for arg in "$@"; do
    [ "$arg" = "--cuda" ] && USE_CUDA=true
  done

  log "Starting voicebox setup..."
  setup_venv
  setup_pip_deps
  if [ "$USE_CUDA" = true ]; then
    setup_pytorch_cuda
  fi
  setup_ffmpeg
  setup_sox
  log "Setup complete. Run './serve.sh' to start the server."
}

# ---------------------------------------------------------------------------
# Serve: Start API server
# ---------------------------------------------------------------------------
run_serve() {
  if [ ! -x "$VENV_PYTHON" ]; then
    die "Run './serve.sh setup' first."
  fi
  log "Starting voicebox API server..."
  exec "$VENV_PYTHON" -m backend.main --host 0.0.0.0 --port 8000 "$@"
}

# ---------------------------------------------------------------------------
# YouTube voice clone (with correct ffmpeg path)
# ---------------------------------------------------------------------------
run_youtube_clone() {
  if [ ! -x "$VENV_PYTHON" ]; then
    die "Run './serve.sh setup' first."
  fi
  ARGS=()
  if [ -x "$FFMPEG_DIR/ffmpeg" ] && [ -x "$FFMPEG_DIR/ffprobe" ]; then
    ARGS+=(--ffmpeg-location "$FFMPEG_DIR")
  elif ! (has_cmd ffmpeg && has_cmd ffprobe); then
    die "FFmpeg not found. Run './serve.sh setup' or install FFmpeg."
  fi
  ARGS+=("$@")
  log "Running YouTube voice clone..."
  exec "$VENV_PYTHON" -m backend.scripts.youtube_voice_clone "${ARGS[@]}"
}

# ---------------------------------------------------------------------------
# Gradio voice clone UI
# ---------------------------------------------------------------------------
run_gradio() {
  if [ ! -x "$VENV_PYTHON" ]; then
    die "Run './serve.sh setup' first."
  fi
  log "Starting Gradio voice clone UI at http://localhost:7860..."
  exec "$VENV_PYTHON" -m backend.scripts.voice_clone_gradio "$@"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:-serve}" in
  setup)
    shift
    run_setup "$@"
    ;;
  serve)
    shift
    run_serve "$@"
    ;;
  youtube-clone)
    shift
    run_youtube_clone "$@"
    ;;
  gradio)
    shift
    run_gradio "$@"
    ;;
  *)
    echo "voicebox serve.sh"
    echo ""
    echo "Usage:"
    echo "  ./serve.sh setup          # First-time setup (venv, deps, FFmpeg, SoX)"
    echo "  ./serve.sh setup --cuda   # Setup with PyTorch CUDA (Linux NVIDIA GPU)"
    echo "  ./serve.sh                # Start the API server (default)"
    echo "  ./serve.sh serve          # Start the API server"
    echo "  ./serve.sh youtube-clone --excel path/to/videos.xlsx -o out   # YouTube voice clone"
    echo "  ./serve.sh gradio           # Gradio voice clone UI (http://localhost:7860)"
    echo ""
    exit 0
    ;;
esac
