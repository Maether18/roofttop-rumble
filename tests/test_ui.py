import csv
import importlib.util
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import protocol as P
from common.game import Game


@unittest.skipUnless(importlib.util.find_spec("pygame"), "Pygame is not installed")
class UITests(unittest.TestCase):
    def test_screens_match_filter_input_and_metrics(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        import pygame
        from client.ui import App

        args = SimpleNamespace(protocol="tcp", name="", host="127.0.0.1", log="",
                               auto_connect=False, port=0, loss=0, delay=0)
        original_mode = pygame.display.set_mode
        with patch("pygame.display.set_mode", side_effect=lambda size, flags=0: original_mode(size)):
            app = App(args)
        self.addCleanup(pygame.quit)
        sent = []
        app.conn = SimpleNamespace(protocol_name="TCP", rtt_ms=160, stats={},
                                   send=sent.append, close=lambda: None)
        app.draw()  # connect
        app.on_message(P.make(P.LOGIN_OK, name="A", players=["A", "B", "C"]))
        app.on_message(P.make(P.ROOM_LIST, rooms=[dict(id=1, name="Test room", owner="A",
                                                       players=["A", "B", "C"], status="waiting", max=3)]))
        app.draw()  # lobby
        game = Game(["A", "B", "C"])
        for _ in range(3):
            game.step(1 / 60)
        start = P.make(P.GAME_START, room_id=1, match_id=1, duration=90,
                       players=[dict(name=n, slot=i) for i, n in enumerate(game.fighters)])
        app.on_message(start)
        app.on_message(P.make(P.STATE, room_id=1, match_id=1, **game.snapshot()))
        app.draw()  # match
        over = P.make(P.GAME_OVER, room_id=1, match_id=1, ranking=game.ranking(), winner=game.winner())
        app.on_message(over)
        app.draw()  # results
        app.on_message(dict(start, match_id=2))
        app.on_message(P.make(P.STATE, room_id=1, match_id=1, **game.snapshot()))
        app.on_message(P.make(P.EVENT, room_id=1, match_id=1, kind="hit"))
        app.on_message(over)
        self.assertEqual(app.mode, "game")
        self.assertEqual(app.net["received"], 0)
        app.on_message(P.make(P.STATE, room_id=1, match_id=2, **game.snapshot()))
        app.presses["jump"] = 9
        app.on_message(dict(start, match_id=2))
        self.assertEqual(app.presses["jump"], 9)
        self.assertEqual(app.net["received"], 1)
        app.send_input(time.time())
        self.assertEqual(sent[-1]["match_id"], 2)
        self.assertEqual(sent[-1]["jump_n"], 9)
        with tempfile.TemporaryDirectory(prefix="rooftop-ui-") as directory:
            app.metrics_file = str(Path(directory) / "metrics.csv")
            app.update_metrics(time.time() + 1.1)
            with open(app.metrics_file, newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["protocol"], "TCP")
            self.assertEqual(rows[0]["snapshots_received"], "1")
        app.running = False
        app.run()


if __name__ == "__main__":
    unittest.main()
