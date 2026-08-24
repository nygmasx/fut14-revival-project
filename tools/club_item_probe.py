#!/usr/bin/env python3
"""Find out which club-item resource ids the console can actually draw.

    tools/club_item_probe.py arm --kind kit --resource-from 6300000 --step 25
    tools/club_item_probe.py record --kind kit --good 6300000-6300860
    tools/club_item_probe.py show

A club item's art resolves from its **resourceId**, not its asset: asset 241
drew FC Barcelona at resource 6000000 and NOT FOUND at 6900241, same asset, one
resource apart. So a sweep varies the resource and pins the asset at a
known-good one.

That includes badges. "A badge asset id is a club id" was a coincidence -- the
four original badges carried resources 6000000-6000003, indices 0 to 3, and
Barcelona's clubId happens to be 241. A badge resource is an index into the
game's own badge table, and index 3 is Manchester City while clubId 3 is
Blackburn Rovers.

`arm` writes a probe roster into `work/club-item-probe.json`, which the server
seeds into the club **instead of** the normal club items when
FIFA14_CLUB_ITEM_PROBE is set. A probe run never writes the save: the seed it
replaces is what the save diffs against, so one investigative launch would
otherwise rewrite the whole club.

A coarse `--step` finds where a range ends in one page-through rather than a
dozen launches. Balls name their own end -- the console draws *BallName_80 past
the last real ball -- while kits, stadiums and badges give a plain NOT FOUND
and need a fine pass to pin the exact edge.

Probe cards go in the CLUB and never in a PACK. The club screen tolerates a
card it cannot draw; the pack reveal is the screen with the freeze history.

The reading is done from screenshots: a card that resolved names itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROSTER = REPO / "work" / "club-item-probe.json"
FINDINGS = REPO / "docs" / "club-item-ids.json"

KINDS = ("kit", "stadium", "ball", "badge")

# The asset a resource sweep holds constant, per family: the first id known to
# render on the console today. A club item's art resolves from its resourceId
# -- asset 241 drew FC Barcelona at resource 6000241 and NOT FOUND at 6900241,
# same asset -- so a sweep moves the resource and pins the asset here.
KNOWN_GOOD_ASSET = {"kit": 14, "stadium": 6, "ball": 23, "badge": 241}


def _spans(text: str) -> list[int]:
    """"1-12,14,17" -> [1..12, 14, 17]."""
    out: list[int] = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            low, high = piece.split("-", 1)
            out.extend(range(int(low), int(high) + 1))
        else:
            out.append(int(piece))
    return sorted(set(out))


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _held_back_clubs() -> list[int]:
    """Obsolete: badges were once picked by club id.

    A badge's crest is an index into the game's own badge table, not a club id,
    so there is no "held back club" to sweep. Kept as a clear error rather than
    a confusing empty roster.
    """
    raise SystemExit(
        "  --band is obsolete: badges are indexed by the game's badge table, "
        "not by club id.\n"
        "  Sweep resources instead:  --kind badge --resource-from 6000000"
    )


def arm(args: argparse.Namespace) -> int:
    roster = _load(ROSTER, {})
    if getattr(args, "band", False):
        if args.kind != "badge":
            print("  --band is for badges only")
            return 1
        ids = _held_back_clubs()
    elif getattr(args, "res_start", None) is not None:
        # A resource sweep: the asset stays at the family's first known-good
        # value and the resource moves, because the art follows the resource.
        anchor = KNOWN_GOOD_ASSET[args.kind]
        step = max(1, int(getattr(args, "step", 1) or 1))
        end = args.res_end if args.res_end is not None else args.res_start + 59 * step
        ids = [[anchor, r] for r in range(args.res_start, end + 1, step)]
    else:
        ids = list(range(args.start, args.end + 1))
    roster[args.kind] = ids
    ROSTER.parent.mkdir(parents=True, exist_ok=True)
    ROSTER.write_text(json.dumps(roster, indent=1))
    if ids and isinstance(ids[0], list):
        print(f"  armed {len(ids)} {args.kind} cards: asset {ids[0][0]}, "
              f"resource {ids[0][1]}-{ids[-1][1]}")
    else:
        print(f"  armed {len(ids)} {args.kind} ids: {ids[0]}-{ids[-1]}")
    print(f"  wrote {ROSTER.relative_to(REPO)}")
    print()
    print("  Launch with the probe on:")
    print("    FIFA14_CLUB_ITEM_PROBE=1 tools/fut.sh")
    print()
    print(f"  Then open My Club and filter to {args.kind}.")
    if ids and isinstance(ids[0], list):
        print(f"  The cards are in resource order: the Nth card is resource")
        print(f"  {ids[0][1]} + N - 1. A card that renders names itself; one")
        print("  that does not draws NOT FOUND or a blank back.")
    else:
        print(f"  The Nth card is asset {ids[0]} + N - 1.")
    return 0


def record(args: argparse.Namespace) -> int:
    findings = _load(FINDINGS, {})
    entry = findings.setdefault(args.kind, {"good": [], "blank": []})
    entry["good"] = sorted(set(entry["good"]) | set(_spans(args.good or "")))
    entry["blank"] = sorted(set(entry["blank"]) | set(_spans(args.blank or "")))
    # An id cannot be both; the later reading wins.
    entry["good"] = [i for i in entry["good"] if i not in set(entry["blank"])]
    FINDINGS.parent.mkdir(parents=True, exist_ok=True)
    FINDINGS.write_text(json.dumps(findings, indent=1, sort_keys=True))
    print(f"  {args.kind}: {len(entry['good'])} resolve, {len(entry['blank'])} blank")
    print(f"  wrote {FINDINGS.relative_to(REPO)}")
    return 0


def show(args: argparse.Namespace) -> int:
    findings = _load(FINDINGS, {})
    if not findings:
        print("  nothing recorded yet")
        return 0
    for kind in sorted(findings):
        entry = findings[kind]
        good, blank = entry.get("good", []), entry.get("blank", [])
        print(f"  {kind:<9}{len(good):>4} resolve  {len(blank):>4} blank")
        if good:
            print(f"    good:  {_compress(good)}")
        if blank:
            print(f"    blank: {_compress(blank)}")
    return 0


def _compress(ids: list[int]) -> str:
    """[1,2,3,5] -> '1-3,5'."""
    if not ids:
        return ""
    parts, start, previous = [], ids[0], ids[0]
    for value in ids[1:] + [None]:  # type: ignore[list-item]
        if value is not None and value == previous + 1:
            previous = value
            continue
        parts.append(f"{start}-{previous}" if previous > start else f"{start}")
        if value is None:
            break
        start = previous = value
    return ",".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    armed = subs.add_parser("arm", help="write a probe roster for one kind")
    armed.add_argument("--kind", choices=KINDS, required=True)
    armed.add_argument("--from", dest="start", type=int, default=1)
    armed.add_argument("--to", dest="end", type=int, default=60)
    armed.add_argument(
        "--band", action="store_true",
        help="badges only: sweep the held-back club ids rather than a range",
    )
    armed.add_argument(
        "--resource-from", dest="res_start", type=int, default=None,
        help="sweep resourceIds from here, holding the asset at a known-good "
             "one. A club item's art resolves from its resourceId, so this is "
             "the sweep that finds new artwork.",
    )
    armed.add_argument(
        "--resource-to", dest="res_end", type=int, default=None,
        help="last resourceId in the sweep",
    )
    armed.add_argument(
        "--step", type=int, default=1,
        help="stride through the resource range. A coarse step finds where the "
             "art stops in one page-through instead of a dozen launches; the "
             "game's own placeholder names its index (*BallName_80), so one "
             "card past the end gives the whole mapping.",
    )
    armed.set_defaults(func=arm)

    said = subs.add_parser("record", help="record what the console showed")
    said.add_argument("--kind", choices=KINDS, required=True)
    said.add_argument("--good", help="ids that rendered, e.g. 1-12,14")
    said.add_argument("--blank", help="ids that drew a card back")
    said.set_defaults(func=record)

    listing = subs.add_parser("show", help="what is known so far")
    listing.set_defaults(func=show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
