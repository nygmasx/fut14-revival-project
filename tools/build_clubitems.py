#!/usr/bin/env python3
"""Build the club-item catalogue from what the console confirmed.

    tools/build_clubitems.py            write server/fifa14_clubitems.json
    tools/build_clubitems.py --summary  report without writing

Four families, each a run of resource ids whose **upper bound** was found on
the console during the probe sessions of 18-19 August 2026:

    kit      6300000-6300860   861
    badge    6000000-6000600   601
    stadium  6200000-6200060    61
    ball     8120091-8120137    47

The resource is what matters. A club item's art resolves from its resourceId,
not its asset -- asset 241 drew FC Barcelona at resource 6000000 and NOT FOUND
at 6900241 -- so every card here holds its family's known-good asset and varies
only the resource.

What the probe established is where each family **stops**, not that everything
below it renders. It visited 24 kit ids and every one was above 6300860; the
interior was assumed contiguous. Kit 6300772 came out of a pack on 24 August
drawing NOT FOUND, so the interiors have holes and the assumption was wrong.
Balls were never probed at all -- their range came from the same reasoning with
no measurements behind it.

Ids that fail are listed in `server/fifa14_clubitems_blank.json` and skipped
here. The server reads the same file at load, so a new one takes effect without
a rebuild. Sweeping the interiors properly is the real fix and has not been
done.

QUICK SELL VALUES are the real ones, from fifauteam's FIFA 14 tables:

    bronze  13 rare / 3 normal
    silver  37 rare / 14 normal
    gold    60 rare / 31 normal

Note that gold normal (31) is worth less than silver rare (37). That is the
game's own table, not a mistake here.

QUALITY follows the game's own ordering. The badge table is a standing order --
6000000 is FC Barcelona, then Real Madrid, Bayern, Manchester City, and 6000600
is Drogheda United -- so the position in the range is what says which crest is
the gold one, and the kit and stadium ranges run the same way.

This used to cycle the six grades by index, on the reasoning that nothing in
the data named a gold one. That was wrong, and it showed: index 0 took the
first grade, so **FC Barcelona's badge was bronze non-rare** and Drogheda's
could be gold. Reported from the console 27 August against the transfer market,
and it was equally true of what the packs were handing out -- the two read the
same catalogue.

The range is banded instead, best first: the top sixth is gold rare, then gold,
silver rare, silver, bronze rare, bronze. Deterministic, stable across
rebuilds, and it puts the famous clubs where a player expects to find them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "server" / "fifa14_clubitems.json"
BLANK = REPO / "server" / "fifa14_clubitems_blank.json"


def blank_ids() -> set[int]:
    """Resource ids known to draw NOT FOUND, so a rebuild does not put them back.

    Shared with the server, which reads the same file at load: an id added
    after a bad card comes out of a pack takes effect without a rebuild, and a
    rebuild then agrees with it.
    """
    try:
        listed = json.loads(BLANK.read_text())["blank"]
    except (OSError, ValueError, KeyError):
        return set()
    return {int(x) for x in listed if str(x).isdigit()}

# (kind, anchor asset, first resource, count) -- confirmed against the console.
# (kind, anchor asset, first resource, count) -- the ranges the game's own
# database holds, cross-checked against MarvelcoCode/Impulsum14's `FUTDB/
# items.tsv`, which is that database extracted rather than a claim about how a
# client behaves. Resource ids come from `cards_ng_db` and are the same game on
# either platform, so the PC caveat this project carries elsewhere is weak here
# -- and the boundaries it gives are corroborated by the console.
#
# What the sweep had wrong:
#
#   kits    ran to 6300860 where the database stops at 6300740. The 119 past
#           the end are not kits: 6300772 drew NOT FOUND in a pack on 25 August
#           and it is one of them. Two sources, one boundary.
#   kits    a whole second family was missing. Home kits are asset 14 at
#           6300000; **away kits are asset 15 at 6400000**, 586 of them, and
#           this catalogue had none. `kitsHome` and `kitsAway` are both members
#           of CardsDLL, which is the shape that should have suggested it.
#   stadia  exactly right, 61, and left alone.
#
# Two of that file's boundaries are **not** taken, because this console says
# otherwise and a measurement here beats a database read elsewhere:
#
#   badges  it stops at 6000586; the probe recorded 6000600 rendering and
#           6000625 blank (`docs/club-item-ids.json`). 601 stays.
#   balls   it has eight more, to 8120145, and it also claims 8120137 which
#           this console does not have. That one came out of a pack on 25
#           August as a grey placeholder captioned `*BallName_83` -- the
#           asterisk is the client saying it built the string key and found no
#           string. The probe called it good, and its ball list ends at exactly
#           the served range, the same tell that made its kit list wrong.
#           So the bound moves to 8120136, 46 balls, and the eight past it stay
#           out: untested is not the same as present, and the one id where the
#           file and this console disagree is one the console lost.
#
# The kit boundary is the other way about, and that is why it moves. The probe
# claims 861 good to 6300860 -- but its `good` list is not exhaustive (badges
# record 28 sampled entries against 601 served), 6300772 **drew NOT FOUND in a
# pack on 25 August**, and the database independently stops at 6300740. A
# sighting and a database against a list that looks written rather than probed.
#
# The anchor asset is kept per family rather than taken per item. Impulsum14
# gives every item its own assetId; this console is *measured* rendering a
# badge from its resourceId with asset 241, and a stadium and a ball the same
# way, so changing those on the strength of a PC file would be trading a
# measurement for a guess. Away kits are new and carry the asset that file
# gives them, 15, because there is nothing here to contradict.
FAMILIES = [
    ("kit", 14, 6_300_000, 741),
    ("kit", 15, 6_400_000, 586),
    ("badge", 241, 6_000_000, 601),
    ("stadium", 6, 6_200_000, 61),
    ("ball", 23, 8_120_091, 46),
]

# (tier, rating, rare, discardValue). The ratings put each grade inside its
# tier's band; the discard values are the game's.
# Best first, because the families are ordered best first. Rare outranks
# non-rare inside a tier -- the game's own quick-sell table says so, with a
# rare silver (37) worth more than a plain gold (31).
GRADES = [
    ("gold", 84, 1, 60),
    ("gold", 78, 0, 31),
    ("silver", 72, 1, 37),
    ("silver", 68, 0, 14),
    ("bronze", 58, 1, 13),
    ("bronze", 48, 0, 3),
]

# Balls are not graded like the rest. Every ball in FIFA 14 is silver -- read
# off the console, and consistent with the quick-sell tables listing exactly one
# value for balls where kits, badges and stadiums have six.
#
# They keep an empty tier rather than "silver" so a ball can still come out of a
# bronze or gold pack. Tier gating exists to stop a gold kit dropping in a
# Bronze Pack; applied to a one-grade family it would simply delete balls from
# four pack tiers out of five.
BALL_RATING = 68
BALL_RARE = 0
BALL_DISCARD = 15
BALL_TIER = ""


def build() -> list[dict]:
    skip = blank_ids()
    items: list[dict] = []
    for kind, asset, first, count in FAMILIES:
        for index in range(count):
            if first + index in skip:
                continue
            # Banded by position, not cycled. The families run best-first, so
            # the top sixth of a range is the gold rare band and the bottom
            # sixth is bronze non-rare.
            band = (index * len(GRADES)) // max(1, count)
            tier, rating, rare, discard = GRADES[min(band, len(GRADES) - 1)]
            if kind == "ball":
                tier, rating, rare, discard = (
                    BALL_TIER, BALL_RATING, BALL_RARE, BALL_DISCARD,
                )
            items.append(
                {
                    "itemType": kind,
                    "assetId": asset,
                    "resourceId": first + index,
                    "rating": rating,
                    "rare": rare,
                    "discardValue": discard,
                    "tier": tier,
                }
            )
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    items = build()
    from collections import Counter

    per_kind = Counter(i["itemType"] for i in items)
    per_tier = Counter((i["itemType"], i["tier"]) for i in items)

    print(f"  {len(items)} club items")
    for kind, _asset, first, count in FAMILIES:
        tiers = " ".join(
            f"{t}={per_tier[(kind, t)]}" for t in ("bronze", "silver", "gold")
        )
        print(f"    {kind:<8}{per_kind[kind]:>5}  {first}-{first+count-1}   {tiers}")

    if args.summary:
        return 0
    OUTPUT.write_text(
        json.dumps({"clubitems": items}, separators=(",", ":"))
    )
    print(f"\n  wrote {OUTPUT.relative_to(REPO)} "
          f"({OUTPUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
