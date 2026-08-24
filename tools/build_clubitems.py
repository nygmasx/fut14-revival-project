#!/usr/bin/env python3
"""Build the club-item catalogue from what the console confirmed.

    tools/build_clubitems.py            write server/fifa14_clubitems.json
    tools/build_clubitems.py --summary  report without writing

Four families, each a contiguous run of resource ids read off the club screen
during the probe sessions of 18-19 August 2026:

    kit      6300000-6300860   861
    badge    6000000-6000600   601
    stadium  6200000-6200060    61
    ball     8120091-8120137    47

The resource is what matters. A club item's art resolves from its resourceId,
not its asset -- asset 241 drew FC Barcelona at resource 6000000 and NOT FOUND
at 6900241 -- so every card here holds its family's known-good asset and varies
only the resource. That is exactly the shape the probe rendered 1570 times.

QUICK SELL VALUES are the real ones, from fifauteam's FIFA 14 tables:

    bronze  13 rare / 3 normal
    silver  37 rare / 14 normal
    gold    60 rare / 31 normal

Note that gold normal (31) is worth less than silver rare (37). That is the
game's own table, not a mistake here.

QUALITY is a house choice and says so. Nothing in the data says which kit is
the gold one -- the console gives a name and a picture and no rating at all --
so the six grades are spread deterministically across each family by resource,
which gives every pack tier something of its own to hand out and keeps the
spread stable across rebuilds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "server" / "fifa14_clubitems.json"

# (kind, anchor asset, first resource, count) -- confirmed against the console.
FAMILIES = [
    ("kit", 14, 6_300_000, 861),
    ("badge", 241, 6_000_000, 601),
    ("stadium", 6, 6_200_000, 61),
    ("ball", 23, 8_120_091, 47),
]

# (tier, rating, rare, discardValue). The ratings put each grade inside its
# tier's band; the discard values are the game's.
GRADES = [
    ("bronze", 48, 0, 3),
    ("bronze", 58, 1, 13),
    ("silver", 68, 0, 14),
    ("silver", 72, 1, 37),
    ("gold", 78, 0, 31),
    ("gold", 84, 1, 60),
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
    items: list[dict] = []
    for kind, asset, first, count in FAMILIES:
        for index in range(count):
            tier, rating, rare, discard = GRADES[index % len(GRADES)]
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
