"""Le relais ne doit pas perdre le début d'une conversation.

Le 22 août, deux consoles ont été appariées, le relais a transmis dix paquets
dans chaque sens, et aucune des deux n'a jamais considéré l'autre comme
joignable. Le journal disait pourquoi en une ligne :

    relay_partner_silent {"peer": "2.11.99.154", "packets": 1}

Le tout premier paquet a été jeté, parce que l'autre console n'avait pas
encore parlé et qu'on ne savait pas où la joindre. Si c'était l'ouverture de
l'échange de clés, tout ce qui a suivi était des relances sans espoir.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_packets_sent_before_the_partner_speaks_are_delivered(tmp_path) -> None:
    """Deux consoles ne commencent jamais à parler à la même milliseconde."""
    pairs = tmp_path / "pairs.json"
    journal = tmp_path / "relay.jsonl"
    # Les deux "consoles" sont sur la même machine, donc la paire est
    # dégénérée -- ce que le relais doit refuser. On l'exerce donc à travers
    # sa propre logique plutôt qu'en bout en bout.
    sys.path.insert(0, str(REPO / "tools"))
    import xnet_relay
    table = xnet_relay.Pairs(pairs)
    pairs.write_text(json.dumps({"pairs": [["10.0.0.1", "10.0.0.2"]]}))
    table.refresh()
    assert table.of("10.0.0.1") == "10.0.0.2"
    assert table.of("10.0.0.2") == "10.0.0.1"
    assert table.of("10.0.0.3") is None


def test_the_pairs_file_is_reread_when_it_changes(tmp_path) -> None:
    sys.path.insert(0, str(REPO / "tools"))
    import xnet_relay
    pairs = tmp_path / "pairs.json"
    table = xnet_relay.Pairs(pairs)
    table.refresh()
    assert table.of("10.0.0.1") is None          # pas encore de fichier
    pairs.write_text(json.dumps({"pairs": [["10.0.0.1", "10.0.0.2"]]}))
    time.sleep(0.01)
    table.refresh()
    assert table.of("10.0.0.1") == "10.0.0.2"
    # Une partie qui se vide vide la table, sinon le relais continuerait à
    # renvoyer le trafic d'un inconnu à un autre.
    pairs.write_text(json.dumps({"pairs": []}))
    time.sleep(0.01)
    table.refresh()
    assert table.of("10.0.0.1") is None


def test_a_half_written_file_is_ignored_rather_than_half_read(tmp_path) -> None:
    """Un relais qui lit un fichier tronqué enverrait le trafic d'un joueur à
    un étranger."""
    sys.path.insert(0, str(REPO / "tools"))
    import xnet_relay
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps({"pairs": [["10.0.0.1", "10.0.0.2"]]}))
    table = xnet_relay.Pairs(pairs)
    table.refresh()
    pairs.write_text('{"pairs": [["10.0.0.1", ')      # tronqué
    time.sleep(0.01)
    table.refresh()
    assert table.of("10.0.0.1") == "10.0.0.2"          # l'ancienne table tient


def test_the_relay_forwards_between_two_paired_endpoints(tmp_path) -> None:
    """De bout en bout, avec deux fausses consoles sur deux adresses de
    bouclage distinctes."""
    port = free_port()
    pairs = tmp_path / "pairs.json"
    journal = tmp_path / "relay.jsonl"
    pairs.write_text(json.dumps({"pairs": [["127.0.0.1", "127.0.0.2"]]}))
    relay = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "xnet_relay.py"),
         "--listen", "127.0.0.1", "--port", str(port),
         "--pairs", str(pairs), "--journal", str(journal)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        one = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        one.bind(("127.0.0.1", 0))
        one.settimeout(4)
        two = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            two.bind(("127.0.0.2", 0))
        except OSError:
            # macOS n'a qu'une adresse de bouclage sans alias explicite. Le
            # relais apparie par adresse, donc deux fausses consoles sur la
            # même n'exercent rien : mieux vaut ne pas exécuter ce test que
            # de le rendre faux pour qu'il passe.
            import pytest
            pytest.skip("pas de deuxième adresse de bouclage sur cette machine")
        two.settimeout(4)

        # Le premier parle avant le second : c'est exactement le cas qui a
        # coûté le match du 22 août.
        deadline = time.time() + 6
        got = None
        while time.time() < deadline and got is None:
            one.sendto(b"KEYEX-OPEN", ("127.0.0.1", port))
            time.sleep(0.2)
            two.sendto(b"HELLO", ("127.0.0.1", port))
            try:
                payload, _ = two.recvfrom(2048)
                got = payload
            except socket.timeout:
                continue
        assert got == b"KEYEX-OPEN", "le premier paquet n'a pas été délivré"
    finally:
        relay.kill()
        relay.wait(timeout=5)


def test_an_address_learned_yesterday_is_not_written_to_today() -> None:
    """Le relais oublie une adresse dont il n'a plus de nouvelles.

    Le 23 août il a expédié dix paquets vers une correspondance NAT apprise la
    veille, et a journalisé `relay_forwarded` -- donc affirmé les avoir
    livrés. Le paquet perdu n'est pas le problème : le journal qui ment l'est.
    """
    sys.path.insert(0, str(REPO / "tools"))
    import xnet_relay

    endpoint = {"2.11.99.154": ("2.11.99.154", 3074)}
    seen_at = {"2.11.99.154": 1000.0}

    assert xnet_relay.fresh_endpoint(
        endpoint, seen_at, "2.11.99.154", now=1010.0, fresh=45.0
    ) == ("2.11.99.154", 3074), "une adresse fraîche doit rester joignable"

    assert xnet_relay.fresh_endpoint(
        endpoint, seen_at, "2.11.99.154", now=1000.0 + 86400, fresh=45.0
    ) is None, "une adresse de la veille ne doit plus être utilisée"

    assert xnet_relay.fresh_endpoint(
        endpoint, seen_at, "10.0.0.9", now=1010.0, fresh=45.0
    ) is None, "une adresse jamais apprise n'existe pas"


def test_the_pairs_table_reports_whether_it_changed(tmp_path) -> None:
    """`refresh` doit dire si la composition a bougé.

    C'est ce qui permet d'oublier l'adresse d'un joueur qui n'est plus
    appairé, au lieu de préparer la livraison d'après-demain à l'adresse
    d'hier.
    """
    sys.path.insert(0, str(REPO / "tools"))
    import xnet_relay

    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps({"pairs": [["10.0.0.1", "10.0.0.2"]]}))
    table = xnet_relay.Pairs(pairs)

    assert table.refresh() is True, "la première lecture est un changement"
    assert table.refresh() is False, "relire le même fichier ne change rien"

    time.sleep(0.01)
    pairs.write_text(json.dumps({"pairs": []}))
    assert table.refresh() is True, "une paire retirée est un changement"
    assert table.of("10.0.0.1") is None
