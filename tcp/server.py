"""TCP server. Uses TCP only.

- One TCP connection per player, one thread per connection.
- Messages are framed as one JSON object per line (TCP is a byte stream).
- Disconnects are detected when recv() returns nothing or fails, or when
  a player sends nothing (not even a heartbeat) for DISCONNECT_TIMEOUT.
- Lobby, rooms and the game itself live in common/lobby.py and
  common/game.py, shared with the UDP version.

Run: python -m tcp.server
     python -m tcp.server --duration 30     (shorter matches for testing)
"""
import argparse
import socket
import threading

from common import protocol as P
from common.config import TCP_PORT, DISCONNECT_TIMEOUT, MATCH_SECONDS
from common.lobby import Lobby


class Connection:
    """A connected player's socket. The lock stops two threads (for example
    the game loop and a lobby reply) from mixing their bytes together."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def send(self, msg):
        data = P.encode_line(msg)
        with self.lock:
            self.sock.sendall(data)


class Server:
    def __init__(self, port=TCP_PORT, match_seconds=MATCH_SECONDS):
        self.port = port
        self.clients = {}                 # name -> Connection
        self.lock = threading.Lock()
        self.lobby = Lobby(self.send_to, match_seconds=match_seconds)

    # ---------- sending ----------
    def send_to(self, name, msg):
        with self.lock:
            conn = self.clients.get(name)
        if conn:
            try:
                conn.send(msg)
            except OSError:
                pass                      # its reader thread will notice and clean up

    def broadcast(self, msg, exclude=None):
        with self.lock:
            names = [n for n in self.clients if n != exclude]
        for n in names:
            self.send_to(n, msg)

    # ---------- one thread per connected player ----------
    def handle_client(self, sock, addr):
        conn = Connection(sock)
        reader = P.LineReader()
        name = None
        sock.settimeout(DISCONNECT_TIMEOUT)
        reason = "connection closed"
        try:
            while True:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    reason = f"no message for {DISCONNECT_TIMEOUT}s"
                    break
                if not data:              # empty read: the client closed the connection
                    break
                for msg in reader.feed(data):
                    if name is None:
                        name = self.login(conn, msg)
                    else:
                        self.handle_message(name, conn, msg)
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            reason = "connection reset"
        finally:
            sock.close()
            if name:
                self.logout(name, reason)

    def login(self, conn, msg):
        try:
            if msg["type"] != P.LOGIN:
                conn.send(P.make(P.ERROR, reason="Log in first"))
                return None
            name = str(msg.get("name", "")).strip()[:16]
            with self.lock:
                ok = bool(name) and name not in self.clients
                if ok:
                    self.clients[name] = conn
            if not ok:
                conn.send(P.make(P.LOGIN_FAIL, reason="Name empty or already taken"))
                return None
            conn.send(P.make(P.LOGIN_OK, name=name, players=list(self.clients)))
        except OSError:
            return None
        print(f"[login] {name} ({len(self.clients)} online)")
        self.lobby.add_player(name)
        return name

    def handle_message(self, name, conn, msg):
        t = msg["type"]
        if t == P.PING:
            self.send_to(name, P.make(P.PONG, t=msg.get("t")))
        elif t == P.CHAT:
            self.broadcast(P.make(P.CHAT, sender=name, text=str(msg.get("text", ""))[:200]))
        else:
            self.lobby.handle(name, msg)

    def logout(self, name, reason):
        with self.lock:
            self.clients.pop(name, None)
        print(f"[left] {name}: {reason} ({len(self.clients)} online)")
        self.lobby.remove_player(name)

    # ---------- main ----------
    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen()
        print(f"TCP server listening on {local_ip()}:{self.port}")
        while True:
            sock, addr = srv.accept()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # send small packets immediately
            threading.Thread(target=self.handle_client, args=(sock, addr), daemon=True).start()


def local_ip():
    """Best guess of this computer's LAN IP, to show clients where to connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))        # no packet is sent; it only picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rooftop Rumble TCP server")
    ap.add_argument("--port", type=int, default=TCP_PORT)
    ap.add_argument("--duration", type=int, default=MATCH_SECONDS, help="match length in seconds")
    args = ap.parse_args()
    try:
        Server(args.port, args.duration).run()
    except KeyboardInterrupt:
        print("\nServer stopped")
