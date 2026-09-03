#!/usr/bin/env python3
"""Emit the complete FIFA 14 patch table as JSON, for the Dashlaunch plugin.

`docs/RELEASE.md` says the plugin is "a transcription, not a rediscovery". This
is what makes that true: rather than copy addresses and bytes into C by hand --
where they would drift the first time a patcher changed -- the plugin builds
from this manifest, and this manifest is produced by importing the very modules
that patch the live console today. If a patcher moves an address, the manifest
moves with it, and a diff shows up here rather than as a silently broken plugin.

    tools/extract_patch_manifest.py --ip 203.0.113.10 --core-port 10041 \
        --identity-port 18080 > plugin/patches.json

The server address is baked into two places -- the connect redirect stub and
the EAS FC endpoint strings -- so the manifest is parameterised by it. A plugin
that resolves a hostname at boot re-runs the address-dependent parts itself;
everything else is fixed for the supported build.

Three stages, applied in this order, exactly as tools/fut.sh applies them:

  1. launch     fixed addresses in default.xex and its code caves, written on
                the module-load notification before game code runs
  2. easfc      two strings in powdllzf, once that module is mapped
  3. tu3        three branches in the helperFunctions APT, which is pattern
                located because the title loads it more than once
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _hex(b: bytes) -> str:
    return b.hex().upper()


def stage_launch(ip_int: int, ip: str, identity_port: int) -> dict:
    import fifa14_connect_bypass as CB
    import fifa14_connect_journal as CJ
    import fifa14_connect_redirect as CR
    import fifa14_early_local_server as E
    import fifa14_fut_resource_url_trace as FR
    import fifa14_redirector_profile_patch as RP
    import fifa14_xnet_startup_patch as XN

    # The cave at CONNECT_STUB (0x83C8E600) receives the 236-byte,
    # IP-carrying stub -- CR.build_stub(ip), the same object the launcher
    # writes there at fifa14_early_local_server.py:623. CB.build_stub() is a
    # different, shorter variant that is not the one installed; do not use it.
    connect_stub = CR.build_stub(ip_int)

    # The launcher arms this under --redirect-fut-resource, which tools/fut.sh
    # passes on every single run. It carries the address a second time.
    fut_url = f"http://{ip}:{identity_port}/futBoot.xml"
    fut_stub = FR.build_stub(fut_url)

    # Guarded writes: each names the exact original bytes the plugin must see
    # before it writes, so it refuses a wrong build instead of corrupting one.
    sites = [
        {
            "name": "connect_hook",
            "address": CB.CONNECT_CALLSITE,
            "expect": _hex(CB.ORIGINAL_CONNECT_CALL),
            "write": _hex(CJ.PATCHED_CONNECT_CALL),
            "note": "redirect only Blaze connects to the local server",
        },
        {
            "name": "ticket_hook",
            "address": E.TICKET_SITE,
            "expect": _hex(E.TICKET_ORIGINAL),
            "write": _hex(E.TICKET_PATCH),
            "note": "branch to the offline-ticket stub",
        },
        {
            "name": "xnet_nosecure",
            "address": XN.NOSECURE_MODE_BRANCH,
            "expect": _hex(XN.NOSECURE_MODE_ORIGINAL),
            "write": _hex(XN.NOSECURE_MODE_PATCHED),
            "note": "take the nosecure path",
        },
        {
            "name": "xnet_bypass",
            "address": XN.XNET_BYPASS_BRANCH,
            "expect": _hex(XN.XNET_BYPASS_ORIGINAL),
            "write": _hex(XN.XNET_BYPASS_PATCHED),
            "note": "skip the secure-XNet gate",
        },
        {
            # The cards and their art are read off the console's own disk
            # rather than sent by the server, and this is the patch that makes
            # that true: the native FUT downloader's futBoot.xml URL is
            # rewritten to the local HTTP service. Without it a plugin comes up
            # with NOT FOUND art on every card -- the failure looks like a
            # server problem and is not one.
            "name": "fut_resource_hook",
            "address": FR.SITE,
            "expect": _hex(FR.ORIGINAL),
            "write": _hex(FR.PATCH),
            "note": "route native futBoot.xml loading to the local service",
        },
    ]

    # Code caves: written first, so the hooks above have something to branch to.
    # connect_stub carries the server IP -- rebuild it when the address changes.
    caves = [
        {"name": "connect_stub", "address": CB.CONNECT_STUB,
         "bytes": _hex(connect_stub), "carries_ip": True},
        {"name": "connect_log", "address": CB.CONNECT_LOG, "bytes": _hex(bytes(0x3C))},
        {"name": "ticket_stub", "address": E.TICKET_STUB, "bytes": _hex(E.TICKET_STUB_BYTES)},
        # The ticket stub loads r4 from here. It is a separate cave, not the
        # tail of the stub's own, and a plugin that writes the code without the
        # data hands the title a pointer into whatever was there.
        {"name": "ticket_dummy", "address": E.TICKET_DUMMY,
         "bytes": _hex(E.TICKET_DUMMY_BYTES)},
        {"name": "connect_result_stub", "address": CR.CONNECT_RESULT_STUB,
         "bytes": _hex(CR.CONNECT_RESULT_STUB_BYTES)},
        {"name": "socket_security_stub", "address": CR.SOCKET_SECURITY_STUB,
         "bytes": _hex(CR.SOCKET_SECURITY_STUB_BYTES)},
        {"name": "fut_resource_journal", "address": FR.JOURNAL,
         "bytes": _hex(bytes(FR.JOURNAL_SIZE))},
        # Second place the server address is baked in: the replacement URL sits
        # inside this cave, at `url_offset`, so a plugin that resolves a name
        # at boot rewrites it there rather than rebuilding the stub.
        {"name": "fut_resource_stub", "address": FR.STUB,
         "bytes": _hex(fut_stub), "carries_ip": True,
         "url_offset": FR.REDIRECT_URL - FR.STUB,
         "url_capacity": FR.STUB_END - FR.REDIRECT_URL,
         "url": fut_url},
    ]

    pointer = {
        "name": "redirector_profile",
        "address": RP.PROFILE_POINTER,
        "write_word": RP.STANDARD_INSECURE,
        "note": "point the redirector at the plaintext-friendly profile",
    }

    return {
        # This is now the whole launch patch, and it was settled by reading the
        # launcher rather than by another launch.
        #
        # The trace stubs that made this uncertain -- postauth_dispatch,
        # login_callback, useradded, connection_result, and the auth2 config
        # pair -- all live inside `arm_login_flow_traces`, which the launcher
        # calls only under `--trace-login-flow`. That flag is `store_true`, and
        # `tools/fut.sh` does not pass it. So not one of them is applied in the
        # configuration that actually works, and a plugin that writes them is
        # patching sites the working console does not have patched.
        #
        # The same reading found the table short in the other direction, which
        # is the half that would have cost a build:
        #
        #   * `ticket_dummy` -- the data cave the ticket stub points r4 at,
        #     written unconditionally beside the code cave, and absent here;
        #   * the native FUT-resource redirect -- `--redirect-fut-resource`,
        #     which fut.sh passes on every run. It is what makes the cards and
        #     their art load from the console's own disk, and it carries the
        #     server address a second time. A plugin without it draws NOT FOUND
        #     on every card, which reads as a server fault and is not one.
        #
        # What is in this table is exactly what fut.sh writes, no more and no
        # less. What still needs a console is whether the plugin's hook fires
        # at the same instant XBDM's modload notification does -- a question
        # about timing, not about which bytes.
        "complete": True,
        "settled_by": "tools/fifa14_early_local_server.py + the flags "
                      "tools/fut.sh passes; --trace-login-flow is not one",
        "caves": caves,
        "sites": sites,
        "pointer": pointer,
    }


def stage_easfc(ip: str, core_port: int, identity_port: int) -> dict:
    import fifa14_easfc_endpoint_patch as EF

    session_addr, session_orig = EF.SESSION
    catalogue_addr, catalogue_orig = EF.CATALOGUE
    # The HTTP port, not the Blaze one -- see the note in
    # fifa14_easfc_endpoint_patch.patch. `core_port` is left in the signature
    # so every caller of this manifest keeps working unchanged.
    session_new = f"http://{ip}:{identity_port}".encode()
    catalogue_new = f"http://{ip}:{identity_port}".encode()
    return {
        "note": "in powdllzf, in place; refuses a replacement longer than the "
                "original, so the server address has a hard length budget",
        "strings": [
            {
                "name": "easfc_session",
                "address": session_addr,
                "expect": session_orig.decode(),
                "budget": len(session_orig),
                "write": session_new.decode(),
                "fits": len(session_new) <= len(session_orig),
            },
            {
                "name": "easfc_catalogue",
                "address": catalogue_addr,
                "expect": catalogue_orig.decode(),
                "budget": len(catalogue_orig),
                "write": catalogue_new.decode(),
                "fits": len(catalogue_new) <= len(catalogue_orig),
            },
        ],
    }


def stage_tu3() -> dict:
    import fifa14_tu3_helperfunctions_runtime_patch as T

    branches = []
    contexts = {co: (before, after) for co, before, after in T.CONTEXTS}
    for offset, expected, replacement in T.PATCHES:
        before, after = contexts.get(offset, (b"", b""))
        branches.append({
            "apt_offset": offset,
            "expect": _hex(expected),
            "write": _hex(replacement),
            "context_before": _hex(before),
            "context_after": _hex(after),
        })
    return {
        "note": "the APT is located by SIGNATURE, not a fixed address, because "
                "the title loads helperFunctions more than once; a plugin "
                "hooks the load instead of scanning",
        "signature": _hex(T.SIGNATURE),
        "signature_to_apt": -T.SIGNATURE_OFFSET
        if hasattr(T, "SIGNATURE_OFFSET") else None,
        "branches": branches,
    }


def build(ip: str, core_port: int, identity_port: int) -> dict:
    ip_int = int(ipaddress.IPv4Address(ip))
    import fifa14_early_local_server as E  # noqa: F401 -- validates the import path
    return {
        "build": {
            "title": "FIFA 14 Xbox 360",
            "default_xex_timestamp": "0x534C8977",
            "runtime_base": "0x82000000",
            "note": "every address here is specific to this build",
        },
        "server": {"ip": ip, "core_port": core_port, "identity_port": identity_port},
        "stage1_launch": stage_launch(ip_int, ip, identity_port),
        "stage2_easfc": stage_easfc(ip, core_port, identity_port),
        "stage3_tu3": stage_tu3(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default="192.168.1.40")
    parser.add_argument("--core-port", type=int, default=10041)
    parser.add_argument("--identity-port", type=int, default=18080)
    args = parser.parse_args(argv)
    manifest = build(args.ip, args.core_port, args.identity_port)
    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")

    # A length overrun on the EAS FC strings is silent in JSON but fatal on the
    # console, so it is called out on stderr where a build script will see it.
    for s in manifest["stage2_easfc"]["strings"]:
        if not s["fits"]:
            sys.stderr.write(
                f"WARNING: {s['name']} needs {len(s['write'])} chars, "
                f"budget is {s['budget']} -- the plugin must relocate this "
                f"string rather than write it in place\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
