"""Le surveillant de patch doit pouvoir demander au serveur, où qu'il soit.

`tools/fut.sh` arrête son balayage XBDM dès que le titre entre dans Ultimate
Team -- continuer, c'est ralentir les menus et l'animation des pochettes chez
quelqu'un qui joue, et c'est ce qui a précédé deux des chutes de console
d'août. L'oracle était le journal local du serveur.

Il ne restait vrai que tant que le serveur tournait sur la même machine. Depuis
que la console parle au VPS, ce journal ne reçoit plus rien : le surveillant ne
pouvait plus s'arrêter, et balayait pendant toute la partie. `GET
/revival/inside-fut` remplace la lecture d'un fichier par une question, et la
réponse est la même que le serveur soit ici ou à Karlsruhe.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "tools"))

import fifa14_blaze_server as SERVER  # noqa: E402


class InsideFutOracleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.service = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
        self.service.start()
        self.base = f"http://127.0.0.1:{self.service.server.server_address[1]}"

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def ask(self, query: str = "") -> dict:
        with urllib.request.urlopen(f"{self.base}/revival/inside-fut{query}") as answer:
            return json.loads(answer.read())

    def fetch(self, path: str) -> int:
        try:
            with urllib.request.urlopen(f"{self.base}{path}") as answer:
                return answer.status
        except urllib.error.HTTPError as refused:
            return refused.code

    def test_a_console_that_never_asked_for_anything_is_not_in_fut(self) -> None:
        answer = self.ask()
        self.assertFalse(answer["inside"])
        self.assertIsNone(answer["age"])

    def test_a_fut_route_puts_the_caller_inside(self) -> None:
        self.fetch("/ut/game/fifa14/user/accountinfo")
        answer = self.ask()
        self.assertTrue(answer["inside"], answer)
        self.assertLess(answer["age"], 5)

    def test_the_xbox_spelling_counts_too(self) -> None:
        """`pow/auth` est le nom que la CardsDLL du 360 donne à `ut/auth`.

        C'est la première requête de la session FUT, donc celle qui compte le
        plus : si elle ne comptait pas, le surveillant balaierait encore
        pendant tout le chargement du club.
        """
        self.fetch("/pow/auth")
        self.assertTrue(self.ask()["inside"])

    def test_the_menu_alone_does_not_count(self) -> None:
        """Le titre au menu principal parle au serveur sans être dans FUT."""
        self.fetch("/health")
        self.fetch("/futBoot.xml")
        self.assertFalse(self.ask()["inside"])

    def test_a_visit_older_than_the_window_has_expired(self) -> None:
        self.fetch("/ut/game/fifa14/user/accountinfo")
        self.assertFalse(self.ask("?window=0")["inside"])

    def test_one_player_does_not_answer_for_another(self) -> None:
        """La route répond sur le pair qui demande, pas sur le serveur entier.

        Le VPS est partagé. Un surveillant qui s'arrêterait parce que
        *quelqu'un d'autre* est entré dans FUT laisserait ce patch-ci tomber.
        """
        self.fetch("/ut/game/fifa14/user/accountinfo")
        self.assertTrue(self.ask()["inside"])
        self.assertFalse(self.ask("?peer=203.0.113.7")["inside"])
