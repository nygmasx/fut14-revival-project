#!/usr/bin/env python3
"""Replay a recorded session against a fresh server and report what breaks.

The journals hold every request this console has ever made -- method, path,
query and, since the query values were added, enough of the body to send it
again. That is a regression suite nobody was running.

Two of tonight's fixes were found by reading those files by hand: the watch
list answering 404 because the client spells it `watchList`, and a consumable
applied by its own item id falling through unhandled. Both had been failing for
days in a screen that simply looked empty. Replaying makes that a command
rather than an act of attention.

    tools/replay_journal.py runtime/live-easw-20260812-003032.jsonl
    tools/replay_journal.py --all --quiet

What it cannot do is judge a *response*: a 200 carrying the wrong document
looks exactly like a 200 carrying the right one. It answers one question --
"did anything the console asked for stop being answered" -- and answers it in
seconds.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import importlib.util
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_server():
    """Import the server module with the club save pointed somewhere harmless.

    Importing it builds a live club from `runtime/club-save.json`, and a replay
    posts quick sells and pack purchases. Without this a replay would spend the
    player's coins.
    """
    import os

    scratch = Path(tempfile.mkdtemp(prefix="fifa14-replay-")) / "club-save.json"
    os.environ["FIFA14_CLUB_SAVE"] = str(scratch)
    spec = importlib.util.spec_from_file_location(
        "fifa14_blaze_server", REPO / "server" / "fifa14_blaze_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def requests_in(path: Path):
    """Every HTTP request the journal recorded, in order."""
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") != "identity_http_request":
            continue
        # Requests this tool made on an earlier run are not the console's.
        if event.get("peer") == "127.0.0.1":
            continue
        target = event.get("path") or ""
        if event.get("query"):
            target = f"{target}?{event['query']}"
        body = event.get("body")
        yield event.get("method") or "GET", target, body


# Answers that are the server doing its job, not a regression.
#
#   /tutorials       declined on purpose; the feed is a document this server
#                    cannot shape and forcing it on pointed the login at it
#   400 on an apply  a consumable the replayed club does not own, or a
#                    goalkeeper style aimed at an outfield player
#   409 on a pack    bought with coins a fresh club has not got
#   400 on a squad   delete aimed at the squad the replayed club is fielding.
#                    A replay rebuilds the club from the seed, so squad 1 is
#                    active here whatever was active in the recorded session --
#                    the console that sent this had squad 3 active and the
#                    request was legitimate.
EXPECTED = {
    ("GET", "/tutorials"): {404},
}

SQUAD_DELETE = re.compile(r"^/ut/delete/game/fifa14/squad/\d+$")
# `GET /trade/<id>/offer`, the View Offer screen. A 404 here is deliberate: the
# offer document's shape is unknown, and the one guess tried -- the single-
# auction status document -- froze the console. A recoverable dead button beats
# a power cycle until the shape is found. See docs/TRADE_PILE.md.
TRADE_OFFER = re.compile(r"^/ut/game/fifa14/trade/\d+/offer$")
# The trophy art lives on the disc, in cards0.big. A 404 here is deliberate:
# answering 200 with an empty BIGF told the client the art existed and was
# empty, and no cup ever drew a trophy. See docs/TOURNAMENT_IDS.md.
TROPHY_ARCHIVE = re.compile(r"^/fut/items/images/.*\.big$")


def expected(method: str, target: str, status: int) -> bool:
    path = target.split("?")[0]
    if status in EXPECTED.get((method, path), set()):
        return True
    if status == 404 and method == "GET" and TRADE_OFFER.match(path):
        return True
    if status == 404 and method == "GET" and TROPHY_ARCHIVE.match(path):
        return True
    if status == 400 and "/ut/game/fifa14/item/" in path:
        return True
    if status == 409 and path.endswith("/purchased/items"):
        return True
    if status == 400 and SQUAD_DELETE.match(path):
        return True
    return False


def replay(journals: list[Path], quiet: bool = False) -> int:
    server = load_server()
    temp = tempfile.TemporaryDirectory()
    journal = server.Journal(Path(temp.name) / "journal.jsonl")
    identity = server.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
    identity.start()
    port = identity.server.server_address[1]

    statuses: Counter = Counter()
    failures: list[tuple[str, str, int]] = []

    def send(method: str, target: str, body) -> None:
        payload = body.encode() if isinstance(body, str) else body
        try:
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            client.request(method, target, payload)
            status = client.getresponse().status
            client.close()
        except Exception:  # a dead connection is a failure too
            statuses["erreur"] += 1
            failures.append((method, target, -1))
            return
        statuses[status] += 1
        if status >= 400 and not expected(method, target, status):
            failures.append((method, target, status))

    try:
        # The server journals to stdout as well as to its file, and 6 668
        # replayed requests is 6 668 lines of it between here and the summary.
        with contextlib.redirect_stdout(io.StringIO()):
            for path in journals:
                for method, target, body in requests_in(path):
                    send(method, target, body)
    finally:
        identity.stop()
        temp.cleanup()

    total = sum(statuses.values())
    print(f"{total} requêtes rejouées depuis {len(journals)} journal(aux)")
    for status, count in sorted(statuses.items(), key=lambda item: str(item[0])):
        print(f"   {status}: {count}")
    if failures:
        print(f"\n{len(failures)} en échec:")
        seen = set()
        for method, target, status in failures:
            key = (method, target.split("?")[0], status)
            if key in seen:
                continue
            seen.add(key)
            print(f"   {status} {method} {target[:100]}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("journals", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="every runtime journal")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    journals = list(args.journals)
    if args.all or not journals:
        journals = sorted((REPO / "runtime").glob("live-easw-*.jsonl"))
    if not journals:
        print("aucun journal à rejouer")
        return 1
    return replay(journals, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
