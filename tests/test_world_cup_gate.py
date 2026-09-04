"""World Cup Ultimate Team : la porte, et les deux routes qui manquaient.

Le mode est **dans ce build**. Le binaire chargé sur la console s'est construit
le 15 avril 2014 -- la date de la mise à jour Coupe du Monde de FIFA 14 --,
`xbeinfo` le confirme sur `default.xex` (0x534C8977), `CardsDLLzf.xex.dll`
(0x534C94B2) et `powdllzf.xex.dll` (0x534C9611). Aucun autre title update n'est
installé : le seul `TU_` du cache est celui de PAYDAY 2.

Ce que le `.rdata` de CardsDLL porte, en clair :

    0x0156b7  0data\\ui\\layout\\fut\\FutFluxHubWCCfg.xml
    0x0156e8  external.ion_fut.components.Tile.Addon_AssignKitWC
    0x01572c  GOTO_WORLD_CUP          à côté de GOTO_MY_CLUB, GOTO_LEADERBOARD
    0x028c54  enableWorldCupMode      dans la liste de membres de /settings
    0x02b17c  /schedule/tournamentid/%u
    0x02b76c  /teams?groupId=%d&count=%d
    0x02dcbc  /user/tournament        suivi de {"cupsWon":%d}
    0x02ee68  /season/user/history?type=WC_TOURNAMENT_OFFINE   (typo d'EA)
    0x02ee98  /season/user/history?type=WC_TOURNAMENT_ONLINE

plus cinq destinations `GOTO_WC_TOURNAMENT_*` et neuf fonctions natives
(`GetOfflineWCTournamentGroupData`, `GetUserCurrentWCRound`,
`UpdateWCGroupData`, `SaveWCTournyGroupLastMatchData`, …).

Rien de tout ça n'a jamais tourné ici : sur tous les journaux, aucune requête
vers une route Coupe du Monde. C'est pourquoi le drapeau reste désarmé par
défaut et pourquoi les routes répondent du vide bien formé -- un 404 est un gel
sans rien à lire, ce que `season/user/history` a déjà coûté une fois.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

import fifa14_blaze_server as SERVER  # noqa: E402


class WorldCupGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("FIFA14_WORLD_CUP")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("FIFA14_WORLD_CUP", None)
        else:
            os.environ["FIFA14_WORLD_CUP"] = self.previous

    def test_the_mode_is_closed_unless_it_is_asked_for(self) -> None:
        os.environ.pop("FIFA14_WORLD_CUP", None)
        self.assertEqual(SERVER.world_cup_mode(), 0)

    def test_the_flag_opens_it(self) -> None:
        for value in ("1", "true", "yes", "on"):
            os.environ["FIFA14_WORLD_CUP"] = value
            self.assertEqual(SERVER.world_cup_mode(), 1, value)

    def test_anything_else_leaves_it_closed(self) -> None:
        for value in ("0", "false", "no", "off", "peut-etre", ""):
            os.environ["FIFA14_WORLD_CUP"] = value
            self.assertEqual(SERVER.world_cup_mode(), 0, repr(value))


class FutSettingsRouteTests(unittest.TestCase):
    """Ce que `/ut/game/fifa14/settings` sert vraiment, sur le fil."""

    def request(self, path: str, world_cup: str | None) -> dict:
        previous = os.environ.get("FIFA14_WORLD_CUP")
        if world_cup is None:
            os.environ.pop("FIFA14_WORLD_CUP", None)
        else:
            os.environ["FIFA14_WORLD_CUP"] = world_cup
        try:
            with tempfile.TemporaryDirectory() as temp:
                journal = SERVER.Journal(Path(temp) / "journal.jsonl")
                identity = SERVER.IdentityHttpService(
                    "127.0.0.1", 0, "127.0.0.1", journal
                )
                identity.start()
                try:
                    port = identity.server.server_address[1]
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=5
                    )
                    client.request("GET", path)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200, path)
                    body = json.loads(response.read())
                    client.close()
                    return body
                finally:
                    identity.stop()
        finally:
            if previous is None:
                os.environ.pop("FIFA14_WORLD_CUP", None)
            else:
                os.environ["FIFA14_WORLD_CUP"] = previous

    def test_the_settings_document_keeps_its_five_other_members(self) -> None:
        """Les noms viennent du parser FutSettings, pas d'une invention."""
        body = self.request("/ut/game/fifa14/settings", None)
        self.assertEqual(set(body), {
            "maximumTradePileSize", "getOperationTimeoutSec",
            "clubCreateThreshold", "fifaPointsCancelTransactionFix",
            "tokenRedemptionEnabled", "enableWorldCupMode",
        })

    def test_the_gate_travels_on_the_wire(self) -> None:
        self.assertEqual(
            self.request("/ut/game/fifa14/settings", None)["enableWorldCupMode"], 0
        )
        self.assertEqual(
            self.request("/ut/game/fifa14/settings", "1")["enableWorldCupMode"], 1
        )

    def test_the_schedule_route_answers_instead_of_404(self) -> None:
        """`/schedule/tournamentid/%u`, lu a 0x2b17c."""
        self.assertEqual(
            self.request("/ut/game/fifa14/tournament/schedule/tournamentid/7", None),
            {},
        )

    def test_the_user_tournament_route_carries_the_shape_the_binary_names(self) -> None:
        """`/user/tournament` est suivi de `{"cupsWon":%d}` a 0x2dcd0."""
        body = self.request("/ut/game/fifa14/user/tournament", None)
        self.assertEqual(list(body), ["cupsWon"])
        self.assertIsInstance(body["cupsWon"], int)

    def test_both_world_cup_history_spellings_are_answered(self) -> None:
        """`WC_TOURNAMENT_OFFINE` porte la faute de frappe d'EA ; on la sert telle quelle."""
        for kind in ("WC_TOURNAMENT_OFFINE", "WC_TOURNAMENT_ONLINE",
                     "offline", "online"):
            self.assertEqual(
                self.request(
                    f"/ut/game/fifa14/season/user/history?type={kind}", None
                ),
                {}, kind,
            )


if __name__ == "__main__":
    unittest.main()
