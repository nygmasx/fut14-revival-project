"""The plugin builds from this manifest, so a silent drift here ships a broken
plugin. These pin the shape and the invariants, not the exact bytes -- the
bytes come from the patch modules and are allowed to move with them."""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import extract_patch_manifest as M  # noqa: E402


def test_the_manifest_has_all_three_stages() -> None:
    m = M.build("192.168.1.40", 10041, 18080)
    assert set(m) >= {"stage1_launch", "stage2_easfc", "stage3_tu3", "server", "build"}
    assert m["build"]["default_xex_timestamp"] == "0x534C8977"


def test_every_launch_hook_names_the_bytes_it_expects() -> None:
    # A hook that writes without checking the original bytes corrupts a wrong
    # build. Every one must carry both.
    m = M.build("192.168.1.40", 10041, 18080)
    sites = m["stage1_launch"]["sites"]
    assert sites, "no launch hooks emitted"
    for site in sites:
        assert site["expect"] and site["write"]
        assert len(bytes.fromhex(site["expect"])) >= 4
        assert isinstance(site["address"], int)


def test_the_ip_actually_changes_the_manifest() -> None:
    # If the server address did not flow into the bytes, a plugin built for one
    # server would talk to another. The connect redirect stub carries it.
    a = M.build("192.168.1.40", 10041, 18080)
    b = M.build("10.20.30.40", 10041, 18080)

    def carriers(man):
        # Two caves carry it: the connect redirect stub and the FUT-resource
        # redirect, whose replacement URL holds the address a second time.
        # Checking only the first would let the second silently keep a stale
        # server, and the symptom would be NOT FOUND art rather than a
        # connection error -- which points at the wrong half of the system.
        return {c["name"]: c["bytes"] for c in man["stage1_launch"]["caves"]
                if c.get("carries_ip")}

    assert set(carriers(a)) == {"connect_stub", "fut_resource_stub"}
    for name, bytes_a in carriers(a).items():
        assert bytes_a != carriers(b)[name], f"{name} did not follow the address"
    # And the EAS FC strings carry it too.
    assert a["stage2_easfc"]["strings"] != b["stage2_easfc"]["strings"]


def test_the_easfc_session_slot_is_nearly_full() -> None:
    # In-place rewrite: a replacement longer than the original cannot be
    # written, and the patcher refuses rather than overrunning into the string
    # that follows.
    #
    # The session slot is 24 bytes and `http://` takes seven of them, which
    # leaves seventeen for `<ip>:<port>`. That is not much:
    #
    #     http://10.0.0.119:18080        23   fits, by one byte
    #     http://192.168.1.40:18080      25   does not fit
    #     http://255.255.255.255:18080   28   does not fit
    #
    # So most home LANs cannot point EAS FC at port 18080 at all. The way out
    # is a shorter identity port -- omitting it entirely leaves
    # `http://255.255.255.255` at 22 bytes, which always fits, at the cost of
    # having to serve EAS FC on port 80. Nothing here does that yet; the limit
    # is asserted so it is found in a test rather than on a console.
    for ip, expected in (
        ("10.0.0.119", True),
        ("192.168.1.40", False),
        ("255.255.255.255", False),
    ):
        session = next(
            s for s in M.build(ip, 10041, 18080)["stage2_easfc"]["strings"]
            if s["name"] == "easfc_session"
        )
        assert session["fits"] is expected, f"{ip}: {session['write']}"
        assert session["budget"] == 24

    # The catalogue slot is 28 bytes and has always carried the scheme, so it
    # has room the session slot does not.
    catalogue = next(
        s for s in M.build("255.255.255.255", 10041, 18080)["stage2_easfc"]["strings"]
        if s["name"] == "easfc_catalogue"
    )
    assert catalogue["fits"]


def test_the_session_slot_cannot_hold_a_maximal_ipv4_with_a_port() -> None:
    # This used to assert the opposite, and the scheme is why it changed.
    #
    # EAS FC needs `http://` on the session endpoint -- without it the module
    # hands `10.0.0.119:18080/pow/auth` to ProtoHttp, which will not open a
    # socket for a scheme-less URL, and no request is ever made. Adding it is
    # what produced the first `POST /pow/auth` this project has seen.
    #
    # It costs seven of the twenty-four bytes `pal.gt.easfc.ea.com:8094`
    # leaves. A maximal IPv4 with a five-digit port needs twenty-eight:
    #
    #     http://255.255.255.255:18080
    #
    # So there is a real address-length limit on EAS FC now, and it is
    # asserted rather than discovered on a console. `fits` reports False and
    # the patcher writes nothing; it does not truncate, and it does not run
    # past the end of the slot.
    m = M.build("255.255.255.255", 10041, 18080)
    slots = {s["name"]: s for s in m["stage2_easfc"]["strings"]}
    assert slots["easfc_catalogue"]["fits"]
    assert not slots["easfc_session"]["fits"]
    assert slots["easfc_session"]["budget"] == 24

    # Where the line falls: this server's own address fits with one byte over.
    fits = M.build("10.0.0.119", 10041, 18080)
    session = next(
        s for s in fits["stage2_easfc"]["strings"] if s["name"] == "easfc_session"
    )
    assert session["fits"]
    assert session["write"] == "http://10.0.0.119:18080"
    assert len(session["write"]) == 23


def test_the_tu3_branches_carry_their_context_guards() -> None:
    # The APT is pattern-located, so each branch write is guarded by the bytes
    # around it. Missing guards would let the plugin write into the wrong place.
    m = M.build("192.168.1.40", 10041, 18080)
    tu3 = m["stage3_tu3"]
    assert len(bytes.fromhex(tu3["signature"])) == 48
    assert len(tu3["branches"]) == 3
    for branch in tu3["branches"]:
        assert branch["context_before"] and branch["context_after"]
        assert len(bytes.fromhex(branch["expect"])) == 6
        assert len(bytes.fromhex(branch["write"])) == 6


# -- what the launch stage must contain, and why it is knowable --------------
#
# The manifest used to be marked `"complete": false`, on the grounds that the
# launcher installs trace stubs whose necessity could not be told from
# diagnostics without another live launch. It could: the flags settle it, and
# these read the launcher and `fut.sh` rather than trusting a comment.


def read(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / name).read_text()


def launcher_flags() -> set[str]:
    """The flags `tools/fut.sh` actually passes to the launcher."""
    script = read("tools/fut.sh")
    start = script.index("fifa14_early_local_server.py")
    end = script.index("\n", script.index("2>&1", start))
    invocation = script[start:end]
    return {word for word in invocation.split() if word.startswith("--")}


def test_the_trace_stubs_are_not_part_of_the_launch_patch() -> None:
    # Every one of them lives inside `arm_login_flow_traces`, which the
    # launcher calls only under --trace-login-flow -- a store_true flag that
    # fut.sh does not pass. So they are not applied on the console that works,
    # and a plugin that writes them patches sites the working console leaves
    # alone.
    assert "--trace-login-flow" not in launcher_flags()
    launcher = read("tools/fifa14_early_local_server.py")
    assert re.search(
        r"if args\.trace_login_flow:\s*\n\s*arm_login_flow_traces\(", launcher
    ), "arm_login_flow_traces is no longer gated by the flag"

    m = M.build("192.168.1.40", 10041, 18080)
    names = {s["name"] for s in m["stage1_launch"]["sites"]}
    names |= {c["name"] for c in m["stage1_launch"]["caves"]}
    assert not names & {"auth2_config_hook", "auth2_config_stub"}


def test_the_fut_resource_redirect_is_part_of_it() -> None:
    # fut.sh passes --redirect-fut-resource on every run, and it is what makes
    # the cards and their art come off the console's own disk. Leaving it out
    # of the plugin draws NOT FOUND on every card -- a failure that looks like
    # the server and is not.
    assert "--redirect-fut-resource" in launcher_flags()
    m = M.build("192.168.1.40", 10041, 18080)
    assert "fut_resource_hook" in {s["name"] for s in m["stage1_launch"]["sites"]}
    caves = {c["name"]: c for c in m["stage1_launch"]["caves"]}
    assert "fut_resource_journal" in caves
    stub = caves["fut_resource_stub"]
    # The URL lives inside the cave, so a plugin resolving a name at boot
    # rewrites it in place instead of rebuilding the stub.
    url = bytes.fromhex(stub["bytes"])[stub["url_offset"]:]
    assert url.split(b"\0")[0].decode() == stub["url"] == (
        "http://192.168.1.40:18080/futBoot.xml"
    )
    assert len(stub["url"]) < stub["url_capacity"]


def test_the_ticket_stub_gets_the_data_cave_it_points_at() -> None:
    # The stub loads r4 from TICKET_DUMMY. Writing the code without the data
    # hands the title a pointer into whatever happened to be there.
    m = M.build("192.168.1.40", 10041, 18080)
    caves = {c["name"]: c for c in m["stage1_launch"]["caves"]}
    assert "ticket_dummy" in caves
    import fifa14_early_local_server as E
    assert caves["ticket_dummy"]["address"] == E.TICKET_DUMMY
    assert caves["ticket_stub"]["address"] + len(
        bytes.fromhex(caves["ticket_stub"]["bytes"])
    ) == E.TICKET_DUMMY


def test_the_launch_stage_claims_to_be_complete_and_says_how() -> None:
    m = M.build("192.168.1.40", 10041, 18080)
    assert m["stage1_launch"]["complete"] is True
    assert m["stage1_launch"]["settled_by"]
