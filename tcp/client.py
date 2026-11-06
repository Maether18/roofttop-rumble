"""TCP client (starter): log in and chat from the terminal.

TODO: replace the terminal chat with the pygame UI (client/ui.py).

Run: python -m tcp.client --host <SERVER_IP> --name Kai
"""
import argparse
import socket
import sys
import threading
import time

from common import protocol as P
from common.config import TCP_PORT, HEARTBEAT_INTERVAL


def main():
    ap = argparse.ArgumentParser(description="Rooftop Rumble TCP client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=TCP_PORT)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=5)
    sock.settimeout(None)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    send_lock = threading.Lock()

    def send(msg):
        with send_lock:
            sock.sendall(P.encode_line(msg))

    send(P.make(P.LOGIN, name=args.name))
    running = threading.Event()
    running.set()

    def receive():
        reader = P.LineReader()
        while running.is_set():
            try:
                data = sock.recv(4096)
            except OSError:
                data = b""
            if not data:
                print("\nDisconnected from server")
                running.clear()
                break
            for msg in reader.feed(data):
                show(msg)

    def heartbeat():
        while running.is_set():
            try:
                send(P.make(P.PING, t=time.time()))
            except OSError:
                break
            time.sleep(HEARTBEAT_INTERVAL)

    threading.Thread(target=receive, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()

    print("Type a message and press Enter. Type /quit to leave.")
    try:
        for line in sys.stdin:
            if not running.is_set():
                break
            line = line.strip()
            if line == "/quit":
                break
            if line:
                send(P.make(P.CHAT, text=line))
    except KeyboardInterrupt:
        pass
    running.clear()
    sock.close()


def show(msg):
    t = msg["type"]
    if t == P.LOGIN_OK:
        print(f"Logged in as {msg['name']}. Online: {', '.join(msg['players'])}")
    elif t == P.LOGIN_FAIL:
        print(f"Login failed: {msg['reason']}")
    elif t == P.CHAT:
        print(f"[{msg['sender']}] {msg['text']}")
    elif t == P.PLAYER_LEFT:
        print(f"{msg['name']} disconnected")
    elif t == P.PONG:
        rtt = (time.time() - msg.get("t", time.time())) * 1000
        # Uncomment to watch latency: print(f"ping {rtt:.0f} ms")


if __name__ == "__main__":
    main()
