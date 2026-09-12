"""
Virtual Xbox 360 Controller for Rocket League on Linux
Uses /dev/uinput via evdev to emulate a standard XInput gamepad.
"""

import math
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
        self.prev_jump = False
        self.air_tick = 0
        self.reset()

    def reset(self):
        """Release all buttons and center analog axes."""
        self.air_tick = 0
        try:
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
        except Exception:
            pass

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

        try:
            throttle_val = float(throttle)
            if math.isnan(throttle_val) or math.isinf(throttle_val): throttle_val = 0.0
        except (TypeError, ValueError):
            throttle_val = 0.0

        try:
            steer_val = float(steer)
            if math.isnan(steer_val) or math.isinf(steer_val): steer_val = 0.0
        except (TypeError, ValueError):
            steer_val = 0.0

        try:
            pitch_val = float(pitch)
            if math.isnan(pitch_val) or math.isinf(pitch_val): pitch_val = 0.0
        except (TypeError, ValueError):
            pitch_val = 0.0

        try:
            yaw_val = float(yaw)
            if math.isnan(yaw_val) or math.isinf(yaw_val): yaw_val = 0.0
        except (TypeError, ValueError):
            yaw_val = 0.0

        try:
            roll_val = float(roll)
            if math.isnan(roll_val) or math.isinf(roll_val): roll_val = 0.0
        except (TypeError, ValueError):
            roll_val = 0.0

        jump_val = bool(jump)
        boost_val = bool(boost)
        handbrake_val = bool(handbrake)

        # Triggers
        rt = int(max(0.0, min(1.0, throttle_val)) * 255)
        lt = int(max(0.0, min(1.0, -throttle_val)) * 255)

        self.ui.write(ecodes.EV_ABS, ecodes.ABS_RZ, rt)
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Z, lt)

        if on_ground:
            self.air_tick = 0
        else:
            self.air_tick += 1

        if on_ground and not jump_val:
            # Normal driving on floor or wall:
            # stick_x drives the wheels (steering).
            # stick_y is kept neutral (0) so car tracks flat and never drags nose on turf
            stick_x = int(steer_val * 32767)
            stick_y = 0
            air_roll_btn = 0
        else:
            # Airborne, or jumping/flipping/wavedashing:
            if jump_val:
                # DODGE / FLIP / WAVEDASH frame:
                # Lateral dodge direction: prefer roll, then yaw, then steering into the jump
                if abs(roll_val) > 0.1:
                    stick_x = int(roll_val * 32767)
                elif abs(yaw_val) > 0.1:
                    stick_x = int(yaw_val * 32767)
                else:
                    stick_x = int(steer_val * 32767)

                # Xbox gamepad: ABS_Y negative = stick UP = pitch nose down
                stick_y = int(pitch_val * 32767)
                air_roll_btn = 0
            else:
                # Free aerial flight / flip cancel / aerial recovery:
                has_roll = abs(roll_val) > 0.15
                has_yaw = abs(yaw_val) > 0.15

                if has_roll and has_yaw:
                    # Both yaw and roll requested by Nexto policy:
                    # If one axis is clearly dominant, execute that axis
                    if abs(yaw_val) > 1.8 * abs(roll_val):
                        stick_x = int(yaw_val * 32767)
                        air_roll_btn = 0
                    elif abs(roll_val) > 1.8 * abs(yaw_val):
                        stick_x = int(roll_val * 32767)
                        air_roll_btn = 1
                    else:
                        # Time-share across 2-tick intervals (16ms each at 120Hz).
                        # Angular momentum smoothly integrates both torques, allowing simultaneous
                        # nose alignment (yaw) and wheel leveling (roll)!
                        if (self.air_tick % 4) < 2:
                            stick_x = int(yaw_val * 32767)
                            air_roll_btn = 0
                        else:
                            stick_x = int(roll_val * 32767)
                            air_roll_btn = 1
                elif has_roll:
                    stick_x = int(roll_val * 32767)
                    air_roll_btn = 1
                else:
                    stick_x = int(yaw_val * 32767) if has_yaw else int(steer_val * 32767)
                    air_roll_btn = 0

                stick_y = int(pitch_val * 32767)

        # Powerslide:
        # Pre-engage powerslide when landing (car_z < 120.0 with handbrake requested)
        # so wheels touch down frictionlessly without scrubbing momentum!
        is_landing = (car_z < 120.0) and handbrake_val
        btn_x = 1 if ((handbrake_val and on_ground) or is_landing or air_roll_btn) else 0

        try:
            self.ui.write(ecodes.EV_ABS, ecodes.ABS_X, max(-32768, min(32767, stick_x)))
            self.ui.write(ecodes.EV_ABS, ecodes.ABS_Y, max(-32768, min(32767, stick_y)))
            self.ui.write(ecodes.EV_KEY, ecodes.BTN_A, 1 if jump_val else 0)
            self.ui.write(ecodes.EV_KEY, ecodes.BTN_B, 1 if boost_val else 0)
            self.ui.write(ecodes.EV_KEY, ecodes.BTN_X, btn_x)
            self.ui.write(ecodes.EV_KEY, ecodes.BTN_TL, 0)
            self.ui.write(ecodes.EV_KEY, ecodes.BTN_TR, 0)
            self.ui.syn()
        except Exception:
            pass
        self.prev_jump = jump_val

    def close(self):
        try:
            self.reset()
            self.ui.close()
        except Exception:
            pass
