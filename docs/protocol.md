# Message protocol

Every message is a JSON object with a `type` field.

- **TCP version:** one message per line, ending with `\n`.
- **UDP version:** one message per datagram. `udp/reliable.py` adds a `seq` field to every packet, and a separate `rseq` field to **reliable** messages. Reliable messages are acknowledged (`{"type":"ACK","ack":<rseq>}`), resent every 0.2 s until acknowledged, and delivered exactly once and in order. Unreliable messages older than the newest one received are dropped.

## Client → server

| Type | Fields | Reliable | Purpose |
|---|---|---|---|
| `LOGIN` | `name` | yes | Identify the player |
| `CHAT` | `text` | yes | Chat message (starter test) |
| `CREATE_ROOM` | `room_name` (optional) | yes | Create a room and join it as owner |
| `JOIN_ROOM` | `room_id` | yes | Enter an available room |
| `LEAVE_ROOM` | | yes | Exit the current room |
| `START_GAME` | | yes | Room owner starts the match |
| `INPUT` | `left`, `right`, `down`, `jump_n`, `punch_n`, `smash_n` | no | Keys, about 30 per second (see below) |
| `PING` | `t` | no | Heartbeat and latency measurement |

## Server → client

| Type | Fields | Reliable | Purpose |
|---|---|---|---|
| `LOGIN_OK` | `name`, `players` | yes | Login accepted |
| `LOGIN_FAIL` | `reason` | yes | Name empty or taken |
| `ROOM_LIST` | `rooms` | yes | All rooms with players, owner and status. Sent to everyone after any change |
| `GAME_START` | `room_id`, `players` (`name`, `slot`), `duration` | yes | The match begins |
| `STATE` | `tick`, `time_left`, `players` | no | Game snapshot, 20 per second |
| `EVENT` | `kind`, details | yes | `hit`, `ko`, `respawn`, `left` |
| `GAME_OVER` | `ranking`, `winner` | yes | Final results |
| `PLAYER_LEFT` | `name`, `reason` | yes | A player in your room left or disconnected |
| `ERROR` | `reason` | yes | Invalid request |
| `PONG` | `t` | no | Heartbeat reply |

## Why some messages are not reliable

`STATE`, `INPUT` and `PING` are sent many times per second, and each one replaces the previous. Resending an old one is useless, so UDP drops lost or outdated ones (`seq` lower than the last received) instead of waiting. This is the main reason UDP feels smoother than TCP under packet loss.

## INPUT: held keys and press counters

`left`, `right` and `down` are held keys (0 or 1). `jump_n`, `punch_n` and `smash_n` are **counters**: the client adds 1 every time the key is pressed, and the server acts when the number goes up. If some INPUT packets are lost or duplicated over UDP, the next one still carries the correct count, so a press is never missed or done twice.

## STATE player fields

`name`, `slot` (0 to 2, used for the colour), `x`, `y`, `vx`, `vy`, `facing` (1 or -1), `ground`, `damage`, `kos`, `falls`, `alive`, `stun`, `shield`, `attack` (`null` or `{"kind": "punch"|"smash", "t": seconds}`).

## EVENT kinds

| `kind` | Fields |
|---|---|
| `hit` | `attacker`, `victim`, `attack`, `dmg`, `damage` (victim's new total), `x`, `y` |
| `ko` | `victim`, `by` (or `null` for a self-fall), `x`, `y` |
| `respawn` | `name` |
| `left` | `name` (disconnected or left during the match) |
