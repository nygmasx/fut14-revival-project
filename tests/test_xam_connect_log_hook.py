"""Le crochet posé dans XAM, vérifié avant d'atteindre la console.

Une faute dans ce stub ne produit pas un message d'erreur : elle produit une
console éteinte, et un aller-retour au bouton d'alimentation. Deux fautes ont
été trouvées au désassemblage le 24 août 2026, avant la pose -- la garde du
sockaddr écrite à l'envers, et un saut de retour qui détruisait le registre que
l'instruction déplacée venait de restaurer. Ni l'une ni l'autre n'aurait été
visible autrement qu'en lisant les mnémoniques une par une.

Ce fichier fait cette lecture à chaque exécution de la suite.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import xam_connect_log_hook as HOOK  # noqa: E402

try:
    from capstone import CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN, Cs
except ImportError:  # pragma: no cover
    Cs = None


def disassemble(data: bytes, base: int) -> list[tuple[int, str, str]]:
    engine = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    return [(i.address, i.mnemonic, i.op_str) for i in engine.disasm(data, base)]


@unittest.skipIf(Cs is None, "capstone absent")
class StubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.code = disassemble(HOOK.stub_bytes(), HOOK.STUB)
        self.text = [f"{m} {o}".strip() for _, m, o in self.code]

    def test_every_word_disassembles(self) -> None:
        """Un mot qu'aucun désassembleur ne lit est un mot que la console exécute."""
        self.assertEqual(len(self.code), len(HOOK.stub_bytes()) // 4)

    def test_the_sockaddr_guard_skips_the_copy_and_not_the_opposite(self) -> None:
        """La garde doit sauter la copie quand le pointeur n'est pas plausible.

        Toute adresse virtuelle de cette console a le bit de poids fort à un,
        donc se compare comme un nombre négatif : `bge` saute sur zéro comme
        sur une petite valeur parasite, et ne laisse passer qu'un vrai
        pointeur. Écrite à l'envers, la garde sautait la copie sur un pointeur
        valide et la laissait s'exécuter sur un pointeur nul -- une faute de
        lecture dans XAM, c'est-à-dire la console.
        """
        index = self.text.index("cmpwi r5, 0")
        mnemonic = self.code[index + 1][1]
        self.assertEqual(mnemonic, "bge", "la garde teste le mauvais sens")
        target = int(self.code[index + 1][2], 16)
        after_copy = [address for address, m, _ in self.code if m == "lis"]
        self.assertIn(target, after_copy, "le saut ne retombe pas sur le code déplacé")
        # Et il saute bien par-dessus les huit instructions de copie.
        skipped = [m for a, m, _ in self.code if self.code[index + 1][0] < a < target]
        self.assertEqual(skipped, ["lwz", "stw"] * 4)

    def test_the_return_jump_does_not_clobber_the_restored_context_register(self) -> None:
        """Le shim relit r11 quatre instructions après la reprise.

        `lis r11, 0x81AC` est déplacée ici, et le shim fait
        `lwz r3, 0x7C04(r11)` juste après la reprise. Un saut de retour qui
        construit son adresse dans r11 détruit exactement ça, et le module va
        chercher son contexte à une adresse inventée.
        """
        tail = self.text[-4:]
        self.assertTrue(tail[-1].startswith("bctr"), tail)
        self.assertTrue(tail[-2].startswith("mtctr"), tail)
        for line in tail:
            self.assertNotIn("r11", line, "le saut de retour écrase r11")

    def test_the_displaced_instructions_are_re_executed_in_order(self) -> None:
        engine_words = HOOK.build_stub()
        window = len(HOOK.DISPLACED_WORDS)
        # Elles précèdent immédiatement le saut de retour, qui fait quatre mots.
        self.assertEqual(
            tuple(engine_words[-4 - window:-4]), HOOK.DISPLACED_WORDS
        )

    def test_the_stub_ends_where_the_displaced_instructions_stopped(self) -> None:
        """Reprendre ailleurs, c'est sauter ou rejouer une instruction du shim."""
        self.assertEqual(HOOK.RESUME, HOOK.SITE + 4 * len(HOOK.DISPLACED_WORDS))
        self.assertIn(f"{HOOK.RESUME & 0xFFFF:#x}", self.text[-3])

    def test_link_register_is_read_and_never_written(self) -> None:
        """LR porte l'identité de l'appelant : c'est toute la mesure."""
        self.assertIn("mflr r10", self.text)
        self.assertNotIn("mtlr", " ".join(self.text))
        self.assertFalse([t for t in self.text if t.startswith("bl ")],
                         "un saut avec lien écraserait LR")

    def test_only_volatile_registers_are_written(self) -> None:
        written = set()
        for _, mnemonic, operands in self.code:
            if mnemonic in ("lis", "ori", "lwz", "addi", "add", "rlwinm", "mflr", "mtctr"):
                written.add(operands.split(",")[0].strip())
        self.assertTrue(written <= {"r10", "r11", "r12", "ctr"}, written)

    def test_the_site_patch_is_exactly_as_long_as_what_it_displaces(self) -> None:
        patch = b"".join(HOOK.insn(w) for w in HOOK.absolute_jump(11, HOOK.STUB))
        self.assertEqual(len(patch), 4 * len(HOOK.DISPLACED_WORDS))

    def test_the_ring_index_is_a_modulo_and_never_leaves_the_ring(self) -> None:
        """`rlwinm r10, r11, 5, 23, 26` doit valoir (n % 16) * 0x20."""
        def rlwinm(value: int, shift: int, begin: int, end: int) -> int:
            rotated = ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF
            mask, bit = 0, begin
            while True:
                mask |= 1 << (31 - bit)
                if bit == end:
                    break
                bit = (bit + 1) % 32
            return rotated & mask

        index = self.text.index("rlwinm r10, r11, 5, 0x17, 0x1a")
        self.assertGreater(index, 0)
        for counter in (1, 15, 16, 17, 255, 4096, 0xFFFF):
            self.assertEqual(
                rlwinm(counter, 5, 23, 26),
                (counter % HOOK.SLOTS) * HOOK.SLOT_SIZE,
                f"compteur {counter}",
            )

    def test_the_slots_never_overwrite_the_counter(self) -> None:
        self.assertGreaterEqual(HOOK.SLOTS_BASE, HOOK.COUNTER + 4)

    def test_the_stub_and_the_ring_do_not_overlap(self) -> None:
        self.assertLessEqual(HOOK.STUB + len(HOOK.stub_bytes()), HOOK.COUNTER)


    def test_the_registers_read_are_the_ones_the_import_shim_leaves(self) -> None:
        """Mesuré, pas supposé.

        Le titre n'appelle pas l'export directement : son shim d'import décale
        les arguments d'un rang. À l'entrée de l'export, r4 est le socket, r5
        le sockaddr et r6 la longueur -- et non r3/r4/r5 comme le dit la
        signature publique. La première version lisait la signature et
        rapportait un socket à 1 et un sockaddr vide.
        """
        self.assertIn("stw r4, 8(r12)", self.text, "le socket n'est pas r4")
        self.assertIn("stw r6, 0xc(r12)", self.text, "la longueur n'est pas r6")
        self.assertTrue(
            [t for t in self.text if t.startswith("lwz r10, 0(r5)")],
            "le sockaddr n'est pas déréférencé depuis r5",
        )


if __name__ == "__main__":
    unittest.main()
