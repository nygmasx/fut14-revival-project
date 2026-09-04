"""Une saison qui se termine, et qui le dit.

Le serveur tenait le compte depuis le 16 août -- `SeasonProgress.settle`
enregistre chaque résultat -- et n'en disait rien : son retour n'allait qu'au
journal, pour écrire la ligne BILAN. Rien de ce qu'il calculait n'entrait dans
la réponse envoyée à la console, et `divisionOffline` valait 10 en dur, donc
une montée n'avait nulle part où s'inscrire.

Le serveur PC d'AC (Discord, 31 août 2026) nomme la pièce qui manque en une
phrase : *« The match/end reply then carries the announcement the client acts
on: seasonEndResult + seasonCoins -- without those the client keeps offering
fixtures. »*

`seasonEndResult` n'est pas un nom deviné : la table de noms de CardsDLL le
porte à `0x101c0`, relevée dans `docs/TOURNAMENTS.md`.
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

import fifa14_blaze_server as SERVER  # noqa: E402
import fut_inventory as INVENTORY  # noqa: E402


class SeasonTableTests(unittest.TestCase):
    """Ce que vaut une saison, lu dans le disque effectivement servi."""

    def test_three_points_a_win_and_one_a_draw(self) -> None:
        self.assertEqual(INVENTORY.season_points(0, 0), 0)
        self.assertEqual(INVENTORY.season_points(3, 1), 10)

    def test_each_threshold_is_the_one_the_screen_shows(self) -> None:
        """Le règlement et l'écran de détails lisent la même liste.

        C'est la raison d'être de `season_definition` : le seuil de titre
        s'était déjà mis à bouger avec celui de la montée sans que rien ne le
        signale, parce qu'ils vivaient à deux endroits.
        """
        record = INVENTORY.season_definition(10)
        levels = {prize["prizeLevel"]: prize["thresholdPoint"]
                  for prize in record["prizeSet"]}
        self.assertEqual(INVENTORY.season_outcome(10, levels["CHAMPIONSHIP"])[0],
                         "CHAMPIONSHIP")
        self.assertEqual(INVENTORY.season_outcome(10, levels["PROMOTION"])[0],
                         "PROMOTION")
        self.assertEqual(INVENTORY.season_outcome(10, 0)[0], "MAINTENANCE")

    def test_a_title_pays_what_the_record_says(self) -> None:
        record = INVENTORY.season_definition(10)
        champion = next(p for p in record["prizeSet"]
                        if p["prizeLevel"] == "CHAMPIONSHIP")
        expected = sum(int(a["value"]) for m in champion["awardMappings"]
                       for a in m["awards"] if a["type"] == "coin")
        self.assertEqual(INVENTORY.season_outcome(10, 99)[1], expected)

    def test_promotion_goes_down_the_numbers(self) -> None:
        """La Division 1 est le sommet, donc monter décrémente."""
        self.assertEqual(INVENTORY.season_next_division(10, "PROMOTION"), 9)
        self.assertEqual(INVENTORY.season_next_division(10, "CHAMPIONSHIP"), 9)
        self.assertEqual(INVENTORY.season_next_division(10, "MAINTENANCE"), 10)
        self.assertEqual(INVENTORY.season_next_division(9, "RELEGATION"), 10)

    def test_neither_end_of_the_ladder_can_be_walked_off(self) -> None:
        self.assertEqual(INVENTORY.season_next_division(1, "CHAMPIONSHIP"), 1)
        self.assertEqual(INVENTORY.season_next_division(10, "RELEGATION"), 10)


class SeasonFinalisationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.progress = INVENTORY.SeasonProgress()
        self.season = INVENTORY.season_id(10)

    def play(self, results: list[str]) -> None:
        for result in results:
            self.progress.settle(self.season, 10, result, 100)

    def test_nothing_is_announced_while_a_fixture_remains(self) -> None:
        self.play(["WIN"] * 9)
        self.assertEqual(self.progress.finalize(self.season, 10), {})

    def test_the_last_fixture_settles_the_season(self) -> None:
        self.play(["WIN"] * 10)
        end = self.progress.finalize(self.season, 10)
        self.assertEqual(end["outcome"], "CHAMPIONSHIP")
        self.assertEqual(end["points"], 30)
        self.assertEqual(end["division"], 9)
        self.assertGreater(end["prize"], 0)

    def test_a_season_is_not_won_twice(self) -> None:
        """`match/end` peut arriver deux fois pour la même rencontre."""
        self.play(["WIN"] * 10)
        self.assertTrue(self.progress.finalize(self.season, 10))
        self.assertEqual(self.progress.finalize(self.season, 10), {})
        self.assertEqual(self.progress.division, 9)

    def test_ten_defeats_hold_the_division(self) -> None:
        """Le seuil de maintien vaut zéro, donc personne ne descend.

        C'est ce que le `prizeSet` servi dit, et le règlement n'a pas d'autre
        source -- inventer une ligne de relégation ici la ferait diverger de
        l'écran de détails, qui n'en montre pas.
        """
        self.play(["LOSS"] * 10)
        end = self.progress.finalize(self.season, 10)
        self.assertEqual(end["outcome"], "MAINTENANCE")
        self.assertEqual(self.progress.division, 10)

    def test_the_division_survives_a_save(self) -> None:
        self.play(["WIN"] * 10)
        self.progress.finalize(self.season, 10)
        restored = INVENTORY.SeasonProgress()
        restored.restore(self.progress.state())
        self.assertEqual(restored.division, 9)

    def test_a_save_written_before_this_field_starts_at_ten(self) -> None:
        restored = INVENTORY.SeasonProgress()
        restored.restore({"1112:10": {"round": 2}})
        self.assertEqual(restored.division, 10)


class ServedDivisionTests(unittest.TestCase):
    """La division du club, dans le document que la console lit.

    `divisionOffline` valait `10` en dur, donc `SeasonProgress` pouvait faire
    monter un club sans que rien ne le lui dise : il rouvrait le mode en
    Division 10 apres avoir gagne sa saison.

    Lecture et ecriture sur le meme thread, parce que le club servi est resolu
    par thread -- ce qui compte ici est que le document suive la valeur, pas
    lequel des clubs la porte.
    """

    def test_the_user_document_reports_the_clubs_division(self) -> None:
        before = int(INVENTORY.SEASON_PROGRESS.division)
        try:
            INVENTORY.SEASON_PROGRESS.division = 4
            served = json.loads(SERVER.WALLET.user_info("Test", "TST"))
            self.assertEqual(int(served["divisionOffline"]), 4)
        finally:
            INVENTORY.SEASON_PROGRESS.division = before


class MatchEndAnnouncementTests(unittest.TestCase):
    """Ce que la console lit à la fin d'une saison."""

    def drive(self, matches: int) -> tuple[list[dict], list[dict]]:
        """Jouer `matches` rencontres, et rendre les reponses et le journal.

        Tout passe par le serveur HTTP, y compris la remise a zero. Le club
        servi est resolu **par thread** avec repli sur le club par defaut : une
        remise a zero faite depuis le thread du test s'applique donc au club
        que ce thread-la regarde, qui n'est pas forcement celui du gestionnaire
        de requete si un autre test a laisse un lien derriere lui. La suite
        complete le montrait et l'execution isolee non, ce qui est la forme
        classique de cette erreur.

        Meme raison pour lire le resultat dans le journal plutot que dans le
        singleton : le journal est ecrit par le thread qui a servi.
        """
        replies = []
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(path)
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                season = INVENTORY.season_id(10)
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                client.request(
                    "PUT",
                    f"/ut/game/fifa14/season/{season}/division/10/reset",
                    b"{}",
                )
                client.getresponse().read()
                client.close()
                for _ in range(matches):
                    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    client.request(
                        "POST", "/ut/game/fifa14/match",
                        json.dumps({"seasonId": season, "divisionId": 10}).encode(),
                    )
                    client.getresponse().read()
                    client.request(
                        "PUT", "/ut/game/fifa14/match/end",
                        json.dumps({
                            "matchData": "QUJD",
                            "myMatchStats": {"goals": 2},
                            "opponentMatchStats": {"goals": 0},
                            "minutesPlayed": 90,
                        }).encode(),
                    )
                    replies.append(json.loads(client.getresponse().read()))
                    client.close()
            finally:
                identity.stop()
            ends = [
                json.loads(line) for line in path.read_text().splitlines()
                if line.strip() and json.loads(line).get("event") == "fut_match_end"
            ]
        return replies, ends

    def test_nothing_is_announced_before_the_last_fixture(self) -> None:
        replies, _ = self.drive(3)
        for reply in replies:
            self.assertNotIn("seasonEndResult", reply)

    def test_the_last_fixture_carries_the_announcement(self) -> None:
        replies, _ = self.drive(10)
        for reply in replies[:-1]:
            self.assertNotIn("seasonEndResult", reply)
        last = replies[-1]
        self.assertEqual(last["seasonEndResult"], "CHAMPIONSHIP")
        # Le prix de la saison, et non le solde du club : servir le second
        # reviendrait à annoncer un gain de neuf cent millions.
        self.assertEqual(last["seasonCoins"],
                         INVENTORY.season_outcome(10, 30)[1])

    def test_the_club_is_told_where_it_now_plays(self) -> None:
        """`divisionOffline` valait 10 en dur : une montée n'allait nulle part."""
        _replies, ends = self.drive(10)
        self.assertEqual(ends[-1]["seasonDivision"], 9)
        self.assertIsNone(ends[0]["seasonDivision"])


if __name__ == "__main__":
    unittest.main()
