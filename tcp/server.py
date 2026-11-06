"""TCP server (starter).

Working today: login with unique names, chat broadcast, heartbeats,
disconnect detection, one thread per client.
TODO: rooms, game loop (common.game.Game), STATE broadcast, GAME_OVER.

Run: python -m tcp.server
"""
import socket
import threading
import time

from common import protocol as P
from common.config import TCP_PORT, DISCONNECT_TIMEOUT


class Server:
    def __init__(self, port=TCP_PORT):
        self.port = port
        self.clients = {}            # name -> client info
        self.lock = threading.Lock()

    # ---------- sending ----------
    def send(self, conn, msg):
        try:
            conn.sendall(P.encode_line(msg))
        except OSError:
            pass  # the reader thread will notice the disconnect

    def broadcast(self, msg, exclude=None):
        with self.lock:
            targets = [c["conn"] for n, c in self.clients.items() if n != exclude]
        for conn in targets:
            self.send(conn, msg)

    # ---------- per-client thread ----------
    def handle_client(self, conn, addr):
        reader = P.LineReader()
        name = None
        conn.settimeout(DISCONNECT_TIMEOUT)
        try:
            while True:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    print(f"[timeout] {name or addr} sent nothing for {DISCONNECT_TIMEOUT}s")
                    break
                if not data:          # empty read = the client closed the connection
                    break
                for msg in reader.feed(data):
                    if name is None:
                        name = self.handle_login(conn, msg)
                    else:
                        self.handle_message(name, conn, msg)
        except (ConnectionResetError, OSError):
            pass
        finally:
            conn.close()
            if name:
                self.remove(name)

    def handle_login(self, conn, msg):
        if msg["type"] != P.LOGIN:
            self.send(conn, P.make(P.ERROR, reason="Log in first"))
            return None
        name = str(msg.get("name", "")).strip()[:16]
        with self.lock:
            taken = not name or name in self.clients
            if not taken:
                self.clients[name] = {"conn": conn, "room": None}
        if taken:
            self.send(conn, P.make(P.LOGIN_FAIL, reason="Name empty or already taken"))
            return None
        self.send(conn, P.make(P.LOGIN_OK, name=name, players=list(self.clients)))
        self.broadcast(P.make(P.CHAT, sender="server", text=f"{name} joined"), exclude=name)
        print(f"[login] {name} ({len(self.clients)} online)")
        return name

    def handle_message(self, name, conn, msg):
        t = msg["type"]
        if t == P.PING:
            self.send(conn, P.make(P.PONG, t=msg.get("t")))
        elif t == P.CHAT:
            self.broadcast(P.make(P.CHAT, sender=name, text=str(msg.get("text", ""))[:200]))
        # TODO: CREATE_ROOM, JOIN_ROOM, LEAVE_ROOM, START_GAME, INPUT

    def remove(self, name):
        with self.lock:
            self.clients.pop(name, None)
        print(f"[left] {name} ({len(self.clients)} online)")
        self.broadcast(P.make(P.PLAYER_LEFT, name=name))

    # ---------- main ----------
    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen()
        print(f"TCP server listening on {local_ip()}:{self.port}")
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # no batching delay
            threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()


def local_ip():
    """Best guess of this computer's LAN IP, to show clients where to connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet is sent; it only picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    try:
        Server().run()
    except KeyboardInterrupt:
        print("\nServer stopped")
