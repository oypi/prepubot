# PrepuBot: Rocket League Autonomous Memory Bot

PrepuBot is a high-performance, out-of-process autonomous bot for Rocket League on Linux. It reads live game telemetry directly from running game process memory, runs real-time inference using state-of-the-art Transformer and Actor-Critic neural network models (**Nexto**, **Seer**, **Element**), and injects inputs through an emulated virtual Xbox 360 gamepad via `/dev/uinput`.

It includes a lightweight desktop overlay and HUD written in Rust with **egui** and global hotkey support (**F6** toggle).

---

## Highlights & Features

- **Direct Memory Telemetry (`read_position.py`)**:
  - **Zero-Latency Auto Offset Recovery**: Instant startup verification of engine globals (`GNames`, `GObjects`) in <0.5ms. If Rocket League updates or recompiles, an automated dynamic memory scanner locates tables in ~1.4s and caches them to disk (`~/.cache/prepubot/offsets.json`), ensuring users never get stuck with a non-functional bot.
  - **PhysX Rigid-Body Simulation State**: Reads player car coordinates, linear velocity, angular velocity, and orientation quaternions atomically from contiguous PhysX simulation structures (`RBState`).
  - **Zero-Footprint Stealth Reading**: Direct Linux kernel reads via `process_vm_readv` syscalls with zero open file descriptors in `/proc/<pid>/fd/` and process name cloaking (`portal-helper`).
  - **Comprehensive Field Physics**: Tracks real-time ball trajectory, active boost pad timers, teammate/opponent positions, and native surface contact flags.
- **Multi-Bot Model Manager (`models_manager.py`)**:
  - Seamless in-game hot-switching between **Nexto**, **Seer**, and **Element**.
  - Direct state translation from memory packets to RLBot GameTickPackets.
  - Configurable policy temperature (`beta`), tactical depth matching, and team inversion.
- **Input Emulation & Air Recovery (`virtual_controller.py`)**:
  - Emulates a hardware-level Microsoft Xbox 360 controller using Linux `/dev/uinput`.
  - Continuous analog triggers for throttle and brake (`RT`/`LT`), left stick for steering, yaw, and pitch.
  - **Balanced 6DOF Air Recovery**: PWM time-sharing between yaw alignment and roll leveling for agile aerial control.
  - **Powerslide Landing Cushioning**: Pre-engages powerslide during descent to preserve 100% forward momentum when landing sideways.
- **Autonomous Play & Kickoffs (`nexto_play.py`)**:
  - Physics-synchronized 120Hz polling with frame-accurate controller input updates.
  - **Overhauled Kickoff Controller**: Mirrored speedflips on diagonal spawns, target-lock homing into ball center, and terminal 50/50 impact dodges.
  - **Window Focus Guard**: Automatically pauses inputs when Rocket League loses focus (supports Niri, Hyprland, Sway, KDE, GNOME, and X11).
  - **In-App Version & Update Detection**: Non-blocking background update check that notifies you with a 1-click update link when a newer PrepuBot release is published on GitHub.
  - Bi-directional JSON IPC protocol for external frontends.
- **Rust Tactical HUD (`nexto_gui/`)**:
  - Cyberpunk dark-mode GUI built with `egui` and `eframe`.
  - Live HUD displaying car speed, boost percentage, ball distance, current action, teammate/opponent status, and FPS.
  - Global background hotkey thread listening for **F6** across all raw input devices.
  - Model selector dropdown (Nexto / Seer / Element), window focus guard, and window pin (always on top).

---

## Repository Structure

```
├── models_manager.py      # Multi-model bot manager (Nexto, Seer, Element)
├── nexto_driver.py        # Observation builder and memory state bridge
├── nexto_gui/             # Rust desktop HUD & launcher (eframe, egui)
├── nexto_play.py          # Main bot runner, 120 Hz tick loop, IPC daemon
├── read_position.py       # Unreal Engine memory scanner & auto offset recovery
├── virtual_controller.py  # evdev /dev/uinput Xbox 360 virtual controller
├── version.txt            # Release version manifest
├── RLMarlbot/             # Bot models, neural weights, and Linux SDK adapters
└── build_standalone.sh    # Script to bundle everything into a single binary
```

---

## Quick Start (Standalone Single-Binary)

If you use the standalone executable, **you do NOT need Python, PyTorch, pip, git, or Rust installed**. Everything is embedded into a single portable binary.

### 1. One-Line System Permission Setup
On Linux (Ubuntu, Debian, Fedora, Arch, SteamOS), the bot requires permission to emulate an Xbox gamepad and inspect game memory:

```bash
# 1. Allow gamepad emulation
sudo chmod 666 /dev/uinput

# 2. Allow reading game memory via process_vm_readv / ptrace
echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope

# 3. (Optional) Ensure user is in input group for global F6 hotkey
sudo usermod -aG input $USER
```

### 2. Run PrepuBot
```bash
chmod +x prepubot
./prepubot
```
On first launch, PrepuBot extracts its internal engine into `~/.cache/prepubot/` and automatically connects to Rocket League.

---

## Easy Anti-Cheat (EAC) Compatibility

Rocket League on Linux (via Steam Proton, Heroic Games Launcher, or Lutris) runs Easy Anti-Cheat in userspace inside Wine:

1. **Automatic Process Disambiguation**:
   When launched with EAC, two Rocket League processes exist:
   - `RocketLeague_EAC.exe`: Integrity monitor bootstrap
   - `RocketLeague.exe`: The actual Unreal Engine 3 game process

   PrepuBot automatically detects and attaches exclusively to `RocketLeague.exe`, completely ignoring `RocketLeague_EAC.exe`.

2. **Dual Operating Modes**:
   - **Default EAC Mode**: PrepuBot functions out-of-process via Linux kernel memory reading.
   - **Offline Mode (`-noeac`)**: For zero-risk offline play in Freeplay and custom training, you can add `-noeac` to Rocket League's launch arguments in Steam or Heroic. PrepuBot seamlessly connects to both modes.

---

## Building the Standalone Binary

To produce the single-file distribution binary (`dist/prepubot`):

```bash
./build_standalone.sh
```

This automated script bundles PyTorch CPU, NumPy, evdev, all bot neural network weights (Nexto, Seer, Element), and the Cyberpunk HUD GUI into `dist/prepubot` (~523 MB).

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
