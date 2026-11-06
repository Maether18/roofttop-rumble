"""UDP client (starter): log in and chat from the terminal.

Supports simulated packet loss and delay for the TCP vs UDP comparison:
    python -m udp.client --host <SERVER_IP> --name Kai --loss 0.10 --delay 80

TODO: replace the terminal chat with the pygame UI (client/ui.py).
"""
import argparse
import socket
import sys
import threading
import time

from common import protocol as P
from common.config import UDP_PORT, HEARTBEAT_INTERVAL, DISCONNECT_TIMEOUT
from udp.reliable import ReliableUDP


def main():
    ap = argparse.ArgumentParser(description="Rooftop Rumble UDP client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=UDP_PORT)
    ap.add_argument("--name", required=True)
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss, 0 to 1")
    ap.add_argument("--delay", type=int, default=0, help="simulated one-way delay in ms")
    args = ap.parse_args()

    server = (socket.gethostbyname(args.host), args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    net = ReliableUDP(sock, loss=args.loss, delay_ms=args.delay)
    running = threading.Event()
    running.set()
    last_heard = [time.time()]

    net.send(P.make(P.LOGIN, name=args.name), server)

    def receive():
        while running.is_set():
            try:
                msg, addr = net.receive()
            except (ConnectionResetError, OSError):
                continue
            if addr != server:
                continue
            last_heard[0] = time.time()
            if msg:
                show(msg)

    def maintenance():
        last_ping = 0
        while running.is_set():
            if net.resend_due():
                print("\nServer stopped answering")
                running.clear()
            now = time.time()
            if now - last_ping >= HEARTBEAT_INTERVAL:
                net.send(P.make(P.PING, t=now), server)
                last_ping = now
            if now - last_heard[0] > DISCONNECT_TIMEOUT * 2:
                print("\nLost connection to the server")
                running.clear()
            time.sleep(0.05)

    threading.Thread(target=receive, daemon=True).start()
    threading.Thread(target=maintenance, daemon=True).start()

    print("Type a message and press Enter. Type /stats for network stats, /quit to leave.")
    try:
        for line in sys.stdin:
            if not running.is_set():
                break
            line = line.strip()
            if line == "/quit":
                break
            if line == "/stats":
                print(net.stats)
            elif line:
                net.send(P.make(P.CHAT, text=line), server)
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


if __name__ == "__main__":
    main()
