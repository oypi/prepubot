"""
Match Flight Recorder & Whiff Diagnostic Engine for PrepuBot
Tracks ball encounters at 120 Hz, detects touches vs near-misses (whiffs),
and logs exact 3D spatial offsets and controller inputs for post-match analysis.
"""

import os
import json
import time
from datetime import datetime
import numpy as np


class FlightRecorder:
    def __init__(self, log_path=None, max_file_mb=10.0):
        if log_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(base_dir, "match_debug.jsonl")
        self.log_path = log_path
        self.max_file_bytes = int(max_file_mb * 1024 * 1024)

        # Encounter tracking state
        self.in_encounter = False
        self.encounter_ticks = []
        self.min_dist = float("inf")
        self.min_dist_tick = None
        self.touch_detected = False
        self.prev_ball_vel = None
        self.encounter_start_time = 0.0

        # Kickoff tracking state
        self.was_kickoff = False
        self.kickoff_start_time = 0.0
        self.kickoff_spawn = ""

        # Session metrics
        self.session_touches = 0
        self.session_whiffs = 0
        self.session_kickoffs = 0

        self._check_rotation()

    def _check_rotation(self):
        """Rotates log file if it exceeds maximum size."""
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > self.max_file_bytes:
                backup = self.log_path + ".old"
                if os.path.exists(backup):
                    os.remove(backup)
                os.rename(self.log_path, backup)
        except Exception:
            pass

    def _write_entry(self, entry):
        try:
            entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _to_car_local(self, car, target_pos):
        """Converts world position to car's local frame: [right(+)/left(-), forward(+)/back(-), up(+)/down(-)]."""
        dp = target_pos - car["pos"]
        fw = car["fw"]
        up = car["up"]
        # In Unreal, right = forward x up
        right = np.cross(fw, up)
        r_norm = np.linalg.norm(right)
        if r_norm > 1e-5:
            right /= r_norm

        local_x = float(np.dot(dp, right))
        local_y = float(np.dot(dp, fw))
        local_z = float(np.dot(dp, up))
        return np.array([local_x, local_y, local_z], dtype=np.float32)

    def step(self, car, ball, action, kickoff_active=False, is_kickoff_ball=False):
        """Called every 120 Hz tick with live car and ball states."""
        if car is None or ball is None:
            if self.in_encounter:
                self._finish_encounter(reason="MATCH_RESET")
            return

        now = time.perf_counter()
        dist = float(np.linalg.norm(car["pos"] - ball["pos"]))
        ball_vel = ball["vel"]

        # Track velocity impulse (deflection)
        delta_v = 0.0
        if self.prev_ball_vel is not None:
            delta_v = float(np.linalg.norm(ball_vel - self.prev_ball_vel))
        self.prev_ball_vel = ball_vel.copy()

        # Kickoff lifecycle tracking
        if is_kickoff_ball and not self.was_kickoff and dist > 1500.0 and car["on_ground"] > 0.5:
            self.was_kickoff = True
            self.kickoff_start_time = now
            car_x = float(car["pos"][0])
            if abs(car_x) > 1000.0:
                self.kickoff_spawn = "DIAGONAL_LEFT" if car_x < 0 else "DIAGONAL_RIGHT"
            elif abs(car_x) > 100.0:
                self.kickoff_spawn = "OFF_CENTER_LEFT" if car_x < 0 else "OFF_CENTER_RIGHT"
            else:
                self.kickoff_spawn = "CENTER"

        if self.was_kickoff and (not is_kickoff_ball or dist < 200.0 or delta_v > 200.0):
            self.session_kickoffs += 1
            duration = now - self.kickoff_start_time
            self._write_entry({
                "type": "kickoff",
                "spawn": self.kickoff_spawn,
                "duration_sec": round(duration, 3),
                "end_ball_dist": round(dist, 1),
                "ball_exit_speed": round(float(np.linalg.norm(ball_vel)), 1),
                "first_touch": bool(dist < 160.0 or delta_v > 150.0),
            })
            self.was_kickoff = False

        # Physical touch criteria: impulse deflection or inside contact hull (< 135 uu)
        is_touch = (dist < 135.0) or (dist < 260.0 and delta_v > 250.0)
        if is_touch:
            self.touch_detected = True

        # Encounter lifecycle (begins when closing within 750 uu)
        if not self.in_encounter:
            if dist < 750.0:
                self.in_encounter = True
                self.encounter_ticks = []
                self.min_dist = dist
                self.touch_detected = is_touch
                self.encounter_start_time = now
        else:
            # Inside encounter
            local_offset = self._to_car_local(car, ball["pos"])
            tick_data = {
                "t": round(now - self.encounter_start_time, 4),
                "dist": round(dist, 1),
                "car_spd": round(float(np.linalg.norm(car["vel"])), 1),
                "ball_spd": round(float(np.linalg.norm(ball_vel)), 1),
                "local_offset": [round(float(v), 1) for v in local_offset],
                "act": [int(a) for a in action],
                "on_ground": bool(car["on_ground"] > 0.5),
                "has_flip": bool(car["has_flip"] > 0.5),
            }
            self.encounter_ticks.append(tick_data)

            if dist < self.min_dist:
                self.min_dist = dist
                self.min_dist_tick = tick_data

            # Encounter finishes when moving away (> 800 uu) or after touch with separation
            if (dist > 800.0 and len(self.encounter_ticks) > 5) or (self.touch_detected and dist > 350.0) or (now - self.encounter_start_time > 2.5):
                self._finish_encounter(reason="SEPARATION")

    def _finish_encounter(self, reason=""):
        if not self.in_encounter:
            return

        self.in_encounter = False
        if not self.encounter_ticks or self.min_dist_tick is None:
            return

        min_d = self.min_dist
        local_off = self.min_dist_tick.get("local_offset", [0.0, 0.0, 0.0])
        car_spd = self.min_dist_tick.get("car_spd", 0.0)
        ball_spd = self.min_dist_tick.get("ball_spd", 0.0)
        last_act = self.min_dist_tick.get("act", [0] * 8)

        if self.touch_detected or min_d < 140.0:
            # Confirmed touch
            self.session_touches += 1
            entry = {
                "type": "encounter",
                "result": "TOUCH",
                "min_dist": round(min_d, 1),
                "contact_offset": local_off,
                "car_spd": car_spd,
                "ball_spd": ball_spd,
                "ticks_in_approach": len(self.encounter_ticks),
            }
            self._write_entry(entry)
        elif min_d <= 400.0:
            # Confirmed Whiff / Near-Miss
            self.session_whiffs += 1
            diagnostic = self._diagnose_miss(local_off, min_d, last_act)

            entry = {
                "type": "encounter",
                "result": "WHIFF",
                "min_dist": round(min_d, 1),
                "miss_offset": {
                    "right_left": local_off[0],   # + = right, - = left
                    "fwd_back": local_off[1],     # + = ahead, - = behind
                    "up_down": local_off[2],      # + = above, - = below
                },
                "car_spd": car_spd,
                "ball_spd": ball_spd,
                "diagnostic": diagnostic,
                "action_at_closest": {
                    "throttle": last_act[0],
                    "steer": last_act[1],
                    "pitch": last_act[2],
                    "yaw": last_act[3],
                    "roll": last_act[4],
                    "jump": last_act[5],
                    "boost": last_act[6],
                },
                "pre_whiff_history": self.encounter_ticks[-15:],  # Last 15 ticks (~125ms)
            }
            self._write_entry(entry)

        self.encounter_ticks = []
        self.min_dist = float("inf")
        self.min_dist_tick = None
        self.touch_detected = False

    def _diagnose_miss(self, local_off, min_d, act):
        """Analyzes 3D vector offset to provide precise plain-English diagnosis."""
        dx, dy, dz = local_off
        notes = []

        # Vertical analysis (Z)
        if dz > 70.0:
            notes.append(f"Ball was {dz:.0f} uu ABOVE car roof (under-jumped / late jump)")
        elif dz < -40.0:
            notes.append(f"Ball was {abs(dz):.0f} uu BELOW car (over-jumped / late recovery)")

        # Lateral analysis (X)
        if dx > 60.0:
            notes.append(f"Ball was {dx:.0f} uu to the RIGHT (steered or dodged too far left)")
        elif dx < -60.0:
            notes.append(f"Ball was {abs(dx):.0f} uu to the LEFT (steered or dodged too far right)")

        # Longitudinal analysis (Y)
        if dy > 80.0:
            notes.append(f"Ball was {dy:.0f} uu AHEAD (arrived late / flipped prematurely)")
        elif dy < -60.0:
            notes.append(f"Ball was {abs(dy):.0f} uu BEHIND (overshot the ball)")

        if not notes:
            notes.append(f"Close graze (closest distance {min_d:.0f} uu)")

        return " | ".join(notes)

    def end_session(self):
        """Logs a session summary block."""
        total = self.session_touches + self.session_whiffs
        if total > 0:
            touch_rate = round((self.session_touches / total) * 100.0, 1)
            self._write_entry({
                "type": "session_summary",
                "total_encounters": total,
                "touches": self.session_touches,
                "whiffs": self.session_whiffs,
                "touch_rate_pct": touch_rate,
                "kickoffs": self.session_kickoffs,
            })
