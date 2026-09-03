"""La sonde répond à une question binaire, et il faut qu'elle y réponde bien.

Un XNADDR fait 36 octets et seuls deux champs du milieu sont réécrits. Les
vingt derniers -- `abOnline`, remplis par la passerelle Xbox LIVE -- sont
précisément la matière qu'on ne sait pas lire, et le seul résultat qui vaut
quelque chose est celui obtenu en n'y touchant pas.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "tools"))

# Le vrai XNADDR envoyé par la console le 21 août.
REAL = bytes.fromhex(
    "C0A80119020B639A0C027CED8D19694F0ADF727600F4E8FD858C6104000000FA01000000"
)


def relayed():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "blaze_server_relay", REPO / "server" / "fifa14_blaze_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Les dataclasses du module cherchent leur propre module dans sys.modules
    # au moment d'être définies ; sans cette ligne, l'import échoue.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.relayed_address


def test_only_the_public_address_and_port_move() -> None:
    out = relayed()(REAL, ("87.106.7.87", 3074))
    assert len(out) == len(REAL) == 36
    assert out[0:4] == REAL[0:4]                      # l'adresse LAN, intacte
    assert out[4:8] == bytes([87, 106, 7, 87])        # l'adresse publique
    assert out[8:10] == (3074).to_bytes(2, "big")     # le port
    assert out[10:16] == REAL[10:16]                  # la MAC de la console
    assert out[16:] == REAL[16:]                      # abOnline, jamais touché


def test_a_malformed_address_is_left_alone() -> None:
    """Mieux vaut relayer une adresse qu'on n'a pas su lire que d'en fabriquer
    une fausse."""
    rewrite = relayed()
    assert rewrite(b"\x01\x02", ("87.106.7.87", 3074)) == b"\x01\x02"
    assert rewrite(REAL, ("pas.une.adresse", 3074)) == REAL
    assert rewrite(REAL, ("1.2.3", 3074)) == REAL


def test_the_probe_records_what_arrives() -> None:
    """Un seul paquet suffit à répondre, donc le premier doit être visible."""
    journal = Path(__file__).parent / "_probe.jsonl"
    if journal.exists():
        journal.unlink()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    probe = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "xnet_probe.py"),
         "--listen", "127.0.0.1", "--port", str(port), "--journal", str(journal)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 5
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while time.time() < deadline and not journal.exists():
            sender.sendto(b"\xDE\xAD\xBE\xEF", ("127.0.0.1", port))
            time.sleep(0.1)
        assert journal.exists(), "la sonde n'a rien écrit"
        import json
        record = json.loads(journal.read_text().splitlines()[0])
        assert record["event"] == "xnet_packet"
        assert record["bytes"] == 4
        assert record["first_from_peer"] is True
        assert record["head"] == "DEADBEEF"
    finally:
        probe.kill()
        probe.wait(timeout=5)
        if journal.exists():
            journal.unlink()


def mirrored():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "blaze_server_mirror", REPO / "server" / "fifa14_blaze_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.mirrored_address


def test_the_borrowed_address_keeps_the_part_nobody_can_read() -> None:
    """abOnline is the whole point of borrowing it.

    The first probe gave a clean but confounded result: the invented host
    carried a fabricated XNADDR with twenty zero bytes where the Xbox LIVE
    gateway writes its own, and the console sent nothing at all -- which is
    what a kernel refusing a malformed address looks like, and says nothing
    about whether a rewritten *real* one would be used.
    """
    out = mirrored()(REAL)
    assert len(out) == 36
    assert out[16:] == REAL[16:]          # abOnline, authentique
    assert out[0:10] == REAL[0:10]        # adresses et port, inchangés ici


def test_the_borrowed_address_does_not_claim_the_same_hardware() -> None:
    """Two machines announcing one MAC on one network is a confusion worth
    avoiding. The locally-administered bit makes an address no sold hardware
    can have."""
    out = mirrored()(REAL)
    assert out[10:16] != REAL[10:16]
    assert out[10] & 0x02                 # administrée localement


def server_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "blaze_server_guest_mirror", REPO / "server" / "fifa14_blaze_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_invented_guest_borrows_too() -> None:
    """The guest was fabricated while only the host could borrow.

    `FIFA14_TEST_OPPONENT` invents a guest, and its address carried twenty zero
    bytes of `abOnline` -- exactly the shape that made a console send nothing at
    all and told us nothing about why. The host had been given a way out of that
    confound and the guest had not, so the host role could never be tested
    cleanly with one console.

    Both roles read either knob now: `FIFA14_TEST_PEER_ADDRESS` names no side,
    and `FIFA14_TEST_HOST_ADDRESS` keeps working.
    """
    import os

    module = server_module()
    Field, STRUCT, BINARY, INTEGER = (
        module.Field, module.STRUCT, module.BINARY, module.INTEGER
    )
    real = (0, Field("VALU", STRUCT, [
        Field("XDDR", BINARY, REAL),
        Field("XUID", INTEGER, 2305837508020095216),
    ]))

    server = module.Fifa14Protocol.__new__(module.Fifa14Protocol)

    for knob in ("FIFA14_TEST_PEER_ADDRESS", "FIFA14_TEST_HOST_ADDRESS"):
        os.environ.pop("FIFA14_TEST_PEER_ADDRESS", None)
        os.environ.pop("FIFA14_TEST_HOST_ADDRESS", None)
        os.environ[knob] = "mirror"
        try:
            worn = server.borrowed_address(real)
        finally:
            os.environ.pop(knob, None)
        assert module.mirror_invented_address.__doc__
        xddr = next(f.value for f in worn[1].value if f.label == "XDDR")
        xuid = next(f.value for f in worn[1].value if f.label == "XUID")
        # The only genuine abOnline available, carried across untouched.
        assert bytes(xddr)[16:] == REAL[16:], knob
        # A MAC no sold hardware can have, so two machines do not claim one.
        assert bytes(xddr)[10:16] != REAL[10:16], knob
        assert bytes(xddr)[10] & 0x02, knob
        # The roster is keyed on the XUID, so the invented player keeps its own.
        assert xuid == module.SYNTHETIC_PERSONA, knob

    # With no real address there is nothing to borrow, and saying so beats
    # handing back something that looks borrowed.
    os.environ["FIFA14_TEST_PEER_ADDRESS"] = "mirror"
    try:
        assert server.borrowed_address(None) is None
    finally:
        os.environ.pop("FIFA14_TEST_PEER_ADDRESS", None)

    # And with the knob off, neither name borrows anything.
    assert module.mirror_invented_address() is False


def test_the_address_on_the_wire_is_the_one_that_was_borrowed() -> None:
    """The borrow is worthless if a later builder overwrites it.

    `synthetic_player` builds notification 21, and it called
    `synthetic_address()` with no argument -- so the fabricated address was
    rebuilt there even when the roster already held a borrowed one. The journal
    said `test_peer_borrowed_address`, and the wire carried twenty zero bytes of
    `abOnline` anyway. A run judged on that would have been judged on nothing.
    """
    import os

    module = server_module()
    Field, STRUCT, BINARY, INTEGER = (
        module.Field, module.STRUCT, module.BINARY, module.INTEGER
    )
    real = (0, Field("VALU", STRUCT, [
        Field("XDDR", BINARY, REAL),
        Field("XUID", INTEGER, 2305837508020095216),
    ]))

    server = module.Fifa14Protocol.__new__(module.Fifa14Protocol)
    server.logger = type("Quiet", (), {"event": lambda *a, **k: None})()

    class Game:
        host_address = real
        members: list = []

    os.environ["FIFA14_PEER_RELAY"] = "10.0.0.116:3074"
    os.environ["FIFA14_TEST_PEER_ADDRESS"] = "mirror"
    try:
        game = Game()
        game.members = [{
            "persona": module.SYNTHETIC_PERSONA,
            "address": server.synthetic_address(real),
        }]
        out = server.synthetic_peer_address(game)
    finally:
        os.environ.pop("FIFA14_PEER_RELAY", None)
        os.environ.pop("FIFA14_TEST_PEER_ADDRESS", None)

    xddr = bytes(next(f.value for f in out[1].value if f.label == "XDDR"))
    # The borrowed abOnline survived all the way to the wire.
    assert xddr[16:] == REAL[16:]
    # And the relay was applied here, because this notification does not pass
    # through `member_player`, where a real member's address is rewritten.
    assert xddr[4:8] == bytes([10, 0, 0, 116])
    assert int.from_bytes(xddr[8:10], "big") == 3074
