"""Bot players for testing the server without three people.

The bots log in, create and join a room, start a match and fight by
sending INPUT messages based on the STATE they receive.

    python -m tools.bots --protocol tcp
    python -m tools.bots --protocol udp --loss 0.1 --delay 50
    python -m tools.bots --protocol tcp --bots 3 --kill-one 10   (one bot quits after 10 s)

Play against bots from the game window: create a room in the game,
then run  python -m tools.bots --bots 2 --join-only  and start the match.
"""
import argparse
import random
import threading
import time

from common import protocol as P
from common.config import TCP_PORT, UDP_PORT


def run_bot(conn, name, is_owner, n_bots, results, no_start, kill_after, join_only=False):
    presses = {"jump": 0, "punch": 0, "smash": 0}
    me, room_id, playing, started_at = None, None, False, None
    last_input = 0.0
    while conn.connected.is_set():
        try:
            msg = conn.messages.get(timeout=1 / 30)
        except Exception:
            msg = None
        if msg:
            t = msg["type"]
            if t == P.ROOM_LIST:
                rooms = msg["rooms"]
                if is_owner and room_id is None and not any(name in r["players"] for r in rooms):
                    conn.send(P.make(P.CREATE_ROOM, room_name="Bot arena"))
                    room_id = -1
                for r in rooms:
                    if name in r["players"]:
                        room_id = r["id"]
                    if (not is_owner and room_id is None and (join_only or r["name"] == "Bot arena")
                            and r["status"] == "waiting" and len(r["players"]) < r["max"]):
                        conn.send(P.make(P.JOIN_ROOM, room_id=r["id"]))
                        room_id = r["id"]
                    if (is_owner and not no_start and r["id"] == room_id and r["status"] == "waiting"
                            and len(r["players"]) >= n_bots and not playing and started_at is None):
                        conn.send(P.make(P.START_GAME))
                        started_at = time.time()
            elif t == P.GAME_START:
                playing = True
                started_at = time.time()
                print(f"[{name}] match started")
            elif t == P.STATE:
                me = msg
            elif t == P.GAME_OVER:
                results[name] = msg
                print(f"[{name}] match over, winner: {msg['winner'] or 'draw'}")
                conn.close()
                return
            elif t == P.ERROR:
                print(f"[{name}] error: {msg['reason']}")
        if kill_after and playing and time.time() - started_at > kill_after:
            print(f"[{name}] quitting suddenly (disconnect test)")
            conn.close()
            return
        now = time.time()
        if playing and me and now - last_input >= 1 / 30:
            last_input = now
            conn.send(P.make(P.INPUT, **decide(name, me, presses)))


def decide(name, state, presses):
    players = {p["name"]: p for p in state["players"]}
    me = players.get(name)
    keys = {"left": 0, "right": 0, "down": 0}
    if me and me["alive"]:
        others = [p for p in players.values() if p["name"] != name and p["alive"]]
        if me["x"] < 200 or me["x"] > 760 or me["y"] > 430:
            keys["right" if me["x"] < 480 else "left"] = 1     # get back to the roof
            if me["vy"] > 0 and random.random() < .2:
                presses["jump"] += 1
        elif others:
            o = min(others, key=lambda p: abs(p["x"] - me["x"]) + abs(p["y"] - me["y"]))
            dx, dy = o["x"] - me["x"], o["y"] - me["y"]
            if abs(dx) > 40:
                keys["right" if dx > 0 else "left"] = 1
            if dy < -70 and random.random() < .1:
                presses["jump"] += 1
            if abs(dx) < 60 and abs(dy) < 46:
                if o["damage"] > 50 and random.random() < .15:
                    presses["smash"] += 1
                elif random.random() < .3:
                    presses["punch"] += 1
    keys.update({k + "_n": v for k, v in presses.items()})
    return keys


def main():
    ap = argparse.ArgumentParser(description="Bot players for testing")
    ap.add_argument("--protocol", choices=["tcp", "udp"], default="tcp")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--bots", type=int, default=3)
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss, 0 to 1")
    ap.add_argument("--delay", type=int, default=0, help="simulated one-way delay (ms)")
    ap.add_argument("--kill-one", type=float, default=0, help="last bot quits after N seconds of play")
    ap.add_argument("--no-start", action="store_true", help="wait for a real player to start")
    ap.add_argument("--join-only", action="store_true", help="bots join any waiting room (yours) instead of creating one")
    args = ap.parse_args()

    names = [f"Bot{i + 1}" for i in range(args.bots)]
    results, threads = {}, []
    for i, name in enumerate(names):
        if args.protocol == "tcp":
            from tcp.client import TCPConnection
            conn = TCPConnection(args.host, TCP_PORT, name, args.loss, args.delay)
        else:
            from udp.client import UDPConnection
            conn = UDPConnection(args.host, UDP_PORT, name, args.loss, args.delay)
        kill = args.kill_one if i == len(names) - 1 else 0
        th = threading.Thread(target=run_bot, args=(conn, name, i == 0 and not args.join_only, args.bots, results,
                                                  args.no_start, kill, args.join_only))
        th.start()
        threads.append(th)
        time.sleep(0.3)
    for th in threads:
        th.join()
    if results:
        final = next(iter(results.values()))
        print("Final ranking:")
        for i, r in enumerate(final["ranking"], 1):
            print(f"  {i}. {r['name']}  {r['kos']} KOs, {r['falls']} falls")


if __name__ == "__main__":
    main()
