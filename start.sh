#!/usr/bin/env bash
#
# Voicebox - Comprehensive startup script
# Clone the repo and run: ./start.sh
#
# Supports: Linux, macOS, WSL
# Auto-installs: Python 3.11+, Bun
# Runs: Backend (FastAPI) + Web (Vite) bound to 0.0.0.0 for network access
#

set -e

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
BACKEND_DIR="$REPO_ROOT/backend"
WEB_DIR="$REPO_ROOT/web"
VENV="$BACKEND_DIR/venv"
VENV_BIN="$VENV/bin"

# Ports (overridable via env)
BACKEND_PORT="${VOICEBOX_BACKEND_PORT:-17493}"
WEB_PORT="${VOICEBOX_WEB_PORT:-5173}"

# Flags
SETUP_ONLY=false
SKIP_SETUP=false
BACKEND_ONLY=false
WEB_ONLY=false
INSTALL_PREREQS=false

# Process PIDs for cleanup
BACKEND_PID=""
WEB_PID=""

# =============================================================================
# COLORS & LOGGING
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log_info() {
  echo -e "${CYAN}[$(timestamp)]${NC} $1"
}

log_success() {
  echo -e "${CYAN}[$(timestamp)]${NC} ${GREEN}✓${NC} $1"
}

log_warn() {
  echo -e "${CYAN}[$(timestamp)]${NC} ${YELLOW}⚠${NC} $1"
}

log_error() {
  echo -e "${CYAN}[$(timestamp)]${NC} ${RED}✗${NC} $1"
}

log_step() {
  echo -e "${CYAN}[$(timestamp)]${NC} ${BOLD}⟳${NC} $1"
}

log_run() {
  echo -e "${CYAN}[$(timestamp)]${NC} ${GREEN}▶${NC} $1"
}

print_banner() {
  local title="${1:-VOICEBOX}"
  echo ""
  echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
  printf "${CYAN}║${NC}  %-58s${CYAN}║${NC}\n" "$title"
  echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
}

print_access_box() {
  local ip="$1"
  echo ""
  echo -e "${GREEN}┌──────────────────────────────────────────────────────────────┐${NC}"
  printf "${GREEN}│${NC}  %-58s${GREEN}│${NC}\n" "Access Voicebox at:"
  printf "${GREEN}│${NC}  %-58s${GREEN}│${NC}\n" "• Web UI:   http://${ip}:${WEB_PORT}"
  printf "${GREEN}│${NC}  %-58s${GREEN}│${NC}\n" "• API:      http://${ip}:${BACKEND_PORT}"
  printf "${GREEN}│${NC}  %-58s${GREEN}│${NC}\n" "• API Docs: http://${ip}:${BACKEND_PORT}/docs"
  echo -e "${GREEN}└──────────────────────────────────────────────────────────────┘${NC}"
  echo ""
}

# =============================================================================
# ARGUMENT PARSING
# =============================================================================

while [[ $# -gt 0 ]]; do
  case $1 in
    --setup-only)
      SETUP_ONLY=true
      shift
      ;;
    --skip-setup)
      SKIP_SETUP=true
      shift
      ;;
    --backend-only)
      BACKEND_ONLY=true
      shift
      ;;
    --web-only)
      WEB_ONLY=true
      shift
      ;;
    --port-backend)
      BACKEND_PORT="$2"
      shift 2
      ;;
    --port-web)
      WEB_PORT="$2"
      shift 2
      ;;
    --install-prereqs)
      INSTALL_PREREQS=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --setup-only       Install dependencies only, do not start servers"
      echo "  --skip-setup       Skip dependency installation, start servers only"
      echo "  --backend-only     Run backend server only"
      echo "  --web-only         Run web server only"
      echo "  --port-backend N   Backend port (default: 17493)"
      echo "  --port-web N       Web port (default: 5173)"
      echo "  --install-prereqs  Force attempt to install Python/Bun"
      echo "  -h, --help         Show this help"
      echo ""
      echo "Environment:"
      echo "  VOICEBOX_BACKEND_PORT  Override backend port"
      echo "  VOICEBOX_WEB_PORT      Override web port"
      exit 0
      ;;
    *)
      log_error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# =============================================================================
# CLEANUP TRAP
# =============================================================================

cleanup() {
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    log_info "Stopping backend (PID $BACKEND_PID)..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    log_info "Stopping web (PID $WEB_PID)..."
    kill "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
  fi
  log_info "Shutdown complete."
  exit 0
}

trap cleanup EXIT SIGINT SIGTERM

# =============================================================================
# PLATFORM DETECTION
# =============================================================================

detect_platform() {
  OS="$(uname -s)"
  ARCH="$(uname -m)"
  case "$OS" in
    Linux)
      if grep -qi microsoft /proc/version 2>/dev/null; then
        PLATFORM="wsl"
      else
        PLATFORM="linux"
      fi
      ;;
    Darwin)
      PLATFORM="macos"
      ;;
    *)
      PLATFORM="unknown"
      ;;
  esac
  log_info "Platform: $PLATFORM ($OS $ARCH)"
}

# =============================================================================
# SERVER IP DETECTION
# =============================================================================

get_server_ip() {
  local ip=""
  if [[ "$PLATFORM" == "macos" ]]; then
    for iface in en0 en1 eth0; do
      ip=$(ipconfig getifaddr "$iface" 2>/dev/null)
      [[ -n "$ip" ]] && break
    done
  else
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [[ -z "$ip" ]] && ip=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
  fi
  echo "${ip:-127.0.0.1}"
}

# =============================================================================
# PREREQUISITES: PYTHON
# =============================================================================

find_python() {
  command -v python3.12 2>/dev/null || \
  command -v python3.13 2>/dev/null || \
  command -v python3.11 2>/dev/null || \
  command -v python3 2>/dev/null || \
  echo ""
}

check_python_version() {
  local py="$1"
  "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null
}

install_python() {
  log_step "Installing Python 3.11+..."
  case "$PLATFORM" in
    linux|wsl)
      if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y software-properties-common 2>/dev/null || true
        if ! command -v python3.12 &>/dev/null && ! command -v python3.11 &>/dev/null; then
          log_info "Adding deadsnakes PPA for Python 3.12..."
          sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
          sudo apt-get update -qq
        fi
        sudo apt-get install -y python3.12 python3.12-venv python3-pip 2>/dev/null || \
        sudo apt-get install -y python3.11 python3.11-venv python3-pip 2>/dev/null || \
        sudo apt-get install -y python3 python3-venv python3-pip
      elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3.12 python3-pip 2>/dev/null || sudo dnf install -y python3 python3-pip
      elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm python python-pip
      else
        log_error "No supported package manager (apt/dnf/pacman). Install Python 3.11+ manually."
        exit 1
      fi
      ;;
    macos)
      if command -v brew &>/dev/null; then
        brew install python@3.12 2>/dev/null || brew install python@3.11 2>/dev/null || brew install python3
      else
        log_error "Homebrew not found. Install from https://brew.sh or install Python 3.11+ manually."
        exit 1
      fi
      ;;
    *)
      log_error "Unsupported platform for auto-install. Install Python 3.11+ manually."
      exit 1
      ;;
  esac
  log_success "Python installed."
}

ensure_python() {
  PYTHON=$(find_python)
  if [[ -z "$PYTHON" ]] || ! check_python_version "$PYTHON"; then
    # Auto-install when missing or wrong version
    log_warn "Python 3.11+ required. Found: $(${PYTHON:-python3} --version 2>/dev/null || echo 'none')"
    install_python
    PYTHON=$(find_python)
  fi
  if ! check_python_version "$PYTHON"; then
    log_error "Python 3.11+ required. Install manually: https://python.org"
    exit 1
  fi
  log_success "Python $($PYTHON --version 2>&1 | cut -d' ' -f2) found"
}

# =============================================================================
# PREREQUISITES: BUN
# =============================================================================

find_bun() {
  if command -v bun &>/dev/null; then
    echo "bun"
    return
  fi
  if [[ -f "$HOME/.bun/bin/bun" ]]; then
    echo "$HOME/.bun/bin/bun"
    return
  fi
  echo ""
}

install_bun() {
  log_step "Installing Bun..."
  # Bun installer requires unzip (and curl)
  if command -v apt-get &>/dev/null; then
    sudo apt-get install -y unzip curl 2>/dev/null || true
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y unzip curl 2>/dev/null || true
  elif command -v brew &>/dev/null; then
    brew install unzip 2>/dev/null || true
  fi
  curl -fsSL https://bun.sh/install | bash
  if [[ -f "$HOME/.bun/bin/bun" ]]; then
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$HOME/.bun/bin:$PATH"
    log_success "Bun installed."
  else
    log_error "Bun install script completed but binary not found at ~/.bun/bin/bun"
    exit 1
  fi
}

ensure_bun() {
  BUN=$(find_bun)
  if [[ -z "$BUN" ]]; then
    # Auto-install when missing
    log_warn "Bun not found. Installing..."
    install_bun
    BUN=$(find_bun)
  fi
  if [[ -z "$BUN" ]]; then
    log_error "Bun installation failed. Install manually: https://bun.sh"
    exit 1
  fi
  # Resolve to full path for consistent usage
  if [[ "$BUN" == "bun" ]]; then
    BUN="$(command -v bun)"
  fi
  if [[ -z "$BUN" ]] && [[ -f "$HOME/.bun/bin/bun" ]]; then
    BUN="$HOME/.bun/bin/bun"
  fi
  log_success "Bun $($BUN --version 2>/dev/null || echo '') found"
}

# =============================================================================
# BACKEND SETUP
# =============================================================================

setup_backend() {
  log_step "Setting up backend..."
  cd "$REPO_ROOT"

  if [[ ! -d "$VENV" ]]; then
    log_info "Creating Python virtual environment..."
    "$PYTHON" -m venv "$VENV"
  fi

  log_info "Installing Python dependencies..."
  "$VENV_BIN/pip" install --upgrade pip -q
  "$VENV_BIN/pip" install -r "$BACKEND_DIR/requirements.txt" -q

  if [[ "$PLATFORM" == "macos" ]] && [[ "$ARCH" == "arm64" ]]; then
    log_info "Installing MLX dependencies (Apple Silicon)..."
    "$VENV_BIN/pip" install -r "$BACKEND_DIR/requirements-mlx.txt" -q
  fi

  log_info "Installing Qwen3-TTS..."
  "$VENV_BIN/pip" install "git+https://github.com/QwenLM/Qwen3-TTS.git" -q

  log_success "Backend ready."
}

# =============================================================================
# FRONTEND SETUP
# =============================================================================

setup_frontend() {
  log_step "Setting up frontend..."
  cd "$REPO_ROOT"
  $BUN install --silent
  log_success "Frontend ready."
}

# =============================================================================
# SERVER STARTUP
# =============================================================================

start_backend() {
  log_run "Backend starting on http://0.0.0.0:${BACKEND_PORT}"
  cd "$REPO_ROOT"
  PYTHONPATH="$REPO_ROOT" "$VENV_BIN/uvicorn" backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT" &
  BACKEND_PID=$!
  sleep 2
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    log_error "Backend failed to start."
    exit 1
  fi
}

start_web() {
  local server_ip="$1"
  log_run "Web starting on http://0.0.0.0:${WEB_PORT}"
  cd "$WEB_DIR"
  VITE_SERVER_URL="http://${server_ip}:${BACKEND_PORT}" $BUN run dev -- --host --port "$WEB_PORT" &
  WEB_PID=$!
  sleep 2
  if ! kill -0 "$WEB_PID" 2>/dev/null; then
    log_error "Web failed to start."
    exit 1
  fi
}

# =============================================================================
# MAIN
# =============================================================================

main() {
  cd "$REPO_ROOT"

  if [[ ! -f "$REPO_ROOT/package.json" ]] || [[ ! -d "$BACKEND_DIR" ]]; then
    log_error "Must be run from voicebox repo root. Not found: package.json or backend/"
    exit 1
  fi

  print_banner "VOICEBOX — Starting..."

  detect_platform
  ensure_python
  ensure_bun

  SERVER_IP=$(get_server_ip)
  log_info "Server IP: $SERVER_IP"

  if ! $SKIP_SETUP; then
    setup_backend
    setup_frontend
  else
    log_info "Skipping setup (--skip-setup)."
    if [[ ! -d "$VENV" ]] || [[ ! -f "$VENV_BIN/uvicorn" ]]; then
      log_error "Backend venv not found. Run without --skip-setup first."
      exit 1
    fi
    if [[ ! -d "$REPO_ROOT/node_modules" ]]; then
      log_error "Frontend node_modules not found. Run without --skip-setup first."
      exit 1
    fi
  fi

  if $SETUP_ONLY; then
    log_success "Setup complete. Run without --setup-only to start servers."
    exit 0
  fi

  if $BACKEND_ONLY; then
    start_backend
    print_access_box "$SERVER_IP"
    log_info "Backend running. Press Ctrl+C to stop."
    wait $BACKEND_PID
    exit 0
  fi

  if $WEB_ONLY; then
    log_warn "Web-only mode: ensure backend is running at http://${SERVER_IP}:${BACKEND_PORT}"
    start_web "$SERVER_IP"
    print_access_box "$SERVER_IP"
    log_info "Web running. Press Ctrl+C to stop."
    wait $WEB_PID
    exit 0
  fi

  # Full stack
  start_backend
  start_web "$SERVER_IP"
  print_access_box "$SERVER_IP"
  log_info "Voicebox is running. Press Ctrl+C to stop."
  wait
}

main "$@"
