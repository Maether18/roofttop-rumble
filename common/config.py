"""Shared settings for both versions."""

SERVER_HOST = "0.0.0.0"   # servers listen on every network interface
TCP_PORT = 5000
UDP_PORT = 5001

MAX_PLAYERS_PER_ROOM = 3
TICK_RATE = 60            # game simulation steps per second (server)
SNAPSHOT_RATE = 20        # state updates sent to clients per second
MATCH_SECONDS = 90

# Timeouts are sized for internet play (up to ~300 ms RTT and some loss),
# not only for a LAN. Clients send a PING every HEARTBEAT_INTERVAL even when
# idle, which also keeps UDP NAT mappings open (they expire after ~30 s).
HEARTBEAT_INTERVAL = 1.0  # seconds between PING messages (must stay <= 1 s for NAT)
DISCONNECT_TIMEOUT = 5.0  # silence after which a player is considered gone

# UDP reliability
RESEND_INTERVAL = 0.2     # seconds before an unacknowledged message is first resent
RESEND_BACKOFF = 1.5      # each further resend waits this much longer...
MAX_RESEND_INTERVAL = 1.0 # ...up to this, so slow links aren't flooded with copies
MAX_RESENDS = 12          # ~10 s of resends before giving up on an address
MAX_PACKET_SIZE = 8192
