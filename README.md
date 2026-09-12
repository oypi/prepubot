# PrepuBot: Rocket League Autonomous Memory Bot

<img width="600" height="338" alt="gameplay_test3" src="https://github.com/user-attachments/assets/365cfef5-52c5-4a24-831f-781682529efb" />

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

## Quick Start (One-Click Launcher)

Running PrepuBot is completely automated. You do not need to manually configure virtual environments or package managers.

### 1. Run PrepuBot
```bash
./run.sh
```

`run.sh` handles everything automatically:
- Checks and sets up Linux device permissions (`/dev/uinput` and `yama/ptrace_scope`).
- Automatically creates a local `.venv` and installs the required Python dependencies (PyTorch CPU, RLBot, evdev, numpy).
- Compiles/launches the Tactical GUI HUD.

### 2. Console Mode (Headless / No GUI)
If you prefer running in a terminal without the GUI window:
```bash
./run.sh --cli
```

---

## System Permissions & F6 Hotkey

The bot requires permission to emulate an Xbox gamepad and read game memory:

```bash
# Allow gamepad emulation
sudo chmod 666 /dev/uinput

# Allow reading game memory via process_vm_readv / ptrace
echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope

# Add user to input group for global F6 toggle (log out and back in after this)
sudo usermod -aG input $USER
```

> [!IMPORTANT]
> **F6 Hotkey Requirement**: The global **F6** key listens across all raw keyboard event devices (`/dev/input/event*`). After running `sudo usermod -aG input $USER`, you **must log out and log back in** (or reboot) for the input group permissions to apply.

---

## In-Game Settings & Controls Configuration

To achieve peak neural performance, configure your Rocket League settings as follows:

#### Controller Bindings
The emulated virtual Xbox 360 controller outputs standard XInput controls:
- **Throttle / Forward**: Right Trigger (`RT`)
- **Brake / Reverse**: Left Trigger (`LT`)
- **Steer / Pitch / Yaw**: Left Analog Stick
- **Jump**: `A` Button
- **Boost**: `B` Button
- **Powerslide & Air Roll**: `X` Button

#### Sensitivity & Deadzones (CRITICAL)
In Rocket League, open **Settings -> Controls**:
- **Controller Deadzone**: Set to **minimum** (`0.05` or lowest stable value).
- **Steering Sensitivity**: Set to **minimum** (`1.00`).
- **Aerial Sensitivity**: Set to **minimum** (`1.00`).
- **Dodge Deadzone**: Default (`0.50` – `0.70`).

> [!TIP]
> **Why minimum sensitivity and deadzones?**
> The neural policy models (**Nexto**, **Seer**, **Element**) calculate exact, linear floating-point stick deflections in `[-1.0, 1.0]`. If you have high sensitivity multipliers or large deadzones set in Rocket League, the game distorts the bot's calculated trajectory, causing jittery steering, over-correction, or inaccurate aerial touches.

---

## Usage & Controls

- **F6**: Global toggle hotkey. Press **F6** at any time while playing to instantly engage or disengage autonomous driving.
- **PIN Button**: Pins the HUD window to stay always on top of Rocket League.
- **GUARD Button**: Focus guard. Pauses bot actions when Rocket League window is unfocused (Alt-Tabbed).
- **HUD Telemetry**: Displays real-time speed, boost percentage, ball distance, action state, and teammate/opponent markers.

---

## License

MIT License. Educational and local offline research use only.
