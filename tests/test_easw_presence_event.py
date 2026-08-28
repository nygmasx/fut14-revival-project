"""The first traffic the EA Sports Football Club module has ever produced.

On 2026-08-28 at 00:09:57, minutes after the ten empty configuration sections
were filled for the first time, the console began posting to
`/easw/event/personas/1000001/sku/FFA14XBX/event` every twenty seconds --
unprompted.  Before that evening the module had sent nothing at all: its
reconnect timer had expired and there was no configured retry period to re-arm
it with.

These tests hold the route that answers it, and the shape of what it records.
"""

from __future__ import annotations

import importlib.util
import http.client
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

# Recorded verbatim from the console, 2026-08-28T00:09:57Z.
PRESENCE = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<entry xmlns:e="http://xmlns.easw.easports.com/event">'
    b"<updated>2026-08-28T00:10:07Z</updated>"
    b'<category term="presence" />'
    b'<content><e:event xuid="901feefe6a599" expiredIn="120000"'
    b' handle="Imskobogota6z" id="998"></e:event></content></entry>'
)
PATH = "/easw/event/personas/1000001/sku/FFA14XBX/event"


class EaswPresenceEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.temp.name) / "journal.jsonl"
        self.journal = SERVER.Journal(self.journal_path)
        store = SERVER.AccountStores()
        store.get(0).save_identity(1000001, "Imskobogota6z")
        self.identity = SERVER.IdentityHttpService(
            "127.0.0.1", 0, "127.0.0.1", self.journal, store
        )
        self.identity.start()
        self.port = self.identity.server.server_address[1]

    def tearDown(self) -> None:
        self.identity.stop()
        self.temp.cleanup()

    def post(self, path: str = PATH, body: bytes = PRESENCE) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST", path, body,
            {"Content-Type": "text/xml", "EASW-Token": SERVER.EASW_TOKEN},
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        return response

    def records(self, kind: str) -> list[dict]:
        found = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("event") == kind:
                found.append(record)
        return found

    def test_the_presence_event_is_accepted(self) -> None:
        # It was answered 404 for as long as the module existed.  404 is what
        # a route nobody wrote looks like, and the console kept re-posting the
        # same event every twenty seconds behind it.
        self.assertEqual(self.post().status, 200)

    def test_what_it_says_is_recorded(self) -> None:
        self.post()
        events = self.records("easw_event")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["persona"], "1000001")
        self.assertEqual(events[0]["sku"], "FFA14XBX")
        self.assertEqual(events[0]["category"], "presence")

    def test_the_session_headers_come_back(self) -> None:
        response = self.post()
        self.assertEqual(response.getheader("EASW-Token"), SERVER.EASW_TOKEN)
        self.assertEqual(response.getheader("EASW-Session"), SERVER.EASW_SESSION)

    def test_a_body_that_is_not_an_entry_still_answers(self) -> None:
        # The route must not depend on parsing the body: an event whose
        # vocabulary we have not seen yet is exactly what we want recorded,
        # not refused.
        response = self.post(body=b"pas du tout du XML")
        self.assertEqual(response.status, 200)
        self.assertEqual(self.records("easw_event")[0]["category"], "")

    def test_another_sku_and_persona_are_read_from_the_path(self) -> None:
        self.post("/easw/event/personas/42/sku/FFA14PCC/event")
        event = self.records("easw_event")[0]
        self.assertEqual((event["persona"], event["sku"]), ("42", "FFA14PCC"))

    def test_a_neighbouring_easw_path_is_not_swallowed(self) -> None:
        # Only the event route is implemented.  The others were configured
        # this evening but have never been seen in use, and answering a route
        # wrongly is worse than not answering it -- the journal will show them
        # if the console starts asking.
        self.assertEqual(self.post("/easw/media/personas/1000001").status, 404)
        self.assertEqual(self.records("easw_event"), [])

    def test_the_category_reader_is_bounded(self) -> None:
        # It reads from the network on every event, so it looks at a bounded
        # prefix and matches a bounded token.
        self.assertEqual(SERVER.easw_event_category(PRESENCE), "presence")
        self.assertEqual(SERVER.easw_event_category(b"x" * 4096 + PRESENCE), "")
        self.assertEqual(
            SERVER.easw_event_category(b'<category term="' + b"a" * 200 + b'"'), ""
        )


if __name__ == "__main__":
    unittest.main()
