"""UDP server (starter). Uses UDP only, no TCP anywhere.

Working today: login with unique names, chat broadcast with acks and
resends, heartbeat-based disconnect detection.
TODO: rooms, game loop (common.game.Game), STATE broadcast, GAME_OVER.

Run: python -m udp.server
"""
import socket
import threading
import time

from common import protocol as P
from common.config import UDP_PORT, DISCONNECT_TIMEOUT
from udp.reliable import ReliableUDP
from tcp.server import local_ip


class Server:
    def __init__(self, port=UDP_PORT):
        self.port = port
        self.clients = {}   # addr -> {"name", "last_seen", "room"}
        self.lock = threading.Lock()

    def name_of(self, addr):
        c = self.clients.get(addr)
        return c["name"] if c else None

    def broadcast(self, msg, exclude=None):
        with self.lock:
            targets = [a for a in self.clients if a != exclude]
        for addr in targets:
            self.net.send(msg, addr)

    def handle(self, msg, addr):
        t = msg["type"]
        with self.lock:
            if addr in self.clients:
                self.clients[addr]["last_seen"] = time.time()
        if t == P.LOGIN:
            name = str(msg.get("name", "")).strip()[:16]
            with self.lock:
                if self.name_of(addr) == name:     # repeated login from same client
                    return
                taken = not name or any(c["name"] == name for c in self.clients.values())
                if not taken:
                    self.clients[addr] = {"name": name, "last_seen": time.time(), "room": None}
            if taken:
                self.net.send(P.make(P.LOGIN_FAIL, reason="Name empty or already taken"), addr)
                return
            players = [c["name"] for c in self.clients.values()]
            self.net.send(P.make(P.LOGIN_OK, name=name, players=players), addr)
            self.broadcast(P.make(P.CHAT, sender="server", text=f"{name} joined"), exclude=addr)
            print(f"[login] {name} from {addr} ({len(self.clients)} online)")
            return
        name = self.name_of(addr)
        if name is None:
            self.net.send(P.make(P.ERROR, reason="Log in first"), addr)
        elif t == P.PING:
            self.net.send(P.make(P.PONG, t=msg.get("t")), addr)
        elif t == P.CHAT:
            self.broadcast(P.make(P.CHAT, sender=name, text=str(msg.get("text", ""))[:200]))
        # TODO: CREATE_ROOM, JOIN_ROOM, LEAVE_ROOM, START_GAME, INPUT

    def remove(self, addr, reason):
        with self.lock:
            c = self.clients.pop(addr, None)
        self.net.forget(addr)
        if c:
            print(f"[left] {c['name']} ({reason}, {len(self.clients)} online)")
            self.broadcast(P.make(P.PLAYER_LEFT, name=c["name"]))

    def maintenance(self):
        """Resends lost reliable messages and detects silent clients."""
        while True:
            for addr in self.net.resend_due():
                self.remove(addr, "stopped acknowledging")
            now = time.time()
            with self.lock:
                silent = [a for a, c in self.clients.items() if now - c["last_seen"] > DISCONNECT_TIMEOUT]
            for addr in silent:
                self.remove(addr, f"no heartbeat for {DISCONNECT_TIMEOUT}s")
            time.sleep(0.05)

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        self.net = ReliableUDP(sock)
        threading.Thread(target=self.maintenance, daemon=True).start()
        print(f"UDP server listening on {local_ip()}:{self.port}")
        while True:
            try:
                msg, addr = self.net.receive()
            except ConnectionResetError:
                continue  # Windows reports ICMP "port unreachable" this way
            if msg:
                self.handle(msg, addr)


if __name__ == "__main__":
    try:
        Server().run()
    except KeyboardInterrupt:
        print("\nServer stopped")
