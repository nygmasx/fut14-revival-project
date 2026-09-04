"""Le dixième type TDF, trouvé dans un rapport de match.

Le 22 août 2026, une console a envoyé un `submitGameReport` et le décodeur est
mort dessus :

    ValueError: Unsupported TDF type 10 for CRAT at offset 0xD3

La liste des types s'arrêtait à `OBJECT_ID`, neuvième. Le dixième est un
flottant sur 32 bits, gros boutiste comme tout le reste du protocole -- quatre
octets, après quoi le champ suivant tombe pile. Rien d'exotique : un rapport
de match porte des moyennes et des notes. Aucune trame lue jusque-là n'en
contenait, voilà tout.

Cette trame est aussi la preuve qu'une resynchronisation peut se tromper. Avant
la découverte, la lecture tolérante rendait `FNSH, PRVT, YSDU` -- et `YSDU`
n'existe pas. Le décalage retenu consommait toute la trame et ses étiquettes
passaient le test de plausibilité ; la lecture était fausse quand même. Le vrai
troisième champ est `RPRT`, le rapport lui-même.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from blaze_tdf import (  # noqa: E402
    FLOAT,
    decode_frame,
    encode_fields,
    encode_frame,
)

GAME_REPORT = bytes.fromhex(
    "01E6001C00020000000000729AECE80000C32DB40700CB0CB4039E1B6507019BFDB5"
    "C50F9E1B65038E7CB407009E1B7203872A6400008E7CB407019DD5DD9F1D8E7CB403"
    "8ED9F203CB6B2D0000DEEC2B000000B64A66000200008F4A6400009F2A6400009F4A"
    "6D00BF56A73A6D0000B27A640000CA1BAB0000CAFA640000CE5A640000CF4D730000"
    "D39C25010B67616D655479706539350000A66C320700C2CE72050003019996B5FEDD"
    "FF80098E4BA600008F0CB407018CADAE880B8F0CB4038EDC32038E1CF300008E7B23"
    "00008EFCAE00048F0CE100A2028F0CED0093028F28740A000000008F387600008F3A"
    "2F00088F4AE100178F4AED000F9AFD6C00009EF86C0001A6ED230000BE69B30003BF"
    "79EC0000C2B9EC0000C338F4008301CA48E40000CE89EC0005D6ECE30000E778E400"
    "0000CE3C32039EC8670000B2FCF30000CB3B340000CE88670003CE89EC0001D2586D"
    "008803D299730000DE9BB300010000008F38EF00008F4CB900009A8CAE00009F2B34"
    "0000A2FB650000B2FCF30000BA1B65010100BF08F40000BF0C320300C25A640000C2"
    "EA640000C30BA1010100C348670000CA5B340000CE3BF20000CE5CA70000CEBA6C00"
    "00CEBC340000D2586D002CD299730000DE4BA60000DE9BB3000000D21B7207000000"
    "9F2A6400009F4E70010B67616D655479706539350000"
)


def test_the_report_decodes_without_tolerance():
    """Mode strict : plus rien à rattraper une fois le type connu."""
    decoded = decode_frame(GAME_REPORT)
    assert (decoded["component"], decoded["command"]) == (28, 2)
    assert [f.label for f in decoded["fields"]] == ["FNSH", "PRVT", "RPRT"]


def test_the_report_re_encodes_byte_for_byte():
    """La preuve que rien n'est deviné.

    Un décodeur qui se trompe de longueur peut produire des champs plausibles ;
    il ne peut pas reconstruire les mêmes octets.
    """
    decoded = decode_frame(GAME_REPORT)
    again = encode_frame(
        decoded["component"], decoded["command"], decoded["error"],
        decoded["message_type"], decoded["message_number"],
        encode_fields(decoded["fields"]),
    )
    assert again == GAME_REPORT
    assert len(again) == 498


def test_a_float_survives_a_round_trip():
    from blaze_tdf import Decoder, Field, encode_field

    original = Field("CRAT", FLOAT, 0.0)
    round_tripped = Decoder(encode_field(original)).field()
    assert round_tripped.label == "CRAT"
    assert round_tripped.type == FLOAT
    assert round_tripped.value == 0.0


def test_the_frame_no_longer_needs_resynchronising():
    """Et la resynchronisation, du coup, ne s'invente plus de champ.

    C'est elle qui rendait `YSDU`. Une lecture franche la rend inutile ici, et
    `resynchronised` le dit : personne n'a eu à deviner.
    """
    decoded = decode_frame(GAME_REPORT, tolerant=True)
    assert decoded["resynchronised"] is None
    assert decoded["leftover"] == 0
    assert [f.label for f in decoded["fields"]] == ["FNSH", "PRVT", "RPRT"]
