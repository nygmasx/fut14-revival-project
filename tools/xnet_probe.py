#!/usr/bin/env python3
"""Listen for whether a console sends anything at all to the relay.

    tools/xnet_probe.py --port 3074

A probe, not a relay. It opens one UDP socket and records what arrives: where
from, how many bytes, and at what time.

What it settles
---------------
Match traffic does not go through the server. The two consoles talk to each
other directly, over UDP, at the address this server handed each of them for
the other. On 22 August two consoles found each other, entered a match, and
never saw one another -- one reported `STAT=0` on the other. Two home NATs,
France and the United States, and no EA service left to help with traversal.

`FIFA14_PEER_RELAY` rewrites the public address and port in the XNADDR each
console receives for the other, so that the traffic heads for this machine
instead. What is not established is whether the console's kernel honours that
rewrite: an XNADDR also carries twenty bytes of `abOnline`, filled in by the
Xbox LIVE gateway, and nothing published says whether the security association
uses them for routing.

If a single packet arrives here, the answer is yes and a relay is worth
writing. If nothing arrives, `abOnline` matters and the answer is somewhere
else. Half an hour for a binary answer, rather than several hours to discover
the wall at the end.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3074)
    parser.add_argument("--journal", type=Path, default=None,
                        help="where to write what arrives, in addition to the screen")
    arguments = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((arguments.listen, arguments.port))
    print(f"probe: UDP {arguments.listen}:{arguments.port}", flush=True)
    print("waiting -- one packet is enough to answer", flush=True)

    seen: dict[str, int] = {}
    while True:
        try:
            payload, peer = sock.recvfrom(4096)
        except KeyboardInterrupt:
            break
        except OSError:
            continue
        where = f"{peer[0]}:{peer[1]}"
        first = where not in seen
        seen[where] = seen.get(where, 0) + 1
        record = {
            "time": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": "xnet_packet",
            "peer": where,
            "bytes": len(payload),
            "packets_from_peer": seen[where],
            "first_from_peer": first,
            # The first bytes only: the contents are encrypted end to end
            # between the two consoles and are not to be read from here. What
            # matters is that it arrived at all.
            "head": payload[:16].hex().upper(),
        }
        line = json.dumps(record, sort_keys=True)
        if first:
            print(f"\n*** FIRST PACKET from {where} -- the rewrite is honoured",
                  flush=True)
        print(line, flush=True)
        if arguments.journal is not None:
            arguments.journal.parent.mkdir(parents=True, exist_ok=True)
            with arguments.journal.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
