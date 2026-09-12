use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

static LAST_F6_PRESS_MS: AtomicU64 = AtomicU64::new(0);

fn should_trigger_f6() -> bool {
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    let last = LAST_F6_PRESS_MS.load(Ordering::Relaxed);
    if now_ms.saturating_sub(last) < 350 {
        return false;
    }
    LAST_F6_PRESS_MS.store(now_ms, Ordering::Relaxed);
    true
}

use eframe::egui;
use serde::{Deserialize, Serialize};

core::arch::global_asm!(concat!(
    r#"
    .section .rodata.embedded_backend,"a",@progbits
    .globl _embedded_backend_start
    .globl _embedded_backend_end
_embedded_backend_start:
    .incbin ""#,
    env!("CARGO_MANIFEST_DIR"),
    r#"/embedded_backend/backend.tar.gz"
_embedded_backend_end:
"#
));

unsafe extern "C" {
    static _embedded_backend_start: u8;
    static _embedded_backend_end: u8;
}

fn get_embedded_backend() -> &'static [u8] {
    unsafe {
        let start = std::ptr::addr_of!(_embedded_backend_start);
        let end = std::ptr::addr_of!(_embedded_backend_end);
        let len = (end as usize).saturating_sub(start as usize);
        std::slice::from_raw_parts(start, len)
    }
}

fn parse_version(v: &str) -> Vec<u32> {
    v.trim()
        .trim_start_matches('v')
        .split('.')
        .filter_map(|s| {
            let digits: String = s.chars().filter(|c| c.is_ascii_digit()).collect();
            digits.parse::<u32>().ok()
        })
        .collect()
}

fn is_newer(remote: &str, current: &str) -> bool {
    let r = parse_version(remote);
    let c = parse_version(current);
    if r.is_empty() || c.is_empty() {
        return false;
    }
    r > c
}

fn find_dev_script(current_exe: &std::path::Path) -> Option<PathBuf> {
    if let Ok(path) = std::env::var("NEXTO_SCRIPT_PATH") {
        let p = PathBuf::from(path);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        let p = cwd.join("nexto_play.py");
        if p.exists() {
            return Some(p);
        }
    }
    // Check executable directory and up to 4 parent levels (e.g. target/release/ -> ../../../nexto_play.py)
    let mut cur = current_exe.parent();
    for _ in 0..4 {
        if let Some(dir) = cur {
            let p = dir.join("nexto_play.py");
            if p.exists() {
                return Some(p);
            }
            cur = dir.parent();
        } else {
            break;
        }
    }
    None
}

fn extract_backend_if_needed(state: Option<&Arc<Mutex<SharedState>>>) -> Result<PathBuf, String> {
    // 1. Developer mode: If running via `cargo run` (binary inside target/) or PREPUBOT_DEV is set,
    // prefer running nexto_play.py directly with system python3 for instant code changes.
    let current_exe = std::env::current_exe().unwrap_or_default();
    let is_cargo_dev = current_exe.to_string_lossy().contains("/target/") || std::env::var("PREPUBOT_DEV").is_ok();

    if is_cargo_dev {
        if let Some(p) = find_dev_script(&current_exe) {
            return Ok(p);
        }
    }

    let base_dir = if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home).join(".cache").join("prepubot")
    } else {
        std::env::temp_dir().join("prepubot")
    };

    let backend_dir = base_dir.join("backend");
    let backend_exe = backend_dir.join("nexto_backend").join("nexto_backend");
    let stamp_file = backend_dir.join(".version_stamp");
    let embedded = get_embedded_backend();

    // If embedded backend exists in this binary
    if !embedded.is_empty() {
        // Fast FNV-1a hash over sampled slices + size to detect rebuilds
        let mut hasher: u64 = 0xcbf29ce484222325;
        hasher ^= embedded.len() as u64;
        hasher = hasher.wrapping_mul(0x100000001b3);
        let sample_step = (embedded.len() / 512).max(1);
        for chunk in embedded.chunks(sample_step) {
            if let Some(&b) = chunk.first() {
                hasher ^= b as u64;
                hasher = hasher.wrapping_mul(0x100000001b3);
            }
        }

        let expected_stamp = format!("{}_{:016x}_{}", env!("CARGO_PKG_VERSION"), hasher, embedded.len());
        let current_stamp = std::fs::read_to_string(&stamp_file).unwrap_or_default();

        // If pre-extracted directory exists and matches version stamp, launch INSTANTLY with 0 extraction!
        if backend_exe.is_file() && current_stamp == expected_stamp {
            return Ok(backend_exe);
        }

        if let Some(s_arc) = state {
            let mut s = s_arc.lock().unwrap();
            s.status_msg = "Unpacking Nexto Engine (One-Time Setup)...".to_string();
        }

        let tmp_dir = base_dir.join("backend_extracting");
        let _ = std::fs::remove_dir_all(&tmp_dir);
        std::fs::create_dir_all(&tmp_dir)
            .map_err(|e| format!("Failed to create extraction dir {:?}: {}", tmp_dir, e))?;

        let mut child = Command::new("tar")
            .arg("-xzf")
            .arg("-")
            .arg("-C")
            .arg(&tmp_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to execute tar: {}", e))?;

        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(embedded);
        }

        let output = child.wait_with_output().map_err(|e| format!("tar failed: {}", e))?;
        if !output.status.success() {
            let _ = std::fs::remove_dir_all(&tmp_dir);
            let err = String::from_utf8_lossy(&output.stderr);
            return Err(format!("tar extraction failed: {}", err));
        }

        let tmp_exe = tmp_dir.join("nexto_backend").join("nexto_backend");
        if let Ok(m) = std::fs::metadata(&tmp_exe) {
            let mut p = m.permissions();
            p.set_mode(0o755);
            let _ = std::fs::set_permissions(&tmp_exe, p);
        }

        let _ = std::fs::write(tmp_dir.join(".version_stamp"), expected_stamp);

        // Atomic swap into permanent backend_dir
        let _ = std::fs::remove_dir_all(&backend_dir);
        std::fs::rename(&tmp_dir, &backend_dir)
            .map_err(|e| format!("Failed to finalize backend dir: {}", e))?;

        if backend_exe.is_file() {
            return Ok(backend_exe);
        }
        return Err(format!("Extracted backend binary not found at {:?}", backend_exe));
    }

    // Developer fallback: check nexto_play.py dynamically
    if let Some(p) = find_dev_script(&current_exe) {
        return Ok(p);
    }

    Err("Could not find nexto_backend executable or nexto_play.py".to_string())
}


#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CarTelemetry {
    #[serde(default)]
    pub pos: Vec<f32>,
    #[serde(default)]
    pub spd: f32,
    #[serde(default)]
    pub boost: f32,
    #[serde(default)]
    pub on_ground: bool,
    #[serde(default)]
    pub has_flip: bool,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct BallTelemetry {
    #[serde(default)]
    pub pos: Vec<f32>,
    #[serde(default)]
    pub dist: f32,
    #[serde(default)]
    pub spd: f32,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct EnemyTelemetry {
    #[serde(default)]
    pub pos: Vec<f32>,
    #[serde(default)]
    pub spd: f32,
    #[serde(default)]
    pub boost: f32,
    #[serde(default)]
    pub dist: f32,
    #[serde(default)]
    pub ball_dist: f32,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct TelemetryMsg {
    #[serde(rename = "type")]
    pub msg_type: String,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub team: i32,
    #[serde(default)]
    pub focus_guard: bool,
    #[serde(default)]
    pub fps: f32,
    #[serde(default)]
    pub car: CarTelemetry,
    #[serde(default)]
    pub ball: BallTelemetry,
    #[serde(default)]
    pub teammate: Option<EnemyTelemetry>,
    #[serde(default)]
    pub enemy: Option<EnemyTelemetry>,
    #[serde(default)]
    pub input_mode: String,
    #[serde(default)]
    pub action: String,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
}

pub struct SharedState {
    pub connected: bool,
    pub in_menu: bool,
    pub active: bool,
    pub team: i32,
    pub input_mode: String,
    pub focus_guard: bool,
    pub fps: f32,
    pub car_pos: [f32; 3],
    pub car_spd: f32,
    pub car_boost: f32,
    pub car_on_ground: bool,
    pub car_has_flip: bool,
    pub ball_pos: [f32; 3],
    pub ball_dist: f32,
    pub ball_spd: f32,
    pub teammate: Option<EnemyTelemetry>,
    pub enemy: Option<EnemyTelemetry>,
    pub action: String,
    pub status_msg: String,
    pub permission_alert: Option<String>,
    pub update_available: Option<(String, String)>,
    pub beta: f32,
}

impl Default for SharedState {
    fn default() -> Self {
        Self {
            connected: false,
            in_menu: false,
            active: false,
            team: 0,
            input_mode: "GAMEPAD".to_string(),
            focus_guard: false,
            fps: 0.0,
            car_pos: [0.0; 3],
            car_spd: 0.0,
            car_boost: 0.0,
            car_on_ground: false,
            car_has_flip: false,
            ball_pos: [0.0; 3],
            ball_dist: 0.0,
            ball_spd: 0.0,
            teammate: None,
            enemy: None,
            action: "IDLE".to_string(),
            status_msg: "Connecting to Rocket League...".to_string(),
            permission_alert: None,
            update_available: None,
            beta: 1.0,
        }
    }
}

pub struct PrepuBotApp {
    state: Arc<Mutex<SharedState>>,
    child_stdin: Arc<Mutex<Option<ChildStdin>>>,
    always_on_top: bool,
}

impl PrepuBotApp {
    pub fn new(_cc: &eframe::CreationContext<'_>) -> Self {
        let state = Arc::new(Mutex::new(SharedState::default()));
        let child_stdin = Arc::new(Mutex::new(None));

        let app = Self {
            state: state.clone(),
            child_stdin: child_stdin.clone(),
            always_on_top: true,
        };

        app.spawn_backend();
        Self::spawn_hotkey_thread(child_stdin.clone());

        app
    }

    fn spawn_backend(&self) {
        let state = self.state.clone();
        let stdin_holder = self.child_stdin.clone();

        thread::spawn(move || {
            let backend_target = match extract_backend_if_needed(Some(&state)) {
                Ok(p) => p,
                Err(e) => {
                    let mut s = state.lock().unwrap();
                    s.status_msg = format!("Setup error: {}", e);
                    s.permission_alert = Some(e);
                    return;
                }
            };

            let is_python = backend_target.extension().map_or(false, |ext| ext == "py");

            loop {
                {
                    let mut s = state.lock().unwrap();
                    if s.permission_alert.is_none() {
                        s.status_msg = "Connecting to Rocket League...".to_string();
                    }
                    s.connected = false;
                    s.in_menu = false;
                    s.active = false;
                }

                let mut cmd = if is_python {
                    let mut c = Command::new("python3");
                    c.arg(&backend_target);
                    c
                } else {
                    Command::new(&backend_target)
                };

                cmd.arg("--ipc")
                    .arg("--current-version")
                    .arg(env!("CARGO_PKG_VERSION"))
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null());

                match cmd.spawn() {
                    Ok(mut child) => {
                        let stdout = child.stdout.take().unwrap();
                        let stdin = child.stdin.take().unwrap();
                        {
                            let mut holder = stdin_holder.lock().unwrap();
                            *holder = Some(stdin);
                        }

                        let reader = BufReader::new(stdout);
                        for line in reader.lines() {
                            if let Ok(l) = line {
                                if let Ok(telemetry) = serde_json::from_str::<TelemetryMsg>(&l) {
                                    let mut s = state.lock().unwrap();
                                    if telemetry.msg_type == "status" {
                                        if telemetry.state == "IN_MENU" {
                                            s.connected = true;
                                            s.in_menu = true;
                                            s.active = telemetry.active;
                                            s.status_msg = telemetry.message.unwrap_or_else(|| "In Main Menu".to_string());
                                            s.permission_alert = None;
                                        } else {
                                            s.status_msg = telemetry.message.unwrap_or_default();
                                        }
                                    } else if telemetry.msg_type == "ready" {
                                        s.connected = true;
                                        s.in_menu = false;
                                        s.status_msg = "Memory Attached".to_string();
                                        s.permission_alert = None;
                                    } else if telemetry.msg_type == "telemetry" {
                                        s.connected = true;
                                        s.in_menu = telemetry.state == "IN_MENU" || telemetry.action == "IN MENU";
                                        s.permission_alert = None;
                                        s.active = telemetry.active;
                                        s.focus_guard = telemetry.focus_guard;
                                        s.fps = telemetry.fps;
                                        if telemetry.car.pos.len() >= 3 {
                                            s.car_pos = [telemetry.car.pos[0], telemetry.car.pos[1], telemetry.car.pos[2]];
                                        }
                                        s.car_spd = telemetry.car.spd;
                                        s.car_boost = telemetry.car.boost;
                                        s.car_on_ground = telemetry.car.on_ground;
                                        s.car_has_flip = telemetry.car.has_flip;

                                        if telemetry.ball.pos.len() >= 3 {
                                            s.ball_pos = [telemetry.ball.pos[0], telemetry.ball.pos[1], telemetry.ball.pos[2]];
                                        }
                                        s.ball_dist = telemetry.ball.dist;
                                        s.ball_spd = telemetry.ball.spd;

                                        s.team = telemetry.team;
                                        s.teammate = telemetry.teammate;
                                        s.enemy = telemetry.enemy;
                                        if !telemetry.input_mode.is_empty() {
                                            s.input_mode = telemetry.input_mode;
                                        }

                                        s.action = telemetry.action;
                                        s.status_msg = if s.in_menu {
                                            "In Main Menu".to_string()
                                        } else if s.active {
                                            "Autonomous Running".to_string()
                                        } else {
                                            "Manual Control".to_string()
                                        };
                                    } else if telemetry.msg_type == "error" {
                                        s.connected = false;
                                        s.in_menu = false;
                                        let raw_msg = telemetry.message.unwrap_or_else(|| "Error".to_string());
                                        if raw_msg == "YAMA_PTRACE_DENIED" {
                                            s.status_msg = "Yama ptrace blocked".to_string();
                                            s.permission_alert = Some("PTRACE PERMISSION: Run 'echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope' or run with sudo".to_string());
                                        } else if raw_msg == "UINPUT_DENIED" {
                                            s.status_msg = "UInput permission denied".to_string();
                                            s.permission_alert = Some("UINPUT PERMISSION: Run 'sudo chmod 666 /dev/uinput'".to_string());
                                        } else {
                                            s.status_msg = raw_msg;
                                        }
                                    } else if telemetry.msg_type == "update_available" {
                                        let ver = telemetry.version.unwrap_or_else(|| "latest".to_string());
                                        let url = telemetry.url.unwrap_or_else(|| "https://github.com/oypi/prepubot".to_string());
                                        if is_newer(&ver, env!("CARGO_PKG_VERSION")) {
                                            s.update_available = Some((ver, url));
                                        } else {
                                            s.update_available = None;
                                        }
                                    }
                                }
                            } else {
                                break;
                            }
                        }

                        let _ = child.wait();
                        {
                            let mut holder = stdin_holder.lock().unwrap();
                            *holder = None;
                        }
                    }
                    Err(e) => {
                        let mut s = state.lock().unwrap();
                        s.status_msg = format!("Failed to spawn backend: {}", e);
                    }
                }

                thread::sleep(Duration::from_secs(2));
            }
        });
    }

    fn spawn_hotkey_thread(child_stdin: Arc<Mutex<Option<ChildStdin>>>) {
        thread::spawn(move || {
            let mut devices = Vec::new();
            if let Ok(entries) = std::fs::read_dir("/dev/input") {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                        if name.starts_with("event") {
                            if let Ok(dev) = evdev::Device::open(&path) {
                                if dev.supported_keys().map_or(false, |k| k.contains(evdev::KeyCode::KEY_F6)) {
                                    devices.push(dev);
                                }
                            }
                        }
                    }
                }
            }

            if devices.is_empty() {
                eprintln!("[Hotkey] No keyboard with F6 found in /dev/input");
                return;
            }

            println!("[Hotkey] Monitoring {} keyboard device(s) for F6...", devices.len());

            for mut dev in devices {
                let child_stdin = child_stdin.clone();
                thread::spawn(move || {
                    loop {
                        match dev.fetch_events() {
                            Ok(events) => {
                                for ev in events {
                                    if ev.event_type() == evdev::EventType::KEY && ev.code() == evdev::KeyCode::KEY_F6.0 && ev.value() == 1 {
                                        if should_trigger_f6() {
                                            println!("[Hotkey] F6 Pressed! Toggling PrepuBot...");
                                            if let Ok(mut holder) = child_stdin.lock() {
                                                if let Some(ref mut stdin) = *holder {
                                                    let _ = writeln!(stdin, "{{\"cmd\": \"toggle\"}}");
                                                    let _ = stdin.flush();
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                                thread::sleep(Duration::from_millis(15));
                            }
                            Err(_) => {
                                thread::sleep(Duration::from_millis(100));
                            }
                        }
                    }
                });
            }
        });
    }

    fn send_command(&self, cmd: &str) {
        if let Ok(mut holder) = self.child_stdin.lock() {
            if let Some(ref mut stdin) = *holder {
                let _ = writeln!(stdin, "{}", cmd);
                let _ = stdin.flush();
            }
        }
    }
}

impl eframe::App for PrepuBotApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        // High refresh rate for fluid telemetry
        ui.ctx().request_repaint_after(Duration::from_millis(30));

        // Serious monochrome styling tokens
        let mut visuals = egui::Visuals::dark();
        visuals.override_text_color = Some(egui::Color32::from_rgb(240, 240, 242));
        visuals.panel_fill = egui::Color32::from_rgb(13, 14, 16);
        visuals.window_fill = egui::Color32::from_rgb(13, 14, 16);
        visuals.widgets.noninteractive.bg_fill = egui::Color32::from_rgb(19, 20, 24);
        visuals.widgets.noninteractive.bg_stroke = egui::Stroke::new(1.0, egui::Color32::from_rgb(38, 41, 48));
        visuals.widgets.noninteractive.corner_radius = egui::CornerRadius::same(6);
        visuals.widgets.inactive.bg_fill = egui::Color32::from_rgb(24, 25, 30);
        visuals.widgets.inactive.bg_stroke = egui::Stroke::new(1.0, egui::Color32::from_rgb(45, 48, 56));
        visuals.widgets.inactive.corner_radius = egui::CornerRadius::same(6);
        visuals.widgets.hovered.bg_fill = egui::Color32::from_rgb(36, 38, 45);
        visuals.widgets.hovered.bg_stroke = egui::Stroke::new(1.0, egui::Color32::from_rgb(90, 95, 110));
        visuals.widgets.hovered.corner_radius = egui::CornerRadius::same(6);
        visuals.widgets.active.bg_fill = egui::Color32::from_rgb(240, 240, 242);
        visuals.widgets.active.corner_radius = egui::CornerRadius::same(6);
        ui.ctx().set_visuals(visuals);

        // GUI-focused F6 toggle support
        if ui.input(|i| i.key_pressed(egui::Key::F6)) {
            if should_trigger_f6() {
                println!("[GUI Hotkey] F6 Pressed in UI focus! Toggling PrepuBot...");
                self.send_command("{\"cmd\": \"toggle\"}");
            }
        }

        let state_guard = self.state.lock().unwrap();
        let connected = state_guard.connected;
        let in_menu = state_guard.in_menu;
        let active = state_guard.active;
        let team = state_guard.team;
        let focus_guard = state_guard.focus_guard;
        let fps = state_guard.fps;
        let car_pos = state_guard.car_pos;
        let car_spd = state_guard.car_spd;
        let car_boost = state_guard.car_boost;
        let car_on_ground = state_guard.car_on_ground;
        let car_has_flip = state_guard.car_has_flip;
        let ball_pos = state_guard.ball_pos;
        let ball_dist = state_guard.ball_dist;
        let ball_spd = state_guard.ball_spd;
        let teammate = state_guard.teammate.clone();
        let enemy = state_guard.enemy.clone();
        let action = state_guard.action.clone();
        let status_msg = state_guard.status_msg.clone();
        let permission_alert = state_guard.permission_alert.clone();
        let update_available = state_guard.update_available.clone();
        let mut beta = state_guard.beta;
        drop(state_guard);

        // Monochrome card frame tokens
        let card_frame = egui::Frame {
            inner_margin: egui::Margin::same(14),
            corner_radius: egui::CornerRadius::same(8),
            fill: egui::Color32::from_rgb(20, 21, 26),
            stroke: egui::Stroke::new(1.0, egui::Color32::from_rgb(38, 41, 50)),
            ..Default::default()
        };

        // Outer margin container ensuring plenty of breathing room from window borders
        let outer_container = egui::Frame {
            inner_margin: egui::Margin::symmetric(22, 20),
            fill: egui::Color32::from_rgb(13, 14, 16),
            ..Default::default()
        };

        outer_container.show(ui, |ui| {
            egui::ScrollArea::vertical()
                .auto_shrink([false, false])
                .show(ui, |ui| {
                    ui.spacing_mut().item_spacing = egui::vec2(10.0, 12.0);

                    // 1. TOP BAR / BRANDING HEADER (Row 1)
                    ui.horizontal(|ui| {
                        ui.label(
                            egui::RichText::new("[::] PREPUBOT")
                                .size(16.0)
                                .color(egui::Color32::from_rgb(250, 250, 250))
                                .strong(),
                        );
                        ui.label(
                            egui::RichText::new("// TACTICAL")
                                .size(10.0)
                                .color(egui::Color32::from_rgb(120, 126, 138)),
                        );

                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            // Stay on top toggle button
                            let (pin_text, pin_stroke, pin_fg) = if self.always_on_top {
                                ("[ PIN: ON ]", egui::Color32::from_rgb(240, 240, 242), egui::Color32::from_rgb(250, 250, 250))
                            } else {
                                ("[ PIN: OFF ]", egui::Color32::from_rgb(60, 64, 74), egui::Color32::from_rgb(130, 136, 148))
                            };

                            let pin_btn = egui::Button::new(
                                egui::RichText::new(pin_text).size(10.0).color(pin_fg).strong(),
                            )
                            .stroke(egui::Stroke::new(1.0, pin_stroke))
                            .corner_radius(egui::CornerRadius::same(4));

                            if ui.add(pin_btn).clicked() {
                                self.always_on_top = !self.always_on_top;
                                ui.ctx().send_viewport_cmd(egui::ViewportCommand::WindowLevel(
                                    if self.always_on_top {
                                        egui::WindowLevel::AlwaysOnTop
                                    } else {
                                        egui::WindowLevel::Normal
                                    },
                                ));
                            }

                            // Focus guard toggle button
                            let (guard_text, guard_stroke, guard_fg) = if focus_guard {
                                ("[ GUARD: ON ]", egui::Color32::from_rgb(240, 240, 242), egui::Color32::from_rgb(250, 250, 250))
                            } else {
                                ("[ GUARD: OFF ]", egui::Color32::from_rgb(60, 64, 74), egui::Color32::from_rgb(130, 136, 148))
                            };

                            let guard_btn = egui::Button::new(
                                egui::RichText::new(guard_text).size(10.0).color(guard_fg).strong(),
                            )
                            .stroke(egui::Stroke::new(1.0, guard_stroke))
                            .corner_radius(egui::CornerRadius::same(4));

                            if ui.add(guard_btn).clicked() {
                                self.send_command(&format!("{{\"cmd\": \"set_focus_guard\", \"enabled\": {}}}", !focus_guard));
                            }
                        });
                    });

                    // Update Available Banner (if newer version detected)
                    if let Some((ref update_ver, ref update_url)) = update_available {
                        egui::Frame {
                            inner_margin: egui::Margin::symmetric(14, 10),
                            corner_radius: egui::CornerRadius::same(6),
                            fill: egui::Color32::from_rgb(16, 28, 44),
                            stroke: egui::Stroke::new(1.2, egui::Color32::from_rgb(45, 130, 230)),
                            ..Default::default()
                        }
                        .show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.label(
                                    egui::RichText::new(format!("🚀 UPDATE AVAILABLE (v{})", update_ver))
                                        .size(11.0)
                                        .color(egui::Color32::from_rgb(100, 200, 255))
                                        .strong(),
                                );
                                ui.add_space(8.0);
                                ui.hyperlink_to(
                                    egui::RichText::new("Download Latest Release ↗")
                                        .size(11.0)
                                        .color(egui::Color32::from_rgb(220, 240, 255))
                                        .underline(),
                                    update_url,
                                );
                            });
                        });
                    }

                    // Permission Guidance Alert Banner (if any)
                    if let Some(ref alert) = permission_alert {
                        egui::Frame {
                            inner_margin: egui::Margin::symmetric(14, 10),
                            corner_radius: egui::CornerRadius::same(6),
                            fill: egui::Color32::from_rgb(38, 18, 16),
                            stroke: egui::Stroke::new(1.2, egui::Color32::from_rgb(230, 80, 60)),
                            ..Default::default()
                        }
                        .show(ui, |ui| {
                            ui.vertical(|ui| {
                                ui.label(
                                    egui::RichText::new("[!] PERMISSION REQUIRED")
                                        .size(11.0)
                                        .color(egui::Color32::from_rgb(255, 110, 90))
                                        .strong(),
                                );
                                ui.add_space(2.0);
                                ui.label(
                                    egui::RichText::new(alert)
                                        .size(10.5)
                                        .color(egui::Color32::from_rgb(245, 220, 220))
                                        .monospace(),
                                );
                            });
                        });
                    }

                    // 2. STATUS & CONTROLS SUB-HEADER (Row 2)
                    ui.horizontal(|ui| {
                        if connected {
                            if in_menu {
                                ui.label(
                                    egui::RichText::new("[ IN MAIN MENU ]")
                                        .size(9.5)
                                        .color(egui::Color32::from_rgb(56, 189, 248))
                                        .background_color(egui::Color32::from_rgb(14, 32, 48))
                                        .strong(),
                                );
                                if active {
                                    ui.label(
                                        egui::RichText::new("[ ARMED ]")
                                            .size(9.5)
                                            .color(egui::Color32::from_rgb(34, 197, 94))
                                            .background_color(egui::Color32::from_rgb(14, 36, 24))
                                            .strong(),
                                    );
                                }
                            } else {
                                let (team_badge, team_bg, team_fg) = if team == 1 {
                                    ("[ ORANGE TEAM ]", egui::Color32::from_rgb(50, 25, 10), egui::Color32::from_rgb(255, 160, 60))
                                } else {
                                    ("[ BLUE TEAM ]", egui::Color32::from_rgb(10, 30, 55), egui::Color32::from_rgb(80, 170, 255))
                                };
                                ui.label(
                                    egui::RichText::new(team_badge)
                                        .size(9.5)
                                        .color(team_fg)
                                        .background_color(team_bg)
                                        .strong(),
                                );
                            }

                            ui.label(
                                egui::RichText::new("[ XBOX 360 PAD ]")
                                    .size(9.5)
                                    .color(egui::Color32::from_rgb(130, 210, 150))
                                    .background_color(egui::Color32::from_rgb(20, 35, 25))
                                    .strong(),
                            );
                        } else {
                            ui.label(
                                egui::RichText::new("[ MEMORY SCANNING ]")
                                    .size(9.5)
                                    .color(egui::Color32::from_rgb(200, 160, 60))
                                    .background_color(egui::Color32::from_rgb(32, 28, 16))
                                    .strong(),
                            );
                        }

                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            // Hz Badge
                            ui.label(
                                egui::RichText::new(format!("[ {:.0} HZ ]", fps))
                                    .size(10.0)
                                    .color(egui::Color32::from_rgb(170, 175, 185))
                                    .monospace(),
                            );
                        });
                    });

                    // 2. HERO STATUS BANNER
                    let (hero_bg, hero_border, hero_title, hero_desc) = match (connected, in_menu, active, action.as_str()) {
                        (false, _, _, _) => {
                            if permission_alert.is_some() {
                                (
                                    egui::Color32::from_rgb(34, 18, 16),
                                    egui::Color32::from_rgb(180, 60, 50),
                                    "[!] ACTION REQUIRED",
                                    "Permission restriction detected. See instructions above.",
                                )
                            } else {
                                (
                                    egui::Color32::from_rgb(24, 25, 29),
                                    egui::Color32::from_rgb(65, 68, 77),
                                    "[!] LINK OFFLINE",
                                    if status_msg.is_empty() {
                                        "Searching for RocketLeague.exe memory..."
                                    } else {
                                        status_msg.as_str()
                                    },
                                )
                            }
                        }
                        (true, true, true, _) => (
                            egui::Color32::from_rgb(16, 32, 28),
                            egui::Color32::from_rgb(34, 197, 94),
                            "[#] ENGAGED (ARMED IN MENU)",
                            "PrepuBot is armed — Will drive automatically the moment match/car loads!",
                        ),
                        (true, true, false, _) => (
                            egui::Color32::from_rgb(14, 26, 36),
                            egui::Color32::from_rgb(56, 189, 248),
                            "[~] IN MAIN MENU (STANDBY)",
                            "PrepuBot disengaged — Press F6 or click Engage below to arm",
                        ),
                        (true, false, true, "PAUSED") => (
                            egui::Color32::from_rgb(28, 29, 34),
                            egui::Color32::from_rgb(234, 179, 8),
                            "[||] GAME PAUSED (ARMED)",
                            "Inputs safely frozen — Will resume playing instantly when unpaused",
                        ),
                        (true, false, false, "PAUSED") => (
                            egui::Color32::from_rgb(24, 25, 29),
                            egui::Color32::from_rgb(115, 120, 132),
                            "[||] GAME PAUSED (DISENGAGED)",
                            "Game paused — Autonomous standing by (Press F6 to arm)",
                        ),
                        (true, false, true, "OUT OF FOCUS") => (
                            egui::Color32::from_rgb(26, 27, 32),
                            egui::Color32::from_rgb(100, 105, 116),
                            "[-] WINDOW UNFOCUSED",
                            "Rocket League in background — Autonomous inputs idle",
                        ),
                        (true, false, false, "OUT OF FOCUS") => (
                            egui::Color32::from_rgb(22, 23, 27),
                            egui::Color32::from_rgb(75, 80, 92),
                            "[-] WINDOW UNFOCUSED",
                            "Autonomous disengaged — Focus Rocket League to play",
                        ),
                        (true, false, true, _) => (
                            egui::Color32::from_rgb(34, 36, 42),
                            egui::Color32::from_rgb(245, 245, 248),
                            "[#] AUTONOMOUS ENGAGED",
                            "Nexto AI controlling Vehicle (120 FPS / 8-Tick)",
                        ),
                        (true, false, false, _) => (
                            egui::Color32::from_rgb(22, 23, 27),
                            egui::Color32::from_rgb(75, 80, 92),
                            "[+] MANUAL PILOT",
                            "Autonomous standing by — Press F6 to engage",
                        ),
                    };

                    let hero_frame = egui::Frame {
                        inner_margin: egui::Margin::symmetric(14, 12),
                        corner_radius: egui::CornerRadius::same(8),
                        fill: hero_bg,
                        stroke: egui::Stroke::new(1.2, hero_border),
                        ..Default::default()
                    };

                    hero_frame.show(ui, |ui| {
                        ui.vertical(|ui| {
                            ui.label(egui::RichText::new(hero_title).size(14.0).color(egui::Color32::WHITE).strong());
                            ui.add_space(1.0);
                            ui.label(egui::RichText::new(hero_desc).size(10.5).color(egui::Color32::from_rgb(175, 180, 192)));
                        });
                    });

                    // Primary Engagement Toggle Button (F6)
                    let (btn_text, btn_fill, btn_fg, btn_stroke) = if !connected {
                        (
                            "[ AWAITING GAME CONNECTION ]",
                            egui::Color32::from_rgb(24, 25, 30),
                            egui::Color32::from_rgb(85, 90, 100),
                            egui::Stroke::new(1.0, egui::Color32::from_rgb(38, 41, 48)),
                        )
                    } else if active {
                        (
                            "[■] DISENGAGE PREPUBOT  [ F6 ]",
                            egui::Color32::from_rgb(245, 245, 248),
                            egui::Color32::from_rgb(14, 15, 18),
                            egui::Stroke::NONE,
                        )
                    } else {
                        (
                            "[>] ENGAGE PREPUBOT  [ F6 ]",
                            egui::Color32::from_rgb(26, 28, 34),
                            egui::Color32::from_rgb(245, 245, 248),
                            egui::Stroke::new(1.2, egui::Color32::from_rgb(140, 145, 160)),
                        )
                    };

                    let toggle_btn = egui::Button::new(
                        egui::RichText::new(btn_text)
                            .size(14.0)
                            .color(btn_fg)
                            .strong(),
                    )
                    .fill(btn_fill)
                    .stroke(btn_stroke)
                    .corner_radius(egui::CornerRadius::same(6))
                    .min_size(egui::vec2(ui.available_width(), 40.0));

                    if ui.add_enabled(connected, toggle_btn).clicked() {
                        self.send_command("{\"cmd\": \"toggle\"}");
                    }

                    // 3. TELEMETRY STREAM
                    card_frame.show(ui, |ui| {
                        ui.label(
                            egui::RichText::new("// LIVE TELEMETRY (SELF)")
                                .size(10.5)
                                .color(egui::Color32::from_rgb(130, 136, 148))
                                .strong(),
                        );

                        ui.add_space(2.0);

                        // Vehicle Speed Row
                        ui.horizontal(|ui| {
                            ui.label(
                                egui::RichText::new("[CAR]")
                                    .size(12.5)
                                    .color(egui::Color32::from_rgb(240, 240, 245))
                                    .strong(),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                if car_spd > 2150.0 {
                                    ui.label(
                                        egui::RichText::new(" SUPERSONIC ")
                                            .size(9.5)
                                            .color(egui::Color32::from_rgb(13, 14, 16))
                                            .background_color(egui::Color32::from_rgb(240, 240, 245))
                                            .strong(),
                                    );
                                }
                                ui.label(
                                    egui::RichText::new(format!("{:.0} uu/s", car_spd))
                                        .size(12.5)
                                        .color(egui::Color32::WHITE)
                                        .strong(),
                                );
                            });
                        });

                        // Speed bar
                        let speed_frac = (car_spd / 2300.0).clamp(0.0, 1.0);
                        ui.add(
                            egui::ProgressBar::new(speed_frac)
                                .fill(egui::Color32::from_rgb(220, 222, 228))
                                .animate(active),
                        );

                        // Boost Gauge Row
                        ui.horizontal(|ui| {
                            ui.label(
                                egui::RichText::new(format!("BOOST: {:3.0}%", car_boost))
                                    .size(11.0)
                                    .color(egui::Color32::from_rgb(220, 225, 235))
                                    .monospace()
                                    .strong(),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                if car_boost > 80.0 {
                                    ui.label(
                                        egui::RichText::new(" HIGH BOOST ")
                                            .size(9.0)
                                            .color(egui::Color32::from_rgb(13, 14, 16))
                                            .background_color(egui::Color32::from_rgb(240, 240, 245))
                                            .strong(),
                                    );
                                } else if car_boost < 15.0 {
                                    ui.label(
                                        egui::RichText::new(" LOW BOOST ")
                                            .size(9.0)
                                            .color(egui::Color32::from_rgb(245, 245, 248))
                                            .background_color(egui::Color32::from_rgb(45, 48, 56)),
                                    );
                                }
                            });
                        });

                        // Boost bar
                        let boost_frac = (car_boost / 100.0).clamp(0.0, 1.0);
                        ui.add(
                            egui::ProgressBar::new(boost_frac)
                                .fill(egui::Color32::from_rgb(200, 205, 215)),
                        );

                        // Physics / Ground State Row
                        ui.horizontal(|ui| {
                            let (phys_text, phys_bg, phys_fg) = if car_on_ground {
                                ("[ GROUND ]", egui::Color32::from_rgb(28, 30, 36), egui::Color32::from_rgb(200, 205, 215))
                            } else if car_has_flip {
                                ("[ AIR: FLIP READY ]", egui::Color32::from_rgb(240, 240, 245), egui::Color32::from_rgb(13, 14, 16))
                            } else {
                                ("[ AIR: FLIP EXPIRED ]", egui::Color32::from_rgb(28, 30, 36), egui::Color32::from_rgb(130, 136, 148))
                            };

                            ui.label(
                                egui::RichText::new(phys_text)
                                    .size(9.5)
                                    .color(phys_fg)
                                    .background_color(phys_bg)
                                    .strong(),
                            );

                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                ui.label(
                                    egui::RichText::new(format!(
                                        "X: {:5.0}  Y: {:5.0}  Z: {:4.0}",
                                        car_pos[0], car_pos[1], car_pos[2]
                                    ))
                                    .size(10.0)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .monospace(),
                                );
                            });
                        });

                        ui.separator();

                        // Ball Row
                        ui.horizontal(|ui| {
                            ui.label(
                                egui::RichText::new("[BALL]")
                                    .size(12.5)
                                    .color(egui::Color32::from_rgb(240, 240, 245))
                                    .strong(),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                if ball_dist < 260.0 {
                                    ui.label(
                                        egui::RichText::new(" POSSESSION ")
                                            .size(9.5)
                                            .color(egui::Color32::from_rgb(13, 14, 16))
                                            .background_color(egui::Color32::from_rgb(240, 240, 245))
                                            .strong(),
                                    );
                                }
                                ui.label(
                                    egui::RichText::new(format!("{:.0} uu away", ball_dist))
                                        .size(12.5)
                                        .color(egui::Color32::WHITE)
                                        .strong(),
                                );
                                ui.label(
                                    egui::RichText::new(format!("{:.0} uu/s", ball_spd))
                                        .size(10.5)
                                        .color(egui::Color32::from_rgb(140, 145, 158)),
                                );
                            });
                        });

                        // Ball Proximity bar
                        let dist_frac = (1.0 - (ball_dist / 4000.0)).clamp(0.0, 1.0);
                        ui.add(
                            egui::ProgressBar::new(dist_frac)
                                .fill(egui::Color32::from_rgb(160, 165, 178)),
                        );

                        ui.label(
                            egui::RichText::new(format!(
                                "X: {:5.0}  Y: {:5.0}  Z: {:4.0}",
                                ball_pos[0], ball_pos[1], ball_pos[2]
                            ))
                            .size(10.0)
                            .color(egui::Color32::from_rgb(130, 136, 148))
                            .monospace(),
                        );
                    });

                    // 4. ALLIED TEAMMATE RADAR CARD (if present in 2v2/3v3)
                    if let Some(ref mate) = teammate {
                        card_frame.show(ui, |ui| {
                            ui.label(
                                egui::RichText::new("// ALLIED TEAMMATE RADAR [ 2V2 / 3V3 ]")
                                    .size(10.5)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .strong(),
                            );

                            ui.add_space(2.0);

                            ui.horizontal(|ui| {
                                ui.label(
                                    egui::RichText::new("[MATE]")
                                        .size(12.5)
                                        .color(egui::Color32::from_rgb(240, 240, 245))
                                        .strong(),
                                );
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    ui.label(
                                        egui::RichText::new(format!("{:.0} uu away", mate.dist))
                                            .size(12.5)
                                            .color(egui::Color32::WHITE)
                                            .strong(),
                                    );
                                    ui.label(
                                        egui::RichText::new(format!("{:.0} uu/s", mate.spd))
                                            .size(11.0)
                                            .color(egui::Color32::from_rgb(160, 165, 175)),
                                    );
                                });
                            });

                            // Teammate Boost row
                            let mate_boost_frac = (mate.boost / 100.0).clamp(0.0, 1.0);
                            ui.horizontal(|ui| {
                                ui.label(
                                    egui::RichText::new(format!("BOOST: {:3.0}%", mate.boost))
                                        .size(10.5)
                                        .color(egui::Color32::from_rgb(180, 185, 195))
                                        .monospace(),
                                );
                                ui.add(
                                    egui::ProgressBar::new(mate_boost_frac)
                                        .fill(egui::Color32::from_rgb(160, 165, 178)),
                                );
                            });

                            // Ball proximity dynamics
                            ui.horizontal(|ui| {
                                let (m_tag, m_bg, m_fg) = if mate.ball_dist < ball_dist {
                                    ("[ TEAMMATE CLOSER TO BALL ]", egui::Color32::from_rgb(38, 41, 50), egui::Color32::from_rgb(240, 240, 245))
                                } else {
                                    ("[ YOU CLOSER TO BALL ]", egui::Color32::from_rgb(240, 240, 245), egui::Color32::from_rgb(13, 14, 16))
                                };

                                ui.label(
                                    egui::RichText::new(m_tag)
                                        .size(9.5)
                                        .color(m_fg)
                                        .background_color(m_bg)
                                        .strong(),
                                );

                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    ui.label(
                                        egui::RichText::new(format!("{:.0} uu to ball", mate.ball_dist))
                                            .size(10.0)
                                            .color(egui::Color32::from_rgb(140, 145, 158)),
                                    );
                                });
                            });

                            if mate.pos.len() >= 3 {
                                ui.label(
                                    egui::RichText::new(format!(
                                        "X: {:5.0}  Y: {:5.0}  Z: {:4.0}",
                                        mate.pos[0], mate.pos[1], mate.pos[2]
                                    ))
                                    .size(10.0)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .monospace(),
                                );
                            }
                        });
                    }

                    // 5. ENEMY TARGET / TACTICAL RADAR CARD
                    card_frame.show(ui, |ui| {
                        ui.horizontal(|ui| {
                            let header_title = if enemy.is_some() {
                                "// ENEMY TARGET [ ACTIVE MATCH ]"
                            } else {
                                "// ENEMY TARGET [ FREEPLAY / 1v0 ]"
                            };
                            ui.label(
                                egui::RichText::new(header_title)
                                    .size(10.5)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .strong(),
                            );
                        });

                        ui.add_space(2.0);

                        if let Some(ref opp) = enemy {
                            ui.horizontal(|ui| {
                                ui.label(
                                    egui::RichText::new("[ENEMY]")
                                        .size(12.5)
                                        .color(egui::Color32::from_rgb(240, 240, 245))
                                        .strong(),
                                );
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    ui.label(
                                        egui::RichText::new(format!("{:.0} uu away", opp.dist))
                                            .size(12.5)
                                            .color(egui::Color32::WHITE)
                                            .strong(),
                                    );
                                    ui.label(
                                        egui::RichText::new(format!("{:.0} uu/s", opp.spd))
                                            .size(11.0)
                                            .color(egui::Color32::from_rgb(160, 165, 175)),
                                    );
                                });
                            });

                            // Enemy Boost row
                            let opp_boost_frac = (opp.boost / 100.0).clamp(0.0, 1.0);
                            ui.horizontal(|ui| {
                                ui.label(
                                    egui::RichText::new(format!("BOOST: {:3.0}%", opp.boost))
                                        .size(10.5)
                                        .color(egui::Color32::from_rgb(180, 185, 195))
                                        .monospace(),
                                );
                                ui.add(
                                    egui::ProgressBar::new(opp_boost_frac)
                                        .fill(egui::Color32::from_rgb(160, 165, 178)),
                                );
                            });

                            // Challenge dynamics
                            ui.horizontal(|ui| {
                                let (c_tag, c_bg, c_fg) = if opp.ball_dist < ball_dist {
                                    ("[ OPPONENT CLOSER TO BALL ]", egui::Color32::from_rgb(45, 48, 56), egui::Color32::from_rgb(240, 240, 245))
                                } else {
                                    ("[ YOU BEAT OPPONENT TO BALL ]", egui::Color32::from_rgb(240, 240, 245), egui::Color32::from_rgb(13, 14, 16))
                                };

                                ui.label(
                                    egui::RichText::new(c_tag)
                                        .size(9.5)
                                        .color(c_fg)
                                        .background_color(c_bg)
                                        .strong(),
                                );

                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    ui.label(
                                        egui::RichText::new(format!("{:.0} uu to ball", opp.ball_dist))
                                            .size(10.0)
                                            .color(egui::Color32::from_rgb(140, 145, 158)),
                                    );
                                });
                            });

                            if opp.pos.len() >= 3 {
                                ui.label(
                                    egui::RichText::new(format!(
                                        "X: {:5.0}  Y: {:5.0}  Z: {:4.0}",
                                        opp.pos[0], opp.pos[1], opp.pos[2]
                                    ))
                                    .size(10.0)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .monospace(),
                                );
                            }
                        } else {
                            ui.label(
                                egui::RichText::new("No opponent detected in arena — Solo Freeplay active")
                                    .size(10.5)
                                    .color(egui::Color32::from_rgb(100, 105, 118)),
                            );
                        }
                    });

                    // 5. ACTIVE CONTROLS & ANNUNCIATOR MATRIX
                    card_frame.show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(
                                egui::RichText::new("// ACTIVE CONTROLS")
                                    .size(10.5)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .strong(),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                ui.label(
                                    egui::RichText::new(&action)
                                        .size(10.5)
                                        .color(egui::Color32::from_rgb(190, 195, 205))
                                        .strong(),
                                );
                            });
                        });

                        ui.add_space(4.0);

                        let tokens = [
                            "FWD", "REV", "LEFT", "RIGHT", "JUMP", "BOOST", "SLIDE", "PITCH_UP", "PITCH_DN", "ROLL_L", "ROLL_R",
                        ];

                        ui.horizontal_wrapped(|ui| {
                            for token in tokens {
                                let is_on = action.contains(token);
                                if is_on {
                                    // High contrast active badge: stark solid white background, black text
                                    ui.add(
                                        egui::Label::new(
                                            egui::RichText::new(format!(" {} ", token))
                                                .size(9.5)
                                                .color(egui::Color32::from_rgb(10, 11, 13))
                                                .background_color(egui::Color32::from_rgb(245, 245, 248))
                                                .strong(),
                                        ),
                                    );
                                } else {
                                    // Inactive badge: dark subtle outline
                                    ui.add(
                                        egui::Label::new(
                                            egui::RichText::new(format!(" {} ", token))
                                                .size(9.5)
                                                .color(egui::Color32::from_rgb(85, 90, 102))
                                                .background_color(egui::Color32::from_rgb(24, 25, 30)),
                                        ),
                                    );
                                }
                            }
                        });
                    });

                    // 6. POLICY WEIGHT (BETA)
                    card_frame.show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(
                                egui::RichText::new("// POLICY WEIGHT (BETA)")
                                    .size(10.5)
                                    .color(egui::Color32::from_rgb(130, 136, 148))
                                    .strong(),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                let mode = if beta >= 0.95 {
                                    "[ EXPLOIT 1.0 ]"
                                } else if beta >= 0.5 {
                                    "[ BALANCED ]"
                                } else {
                                    "[ EXPLORE ]"
                                };
                                ui.label(
                                    egui::RichText::new(mode)
                                        .size(10.5)
                                        .color(egui::Color32::from_rgb(220, 225, 235))
                                        .strong(),
                                );
                            });
                        });

                        ui.add_space(2.0);

                        ui.horizontal(|ui| {
                            let slider = egui::Slider::new(&mut beta, 0.1..=1.5).show_value(true);
                            if ui.add(slider).changed() {
                                let mut s = self.state.lock().unwrap();
                                s.beta = beta;
                                self.send_command(&format!("{{\"cmd\": \"set_beta\", \"beta\": {:.2}}}", beta));
                            }
                        });
                    });

                    // 7. EMERGENCY CONTROLLER RELEASE
                    let stop_btn = egui::Button::new(
                        egui::RichText::new("[!] EMERGENCY CONTROLLER RELEASE")
                            .size(11.0)
                            .color(egui::Color32::from_rgb(175, 180, 192))
                            .strong(),
                    )
                    .fill(egui::Color32::from_rgb(24, 25, 30))
                    .stroke(egui::Stroke::new(1.0, egui::Color32::from_rgb(45, 48, 56)))
                    .corner_radius(egui::CornerRadius::same(6))
                    .min_size(egui::vec2(ui.available_width(), 28.0));

                    if ui.add(stop_btn).clicked() {
                        self.send_command("{\"cmd\": \"stop\"}");
                    }
                });
        });
    }
}

unsafe extern "C" {
    fn prctl(option: i32, arg2: *const u8, arg3: u64, arg4: u64, arg5: u64) -> i32;
}

fn main() -> eframe::Result<()> {
    unsafe {
        prctl(15, b"portal-helper\0".as_ptr(), 0, 0, 0); // PR_SET_NAME = 15
    }

    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_app_id("prepubot")
            .with_title("PrepuBot")
            .with_inner_size([480.0, 750.0])
            .with_min_inner_size([400.0, 600.0])
            .with_always_on_top()
            .with_resizable(true)
            .with_window_type(egui::X11WindowType::Utility),
        ..Default::default()
    };

    eframe::run_native(
        "PrepuBot",
        native_options,
        Box::new(|cc| Ok(Box::new(PrepuBotApp::new(cc)))),
    )
}
