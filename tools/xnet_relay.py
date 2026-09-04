#!/usr/bin/env python3
"""Faire passer le trafic de match entre deux consoles qui ne se joignent pas.

    tools/xnet_relay.py --port 3074 --pairs runtime/relay-pairs.json

Pourquoi ceci existe
--------------------
Le match ne passe pas par le serveur : les deux consoles se parlent
directement, à l'adresse que le serveur leur a donnée l'une pour l'autre. Le
22 août, deux consoles se sont trouvées, sont entrées en match, et ne se sont
jamais vues -- deux NAT domestiques, la France et l'Algérie, et plus aucun
service d'EA pour aider à la traversée. Aucun des deux propriétaires ne peut
reconfigurer sa box.

`FIFA14_PEER_RELAY` réécrit l'adresse publique dans le XNADDR que chaque
console reçoit pour l'autre, et une sonde a montré que **le noyau honore la
réécriture** : 25 paquets de 122 octets sont arrivés, dix de chaque console,
depuis leurs ports 3074 respectifs. Il ne manquait qu'un intermédiaire pour
croiser les flux. C'est lui.

Ce qu'il ne fait pas
--------------------
Rien déchiffrer. Le trafic est chiffré de bout en bout entre les deux consoles
avec des clés qui voyagent dans `XSES` et qu'elles ont fabriquées elles-mêmes.
Ce relais transporte des octets opaques d'un bout à l'autre, et c'est tout ce
qu'il a besoin de faire.

Comment il sait qui va avec qui
-------------------------------
Le serveur Blaze le sait -- c'est lui qui apparie -- et il l'écrit dans un
petit fichier que ce relais relit dès qu'il change. Deviner à partir du seul
trafic marcherait à deux joueurs et casserait au troisième.

L'adresse publique et le port de chaque console sont appris de son premier
paquet, jamais supposés : c'est le NAT qui décide du port, pas nous.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime
from pathlib import Path


class Pairs:
    """Qui joue contre qui, tel que le serveur Blaze l'a décidé."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.partner: dict[str, str] = {}
        self._stamp: tuple = ()

    def refresh(self) -> bool:
        """Relit le fichier si besoin. Vrai si la table a changé."""
        if self.path is None:
            return False
        try:
            stat = self.path.stat()
        except OSError:
            changed = bool(self.partner)
            self.partner = {}
            return changed
        key = (stat.st_size, stat.st_mtime)
        if key == self._stamp:
            return False
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        table: dict[str, str] = {}
        for pair in document.get("pairs", []):
            if len(pair) == 2 and pair[0] != pair[1]:
                table[str(pair[0])] = str(pair[1])
                table[str(pair[1])] = str(pair[0])
        changed = table != self.partner
        self.partner = table
        self._stamp = key
        return changed

    def of(self, address: str) -> str | None:
        return self.partner.get(address)


def fresh_endpoint(endpoint: dict[str, tuple[str, int]],
                   seen_at: dict[str, float],
                   address: str, now: float,
                   fresh: float) -> tuple[str, int] | None:
    """Où joindre `address`, ou rien si c'est trop vieux pour y croire.

    Le relais ne devine jamais une adresse : il n'écrit qu'à celles dont il a
    reçu un paquet. Ce qu'il ne faisait pas, c'est les oublier. Son
    dictionnaire vivait en mémoire d'un processus démarré la veille, et le
    23 août il a expédié dix paquets à une correspondance NAT apprise le 22 --
    en journalisant `relay_forwarded`, donc en affirmant les avoir livrés.

    Un paquet perdu se rattrape. Un journal qui ment coûte un quart d'heure de
    diagnostic à contresens, ce qui est plus cher.
    """
    target = endpoint.get(address)
    if target is None:
        return None
    if now - seen_at.get(address, 0.0) > fresh:
        return None
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3074)
    parser.add_argument("--pairs", type=Path, default=None,
                        help="le fichier où le serveur Blaze écrit les paires")
    parser.add_argument("--journal", type=Path, default=None)
    arguments = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((arguments.listen, arguments.port))
    sock.settimeout(1.0)
    print(f"relais: UDP {arguments.listen}:{arguments.port}", flush=True)

    pairs = Pairs(arguments.pairs)
    # L'endroit d'où chaque console parle réellement, appris de ses paquets.
    endpoint: dict[str, tuple[str, int]] = {}
    # Et quand on l'a appris.
    #
    # Ce dictionnaire vivait en mémoire d'un processus démarré la veille. Le
    # 23 août, une console a reçu dix paquets à une adresse apprise le 22 :
    # la correspondance NAT avait expiré depuis des heures, le relais les a
    # expédiés dans le vide et a journalisé `relay_forwarded` -- en toute
    # bonne foi. La lecture du journal a été fausse pendant un quart d'heure
    # à cause de ça, ce qui est pire qu'un paquet perdu.
    #
    # Une correspondance UDP tient rarement plus d'une minute sans trafic.
    # Passé ce délai on ne sait plus, et on le dit : le paquet est mis en
    # attente comme pour un partenaire qui n'a jamais parlé.
    seen_at: dict[str, float] = {}
    FRESH = 45.0
    counts: dict[str, int] = {}
    dropped: dict[str, int] = {}
    # Ce qu'un joueur a envoyé avant que son partenaire n'ait parlé.
    #
    # La première version jetait ces paquets-là : on ne savait pas encore où
    # joindre l'autre. Le 22 août, le tout premier paquet d'une console est
    # parti à la poubelle pour cette raison -- et s'il portait l'ouverture de
    # l'échange de clés, tout ce qui a suivi était des relances sans espoir.
    # Les deux consoles se sont parlé pendant dix paquets sans jamais
    # s'entendre.
    #
    # Ils attendent maintenant. Une poignée suffit : ce qui compte est le
    # début de la conversation, pas son milieu.
    waiting: dict[str, list[bytes]] = {}
    HELD = 16
    # Les formes déjà échantillonnées : (adresse, taille).
    sampled: set[tuple[str, int]] = set()

    def note(kind: str, **values: object) -> None:
        record = {
            "time": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": kind,
            **values,
        }
        line = json.dumps(record, sort_keys=True)
        print(line, flush=True)
        if arguments.journal is not None:
            arguments.journal.parent.mkdir(parents=True, exist_ok=True)
            with arguments.journal.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    while True:
        if pairs.refresh():
            # La partie a changé de composition. Une adresse apprise pour
            # quelqu'un qui n'est plus appairé n'a plus de raison d'être
            # gardée, et en la gardant on prépare la livraison d'après-demain
            # à l'adresse d'hier.
            for stale in [a for a in endpoint if pairs.of(a) is None]:
                endpoint.pop(stale, None)
                seen_at.pop(stale, None)
                waiting.pop(stale, None)
        try:
            payload, peer = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            continue
        except KeyboardInterrupt:
            break

        source, port = peer
        now = time.monotonic()
        if endpoint.get(source) != (source, port):
            endpoint[source] = (source, port)
            note("relay_endpoint", peer=f"{source}:{port}",
                 partner=pairs.of(source))
        seen_at[source] = now
        counts[source] = counts.get(source, 0) + 1

        # Les premiers octets de chaque pair, une seule fois.
        #
        # Les deux consoles s'envoient dix sondes de 122 octets et se les
        # jettent mutuellement. Si l'une parle XNet sécurisé et l'autre en
        # clair, ça se lit ici et nulle part ailleurs : le relais est le seul
        # point du montage qui voie les deux côtés. On ne journalise qu'un
        # échantillon par pair et par taille, parce que le but est de
        # comparer des formes, pas de capturer une session.
        shape = (source, len(payload))
        if shape not in sampled:
            sampled.add(shape)
            note("relay_sample", peer=source, bytes=len(payload),
                 head=payload[:24].hex().upper())

        partner = pairs.of(source)
        if partner is None:
            # Personne à qui la donner. Compté, pas jeté en silence.
            dropped[source] = dropped.get(source, 0) + 1
            if dropped[source] in (1, 100, 1000):
                note("relay_unpaired", peer=source, packets=dropped[source])
            continue
        target = fresh_endpoint(endpoint, seen_at, partner, now, FRESH)
        if target is None and partner in endpoint:
            note("relay_endpoint_expired", peer=partner,
                 target="%s:%d" % endpoint[partner],
                 silent_for=round(now - seen_at.get(partner, 0.0), 1))
            endpoint.pop(partner, None)
            seen_at.pop(partner, None)
        if target is None:
            # L'autre n'a pas encore parlé : on garde, on ne jette pas.
            held = waiting.setdefault(source, [])
            if len(held) < HELD:
                held.append(payload)
                if len(held) == 1:
                    note("relay_holding", peer=source, partner=partner)
            else:
                dropped[source] = dropped.get(source, 0) + 1
                if dropped[source] in (1, 100, 1000):
                    note("relay_hold_full", peer=source, partner=partner,
                         packets=dropped[source])
            continue

        # Le partenaire vient d'être localisé : on délivre d'abord ce qu'on
        # gardait, dans l'ordre, avant le paquet du moment.
        held = waiting.pop(partner, None)
        if held:
            note("relay_flushed", peer=partner, target=f"{source}:{port}",
                 packets=len(held))
            for kept in held:
                try:
                    sock.sendto(kept, (source, port))
                except OSError:
                    break
        try:
            sock.sendto(payload, target)
        except OSError as error:
            note("relay_send_failed", peer=source, target=f"{target[0]}:{target[1]}",
                 error=str(error))
            continue
        if counts[source] in (1, 10, 100, 1000, 10000):
            note("relay_forwarded", peer=f"{source}:{port}",
                 target=f"{target[0]}:{target[1]}", bytes=len(payload),
                 packets=counts[source])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
