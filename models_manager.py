"""
models_manager.py - Multi-Model Bot Manager (Nexto, Seer, Element)

Wraps Nexto, Seer, and Element agents and translates live game memory into
clean RLBot GameTickPackets and controller action outputs.
"""

import math
import os
import struct
import sys
import time
from typing import Optional, Tuple, Any

import numpy as np

# Add RLMarlbot to sys.path so its internal modules can import seamlessly
CURRENT_DIR = sys._MEIPASS if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS") else os.path.dirname(os.path.realpath(__file__))
RLMARLBOT_ROOT = os.path.join(CURRENT_DIR, "RLMarlbot")
RLMARLBOT_DIR = os.path.join(RLMARLBOT_ROOT, "rlmarlbot")
for p in [RLMARLBOT_ROOT, RLMARLBOT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from rlbot.utils.structures.game_data_struct import (
    GameTickPacket, BallInfo, PlayerInfo, GameInfo, BoostPadState, FieldInfoPacket
)
from rlbot.agents.base_agent import SimpleControllerState

from nexto_driver import BOOST_LOCATIONS


def quat_to_euler(qx: float, qy: float, qz: float, qw: float) -> Tuple[float, float, float]:
    """Converts a quaternion to Euler angles (pitch, yaw, roll) in radians."""
    sinp = 2.0 * (qw * qy - qz * qx)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))
    roll = math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    return pitch, yaw, roll


class BotModelManager:
    AVAILABLE_BOTS = ["nexto", "seer", "element"]

    def __init__(self, bot_name: str = "nexto", team: int = 0, player_name: str = "Player"):
        self.bot_name = bot_name.lower()
        self.team = team
        self.player_name = player_name
        self.bot = None
        self.field_info = self._create_field_info()
        self.start_time = time.perf_counter()
        self.last_packet: Optional[GameTickPacket] = None
        self.last_action = [0.0] * 8
        self.boost_timers = np.zeros(34, dtype=np.float32)
        self.last_boost_time = time.perf_counter()
        self.prev_car_boost = 0.33

        self._init_bot()

    def _create_field_info(self) -> FieldInfoPacket:
        field_info = FieldInfoPacket()
        field_info.num_boosts = 34
        for i, b in enumerate(BOOST_LOCATIONS):
            field_info.boost_pads[i].location.x = float(b[0])
            field_info.boost_pads[i].location.y = float(b[1])
            field_info.boost_pads[i].location.z = float(b[2])
            field_info.boost_pads[i].is_full_boost = bool(b[2] > 72.0)
        return field_info

    def _init_bot(self):
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            print(f"[BotModelManager] Initializing bot model: {self.bot_name.upper()} (Team {self.team})", file=sys.stderr)
            self.start_time = time.perf_counter()
            if self.bot_name == "seer":
                from rlmarlbot.seer.bot import Seer
                self.bot = Seer(self.player_name, self.team, 0)
                self.bot.initialize_agent()
            elif self.bot_name == "element":
                from rlmarlbot.element.bot import Element
                self.bot = Element(self.player_name, self.team, 0)
                self.bot.initialize_agent(self.field_info)
            else: # Default to nexto
                self.bot_name = "nexto"
                from rlmarlbot.nexto.bot import Nexto
                self.bot = Nexto(self.player_name, self.team, 0, beta=1.0, hardcoded_kickoffs=False)
                self.bot.initialize_agent(self.field_info)

    def set_bot(self, bot_name: str):
        bot_name = bot_name.lower()
        if bot_name in self.AVAILABLE_BOTS and bot_name != self.bot_name:
            self.bot_name = bot_name
            self._init_bot()

    def build_packet(self, car_state: dict, ball_state: dict, mates: list, opps: list, boost_timers: np.ndarray) -> GameTickPacket:
        packet = GameTickPacket()
        now = time.perf_counter()
        elapsed = now - self.start_time

        # Ball Info (PhysX Ground Truth)
        b_pos = ball_state["pos"]
        b_vel = ball_state["vel"]
        b_ang = ball_state.get("ang_vel", np.zeros(3, dtype=np.float32))
        ball_dist_center = float(np.linalg.norm(b_pos[:2]))
        ball_spd = float(np.linalg.norm(b_vel))
        is_kickoff = (ball_dist_center < 35.0 and ball_spd < 50.0)

        # Game Info
        packet.game_info.seconds_elapsed = elapsed
        packet.game_info.game_time_remaining = max(0.0, 300.0 - elapsed)
        packet.game_info.game_speed = 1.0
        packet.game_info.is_round_active = True
        packet.game_info.is_kickoff_pause = is_kickoff
        packet.game_info.frame_num = int((elapsed * 120.0) % 10000000)

        if is_kickoff:
            packet.game_ball.physics.location.x = 0.0
            packet.game_ball.physics.location.y = 0.0
            packet.game_ball.physics.location.z = 92.75
            packet.game_ball.physics.velocity.x = 0.0
            packet.game_ball.physics.velocity.y = 0.0
            packet.game_ball.physics.velocity.z = 0.0
        else:
            packet.game_ball.physics.location.x = float(b_pos[0])
            packet.game_ball.physics.location.y = float(b_pos[1])
            packet.game_ball.physics.location.z = float(b_pos[2])
            packet.game_ball.physics.velocity.x = float(b_vel[0])
            packet.game_ball.physics.velocity.y = float(b_vel[1])
            packet.game_ball.physics.velocity.z = float(b_vel[2])
        packet.game_ball.physics.angular_velocity.x = float(b_ang[0])
        packet.game_ball.physics.angular_velocity.y = float(b_ang[1])
        packet.game_ball.physics.angular_velocity.z = float(b_ang[2])

        if "rot" in ball_state:
            packet.game_ball.physics.rotation.pitch = float(ball_state["rot"][0])
            packet.game_ball.physics.rotation.yaw = float(ball_state["rot"][1])
            packet.game_ball.physics.rotation.roll = float(ball_state["rot"][2])

        # Self Car (Index 0)
        packet.num_cars = 1
        p_self = packet.game_cars[0]
        c_pos = car_state["pos"]
        c_vel = car_state["vel"]
        c_ang = car_state.get("ang_vel", np.zeros(3, dtype=np.float32))
        p_self.physics.location.x = float(c_pos[0])
        p_self.physics.location.y = float(c_pos[1])
        p_self.physics.location.z = float(c_pos[2])
        p_self.physics.velocity.x = float(c_vel[0])
        p_self.physics.velocity.y = float(c_vel[1])
        p_self.physics.velocity.z = float(c_vel[2])
        p_self.physics.angular_velocity.x = float(c_ang[0])
        p_self.physics.angular_velocity.y = float(c_ang[1])
        p_self.physics.angular_velocity.z = float(c_ang[2])

        # Exact Unreal Engine FRotator (pitch, yaw, roll) from 0x9C
        if "rot" in car_state:
            p, y, r = car_state["rot"]
        elif "quat" in car_state:
            qx, qy, qz, qw = car_state["quat"]
            p, y, r = quat_to_euler(qx, qy, qz, qw)
        else:
            fw = car_state.get("fw", np.array([1.0, 0.0, 0.0]))
            y = math.atan2(fw[1], fw[0])
            p = math.asin(max(-1.0, min(1.0, fw[2])))
            r = 0.0

        p_self.physics.rotation.pitch = float(p)
        p_self.physics.rotation.yaw = float(y)
        p_self.physics.rotation.roll = float(r)
        p_self.has_wheel_contact = bool(car_state.get("on_ground", 1.0) > 0.5)
        p_self.jumped = bool(car_state.get("jumped", False))
        p_self.double_jumped = bool(car_state.get("double_jumped", False))
        p_self.boost = int(round(car_state.get("boost", 0.0) * 100.0))
        p_self.team = self.team
        p_self.is_super_sonic = bool(np.linalg.norm(c_vel) >= 2200.0)

        # Other cars (Teammates + Opponents)
        car_idx = 1
        for m in mates:
            if m is None or car_idx >= 64:
                continue
            pm = packet.game_cars[car_idx]
            pm.physics.location.x = float(m["pos"][0])
            pm.physics.location.y = float(m["pos"][1])
            pm.physics.location.z = float(m["pos"][2])
            pm.physics.velocity.x = float(m["vel"][0])
            pm.physics.velocity.y = float(m["vel"][1])
            pm.physics.velocity.z = float(m["vel"][2])
            pm.team = self.team
            pm.has_wheel_contact = bool(m.get("on_ground", 1.0) > 0.5)
            pm.boost = int(round(m.get("boost", 0.0) * 100.0))
            car_idx += 1

        for o in opps:
            if o is None or car_idx >= 64:
                continue
            po = packet.game_cars[car_idx]
            po.physics.location.x = float(o["pos"][0])
            po.physics.location.y = float(o["pos"][1])
            po.physics.location.z = float(o["pos"][2])
            po.physics.velocity.x = float(o["vel"][0])
            po.physics.velocity.y = float(o["vel"][1])
            po.physics.velocity.z = float(o["vel"][2])
            po.team = 1 - self.team
            po.has_wheel_contact = bool(o.get("on_ground", 1.0) > 0.5)
            po.boost = int(round(o.get("boost", 0.0) * 100.0))
            car_idx += 1

        # Real match car count: in Freeplay, num_cars = 1 (Nexto pads missing players with zeros natively)
        packet.num_cars = car_idx

        # Boost Pads
        packet.num_boost = 34
        effective_timers = self.boost_timers
        if boost_timers is not None and np.any(boost_timers > 0.0):
            effective_timers = np.maximum(effective_timers, boost_timers[:34])

        for i in range(34):
            timer = float(effective_timers[i]) if i < len(effective_timers) else 0.0
            packet.game_boosts[i].is_active = bool(timer <= 0.0)
            packet.game_boosts[i].timer = max(0.0, timer)

        return packet

    def update_boost_timers(self, car_state: dict, mates: list, opps: list, is_kickoff: bool):
        now = time.perf_counter()
        dt = min(max(now - self.last_boost_time, 0.001), 0.25)
        self.last_boost_time = now
        self.boost_timers = np.maximum(0.0, self.boost_timers - dt)

        if is_kickoff:
            self.boost_timers.fill(0.0)
            return

        # 1. Detect self car boost increase (collected a pad)
        cur_boost = float(car_state.get("boost", 0.0))
        if cur_boost > self.prev_car_boost + 0.03:
            c_pos = car_state["pos"]
            dists = np.linalg.norm(BOOST_LOCATIONS - c_pos, axis=1)
            closest_idx = int(np.argmin(dists))
            if dists[closest_idx] < 260.0:
                is_big = bool(BOOST_LOCATIONS[closest_idx, 2] > 72.0)
                self.boost_timers[closest_idx] = 10.0 if is_big else 4.0
        self.prev_car_boost = cur_boost

        # 2. Check proximity for all active cars on pitch
        all_cars = [car_state] + [m for m in mates if m is not None] + [o for o in opps if o is not None]
        for c in all_cars:
            if not isinstance(c, dict) or "pos" not in c:
                continue
            c_pos = c["pos"]
            dists = np.linalg.norm(BOOST_LOCATIONS - c_pos, axis=1)
            collected_indices = np.where((dists < 185.0) & (self.boost_timers <= 0.0))[0]
            for idx in collected_indices:
                is_big = bool(BOOST_LOCATIONS[idx, 2] > 72.0)
                self.boost_timers[idx] = 10.0 if is_big else 4.0

    def step(self, car_state: dict, ball_state: dict, mates: list, opps: list, boost_timers: np.ndarray = None) -> SimpleControllerState:
        b_pos = ball_state.get("pos", [0.0, 0.0, 0.0])
        b_vel = ball_state.get("vel", [0.0, 0.0, 0.0])
        ball_dist_center = float(np.linalg.norm(b_pos[:2]))
        ball_spd = float(np.linalg.norm(b_vel))
        is_kickoff = (ball_dist_center < 35.0 and ball_spd < 50.0)

        self.update_boost_timers(car_state, mates, opps, is_kickoff)
        packet = self.build_packet(car_state, ball_state, mates, opps, boost_timers)
        self.last_packet = packet

        try:
            ctrl = self.bot.get_output(packet)
            if ctrl is None:
                ctrl = SimpleControllerState()
        except Exception as e:
            ctrl = SimpleControllerState()

        return ctrl
