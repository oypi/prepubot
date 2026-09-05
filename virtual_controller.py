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
        # Triggers
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Z, 0)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_RZ, 0)
        # Buttons
        for btn in [ecodes.BTN_A, ecodes.BTN_B, ecodes.BTN_X, ecodes.BTN_Y, ecodes.BTN_TL, ecodes.BTN_TR]:
            self.ui.write(ecodes.EV_KEY, btn, 0)
        self.ui.syn()

    def apply_action(self, action, on_ground=True, car_z=17.0, time_in_decision=0.0):
        """
        Translates Nexto action array into gamepad events.
        action format: [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
        """
        if hasattr(action, "throttle"):
            action = [
                action.throttle,
                action.steer,
                action.pitch,
                action.yaw,
                action.roll,
                1 if action.jump else 0,
                1 if action.boost else 0,
                1 if action.handbrake else 0,
            ]
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

        if on_ground and not jump_val:
            # Normal ground driving: stick X steers, stick Y centered
            stick_x = int(steer_val * 32767)
            stick_y = 0
            roll_left = 0
            roll_right = 0
            air_roll_btn = 0
        else:
            # Airborne or jumping/flipping
            if jump_val:
                # DODGE / FLIP frame:
                # In Rocket League, DodgeForward is -pitch, DodgeRight is roll!
                # If roll is ~0, stick_x MUST BE 0 to guarantee clean straight flips/flicks!
                # Never let residual steering or yaw trigger an accidental sideflip!
                if abs(roll_val) > 0.1:
                    stick_x = int(roll_val * 32767)
                else:
                    stick_x = 0

                # Xbox gamepad: ABS_Y negative = stick UP = pitch nose down
                stick_y = int(pitch_val * 32767)
                roll_left = 0
                roll_right = 0
                air_roll_btn = 0
            else:
                # Free aerial flight / flip cancel / recovery
                if abs(roll_val) > 0.1:
                    # When rolling in the air, deflect stick_x and engage Air Roll (BTN_X + directional bumpers)
                    stick_x = int(roll_val * 32767)
                    air_roll_btn = 1
                    roll_left = 1 if roll_val < -0.1 else 0
                    roll_right = 1 if roll_val > 0.1 else 0
                else:
                    stick_x = int(yaw_val * 32767) if abs(yaw_val) > 0.1 else int(steer_val * 32767)
                    air_roll_btn = 0
                    roll_left = 0
                    roll_right = 0

                stick_y = int(pitch_val * 32767)

        # Powerslide on ground or Air Roll in the air
        btn_x = 1 if ((handbrake_val and on_ground) or air_roll_btn) else 0

        self.ui.write(ecodes.EV_ABS, ecodes.ABS_X, max(-32768, min(32767, stick_x)))
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Y, max(-32768, min(32767, stick_y)))
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_A, 1 if jump_val else 0)
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_B, 1 if boost_val else 0)
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_X, btn_x)
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
