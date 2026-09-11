"""
Events definitions for Linux RLSDK drop-in.
"""

class EventPlayerTick:
    def __init__(self, delta_time: float = 1.0 / 120.0):
        self.delta_time = delta_time


class EventRoundActiveStateChanged:
    def __init__(self, is_active: bool = True):
        self.is_active = is_active


class EventKeyPressed:
    def __init__(self, key: str = "", key_type: str = "pressed"):
        self.key = key
        self.type = key_type


class EventGameEventDestroyed:
    pass
