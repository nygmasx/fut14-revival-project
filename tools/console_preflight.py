#!/usr/bin/env python3
r"""Est-ce que la console est prête, et si non, laquelle des raisons.

Chaque échec de ce projet ressemble aux autres vu du Mac. Le titre reste sur un
chargeur, ou le patch « ne prend pas », et la cause est presque toujours en
amont : la console a redémarré et l'exploit est parti avec, le jeu n'est pas
installé là où on croit, le TU3 n'est pas là. Ces trois-là se vérifient sans
rien écrire, en quelques secondes, et se distinguent les unes des autres — ce
qu'un timeout de patcheur ne fait pas.

    tools/console_preflight.py            l'adresse vient de fifa14revival.ini
    tools/console_preflight.py <console-ip>

Rien n'est écrit sur la console : `dirlist` et `xbeinfo` sont en lecture seule.

Sur un softmod BadUpdate, XBDM absent n'est pas une panne : c'est l'état normal
d'une console qui a redémarré. Les correctifs de l'hyperviseur sont volatils,
donc la question « XBDM répond-il » est la même que « l'exploit tourne-t-il
encore », et elle doit être posée en premier.
"""

from __future__ import annotations

import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import revival_config  # noqa: E402

# Le titre, et les deux dossiers de contenu qui décident si on peut jouer.
# 454109C3 est FIFA 14 : c'est sous cet identifiant que le TU3 de ce projet a
# toujours vécu (docs/TU3_STATIC_PATCH.md).
TITLE_ID = "454109C3"
CONTENT = r"Hdd:\Content\0000000000000000"
GAME_INSTALL = "00007000"   # le jeu installé depuis le disque
TITLE_UPDATES = "000B0000"  # les mises à jour de titre
SAVES = "00004000"          # les sauvegardes -- présentes même sans le jeu

# La signature du build visé (README, « Supported build »). Toutes les adresses
# statiques de ce dépôt ne valent que pour lui.
#
# Le timestamp est celui du `default.xex` **corrigé par le TU3**, pas celui du
# disque : un TU de 360 patche l'exécutable au chargement, donc l'en-tête lue
# par `modules` est celle d'après le patch. Mesuré ici le 15 août 2026, un
# disque sans TU rapporte 0x5221C86B -- le 31 août 2013, la version de sortie --
# là où le TU3 rapporte 0x534C8977, le 15 avril 2014. Une console qui présente
# le premier n'a pas un jeu d'une autre région : elle n'a pas le TU3.
EXPECTED_TIMESTAMP = 0x534C8977
RETAIL_TIMESTAMP = 0x5221C86B
EXPECTED_BASE = 0x82000000
EXPECTED_POWDLL_BASE = 0x89700000

OK, WARN, BAD = "  OK  ", " !!   ", " XX   "


class Console:
    def __init__(self, host: str, port: int = 730) -> None:
        self.host = host
        self.port = port

    def command(self, text: str, wait: float = 1.0) -> str:
        sock = socket.create_connection((self.host, self.port), timeout=8)
        try:
            sock.recv(256)
            sock.sendall(text.encode() + b"\r\n")
            time.sleep(wait)
            sock.settimeout(2.5)
            chunks = b""
            try:
                while True:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chunks += data
                    if chunks.rstrip().endswith(b"\r\n.") or chunks.startswith(b"4"):
                        break
            except socket.timeout:
                pass
            return chunks.decode(errors="replace")
        finally:
            sock.close()

    def listing(self, path: str) -> list[str] | None:
        """Les noms d'un dossier, ou None s'il n'existe pas.

        XBDM répond `414- access denied` aussi bien pour un chemin absent que
        pour un chemin interdit, donc on ne peut pas distinguer les deux — et
        pour ce qu'on demande ici, absent est la seule lecture utile.
        """
        out = self.command(f'dirlist name="{path}\\"')
        if out.startswith("4"):
            return None
        return [m.group(1) for line in out.splitlines()
                if (m := re.search(r'name="([^"]+)"', line))]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    host = args[0] if args else revival_config.value("console.address")
    mac = revival_config.server_host()

    print(f"console {host}   serveur {mac}\n")

    console = Console(host)

    # 1. XBDM. Tout le reste en dépend, donc on s'arrête ici si c'est non.
    try:
        version = console.command("dmversion").strip()
    except OSError:
        print(f"{BAD} XBDM ne répond pas sur {host}:730")
        print("       console éteinte, redémarrée (les correctifs sont volatils),")
        print("       exploit non relancé, ou xbdm.xex absent de launch.ini")
        return 1
    print(f"{OK} XBDM {version.replace('200- ', '')}")

    # 2. Quel titre tourne. FIFA déjà chargé veut dire qu'il faut le quitter :
    #    réentrer dans FUT demande un relancement complet du titre.
    running = console.command("xbeinfo running")
    name = re.search(r'name="([^"]+)"', running)
    current = name.group(1) if name else "?"
    short = current.rsplit("\\", 1)[-1]
    if "fifa" in current.lower() or "default.xex" in short.lower():
        print(f"{WARN} titre en cours : {short} -- quitter avant d'armer")
    else:
        print(f"{OK} titre en cours : {short}")

    # 3. Le jeu et le TU3.
    kinds = console.listing(rf"{CONTENT}\{TITLE_ID}") or []
    ready = True

    # Attention au faux négatif, il a coûté une heure ici le 15 août 2026 :
    # un jeu installé en **paquet STFS** se monte sous
    # `\Device\Package_<hash>\` et n'apparaît sous `Content` sous aucune de ses
    # dix racines. Absent ici ne veut donc pas dire absent de la console -- la
    # seule preuve qui vaut est qu'il démarre, et le contrôle de build plus bas
    # la fournit. Ce test informe, il ne juge pas.
    if GAME_INSTALL in kinds:
        print(f"{OK} FIFA 14 installé ({TITLE_ID}\\{GAME_INSTALL})")
    elif console.listing(r"Hdd:\Games"):
        print(f"{OK} Hdd:\\Games présent")
    else:
        print(f"{WARN} pas de {GAME_INSTALL} ni de Hdd:\\Games -- normal pour un paquet STFS")

    # Le nom du fichier ne dit pas quel TU c'est.
    #
    # Vérifié sur cette console le 15 août 2026 : Aurora stocke deux mises à
    # jour différentes -- 64,4 Mo et 149,8 Mo -- et les nomme *toutes les deux*
    # `tu00000010_00000000`, en ne les distinguant que par le dossier de hash
    # au-dessus. Le dépôt, lui, a toujours parlé de `tu00000003_00000000`. Se
    # fier au nom fait donc refuser une console parfaitement bonne.
    #
    # Ce qui identifie un TU, c'est sa taille (157 052 928 octets pour celui-ci,
    # docs/TU3_STATIC_PATCH.md) et surtout le timestamp du `default.xex` qu'il
    # produit une fois chargé. Le contrôle de build plus bas est le juge ; ceci
    # ne fait qu'informer.
    #
    # Aurora applique le TU au lancement plutôt qu'à l'installation : ce dossier
    # est vide tant que le titre n'a pas démarré au moins une fois.
    updates = console.listing(rf"{CONTENT}\{TITLE_ID}\{TITLE_UPDATES}") if TITLE_UPDATES in kinds else None
    if updates:
        for update in updates:
            print(f"{OK} mise à jour présente : {update}")
    else:
        print(f"{WARN} aucun TU sous {TITLE_UPDATES} -- Aurora l'applique au lancement")

    if SAVES in kinds:
        print(f"{OK} sauvegardes FIFA 14 présentes ({SAVES})")

    # 4. Le build, si le titre tourne. C'est la seule vérification qui demande
    #    FIFA chargé, et c'est la seule qui réponde vraiment « ce dépôt
    #    s'applique-t-il à ce jeu ».
    if "default.xex" in running or "fifa" in current.lower():
        mods = console.command("modules", wait=2.5)
        line = next((l for l in mods.splitlines() if 'name="default.xex"' in l), "")
        stamp = re.search(r"timestamp=0x([0-9a-fA-F]+)", line)
        base = re.search(r"base=0x([0-9a-fA-F]+)", line)
        if stamp:
            found = int(stamp.group(1), 16)
            if found == EXPECTED_TIMESTAMP:
                print(f"{OK} build 0x{found:08X} -- celui du dépôt")
            elif found == RETAIL_TIMESTAMP:
                print(f"{BAD} build 0x{found:08X} : disque retail sans TU3")
                print("       le TU3 est ce qui amène ce timestamp à 0x534C8977")
                ready = False
            else:
                print(f"{BAD} build 0x{found:08X}, attendu 0x{EXPECTED_TIMESTAMP:08X} -- inconnu")
                print("       ne rien patcher : toutes les adresses sont propres à un build")
                ready = False
        if base and int(base.group(1), 16) != EXPECTED_BASE:
            print(f"{WARN} base 0x{int(base.group(1), 16):08X}, attendu 0x{EXPECTED_BASE:08X}")
        if "powdllzf" in mods:
            pow_line = next(l for l in mods.splitlines() if "powdllzf" in l)
            pb = re.search(r"base=0x([0-9a-fA-F]+)", pow_line)
            if pb:
                same = int(pb.group(1), 16) == EXPECTED_POWDLL_BASE
                print(f"{OK if same else WARN} powdllzf @ 0x{int(pb.group(1), 16):08X}")
    else:
        print(f"{WARN} build non vérifié : lancer FIFA une fois et relancer ce script")
        ready = False

    print()
    print("PRÊT pour tools/fut.sh" if ready else "PAS PRÊT -- voir les lignes XX ci-dessus")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
