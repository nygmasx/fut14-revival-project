"""Deux consoles derrière le même routeur ne peuvent pas se joindre par sa façade.

Le 4 septembre 2026, les deux consoles de ce projet se sont appariées, ont
échangé `XNNC` et `XSES`, et ont fermé leur maillage : `updateMeshConnection`
avec `STAT = 2` dans les deux sens, `mesh_complete` sans rien de synthétique.
Puis quarante-quatre secondes de silence total, et les deux titres ont conclu
par `PUT /ut/game/fifa14/match/end` avec `endReason: DNF`.

Le journal dit pourquoi. Les deux `startMatchmaking` portaient :

    console 1   ina = 192.168.1.25   inaOnline = 2.11.99.154   port = 1024
    console 2   ina = 192.168.1.45   inaOnline = 2.11.99.154   port = 3074

Le même `inaOnline` des deux côtés : l'adresse publique de la box qu'elles
partagent. Chaque console recevait donc, pour joindre l'autre, la façade de
son propre routeur -- il faudrait qu'il renvoie vers le LAN un paquet adressé
à lui-même, ce que la plupart des box domestiques ne font pas.

C'est le miroir exact du défaut du relais : `xnet_relay.py` indexe ses paires
par adresse source, donc deux consoles derrière un seul NAT lui sont
indiscernables. Le relais ne peut pas router cette paire-là, et la façade non
plus. Il reste l'adresse locale, que chaque console met elle-même dans son
XNADDR et dont personne ne se servait.
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

CONSOLE_ONE = 2535469248587161
CONSOLE_TWO = 2535465199097436

PUBLIC = "2.11.99.154"


def xnaddr(local: str, online: str, port: int) -> bytes:
    return (
        bytes(int(p) for p in local.split("."))
        + bytes(int(p) for p in online.split("."))
        + port.to_bytes(2, "big")
        + bytes.fromhex("7CED8D19694F")
        + bytes(range(20))
    )


# Les deux XNADDR du 4 septembre, tels que le journal les a enregistrés.
ONE_XNADDR = xnaddr("192.168.1.25", PUBLIC, 1024)
TWO_XNADDR = xnaddr("192.168.1.45", PUBLIC, 3074)


def network_union(address: bytes) -> tuple[int, SERVER.Field]:
    return (0, SERVER.Field("VALU", SERVER.STRUCT, [
        SERVER.Field("MACI", SERVER.INTEGER, 597374347),
        SERVER.Field("XDDR", SERVER.BINARY, address),
    ]))


def host_network(address: bytes) -> SERVER.Field:
    return SERVER.Field("HNET", SERVER.LIST, (SERVER.STRUCT, [
        (0, [
            SERVER.Field("MACI", SERVER.INTEGER, 597374347),
            SERVER.Field("XDDR", SERVER.BINARY, address),
        ]),
    ]))


def every_xnaddr(node, found: list[bytes]) -> None:
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


def port_of(address: bytes) -> int:
    return int.from_bytes(address[8:10], "big")


class LanDirectAddressingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {
            name: os.environ.get(name)
            for name in ("FIFA14_LAN_DIRECT", "FIFA14_PEER_RELAY")
        }
        os.environ.pop("FIFA14_PEER_RELAY", None)
        os.environ["FIFA14_LAN_DIRECT"] = "1"
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)

        self.one_state = SERVER.ClientState(2, (PUBLIC, 1032), 10041)
        self.one_state.xuid = CONSOLE_ONE
        self.two_state = SERVER.ClientState(4, (PUBLIC, 1037), 10041)
        self.two_state.xuid = CONSOLE_TWO

        self.game = SERVER.HostedGame(
            game_id=1, persona_id=CONSOLE_ONE, gamertag="imskobogota6z",
            state=SERVER.GAME_STATE_PRE_GAME, roster=[2],
        )
        self.game.host_addresses = host_network(ONE_XNADDR)
        self.game.host_state = self.one_state
        self.game.members = [
            self.protocol.member(
                self.game, CONSOLE_ONE, "imskobogota6z", 2,
                network_union(ONE_XNADDR), slot=0, team=0,
                state=self.one_state,
            ),
            self.protocol.member(
                self.game, CONSOLE_TWO, "psyko mg", 4,
                network_union(TWO_XNADDR), slot=1, team=1,
                state=self.two_state,
            ),
        ]
        self.protocol.games[1] = self.game

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def setup_addresses_for(self, viewer: SERVER.ClientState) -> list[bytes]:
        payload = self.protocol.game_setup_payload(self.game, viewer=viewer)
        frame = SERVER.notification_frame(
            SERVER.GAME_MANAGER, SERVER.NOTIFY_GAME_SETUP,
            SERVER.encode_fields(payload),
        )
        found: list[bytes] = []
        every_xnaddr(decode_frame(frame)["fields"], found)
        return found

    def others(self, viewer: SERVER.ClientState,
               own_local: str) -> list[bytes]:
        """Les adresses de la trame, sauf celle du destinataire lui-même.

        Une console n'est pas réadressée vers elle-même, ni par le relais ni
        ici : elle sait où elle habite, et sa propre copie porte donc encore la
        façade du routeur. C'est celle des *autres* qui doit avoir changé.
        """
        return [
            address for address in self.setup_addresses_for(viewer)
            if ".".join(str(byte) for byte in address[:4]) != own_local
        ]

    def test_no_console_is_ever_handed_the_shared_public_address(self) -> None:
        """Le cas qui a échoué : la façade du NAT ne mène à aucune des deux."""
        addresses = self.others(self.two_state, "192.168.1.45")
        self.assertTrue(addresses, "aucun XDDR de l'adversaire dans la notif 20")
        for address in addresses:
            self.assertNotEqual(
                online_of(address), PUBLIC,
                "la façade du routeur est restée dans la trame",
            )

    def test_the_online_address_becomes_the_local_one(self) -> None:
        """`inaOnline` prend la valeur d'`ina`, que la console a mise elle-même."""
        addresses = self.others(self.two_state, "192.168.1.45")
        self.assertTrue(addresses)
        for address in addresses:
            local = ".".join(str(byte) for byte in address[:4])
            self.assertEqual(online_of(address), local)

    def test_the_untouched_bytes_are_untouched(self) -> None:
        """`abEnet` et les vingt octets d'`abOnline` ne sont pas de notre ressort.

        Un XNADDR fabriqué de bout en bout avait déjà été refusé par le noyau
        sans qu'un seul paquet parte. On ne réécrit que ce qu'on comprend.
        """
        rewritten = SERVER.peer_address(ONE_XNADDR)
        self.assertEqual(rewritten[10:], ONE_XNADDR[10:])
        self.assertEqual(rewritten[:4], ONE_XNADDR[:4])

    def test_the_port_is_kept_unless_one_is_named(self) -> None:
        """Les deux consoles ont annoncé des ports différents -- 1024 et 3074.

        Un port remappé par le NAT n'a aucune raison d'être celui sur lequel la
        console écoute chez elle, mais lequel des deux est juste se lit sur le
        réseau et pas ici. D'où les deux modes.
        """
        self.assertEqual(port_of(SERVER.peer_address(ONE_XNADDR)), 1024)
        os.environ["FIFA14_LAN_DIRECT"] = "3074"
        self.assertEqual(port_of(SERVER.peer_address(ONE_XNADDR)), 3074)

    def test_the_relay_still_wins_when_lan_direct_is_disarmed(self) -> None:
        """Désarmé, rien ne change : un adversaire distant reprend le relais."""
        os.environ.pop("FIFA14_LAN_DIRECT", None)
        os.environ["FIFA14_PEER_RELAY"] = "87.106.7.87:3074"
        addresses = self.others(self.two_state, "192.168.1.45")
        self.assertTrue(addresses)
        for address in addresses:
            self.assertEqual(online_of(address), "87.106.7.87")

    def test_nothing_is_rewritten_with_neither_armed(self) -> None:
        os.environ.pop("FIFA14_LAN_DIRECT", None)
        os.environ.pop("FIFA14_PEER_RELAY", None)
        online = {online_of(a) for a in self.setup_addresses_for(self.two_state)}
        self.assertEqual(online, {PUBLIC})


if __name__ == "__main__":
    unittest.main()
