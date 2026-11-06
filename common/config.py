"""Shared settings for both versions."""

TCP_PORT = 5000
UDP_PORT = 5001

MAX_PLAYERS_PER_ROOM = 3
TICK_RATE = 60            # game simulation steps per second (server)
SNAPSHOT_RATE = 20        # state updates sent to clients per second
MATCH_SECONDS = 90

HEARTBEAT_INTERVAL = 1.0  # seconds between PING messages
DISCONNECT_TIMEOUT = 3.0  # silence after which a player is considered gone

# UDP reliability
RESEND_INTERVAL = 0.2     # seconds before an unacknowledged message is resent
MAX_RESENDS = 10
MAX_PACKET_SIZE = 8192
