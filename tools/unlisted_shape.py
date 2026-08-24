#!/usr/bin/env python3
"""Change how a card awaiting listing is shaped, without relaunching.

    tools/unlisted_shape.py            what is being served now
    tools/unlisted_shape.py prices     serve the next candidate
    tools/unlisted_shape.py --list     every candidate, and what it changes

The standalone Transfer List screen says "This item is not currently listed.
Press (A) to list this item." and then pressing A sends no request at all -- so
the client is refusing to open the price dialog on the data it has, before any
network call. The entry already matches Kyro's build field for field, so every
candidate here is something Kyro does not send either.

The shape is read on every trade-pile request and the console re-fetches that
route whenever the Transfer List is opened: set it, back out of the screen, go
back in.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILE = REPO / "runtime" / "unlisted-shape.txt"

CANDIDATES = {
    "plain": "the measured shape: a trade id of its own, and tradeState "
             "expired. Both are needed -- the id is how the screen resolves the "
             "row to a card, the state is what gives it actions. See "
             "UNLISTED_TRADE_ID_BASE in server/fut_inventory.py.",
}

# The ten coded candidates are retired: prices, forsale, itemid, duration, club,
# listinglike, asitwas, barecard, emptystate, tradeid. Nine were eliminated
# against the console and the tenth is the default now. `asitwas` and `barecard`
# are worth remembering for the method rather than the result -- they restored
# the entry, and then the card, to the shape of the window the press was last
# seen working in, and clearing both is what left the trade id as the only thing
# it could be.
#
# What is still open is presentation, not function: the row is served as a
# lapsed auction, so the card draws in the expired tint and the panel says "No
# buyer was found for this item" over a card that was never listed. Candidates
# for that live in runtime/unlisted-shapes.json.

# Candidates written as data rather than code, in `runtime/unlisted-shapes.json`.
# The server reads that file per request, so one of these is live when saved --
# no restart, and therefore no relaunch. See `custom_unlisted_shapes` in
# server/fut_inventory.py for the format.
SHAPES_FILE = REPO / "runtime" / "unlisted-shapes.json"


def known() -> dict[str, str]:
    """Every candidate that can be served: the coded ones and the written ones."""
    everything = dict(CANDIDATES)
    try:
        written = json.loads(SHAPES_FILE.read_text())
    except (OSError, ValueError):
        return everything
    if not isinstance(written, dict):
        return everything
    for name, spec in written.items():
        base = (spec or {}).get("base") if isinstance(spec, dict) else None
        everything[name] = (
            f"written in {SHAPES_FILE.name}"
            + (f", on top of {base}" if base else "")
        )
    return everything


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a]
    candidates = known()
    if args and args[0] in ("--list", "-l"):
        width = max(len(name) for name in candidates)
        for name, why in candidates.items():
            print(f"  {name:<{width}}  {why}")
        return 0

    if not args:
        current = FILE.read_text().strip() if FILE.exists() else "plain"
        print(f"  serving: {current}")
        print(f"  {candidates.get(current, 'unknown candidate')}")
        rest = [n for n in candidates if n != current]
        print(f"\n  next:    {' '.join(rest)}")
        return 0

    choice = args[0].strip().lower()
    if choice not in candidates:
        print(f"  unknown candidate: {choice}")
        print(f"  known: {', '.join(candidates)}")
        return 1
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(choice + "\n")
    print(f"  serving: {choice}")
    print(f"  {candidates[choice]}")
    _warn_if_stale(choice)
    return 0


def _console_session() -> str:
    """The X-UT-SID the console is using, from the newest journal."""
    journals = sorted((REPO / "runtime").glob("live-easw-*.jsonl"))
    if not journals:
        return ""
    try:
        for line in journals[-1].open():
            headers = (json.loads(line).get("headers") or {})
            if headers.get("X-UT-SID"):
                return str(headers["X-UT-SID"])
    except (OSError, ValueError):
        return ""
    return ""


def _warn_if_stale(choice: str) -> None:
    """Say so if the running server cannot read this file.

    The switch is read by the *server*, so a server started before the reading
    code existed ignores it silently -- and a candidate then reads as "no
    change" when it was never served at all. Two candidates were judged that
    way before anyone thought to check. Asking the live server what it is
    actually serving is the only honest confirmation.
    """
    go = "\n  On the console: back out of the Transfer List, then open it again."
    # The session id matters: without it the request lands on an empty tenant
    # and the pile comes back with nothing to check, which reads as "fine".
    try:
        url = "http://127.0.0.1:18080/ut/game/fifa14/tradePile"
        request = urllib.request.Request(url)
        session = _console_session()
        if session:
            request.add_header("X-UT-SID", session)
        with urllib.request.urlopen(request, timeout=5) as reply:
            served = json.loads(reply.read().decode())
    except Exception:
        print(go)
        return

    # An unlisted row is one carrying no auction state, whatever this
    # candidate calls that. Neither `tradeId == 0` nor `tradeState ==
    # "inactive"` will do: `tradeid` changes the first and `asitwas` and
    # `emptystate` change the second, so either filter drops exactly the rows
    # its own candidate needs checked and reads as "nothing to say" over a
    # candidate that was working.
    unlisted = [
        e for e in served.get("auctionInfo") or []
        if e.get("tradeState") not in ("active", "closed", "expired")
    ]
    if not unlisted:
        print(go)
        return

    # Only the candidates that change something visible on the wire are
    # detectable from outside; they are enough to tell a stale server from a
    # live one.
    stale = (
        (choice in {"prices", "listinglike"} and unlisted[0].get("startingBid") == 0)
        or (choice == "tradeid" and not unlisted[0].get("tradeId"))
        or (choice in {"asitwas", "emptystate", "barecard"}
            and unlisted[0].get("tradeState") != "")
        or (choice == "barecard"
            and "itemId" in (unlisted[0].get("itemData") or {}))
    )
    if not stale:
        print(go)
        return

    print("")
    print("  !! The running server is NOT applying this. It started before the")
    print("     switch existed, so it cannot read this file, and the candidate")
    print("     would read as 'no change' having never been served at all.")
    print("     Relaunch first:  tools/fut.sh")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
