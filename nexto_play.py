#!/usr/bin/env python3
"""
Autonomous Bot Player for Rocket League (Freeplay / Exhibition / Custom)
Supports Nexto, Seer, and Element with Direct Memory Input (process_vm_writev)
and Virtual Gamepad (uinput) modes.
"""

import sys
import os
import time
import signal
import math
import numpy as np
import subprocess
import argparse
import json
import select
import threading
import urllib.request

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, sys._MEIPASS)
    sys.path.insert(0, os.path.join(sys._MEIPASS, "RLMarlbot"))
    sys.path.insert(0, os.path.join(sys._MEIPASS, "RLMarlbot", "rlmarlbot"))
else:
    sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "RLMarlbot"))
    sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "RLMarlbot", "rlmarlbot"))

from read_position import get_rocket_league_pid, RLMemoryReader, cloak_process_name
from nexto_driver import NextoDriver
from virtual_controller import VirtualXboxController
from models_manager import BotModelManager

# Explicit imports to ensure PyInstaller standalone packaging bundles all dependencies
import rlgym_compat
import rlbot


# Cloak process name in /proc/self/comm to blend in as a standard desktop portal daemon
cloak_process_name("portal-helper")


def get_current_version():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "version.txt") if hasattr(sys, "_MEIPASS") else "",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                    if v:
                        return v
            except Exception:
                pass
    return "1.1.0"


def parse_version(v_str):
    parts = []
    for chunk in str(v_str).strip().lstrip("v").split("."):
        digits = "".join(filter(str.isdigit, chunk))
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def is_newer_version(remote_ver, local_ver):
    try:
        r = parse_version(remote_ver)
        l = parse_version(local_ver)
        return r > l
    except Exception:
        return False


def start_update_checker(ipc_mode=False):
    """
    Non-blocking background thread that queries the remote repository version.txt.
    If an update is found, emits an IPC message (in IPC mode) or logs to console.
    Errors (offline, 404 while repo is private, timeouts) are silently suppressed.
    """
    def _worker():
        current_ver = get_current_version()
        remote_url = "https://raw.githubusercontent.com/oypi/prepubot/main/version.txt"
        repo_url = "https://github.com/oypi/prepubot"
        try:
            req = urllib.request.Request(
                remote_url,
                headers={"User-Agent": f"PrepuBot/{current_ver}"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    remote_ver = resp.read().decode("utf-8").strip()
                    if is_newer_version(remote_ver, current_ver):
                        if ipc_mode:
                            print(json.dumps({
                                "type": "update_available",
                                "current_version": current_ver,
                                "version": remote_ver,
                                "url": repo_url,
                            }), flush=True)
                        else:
                            print(f"\n[PrepuBot] 🚀 Update Available: v{remote_ver} (Current: v{current_ver})", flush=True)
                            print(f"[PrepuBot] 👉 Download latest release at: {repo_url}\n", flush=True)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def check_game_window_focused():
    """Universal window focus check supporting Niri, Hyprland, Sway, KDE, GNOME, X11, and fallbacks."""
    # 1. Niri (Wayland)
    try:
        out = subprocess.check_output(["niri", "msg", "focused-window"], text=True, timeout=0.15, stderr=subprocess.DEVNULL)
        lower = out.lower()
        if any(k in lower for k in ["rocket league", "252950", "rocketleague", "prepubot"]):
            return True
        return False
    except Exception:
        pass

    # 2. Hyprland (Wayland)
    try:
        out = subprocess.check_output(["hyprctl", "activewindow", "-j"], text=True, timeout=0.15, stderr=subprocess.DEVNULL)
        lower = out.lower()
        if any(k in lower for k in ["rocket league", "252950", "rocketleague", "prepubot"]):
            return True
        return False
    except Exception:
        pass

    # 3. Sway / i3-wayland
    try:
        out = subprocess.check_output(["swaymsg", "-t", "get_tree"], text=True, timeout=0.15, stderr=subprocess.DEVNULL)
        if '"focused": true' in out and any(k in out.lower() for k in ["rocket league", "252950", "rocketleague", "prepubot"]):
            return True
    except Exception:
        pass

    # 4. Standard X11 / Xwayland via xprop (XFCE, KDE, GNOME X11, Cinnamon, MATE, i3, etc.)
    try:
        root_out = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], text=True, timeout=0.15, stderr=subprocess.DEVNULL)
        win_id = root_out.strip().split()[-1]
        if win_id and win_id != "0x0":
            win_out = subprocess.check_output(["xprop", "-id", win_id, "WM_NAME", "WM_CLASS"], text=True, timeout=0.15, stderr=subprocess.DEVNULL)
            lower = win_out.lower()
            if any(k in lower for k in ["rocket league", "252950", "rocketleague", "prepubot"]):
                return True
            return False
    except Exception:
        pass

    # 5. Safe fallback
    return True


# Frame-accurate speedflip kickoff sequence from Nexto (164 ticks @ 120Hz = 1.37s)
# action format: [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
# Base sequence is calibrated for LEFT diagonal spawn:
# drives forward, steers slightly away (left), jumps, diagonal dodges towards ball (right),
# cancels flip immediately, recovers roll to land flat.
# Frame-accurate speedflip kickoff sequence from Nexto (164 ticks @ 120Hz = 1.37s)
# Base calibrated for LEFT diagonal spawn: steer slightly left, jump, diagonal dodge right, flip cancel, land flat.
# action format: [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
DIAGONAL_KICKOFF_SEQUENCE = np.array(
    11 * 4 * [[1.0,  0.0,  0.0,  0.0,  0.0, 0, 1, 0]]  # Drive & boost (0..44 ticks)
    + 3 * 4 * [[1.0, -1.0,  0.0,  0.0,  0.0, 0, 1, 0]]  # Steer slightly away from center (44..56 ticks)
    + 2 * 4 * [[1.0,  0.0,  0.0,  0.0,  0.0, 1, 1, 0]]  # First jump (56..64 ticks)
    + 1 * 4 * [[1.0,  0.0,  0.0,  0.0,  0.0, 0, 1, 0]]  # Release jump (64..68 ticks)
    + 1 * 4 * [[1.0,  0.0, -0.7,  0.8,  0.8, 1, 1, 0]]  # Diagonal flip towards ball (68..72 ticks)
    + 13 * 4 * [[1.0,  0.0,  1.0,  0.0,  0.0, 0, 1, 0]]  # Flip cancel (pitch up) (72..124 ticks)
    + 10 * 4 * [[1.0,  0.0,  0.5,  0.0,  1.0, 0, 0, 1]], # Air roll recovery + powerslide cushion (124..164 ticks)
    dtype=np.float32
)


class KickoffController:
    """
    Kickoff controller executing frame-accurate speedflips exclusively on Diagonal Kickoffs (|x| > 800).
    Non-diagonal spawns (off-center, center) are handled directly by Nexto's neural policy.
    """
    def __init__(self):
        self.active = False
        self.tick = 0
        self.start_time = 0.0
        self.mirror = False
        self.current_seq = None

    def reset(self):
        self.active = False
        self.tick = 0
        self.start_time = 0.0
        self.mirror = False
        self.current_seq = None

    def step(self, car, ball, mates, team: int, now: float):
        """
        Returns (kickoff_action, act_str) if diagonal speedflip is active, else (None, None).
        """
        ball_pos = ball["pos"]
        ball_dist_center = float(np.linalg.norm(ball_pos[:2]))
        ball_spd = float(np.linalg.norm(ball["vel"]))
        is_kickoff_ball = (ball_dist_center < 45.0 and ball_spd < 50.0)

        dist_to_ball = float(np.linalg.norm(car["pos"] - ball_pos))
        car_spd = float(np.linalg.norm(car["vel"]))

        # Kickoff ends when ball is struck or moves significantly
        if not is_kickoff_ball or ball_spd > 150.0 or ball_dist_center > 75.0:
            self.reset()
            return None, None

        if dist_to_ball > 1200.0 and not self.active:
            # Check spawn position: ONLY activate for Diagonal spawns (|x| > 800)
            car_x = float(car["pos"][0])
            abs_x = abs(car_x)
            if abs_x <= 800.0:
                self.reset()
                return None, None

            # Check if this car is the kickoff taker (closest, or left-goes if tied)
            my_xy_dist = float(np.linalg.norm(car["pos"][:2]))
            is_taker = True
            for mate in mates:
                if mate is not None and isinstance(mate, dict) and "pos" in mate:
                    m_dist = float(np.linalg.norm(mate["pos"][:2]))
                    if m_dist < my_xy_dist - 40.0:
                        is_taker = False
                        break
                    elif abs(m_dist - my_xy_dist) <= 40.0:
                        # Tied distance: left goes
                        is_left = (car["pos"][0] < mate["pos"][0]) if team == 0 else (car["pos"][0] > mate["pos"][0])
                        if not is_left:
                            is_taker = False
                            break

            if is_taker:
                if car_spd < 25.0:
                    # Countdown frozen: hold throttle and boost so car launches instantly on GO!
                    return [1.0, 0.0, 0.0, 0.0, 0.0, 0, 1, 0], "KICKOFF READY"
                else:
                    # Countdown ended, car is moving: execute diagonal speedflip
                    is_spawn_right = (car_x > 0.0) if team == 0 else (car_x < 0.0)
                    self.active = True
                    self.start_time = now
                    self.tick = 0
                    self.current_seq = DIAGONAL_KICKOFF_SEQUENCE
                    self.mirror = is_spawn_right

        if self.active and self.current_seq is not None:
            ticks_elapsed = int((now - self.start_time) * 120.0)
            self.tick = ticks_elapsed

            if self.tick < len(self.current_seq):
                raw_act = self.current_seq[self.tick].copy()
                if self.mirror:
                    raw_act[1] = -raw_act[1]  # Invert steer
                    raw_act[3] = -raw_act[3]  # Invert yaw
                    raw_act[4] = -raw_act[4]  # Invert roll
                return raw_act, f"SPEEDFLIP DIAG [{self.tick + 1}/{len(self.current_seq)}]"
            else:
                self.reset()
                return None, None

        return None, None



def action_to_act_str(ctrl):
    """Formats a controller state or 8-element action vector into human-readable string."""
    if hasattr(ctrl, "throttle"):
        thr = ctrl.throttle
        steer = ctrl.steer
        pitch = ctrl.pitch
        yaw = ctrl.yaw
        roll = ctrl.roll
        jump = bool(ctrl.jump)
        boost = bool(ctrl.boost)
        handbrake = bool(ctrl.handbrake)
    else:
        thr = ctrl[0]
        steer = ctrl[1]
        pitch = ctrl[2]
        yaw = ctrl[3]
        roll = ctrl[4]
        jump = bool(ctrl[5] > 0.5) if len(ctrl) > 5 else False
        boost = bool(ctrl[6] > 0.5) if len(ctrl) > 6 else False
        handbrake = bool(ctrl[7] > 0.5) if len(ctrl) > 7 else False

    active_acts = []
    if thr > 0: active_acts.append("FWD")
    elif thr < 0: active_acts.append("REV")
    if steer > 0: active_acts.append("RIGHT")
    elif steer < 0: active_acts.append("LEFT")
    if pitch > 0: active_acts.append("PITCH_UP")
    elif pitch < 0: active_acts.append("PITCH_DN")
    if roll < 0: active_acts.append("ROLL_L")
    elif roll > 0: active_acts.append("ROLL_R")
    if jump: active_acts.append("JUMP")
    if boost: active_acts.append("BOOST")
    if handbrake: active_acts.append("POWERSLIDE")
    return "+".join(active_acts) if active_acts else "IDLE"


def run_ipc(driver, controller, bot_manager, initial_mode="DIRECT MEMORY", initial_active=False):
    active = initial_active
    focus_guard = False
    input_mode = initial_mode
    fps_timer = time.perf_counter()
    frames = 0
    fps = 0.0

    last_focus_check = 0.0
    game_focused = True
    car = None
    ball = None
    act_str = "IDLE"
    kickoff_mgr = KickoffController()

    print(json.dumps({"type": "ready"}), flush=True)

    while True:
        loop_start = time.perf_counter()

        # Check for stdin commands without blocking
        while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            line = sys.stdin.readline()
            if not line:
                return
            try:
                msg = json.loads(line.strip())
                cmd = msg.get("cmd")
                if cmd == "start":
                    active = True
                elif cmd == "stop":
                    active = False
                    act_str = "IDLE"
                    controller.reset()
                elif cmd == "toggle":
                    active = not active
                    if not active:
                        act_str = "IDLE"
                        controller.reset()
                elif cmd == "set_beta":
                    beta = max(0.0, min(1.0, float(msg.get("beta", 1.0))))
                    if hasattr(bot_manager.bot, "beta"):
                        bot_manager.bot.beta = beta
                elif cmd == "set_bot":
                    new_bot = msg.get("bot", "nexto")
                    bot_manager.set_bot(new_bot)
                elif cmd == "set_focus_guard":
                    focus_guard = bool(msg.get("enabled", False))
                elif cmd == "quit":
                    controller.reset()
                    return
            except Exception:
                pass

        now = time.perf_counter()

        # Check window focus every 250ms using universal multi-desktop detection
        if now - last_focus_check >= 0.25:
            last_focus_check = now
            game_focused = check_game_window_focused()

        is_paused = driver.is_paused()
        has_entities = driver.update_entities()

        # Keep controller pointers synchronized with active game objects (respawns, goals)
        if hasattr(controller, "update_pointers"):
            controller.update_pointers(driver.pc_ptr, driver.car_ptr)

        car = driver.read_car_state() if has_entities else None
        ball = driver.read_ball_state() if has_entities else None
        mates = [driver.read_car_state(car_ptr=p) for p in driver.mate_ptrs] if has_entities else []
        opponents = [driver.read_car_state(car_ptr=p) for p in driver.opp_ptrs] if has_entities else []

        if not has_entities or car is None or ball is None:
            controller.reset()
            kickoff_mgr.reset()
            is_in_menu = (driver.pc_ptr is None)
            telemetry = {
                "type": "telemetry",
                "state": "IN_MENU" if is_in_menu else "MATCH",
                "active": active,
                "team": driver.team,
                "bot": bot_manager.bot_name,
                "input_mode": input_mode,
                "focus_guard": focus_guard,
                "fps": round(fps, 1),
                "car": {"pos": [0.0, 0.0, 0.0], "spd": 0.0, "boost": 0.0, "on_ground": False, "has_flip": False},
                "ball": {"pos": [0.0, 0.0, 0.0], "dist": 0.0, "spd": 0.0},
                "teammate": None,
                "enemy": None,
                "action": "IN MENU" if is_in_menu else "GOAL / RESPAWN",
            }
            print(json.dumps(telemetry), flush=True)
            time.sleep(0.1 if is_in_menu else 0.01)
            continue

        # Check if ball is kickoff ball to reset boost pads
        ball_dist_center = float(np.linalg.norm(ball["pos"][:2]))
        ball_spd = float(np.linalg.norm(ball["vel"]))
        if ball_dist_center < 35.0 and ball_spd < 50.0:
            driver.reset_boost_pads()

        # Update bot team if team changed
        if driver.team != bot_manager.team:
            bot_manager.team = driver.team
            bot_manager.bot.team = driver.team

        # Execute decision & control
        now = time.perf_counter()
        if is_paused:
            kickoff_mgr.reset()
            controller.reset()
            act_str = "PAUSED"
        elif focus_guard and not game_focused:
            kickoff_mgr.reset()
            controller.reset()
            act_str = "OUT OF FOCUS"
        elif active:
            kick_act, kick_str = kickoff_mgr.step(car, ball, mates, driver.team, now)
            if kick_act is not None:
                controller.apply_action(kick_act, on_ground=(car["on_ground"] > 0.5), car_z=float(car["pos"][2]))
                act_str = kick_str
            else:
                ctrl = bot_manager.step(car, ball, mates, opponents, driver.boost_timers)
                controller.apply_action(ctrl, on_ground=(car["on_ground"] > 0.5), car_z=float(car["pos"][2]))
                act_str = action_to_act_str(ctrl)
        else:
            kickoff_mgr.reset()
            controller.reset()
            act_str = "IDLE"

        frames += 1
        now = time.perf_counter()
        if now - fps_timer >= 1.0:
            fps = frames / (now - fps_timer)
            frames = 0
            fps_timer = now

        dist_to_ball = float(np.linalg.norm(car["pos"] - ball["pos"]))
        speed = float(np.linalg.norm(car["vel"]))

        mate_telemetry = None
        if mates and len(mates) > 0 and mates[0] is not None:
            mate = mates[0]
            mate_telemetry = {
                "pos": [round(float(v), 1) for v in mate["pos"]],
                "spd": round(float(np.linalg.norm(mate["vel"])), 1),
                "boost": round(float(mate["boost"]) * 100.0, 1),
                "dist": round(float(np.linalg.norm(car["pos"] - mate["pos"])), 1),
                "ball_dist": round(float(np.linalg.norm(ball["pos"] - mate["pos"])), 1),
            }

        opp_telemetry = None
        if opponents and len(opponents) > 0 and opponents[0] is not None:
            opp = opponents[0]
            opp_telemetry = {
                "pos": [round(float(v), 1) for v in opp["pos"]],
                "spd": round(float(np.linalg.norm(opp["vel"])), 1),
                "boost": round(float(opp["boost"]) * 100.0, 1),
                "dist": round(float(np.linalg.norm(car["pos"] - opp["pos"])), 1),
                "ball_dist": round(float(np.linalg.norm(ball["pos"] - opp["pos"])), 1),
            }

        telemetry = {
            "type": "telemetry",
            "active": active,
            "team": driver.team,
            "bot": bot_manager.bot_name,
            "input_mode": input_mode,
            "focus_guard": focus_guard,
            "fps": round(fps, 1),
            "car": {
                "pos": [round(float(v), 1) for v in car["pos"]],
                "spd": round(speed, 1),
                "boost": round(float(car["boost"]) * 100.0, 1),
                "on_ground": bool(car["on_ground"] > 0.5),
                "has_flip": bool(car["has_flip"] > 0.5),
            },
            "ball": {
                "pos": [round(float(v), 1) for v in ball["pos"]],
                "dist": round(dist_to_ball, 1),
                "spd": round(ball_spd, 1),
            },
            "teammate": mate_telemetry,
            "enemy": opp_telemetry,
            "action": act_str,
        }
        print(json.dumps(telemetry), flush=True)

        # Precise 120.0 Hz loop timing (8.333 ms per frame)
        target_tick = loop_start + (1.0 / 120.0)
        remaining = target_tick - time.perf_counter()
        if remaining > 0.002:
            time.sleep(remaining - 0.0015)
        while time.perf_counter() < target_tick:
            pass


def main():
    parser = argparse.ArgumentParser(description="Rocket League Autonomous Bot Player")
    parser.add_argument("--ipc", action="store_true", help="Run in JSON IPC mode for GUI integration")
    parser.add_argument("--start-active", action="store_true", help="Start playing immediately in IPC mode")
    parser.add_argument("--bot", type=str, default="nexto", choices=["nexto", "seer", "element"], help="Bot AI model (default: nexto)")
    parser.add_argument("--mode", type=str, default="uinput", help="Input mode (default: uinput)")
    args = parser.parse_args()

    start_update_checker(ipc_mode=args.ipc)

    pid = get_rocket_league_pid()
    if not pid:
        if args.ipc:
            print(json.dumps({"type": "error", "message": "Rocket League process not running"}), flush=True)
        else:
            print("Error: Rocket League process (RocketLeague.exe) not running!")
        sys.exit(1)

    try:
        scanner = RLMemoryReader(pid)
    except PermissionError as e:
        if args.ipc:
            print(json.dumps({"type": "error", "message": "YAMA_PTRACE_DENIED", "details": str(e)}), flush=True)
        else:
            print(f"\n[!] {e}\n")
        sys.exit(1)

    mode_str = "GAMEPAD"
    try:
        controller = VirtualXboxController()
    except Exception as e:
        if args.ipc:
            print(json.dumps({"type": "error", "message": "UINPUT_DENIED", "details": "Permission denied for /dev/uinput. Run: sudo chmod 666 /dev/uinput"}), flush=True)
        else:
            print("\n[!] Permission denied for /dev/uinput. Run: sudo chmod 666 /dev/uinput\n")
        sys.exit(1)

    pc_ptr = scanner.find_player_controller()
    is_active = args.start_active

    # If in main menu (PlayerController not yet active in a match)
    if not pc_ptr:
        if args.ipc:
            print(json.dumps({"type": "status", "state": "IN_MENU", "active": is_active, "message": "In Main Menu"}), flush=True)
            while not pc_ptr:
                if not os.path.exists(f"/proc/{pid}"):
                    print(json.dumps({"type": "error", "message": "Rocket League process not running"}), flush=True)
                    sys.exit(1)

                while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line.strip())
                        cmd = msg.get("cmd")
                        if cmd == "start":
                            is_active = True
                        elif cmd == "stop":
                            is_active = False
                        elif cmd == "toggle":
                            is_active = not is_active
                        elif cmd == "quit":
                            return
                    except Exception:
                        pass

                print(json.dumps({"type": "status", "state": "IN_MENU", "active": is_active, "message": "In Main Menu"}), flush=True)
                time.sleep(0.2)
                try:
                    pc_ptr = scanner.find_player_controller()
                except Exception:
                    pass
        else:
            print("[-] In Main Menu — Waiting for Freeplay / match...", end="\r", flush=True)
            while not pc_ptr:
                if not os.path.exists(f"/proc/{pid}"):
                    print("\n[-] Rocket League process exited.")
                    sys.exit(1)
                time.sleep(1.0)
                try:
                    pc_ptr = scanner.find_player_controller()
                except Exception:
                    pass
            print("\n[+] Match detected! Attaching Bot...")

    scanner.close()

    try:
        driver = NextoDriver(pid, pc_ptr)
    except PermissionError as e:
        if args.ipc:
            print(json.dumps({"type": "error", "message": "YAMA_PTRACE_DENIED", "details": str(e)}), flush=True)
        else:
            print(f"\n[!] {e}\n")
        sys.exit(1)

    if hasattr(controller, "update_pointers"):
        controller.update_pointers(pc_ptr=pc_ptr, car_ptr=driver.car_ptr)

    bot_manager = BotModelManager(bot_name=args.bot, team=driver.team)

    if args.ipc:
        try:
            run_ipc(driver, controller, bot_manager, initial_mode=mode_str, initial_active=is_active)
        finally:
            controller.reset()
            controller.close()
            driver.close()
        return

    print("==================================================")
    print(f"      Rocket League Bot ({bot_manager.bot_name.upper()})")
    print("==================================================")
    print(f"[+] Attached to RocketLeague.exe (PID: {pid})")
    print(f"[+] Controller: {hex(pc_ptr)}")
    print(f"[+] Mode: {mode_str}")
    print(f"[+] Bot Model: {bot_manager.bot_name.upper()} ready!")

    running = True

    def sig_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    print(f"\n[>>>] {bot_manager.bot_name.upper()} IS PLAYING! Press Ctrl+C to stop.\n")

    fps_timer = time.perf_counter()
    frames = 0
    fps = 0.0
    kickoff_mgr = KickoffController()

    try:
        while running:
            loop_start = time.perf_counter()

            is_paused = driver.is_paused()
            has_entities = driver.update_entities()

            if hasattr(controller, "update_pointers"):
                controller.update_pointers(driver.pc_ptr, driver.car_ptr)

            car = driver.read_car_state() if has_entities else None
            ball = driver.read_ball_state() if has_entities else None
            mates = [driver.read_car_state(car_ptr=p) for p in driver.mate_ptrs] if has_entities else []
            opponents = [driver.read_car_state(car_ptr=p) for p in driver.opp_ptrs] if has_entities else []

            if not has_entities or car is None or ball is None:
                controller.reset()
                kickoff_mgr.reset()
                if driver.pc_ptr is None:
                    sys.stdout.write("\r[IN MENU] Waiting for match / Freeplay...                         ")
                else:
                    sys.stdout.write("\r[GOAL / RESPAWN] Waiting for kickoff...                          ")
                sys.stdout.flush()
                time.sleep(0.05)
                continue

            ball_dist_center = float(np.linalg.norm(ball["pos"][:2]))
            ball_spd = float(np.linalg.norm(ball["vel"]))
            if ball_dist_center < 35.0 and ball_spd < 50.0:
                driver.reset_boost_pads()

            if driver.team != bot_manager.team:
                bot_manager.team = driver.team
                bot_manager.bot.team = driver.team

            now = time.perf_counter()
            if is_paused:
                kickoff_mgr.reset()
                controller.reset()
                act_str = "PAUSED"
            else:
                kick_act, kick_str = kickoff_mgr.step(car, ball, mates, driver.team, now)
                if kick_act is not None:
                    controller.apply_action(kick_act, on_ground=(car["on_ground"] > 0.5), car_z=float(car["pos"][2]))
                    act_str = kick_str
                else:
                    ctrl = bot_manager.step(car, ball, mates, opponents, driver.boost_timers)
                    controller.apply_action(ctrl, on_ground=(car["on_ground"] > 0.5), car_z=float(car["pos"][2]))
                    act_str = action_to_act_str(ctrl)

            frames += 1
            now = time.perf_counter()
            if now - fps_timer >= 1.0:
                fps = frames / (now - fps_timer)
                frames = 0
                fps_timer = now

            dist_to_ball = np.linalg.norm(car["pos"] - ball["pos"])
            speed = np.linalg.norm(car["vel"])

            hud = (
                f"\r[{fps:4.1f} Hz] [{bot_manager.bot_name.upper()}] "
                f"Car: ({car['pos'][0]:6.0f}, {car['pos'][1]:6.0f}, {car['pos'][2]:4.0f}) | "
                f"Spd: {speed:5.0f} | "
                f"Ball Dist: {dist_to_ball:5.0f} | "
                f"Act: {act_str:<22}"
            )
            sys.stdout.write(hud)
            sys.stdout.flush()

            target_tick = loop_start + (1.0 / 120.0)
            remaining = target_tick - time.perf_counter()
            if remaining > 0.002:
                time.sleep(remaining - 0.0015)
            while time.perf_counter() < target_tick:
                pass

    except Exception as e:
        print(f"\n[-] Error in control loop: {e}")
    finally:
        print(f"\n[+] Shutting down {bot_manager.bot_name.upper()} and releasing controller...")
        controller.reset()
        controller.close()
        driver.close()
        print("[+] Done. Safe to resume manual play.")


if __name__ == "__main__":
    main()
