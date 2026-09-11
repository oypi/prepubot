"""
rlsdk_python - Native Linux Drop-In for RLMarlbot
Provides high-performance, stealth memory reading via process_vm_readv.
"""

import math
import os
import struct
import sys
import time
from typing import List, Tuple, Optional, Callable, Dict

# Ensure parent directory is accessible to import read_position
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PARENT_DIR not in sys.path:
    sys.path.append(PARENT_DIR)

from read_position import RLMemoryReader, get_rocket_league_pid

PROCESS_NAME = "RocketLeague.exe"


class EventTypes:
    ON_PLAYER_TICK = "on_player_tick"
    ON_KEY_PRESSED = "on_key_pressed"
    ON_GAME_EVENT_DESTROYED = "on_game_event_destroyed"
    ON_ROUND_ACTIVE_STATE_CHANGED = "on_round_active_state_changed"


class EventManager:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def publish(self, event_type: str, event_data=None):
        if event_type in self._subscribers:
            for cb in self._subscribers[event_type]:
                try:
                    cb(event_data)
                except Exception as e:
                    pass


class FVector:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def get_x(self) -> float:
        return self.x

    def get_y(self) -> float:
        return self.y

    def get_z(self) -> float:
        return self.z

    def get_xyz(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)


class FRotator:
    def __init__(self, pitch: float = 0.0, yaw: float = 0.0, roll: float = 0.0):
        self.pitch = float(pitch)
        self.yaw = float(yaw)
        self.roll = float(roll)

    def get_pitch(self) -> float:
        return self.pitch

    def get_yaw(self) -> float:
        return self.yaw

    def get_roll(self) -> float:
        return self.roll

    def get_pyr(self) -> Tuple[float, float, float]:
        return (self.pitch, self.yaw, self.roll)


class BoostPadVector:
    def __init__(self, x: float, y: float, z: float):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def distance_to(self, other) -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2) ** 0.5


class BoostPad:
    BOOST_LOCATIONS = (
        (0.0, -4240.0, 70.0, False),
        (-1792.0, -4184.0, 70.0, False),
        (1792.0, -4184.0, 70.0, False),
        (-3072.0, -4096.0, 73.0, True),
        (3072.0, -4096.0, 73.0, True),
        (-940.0, -3308.0, 70.0, False),
        (940.0, -3308.0, 70.0, False),
        (0.0, -2816.0, 70.0, False),
        (-3584.0, -2484.0, 70.0, False),
        (3584.0, -2484.0, 70.0, False),
        (-1788.0, -2300.0, 70.0, False),
        (1788.0, -2300.0, 70.0, False),
        (-2048.0, -1036.0, 70.0, False),
        (0.0, -1024.0, 70.0, False),
        (2048.0, -1036.0, 70.0, False),
        (-3584.0, 0.0, 73.0, True),
        (-1024.0, 0.0, 70.0, False),
        (1024.0, 0.0, 70.0, False),
        (3584.0, 0.0, 73.0, True),
        (-2048.0, 1036.0, 70.0, False),
        (0.0, 1024.0, 70.0, False),
        (2048.0, 1036.0, 70.0, False),
        (-1788.0, 2300.0, 70.0, False),
        (1788.0, 2300.0, 70.0, False),
        (-3584.0, 2484.0, 70.0, False),
        (3584.0, 2484.0, 70.0, False),
        (0.0, 2816.0, 70.0, False),
        (-940.0, 3310.0, 70.0, False),
        (940.0, 3308.0, 70.0, False),
        (-3072.0, 4096.0, 73.0, True),
        (3072.0, 4096.0, 73.0, True),
        (-1792.0, 4184.0, 70.0, False),
        (1792.0, 4184.0, 70.0, False),
        (0.0, 4240.0, 70.0, False),
    )

    def __init__(self, x: float, y: float, z: float, is_big: bool = False):
        self.is_active = True
        self.is_big = is_big
        self.location = BoostPadVector(x, y, z)
        self.picked_up_time: Optional[float] = None

    def reset(self):
        self.is_active = True
        self.picked_up_time = None

    def get_elapsed_time(self) -> float:
        if self.picked_up_time:
            return time.time() - self.picked_up_time
        return 0.0

    def respawn_time(self) -> float:
        return 10.0 if self.is_big else 4.0

    def get_remaining_time(self) -> float:
        if self.picked_up_time:
            return max(0.0, self.respawn_time() - self.get_elapsed_time())
        return 0.0


class Field:
    def __init__(self):
        self.boostpads = [BoostPad(x, y, z, is_big) for x, y, z, is_big in BoostPad.BOOST_LOCATIONS]

    def reset_boostpads(self):
        for pad in self.boostpads:
            pad.reset()


class BoostComponent:
    def __init__(self, amount: float = 1.0):
        self._amount = max(0.0, min(1.0, float(amount)))

    def get_amount(self) -> float:
        return self._amount


class TeamInfo:
    def __init__(self, team_index: int = 0):
        self._team_index = int(team_index)

    def get_index(self) -> int:
        return self._team_index


class PRI:
    def __init__(self, address: int, reader: RLMemoryReader, player_name: str = "Player", team_index: int = 0):
        self.address = address
        self._reader = reader
        self._name = player_name
        self._team = TeamInfo(team_index)
        self._car_ptr: Optional[int] = None

    def get_player_name(self) -> str:
        return self._name

    def get_team_info(self) -> TeamInfo:
        return self._team

    def get_car(self) -> "Car":
        if not self._car_ptr:
            pc = self._reader.find_player_controller()
            if pc:
                self._reader.mem_file.seek(pc + 0x9A0)
                self._car_ptr = struct.unpack("<Q", self._reader.mem_file.read(8))[0]
        return Car(self._car_ptr or 0, self._reader)


class Car:
    def __init__(self, address: int, reader: RLMemoryReader):
        self.address = address
        self._reader = reader

    def get_location(self) -> FVector:
        if not self.address:
            return FVector(0.0, 0.0, 17.0)
        self._reader.mem_file.seek(self.address + 0x90)
        x, y, z = struct.unpack("<3f", self._reader.mem_file.read(12))
        return FVector(x, y, z)

    def get_velocity(self) -> FVector:
        if not self.address:
            return FVector(0.0, 0.0, 0.0)
        self._reader.mem_file.seek(self.address + 0x1A8)
        vx, vy, vz = struct.unpack("<3f", self._reader.mem_file.read(12))
        return FVector(vx, vy, vz)

    def get_rotation(self) -> FRotator:
        if not self.address:
            return FRotator(0.0, 0.0, 0.0)
        self._reader.mem_file.seek(self.address + 0x9C)
        p_raw, y_raw, r_raw = struct.unpack("<3i", self._reader.mem_file.read(12))
        const = 0.00009587379924285
        return FRotator(p_raw * const, y_raw * const, r_raw * const)

    def get_angular_velocity(self) -> FVector:
        if not self.address:
            return FVector(0.0, 0.0, 0.0)
        self._reader.mem_file.seek(self.address + 0x1C0)
        wx, wy, wz = struct.unpack("<3f", self._reader.mem_file.read(12))
        return FVector(wx, wy, wz)

    def _read_flags(self) -> int:
        if not self.address:
            return 0
        # Try current Vehicle_TA flags at 0x7F8, fallback to 0x7C8
        for off in (0x7F8, 0x7C8):
            try:
                self._reader.mem_file.seek(self.address + off)
                val = struct.unpack("<I", self._reader.mem_file.read(4))[0]
                if val != 0:
                    return val
            except Exception:
                pass
        return 0

    def is_on_ground(self) -> bool:
        flags = self._read_flags()
        if (flags >> 4) & 1:
            return True
        loc = self.get_location()
        vel = self.get_velocity()
        return bool(loc.z < 25.0 and abs(vel.z) < 80.0)

    def is_supersonic(self) -> bool:
        flags = self._read_flags()
        if (flags >> 5) & 1:
            return True
        v = self.get_velocity()
        spd = math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
        return spd >= 2200.0

    def is_jumped(self) -> bool:
        return bool((self._read_flags() >> 2) & 1)

    def is_double_jumped(self) -> bool:
        return bool((self._read_flags() >> 3) & 1)

    def get_boost_component(self) -> BoostComponent:
        if not self.address:
            return BoostComponent(1.0)
        for off in (0x870, 0x840):
            try:
                self._reader.mem_file.seek(self.address + off)
                boost_comp = struct.unpack("<Q", self._reader.mem_file.read(8))[0]
                if boost_comp and 0x10000000 < boost_comp < 0x7fffffffffff:
                    self._reader.mem_file.seek(boost_comp + 0x338)
                    amt = struct.unpack("<f", self._reader.mem_file.read(4))[0]
                    if 0.0 <= amt <= 1.0:
                        return BoostComponent(amt)
                    elif 1.0 < amt <= 100.0:
                        return BoostComponent(amt / 100.0)
            except Exception:
                pass
        return BoostComponent(1.0)

    def get_pri(self) -> PRI:
        team_idx = self._reader.get_car_team(self.address)
        return PRI(0, self._reader, "Player", team_idx)


class Ball:
    def __init__(self, address: int, reader: RLMemoryReader):
        self.address = address
        self._reader = reader

    def get_location(self) -> FVector:
        if not self.address:
            return FVector(0.0, 0.0, 92.75)
        self._reader.mem_file.seek(self.address + 0x90)
        x, y, z = struct.unpack("<3f", self._reader.mem_file.read(12))
        return FVector(x, y, z)

    def get_velocity(self) -> FVector:
        if not self.address:
            return FVector(0.0, 0.0, 0.0)
        self._reader.mem_file.seek(self.address + 0x1A8)
        vx, vy, vz = struct.unpack("<3f", self._reader.mem_file.read(12))
        return FVector(vx, vy, vz)

    def get_rotation(self) -> FRotator:
        if not self.address:
            return FRotator(0.0, 0.0, 0.0)
        self._reader.mem_file.seek(self.address + 0x9C)
        p_raw, y_raw, r_raw = struct.unpack("<3i", self._reader.mem_file.read(12))
        const = 0.00009587379924285
        return FRotator(p_raw * const, y_raw * const, r_raw * const)

    def get_angular_velocity(self) -> FVector:
        if not self.address:
            return FVector(0.0, 0.0, 0.0)
        self._reader.mem_file.seek(self.address + 0x1C0)
        wx, wy, wz = struct.unpack("<3f", self._reader.mem_file.read(12))
        return FVector(wx, wy, wz)


class Goal:
    def __init__(self, loc: Tuple[float, float, float], direction: Tuple[float, float, float], team: int):
        self._loc = FVector(*loc)
        self._dir = FVector(*direction)
        self._team = team

    def get_location(self) -> FVector:
        return self._loc

    def get_direction(self) -> FVector:
        return self._dir

    def get_team_num(self) -> int:
        return self._team

    def get_width(self) -> float:
        return 1785.0

    def get_height(self) -> float:
        return 643.0


class Team:
    def __init__(self, index: int, score: int = 0):
        self._index = index
        self._score = score

    def get_index(self) -> int:
        return self._index

    def get_score(self) -> int:
        return self._score


class PlayerController:
    def __init__(self, address: int, reader: RLMemoryReader, pri: Optional[PRI] = None):
        self.address = address
        self._reader = reader
        self._pri = pri or PRI(0, reader)

    def get_pri(self) -> PRI:
        return self._pri

    def get_car(self) -> Car:
        self._reader.mem_file.seek(self.address + 0x9A0)
        car_ptr = struct.unpack("<Q", self._reader.mem_file.read(8))[0]
        return Car(car_ptr, self._reader)


class GameEvent:
    def __init__(self, reader: RLMemoryReader):
        self._reader = reader
        self._goals = [
            Goal((0.0, -5120.0, 321.387), (0.0, 1.0, 0.0), 0),
            Goal((0.0, 5120.0, 321.387), (0.0, -1.0, 0.0), 1),
        ]
        self._teams = [Team(0, 0), Team(1, 0)]

    def is_round_active(self) -> bool:
        return True

    def is_overtime(self) -> bool:
        return False

    def is_match_ended(self) -> bool:
        return False

    def is_unlimited_time(self) -> bool:
        return True

    def get_time_remaining(self) -> float:
        return 300.0

    def get_goals(self) -> List[Goal]:
        return self._goals

    def get_teams(self) -> List[Team]:
        return self._teams

    def get_local_players(self) -> List[PlayerController]:
        pc = self._reader.find_player_controller()
        if not pc:
            return []
        team = self._reader.get_car_team(0, pc_ptr=pc)
        pri = PRI(pc + 0x9A8, self._reader, "Player", team)
        return [PlayerController(pc, self._reader, pri)]

    def get_cars(self) -> List[Car]:
        pc = self._reader.find_player_controller()
        my_car = 0
        if pc:
            try:
                self._reader.mem_file.seek(pc + 0x9A0)
                my_car = struct.unpack("<Q", self._reader.mem_file.read(8))[0]
            except Exception:
                pass
        if not my_car:
            c, _ = self._reader.find_active_entities()
            my_car = c or 0

        cars = []
        if my_car:
            cars.append(Car(my_car, self._reader))

        mates, opps = self._reader.find_cars(my_car, pc_ptr=pc)
        for ptr in mates + opps:
            if ptr and ptr != my_car:
                cars.append(Car(ptr, self._reader))

        return cars

    def get_balls(self) -> List[Ball]:
        _, ball_ptr = self._reader.find_active_entities()
        if not ball_ptr:
            pc = self._reader.find_player_controller()
            if pc:
                _, b = self._reader.get_entities_from_pc(pc)
                ball_ptr = b or 0
        return [Ball(ball_ptr or 0, self._reader)]


class RLSDK:
    def __init__(self, hook_player_tick: bool = True, pid: Optional[int] = None):
        self.pid = pid or get_rocket_league_pid()
        if not self.pid:
            raise RuntimeError("Rocket League process (RocketLeague.exe) not found!")

        self.reader = RLMemoryReader(self.pid)
        self.field = Field()
        self.event = EventManager()
        self._game_event = GameEvent(self.reader)

    def get_game_event(self) -> GameEvent:
        return self._game_event
