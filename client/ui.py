"""Rooftop Rumble game window (pygame-ce).

Screens: connect -> lobby -> game -> results -> lobby.
The UI talks to the server through a connection object with
send(msg), messages (queue), connected (Event) and close(). It gets a
TCPConnection or a UDPConnection and never needs to know which one.

Run:
    python -m client.ui
    python -m client.ui --protocol udp --host 192.168.1.23 --name Kai
    python -m client.ui --protocol tcp --host myserver.example.com --port 5000
    python -m client.ui --protocol udp --loss 0.1 --delay 80 --log metrics.csv

Controls in a match:
    A / D or arrows   move          W, Up or Space   jump (twice in the air)
    S or Down         drop through a platform / fall faster
    J                 punch         K                charged smash
    F3                show or hide the network stats
"""
import argparse
import bisect
import csv
import math
import os
import random
import socket
import time

import pygame

from common import protocol as P
from common.config import TCP_PORT, UDP_PORT, SNAPSHOT_RATE, TICK_RATE
from common.game import ARENA_WIDTH, ARENA_HEIGHT, PLATFORMS, ATTACKS

WIDTH, HUD = ARENA_WIDTH, 72
HEIGHT = ARENA_HEIGHT + HUD
INTERP_DELAY = 0.075          # draw the world slightly in the past to smooth between snapshots
INPUT_RATE = 30               # INPUT messages per second

# Colours
BG = (18, 11, 31)
PANEL = (28, 19, 48)
PANEL_2 = (38, 27, 62)
LINE = (58, 44, 90)
TEXT = (251, 243, 255)
MUTED = (164, 147, 196)
INK = (26, 15, 38)
GOLD = (255, 181, 46)
RED = (255, 79, 94)
GOOD = (94, 240, 138)
SLOT_COLORS = [(46, 230, 214), (255, 79, 94), (255, 181, 46)]


def slot_color(slot):
    return SLOT_COLORS[slot % len(SLOT_COLORS)]


# ---------------------------------------------------------------- widgets
class Button:
    def __init__(self, rect, label, action, primary=False):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.action = action
        self.primary = primary
        self.enabled = True

    def draw(self, surf, fonts, mouse):
        hover = self.enabled and self.rect.collidepoint(mouse)
        if self.primary and self.enabled:
            bg, fg, border = (GOLD if not hover else (255, 200, 90)), INK, GOLD
        else:
            bg = PANEL_2 if not hover else LINE
            fg = TEXT if self.enabled else (100, 90, 125)
            border = LINE
        pygame.draw.rect(surf, bg, self.rect, border_radius=8)
        pygame.draw.rect(surf, border, self.rect, 1, border_radius=8)
        draw_text(surf, self.label, fonts["button"], fg, self.rect.center, "center")

    def click(self, pos):
        if self.enabled and self.rect.collidepoint(pos):
            self.action()
            return True
        return False


class TextField:
    def __init__(self, rect, label, text="", max_len=24):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.text = text
        self.max_len = max_len
        self.focused = False

    def draw(self, surf, fonts, now):
        draw_text(surf, self.label, fonts["small"], MUTED, (self.rect.x, self.rect.y - 22))
        pygame.draw.rect(surf, PANEL_2, self.rect, border_radius=8)
        pygame.draw.rect(surf, GOLD if self.focused else LINE, self.rect, 2 if self.focused else 1, border_radius=8)
        shown = self.text + ("|" if self.focused and int(now * 2) % 2 == 0 else "")
        while len(shown) > 1 and fonts["body"].size(shown)[0] > self.rect.w - 24:
            shown = shown[1:]               # long hostnames: show the end being typed
        draw_text(surf, shown, fonts["body"], TEXT, (self.rect.x + 12, self.rect.centery), "midleft")

    def key(self, event):
        if event.key == pygame.K_BACKSPACE:
            self.text = self.text[:-1]

    def type(self, chars):
        self.text = (self.text + chars)[: self.max_len]


def default_port(protocol):
    return TCP_PORT if protocol == "tcp" else UDP_PORT


def draw_text(surf, text, font, color, pos, anchor="topleft"):
    img = font.render(str(text), True, color)
    rect = img.get_rect(**{anchor: pos})
    surf.blit(img, rect)
    return rect


# ---------------------------------------------------------------- the app
class App:
    def __init__(self, args):
        pygame.init()
        pygame.display.set_caption("Rooftop Rumble")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.SCALED | pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.args = args
        self.fonts = self.load_fonts()
        self.running = True
        self.mode = "connect"
        self.conn = None
        self.me = None
        self.rooms = []
        self.toasts = []                    # (time, text, color)
        self.status = ""
        self.protocol = args.protocol
        self.fields = [
            TextField((330, 250, 300, 44), "Your name", args.name or "", 16),
            TextField((330, 340, 210, 44), "Server IP or hostname", args.host, 64),
            TextField((550, 340, 80, 44), "Port", str(args.port or default_port(args.protocol)), 5),
        ]
        self.fields[0].focused = True
        self.connect_buttons = [
            Button((330, 410, 146, 40), "TCP", lambda: self.set_protocol("tcp")),
            Button((484, 410, 146, 40), "UDP", lambda: self.set_protocol("udp")),
            Button((330, 474, 300, 50), "Connect", self.connect, primary=True),
        ]
        self.lobby_buttons = []
        self.result_button = Button((WIDTH // 2 - 110, 520, 220, 48), "Back to lobby", self.back_to_lobby, primary=True)
        self.show_net = True
        self.metrics_file = args.log
        self.background = None
        self.reset_match()
        if args.name and args.auto_connect:
            self.connect()

    def load_fonts(self):
        def f(size, bold=False):
            return pygame.font.SysFont("segoeui,arial,dejavusans", size, bold=bold)
        return {
            "title": f(64, True), "h1": f(30, True), "h2": f(22, True), "body": f(19),
            "button": f(18, True), "small": f(15), "tiny": f(13), "mono": pygame.font.SysFont("consolas,dejavusansmono,monospace", 14),
            "big": f(110, True), "pop": f(26, True), "dmg": f(30, True),
        }

    # ------------------------------------------------------------ connection
    def set_protocol(self, p):
        port = self.fields[2]
        if port.text.strip() in ("", str(default_port(self.protocol))):
            port.text = str(default_port(p))   # follow the protocol unless the user typed a port
        self.protocol = p

    def connect(self):
        name = self.fields[0].text.strip()
        host = self.fields[1].text.strip() or "127.0.0.1"
        port_text = self.fields[2].text.strip() or str(default_port(self.protocol))
        if not name:
            self.status = "Enter a name first."
            return
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            self.status = "Can't use that port: enter a number from 1 to 65535."
            return
        port = int(port_text)
        self.status = f"Connecting to {host}:{port} over {self.protocol.upper()}..."
        self.draw()
        pygame.display.flip()
        try:
            if self.protocol == "tcp":
                from tcp.client import TCPConnection
                self.conn = TCPConnection(
                    host, port, name,
                    loss=self.args.loss, delay_ms=self.args.delay,
                )
            else:
                from udp.client import UDPConnection
                self.conn = UDPConnection(host, port, name,
                                          loss=self.args.loss, delay_ms=self.args.delay)
        except socket.gaierror:
            self.conn = None
            self.status = f"Can't find a server called '{host}'. Check the address."
        except OSError as exc:
            self.conn = None
            self.status = f"Can't reach the server at {host}:{port}. Is it running? ({exc.__class__.__name__})"

    def disconnect(self, reason):
        if self.conn:
            self.conn.close()
        self.conn = None
        self.me = None
        self.mode = "connect"
        self.status = reason

    def back_to_lobby(self):
        self.mode = "lobby"

    # ------------------------------------------------------------ messages
    def poll_network(self):
        if not self.conn:
            return
        while True:
            try:
                msg = self.conn.messages.get_nowait()
            except Exception:
                break
            self.on_message(msg)
            if not self.conn:
                break

    def on_message(self, msg):
        t = msg["type"]
        now = time.time()
        if t == P.LOGIN_OK:
            self.me = msg["name"]
            self.mode = "lobby"
            self.status = ""
            self.toast(f"Welcome, {self.me}. Connected over {self.conn.protocol_name}.", GOOD)
        elif t == P.LOGIN_FAIL:
            self.disconnect(f"Login refused: {msg['reason']}.")
        elif t == "DISCONNECTED":
            self.disconnect(msg["reason"] + ".")
        elif t == P.ROOM_LIST:
            self.rooms = msg["rooms"]
        elif t == P.ERROR:
            self.toast(msg["reason"], RED)
        elif t == P.CHAT:
            self.toast(f"{msg['sender']}: {msg['text']}", MUTED)
        elif t == P.PLAYER_LEFT:
            self.toast(f"{msg['name']} {msg.get('reason', 'left')}", RED)
            if self.mode == "game":
                self.banner = (now, f"{msg['name']} left", RED)
        elif t == P.GAME_START:
            self.reset_match()
            self.slots = {p["name"]: p["slot"] for p in msg["players"]}
            self.duration = msg["duration"]
            self.mode = "game"
            self.banner = (now, "FIGHT!", GOLD)
        elif t == P.STATE:
            self.on_state(msg, now)
        elif t == P.EVENT:
            self.on_event(msg, now)
        elif t == P.GAME_OVER:
            self.results = msg
            self.mode = "results"

    def toast(self, text, color=TEXT):
        self.toasts.append((time.time(), text, color))
        self.toasts = self.toasts[-5:]

    # ------------------------------------------------------------ match state
    def reset_match(self):
        self.snapshots = []                 # (arrival time, state)
        self.slots = {}
        self.duration = 90
        self.results = None
        self.particles = []
        self.sparks = []
        self.pops = []
        self.banner = None
        self.shake = 0.0
        self.presses = {"jump": 0, "punch": 0, "smash": 0}
        self.last_input_sent = 0.0
        self.last_keys = None
        self.net = {"last_tick": None, "missed": 0, "received": 0, "last_arrival": None,
                    "max_gap": 0.0, "window": [], "second_start": time.time(),
                    "sec_received": 0, "sec_missed": 0, "sec_gap": 0.0}

    def on_state(self, msg, now):
        n = self.net
        expected_step = max(1, TICK_RATE // SNAPSHOT_RATE)
        if n["last_tick"] is not None:
            skipped = (msg["tick"] - n["last_tick"]) // expected_step - 1
            if skipped > 0:
                n["missed"] += skipped
                n["sec_missed"] += skipped
        if n["last_tick"] is None or msg["tick"] > n["last_tick"]:
            n["last_tick"] = msg["tick"]
        if n["last_arrival"] is not None:
            gap = now - n["last_arrival"]
            n["max_gap"] = max(n["max_gap"], gap)
            n["sec_gap"] = max(n["sec_gap"], gap)
        n["last_arrival"] = now
        n["received"] += 1
        n["sec_received"] += 1
        n["window"].append(now)
        self.snapshots.append((now, msg))
        self.snapshots = self.snapshots[-30:]

    def on_event(self, e, now):
        kind = e.get("kind")
        ox, oy = e.get("x", 0), e.get("y", 0)
        if kind == "hit":
            color = slot_color(self.slots.get(e["attacker"], 0))
            big = e.get("attack") == "smash"
            self.sparks.append([ox, oy, now, color, big])
            self.burst(ox, oy, (255, 255, 255), 16 if big else 7, 7 if big else 4)
            self.burst(ox, oy, color, 22 if big else 8, 8 if big else 5)
            self.pops.append([ox, oy - 10, now, f"+{e['dmg']}%", color])
            if self.me in (e["attacker"], e["victim"]):
                self.shake = max(self.shake, 9 if big else 3)
        elif kind == "ko":
            color = slot_color(self.slots.get(e["victim"], 0))
            self.burst(ox, oy, color, 90, 11, size=4, decay=.012)
            self.burst(ox, oy, (255, 255, 255), 40, 8)
            self.sparks.append([ox, oy, now, color, True, True])
            self.shake = 16
            if e.get("by") == self.me:
                self.banner = (now, "KO!", GOLD)
            elif e.get("victim") == self.me:
                self.banner = (now, "KNOCKED OUT", RED)
            else:
                self.banner = (now, "KO!", color)
        elif kind == "left":
            self.toast(f"{e['name']} left the match", RED)

    def burst(self, x, y, color, n, speed, size=3, decay=.03):
        for _ in range(n):
            a = random.uniform(0, math.tau)
            v = speed * random.uniform(.3, 1)
            self.particles.append([x, y, math.cos(a) * v, math.sin(a) * v, 1.0, decay * random.uniform(.7, 1.3), color, size])

    def view_state(self, now):
        """Interpolated players at (now - INTERP_DELAY). Returns (state, players)."""
        if not self.snapshots:
            return None, []
        render_t = now - INTERP_DELAY
        times = [t for t, _ in self.snapshots]
        i = bisect.bisect_right(times, render_t)
        if i == 0:
            return self.snapshots[0][1], self.snapshots[0][1]["players"]
        if i >= len(self.snapshots):
            latest = self.snapshots[-1][1]
            return latest, latest["players"]
        (t0, s0), (t1, s1) = self.snapshots[i - 1], self.snapshots[i]
        k = (render_t - t0) / max(1e-6, t1 - t0)
        before = {p["name"]: p for p in s0["players"]}
        players = []
        for p in s1["players"]:
            q = before.get(p["name"])
            if q and q["alive"] and p["alive"] and math.hypot(p["x"] - q["x"], p["y"] - q["y"]) < 160:
                p = dict(p, x=q["x"] + (p["x"] - q["x"]) * k, y=q["y"] + (p["y"] - q["y"]) * k)
            players.append(p)
        return s1, players

    # ------------------------------------------------------------ input
    def send_input(self, now):
        keys = pygame.key.get_pressed()
        held = {
            "left": int(keys[pygame.K_a] or keys[pygame.K_LEFT]),
            "right": int(keys[pygame.K_d] or keys[pygame.K_RIGHT]),
            "down": int(keys[pygame.K_s] or keys[pygame.K_DOWN]),
        }
        current = dict(held, **{k + "_n": v for k, v in self.presses.items()})
        changed = current != self.last_keys
        if changed or now - self.last_input_sent >= 1 / INPUT_RATE:
            self.conn.send(P.make(P.INPUT, **current))
            self.last_input_sent = now
            self.last_keys = current

    # ------------------------------------------------------------ events
    def handle_events(self):
        mouse_click = None
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_click = event.pos
            elif event.type == pygame.KEYDOWN:
                self.on_key(event)
            elif event.type == pygame.TEXTINPUT and self.mode == "connect":
                for field in self.fields:
                    if field.focused:
                        field.type(event.text)
        if mouse_click:
            self.on_click(mouse_click)

    def on_key(self, event):
        if event.key == pygame.K_F3:
            self.show_net = not self.show_net
        if self.mode == "connect":
            if event.key == pygame.K_TAB:
                i = next((k for k, f in enumerate(self.fields) if f.focused), 0)
                for f in self.fields:
                    f.focused = False
                self.fields[(i + 1) % len(self.fields)].focused = True
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.connect()
            else:
                for field in self.fields:
                    if field.focused:
                        field.key(event)
        elif self.mode == "game":
            if event.key in (pygame.K_w, pygame.K_UP, pygame.K_SPACE):
                self.presses["jump"] += 1
            elif event.key == pygame.K_j:
                self.presses["punch"] += 1
            elif event.key == pygame.K_k:
                self.presses["smash"] += 1
        elif self.mode == "results" and event.key in (pygame.K_RETURN, pygame.K_ESCAPE):
            self.back_to_lobby()

    def on_click(self, pos):
        if self.mode == "connect":
            for field in self.fields:
                field.focused = field.rect.collidepoint(pos)
            if not any(f.focused for f in self.fields):
                self.fields[0].focused = True
            for b in self.connect_buttons:
                b.click(pos)
        elif self.mode == "lobby":
            for b in self.lobby_buttons:
                if b.click(pos):
                    break
        elif self.mode == "results":
            self.result_button.click(pos)

    # ------------------------------------------------------------ main loop
    def run(self):
        while self.running:
            now = time.time()
            self.handle_events()
            self.poll_network()
            if self.mode == "game" and self.conn:
                self.send_input(now)
            self.update_metrics(now)
            self.draw()
            pygame.display.flip()
            self.clock.tick(60)
        if self.conn:
            self.conn.close()
        pygame.quit()

    def update_metrics(self, now):
        n = self.net
        n["window"] = [t for t in n["window"] if now - t < 1.0]
        if now - n["second_start"] < 1.0:
            return
        if self.metrics_file and self.mode == "game" and self.conn:
            new = not os.path.exists(self.metrics_file)
            with open(self.metrics_file, "a", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["time", "protocol", "player", "ping_ms", "snapshots_received",
                                "snapshots_missed", "max_gap_ms", "loss_sim", "delay_sim_ms"])
                rtt = self.conn.rtt_ms
                w.writerow([time.strftime("%H:%M:%S"), self.conn.protocol_name, self.me,
                            round(rtt, 1) if rtt is not None else "", n["sec_received"], n["sec_missed"],
                            round(n["sec_gap"] * 1000), self.args.loss, self.args.delay])
        n["second_start"] = now
        n["sec_received"] = n["sec_missed"] = 0
        n["sec_gap"] = 0.0

    # ------------------------------------------------------------ drawing
    def draw(self):
        now = time.time()
        mouse = pygame.mouse.get_pos()
        if self.mode == "connect":
            self.draw_connect(now, mouse)
        elif self.mode == "lobby":
            self.draw_lobby(now, mouse)
        else:
            self.draw_game(now)
            if self.mode == "results":
                self.draw_results(mouse)
        self.draw_toasts(now)

    def draw_backdrop(self):
        if self.background is None:
            self.background = build_background()
        self.screen.blit(self.background, (0, HUD))
        self.screen.fill(BG, (0, 0, WIDTH, HUD))

    def draw_connect(self, now, mouse):
        self.draw_backdrop()
        shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        shade.fill((18, 11, 31, 170))
        self.screen.blit(shade, (0, 0))
        draw_title(self.screen, self.fonts, (WIDTH // 2, 110))
        draw_text(self.screen, "Three fighters. One rooftop. Knock them into the city.",
                  self.fonts["body"], MUTED, (WIDTH // 2, 170), "midtop")
        panel = pygame.Rect(300, 205, 360, 345)
        pygame.draw.rect(self.screen, PANEL, panel, border_radius=14)
        pygame.draw.rect(self.screen, LINE, panel, 1, border_radius=14)
        for f in self.fields:
            f.draw(self.screen, self.fonts, now)
        draw_text(self.screen, "Protocol", self.fonts["small"], MUTED, (330, 388))
        tcp_b, udp_b, connect_b = self.connect_buttons
        tcp_b.primary = self.protocol == "tcp"
        udp_b.primary = self.protocol == "udp"
        for b in self.connect_buttons:
            b.draw(self.screen, self.fonts, mouse)
        if self.status:
            draw_text(self.screen, self.status, self.fonts["small"],
                      RED if "Can't" in self.status or "refused" in self.status or "lost" in self.status else MUTED,
                      (WIDTH // 2, 570), "midtop")
        draw_text(self.screen, "Tab switches fields, Enter connects", self.fonts["tiny"], MUTED, (WIDTH // 2, 600), "midtop")

    def draw_lobby(self, now, mouse):
        s = self.screen
        s.fill(BG)
        draw_text(s, "ROOFTOP RUMBLE", self.fonts["h1"], GOLD, (30, 22))
        ping = f"{self.conn.rtt_ms:.0f} ms" if self.conn and self.conn.rtt_ms is not None else "..."
        draw_text(s, f"{self.me}  ·  {self.conn.protocol_name if self.conn else ''}  ·  ping {ping}",
                  self.fonts["body"], MUTED, (WIDTH - 30, 30), "topright")
        my_room = next((r for r in self.rooms if self.me in r["players"]), None)
        buttons = []

        # rooms list
        left = pygame.Rect(30, 80, 540, 470)
        pygame.draw.rect(s, PANEL, left, border_radius=14)
        draw_text(s, "Rooms", self.fonts["h2"], TEXT, (50, 98))
        create = Button((410, 92, 140, 38), "Create room",
                        lambda: self.conn.send(P.make(P.CREATE_ROOM)), primary=my_room is None)
        create.enabled = my_room is None
        buttons.append(create)
        if not self.rooms:
            draw_text(s, "No rooms yet. Create one and invite your friends.", self.fonts["body"], MUTED, (50, 160))
        y = 146
        for r in self.rooms[:5]:
            row = pygame.Rect(46, y, 508, 74)
            mine = r is my_room
            pygame.draw.rect(s, PANEL_2, row, border_radius=10)
            pygame.draw.rect(s, GOLD if mine else LINE, row, 2 if mine else 1, border_radius=10)
            draw_text(s, r["name"], self.fonts["h2"], TEXT, (64, y + 10))
            playing = r["status"] == "playing"
            badge = "In a match" if playing else "Waiting"
            draw_text(s, f"{len(r['players'])}/{r['max']} players  ·  {badge}", self.fonts["small"],
                      GOLD if playing else MUTED, (64, y + 42))
            draw_text(s, ", ".join(r["players"]), self.fonts["small"], MUTED, (270, y + 42))
            join = Button((444, y + 18, 94, 38), "Join",
                          lambda rid=r["id"]: self.conn.send(P.make(P.JOIN_ROOM, room_id=rid)))
            join.enabled = my_room is None and not playing and len(r["players"]) < r["max"]
            if not mine:
                buttons.append(join)
            y += 84

        # my room panel
        right = pygame.Rect(590, 80, 340, 470)
        pygame.draw.rect(s, PANEL, right, border_radius=14)
        draw_text(s, "Your room", self.fonts["h2"], TEXT, (610, 98))
        if my_room is None:
            draw_text(s, "Create a room or join one", self.fonts["body"], MUTED, (610, 146))
            draw_text(s, "to start a match.", self.fonts["body"], MUTED, (610, 172))
        else:
            draw_text(s, my_room["name"], self.fonts["body"], GOLD, (610, 134))
            for i, name in enumerate(my_room["players"]):
                py = 180 + i * 52
                pygame.draw.circle(s, slot_color(i), (628, py + 14), 10)
                draw_text(s, name + (" (you)" if name == self.me else ""), self.fonts["body"], TEXT, (650, py + 2))
                if name == my_room["owner"]:
                    draw_text(s, "owner", self.fonts["tiny"], GOLD, (650, py + 26))
            for i in range(len(my_room["players"]), my_room["max"]):
                py = 180 + i * 52
                pygame.draw.circle(s, LINE, (628, py + 14), 10, 2)
                draw_text(s, "Waiting for a player...", self.fonts["body"], MUTED, (650, py + 2))
            is_owner = my_room["owner"] == self.me
            if my_room["status"] == "playing":
                draw_text(s, "Match in progress...", self.fonts["body"], GOLD, (610, 360))
            elif is_owner:
                start = Button((610, 400, 300, 50), "Start match", lambda: self.conn.send(P.make(P.START_GAME)), primary=True)
                start.enabled = len(my_room["players"]) >= 2
                buttons.append(start)
                if not start.enabled:
                    draw_text(s, "You need at least 2 players.", self.fonts["small"], MUTED, (610, 370))
            else:
                draw_text(s, f"Waiting for {my_room['owner']} to start.", self.fonts["body"], MUTED, (610, 370))
            buttons.append(Button((610, 470, 300, 44), "Leave room", lambda: self.conn.send(P.make(P.LEAVE_ROOM))))

        draw_text(s, "Controls: A/D move · W jump · S drop · J punch · K smash · F3 network stats",
                  self.fonts["small"], MUTED, (WIDTH // 2, 600), "midtop")
        for b in buttons:
            b.draw(s, self.fonts, mouse)
        self.lobby_buttons = buttons

    def draw_game(self, now):
        s = self.screen
        if self.background is None:
            self.background = build_background()
        arena = self.background.copy()
        state, players = self.view_state(now)

        for p in players:
            draw_fighter(arena, p, slot_color(p.get("slot", 0)), p["name"] == self.me, self.fonts, now)
        # sparks
        for sp in self.sparks[:]:
            x, y, t0, color, big = sp[:5]
            ko = len(sp) > 5
            t = (now - t0) / (0.5 if ko else 0.2)
            if t > 1:
                self.sparks.remove(sp)
                continue
            radius = (120 if ko else 44 if big else 24) * (.4 + t)
            n = 14 if ko else 8
            for k in range(n):
                a = k / n * math.tau + t0
                pygame.draw.line(arena, color if ko else (255, 255, 255),
                                 (x + math.cos(a) * radius * .4, y + math.sin(a) * radius * .4),
                                 (x + math.cos(a) * radius, y + math.sin(a) * radius), max(1, int((5 if big else 3) * (1 - t))))
        # particles
        alive = []
        for q in self.particles:
            q[0] += q[2]; q[1] += q[3]; q[2] *= .95; q[3] *= .95; q[4] -= q[5]
            if q[4] > 0:
                c = tuple(int(v * q[4] + 18 * (1 - q[4])) for v in q[6])
                sz = q[7]
                arena.fill(c, (q[0] - sz / 2, q[1] - sz / 2, sz, sz))
                alive.append(q)
        self.particles = alive
        # damage pops
        for pop in self.pops[:]:
            x, y, t0, txt, color = pop
            t = (now - t0) / 0.7
            if t > 1:
                self.pops.remove(pop)
                continue
            outline_text(arena, txt, self.fonts["pop"], color, (x, y - t * 30), "center")
        # banner
        if self.banner:
            t0, txt, color = self.banner
            t = (now - t0) / 0.9
            if t > 1:
                self.banner = None
            else:
                outline_text(arena, txt, self.fonts["big"], color, (WIDTH // 2, ARENA_HEIGHT // 2 - 40), "center", 6)
        # countdown-free waiting state
        if state is None:
            outline_text(arena, "Waiting for the server...", self.fonts["h1"], TEXT, (WIDTH // 2, ARENA_HEIGHT // 2), "center")
        if self.show_net:
            self.draw_net_stats(arena, now)

        shake = self.shake
        self.shake = shake * .86 if shake > .3 else 0
        ox = random.uniform(-shake, shake) if shake else 0
        oy = random.uniform(-shake, shake) if shake else 0
        s.fill(BG)
        s.blit(arena, (ox, HUD + oy))
        self.draw_hud(state, now)

    def draw_hud(self, state, now):
        s = self.screen
        s.fill(BG, (0, 0, WIDTH, HUD))
        players = state["players"] if state else []
        by_slot = sorted(players, key=lambda p: p.get("slot", 0))
        for i, p in enumerate(by_slot[:3]):
            r = pygame.Rect(16 + i * 250, 10, 238, 54)
            color = slot_color(p.get("slot", 0))
            pygame.draw.rect(s, PANEL, r, border_radius=10)
            pygame.draw.rect(s, GOLD if p["name"] == self.me else LINE, r, 2 if p["name"] == self.me else 1, border_radius=10)
            pygame.draw.circle(s, color, (r.x + 22, r.centery), 14)
            draw_text(s, p["name"] + (" (you)" if p["name"] == self.me else ""), self.fonts["button"], color, (r.x + 44, r.y + 7))
            draw_text(s, f"{p['kos']} KO · {p['falls']} falls", self.fonts["tiny"], MUTED, (r.x + 44, r.y + 31))
            dmg = p["damage"] if p["alive"] else "—"
            heat = min(1, p["damage"] / 140)
            dcol = (255, int(255 - heat * 190), int(255 - heat * 200))
            draw_text(s, f"{dmg}%" if p["alive"] else "—", self.fonts["dmg"], dcol, (r.right - 12, r.centery), "midright")
        box = pygame.Rect(WIDTH - 180, 10, 164, 54)
        pygame.draw.rect(s, PANEL, box, border_radius=10)
        left = state["time_left"] if state else self.duration
        secs = int(math.ceil(left))
        draw_text(s, "TIME", self.fonts["tiny"], MUTED, (box.centerx, box.y + 6), "midtop")
        draw_text(s, f"{secs // 60}:{secs % 60:02d}", self.fonts["h1"], RED if secs <= 10 else TEXT, (box.centerx, box.y + 20), "midtop")

    def draw_net_stats(self, surf, now):
        n = self.net
        rtt = f"{self.conn.rtt_ms:.0f} ms" if self.conn and self.conn.rtt_ms is not None else "..."
        lines = [
            f"{self.conn.protocol_name if self.conn else ''}  ping {rtt}",
            f"snapshots/s {len(n['window'])}   missed {n['missed']}",
            f"longest gap {n['max_gap'] * 1000:.0f} ms",
        ]
        if self.args.loss or self.args.delay:
            lines.append(f"simulated loss {self.args.loss * 100:.0f}%  delay {self.args.delay} ms")
            st = getattr(self.conn, "stats", {})
            if self.conn and self.conn.protocol_name == "TCP":
                lines.append(f"TCP HoL stalls {st.get('hol_stalls', 0)}  retrans {st.get('retransmitted_sim', 0)}")
            elif self.conn and self.conn.protocol_name == "UDP":
                lines.append(f"UDP ack resends {st.get('resent', 0)}  old dropped {st.get('dropped_old', 0)}")
        h = 18 * len(lines) + 12
        box = pygame.Rect(10, ARENA_HEIGHT - h - 10, 290, h)
        pygame.draw.rect(surf, (18, 11, 31), box, border_radius=8)
        pygame.draw.rect(surf, LINE, box, 1, border_radius=8)
        for i, line in enumerate(lines):
            draw_text(surf, line, self.fonts["mono"], TEXT if i == 0 else MUTED, (20, ARENA_HEIGHT - h - 4 + i * 18))

            
    def draw_results(self, mouse):
        s = self.screen
        shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        shade.fill((18, 11, 31, 228))
        s.blit(shade, (0, 0))
        r = self.results
        winner = r.get("winner")
        if winner is None:
            title, color = "DRAW!", TEXT
        elif winner == self.me:
            title, color = "YOU WIN!", GOLD
        else:
            title, color = f"{winner.upper()} WINS", slot_color(self.slots.get(winner, 0))
        outline_text(s, title, self.fonts["big"], color, (WIDTH // 2, 170), "center", 6)
        y = 270
        draw_text(s, "Rank", self.fonts["small"], MUTED, (300, y))
        draw_text(s, "Fighter", self.fonts["small"], MUTED, (380, y))
        draw_text(s, "KOs", self.fonts["small"], MUTED, (580, y))
        draw_text(s, "Falls", self.fonts["small"], MUTED, (650, y))
        for i, row in enumerate(r["ranking"]):
            yy = y + 34 + i * 44
            c = slot_color(self.slots.get(row["name"], i))
            draw_text(s, str(i + 1), self.fonts["h2"], TEXT, (310, yy))
            pygame.draw.circle(s, c, (390, yy + 14), 9)
            draw_text(s, row["name"] + (" (you)" if row["name"] == self.me else ""), self.fonts["h2"], TEXT, (408, yy))
            draw_text(s, str(row["kos"]), self.fonts["h2"], TEXT, (588, yy))
            draw_text(s, str(row["falls"]), self.fonts["h2"], TEXT, (662, yy))
        self.result_button.draw(s, self.fonts, mouse)

    def draw_toasts(self, now):
        self.toasts = [t for t in self.toasts if now - t[0] < 4]
        in_match = self.mode in ("game", "results")
        y = HUD + 30 if in_match else 568          # in a match: under the score bar
        for t0, text, color in reversed(self.toasts[-3:]):
            img = self.fonts["small"].render(text, True, color)
            r = img.get_rect(midtop=(WIDTH // 2, y)) if in_match else img.get_rect(midbottom=(WIDTH // 2, y))
            bg = r.inflate(20, 10)
            pygame.draw.rect(self.screen, PANEL, bg, border_radius=8)
            pygame.draw.rect(self.screen, LINE, bg, 1, border_radius=8)
            self.screen.blit(img, r)
            y += 32 if in_match else -32


# ---------------------------------------------------------------- art
def outline_text(surf, text, font, color, pos, anchor="center", width=3):
    base = font.render(text, True, INK)
    img = font.render(text, True, color)
    rect = img.get_rect(**{anchor: (int(pos[0]), int(pos[1]))})
    for dx in (-width, 0, width):
        for dy in (-width, 0, width):
            if dx or dy:
                surf.blit(base, rect.move(dx, dy))
    surf.blit(img, rect)


def draw_title(surf, fonts, center):
    outline_text(surf, "ROOFTOP RUMBLE", fonts["title"], GOLD, center, "center", 4)


def build_background():
    """Sunset city with the rooftop and platforms. Built once, then reused."""
    bg = pygame.Surface((WIDTH, ARENA_HEIGHT))
    stops = [(0, (26, 16, 51)), (.45, (91, 34, 87)), (.75, (226, 86, 90)), (1, (255, 179, 92))]
    for y in range(ARENA_HEIGHT):
        t = y / ARENA_HEIGHT
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0)
                pygame.draw.line(bg, tuple(int(a + (b - a) * k) for a, b in zip(c0, c1)), (0, y), (WIDTH, y))
                break
    glow = pygame.Surface((240, 240), pygame.SRCALPHA)
    pygame.draw.circle(glow, (255, 226, 150, 46), (120, 120), 110)
    bg.blit(glow, (700 - 120, 330 - 120))
    pygame.draw.circle(bg, (255, 226, 150), (700, 330), 70)
    rng = random.Random(7)
    for _ in range(60):
        bg.fill((255, 255, 255), (rng.random() * WIDTH, rng.random() * 170, 2, 2))
    for base, color, window, lo, hi in [(470, (58, 26, 69), None, 80, 230),
                                         (520, (36, 17, 46), (255, 200, 120), 60, 200),
                                         (560, (21, 10, 29), (255, 170, 90), 40, 130)]:
        x = -10
        while x < WIDTH:
            w, h = 40 + rng.random() * 70, lo + rng.random() * (hi - lo)
            bg.fill(color, (x, base - h, w, h + 200))
            if window:
                wy = base - h + 10
                while wy < base:
                    wx = x + 6
                    while wx < x + w - 6:
                        if rng.random() < .3:
                            bg.fill(window, (wx, wy, 5, 7))
                        wx += 12
                    wy += 14
            x += w + 2
    x1, x2, y, _ = PLATFORMS[0]
    bg.fill((43, 29, 58), (x1, y, x2 - x1, 170))
    bg.fill((63, 44, 85), (x1, y, x2 - x1, 10))
    bg.fill(GOLD, (x1, y, x2 - x1, 3))
    for vx in range(x1 + 30, x2 - 20, 70):
        bg.fill((34, 23, 47), (vx, y + 30, 34, 22))
        bg.fill((26, 120, 115), (vx + 3, y + 33, 28, 4))
    pygame.draw.lines(bg, (34, 23, 47), False, [(x2 - 80, y), (x2 - 80, y - 60), (x2 - 40, y - 60)], 3)
    for px1, px2, py, _ in PLATFORMS[1:]:
        bg.fill((20, 10, 30), (px1 + 6, py + 12, px2 - px1 - 12, 6))
        bg.fill((75, 52, 102), (px1, py, px2 - px1, 10))
        bg.fill((255, 210, 122), (px1, py, px2 - px1, 3))
        pygame.draw.line(bg, (75, 52, 102), (px1 + 14, py + 10), (px1 + 14, py + 40), 2)
        pygame.draw.line(bg, (75, 52, 102), (px2 - 14, py + 10), (px2 - 14, py + 40), 2)
    return bg


def draw_fighter(surf, p, color, is_me, fonts, now):
    if not p["alive"]:
        return
    if p.get("shield") and int(now * 12) % 2:
        return                                   # blink while protected after respawn
    x, y, d = p["x"], p["y"], p.get("facing", 1)
    if p.get("ground"):
        pygame.draw.ellipse(surf, (20, 10, 30), (x - 16, y - 4, 32, 8))
    hip = (x, y - 30)
    shoulder = (x, y - 50)
    run = p.get("ground") and abs(p.get("vx", 0)) > 30 and not p.get("attack")
    sw = math.sin(now * 12) * .9 if run else 0
    legs = [(-6 + sw * 9, 30), (6 - sw * 9, 30)] if p.get("ground") else [(-9, 24), (8, 18)]
    arms = [(d * -10 + sw * -8, -20 + 10), (d * 10 + sw * 8, -20 + 10)]
    attack = p.get("attack")
    lean = 0
    if attack:
        A, t = ATTACKS[attack["kind"]], attack["t"]
        if attack["kind"] == "punch":
            ext = 1 if A["a"] <= t <= A["b"] + .05 else (t / A["a"] if t < A["a"] else .4)
            arms = [(d * -8, -14), (d * (10 + 26 * ext), -36)]
            lean = d * 4 * ext
        else:
            if t < A["a"]:
                c = t / A["a"]
                arms = [(d * -20 * c, -26), (d * -24 * c, -30)]
                lean = -d * 6 * c
            else:
                arms = [(d * 30, -42), (d * 34, -32)]
                lean = d * 8
                if p.get("ground"):
                    legs = [(d * -16, 30), (d * 12, 30)]
    if p.get("stun"):
        arms = [(-18, -40), (18, -42)]
        legs = [(-14, 24), (14, 22)]
    elif not p.get("ground") and not attack:
        arms = [(d * -14, -30), (d * 14, -34)]
    sx = shoulder[0] + lean
    for width, col in ((12, INK), (7, color)):
        for lx, ly in legs:
            mid = (hip[0] + lx / 2 + d * 3, hip[1] + ly / 2)
            pygame.draw.lines(surf, col, False, [hip, mid, (hip[0] + lx, hip[1] + ly)], width)
        pygame.draw.line(surf, col, hip, (sx, shoulder[1]), width)
        for ax, ay in arms:
            end = (hip[0] + ax + lean, hip[1] + ay)
            mid = ((sx + end[0]) / 2, (shoulder[1] + end[1]) / 2 + 4)
            pygame.draw.lines(surf, col, False, [(sx, shoulder[1]), mid, end], width)
    for ax, ay in arms:
        pygame.draw.circle(surf, (255, 255, 255), (int(hip[0] + ax + lean), int(hip[1] + ay)), 5)
    head = (int(sx + d * 2), int(y - 63))
    pygame.draw.circle(surf, INK, head, 13)
    pygame.draw.circle(surf, color, head, 10)
    pygame.draw.arc(surf, (255, 255, 255), (head[0] - 10, head[1] - 10, 20, 20), math.pi * .1, math.pi * .9, 3)
    pygame.draw.circle(surf, INK, (head[0] + d * 5, head[1] + 1), 2)
    if attack and attack["kind"] == "smash" and attack["t"] < ATTACKS["smash"]["a"]:
        pygame.draw.circle(surf, (255, 255, 255), (int(x), int(y - 45)), int(30 + math.sin(now * 25) * 3), 2)
    label = f"{'YOU' if is_me else p['name']}  {p['damage']}%"
    outline_text(surf, label, fonts["tiny"], (255, 255, 255) if is_me else color, (x, y - 92), "center", 1)
    if is_me:
        pygame.draw.polygon(surf, GOLD, [(x - 6, y - 108), (x + 6, y - 108), (x, y - 101)])


# ---------------------------------------------------------------- entry point
def main():
    ap = argparse.ArgumentParser(description="Rooftop Rumble game client")
    ap.add_argument("--protocol", choices=["tcp", "udp"], default="tcp")
    ap.add_argument("--host", default="127.0.0.1", help="server IP or hostname")
    ap.add_argument("--port", type=int, default=0, help="default: 5000 for TCP, 5001 for UDP")
    ap.add_argument("--name", default="")
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss, 0 to 1 (both TCP and UDP)")
    ap.add_argument("--delay", type=int, default=0, help="simulated one-way delay in ms (both TCP and UDP)")
    ap.add_argument("--log", default="", help="write per-second network metrics to this CSV file")
    ap.add_argument("--auto-connect", action="store_true", help="connect immediately with --name")
    App(ap.parse_args()).run()


if __name__ == "__main__":
    main()
