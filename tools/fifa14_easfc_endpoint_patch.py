#!/usr/bin/env python3
"""Point the EAS FC session and catalogue at the local server.

The header banner reads "EAS FC non connecté", and after a while the main menu
says outright: *Vous avez perdu la connexion avec les serveurs EA. Vous ne
pourrez accéder aux fonctionnalités...*

That is a **second** Blaze connection, from `powdllzf.xex.dll`, to endpoints of
its own:

    0x89706250   pal.gt.easfc.ea.com:8094       the session
    0x897061B0   content.lt.easfc.ea.com:8080   the catalogue

Neither hostname is among the four the launch patch rewrites in `default.xex`,
so the client resolves them for real, reaches nothing, and reports the banner.
Nothing appears in the journal because the traffic never comes near us.

`docs/EASFC_NOT_CONNECTED.md` tried serving `ONLINE/POW_CUSTOMURL` and its four
siblings through `OSDK_CORE` instead, and left it unverified. It is verified
now, and it did not work: both strings were read back from a running title on
12 August still holding their retail values. The module does not take the
configuration, or does not take it in time.

So the hostnames are rewritten in the image, which is what the launch patch
already does for the other four. Both replacements are shorter than what they
replace, so nothing moves and the following string is untouched.

This has to run **before** the POW module connects, which is a few seconds into
the title's life -- hence the polling: `powdllzf` is not mapped at the instant
the title resumes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import revival_config  # noqa: E402
from fifa14_plain_send_hook import Xbdm  # noqa: E402

SESSION = (0x89706250, b"pal.gt.easfc.ea.com:8094")
CATALOGUE = (0x897061B0, b"content.lt.easfc.ea.com:8080")


def patch(host: str, local: str, core_port: int, identity_port: int) -> bool:
    # The session endpoint goes to the HTTP port, not the Blaze one.
    #
    # It was `{local}:{core_port}` until 28 August, and the retail value it
    # replaces says why that is wrong: `pal.gt.easfc.ea.com:8094` is an HTTP
    # endpoint. POW speaks HTTP here -- `POST /pow/auth`, then the hub -- and
    # the core port is Blaze, where nothing answers it.
    #
    # The PS3 line found the same thing from the other side and wrote it down:
    # sending POW at the Blaze core port means "the request leaves the console
    # and is never seen again". That console has EASFC connected today, with
    # 1,178 `/pow/` requests in its journal, and this one has never made a
    # single one.
    #
    # It also explains what the trace found and could not place. POW never
    # opened a socket because 10041 was already open -- it is the Blaze
    # connection -- so there was never a new one to see. And the failure was
    # `TXT_EASFC_SERVER_ERROR` rather than a sign-in gate, which is what a
    # module that ran, sent, and got nothing readable back reports.
    #
    # `core_port` stays in the signature: every caller passes it, and the
    # budget check below is the only reason this ever fitted -- 16 bytes of
    # `10.0.0.119:18080` into 24 of `pal.gt.easfc.ea.com:8094`.
    replacements = (
        # `http://`, and it is the difference between EAS FC working and not.
        #
        # The session string was written scheme-less for months, matching the
        # retail value it replaces -- `pal.gt.easfc.ea.com:8094` carries no
        # scheme either. The module formats it as `"%s/%s"` with `pow/auth`,
        # so what reached the transport was `10.0.0.119:18080/pow/auth`, and
        # default.xex's ProtoHttp will not open a socket for that. No
        # connection, no error, a 40-second retry window that doubled on each
        # attempt, and a Connect button that appeared to do nothing.
        #
        # Adding the scheme produced `POST /pow/auth` on the first press --
        # the first EAS FC request this console has ever made. 23 bytes into
        # a 24-byte budget, with nothing to spare.
        (SESSION, f"http://{local}:{identity_port}".encode()),
        (CATALOGUE, f"http://{local}:{identity_port}".encode()),
    )
    client = Xbdm(host)
    try:
        written = 0
        for (address, original), replacement in replacements:
            current = client.read(address, len(original) + 1)
            if current[: len(replacement)] == replacement:
                written += 1
                continue
            # Either the retail string, or one this patcher wrote earlier.
            #
            # Only the retail value was accepted until 28 August, which made
            # the endpoint unrepointable on a running title: the session
            # string was found holding `10.0.0.119:10041` from a previous
            # launch, did not match the retail host, and the patcher refused
            # rather than correcting it. That is exactly the case worth
            # handling -- the value is wrong and this tool exists to fix it.
            #
            # An address this patcher wrote is recognisable without guessing:
            # it starts with the local IP. Anything else is still refused,
            # because writing over content nobody recognises is how a live
            # title gets corrupted.
            settled = current[: len(original)]
            ours = settled.startswith(local.encode()) or settled.startswith(
                f"http://{local}".encode()
            )
            if settled != original and not ours:
                print(
                    f"0x{address:08X}: unexpected content "
                    f"{settled!r}; nothing written"
                )
                return False
            if len(replacement) > len(original):
                print(f"0x{address:08X}: replacement is longer; nothing written")
                return False
            client.write(
                address,
                replacement + b"\x00" * (len(original) + 1 - len(replacement)),
            )
            if client.read(address, len(replacement)) != replacement:
                print(f"0x{address:08X}: verification failed")
                return False
            written += 1
        return written == len(replacements)
    finally:
        try:
            client.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    # Defaults come from fifa14revival.ini; see tools/revival_config.py.
    parser.add_argument("--local-ip", default=None)
    parser.add_argument("--core-port", type=int, default=None)
    parser.add_argument("--identity-port", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    if args.local_ip is None:
        args.local_ip = revival_config.server_host()
    if args.core_port is None:
        args.core_port = revival_config.port("server.core_port")
    if args.identity_port is None:
        args.identity_port = revival_config.port("server.identity_port")

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            if patch(args.host, args.local_ip, args.core_port, args.identity_port):
                print(
                    "Verified: EAS FC session and catalogue point at "
                    f"{args.local_ip}"
                )
                return 0
        except Exception:
            pass          # powdllzf is not mapped yet
        time.sleep(2)
    print("Error: powdllzf was not mapped before timeout")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
