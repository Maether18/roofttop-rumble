"""Pygame client UI (to build).

Screens:
1. Login: type a username, connect.
2. Lobby: list of rooms, buttons to create, join and leave a room, start the game.
3. Game: draw the rooftop, platforms and fighters from the latest STATE.
   Send INPUT messages with the pressed keys about 30 times per second.
4. Results: ranking and winner from GAME_OVER, button back to the lobby.

The UI should not know whether it talks TCP or UDP: give it a small
network object with send(msg) and a queue of received messages, and
create it from tcp/client.py or udp/client.py.
"""
