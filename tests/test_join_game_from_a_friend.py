"""Rejoindre la partie d'un ami, avec la trame que la console a vraiment envoyée.

Le 22 août 2026, deux consoles ont essayé trois fois de se rejoindre par la
liste d'amis. Le journal disait à chaque fois la même chose :

    player_joined      {game: 1, persona: 2535464170083733, by: host_persona}
    connection_error   ValueError: Unsupported TDF type 201 for @PCN at 0x7B

Le joueur rejoignait vraiment -- puis on lui raccrochait au nez. `response_frame`
redécodait la requête entière, en mode strict, pour en tirer trois nombres qui
tiennent dans les douze premiers octets. Sur la console, ça s'affichait
« Cette session de jeu n'existe plus ».

La trame est gardée telle quelle parce qu'elle est la preuve : elle porte
`GID = 0` et un `USER` qui désigne l'hôte, et elle contient le champ `RRST`
dont la grammaire nous échappe toujours.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "tools"))

import fifa14_blaze_server as SERVER  # noqa: E402
from blaze_tdf import decode_frame  # noqa: E402

JOIN_GAME = bytes.fromhex(
    "00DA000400090000000000708B4C2C090000009E5BB400009E990000009F6972010E"
    "71612D6F6E6C792D646179343500AAD9740004C2E9740600DA1B3503B618E90085D4"
    "E3DC12E249320224C0A8640869635C8E0C020017FA14CD2B0ADF72760030008422BF"
    "D400000000FA01000000E35A640095D69693B8FF800900CB2CF40501030101000001"
    "00CEC96E0000CECA6400BF03CECBF40001CF4CB40000D2993800BFFF07D739720386"
    "9900000086CBE300009788A20200978A64009996B5FEDDFF8009A64000009996B5FE"
    "DDFF8009BA1B65010100BF2A670000C29929000000E339730200"
)

HOST = 2535469248587161
GUEST = 2535464170083733


class JoinFromFriendsListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.26", 12345), 10041)
        self.state.xuid = GUEST
        self.state.gamertag = "Racim Madaci"

        self.game = SERVER.HostedGame(
            game_id=1, persona_id=HOST, gamertag="Imrane",
            state=SERVER.GAME_STATE_PRE_GAME, roster=[9],
        )
        self.game.members = [self.protocol.member(
            self.game, HOST, "Imrane", 9, None, slot=0, team=0, state=None,
        )]
        self.protocol.games[1] = self.game

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def test_the_request_is_answered_and_not_raised_on(self) -> None:
        """Le seul comportement qui compte : ça ne lève pas.

        Le correctif n'est pas d'avoir rattrapé l'exception plus haut, c'est
        que ce travail n'avait pas lieu d'être. Répondre à une requête ne
        demande pas de la comprendre.
        """
        answered = self.protocol.handle(JOIN_GAME, self.state)
        self.assertTrue(answered)
        reply = decode_frame(answered[0])
        self.assertEqual((reply["component"], reply["command"]), (4, 9))

    def test_the_reply_header_matches_the_request(self) -> None:
        """L'en-tête de la réponse se déduit de celui de la requête, seul.

        Même numéro de message : sans lui le client ne saurait pas à laquelle
        de ses questions on répond.
        """
        answered = self.protocol.handle(JOIN_GAME, self.state)
        reply = decode_frame(answered[0])
        self.assertEqual(
            reply["message_number"],
            ((JOIN_GAME[9] & 0x0F) << 16) | int.from_bytes(JOIN_GAME[10:12], "big"),
        )
        self.assertEqual(reply["error"], 0)

    def test_the_guest_lands_in_the_game_its_host_holds(self) -> None:
        """`GID` vaut zéro, et pourtant la bonne partie est trouvée.

        C'est tout le point : le client désigne son hôte par son identifiant
        de joueur parce qu'il l'a pris dans sa liste d'amis. Chercher la
        partie numéro zéro revient à refuser une demande valide.
        """
        self.protocol.handle(JOIN_GAME, self.state)
        self.assertEqual(len(self.game.members), 2)
        arrival = self.game.members[1]
        self.assertIn(GUEST, arrival.values())

    def test_the_arrival_is_told_the_game_before_anything_else(self) -> None:
        """La réponse part avant les notifications qu'elle déclenche.

        L'inverse laissait l'invité muet : il recevait la mise en place d'une
        partie avant la réponse qui lui disait laquelle.
        """
        answered = self.protocol.handle(JOIN_GAME, self.state)
        commands = [decode_frame(f)["command"] for f in answered]
        self.assertEqual(commands[0], 9)
        self.assertEqual(commands[1:], [20, 71, 30, 100])


if __name__ == "__main__":
    unittest.main()


class JoinNotificationChoiceTests(unittest.TestCase):
    """Ce que reçoit un arrivant est réglable, et le défaut est celui qu'on a mesuré.

    La 22 s'appelle `NotifyJoiningPlayerInitiateConnections` et porte la même
    charge que la 20 -- le binaire n'a qu'une classe 557. Elle a été essayée
    seule le 22 août : la console qui l'a reçue n'a plus rien dit, quand celle
    qui recevait la 20 répondait deux secondes plus tard avec sa session XNet.
    Le défaut protège ce résultat ; le réglage existe pour essayer `both`, qui
    n'a jamais été tenté, sans avoir à réécrire quoi que ce soit.
    """

    def setUp(self) -> None:
        self.previous = os.environ.get("FIFA14_JOIN_NOTIFICATION")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("FIFA14_JOIN_NOTIFICATION", None)
        else:
            os.environ["FIFA14_JOIN_NOTIFICATION"] = self.previous

    def test_the_default_is_the_one_the_consoles_answered(self) -> None:
        os.environ.pop("FIFA14_JOIN_NOTIFICATION", None)
        self.assertEqual(SERVER.join_notification_choice(), "20")

    def test_an_unreadable_setting_falls_back_rather_than_guessing(self) -> None:
        """Une valeur invalide ne doit pas silencieusement changer le
        comportement observé sur le matériel."""
        os.environ["FIFA14_JOIN_NOTIFICATION"] = "yes please"
        self.assertEqual(SERVER.join_notification_choice(), "20")

    def test_the_three_shapes_are_reachable(self) -> None:
        for value, expected in (("22", "22"), ("both", "both"), ("20", "20")):
            os.environ["FIFA14_JOIN_NOTIFICATION"] = value
            self.assertEqual(SERVER.join_notification_choice(), expected)
