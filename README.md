# Rooftop Rumble

A competitive, real-time multiplayer platform fighting game for 3 players, built with raw sockets in two versions: **TCP only** and **UDP only**.

> CS411 Computer Networks · Project 2 · Multiplayer Game Arena
> Demo day: **Friday, November 13, 2026**

---

## The game

Three fighters battle on a city rooftop at sunset.

- Move, jump (double jump), punch, and use a charged smash attack.
- Every hit raises the victim's **damage %**. The higher it is, the further they fly when hit.
- Knock a rival off the roof to score a **KO**. Knocked-out players respawn after a short delay.
- The player with the **most KOs after 90 seconds** wins.

The game is purely competitive: every player fights the other two.

## Requirements covered

Both versions implement every mandatory feature, with identical rules. Only the transport protocol changes.

| Feature | How it works |
|---|---|
| Registration and login | A player connects with a unique username. The server rejects duplicates. |
| Lobby and rooms | After login, players see the lobby and can create a room. |
| Joining or leaving a room | Players can enter or exit any available room. |
| Game start and end | The room owner starts the match. The server ends it when the timer reaches zero and tracks the session status. |
| Real-time game actions | Clients send their inputs. The server runs the game and broadcasts the state 20 times per second. |
| Scores and final results | Damage %, KOs, final ranking and the winner are sent to every player. |
| Player disconnection | A disconnected player's fighter is removed and the match continues without crashing. |

## Architecture

```
 Client 1 ─┐
 Client 2 ─┼──►  SERVER (authoritative)
 Client 3 ─┘
```

- **One server, three clients**, each on its own computer.
- The **server is authoritative**: it runs the game rules and decides every hit, KO and the winner. Clients only send inputs and draw what the server sends back.
- The **game rules live in `common/`** and are shared by both versions, so they are guaranteed to be the same game.

### TCP version (`tcp/`)
- One TCP connection per client, one thread per client on the server.
- TCP is a byte stream, so messages are **framed as one JSON object per line** (`\n`-delimited).
- Disconnections are detected when `recv()` returns empty or raises an error, plus a heartbeat timeout.

### UDP version (`udp/`)
- One UDP socket on the server. Clients are identified by their `(IP, port)` address.
- No TCP is used anywhere, including login and the lobby.
- Our own reliability layer:
  - **Sequence numbers** on every packet, so old or duplicate packets are ignored.
  - **Acknowledgments and resends** for critical messages (login, rooms, game start, hits, KOs, game over).
  - Frequent game-state updates are sent **without acks**, because the next update replaces a lost one.
- Disconnections are detected with **heartbeats**: a player who sends nothing for 3 seconds is considered disconnected.

## Message protocol

All messages are JSON objects with a `type` field. Full list in [`docs/protocol.md`](docs/protocol.md).

```json
{"type": "LOGIN", "name": "Kai"}
{"type": "INPUT", "left": 1, "right": 0, "jump": 0, "punch": 1, "smash": 0}
{"type": "STATE", "tick": 1520, "players": [...], "time_left": 42.5}
```

## Project structure

```
rooftop-rumble/
├── common/
│   ├── config.py      # ports, tick rate, timeouts
│   ├── protocol.py    # message types and JSON encoding
│   └── game.py        # game rules (no networking)
├── tcp/
│   ├── server.py      # TCP server
│   └── client.py      # TCP client
├── udp/
│   ├── server.py      # UDP server with reliability and heartbeats
│   └── client.py      # UDP client
├── client/
│   └── ui.py          # pygame screens: login, lobby, game, results
├── docs/
│   └── protocol.md    # message reference
├── requirements.txt
└── README.md
```

## Getting started

### Requirements
- Python 3.10 or newer
- `pygame-ce` for the game window

```bash
pip install -r requirements.txt
```

### Run the TCP version

On the **server computer**:
```bash
python -m tcp.server
```
The server prints its IP address. On **each client computer**:
```bash
python -m tcp.client --host <SERVER_IP> --name Kai
```

### Run the UDP version

On the **server computer**:
```bash
python -m udp.server
```
On **each client computer**:
```bash
python -m udp.client --host <SERVER_IP> --name Kai
```

### Testing on one computer
Use `--host 127.0.0.1` and open several terminals. For the real demo, use four computers on the same network.

### Firewall
If clients can't connect, allow Python through the firewall on the server computer, or open ports `5000` (TCP) and `5001` (UDP).

## Comparing TCP and UDP

Both versions can simulate bad network conditions so we can measure the difference fairly:

```bash
python -m udp.client --host <SERVER_IP> --name Kai --loss 0.10 --delay 80
```

Metrics we measure:
- **Perceived lag**: round-trip time between sending an input and seeing its result.
- **Dropped updates**: game-state packets that never arrived.
- **Desync**: moments when a client shows a different position or result than the server.
- **Stalls**: time the game freezes while TCP waits to resend a lost packet.

## Team

| Member | Responsibility |
|---|---|
| [Name 1] | Game rules (`common/game.py`) |
| [Name 2] | TCP server and client |
| [Name 3] | UDP server, client and reliability layer |
| [Name 4] | Client UI and presentation |

## Roadmap

- [x] Project structure and protocol
- [x] TCP and UDP connection, login and chat starter
- [ ] Lobby: create, join and leave rooms
- [ ] Game rules and real-time gameplay
- [ ] Scores, game over and results screen
- [ ] Disconnection handling in both versions
- [ ] UDP reliability for critical messages
- [ ] Packet loss and delay simulation, metrics logging
- [ ] Test on 4 computers
- [ ] Slides (5 to 7)
- [ ] Bonus: cross-network play
