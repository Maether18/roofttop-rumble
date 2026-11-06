"""Terminal client for testing the server without the game window.

Works with TCPConnection or UDPConnection. Commands:
    /rooms            show the rooms
    /create [name]    create a room
    /join <id>        join a room
    /leave            leave your room
    /start            start the match (room owner only)
    /stats            network statistics
    /quit             disconnect
    anything else     chat message
During a match the console shows hits, KOs and the score every 5 seconds.
"""
import sys
import threading
import time

from common import protocol as P


def show_rooms(rooms):
    if not rooms:
        print("  No rooms yet. Create one with /create")
    for r in rooms:
        print(f"  #{r['id']} {r['name']}  [{r['status']}]  "
              f"{len(r['players'])}/{r['max']}: {', '.join(r['players'])}  (owner {r['owner']})")


def show(msg, state):
    t = msg["type"]
    if t == P.LOGIN_OK:
        print(f"Logged in as {msg['name']}. Online: {', '.join(msg['players'])}")
    elif t == P.LOGIN_FAIL:
        print(f"Login failed: {msg['reason']}")
    elif t == P.ROOM_LIST:
        state["rooms"] = msg["rooms"]
        print("Rooms:")
        show_rooms(msg["rooms"])
    elif t == P.CHAT:
        print(f"[{msg['sender']}] {msg['text']}")
    elif t == P.ERROR:
        print(f"! {msg['reason']}")
    elif t == P.GAME_START:
        print(f"*** Match started: {', '.join(p['name'] for p in msg['players'])} ({msg['duration']}s) ***")
        state["last_score"] = 0
    elif t == P.STATE:
        now = time.time()
        if now - state.get("last_score", 0) >= 5:
            state["last_score"] = now
            score = ", ".join(f"{p['name']} {p['kos']} KO {p['damage']}%" for p in msg["players"])
            print(f"  [{msg['time_left']:.0f}s left] {score}")
    elif t == P.EVENT:
        kind = msg.get("kind")
        if kind == "ko":
            print(f"  KO! {msg['victim']} fell" + (f", point for {msg['by']}" if msg.get("by") else ""))
        elif kind == "hit" and msg.get("attack") == "smash":
            print(f"  {msg['attacker']} smashed {msg['victim']} ({msg['damage']}%)")
        elif kind == "left":
            print(f"  {msg['name']} left the match")
    elif t == P.GAME_OVER:
        print("*** Match over ***")
        for i, r in enumerate(msg["ranking"], 1):
            print(f"  {i}. {r['name']}  {r['kos']} KOs, {r['falls']} falls")
        print(f"  Winner: {msg['winner'] or 'draw'}")
    elif t == P.PLAYER_LEFT:
        print(f"{msg['name']} {msg.get('reason', 'left')}")
    elif t == "DISCONNECTED":
        print(f"\n{msg['reason']}")


def run_console(conn):
    state = {"rooms": []}

    def printer():
        while conn.connected.is_set() or not conn.messages.empty():
            try:
                msg = conn.messages.get(timeout=0.2)
            except Exception:
                continue
            show(msg, state)

    threading.Thread(target=printer, daemon=True).start()
    print(f"Connected over {conn.protocol_name}. Commands: /rooms /create /join <id> /leave /start /stats /quit")
    try:
        for line in sys.stdin:
            if not conn.connected.is_set():
                break
            line = line.strip()
            cmd, _, arg = line.partition(" ")
            if cmd == "/quit":
                break
            elif cmd == "/rooms":
                show_rooms(state["rooms"])
            elif cmd == "/create":
                conn.send(P.make(P.CREATE_ROOM, room_name=arg.strip() or None))
            elif cmd == "/join":
                try:
                    conn.send(P.make(P.JOIN_ROOM, room_id=int(arg)))
                except ValueError:
                    print("Usage: /join <room id>")
            elif cmd == "/leave":
                conn.send(P.make(P.LEAVE_ROOM))
            elif cmd == "/start":
                conn.send(P.make(P.START_GAME))
            elif cmd == "/stats":
                rtt = f"{conn.rtt_ms:.0f} ms" if conn.rtt_ms is not None else "unknown"
                print(f"ping {rtt}, {conn.stats}")
            elif line:
                conn.send(P.make(P.CHAT, text=line))
    except KeyboardInterrupt:
        pass
    conn.close()
