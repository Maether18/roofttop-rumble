"""Message types and encoding shared by the TCP and UDP versions.

Every message is a JSON object with a "type" field.
TCP frames each message as one line (ends with "\\n").
UDP sends one message per datagram.
"""
import json

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
        return msg if isinstance(msg, dict) and "type" in msg else None
    except (UnicodeDecodeError, json.JSONDecodeError):
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
