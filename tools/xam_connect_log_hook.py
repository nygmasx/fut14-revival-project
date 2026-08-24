#!/usr/bin/env python3
"""Journalise passivement chaque `NetDll_connect`, et surtout qui l'appelle.

Pourquoi sur l'export de XAM, et pas sur un site d'appel du titre
-----------------------------------------------------------------

`docs/EASFC_NOT_CONNECTED.md` clôt une ligne de travail entière -- réécrire les
endpoints de `powdllzf` -- et laisse une question ouverte : le module
compose-t-il seulement ? Le crochet existant (`fifa14_connect_redirect.py`) est
posé sur **un site d'appel dans `default.xex`**, et `powdllzf.xex.dll` est un
module séparé, chargé à 0x89700000, avec son propre code lié. S'il embarque sa
propre copie de DirtySock, ses connexions passent à côté de ce crochet sans
jamais le toucher -- et l'absence de trace se lit alors « il ne compose pas »,
ce qui est peut-être faux. C'est écrit noir sur blanc dans ce document comme
l'hypothèse la moins chère et jamais essayée.

`NetDll_connect` est le goulot que personne ne contourne : c'est l'export de
`xam.xex`, et tout module qui ouvre une connexion TCP sur cette console y
passe. Un seul crochet, et la question se tranche dans les deux sens.

Ce que fait le shim, lu sur la console le 24 août 2026
------------------------------------------------------

    0x81741BF8  lis  r11, 0x81AC
    0x81741BFC  mr   r7, r6
    0x81741C00  mr   r6, r5
    0x81741C04  mr   r5, r4
    0x81741C08  mr   r4, r3
    0x81741C0C  lwz  r3, 0x7C04(r11)
    0x81741C10  b    +0x3740

Il décale les arguments d'un rang et **branche** (`b`, pas `bl`) vers
l'implémentation. Deux conséquences, et les deux comptent :

* à l'entrée, et seulement là, les registres portent encore la signature
  publique -- r3 = socket, r4 = sockaddr, r5 = longueur ;
* LR contient l'adresse de retour de **l'appelant** de `NetDll_connect`, pas
  celle d'un maillon interne. C'est la réponse cherchée : un LR en 0x897xxxxx
  est `powdllzf`, un LR en 0x82xxxxxx est le titre.

Le crochet est donc posé sur la première instruction, et il y branche sans
lien, pour ne pas écraser ce LR-là.

Ce qu'il enregistre, et ce qu'il ne fait pas
---------------------------------------------

Un compteur, puis un anneau de seize entrées de 0x20 octets :

    +0x00  numéro d'ordre
    +0x04  LR de l'appelant       <- la réponse cherchée
    +0x08  handle de socket
    +0x0C  longueur du sockaddr
    +0x10  les seize premiers octets du sockaddr, ou zéro s'il est nul

Il ne change aucun argument, aucune valeur de retour, aucun drapeau. Il exécute
l'instruction déplacée et reprend. C'est le sens de `log` dans le nom -- et dans
ce dépôt ce mot a déjà menti une fois : `fifa14_plain_send_hook`, dont la
branche `local_ack` avalait des requêtes en silence. Donc le stub est
ci-dessous en entier, et `--dry-run` le désassemble sans rien écrire.

Deux dangers, nommés
---------------------

**Le stub vit dans la mémoire du titre** (0x83C8FA00) alors que le crochet est
posé dans XAM. Si le titre est déchargé pendant que le crochet tient, XAM
branchera dans de la mémoire libérée : `restore` avant toute relance, sans
exception. `apply` refuse de s'installer si le titre n'est pas là.

**Un sockaddr nul ferait fauter le crochet**, et une faute dans XAM emmène la
console. La copie est donc gardée par un test explicite ; le sockaddr reste à
zéro dans l'entrée plutôt que de risquer la lecture.

    tools/xam_connect_log_hook.py 192.168.1.25 apply
    tools/xam_connect_log_hook.py 192.168.1.25 read
    tools/xam_connect_log_hook.py 192.168.1.25 restore
    tools/xam_connect_log_hook.py 192.168.1.25 apply --dry-run
"""

from __future__ import annotations

import argparse
import struct

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    branch,
    cmpwi,
    conditional_branch,
    insn,
    lwz,
    rlwinm,
    stw,
)

SITE = 0x81741BF8
# Les quatre instructions déplacées, et pourquoi quatre.
#
# XAM est à 0x817xxxxx, la mémoire de travail du titre à 0x83C8xxxx : 39 Mo les
# séparent, et un `b` PowerPC ne porte qu'à ±32 Mo. Aucun des deux sauts ne
# tient dans une instruction. Il faut donc la séquence absolue
# `lis`/`ori`/`mtctr`/`bctr`, qui occupe quatre mots -- d'où quatre instructions
# déplacées au lieu d'une.
#
# Les quatre sont relogeables telles quelles : un `lis` d'immédiat et trois
# `mr`, rien qui dépende de l'adresse où elles s'exécutent.
DISPLACED_WORDS = (
    0x3D6081AC,   # lis r11, 0x81AC
    0x7CC73378,   # mr  r7, r6
    0x7CA62B78,   # mr  r6, r5
    0x7C852378,   # mr  r5, r4
)
RESUME = SITE + 4 * len(DISPLACED_WORDS)
STUB = 0x83C8FA00
COUNTER = 0x83C8FB00
SLOTS_BASE = 0x83C8FB20              # après le compteur, pour que l'entrée 0 ne l'écrase pas
SLOTS = 16
SLOT_SIZE = 0x20
# `bc BO, BI, cible`. BO = 12 saute si le bit testé est **vrai**, BO = 4 s'il
# est faux. La garde doit sauter la copie quand le sockaddr est nul, c'est-à-dire
# quand CR0[EQ] est vrai : BO = 12.
#
# Écrit avec BO = 4 au premier essai, ce qui donnait un `bne` : la copie était
# sautée quand le pointeur était valide et exécutée quand il était nul. La garde
# faisait précisément ce qu'elle existait pour empêcher. Vu au désassemblage,
# jamais sur la console.
BRANCH_IF_TRUE = 12
CONDITION_EQUAL = 2                  # bit CR0[EQ]


def ori(ra: int, rs: int, immediate: int) -> int:
    return (24 << 26) | (rs << 21) | (ra << 16) | (immediate & 0xFFFF)


def load_address(register: int, address: int) -> list[int]:
    """`lis`/`ori` vers une adresse absolue.

    `ori` plutôt que `addi` : `addi` étend le signe de son immédiat, donc seize
    bits bas au-dessus de 0x7FFF comptent pour un nombre négatif et la moitié
    haute doit être remontée d'un pour compenser. `ori` ne signe rien. La
    compensation est facile à écrire juste et facile à oublier, et dans un
    crochet posé dans XAM une adresse fausse de 0x10000 ne se lit pas dans un
    message d'erreur : elle se lit sur la console qui s'éteint.
    """
    return [addis(register, 0, address >> 16), ori(register, register, address & 0xFFFF)]


def absolute_jump(register: int, target: int) -> list[int]:
    """Un saut inconditionnel hors de portée d'un `b`, sans toucher à LR.

    `bctr` et non `bctrl` : LR doit rester celui de l'appelant de
    `NetDll_connect`, puisque c'est très exactement ce qu'on est venu lire.
    CTR n'est pas vivant ici -- le shim se termine lui-même par un `b`.
    """
    return load_address(register, target) + [
        0x7C0903A6 | (register << 21),   # mtctr rN
        0x4E800420,                      # bctr
    ]


def build_stub() -> list[int]:
    """Le crochet, en entier, dans l'ordre où il s'exécute.

    r10, r11 et r12 sont les seuls registres touchés. Les trois sont volatiles
    dans l'ABI PowerPC de la 360, et r11 est de toute façon réécrit par la
    première instruction déplacée -- rien de vivant n'est abîmé.

    L'ordre compte : tout ce qui est lu (r3, r4, r5) l'est **avant** que les
    instructions déplacées ne décalent les arguments d'un rang. C'est le seul
    endroit de la fonction où ils portent encore la signature publique.
    """
    words: list[int] = []
    words += load_address(12, COUNTER)
    words += [
        lwz(11, 12, 0x00),           # r11 = compteur
        addi(11, 11, 1),
        stw(11, 12, 0x00),           # compteur += 1
        # r10 = (compteur & 15) * 0x20. Une rotation de 5 amène les quatre bits
        # bas en position 23..26, et le masque ne garde qu'eux : le modulo et la
        # multiplication en une instruction, sans dépassement possible.
        rlwinm(10, 11, 5, 23, 26),
    ]
    words += load_address(12, SLOTS_BASE)
    words += [
        add(12, 12, 10),             # r12 = l'entrée à écrire
        stw(11, 12, 0x00),           # numéro d'ordre
        0x7D4802A6,                  # mflr r10 -- lit LR sans y toucher
        stw(10, 12, 0x04),           # l'appelant : toute la question est là
        stw(3, 12, 0x08),            # handle de socket
        stw(5, 12, 0x0C),            # longueur du sockaddr
    ]

    # Le sockaddr, sous garde. Un pointeur nul est légal à l'appel et ferait
    # fauter la copie ; une faute dans XAM emmène la console.
    copy: list[int] = []
    for offset in (0x00, 0x04, 0x08, 0x0C):
        copy += [lwz(10, 4, offset), stw(10, 12, 0x10 + offset)]
    guard_at = STUB + 4 * (len(words) + 1)
    words += [
        cmpwi(4, 0),
        conditional_branch(
            guard_at, guard_at + 4 + 4 * len(copy),
            BRANCH_IF_TRUE, CONDITION_EQUAL,
        ),
    ]
    words += copy

    words += list(DISPLACED_WORDS)
    # r10, et surtout pas r11.
    #
    # La première instruction déplacée est `lis r11, 0x81AC`, et le shim s'en
    # sert quatre instructions après la reprise : `lwz r3, 0x7C04(r11)`. Un
    # saut de retour qui construit son adresse dans r11 détruit donc très
    # exactement ce que la copie venait de restaurer, et le shim va chercher
    # son contexte à une adresse inventée. r10 est mort ici, et volatile.
    words += absolute_jump(10, RESUME)
    return words


def stub_bytes() -> bytes:
    return b"".join(insn(word) for word in build_stub())


def title_is_up(client: "Xbdm") -> bool:
    try:
        return "default.xex" in "".join(str(l) for l in client.multiline("xbeinfo running"))
    except Exception:
        return False


def read_ring(client: "Xbdm") -> tuple[int, list[dict]]:
    count = struct.unpack(">I", client.read(COUNTER, 4))[0]
    raw = client.read(SLOTS_BASE, SLOTS * SLOT_SIZE)
    entries = []
    for index in range(SLOTS):
        chunk = raw[index * SLOT_SIZE:(index + 1) * SLOT_SIZE]
        sequence, caller, handle, length = struct.unpack_from(">IIII", chunk)
        if sequence == 0:
            continue
        entries.append({
            "sequence": sequence,
            "caller": caller,
            "handle": handle,
            "length": length,
            "sockaddr": chunk[0x10:0x20],
        })
    return count, sorted(entries, key=lambda entry: entry["sequence"])


def describe(caller: int) -> str:
    if 0x89700000 <= caller < 0x89850000:
        return "powdllzf"
    if 0x82000000 <= caller < 0x83F20000:
        return "default.xex"
    if 0x815F0000 <= caller < 0x81C00000:
        return "xam.xex"
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("apply", "read", "restore", "state"))
    parser.add_argument("--dry-run", action="store_true",
                        help="montrer ce qui serait écrit, sans rien écrire")
    args = parser.parse_args()

    stub = stub_bytes()
    patch = b"".join(insn(word) for word in absolute_jump(11, STUB))
    original = b"".join(insn(word) for word in DISPLACED_WORDS)

    if args.dry_run:
        print(f"site      0x{SITE:08X}: {original.hex()}")
        print(f"       -> {patch.hex()}")
        print(f"reprise   0x{RESUME:08X}")
        print(f"stub      0x{STUB:08X}, {len(stub)} octets")
        for index, word in enumerate(build_stub()):
            print(f"  0x{STUB + 4 * index:08X}: {word & 0xFFFFFFFF:08X}")
        print(f"compteur  0x{COUNTER:08X}")
        print(f"anneau    0x{SLOTS_BASE:08X}, {SLOTS} x 0x{SLOT_SIZE:02X}")
        return 0

    client = Xbdm(args.host)
    try:
        if args.action == "apply":
            if not title_is_up(client):
                print("le titre n'est pas chargé -- le stub vivrait dans le vide")
                return 1
            present = client.read(SITE, len(original))
            if present != original:
                print(f"0x{SITE:08X} porte {present.hex()}, pas les instructions attendues")
                return 1
            client.write(COUNTER, bytes(4))
            client.write(SLOTS_BASE, bytes(SLOTS * SLOT_SIZE))
            client.write(STUB, stub)
            written = client.read(STUB, len(stub))
            if written != stub:
                print("le stub ne s'est pas écrit tel quel -- rien n'est branché")
                return 1
            client.write(SITE, patch)
            print(f"crochet posé sur 0x{SITE:08X}; `restore` avant toute relance")
            return 0

        if args.action == "restore":
            client.write(SITE, original)
            back = client.read(SITE, len(original))
            print("restauré" if back == original
                  else f"échec: 0x{SITE:08X} porte {back.hex()}")
            return 0

        if args.action == "state":
            present = client.read(SITE, len(patch))
            hooked = present == patch
            print(f"0x{SITE:08X}: {present.hex()} -> {'crocheté' if hooked else 'intact'}")
            return 0

        count, entries = read_ring(client)
        print(f"{count} appels à NetDll_connect depuis la pose du crochet")
        for entry in entries:
            family, port = struct.unpack_from(">HH", entry["sockaddr"])
            address = ".".join(str(b) for b in entry["sockaddr"][4:8])
            print(
                f"  #{entry['sequence']:<4} appelant 0x{entry['caller']:08X}"
                f" ({describe(entry['caller'])})"
                f"  socket 0x{entry['handle']:08X}"
                f"  famille {family} {address}:{port}"
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
