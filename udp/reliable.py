"""Our own reliability layer on top of UDP.

UDP can lose, duplicate and reorder datagrams. This class adds:
- a sequence number ("seq") on every packet we send to a given address,
- ACK + resend for messages in protocol.RELIABLE,
- duplicate filtering for reliable messages,
- "newest wins" for unreliable messages (old STATE packets are dropped),
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
        self.next_seq = {}        # addr -> next seq to send
        self.pending = {}         # (addr, seq) -> [data, last_sent, tries]
        self.seen = {}            # addr -> set of reliable seqs already delivered
        self.last_unreliable = {} # addr -> highest unreliable seq delivered
        self.stats = {"sent": 0, "received": 0, "resent": 0, "dropped_old": 0, "lost_sim": 0}

    # ---------- sending ----------
    def _raw_send(self, data, addr):
        if self.loss and random.random() < self.loss:
            self.stats["lost_sim"] += 1
            return
        if self.delay:
            threading.Timer(self.delay, self._safe_sendto, (data, addr)).start()
        else:
            self._safe_sendto(data, addr)

    def _safe_sendto(self, data, addr):
        try:
            self.sock.sendto(data, addr)
        except OSError:
            pass

    def send(self, msg, addr):
        with self.lock:
            seq = self.next_seq.get(addr, 0)
            self.next_seq[addr] = seq + 1
            data = P.encode({**msg, "seq": seq})
            if msg["type"] in P.RELIABLE:
                self.pending[(addr, seq)] = [data, time.time(), 0]
        self.stats["sent"] += 1
        self._raw_send(data, addr)

    # ---------- receiving ----------
    def receive(self):
        """Blocking. Returns (msg, addr) for a new message, or (None, addr) for
        packets handled internally (ACKs, duplicates, stale updates)."""
        data, addr = self.sock.recvfrom(MAX_PACKET_SIZE)
        if self.loss and random.random() < self.loss:
            self.stats["lost_sim"] += 1
            return None, addr
        msg = P.decode(data)
        if msg is None:
            return None, addr
        seq = msg.pop("seq", None)
        if msg["type"] == P.ACK:
            with self.lock:
                self.pending.pop((addr, msg.get("ack")), None)
            return None, addr
        if msg["type"] in P.RELIABLE:
            self._raw_send(P.encode({"type": P.ACK, "ack": seq}), addr)  # always ack, even duplicates
            seen = self.seen.setdefault(addr, set())
            if seq in seen:
                return None, addr
            seen.add(seq)
        elif seq is not None:
            if seq <= self.last_unreliable.get(addr, -1):
                self.stats["dropped_old"] += 1
                return None, addr
            self.last_unreliable[addr] = seq
        self.stats["received"] += 1
        return msg, addr

    # ---------- resend loop ----------
    def resend_due(self):
        """Call regularly. Resends unacknowledged reliable messages.
        Returns the addresses that stopped answering (gave up)."""
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
            self.next_seq.pop(addr, None)
            self.seen.pop(addr, None)
            self.last_unreliable.pop(addr, None)
            for key in [k for k in self.pending if k[0] == addr]:
                del self.pending[key]
