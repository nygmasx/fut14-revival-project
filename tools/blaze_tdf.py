#!/usr/bin/env python3
"""Minimal Blaze 3 ProtoFire/TDF codec used by the FIFA 14 revival tools."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# The smallest first byte any TDF tag can have. `encode_tag` puts
# (0x20 | c & 0x1F) in the top six bits of a 24-bit value, so the leading byte
# is never below 0x20 << 2. Everything below this is therefore not a tag, which
# is what makes a union inside a list of structs decodable -- see `list_item`.
TAG_FIRST_BYTE = 0x80

INTEGER = 0
STRING = 1
BINARY = 2
STRUCT = 3
LIST = 4
MAP = 5
UNION = 6
VARIABLE = 7
OBJECT_TYPE = 8
OBJECT_ID = 9


@dataclass
class Field:
    label: str
    type: int
    value: Any


def encode_tag(label: str) -> bytes:
    label = label[:4]
    value = 0
    for index, character in enumerate(label):
        value |= (0x20 | (ord(character) & 0x1F)) << ((3 - index) * 6)
    return value.to_bytes(3, "big")


def decode_tag(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    label = "".join(
        chr(((((value >> shift) & 0x3F) & 0x1F) | 0x40))
        for shift in (18, 12, 6, 0)
    )
    return label.rstrip("@")


def encode_integer(value: int) -> bytes:
    if value < 0:
        value = ((-value) << 1) | 1
    if value < 0x40:
        return bytes((value,))
    output = bytearray(((value & 0x3F) | 0x80,))
    value >>= 6
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


TAG_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ "
)


def plausible_tag(label: str) -> bool:
    """Un tag Blaze ne s'écrit qu'avec ces caractères-là.

    Les quatre caractères d'un tag sortent d'un encodage sur six bits qui ne
    produit rien d'autre. Une étiquette qui contient `]`, `@` ou `[` n'est pas
    un tag : c'est du bruit lu comme un tag, donc la preuve qu'on est décalé.
    """
    return bool(label) and all(character in TAG_ALPHABET for character in label)


class Decoder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0
        self.leftover = 0
        self.resynchronised_at = None
        self.skipped = 0

    def take(self, size: int) -> bytes:
        end = self.position + size
        if end > len(self.data):
            raise ValueError(f"TDF truncated at offset 0x{self.position:X}")
        result = self.data[self.position:end]
        self.position = end
        return result

    def byte(self) -> int:
        return self.take(1)[0]

    def integer(self) -> int:
        value = self.byte()
        if value < 0x80:
            return value
        result = value & 0x3F
        for index in range(1, 9):
            value = self.byte()
            result |= (value & 0x7F) << ((index * 7) - 1)
            if value < 0x80:
                break
        return result

    def string(self) -> str:
        size = self.integer()
        if size == 0:
            return ""
        raw = self.take(size)
        if raw[-1] != 0:
            raise ValueError("TDF string is not NUL terminated")
        return raw[:-1].decode("ascii", "replace")

    def struct(self) -> list[Field]:
        fields: list[Field] = []
        while self.position < len(self.data) and self.data[self.position] != 0:
            fields.append(self.field())
        if self.position >= len(self.data):
            raise ValueError("Unterminated TDF struct")
        self.position += 1
        return fields

    def list_item(self, item_type: int, unions: bool = True) -> Any:
        if item_type == INTEGER:
            return self.integer()
        if item_type == STRING:
            return self.string()
        if item_type == STRUCT:
            # A list of unions is declared on the wire as a list of structs,
            # and each element then begins with one byte naming the active
            # member. `createGame` carries exactly that in `HNET`, its list of
            # NetworkAddress: item type 3, count 1, and then 0x00 for
            # XboxClientAddress before MACI/XDDR/XUID.
            #
            # Read as a plain struct, that 0x00 is a terminator -- so the
            # element decoded as empty, the union's own fields were read as
            # siblings of HNET, and the real terminator forty bytes later
            # became a nonsense tag. The frame took the Blaze connection down
            # with it on 21 August, which is how this was found.
            #
            # The two cases are disjoint rather than guessed at. A tag's first
            # byte is at least 0x80: the first character contributes
            # (0x20 | c & 0x1F) << 18, whose smallest value is 0x20 << 18. An
            # active-member index is a small number. So a struct element
            # always starts >= 0x80 and a union element always starts below
            # it, and 852 struct lists across this repo's captures decode
            # identically either way.
            #
            # The one shape this cannot tell apart is an empty struct element,
            # which is also a lone 0x00. None has ever appeared here, and a
            # union with no members would be indistinguishable from it on the
            # wire in any decoder.
            #
            # `unions` is False for map keys and values, and that is not a
            # detail. `joinGame` carries a map of strings to structs whose one
            # value is an *empty* struct -- a lone 0x00 -- and this rule read
            # that as a union index and then swallowed the fields after it.
            # The frame died at "Unsupported TDF type 201 for @PCN", which is
            # what a desynchronised decoder always looks like.
            #
            # Blaze has lists of unions; it does not have maps of them. So the
            # rule belongs to lists, where the evidence for it came from, and
            # nowhere else.
            if (unions and self.position < len(self.data)
                    and self.data[self.position] < TAG_FIRST_BYTE):
                return (self.byte(), self.struct())
            return self.struct()
        if item_type == OBJECT_TYPE:
            return (self.integer(), self.integer())
        if item_type == OBJECT_ID:
            return (self.integer(), self.integer(), self.integer())
        raise ValueError(
            f"Unsupported TDF list item type {item_type} "
            f"at offset 0x{self.position:X}"
        )

    def field(self) -> Field:
        offset = self.position
        label = decode_tag(self.take(3))
        field_type = self.byte()
        if field_type == INTEGER:
            value: Any = self.integer()
        elif field_type == STRING:
            value = self.string()
        elif field_type == BINARY:
            value = self.take(self.integer())
        elif field_type == STRUCT:
            value = self.struct()
        elif field_type == LIST:
            item_type = self.byte()
            count = self.integer()
            value = (item_type, [self.list_item(item_type) for _ in range(count)])
        elif field_type == MAP:
            key_type = self.byte()
            value_type = self.byte()
            count = self.integer()
            pairs = [
                (
                    self.list_item(key_type, unions=False),
                    self.list_item(value_type, unions=False),
                )
                for _ in range(count)
            ]
            value = (key_type, value_type, pairs)
        elif field_type == VARIABLE:
            # A variable TDF: a flag, and when it is set, the 32-bit id of the
            # class that follows and then that class's fields.
            #
            #     <u8 set>  [ <varint tdfId>  <fields...>  0x00 ]
            #
            # Every offline game report FIFA has ever submitted here carries
            # three of these -- `PRVT` unset, `GAME` holding the report class,
            # `CGRT` holding the club record -- and this decoder had no case
            # for type 7 at all. It raised, the exception took the Blaze
            # connection down with it, and the report was lost. Sixteen
            # connection_error lines in the journals say so, all of them
            # component 28 command 2, all of them "Unsupported TDF type 7 for
            # PRVT at offset 0x5".
            #
            # The shape was read off those captures rather than assumed: it is
            # the only rule under which all 74 bytes of one frame and all 175
            # of the other decode to the end with nothing left over.
            if self.byte() == 0:
                value = None
            else:
                tdf_id = self.integer()
                value = (tdf_id, self.struct())
        elif field_type == UNION:
            active = self.byte()
            value = (active, None if active == 0x7F else self.field())
        elif field_type == OBJECT_TYPE:
            value = (self.integer(), self.integer())
        elif field_type == OBJECT_ID:
            value = (self.integer(), self.integer(), self.integer())
        else:
            raise ValueError(
                f"Unsupported TDF type {field_type} for {label} "
                f"at offset 0x{offset:X}"
            )
        return Field(label, field_type, value)

    def all(self, tolerant: bool = False) -> list[Field]:
        """Décode les champs. En mode tolérant, s'arrête à la première
        incompréhension au lieu de la propager.

        Une trame Blaze est une suite de champs indépendants, dans l'ordre des
        tags. Si l'on ne sait pas lire le neuvième, cela ne rend pas les huit
        premiers faux -- ils ont déjà été lus, entièrement, et ils portent
        presque toujours ce qui compte. Le `joinGame` du 22 août en est
        l'exemple : il s'est cassé sur un dictionnaire de chaînes vers structs,
        mais `GID`, le numéro de la partie à rejoindre, était lu depuis
        longtemps.

        Le reste est perdu, et c'est assumé -- pas deviné. `self.leftover`
        retient combien d'octets n'ont pas été compris, pour que l'appelant
        sache qu'il travaille sur une lecture partielle et que le journal
        garde de quoi finir le travail plus tard.
        """
        if not tolerant:
            return self._all_strict()
        fields: list[Field] = []
        while self.position < len(self.data):
            mark = self.position
            try:
                fields.append(self.field())
            except (ValueError, IndexError):
                self.position = mark
                recovered = self.resynchronise()
                if recovered is None:
                    break
                fields.extend(recovered)
                break
        self.leftover = len(self.data) - self.position
        return fields

    def resynchronise(self) -> list[Field] | None:
        """Reprendre après un champ qu'on ne sait pas mesurer.

        C'est une resynchronisation, pas une grammaire : on ne prétend pas
        comprendre le champ fautif, on cherche où la trame redevient lisible.
        Deux conditions, et elles sont strictes.

        D'abord, le reste doit se décoder **exactement** jusqu'au dernier
        octet. Un décalage d'un seul octet produit presque toujours un type
        inconnu ou une longueur qui dépasse la fin ; tomber pile sur la fin
        par hasard est possible mais rare, et c'est ce qui rend le critère
        utile.

        Ensuite, toutes les étiquettes retrouvées doivent être de vrais tags.
        Sur le `joinGame` du 22 août, quatre décalages décodaient jusqu'au
        bout -- mais trois rendaient des étiquettes comme `]@TA` ou `@P`, et
        un seul rendait `SLEN SLID SLOT STRT TIDX USER XSES`. Sans cette
        seconde condition on aurait pris le premier, et lu de travers.

        On prend le plus proche qui satisfait les deux. S'il n'y en a aucun,
        on ne rend rien : mieux vaut une trame amputée qu'une trame inventée.
        """
        start = self.position
        end = len(self.data)
        for offset in range(start + 1, end):
            probe = Decoder(self.data[offset:])
            try:
                candidate = probe._all_strict()
            except (ValueError, IndexError):
                continue
            if probe.position != end - offset or not candidate:
                continue
            if not all(plausible_tag(field.label) for field in candidate):
                continue
            self.position = end
            self.resynchronised_at = offset
            self.skipped = offset - start
            return candidate
        return None

    def _all_strict(self) -> list[Field]:
        fields: list[Field] = []
        while self.position < len(self.data):
            fields.append(self.field())
        return fields


def encode_string(value: str) -> bytes:
    raw = value.encode("ascii") + b"\0"
    return encode_integer(len(raw)) + raw


def encode_item(item_type: int, value: Any) -> bytes:
    if item_type == INTEGER:
        return encode_integer(int(value))
    if item_type == STRING:
        return encode_string(str(value))
    if item_type == STRUCT:
        # `(active, fields)` is a union element -- see `Decoder.list_item`.
        if isinstance(value, tuple):
            active, fields = value
            return bytes((active,)) + encode_fields(fields) + b"\0"
        return encode_fields(value) + b"\0"
    if item_type == OBJECT_TYPE:
        return encode_integer(value[0]) + encode_integer(value[1])
    if item_type == OBJECT_ID:
        return (
            encode_integer(value[0])
            + encode_integer(value[1])
            + encode_integer(value[2])
        )
    raise ValueError(f"Unsupported TDF item type {item_type}")


def encode_field(field: Field) -> bytes:
    output = bytearray(encode_tag(field.label))
    output.append(field.type)
    if field.type == INTEGER:
        output += encode_integer(int(field.value))
    elif field.type == STRING:
        output += encode_string(str(field.value))
    elif field.type == BINARY:
        output += encode_integer(len(field.value))
        output += field.value
    elif field.type == STRUCT:
        output += encode_fields(field.value)
        output.append(0)
    elif field.type == LIST:
        item_type, values = field.value
        output.append(item_type)
        output += encode_integer(len(values))
        for value in values:
            output += encode_item(item_type, value)
    elif field.type == MAP:
        key_type, value_type, pairs = field.value
        output += bytes((key_type, value_type))
        output += encode_integer(len(pairs))
        for key, value in pairs:
            output += encode_item(key_type, key)
            output += encode_item(value_type, value)
    elif field.type == VARIABLE:
        if field.value is None:
            output.append(0)
        else:
            tdf_id, fields = field.value
            output.append(1)
            output += encode_integer(tdf_id)
            output += encode_fields(fields)
            output.append(0)
    elif field.type == UNION:
        active, nested = field.value
        output.append(active)
        if nested is not None:
            output += encode_field(nested)
    elif field.type == OBJECT_TYPE:
        output += encode_integer(field.value[0])
        output += encode_integer(field.value[1])
    elif field.type == OBJECT_ID:
        output += encode_integer(field.value[0])
        output += encode_integer(field.value[1])
        output += encode_integer(field.value[2])
    else:
        raise ValueError(f"Unsupported TDF type {field.type}")
    return bytes(output)


def encode_fields(fields: list[Field]) -> bytes:
    return b"".join(encode_field(field) for field in fields)


def encode_frame(
    component: int,
    command: int,
    error: int,
    message_type: int,
    message_number: int,
    payload: bytes,
) -> bytes:
    if len(payload) > 0xFFFF:
        raise ValueError("Extended ProtoFire frames are not implemented")
    return (
        len(payload).to_bytes(2, "big")
        + component.to_bytes(2, "big")
        + command.to_bytes(2, "big")
        + error.to_bytes(2, "big")
        + bytes(((message_type & 0xF) << 4, (message_number >> 16) & 0xF))
        + (message_number & 0xFFFF).to_bytes(2, "big")
        + payload
    )


def json_value(value: Any) -> Any:
    if isinstance(value, Field):
        return {
            "label": value.label,
            "type": value.type,
            "value": json_value(value.value),
        }
    if isinstance(value, bytes):
        return {"hex": value.hex().upper()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, list):
        return [json_value(item) for item in value]
    return value


def decode_frame(data: bytes, tolerant: bool = False) -> dict[str, Any]:
    if len(data) < 12:
        raise ValueError("ProtoFire frame is shorter than its header")
    payload_size = int.from_bytes(data[0:2], "big")
    if len(data) != 12 + payload_size:
        raise ValueError(
            f"ProtoFire size mismatch: header={payload_size}, "
            f"actual={len(data) - 12}"
        )
    decoder = Decoder(data[12:])
    fields = decoder.all(tolerant=tolerant)
    message_type = data[8] >> 4
    message_number = ((data[9] & 0xF) << 16) | int.from_bytes(data[10:12], "big")
    return {
        "payload_size": payload_size,
        "component": int.from_bytes(data[2:4], "big"),
        "command": int.from_bytes(data[4:6], "big"),
        "error": int.from_bytes(data[6:8], "big"),
        "message_type": message_type,
        "message_number": message_number,
        "fields": fields,
        "leftover": decoder.leftover,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frame", type=Path)
    args = parser.parse_args()
    result = decode_frame(args.frame.read_bytes())
    print(json.dumps(json_value(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
