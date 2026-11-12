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
{"type": "INPUT", "match_id": 1, "left": 1, "right": 0, "jump_n": 0, "punch_n": 1, "smash_n": 0}
{"type": "STATE", "room_id": 1, "match_id": 1, "tick": 1520, "players": [...], "time_left": 42.5}
```

## Project structure

```
rooftop-rumble/
├── common/
│   ├── config.py      # ports, tick rate, timeouts
│   ├── protocol.py    # message types and JSON encoding
│   ├── game.py        # game rules (no networking)
│   └── lobby.py       # rooms and the game loop, shared by both servers
├── tcp/
│   ├── server.py      # TCP server
│   └── client.py      # TCP connection used by the clients
├── udp/
│   ├── server.py      # UDP server
│   ├── client.py      # UDP connection used by the clients
│   └── reliable.py    # our reliability layer: seq, acks, resends, ordering
├── client/
│   ├── console.py     # terminal client for testing (rooms, chat, scores)
│   └── ui.py          # the game window: connect, lobby, match, results
├── tools/
│   └── bots.py        # bot players to test the server without 3 people
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

### Run the game

**1. Start the server** on one computer (TCP or UDP):
```bash
python -m tcp.server
python -m udp.server
```
It prints its IP address, for example `192.168.1.23`.

**2. Start the game window** on each player's computer:
```bash
python -m client.ui
```
Type your name and the server IP, pick **TCP** or **UDP** (the same as the server), and click **Connect**. In the lobby, one player creates a room, the others join, and the owner clicks **Start match**.

You can also fill everything from the command line:
```bash
python -m client.ui --protocol udp --host 192.168.1.23 --name Kai --auto-connect
```

**Controls:** A/D or arrows to move, W/Up/Space to jump (twice in the air), S/Down to drop through a platform, J to punch, K for a charged smash, F3 to show or hide the network stats.

### Play against bots
Create a room in the game window, then in another terminal:
```bash
python -m tools.bots --protocol tcp --bots 2 --join-only
```
The bots join your room. Click **Start match**.

### Test the server with bots only
```bash
python -m tcp.server --duration 30
python -m tools.bots --protocol tcp
python -m tools.bots --protocol tcp --kill-one 10            # one bot quits mid-match
python -m tools.bots --protocol udp --loss 0.1 --delay 50    # with the UDP server running
```

### Terminal client
`python -m tcp.client --name Kai` (or `udp.client`) gives a text client with `/rooms`, `/create`, `/join <id>`, `/leave`, `/start`, `/stats`, `/quit`. Anything else is a chat message.

### Testing on one computer
Use `--host 127.0.0.1` (the default) and open several terminals. For the real demo, use four computers on the same network.

### Firewall
If clients can't connect, allow Python through the firewall on the server computer, or open ports `5000` (TCP) and `5001` (UDP).

## Comparing TCP and UDP

Both versions support simulated loss and the same added one-way delay:

```bash
python -m client.ui --protocol udp --host <SERVER_IP> --name Kai --loss 0.10 --delay 80 --log metrics.csv
```
Use `--protocol tcp` for the TCP version. In both clients, `--delay 80` adds 80 ms in each direction, so an otherwise local PING round trip takes about 160 ms. UDP delays datagrams and ACKs independently without reducing the snapshot rate. TCP preserves stream order and adds synthetic retransmission stalls. Loss is sampled per application message for TCP and per datagram (including ACKs) for UDP; these application-level models illustrate transport behavior and do not measure actual TCP retransmissions.

`--log` writes one row per second during matches: ping, snapshots received, snapshots missed, and the longest gap between two snapshots. Open the CSV in Excel to build the comparison charts.

Metrics currently recorded:
- **Ping**: PING/PONG round-trip time, including artificial delay.
- **Missed snapshots**: gaps in the expected snapshot tick sequence.
- **Update gaps**: the longest interval between snapshots processed by the UI.
- **F3 counters**: synthetic TCP stalls/retries or UDP resends/stale updates (shown in the overlay).

Input-to-display latency and numerical positional desynchronization are not currently measured. For network performance conclusions, use repeated controlled trials with a common external impairment setup.

Run the networking, multiplayer, and headless UI regression checks from the project directory:
```bash
python -m unittest discover -v
```

## Team

| Abdelkarim Haamdi |
| Brahim Amous | 
| Maether Guennichi |
| Malek Mimouna |

## Roadmap

- [x] Project structure and protocol
- [x] TCP and UDP connection, login and chat
- [x] Lobby: create, join, leave rooms, start (both versions)
- [x] Game rules (`common/game.py`)
- [x] Server game loop: inputs, 20 snapshots/s, events, game over, ranking
- [x] Disconnection handling in both versions
- [x] UDP reliability: acks, resends, in-order delivery of critical messages
- [x] Packet loss and delay simulation (UDP client)
- [x] Game window with pygame (`client/ui.py`)
- [x] Metrics logging to CSV (`--log`)
- [ ] Test on 4 computers
- [ ] Slides (5 to 7)
- [ ] Bonus: cross-network play
