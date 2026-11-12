import json
import queue
import socket
import threading
import time
import unittest
from unittest.mock import patch

from client.session import MatchSession
from common import protocol as P
from common.game import Game
from common.lobby import Lobby, PLAYING, WAITING
from tcp.client import TCPConnection
from udp.reliable import ReliableUDP, REORDER_WINDOW
from udp.server import Server as UDPServer

ADDR = ("127.0.0.1", 12345)


class DatagramSocket:
    def __init__(self, packets):
        self.packets = list(packets)
        self.sent = []

    def recvfrom(self, size):
        item = self.packets.pop(0)
        packet, addr = item if isinstance(item, tuple) else (item, ADDR)
        return packet if isinstance(packet, bytes) else P.encode(packet), addr

    def sendto(self, data, addr):
        self.sent.append((P.decode(data), addr))


def drain(lobby, timeout=3):
    end = time.monotonic() + timeout
    while lobby.outbox.unfinished_tasks:
        if time.monotonic() > end:
            raise AssertionError("outbound delivery did not finish")
        time.sleep(.005)


class ProtocolTests(unittest.TestCase):
    def test_decoder_rejects_malformed_types_and_json(self):
        packets = [b"not-json", b"\xff", b"[]", b"{}", b'{"type":[]}',
                   b'{"type":{}}', b'{"type":null}', b'{"type":""}',
                   b'{"type":"PING","x":' + b"[" * 1100 + b"0",
                   b'{"type":"PING","x":' + b"9" * 5000]
        for packet in packets:
            with self.subTest(packet=packet[:60]):
                self.assertIsNone(P.decode(packet))

    def test_decoder_contains_parser_limit_errors(self):
        # JSON nesting/integer limits vary across supported Python versions.
        for error in (RecursionError, ValueError):
            with self.subTest(error=error), patch("common.protocol.json.loads", side_effect=error):
                self.assertIsNone(P.decode(b'{"type":"PING"}'))

    def test_tcp_framing_still_handles_partial_and_combined_messages(self):
        reader = P.LineReader()
        wire = P.encode_line(P.make(P.LOGIN, name="Kai"))
        self.assertEqual(reader.feed(wire[:4]), [])
        self.assertEqual(len(reader.feed(wire[4:] + b"invalid\n" + wire)), 2)

    def test_command_fields_are_checked_without_raising(self):
        invalid = [P.make([], room_id=1), P.make(P.LOGIN, name=[]),
                   P.make(P.CHAT, text={}), P.make(P.CREATE_ROOM, room_name=[]),
                   P.make(P.JOIN_ROOM, room_id=[]), P.make(P.JOIN_ROOM, room_id=True),
                   P.make(P.INPUT, match_id=1, left=[]),
                   P.make(P.INPUT, match_id=1, jump_n=True),
                   P.make(P.INPUT, match_id=1, punch_n=-1),
                   P.make(P.INPUT, match_id=1, smash_n=1.5),
                   P.make(P.INPUT), P.make(P.PING, t=float("nan")),
                   P.make(P.PING, t=10 ** 1000)]
        for msg in invalid:
            with self.subTest(msg=msg):
                self.assertIsInstance(P.client_message_error(msg), str)
        for msg in [P.make(P.LOGIN, name="Kai"), P.make(P.CREATE_ROOM),
                    P.make(P.JOIN_ROOM, room_id=1), P.make(P.START_GAME),
                    P.make(P.LEAVE_ROOM), P.make(P.PING, t=time.time()),
                    P.make(P.INPUT, match_id=1, left=1, jump_n=3)]:
            self.assertIsNone(P.client_message_error(msg))

    def test_both_transports_reject_invalid_impairment_settings(self):
        for loss, delay in [(1, 0), (-.1, 0), (float("nan"), 0),
                            (0, -1), (0, float("inf"))]:
            with self.subTest(loss=loss, delay=delay):
                with self.assertRaises(ValueError):
                    TCPConnection("127.0.0.1", 1, "A", loss, delay)
                with self.assertRaises(ValueError):
                    ReliableUDP(DatagramSocket([]), loss, delay)


class ReliabilityTests(unittest.TestCase):
    def test_invalid_metadata_cannot_ack_or_mutate_ordering(self):
        packets = [b"garbage", b"\xff", b'{"type":[]}',
                   {"type": P.LOGIN, "seq": 0, "rseq": "bad"},
                   {"type": P.LOGIN, "seq": 0},
                   {"type": P.STATE},
                   {"type": P.ACK}, {"type": P.ACK, "ack": []},
                   {"type": P.ACK, "ack": {}}, {"type": P.ACK, "ack": True},
                   {"type": P.ACK, "ack": -1},
                   {"type": P.STATE, "seq": 0, "rseq": 0},
                   b"x" * 8193]
        for field in ("seq", "rseq"):
            for bad in ([], {}, True, -1, 1.2, None, "1"):
                packet = {"type": P.LOGIN, "seq": 0, "rseq": 0}
                packet[field] = bad
                packets.append(packet)
        for packet in packets:
            with self.subTest(packet=str(packet)[:100]):
                sock = DatagramSocket([packet])
                net = ReliableUDP(sock)
                net.pending[(ADDR, 0)] = [b"pending", 0, 0]
                self.assertEqual(net.receive(), ([], ADDR))
                self.assertEqual(net.expected, {})
                self.assertEqual(net.held, {})
                self.assertEqual(net.last_unreliable, {})
                self.assertIn((ADDR, 0), net.pending)
                self.assertEqual(sock.sent, [])

    def test_reliable_reordering_duplicates_and_lost_ack_recovery(self):
        sock = DatagramSocket([
            dict(type=P.CHAT, seq=1, rseq=1, text="second"),
            dict(type=P.CHAT, seq=0, rseq=0, text="first"),
            dict(type=P.CHAT, seq=0, rseq=0, text="first")])
        net = ReliableUDP(sock)
        self.assertEqual(net.receive()[0], [])
        self.assertEqual([m["text"] for m in net.receive()[0]], ["first", "second"])
        self.assertEqual(net.receive()[0], [])
        self.assertEqual(len(sock.sent), 3, "duplicates must be ACKed again")

    def test_stale_updates_are_dropped_per_message_type(self):
        net = ReliableUDP(DatagramSocket([
            dict(type=P.STATE, seq=6, tick=6), dict(type=P.PONG, seq=8),
            dict(type=P.STATE, seq=3, tick=3), dict(type=P.STATE, seq=7, tick=9)]))
        self.assertEqual(net.receive()[0][0]["tick"], 6)
        self.assertEqual(net.receive()[0][0]["type"], P.PONG)
        self.assertEqual(net.receive()[0], [])
        self.assertEqual(net.receive()[0][0]["tick"], 9)

    def test_far_future_reliable_packet_does_not_poison_stream(self):
        sock = DatagramSocket([dict(type=P.LOGIN, seq=0, rseq=REORDER_WINDOW),
                               dict(type=P.LOGIN, seq=1, rseq=0, name="A")])
        net = ReliableUDP(sock)
        self.assertEqual(net.receive()[0], [])
        self.assertEqual(sock.sent, [])
        self.assertEqual(net.held, {})
        self.assertEqual(net.receive()[0][0]["name"], "A")

    def test_bad_application_payload_does_not_block_next_reliable_command(self):
        sock = DatagramSocket([dict(type=P.JOIN_ROOM, room_id=[], seq=0, rseq=0),
                               dict(type=P.CREATE_ROOM, seq=1, rseq=1)])
        net = ReliableUDP(sock)
        sent = []
        server = UDPServer(port=0)
        server.net = net
        server.lobby._send = lambda name, msg: sent.append(msg)
        self.addCleanup(server.lobby.close)
        server.by_addr[ADDR] = {"name": "A", "last_seen": time.time()}
        server.by_name["A"] = ADDR
        server.lobby.add_player("A")
        for _ in range(2):
            messages, addr = net.receive()
            for msg in messages:
                server.handle(msg, addr)
        drain(server.lobby)
        self.assertTrue(any(m["type"] == P.ERROR for m, _ in sock.sent))
        self.assertEqual(server.lobby.rooms[1].owner, "A")
        self.assertEqual(net.expected[ADDR], 2)

    def test_client_filters_foreign_sender_before_ack_and_state(self):
        foreign = ("127.0.0.1", 23456)
        sock = DatagramSocket([(dict(type=P.LOGIN_OK, seq=0, rseq=0), foreign),
                               (dict(type=P.LOGIN_OK, seq=0, rseq=0), ADDR)])
        net = ReliableUDP(sock, peer=ADDR)
        self.assertEqual(net.receive()[1], ADDR)
        self.assertNotIn(foreign, net.expected)
        self.assertEqual([addr for _, addr in sock.sent], [ADDR])

    def test_resend_budget_and_ack_cleanup(self):
        net = ReliableUDP(DatagramSocket([dict(type=P.ACK, ack=0)]))
        with patch("udp.reliable.time.monotonic", return_value=100):
            net.send(P.make(P.LOGIN, name="A"), ADDR)
        for i in range(1, 12):
            with patch("udp.reliable.time.monotonic", return_value=100 + .21 * i):
                failed = net.resend_due()
        self.assertEqual(failed, {ADDR})
        self.assertEqual(net.stats["resent"], 10)
        net.send(P.make(P.LOGIN, name="A"), ADDR)
        # ACK the new pending reliable number.
        net.sock.packets = [dict(type=P.ACK, ack=1)]
        net.receive()
        self.assertEqual(net.pending, {})

    def test_incoming_delay_is_pipelined_at_game_snapshot_rates(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2)
            net = ReliableUDP(receiver, delay_ms=80)
            for i in range(40):
                sender.sendto(P.encode(dict(type=P.STATE, seq=i, tick=i)), receiver.getsockname())
            start = time.monotonic()
            ticks = [net.receive()[0][0]["tick"] for _ in range(40)]
            elapsed = time.monotonic() - start
            self.assertEqual(ticks, list(range(40)))
            self.assertGreaterEqual(elapsed, .06)
            self.assertLess(elapsed, 1.0, "80 ms must not be added serially to each packet")

    def test_ack_is_delayed_in_both_directions(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            receiver.bind(("127.0.0.1", 0))
            sender.settimeout(2)
            net = ReliableUDP(receiver, delay_ms=80)
            start = time.monotonic()
            sender.sendto(P.encode(dict(type=P.CHAT, seq=0, rseq=0, text="hello")), receiver.getsockname())
            self.assertEqual(net.receive()[0][0]["text"], "hello")
            ack = P.decode(sender.recvfrom(8192)[0])
            self.assertEqual(ack, dict(type=P.ACK, ack=0))
            self.assertGreaterEqual(time.monotonic() - start, .14)

    def test_closing_socket_unblocks_delayed_receiver(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        sock.bind(("127.0.0.1", 0))
        net = ReliableUDP(sock, delay_ms=80)
        stopped = threading.Event()
        def receive():
            try:
                net.receive()
            except OSError:
                stopped.set()
        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        time.sleep(.02)
        sock.close()
        self.assertTrue(stopped.wait(1), "closing must not leave select blocked forever")


class MatchTests(unittest.TestCase):
    def test_client_rejects_unstarted_old_duplicate_and_finished_match_packets(self):
        session = MatchSession()
        def msg(t, match_id=1, **fields):
            return P.make(t, room_id=1, match_id=match_id, **fields)
        self.assertFalse(session.accept(msg(P.STATE, tick=3)))
        self.assertTrue(session.accept(msg(P.GAME_START)))
        self.assertFalse(session.accept(msg(P.GAME_START)))
        self.assertTrue(session.accept(msg(P.STATE, tick=3)))
        self.assertFalse(session.accept(msg(P.STATE, tick=3)))
        self.assertFalse(session.accept(msg(P.STATE, tick=0)))
        self.assertTrue(session.accept(msg(P.GAME_OVER)))
        self.assertFalse(session.accept(msg(P.STATE, tick=6)))
        self.assertFalse(session.accept(msg(P.GAME_START)))
        self.assertTrue(session.accept(msg(P.GAME_START, 2)))
        for t in (P.STATE, P.EVENT, P.GAME_OVER, P.PLAYER_LEFT, P.GAME_START):
            self.assertFalse(session.accept(msg(t, 1, tick=99)))
        self.assertTrue(session.accept(msg(P.STATE, 2, tick=3)))
        self.assertTrue(session.accept(P.make(P.PLAYER_LEFT, name="B")))

    def test_stale_input_cannot_poison_new_match_action_counters(self):
        lobby = Lobby(lambda *_: None, log=lambda _: None)
        self.addCleanup(lobby.close)
        lobby.add_player("A")
        lobby.handle("A", P.make(P.CREATE_ROOM))
        room = lobby.rooms[1]
        room.game, room.status, room.match_id = Game(["A", "B"]), PLAYING, 7
        lobby.handle("A", P.make(P.INPUT, match_id=6, right=1, jump_n=99999))
        f = room.game.fighters["A"]
        self.assertEqual(f.counters["jump"], 0)
        self.assertEqual(f.held["right"], 0)
        lobby.handle("A", P.make(P.INPUT, match_id=7, left=1, jump_n=1))
        self.assertEqual(f.counters["jump"], 1)
        self.assertEqual(f.held["left"], 1)

    def test_blocked_start_send_cannot_be_overtaken_even_by_rematch(self):
        entered, release = threading.Event(), threading.Event()
        output = []
        def send(name, msg):
            if name == "A" and msg["type"] == P.GAME_START and msg["match_id"] == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test did not release sender")
            if name == "A":
                output.append(msg)
        lobby = Lobby(send, match_seconds=.05, log=lambda _: None)
        self.addCleanup(lobby.close)
        self.addCleanup(release.set)
        for name in ("A", "B"):
            lobby.add_player(name)
        lobby.handle("A", P.make(P.CREATE_ROOM))
        lobby.handle("B", P.make(P.JOIN_ROOM, room_id=1))
        drain(lobby)
        lobby.handle("A", P.make(P.START_GAME))
        self.assertTrue(entered.wait(2))
        for match_id in (1, 2):
            end = time.monotonic() + 2
            while True:
                with lobby.lock:
                    finished = lobby.rooms[1].status == WAITING
                if finished or time.monotonic() > end:
                    break
                time.sleep(.005)
            self.assertTrue(finished, "sending must not stall simulation")
            if match_id == 1:
                lobby.handle("A", P.make(P.START_GAME))
        release.set()
        drain(lobby)
        messages = [(m["type"], m["match_id"]) for m in output if "match_id" in m]
        self.assertEqual(messages, [(P.GAME_START, 1), (P.STATE, 1), (P.GAME_OVER, 1),
                                    (P.GAME_START, 2), (P.STATE, 2), (P.GAME_OVER, 2)])

    def test_failed_recipient_does_not_stop_other_match_messages(self):
        output = []
        def send(name, msg):
            if name == "A":
                raise OSError("disconnected")
            output.append(msg)
        lobby = Lobby(send, match_seconds=.05, log=lambda _: None)
        self.addCleanup(lobby.close)
        for name in ("A", "B"):
            lobby.add_player(name)
        lobby.handle("A", P.make(P.CREATE_ROOM))
        lobby.handle("B", P.make(P.JOIN_ROOM, room_id=1))
        lobby.handle("A", P.make(P.START_GAME))
        end = time.monotonic() + 2
        while not any(m["type"] == P.GAME_OVER for m in output) and time.monotonic() < end:
            time.sleep(.005)
        self.assertTrue(any(m["type"] == P.GAME_OVER for m in output))

    def test_game_scoring_respawn_and_tiebreak_are_preserved(self):
        game = Game(["A", "B", "C"])
        victim = game.fighters["B"]
        victim.last_hit_by, victim.last_hit_at = "A", game.clock
        game._fall(victim)
        self.assertEqual(game.fighters["A"].kos, 1)
        self.assertEqual(victim.falls, 1)
        for _ in range(91):
            game.step(1 / 60)
        self.assertTrue(victim.alive)
        self.assertEqual(victim.damage, 0)
        self.assertGreater(victim.shield_until, game.clock)
        self.assertEqual(game.winner(), "A")
        game.fighters["B"].kos = 1
        game.fighters["B"].falls = 0
        self.assertIsNone(game.winner())


class TCPDelayTests(unittest.TestCase):
    def test_outgoing_stalls_preserve_order_without_serializing_propagation_delay(self):
        conn = TCPConnection.__new__(TCPConnection)
        conn.connected = threading.Event()
        conn.connected.set()
        conn.send_lock = threading.Lock()
        conn.out_queue = queue.Queue()
        conn.stats = {"sent": 0}
        conn.delay, conn.stream_send_at = .08, 0
        with patch.object(conn, "_stream_stall", side_effect=[.2, .2, 0]), \
                patch("tcp.client.time.monotonic", return_value=100):
            for i in range(3):
                conn.send(P.make(P.INPUT, match_id=1, jump_n=i))
        packets = [conn.out_queue.get_nowait() for _ in range(3)]
        self.assertAlmostEqual(packets[0][0], 100.28)
        self.assertAlmostEqual(packets[1][0], 100.48)
        self.assertAlmostEqual(packets[2][0], 100.48)
        self.assertEqual([P.decode(data)["jump_n"] for _, data in packets], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
