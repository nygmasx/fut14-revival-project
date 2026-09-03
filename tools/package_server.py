#!/usr/bin/env python3
"""Assemble a shippable server package -- and only the server.

The running server needs three Python modules and four JSON files; everything
else in the repo is research, tools, tests, or the club saves themselves. This
copies exactly the runtime set into a tarball, so what ships is the server and
nothing that should not leave this machine: no game files, no club data, no
disassembly, no journals.

    tools/package_server.py --output fifa14-revival-server.tgz

The set is computed the same way `docs/DEPLOY.md` describes it, and pinned by
`tests/test_package_server.py` so a new server dependency cannot be forgotten
here and produce a package that fails to boot on the VPS.
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The three modules the server imports at runtime (fifa14_blaze_server pulls in
# fut_inventory and blaze_tdf), and the four data files they read. If the
# server grows a dependency, it goes here -- and the test will fail until it
# does.
# blaze_tdf lives in tools/ and the server adds tools/ to sys.path (see its
# TOOLS = REPOSITORY/"tools"), so it ships from there and lands in the same
# relative place in the package -- the layout the server expects is preserved.
SERVER_MODULES = [
    "server/fifa14_blaze_server.py",
    "server/fut_inventory.py",
    "tools/blaze_tdf.py",
]
SERVER_DATA = [
    "server/fifa14_cards.json",
    "server/fifa14_clubitems.json",
    "server/fifa14_clubitems_blank.json",
    "server/fifa14_managers.json",
    "server/fifa14_consumables.json",
    "server/fifa14_totw.json",
    "server/icebreakerpacklist.json",
]
SUPPORT = [
    "tools/revival_config.py",
    "fifa14revival.example.ini",
    "deploy/run.sh",
    "deploy/fifa14-revival.service",
    "deploy/DEPLOY.md",
    "NOTICE.md",
    "docs/PLUGIN.md",
]

# Never in the package, even if a glob would catch them. Stated so the intent
# is auditable rather than implicit in what happens to be listed above.
NEVER_SHIP = ["runtime/", "work/", "*.stfs", "default.xex", "*.big"]

TOP = "fifa14-revival-server"


def members() -> list[str]:
    return SERVER_MODULES + SERVER_DATA + SUPPORT


def build(output: Path) -> list[str]:
    included: list[str] = []
    with tarfile.open(output, "w:gz") as tar:
        for relative in members():
            source = REPO / relative
            if not source.exists():
                raise FileNotFoundError(f"missing from repo: {relative}")
            tar.add(source, arcname=f"{TOP}/{relative}")
            included.append(relative)
        # An empty writable runtime dir, so the first boot has somewhere to put
        # clubs and sessions without the operator creating it by hand.
        placeholder = tarfile.TarInfo(f"{TOP}/runtime/.keep")
        placeholder.size = 0
        tar.addfile(placeholder, io.BytesIO(b""))
        included.append("runtime/.keep")
    return included


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "fifa14-revival-server.tgz")
    parser.add_argument("--list", action="store_true", help="print the set and exit")
    args = parser.parse_args(argv)

    if args.list:
        for relative in members():
            print(relative)
        return 0

    included = build(args.output)
    size = args.output.stat().st_size
    print(f"{args.output} : {len(included)} fichiers, {size/1024:.0f} Ko")
    print("jamais inclus :", ", ".join(NEVER_SHIP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
