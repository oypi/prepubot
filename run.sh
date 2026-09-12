#!/usr/bin/env bash
# ==============================================================================
# PrepuBot - Autonomous Rocket League Bot Engine
# One-click launcher for Linux
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "       PrepuBot - Rocket League Bot Engine       "
echo "=================================================="

# 1. System permissions check (/dev/uinput and ptrace_scope)
NEED_SUDO=0

# Skip permission prompts if only requesting help
if [[ ! " $* " =~ " --help " ]] && [[ ! " $* " =~ " -h " ]]; then
    if [ ! -w /dev/uinput ]; then
        echo "[!] /dev/uinput is not writable (required for virtual controller)."
        NEED_SUDO=1
    fi
    PTRACE_SCOPE=$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo "1")
    if [ "$PTRACE_SCOPE" != "0" ]; then
        echo "[!] ptrace_scope is $PTRACE_SCOPE (required to read Rocket League process memory)."
        NEED_SUDO=1
    fi
fi

if [ "$NEED_SUDO" -eq 1 ]; then
    if [ -t 0 ]; then
        echo "[*] Configuring required system permissions (sudo required)..."
        if [ ! -w /dev/uinput ]; then
            sudo chmod 666 /dev/uinput
        fi
        if [ "$PTRACE_SCOPE" != "0" ]; then
            echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope >/dev/null
        fi
        echo "[✓] System permissions configured."
    else
        echo "[!] NOTICE: System permissions may need manual setup:"
        if [ ! -w /dev/uinput ]; then
            echo "    sudo chmod 666 /dev/uinput"
        fi
        if [ "$PTRACE_SCOPE" != "0" ]; then
            echo "    echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope"
        fi
    fi
fi

# 2. Python environment check & setup
PYTHON_CMD="python3"
if ! command -v "$PYTHON_CMD" &>/dev/null; then
    echo "[ERROR] python3 is not installed on this system. Please install python3."
    exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python3"

HAS_DEPS=0
if [ -f "$VENV_PY" ]; then
    if "$VENV_PY" -c "import torch, rlbot, evdev, numpy" &>/dev/null; then
        HAS_DEPS=1
    fi
elif "$PYTHON_CMD" -c "import torch, rlbot, evdev, numpy" &>/dev/null; then
    VENV_PY="$PYTHON_CMD"
    HAS_DEPS=1
fi

if [ "$HAS_DEPS" -eq 0 ]; then
    echo "[*] Setting up Python virtual environment in .venv (one-time setup)..."
    if [ ! -d "$VENV_DIR" ]; then
        "$PYTHON_CMD" -m venv "$VENV_DIR"
    fi
    echo "[*] Installing Python dependencies (PyTorch CPU, RLBot, etc.)..."
    "$VENV_DIR/bin/pip" install --upgrade pip --quiet
    "$VENV_DIR/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu --quiet
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet
    VENV_PY="$VENV_DIR/bin/python3"
    echo "[✓] Python dependencies installed successfully."
fi

# 3. GUI binary preparation
GUI_BIN="$SCRIPT_DIR/prepubot"

if [ ! -f "$GUI_BIN" ]; then
    if [ -f "$SCRIPT_DIR/nexto_gui/target/release/prepubot" ]; then
        cp "$SCRIPT_DIR/nexto_gui/target/release/prepubot" "$GUI_BIN"
    elif command -v cargo &>/dev/null; then
        echo "[*] Compiling PrepuBot Tactical GUI..."
        cargo build --release --manifest-path "$SCRIPT_DIR/nexto_gui/Cargo.toml"
        cp "$SCRIPT_DIR/nexto_gui/target/release/prepubot" "$GUI_BIN"
    fi
fi

# 4. Launch Application
export NEXTO_SCRIPT_PATH="$SCRIPT_DIR/nexto_play.py"

# Support --cli / --no-gui flags
for arg in "$@"; do
    if [ "$arg" == "--cli" ] || [ "$arg" == "--no-gui" ]; then
        echo "[*] Launching PrepuBot in Console Mode..."
        exec "$VENV_PY" "$SCRIPT_DIR/nexto_play.py" "$@"
    fi
done

if [ -x "$GUI_BIN" ]; then
    echo "[*] Launching PrepuBot Tactical GUI..."
    exec "$GUI_BIN" "$@"
else
    echo "[*] GUI binary not available. Launching in Console Mode..."
    exec "$VENV_PY" "$SCRIPT_DIR/nexto_play.py" "$@"
fi
