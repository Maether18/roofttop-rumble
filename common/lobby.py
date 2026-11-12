"""Lobby, rooms and game loop. Shared by the TCP and UDP servers.

This file knows nothing about TCP or UDP. The server gives it a
send(name, msg) function, and calls:
    lobby.add_player(name)       after a successful login
    lobby.handle(name, msg)      for every message from a logged-in player
    lobby.remove_player(name)    when a player disconnects
Because both servers use this same code, both versions have exactly the
same rooms, rules and results. Only the transport changes.
"""
import threading
import time
import queue

from common import protocol as P
from common.config import MAX_PLAYERS_PER_ROOM, TICK_RATE, SNAPSHOT_RATE, MATCH_SECONDS
from common.game import Game

WAITING, PLAYING = "waiting", "playing"


class Room:
    def __init__(self, room_id, name, owner):
        self.id = room_id
        self.name = name
        self.owner = owner
        self.players = [owner]
        self.status = WAITING
        self.game = None
        self.match_id = None

    def to_dict(self):
        return {"id": self.id, "name": self.name, "owner": self.owner,
                "players": list(self.players), "status": self.status,
                "max": MAX_PLAYERS_PER_ROOM}


class Lobby:
    def __init__(self, send, match_seconds=MATCH_SECONDS, log=print):
        self._send = send                 # send(name, msg), provided by the server
        self.match_seconds = match_seconds
        self.log = log
        self.lock = threading.RLock()
        self.players = {}                 # name -> room id, or None when in the lobby
        self.rooms = {}                   # room id -> Room
        self.next_room_id = 1
        self.next_match_id = 1
        self.outbox = queue.Queue()
        threading.Thread(target=self._send_loop, daemon=True).start()

    # ---------- sending ----------
    # Enqueue each batch while holding the state lock. One sender preserves
    # that order without doing socket I/O on a simulation/room-handler thread.
    def _flush(self, out):
        if out:
            self.outbox.put(tuple(out))

    def _send_loop(self):
        while True:
            batch = self.outbox.get()
            try:
                if batch is None:
                    return
                for name, msg in batch:
                    try:
                        self._send(name, msg)
                    except Exception as exc:
                        self.log(f"[send error] {name}: {exc}")
            finally:
                self.outbox.task_done()

    def close(self):
        """Finish already queued output and stop the sender."""
        self.outbox.put(None)

    def _room_list_to_all(self, out):
        msg = P.make(P.ROOM_LIST, rooms=[r.to_dict() for r in self.rooms.values()])
        out.extend((name, msg) for name in self.players)

    def _to_room(self, room, msg, out):
        if msg["type"] in (P.GAME_START, P.STATE, P.EVENT, P.GAME_OVER) or (
                msg["type"] == P.PLAYER_LEFT and room.status == PLAYING):
            msg = dict(msg, room_id=room.id, match_id=room.match_id)
        out.extend((name, msg) for name in room.players)

    def _error(self, name, reason, out):
        out.append((name, P.make(P.ERROR, reason=reason)))

    # ---------- players ----------
    def add_player(self, name):
        out = []
        with self.lock:
            self.players[name] = None
            out.append((name, P.make(P.ROOM_LIST, rooms=[r.to_dict() for r in self.rooms.values()])))
            self._flush(out)

    def remove_player(self, name):
        out = []
        with self.lock:
            if name not in self.players:
                return
            self._leave_room(name, out, reason="disconnected")
            del self.players[name]
            self._room_list_to_all(out)
            self._flush(out)

    # ---------- messages ----------
    def handle(self, name, msg):
        error = P.client_message_error(msg)
        if error:
            with self.lock:
                self._flush([(name, P.make(P.ERROR, reason=error))])
            return
        handler = {
            P.CREATE_ROOM: self._create_room,
            P.JOIN_ROOM: self._join_room,
            P.LEAVE_ROOM: self._leave_room_request,
            P.START_GAME: self._start_game,
            P.INPUT: self._input,
        }.get(msg["type"])
        if handler is None:
            return
        out = []
        with self.lock:
            if name in self.players:
                handler(name, msg, out)
            self._flush(out)

    def _create_room(self, name, msg, out):
        if self.players[name] is not None:
            return self._error(name, "Leave your current room first", out)
        room_name = str(msg.get("room_name") or f"{name}'s room")[:24]
        room = Room(self.next_room_id, room_name, name)
        self.next_room_id += 1
        self.rooms[room.id] = room
        self.players[name] = room.id
        self.log(f"[room] {name} created room {room.id} '{room_name}'")
        self._room_list_to_all(out)

    def _join_room(self, name, msg, out):
        room = self.rooms.get(msg.get("room_id"))
        if self.players[name] is not None:
            return self._error(name, "Leave your current room first", out)
        if room is None:
            return self._error(name, "This room doesn't exist", out)
        if room.status != WAITING:
            return self._error(name, "This match has already started", out)
        if len(room.players) >= MAX_PLAYERS_PER_ROOM:
            return self._error(name, "This room is full", out)
        room.players.append(name)
        self.players[name] = room.id
        self.log(f"[room] {name} joined room {room.id}")
        self._room_list_to_all(out)

    def _leave_room_request(self, name, msg, out):
        if self.players[name] is None:
            return self._error(name, "You are not in a room", out)
        self._leave_room(name, out, reason="left the room")
        self._room_list_to_all(out)

    def _leave_room(self, name, out, reason):
        room = self.rooms.get(self.players.get(name))
        if room is None:
            return
        room.players.remove(name)
        self.players[name] = None
        self.log(f"[room] {name} {reason} (room {room.id})")
        if room.status == PLAYING and room.game:
            room.game.remove_player(name)          # the match continues without them
        if room.players:
            self._to_room(room, P.make(P.PLAYER_LEFT, name=name, reason=reason), out)
            if room.owner == name:
                room.owner = room.players[0]
        else:
            room.status = WAITING                  # stops the game loop
            room.game = None
            del self.rooms[room.id]
            self.log(f"[room] room {room.id} closed (empty)")

    def _start_game(self, name, msg, out):
        room = self.rooms.get(self.players[name])
        if room is None:
            return self._error(name, "You are not in a room", out)
        if room.owner != name:
            return self._error(name, "Only the room owner can start the match", out)
        if room.status != WAITING:
            return self._error(name, "The match has already started", out)
        if len(room.players) < 2:
            return self._error(name, "You need at least 2 players to start", out)
        room.game = Game(room.players, duration=self.match_seconds)
        room.match_id = self.next_match_id
        self.next_match_id += 1
        room.status = PLAYING
        players = [{"name": f.name, "slot": f.slot} for f in room.game.fighters.values()]
        self._to_room(room, P.make(P.GAME_START, room_id=room.id, players=players,
                                   duration=self.match_seconds), out)
        self._room_list_to_all(out)
        self.log(f"[game] room {room.id} started: {', '.join(room.players)}")
        threading.Thread(target=self._game_loop, args=(room, room.game), daemon=True).start()

    def _input(self, name, msg, out):
        room = self.rooms.get(self.players[name])
        if (room and room.status == PLAYING and room.game
                and msg.get("match_id") == room.match_id):
            room.game.apply_input(name, msg)

    # ---------- game loop (one thread per playing room) ----------
    def _game_loop(self, room, game):
        dt = 1.0 / TICK_RATE
        every = max(1, TICK_RATE // SNAPSHOT_RATE)
        next_tick = time.perf_counter()
        while True:
            out = []
            with self.lock:
                if room.game is not game or room.status != PLAYING:
                    return                      # room closed or emptied
                game.step(dt)
                for event in game.pop_events():
                    self._to_room(room, P.make(P.EVENT, **event), out)
                if game.tick % every == 0:
                    self._to_room(room, P.make(P.STATE, **game.snapshot()), out)
                over = game.is_over()
                if over:
                    self._to_room(room, P.make(P.GAME_OVER, ranking=game.ranking(),
                                               winner=game.winner()), out)
                    room.status, room.game = WAITING, None
                    self._room_list_to_all(out)
                    self.log(f"[game] room {room.id} finished, winner: {game.winner() or 'draw'}")
                self._flush(out)
            if over:
                return
            next_tick += dt
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:                 # fell far behind: don't try to catch up
                next_tick = time.perf_counter()
