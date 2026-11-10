"""TCP client connection.

TCPConnection is what the UI uses to talk to the server over TCP:
    conn.send(msg)      send a message
    conn.messages       queue.Queue of received messages
    conn.connected      threading.Event, cleared when the link is lost
    conn.close()

The UDP version (udp/client.py) has the same interface, so the UI does not
need to know which protocol it is using.

Simulated packet loss and delay (models TCP Head-of-Line blocking stalls):
    python -m tcp.client --host <SERVER_IP> --name Kai --loss 0.10 --delay 80
"""

import argparse
import queue
import random
import socket
import threading
import time

from common import protocol as P
from common.config import TCP_PORT, HEARTBEAT_INTERVAL, RESEND_INTERVAL


class TCPConnection:
    protocol_name = "TCP"

    def __init__(self, host, port, name, loss=0.0, delay_ms=0):
        self.loss = loss
        self.delay = delay_ms / 1000.0
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.send_lock = threading.Lock()
        self.out_queue = queue.Queue()
        self.messages = queue.Queue()
        self.connected = threading.Event()
        self.connected.set()
        self.rtt_ms = None
        self.stats = {
            "sent": 0,
            "received": 0,
            "retransmitted_sim": 0,
            "hol_stalls": 0,
        }

        threading.Thread(target=self._send_loop, daemon=True).start()
        self.send(P.make(P.LOGIN, name=name))  # LOGIN must be the first message
        threading.Thread(target=self._receive_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _stream_stall(self):
        """Simulate TCP wait-and-retransmit when a segment is lost on the wire.
        In TCP, losing a segment stalls the ordered stream until retransmission
        completes (~1 RTT + detection, or RESEND_INTERVAL on localhost)."""
        extra = 0.0
        while self.loss and random.random() < self.loss:
            self.stats["retransmitted_sim"] += 1
            extra += max(self.delay * 2.0, RESEND_INTERVAL)
        if extra > 0:
            self.stats["hol_stalls"] += 1
        return extra

    def send(self, msg):
        if not self.connected.is_set():
            return
        data = P.encode_line(msg)
        self.stats["sent"] += 1
        # Preserve stream ordering while applying one-way delay + TCP retransmit stall
        deliver_at = time.time() + self.delay + self._stream_stall()
        self.out_queue.put((deliver_at, data))

    def _send_loop(self):
        while self.connected.is_set():
            try:
                deliver_at, data = self.out_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            wait = deliver_at - time.time()
            if wait > 0:
                time.sleep(wait)
            if not self.connected.is_set():
                return
            try:
                with self.send_lock:
                    self.sock.sendall(data)
            except OSError:
                self._lost()
                return

    def _receive_loop(self):
        reader = P.LineReader()
        in_queue = queue.Queue()

        def _deliver():
            while self.connected.is_set():
                try:
                    deliver_at, msgs = in_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                wait = deliver_at - time.time()
                if wait > 0:
                    time.sleep(wait)
                for msg in msgs:
                    self.stats["received"] += 1
                    if msg["type"] == P.PONG and isinstance(msg.get("t"), (int, float)):
                        self.rtt_ms = (time.time() - msg["t"]) * 1000
                    else:
                        self.messages.put(msg)

        threading.Thread(target=_deliver, daemon=True).start()
        stream_ready_at = 0.0

        while self.connected.is_set():
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                return self._lost()
            msgs = list(reader.feed(data))
            if not msgs:
                continue
            now = time.time()
            # Head-of-Line blocking: if this TCP segment was lost and retransmitted,
            # every subsequent byte in the stream is held back behind it.
            arrival = max(now + self.delay, stream_ready_at) + self._stream_stall()
            stream_ready_at = arrival
            in_queue.put((arrival, msgs))

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
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss, 0 to 1")
    ap.add_argument("--delay", type=int, default=0, help="simulated one-way delay in ms")
    args = ap.parse_args()
    run_console(TCPConnection(args.host, args.port, args.name, args.loss, args.delay))