#!/usr/bin/env bash
# =============================================================================
#  run_pump_ai.sh  —  PumpAI Daily Startup Automation (macOS / Linux)
#  Usage:  ./run_pump_ai.sh
#  Effect: Boots the FastAPI backend + static frontend server, opens browser.
#          Closing this terminal (or pressing Ctrl+C) kills BOTH processes.
# =============================================================================

set -euo pipefail

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log_info()    { echo -e "${CYAN}[PumpAI]${RESET}  $*"; }
log_ok()      { echo -e "${GREEN}[  OK  ]${RESET}  $*"; }
log_warn()    { echo -e "${YELLOW}[ WARN ]${RESET}  $*"; }
log_error()   { echo -e "${RED}[ERROR ]${RESET}  $*"; }
log_section() { echo -e "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; echo -e "  ${BOLD}$*${RESET}"; echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }

# ── Constants ────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
BACKEND_HOST="0.0.0.0"
BACKEND_PORT="8000"
FRONTEND_HOST="127.0.0.1"
FRONTEND_PORT="3000"
BROWSER_URL="http://127.0.0.1:${FRONTEND_PORT}"
BACKEND_PID=""
FRONTEND_PID=""

# ── Detect LAN IP (for QR / mobile access info) ─────────────────────────────
LAN_IP=$(python3 -c "
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    s.connect(('8.8.8.8', 80))
    print(s.getsockname()[0])
    s.close()
except Exception:
    print('127.0.0.1')
" 2>/dev/null || echo "127.0.0.1")

# ── Clean shutdown handler ───────────────────────────────────────────────────
cleanup() {
    log_section "🛑  Shutdown Signal Received — Stopping All Services"

    if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        log_info "Killing frontend server   (PID $FRONTEND_PID)..."
        kill -TERM "$FRONTEND_PID" 2>/dev/null
        # give it 2 s to die gracefully, then force
        sleep 2
        kill -KILL "$FRONTEND_PID" 2>/dev/null || true
        log_ok "Frontend stopped."
    fi

    if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        log_info "Killing backend FastAPI    (PID $BACKEND_PID) and reload children..."
        # uvicorn --reload spawns worker children — kill the whole process group
        kill -TERM -"$BACKEND_PID" 2>/dev/null || kill -TERM "$BACKEND_PID" 2>/dev/null
        sleep 2
        kill -KILL "$BACKEND_PID" 2>/dev/null || true
        log_ok "Backend stopped."
    fi

    # Belt-and-braces: release ports by name in case PIDs drifted
    if command -v fuser &>/dev/null; then
        fuser -k "${BACKEND_PORT}/tcp"  2>/dev/null || true
        fuser -k "${FRONTEND_PORT}/tcp" 2>/dev/null || true
    fi

    echo ""
    log_ok "All PumpAI processes terminated cleanly. Goodbye."
    exit 0
}

# Traps are not registered because the script exits immediately after launching the tray app.

# ============================================================================
log_section "🚀  PumpAI Startup Automation — $(date '+%d %b %Y  %H:%M:%S')"
# ============================================================================

# ── STEP 1 : Python environment verification ─────────────────────────────────
log_section "STEP 1 — Verifying Python Environment"

cd "$BACKEND_DIR"

# Locate Python 3 (prefer venv, fallback to system)
PYTHON_BIN=""
VENV_ACTIVATE=""

if [[ -f "$BACKEND_DIR/venv/bin/activate" ]]; then
    log_info "Virtual environment found → activating venv..."
    # shellcheck disable=SC1091
    source "$BACKEND_DIR/venv/bin/activate"
    PYTHON_BIN="$(which python3)"
    VENV_ACTIVATE="active"
    log_ok "venv activated: $PYTHON_BIN"
elif [[ -f "$BACKEND_DIR/.venv/bin/activate" ]]; then
    log_info "Virtual environment found at .venv → activating..."
    source "$BACKEND_DIR/.venv/bin/activate"
    PYTHON_BIN="$(which python3)"
    VENV_ACTIVATE="active"
    log_ok ".venv activated: $PYTHON_BIN"
else
    log_warn "No virtual environment found. Checking system Python..."
    if command -v python3 &>/dev/null; then
        PYTHON_BIN="$(which python3)"
        log_ok "System Python located: $PYTHON_BIN"
        log_warn "Consider creating a venv:  cd backend && python3 -m venv venv"
    else
        log_error "Python 3 not found. Please install Python 3.9+ and try again."
        exit 1
    fi
fi

PYTHON_VERSION=$("$PYTHON_BIN" --version 2>&1)
log_ok "Runtime: $PYTHON_VERSION"

# ── STEP 2 : Install / sync requirements.txt ────────────────────────────────
log_section "STEP 2 — Installing / Verifying Python Dependencies"

REQ_FILE="$BACKEND_DIR/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
    log_info "Running: pip install -r requirements.txt ..."
    "$PYTHON_BIN" -m pip install -r "$REQ_FILE" --quiet --disable-pip-version-check
    log_ok "All requirements satisfied."
else
    log_warn "requirements.txt not found at $REQ_FILE — skipping pip install."
fi

# Verify uvicorn is reachable
if ! "$PYTHON_BIN" -m uvicorn --version &>/dev/null; then
    log_error "uvicorn is not installed or not on PATH."
    log_info  "Run:  pip install uvicorn  and try again."
    exit 1
fi
log_ok "uvicorn: $("$PYTHON_BIN" -m uvicorn --version)"

# ── STEP 3 : Launch PumpAI System Tray Application ──────────────────────────
log_section "STEP 3 — Launching PumpAI in System Tray"

cd "$BACKEND_DIR"
log_info "Starting tray_app.py in background..."
nohup "$PYTHON_BIN" "$BACKEND_DIR/tray_app.py" > /dev/null 2>&1 &

log_ok "PumpAI has been launched in the system tray."
log_ok "You can open the workspace, trigger backups, or view active logs via the system tray icon."
log_section "✅  PumpAI — Background Services Initialized"
exit 0
