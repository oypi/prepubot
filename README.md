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

## Prerequisites

1. **Linux Kernel Permissions**:
   The bot creates a virtual gamepad using `/dev/uinput` and reads process memory from `/proc/<pid>/mem`. Ensure your user has access:
   ```bash
   sudo usermod -aG input $USER
   echo 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/99-uinput.rules
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```
   *(Alternatively, run `sudo chmod 666 /dev/uinput` for temporary access).*

2. **Python Dependencies**:
   ```bash
   pip install torch numpy evdev
   ```

3. **Rust Toolchain** (for compiling GUI):
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

---

## Building and Running

### 1. Build the Rust GUI
```bash
cd nexto_gui
cargo build --release
```

### 2. Launch PrepuBot
Make sure Rocket League is running (in Freeplay, Custom Match, or Exhibition), then run:
```bash
./nexto_gui/target/release/prepubot
```

### 3. Usage & Hotkeys
- **F6**: Global toggle hotkey. Press **F6** at any time while playing to instantly activate or deactivate the bot.
- **Overlay HUD**: Displays real-time memory telemetry and live controller actions.
- **Input Emulation**: Pure native Microsoft Xbox 360 gamepad emulation via `/dev/uinput`.

### 4. Running CLI-only (without GUI)
You can also run the bot directly in terminal mode:
```bash
python3 nexto_play.py
```

---

## License

MIT License. Educational and local offline research use only.
