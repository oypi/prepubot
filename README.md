# PrepuBot: Rocket League Autonomous Memory Bot

PrepuBot is a high-performance, out-of-process autonomous bot for Rocket League on Linux. It reads live game telemetry directly from the running game process memory via `/proc/<pid>/mem`, runs inference using the **Nexto** Transformer neural network model, and injects inputs through an emulated virtual Xbox 360 gamepad via `/dev/uinput`.

It includes a lightweight desktop overlay and HUD written in Rust with **egui** and global hotkey support (**F6** toggle).

---

## Features

- **Direct Memory Telemetry (`read_position.py`)**:
  - Discovers engine globals (`GNames`, `GObjects`) and traverses Unreal Engine 3 structures.
  - Reads player car coordinates, velocity, angular velocity, and PhysX rotation quaternion (`0x5D0`).
  - Tracks ball physics and dynamically discovers teammate/opponent cars.
  - Ground contact and double jump flags via `Vehicle_TA` flags (`0x7F8`).
- **Nexto Transformer Integration (`nexto_driver.py`)**:
  - Constructs `(q, kv, m)` attention tensors in a self-relative coordinate frame identical to official `NextoObsBuilder`.
  - Full support for 1v1, 2v2, and 3v3 matches, including 180° field inversion for Orange team.
  - Runs Nexto TorchScript model (`nexto/nexto-model.pt`) with configurable temperature (`beta`).
- **Input Emulation (`virtual_controller.py`)**:
  - Emulates a hardware-level Microsoft Xbox 360 controller using Linux `/dev/uinput`.
  - Continuous analog triggers for throttle and brake (`RT`/`LT`), left stick for steering, yaw, and pitch.
  - Directional flip mapping for dodges and aerial maneuvers.
- **Autonomous Play & Kickoffs (`nexto_play.py`)**:
  - Physics-synchronized 120Hz polling with 15 Hz decision intervals (`tick_skip = 8`).
  - Automated kickoff detection: frame-accurate speedflip sequences for diagonal spawns and neural net play for central kickoffs.
  - Window focus guard: automatically pauses inputs when Rocket League loses focus (supports Niri, Hyprland, Sway, KDE, GNOME, and X11).
  - Bi-directional JSON IPC protocol for external frontends.
- **Rust Desktop HUD (`nexto_gui/`)**:
  - Clean cyberpunk dark-mode GUI built with `egui` and `eframe`.
  - Live HUD displaying car speed, boost percentage, ball distance, current action, teammate/opponent status, and FPS.
  - Global background hotkey thread listening for **F6** across all raw input devices.
  - Controls for window focus guard, and window pin (always on top).

---

## Repository Structure

```
├── nexto/                 # Nexto neural network assets
│   ├── nexto-model.pt     # TorchScript model weights
│   ├── agent.py           # Action lookup table and model runner
│   └── nexto_obs.py       # Reference observation builder specification
├── nexto_gui/             # Rust desktop HUD & launcher
│   ├── Cargo.toml         # Rust package manifest (eframe, serde, evdev)
│   └── src/main.rs        # GUI application, telemetry receiver, hotkey listener
├── nexto_driver.py        # Observation builder and memory state bridge
├── nexto_play.py          # Bot runner, decision scheduler, kickoff sequencer, IPC
├── read_position.py       # Unreal Engine memory reflection & scanner
└── virtual_controller.py  # evdev /dev/uinput Xbox 360 virtual controller
```

---

## Quick Start (Standalone Single-Binary for Friends)

If you share the standalone executable with friends, **they do NOT need Python, PyTorch, pip, git, or Rust installed**. Everything is embedded into a single portable binary.

### 1. One-Line System Permission Setup
On Linux (Ubuntu, Debian, Fedora, Arch, SteamOS), the bot requires permission to emulate an Xbox gamepad and inspect game memory:

```bash
# 1. Allow gamepad emulation
sudo chmod 666 /dev/uinput

# 2. Allow reading game memory via /proc/<pid>/mem
echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope

# 3. (Optional) Ensure user is in input group for global F6 hotkey
sudo usermod -aG input $USER
```

### 2. Run PrepuBot
```bash
chmod +x prepubot
./prepubot
```
On first launch, PrepuBot automatically extracts its internal Nexto engine into `~/.cache/prepubot/` and connects to Rocket League.

---

## Easy Anti-Cheat (EAC) Compatibility

Rocket League on Linux (via Steam Proton, Heroic Games Launcher, or Lutris) runs Easy Anti-Cheat in userspace inside Wine:

1. **Automatic Process Disambiguation**:
   When launched with EAC, two Rocket League processes exist:
   - `RocketLeague_EAC.exe`: Integrity monitor bootstrap
   - `RocketLeague.exe`: The actual Unreal Engine 3 game process

   PrepuBot automatically detects and attaches exclusively to `RocketLeague.exe`, completely ignoring `RocketLeague_EAC.exe`.

2. **Dual Operating Modes**:
   - **Default EAC Mode**: PrepuBot functions out-of-process via Linux `/proc/<pid>/mem` reading.
   - **Offline Mode (`-noeac`)**: For zero-risk offline play in Freeplay and custom training, you can add `-noeac` to Rocket League's launch arguments in Steam or Heroic. PrepuBot seamlessly connects to both modes.

---

## Building the Standalone Binary

To produce the single-file distribution binary (`dist/prepubot`):

```bash
./build_standalone.sh
```

This automated script bundles PyTorch CPU, NumPy, evdev, the Nexto Transformer weights, and the Cyberpunk HUD GUI into `dist/prepubot` (~388 MB).

---

## Building from Source (Developer Mode)

### Prerequisites
- Python 3.10+ with `torch`, `numpy`, `evdev`
- Rust toolchain (`cargo`, `rustc`)

### 1. Build GUI
```bash
cd nexto_gui
cargo build --release
```

### 2. Run
```bash
./nexto_gui/target/release/prepubot
```

---

## Usage & Controls

- **F6**: Global toggle hotkey. Press **F6** at any time while playing to instantly engage or disengage autonomous driving.
- **PIN Button**: Pins the HUD window to stay always on top of Rocket League.
- **GUARD Button**: Focus guard. Pauses bot actions when Rocket League window is unfocused (Alt-Tabbed).
- **HUD Telemetry**: Displays real-time speed, boost percentage, ball distance, action state, and teammate/opponent markers.

---

## License

MIT License. Educational and local offline research use only.

