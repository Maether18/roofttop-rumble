"""Our own reliability layer on top of UDP.

UDP can lose, duplicate and reorder datagrams. This class adds:
- "seq": a number on every packet we send to an address. For unreliable
  messages (STATE, INPUT, PING) the receiver keeps only the newest one
  and drops anything older ("newest wins").
- "rseq": a separate counter for reliable messages (protocol.RELIABLE).
  Each one is acknowledged with an ACK and resent until acknowledged.
  The receiver delivers them exactly once and in order, holding back
  any that arrive early until the missing ones come in.
- optional simulated packet loss and delay, to compare with TCP fairly.
"""
import random
import threading
import time

from common import protocol as P
from common.config import RESEND_INTERVAL, MAX_RESENDS, MAX_PACKET_SIZE


class ReliableUDP:
    def __init__(self, sock, loss=0.0, delay_ms=0):
        self.sock = sock
        self.loss = loss
        self.delay = delay_ms / 1000
        self.lock = threading.Lock()
        self.next_seq = {}          # addr -> next seq to send
        self.next_rseq = {}         # addr -> next reliable seq to send
        self.pending = {}           # (addr, rseq) -> [data, last_sent, tries]
        self.expected = {}          # addr -> next reliable seq we expect to deliver
        self.held = {}              # addr -> {rseq: msg} arrived early, waiting for a gap
        self.last_unreliable = {}   # addr -> newest unreliable seq delivered
        self.stats = {"sent": 0, "received": 0, "resent": 0, "dropped_old": 0,
                      "duplicates": 0, "reordered": 0, "lost_sim": 0}

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
                self.pending[(addr, rseq)] = [data, time.time(), 0]
            else:
                data = P.encode(packet)
        self.stats["sent"] += 1
        self._raw_send(data, addr)

    # ---------- receiving ----------
    def receive(self):
        """Blocking. Returns (messages, addr): the list of new messages ready
        to handle, in order. The list is empty for ACKs, duplicates, stale
        updates, or reliable messages held back until a gap is filled."""
        data, addr = self.sock.recvfrom(MAX_PACKET_SIZE)
        if self.loss and random.random() < self.loss:
            self.stats["lost_sim"] += 1
            return [], addr
        msg = P.decode(data)
        if msg is None:
            return [], addr
        seq = msg.pop("seq", None)
        rseq = msg.pop("rseq", None)

        if msg["type"] == P.ACK:
            with self.lock:
                self.pending.pop((addr, msg.get("ack")), None)
            return [], addr

        if rseq is not None:
            # Always ACK, even duplicates: our previous ACK may have been lost.
            self._raw_send(P.encode({"type": P.ACK, "ack": rseq}), addr)
            with self.lock:
                expected = self.expected.get(addr, 0)
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
            with self.lock:
                if seq <= self.last_unreliable.get(addr, -1):
                    self.stats["dropped_old"] += 1
                    return [], addr
                self.last_unreliable[addr] = seq
        self.stats["received"] += 1
        return [msg], addr

    # ---------- resend loop ----------
    def resend_due(self):
        """Call regularly. Resends unacknowledged reliable messages.
        Returns the addresses that stopped answering (gave up on them)."""
        now, failed = time.time(), set()
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
            for table in (self.next_seq, self.next_rseq, self.expected, self.held, self.last_unreliable):
                table.pop(addr, None)
            for key in [k for k in self.pending if k[0] == addr]:
                del self.pending[key]
