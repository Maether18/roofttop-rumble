"""Real-socket multiplayer checks, isolated to subprocesses and ephemeral ports."""
from contextlib import contextmanager
import json
from pathlib import Path
import queue
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

from client.session import MatchSession
from common import protocol as P
from tcp.client import TCPConnection
from tools.bots import decide
from udp.client import UDPConnection

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running_server(protocol, duration=2):
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="rooftop-tests-") as directory:
        log_path = Path(directory) / "server.log"
        with log_path.open("wb") as log:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", f"{protocol}.server", "--port", str(port),
                 "--duration", str(duration)], cwd=ROOT, stdout=log, stderr=log)
            try:
                end = time.monotonic() + 5
                while "server listening" not in log_path.read_text(encoding="utf-8", errors="replace"):
                    if proc.poll() is not None or time.monotonic() > end:
                        raise AssertionError("server failed to start: " + log_path.read_text(errors="replace"))
                    time.sleep(.01)
                yield proc, port, log_path
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)


class Clients:
    def __init__(self, protocol, port, proc, log_path, loss=0, delay=0):
        self.cls = TCPConnection if protocol == "tcp" else UDPConnection
        self.port, self.proc, self.log_path = port, proc, log_path
        self.loss, self.delay = loss, delay
        self.connections, self.messages, self.sessions, self.states, self.presses = {}, {}, {}, {}, {}

    def add(self, name):
        self.connections[name] = self.cls("127.0.0.1", self.port, name, self.loss, self.delay)
        self.messages[name], self.states[name] = [], []
        self.sessions[name] = MatchSession()
        self.presses[name] = dict(jump=0, punch=0, smash=0)

    def close(self):
        for conn in self.connections.values():
            conn.close()

    def poll(self):
        for name, conn in self.connections.items():
            while True:
                try:
                    msg = conn.messages.get_nowait()
                except queue.Empty:
                    break
                self.messages[name].append(msg)
                if self.sessions[name].accept(msg):
                    if msg["type"] == P.GAME_START:
                        self.presses[name] = dict(jump=0, punch=0, smash=0)
                        self.states[name] = []
                    elif msg["type"] == P.STATE:
                        self.states[name].append(msg)

    def wait(self, predicate, timeout=8, play=False):
        end, last_input = time.monotonic() + timeout, 0
        while time.monotonic() < end:
            self.poll()
            if predicate():
                return
            if self.proc.poll() is not None:
                raise AssertionError("server exited: " + self.log_path.read_text(errors="replace"))
            now = time.monotonic()
            if play and now - last_input > 1 / 30:
                last_input = now
                for name, session in self.sessions.items():
                    if session.active and self.states[name] and self.connections[name].connected.is_set():
                        keys = decide(name, self.states[name][-1], self.presses[name])
                        self.send(name, P.INPUT, match_id=session.match_id, **keys)
            time.sleep(.005)
        detail = {n: [m["type"] for m in values[-10:]] for n, values in self.messages.items()}
        raise AssertionError(f"timed out waiting for multiplayer transition: {detail}")

    def send(self, name, t, **fields):
        self.connections[name].send(P.make(t, **fields))

    def has(self, player, t, **fields):
        return any(m["type"] == t and all(m.get(k) == v for k, v in fields.items())
                   for m in self.messages[player])

    def room(self, size, owner=None):
        for msg in reversed(self.messages["A"]):
            if msg["type"] == P.ROOM_LIST:
                return any(len(r["players"]) == size and (owner is None or r["owner"] == owner)
                           for r in msg["rooms"])
        return False

    def prepare(self):
        for name in ("A", "B", "C"):
            self.add(name)
        self.wait(lambda: all(self.has(n, P.LOGIN_OK) for n in ("A", "B", "C")))
        self.send("A", P.CREATE_ROOM, room_name="Regression arena")
        self.wait(lambda: self.room(1))
        for name, size in (("B", 2), ("C", 3)):
            self.send(name, P.JOIN_ROOM, room_id=1)
            self.wait(lambda size=size: self.room(size))

    def start(self):
        previous = self.sessions["A"].latest_match_id
        self.send("A", P.START_GAME)
        self.wait(lambda: all(self.sessions[n].active and self.sessions[n].match_id > previous
                              for n in ("A", "B", "C")))
        return self.sessions["A"].match_id

    def complete(self, match_id, names=("A", "B", "C")):
        self.wait(lambda: all(self.has(n, P.GAME_OVER, match_id=match_id) for n in names), play=True)
        finals = [next(m for m in self.messages[n] if m["type"] == P.GAME_OVER
                       and m.get("match_id") == match_id) for n in names]
        if not all(m == finals[0] for m in finals):
            raise AssertionError("clients disagree about results")
        for name in names:
            critical = [m for m in self.messages[name] if m.get("match_id") == match_id
                        and m["type"] in (P.GAME_START, P.EVENT, P.GAME_OVER)]
            if critical[0]["type"] != P.GAME_START or critical[-1]["type"] != P.GAME_OVER:
                raise AssertionError("critical match messages arrived out of order")
        return finals[0]


class MultiplayerTests(unittest.TestCase):
    def exercise_normal(self, protocol):
        with running_server(protocol) as (proc, port, log_path):
            clients = Clients(protocol, port, proc, log_path)
            try:
                clients.prepare()
                duplicate = clients.cls("127.0.0.1", port, "A")
                try:
                    end = time.monotonic() + 3
                    rejected = False
                    while time.monotonic() < end and not rejected:
                        try:
                            rejected = duplicate.messages.get(timeout=.1)["type"] == P.LOGIN_FAIL
                        except queue.Empty:
                            pass
                    self.assertTrue(rejected, "duplicate name must be rejected")
                finally:
                    duplicate.close()
                clients.send("A", P.CHAT, text="hello room")
                clients.wait(lambda: all(clients.has(n, P.CHAT, text="hello room") for n in ("A", "B", "C")))
                clients.send("B", P.START_GAME)
                clients.wait(lambda: clients.has("B", P.ERROR))
                clients.add("D")
                clients.wait(lambda: clients.has("D", P.LOGIN_OK))
                clients.send("D", P.JOIN_ROOM, room_id=1)
                clients.wait(lambda: clients.has("D", P.ERROR, reason="This room is full"))
                clients.send("C", P.LEAVE_ROOM)
                clients.wait(lambda: clients.room(2))
                clients.send("C", P.JOIN_ROOM, room_id=1)
                clients.wait(lambda: clients.room(3))
                first = clients.start()
                final = clients.complete(first)
                self.assertEqual(len(final["ranking"]), 3)
                for name in ("A", "B", "C"):
                    self.assertGreaterEqual(len(clients.states[name]), 30)
                    xs = [m["players"][0]["x"] for m in clients.states[name]]
                    self.assertGreater(max(xs) - min(xs), 10, "inputs must move fighters")
                self.assertTrue(any(m["type"] == P.EVENT and m.get("kind") == "hit"
                                    for m in clients.messages["A"]), "combat events must still work")
                second = clients.start()
                self.assertGreater(second, first)
                clients.complete(second)
                clients.send("A", P.LEAVE_ROOM)
                clients.wait(lambda: clients.room(2, owner="B"))
                clients.send("B", P.LEAVE_ROOM)
                clients.send("C", P.LEAVE_ROOM)
                clients.wait(lambda: any(m["type"] == P.ROOM_LIST and not m["rooms"]
                                         for m in clients.messages["A"][-10:]))
            finally:
                clients.close()

    def exercise_impairment(self, protocol):
        with running_server(protocol) as (proc, port, log_path):
            clients = Clients(protocol, port, proc, log_path, loss=.1, delay=80)
            try:
                clients.prepare()
                match_id = clients.start()
                clients.complete(match_id)
                for name in ("A", "B", "C"):
                    self.assertGreater(len(clients.states[name]), 10)
                    self.assertTrue(clients.connections[name].connected.is_set())
            finally:
                clients.close()

    def exercise_disconnect(self, protocol):
        with running_server(protocol, duration=4) as (proc, port, log_path):
            clients = Clients(protocol, port, proc, log_path)
            try:
                clients.prepare()
                match_id = clients.start()
                clients.connections["C"].close()
                clients.wait(lambda: clients.has("A", P.PLAYER_LEFT, name="C"), play=True)
                final = clients.complete(match_id, ("A", "B"))
                self.assertEqual({r["name"] for r in final["ranking"]}, {"A", "B"})
                self.assertIsNone(proc.poll())
            finally:
                clients.close()

    def test_tcp_multiplayer_requirements_and_rematch(self):
        self.exercise_normal("tcp")

    def test_udp_multiplayer_requirements_and_rematch(self):
        self.exercise_normal("udp")

    def test_tcp_loss_and_delay(self):
        self.exercise_impairment("tcp")

    def test_udp_loss_and_delay(self):
        self.exercise_impairment("udp")

    def test_tcp_disconnect(self):
        self.exercise_disconnect("tcp")

    def test_udp_disconnect(self):
        self.exercise_disconnect("udp")

    def test_malformed_udp_corpus_cannot_stop_live_server(self):
        with running_server("udp") as (proc, port, log_path):
            packets = [b"garbage", b"\xff", b'{"type":[]}',
                       P.encode(dict(type=P.LOGIN, seq=0, rseq="bad")),
                       P.encode(dict(type=P.ACK, ack=[])),
                       P.encode(dict(type=P.ACK, ack={})),
                       b'{"type":"PING","x":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
                       b'{"type":"PING","x":' + b"9" * 5000 + b"}",
                       b"x" * 9000]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for packet in packets:
                    sender.sendto(packet, ("127.0.0.1", port))
            clients = Clients("udp", port, proc, log_path)
            try:
                clients.prepare()
                clients.send("A", P.JOIN_ROOM, room_id=[])
                clients.wait(lambda: clients.has("A", P.ERROR, reason="Room id must be a positive integer"))
                clients.send("A", P.CHAT, text="still alive")
                clients.wait(lambda: clients.has("B", P.CHAT, text="still alive"))
                clients.complete(clients.start())
                self.assertIsNone(proc.poll())
            finally:
                clients.close()

    def test_equal_delay_settings_produce_comparable_round_trips(self):
        medians = {}
        for protocol in ("tcp", "udp"):
            with running_server(protocol) as (proc, port, log_path):
                cls = TCPConnection if protocol == "tcp" else UDPConnection
                conn = cls("127.0.0.1", port, "Delay", delay_ms=80)
                try:
                    values, previous = [], None
                    end = time.monotonic() + 2.4
                    while time.monotonic() < end:
                        if conn.rtt_ms is not None and conn.rtt_ms != previous:
                            previous = conn.rtt_ms
                            values.append(previous)
                        time.sleep(.005)
                    self.assertGreaterEqual(len(values), 2)
                    medians[protocol] = statistics.median(values)
                finally:
                    conn.close()
        print("\n80 ms one-way delay, median RTT:", {p: round(v, 2) for p, v in medians.items()})
        for median in medians.values():
            self.assertGreater(median, 140)
            self.assertLess(median, 400)
        self.assertLess(abs(medians["tcp"] - medians["udp"]), 70)


if __name__ == "__main__":
    random.seed(411)
    unittest.main()
