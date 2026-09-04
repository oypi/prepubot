#!/usr/bin/env python3
"""
Rocket League Memory Reader - Player & Ball Telemetry
Reads live coordinates, orientation, and velocity directly from the game's memory on Linux (via /proc/<pid>/mem).
"""

import sys
import os
import struct
import time
import subprocess

GNAMES_ADDR = 0x142418148
GOBJECTS_ADDR = 0x142418190
GOBJECTS_COUNT_ADDR = 0x142418198

# Property offsets discovered from engine reflection (AActor)
OFFSET_LOCATION = 0x90     # FVector: float X, float Y, float Z
OFFSET_ROTATION = 0x9C     # FRotator: int32 Pitch, int32 Yaw, int32 Roll
OFFSET_VELOCITY = 0x1A8    # FVector: float Vx, float Vy, float Vz


def get_rocket_league_pid():
    candidates = []
    # 1. Try pgrep
    try:
        out = subprocess.check_output(["pgrep", "-i", "RocketLeague"]).decode().strip().split()
        pids = [int(p) for p in out if p.isdigit()]
    except Exception:
        pids = []

    # 2. Fallback to /proc directory listing if pgrep is unavailable or empty
    if not pids:
        try:
            for entry in os.listdir("/proc"):
                if entry.isdigit():
                    pids.append(int(entry))
        except Exception:
            pass

    for pid in pids:
        if pid == os.getpid():
            continue
        cmdline_path = f"/proc/{pid}/cmdline"
        if not os.path.exists(cmdline_path):
            continue
        try:
            with open(cmdline_path, "rb") as f:
                cmdline = f.read().decode(errors="ignore").replace("\0", " ")

            cmdline_lower = cmdline.lower()
            # Strictly exclude EAC integrity bootstrap, launchers, and our own processes
            if "rocketleague_eac.exe" in cmdline_lower or "easyanticheat" in cmdline_lower:
                continue
            if "launcher.exe" in cmdline_lower:
                continue
            if "nexto" in cmdline_lower or "prepubot" in cmdline_lower or "python" in cmdline_lower:
                continue
            if "rocketleague.exe" in cmdline_lower:
                candidates.append(pid)
        except Exception:
            continue

    return candidates[0] if candidates else None



import ctypes

class _IOVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


def cloak_process_name(name="portal-helper"):
    """Cloaks process name in /proc/<pid>/comm to blend in as a standard desktop daemon."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, name.encode("utf-8")[:15], 0, 0, 0)
    except Exception:
        pass


class StealthMemIO:
    """
    Direct kernel memory reader using process_vm_readv syscalls.
    Eliminates open file descriptors to /proc/<pid>/mem, making memory reading
    completely invisible to file-descriptor monitoring anti-cheat mechanisms.
    """
    def __init__(self, pid):
        self.pid = pid
        self._pos = 0
        self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self._has_vm_readv = hasattr(self._libc, "process_vm_readv")
        if self._has_vm_readv:
            self._process_vm_readv = self._libc.process_vm_readv
            self._process_vm_readv.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(_IOVec),
                ctypes.c_ulong,
                ctypes.POINTER(_IOVec),
                ctypes.c_ulong,
                ctypes.c_ulong,
            ]
            self._process_vm_readv.restype = ctypes.c_ssize_t
            self._local_iov = _IOVec()
            self._remote_iov = _IOVec()
            self._local_ref = ctypes.byref(self._local_iov)
            self._remote_ref = ctypes.byref(self._remote_iov)
        self._fallback_file = None

    def seek(self, pos, whence=0):
        if whence == 0:
            self._pos = pos
        elif whence == 1:
            self._pos += pos
        return self._pos

    def read(self, size):
        data = self.read_bytes(self._pos, size)
        self._pos += len(data) if data else size
        return data if data else b""

    def read_bytes(self, address, size):
        if self._has_vm_readv:
            buf = ctypes.create_string_buffer(size)
            self._local_iov.iov_base = ctypes.cast(buf, ctypes.c_void_p)
            self._local_iov.iov_len = size
            self._remote_iov.iov_base = address
            self._remote_iov.iov_len = size
            nread = self._process_vm_readv(self.pid, self._local_ref, 1, self._remote_ref, 1, 0)
            if nread == size:
                return buf.raw
            errno = ctypes.get_errno()
            if errno in (1, 13):  # EPERM / EACCES
                raise PermissionError(
                    f"Linux kernel security (ptrace_scope) restricted memory reading.\n"
                    f"Fix: Run 'echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope' or grant CAP_SYS_PTRACE."
                )
        if not self._fallback_file:
            self._fallback_file = open(f"/proc/{self.pid}/mem", "rb")
        self._fallback_file.seek(address)
        return self._fallback_file.read(size)

    def close(self):
        if self._fallback_file:
            try:
                self._fallback_file.close()
            except Exception:
                pass
            self._fallback_file = None


class RLMemoryReader:
    def __init__(self, pid):
        self.pid = pid
        try:
            self.mem_file = StealthMemIO(pid)
        except PermissionError as e:
            raise PermissionError(
                f"Failed to access process memory: Permission denied.\n"
                f"Linux kernel Yama security (ptrace_scope) is restricting memory reading.\n"
                f"Fix: Run 'echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope' or run prepubot with sudo."
            ) from e
        self.names_cache = {}
        self.resolve_globals()

    def resolve_globals(self):
        self.mem_file.seek(GNAMES_ADDR)
        self.gnames_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]

        self.mem_file.seek(GOBJECTS_ADDR)
        raw = self.mem_file.read(16)
        if len(raw) >= 12:
            self.gobjects_ptr, self.gobjects_count = struct.unpack("<QI", raw[:12])
        else:
            self.mem_file.seek(GOBJECTS_ADDR)
            self.gobjects_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
            self.mem_file.seek(GOBJECTS_COUNT_ADDR)
            self.gobjects_count = struct.unpack("<I", self.mem_file.read(4))[0]

    def get_name(self, name_idx):
        if name_idx in self.names_cache:
            return self.names_cache[name_idx]
        try:
            self.mem_file.seek(self.gnames_ptr + name_idx * 8)
            entry_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
            if not entry_ptr:
                return "None"
            self.mem_file.seek(entry_ptr + 0x18)
            raw = self.mem_file.read(128)
            chars = []
            for i in range(0, len(raw), 2):
                c = struct.unpack("<H", raw[i : i + 2])[0]
                if c == 0:
                    break
                chars.append(chr(c))
            name = "".join(chars)
            self.names_cache[name_idx] = name
            return name
        except Exception:
            return "Err"

    def find_player_controller(self):
        """Locates the active PlayerController_TA in PersistentLevel (sub-100ms via class pointer caching)."""
        self.resolve_globals()
        self.mem_file.seek(self.gobjects_ptr)
        raw_ptrs = self.mem_file.read(self.gobjects_count * 8)
        ptrs = struct.unpack(f"<{self.gobjects_count}Q", raw_ptrs)

        pc_class = getattr(self, "pc_class_ptr", None)
        level_class = getattr(self, "level_class_ptr", None)

        best_pc = None
        fallback_pc = None

        for ptr in reversed(ptrs):
            if not ptr:
                continue
            try:
                self.mem_file.seek(ptr + 0x50)
                class_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
                if not class_ptr:
                    continue

                is_pc = False
                if pc_class and class_ptr == pc_class:
                    is_pc = True
                elif not pc_class:
                    self.mem_file.seek(class_ptr + 0x48)
                    if self.get_name(struct.unpack("<I", self.mem_file.read(4))[0]) == "PlayerController_TA":
                        self.pc_class_ptr = class_ptr
                        is_pc = True

                if is_pc:
                    # CRITICAL: Verify this is strictly the LOCAL human player!
                    # In Unreal Engine 3, PlayerController::Player (offset 0x478) points to ULocalPlayer.
                    # Remote network players, bots, and spectator slots have Player = 0x0.
                    self.mem_file.seek(ptr + 0x478)
                    local_player = struct.unpack("<Q", self.mem_file.read(8))[0]
                    if not (0x10000000 < local_player < 0x7fffffffffff):
                        continue

                    self.mem_file.seek(ptr + 0x40)
                    outer = struct.unpack("<Q", self.mem_file.read(8))[0]
                    if outer:
                        is_level = False
                        if level_class:
                            self.mem_file.seek(outer + 0x50)
                            if struct.unpack("<Q", self.mem_file.read(8))[0] == level_class:
                                is_level = True
                        else:
                            self.mem_file.seek(outer + 0x48)
                            if self.get_name(struct.unpack("<I", self.mem_file.read(4))[0]) == "PersistentLevel":
                                self.mem_file.seek(outer + 0x50)
                                self.level_class_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
                                is_level = True

                        if is_level:
                            # Crucial: verify candidate actually owns an active car with valid transform!
                            car, ball = self.get_entities_from_pc(ptr)
                            if car and self.read_transform(car) is not None:
                                if ball:
                                    return ptr
                                elif not best_pc:
                                    best_pc = ptr
                            elif not fallback_pc:
                                fallback_pc = ptr
                        elif not fallback_pc:
                            fallback_pc = ptr
            except Exception:
                continue

        return best_pc or fallback_pc

    def get_entities_from_pc(self, pc_ptr):
        """Reads active Car and Ball directly from PlayerController_TA (survives goals & respawns)."""
        if not pc_ptr:
            return None, None
        try:
            self.mem_file.seek(pc_ptr + 0x9A0)
            car_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]

            self.mem_file.seek(pc_ptr + 0xC70)
            ge_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
            ball_ptr = None
            if ge_ptr:
                self.mem_file.seek(ge_ptr + 0x908)
                data_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
                count = struct.unpack("<I", self.mem_file.read(4))[0]
                if data_ptr and count > 0:
                    self.mem_file.seek(data_ptr)
                    ball_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]

            return (car_ptr if (car_ptr and 0x10000000 < car_ptr < 0x7fffffffffff) else None), (ball_ptr if (ball_ptr and 0x10000000 < ball_ptr < 0x7fffffffffff) else None)
        except Exception:
            return None, None

    def get_car_team(self, car_ptr, pc_ptr=None):
        """Returns 0 for Blue team, 1 for Orange team from Car_TA / PlayerController -> PRI_TA -> Team_Soccar_TA."""
        if not car_ptr and not pc_ptr:
            return 0
        try:
            pri = 0
            if car_ptr:
                # Vehicle_TA::PRI is at car + 0x830
                self.mem_file.seek(car_ptr + 0x830)
                pri = struct.unpack("<Q", self.mem_file.read(8))[0]
                if not (0x10000000 < pri < 0x7fffffffffff):
                    # Fallback to Pawn::PlayerReplicationInfo at 0x410
                    self.mem_file.seek(car_ptr + 0x410)
                    pri = struct.unpack("<Q", self.mem_file.read(8))[0]

            if not (0x10000000 < pri < 0x7fffffffffff) and pc_ptr:
                # PlayerController_TA::PRI is at pc + 0x9A8
                self.mem_file.seek(pc_ptr + 0x9A8)
                pri = struct.unpack("<Q", self.mem_file.read(8))[0]

            if not (0x10000000 < pri < 0x7fffffffffff):
                return 0

            # PlayerReplicationInfo::Team is at pri + 0x2B0
            self.mem_file.seek(pri + 0x2B0)
            team_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
            if not (0x10000000 < team_ptr < 0x7fffffffffff):
                return 0

            # TeamInfo::TeamIndex is at team_ptr + 0x280
            self.mem_file.seek(team_ptr + 0x280)
            t = struct.unpack("<i", self.mem_file.read(4))[0]
            return t if t in (0, 1) else 0
        except Exception:
            return 0

    def find_cars(self, my_car_ptr, pc_ptr=None):
        """Locates all active Car_TA actors in PersistentLevel, partitioned into teammates and opponents."""
        teammates = []
        opponents = []
        if not my_car_ptr:
            return teammates, opponents

        pc = pc_ptr or getattr(self, "pc_ptr", None) or self.find_player_controller()
        my_team = self.get_car_team(my_car_ptr, pc_ptr=pc)
        if pc:
            try:
                self.mem_file.seek(pc + 0xC70)
                ge = struct.unpack("<Q", self.mem_file.read(8))[0]
                if ge:
                    self.mem_file.seek(ge + 0x350)
                    data_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
                    count = struct.unpack("<I", self.mem_file.read(4))[0]
                    if data_ptr and 0 < count <= 16:
                        self.mem_file.seek(data_ptr)
                        raw = self.mem_file.read(count * 8)
                        for p in struct.unpack(f"<{count}Q", raw):
                            if not p or p == my_car_ptr:
                                continue
                            if self.read_transform(p) is not None:
                                team = self.get_car_team(p)
                                if team == my_team:
                                    teammates.append(p)
                                else:
                                    opponents.append(p)
                        return teammates, opponents
            except Exception:
                pass

        # 2. Fast Fallback: Scan GObjects using cached car_class and level_class from my_car_ptr
        try:
            self.mem_file.seek(my_car_ptr + 0x50)
            car_class = struct.unpack("<Q", self.mem_file.read(8))[0]
            self.mem_file.seek(my_car_ptr + 0x40)
            outer = struct.unpack("<Q", self.mem_file.read(8))[0]
            level_class = None
            if outer:
                self.mem_file.seek(outer + 0x50)
                level_class = struct.unpack("<Q", self.mem_file.read(8))[0]

            self.resolve_globals()
            self.mem_file.seek(self.gobjects_ptr)
            raw_ptrs = self.mem_file.read(self.gobjects_count * 8)
            ptrs = struct.unpack(f"<{self.gobjects_count}Q", raw_ptrs)

            for ptr in reversed(ptrs[-30000:]):
                if not ptr or ptr == my_car_ptr:
                    continue
                self.mem_file.seek(ptr + 0x50)
                if struct.unpack("<Q", self.mem_file.read(8))[0] != car_class:
                    continue
                if level_class:
                    self.mem_file.seek(ptr + 0x40)
                    o = struct.unpack("<Q", self.mem_file.read(8))[0]
                    if not o: continue
                    self.mem_file.seek(o + 0x50)
                    if struct.unpack("<Q", self.mem_file.read(8))[0] != level_class:
                        continue
                if self.read_transform(ptr) is not None:
                    team = self.get_car_team(ptr)
                    if team == my_team:
                        teammates.append(ptr)
                    else:
                        opponents.append(ptr)
        except Exception:
            pass

        return teammates, opponents

    def find_opponents(self, my_car_ptr):
        """Backwards compatibility wrapper returning only true opponents."""
        _, opps = self.find_cars(my_car_ptr)
        return opps

    def find_active_entities(self):
        """Locates the active Player Car and Ball in the current PersistentLevel."""
        pc_ptr = self.find_player_controller()
        if pc_ptr:
            car_ptr, ball_ptr = self.get_entities_from_pc(pc_ptr)
            if car_ptr and ball_ptr:
                return car_ptr, ball_ptr

        # Fallback scan backwards through GObjects
        self.resolve_globals()
        self.mem_file.seek(self.gobjects_ptr)
        raw_ptrs = self.mem_file.read(self.gobjects_count * 8)
        ptrs = struct.unpack(f"<{self.gobjects_count}Q", raw_ptrs)

        car_ptr = None
        ball_ptr = None

        for ptr in reversed(ptrs):
            if not ptr:
                continue
            try:
                self.mem_file.seek(ptr + 0x48)
                name_idx, num, class_ptr = struct.unpack("<IIQ", self.mem_file.read(16))
                if not class_ptr:
                    continue

                self.mem_file.seek(class_ptr + 0x48)
                class_name = self.get_name(struct.unpack("<I", self.mem_file.read(4))[0])

                if class_name in ("Car_Freeplay_TA", "Car_TA") and not car_ptr:
                    self.mem_file.seek(ptr + 0x40)
                    outer_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
                    if outer_ptr:
                        self.mem_file.seek(outer_ptr + 0x48)
                        if self.get_name(struct.unpack("<I", self.mem_file.read(4))[0]) == "PersistentLevel":
                            car_ptr = ptr
                elif class_name == "Ball_TA" and not ball_ptr:
                    self.mem_file.seek(ptr + 0x40)
                    outer_ptr = struct.unpack("<Q", self.mem_file.read(8))[0]
                    if outer_ptr:
                        self.mem_file.seek(outer_ptr + 0x48)
                        if self.get_name(struct.unpack("<I", self.mem_file.read(4))[0]) == "PersistentLevel":
                            ball_ptr = ptr
                if car_ptr and ball_ptr:
                    break
            except Exception:
                continue

        return car_ptr, ball_ptr

    def read_transform(self, actor_ptr):
        if not actor_ptr:
            return None
        self.mem_file.seek(actor_ptr + OFFSET_LOCATION)
        x, y, z = struct.unpack("<fff", self.mem_file.read(12))

        self.mem_file.seek(actor_ptr + OFFSET_ROTATION)
        pitch, yaw, roll = struct.unpack("<iii", self.mem_file.read(12))

        self.mem_file.seek(actor_ptr + OFFSET_VELOCITY)
        vx, vy, vz = struct.unpack("<fff", self.mem_file.read(12))

        speed = (vx**2 + vy**2 + vz**2) ** 0.5
        # Convert Unreal Engine rotation units (65536 = 360 deg) to degrees
        deg_pitch = (pitch / 65536.0) * 360.0
        deg_yaw = (yaw / 65536.0) * 360.0
        deg_roll = (roll / 65536.0) * 360.0

        return {
            "pos": (x, y, z),
            "rot": (deg_pitch, deg_yaw, deg_roll),
            "vel": (vx, vy, vz),
            "speed": speed,
        }

    def close(self):
        self.mem_file.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rocket League Memory Reader")
    parser.add_argument("--once", action="store_true", help="Print position once and exit")
    args = parser.parse_args()

    pid = get_rocket_league_pid()
    if not pid:
        print("Rocket League process (RocketLeague.exe) not found!")
        sys.exit(1)

    reader = RLMemoryReader(pid)
    car_ptr, ball_ptr = reader.find_active_entities()
    if not car_ptr:
        print("Could not locate active car in PersistentLevel (are you in a match / freeplay?)")
        sys.exit(1)

    if args.once:
        car_data = reader.read_transform(car_ptr)
        cx, cy, cz = car_data["pos"]
        cp, cyw, cr = car_data["rot"]
        vx, vy, vz = car_data["vel"]
        speed = car_data["speed"]

        print(f"Player Car Location: X = {cx:.2f}, Y = {cy:.2f}, Z = {cz:.2f}")
        print(f"Player Car Rotation: Pitch = {cp:.1f}°, Yaw = {cyw:.1f}°, Roll = {cr:.1f}°")
        print(f"Player Car Velocity: Vx = {vx:.2f}, Vy = {vy:.2f}, Vz = {vz:.2f} (Total Speed: {speed:.2f} uu/s)")

        if ball_ptr:
            ball_data = reader.read_transform(ball_ptr)
            bx, by, bz = ball_data["pos"]
            print(f"Ball Location:       X = {bx:.2f}, Y = {by:.2f}, Z = {bz:.2f}")
        reader.close()
        return

    print(f"Connected to Rocket League process (PID: {pid})")
    print(f"GNames: {hex(reader.gnames_ptr)}, GObjects: {hex(reader.gobjects_ptr)} ({reader.gobjects_count} objects)")
    print(f"Found Player Car: {hex(car_ptr)}")
    if ball_ptr:
        print(f"Found Ball:       {hex(ball_ptr)}")

    print("\n--- Live Telemetry (Press Ctrl+C to exit) ---")
    try:
        while True:
            car_data = reader.read_transform(car_ptr)
            cx, cy, cz = car_data["pos"]
            cp, cyw, cr = car_data["rot"]
            speed = car_data["speed"]

            output = f"\r[Car] X: {cx:8.2f} | Y: {cy:8.2f} | Z: {cz:7.2f} (Speed: {speed:6.1f} uu/s | Yaw: {cyw:6.1f}°)"

            if ball_ptr:
                ball_data = reader.read_transform(ball_ptr)
                bx, by, bz = ball_data["pos"]
                output += f"  [Ball] X: {bx:8.2f} | Y: {by:8.2f} | Z: {bz:7.2f}"

            sys.stdout.write(output)
            sys.stdout.flush()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nExiting telemetry reader.")
    finally:
        reader.close()


if __name__ == "__main__":
    main()
