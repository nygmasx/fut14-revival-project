"""The vocabulary reader, held to what makes a run a table rather than noise."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dump_easfc_vocabulary", ROOT / "tools" / "dump_easfc_vocabulary.py"
)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)


def table(names: list[str]) -> bytes:
    return b"".join(name.encode() + b"\x00" for name in names)


class VocabularyTests(unittest.TestCase):
    def test_a_dense_run_is_found_with_its_addresses(self) -> None:
        names = [f"field{index:03d}" for index in range(60)]
        found = TOOL.runs(table(names), 0x89700400)
        self.assertEqual(len(found), 1)
        self.assertEqual([name for _, name in found[0]], names)
        self.assertEqual(found[0][0][0], 0x89700400)

    def test_a_short_run_is_not_a_table(self) -> None:
        # Stray printable bytes are common in a binary; a handful of them in a
        # row say nothing.
        self.assertEqual(TOOL.runs(table(["alpha", "beta", "gamma"]), 0), [])

    def test_a_gap_ends_the_run(self) -> None:
        names = [f"field{index:03d}" for index in range(60)]
        data = table(names) + b"\x00" * 64 + table(names)
        self.assertEqual(len(TOOL.runs(data, 0)), 2)

    def test_prose_and_paths_are_not_identifiers(self) -> None:
        # Only bare identifiers count.  A sentence or a URL in the same
        # section is not part of a field-name table.
        noisy = [f"field{index:03d}" for index in range(60)]
        data = table(noisy).replace(b"field030", b"not a name")
        found = TOOL.runs(data, 0)
        self.assertNotIn("not a name", [name for run in found for _, name in run])

    def test_the_real_dump_carries_the_names_the_module_asked_for(self) -> None:
        dump = ROOT / "work" / "powdll" / "powdllzf.rdata.bin"
        if not dump.is_file():
            self.skipTest("le vidage de powdllzf n'est pas presente ici")
        names = {name for run in TOOL.runs(dump.read_bytes(), TOOL.RDATA_BASE)
                 for _, name in run}
        # The two reads the module makes and this server does not answer.
        self.assertIn("configuration", names)
        self.assertIn("sku", names)
        # And the values it uploads, which proves the table is the right one.
        self.assertIn("stats_dnf", names)
        self.assertIn("friends", names)


if __name__ == "__main__":
    unittest.main()
