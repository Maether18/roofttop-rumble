"""Rooftop Rumble game rules. No networking code here.

The server creates one Game per room, calls apply_input() whenever a
player's input arrives, calls step() TICK_RATE times per second, and
sends snapshot() to the clients. Both TCP and UDP versions use this
exact file, so the rules are identical.

TODO: implement movement, platforms, attacks, knockback and KOs
(see the concept demo for the intended behaviour).
"""
from common.config import MATCH_SECONDS

ARENA_WIDTH = 960
ARENA_HEIGHT = 560
SPAWN_POINTS = [(300, 420), (660, 420), (480, 200)]


class Fighter:
    def __init__(self, name, x, y):
        self.name = name
        self.x, self.y = x, y
        self.vx, self.vy = 0.0, 0.0
        self.facing = 1
        self.damage = 0
        self.kos = 0
        self.falls = 0
        self.alive = True
        self.input = {"left": 0, "right": 0, "jump": 0, "punch": 0, "smash": 0}

    def to_dict(self):
        return {
            "name": self.name, "x": round(self.x, 1), "y": round(self.y, 1),
            "facing": self.facing, "damage": self.damage,
            "kos": self.kos, "falls": self.falls, "alive": self.alive,
        }


class Game:
    def __init__(self, player_names):
        self.fighters = {
            name: Fighter(name, *SPAWN_POINTS[i % len(SPAWN_POINTS)])
            for i, name in enumerate(player_names)
        }
        self.time_left = float(MATCH_SECONDS)
        self.tick = 0
        self.events = []  # hits, KOs... collected each step, sent reliably

    def apply_input(self, name, keys):
        if name in self.fighters:
            self.fighters[name].input.update(keys)

    def remove_player(self, name):
        """Called when a player disconnects: the match continues."""
        self.fighters.pop(name, None)
        self.events.append({"kind": "left", "name": name})

    def step(self, dt):
        self.tick += 1
        self.time_left = max(0.0, self.time_left - dt)
        # TODO: physics, attacks, knockback, KOs, respawns

    def is_over(self):
        return self.time_left <= 0 or len(self.fighters) < 2

    def ranking(self):
        return sorted(self.fighters.values(), key=lambda f: (-f.kos, f.falls))

    def snapshot(self):
        return {
            "tick": self.tick,
            "time_left": round(self.time_left, 1),
            "players": [f.to_dict() for f in self.fighters.values()],
        }

    def pop_events(self):
        events, self.events = self.events, []
        return events
