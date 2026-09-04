"""Une trame qu'on ne sait pas lire entièrement ne doit pas couper la ligne.

Le 22 août 2026, deux consoles -- une en France, une en Algérie -- étaient
appariées et l'une essayait de rejoindre la partie de l'autre. Le serveur est
mort en décodant sa requête :

    ValueError: Unsupported TDF type 201 for @PCN at offset 0x7B

C'est un `joinGame` (composant 4, commande 9). Il porte un champ `RRST` qui
est un dictionnaire de chaînes vers structs -- une forme que ce projet
n'avait jamais rencontrée, et dont la grammaire exacte reste inconnue : le
champ occupe douze octets là où notre lecture n'en consomme que dix, et la
suite se resynchronise à 0x7D.

Cette grammaire n'est **pas** devinée ici. Ce qui est corrigé, c'est que
l'incompréhension était fatale : l'exception remontait jusqu'à la boucle de
connexion et fermait la socket Blaze du joueur à l'instant précis où il
essayait de rejoindre l'autre, trois fois de suite, ce que le jeu affiche
comme « serveur momentanément indisponible ».

Une trame Blaze est une suite de champs indépendants dans l'ordre des tags.
Ne pas savoir lire le septième ne rend pas les six premiers faux. Le mode
tolérant s'arrête à la première incompréhension, dit combien d'octets il
laisse derrière lui, et rend ce qu'il a lu.

La trame est gardée en entier parce qu'elle est la pièce à conviction, et
qu'elle contient la seule occurrence connue de cette forme.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from blaze_tdf import (  # noqa: E402
    INTEGER,
    STRING,
    Field,
    decode_frame,
    encode_fields,
    encode_frame,
)

JOIN_GAME = bytes.fromhex(
    "00DA000400090000000000708B4C2C090000009E5BB400009E990000009F6972010E"
    "71612D6F6E6C792D646179343500AAD9740004C2E9740600DA1B3503B618E90085D4"
    "E3DC12E249320224C0A8640869635C8E0C020017FA14CD2B0ADF72760030008422BF"
    "D400000000FA01000000E35A640095D69693B8FF800900CB2CF40501030101000001"
    "00CEC96E0000CECA6400BF03CECBF40001CF4CB40000D2993800BFFF07D739720386"
    "9900000086CBE300009788A20200978A64009996B5FEDDFF8009A64000009996B5FE"
    "DDFF8009BA1B65010100BF2A670000C29929000000E339730200"
)


def test_the_frame_is_a_join_game_request():
    """L'en-tête se lit toujours, même quand la charge utile résiste."""
    decoded = decode_frame(JOIN_GAME, tolerant=True)
    assert (decoded["component"], decoded["command"]) == (4, 9)


def test_strict_decoding_still_refuses_what_it_cannot_read():
    """Le mode strict ne ment pas : il échoue là où il ne comprend plus.

    C'est important. La tolérance est une décision de l'appelant, pas une
    dégradation silencieuse du décodeur -- les outils d'analyse hors ligne
    doivent continuer à voir l'erreur, sinon une trame mal lue passerait
    pour une trame bien lue.
    """
    with pytest.raises(ValueError):
        decode_frame(JOIN_GAME)


def test_tolerant_decoding_recovers_the_whole_frame():
    """La resynchronisation retrouve tout, y compris ce qui suivait.

    Le champ `RRST` reste incompris -- il occupe douze octets et notre
    lecture n'en consomme que dix. Mais la trame redevient lisible à 0x7D, et
    les sept champs qui suivaient sont là.
    """
    decoded = decode_frame(JOIN_GAME, tolerant=True)
    labels = [field.label for field in decoded["fields"]]
    assert labels == [
        "BTPL", "GENT", "GID", "GVER", "JMET", "PNET", "RRST",
        "SLEN", "SLID", "SLOT", "STRT", "TIDX", "USER", "XSES",
    ]
    assert decoded["leftover"] == 0


def test_the_recovered_tail_carries_the_player_being_joined():
    """Ce qui était perdu était exactement ce qui comptait.

    `GID` vaut zéro : le client ne désigne pas la partie par son numéro. Il
    désigne son hôte par son identifiant de joueur, dans `USER` -- et `USER`
    était dans la partie que le décodeur abandonnait. Sans lui, la requête
    est illisible dans son intention même, et le serveur répondait « aucune
    partie » à une demande valide.
    """
    fields = decode_frame(JOIN_GAME, tolerant=True)["fields"]
    by_label = {field.label: field for field in fields}
    assert by_label["GID"].value == 0
    assert by_label["SLOT"].value == 1
    user = {field.label: field.value for field in by_label["USER"].value}
    assert user["ID"] == 2535469248587161
    assert user["EXID"] == user["ID"]


def test_resynchronisation_refuses_a_plausible_looking_wrong_offset():
    """Décoder jusqu'au bout ne suffit pas à prouver qu'on est aligné.

    Quatre décalages de cette trame se décodent entièrement jusqu'au dernier
    octet. Trois sont faux, et se dénoncent par leurs étiquettes -- `]@TA`,
    `@P` -- qui ne peuvent pas être des tags. C'est cette seconde condition
    qui choisit le bon, et sans elle on prendrait le premier venu.
    """
    from blaze_tdf import Decoder, plausible_tag

    payload = JOIN_GAME[12:]
    decodes_fully = []
    for offset in range(0x71, 0x86):
        probe = Decoder(payload[offset:])
        try:
            fields = probe._all_strict()
        except (ValueError, IndexError):
            continue
        if fields and probe.position == len(payload) - offset:
            decodes_fully.append(offset)

    assert len(decodes_fully) > 1, "le critère serait inutile s'il n'y avait qu'un candidat"
    survivors = [
        offset for offset in decodes_fully
        if all(plausible_tag(f.label) for f in Decoder(payload[offset:])._all_strict())
    ]
    assert survivors[0] == 0x7D


def test_a_complete_frame_is_unaffected_by_tolerance():
    """La tolérance ne change rien à ce qui se lisait déjà.

    Le mode tolérant n'est pas un second décodeur : c'est le même, qui
    s'arrête au lieu de lever. Sur une trame entière il doit rendre
    exactement les mêmes champs que le mode strict, et ne rien laisser
    derrière lui -- sinon on aurait échangé une panne bruyante contre une
    perte silencieuse.
    """
    whole = encode_frame(
        4, 13, 0, 0, 1,
        encode_fields([
            Field("GID", INTEGER, 7),
            Field("GVER", STRING, "qa-only-day45"),
        ]),
    )
    strict = decode_frame(whole)
    lenient = decode_frame(whole, tolerant=True)
    assert [f.label for f in strict["fields"]] == ["GID", "GVER"]
    assert [(f.label, f.value) for f in lenient["fields"]] == [
        (f.label, f.value) for f in strict["fields"]
    ]
    assert lenient["leftover"] == 0
