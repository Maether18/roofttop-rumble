"""UDP client connection. Uses UDP only.

UDPConnection has the same interface as tcp/client.py's TCPConnection:
    conn.send(msg), conn.messages (queue), conn.connected (Event), conn.close()

Simulated packet loss and delay, for the TCP vs UDP comparison:
    python -m udp.client --host <SERVER_IP> --name Kai --loss 0.10 --delay 80
"""
import argparse
import queue
import socket
import threading
import time

from common import protocol as P
from common.config import UDP_PORT, HEARTBEAT_INTERVAL, DISCONNECT_TIMEOUT
from common.net import resolve_host
from udp.reliable import ReliableUDP


class UDPConnection:
    protocol_name = "UDP"

    def __init__(self, host, port, name, loss=0.0, delay_ms=0):
        self.server = (resolve_host(host), port)   # replies are matched against this
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.net = ReliableUDP(self.sock, loss=loss, delay_ms=delay_ms)
        self.stats = self.net.stats
        self.messages = queue.Queue()
        self.connected = threading.Event()
        self.connected.set()
        self.rtt_ms = None
        self.last_heard = time.time()
        self.send(P.make(P.LOGIN, name=name))      # LOGIN must be the first message
        threading.Thread(target=self._receive_loop, daemon=True).start()
        threading.Thread(target=self._maintenance_loop, daemon=True).start()

    def send(self, msg):
        if self.connected.is_set():
            self.net.send(msg, self.server)

    def _receive_loop(self):
        while self.connected.is_set():
            try:
                messages, addr = self.net.receive()
            except (ConnectionResetError, OSError):
                if not self.connected.is_set():
                    return
                continue
            if addr != self.server:
                continue
            self.last_heard = time.time()
            for msg in messages:
                if msg["type"] == P.PONG and isinstance(msg.get("t"), (int, float)):
                    self.rtt_ms = (time.time() - msg["t"]) * 1000
                else:
                    self.messages.put(msg)

    def _maintenance_loop(self):
        last_ping = 0.0
        while self.connected.is_set():
            if self.net.resend_due():
                return self._lost("The server stopped answering")
            now = time.time()
            if now - last_ping >= HEARTBEAT_INTERVAL:
                self.send(P.make(P.PING, t=now))
                last_ping = now
            if now - self.last_heard > DISCONNECT_TIMEOUT * 2:
                return self._lost("Connection to the server lost")
            time.sleep(0.02)

    def _lost(self, reason):
        if self.connected.is_set():
            self.connected.clear()
            self.messages.put(P.make("DISCONNECTED", reason=reason))

    def close(self):
        self.connected.clear()
        try:
            self.sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    from client.console import run_console
    ap = argparse.ArgumentParser(description="Rooftop Rumble UDP client (terminal)")
    ap.add_argument("--host", default="127.0.0.1", help="server IP or hostname")
    ap.add_argument("--port", type=int, default=UDP_PORT)
    ap.add_argument("--name", required=True)
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss, 0 to 1")
    ap.add_argument("--delay", type=int, default=0, help="simulated one-way delay in ms")
    args = ap.parse_args()
    run_console(UDPConnection(args.host, args.port, args.name, args.loss, args.delay))
