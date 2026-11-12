"""Match lifecycle filtering shared by graphical, console and bot clients."""
from common import protocol as P


class MatchSession:
    def __init__(self):
        self.match_id = None
        self.room_id = None
        self.latest_match_id = 0
        self.active = False
        self.last_tick = -1

    def accept(self, msg):
        t = msg["type"]
        if t == P.GAME_START:
            match_id = msg.get("match_id")
            if not P.nonnegative_int(match_id) or match_id <= self.latest_match_id:
                return False
            self.match_id = self.latest_match_id = match_id
            self.room_id = msg.get("room_id")
            self.active = True
            self.last_tick = -1
        elif t in (P.STATE, P.EVENT, P.GAME_OVER) or (
                t == P.PLAYER_LEFT and "match_id" in msg):
            if (not self.active or msg.get("match_id") != self.match_id
                    or msg.get("room_id") != self.room_id):
                return False
            if t == P.STATE:
                tick = msg.get("tick")
                if not P.nonnegative_int(tick) or tick <= self.last_tick:
                    return False
                self.last_tick = tick
            elif t == P.GAME_OVER:
                self.active = False
        return True
