"""
Virtual Xbox 360 Controller for Rocket League on Linux
Uses /dev/uinput via evdev to emulate a standard XInput gamepad.
"""

import evdev
from evdev import UInput, ecodes, AbsInfo


class VirtualXboxController:
    def __init__(self):
        cap = {
            ecodes.EV_KEY: [
                ecodes.BTN_A, ecodes.BTN_B, ecodes.BTN_X, ecodes.BTN_Y,
                ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_SELECT, ecodes.BTN_START,
                ecodes.BTN_MODE, ecodes.BTN_THUMBL, ecodes.BTN_THUMBR
            ],
            ecodes.EV_ABS: [
                (ecodes.ABS_X, AbsInfo(value=0, min=-32768, max=32767, fuzz=0, flat=0, resolution=0)),
                (ecodes.ABS_Y, AbsInfo(value=0, min=-32768, max=32767, fuzz=0, flat=0, resolution=0)),
                (ecodes.ABS_RX, AbsInfo(value=0, min=-32768, max=32767, fuzz=0, flat=0, resolution=0)),
                (ecodes.ABS_RY, AbsInfo(value=0, min=-32768, max=32767, fuzz=0, flat=0, resolution=0)),
                (ecodes.ABS_Z, AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)),     # LT
                (ecodes.ABS_RZ, AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)),    # RT
                (ecodes.ABS_HAT0X, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
                (ecodes.ABS_HAT0Y, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
            ]
        }
        self.ui = UInput(cap, name="Microsoft X-Box 360 pad", vendor=0x045e, product=0x028e, version=0x110)
        self.reset()

    def reset(self):
        """Release all buttons and center analog axes."""
        # Analog sticks
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_X, 0)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Y, 0)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_RX, 0)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_RY, 0)
        self.prev_jump = False
        # Triggers
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Z, 0)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_RZ, 0)
        # Buttons
        for btn in [ecodes.BTN_A, ecodes.BTN_B, ecodes.BTN_X, ecodes.BTN_Y, ecodes.BTN_TL, ecodes.BTN_TR]:
            self.ui.write(ecodes.EV_KEY, btn, 0)
        self.ui.syn()

    def apply_action(self, action, on_ground=True, car_z=17.0, time_in_decision=0.0, is_kickoff=False):
        """
        Translates Nexto action array into gamepad events.
        action format: [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
        """
        throttle, steer, pitch, yaw, roll, jump, boost, handbrake = action

        throttle_val = float(throttle)
        steer_val = float(steer)
        pitch_val = float(pitch)
        yaw_val = float(yaw)
        roll_val = float(roll)
        jump_val = bool(jump)
        boost_val = bool(boost)
        handbrake_val = bool(handbrake)

        # Triggers
        rt = int(max(0.0, throttle_val) * 255)
        lt = int(max(0.0, -throttle_val) * 255)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_RZ, rt)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Z, lt)

        # Jump button pulse handling:
        # If jumping while airborne and jump was held in the prior decision step,
        # release BTN_A for 1 physics tick (~8.33ms) so Rocket League detects the rising edge
        # required to trigger bJump_JustPressed for double jumps and dodges.
        if jump_val:
            if not on_ground and getattr(self, "prev_jump", False) and time_in_decision < 0.012:
                jump_btn = 0
            else:
                jump_btn = 1
        else:
            jump_btn = 0

        if on_ground:
            # ON GROUND: stick X controls steering, stick Y centered (no pitch)
            stick_x = int(steer_val * 32767)
            stick_y = 0
            roll_left = 0
            roll_right = 0
        else:
            # AIRBORNE
            if jump_val:
                # DODGE / FLIP / DOUBLE JUMP frame
                # Nexto conveys flip direction strictly via roll (roll > 0 = right, roll < 0 = left).
                # If roll is 0, the lateral flip deflection MUST be 0 to allow clean vertical double jumps.
                if abs(roll_val) > 0.1:
                    stick_x = int(roll_val * 32767)
                elif is_kickoff and abs(yaw_val) > 0.1:
                    stick_x = int(yaw_val * 32767)
                else:
                    stick_x = 0  # Center stick X! Eliminates accidental side flips during double jumps & aerial contests

                # Xbox gamepad: ABS_Y negative = stick UP = pitch nose down
                stick_y = int(pitch_val * 32767)
                roll_left = 0
                roll_right = 0
            else:
                # Free aerial flight / flip cancel / recovery
                stick_x = int(yaw_val * 32767) if abs(yaw_val) > 0.05 else int(steer_val * 32767)
                stick_y = int(pitch_val * 32767)
                roll_left = 1 if roll_val < -0.1 else 0
                roll_right = 1 if roll_val > 0.1 else 0

        self.ui.write(ecodes.EV_ABS, ecodes.ABS_X, max(-32768, min(32767, stick_x)))
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Y, max(-32768, min(32767, stick_y)))
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_A, jump_btn)
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_B, 1 if boost_val else 0)
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_X, 1 if (handbrake_val and (on_ground or car_z < 50.0)) else 0)
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_TL, roll_left)
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_TR, roll_right)
        self.ui.syn()
        self.prev_jump = jump_val

    def close(self):
        try:
            self.reset()
            self.ui.close()
        except Exception:
            pass
