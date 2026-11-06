# Message protocol

Every message is a JSON object with a `type` field.

- **TCP version:** one message per line, ending with `\n`.
- **UDP version:** one message per datagram, plus a `seq` field added by `udp/reliable.py`. Messages marked **reliable** are acknowledged (`{"type":"ACK","ack":<seq>}`) and resent until acknowledged.

## Client → server

| Type | Fields | Reliable | Purpose |
|---|---|---|---|
| `LOGIN` | `name` | yes | Identify the player |
| `CHAT` | `text` | yes | Chat message (starter test) |
| `CREATE_ROOM` | `room_name` | yes | Create a room and join it as owner |
| `JOIN_ROOM` | `room_id` | yes | Enter an available room |
| `LEAVE_ROOM` | | yes | Exit the current room |
| `START_GAME` | | yes | Room owner starts the match |
| `INPUT` | `left`, `right`, `jump`, `punch`, `smash` | no | Pressed keys, about 30 per second |
| `PING` | `t` | no | Heartbeat and latency measurement |

## Server → client

| Type | Fields | Reliable | Purpose |
|---|---|---|---|
| `LOGIN_OK` | `name`, `players` | yes | Login accepted |
| `LOGIN_FAIL` | `reason` | yes | Name empty or taken |
| `ROOM_LIST` | `rooms` | yes | Rooms in the lobby with their players and status |
| `ROOM_UPDATE` | `room` | yes | A room changed (player joined or left) |
| `GAME_START` | `players`, `duration` | yes | The match begins |
| `STATE` | `tick`, `time_left`, `players` | no | Game snapshot, 20 per second |
| `EVENT` | `kind`, details | yes | Hit, KO, respawn |
| `GAME_OVER` | `ranking`, `winner` | yes | Final results |
| `PLAYER_LEFT` | `name` | yes | A player disconnected |
| `ERROR` | `reason` | yes | Invalid request |
| `PONG` | `t` | no | Heartbeat reply |

## Why some messages are not reliable

`STATE`, `INPUT` and `PING` are sent many times per second, and each one replaces the previous. Resending an old one is useless, so UDP drops lost or outdated ones (`seq` lower than the last received) instead of waiting. This is the main reason UDP feels smoother than TCP under packet loss.
