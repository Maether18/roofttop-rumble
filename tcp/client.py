"""TCP client connection.

TCPConnection is what the UI uses to talk to the server over TCP:
    conn.send(msg)          send a message
    conn.messages           queue.Queue of received messages
    conn.connected          threading.Event, cleared when the link is lost
    conn.close()
The UDP version (udp/client.py) has the same interface, so the UI does
not need to know which protocol it is using.

Run the terminal client: python -m tcp.client --host <SERVER_IP> --name Kai
"""
import argparse
import queue
import socket
import threading
import time

from common import protocol as P
from common.config import TCP_PORT, HEARTBEAT_INTERVAL


class TCPConnection:
    protocol_name = "TCP"

    def __init__(self, host, port, name):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.send_lock = threading.Lock()
        self.messages = queue.Queue()
        self.connected = threading.Event()
        self.connected.set()
        self.rtt_ms = None
        self.stats = {"sent": 0, "received": 0}
        self.send(P.make(P.LOGIN, name=name))      # LOGIN must be the first message
        threading.Thread(target=self._receive_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def send(self, msg):
        if not self.connected.is_set():
            return
        try:
            with self.send_lock:
                self.sock.sendall(P.encode_line(msg))
            self.stats["sent"] += 1
        except OSError:
            self._lost()

    def _receive_loop(self):
        reader = P.LineReader()
        while self.connected.is_set():
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                return self._lost()
            for msg in reader.feed(data):
                self.stats["received"] += 1
                if msg["type"] == P.PONG and isinstance(msg.get("t"), (int, float)):
                    self.rtt_ms = (time.time() - msg["t"]) * 1000
                else:
                    self.messages.put(msg)

    def _heartbeat_loop(self):
        while self.connected.is_set():
            self.send(P.make(P.PING, t=time.time()))
            time.sleep(HEARTBEAT_INTERVAL)

    def _lost(self):
        if self.connected.is_set():
            self.connected.clear()
            self.messages.put(P.make("DISCONNECTED", reason="Connection to the server lost"))

    def close(self):
        self.connected.clear()
        try:
            self.sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    from client.console import run_console
    ap = argparse.ArgumentParser(description="Rooftop Rumble TCP client (terminal)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=TCP_PORT)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    run_console(TCPConnection(args.host, args.port, args.name))
