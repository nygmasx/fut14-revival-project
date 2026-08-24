#!/usr/bin/env python3
r"""Take the World Cup cards out of the catalogue, or put them back.

    tools/worldcup_cards.py split      # 1077 cards out to their own file
    tools/worldcup_cards.py restore    # and back again
    tools/worldcup_cards.py status

The World Cup cards belong to World Cup Ultimate Team, a different mode. They
are in the catalogue because the scrape took everything wefut had for FIFA 14,
not because this server models that mode -- and they are the **largest** special
family in the file, 1077 cards against Team of the Week's 768, so they are not
a rounding error in a pack or on the market.

They are also the ones that draw wrong. Measured 2026-08-16, every one of them
carries a confederation where the club should be:

    UEFA 465, CONMEBOL 189, CAF 144, CONCACAF 141, AFC 137, blank 1

with `clubId` in 200000-200004 and `leagueId` 2014. The highest genuine club id
in the file is 112679, so nothing on the disc answers to those numbers: the card
draws with a blank crest, and -- the part that is not cosmetic -- it can build
no club or league chemistry with anybody. Nation still links, because the
nation ids are real.

Splitting rather than deleting: the file that comes out is the whole card
record, so `restore` puts the catalogue back exactly as it was. Nothing here
edits a club save, and a player who owns one of these cards keeps it; the
catalogue decides what packs draw and what the market sells, not what is
already owned.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOGUE = REPO / "server" / "fifa14_cards.json"
EXTRACTED = REPO / "server" / "fifa14_cards_worldcup.json"
RARITY = "World Cup"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _write(path: Path, document: dict) -> None:
    """Write through a temporary file.

    The catalogue is 3.7 MB and the server reads it at startup; a half-written
    one is a server that will not start, and this runs against a live install.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, separators=(",", ":")))
    tmp.replace(path)


def status() -> int:
    cards = _load(CATALOGUE)["cards"]
    present = [c for c in cards if str(c.get("rarity")) == RARITY]
    print(f"  catalogue: {len(cards):,} cards, {len(present):,} World Cup")
    if EXTRACTED.exists():
        held = _load(EXTRACTED)["cards"]
        print(f"  extracted: {len(held):,} cards in {EXTRACTED.name}")
    else:
        print(f"  extracted: {EXTRACTED.name} does not exist")
    return 0


def split() -> int:
    document = _load(CATALOGUE)
    cards = document["cards"]
    keep = [c for c in cards if str(c.get("rarity")) != RARITY]
    move = [c for c in cards if str(c.get("rarity")) == RARITY]
    if not move:
        print("  nothing to split -- no World Cup cards in the catalogue")
        return 0
    if EXTRACTED.exists():
        print(f"  refusing: {EXTRACTED.name} already exists (restore first)")
        return 1
    _write(EXTRACTED, {"cards": move})
    document["cards"] = keep
    _write(CATALOGUE, document)
    print(f"  moved {len(move):,} World Cup cards to {EXTRACTED.name}")
    print(f"  catalogue is now {len(keep):,} cards")
    return 0


def restore() -> int:
    if not EXTRACTED.exists():
        print(f"  nothing to restore -- {EXTRACTED.name} does not exist")
        return 1
    document = _load(CATALOGUE)
    cards = document["cards"]
    held = _load(EXTRACTED)["cards"]
    known = {(c.get("assetId"), str(c.get("rarity")), c.get("rating")) for c in cards}
    back = [
        c for c in held
        if (c.get("assetId"), str(c.get("rarity")), c.get("rating")) not in known
    ]
    document["cards"] = cards + back
    _write(CATALOGUE, document)
    EXTRACTED.unlink()
    print(f"  restored {len(back):,} cards -- catalogue is now {len(document['cards']):,}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "status"
    if action == "split":
        return split()
    if action == "restore":
        return restore()
    if action == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
