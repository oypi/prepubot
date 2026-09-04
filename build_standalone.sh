#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# PrepuBot Standalone Single-Binary Builder
# Packages the entire system (Rust GUI + PyTorch + Nexto Model + Drivers)
# into a single executable: dist/prepubot
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================"
echo "          Building PrepuBot Single-Binary Standalone            "
echo "================================================================"

BUILD_VENV="/tmp/test_pyinstaller2"
EMBEDDED_DIR="$SCRIPT_DIR/nexto_gui/embedded_backend"
DIST_DIR="$SCRIPT_DIR/dist"

mkdir -p "$EMBEDDED_DIR"
mkdir -p "$DIST_DIR"

# 1. Ensure builder Python virtual environment exists with dependencies
if [ ! -f "$BUILD_VENV/bin/pyinstaller" ]; then
    echo "[1/4] Setting up build Python virtual environment at $BUILD_VENV..."
    python3 -m venv "$BUILD_VENV"
    "$BUILD_VENV/bin/pip" install --upgrade pip
    "$BUILD_VENV/bin/pip" install torch numpy evdev pyinstaller
else
    echo "[1/4] Reusing existing build virtual environment at $BUILD_VENV"
fi

# 2. Compile standalone Python backend with PyInstaller
echo "[2/4] Bundling Nexto Python backend into standalone executable..."
"$BUILD_VENV/bin/pyinstaller" \
    --onefile "$SCRIPT_DIR/nexto_play.py" \
    --name nexto_backend \
    --distpath "$EMBEDDED_DIR" \
    --workpath /tmp/build_prepubot \
    --add-data "$SCRIPT_DIR/nexto:nexto" \
    --exclude-module matplotlib \
    --exclude-module scipy \
    --exclude-module pandas \
    --exclude-module sympy \
    --exclude-module PIL \
    --exclude-module tkinter \
    --exclude-module sqlite3 \
    --exclude-module pygments \
    --exclude-module IPython \
    --exclude-module pytest \
    -y

chmod +x "$EMBEDDED_DIR/nexto_backend"
echo " Backend binary ready: $(ls -lh "$EMBEDDED_DIR/nexto_backend" | awk '{print $5}')"

# 3. Build single Rust executable containing embedded backend
echo "[3/4] Compiling Rust Tactical GUI with embedded backend payload..."
cargo build --release --manifest-path "$SCRIPT_DIR/nexto_gui/Cargo.toml"

# 4. Copy to dist
echo "[4/4] Finalizing single binary in $DIST_DIR/prepubot..."
cp "$SCRIPT_DIR/nexto_gui/target/release/prepubot" "$DIST_DIR/prepubot"
chmod +x "$DIST_DIR/prepubot"

echo ""
echo "================================================================"
echo " SUCCESS! Standalone executable generated at:"
echo "   $DIST_DIR/prepubot"
echo " Size: $(ls -lh "$DIST_DIR/prepubot" | awk '{print $5}')"
echo "================================================================"
echo ""
echo "How your friends can run it on Linux (Ubuntu, Debian, Fedora, Arch, SteamOS):"
echo "  1. Copy 'prepubot' to their machine"
echo "  2. Ensure permissions:"
echo "       sudo chmod 666 /dev/uinput"
echo "       echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope"
echo "       sudo usermod -aG input \$USER   # (only needed if hotkey F6 is restricted)"
echo "  3. Run:"
echo "       ./prepubot"
echo ""
echo "No Python, no PyTorch, no pip, and no git required!"
echo "================================================================"
