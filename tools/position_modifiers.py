#!/usr/bin/env python3
r"""Teach the twenty position modifiers what they change.

    tools/position_modifiers.py add       # annotate 91-110 with from/to
    tools/position_modifiers.py remove
    tools/position_modifiers.py status

Subtypes 91-110 are position modifiers -- settled 16 August 2026, see
`docs/CONSUMABLES.md`. Knowing that is not enough to apply one: a card that
moves a player has to say which player it moves and where to, and this
catalogue carried neither. So they were refused, correctly, because writing
`preferredPosition` on a guess changes the wrong field on a real card and the
card is spent doing it.

The PC revival's catalogue names every one of them as an explicit transition:

    91 LWB->LB    95 LM->LW     99 LW->LF    103 CM->CAM   107 CAM->CF
    92 LB->LWB    96 RM->RW    100 RW->RF    104 CAM->CM   108 CF->CAM
    93 RWB->RB    97 LW->LM    101 LF->LW    105 CDM->CM   109 CF->ST
    94 RB->RWB    98 RM->RM*   102 RF->RW    106 CM->CDM   110 ST->CF

(* 98 is RW->RM; the column above is set out four to a row, not paired.)

Both catalogues already agree on the definition ids -- 5003059 is subtype 91 in
each -- so this matches on that and writes two members, `from` and `to`, onto
rows that already exist. Nothing is added and nothing is renumbered.

The rule that follows is retail's, not one invented here: a modifier applies
only to a player already in its `from` position. A CM->CAM card on a striker is
refused with the card unspent, which is what the real game does and what the
picker's own filtering implies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOGUE = REPO / "server" / "fifa14_consumables.json"
DEFAULT_SOURCE = REPO / "runtime" / "kyro" / "fifa14-consumable-catalog.v2412.json"

POSITION = range(91, 111)
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


def _transition(kind: str) -> tuple[str, str] | None:
    """`LWB->LB` in whichever arrow the source happens to use."""
    for arrow in ("→", "->", "➔", ">"):
        if arrow in kind:
            start, _, end = kind.partition(arrow)
            start, end = start.strip().upper(), end.strip().upper()
            if start and end:
                return start, end
    return None


def status() -> int:
    rows = _rows(json.loads(CATALOGUE.read_text()))
    block = [r for r in rows if r.get("cardsubtypeid") in POSITION]
    known = [r for r in block if r.get("from") and r.get("to")]
    print(f"  position modifiers in the catalogue: {len(block)}")
    print(f"  carrying a transition:               {len(known)}")
    for row in sorted(known, key=lambda r: r["cardsubtypeid"])[:4]:
        print(f"    {row['cardsubtypeid']}  {row['from']} -> {row['to']}")
    if len(known) > 4:
        print(f"    ... and {len(known) - 4} more")
    return 0


def add(source: Path) -> int:
    if not source.exists():
        print(f"  source not found: {source}")
        return 1
    document = json.loads(CATALOGUE.read_text())
    rows = _rows(document)

    transitions: dict[int, tuple[str, str]] = {}
    for row in _rows(json.loads(source.read_text())):
        subtype = row.get("cardsubtypeid")
        if subtype not in POSITION:
            continue
        moved = _transition(str(row.get("kind") or ""))
        if moved:
            transitions[subtype] = moved

    if len(transitions) != len(POSITION):
        print(f"  expected {len(POSITION)} transitions, read {len(transitions)}")
        return 1

    annotated = 0
    for row in rows:
        moved = transitions.get(row.get("cardsubtypeid"))
        if not moved:
            continue
        row["from"], row["to"] = moved
        row["source"] = SOURCE_NOTE
        annotated += 1

    if not annotated:
        print("  no position rows found to annotate")
        return 1

    _write(CATALOGUE, document)
    print(f"  annotated {annotated} position modifiers")
    for subtype in sorted(transitions)[:3]:
        start, end = transitions[subtype]
        print(f"    {subtype}  {start} -> {end}")
    print(f"    ... through {max(transitions)}  "
          f"{transitions[max(transitions)][0]} -> {transitions[max(transitions)][1]}")
    return 0


def remove() -> int:
    document = json.loads(CATALOGUE.read_text())
    rows = _rows(document)
    cleared = 0
    for row in rows:
        if row.get("cardsubtypeid") in POSITION and "from" in row:
            row.pop("from", None)
            row.pop("to", None)
            cleared += 1
    _write(CATALOGUE, document)
    print(f"  cleared the transition from {cleared} rows -- they refuse again")
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
