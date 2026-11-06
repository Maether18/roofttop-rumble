"""Rooftop Rumble game rules. No networking code here.

The server creates one Game per room, calls apply_input() when a
player's INPUT arrives, calls step() TICK_RATE times per second, sends
snapshot() as STATE and pop_events() as EVENT messages. Both the TCP
and UDP versions use this exact file, so the rules are identical.

Rules:
- Move, double jump, drop through thin platforms, punch, charged smash.
- Every hit adds damage %. More damage means more knockback.
- Leaving the screen is a fall. The last player who hit you within
  6 seconds gets the KO. Fallen players respawn after 1.5 s.
- Most KOs when the timer ends wins (ties broken by fewer falls).

Inputs: "left", "right", "down" are held keys (0 or 1).
"jump_n", "punch_n", "smash_n" are press COUNTERS: the client adds 1
each time the key is pressed. The server compares with the last value
it saw, so a press is never missed or doubled, even if some INPUT
packets are lost or duplicated (important for the UDP version).
"""
import math

from common.config import MATCH_SECONDS

ARENA_WIDTH = 960
ARENA_HEIGHT = 560

# x1, x2, y, solid (solid = you can't drop through it)
PLATFORMS = [
    (170, 790, 420, True),    # the rooftop
    (250, 390, 305, False),
    (570, 710, 305, False),
    (410, 550, 200, False),
]
SPAWN_POINTS = [(300, 420), (660, 420), (480, 200)]
RESPAWN_POINTS = [(480, 200), (320, 305), (640, 305)]

GRAVITY = 1900
MAX_FALL = 950
RESPAWN_DELAY = 1.5
SPAWN_SHIELD = 1.6
KO_CREDIT_WINDOW = 6.0

ATTACKS = {
    # dur: total time, a..b: active window, dx/dy/w/h: hitbox,
    # dmg: damage %, base/scale: knockback, ang: launch angle, stun: hitstun
    "punch": dict(dur=.24, a=.05, b=.13, dx=30, dy=-48, w=42, h=28, dmg=4,
                  base=150, scale=2.6, ang=.35, stun=.18, freeze=2),
    "smash": dict(dur=.52, a=.22, b=.34, dx=36, dy=-44, w=58, h=42, dmg=13,
                  base=330, scale=6.8, ang=.72, stun=.32, freeze=6),
}


class Fighter:
    def __init__(self, name, slot, x, y):
        self.name = name
        self.slot = slot              # 0, 1, 2: the client picks a colour from it
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0
        self.facing = 1 if x < ARENA_WIDTH / 2 else -1
        self.ground = False
        self.jumps = 2
        self.damage = 0
        self.kos = 0
        self.falls = 0
        self.alive = True
        self.respawn_at = 0.0
        self.shield_until = 1.0
        self.stun = 0.0
        self.drop = 0.0
        self.cooldown = 0.0
        self.attack = None            # {"kind", "t", "hit": set()}
        self.last_hit_by = None
        self.last_hit_at = -99.0
        # input
        self.held = {"left": 0, "right": 0, "down": 0}
        self.counters = {"jump": 0, "punch": 0, "smash": 0}
        self.pressed = {"jump": False, "punch": False, "smash": False}

    def to_dict(self, clock):
        return {
            "name": self.name, "slot": self.slot,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "vx": round(self.vx), "vy": round(self.vy),
            "facing": self.facing, "ground": self.ground,
            "damage": self.damage, "kos": self.kos, "falls": self.falls,
            "alive": self.alive, "stun": self.stun > 0,
            "shield": clock < self.shield_until,
            "attack": ({"kind": self.attack["kind"], "t": round(self.attack["t"], 3)}
                       if self.attack else None),
        }


class Game:
    def __init__(self, player_names, duration=MATCH_SECONDS):
        self.fighters = {}
        for i, name in enumerate(player_names):
            x, y = SPAWN_POINTS[i % len(SPAWN_POINTS)]
            self.fighters[name] = Fighter(name, i, x, y)
        self.duration = float(duration)
        self.time_left = float(duration)
        self.clock = 0.0              # seconds since the match started
        self.tick = 0
        self.freeze = 0               # hit-stop frames after a hit
        self.events = []

    # ---------- input ----------
    def apply_input(self, name, keys):
        f = self.fighters.get(name)
        if f is None:
            return
        for k in f.held:
            f.held[k] = 1 if keys.get(k) else 0
        for k in f.counters:
            n = keys.get(k + "_n")
            if isinstance(n, int) and n > f.counters[k]:
                f.counters[k] = n
                f.pressed[k] = True

    def remove_player(self, name):
        """A player disconnected or left: the match continues without them."""
        if self.fighters.pop(name, None):
            self.events.append({"kind": "left", "name": name})

    # ---------- simulation ----------
    def step(self, dt):
        self.tick += 1
        self.clock += dt
        self.time_left = max(0.0, self.time_left - dt)
        if self.freeze > 0:
            self.freeze -= 1
            return
        fighters = list(self.fighters.values())
        for f in fighters:
            if not f.alive:
                if self.clock >= f.respawn_at:
                    self._respawn(f)
                continue
            self._move(f, dt)
            self._attack(f, fighters, dt)
        self._separate(fighters)
        for f in fighters:
            if f.alive and (f.x < -130 or f.x > ARENA_WIDTH + 130
                            or f.y > ARENA_HEIGHT + 150 or f.y < -280):
                self._fall(f)

    def _move(self, f, dt):
        move = f.held["right"] - f.held["left"]
        if f.stun > 0:
            f.stun -= dt
            f.vx *= .995
        else:
            acc = 2600 if f.ground else 1500
            top = 270 if f.ground else 250
            slow = .35 if f.attack and f.ground else 1
            if move:
                f.vx = max(-top, min(top, f.vx + move * acc * dt * slow))
                if not f.attack:
                    f.facing = move
            else:
                f.vx *= .78 if f.ground else .98
            if f.pressed["jump"] and f.jumps > 0:
                f.vy = -660 if f.ground else -600
                f.jumps -= 1
                f.ground = False
            if f.held["down"] and f.ground and self._on_thin_platform(f):
                f.drop, f.ground = .22, False
                f.y += 2
            if f.held["down"] and not f.ground and f.vy > 0:
                f.vy += 1400 * dt
            if f.pressed["punch"]:
                self._start_attack(f, "punch")
            elif f.pressed["smash"]:
                self._start_attack(f, "smash")
        for k in f.pressed:
            f.pressed[k] = False

        f.cooldown -= dt
        f.drop -= dt
        f.vy = min(f.vy + GRAVITY * dt, MAX_FALL)
        prev_y = f.y
        f.x += f.vx * dt
        f.y += f.vy * dt
        was_ground, f.ground = f.ground, False
        if f.vy >= 0:
            for x1, x2, py, solid in PLATFORMS:
                if x1 <= f.x <= x2 and prev_y <= py + 1 and f.y >= py and (solid or f.drop <= 0):
                    f.y, f.vy, f.ground, f.jumps = py, 0.0, True, 2
                    if f.stun > 0:
                        f.stun = min(f.stun, .08)
                        f.vx *= .5
                    break

    def _on_thin_platform(self, f):
        return any(not solid and abs(f.y - py) < 2 and x1 <= f.x <= x2
                   for x1, x2, py, solid in PLATFORMS)

    def _start_attack(self, f, kind):
        if f.attack is None and f.cooldown <= 0 and f.stun <= 0:
            f.attack = {"kind": kind, "t": 0.0, "hit": set()}

    def _attack(self, f, fighters, dt):
        if f.attack is None:
            return
        A = ATTACKS[f.attack["kind"]]
        f.attack["t"] += dt
        t = f.attack["t"]
        if A["a"] <= t <= A["b"]:
            hx = f.x + f.facing * A["dx"]
            hy = f.y + A["dy"]
            for o in fighters:
                if (o is f or not o.alive or o.name in f.attack["hit"]
                        or self.clock < o.shield_until):
                    continue
                if abs(o.x - hx) < A["w"] / 2 + 12 and abs((o.y - 34) - hy) < A["h"] / 2 + 26:
                    f.attack["hit"].add(o.name)
                    self._hit(f, o, A)
        if f.attack is not None and f.attack["t"] >= A["dur"]:
            f.attack = None
            f.cooldown = .07

    def _hit(self, attacker, victim, A):
        victim.damage += A["dmg"]
        kb = A["base"] + victim.damage * A["scale"]
        victim.vx = attacker.facing * math.cos(A["ang"]) * kb
        victim.vy = -math.sin(A["ang"]) * kb
        victim.ground = False
        victim.stun = A["stun"] + kb / 2600
        victim.attack = None
        victim.facing = -attacker.facing
        victim.last_hit_by = attacker.name
        victim.last_hit_at = self.clock
        self.freeze = A["freeze"]
        self.events.append({
            "kind": "hit", "attacker": attacker.name, "victim": victim.name,
            "attack": attacker.attack["kind"], "dmg": A["dmg"], "damage": victim.damage,
            "x": round((attacker.x + victim.x) / 2), "y": round(victim.y - 38),
        })

    def _separate(self, fighters):
        for i, a in enumerate(fighters):
            for b in fighters[i + 1:]:
                if not (a.alive and b.alive) or a.stun > 0 or b.stun > 0:
                    continue
                dx = b.x - a.x
                if abs(dx) < 22 and abs(a.y - b.y) < 50:
                    push = (22 - abs(dx)) / 2 * (1 if dx >= 0 else -1)
                    a.x -= push
                    b.x += push

    def _fall(self, f):
        f.alive = False
        f.falls += 1
        f.respawn_at = self.clock + RESPAWN_DELAY
        killer = None
        if f.last_hit_by in self.fighters and self.clock - f.last_hit_at < KO_CREDIT_WINDOW:
            killer = self.fighters[f.last_hit_by]
            killer.kos += 1
        self.events.append({
            "kind": "ko", "victim": f.name, "by": killer.name if killer else None,
            "x": round(min(max(f.x, 10), ARENA_WIDTH - 10)),
            "y": round(min(max(f.y, 10), ARENA_HEIGHT - 10)),
        })

    def _respawn(self, f):
        others = [o for o in self.fighters.values() if o is not f and o.alive]

        def space(p):
            return min((math.hypot(o.x - p[0], o.y - p[1]) for o in others), default=1e9)

        x, y = max(RESPAWN_POINTS, key=space)
        f.x, f.y = float(x), float(y - 140)
        f.vx = f.vy = 0.0
        f.alive, f.ground, f.jumps = True, False, 2
        f.damage, f.stun, f.attack = 0, 0.0, None
        f.last_hit_by = None
        f.shield_until = self.clock + SPAWN_SHIELD
        self.events.append({"kind": "respawn", "name": f.name})

    # ---------- results ----------
    def is_over(self):
        return self.time_left <= 1e-6 or len(self.fighters) < 2

    def ranking(self):
        ordered = sorted(self.fighters.values(), key=lambda f: (-f.kos, f.falls))
        return [{"name": f.name, "kos": f.kos, "falls": f.falls} for f in ordered]

    def winner(self):
        r = self.ranking()
        if not r:
            return None
        if len(r) > 1 and (r[0]["kos"], r[0]["falls"]) == (r[1]["kos"], r[1]["falls"]):
            return None  # draw
        return r[0]["name"]

    def snapshot(self):
        return {
            "tick": self.tick,
            "time_left": round(self.time_left, 2),
            "players": [f.to_dict(self.clock) for f in self.fighters.values()],
        }

    def pop_events(self):
        events, self.events = self.events, []
        return events
