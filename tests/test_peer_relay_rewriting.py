"""Quand un relais est armé, aucune adresse réelle ne doit rester dans la trame.

Le 23 août 2026, deux consoles -- une en France, une aux États-Unis -- ont
essayé de se rejoindre. Le Blaze s'est déroulé sans une faute : `joinGame`
résolu par le persona de l'hôte, notifications 20/71/30/100/115 chez l'invité,
21 et 30 chez l'hôte. Puis `updateMeshConnection` a dit `STAT = 0`, et le
journal du relais est resté vide.

La trame envoyée à l'invité portait **trois** `XDDR` :

    192.168.1.25 / 2.11.99.154:3074     l'hôte, adresse réelle
    192.168.1.25 / 87.106.7.87:3074     l'hôte, réécrite vers le relais
    10.0.0.179  / 73.128.188.206:3074   l'invité lui-même, laissée telle quelle

Une seule des deux copies de l'adresse de l'hôte était réécrite, et la console
a composé l'autre. Réécrire une copie d'une adresse et pas l'autre revient à
n'en réécrire aucune -- d'où la forme de ce test : il ne vérifie pas qu'une
réécriture a eu lieu, il vérifie qu'**aucune** adresse réelle ne subsiste.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "tools"))

import fifa14_blaze_server as SERVER  # noqa: E402
from blaze_tdf import decode_frame  # noqa: E402

HOST = 2535469248587161
GUEST = 2533274966877915

RELAY_HOST = "87.106.7.87"
RELAY_PORT = 3074


def xnaddr(local: str, online: str, port: int = 3074) -> bytes:
    """Les trente-six octets d'un XNADDR, dont seuls les dix premiers parlent."""
    return (
        bytes(int(p) for p in local.split("."))
        + bytes(int(p) for p in online.split("."))
        + port.to_bytes(2, "big")
        + bytes.fromhex("7C1E523CDC3D")
        + bytes(range(20))
    )


HOST_XNADDR = xnaddr("192.168.1.25", "2.11.99.154")
GUEST_XNADDR = xnaddr("10.0.0.179", "73.128.188.206")


def network_union(address: bytes) -> tuple[int, SERVER.Field]:
    """`PNET`, tel que la console l'envoie : une union sur une struct."""
    return (0, SERVER.Field("VALU", SERVER.STRUCT, [
        SERVER.Field("MACI", SERVER.INTEGER, 244320674),
        SERVER.Field("XDDR", SERVER.BINARY, address),
    ]))


def host_network(address: bytes) -> SERVER.Field:
    """`HNET`, tel que `createGame` le dépose sur la partie."""
    return SERVER.Field("HNET", SERVER.LIST, (SERVER.STRUCT, [
        (0, [
            SERVER.Field("MACI", SERVER.INTEGER, 244320674),
            SERVER.Field("XDDR", SERVER.BINARY, address),
        ]),
    ]))


def every_xnaddr(node, found: list[bytes]) -> None:
    """Tous les `XDDR` d'une trame décodée, à n'importe quelle profondeur.

    Un `Field` décodé est un objet, pas un dict, et il se trouve qu'il est
    aussi itérable -- d'où l'ordre des branches : on lit l'étiquette avant de
    laisser la récursion le traiter comme une simple séquence.
    """
    label = getattr(node, "label", None)
    if label is not None:
        value = getattr(node, "value", None)
        if label == "XDDR" and isinstance(value, (bytes, bytearray)):
            found.append(bytes(value))
        every_xnaddr(value, found)
        return
    if isinstance(node, (bytes, bytearray, str)):
        return
    if isinstance(node, dict):
        for item in node.values():
            every_xnaddr(item, found)
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            every_xnaddr(item, found)


def online_of(address: bytes) -> str:
    return ".".join(str(byte) for byte in address[4:8])


class PeerRelayRewritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("FIFA14_PEER_RELAY")
        os.environ["FIFA14_PEER_RELAY"] = f"{RELAY_HOST}:{RELAY_PORT}"
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)

        self.host_state = SERVER.ClientState(9, ("2.11.99.154", 1032), 10041)
        self.host_state.xuid = HOST
        self.guest_state = SERVER.ClientState(1, ("73.128.188.206", 1049), 10041)
        self.guest_state.xuid = GUEST

        self.game = SERVER.HostedGame(
            game_id=1, persona_id=HOST, gamertag="Imrane",
            state=SERVER.GAME_STATE_PRE_GAME, roster=[9],
        )
        self.game.host_addresses = host_network(HOST_XNADDR)
        self.game.host_state = self.host_state
        self.game.members = [
            self.protocol.member(
                self.game, HOST, "Imrane", 9, network_union(HOST_XNADDR),
                slot=0, team=0, state=self.host_state,
            ),
            self.protocol.member(
                self.game, GUEST, "Mosebee", 1, network_union(GUEST_XNADDR),
                slot=1, team=1, state=self.guest_state,
            ),
        ]
        self.protocol.games[1] = self.game

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()
        if self.previous is None:
            os.environ.pop("FIFA14_PEER_RELAY", None)
        else:
            os.environ["FIFA14_PEER_RELAY"] = self.previous

    def setup_addresses_for(self, viewer: SERVER.ClientState) -> list[bytes]:
        payload = self.protocol.game_setup_payload(self.game, viewer=viewer)
        # On repasse par la trame encodée puis décodée : c'est ce que la
        # console lit réellement, et non ce qu'on croit avoir construit.
        frame = SERVER.notification_frame(
            SERVER.GAME_MANAGER, SERVER.NOTIFY_GAME_SETUP,
            SERVER.encode_fields(payload),
        )
        found: list[bytes] = []
        every_xnaddr(decode_frame(frame)["fields"], found)
        return found

    def test_the_guest_is_never_handed_the_host_real_address(self) -> None:
        """Le cas qui a échoué en vrai : trois `XDDR`, une seule réécrite."""
        addresses = self.setup_addresses_for(self.guest_state)
        self.assertTrue(addresses, "aucun XDDR dans la notification 20")
        for address in addresses:
            online = online_of(address)
            if online == "73.128.188.206":
                continue  # l'invité lui-même : il sait où il habite
            self.assertEqual(
                online, RELAY_HOST,
                f"adresse réelle {online} laissée dans la trame de l'invité",
            )

    def test_the_host_keeps_reading_its_own_address(self) -> None:
        """Une console ne passe jamais par un relais pour s'atteindre elle-même."""
        addresses = self.setup_addresses_for(self.host_state)
        self.assertIn(
            "2.11.99.154", [online_of(address) for address in addresses],
            "l'hôte ne retrouve plus sa propre adresse",
        )

    def test_the_arrival_announced_to_the_host_goes_through_the_relay(self) -> None:
        """La notification 21 décrit toujours quelqu'un d'autre que son destinataire.

        Elle était construite sans `viewer`, donc sans réécriture, et l'hôte y
        lisait l'adresse réelle de l'arrivant.
        """
        frame = SERVER.notification_frame(
            SERVER.GAME_MANAGER, SERVER.NOTIFY_PLAYER_JOINING,
            SERVER.encode_fields([
                SERVER.Field("GID", SERVER.INTEGER, self.game.game_id),
                SERVER.Field("PDAT", SERVER.STRUCT,
                             self.protocol.member_player(
                                 self.game, self.game.members[1])),
            ]),
        )
        found: list[bytes] = []
        every_xnaddr(decode_frame(frame)["fields"], found)
        self.assertTrue(found, "aucun XDDR dans la notification 21")
        for address in found:
            self.assertEqual(online_of(address), RELAY_HOST)

    def test_nothing_is_rewritten_without_a_relay(self) -> None:
        """Sans relais armé, la trame doit être exactement celle d'avant."""
        os.environ.pop("FIFA14_PEER_RELAY", None)
        addresses = self.setup_addresses_for(self.guest_state)
        online = {online_of(address) for address in addresses}
        self.assertIn("2.11.99.154", online)
        self.assertNotIn(RELAY_HOST, online)


if __name__ == "__main__":
    unittest.main()
