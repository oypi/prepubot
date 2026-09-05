"""
memory_controller.py - Direct Memory Input Controller for Rocket League (Linux)

Uses the Linux kernel's process_vm_writev system call to directly inject
native FVehicleInputs (32 bytes) into Rocket League's PlayerController_TA and Vehicle_TA memory.

Offsets in current Rocket League x64:
- PlayerController_TA::VehicleInput = pc + 0x9B0
- Vehicle_TA::Input = car + 0x7FC

Struct FVehicleInputs layout (32 bytes):
- 0x00: float Throttle       [-1.0 .. 1.0]
- 0x04: float Steer          [-1.0 .. 1.0]
- 0x08: float Pitch          [-1.0 .. 1.0]
- 0x0C: float Yaw            [-1.0 .. 1.0]
- 0x10: float Roll           [-1.0 .. 1.0]
- 0x14: float DodgeForward   [-1.0 .. 1.0] (-Pitch)
- 0x18: float DodgeRight     [-1.0 .. 1.0] (Roll or Yaw)
- 0x1C: uint32 Flags:
        bit 0: Handbrake
        bit 1: Jump
        bit 2: ActivateBoost
        bit 3: HoldingBoost
        bit 4: UseItem
"""

import ctypes
import struct
import threading
import time
from typing import Optional, Union, Sequence

libc = ctypes.CDLL("libc.so.6")


class IOVec(ctypes.Structure):
    _fields_ = [
        ("iov_base", ctypes.c_void_p),
        ("iov_len", ctypes.c_size_t),
    ]


_process_vm_writev = libc.process_vm_writev
_process_vm_writev.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(IOVec),
    ctypes.c_ulong,
    ctypes.POINTER(IOVec),
    ctypes.c_ulong,
    ctypes.c_ulong,
]
_process_vm_writev.restype = ctypes.c_long


class MemoryController:
    def __init__(self, pid: int, pc_ptr: Optional[int] = None, car_ptr: Optional[int] = None, write_rate_hz: float = 1000.0):
        self.pid = pid
        self.pc_ptr = pc_ptr
        self.car_ptr = car_ptr
        self.write_rate_hz = max(120.0, min(write_rate_hz, 2000.0))
        self.sleep_interval = 1.0 / self.write_rate_hz

        self.current_payload = bytearray(32)
        self.lock = threading.Lock()
        self.running = False
        self.worker_thread: Optional[threading.Thread] = None

        self.start_worker()

    def update_pointers(self, pc_ptr: Optional[int] = None, car_ptr: Optional[int] = None):
        with self.lock:
            if pc_ptr is not None:
                self.pc_ptr = pc_ptr
            if car_ptr is not None:
                self.car_ptr = car_ptr

    def start_worker(self):
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._writer_loop, daemon=True, name="MemoryWriterLoop")
            self.worker_thread.start()

    def stop_worker(self):
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=0.2)
        self.reset()

    def _writer_loop(self):
        while self.running:
            with self.lock:
                pc = self.pc_ptr
                car = self.car_ptr
                payload = bytes(self.current_payload)

            if not pc and not car:
                time.sleep(0.01)
                continue

            buf = ctypes.create_string_buffer(payload)
            l_iov = IOVec(ctypes.cast(buf, ctypes.c_void_p), 32)

            # Inject directly into PlayerController_TA at 0x9B0
            if pc:
                r_iov_pc = IOVec(ctypes.c_void_p(pc + 0x9B0), 32)
                _process_vm_writev(self.pid, ctypes.byref(l_iov), 1, ctypes.byref(r_iov_pc), 1, 0)

            time.sleep(self.sleep_interval)

    def set_controls(self, action_or_controller: Union[Sequence[float], object]):
        """
        Accepts either:
        - 8-element array: [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
        - SimpleControllerState object with attributes .throttle, .steer, .pitch, .yaw, .roll, .jump, .boost, .handbrake
        """
        if hasattr(action_or_controller, "throttle"):
            # SimpleControllerState
            thr = float(action_or_controller.throttle)
            steer = float(action_or_controller.steer)
            pitch = float(action_or_controller.pitch)
            yaw = float(action_or_controller.yaw)
            roll = float(action_or_controller.roll)
            jump = bool(action_or_controller.jump)
            boost = bool(action_or_controller.boost)
            handbrake = bool(action_or_controller.handbrake)
            use_item = bool(getattr(action_or_controller, "use_item", False))
        else:
            # Vector [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
            act = list(action_or_controller)
            thr = float(act[0])
            steer = float(act[1])
            pitch = float(act[2])
            yaw = float(act[3])
            roll = float(act[4])
            jump = bool(act[5] > 0.5) if len(act) > 5 else False
            boost = bool(act[6] > 0.5) if len(act) > 6 else False
            handbrake = bool(act[7] > 0.5) if len(act) > 7 else False
            use_item = False

        # In Rocket League's FVehicleInputs:
        # DodgeForward = -pitch (positive = front flip)
        # DodgeRight = roll if abs(roll) > 0.1 else yaw
        dodge_fwd = -pitch
        dodge_right = roll if abs(roll) > 0.1 else yaw

        flags = 0
        if handbrake:
            flags |= 0x01
        if jump:
            flags |= 0x02
        if boost:
            flags |= 0x04 | 0x08  # ActivateBoost + HoldingBoost
        if use_item:
            flags |= 0x10

        packed = struct.pack("<7fI", thr, steer, pitch, yaw, roll, dodge_fwd, dodge_right, flags)
        with self.lock:
            self.current_payload[:] = packed

    def apply_action(self, action, on_ground: bool = True, car_z: float = 17.0, time_in_decision: float = 0.0):
        """Uniform controller interface matching VirtualXboxController."""
        self.set_controls(action)

    def reset(self):
        """Immediately zeroes all inputs and sends blank payload."""
        zero = struct.pack("<7fI", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
        with self.lock:
            self.current_payload[:] = zero
            pc = self.pc_ptr
            car = self.car_ptr

        buf = ctypes.create_string_buffer(zero)
        l_iov = IOVec(ctypes.cast(buf, ctypes.c_void_p), 32)
        if pc:
            r_iov_pc = IOVec(ctypes.c_void_p(pc + 0x9B0), 32)
            _process_vm_writev(self.pid, ctypes.byref(l_iov), 1, ctypes.byref(r_iov_pc), 1, 0)

    def close(self):
        self.stop_worker()
