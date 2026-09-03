#!/usr/bin/env python3
r"""Add the twenty-seven manager league modifiers, subtypes 300-326.

    tools/manager_league_mods.py add
    tools/manager_league_mods.py remove
    tools/manager_league_mods.py status

**What was wrong.** The club's manager-league tab reported sixty-nine cards and
then found none, because there were none: `CONSUMABLE_FALLBACKS` had
`consumablesTrainingManagerLeagueModifier` report the training family's count
so the apply screen would not refuse on a zero. That fallback was honest about
itself -- it said the manager's cards were in the database at subtypes 300-340
but that nothing named which member each block belonged to, and that guessing
would file the wrong card under the wrong name. This file is that block being
named rather than guessed, so the fallback comes out.

**Where the naming comes from.** Two extracts of the game's own database, taken
by different people from different builds, agreeing on every row they share:

  * `MarvelcoCode/Impulsum14`'s `FUTDB/consumables.tsv` gives all twenty-seven
    rows with the item type spelled out -- `managerLeagueModifier` -- their
    subtypes, their resource ids and their names. This is the same extract the
    goalkeeper chemistry styles came from, and it continues this catalogue's
    own sequence without a gap: 273 is resource 5003118, 300 is 5003119.
  * `KyroGeorge2/FIFA-14-Local-FUT`'s catalogue carries six of the same rows
    plus Portugal and Legends, tagged `category: "Manager League"` and
    `class: "Manager"`, and adds the two columns Impulsum's extract does not
    have: `cardassetid` and `weightrare`.

All six overlapping rows match on subtype and resource id exactly. The two
sources were not derived from each other.

**The art.** Asset 32, uniform across all eight rows Kyro holds, so it is the
block's id rather than one card's. It is not invented -- an invented asset id
draws NOT FOUND, which is what `tools/chemistry_styles.py` exists to have
stopped doing -- but it has not been seen on this console yet, so these go into
`UNDRAWN_CONSUMABLE_TYPES` and stay out of packs until one is looked at.

**`amount` is the league id**, not an ordinal. Kyro's rows give 13 for the
Premier League, 16 for Ligue 1, 19 for the Bundesliga, 31 for Serie A, 53 for
La Liga and 67 for the Russian Premier League -- FIFA's own league numbers, and
each one lands exactly where an ascending walk through the subtype block puts
it. The remaining twenty-one are that walk continued, and marked below. Two
rows outside this range check the same walk from the far end: Kyro has subtype
328 at league 308, which is Portugal's number, and 337 at 2118, which is
Legends'.

`amount` does go out on the wire -- `_consumable_item` sends it for every
consumable, and it is what a contract's match count travels in -- so the walk
is not a private numbering. That is the reason to send it rather than a zero:
these are seeded into the club to be looked at, and if a card comes back naming
the wrong league then the walk is wrong and the console will have said so.

**`rare` is where the sources disagree.** Impulsum's `rareflag` column is 0 on
all twenty-seven; Kyro's `weightrare` is 15 on the five biggest leagues and on
Legends, 0 on Russia and Portugal. `weightrare` is the column
`tools/build_consumables.py` reads for `rare` on every other card in this file,
so it wins where it exists, and the rows it does not cover stay common. That
reading is also the one that describes retail: the top five leagues are the
rare ones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOGUE = REPO / "server" / "fifa14_consumables.json"

BLOCK = range(300, 327)

MEMBER = "consumablesTrainingManagerLeagueModifier"
ASSET = 32
RATING = 95

SOURCE_NOTE = (
    "MarvelcoCode/Impulsum14 FUTDB/consumables.tsv for the block; "
    "KyroGeorge2/FIFA-14-Local-FUT for cardassetid and weightrare"
)

# subtype, resourceId, name, league id, rare
#
# The names are Impulsum's verbatim, including the division number the game's
# own league table carries in the string -- "(1)" for a first tier, "(2)" for a
# second. The console draws the name itself from the subtype, so these are for
# reading this file by.
#
# A league id marked `#` is Kyro's, read from the game's database. The rest are
# the ascending walk between them.
LEAGUES: list[tuple[int, int, str, int, bool]] = [
    (300, 5_003_119, "Denmark Superliga (1)",              1, False),
    (301, 5_003_120, "Belgium Jupiler Pro League (1)",     4, False),
    (302, 5_003_121, "Brazil Campeonato Brasileiro (1)",   7, False),
    (303, 5_003_122, "Holland Eredivisie (1)",            10, False),
    (304, 5_003_123, "England Premier League (1)",        13, True),   #
    (305, 5_003_124, "England League Championship (2)",   14, False),
    (306, 5_003_125, "France Ligue 1 (1)",                16, True),   #
    (307, 5_003_126, "France Ligue 2 (2)",                17, False),
    (308, 5_003_127, "Germany 1. Bundesliga (1)",         19, True),   #
    (309, 5_003_128, "Germany 2. Bundesliga (2)",         20, False),
    (310, 5_003_129, "Italy Serie A (1)",                 31, True),   #
    (311, 5_003_130, "Italy Serie B (2)",                 32, False),
    (312, 5_003_131, "USA Major League Soccer (1)",       39, False),
    (313, 5_003_132, "Norway Tippeligaen (1)",            41, False),
    (314, 5_003_133, "Scotland Premier League (1)",       50, False),
    (315, 5_003_134, "Spain Primera Division (1)",        53, True),   #
    (316, 5_003_135, "Spain Segunda A (2)",               54, False),
    (317, 5_003_136, "Sweden Allsvenskan (1)",            56, False),
    (318, 5_003_137, "England League One (3)",            60, False),
    (319, 5_003_138, "England League Two (4)",            61, False),
    (320, 5_003_139, "Greece A'Ethniki (1)",              63, False),
    (321, 5_003_140, "Rep. Ireland Airtricity League (1)", 65, False),
    (322, 5_003_141, "Poland T-Mobile Ekstraklasa (1)",   66, False),
    (323, 5_003_142, "Russia Premier League (1)",         67, False),  #
    (324, 5_003_143, "Turkey Super Lig (1)",              68, False),
    (325, 5_003_144, "Austria tipp3-Bundesliga (1)",      80, False),
    (326, 5_003_145, "Korea K League Classic (1)",        83, False),
]


def _rows(document):
    if isinstance(document, list):
        return document
    for value in document.values():
        if isinstance(value, list):
            return value
    raise SystemExit("unrecognised catalogue shape")


def _write(path: Path, document) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, separators=(",", ":")))
    tmp.replace(path)


def status() -> int:
    rows = _rows(json.loads(CATALOGUE.read_text()))
    held = [r for r in rows if r.get("cardsubtypeid") in BLOCK]
    print(f"  catalogue: {len(rows)} cards")
    print(f"  manager league modifiers (300-326): {len(held)} of {len(LEAGUES)}")
    return 0


def add() -> int:
    document = json.loads(CATALOGUE.read_text())
    rows = _rows(document)
    if any(r.get("cardsubtypeid") in BLOCK for r in rows):
        print("  manager league modifiers already present -- remove first")
        return 1

    for subtype, resource, name, league, rare in LEAGUES:
        rows.append(
            {
                "definitionId": resource,
                "assetId": ASSET,
                "cardsubtypeid": subtype,
                "itemType": "managerLeagueModifier",
                "member": MEMBER,
                "rating": RATING,
                # The league the modifier names, by FIFA's own numbering.
                "amount": league,
                "rare": rare,
                "table": "fcc_trainingcards",
                "name": name,
                "source": SOURCE_NOTE,
            }
        )

    _write(CATALOGUE, document)
    print(f"  added {len(LEAGUES)} manager league modifiers "
          f"({LEAGUES[0][2]} .. {LEAGUES[-1][2]})")
    print(f"  catalogue is now {len(rows)} cards")
    return 0


def remove() -> int:
    document = json.loads(CATALOGUE.read_text())
    rows = _rows(document)
    keep = [r for r in rows if r.get("cardsubtypeid") not in BLOCK]
    dropped = len(rows) - len(keep)
    if isinstance(document, list):
        document = keep
    else:
        for key, value in document.items():
            if isinstance(value, list):
                document[key] = keep
                break
    _write(CATALOGUE, document)
    print(f"  removed {dropped} manager league modifiers")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "status"
    if action == "add":
        return add()
    if action == "remove":
        return remove()
    if action == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
