"""Message types and encoding shared by the TCP and UDP versions.

Every message is a JSON object with a "type" field.
TCP frames each message as one line (ends with "\\n").
UDP sends one message per datagram.
"""
import json
import math

# Client -> server
LOGIN = "LOGIN"
CHAT = "CHAT"
CREATE_ROOM = "CREATE_ROOM"
JOIN_ROOM = "JOIN_ROOM"
LEAVE_ROOM = "LEAVE_ROOM"
START_GAME = "START_GAME"
INPUT = "INPUT"
PING = "PING"

# Server -> client
LOGIN_OK = "LOGIN_OK"
LOGIN_FAIL = "LOGIN_FAIL"
ROOM_LIST = "ROOM_LIST"
ROOM_UPDATE = "ROOM_UPDATE"
GAME_START = "GAME_START"
STATE = "STATE"
EVENT = "EVENT"
GAME_OVER = "GAME_OVER"
PLAYER_LEFT = "PLAYER_LEFT"
ERROR = "ERROR"
PONG = "PONG"

# UDP reliability
ACK = "ACK"

# Messages that must arrive. In UDP they are acknowledged and resent.
RELIABLE = {
    LOGIN, LOGIN_OK, LOGIN_FAIL, CHAT,
    CREATE_ROOM, JOIN_ROOM, LEAVE_ROOM, ROOM_LIST, ROOM_UPDATE,
    START_GAME, GAME_START, EVENT, GAME_OVER, PLAYER_LEFT, ERROR,
}


def make(msg_type, **fields):
    """Build a message dict: make(LOGIN, name="Kai")."""
    return {"type": msg_type, **fields}


def encode(msg):
    """Message dict -> bytes (without framing)."""
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


def decode(data):
    """Bytes -> message dict. Returns None for invalid data."""
    try:
        msg = json.loads(data.decode("utf-8"))
        return msg if (isinstance(msg, dict) and isinstance(msg.get("type"), str)
                       and msg["type"]) else None
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def nonnegative_int(value):
    """Sequence numbers are integers, never JSON booleans or floats."""
    return type(value) is int and value >= 0


def validate_impairment(loss, delay_ms):
    """Both transports interpret delay as added latency in each direction."""
    if not math.isfinite(loss) or not 0 <= loss < 1:
        raise ValueError("loss must be at least 0 and less than 1")
    if not math.isfinite(delay_ms) or delay_ms < 0:
        raise ValueError("delay must be a finite, nonnegative number of milliseconds")


def client_message_error(msg):
    """Validate commands after reliable delivery, so bad payloads leave no gaps."""
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return "Invalid message type"
    t = msg["type"]
    if t == LOGIN:
        if not isinstance(msg.get("name", ""), str):
            return "Name must be text"
    elif t == CHAT:
        if not isinstance(msg.get("text", ""), str):
            return "Chat must be text"
    elif t == CREATE_ROOM:
        if msg.get("room_name") is not None and not isinstance(msg["room_name"], str):
            return "Room name must be text"
    elif t == JOIN_ROOM:
        if not nonnegative_int(msg.get("room_id")) or msg["room_id"] == 0:
            return "Room id must be a positive integer"
    elif t == INPUT:
        if not nonnegative_int(msg.get("match_id")) or msg["match_id"] == 0:
            return "Input requires a match id"
        for key in ("left", "right", "down"):
            if type(msg.get(key, 0)) not in (int, bool) or msg.get(key, 0) not in (0, 1):
                return "Held keys must be 0 or 1"
        for key in ("jump_n", "punch_n", "smash_n"):
            if not nonnegative_int(msg.get(key, 0)):
                return "Press counters must be nonnegative integers"
    elif t == PING:
        stamp = msg.get("t")
        if stamp is not None:
            try:
                valid = type(stamp) in (int, float) and math.isfinite(stamp)
            except OverflowError:
                valid = False
            if not valid:
                return "Ping timestamp must be finite"
    elif t not in (LEAVE_ROOM, START_GAME):
        return "Unknown client message"
    return None


# ---------- TCP framing: one JSON message per line ----------

def encode_line(msg):
    return encode(msg) + b"\n"


class LineReader:
    """Splits the TCP byte stream into complete messages.

    TCP has no message boundaries: one recv() may return half a message
    or several messages at once. We buffer bytes until we see "\\n".
    """

    def __init__(self):
        self.buffer = b""

    def feed(self, data):
        self.buffer += data
        messages = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            msg = decode(line)
            if msg is not None:
                messages.append(msg)
        return messages
