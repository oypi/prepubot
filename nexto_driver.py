"""
Nexto Driver & Observation Pipeline
Extracts game state directly from Rocket League memory and constructs
observation tensors for Nexto's Transformer network.
"""

import time
import math
import struct
import numpy as np
import torch

try:
    from rlmarlbot.nexto.agent import Agent
except ImportError:
    try:
        from nexto.agent import Agent
    except ImportError:
        Agent = None

# Standard Rocket League boost pad positions (34 pads in standard arena)
BOOST_LOCATIONS = np.array([
    (0.0, -4240.0, 70.0),
    (-1792.0, -4184.0, 70.0),
    (1792.0, -4184.0, 70.0),
    (-3072.0, -4096.0, 73.0),
    (3072.0, -4096.0, 73.0),
    (-940.0, -3308.0, 70.0),
    (940.0, -3308.0, 70.0),
    (0.0, -2816.0, 70.0),
    (-3584.0, -2484.0, 70.0),
    (3584.0, -2484.0, 70.0),
    (-1788.0, -2300.0, 70.0),
    (1788.0, -2300.0, 70.0),
    (-2048.0, -1036.0, 70.0),
    (0.0, -1024.0, 70.0),
    (2048.0, -1036.0, 70.0),
    (-3584.0, 0.0, 73.0),
    (-1024.0, 0.0, 70.0),
    (1024.0, 0.0, 70.0),
    (3584.0, 0.0, 73.0),
    (-2048.0, 1036.0, 70.0),
    (0.0, 1024.0, 70.0),
    (2048.0, 1036.0, 70.0),
    (-1788.0, 2300.0, 70.0),
    (1788.0, 2300.0, 70.0),
    (-3584.0, 2484.0, 70.0),
    (3584.0, 2484.0, 70.0),
    (0.0, 2816.0, 70.0),
    (-940.0, 3310.0, 70.0),
    (940.0, 3308.0, 70.0),
    (-3072.0, 4096.0, 73.0),
    (3072.0, 4096.0, 73.0),
    (-1792.0, 4184.0, 70.0),
    (1792.0, 4184.0, 70.0),
    (0.0, 4240.0, 70.0),
], dtype=np.float32)

IS_SELF, IS_MATE, IS_OPP, IS_BALL, IS_BOOST = range(5)
POS = slice(5, 8)
LIN_VEL = slice(8, 11)
FW = slice(11, 14)
UP = slice(14, 17)
ANG_VEL = slice(17, 20)
BOOST_FEAT, DEMO, ON_GROUND, HAS_FLIP = range(20, 24)
ACTIONS = slice(24, 32)


class NextoDriver:
    def __init__(self, pid, pc_ptr=None):
        self.pid = pid
        from read_position import RLMemoryReader, StealthMemIO, cloak_process_name
        cloak_process_name("portal-helper")
        self.mem = StealthMemIO(pid)
        self.reader = RLMemoryReader(pid)
        self.pc_ptr = pc_ptr
        self.car_ptr = None
        self.ball_ptr = None
        self.team = 0
        self.mate_ptrs = []
        self.opp_ptrs = []
        self.last_opp_scan = 0.0
        self.car_air_times = {}
        self.car_last_state_time = {}
        self.agent = Agent() if Agent is not None else None
        self.prev_action = np.zeros(8, dtype=np.float32)
        self.norm = np.array([1.0] * 5 + [2300.0] * 6 + [1.0] * 6 + [5.5] * 3 + [1.0] * 4, dtype=np.float32)
        self.boost_timers = np.zeros(34, dtype=np.float32)
        self.last_obs_time = time.time()

        if not self.pc_ptr:
            self.reacquire_player_controller()
        self.update_entities()

    def reset_boost_pads(self):
        """Resets all boost pads to fully active (called on kickoff / match start)."""
        self.boost_timers.fill(0.0)

    def reacquire_player_controller(self):
        try:
            self.pc_ptr = self.reader.find_player_controller()
        except Exception:
            self.pc_ptr = None

    def update_entities(self):
        """Dynamically re-reads car and ball pointers from PlayerController_TA, auto-reacquiring across matches."""
        if not self.pc_ptr:
            self.reacquire_player_controller()
            if not self.pc_ptr:
                return False

        try:
            # Read current Car at pc + 0x9A0
            self.mem.seek(self.pc_ptr + 0x9A0)
            self.car_ptr = struct.unpack("<Q", self.mem.read(8))[0]

            # Read current Ball from SoccarGameEvent at pc + 0xC70 -> GameBalls[0] at 0x908
            self.mem.seek(self.pc_ptr + 0xC70)
            ge_ptr = struct.unpack("<Q", self.mem.read(8))[0]
            self.ball_ptr = None
            if ge_ptr:
                self.mem.seek(ge_ptr + 0x908)
                data_ptr = struct.unpack("<Q", self.mem.read(8))[0]
                count = struct.unpack("<I", self.mem.read(4))[0]
                if data_ptr and count > 0:
                    self.mem.seek(data_ptr)
                    self.ball_ptr = struct.unpack("<Q", self.mem.read(8))[0]

            # If either car or ball is missing, PlayerController may be stale (new match or map started)
            if not self.car_ptr or not self.ball_ptr:
                new_pc = self.reader.find_player_controller()
                if new_pc:
                    self.pc_ptr = new_pc
                    c, b = self.reader.get_entities_from_pc(new_pc)
                    if c: self.car_ptr = c
                    if b: self.ball_ptr = b
                else:
                    # PlayerController is gone — player left match or returned to main menu!
                    self.pc_ptr = None
                    self.car_ptr = None
                    self.ball_ptr = None
                    self.mate_ptrs = []
                    self.opp_ptrs = []
                    return False

            if not self.car_ptr or not self.ball_ptr:
                return False

            # Read player team (0 = Blue, 1 = Orange)
            self.team = self.reader.get_car_team(self.car_ptr)

            # Validate existing teammate and opponent pointers
            self.mate_ptrs = [p for p in self.mate_ptrs if self.reader.read_transform(p) is not None]
            self.opp_ptrs = [p for p in self.opp_ptrs if self.reader.read_transform(p) is not None]

            # Periodic scan (every 1.0s or if cars not yet discovered)
            now = time.time()
            if (now - self.last_opp_scan >= 1.0) or (not self.mate_ptrs and not self.opp_ptrs and (now - self.last_opp_scan >= 0.5)):
                self.last_opp_scan = now
                mates, opps = self.reader.find_cars(self.car_ptr, pc_ptr=self.pc_ptr)
                self.mate_ptrs = mates
                self.opp_ptrs = opps

            return True
        except Exception:
            self.pc_ptr = None
            self.reacquire_player_controller()
            return False

    def read_car_state(self, car_ptr=None):
        target_ptr = car_ptr if car_ptr is not None else self.car_ptr
        if not target_ptr:
            return None
        try:
            # Read contiguous 52-byte PhysX RBState at 0x5D0:
            # 0x00: Quaternion (qx, qy, qz, qw)
            # 0x10: Location (x, y, z)
            # 0x1C: Velocity (vx, vy, vz)
            # 0x28: Angular Velocity (wx, wy, wz)
            self.mem.seek(target_ptr + 0x5D0)
            raw = self.mem.read(52)
            qx, qy, qz, qw, x, y, z, vx, vy, vz, wx, wy, wz = struct.unpack("<4f3f3f3f", raw)
            if abs(x) > 100000.0 or math.isnan(x):
                return None

            # Read exact Unreal Engine FRotator at 0x9C (pitch, yaw, roll)
            self.mem.seek(target_ptr + 0x9C)
            p_raw, y_raw, r_raw = struct.unpack("<3i", self.mem.read(12))
            _const = 0.00009587379924285
            car_rot = (float(p_raw * _const), float(y_raw * _const), float(r_raw * _const))

            s = 1.0 / max(1e-6, qx*qx + qy*qy + qz*qz + qw*qw)
            fw = np.array([
                1.0 - 2.0 * s * (qy * qy + qz * qz),
                2.0 * s * (qx * qy + qz * qw),
                2.0 * s * (qx * qz - qy * qw),
            ], dtype=np.float32)
            up = np.array([
                2.0 * s * (qx * qz + qy * qw),
                2.0 * s * (qy * qz - qx * qw),
                1.0 - 2.0 * s * (qx * qx + qy * qy),
            ], dtype=np.float32)

            # Boost amount from BoostComponent at 0x870 -> CurrentBoostAmount at 0x338
            boost = 0.333
            try:
                self.mem.seek(target_ptr + 0x870)
                boost_comp = struct.unpack("<Q", self.mem.read(8))[0]
                if boost_comp:
                    self.mem.seek(boost_comp + 0x338)
                    b = struct.unpack("<f", self.mem.read(4))[0]
                    if 0.0 <= b <= 1.0:
                        boost = b
                    elif 1.0 < b <= 100.0:
                        boost = b / 100.0
                    else:
                        # Fallback to ReplicatedBoostAmount at 0x361 (uint8 0..255)
                        self.mem.seek(boost_comp + 0x361)
                        repl_b = struct.unpack("<B", self.mem.read(1))[0]
                        boost = repl_b / 255.0
            except Exception:
                pass

            # Ground detection via Vehicle_TA flags (0x7F8)
            # Bit 0x10: bOnGround, Bit 0x08: bDoubleJumped, Bit 0x04: bJumped
            on_ground = 0.0
            double_jumped = False
            jumped = False
            try:
                self.mem.seek(target_ptr + 0x7F8)
                flags = struct.unpack("<I", self.mem.read(4))[0]
                is_grounded_flag = bool(flags & 0x10)
                double_jumped = bool(flags & 0x08)
                jumped = bool(flags & 0x04)
                if is_grounded_flag or (z < 25.0 and abs(vz) < 80.0):
                    on_ground = 1.0
            except Exception:
                if z < 25.0 and abs(vz) < 80.0:
                    on_ground = 1.0

            # DoubleJumps counter from Car_TA at 0xADC
            double_jumps = 0
            try:
                self.mem.seek(target_ptr + 0xADC)
                double_jumps = struct.unpack("<I", self.mem.read(4))[0]
            except Exception:
                pass

            now = time.time()
            last_t = self.car_last_state_time.get(target_ptr, now)
            dt = max(0.001, min(0.1, now - last_t))
            self.car_last_state_time[target_ptr] = now

            if target_ptr not in self.car_air_times:
                self.car_air_times[target_ptr] = 0.0

            # Track flip availability per car independently
            if on_ground > 0.5:
                self.car_air_times[target_ptr] = 0.0
                has_flip = 1.0
            else:
                self.car_air_times[target_ptr] += dt
                if double_jumped or double_jumps > 0 or (jumped and self.car_air_times[target_ptr] > 1.40):
                    has_flip = 0.0
                else:
                    has_flip = 1.0

            return {
                "pos": np.array([x, y, z], dtype=np.float32),
                "vel": np.array([vx, vy, vz], dtype=np.float32),
                "ang_vel": np.array([wx, wy, wz], dtype=np.float32),
                "fw": fw,
                "up": up,
                "boost": boost,
                "on_ground": on_ground,
                "has_flip": has_flip,
                "quat": (qx, qy, qz, qw),
                "rot": car_rot,
                "jumped": bool(jumped),
                "double_jumped": bool(double_jumped),
            }
        except Exception:
            return None

    def is_paused(self):
        """Returns True if the game is paused (Escape / pause menu)."""
        if not self.car_ptr:
            return False
        try:
            self.mem.seek(self.car_ptr + 0x130)
            world_info = struct.unpack("<Q", self.mem.read(8))[0]
            if world_info:
                self.mem.seek(world_info + 0x570)
                pauser = struct.unpack("<Q", self.mem.read(8))[0]
                return pauser != 0
        except Exception:
            pass
        return False

    def read_ball_state(self):
        if not self.ball_ptr:
            return None
        try:
            # Read contiguous 52-byte PhysX RBState at 0x5D0 (atomic simulation state):
            # 0x00: Quaternion (qx, qy, qz, qw)
            # 0x10: Location (x, y, z)
            # 0x1C: Velocity (vx, vy, vz)
            # 0x28: Angular Velocity (wx, wy, wz)
            self.mem.seek(self.ball_ptr + 0x5D0)
            raw = self.mem.read(52)
            _, _, _, _, x, y, z, vx, vy, vz, wx, wy, wz = struct.unpack("<4f3f3f3f", raw)
            if abs(x) > 100000.0 or math.isnan(x):
                return None

            # Read exact Unreal Engine FRotator at 0x9C (pitch, yaw, roll)
            self.mem.seek(self.ball_ptr + 0x9C)
            p_raw, y_raw, r_raw = struct.unpack("<3i", self.mem.read(12))
            _const = 0.00009587379924285
            ball_rot = (float(p_raw * _const), float(y_raw * _const), float(r_raw * _const))

            return {
                "pos": np.array([x, y, z], dtype=np.float32),
                "vel": np.array([vx, vy, vz], dtype=np.float32),
                "ang_vel": np.array([wx, wy, wz], dtype=np.float32),
                "rot": ball_rot,
            }
        except Exception:
            return None

    def build_observation(self, car, ball, teammates=None, opponents=None, team=0, latency_comp=0.0, latency_comp_ball=0.0):
        """
        Constructs Nexto's (q, kv, m) tensors in self-relative coordinate frame.
        Supports 1v1, 2v2, 3v3 and inverts the field by 180 deg when playing on Orange team (team == 1).
        Uses pure ground-truth PhysX simulation coordinates (zero latency compensation offset)
        to ensure exact, sub-millimeter ball-balancing and dribble stability on the car's roof.
        """
        mate_list = [m for m in (teammates or []) if m is not None]
        opp_list = [o for o in (opponents or []) if o is not None]

        # In freeplay (no teammates or opponents detected), synthesize a dummy
        # opponent parked in the opposing half so Nexto's transformer always
        # receives its native 1v1 input format.
        if len(opp_list) == 0 and len(mate_list) == 0:
            opp_y = 5120.0 if team == 0 else -5120.0
            opp_fw_y = -1.0 if team == 0 else 1.0
            dummy_opp = {
                "pos": np.array([0.0, opp_y, 17.0], dtype=np.float32),
                "vel": np.zeros(3, dtype=np.float32),
                "ang_vel": np.zeros(3, dtype=np.float32),
                "fw": np.array([0.0, opp_fw_y, 0.0], dtype=np.float32),
                "up": np.array([0.0, 0.0, 1.0], dtype=np.float32),
                "boost": 0.0,
                "on_ground": 1.0,
                "has_flip": 1.0,
            }
            opp_list = [dummy_opp]

        n_players = 1 + len(mate_list) + len(opp_list)
        n_entities = n_players + 1 + 34

        q = np.zeros((1, 1, 32), dtype=np.float32)
        kv = np.zeros((1, n_entities, 24), dtype=np.float32)
        m = np.zeros((1, n_entities), dtype=np.float32)

        # Ground-truth car and ball states (pure unextrapolated PhysX coordinates)
        if latency_comp > 0.0:
            car_pos = (car["pos"] + car["vel"] * latency_comp).copy()
            car_pos[0] = np.clip(car_pos[0], -4096.0, 4096.0)
            car_pos[1] = np.clip(car_pos[1], -5120.0, 5120.0)
            car_pos[2] = np.clip(car_pos[2], 17.0, 2048.0)
        else:
            car_pos = car["pos"]

        if latency_comp_ball > 0.0:
            ball_pos = (ball["pos"] + ball["vel"] * latency_comp_ball).copy()
            ball_vel = ball["vel"].copy()
            if ball_pos[2] > 95.0:
                ball_pos[2] += 0.5 * (-650.0) * (latency_comp_ball ** 2)
                ball_vel[2] += (-650.0) * latency_comp_ball
            ball_pos[0] = np.clip(ball_pos[0], -4096.0, 4096.0)
            ball_pos[1] = np.clip(ball_pos[1], -5120.0, 5120.0)
            ball_pos[2] = np.clip(ball_pos[2], 92.75, 2048.0)
        else:
            ball_pos = ball["pos"]
            ball_vel = ball["vel"]

        def extrapolate_car_pos(c):
            if latency_comp <= 0.0 or c is None:
                return c["pos"] if c is not None else np.zeros(3, dtype=np.float32)
            p = (c["pos"] + c["vel"] * latency_comp).copy()
            p[0] = np.clip(p[0], -4096.0, 4096.0)
            p[1] = np.clip(p[1], -5120.0, 5120.0)
            p[2] = np.clip(p[2], 17.0, 2048.0)
            return p

        # 0: Car (Self)
        kv[0, 0, IS_SELF] = 1.0
        kv[0, 0, IS_MATE] = 1.0
        kv[0, 0, POS] = car_pos
        kv[0, 0, LIN_VEL] = car["vel"]
        kv[0, 0, FW] = car["fw"]
        kv[0, 0, UP] = car["up"]
        kv[0, 0, ANG_VEL] = car["ang_vel"]
        kv[0, 0, BOOST_FEAT] = car["boost"]
        kv[0, 0, DEMO] = 0.0
        kv[0, 0, ON_GROUND] = car["on_ground"]
        kv[0, 0, HAS_FLIP] = car["has_flip"]

        # 1 .. len(mate_list): Teammates
        curr_idx = 1
        for mate in mate_list:
            kv[0, curr_idx, IS_MATE] = 1.0
            kv[0, curr_idx, POS] = extrapolate_car_pos(mate)
            kv[0, curr_idx, LIN_VEL] = mate["vel"]
            kv[0, curr_idx, FW] = mate["fw"]
            kv[0, curr_idx, UP] = mate["up"]
            kv[0, curr_idx, ANG_VEL] = mate["ang_vel"]
            kv[0, curr_idx, BOOST_FEAT] = mate["boost"]
            kv[0, curr_idx, DEMO] = 0.0
            kv[0, curr_idx, ON_GROUND] = mate["on_ground"]
            kv[0, curr_idx, HAS_FLIP] = mate["has_flip"]
            curr_idx += 1

        # curr_idx .. n_players - 1: Opponents
        for opp in opp_list:
            kv[0, curr_idx, IS_OPP] = 1.0
            kv[0, curr_idx, POS] = extrapolate_car_pos(opp)
            kv[0, curr_idx, LIN_VEL] = opp["vel"]
            kv[0, curr_idx, FW] = opp["fw"]
            kv[0, curr_idx, UP] = opp["up"]
            kv[0, curr_idx, ANG_VEL] = opp["ang_vel"]
            kv[0, curr_idx, BOOST_FEAT] = opp["boost"]
            kv[0, curr_idx, DEMO] = 0.0
            kv[0, curr_idx, ON_GROUND] = opp["on_ground"]
            kv[0, curr_idx, HAS_FLIP] = opp["has_flip"]
            curr_idx += 1

        # Ball
        ball_idx = n_players
        kv[0, ball_idx, IS_BALL] = 1.0
        kv[0, ball_idx, POS] = ball_pos
        kv[0, ball_idx, LIN_VEL] = ball_vel
        kv[0, ball_idx, ANG_VEL] = ball["ang_vel"]

        # Advance boost pad simulation timers
        now = time.time()
        dt = min(max(now - self.last_obs_time, 0.001), 0.5)
        self.last_obs_time = now
        self.boost_timers = np.maximum(0.0, self.boost_timers - dt)

        # Detect boost pickup by any active car on pitch
        all_cars = [car] + [m for m in mate_list if m is not None] + [o for o in opp_list if o is not None]
        for c in all_cars:
            c_pos = c["pos"]
            dists = np.linalg.norm(BOOST_LOCATIONS - c_pos, axis=1)
            collected_indices = np.where((dists < 165.0) & (self.boost_timers <= 0.0))[0]
            for idx in collected_indices:
                is_big = BOOST_LOCATIONS[idx, 2] > 72.0
                self.boost_timers[idx] = 10.0 if is_big else 4.0

        # Boost pads
        boost_start = n_players + 1
        for i in range(34):
            b_idx = boost_start + i
            kv[0, b_idx, IS_BOOST] = 1.0
            kv[0, b_idx, POS] = BOOST_LOCATIONS[i]
            kv[0, b_idx, BOOST_FEAT] = 1.0 if BOOST_LOCATIONS[i, 2] > 72 else 0.12
            # Nexto observation spec: DEMO feature on boost entities is 1.0 if pad is available, 0.0 if on cooldown
            kv[0, b_idx, DEMO] = 1.0 if self.boost_timers[i] <= 0.0 else 0.0

        # 180-degree field inversion for Orange Team (team == 1)
        # Inverts (x, y) coordinates so Orange goal (+Y) is treated as Blue goal (-Y)
        # Prevents scoring on own net and orients Nexto toward opponent goal
        if team == 1:
            inv = np.array([-1.0, -1.0, 1.0], dtype=np.float32)
            kv[0, :, POS] *= inv
            kv[0, :, LIN_VEL] *= inv
            kv[0, :, FW] *= inv
            kv[0, :, UP] *= inv
            kv[0, :, ANG_VEL] *= inv

        # Normalize features
        kv /= self.norm

        # Copy unrotated normalized car state to query q
        q[0, 0, :24] = kv[0, 0, :]
        q[0, 0, ACTIONS] = self.prev_action

        # Convert to self-relative frame (rotate around yaw axis)
        kv[..., POS] -= q[..., POS]
        forward = q[..., FW]
        theta = np.arctan2(forward[..., 0], forward[..., 1])
        theta = np.expand_dims(theta, axis=-1)
        ct = np.cos(theta)
        st = np.sin(theta)

        # Rotate position, velocity, and angular velocity
        xs = kv[..., POS.start : ANG_VEL.stop : 3]
        ys = kv[..., POS.start + 1 : ANG_VEL.stop : 3]
        nx = ct * xs - st * ys
        ny = st * xs + ct * ys
        kv[..., POS.start : ANG_VEL.stop : 3] = nx
        kv[..., POS.start + 1 : ANG_VEL.stop : 3] = ny

        return q, kv, m

    def step(self, beta=1.0):
        if not self.update_entities():
            return np.zeros(8, dtype=np.int32), None, None

        car = self.read_car_state()
        ball = self.read_ball_state()
        if car is None or ball is None:
            return np.zeros(8, dtype=np.int32), None, None

        mates = [self.read_car_state(car_ptr=p) for p in self.mate_ptrs]
        opponents = [self.read_car_state(car_ptr=p) for p in self.opp_ptrs]
        q, kv, m = self.build_observation(car, ball, teammates=mates, opponents=opponents, team=self.team)

        state = (q, kv, m)
        action, weights = self.agent.act(state, beta)
        self.prev_action = np.array(action, dtype=np.float32)

        return action, car, ball

    def close(self):
        self.mem.close()
        try:
            self.reader.close()
        except Exception:
            pass
