#!/usr/bin/env python3
"""Turn Impulsum14's generated Blaze SDK into one JSON reference table.

This project has spent a lot of its life guessing the shape of a Blaze
response: which four-character labels a struct carries, in which order, and of
which type.  Every guess cost a console session to test.  Impulsum14 ships a
generated SDK for the same Blaze 13 that FIFA 14 speaks -- 24 component
definitions and 919 TDF classes, each member carrying its label, its type and
its index -- and reading it costs nothing.

The SDK is C#, and we are Python, so nothing here is *run*: it is read once
and flattened into `docs/blaze-sdk-reference.json`, which is what the rest of
this repo consults.  Keeping the extractor rather than only its output means
the table can be rebuilt when their SDK moves, and that a reader can check any
line of it against the source it came from.

    tools/import_blaze_sdk.py ~/somewhere/Impulsum14

The SDK is not vendored: it is Apache-2.0 but it is 14 MB of generated C# for
a platform we do not target, and what we need from it is the table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# new TdfMemberInfo("AggrFlags", "mAggrFlags", 0x8679F200, TdfType.Enum, 0, true), // AGGR
MEMBER = re.compile(
    r'new\s+TdfMemberInfo\(\s*"(?P<name>[^"]*)"\s*,\s*"(?P<field>[^"]*)"\s*,\s*'
    r'(?P<tag>0x[0-9A-Fa-f]+)\s*,\s*TdfType\.(?P<type>\w+)\s*,\s*(?P<index>\d+)\s*,'
    r'\s*\w+\s*\)\s*,\s*//\s*(?P<label>\S+)'
)
FULL_NAME = re.compile(r'GetFullClassName\(\)\s*=>\s*"([^"]+)"')


def read_types(sdk: Path) -> dict[str, list[dict]]:
    """Every TDF class, keyed by its Blaze name, e.g. Blaze::Stats::Foo."""
    types: dict[str, list[dict]] = {}
    for source in sorted((sdk / "Blaze").rglob("*.cs")):
        text = source.read_text(encoding="utf-8", errors="replace")
        full = FULL_NAME.search(text)
        if full is None:
            # Enums and flag types have no members and no full name; they are
            # not structs and nothing here needs to encode one.
            continue
        members = [
            {
                "label": match["label"],
                "type": match["type"],
                "index": int(match["index"]),
                "tag": int(match["tag"], 16),
                "name": match["name"],
            }
            for match in MEMBER.finditer(text)
        ]
        types[full[1]] = sorted(members, key=lambda member: member["index"])
    return types


def read_components(sdk: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Every component keyed by its decimal id, and the server-wide errors.

    `RootComponentBase.json` is the odd one out: it carries no component id
    because it is not a component.  It holds the two error tables every
    component can answer with -- the server's own and the SDK's -- which is
    exactly what a bare numeric error code in our journal needs to be read
    against.
    """
    components: dict[str, dict] = {}
    shared_errors: dict[str, dict] = {}
    for source in sorted((sdk / "Components").glob("*.json")):
        raw = json.loads(source.read_text(encoding="utf-8"))
        if "Id" not in raw:
            shared_errors = {
                "server": raw.get("ServerErrors") or {},
                "sdk": raw.get("SdkErrors") or {},
            }
            continue
        components[str(raw["Id"])] = {
            "name": raw.get("Name"),
            "short": raw.get("ShortName"),
            "methods": {
                str(method["Id"]): {
                    "name": method.get("Name"),
                    "request": method.get("RequestType"),
                    "response": method.get("ResponseType"),
                }
                for method in raw.get("Methods") or []
            },
            "notifications": {
                str(notification["Id"]): {
                    "name": notification.get("Name"),
                    "type": notification.get("Type"),
                }
                for notification in raw.get("Notifications") or []
            },
            "errors": raw.get("ErrorCodes") or {},
        }
    return components, shared_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path,
                        help="a checkout of github.com/MarvelcoCode/Impulsum14")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1]
                        / "docs" / "blaze-sdk-reference.json")
    arguments = parser.parse_args(argv)

    sdk = arguments.checkout / "Impulsum14" / "SDK" / "Blaze3SDK"
    if not (sdk / "Components").is_dir():
        print(f"pas de SDK Blaze3 sous {sdk}", file=sys.stderr)
        return 1

    components, shared_errors = read_components(sdk)
    table = {
        "source": "github.com/MarvelcoCode/Impulsum14 -- SDK/Blaze3SDK (Apache-2.0)",
        "components": components,
        "shared_errors": shared_errors,
        "types": read_types(sdk),
    }
    arguments.output.write_text(
        json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    components = len(table["components"])
    methods = sum(len(one["methods"]) for one in table["components"].values())
    types = len(table["types"])
    members = sum(len(one) for one in table["types"].values())
    print(f"{arguments.output}: {components} composants, {methods} commandes, "
          f"{types} structures, {members} membres")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
