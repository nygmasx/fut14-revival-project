#!/usr/bin/env python3
r"""Pull CardsDLL's mapped image off the console, for reading offline.

    tools/dump_cardsdll.py                 the whole module
    tools/dump_cardsdll.py --from 0x60000  resume from an offset

The module only exists once Ultimate Team has been entered -- at the dashboard
it is not loaded at all -- and it maps at 0x89000000, 0x2B0000 bytes.

This is what settled the questions no amount of guessing could. The JSON member
table lives in the first 384 KB and gave `style`, `gamesPlayed`, `won`/`draw`/
`loss`, `totalCredits`, and the fact that `completionAward`, `skillAward` and
`goals` are not members of this binary at all. What is still unread is the code
-- and the open question now is what draws a card's chemistry style label,
which is a disassembly job rather than a string search.

Reading a mapped module's static image is not the heap sweep that once dropped
this console off the network: it is bounded, sequential, and touches nothing the
title writes. It still costs a few minutes of XBDM traffic, so it is worth doing
while the console is idle on a menu rather than mid-match.

Written incrementally, so an interrupted run resumes with `--from` instead of
starting over.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = 0x89000000
SIZE = 0x2B0000
OUTPUT = REPO / "work" / "cardsdll.bin"


class Xbdm:
    def __init__(self, host: str, port: int = 730, timeout: float = 20.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.reader = self.sock.makefile("rb")
        banner = self.reader.readline()
        if not banner.startswith(b"201-"):
            raise RuntimeError(f"unexpected banner: {banner!r}")

    def memory(self, address: int, length: int) -> bytes:
        self.sock.sendall(
            f"getmem addr=0x{address:08X} length=0x{length:X}\r\n".encode("ascii")
        )
        status = self.reader.readline()
        if not status.startswith(b"202-"):
            raise RuntimeError(status.decode(errors="replace").strip())
        out = bytearray()
        while True:
            line = self.reader.readline().rstrip(b"\r\n")
            if line == b".":
                break
            try:
                out += bytes.fromhex(line.decode("ascii"))
            except ValueError:
                # Unreadable pages come back as '??' rather than hex. Keep the
                # offsets honest by padding: a short read here would shift every
                # address after it, and every address in this project's notes is
                # relative to the module base.
                out += b"\x00" * (len(line) // 2)
        return bytes(out)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", nargs="?", default=None)
    parser.add_argument("--from", dest="start", type=lambda v: int(v, 0), default=0)
    parser.add_argument("--chunk", type=lambda v: int(v, 0), default=0x2000)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    host = args.host
    if host is None:
        sys.path.insert(0, str(REPO / "tools"))
        import revival_config

        host = revival_config.value("console.address")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+b" if args.output.exists() and args.start else "wb"
    client = Xbdm(host)
    started = time.time()
    try:
        with open(args.output, mode) as handle:
            if args.start:
                handle.seek(args.start)
            for offset in range(args.start, SIZE, args.chunk):
                length = min(args.chunk, SIZE - offset)
                handle.write(client.memory(BASE + offset, length))
                if offset % 0x40000 == 0:
                    done = offset + length
                    rate = done / max(1e-6, time.time() - started) / 1024
                    print(
                        f"  0x{offset:06X}  {done:,}/{SIZE:,} bytes  {rate:.0f} KB/s",
                        flush=True,
                    )
    finally:
        client.close()
    print(f"  done: {args.output} ({args.output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
