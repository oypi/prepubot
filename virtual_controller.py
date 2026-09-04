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
        else:
            # Airborne or jumping/flipping
            if jump_val:
                # DODGE / FLIP frame: stick controls flip direction
                if abs(roll_val) > 0.1:
                    stick_x = int(roll_val * 32767)
                elif abs(yaw_val) > 0.1:
                    stick_x = int(yaw_val * 32767)
                else:
                    stick_x = int(steer_val * 32767)

                # Xbox gamepad: ABS_Y negative = stick UP = pitch nose down
                stick_y = int(pitch_val * 32767)
                roll_left = 0
                roll_right = 0
            else:
                # Free aerial flight / flip cancel / recovery
                stick_x = int(yaw_val * 32767) if abs(yaw_val) > 0.1 else int(steer_val * 32767)
                stick_y = int(pitch_val * 32767)
                roll_left = 1 if roll_val < -0.1 else 0
                roll_right = 1 if roll_val > 0.1 else 0

        self.ui.write(ecodes.EV_ABS, ecodes.ABS_X, max(-32768, min(32767, stick_x)))
        self.ui.write(ecodes.EV_ABS, ecodes.ABS_Y, max(-32768, min(32767, stick_y)))
        self.ui.write(ecodes.EV_KEY, ecodes.BTN_A, 1 if jump_val else 0)
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


class VirtualKeyboardController:
    """
    Virtual Keyboard & Mouse Controller for Rocket League on Linux.
    Emulates standard PC KBM bindings via /dev/uinput:
    - W: Throttle Forward (ground) / Pitch Down (air/flip)
    - S: Throttle Reverse (ground) / Pitch Up (air/flip)
    - A: Steer Left (ground) / Yaw Left (air) / Side Flip Left (flip)
    - D: Steer Right (ground) / Yaw Right (air) / Side Flip Right (flip)
    - Q: Air Roll Left (air)
    - E: Air Roll Right (air)
    - Right Mouse Button: Jump (Note: Spacebar is reserved for Camera Toggle, never touched)
    - Left Mouse Button: Boost
    - Left Shift: Powerslide (ground & landing)
    """
    def __init__(self):
        cap = {
            ecodes.EV_KEY: [
                ecodes.KEY_W,
                ecodes.KEY_A,
                ecodes.KEY_S,
                ecodes.KEY_D,
                ecodes.KEY_Q,
                ecodes.KEY_E,
                ecodes.KEY_LEFTSHIFT,
                ecodes.BTN_LEFT,   # Boost (LMB)
                ecodes.BTN_RIGHT,  # Jump (RMB)
            ],
            ecodes.EV_REL: [
                ecodes.REL_X,
                ecodes.REL_Y,
            ],
        }
        self.ui = UInput(cap, name="PrepuBot Virtual KBM", vendor=0x0001, product=0x0001, version=0x100)
        self.prev_jump = False
        self.reset()

    def reset(self):
        """Releases all pressed keys and mouse buttons."""
        for key in [
            ecodes.KEY_W, ecodes.KEY_A, ecodes.KEY_S, ecodes.KEY_D,
            ecodes.KEY_Q, ecodes.KEY_E, ecodes.KEY_LEFTSHIFT,
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT
        ]:
            self.ui.write(ecodes.EV_KEY, key, 0)
        self.ui.syn()
        self.prev_jump = False

    def apply_action(self, action, on_ground=True, car_z=17.0, time_in_decision=0.0):
        """
        Translates Nexto action array [throttle, steer, pitch, yaw, roll, jump, boost, handbrake]
        into instantaneous digital keyboard and mouse events matching standard Rocket League KBM bindings.
        """
        throttle, steer, pitch, yaw, roll, jump, boost, handbrake = action
        press = set()

        throttle_val = float(throttle)
        steer_val = float(steer)
        pitch_val = float(pitch)
        yaw_val = float(yaw)
        roll_val = float(roll)
        jump_val = bool(jump)
        boost_val = bool(boost)
        handbrake_val = bool(handbrake)

        # 1. THROTTLE & STEER (Ground driving when not flipping)
        if on_ground and not jump_val:
            if throttle_val > 0.1:
                press.add(ecodes.KEY_W)
            elif throttle_val < -0.1:
                press.add(ecodes.KEY_S)

            if steer_val < -0.1:
                press.add(ecodes.KEY_A)
            elif steer_val > 0.1:
                press.add(ecodes.KEY_D)
        else:
            # Airborne or jumping/flipping:
            # Keep forward drive active when jumping off ground unless pitching up
            if throttle_val > 0.1 and pitch_val <= 0.1:
                press.add(ecodes.KEY_W)

            # 2. PITCH (In Air OR Flip/Dodge frame)
            # In Rocket League KBM: W is pitch DOWN (nose down), S is pitch UP (nose up)
            if pitch_val < -0.1:
                press.add(ecodes.KEY_W)
            elif pitch_val > 0.1:
                press.add(ecodes.KEY_S)

            # 3. YAW & ROLL
            if jump_val:
                # FLIP / DODGE frame:
                # Side/diagonal flip in Rocket League KBM is executed with A (left) or D (right) + Jump
                if roll_val < -0.1 or yaw_val < -0.1:
                    press.add(ecodes.KEY_A)
                elif roll_val > 0.1 or yaw_val > 0.1:
                    press.add(ecodes.KEY_D)
            else:
                # FREE AERIAL FLIGHT:
                # Yaw via A / D
                if yaw_val < -0.1 or steer_val < -0.1:
                    press.add(ecodes.KEY_A)
                elif yaw_val > 0.1 or steer_val > 0.1:
                    press.add(ecodes.KEY_D)

                # Directional Air Roll via Q / E (independent of yaw)
                if roll_val < -0.1:
                    press.add(ecodes.KEY_Q)
                elif roll_val > 0.1:
                    press.add(ecodes.KEY_E)

        # 4. JUMP: Right Mouse Button (RMB)
        if jump_val:
            press.add(ecodes.BTN_RIGHT)

        # 5. BOOST: Left Mouse Button (LMB)
        if boost_val:
            press.add(ecodes.BTN_LEFT)

        # 6. POWERSLIDE: Left Shift
        # Only press Shift on ground or near landing (z < 50.0)
        # Never hold Shift high in air so A/D remains pure Yaw without triggering free air roll
        if handbrake_val and (on_ground or car_z < 50.0):
            press.add(ecodes.KEY_LEFTSHIFT)

        all_keys = [
            ecodes.KEY_W, ecodes.KEY_A, ecodes.KEY_S, ecodes.KEY_D,
            ecodes.KEY_Q, ecodes.KEY_E, ecodes.KEY_LEFTSHIFT,
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT
        ]
        for k in all_keys:
            self.ui.write(ecodes.EV_KEY, k, 1 if k in press else 0)

        self.ui.syn()
        self.prev_jump = jump_val

    def close(self):
        try:
            self.reset()
            self.ui.close()
        except Exception:
            pass
