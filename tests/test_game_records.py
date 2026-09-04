"""Les classements sont la somme des matchs joués, et rien d'autre.

Tout part d'une trame réelle : le `submitGameReport` envoyé par une console le
22 août 2026, à la fin d'un match gagné 1-0. Elle est reprise du test du type
flottant plutôt que recopiée -- deux copies d'une même preuve finissent
toujours par diverger.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "tests"))

from blaze_tdf import decode_frame  # noqa: E402
from game_records import (  # noqa: E402
    PlayerRecord,
    RecordStore,
    extract_players,
    match_duration,
    match_type,
)
from test_blaze_tdf_float import GAME_REPORT  # noqa: E402

IMRANE = 2535469248587161


def report_field():
    fields = decode_frame(GAME_REPORT)["fields"]
    return next(f for f in fields if f.label == "RPRT")


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.players = extract_players(report_field())

    def test_the_match_has_one_player(self) -> None:
        """Un seul joueur dans le rapport : c'était un match local.

        Ça compte pour la suite -- un rapport de match en ligne en portera
        deux, et c'est la seule chose qui distingue les deux cas ici.
        """
        self.assertEqual(len(self.players), 1)
        self.assertEqual(self.players[0].persona_id, IMRANE)

    def test_the_score_is_read_from_the_report(self) -> None:
        """1-0, et les trois membres concordent.

        `GOAL` à 1, `GLAG` à 0, `WINS` à 1 : c'est cette concordance qui rend
        la lecture des libellés sûre, pas leur ressemblance avec des mots
        anglais.
        """
        stats = self.players[0].stats
        self.assertEqual(stats["buts"], 1)
        self.assertEqual(stats["buts_encaisses"], 0)
        self.assertEqual(stats["victoires"], 1)
        self.assertEqual(stats["defaites"], 0)
        self.assertEqual(stats["nuls"], 0)

    def test_the_match_statistics_are_read_too(self) -> None:
        stats = self.players[0].stats
        self.assertEqual(stats["tirs"], 8)
        self.assertEqual(stats["tirs_cadres"], 5)
        self.assertEqual(stats["passes_tentees"], 162)
        self.assertEqual(stats["passes_reussies"], 147)
        self.assertEqual(stats["tacles_tentes"], 23)
        self.assertEqual(stats["tacles_reussis"], 15)
        self.assertEqual(stats["corners"], 4)
        self.assertEqual(stats["hors_jeu"], 3)
        self.assertEqual(stats["cartons_jaunes"], 0)
        self.assertEqual(stats["cartons_rouges"], 0)

    def test_the_kind_and_length_of_the_match(self) -> None:
        self.assertEqual(match_type(report_field()), "gameType95")
        self.assertEqual(match_duration(report_field()), 5567)

    def test_a_report_without_players_yields_nothing(self) -> None:
        """Et ne lève pas.

        Le rapport arrive à la fin d'un match, et le titre attend son accusé
        de réception pour quitter l'écran. Un rapport d'une forme inattendue
        doit rendre une liste vide, pas laisser le joueur bloqué.
        """
        self.assertEqual(extract_players(None), [])


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = RecordStore(Path(self.temp.name) / "records.jsonl")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add(self, persona: int, **stats: int) -> None:
        self.store.add([PlayerRecord(persona_id=persona, stats=dict(stats))])

    def test_a_match_is_appended_never_rewritten(self) -> None:
        """Un match joué est un fait accompli.

        Le fichier ne fait que grandir : deux matchs, deux lignes. Rien ne
        réécrit une ligne existante, donc rien ne peut effacer un résultat en
        se trompant de joueur.
        """
        self.add(1, buts=2)
        self.add(1, buts=1)
        lines = self.store.path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["players"][0]["buts"], 2)

    def test_totals_add_matches_up(self) -> None:
        self.add(1, buts=2, victoires=1)
        self.add(1, buts=1, victoires=0)
        totals = self.store.totals()
        self.assertEqual(totals[1]["buts"], 3)
        self.assertEqual(totals[1]["victoires"], 1)
        self.assertEqual(totals[1]["matchs"], 2)

    def test_goals_conceded_are_ranked_the_other_way(self) -> None:
        """En encaisser moins est mieux.

        Une table qui classe tout « du plus grand au plus petit » met le pire
        gardien en tête. C'est le genre de détail qu'on ne voit qu'une fois
        publié, donc il est vérifié ici.
        """
        self.add(1, buts_encaisses=7)
        self.add(2, buts_encaisses=1)
        board = self.store.leaderboard("buts_encaisses")
        self.assertEqual([row["persona_id"] for row in board], [2, 1])
        self.assertEqual(board[0]["rang"], 1)

    def test_wins_are_ranked_highest_first(self) -> None:
        self.add(1, victoires=1)
        self.add(2, victoires=3)
        board = self.store.leaderboard("victoires")
        self.assertEqual([row["persona_id"] for row in board], [2, 1])
        self.assertEqual(board[0]["valeur"], 3)

    def test_an_empty_store_has_empty_boards(self) -> None:
        """Aucun match joué : aucun classement, et pas une erreur."""
        self.assertEqual(self.store.matches(), [])
        self.assertEqual(self.store.leaderboard("victoires"), [])

    def test_a_truncated_line_does_not_lose_the_rest(self) -> None:
        """Un fichier coupé net garde ce qui le précède.

        Le serveur peut être arrêté en pleine écriture. La dernière ligne est
        alors incomplète, et ce serait absurde de perdre tous les matchs
        précédents pour ça.
        """
        self.add(1, buts=2)
        with self.store.path.open("a", encoding="utf-8") as stream:
            stream.write('{"players": [{"persona_i')
        self.assertEqual(len(self.store.matches()), 1)

    def test_the_real_report_goes_in_and_comes_back_out(self) -> None:
        """De la trame au classement, sans rien saisir à la main."""
        self.store.add(extract_players(report_field()))
        board = self.store.leaderboard("buts")
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["persona_id"], IMRANE)
        self.assertEqual(board[0]["valeur"], 1)
        self.assertEqual(board[0]["matchs"], 1)


if __name__ == "__main__":
    unittest.main()
