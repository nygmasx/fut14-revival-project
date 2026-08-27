"""The fourteen configuration sections the title actually asks for.

The Xbox 360 build asks `fetchClientConfig` for fourteen distinct sections in
a single session -- the VPS journal counts them -- and until the shared block
existed, ten of them were answered with an empty map: OSDK_TICKER, OSDK_ARENA,
OSDK_ABUSE_REPORTING, OSDK_NUCLEUS, OSDK_WEBOFFER, OSDK_TOLLBOOTH,
OSDK_SOCIAL_NETWORKS, OSDK_XMS_ABUSE_REPORTING, OSDK_CUSTOM_DATA and
FIFA_H2H_SEASONALPLAY.

An empty section is not a neutral answer.  A module that reads its endpoint
and its retry period from a section and receives nothing has neither, and
falls back on hostnames that stopped answering in 2016.  These tests hold the
shape that fixes that: every section carries the shared block, the four
sections whose contents were recovered from the Xbox image keep their own
values on top, and no key is ever served twice in one map.
"""

from __future__ import annotations

import importlib.util
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

from blaze_tdf import STRING, Field, decode_frame, encode_fields, encode_frame

# Every section name observed in the VPS journal, with the count of times the
# console asked for it over the recorded sessions.
SECTIONS_THE_TITLE_ASKS_FOR = (
    "OSDK_TICKER",
    "OSDK_ABUSE_REPORTING",
    "OSDK_ARENA",
    "OSDK_ROSTER",
    "OSDK_CORE",
    "OSDK_CLIENT",
    "OSDK_NUCLEUS",
    "OSDK_WEBOFFER",
    "OSDK_XMS_ABUSE_REPORTING",
    "OSDK_TOLLBOOTH",
    "OSDK_SOCIAL_NETWORKS",
    "IdentityParams",
    "OSDK_CUSTOM_DATA",
    "FIFA_H2H_SEASONALPLAY",
)


class SharedConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def served(self, section: str) -> dict[str, str]:
        fields = [Field("CFID", STRING, section)]
        frame = encode_frame(9, 1, 0, 0, 0x12345, encode_fields(fields))
        response = self.protocol.fetch_config(frame, fields, self.state)
        conf = SERVER.find_field(decode_frame(response)["fields"], "CONF")
        assert conf is not None
        return dict(conf.value[2])

    def test_no_section_the_title_asks_for_comes_back_empty(self) -> None:
        for section in SECTIONS_THE_TITLE_ASKS_FOR:
            with self.subTest(section=section):
                self.assertTrue(self.served(section))

    def test_the_sections_read_off_the_xbox_image_are_untouched(self) -> None:
        # Serving the shared block everywhere broke the login outright on
        # 2026-08-27: the console authenticated, took the redirect, dropped
        # one second later, and retried every seventy seconds behind "les
        # serveurs EA ne sont pas disponibles".  Reverting the server and
        # relaunching held the session, which named the change.
        #
        # These four were recovered from this console's own image and verified
        # on hardware -- OSDK_CORE and OSDK_CLIENT are what CardsDLL reads.
        # Keys lifted from a PC server do not get to outrank that.
        self.assertEqual(set(self.served("IdentityParams")), {"client_id", "redirect_uri"})
        for section in ("OSDK_CORE", "OSDK_CLIENT", "OSDK_ROSTER"):
            with self.subTest(section=section):
                served = self.served(section)
                self.assertNotIn("ALLOW_OFFLINE", served)
                self.assertNotIn("SKIP_LEGAL_DOC", served)
                self.assertNotIn("OSDK_ONLINE_ENABLED", served)

    def test_osdk_roster_keeps_its_four_names_and_nothing_else(self) -> None:
        self.assertEqual(
            set(self.served("OSDK_ROSTER")),
            {"ROSTER_URL", "ROSTER_VER", "ROSTER_LKR", "ROSTER_CSUM"},
        )

    def test_every_section_carries_the_easfc_retry_period(self) -> None:
        # This is the key the reconnect timer in powdllzf has to read to have
        # anything to re-arm itself with.  Which section the module reads is
        # not known, so all of them carry it -- all but the one that is not a
        # section at all.
        for section in SECTIONS_THE_TITLE_ASKS_FOR:
            if section in SERVER.SECTIONS_RECOVERED_FROM_THE_XBOX_IMAGE:
                continue
            with self.subTest(section=section):
                served = self.served(section)
                self.assertEqual(served["OSDK_EASW_CONNECT_RETRY_PERIOD"], "30")
                self.assertEqual(served["EASW/ENABLED"], "1")

    def test_a_section_never_serves_the_same_key_twice(self) -> None:  # noqa: D401
        # The frame carries a map.  A repeated key is a decoder's problem, and
        # the merge is what keeps it from happening when a section overrides a
        # shared value.
        for section in SECTIONS_THE_TITLE_ASKS_FOR:
            with self.subTest(section=section):
                fields = [Field("CFID", STRING, section)]
                frame = encode_frame(9, 1, 0, 0, 0x12345, encode_fields(fields))
                response = self.protocol.fetch_config(frame, fields, self.state)
                conf = SERVER.find_field(decode_frame(response)["fields"], "CONF")
                assert conf is not None
                keys = [key for key, _ in conf.value[2]]
                self.assertEqual(len(keys), len(set(keys)))

    def test_the_xbox_locale_is_still_four_characters(self) -> None:
        # OSDK_CORE's locale was read off this console's own PreAuth and is
        # four characters exactly; Impulsum14 serves a comma-separated PC list
        # under the same name.  Nothing shared may reach this section.
        self.state.locale = "frFR"
        served = self.served("OSDK_CORE")
        self.assertEqual(served["OSDK_EASW_ALLOWED_LOCALES"], "frFR")
        self.assertNotIn(",", served["OSDK_EASW_ALLOWED_LOCALES"])

    def test_the_shared_block_steers_no_authentication(self) -> None:
        # This console logs in through Xbox Live and that path works.  The PC
        # server's login keys would point it at Origin and Nucleus instead, so
        # none of them may appear.
        forbidden = {
            "AUTH_TYPE",
            "USE_TOKEN_AUTH",
            "OSDK_AUTH_REQUIRED",
            "ORIGIN_LOGIN_ENABLED",
            "NUCLEUS_LOGIN_ENABLED",
            "nucleusHost",
            "nucleusPort",
            "useNucleusSSL",
        }
        shared = {key for key, _ in self.protocol.shared_config(self.state)}
        self.assertEqual(shared & forbidden, set())

    def test_every_shared_url_points_at_a_server_we_run(self) -> None:
        # A URL that 404s is worse than a switch left at its default, so no
        # shared value may name a host that is not ours.
        for key, value in self.protocol.shared_config(self.state):
            if value.startswith("http"):
                with self.subTest(key=key):
                    self.assertTrue(value.startswith(self.protocol.identity_base))

    def test_merge_keeps_the_section_and_drops_the_duplicate(self) -> None:
        merged = SERVER.Fifa14Protocol.merge_config(
            [("A", "shared"), ("B", "shared")], [("B", "section"), ("C", "section")]
        )
        self.assertEqual(merged, [("A", "shared"), ("B", "section"), ("C", "section")])


if __name__ == "__main__":
    unittest.main()
