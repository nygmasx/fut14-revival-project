#!/usr/bin/env python3
"""The field names EA Sports Football Club speaks, read out of powdllzf.

The module started talking on 2026-08-28 and immediately asked for two
documents this server does not serve -- `configuration` and the persona
profile.  Inventing a shape for either is the failure mode that cost this
project an evening on the stats screens, where a document the client
half-accepted left it waiting forever.  So the shape is read instead.

`configuration` turns out not to be only a URL segment: it sits inside a dense
table of short identifiers in the module's read-only data, among `division`,
`season_played`, `futplayercard`, `content_fileId_career_cup_summary` and some
nine hundred more.  That table is the vocabulary of the documents the module
parses, and it is the honest starting point for answering either read.

    tools/dump_easfc_vocabulary.py work/powdll/powdllzf.rdata.bin

The dump itself comes from `tools/xbox360_xbdm_dump.py`, which reads the
module image over XBDM.  Static image reads are safe on this console; the
heap sweeps are not.  Stop the patch watcher first either way.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# Where the .rdata section of powdllzf.xex.dll is mapped on this console.
RDATA_BASE = 0x89700400

# A run this long is a table rather than a coincidence: the module's own
# strings are dense and aligned, while stray printable bytes are not.
LEAST_INTERESTING_RUN = 40
LONGEST_PLAUSIBLE_NAME = 40
GREATEST_GAP = 8

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PRINTABLE = re.compile(rb"[\x20-\x7e]{3,}\x00")


def runs(data: bytes, base: int) -> list[list[tuple[int, str]]]:
    """The dense runs of identifiers, each as (address, name) pairs."""
    found: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    previous_end: int | None = None
    for match in PRINTABLE.finditer(data):
        name = match.group().rstrip(b"\x00").decode("ascii")
        dense = (
            len(name) <= LONGEST_PLAUSIBLE_NAME
            and IDENTIFIER.fullmatch(name) is not None
        )
        if dense and (previous_end is None or match.start() - previous_end <= GREATEST_GAP):
            current.append((base + match.start(), name))
        else:
            if len(current) > LEAST_INTERESTING_RUN:
                found.append(current)
            current = [(base + match.start(), name)] if dense else []
        previous_end = match.end()
    if len(current) > LEAST_INTERESTING_RUN:
        found.append(current)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dump", type=Path, help="powdllzf.rdata.bin")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=RDATA_BASE)
    arguments = parser.parse_args(argv)

    data = arguments.dump.read_bytes()
    total = 0
    for run in runs(data, arguments.base):
        print(f"--- 0x{run[0][0]:08X}..0x{run[-1][0]:08X}  {len(run)} noms ---")
        for address, name in run:
            print(f"  0x{address:08X}  {name}")
        total += len(run)
    print(f"{total} noms au total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
