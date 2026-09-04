"""An unanswered Blaze command must record what was asked, not just that it was.

The VPS journal counts twenty-four distinct commands the console sent and this
server never answered, `Stats::getStatGroup` most often of all.  Naming them
was possible only once the generated Blaze SDK was on hand; *answering* them
needs one thing more, which the journal did not keep: the request's own
fields.  A `getStatGroup` reply is built around the group NAME it carries, and
a line that records only "component 7, command 4" cannot be answered from.

So the next console session has to leave a specification behind rather than a
list of things to guess at.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server" / "fifa14_blaze_server.py"
SPEC = importlib.util.spec_from_file_location("fifa14_blaze_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)

from blaze_tdf import INTEGER, STRING, Field, encode_fields, encode_frame

# GameManager::setPlayerAttributes: ten calls in the VPS journal and no
# handler, which makes it the honest example.  `Stats::getStatGroup` looked
# like the worst gap until the journal was read against the *current* code
# rather than cumulatively -- it has been answered for a while.
GAME_MANAGER = 4
SET_PLAYER_ATTRIBUTES = 8


class UnknownRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.jsonl"
        self.journal = SERVER.Journal(self.path)
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def unknown(self, fields: list[Field]) -> dict:
        frame = encode_frame(GAME_MANAGER, SET_PLAYER_ATTRIBUTES, 0, 0, 0x12345, encode_fields(fields))
        self.protocol.handle(frame, self.state)
        for line in self.path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("event") == "unknown_route":
                return record
        raise AssertionError("aucune ligne unknown_route")

    def test_the_request_fields_are_recorded(self) -> None:
        record = self.unknown([Field("NAME", STRING, "MyStatGroup")])
        self.assertEqual(record["component"], GAME_MANAGER)
        self.assertEqual(record["command"], SET_PLAYER_ATTRIBUTES)
        served = {field["label"]: field["value"] for field in record["fields"]}
        self.assertEqual(served["NAME"], "MyStatGroup")

    def test_several_fields_survive_in_order(self) -> None:
        record = self.unknown(
            [Field("LBID", INTEGER, 42), Field("NAME", STRING, "SeasonalPlay")]
        )
        self.assertEqual([field["label"] for field in record["fields"]], ["LBID", "NAME"])

    def test_the_raw_frame_is_kept_beside_them(self) -> None:
        # A field this decoder reads wrongly would be recorded wrongly and
        # look right.  The raw frame is the one thing that cannot be misread
        # later.
        record = self.unknown([Field("NAME", STRING, "MyStatGroup")])
        self.assertIn("hex", record)
        # Labels travel as a three-byte tag hash, not as their four letters,
        # so it is the value that is looked for here.
        self.assertIn("MyStatGroup".encode().hex().upper(), record["hex"])

    def test_a_request_with_no_fields_still_records_an_empty_list(self) -> None:
        record = self.unknown([])
        self.assertEqual(record["fields"], [])

    def test_the_console_still_gets_an_answer(self) -> None:
        # Recording more must not change what goes back on the wire: an
        # unanswered command still gets an empty response rather than silence,
        # because silence is what makes the title wait.
        frame = encode_frame(GAME_MANAGER, SET_PLAYER_ATTRIBUTES, 0, 0, 0x12345, encode_fields([]))
        replies = self.protocol.handle(frame, self.state)
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0])


if __name__ == "__main__":
    unittest.main()
