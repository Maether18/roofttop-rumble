# Message protocol

Every message is a JSON object with a `type` field.

- **TCP version:** one message per line, ending with `\n`.
- **UDP version:** one message per datagram. `udp/reliable.py` adds a `seq` field to ordinary packets, and a separate `rseq` field to **reliable** messages. ACK packets contain `{"type":"ACK","ack":<rseq>}`. Sequence fields must be nonnegative integers, excluding booleans. Reliable messages are acknowledged, retried every 0.2 s up to ten times, and delivered once in order within a live session. Unreliable messages older than the newest one received for that message type are dropped. Malformed metadata and packets beyond the 128-message reliable receive window are discarded before ACK or ordering-state changes. Invalid command payloads receive an error after reliable delivery so they cannot leave a gap in that stream.

## Client → server

| Type | Fields | Reliable | Purpose |
|---|---|---|---|
| `LOGIN` | `name` | yes | Identify the player |
| `CHAT` | `text` | yes | Chat message (starter test) |
| `CREATE_ROOM` | `room_name` (optional) | yes | Create a room and join it as owner |
| `JOIN_ROOM` | `room_id` | yes | Enter an available room |
| `LEAVE_ROOM` | | yes | Exit the current room |
| `START_GAME` | | yes | Room owner starts the match |
| `INPUT` | `match_id`, `left`, `right`, `down`, `jump_n`, `punch_n`, `smash_n` | no | Keys for the current match, about 30 per second (see below) |
| `PING` | `t` | no | Heartbeat and latency measurement |

## Server → client

| Type | Fields | Reliable | Purpose |
|---|---|---|---|
| `LOGIN_OK` | `name`, `players` | yes | Login accepted |
| `LOGIN_FAIL` | `reason` | yes | Name empty or taken |
| `ROOM_LIST` | `rooms` | yes | All rooms with players, owner and status. Sent to everyone after any change |
| `GAME_START` | `room_id`, `match_id`, `players` (`name`, `slot`), `duration` | yes | The match begins |
| `STATE` | `room_id`, `match_id`, `tick`, `time_left`, `players` | no | Game snapshot, 20 per second |
| `EVENT` | `room_id`, `match_id`, `kind`, details | yes | `hit`, `ko`, `respawn`, `left` |
| `GAME_OVER` | `room_id`, `match_id`, `ranking`, `winner` | yes | Final results |
| `PLAYER_LEFT` | `name`, `reason`, plus `room_id` and `match_id` during play | yes | A player in your room left or disconnected |
| `ERROR` | `reason` | yes | Invalid request |
| `PONG` | `t` | no | Heartbeat reply |

## Why some messages are not reliable

`STATE`, `INPUT` and `PING` are sent many times per second, and each one replaces the previous. Resending an old one is useless, so UDP drops lost or outdated ones (`seq` lower than the last received) instead of waiting. This is the main reason UDP feels smoother than TCP under packet loss.

## INPUT: held keys and press counters

The server allocates an increasing `match_id` for each match. Lobby output is queued in state-change order, so `GAME_START` is handed to the transport before that match's events and `GAME_OVER` before a rematch's start. Clients accept match traffic only after its `GAME_START`, reject duplicate starts and stale snapshots, and ignore traffic after its `GAME_OVER`. An early unreliable UDP snapshot can be discarded while a reliable start is being recovered; the next snapshot refreshes the view. `INPUT` must include the ID of the match actually processed by the client; the server ignores input for other matches. All bundled graphical, console, and bot clients use this lifecycle filtering. Use matching client/server versions when upgrading.

`left`, `right` and `down` are held keys (0 or 1). `jump_n`, `punch_n` and `smash_n` are **counters**: the client adds 1 every time the key is pressed, and the server acts when the number goes up. If some INPUT packets are lost or duplicated over UDP, the next one still carries the correct count, so a press is never missed or done twice.

Both client transports apply `--delay N` as added one-way latency in **each direction**; a client-only `--delay 80` adds about 160 ms to a PING round trip. UDP pipelines delayed datagrams and ACKs instead of sleeping per packet. TCP pipelines propagation delay while retaining ordered delivery and synthetic retransmission stalls. Simulated loss is sampled per application message on TCP and per datagram on UDP, including UDP ACKs. This is an illustrative application-level simulator, not a measurement of operating-system TCP retransmissions or a substitute for controlled network impairment.

## STATE player fields

`name`, `slot` (0 to 2, used for the colour), `x`, `y`, `vx`, `vy`, `facing` (1 or -1), `ground`, `damage`, `kos`, `falls`, `alive`, `stun`, `shield`, `attack` (`null` or `{"kind": "punch"|"smash", "t": seconds}`).

## EVENT kinds

| `kind` | Fields |
|---|---|
| `hit` | `attacker`, `victim`, `attack`, `dmg`, `damage` (victim's new total), `x`, `y` |
| `ko` | `victim`, `by` (or `null` for a self-fall), `x`, `y` |
| `respawn` | `name` |
| `left` | `name` (disconnected or left during the match) |
