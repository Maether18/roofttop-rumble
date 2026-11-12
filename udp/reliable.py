"""Our own reliability layer on top of UDP.

UDP can lose, duplicate and reorder datagrams. This class adds:
- "seq": a number on every packet we send to an address. For unreliable
  messages (STATE, INPUT, PING) the receiver keeps only the newest one
  of each type and drops anything older ("newest wins"). It is per type
  because different threads send different types: a PONG may leave
  before an older-numbered STATE, and that STATE is still new.
- "rseq": a separate counter for reliable messages (protocol.RELIABLE).
  Each one is acknowledged with an ACK and resent until acknowledged.
  The receiver delivers them exactly once and in order, holding back
  any that arrive early until the missing ones come in.
- optional simulated packet loss and delay, to compare with TCP fairly.
"""
import random
import collections
import select
import threading
import time

from common import protocol as P
from common.config import RESEND_INTERVAL, MAX_RESENDS, MAX_PACKET_SIZE

REORDER_WINDOW = 128


class ReliableUDP:
    def __init__(self, sock, loss=0.0, delay_ms=0, peer=None):
        P.validate_impairment(loss, delay_ms)
        self.sock = sock
        self.loss = loss
        self.delay = delay_ms / 1000
        self.peer = peer
        self.incoming = collections.deque()
        self.lock = threading.Lock()
        self.next_seq = {}          # addr -> next seq to send
        self.next_rseq = {}         # addr -> next reliable seq to send
        self.pending = {}           # (addr, rseq) -> [data, last_sent, tries]
        self.expected = {}          # addr -> next reliable seq we expect to deliver
        self.held = {}              # addr -> {rseq: msg} arrived early, waiting for a gap
        self.last_unreliable = {}   # (addr, type) -> newest seq delivered for that type
        self.stats = {"sent": 0, "received": 0, "resent": 0, "dropped_old": 0,
                      "duplicates": 0, "reordered": 0, "lost_sim": 0, "invalid": 0}

    # ---------- sending ----------
    def _raw_send(self, data, addr):
        if self.loss and random.random() < self.loss:
            self.stats["lost_sim"] += 1
            return
        if self.delay:
            threading.Timer(self.delay, self._sendto, (data, addr)).start()
        else:
            self._sendto(data, addr)

    def _sendto(self, data, addr):
        try:
            self.sock.sendto(data, addr)
        except OSError:
            pass

    def send(self, msg, addr):
        with self.lock:
            seq = self.next_seq.get(addr, 0)
            self.next_seq[addr] = seq + 1
            packet = {**msg, "seq": seq}
            if msg["type"] in P.RELIABLE:
                rseq = self.next_rseq.get(addr, 0)
                self.next_rseq[addr] = rseq + 1
                packet["rseq"] = rseq
                data = P.encode(packet)
                self.pending[(addr, rseq)] = [data, time.monotonic(), 0]
            else:
                data = P.encode(packet)
        self.stats["sent"] += 1
        self._raw_send(data, addr)

    # ---------- receiving ----------
    def _receive_datagram(self):
        """Pipeline incoming delay without sleeping once for every packet.

        Keep reading the socket while earlier datagrams wait. ACKs pass through
        this same delay, and an optional peer filter runs before any parsing.
        """
        while True:
            if self.delay:
                now = time.monotonic()
                if self.incoming and self.incoming[0][0] <= now:
                    _, data, addr = self.incoming.popleft()
                    return data, addr
                timeout = min(.1, max(0, self.incoming[0][0] - now)) if self.incoming else .1
                try:
                    if not select.select([self.sock], [], [], timeout)[0]:
                        continue
                except ValueError as exc:  # a socket closed during select()
                    raise OSError("UDP socket closed") from exc
            data, addr = self.sock.recvfrom(MAX_PACKET_SIZE + 1)
            if self.peer is not None and addr != self.peer:
                continue
            if not self.delay:
                return data, addr
            self.incoming.append((time.monotonic() + self.delay, data, addr))

    def receive(self):
        """Blocking. Returns (messages, addr): the list of new messages ready
        to handle, in order. The list is empty for ACKs, duplicates, stale
        updates, or reliable messages held back until a gap is filled."""
        data, addr = self._receive_datagram()
        if self.loss and random.random() < self.loss:
            self.stats["lost_sim"] += 1
            return [], addr
        msg = P.decode(data) if len(data) <= MAX_PACKET_SIZE else None
        if msg is None:
            self.stats["invalid"] += 1
            return [], addr
        # Reject malformed metadata before ACKs, comparisons or state changes.
        if any(not P.nonnegative_int(msg[key]) for key in ("seq", "rseq", "ack") if key in msg):
            self.stats["invalid"] += 1
            return [], addr
        if msg["type"] == P.ACK:
            if "ack" not in msg or "rseq" in msg:
                self.stats["invalid"] += 1
                return [], addr
        elif ("seq" not in msg or ((msg["type"] in P.RELIABLE) != ("rseq" in msg))):
            self.stats["invalid"] += 1
            return [], addr
        seq = msg.pop("seq", None)
        rseq = msg.pop("rseq", None)

        if msg["type"] == P.ACK:
            with self.lock:
                self.pending.pop((addr, msg.get("ack")), None)
            return [], addr

        if rseq is not None:
            with self.lock:
                expected = self.expected.get(addr, 0)
                if rseq >= expected + REORDER_WINDOW:
                    self.stats["invalid"] += 1
                    return [], addr
                # ACK duplicates too: our previous ACK may have been lost.
                self._raw_send(P.encode({"type": P.ACK, "ack": rseq}), addr)
                held = self.held.setdefault(addr, {})
                if rseq < expected or rseq in held:
                    self.stats["duplicates"] += 1
                    return [], addr
                if rseq > expected:
                    self.stats["reordered"] += 1
                held[rseq] = msg
                ready = []
                while expected in held:
                    ready.append(held.pop(expected))
                    expected += 1
                self.expected[addr] = expected
            self.stats["received"] += len(ready)
            return ready, addr

        if seq is not None:
            key = (addr, msg["type"])
            with self.lock:
                if seq <= self.last_unreliable.get(key, -1):
                    self.stats["dropped_old"] += 1
                    return [], addr
                self.last_unreliable[key] = seq
        self.stats["received"] += 1
        return [msg], addr

    # ---------- resend loop ----------
    def resend_due(self):
        """Call regularly. Resends unacknowledged reliable messages.
        Returns the addresses that stopped answering (gave up on them)."""
        now, failed = time.monotonic(), set()
        with self.lock:
            for key, entry in list(self.pending.items()):
                data, last, tries = entry
                if now - last < RESEND_INTERVAL:
                    continue
                if tries >= MAX_RESENDS:
                    failed.add(key[0])
                    del self.pending[key]
                    continue
                entry[1], entry[2] = now, tries + 1
                self.stats["resent"] += 1
                self._raw_send(data, key[0])
        return failed

    def forget(self, addr):
        """Drop all state for a disconnected address."""
        with self.lock:
            for table in (self.next_seq, self.next_rseq, self.expected, self.held):
                table.pop(addr, None)
            for key in [k for k in self.last_unreliable if k[0] == addr]:
                del self.last_unreliable[key]
            for key in [k for k in self.pending if k[0] == addr]:
                del self.pending[key]
