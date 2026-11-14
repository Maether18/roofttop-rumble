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
- Disconnections are detected with **heartbeats**: a player who sends nothing for 5 seconds is considered disconnected.

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
│   ├── net.py         # address helpers: resolve hostnames, LAN and public IP
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
├── deploy/
│   ├── setup.sh       # one-shot setup of both servers on an Ubuntu cloud VM
│   ├── update.sh      # pull new code and restart the servers
│   └── rooftop-*.service  # systemd units for the TCP and UDP servers
├── requirements.txt         # game window (pygame-ce)
├── requirements-server.txt  # servers: standard library only
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
It prints its LAN IP address (for example `192.168.1.23`) and its public IP. Use `--port` to change the port and `--host` to listen on one interface only (default `0.0.0.0` = all).

**2. Start the game window** on each player's computer:
```bash
python -m client.ui
```
Type your name and the server's IP or hostname, check the port (it follows the protocol: 5000 for TCP, 5001 for UDP), pick **TCP** or **UDP** (the same as the server), and click **Connect**. In the lobby, one player creates a room, the others join, and the owner clicks **Start match**.

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

## Cross-network play

Players can be on different networks (another Wi-Fi, home internet, a 4G hotspot) and still play a full match on one server, in **both** the TCP and UDP versions. LAN play works exactly as before.

What makes it work:
- Both servers listen on `0.0.0.0` (every interface) and print their LAN and public IP at startup.
- All clients accept an IP **or a hostname** for `--host`, and a `--port` (the game window has a Port field).
- Timeouts are sized for the internet (`common/config.py`): 5 s heartbeat timeout, and UDP resends that start at 0.2 s and back off up to 1 s, for about 10 s in total before giving up. This tolerates around 300 ms RTT and some packet loss.
- UDP and NAT: every client sends at least one message per second (a `PING`, plus `INPUT` 30 times a second during a match), so the router's NAT mapping stays open. The server always replies to the address and port the packet came from.

The hard part is letting other players **reach** the server, because home routers block incoming connections. There are three ways to do it. Pick one.

### Option A: VPN with Tailscale or ZeroTier (recommended)
This is the easiest option. It works for TCP and UDP, needs no router access, and works on 4G and university Wi-Fi.

**Tailscale**
1. Everyone (the server and every player) installs Tailscale from https://tailscale.com/download and signs in. Either everyone uses the same account, or the server owner invites the others to their tailnet (admin console → Users → Invite).
2. On the server computer, get its VPN IP:
   ```bash
   tailscale ip -4
   ```
   It looks like `100.101.102.103`.
3. Start the server as usual:
   ```bash
   python -m tcp.server        # or: python -m udp.server
   ```
4. Each player connects to the VPN IP:
   ```bash
   python -m client.ui --protocol tcp --host 100.101.102.103 --name Kai
   python -m client.ui --protocol udp --host 100.101.102.103 --name Kai
   ```
   The bots and terminal clients work the same way:
   ```bash
   python -m tools.bots --protocol udp --host 100.101.102.103 --bots 2 --join-only
   python -m udp.client --host 100.101.102.103 --name Kai
   ```
   You can also use the server's Tailscale machine name instead of the IP (MagicDNS), for example `--host lab-pc`.

**ZeroTier** (same idea)
1. Create a network at https://my.zerotier.com and copy its 16-character network ID.
2. Everyone installs ZeroTier from https://www.zerotier.com/download and joins: open the tray icon → *Join New Network*, or run `zerotier-cli join <NETWORK_ID>` in an admin terminal.
3. In the web console, tick **Auth** for each member. Each computer gets a managed IP, for example `10.147.17.5`.
4. Run the server as usual and have the clients use the server's managed IP: `python -m client.ui --protocol udp --host 10.147.17.5 --name Kai`.

If clients still can't connect, add the Windows firewall rules from Option B (step 3) on the server computer.

### Option B: Port forwarding on the server's router
Use this when you control the router at the server's location.
1. Find the server's LAN IP. The server prints it at startup (`LAN clients: --host 192.168.1.23`), or run `ipconfig`. In the router, give this computer a fixed IP (*DHCP reservation*) so it doesn't change.
2. Open the router admin page (usually http://192.168.1.1 or http://192.168.0.1) → *Port forwarding* / *Virtual server*, and add:

   | Protocol | External port | Internal IP | Internal port |
   |---|---|---|---|
   | TCP | 5000 | 192.168.1.23 | 5000 |
   | UDP | 5001 | 192.168.1.23 | 5001 |
3. Allow the ports in the Windows firewall on the server. Run this in an **administrator** terminal:
   ```bash
   netsh advfirewall firewall add rule name="Rooftop Rumble TCP" dir=in action=allow protocol=TCP localport=5000
   netsh advfirewall firewall add rule name="Rooftop Rumble UDP" dir=in action=allow protocol=UDP localport=5001
   ```
4. Start the server. It prints the public IP (`Internet clients: --host 203.0.113.7`):
   ```bash
   python -m tcp.server
   python -m udp.server
   ```
5. Remote players connect to the public IP:
   ```bash
   python -m client.ui --protocol tcp --host 203.0.113.7 --port 5000 --name Kai
   python -m client.ui --protocol udp --host 203.0.113.7 --port 5001 --name Kai
   ```
   Players on the **same LAN** as the server should keep using the LAN IP, because many routers can't loop the public IP back inside.

Limits: this does **not** work if the server's internet uses CGNAT, which is common on 4G and some ISPs. You can tell because the WAN IP shown on the router's status page differs from the public IP the server prints. In that case use Option A or C. The server can't be on a phone hotspot for this option.

### Option C: A tunnel service with TCP and UDP (for example playit.gg)
Use this when you can't install a VPN on every player's computer and can't change the router. Only the server computer needs software.
1. On the server computer, install the playit agent from https://playit.gg/download, run it, and link it to your playit.gg account.
2. In the playit.gg dashboard, add two tunnels pointing at this computer:
   - type **TCP**, local address `127.0.0.1`, local port `5000`
   - type **UDP**, local address `127.0.0.1`, local port `5001`
3. playit gives each tunnel a public address and port, for example `blue-cat.gl.joinmc.link:41234` (TCP) and `blue-cat.gl.joinmc.link:52012` (UDP). The ports are usually not 5000/5001.
4. Start the server as usual (`python -m tcp.server` or `python -m udp.server`).
5. Players use the tunnel's hostname **and** port:
   ```bash
   python -m client.ui --protocol tcp --host blue-cat.gl.joinmc.link --port 41234 --name Kai
   python -m client.ui --protocol udp --host blue-cat.gl.joinmc.link --port 52012 --name Kai
   python -m tools.bots --protocol udp --host blue-cat.gl.joinmc.link --port 52012 --bots 2 --join-only
   ```

Note: **ngrok's free tier only tunnels TCP**, so it works for the TCP version but **not** for the UDP version. Through a tunnel, every player's packets come from the tunnel agent, but each player gets a different source port, so the UDP server still tells them apart.

### Troubleshooting
- "Can't find a server called ...": the hostname is wrong, or there's no internet or DNS.
- "Can't reach the server": the server isn't running, or the firewall, port forward or tunnel is wrong. Check that the protocol and port match the server.
- Press **F3** in a match to see the ping. Above about 300 ms the game still works, but hits feel late.
- UDP: if a player's router changes their public port in the middle of a session (rare, because the client keeps the mapping open), the server sees a new address. That player has to reconnect with a different name, or wait 5 s for the old session to time out.

## Deploy to the cloud

Run both servers on a small Linux VM with a public IP, so players on any network connect straight to it. No VPN, router access or tunnel is needed. The servers use only the Python standard library (no pygame), so they run on a headless machine. TCP uses port 5000 and UDP uses port 5001, so both servers run side by side.

> Until `feature` is merged into `main`, use `feature` as `<branch>` below. `main` doesn't have the `deploy/` folder yet.

**1. Create the VM.** Ubuntu 22.04 or 24.04, the smallest size is enough: Azure `B1s`, AWS `t2.micro`/`t3.micro`, or Oracle `VM.Standard.E2.1.Micro` / Ampere A1. Pick a region close to the players for a lower ping. Allow SSH (port 22) and note the **public IP**.

**2. Open inbound TCP 5000 and UDP 5001 in the cloud firewall.** Without this step, nothing reaches the VM.
- **Azure**: VM → *Networking* → *Network settings* → *Create port rule* → *Inbound port rule*. Add one rule with destination port `5000`, protocol **TCP**, action Allow, and a second rule with port `5001`, protocol **UDP**. With the Azure CLI, this opens both ports for TCP and UDP:
  ```bash
  az vm open-port --resource-group <RESOURCE_GROUP> --name <VM_NAME> --port 5000-5001 --priority 1010
  ```
- **AWS**: EC2 → *Instances* → your instance → *Security* tab → click the security group → *Edit inbound rules*. Add **Custom TCP**, port `5000`, source `0.0.0.0/0`, and **Custom UDP**, port `5001`, source `0.0.0.0/0`.
- **Oracle Cloud**: *Networking* → *Virtual cloud networks* → your VCN → your subnet → its *Security List* → *Add Ingress Rules*. Add source CIDR `0.0.0.0/0`, IP protocol **TCP**, destination port `5000`, and a second rule with **UDP** and port `5001`.
  Oracle's Ubuntu images **also block ports in iptables** inside the VM. `setup.sh` detects this and opens the ports for you. To do it by hand:
  ```bash
  sudo iptables -I INPUT -p tcp --dport 5000 -j ACCEPT
  sudo iptables -I INPUT -p udp --dport 5001 -j ACCEPT
  sudo netfilter-persistent save
  ```

**3. SSH in and run the setup script.** It installs git and python3, puts the code in `/opt/rooftop-rumble`, installs and starts the two systemd services (they restart on failure and start on boot), and opens the ports in `ufw` if ufw is active. At the end it prints the public IP and the exact client commands.
```bash
ssh <user>@<VM_PUBLIC_IP>          # azureuser on Azure, ubuntu on AWS and Oracle
curl -fsSL https://raw.githubusercontent.com/Maether18/roofttop-rumble/<branch>/deploy/setup.sh | bash -s <branch>
```
Or clone first and run it:
```bash
git clone -b <branch> https://github.com/Maether18/roofttop-rumble.git
bash roofttop-rumble/deploy/setup.sh <branch>
```
Running it again is safe: it updates the code and restarts the servers.

**4. Check that it's running.**
```bash
systemctl status rooftop-tcp rooftop-udp
journalctl -u rooftop-udp -f        # live log: logins, disconnects (Ctrl+C to quit)
journalctl -u rooftop-tcp -f
```

**5. Connect the clients** from any network:
```bash
python -m client.ui --protocol udp --host <VM_PUBLIC_IP>
python -m client.ui --protocol tcp --host <VM_PUBLIC_IP>
python -m tools.bots --protocol udp --host <VM_PUBLIC_IP> --bots 2 --join-only   # fill a room with bots
```

**Update after pushing new code**: `bash /opt/rooftop-rumble/deploy/update.sh`. It runs `git pull` and restarts both servers, so players in a match are disconnected. To switch branches, run `setup.sh <branch>` again.

**After the demo**, stop the VM in the cloud console to save credits, or turn the servers off with `sudo systemctl disable --now rooftop-tcp rooftop-udp`. The servers have no passwords, so anyone who knows the IP can join.

### Troubleshooting the cloud server
If a client can't connect:
1. **Cloud firewall**: check that the inbound rules for TCP 5000 and **UDP** 5001 exist (step 2). UDP is the one people forget.
2. **Service status**: run `systemctl status rooftop-udp` (or `rooftop-tcp`). If it isn't `active (running)`, look at `journalctl -u rooftop-udp -n 50`.
3. **VM firewall**: run `sudo ufw status` (5000/tcp and 5001/udp should be ALLOW if ufw is active). On Oracle, run `sudo iptables -L INPUT -n --line-numbers`: the ACCEPT rules must come before the REJECT rule.
4. **Is the server listening?** `sudo ss -lntup | grep -E ':500[01]'` should show python3 on 5000 (tcp) and 5001 (udp).
5. **Protocol and port**: the client's protocol must match the port (TCP to 5000, UDP to 5001).
6. **Backup plan**: if the cloud network still blocks you, install Tailscale on the VM (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and on the players' computers, then connect to the VM's `tailscale ip -4` address. See [Option A](#option-a-vpn-with-tailscale-or-zerotier-recommended).

## Comparing TCP and UDP

Both versions can simulate bad network conditions so we can measure the difference fairly:

```bash
python -m client.ui --protocol udp --host <SERVER_IP> --name Kai --loss 0.10 --delay 80 --log metrics.csv
```
`--log` writes one row per second during matches: ping, snapshots received, snapshots missed, and the longest gap between two snapshots. Open the CSV in Excel to build the comparison charts.

Metrics we measure:
- **Perceived lag**: round-trip time between sending an input and seeing its result.
- **Dropped updates**: game-state packets that never arrived.
- **Desync**: moments when a client shows a different position or result than the server.
- **Stalls**: time the game freezes while TCP waits to resend a lost packet.

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
- [x] Bonus: cross-network play (VPN, port forwarding or tunnel, see [Cross-network play](#cross-network-play))
