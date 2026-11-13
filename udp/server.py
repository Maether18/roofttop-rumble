"""UDP server. Uses UDP only: no TCP anywhere, including login and the lobby.

- One UDP socket for everyone. Players are identified by their (IP, port).
- udp/reliable.py adds sequence numbers, ACKs, resends and in-order
  delivery for important messages. STATE, INPUT and PING are sent
  without ACKs: a newer one always replaces a lost one.
- Disconnects are detected with heartbeats: a player who sends nothing
  for DISCONNECT_TIMEOUT seconds, or stops acknowledging, is removed.
- Lobby, rooms and the game itself live in common/lobby.py and
  common/game.py, shared with the TCP version.

Run: python -m udp.server
     python -m udp.server --duration 30     (shorter matches for testing)
"""
import argparse
import socket
import threading
import time

from common import protocol as P
from common.config import UDP_PORT, DISCONNECT_TIMEOUT, MATCH_SECONDS
from common.lobby import Lobby
from tcp.server import local_ip
from udp.reliable import ReliableUDP


class Server:
    def __init__(self, port=UDP_PORT, match_seconds=MATCH_SECONDS):
        self.port = port
        self.by_addr = {}         # addr -> {"name", "last_seen"}
        self.by_name = {}         # name -> addr
        self.lock = threading.Lock()
        self.lobby = Lobby(self.send_to, match_seconds=match_seconds)

    # ---------- sending ----------
    def send_to(self, name, msg):
        with self.lock:
            addr = self.by_name.get(name)
        if addr:
            self.net.send(msg, addr)

    def broadcast(self, msg, exclude=None):
        with self.lock:
            names = [n for n in self.by_name if n != exclude]
        for n in names:
            self.send_to(n, msg)

    # ---------- receiving ----------
    def handle(self, msg, addr):
        with self.lock:
            client = self.by_addr.get(addr)
            if client:
                client["last_seen"] = time.time()
        if msg["type"] == P.LOGIN:
            return self.login(msg, addr)
        if client is None:
            if msg["type"] != P.PING:
                self.net.send(P.make(P.ERROR, reason="Log in first"), addr)
            return
        name, t = client["name"], msg["type"]
        if t == P.PING:
            self.net.send(P.make(P.PONG, t=msg.get("t")), addr)
        elif t == P.CHAT:
            self.broadcast(P.make(P.CHAT, sender=name, text=str(msg.get("text", ""))[:200]))
        else:
            self.lobby.handle(name, msg)

    def login(self, msg, addr):
        name = str(msg.get("name", "")).strip()[:16]
        with self.lock:
            if addr in self.by_addr:
                return            # this address is already logged in
            ok = bool(name) and name not in self.by_name
            if ok:
                self.by_addr[addr] = {"name": name, "last_seen": time.time()}
                self.by_name[name] = addr
        if not ok:
            return self.net.send(P.make(P.LOGIN_FAIL, reason="Name empty or already taken"), addr)
        self.net.send(P.make(P.LOGIN_OK, name=name, players=list(self.by_name)), addr)
        print(f"[login] {name} from {addr[0]}:{addr[1]} ({len(self.by_name)} online)")
        self.lobby.add_player(name)

    def logout(self, addr, reason):
        with self.lock:
            client = self.by_addr.pop(addr, None)
            if client:
                self.by_name.pop(client["name"], None)
        if client:
            print(f"[left] {client['name']}: {reason} ({len(self.by_name)} online)")
            self.lobby.remove_player(client["name"])
        self.net.forget(addr)

    # ---------- background: resends and heartbeat checks ----------
    def maintenance(self):
        while True:
            for addr in self.net.resend_due():
                self.logout(addr, "stopped acknowledging messages")
            now = time.time()
            with self.lock:
                silent = [a for a, c in self.by_addr.items() if now - c["last_seen"] > DISCONNECT_TIMEOUT]
            for addr in silent:
                self.logout(addr, f"no heartbeat for {DISCONNECT_TIMEOUT}s")
            time.sleep(0.05)

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        sock.settimeout(0.5)
        self.net = ReliableUDP(sock)
        threading.Thread(target=self.maintenance, daemon=True).start()
        print(f"UDP server listening on {local_ip()}:{self.port}")
        while True:
            try:
                messages, addr = self.net.receive()
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError):
                continue          # Windows reports ICMP "port unreachable" this way
            with self.lock:
                if addr in self.by_addr:
                    self.by_addr[addr]["last_seen"] = time.time()
            for msg in messages:
                self.handle(msg, addr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rooftop Rumble UDP server")
    ap.add_argument("--port", type=int, default=UDP_PORT)
    ap.add_argument("--duration", type=int, default=MATCH_SECONDS, help="match length in seconds")
    args = ap.parse_args()
    try:
        Server(args.port, args.duration).run()
    except KeyboardInterrupt:
        print("\nServer stopped")
