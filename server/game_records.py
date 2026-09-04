"""Ce qu'un match laisse derrière lui, et ce qu'on en tire.

Un classement n'est pas une donnée : c'est ce qui reste quand on a joué. Le
titre envoie un `submitGameReport` à la fin de chaque partie, et ce rapport
porte tout -- buts, tirs, passes, tacles, cartons, résultat. Il a longtemps été
illisible ici, faute d'un dixième type TDF (un flottant sur 32 bits) sur lequel
le décodeur mourait. Depuis qu'il se lit, il n'y a plus de raison de jeter ce
qu'il contient.

Deux choses vivent ici, et elles sont séparées à dessein :

- `extract_players` traduit la structure du rapport en chiffres. Elle ne sait
  rien du stockage et se teste avec une trame et rien d'autre.
- `RecordStore` accumule et agrège. Il ne sait rien de Blaze.

Le format de stockage est un fichier de lignes JSON. Un match est un fait
accompli : on l'ajoute, on ne le modifie jamais. Un fichier qui ne fait que
grandir se répare à la main quand quelque chose tourne mal, ce qu'aucune base
de données ne permet aussi simplement.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Iterable


# Ce que les libellés du rapport veulent dire.
#
# Ceux de la première liste sont sûrs : leur nom et leur valeur concordent sur
# la trame du 22 août -- GOAL 1 avec GLAG 0 et WINS 1, pour un match gagné 1-0.
# `PSCT` à 67 est très probablement la possession en pourcentage et `GTIM` une
# durée dont l'unité reste inconnue ; les deux sont gardés tels quels, sans
# être présentés comme établis.
MATCH_STATS = {
    "GOAL": "buts",
    "CSHO": "tirs",
    "SHGL": "tirs_cadres",
    "CPSA": "passes_tentees",
    "CPSM": "passes_reussies",
    "CTKA": "tacles_tentes",
    "CTKM": "tacles_reussis",
    "CORN": "corners",
    "OFFS": "hors_jeu",
    "FOUL": "fautes",
    "YWCD": "cartons_jaunes",
    "RDCD": "cartons_rouges",
    "CSAV": "arrets",
    "INTC": "interceptions",
    "OWGL": "buts_contre_son_camp",
    "PKGL": "buts_sur_penalty",
    "PSCT": "possession",
}

RESULT_STATS = {
    "GLAG": "buts_encaisses",
    "SHAG": "tirs_subis",
    "WINS": "victoires",
    "LOSS": "defaites",
    "TIES": "nuls",
    "TEAM": "equipe_adverse",
}


@dataclass
class PlayerRecord:
    """Ce qu'un joueur a fait dans un match."""

    persona_id: int
    name: str = ""
    team: int = 0
    stats: dict[str, int] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "name": self.name,
            "team": self.team,
            **self.stats,
        }


def _find(fields: Iterable[Any], label: str) -> Any:
    for entry in fields or ():
        if getattr(entry, "label", None) == label:
            return entry
    return None


def _unwrap(entry: Any) -> list:
    """Rendre les champs d'une struct, qu'elle soit nue ou emballée.

    Le rapport alterne les deux : `GAMR` est une struct simple, `CPRT` est une
    variable qui porte un identifiant de classe puis la struct. Les traiter
    séparément partout rendrait le reste illisible.
    """
    if entry is None:
        return []
    value = entry.value
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], list):
        return value[1]
    if isinstance(value, list):
        return value
    return []


def _numbers(fields: Iterable[Any], table: dict[str, str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for entry in fields or ():
        name = table.get(getattr(entry, "label", ""))
        if name is None:
            continue
        value = entry.value
        if isinstance(value, (int, float)):
            found[name] = int(value)
    return found


def extract_players(report: Any) -> list[PlayerRecord]:
    """Sortir un enregistrement par joueur du champ `RPRT`.

    Le chemin est celui qu'on a déplié à la main sur la trame réelle :

        RPRT -> GAME -> GAME -> PLYR      dictionnaire, clé = identifiant
        PLYR[id] -> CPRT -> CPRT -> CMPR  les statistiques du match
                                 -> SCPR  le résultat

    Tout ce qui manque est absent, pas nul : un rapport qui n'a pas de `SCPR`
    rend un joueur sans résultat plutôt qu'un joueur qui aurait perdu.
    """
    outer = _unwrap(report)
    game = _unwrap(_find(outer, "GAME"))
    inner = _unwrap(_find(game, "GAME")) or game
    players = _find(inner, "PLYR")
    if players is None or not isinstance(players.value, tuple):
        return []
    _, _, pairs = players.value

    records: list[PlayerRecord] = []
    for key, value in pairs:
        try:
            persona = int(key)
        except (TypeError, ValueError):
            continue
        fields = value if isinstance(value, list) else []
        record = PlayerRecord(persona_id=persona)
        name = _find(fields, "NAME")
        if name is not None and isinstance(name.value, str):
            record.name = name.value
        team = _find(fields, "TEAM")
        if team is not None and isinstance(team.value, int):
            record.team = team.value

        competition = _unwrap(_find(_unwrap(_find(fields, "CPRT")), "CPRT"))
        record.stats.update(_numbers(_unwrap(_find(competition, "CMPR")), MATCH_STATS))
        record.stats.update(_numbers(_unwrap(_find(competition, "SCPR")), RESULT_STATS))
        records.append(record)
    return records


def match_type(report: Any) -> str:
    outer = _unwrap(report)
    kind = _find(outer, "GTYP")
    return str(kind.value) if kind is not None and kind.value else ""


def match_duration(report: Any) -> int:
    game = _unwrap(_find(_unwrap(report), "GAME"))
    inner = _unwrap(_find(game, "GAME")) or game
    duration = _find(_unwrap(_find(inner, "GAMR")), "GTIM")
    return int(duration.value) if duration is not None and isinstance(duration.value, int) else 0


# Ce qu'un classement ordonne, et dans quel sens.
#
# Les buts encaissés se classent à l'envers : en encaisser moins est mieux.
# Une table qui l'ignore met le pire gardien en tête, ce qui se remarque tout
# de suite -- et c'est bien pour ça qu'on l'écrit plutôt que de supposer que
# « plus haut est meilleur » vaut partout.
BOARDS = {
    "victoires": ("victoires", True),
    "buts": ("buts", True),
    "matchs": ("matchs", True),
    "tirs_cadres": ("tirs_cadres", True),
    "passes_reussies": ("passes_reussies", True),
    "tacles_reussis": ("tacles_reussis", True),
    "buts_encaisses": ("buts_encaisses", False),
}


class RecordStore:
    """Les matchs joués, et ce qu'on en déduit."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()

    def add(self, players: list[PlayerRecord], kind: str = "",
            duration: int = 0, when: str = "") -> None:
        if not players:
            return
        line = json.dumps({
            "when": when,
            "type": kind,
            "duration": duration,
            "players": [record.as_dict() for record in players],
        }, ensure_ascii=False)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    def matches(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue        # une ligne tronquée n'invalide pas le reste
        return out

    def totals(self) -> dict[int, dict[str, Any]]:
        """Additionner les matchs, joueur par joueur."""
        people: dict[int, dict[str, Any]] = {}
        for match in self.matches():
            for player in match.get("players", []):
                persona = player.get("persona_id")
                if not isinstance(persona, int):
                    continue
                entry = people.setdefault(persona, {
                    "persona_id": persona, "name": "", "matchs": 0,
                })
                if player.get("name"):
                    entry["name"] = player["name"]
                entry["matchs"] += 1
                for key, value in player.items():
                    if key in ("persona_id", "name", "team") or not isinstance(value, int):
                        continue
                    entry[key] = entry.get(key, 0) + value
        return people

    def leaderboard(self, name: str, limit: int = 50) -> list[dict]:
        column, descending = BOARDS.get(name, (name, True))
        rows = [
            row for row in self.totals().values()
            if row.get(column) is not None
        ]
        rows.sort(key=lambda row: row.get(column, 0), reverse=descending)
        for rank, row in enumerate(rows[:limit], start=1):
            row["rang"] = rank
            row["valeur"] = row.get(column, 0)
        return rows[:limit]

    def leaderboards(self, limit: int = 50) -> dict[str, list[dict]]:
        return {name: self.leaderboard(name, limit) for name in BOARDS}
