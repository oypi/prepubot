#!/usr/bin/env python3
"""
Nexto Autonomous Player for Rocket League (Freeplay / Exhibition)
Reads game memory directly via /proc/<pid>/mem and plays using a virtual Xbox 360 controller.
"""

import sys
import os
import time
import signal
import numpy as np
import subprocess

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, sys._MEIPASS)
    sys.path.insert(0, os.path.join(sys._MEIPASS, "nexto"))
else:
    sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "nexto"))

from read_position import get_rocket_league_pid, RLMemoryReader
from nexto_driver import NextoDriver
from virtual_controller import VirtualXboxController


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


# Official Nexto speedflip kickoff sequence from nexto/bot.py (168 ticks @ 120Hz = 1.40s)
# action format: [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
DIAGONAL_KICKOFF_SEQUENCE = np.array(
    11 * 4 * [[1.0,  0.0,  0.0,  0.0,  0.0, 0, 1, 0]]  # Drive & boost
    + 4 * 4 * [[1.0, -1.0,  0.0,  0.0,  0.0, 0, 1, 0]]  # Steer slightly left
    + 2 * 4 * [[1.0,  0.0,  0.0,  0.0,  0.0, 1, 1, 0]]  # First jump
    + 1 * 4 * [[1.0,  0.0,  0.0,  0.0,  0.0, 0, 1, 0]]  # Release jump
    + 1 * 4 * [[1.0,  0.0, -0.7,  0.8,  0.0, 1, 1, 0]]  # Diagonal flip right
    + 13 * 4 * [[1.0,  0.0,  1.0,  0.0,  0.0, 0, 1, 0]]  # Flip cancel (pitch up)
    + 10 * 4 * [[1.0,  0.0,  0.5,  0.0,  1.0, 0, 0, 0]], # Air roll recovery
    dtype=np.float32
)


def run_ipc(driver, controller, initial_mode="GAMEPAD"):
    import json
    import select

    active = False
    focus_guard = False
    input_mode = initial_mode
    beta = 1.0
    fps_timer = time.time()
    frames = 0
    fps = 0.0

    TICK_SKIP = 8
    PHYSICS_HZ = 120.0
    DECISION_INTERVAL = TICK_SKIP / PHYSICS_HZ  # ~0.06667s (15 decisions/sec)

    last_decision_time = 0.0
    last_focus_check = 0.0
    game_focused = True
    action = np.zeros(8, dtype=np.int32)
    car = None
    ball = None
    act_str = "IDLE"

    kickoff_active = False
    kickoff_tick = 0
    kickoff_start_time = 0.0
    kickoff_mirror = False
    current_kickoff_seq = None
    kickoff_type = ""

    print(json.dumps({"type": "ready"}), flush=True)

    while True:
        loop_start = time.time()

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
                    action = np.zeros(8, dtype=np.int32)
                    act_str = "IDLE"
                    kickoff_active = False
                    controller.reset()
                elif cmd == "toggle":
                    active = not active
                    if not active:
                        action = np.zeros(8, dtype=np.int32)
                        act_str = "IDLE"
                        kickoff_active = False
                        controller.reset()
                elif cmd == "set_beta":
                    beta = float(msg.get("beta", 1.0))
                elif cmd == "set_focus_guard":
                    focus_guard = bool(msg.get("enabled", False))
                elif cmd == "set_input_mode":
                    pass  # Pure Gamepad emulation, KBM deprecated
                elif cmd == "quit":
                    controller.reset()
                    return
            except Exception:
                pass

        now = time.time()

        # Check window focus every 250ms using universal multi-desktop detection
        if now - last_focus_check >= 0.25:
            last_focus_check = now
            game_focused = check_game_window_focused()

        is_paused = driver.is_paused()

        has_entities = driver.update_entities()
        car = driver.read_car_state() if has_entities else None
        ball = driver.read_ball_state() if has_entities else None
        mates = [driver.read_car_state(car_ptr=p) for p in driver.mate_ptrs] if has_entities else []
        opponents = [driver.read_car_state(car_ptr=p) for p in driver.opp_ptrs] if has_entities else []

        if not has_entities or car is None or ball is None:
            kickoff_active = False
            action = np.zeros(8, dtype=np.int32)
            controller.reset()
            # If entities are missing, check if player has returned to the main menu
            if driver.pc_ptr:
                try:
                    active_pc = driver.reader.find_player_controller()
                    if not active_pc:
                        driver.pc_ptr = None
                        driver.car_ptr = None
                        driver.ball_ptr = None
                        driver.mate_ptrs = []
                        driver.opp_ptrs = []
                except Exception:
                    driver.pc_ptr = None
            is_in_menu = (driver.pc_ptr is None)
            telemetry = {
                "type": "telemetry",
                "state": "IN_MENU" if is_in_menu else "MATCH",
                "active": active,
                "team": driver.team,
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

        car_spd = float(np.linalg.norm(car["vel"]))
        ball_spd = float(np.linalg.norm(ball["vel"]))
        ball_dist_center = float(np.linalg.norm(ball["pos"][:2]))
        dist_to_ball = float(np.linalg.norm(car["pos"] - ball["pos"]))

        # Kickoff detection: ball is at center and nearly stationary
        is_kickoff_ball = (ball_dist_center < 35.0 and ball_spd < 50.0)
        if is_kickoff_ball:
            driver.reset_boost_pads()

        if not is_kickoff_ball or ball_dist_center > 60.0 or ball_spd > 150.0:
            kickoff_active = False

        if active and not is_paused and is_kickoff_ball:
            if dist_to_ball > 1500.0 and car["on_ground"] > 0.5 and not kickoff_active:
                my_xy_dist = float(np.linalg.norm(car["pos"][:2]))
                is_taker = True
                for mate in mates:
                    if mate is not None:
                        m_dist = float(np.linalg.norm(mate["pos"][:2]))
                        if m_dist < my_xy_dist - 40.0:
                            is_taker = False
                            break
                        elif abs(m_dist - my_xy_dist) <= 40.0:
                            # Tied distance: left goes
                            is_left = (car["pos"][0] < mate["pos"][0]) if driver.team == 0 else (car["pos"][0] > mate["pos"][0])
                            if not is_left:
                                is_taker = False
                                break

                if is_taker:
                    if car_spd < 25.0:
                        # Countdown frozen: prime throttle and boost
                        action = np.array([1, 0, 0, 0, 0, 0, 1, 0], dtype=np.float32)
                        act_str = "KICKOFF READY"
                    else:
                        # Countdown ended, car is moving:
                        # Diagonal spawns (|x| > 1000) use the official Nexto speedflip sequence from nexto/bot.py
                        # Straight center and off-center spawns let Nexto's neural network drive naturally (beta=0.5)
                        car_x = float(car["pos"][0])
                        abs_x = abs(car_x)
                        is_spawn_left = (car_x < 0.0) if driver.team == 0 else (car_x > 0.0)

                        if abs_x > 1000.0:
                            current_kickoff_seq = DIAGONAL_KICKOFF_SEQUENCE
                            kickoff_mirror = is_spawn_left
                            kickoff_active = True
                            kickoff_tick = 0
                            kickoff_start_time = now

            if kickoff_active and current_kickoff_seq is not None:
                # Advance kickoff_tick by elapsed physics time (120 Hz) instead of per-loop
                ticks_elapsed = int((now - kickoff_start_time) * 120.0)
                kickoff_tick = min(ticks_elapsed, len(current_kickoff_seq))
                if kickoff_tick < len(current_kickoff_seq):
                    raw_act = current_kickoff_seq[kickoff_tick].copy()
                    if kickoff_mirror:
                        raw_act[1] = -raw_act[1]  # Invert steer
                        raw_act[3] = -raw_act[3]  # Invert yaw
                        raw_act[4] = -raw_act[4]  # Invert roll
                    action = raw_act
                    act_str = f"SPEEDFLIP DIAG [{kickoff_tick + 1}/{len(current_kickoff_seq)}]"
                else:
                    kickoff_active = False

        if not kickoff_active:
            time_since_decision = now - last_decision_time
            if time_since_decision >= DECISION_INTERVAL:
                last_decision_time = now
                time_since_decision = 0.0

                if is_paused:
                    action = np.zeros(8, dtype=np.int32)
                    act_str = "PAUSED"
                elif focus_guard and not game_focused:
                    action = np.zeros(8, dtype=np.int32)
                    act_str = "OUT OF FOCUS"
                elif active:
                    # When inside kickoff approach on non-diagonal spawns, use official Nexto stochastic kickoff (beta=0.5, nexto/bot.py line 190)
                    kickoff_beta = 0.5 if (is_kickoff_ball and dist_to_ball > 800.0) else beta
                    q, kv, m = driver.build_observation(car, ball, teammates=mates, opponents=opponents, team=driver.team)
                    action, _ = driver.agent.act((q, kv, m), beta=kickoff_beta)
                    driver.prev_action = np.array(action, dtype=np.float32)

                    active_acts = []
                    if action[0] > 0: active_acts.append("FWD")
                    elif action[0] < 0: active_acts.append("REV")
                    if action[1] > 0: active_acts.append("RIGHT")
                    elif action[1] < 0: active_acts.append("LEFT")
                    if action[2] > 0: active_acts.append("PITCH_UP")
                    elif action[2] < 0: active_acts.append("PITCH_DN")
                    if action[4] < 0: active_acts.append("ROLL_L")
                    elif action[4] > 0: active_acts.append("ROLL_R")
                    if action[5]: active_acts.append("JUMP")
                    if action[6]: active_acts.append("BOOST")
                    if action[7]: active_acts.append("POWERSLIDE")
                    act_str = "+".join(active_acts) if active_acts else "IDLE"
                else:
                    action = np.zeros(8, dtype=np.int32)
                    act_str = "IDLE"
        else:
            time_since_decision = 0.0

        # Apply controller input continuously for the tick duration
        should_drive = active and not is_paused and car is not None
        if focus_guard and not game_focused:
            should_drive = False

        if should_drive:
            controller.apply_action(
                action,
                on_ground=(car["on_ground"] > 0.5),
                car_z=float(car["pos"][2]),
                time_in_decision=time_since_decision,
            )
        else:
            controller.reset()

        frames += 1
        now = time.time()
        if now - fps_timer >= 1.0:
            fps = frames / (now - fps_timer)
            frames = 0
            fps_timer = now

        if car is not None and ball is not None:
            dist_to_ball = float(np.linalg.norm(car["pos"] - ball["pos"]))
            speed = float(np.linalg.norm(car["vel"]))
            ball_spd = float(np.linalg.norm(ball["vel"]))

            mate_telemetry = None
            if mates and len(mates) > 0 and mates[0] is not None:
                mate = mates[0]
                mate_dist_to_me = float(np.linalg.norm(car["pos"] - mate["pos"]))
                mate_dist_to_ball = float(np.linalg.norm(ball["pos"] - mate["pos"]))
                mate_speed = float(np.linalg.norm(mate["vel"]))
                mate_telemetry = {
                    "pos": [round(float(v), 1) for v in mate["pos"]],
                    "spd": round(mate_speed, 1),
                    "boost": round(float(mate["boost"]) * 100.0, 1),
                    "dist": round(mate_dist_to_me, 1),
                    "ball_dist": round(mate_dist_to_ball, 1),
                }

            opp_telemetry = None
            if opponents and len(opponents) > 0 and opponents[0] is not None:
                opp = opponents[0]
                opp_dist_to_me = float(np.linalg.norm(car["pos"] - opp["pos"]))
                opp_dist_to_ball = float(np.linalg.norm(ball["pos"] - opp["pos"]))
                opp_speed = float(np.linalg.norm(opp["vel"]))
                opp_telemetry = {
                    "pos": [round(float(v), 1) for v in opp["pos"]],
                    "spd": round(opp_speed, 1),
                    "boost": round(float(opp["boost"]) * 100.0, 1),
                    "dist": round(opp_dist_to_me, 1),
                    "ball_dist": round(opp_dist_to_ball, 1),
                }

            telemetry = {
                "type": "telemetry",
                "active": active,
                "team": driver.team,
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
                "input_mode": input_mode,
                "action": act_str,
            }
            print(json.dumps(telemetry), flush=True)

        elapsed = time.time() - loop_start
        # Poll at ~120 Hz (8.33 ms)
        time.sleep(max(0.001, (1.0 / 120.0) - elapsed))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rocket League Nexto Autonomous Bot")
    parser.add_argument("--ipc", action="store_true", help="Run in JSON IPC mode for GUI integration")
    parser.add_argument("--start-active", action="store_true", help="Start playing immediately in IPC mode")
    args = parser.parse_args()

    pid = get_rocket_league_pid()
    if not pid:
        if args.ipc:
            import json
            print(json.dumps({"type": "error", "message": "Rocket League process not running"}), flush=True)
        else:
            print("Error: Rocket League process (RocketLeague.exe) not running!")
        sys.exit(1)

    try:
        scanner = RLMemoryReader(pid)
    except PermissionError as e:
        if args.ipc:
            import json
            print(json.dumps({"type": "error", "message": "YAMA_PTRACE_DENIED", "details": str(e)}), flush=True)
        else:
            print(f"\n[!] {e}\n")
        sys.exit(1)

    try:
        controller = VirtualXboxController()
    except Exception as e:
        if args.ipc:
            import json
            print(json.dumps({"type": "error", "message": "UINPUT_DENIED", "details": "Permission denied for /dev/uinput. Run: sudo chmod 666 /dev/uinput"}), flush=True)
        else:
            print("\n[!] Permission denied for /dev/uinput. Run: sudo chmod 666 /dev/uinput\n")
        sys.exit(1)

    pc_ptr = scanner.find_player_controller()

    # If in main menu (PlayerController not yet active in a match)
    if not pc_ptr:
        if args.ipc:
            import json
            import select
            print(json.dumps({"type": "status", "state": "IN_MENU", "message": "In Main Menu"}), flush=True)
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
                        if msg.get("cmd") == "quit":
                            return
                    except Exception:
                        pass

                print(json.dumps({"type": "status", "state": "IN_MENU", "message": "In Main Menu"}), flush=True)
                time.sleep(0.5)
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
            print("\n[+] Match detected! Attaching Nexto...")

    scanner.close()

    try:
        driver = NextoDriver(pid, pc_ptr)
    except PermissionError as e:
        if args.ipc:
            import json
            print(json.dumps({"type": "error", "message": "YAMA_PTRACE_DENIED", "details": str(e)}), flush=True)
        else:
            print(f"\n[!] {e}\n")
        sys.exit(1)

    if args.ipc:
        try:
            run_ipc(driver, controller)
        finally:
            controller.reset()
            controller.close()
            driver.close()
        return

    print("==================================================")
    print("         Rocket League Nexto Autonomous Bot       ")
    print("==================================================")
    print(f"[+] Attached to RocketLeague.exe (PID: {pid})")
    print(f"[+] Controller: {hex(pc_ptr)}")
    print("[+] Virtual Xbox 360 Controller ready!")
    print("[+] Nexto Neural Network ready!")

    running = True

    def sig_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    print("\n[>>>] NEXTO IS PLAYING! Press Ctrl+C to stop.\n")

    fps_timer = time.time()
    frames = 0
    fps = 0.0

    TICK_SKIP = 8
    PHYSICS_HZ = 120.0
    DECISION_INTERVAL = TICK_SKIP / PHYSICS_HZ  # ~0.06667s

    last_decision_time = 0.0
    action = np.zeros(8, dtype=np.int32)
    car = None
    ball = None

    try:
        while running:
            loop_start = time.time()
            now = time.time()

            if now - last_decision_time >= DECISION_INTERVAL:
                last_decision_time = now
                action, car, ball = driver.step(beta=1.0)
                if car is None or ball is None:
                    controller.reset()
                    if driver.pc_ptr is None:
                        sys.stdout.write("\r[IN MENU] Waiting for match / Freeplay...                         ")
                    else:
                        sys.stdout.write("\r[GOAL / RESPAWN] Waiting for kickoff...                          ")
                    sys.stdout.flush()
                    time.sleep(0.05)
                    continue

            if car is not None:
                controller.apply_action(action, on_ground=(car["on_ground"] > 0.5), car_z=float(car["pos"][2]), time_in_decision=(now - last_decision_time))

            frames += 1
            now = time.time()
            if now - fps_timer >= 1.0:
                fps = frames / (now - fps_timer)
                frames = 0
                fps_timer = now

            dist_to_ball = np.linalg.norm(car["pos"] - ball["pos"])
            speed = np.linalg.norm(car["vel"])

            active_acts = []
            if action[0] > 0: active_acts.append("FWD")
            elif action[0] < 0: active_acts.append("REV")
            if action[1] > 0: active_acts.append("RIGHT")
            elif action[1] < 0: active_acts.append("LEFT")
            if action[2] > 0: active_acts.append("PITCH_UP")
            elif action[2] < 0: active_acts.append("PITCH_DN")
            if action[4] < 0: active_acts.append("ROLL_L")
            elif action[4] > 0: active_acts.append("ROLL_R")
            if action[5]: active_acts.append("JUMP")
            if action[6]: active_acts.append("BOOST")
            if action[7]: active_acts.append("POWERSLIDE")
            act_str = "+".join(active_acts) if active_acts else "IDLE"

            hud = (
                f"\r[{fps:4.1f} Hz] "
                f"Car: ({car['pos'][0]:6.0f}, {car['pos'][1]:6.0f}, {car['pos'][2]:4.0f}) | "
                f"Spd: {speed:5.0f} | "
                f"Ball Dist: {dist_to_ball:5.0f} | "
                f"Act: {act_str:<22}"
            )
            sys.stdout.write(hud)
            sys.stdout.flush()

            elapsed = time.time() - loop_start
            sleep_time = max(0.001, (1.0 / 120.0) - elapsed)
            time.sleep(sleep_time)

    except Exception as e:
        print(f"\n[-] Error in control loop: {e}")
    finally:
        print("\n[+] Shutting down Nexto and releasing controller...")
        controller.reset()
        controller.close()
        driver.close()
        print("[+] Done. Safe to resume manual play.")


if __name__ == "__main__":
    main()
