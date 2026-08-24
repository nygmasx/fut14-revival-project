#!/usr/bin/env python3
r"""Add the nineteen chemistry styles, and file the position block correctly.

    tools/chemistry_styles.py add       # 250-268 in, 91-136 refiled
    tools/chemistry_styles.py remove    # both undone
    tools/chemistry_styles.py status

Two corrections, one cause. Until 16 August 2026 this catalogue held 36 cards
under the family `playStyle` and none of them were chemistry styles: they were
subtypes 91-110 and 121-136. So the game's chemistry-style tab asked for
`playStyle`, this server handed over 36 position modifiers, and the client drew
them correctly -- as position modifiers. A player could never find a chemistry
style because there was not one in the file.

**What goes in.** Subtypes 250-268: Basic, Sniper, Finisher, Deadeye, Marksman,
Hawk, Artist, Architect, Powerhouse, Maestro, Engine, Sentinel, Guardian,
Gladiator, Backbone, Anchor, Hunter, Catalyst, Shadow. Nineteen of them, all
sharing card art 50, with contiguous definition ids 5003095-5003113.

**What the style is keyed by.** `amount`, running 0 to 18 in subtype order --
not the subtype. `docs/CONSUMABLES.md` recorded that `FUT_PLAYSTYLE_%d` proves
play styles are keyed by an integer in some range without proving which range.
This is the range. The old code wrote the subtype (91-136), which was the wrong
cards *and* the wrong value space.

**What gets refiled.** 91-110 and 121-136 move from family `playStyle` to
`position`. That empties the chemistry tab of everything that is not a
chemistry style, and it also takes them out of packs -- `position` carries no
entry in `CONSUMABLE_DRAW_WEIGHT`, and these are refused on application
anyway, so drawing them was dead weight.

**Source.** The rows come from the PC revival's catalogue
(`KyroGeorge2/FIFA-14-Local-FUT`, `server/fifa14-consumable-catalog.v2412.json`),
which credits the FUT consumable resource schema by koolaidjones. Each imported
row records that in a `source` member so the provenance travels with the data.
Nothing is invented here: an invented asset id draws a card the disc cannot
resolve, which is the failure this file exists to avoid.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOGUE = REPO / "server" / "fifa14_consumables.json"
DEFAULT_SOURCE = REPO / "runtime" / "kyro" / "fifa14-consumable-catalog.v2412.json"

CHEMISTRY = range(250, 269)
POSITION_BLOCK = list(range(91, 111)) + list(range(121, 137))
SOURCE_NOTE = (
    "KyroGeorge2/FIFA-14-Local-FUT consumable catalogue; "
    "FUT consumable resource schema by koolaidjones"
)


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
    styles = [r for r in rows if r.get("cardsubtypeid") in CHEMISTRY]
    misfiled = [
        r for r in rows
        if r.get("cardsubtypeid") in POSITION_BLOCK and r.get("itemType") == "playStyle"
    ]
    print(f"  catalogue: {len(rows)} cards")
    print(f"  chemistry styles (250-268): {len(styles)}")
    print(f"  position block still filed as playStyle: {len(misfiled)}")
    return 0


def add(source: Path) -> int:
    if not source.exists():
        print(f"  source not found: {source}")
        return 1
    document = json.loads(CATALOGUE.read_text())
    rows = _rows(document)
    if any(r.get("cardsubtypeid") in CHEMISTRY for r in rows):
        print("  chemistry styles already present -- remove first")
        return 1

    incoming = sorted(
        (r for r in _rows(json.loads(source.read_text()))
         if r.get("cardsubtypeid") in CHEMISTRY),
        key=lambda r: r["cardsubtypeid"],
    )
    if len(incoming) != len(CHEMISTRY):
        print(f"  expected {len(CHEMISTRY)} styles in the source, found {len(incoming)}")
        return 1

    added = []
    for row in incoming:
        added.append(
            {
                "definitionId": row["resourceId"],
                "assetId": row["cardassetid"],
                "cardsubtypeid": row["cardsubtypeid"],
                # The family the picker filters on. These are the only cards in
                # the file that belong under it.
                "itemType": "playStyle",
                "member": "consumablesTrainingPlayerPlayStyle",
                "rating": row.get("rating", 95),
                # The style itself, 0-18. Not the subtype.
                "amount": row.get("amount", 0),
                "rare": bool(row.get("rareflag") or row.get("rareFlag")),
                "table": "fcc_trainingcards",
                "name": row.get("kind", ""),
                "source": SOURCE_NOTE,
            }
        )

    refiled = 0
    for row in rows:
        if row.get("cardsubtypeid") in POSITION_BLOCK and row.get("itemType") == "playStyle":
            row["itemType"] = "position"
            refiled += 1

    rows.extend(added)
    _write(CATALOGUE, document)
    print(f"  added {len(added)} chemistry styles ({added[0]['name']} .. {added[-1]['name']})")
    print(f"  refiled {refiled} position-block cards out of playStyle")
    print(f"  catalogue is now {len(rows)} cards")
    return 0


def remove() -> int:
    document = json.loads(CATALOGUE.read_text())
    rows = _rows(document)
    keep = [r for r in rows if r.get("cardsubtypeid") not in CHEMISTRY]
    dropped = len(rows) - len(keep)
    restored = 0
    for row in keep:
        if row.get("cardsubtypeid") in POSITION_BLOCK and row.get("itemType") == "position":
            row["itemType"] = "playStyle"
            restored += 1
    if isinstance(document, list):
        document = keep
    else:
        for key, value in document.items():
            if isinstance(value, list):
                document[key] = keep
                break
    _write(CATALOGUE, document)
    print(f"  removed {dropped} chemistry styles, restored {restored} to playStyle")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "status"
    if action == "add":
        return add(Path(args[1]) if len(args) > 1 else DEFAULT_SOURCE)
    if action == "remove":
        return remove()
    if action == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
