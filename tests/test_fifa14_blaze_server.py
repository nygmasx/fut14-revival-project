from __future__ import annotations

import http.client
import json
import importlib.util
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server" / "fifa14_blaze_server.py"
SPEC = importlib.util.spec_from_file_location("fifa14_blaze_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)

from blaze_tdf import BINARY, INTEGER, LIST, MAP, STRING, STRUCT, Field, decode_frame, encode_fields, encode_frame


def request(component: int, command: int, fields: list[Field] | None = None) -> bytes:
    return encode_frame(
        component,
        command,
        0,
        0,
        0x12345,
        encode_fields(fields or []),
    )


def by_label(decoded: dict, label: str) -> Field:
    value = SERVER.find_field(decoded["fields"], label)
    if value is None:
        raise AssertionError(f"Missing {label}")
    return value


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def test_redirector_points_to_local_core(self) -> None:
        response = self.protocol.handle(request(5, 1), self.state)[0]
        decoded = decode_frame(response)
        self.assertEqual(decoded["message_type"], 1)
        self.assertEqual(decoded["message_number"], 0x12345)
        self.assertEqual(by_label(decoded, "HOST").value, "192.0.2.35")
        self.assertEqual(by_label(decoded, "PORT").value, 10041)
        self.assertEqual(by_label(decoded, "SECU").value, 0)

    def test_preauth_advertises_fifa_xbox_and_cardhouse(self) -> None:
        response = self.protocol.handle(request(9, 7), self.state)[0]
        decoded = decode_frame(response)
        self.assertEqual(by_label(decoded, "EEFA").value, 1)
        self.assertEqual(by_label(decoded, "ESRC").value, "fifa-2014-xbl2")
        self.assertEqual(by_label(decoded, "INST").value, "fifa-2014-xbl2")
        self.assertEqual(by_label(decoded, "PILD").value, "fifa-2014-xbl2")
        self.assertEqual(by_label(decoded, "PLAT").value, "xbox360")
        item_type, component_ids = by_label(decoded, "CIDS").value
        self.assertEqual(item_type, INTEGER)
        self.assertIn(2148, component_ids)
        self.assertIn(35, component_ids)
        outer_config = by_label(decoded, "CONF")
        inner_config = SERVER.find_field(outer_config.value, "CONF")
        self.assertIsNotNone(inner_config)
        self.assertEqual(inner_config.type, MAP)
        config = dict(inner_config.value[2])
        self.assertEqual(config["nucleusConnect"], "http://192.0.2.35:18080")
        self.assertEqual(config["xblTokenUrn"], "http://accounts.ea.com")

    def test_identity_params_point_to_local_redirect(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "IdentityParams")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        config = by_label(decoded, "CONF")
        self.assertEqual(config.type, MAP)
        values = dict(config.value[2])
        self.assertEqual(values["client_id"], "fifa14-xbox360-offline")
        self.assertEqual(
            values["redirect_uri"],
            "http://192.0.2.35:18080/connect/redirect",
        )

    def test_osdk_client_bypasses_dead_refresh_and_enables_real_dlc_load(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "OSDK_CLIENT")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        config = by_label(decoded, "CONF")
        self.assertEqual(config.type, MAP)
        values = dict(config.value[2])
        self.assertEqual(values["ONLINE/NO_ASSET_UPDATE"], "1")
        self.assertEqual(values["DLC_USE_REAL_DLL_LOAD"], "1")
        self.assertEqual(values["FUT_RS4_BASE_URL"], "http://192.0.2.35:18080/")
        for key in (
            "FUT/SINGLE_BASEURL_XBox360",
            "FUT_RS4_URL_XBox360",
            "FUT_RS4_APIURL_XBox360",
            "FUT/MODULE_BASEURL_XBox360",
        ):
            self.assertEqual(values[key], "http://192.0.2.35:18080/")
        self.assertEqual(
            values["FUTDYNAMICMESSAGES_URL_BASE"],
            "http://192.0.2.35:18080",
        )
        self.assertEqual(values["CARDS/DIRECTED_BLAZEENV"], "prod")
        self.assertEqual(values["FCC/FUT_DEPLOY_LANGUAGE"], "en_US")
        # Tutorials stay off: the client asks for the tutorial feed either
        # way, so these keys buy nothing, and forcing them on pointed the
        # login at a document this server cannot yet shape correctly.
        self.assertEqual(values["FUT/FORCE_TUTORIALS"], "0")
        self.assertEqual(values["FUT/DISABLE_TUTORIALS"], "1")

    def test_osdk_roster_declares_local_base_roster(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "OSDK_ROSTER")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        values = dict(by_label(decoded, "CONF").value[2])
        self.assertEqual(values["ROSTER_URL"], "http://192.0.2.35:18080/roster")
        self.assertEqual(values["ROSTER_VER"], "1.0")
        self.assertIn("ROSTER_LKR", values)
        self.assertIn("ROSTER_CSUM", values)

    def test_xbox_login_returns_session_and_user_notification(self) -> None:
        login = request(
            1,
            170,
            [
                Field("GTAG", STRING, "TestGamer"),
                Field("MAIL", STRING, "test@example.invalid"),
                Field("XUID", INTEGER, 0x12345678),
            ],
        )
        response, notification = self.protocol.handle(login, self.state)
        decoded = decode_frame(response)
        self.assertEqual(by_label(decoded, "DSNM").value, "TestGamer")
        self.assertEqual(by_label(decoded, "XREF").value, 0x12345678)
        self.assertEqual(by_label(decoded, "XTYP").value, 1)
        self.assertEqual(by_label(decoded, "STAS").value, 2)
        self.assertTrue(self.state.authenticated)

        added = decode_frame(notification)
        self.assertEqual(added["component"], 0x7802)
        self.assertEqual(added["command"], 2)
        self.assertEqual(added["message_type"], 2)
        self.assertEqual(by_label(added, "NAME").value, "TestGamer")

    def test_authentication2_reuses_the_persona_fut_auth_adopted(self) -> None:
        external_id = 2535469248587161
        self.protocol.accounts.get(external_id).save_identity(
            external_id, "Imskobogota6z"
        )
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, external_id),
            ],
        )
        response = self.protocol.handle(login, self.state)[0]
        persona = by_label(decode_frame(response), "PDTL")
        self.assertEqual(persona.value[0].value, "Imskobogota6z")
        self.assertEqual(self.state.gamertag, "Imskobogota6z")
        self.assertEqual(
            self.protocol.accounts.get(external_id).load_identity(),
            (external_id, "Imskobogota6z"),
        )

    def test_authentication2_keeps_a_gamertag_the_client_supplied(self) -> None:
        external_id = 2535469248587161
        self.protocol.accounts.get(external_id).save_identity(external_id, "StoredName")
        self.state.gamertag = "LiveGamertag"
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, external_id),
            ],
        )
        response = self.protocol.handle(login, self.state)[0]
        persona = by_label(decode_frame(response), "PDTL")
        self.assertEqual(persona.value[0].value, "LiveGamertag")

    def test_authentication2_ignores_a_persona_stored_for_another_account(
        self,
    ) -> None:
        # Stored against persona 42, and the login presents another id --
        # so with a store per persona this cannot leak even by accident.
        self.protocol.accounts.get(42).save_identity(42, "OtherAccount")
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, 2535469248587161),
            ],
        )
        response = self.protocol.handle(login, self.state)[0]
        persona = by_label(decode_frame(response), "PDTL")
        self.assertEqual(persona.value[0].value, "OfflineFUT")

    def test_authentication2_login_uses_exact_fifa14_schema(self) -> None:
        external_id = 2535469248587161
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, external_id),
            ],
        )
        response, authenticated_notification, notification, extended_notification = self.protocol.handle(
            login, self.state
        )
        decoded = decode_frame(response)
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            ["ANON", "SESS", "SPAM", "UNDR"],
        )
        self.assertEqual(by_label(decoded, "BUID").value, external_id)
        self.assertEqual(by_label(decoded, "UID").value, external_id)
        persona = by_label(decoded, "PDTL")
        self.assertEqual(
            [field.label for field in persona.value],
            ["DSNM", "PID", "PLAT"],
        )
        self.assertEqual(by_label(decoded, "PLAT").value, 1)
        self.assertEqual(by_label(decoded, "UNDR").value, 0)
        self.assertTrue(self.state.authenticated)

        authenticated = decode_frame(authenticated_notification)
        self.assertEqual(
            (authenticated["component"], authenticated["command"]),
            (0x7802, 8),
        )
        self.assertEqual(
            [field.label for field in authenticated["fields"]],
            [
                "ALOC", "BUID", "DSNM", "FRST", "KEY", "LAST", "LLOG",
                "MAIL", "PID", "PLAT", "UID", "USTP", "XREF",
            ],
        )
        self.assertEqual(by_label(authenticated, "BUID").value, external_id)
        self.assertEqual(by_label(authenticated, "UID").value, external_id)
        self.assertEqual(by_label(authenticated, "XREF").value, external_id)
        self.assertEqual(by_label(authenticated, "DSNM").value, "OfflineFUT")
        self.assertEqual(by_label(authenticated, "ALOC").value, 1718765138)

        added = decode_frame(notification)
        self.assertEqual((added["component"], added["command"]), (0x7802, 2))
        self.assertEqual(
            [field.label for field in added["fields"]],
            ["DATA", "USER"],
        )
        self.assertEqual(by_label(added, "BPS").value, "ams")
        self.assertEqual(by_label(added, "EXID").value, external_id)

        extended = decode_frame(extended_notification)
        self.assertEqual((extended["component"], extended["command"]), (0x7802, 1))
        self.assertEqual(
            [field.label for field in extended["fields"]],
            ["DATA", "SUBS", "USID"],
        )
        self.assertEqual(by_label(extended, "SUBS").value, 1)
        self.assertEqual(by_label(extended, "USID").value, external_id)

    def test_postauth_emits_complete_fifa14_response(self) -> None:
        self.state.xuid = 0x12345678
        decoded = decode_frame(
            self.protocol.handle(request(9, 8), self.state)[0]
        )
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            ["PSS", "TELE", "TICK", "UROP"],
        )

        pss = by_label(decoded, "PSS")
        self.assertEqual(
            [field.label for field in pss.value],
            ["ADRS", "CSIG", "OIDS", "PJID", "PORT", "RPRT", "TIID"],
        )
        self.assertEqual(SERVER.find_field(pss.value, "CSIG").type, BINARY)
        self.assertEqual(SERVER.find_field(pss.value, "OIDS").type, LIST)

        self.assertEqual(by_label(decoded, "PORT").value, 0)
        telemetry = SERVER.find_field(decoded["fields"], "TELE")
        ticker = SERVER.find_field(decoded["fields"], "TICK")
        options = SERVER.find_field(decoded["fields"], "UROP")
        self.assertIsNotNone(telemetry)
        self.assertIsNotNone(ticker)
        self.assertIsNotNone(options)
        self.assertEqual(SERVER.find_field(telemetry.value, "PORT").value, 6767)
        self.assertEqual(SERVER.find_field(ticker.value, "PORT").value, 6776)
        self.assertEqual(SERVER.find_field(options.value, "UID").value, 0x12345678)

    def test_cardhouse_new_user_flow(self) -> None:
        login = decode_frame(
            self.protocol.handle(request(2148, 101), self.state)[0]
        )
        self.assertEqual(login["error"], 0)
        self.assertIsNone(SERVER.find_field(login["fields"], "NAME"))

        missing = decode_frame(
            self.protocol.handle(request(2148, 104), self.state)[0]
        )
        self.assertEqual(missing["message_type"], 3)
        self.assertEqual(missing["error"], 1)
        self.assertEqual(missing["component"], 2148)

    def test_sponsored_events_url_is_non_empty_and_local(self) -> None:
        decoded = decode_frame(
            self.protocol.handle(request(0x081C, 3), self.state)[0]
        )
        self.assertEqual(decoded["error"], 0)
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            ["URL"],
        )
        self.assertEqual(
            by_label(decoded, "URL").value,
            "http://192.0.2.35:18080/sponsored-events",
        )

    def test_telemetry_server_has_complete_blaze3_schema(self) -> None:
        decoded = decode_frame(
            self.protocol.handle(request(9, 5), self.state)[0]
        )
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            [
                "ADRS", "ANON", "DISA", "FILT", "LOC", "NOOK", "PORT",
                "SDLY", "SESS", "SKEY", "SPCT", "STIM",
            ],
        )
        self.assertEqual(by_label(decoded, "ADRS").value, "192.0.2.35")
        self.assertEqual(by_label(decoded, "PORT").value, 6767)

    def test_early_osdk_and_blaze_components_return_typed_payloads(self) -> None:
        messages = decode_frame(
            self.protocol.handle(request(15, 2), self.state)[0]
        )
        self.assertEqual(by_label(messages, "MCNT").value, 0)

        association = decode_frame(
            self.protocol.handle(request(25, 6), self.state)[0]
        )
        self.assertEqual(by_label(association, "LMAP").value, (STRUCT, []))

        clubs = decode_frame(
            self.protocol.handle(request(11, 2600), self.state)[0]
        )
        self.assertEqual(
            [field.label for field in clubs["fields"]],
            ["CLDS", "MXEV", "MXRV", "PUHR", "SOVR", "STRT"],
        )

        key_scopes = decode_frame(
            self.protocol.handle(request(7, 15), self.state)[0]
        )
        self.assertEqual(by_label(key_scopes, "KSIT").value, (STRING, STRUCT, []))

        stat_groups = decode_frame(
            self.protocol.handle(request(7, 3), self.state)[0]
        )
        self.assertEqual(by_label(stat_groups, "GRPS").value, (STRUCT, []))

        periods = decode_frame(
            self.protocol.handle(request(7, 20), self.state)[0]
        )
        self.assertEqual(len(periods["fields"]), 14)
        self.assertTrue(all(field.value == 0 for field in periods["fields"]))

        settings = decode_frame(
            self.protocol.handle(request(2249, 1), self.state)[0]
        )
        item_type, setting_items = by_label(settings, "LSST").value
        self.assertEqual(item_type, STRUCT)
        self.assertEqual(SERVER.find_field(setting_items[0], "ID").value, "O_TKfilter")

        groups = decode_frame(
            self.protocol.handle(request(2249, 2), self.state)[0]
        )
        _, group_items = by_label(groups, "LGRP").value
        self.assertEqual(SERVER.find_field(group_items[0], "ID").value, "O_SG_TCKR")
        self.assertEqual(
            SERVER.find_field(group_items[0], "LSET").value,
            (STRING, ["O_TKfilter"]),
        )

        gates = decode_frame(
            self.protocol.handle(request(2268, 3), self.state)[0]
        )
        self.assertEqual(by_label(gates, "LIST").value, (STRUCT, []))

    def test_first_time_setting_is_loaded_and_saved(self) -> None:
        loaded_all = decode_frame(
            self.protocol.handle(request(9, 12), self.state)[0]
        )
        settings_map = by_label(loaded_all, "SMAP")
        self.assertEqual(settings_map.type, MAP)
        self.assertEqual(
            dict(settings_map.value[2]),
            {"FirstTimeFlag": "0"},
        )

        loaded = decode_frame(
            self.protocol.handle(
                request(
                    9,
                    10,
                    [
                        Field("KEY", STRING, "FirstTimeFlag"),
                        Field("UID", INTEGER, 0),
                    ],
                ),
                self.state,
            )[0]
        )
        self.assertEqual(by_label(loaded, "DATA").value, "0")

        saved = decode_frame(
            self.protocol.handle(
                request(
                    9,
                    11,
                    [
                        Field("DATA", STRING, "1"),
                        Field("KEY", STRING, "FirstTimeFlag"),
                        Field("UID", INTEGER, 0),
                    ],
                ),
                self.state,
            )[0]
        )
        self.assertEqual(saved["error"], 0)

        reloaded = decode_frame(
            self.protocol.handle(
                request(9, 10, [Field("KEY", STRING, "FirstTimeFlag")]),
                self.state,
            )[0]
        )
        self.assertEqual(by_label(reloaded, "DATA").value, "1")


    def test_preauth_locale_opens_the_easw_gate(self) -> None:
        # PreAuth's LANG is the four-byte locale the EASW gate compares
        # against, so OSDK_CORE must echo exactly what the console reported.
        assert SERVER.Fifa14Protocol.decode_locale(0x66724652) == "frFR"
        assert SERVER.Fifa14Protocol.decode_locale(0) == ""
        assert SERVER.Fifa14Protocol.decode_locale("frFR") == ""
        assert SERVER.Fifa14Protocol.decode_locale(0x00302D31) == ""

        self.state.locale = "frFR"
        response = self.protocol.fetch_config(
            request(9, 1, [Field("CFID", STRING, "OSDK_CORE")]),
            [Field("CFID", STRING, "OSDK_CORE")],
            self.state,
        )
        config = dict(by_label(decode_frame(response), "CONF").value[2])
        assert config["OSDK_EASW_ALLOWED_LOCALES"] == "frFR"
        assert len(config["OSDK_EASW_ALLOWED_LOCALES"]) == 4
        assert config["OSDK_EASW_AUTH_URL"].startswith("http://")
        # Without this the DLL falls back to the retired easw.easports.com.
        assert config["FUT_RS4_BASE_URL"].startswith("http://")

    def test_osdk_core_falls_back_to_a_valid_locale(self) -> None:
        response = self.protocol.fetch_config(
            request(9, 1, [Field("CFID", STRING, "OSDK_CORE")]),
            [Field("CFID", STRING, "OSDK_CORE")],
            self.state,
        )
        config = dict(by_label(decode_frame(response), "CONF").value[2])
        assert len(config["OSDK_EASW_ALLOWED_LOCALES"]) == 4

    def test_easw_authentication_returns_the_session_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            store = SERVER.AccountStores()
            # Persona 0: this request carries no session, so it is bound to the
            # default club and reads the default club's account state.
            store.get(0).save_identity(4242, "Local")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, store
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                # This Xbox build posts a signed form to /authentication360
                # with a version query; the PC build posts JSON to the /v2
                # path. Both have to answer with the same headers.
                for path, body, content_type in (
                    (
                        "/authentication360?version=2.0.5.0",
                        b"gamertag=Local&xuid=1&locale=fr_FR&skuid=FFA14XBX",
                        "application/x-www-form-urlencoded",
                    ),
                    ("/v2/authenticationNucleusPersona", b"{}", "application/json"),
                ):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request(
                        "POST", path, body=body,
                        headers={"Content-Type": content_type},
                    )
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        response.getheader("EASW-Token"), SERVER.EASW_TOKEN
                    )
                    self.assertEqual(
                        response.getheader("EASW-Session"), SERVER.EASW_SESSION
                    )
                    self.assertEqual(
                        response.getheader("EASW-Nucleus-Persona"), "4242"
                    )
                    self.assertEqual(response.getheader("EASW-Userid"), "4242")
                    response.read()
                    client.close()
            finally:
                identity.stop()
            self.assertIn(
                '"event": "easw_auth_request"',
                journal_path.read_text(encoding="utf-8"),
            )

    def test_fut_settings_and_locstrings_are_served(self) -> None:
        # The console walked security to completion and then died on these two
        # 404s before logging out again.
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/ut/game/fifa14/settings")
                response = client.getresponse()
                settings = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                # Zero lets a brand-new account create its club immediately.
                self.assertEqual(settings["clubCreateThreshold"], 0)
                self.assertIn("maximumTradePileSize", settings)
                self.assertIn("getOperationTimeoutSec", settings)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/fut/loc/XBox360/leaderboards.FRE_FR.xml")
                response = client.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertTrue(body.startswith(b"<?xml"))
                client.close()
            finally:
                identity.stop()

    def test_every_fut_route_answers_with_valid_json(self) -> None:
        # Each body must parse: a malformed one would reach the native parser
        # as a failure rather than as "nothing yet".
        for path, body in SERVER.FUT_ROUTES.items():
            self.assertTrue(path.startswith("/ut/"), path)
            __import__("json").loads(body)

    def test_first_use_fan_out_routes_return_parser_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                # These three are answered from the wallet, whose balance
                # moves, so a fixed body would only assert today's number.
                dynamic = {
                    "/ut/game/fifa14/user",
                    "/ut/game/fifa14/user/credits",
                    "/ut/delete/game/fifa14/item",
                    "/ut/game/fifa14/trade/status",
                    "/ut/game/fifa14/tradePile",
                    "/ut/game/fifa14/watchlist",
                    # Generated from the pack table now, not a fixture.
                    "/ut/game/fifa14/store",
                    "/ut/game/fifa14/store/purchasegroup/all",
                    # Modes are generated too.
                    "/ut/game/fifa14/season/list",
                    "/ut/game/fifa14/season/user",
                    "/ut/game/fifa14/tournament/list",
                    "/ut/game/fifa14/tournament/user/list",
                    "/ut/game/fifa14/clientdata/totw",
                    # Club counters are computed from the inventory now.
                    "/ut/game/fifa14/club/stats/staff",
                    "/ut/game/fifa14/club/stats/year",
                    "/ut/game/fifa14/club/stats/consumables",
                    "/ut/game/fifa14/club/stats/newcards",
                    # Counted from the club, which grows as cards are kept.
                    "/ut/game/fifa14/hub",
                    # Manager tasks are tracked, not fixed.
                    "/ut/game/fifa14/clientdata/managerquest",
                    # Carries the club's own cards and the adopted persona.
                    # The fixture beside it is the shape, not the contents.
                    "/ut/game/fifa14/clubUser",
                }
                for path in sorted(set(SERVER.FUT_ROUTES) - dynamic):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request("GET", path)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200, path)
                    # Every FUT reply now carries the coin total as well, so
                    # compare the fixture's own members rather than the whole
                    # body.
                    body = __import__("json").loads(response.read())
                    expected = __import__("json").loads(SERVER.FUT_ROUTES[path])
                    for key, value in expected.items():
                        self.assertEqual(body.get(key), value, f"{path}:{key}")
                    client.close()
            finally:
                identity.stop()

    def test_match_reset_acknowledges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("PUT", "/ut/game/fifa14/match/reset")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    __import__("json").loads(response.read()), {"reset": True}
                )
                client.close()
            finally:
                identity.stop()


class MatchEndTests(unittest.TestCase):
    def test_the_destroy_response_carries_the_award_scalars(self) -> None:
        # This replaces `test_the_destroy_response_carries_its_three_members`,
        # which held the reply to exactly myMatchStats, opponentMatchStats and
        # matchData -- the first two as empty *strings*.
        #
        # That restriction was deliberate and temporary: the scalars were not
        # to go out until a real match end from this console had been read,
        # because a frontend that hangs after a won final is worse than an
        # award screen showing zeroes. Four have now been read -- a Cup 2 run
        # played to the final and won on 16 August 2026, 6 349 coins credited
        # across four matches, and the player shown none of it.
        #
        # The stats are echoed rather than invented: the twelve members the
        # client itself submitted, returned as integers. An empty string where
        # the parser expects a stat block is what rendered the zeroes.
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "PUT",
                    "/ut/game/fifa14/match/end",
                    __import__("json").dumps({"matchData": "QUJD"}).encode(),
                )
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                body = __import__("json").loads(response.read())
                client.close()

                self.assertLessEqual(
                    {"myMatchStats", "opponentMatchStats", "matchData", "endReason"},
                    set(body),
                )
                self.assertEqual(body["matchData"], "QUJD")
                # The stat blocks are objects now, not empty strings, and every
                # member the client submits comes back as an integer.
                for block in ("myMatchStats", "opponentMatchStats"):
                    self.assertIsInstance(body[block], dict)
                    self.assertIn("goals", body[block])
                    self.assertIsInstance(body[block]["goals"], int)
            finally:
                identity.stop()


class TournamentRouteTests(unittest.TestCase):
    """The cups.

    Every member asserted here is one CardsDLL's own JSON name table carries.
    The catalogue was served empty because an earlier guessed shape froze the
    title on Competition Joueur Solo, so the two things that matter are that
    `rounds` is an array of records and that no invented member goes out.
    """

    def setUp(self) -> None:
        SERVER.TOURNAMENT_PROGRESS.entries.clear()

    tearDown = setUp

    def _identity(self, temp: str):
        journal = SERVER.Journal(Path(temp) / "journal.jsonl")
        identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
        identity.start()
        return identity

    def _get(self, port: int, path: str, method: str = "GET", body: bytes | None = None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        client.request(method, path, body)
        response = client.getresponse()
        payload = __import__("json").loads(response.read())
        status = response.status
        client.close()
        return status, payload

    def test_catalogue_carries_the_native_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                for path in (
                    "/ut/game/fifa14/tournament",
                    "/ut/game/fifa14/tournament/list",
                ):
                    status, body = self._get(port, path)
                    self.assertEqual(status, 200, path)
                    cups = body["tournament"]
                    self.assertTrue(cups, path)
                    for cup in cups:
                        # The freeze: a count where the parser walks records.
                        self.assertIsInstance(cup["rounds"], list)
                        self.assertEqual(len(cup["rounds"]), cup["numRounds"])
                        for entry in cup["rounds"]:
                            self.assertEqual(
                                set(entry),
                                {"id", "difficulty", "rewardMultiplier", "coins"},
                            )
                        self.assertEqual(cup["treeType"], "knockout")
                        self.assertEqual(cup["type"], "offline")
                        self.assertEqual(cup["lock"], "UNLOCKED")
                        self.assertEqual(
                            set(cup["awardSet"]["awards"][0]),
                            {"awardType", "value", "halid"},
                        )
                        # Members the previous attempt invented; none of these
                        # appear in the module's name table.
                        for absent in ("name", "level", "entryFee", "active", "won"):
                            self.assertNotIn(absent, cup)
            finally:
                identity.stop()

    def test_teams_draw_is_one_short_of_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                status, body = self._get(
                    port, "/ut/game/fifa14/tournament/teams?count=15"
                )
                self.assertEqual(status, 200)
                self.assertEqual(set(body), {"teamId"})
                self.assertEqual(len(body["teamId"]), 15)
                self.assertTrue(all(isinstance(x, int) for x in body["teamId"]))
            finally:
                identity.stop()

    def test_a_cup_never_entered_reports_only_its_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertEqual((status, body), (200, {"tournamentId": []}))
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/1")
                self.assertEqual((status, body), (200, {"tournamentId": 1}))
            finally:
                identity.stop()

    def test_progress_is_kept_and_read_back(self) -> None:
        # `full` is the shape under test. The default is `off`, because
        # handing a played run back freezes this title -- twice measured, with
        # and without `tournamentId`. See `cup_resume_mode`.
        previous_mode = os.environ.get("FIFA14_CUP_RESUME")
        os.environ["FIFA14_CUP_RESUME"] = "full"
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                # The body the client builds itself, from the format string
                # that sits among the cup constants in .rdata.
                sent = __import__("json").dumps(
                    {
                        "round": 3,
                        "dataVersion": 1,
                        "tournamentData": "QUJD",
                        "progressDataVersion": 1,
                        "progressData": "REVG",
                    }
                ).encode()
                status, _ = self._get(
                    port, "/ut/game/fifa14/tournament/user/2", "PUT", sent
                )
                self.assertEqual(status, 200)

                status, body = self._get(port, "/ut/game/fifa14/tournament/user/2")
                self.assertEqual(status, 200)
                self.assertEqual(body["round"], 3)
                self.assertEqual(body["tournamentData"], "QUJD")
                # Exactly the three members the reader at CardsDLLzf+0x1be840
                # matches, and `tournamentData` **before** `dataVersion`: the
                # version branch is what decodes, using what the data branch
                # left behind. See `cup_resume_mode`.
                self.assertEqual(
                    list(body), ["tournamentId", "round", "tournamentData", "dataVersion"]
                )
                self.assertNotIn("progressData", body)
                self.assertNotIn("progressdata", body)
                # Handed back as it was written, under its own id.
                #
                # The id was taken out of here once, on the reasoning that the
                # path already carries it -- a guess, made in the same change
                # that removed a duplicate lower-case `progressdata`. That
                # second spelling is in the name table, so it is the same
                # known field twice rather than a sibling the parser skips,
                # and it is reason enough for a freeze on its own. Removing
                # both together proved nothing about either.
                self.assertEqual(body["tournamentId"], 2)
                # `data` is the season spelling and does not go out here.
                self.assertNotIn("data", body)

                # Only the cup actually entered is named.
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertEqual((status, body), (200, {"tournamentId": [2]}))

                status, _ = self._get(
                    port, "/ut/delete/game/fifa14/tournament/user/2", "POST", b"{}"
                )
                self.assertEqual(status, 200)
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertEqual((status, body), (200, {"tournamentId": []}))
            finally:
                identity.stop()
                if previous_mode is None:
                    os.environ.pop("FIFA14_CUP_RESUME", None)
                else:
                    os.environ["FIFA14_CUP_RESUME"] = previous_mode

    def test_a_cup_run_can_be_withheld_entirely(self) -> None:
        # The escape hatch. A frozen console costs a relaunch, so `off`
        # stays reachable: the run is still kept -- the list names it and
        # the save holds it -- but the document that reopens it never goes
        # out, and the cup restarts instead.
        previous_mode = os.environ.get("FIFA14_CUP_RESUME")
        os.environ["FIFA14_CUP_RESUME"] = "off"
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            json_module = __import__("json")
            try:
                port = identity.server.server_address[1]
                self._get(
                    port, "/ut/game/fifa14/tournament/user/5", "PUT",
                    json_module.dumps(
                        {"round": 2, "dataVersion": 1, "tournamentData": "QUJD",
                         "progressDataVersion": 1, "progressData": "AAAAAgAB"}
                    ).encode(),
                )
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/5")
                self.assertEqual((status, body), (200, {"tournamentId": 5}))
                # Kept, not forgotten.
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertIn(5, body["tournamentId"])
            finally:
                SERVER.TOURNAMENT_PROGRESS.entries.clear()
                identity.stop()
                if previous_mode is None:
                    os.environ.pop("FIFA14_CUP_RESUME", None)
                else:
                    os.environ["FIFA14_CUP_RESUME"] = previous_mode

    def test_a_season_is_saved_under_its_season_and_division(self) -> None:
        # The route the console actually sent on starting a Saison Joueur
        # Solo. `ut/%s/season/%s/user` in the URL template table reads as one
        # id; the format string beside the season serialiser is
        # `%d/division/%d`, and the wire agrees with the second reading.
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                path = "/ut/game/fifa14/season/1/division/10/user"
                # Round one with an empty progress blob is a season with no
                # first match behind it -- the same shape that froze the cups
                # when it was handed back. It is answered as no season at all.
                started = __import__("json").dumps(
                    {
                        "round": 1,
                        "dataVersion": 1,
                        "data": "AAAAEAUAAAABAAAAAAAAAAAAAAA=",
                        "progressDataVersion": 1,
                        "progressData": "AAAAAA==",
                    }
                ).encode()
                status, body = self._get(port, path, "PUT", started)
                self.assertEqual((status, body), (200, {}))

                # A season actually under way comes back the way it went up,
                # spelled `data` -- the seasons' word, not the cups'.
                played = __import__("json").dumps(
                    {
                        "round": 3,
                        "dataVersion": 1,
                        "data": "QUJD",
                        "progressDataVersion": 1,
                        "progressData": "REVG",
                    }
                ).encode()
                status, _ = self._get(port, path, "PUT", played)
                self.assertEqual(status, 200)
                status, body = self._get(port, path, "GET")
                self.assertEqual(status, 200)
                self.assertEqual(body["round"], 3)
                # The five members the season reader at CardsDLLzf+0x1adf28
                # matches are data(133), dataVersion(134), divisionId(148),
                # round(429) and seasonId(445) -- so the blob is `data`, and it
                # goes out before the version that decodes it.
                self.assertEqual(list(body), ["round", "data", "dataVersion"])
                self.assertEqual(body["data"], "QUJD")
                self.assertNotIn("seasonData", body)
                self.assertNotIn("progressData", body)
                self.assertNotIn("tournamentData", body)
                self.assertNotIn("seasonId", body)

                # Another division is another season, not the same one.
                status, body = self._get(
                    port, "/ut/game/fifa14/season/1/division/9/user", "GET"
                )
                self.assertEqual((status, body), (200, {}))

                status, _ = self._get(
                    port, "/ut/game/fifa14/season/1/division/10/reset", "PUT", b"{}"
                )
                self.assertEqual(status, 200)
                status, body = self._get(port, path, "GET")
                self.assertEqual((status, body), (200, {}))
            finally:
                identity.stop()

    def test_a_season_match_settles_into_the_season_it_was_created_in(self) -> None:
        # The whole chain, in the order the console walks it. Which mode owns
        # a result had to be inferred for cups, from whichever cup saved its
        # progress last; a season match says so itself, in the body that
        # creates it.
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            json_module = __import__("json")
            previous = SERVER.TENANTS.default().active_season
            coins_before = SERVER.WALLET.coins
            try:
                port = identity.server.server_address[1]
                SERVER.SEASON_PROGRESS.entries.clear()

                status, _ = self._get(
                    port,
                    "/ut/game/fifa14/match",
                    "POST",
                    json_module.dumps(
                        {
                            "squadId": 4,
                            "type": "OFFLINE",
                            "seasonId": 1,
                            "divisionId": 10,
                        }
                    ).encode(),
                )
                self.assertEqual(status, 200)
                self.assertEqual(SERVER.TENANTS.default().active_season, (1, 10))

                status, _ = self._get(
                    port,
                    "/ut/game/fifa14/match/end",
                    "PUT",
                    json_module.dumps(
                        {"endReason": "WIN", "items": [], "matchData": "ab"}
                    ).encode(),
                )
                self.assertEqual(status, 200)

                entry = SERVER.SEASON_PROGRESS.entries[(1, 10)]
                self.assertEqual(entry["won"], 1)
                self.assertEqual(entry["lost"], 0)
                # Whatever the match paid went to the wallet and to the
                # season's own total, which are two different numbers on two
                # different screens.
                self.assertEqual(entry["coins"], SERVER.WALLET.coins - coins_before)

                # A cup match afterwards must not settle into the season.
                status, _ = self._get(
                    port,
                    "/ut/game/fifa14/match",
                    "POST",
                    json_module.dumps(
                        {"squadId": 4, "type": "OFFLINE", "tournamentId": 3}
                    ).encode(),
                )
                self.assertEqual(status, 200)
                self.assertIsNone(SERVER.TENANTS.default().active_season)
            finally:
                SERVER.TENANTS.default().active_season = previous
                SERVER.SEASON_PROGRESS.entries.clear()
                identity.stop()

    def _sid_get(self, port: int, path: str, method: str, body: bytes | None,
                 sid: str | None = None, nucleus: int | None = None):
        """Like `_get`, but says who is asking."""
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        headers = {}
        if sid is not None:
            headers["X-UT-SID"] = sid
        if nucleus is not None:
            headers["Easw-Session-Data-Nucleus-Id"] = str(nucleus)
        client.request(method, path, body, headers)
        response = client.getresponse()
        payload = __import__("json").loads(response.read())
        status = response.status
        client.close()
        return status, payload

    def test_two_consoles_do_not_share_a_club(self) -> None:
        # The routing key is the FUT session id, not the nucleus header. On a
        # full session into Saison Joueur Solo the nucleus header appeared on
        # one request out of forty-nine and `X-UT-SID` on forty-six -- so the
        # session id is the only thing that can route a request generally,
        # which is what it is for.
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            json_module = __import__("json")
            try:
                port = identity.server.server_address[1]

                # `/ut/auth` mints the session id, derived from the persona
                # the request itself presents. Derived rather than stored: the
                # launcher restarts this server on every run, and a stored
                # table would strand a client still holding the last one.
                status, body = self._sid_get(
                    port, "/ut/auth", "POST",
                    json_module.dumps(
                        {"nuc": 111, "nucleusPersonaId": 111,
                         "nucleusPersonaDisplayName": "Un"}
                    ).encode(),
                )
                self.assertEqual(status, 200)
                first = body["sid"]
                self.assertEqual(SERVER.SESSIONS.persona(first), 111)
                self.assertNotEqual(first, SERVER.UT_SID_BASE)

                status, body = self._sid_get(
                    port, "/ut/auth", "POST",
                    json_module.dumps(
                        {"nuc": 222, "nucleusPersonaId": 222,
                         "nucleusPersonaDisplayName": "Deux"}
                    ).encode(),
                )
                second = body["sid"]
                self.assertNotEqual(first, second)

                # Both clubs seeded from the suite's scratch save, which by
                # now carries whatever an earlier test left in it. What is
                # under test is where a result *lands*, so start both from a
                # clean season table.
                for persona in (111, 222):
                    SERVER.TENANTS.get(persona).seasons.entries.clear()

                # Each console starts a season, and each one keeps its own.
                for sid, season in ((first, 1), (second, 7)):
                    status, _ = self._sid_get(
                        port, "/ut/game/fifa14/match", "POST",
                        json_module.dumps(
                            {"squadId": 4, "type": "OFFLINE",
                             "seasonId": season, "divisionId": 10}
                        ).encode(),
                        sid=sid,
                    )
                    self.assertEqual(status, 200)

                self.assertEqual(SERVER.TENANTS.get(111).active_season, (1, 10))
                self.assertEqual(SERVER.TENANTS.get(222).active_season, (7, 10))

                # And a win settles into the club that created the match, not
                # into whichever one the server saw last.
                status, _ = self._sid_get(
                    port, "/ut/game/fifa14/match/end", "PUT",
                    json_module.dumps(
                        {"endReason": "WIN", "items": [], "matchData": "ab"}
                    ).encode(),
                    sid=first,
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    SERVER.TENANTS.get(111).seasons.entries[(1, 10)]["won"], 1
                )
                self.assertEqual(SERVER.TENANTS.get(222).seasons.entries, {})

                # An id this server never issued proves nothing, and lands on
                # the default club rather than on somebody else's.
                self.assertEqual(SERVER.SESSIONS.persona(SERVER.UT_SID_BASE), 0)
                self.assertEqual(SERVER.SESSIONS.persona("forgé"), 0)

                # And the nucleus header alone no longer names a club on a
                # route that can change one: that header is the user id in
                # plain sight.
                status, body = self._sid_get(
                    port, "/ut/game/fifa14/tournament/user/list", "GET", None,
                    nucleus=111,
                )
                self.assertEqual(status, 200)
                self.assertEqual(body, {"tournamentId": []})
            finally:
                SERVER.TENANTS.forget(111)
                SERVER.TENANTS.forget(222)
                identity.stop()

    def test_the_season_history_is_answered_empty_rather_than_404ed(self) -> None:
        # Asked for once per type the moment a season starts. A 404 here is a
        # hang with nothing to read, and no season has ever been finished, so
        # there is nothing to invent either.
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                for kind in ("offline", "online", "WC_TOURNAMENT_OFFINE"):
                    status, body = self._get(
                        port, f"/ut/game/fifa14/season/user/history?type={kind}"
                    )
                    self.assertEqual((kind, status, body), (kind, 200, {}))
            finally:
                identity.stop()


class TcpServerTests(unittest.TestCase):
    def test_fut_boot_xml_has_required_native_parser_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/futBoot.xml")
                response = client.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Content-Type"),
                    "application/xml; charset=utf-8",
                )
                self.assertEqual(body, SERVER.FUT_BOOT_XML)
                for required in (
                    b"<FutCfg>",
                    b"<cfgVersion>1</cfgVersion>",
                    b"<minorVersion>1</minorVersion>",
                    b"<bootString>fut12</bootString>",
                    b"<futSubVersion>1</futSubVersion>",
                    b"<Language>",
                    b"<dimeUniqueId>1</dimeUniqueId>",
                    b"<key>",
                    b"<dimeUniqueId>2</dimeUniqueId>",
                    b"<futKeyType>0</futKeyType>",
                ):
                    self.assertIn(required, body)
                client.close()

                journal_text = journal_path.read_text(encoding="utf-8")
                self.assertIn('"event": "fut_boot_served"', journal_text)
            finally:
                identity.stop()

    def test_identity_http_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/connect/auth?response_type=code")
                response = client.getresponse()
                self.assertEqual(response.status, 302)
                self.assertEqual(
                    response.getheader("Location"),
                    f"http://127.0.0.1:{port}/connect/redirect?code=offline-fifa14-auth",
                )
                response.read()
                client.close()
            finally:
                identity.stop()

    def test_fut_auth_and_first_use_account_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            accounts = SERVER.AccountStores()
            accounts.get(0x123456789).save_identity(
                0x123456789, "MatchedPersona"
            )
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, accounts
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/ut/auth",
                    body=b'{"isReadOnly":false}',
                    headers={"Content-Type": "application/json"},
                )
                response = client.getresponse()
                auth = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                # Random, not derived from the persona: on a public server a
                # session id derived from the XUID *is* the user id, and a XUID
                # is not a secret. See `SessionStore`.
                self.assertTrue(auth["sid"].startswith("LOCAL-XBOX360-FIFA14-SID-"))
                self.assertGreater(len(auth["sid"]), len("LOCAL-XBOX360-FIFA14-SID-") + 16)
                self.assertIn("serverTime", auth)
                self.assertIn("lastOnlineTime", auth)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/pow/auth",
                    body=b'{"EASW-Session":"LOCAL"}',
                    headers={"Content-Type": "application/json"},
                )
                response = client.getresponse()
                xbox_auth = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertTrue(
                    response.getheader("X-UT-SID").startswith(
                        "LOCAL-XBOX360-FIFA14-SID-"
                    )
                )
                self.assertTrue(
                    xbox_auth["sid"].startswith("LOCAL-XBOX360-FIFA14-SID-")
                )
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/ut/game/fifa14/user/accountinfo")
                response = client.getresponse()
                account = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                # This used to assert an empty persona list, on the grounds
                # that offering one claims a club, squad and identity "that
                # this server cannot then produce". It can produce them now,
                # and a launch on 25 August carried the real persona through a
                # complete login.
                #
                # So the thing that may not exist yet is the *club*, not the
                # persona: the console says who it is the moment Blaze
                # authenticates. An account with no club advertises the persona
                # with an empty `userClubList`, which is the same "no FUT
                # account yet" statement without lying about the player. See
                # AccountInfoPersona for that case.
                persona = account["userAccountInfo"]["personas"][0]
                self.assertEqual(persona["personaName"], SERVER.ClientState.gamertag)
                self.assertIn("userClubList", persona)
                client.close()
            finally:
                identity.stop()

    def test_identity_http_journal_records_request_body_and_unhandled_route(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/pow/auth",
                    body=b'{"EASW-Session":"LOCAL-FIFA14-EASW-SESSION"}',
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(client.getresponse().status, 200)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/ut/game/fifa14/unmodelled",
                    body=b'{"probe":1}',
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(client.getresponse().status, 404)
                client.close()
            finally:
                identity.stop()

            events = [
                __import__("json").loads(line)
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            requests = [
                event
                for event in events
                if event["event"] == "identity_http_request"
            ]
            self.assertEqual(
                requests[0]["body"],
                '{"EASW-Session":"LOCAL-FIFA14-EASW-SESSION"}',
            )
            unhandled = next(
                event
                for event in events
                if event["event"] == "identity_http_unhandled"
            )
            self.assertEqual(unhandled["path"], "/ut/game/fifa14/unmodelled")
            self.assertEqual(unhandled["body"], '{"probe":1}')

    def test_fut_auth_adopts_the_persona_the_client_presents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            accounts = SERVER.AccountStores()
            accounts.get(1_000_001).save_identity(1_000_001, "OfflineFUT")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, accounts
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/pow/auth",
                    body=__import__("json").dumps(
                        {
                            "isReadOnly": False,
                            "sku": "FFA14XBX",
                            "nuc": 2535469248587161,
                            "nucleusPersonaId": 0,
                            "nucleusPersonaDisplayName": "Imskobogota6z",
                            "method": "cas",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(client.getresponse().status, 200)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/ut/game/fifa14/user/accountinfo")
                response = client.getresponse()
                persona = __import__("json").loads(response.read())
                # The Blaze side adopts the persona the client presents --
                # that is what this test is about -- and accountinfo now
                # carries it rather than sending an empty list. The gamertag
                # travelling this far is the point: Impulsum's build hardcodes
                # "FUT14" here, and this console has already said who it is.
                self.assertEqual(
                    persona["userAccountInfo"]["personas"][0]["personaName"],
                    SERVER.ClientState.gamertag,
                )
                client.close()
            finally:
                identity.stop()

            self.assertIn(
                '"event": "fut_auth_identity_adopted"',
                journal_path.read_text(encoding="utf-8"),
            )

    def test_auth_request_identity_rejects_incomplete_documents(self) -> None:
        self.assertIsNone(SERVER.auth_request_identity(b""))
        self.assertIsNone(SERVER.auth_request_identity(b"not json"))
        self.assertIsNone(
            SERVER.auth_request_identity(b'{"nuc":123}')
        )
        self.assertIsNone(
            SERVER.auth_request_identity(b'{"nucleusPersonaDisplayName":"X"}')
        )
        self.assertIsNone(
            SERVER.auth_request_identity(
                b'{"nuc":0,"nucleusPersonaDisplayName":"X"}'
            )
        )
        self.assertEqual(
            SERVER.auth_request_identity(
                b'{"nuc":7,"nucleusPersonaId":9,"nucleusPersonaDisplayName":"X"}'
            ),
            (9, "X"),
        )

    def test_request_body_preview_bounds_and_binary(self) -> None:
        self.assertIsNone(SERVER.request_body_preview(b""))
        self.assertEqual(SERVER.request_body_preview(b'{"a":1}'), '{"a":1}')
        oversized = b"x" * (SERVER.REQUEST_BODY_PREVIEW_LIMIT + 10)
        preview = SERVER.request_body_preview(oversized)
        self.assertTrue(preview.startswith("x" * 64))
        self.assertIn(f"{len(oversized)} bytes total", preview)
        binary = SERVER.request_body_preview(b"\xff\xfe\x00\x01")
        self.assertTrue(binary.startswith("<4 non-utf8 bytes>"))

    def test_fut_first_use_security_and_icebreaker_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]

                def request_json(method: str, path: str, body: bytes | None = None):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request(method, path, body=body)
                    response = client.getresponse()
                    document = __import__("json").loads(response.read())
                    status = response.status
                    headers = dict(response.getheaders())
                    client.close()
                    return status, headers, document

                status, _, trusted = request_json(
                    "GET", "/ut/game/fifa14/phishing/trusteddevice"
                )
                # A known device is what keeps the client from asking its
                # security question on every launch. The four booleans are
                # the whole of what CardsDLL's parser reads here.
                self.assertEqual(status, 200)
                self.assertEqual(
                    trusted,
                    {
                        "trusted": True,
                        "changed": False,
                        "exists": True,
                        "locked": False,
                    },
                )

                status, _, question = request_json(
                    "GET", "/ut/game/fifa14/phishing/question"
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    question,
                    {"question": 0, "attempts": 5, "recoverAttempts": 20},
                )

                status, headers, validation = request_json(
                    "POST",
                    "/ut/game/fifa14/phishing/validate",
                    b'{"answer":"offline"}',
                )
                self.assertEqual(status, 200)
                self.assertEqual(validation["token"], "LOCAL-FIFA14-PHISHING")
                self.assertIn("FUTWebPhishing=", headers["Set-Cookie"])

                status, _, actions = request_json(
                    "GET", "/ut/game/fifa14/user/action"
                )
                # The collection CardsDLL reads is `actions` -- `userActionList`
                # is in no member-name table, so it was a list the parser could
                # not see. Both spellings go out; the unrecognised one is
                # skipped. This is the list FUT_IcebreakerManager consults
                # before deciding whether the captain selection is owed.
                self.assertEqual(status, 200)
                self.assertEqual(actions["actions"], [])
                self.assertEqual(actions["userActionList"], [])

                status, _, updated = request_json(
                    "PUT", "/ut/game/fifa14/user/action/firstUse", b"{}"
                )
                self.assertEqual((status, updated), (200, {}))

                status, _, pack_list = request_json(
                    "GET",
                    "/fut/packs/icebreaker/icebreakerpacklist.json",
                )
                self.assertEqual(status, 200)
                # Four dock rows, each carrying the arrays the card
                # constructor reads.  With only id and image the retail
                # constructor dereferences a null player and the client
                # restarts its bootstrap, so the arrays are the contract,
                # not decoration.
                packs = pack_list["packList"]
                self.assertEqual([pack["id"] for pack in packs], [0, 1, 2, 3])
                self.assertEqual([pack["image"] for pack in packs], [0, 1, 2, 3])
                for pack in packs:
                    self.assertEqual(len(pack["squad"]), 23)
                    self.assertEqual(len(pack["Rating"]), 23)
                    self.assertTrue(all(pack["squad"]))
                self.assertEqual(
                    True,
                    True,
                )
            finally:
                identity.stop()

    def test_fragmented_ping_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            protocol = SERVER.Fifa14Protocol("127.0.0.1", 10041, journal)
            service = SERVER.BlazeService("127.0.0.1", [0], protocol, journal)
            service.start()
            try:
                port = service.listeners[0].getsockname()[1]
                wire = request(9, 2)
                with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                    client.sendall(wire[:5])
                    time.sleep(0.01)
                    client.sendall(wire[5:])
                    header = client.recv(12)
                    self.assertEqual(len(header), 12)
                    payload_size = int.from_bytes(header[:2], "big")
                    payload = b""
                    while len(payload) < payload_size:
                        payload += client.recv(payload_size - len(payload))
                decoded = decode_frame(header + payload)
                self.assertEqual((decoded["component"], decoded["command"]), (9, 2))
                self.assertEqual(decoded["message_number"], 0x12345)
                self.assertIsNotNone(SERVER.find_field(decoded["fields"], "STIM"))
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()


class RouteSpellingTests(unittest.TestCase):
    """The client's spelling of a path and this server's have to agree."""

    def test_the_client_spelling_of_the_watch_list_is_answered(self) -> None:
        import fifa14_blaze_server as server

        # The client asks for `watchList`; this server registered `watchlist`,
        # and every time the watch list was opened it got a 404. Nothing
        # reported it -- an empty watch list looks like an empty watch list.
        self.assertEqual(
            server.FUT_ROUTE_SPELLINGS["/ut/game/fifa14/watchlist"],
            "/ut/game/fifa14/watchlist",
        )
        for spelling in (
            "/ut/game/fifa14/watchList",
            "/ut/game/fifa14/WATCHLIST",
            "/ut/game/fifa14/tradepile",
            "/ut/game/fifa14/clubuser",
        ):
            self.assertIn(
                spelling.lower(), server.FUT_ROUTE_SPELLINGS,
                f"{spelling} does not resolve to a registered route",
            )

    def test_every_route_the_server_names_can_be_reached_in_any_case(self) -> None:
        # The map is built from two lists and the handlers are written by hand,
        # so it drifts unless something checks. Every `/ut/game/fifa14/...`
        # literal in the module has to be in it.
        import re
        from pathlib import Path

        import fifa14_blaze_server as server

        source = Path(server.__file__).read_text()
        literals = set(re.findall(r'"(/ut/game/fifa14/[a-zA-Z0-9/_-]*)"', source))
        # Prefixes used with startswith, not whole routes.
        literals = {route for route in literals if not route.endswith("/")}
        missing = sorted(
            route for route in literals
            if route.lower() not in server.FUT_ROUTE_SPELLINGS
        )
        self.assertEqual(missing, [], f"routes missing from the spelling map: {missing}")


class GameReportingTests(unittest.TestCase):
    """The offline game report the console really submits, component 28/2."""

    # Both captured off this console. The first is what a FUT match submits
    # (`gameType21`), the second a longer report carrying a club record
    # (`gameType85`). Each one used to take the Blaze connection down with it:
    # the TDF decoder had no case for type 7 and raised on `PRVT` at offset 5.
    GAME_TYPE_21 = bytes.fromhex(
        "004A001C000200000000003F9AECE80000C32DB40700CB0CB4039E1B650701"
        "9FC2908E179E1B65038F4CB9010100C2CA640000CE3BF2009C76CEBB270001"
        "00009F2A6400009F4E70010B67616D655479706532310000"
    )
    GAME_TYPE_85 = bytes.fromhex(
        "00AF001C000200000000007D9AECE80000C32DB40700CB0CB4039E1B650701"
        "9BFDB5C50F9E1B65038E7CB407009E1B7203872A6400008E7CB40701"
        "9DD5DD9F1D8E7CB4038ED9F203CB6B2D0000DEEC2B000000B64A6600020000"
        "8F4A6400009F2A6400009F4A6D00AE57A73A6D0000B27A640000CA1BAB0000"
        "CAFA640000CE5A640000CF4D730000D39C25010B67616D655479706538350000"
        "A66C320700D21B72070000009F2A6400009F4E70010B67616D65547970653835"
        "0000"
    )

    def test_the_offline_game_report_decodes_and_re_encodes_unchanged(self) -> None:
        for name, frame in (("21", self.GAME_TYPE_21), ("85", self.GAME_TYPE_85)):
            with self.subTest(game_type=name):
                decoded = decode_frame(frame)
                self.assertEqual(decoded["component"], 28)
                self.assertEqual(decoded["command"], 2)
                labels = [field.label for field in decoded["fields"]]
                self.assertEqual(labels, ["FNSH", "PRVT", "RPRT"])
                # Re-encoding byte for byte is what says the shape was read
                # rather than guessed: there is no slack for a wrong rule to
                # hide in.
                self.assertEqual(encode_fields(decoded["fields"]), frame[12:])

    def test_the_report_carries_a_variable_tdf_holding_the_game(self) -> None:
        decoded = decode_frame(self.GAME_TYPE_21)
        report = SERVER.find_field(decoded["fields"], "RPRT")
        self.assertEqual(report.type, STRUCT)
        # PRVT is an unset variable; GAME is a set one, carrying the 32-bit id
        # of the class whose fields follow.
        private = SERVER.find_field(decoded["fields"], "PRVT")
        self.assertEqual(private.type, 7)
        self.assertIsNone(private.value)
        game = SERVER.find_field(report.value, "GAME")
        self.assertEqual(game.type, 7)
        tdf_id, fields = game.value
        self.assertEqual(tdf_id, 0xB8E2109F)
        inner = SERVER.find_field(fields, "GAME")
        self.assertEqual(SERVER.find_field(inner.value, "SCOR").value, 7580)

    def test_submitting_a_report_is_answered_rather_than_dropped(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SERVER.Journal(Path(temp.name) / "journal.jsonl")
        protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, journal)
        state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)
        replies = protocol.handle(self.GAME_TYPE_21, state)
        # Two: the RPC answer, and the asynchronous ResultNotification the
        # post-match screen waits on before it will leave. Answering the RPC
        # alone is not the end of the handshake.
        self.assertEqual(len(replies), 2)
        answer = decode_frame(replies[0])
        self.assertEqual(answer["message_type"], 1)
        self.assertEqual(answer["error"], 0)

        notification = decode_frame(replies[1])
        self.assertEqual(notification["component"], 28)
        self.assertEqual(notification["command"], 114)
        self.assertEqual(notification["message_type"], 2)
        labels = {field.label: field.value for field in notification["fields"]}
        self.assertEqual(labels["EROR"], 0)
        self.assertEqual(labels["FNL"], 1)
        # GRID travels back in both id members so the notification can be
        # matched to the report that caused it.
        self.assertEqual(labels["GRID"], labels["GHID"])


class TrophyItemTests(unittest.TestCase):
    def test_a_negative_trophy_id_is_answered_like_any_other(self) -> None:
        # The seasons screen asks for /fut/items/xbl2/-1.json, once per
        # division. A digits-only pattern let all ten fall through to the
        # blanket `{"itemData":[]}` that this route exists to replace, and the
        # console then built /fut/items/images/trophies/xbl2/.big with no
        # basename -- eighteen of those are in the journals.
        import json
        import re

        pattern = r"/fut/items/xbl2/(-?\d+)\.json"
        self.assertIsNotNone(re.fullmatch(pattern, "/fut/items/xbl2/-1.json"))
        self.assertIsNotNone(re.fullmatch(pattern, "/fut/items/xbl2/1102.json"))

        # The shape is flat from 25 August -- tournamentId, assetName,
        # silName, locString -- which is what Impulsum's build answers and it
        # has a working trophy screen behind it. The `itemData` wrapper it
        # replaced never rendered a trophy in any session recorded here.
        document = json.loads(SERVER.trophy_item_response(-1))
        # The basename is what the console builds the archive path from, so
        # the only thing that matters is that there is one.
        self.assertEqual(document["assetName"], "trophy_-1_gold")
        self.assertEqual(document["silName"], "trophy_-1_dark")
        # -1 is the seasons screen asking, not a cup. It belongs to no
        # tournament and says so rather than borrowing the first cup's name.
        self.assertEqual(document["tournamentId"], 0)
        self.assertEqual(document["locString"], [])

        # A real cup carries its name here, which is the second route a name
        # travels -- TOURNY_LOC_%d is the first.
        starter = json.loads(SERVER.trophy_item_response(1100))
        self.assertEqual(starter["tournamentId"], 1)
        self.assertEqual(
            starter["locString"], [{"lang": "ENG_US", "label": "Starter Cup"}]
        )


class ConsumableByItemIdTests(unittest.TestCase):
    def test_a_consumable_can_be_applied_by_its_own_item_id(self) -> None:
        # The client addresses a consumable two ways: `item/resource/<id>`
        # names the definition, `item/<id>` names one particular card in the
        # club. Only the first was handled, so this real request on 11 August
        #
        #     POST /ut/game/fifa14/item/1950000106
        #     {"apply":[{"id":1700000004}]}
        #
        # was answered 404 and went into the unhandled journal, where nobody
        # looked. From the player's side the card simply did nothing.
        import fut_inventory as inventory

        club = inventory.ClubInventory()
        rack = inventory.ConsumableRack(club)
        consumable = next(
            item for item in club.items
            if item.get("itemType") in inventory.CONSUMABLE_TYPES
            and item.get("resourceId")
        )
        self.assertEqual(
            rack.resource_of(consumable["id"]), consumable["resourceId"]
        )

        player = next(i for i in club.items if i.get("itemType") == "player")
        with self.assertRaises(inventory.ConsumableRefused):
            rack.resource_of(player["id"])
        with self.assertRaises(inventory.ConsumableRefused):
            rack.resource_of(-999)


class EveryRouteAnswersTests(unittest.TestCase):
    """A GET on every registered FUT route comes back 200 and parseable.

    The watch list was a 404 for as long as it has existed, because the client
    spells it `watchList` and this server registered `watchlist`. Nothing
    noticed, because a 404 on a FUT route just leaves a screen empty. This
    walks the whole surface so the next one is noticed by a test rather than by
    a player wondering why a screen is blank.

    Routes taking an id, and the two that are not JSON, are named below rather
    than skipped silently -- a skip list nobody reads is how the last one got
    through.
    """

    # Prefixes, not whole routes: they need an id or a body to mean anything.
    NEEDS_MORE = (
        "/ut/game/fifa14/trade",
        "/ut/game/fifa14/user/action",
        "/ut/game/fifa14/phishing",
        "/ut/game/fifa14/item",
        "/ut/game/fifa14/auctionhouse",
        "/ut/game/fifa14/squad",
        "/ut/game/fifa14/tournament/user",
        "/ut/game/fifa14/store",
    )

    def test_every_registered_route_answers_a_get(self) -> None:
        import http.client
        import json as jsonlib

        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
            identity.start()
            try:
                port = identity.server.server_address[1]
                routes = sorted(
                    set(SERVER.FUT_ROUTES) | set(SERVER.HANDLED_ROUTES)
                )
                checked = 0
                for route in routes:
                    if route in self.NEEDS_MORE:
                        continue
                    for spelling in (route, route.lower()):
                        client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                        client.request("GET", spelling)
                        response = client.getresponse()
                        payload = response.read()
                        status = response.status
                        client.close()
                        self.assertEqual(status, 200, f"{spelling} answered {status}")
                        if payload.strip():
                            jsonlib.loads(payload)
                    checked += 1
                # If this ever drops to nothing the loop has stopped testing.
                self.assertGreater(checked, 30)
            finally:
                identity.stop()


class JournalReplayTests(unittest.TestCase):
    def test_the_most_recent_recorded_session_still_answers(self) -> None:
        # The journals are a regression suite nobody was running. Two of
        # tonight's fixes came out of reading them by hand -- the watch list
        # 404ing on a capital L, and a consumable applied by its own item id
        # falling through unhandled -- and both had been failing for days in a
        # screen that merely looked empty.
        import importlib.util

        journals = sorted((ROOT / "runtime").glob("live-easw-*.jsonl"))
        if not journals:
            self.skipTest("no recorded session in runtime/")

        spec = importlib.util.spec_from_file_location(
            "replay_journal", ROOT / "tools" / "replay_journal.py"
        )
        replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replay)

        # The newest session with enough in it to be worth replaying.
        substantial = [
            path for path in journals
            if sum(1 for _ in replay.requests_in(path)) >= 20
        ]
        if not substantial:
            self.skipTest("no recorded session with requests in it")
        self.assertEqual(replay.replay(substantial[-1:], quiet=True), 0)


class SessionResumeTests(unittest.TestCase):
    def test_a_second_connection_is_told_whose_it_is(self) -> None:
        # The EAS FC module opens a Blaze connection of its own once its
        # endpoints point somewhere reachable, and the first thing it says is
        #
        #     component 0x7802 command 35   SKEY "offline-901feefe6a599"
        #
        # which is the key the login handed out on the first connection. It was
        # answered with a fieldless success and nothing else, so the module was
        # acknowledged and then never told who it was.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SERVER.Journal(Path(temp.name) / "journal.jsonl")
        store = SERVER.AccountStores(Path(temp.name) / "account.json")
        protocol = SERVER.Fifa14Protocol(
            "192.0.2.35", 10041, journal, accounts=store
        )
        xuid = 0x901FEEFE6A599
        # Stored against that persona, because the key names it: the resume
        # reads "offline-<persona in hex>" and looks that persona up rather
        # than consulting one shared store, which would resume whoever logged
        # in last.
        store.get(xuid).save_identity(xuid, "Imskobogota6z")

        state = SERVER.ClientState(2, ("192.0.2.25", 1037), 10041)
        replies = protocol.handle(
            request(0x7802, 35, [Field("SKEY", STRING, f"offline-{xuid:x}")]),
            state,
        )
        self.assertEqual(len(replies), 4)
        self.assertEqual(decode_frame(replies[0])["message_type"], 1)
        self.assertTrue(state.authenticated)
        self.assertEqual(state.xuid, xuid)

        sent = [decode_frame(frame) for frame in replies[1:]]
        self.assertEqual([frame["command"] for frame in sent], [8, 2, 1])
        for frame in sent:
            self.assertEqual(frame["component"], 0x7802)
            self.assertEqual(frame["message_type"], 2)
        authenticated = {f.label: f.value for f in sent[0]["fields"]}
        self.assertEqual(authenticated["DSNM"], "Imskobogota6z")
        self.assertEqual(authenticated["BUID"], xuid)

    def test_a_key_that_names_nobody_is_refused_quietly(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SERVER.Journal(Path(temp.name) / "journal.jsonl")
        store = SERVER.AccountStores(Path(temp.name) / "account.json")
        protocol = SERVER.Fifa14Protocol(
            "192.0.2.35", 10041, journal, accounts=store
        )
        store.get(1234).save_identity(1234, "Someone")
        state = SERVER.ClientState(2, ("192.0.2.25", 1037), 10041)
        replies = protocol.handle(
            request(0x7802, 35, [Field("SKEY", STRING, "offline-deadbeef")]),
            state,
        )
        # Answered, but nobody is claimed on the strength of a key that names
        # a session this server never handed out.
        self.assertEqual(len(replies), 1)
        self.assertFalse(state.authenticated)


class IdentityChannelTests(unittest.TestCase):
    def test_the_user_document_carries_the_persona_the_headers_do(self) -> None:
        # FUT tells a client who it is through four channels and they have to
        # agree: the /user body's personaId, /eaid/personas, and the
        # EASW-Nucleus-Persona and EASW-Userid headers. Ours carried the
        # console's real nucleus id in both headers and a flat 0 in the body.
        import json

        import fut_inventory as inventory

        wallet = inventory.Wallet()
        persona = 2535469248587161
        document = json.loads(wallet.user_info("Fondateur FUT", "FUT", persona))
        self.assertEqual(document["personaId"], persona)

        # And every other document that carries one carries the same. Aligning
        # /user alone is worse than leaving them all wrong: the squad screen
        # came back with eleven blank cards, because a client will not show a
        # squad that belongs to somebody else.
        inventory.PERSONA.adopt(persona)
        try:
            club = inventory.ClubInventory()
            squad = json.loads(club.squad_document(club.active_squad_id(), "bpl"))
            self.assertEqual(squad["personaId"], persona)
            self.assertEqual(
                json.loads(club.active_squad_response("bpl"))["personaId"], persona
            )
            self.assertEqual(
                json.loads(wallet.user_info("bpl", "FUT"))["personaId"], persona
            )
        finally:
            inventory.PERSONA.id = 0


class AccountStoreTenancyTests(unittest.TestCase):
    """Two players must not share account state.

    This is not hypothetical. On 20 August a second player logged into the
    public server and `runtime/local-account.json` came back carrying *his*
    gamertag: one file for the whole machine, last writer wins. The clubs were
    already per-tenant, so nothing visible broke -- the club is what holds the
    cards and the coins -- but the identity and the first-login flag were
    shared, and either player relaunching reset the other.
    """

    def test_two_personas_keep_separate_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            accounts = SERVER.AccountStores(Path(temp) / "local-account.json")
            accounts.get(111).save_identity(111, "PlayerOne")
            accounts.get(222).save_identity(222, "PlayerTwo")
            self.assertEqual(accounts.get(111).load_identity(), (111, "PlayerOne"))
            self.assertEqual(accounts.get(222).load_identity(), (222, "PlayerTwo"))

    def test_a_reset_touches_only_the_caller(self) -> None:
        # `/revival/reset` is sent by every launch. Shared, it logged the other
        # player's console back to its first run in the middle of their game.
        with tempfile.TemporaryDirectory() as temp:
            accounts = SERVER.AccountStores(Path(temp) / "local-account.json")
            accounts.get(111).save_setting("FirstTimeFlag", "1")
            accounts.get(222).save_setting("FirstTimeFlag", "1")
            accounts.get(111).reset()
            self.assertEqual(accounts.get(111).load_setting("FirstTimeFlag"), "0")
            self.assertEqual(accounts.get(222).load_setting("FirstTimeFlag"), "1")

    def test_each_persona_gets_its_own_file_beside_the_clubs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "local-account.json"
            accounts = SERVER.AccountStores(root)
            accounts.get(111).save_identity(111, "PlayerOne")
            self.assertTrue((Path(temp) / "accounts" / "111.json").exists())

    def test_persona_zero_keeps_the_historical_path(self) -> None:
        # A single console that never identifies itself, and the whole test
        # suite, must behave exactly as before -- same convention as
        # club_save_path.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "local-account.json"
            accounts = SERVER.AccountStores(root)
            self.assertEqual(accounts.path_for(0), root)
            accounts.get(0).save_identity(7, "Nobody")
            self.assertTrue(root.exists())

    def test_the_same_persona_gets_the_same_store_back(self) -> None:
        accounts = SERVER.AccountStores()
        self.assertIs(accounts.get(111), accounts.get(111))
        self.assertIsNot(accounts.get(111), accounts.get(222))


class RoutesTheTitleAsksForTests(unittest.TestCase):
    """The nine routes the journal kept writing down as unknown.

    They were found the way they should be found: the server writes
    `unknown_route` every time the title sends a component and command it has
    no handler for, so between that and the 404s on the identity port the game
    keeps its own list of what is missing. These are what was on it.

    Seven of them are acknowledged and nothing more, which is exactly what the
    fallback already did -- naming them is about the list, not the game. The
    two lookups are different: the server knew the answer and was throwing it
    away.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.accounts = SERVER.AccountStores(Path(self.temp.name) / "account.json")
        self.protocol = SERVER.Fifa14Protocol(
            "192.0.2.35", 10041, self.journal, accounts=self.accounts
        )
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)
        self.state.xuid = 2535469248587161
        self.state.gamertag = "Imskobogota6z"
        self.state.authenticated = True

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def unknown_routes(self) -> list[tuple[int, int]]:
        # The journal is opened on the first line written, so no file at all
        # is the strongest form of "nothing was unknown".
        path = Path(self.temp.name) / "journal.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
        return [
            (json.loads(line)["component"], json.loads(line)["command"])
            for line in lines
            if json.loads(line).get("event") == "unknown_route"
        ]

    def test_a_user_looks_itself_up_and_gets_its_own_name(self) -> None:
        """The title asks with everything zeroed: it means itself."""
        response = self.protocol.handle(
            request(0x7802, 12, [
                Field("ID", INTEGER, 0),
                Field("NAME", STRING, "Imskobogota6z"),
            ]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        user = by_label(decoded, "USER")
        self.assertEqual(SERVER.find_field(user.value, "NAME").value, "Imskobogota6z")
        self.assertEqual(
            SERVER.find_field(user.value, "ID").value, 2535469248587161
        )
        # And the extended data the client pairs with a user everywhere else.
        self.assertIsNotNone(by_label(decoded, "DATA"))

    def test_a_bulk_lookup_answers_in_the_label_it_was_asked_in(self) -> None:
        response = self.protocol.handle(
            request(0x7802, 13, [
                Field("LTYP", INTEGER, 1),
                Field("ULST", LIST, (STRUCT, [
                    [Field("ID", INTEGER, 0), Field("NAME", STRING, "")],
                ])),
            ]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        item_type, entries = by_label(decoded, "ULST").value
        self.assertEqual(item_type, STRUCT)
        self.assertEqual(len(entries), 1)
        user = SERVER.find_field(entries[0], "USER")
        self.assertEqual(
            SERVER.find_field(user.value, "NAME").value, "Imskobogota6z"
        )

    def test_a_lookup_for_a_stranger_returns_nobody_rather_than_the_asker(self) -> None:
        """Answering "that is me" to a question about someone else is worse
        than answering nothing: it would make two clubs one player."""
        response = self.protocol.handle(
            request(0x7802, 13, [
                Field("ULST", LIST, (STRUCT, [
                    [Field("ID", INTEGER, 999_000_111)],
                ])),
            ]),
            self.state,
        )[0]
        _, entries = by_label(decode_frame(response), "ULST").value
        self.assertEqual(entries, [])

    def test_a_lookup_finds_the_other_player_on_this_server(self) -> None:
        """With a second club present, its own name comes back -- not the
        name of whoever logged in last, which is what one shared store did."""
        self.accounts.get(700_100).save_identity(700_100, "Racim")
        response = self.protocol.handle(
            request(0x7802, 12, [Field("ID", INTEGER, 700_100)]),
            self.state,
        )[0]
        user = by_label(decode_frame(response), "USER")
        self.assertEqual(SERVER.find_field(user.value, "NAME").value, "Racim")

    def test_the_seven_quiet_routes_stop_being_written_down_as_unknown(self) -> None:
        quiet = [
            (10, 2),    # CensusData, unsubscribe
            (21, 11),   # Rooms, category updates
            (21, 150),  # Rooms, an ENBL toggle
            (7, 10),    # Stats, a leaderboard descriptor
            (7, 13),    # Stats, a centred leaderboard page
            (1, 48),    # Authentication, entitlements for one persona
            (1, 39),    # Authentication, an entitlement grant
        ]
        for component, command in quiet:
            answered = self.protocol.handle(request(component, command), self.state)
            self.assertEqual(len(answered), 1, (component, command))
            # Still a success, and still carrying nothing -- the point is the
            # journal, not the payload.
            self.assertEqual(decode_frame(answered[0])["error"], 0)
        self.assertEqual(self.unknown_routes(), [])

    def test_a_route_nobody_has_implemented_is_still_written_down(self) -> None:
        """GameManager is component 4, and the day it appears in this list is
        the day the online modes start asking to exist."""
        self.protocol.handle(request(4, 60), self.state)
        self.assertEqual(self.unknown_routes(), [(4, 60)])


class MatchmakingTests(unittest.TestCase):
    """GameManager, from the day the game first spoke to it.

    Component 4 was advertised from the start and never used. On 21 August
    2026 a console was taken into Face-à-Face and sent `startMatchmaking`
    carrying NTOP 130 -- PEER_TO_PEER_FULL_MESH -- which says the match runs
    console to console and this server is the matchmaker, not a game host.

    What is pinned here is the smallest reply that turns a silent hang into a
    search the client is running: a non-zero session id, and a cancellation
    that ends it cleanly.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)
        self.state.xuid = 2535469248587161
        self.state.authenticated = True

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def search(self) -> list[bytes]:
        """The frame the console actually sent, trimmed to what is read."""
        return self.protocol.handle(
            request(4, 13, [
                Field("DUR", INTEGER, 20000),
                Field("GVER", STRING, "qa-only-day45"),
                Field("MODE", INTEGER, 3),
                Field("NTOP", INTEGER, 130),
                Field("PNET", SERVER.UNION, (0, Field("VALU", STRUCT, [
                    Field("MACI", INTEGER, 1780144225),
                    Field("XDDR", BINARY, bytes.fromhex("C0A80119020B639A")),
                    Field("XUID", INTEGER, 2535469248587161),
                ]))),
            ]),
            self.state,
        )

    def journal_events(self, kind: str) -> list[dict]:
        path = Path(self.temp.name) / "journal.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
            if json.loads(line).get("event") == kind
        ]

    def test_a_search_comes_back_with_a_session_the_client_can_name(self) -> None:
        """A fieldless success decodes MSID as 0, and the client then has no
        session to wait on and none to cancel. That was the silent hang."""
        answered = self.search()
        session = by_label(decode_frame(answered[0]), "MSID").value
        self.assertNotEqual(session, 0)

    def test_the_search_is_followed_by_an_async_status_on_the_same_session(self) -> None:
        """Proof the push path works, before anything is built on it."""
        answered = self.search()
        self.assertEqual(len(answered), 2)
        status = decode_frame(answered[1])
        self.assertEqual(status["component"], 4)
        self.assertEqual(status["command"], 12)
        self.assertEqual(
            by_label(status, "MSID").value,
            by_label(decode_frame(answered[0]), "MSID").value,
        )
        self.assertEqual(by_label(status, "USID").value, 2535469248587161)

    def test_backing_out_ends_the_search_rather_than_leaving_it_running(self) -> None:
        session = by_label(decode_frame(self.search()[0]), "MSID").value
        answered = self.protocol.handle(request(4, 14), self.state)
        failed = decode_frame(answered[1])
        self.assertEqual(failed["command"], 10)
        self.assertEqual(by_label(failed, "MSID").value, session)
        # 4 is SESSION_CANCELED. Notification 10 is `NotifyMatchmakingFailed`
        # in this build, not Blaze 2's `NotifyMatchmakingFinished`: it carries
        # the failure path only, so it can end a search and never start a match.
        self.assertEqual(by_label(failed, "RSLT").value, 4)

    def test_two_searches_are_two_sessions(self) -> None:
        first = by_label(decode_frame(self.search()[0]), "MSID").value
        self.protocol.handle(request(4, 14), self.state)
        second = by_label(decode_frame(self.search()[0]), "MSID").value
        self.assertNotEqual(first, second)

    def test_the_peer_address_is_written_down_because_it_is_what_gets_relayed(self) -> None:
        """With a second console, the other side needs this blob verbatim to
        dial back. There is no second console yet, so it goes in the journal."""
        self.search()
        started = self.journal_events("matchmaking_started")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["topology"], 130)
        self.assertEqual(started[0]["game_version"], "qa-only-day45")
        self.assertIn("XDDR", json.dumps(started[0]["network"]))

    def test_matchmaking_is_no_longer_written_down_as_unknown(self) -> None:
        self.search()
        path = Path(self.temp.name) / "journal.jsonl"
        unknown = [
            line for line in path.read_text().splitlines()
            if json.loads(line).get("event") == "unknown_route"
        ]
        self.assertEqual(unknown, [])


class MatchmakingTimeoutTests(unittest.TestCase):
    """The server has to be the one that ends a search.

    On 21 August the console sat on the spinner for minutes with `DUR` set to
    twenty seconds. Blaze's duration is an instruction to the matchmaker, not
    a client-side timeout: the client waits until it is told. Until this, the
    server had no way to tell it anything -- every frame it had ever sent was
    a reply written by the loop that had just read a request.
    """

    class Channel:
        """Stands in for the socket, and records what was pushed."""

        def __init__(self) -> None:
            self.sent: list[bytes] = []
            self.broken = False

        def sendall(self, data: bytes) -> None:
            if self.broken:
                raise OSError("connection reset")
            self.sent.append(data)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)
        self.state.xuid = 2535469248587161
        self.channel = self.Channel()
        self.state.channel = self.channel

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def start(self, duration_ms: int = 20000) -> int:
        answered = self.protocol.handle(
            request(4, 13, [Field("DUR", INTEGER, duration_ms)]), self.state
        )
        return by_label(decode_frame(answered[0]), "MSID").value

    def test_a_search_nobody_answers_ends_by_itself(self) -> None:
        session = self.start()
        self.protocol.expire_matchmaking(self.state, session)
        self.assertEqual(len(self.channel.sent), 1)
        failed = decode_frame(self.channel.sent[0])
        self.assertEqual(failed["component"], 4)
        self.assertEqual(failed["command"], 10)
        self.assertEqual(by_label(failed, "MSID").value, session)
        # 3 is SESSION_TIMED_OUT, and it is the truth: there is genuinely
        # nobody else on this server to be matched with.
        self.assertEqual(by_label(failed, "RSLT").value, 3)

    def test_a_search_already_cancelled_is_not_ended_twice(self) -> None:
        session = self.start()
        self.protocol.handle(request(4, 14), self.state)
        self.protocol.expire_matchmaking(self.state, session)
        self.assertEqual(self.channel.sent, [])

    def test_a_timer_from_an_abandoned_search_cannot_end_the_new_one(self) -> None:
        """Back out, search again, and the first timer fires mid-search."""
        first = self.start()
        self.protocol.handle(request(4, 14), self.state)
        second = self.start()
        self.protocol.expire_matchmaking(self.state, first)
        self.assertEqual(self.channel.sent, [])
        self.assertNotEqual(second, first)

    def test_a_console_that_went_away_is_not_an_error(self) -> None:
        """The console dropped off the network twice today. A timer firing
        into a dead socket has to be a non-event, not a traceback."""
        session = self.start()
        self.channel.broken = True
        self.protocol.expire_matchmaking(self.state, session)
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        timed_out = [e for e in events if e.get("event") == "matchmaking_timed_out"]
        self.assertEqual(len(timed_out), 1)
        self.assertIs(timed_out[0]["delivered"], False)

    def test_nothing_is_pushed_before_a_connection_has_a_channel(self) -> None:
        self.state.channel = None
        session = self.start()
        self.protocol.expire_matchmaking(self.state, session)  # must not raise

    def test_the_timer_is_armed_from_the_duration_the_client_asked_for(self) -> None:
        timer = self.protocol.schedule_matchmaking_timeout(self.state, 7, 20000)
        try:
            self.assertAlmostEqual(timer.interval, 20.0, places=3)
        finally:
            timer.cancel()
        # And a floor, so a very short search is not answered before the
        # client's own screen has drawn.
        timer = self.protocol.schedule_matchmaking_timeout(self.state, 8, 10)
        try:
            self.assertAlmostEqual(timer.interval, 2.0, places=3)
        finally:
            timer.cancel()

    def test_a_search_takes_its_timer_with_it_when_it_ends(self) -> None:
        """Six of these fired after their own journals had been deleted.

        A cancelled search left its timer running for the full twenty
        seconds. Nothing broke on the console -- `expire_matchmaking` checks
        the session id and finds nothing to do -- but a timer that outlives
        its reason is a thread waiting to touch state that has gone.
        """
        self.start()
        self.protocol.handle(request(4, 14), self.state)
        self.assertEqual(self.protocol.matchmaking_timers, {})

    def test_starting_again_replaces_the_previous_timer(self) -> None:
        self.start()
        first = self.protocol.matchmaking_timers[self.state.connection_id]
        self.start()
        second = self.protocol.matchmaking_timers[self.state.connection_id]
        self.assertIsNot(first, second)
        self.assertFalse(first.is_alive())


class CreateGameTests(unittest.TestCase):
    """The real 611-byte createGame, replayed against the server.

    Same frame the decoder used to die on, so this also proves the two fixes
    hold together: the union-in-a-list rule reads it, and the handler answers
    it with a game number instead of a fieldless success that decodes as 0.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.168.1.25", 1040), 10041)
        self.state.xuid = 2535469248587161
        self.state.authenticated = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blaze_tdf_union_lists import CREATE_GAME
        self.frame = CREATE_GAME

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def events(self, kind: str) -> list[dict]:
        path = Path(self.temp.name) / "journal.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
            if json.loads(line).get("event") == kind
        ]

    def test_the_console_gets_a_game_number_and_then_the_game(self) -> None:
        """A number alone is not a game: the client waits to be told what it
        is, and by whom it is hosted."""
        answered = self.protocol.handle(self.frame, self.state)
        self.assertEqual(len(answered), 5)
        game_id = by_label(decode_frame(answered[0]), "GID").value
        self.assertNotEqual(game_id, 0)

        setup = decode_frame(answered[1])
        self.assertEqual((setup["component"], setup["command"]), (4, 20))
        # Five members, and LFPJ is in no published table for any other Blaze
        # title -- it is a FIFA-14-era addition read out of this binary.
        self.assertEqual(
            [field.label for field in setup["fields"]],
            ["GAME", "LFPJ", "PROS", "QUEU", "REAS"],
        )

        host = decode_frame(answered[2])
        self.assertEqual((host["component"], host["command"]), (4, 71))
        self.assertEqual(by_label(host, "GID").value, game_id)
        self.assertEqual(by_label(host, "PHID").value, 2535469248587161)

    def test_the_game_it_is_told_about_is_the_one_it_asked_for(self) -> None:
        setup = decode_frame(self.protocol.handle(self.frame, self.state)[1])
        game = {f.label: f.value for f in by_label(setup, "GAME").value}
        self.assertEqual(game["GTYP"], "gameType0")
        self.assertEqual(game["CAP"], (INTEGER, [2, 0, 0, 0]))
        # All thirty-six members, less the two the host has not handed over
        # yet. The first pass sent fourteen, because fourteen was all the
        # member table appeared to hold -- the other twenty-two are written
        # into that table at startup, each tag assembled from a pair of
        # instructions, which is why searching the image for them found
        # nothing while they travelled on the wire the whole time.
        self.assertEqual(
            sorted(game),
            ["ADMN", "ATTR", "CAP", "CRIT", "GID", "GMRG", "GNAM", "GPVH",
             "GSET", "GSID", "GSTA", "GTYP", "GURL", "HNET", "HSES", "IGNO",
             "MATR", "MCAP", "NQOS", "NRES", "NTOP", "PGID", "PGSR", "PHST",
             "PRES", "PSAS", "QCAP", "RNFO", "SEED", "THST", "TIDS", "UUID",
             "VOIP", "VSTR"],
        )
        # XNNC and XSES are members and are deliberately absent: they are the
        # host's XNet nonce and session, which arrive in finalizeGameCreation.
        self.assertNotIn("XNNC", game)
        self.assertNotIn("XSES", game)
        # Which session hosts, said twice because the game data carries it
        # twice -- platform host and topology host.
        for label in ("PHST", "THST"):
            host = {f.label: f.value for f in game[label]}
            self.assertEqual(host["HPID"], 2535469248587161)
        # The host's address, handed straight back. A second console dials
        # this blob; a byte changed here is a match that never connects.
        addresses = game["HNET"][1]
        self.assertEqual(len(addresses), 1)
        active, members = addresses[0]
        self.assertEqual(active, 0)
        self.assertEqual(
            SERVER.find_field(members, "XDDR").value[:4], bytes([192, 168, 1, 25])
        )

    def test_the_roster_holds_the_host_with_its_own_address(self) -> None:
        setup = decode_frame(self.protocol.handle(self.frame, self.state)[1])
        item_type, roster = by_label(setup, "PROS").value
        self.assertEqual(item_type, STRUCT)
        self.assertEqual(len(roster), 1)
        player = {f.label: f.value for f in roster[0]}
        self.assertEqual(player["PID"], 2535469248587161)
        self.assertEqual(player["STAT"], 4)  # ACTIVE_CONNECTED, not 2
        # CONG, CSID and ROLE are in no published table for any other title.
        self.assertIn("CONG", player)
        self.assertIn("CSID", player)
        # As a field rather than a list element, a union carries a VALU.
        active, valu = player["PNET"]
        self.assertEqual(active, 0)
        self.assertEqual(valu.label, "VALU")

    def test_the_reason_says_the_client_created_this_itself(self) -> None:
        """CREATE_GAME_SETUP_CONTEXT is not a union index -- it is a value of
        the DCTX enum inside index 0. Getting that round the wrong way would
        tell the client it had joined something."""
        setup = decode_frame(self.protocol.handle(self.frame, self.state)[1])
        active, valu = by_label(setup, "REAS").value
        self.assertEqual(active, 0)
        self.assertEqual(valu.label, "VALU")
        self.assertEqual(SERVER.find_field(valu.value, "DCTX").value, 0)

    def test_the_game_data_goes_out_in_ascending_tag_order(self) -> None:
        """What the client does with its own fields, so what it is given."""
        from blaze_tdf import encode_tag
        setup = decode_frame(self.protocol.handle(self.frame, self.state)[1])
        labels = [f.label for f in by_label(setup, "GAME").value]
        self.assertEqual(labels, sorted(labels, key=encode_tag))

    def test_two_games_are_two_numbers(self) -> None:
        first = by_label(decode_frame(self.protocol.handle(self.frame, self.state)[0]), "GID").value
        second = by_label(decode_frame(self.protocol.handle(self.frame, self.state)[0]), "GID").value
        self.assertNotEqual(first, second)

    def test_what_the_game_is_gets_written_down(self) -> None:
        """Including the host address, which is the thing a second console
        will need verbatim to dial this one."""
        self.protocol.handle(self.frame, self.state)
        created = self.events("game_created")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["topology"], 130)
        self.assertEqual(created[0]["game_type"], "gameType0")
        self.assertEqual(created[0]["protocol_version"], "qa-only-day45")
        self.assertIn("C0A80119", json.dumps(created[0]["host_addresses"]))
        self.assertIn("fifaHalfLength", json.dumps(created[0]["settings"]))

    def test_creating_a_game_is_no_longer_an_unknown_route(self) -> None:
        self.protocol.handle(self.frame, self.state)
        self.assertEqual(self.events("unknown_route"), [])


    def test_the_game_is_moved_out_of_initialising(self) -> None:
        """The setup alone left the console back on the settings screen.

        A game that has just been created is INITIALIZING; a game waiting for
        an opponent is PRE_GAME. The client had read the game and had no
        reason to sit in one, because as far as it knew it was still being
        built.
        """
        answered = self.protocol.handle(self.frame, self.state)
        state_change = decode_frame(answered[4])
        self.assertEqual((state_change["component"], state_change["command"]), (4, 100))
        # 130, not 3 -- the two GameState values that matter are the two that
        # look least like the rest of the enum.
        self.assertEqual(by_label(state_change, "GSTA").value, 130)
        self.assertEqual(
            by_label(state_change, "GID").value,
            by_label(decode_frame(answered[0]), "GID").value,
        )

    def test_the_game_the_server_remembers_says_pre_game_too(self) -> None:
        self.protocol.handle(self.frame, self.state)
        game = next(iter(self.protocol.games.values()))
        self.assertEqual(game.state, 130)

    def test_the_host_is_told_it_finished_joining_its_own_game(self) -> None:
        """The roster describes the host as connected; this is the event that
        says so. Pressing "Créer un match" drops the console's A/B prompts --
        it acts on the setup -- and then waits."""
        answered = self.protocol.handle(self.frame, self.state)
        joined = decode_frame(answered[3])
        self.assertEqual((joined["component"], joined["command"]), (4, 30))
        self.assertEqual(by_label(joined, "PID").value, 2535469248587161)
        self.assertEqual(
            by_label(joined, "GID").value,
            by_label(decode_frame(answered[0]), "GID").value,
        )


class CensusTests(unittest.TestCase):
    """The two counters at the top of the Face-à-Face screen.

    "Joueurs en ligne : 0 — En cours de partie : 0" was not a bug in the
    title. It subscribes to the census and this server answered with a
    fieldless success and then never pushed anything, so zero was all it had.

    The tags are the title's own compiled-in member names: `LSN` is
    mNumOfLoggedSession and `AGN` is mNumOfActiveGame, in a
    GameManagerCensusData whose class id rides in the variable TDF that
    carries it.
    """

    class Channel:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.168.1.25", 1040), 10041)
        self.state.xuid = 2535469248587161
        self.state.authenticated = True
        self.channel = self.Channel()
        self.state.channel = self.channel
        self.protocol.remember_connection(self.state)

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def numbers(self, frame: bytes) -> dict:
        decoded = decode_frame(frame)
        self.assertEqual((decoded["component"], decoded["command"]), (10, 1))
        _, items = by_label(decoded, "TDFL").value
        variable = SERVER.find_field(items[0], "TDF")
        tdf_id, fields = variable.value
        self.assertEqual(tdf_id, 0x21239231)
        return {field.label: field.value for field in fields}

    def test_subscribing_gets_the_numbers_with_the_reply(self) -> None:
        """So the first paint already has them rather than a zero that is
        later corrected."""
        answered = self.protocol.handle(request(10, 1), self.state)
        self.assertEqual(len(answered), 2)
        counts = self.numbers(answered[1])
        self.assertEqual(counts["LSN"], 1)   # one console, and 1 is the truth
        self.assertEqual(counts["AGN"], 0)

    def test_creating_a_game_pushes_a_new_count(self) -> None:
        self.protocol.handle(request(10, 1), self.state)
        self.channel.sent.clear()
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blaze_tdf_union_lists import CREATE_GAME
        self.protocol.handle(CREATE_GAME, self.state)
        pushed = [f for f in self.channel.sent if decode_frame(f)["component"] == 10]
        self.assertTrue(pushed)
        self.assertEqual(self.numbers(pushed[-1])["AGN"], 1)

    def test_searching_shows_up_as_a_matchmaking_session(self) -> None:
        self.protocol.handle(request(10, 1), self.state)
        self.channel.sent.clear()
        self.protocol.handle(request(4, 13, [Field("DUR", INTEGER, 20000)]), self.state)
        pushed = [f for f in self.channel.sent if decode_frame(f)["component"] == 10]
        self.assertEqual(self.numbers(pushed[-1])["MMSN"], 1)

    def test_unsubscribing_stops_the_pushes(self) -> None:
        self.protocol.handle(request(10, 1), self.state)
        self.protocol.handle(request(10, 2), self.state)
        self.channel.sent.clear()
        self.protocol.handle(request(4, 13, [Field("DUR", INTEGER, 20000)]), self.state)
        self.assertEqual(
            [f for f in self.channel.sent if decode_frame(f)["component"] == 10], []
        )

    def test_a_console_that_leaves_takes_its_game_with_it(self) -> None:
        """Otherwise the count only ever goes up, and "En cours de partie"
        would climb every time somebody pressed create and walked away."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blaze_tdf_union_lists import CREATE_GAME
        self.protocol.handle(CREATE_GAME, self.state)
        self.assertEqual(len(self.protocol.games), 1)
        self.protocol.forget_connection(self.state)
        self.assertEqual(self.protocol.games, {})
        self.assertEqual(self.protocol.live, {})

    def test_the_census_keeps_being_pushed_while_somebody_is_subscribed(self) -> None:
        """The numbers were right the first time and the screen still read
        zero: the one push went out at boot, long before anybody walked into
        the screen that draws them."""
        self.protocol.census_interval = 0.05
        self.protocol.handle(request(10, 1), self.state)
        self.channel.sent.clear()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if [f for f in self.channel.sent if decode_frame(f)["component"] == 10]:
                break
            time.sleep(0.02)
        pushed = [f for f in self.channel.sent if decode_frame(f)["component"] == 10]
        self.assertTrue(pushed, "rien poussé sans changement d'état")
        self.assertEqual(self.numbers(pushed[-1])["LSN"], 1)

    def test_the_heartbeat_stops_when_the_last_subscriber_leaves(self) -> None:
        self.protocol.handle(request(10, 1), self.state)
        self.assertIsNotNone(self.protocol.census_pulse)
        self.protocol.handle(request(10, 2), self.state)
        self.assertIsNone(self.protocol.census_pulse)


class SyntheticOpponentTests(unittest.TestCase):
    """Finding somebody who does not exist, on purpose.

    There is nobody else here, so a search honestly times out. That leaves one
    question unanswerable with a single console: how far does the title get
    into a match before it needs a peer that answers UDP? Every Blaze-side
    layout can be wrong in ways that look identical to a network failure from
    the outside, and telling those apart is the whole point.
    """

    class Channel:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.168.1.25", 1040), 10041)
        self.state.xuid = 2535469248587161
        self.state.gamertag = "Imskobogota6z"
        self.state.authenticated = True
        self.channel = self.Channel()
        self.state.channel = self.channel

    def tearDown(self) -> None:
        os.environ.pop("FIFA14_TEST_OPPONENT", None)
        self.protocol.stop()
        self.temp.cleanup()

    def search(self) -> int:
        answered = self.protocol.handle(
            request(4, 13, [
                Field("DUR", INTEGER, 20000),
                Field("GVER", STRING, "qa-only-day45"),
                Field("NTOP", INTEGER, 130),
                Field("PNET", SERVER.UNION, (0, Field("VALU", STRUCT, [
                    Field("MACI", INTEGER, 839678451),
                    Field("XDDR", BINARY, bytes([192, 168, 1, 25]) + bytes(32)),
                    Field("XUID", INTEGER, 2535469248587161),
                ]))),
            ]),
            self.state,
        )
        return by_label(decode_frame(answered[0]), "MSID").value

    def pushed(self) -> list[dict]:
        return [decode_frame(frame) for frame in self.channel.sent]

    def test_without_the_flag_a_search_still_honestly_times_out(self) -> None:
        """Off by default. A server that invented opponents on its own would
        be lying to whoever ran it."""
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        commands = [(f["component"], f["command"]) for f in self.pushed()]
        self.assertEqual(commands, [(4, 10)])

    def test_with_the_flag_the_search_finds_somebody(self) -> None:
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        commands = [(f["component"], f["command"]) for f in self.pushed()]
        self.assertEqual(
            commands,
            [(4, 20), (4, 71), (4, 30), (4, 100), (4, 21), (4, 30)],
        )

    def test_the_setup_reason_says_a_search_found_it(self) -> None:
        """Not the dataless CREATE_GAME context -- this game was matchmade,
        which is union index 3 and carries the session it belongs to."""
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        setup = self.pushed()[0]
        active, valu = by_label(setup, "REAS").value
        self.assertEqual(active, 3)
        members = {f.label: f.value for f in valu.value}
        self.assertEqual(members["MSID"], session)
        self.assertEqual(members["RSLT"], 0)
        self.assertNotIn("USID", members)

    def test_the_game_is_built_from_what_the_console_asked_for(self) -> None:
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        game = {f.label: f.value for f in by_label(self.pushed()[0], "GAME").value}
        # Still INITIALIZING in the setup itself; notification 100 moves it to
        # PRE_GAME a frame later.
        self.assertEqual(game["GSTA"], 1)
        addresses = game["HNET"][1]
        active, members = addresses[0]
        self.assertEqual(active, 0)
        self.assertEqual(
            SERVER.find_field(members, "XDDR").value[:4], bytes([192, 168, 1, 25])
        )

    def test_the_opponent_arrives_as_two_events(self) -> None:
        """One that it is happening and one that it is done, because that is
        how a client tracks a player."""
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        joining = next(f for f in self.pushed() if f["command"] == 21)
        player = {f.label: f.value for f in by_label(joining, "PDAT").value}
        self.assertEqual(player["NAME"], "Sparring")
        self.assertEqual(player["PID"], SERVER.SYNTHETIC_PERSONA)
        self.assertEqual(player["TIDX"], 1)  # the other team

    def test_the_journal_says_it_was_made_up(self) -> None:
        """A server that invented an opponent and did not say so would be a
        server nobody could trust the rest of."""
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.protocol.expire_matchmaking(self.state, session)
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        found = [e for e in events if e.get("event") == "matchmaking_found_synthetic_opponent"]
        self.assertEqual(len(found), 1)
        self.assertIs(found[0]["synthetic"], True)
        self.assertEqual(found[0]["opponent"], "Sparring")

    def test_the_roster_carries_the_session_id_the_client_knows_itself_by(self) -> None:
        """`UID` is mPlayerSessionId, and it is how the client recognises
        itself in a roster. Without it there is a game with no local player in
        it, and the setup is dropped without an error and without a word --
        which is what six delivered frames and a spinning console looked like.
        """
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        _, roster = by_label(self.pushed()[0], "PROS").value
        player = {f.label: f.value for f in roster[0]}
        self.assertEqual(player["UID"], 2535469248587161)
        self.assertEqual(player["UGID"], (0, 0, 0))
        # Fifteen of the eighteen: BLOB, PATT and ROLE are empty and left out,
        # which is what the client does with its own -- its 611-byte
        # createGame is thirty-four members less seven empty containers.
        self.assertEqual(len(player), 15)
        # And the opponent knows itself by its own session too.
        joining = next(f for f in self.pushed() if f["command"] == 21)
        other = {f.label: f.value for f in by_label(joining, "PDAT").value}
        self.assertEqual(other["UID"], SERVER.SYNTHETIC_PERSONA)

    def test_the_host_hands_over_its_xnet_session(self) -> None:
        """The frame that says Blaze is done and the network has begun.

        The console never sent one of these until the roster carried `UID`
        and it could find itself in there. It carries a sixteen-byte nonce and
        the XSESSION_INFO holding the session key -- neither of which is this
        server's to invent, and both of which a second console will need.
        """
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.protocol.expire_matchmaking(self.state, session)
        game_id = next(iter(self.protocol.games))
        nonce = bytes.fromhex("C783DB9DF0BE9BEBF3800000000E71CE")
        answered = self.protocol.handle(
            request(4, 15, [
                Field("GID", INTEGER, game_id),
                Field("XNNC", BINARY, nonce),
                Field("XSES", BINARY, b"\x28\x21\x24\xC0" + bytes(56)),
            ]),
            self.state,
        )
        self.assertEqual(self.protocol.games[game_id].xnet_nonce, nonce)
        self.assertEqual(len(self.protocol.games[game_id].xnet_session), 60)
        # Echoed straight back, because that is what the other side reads.
        updated = decode_frame(answered[1])
        self.assertEqual((updated["component"], updated["command"]), (4, 115))
        self.assertEqual(by_label(updated, "XNNC").value, nonce)

    def test_the_console_reports_the_mesh_truthfully(self) -> None:
        """Connected to itself, disconnected from the opponent that does not
        exist. The first thing in this whole exchange that is about the
        network rather than about the protocol."""
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.protocol.expire_matchmaking(self.state, session)
        for status, target in ((2, 2), (0, SERVER.SYNTHETIC_PERSONA)):
            self.protocol.handle(
                request(4, 29, [
                    Field("FLGS", INTEGER, 0),
                    Field("GID", INTEGER, 1),
                    Field("SCG", SERVER.OBJECT_ID, (0, 0, 0)),
                    Field("STAT", INTEGER, status),
                    Field("TCG", SERVER.OBJECT_ID, (30722, 2, target)),
                ]),
                self.state,
            )
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        mesh = [e for e in events if e.get("event") == "mesh_connection"]
        self.assertEqual([e["status"] for e in mesh], [2, 0])
        self.assertEqual(mesh[1]["target"][2], SERVER.SYNTHETIC_PERSONA)

    def test_neither_is_an_unknown_route_any_more(self) -> None:
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.protocol.expire_matchmaking(self.state, session)
        self.protocol.handle(request(4, 15, [Field("GID", INTEGER, 1)]), self.state)
        self.protocol.handle(request(4, 29, [Field("GID", INTEGER, 1)]), self.state)
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual([e for e in events if e.get("event") == "unknown_route"], [])

    def mesh(self, target: int, status: int) -> list:
        return self.protocol.handle(
            request(4, 29, [
                Field("FLGS", INTEGER, 0),
                Field("GID", INTEGER, 1),
                Field("SCG", SERVER.OBJECT_ID, (0, 0, 0)),
                Field("STAT", INTEGER, status),
                Field("TCG", SERVER.OBJECT_ID, (30722, 2, target)),
            ]),
            self.state,
        )

    def test_the_match_starts_once_everybody_can_see_everybody(self) -> None:
        """The server's one job in a peer-to-peer game: it is not in the data
        path, it only decides when the players have found each other."""
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.protocol.expire_matchmaking(self.state, session)
        answered = self.mesh(self.state.connection_id, 2)
        started = [decode_frame(f) for f in answered[1:]]
        self.assertEqual([(f["component"], f["command"]) for f in started], [(4, 100)])
        # 131 is IN_GAME. 130 was PRE_GAME, which is where the setup left it.
        self.assertEqual(by_label(started[0], "GSTA").value, 131)

    def test_a_peer_that_cannot_be_seen_holds_the_match_back(self) -> None:
        """Without the invented opponent counted as present, a DISCONNECTED
        peer is the truth and the game stays where it is."""
        session = self.search()          # no flag: no synthetic opponent
        self.protocol.expire_matchmaking(self.state, session)
        self.protocol.games[1] = SERVER.HostedGame(
            game_id=1, persona_id=self.state.xuid, gamertag="x",
            state=SERVER.GAME_STATE_PRE_GAME,
            roster=[2, SERVER.SYNTHETIC_PERSONA],
        )
        answered = self.mesh(2, 2)
        self.assertEqual(len(answered), 1)          # acknowledged, nothing more
        answered = self.mesh(SERVER.SYNTHETIC_PERSONA, 0)
        self.assertEqual(len(answered), 1)
        self.assertEqual(self.protocol.games[1].state, SERVER.GAME_STATE_PRE_GAME)

    def test_a_match_already_started_is_not_started_again(self) -> None:
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.protocol.expire_matchmaking(self.state, session)
        host = self.state.connection_id
        self.assertEqual(len(self.mesh(host, 2)), 2)
        self.assertEqual(len(self.mesh(host, 2)), 1)

    def test_a_game_with_nobody_to_play_never_starts(self) -> None:
        """A host connected to itself is not a match. "Everything reported is
        connected" was true of a one-player game, and started one."""
        self.protocol.games[1] = SERVER.HostedGame(
            game_id=1, persona_id=self.state.xuid, gamertag="x",
            state=SERVER.GAME_STATE_PRE_GAME,
            roster=[2],
        )
        self.assertEqual(len(self.mesh(2, 2)), 1)
        self.assertEqual(self.protocol.games[1].state, SERVER.GAME_STATE_PRE_GAME)

    def test_the_roster_holds_both_players_not_just_the_host(self) -> None:
        """What a second console will read to find out who it is playing.

        A peer-to-peer game needs exactly one thing from a server:
        introductions. The roster is the introduction.
        """
        os.environ["FIFA14_TEST_OPPONENT"] = "Sparring"
        session = self.search()
        self.channel.sent.clear()
        self.protocol.expire_matchmaking(self.state, session)
        _, roster = by_label(self.pushed()[0], "PROS").value
        players = [{f.label: f.value for f in entry} for entry in roster]
        self.assertEqual([p["NAME"] for p in players], ["Imskobogota6z", "Sparring"])
        self.assertEqual([p["TIDX"] for p in players], [0, 1])
        # Each carries the address the other has to dial.
        for player in players:
            active, valu = player["PNET"]
            self.assertEqual(active, 0)
            self.assertIsNotNone(SERVER.find_field(valu.value, "XDDR"))


class GameLifecycleTests(unittest.TestCase):
    """The commands a game sends once it exists.

    The host moves its own game on and tears it down at the end. Neither is
    the server's decision on a peer-to-peer game -- it records and relays.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.168.1.25", 1040), 10041)
        self.state.xuid = 2535469248587161
        self.state.authenticated = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blaze_tdf_union_lists import CREATE_GAME
        self.protocol.handle(CREATE_GAME, self.state)
        self.game_id = next(iter(self.protocol.games))

    def tearDown(self) -> None:
        os.environ.pop("FIFA14_TEST_OPPONENT", None)
        self.protocol.stop()
        self.temp.cleanup()

    def test_the_host_can_move_its_own_game_on(self) -> None:
        self.protocol.handle(
            request(4, 3, [
                Field("GID", INTEGER, self.game_id),
                Field("GSTA", INTEGER, 131),
            ]),
            self.state,
        )
        self.assertEqual(self.protocol.games[self.game_id].state, 131)

    def test_a_game_that_is_destroyed_stops_being_counted(self) -> None:
        """Otherwise "En cours de partie" only ever climbs."""
        self.protocol.handle(
            request(4, 2, [Field("GID", INTEGER, self.game_id)]), self.state
        )
        self.assertEqual(self.protocol.games, {})

    def test_neither_is_an_unknown_route(self) -> None:
        self.protocol.handle(request(4, 3, [Field("GID", INTEGER, 1)]), self.state)
        self.protocol.handle(request(4, 2, [Field("GID", INTEGER, 1)]), self.state)
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual([e for e in events if e.get("event") == "unknown_route"], [])


class TwoConsolesTests(unittest.TestCase):
    """Real matchmaking: two consoles looking at the same time.

    This needs nothing that has not already been proved on hardware. Both
    consoles send `startMatchmaking` carrying their own XNADDR, so the server
    has both addresses and its whole job is to put them in one game and hand
    each the other's.
    """

    class Channel:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

    def console(self, connection: int, xuid: int, name: str, ip: int):
        state = SERVER.ClientState(connection, ("192.168.1.%d" % ip, 1040), 10041)
        state.xuid = xuid
        state.gamertag = name
        state.authenticated = True
        state.channel = self.Channel()
        self.protocol.remember_connection(state)
        return state

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.one = self.console(1, 2535469248587161, "Imskobogota6z", 25)
        self.two = self.console(2, 2535424500563471, "Igordos7943", 26)

    def tearDown(self) -> None:
        self.protocol.stop()
        self.temp.cleanup()

    def search(self, state, version: str = "qa-only-day45", ip: int = 25):
        return self.protocol.handle(
            request(4, 13, [
                Field("DUR", INTEGER, 20000),
                Field("GVER", STRING, version),
                Field("NTOP", INTEGER, 130),
                Field("PNET", SERVER.UNION, (0, Field("VALU", STRUCT, [
                    Field("MACI", INTEGER, ip),
                    Field("XDDR", BINARY, bytes([192, 168, 1, ip]) + bytes(32)),
                    Field("XUID", INTEGER, state.xuid),
                ]))),
            ]),
            state,
        )

    def pushed(self, state):
        return [decode_frame(frame) for frame in state.channel.sent]

    def settle(self, timeout: float = 3.0) -> None:
        """Wait for the pairing, which no longer happens inside the handler.

        It is deferred by half a second so that the reply carrying the
        client's own matchmaking session id is written before it is told about
        a game -- two consoles were paired correctly on 22 August and both
        carried on searching, because the guest heard about the game first and
        had nothing to attach it to.
        """
        deadline = time.time() + timeout
        while time.time() < deadline and not self.protocol.games:
            time.sleep(0.02)
        # The game is registered before its notifications are pushed, so
        # waiting for the game alone races the frames. Wait for the pushing to
        # stop instead.
        sent = -1
        while time.time() < deadline:
            now = len(self.one.channel.sent) + len(self.two.channel.sent)
            if now and now == sent:
                return
            sent = now
            time.sleep(0.05)

    def test_the_second_console_to_look_finds_the_first(self) -> None:
        self.search(self.one, ip=25)
        self.settle(0.8)
        self.assertEqual(self.protocol.games, {})   # nobody to pair with yet
        self.search(self.two, ip=26)
        self.settle()
        self.assertEqual(len(self.protocol.games), 1)
        game = next(iter(self.protocol.games.values()))
        self.assertEqual(
            [member["gamertag"] for member in game.members],
            ["Imskobogota6z", "Igordos7943"],
        )

    def test_each_console_is_told_the_other_address(self) -> None:
        """The whole point. A peer-to-peer game needs introductions and
        nothing else from a server."""
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        setup = next(f for f in self.pushed(self.one) if f["command"] == 20)
        _, roster = by_label(setup, "PROS").value
        addresses = []
        for entry in roster:
            _, valu = SERVER.find_field(entry, "PNET").value
            addresses.append(SERVER.find_field(valu.value, "XDDR").value[:4])
        self.assertEqual(
            addresses, [bytes([192, 168, 1, 25]), bytes([192, 168, 1, 26])]
        )

    def test_the_one_who_waited_hosts_and_the_other_dials(self) -> None:
        """The host is the one whose XNet session the other will dial, so it
        has to be the one that was already there to be dialled into."""
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        first = [f["command"] for f in self.pushed(self.one)]
        second = [f["command"] for f in self.pushed(self.two)]
        # Both get notification 20. 22 was the family convention and the
        # journal refuted it: paired across the Atlantic, the host got 20 and
        # answered with its XNet session two seconds later, and the guest got
        # 22 and never said another word.
        self.assertIn(20, first)
        self.assertIn(20, second)
        self.assertNotIn(22, second)
        # What tells them apart is the setup reason, which is what actually
        # says who joined what.
        host_setup = next(f for f in self.pushed(self.one) if f["command"] == 20)
        guest_setup = next(f for f in self.pushed(self.two) if f["command"] == 20)
        _, host_valu = by_label(host_setup, "REAS").value
        _, guest_valu = by_label(guest_setup, "REAS").value
        self.assertEqual(SERVER.find_field(host_valu.value, "RSLT").value, 0)
        self.assertEqual(SERVER.find_field(guest_valu.value, "RSLT").value, 2)

    def test_two_builds_that_disagree_are_not_matched(self) -> None:
        """A mismatch would fail in the network layer for a reason that has
        nothing to do with the network."""
        self.search(self.one, version="qa-only-day45")
        self.search(self.two, version="some-other-build")
        self.settle(0.8)
        self.assertEqual(self.protocol.games, {})

    def test_one_player_on_two_connections_is_not_two_players(self) -> None:
        third = self.console(3, self.one.xuid, "Imskobogota6z", 25)
        self.search(self.one)
        self.search(third)
        self.settle(0.8)
        self.assertEqual(self.protocol.games, {})

    def test_the_pairing_is_written_down(self) -> None:
        self.search(self.one)
        self.search(self.two)
        self.settle()
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        paired = [e for e in events if e.get("event") == "matchmaking_paired"]
        self.assertEqual(len(paired), 1)
        self.assertEqual(paired[0]["host"], self.one.xuid)
        self.assertEqual(paired[0]["guest"], self.two.xuid)

    def test_a_console_can_join_a_game_that_already_exists(self) -> None:
        """The other way in. The joiner brings its own address and its own
        Xbox session, so nothing about it has to have been cached first."""
        self.search(self.one, ip=25)
        self.protocol.forget_matchmaking(self.one.connection_id)
        self.protocol.games[7] = SERVER.HostedGame(
            game_id=7, persona_id=self.one.xuid, gamertag=self.one.gamertag,
            state=SERVER.GAME_STATE_PRE_GAME,
            roster=[self.one.connection_id],
        )
        game = self.protocol.games[7]
        game.members = [self.protocol.member(
            game, self.one.xuid, self.one.gamertag, self.one.connection_id,
            None, slot=0, team=0, state=self.one,
        )]
        answered = self.protocol.handle(
            request(4, 9, [
                Field("GID", INTEGER, 7),
                Field("GVER", STRING, "qa-only-day45"),
                Field("JMET", INTEGER, 1),
                Field("PNET", SERVER.UNION, (0, Field("VALU", STRUCT, [
                    Field("XDDR", BINARY, bytes([192, 168, 1, 26]) + bytes(32)),
                    Field("XUID", INTEGER, self.two.xuid),
                ]))),
            ]),
            self.two,
        )
        reply = decode_frame(answered[0])
        self.assertEqual(by_label(reply, "GID").value, 7)
        # 0 is JOINED_GAME. Four members, not the two the published tables give.
        self.assertEqual(by_label(reply, "JGS").value, 0)
        self.assertEqual(
            sorted(f.label for f in reply["fields"]), ["GID", "JEX", "JGS", "REX"]
        )
        self.assertEqual([f["command"] for f in self.pushed(self.two)], [])
        self.assertEqual(
            [decode_frame(f)["command"] for f in answered[1:]], [22, 30]
        )
        # And the one already in there hears that somebody arrived.
        self.assertEqual([f["command"] for f in self.pushed(self.one)], [21, 30])
        self.assertEqual(len(self.protocol.games[7].members), 2)

    def test_joining_a_game_that_does_not_exist_is_refused_quietly(self) -> None:
        answered = self.protocol.handle(
            request(4, 9, [Field("GID", INTEGER, 999)]), self.two
        )
        self.assertEqual(len(answered), 1)
        self.assertEqual(decode_frame(answered[0])["fields"], [])

    def test_a_player_walking_out_takes_itself_off_the_roster(self) -> None:
        """Sent straight after "votre adversaire a quitté la partie" -- the
        console drawing the conclusion that its peer never answered.

        With two real players, the one who stays keeps the game.
        """
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        game_id = next(iter(self.protocol.games))
        self.protocol.handle(
            request(4, 22, [
                Field("GID", INTEGER, game_id),
                Field("PID", INTEGER, self.two.xuid),
                Field("REAS", INTEGER, 7),
            ]),
            self.two,
        )
        game = self.protocol.games[game_id]
        self.assertEqual([m["persona"] for m in game.members], [self.one.xuid])
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        left = [e for e in events if e.get("event") == "player_left"]
        self.assertEqual(len(left), 1)
        # The reason is recorded rather than interpreted: that enum is unread.
        self.assertEqual(left[0]["reason"], 7)

    def test_the_last_one_out_takes_the_game_with_them(self) -> None:
        """A game nobody is in is not a game -- and otherwise "En cours de
        partie" climbs by one every time somebody tries and gives up."""
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        game_id = next(iter(self.protocol.games))
        for who in (self.two, self.one):
            self.protocol.handle(
                request(4, 22, [
                    Field("GID", INTEGER, game_id),
                    Field("PID", INTEGER, who.xuid),
                ]),
                who,
            )
        self.assertEqual(self.protocol.games, {})

    def test_leaving_is_no_longer_an_unknown_route(self) -> None:
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        self.protocol.handle(request(4, 22, [Field("GID", INTEGER, 1)]), self.two)
        events = [
            json.loads(line)
            for line in (Path(self.temp.name) / "journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual([e for e in events if e.get("event") == "unknown_route"], [])

    def test_a_search_can_be_held_open_longer_than_the_client_asked(self) -> None:
        """Two friends on two consoles cannot press within the same twenty
        seconds of each other.

        The client's twenty seconds are a note to the matchmaker, not a
        deadline of its own -- it waits until it is told, which is exactly why
        the server can hold the door open. Nothing is faked: there is still no
        opponent until one arrives, and the failure is still announced if none
        does.
        """
        os.environ["FIFA14_SEARCH_WINDOW"] = "180"
        try:
            timer = self.protocol.schedule_matchmaking_timeout(self.one, 1, 20000)
            self.assertAlmostEqual(timer.interval, 180.0, places=3)
            timer.cancel()
        finally:
            os.environ.pop("FIFA14_SEARCH_WINDOW", None)

    def test_without_the_window_the_client_gets_what_it_asked_for(self) -> None:
        timer = self.protocol.schedule_matchmaking_timeout(self.one, 1, 20000)
        self.assertAlmostEqual(timer.interval, 20.0, places=3)
        timer.cancel()

    def test_the_second_console_is_paired_however_late_it_arrives(self) -> None:
        """The point of the window: whoever comes second finds the first
        still waiting rather than a search that has already been given up on."""
        os.environ["FIFA14_SEARCH_WINDOW"] = "180"
        try:
            self.search(self.one, ip=25)
            self.settle(0.8)
            self.assertEqual(self.protocol.games, {})
            self.search(self.two, ip=26)
            self.settle()
            self.assertEqual(len(self.protocol.games), 1)
        finally:
            os.environ.pop("FIFA14_SEARCH_WINDOW", None)

    def test_a_cancelled_search_leaves_the_pool(self) -> None:
        """Backing out to let the other player host has to actually work.

        A cancelled search stayed in the pool and could still be paired --
        which is how somebody who had deliberately stood down was made host
        anyway, a minute after standing down.
        """
        self.search(self.one, ip=25)
        self.protocol.handle(request(4, 14), self.one)
        self.assertEqual(self.protocol.searches, {})
        self.search(self.two, ip=26)
        self.settle(0.8)
        self.assertEqual(self.protocol.games, {})

    def test_the_host_key_reaches_the_guest(self) -> None:
        """XSES is the session key the guest's console dials the host with.

        It was sent back only to the host, which had built it. The guest sat
        through pairing after pairing saying nothing -- and the journal showed
        it leaving the game cleanly at the end, so it had registered the game
        perfectly well. It simply had no way to reach the other console.
        """
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        game_id = next(iter(self.protocol.games))
        host = self.one if self.protocol.games[game_id].persona_id == self.one.xuid else self.two
        guest = self.two if host is self.one else self.one
        guest.channel.sent.clear()
        nonce = bytes.fromhex("C9B3ECE69B85C5B4DC8000000006BBDD")
        self.protocol.handle(
            request(4, 15, [
                Field("GID", INTEGER, game_id),
                Field("XNNC", BINARY, nonce),
                Field("XSES", BINARY, b"\xAB" * 256),
            ]),
            host,
        )
        updated = [f for f in self.pushed(guest) if f["command"] == 115]
        self.assertEqual(len(updated), 1)
        self.assertEqual(by_label(updated[0], "XNNC").value, nonce)
        self.assertEqual(len(by_label(updated[0], "XSES").value), 256)

    def test_a_waiting_test_host_makes_the_real_console_the_guest(self) -> None:
        """The role that fails, put on hardware that can be watched.

        FIFA14_TEST_OPPONENT puts the invented player on the guest side, so
        the console always plays the role that works. This puts it on the host
        side instead, already waiting, so the console arrives second and
        receives exactly what the far-away console was receiving.
        """
        os.environ["FIFA14_TEST_HOST"] = "Sparring"
        try:
            self.search(self.one, ip=25)
            self.settle()
            self.assertEqual(len(self.protocol.games), 1)
            game = next(iter(self.protocol.games.values()))
            # The invented one hosts; the real console joined it.
            self.assertEqual(game.persona_id, SERVER.SYNTHETIC_PERSONA)
            self.assertEqual(
                [m["gamertag"] for m in game.members], ["Sparring", "Imskobogota6z"]
            )
            commands = [f["command"] for f in self.pushed(self.one)]
            # Everything a guest gets, including the session key -- which the
            # invented host cannot produce, so the server posts it for it.
            self.assertEqual(commands, [20, 71, 30, 100, 115])
        finally:
            os.environ.pop("FIFA14_TEST_HOST", None)

    def test_the_guest_is_told_it_is_still_connecting(self) -> None:
        """Its own roster entry has to say what is true of it."""
        os.environ["FIFA14_TEST_HOST"] = "Sparring"
        try:
            self.search(self.one, ip=25)
            self.settle()
            setup = next(f for f in self.pushed(self.one) if f["command"] == 20)
            _, roster = by_label(setup, "PROS").value
            players = {
                SERVER.find_field(e, "NAME").value:
                SERVER.find_field(e, "STAT").value for e in roster
            }
            self.assertEqual(players["Sparring"], 4)          # ACTIVE_CONNECTED
            self.assertEqual(players["Imskobogota6z"], 2)     # ACTIVE_CONNECTING
        finally:
            os.environ.pop("FIFA14_TEST_HOST", None)

    def test_without_the_flag_nobody_is_waiting(self) -> None:
        self.search(self.one, ip=25)
        self.settle(0.8)
        self.assertEqual(self.protocol.games, {})

    def test_each_console_is_sent_the_other_address_through_the_relay(self) -> None:
        """The lever: it is this server that hands each console the other's
        address, so it is this server that can point it somewhere reachable.

        Two consoles found each other on 22 August, entered the match, and
        never saw each other -- two home NATs, France and the United States,
        and no EA service left to help them through. Nobody can reconfigure
        either router.
        """
        os.environ["FIFA14_PEER_RELAY"] = "87.106.7.87:3074"
        try:
            self.search(self.one, ip=25)
            self.search(self.two, ip=26)
            self.settle()
            for viewer, mine, theirs in (
                (self.one, 25, 26), (self.two, 26, 25),
            ):
                setup = next(
                    f for f in self.pushed(viewer) if f["command"] in (20, 22)
                )
                _, roster = by_label(setup, "PROS").value
                addresses = {}
                for entry in roster:
                    name = SERVER.find_field(entry, "NAME").value
                    _, valu = SERVER.find_field(entry, "PNET").value
                    addresses[name] = SERVER.find_field(valu.value, "XDDR").value
                own = addresses[viewer.gamertag]
                other = addresses[
                    self.two.gamertag if viewer is self.one else self.one.gamertag
                ]
                # Its own address is left alone: it knows where it lives.
                self.assertEqual(own[:4], bytes([192, 168, 1, mine]))
                self.assertEqual(own[4:8], bytes(4))
                # The one it will dial points at the relay.
                self.assertEqual(other[:4], bytes([192, 168, 1, theirs]))
                self.assertEqual(other[4:8], bytes([87, 106, 7, 87]))
                self.assertEqual(other[8:10], (3074).to_bytes(2, "big"))
                # And everything the console built for itself is untouched --
                # the MAC, and the twenty bytes of abOnline nobody can read.
                self.assertEqual(len(other), 36)
                self.assertEqual(other[10:], bytes(26))
        finally:
            os.environ.pop("FIFA14_PEER_RELAY", None)

    def test_without_a_relay_the_addresses_are_handed_over_untouched(self) -> None:
        self.search(self.one, ip=25)
        self.search(self.two, ip=26)
        self.settle()
        setup = next(f for f in self.pushed(self.one) if f["command"] == 20)
        _, roster = by_label(setup, "PROS").value
        for entry in roster:
            _, valu = SERVER.find_field(entry, "PNET").value
            self.assertEqual(
                SERVER.find_field(valu.value, "XDDR").value[4:8], bytes(4)
            )

    def test_the_pairs_file_names_the_two_public_addresses(self) -> None:
        """The relay sees packets and a source address, nothing more. This
        server is what knows who was paired with whom, so it is what has to
        say so -- guessing from traffic alone works with two players and
        breaks at the third."""
        pairs = Path(self.temp.name) / "relay-pairs.json"
        os.environ["FIFA14_RELAY_PAIRS"] = str(pairs)
        try:
            self.search(self.one, ip=25)
            self.search(self.two, ip=26)
            self.settle()
            written = json.loads(pairs.read_text())
            self.assertEqual(
                [sorted(p) for p in written["pairs"]],
                [["192.168.1.25", "192.168.1.26"]],
            )
        finally:
            os.environ.pop("FIFA14_RELAY_PAIRS", None)

    def test_a_game_that_empties_leaves_no_pair_behind(self) -> None:
        """Otherwise the relay keeps forwarding one stranger's traffic to
        another long after the game is over."""
        pairs = Path(self.temp.name) / "relay-pairs.json"
        os.environ["FIFA14_RELAY_PAIRS"] = str(pairs)
        try:
            self.search(self.one, ip=25)
            self.search(self.two, ip=26)
            self.settle()
            game_id = next(iter(self.protocol.games))
            for who in (self.two, self.one):
                self.protocol.handle(
                    request(4, 22, [Field("GID", INTEGER, game_id)]), who
                )
            self.assertEqual(json.loads(pairs.read_text())["pairs"], [])
        finally:
            os.environ.pop("FIFA14_RELAY_PAIRS", None)


class AccountInfoPersona(unittest.TestCase):
    """`/user/accountinfo`, which the console asks for 142 times over.

    The empty persona list was right when it was written -- a populated one
    sends the login helper looking for a club that did not exist. The club
    exists now, so the flag states the true thing instead.
    """

    def tearDown(self) -> None:
        os.environ.pop("FIFA14_ACCOUNT_PERSONA", None)
        SERVER.CLUB_IDENTITY.name = ""
        SERVER.CLUB_IDENTITY.abbr = "FUT"

    def test_the_empty_list_is_still_reachable(self) -> None:
        # On by default now -- a launch carried the real persona end to end and
        # the login completed. `FIFA14_ACCOUNT_PERSONA=0` goes back, because
        # the failure mode this route can have is a login that never finishes
        # and one launch is one launch.
        os.environ["FIFA14_ACCOUNT_PERSONA"] = "0"
        document = SERVER.account_info_document(2305837508020095216, "Mosebee")
        self.assertEqual(document["userAccountInfo"]["personas"], [])
        self.assertFalse(document["userAccountInfo"]["returningUser"])

    def test_the_persona_is_the_console_gamertag_not_an_invented_one(self) -> None:
        # Blaze carries the gamertag in DSNM and PersistentAccountStore writes
        # it down; this route used to throw it away. Impulsum's build hardcodes
        # personaName to "FUT14" with personaId 1000 -- there is no reason to
        # invent a persona when the console has already said who it is.
        os.environ.pop("FIFA14_ACCOUNT_PERSONA", None)
        SERVER.CLUB_IDENTITY.name = "Mosebeest FC"
        SERVER.CLUB_IDENTITY.abbr = "MOS"
        persona = SERVER.account_info_document(
            2305837508020095216, "Mosebee"
        )["userAccountInfo"]["personas"][0]
        self.assertEqual(persona["personaId"], 2305837508020095216)
        self.assertEqual(persona["personaName"], "Mosebee")
        self.assertTrue(persona["isReturningUser"])

        club = persona["userClubList"][0]
        self.assertEqual(club["clubName"], "Mosebeest FC")
        self.assertEqual(club["clubAbbr"], "MOS")
        # The Xbox SKU, which is the one in CardsDLL's string table. Impulsum
        # sends FFA14PCC and FFA14PS3 beside it; this console is neither.
        self.assertEqual(club["skuAccessList"], {"FFA14XBX": 1})
        # No invented club id. Inventing ids is what drew NOT FOUND on every
        # club item until the four families were measured.
        self.assertNotIn("clubId", club)
        self.assertNotIn("teamId", club)

    def test_a_club_that_does_not_exist_yet_is_not_claimed(self) -> None:
        # An unestablished club sends an empty userClubList -- the same "no FUT
        # account yet" statement the empty persona list was making, one level
        # down and without lying about who the player is.
        os.environ.pop("FIFA14_ACCOUNT_PERSONA", None)
        SERVER.CLUB_IDENTITY.name = ""
        persona = SERVER.account_info_document(
            2305837508020095216, "Mosebee"
        )["userAccountInfo"]["personas"][0]
        self.assertEqual(persona["userClubList"], [])
        self.assertFalse(persona["isReturningUser"])
        self.assertEqual(persona["personaName"], "Mosebee")



class EasFcPowSurface(unittest.TestCase):
    """EA Sports Football Club, ported from the PS3 line on 28 August.

    That console has EASFC connected and this same handler answering it --
    1,178 `/pow/` requests across sixteen routes in its journal. The Xbox has
    never recorded one, and the reason was not that the module never starts:
    it starts, and reports `TXT_EASFC_SERVER_ERROR`.
    """

    def test_the_endpoint_keys_point_at_a_port_that_speaks_http(self) -> None:
        # The whole change, in one assertion.
        #
        # `POW_CUSTOMURL` and `FIFA_POW_URL` were `<advertise>:<core_port>` --
        # the Blaze port, where nothing answers HTTP. The PS3 line hit exactly
        # this and recorded that the request "leaves the console and is never
        # seen again", which is what a journal with zero `/pow/` requests looks
        # like from the server side.
        class Stub:
            advertise = "10.0.0.119"
            core_port = 10041
            identity_port = 18080

        endpoint = SERVER.Fifa14Protocol.pow_endpoint.fget(Stub())
        self.assertEqual(endpoint, "http://10.0.0.119:18080")
        # The scheme is the whole fix, and this assertion used to say the
        # opposite. Without it the module formats `10.0.0.119:18080/pow/auth`
        # and default.xex's ProtoHttp opens no socket at all -- no request, no
        # error, a retry window that doubles each attempt.
        self.assertTrue(endpoint.startswith("http://"))

        # And the way back, should this prove worse than what it replaced.
        from unittest import mock

        with mock.patch.dict(os.environ, {"FIFA14_POW_CORE_URL": "1"}):
            self.assertEqual(
                SERVER.Fifa14Protocol.pow_endpoint.fget(Stub()),
                "10.0.0.119:10041",
            )

    def test_the_pow_endpoint_keys_are_served(self) -> None:
        # Withholding them was tried on 28 August, to match Zamboni, which
        # serves none and has EASFC connected. It does not port: with the keys
        # unserved this console made no `/pow/` request at all -- not even the
        # handshake it had been making an hour before -- while the patched
        # module string beside it read the right URL the whole time.
        #
        # POW reads the config, and the string is not a fallback it reaches.
        # The PS3 can serve nothing because RPCS3 redirects the EASFC hostname
        # back to the host; nothing sits in front of the Xbox, so an unserved
        # key points at EA.
        class Stub:
            advertise = "10.0.0.119"
            core_port = 10041
            identity_port = 18080
            identity_base = property(
                lambda self: SERVER.Fifa14Protocol.identity_base.fget(self)
            )
            pow_endpoint = property(
                lambda self: SERVER.Fifa14Protocol.pow_endpoint.fget(self)
            )

        keys = dict(SERVER.Fifa14Protocol.pow_config_keys.fget(Stub()))
        self.assertEqual(len(keys), 6)
        # The two that decide where the handshake goes carry the scheme.
        for name in ("ONLINE/POW_CUSTOMURL", "FIFA_POW_URL"):
            self.assertEqual(keys[name], "http://10.0.0.119:18080", name)

        from unittest import mock

        with mock.patch.dict(os.environ, {"FIFA14_POW_URLS": "0"}):
            self.assertEqual(
                SERVER.Fifa14Protocol.pow_config_keys.fget(Stub()), []
            )

    def test_every_route_the_ps3_asks_for_is_answered(self) -> None:
        # The sixteen routes, by request count, out of the PS3's journal. A
        # console that asks 302 times is not idly curious.
        for path in (
            "/pow/chal/user/prog",
            "/pow/lvl/user/tiergp/businessunit/tiertp/fifa",
            "/pow/mm/game/fifa14/message/list",
            "/pow/bank/user/account",
            "/pow/pfyc/user",
            "/pow/v2/activity",
            "/pow/store/game/fifa14/catalog/list",
            "/pow/news/user",
            "/pow/communication/all",
            "/pow/lvl/weight/tiergp/businessunit/tiertp/fifa",
            "/pow/inventory/item/list",
            "/pow/bank/currency/pow_funds/cap/info",
            "/pow/pfyc/user/prefs/shareinfo",
            "/pow/news/opt/off",
            "/pow/pfyc/user/club",
        ):
            with self.subTest(path=path):
                # Parseable JSON, always. An unmodelled route answers `{}`
                # rather than 404: the distinction the PS3 established is
                # between "nothing here" and "no answer", and a datacache
                # treats them differently.
                json.loads(SERVER.pow_service_document(path))

    def test_the_catalogue_is_one_store_and_not_none(self) -> None:
        # Measured on the PS3, 2026-08-27: `{"catalogs":[]}` was answered and
        # the console re-asked every sixteen seconds. A cache filled with
        # nothing reads as a fetch that produced nothing.
        doc = json.loads(
            SERVER.pow_service_document("/pow/store/game/fifa14/catalog/list")
        )
        self.assertEqual(len(doc["catalogs"]), 1)
        # EA's own id, out of the module's canned response, rather than a
        # plausible number this server invented.
        self.assertEqual(doc["catalogs"][0]["catalogId"], 45635)

    def test_the_handshake_is_not_swallowed_by_the_new_surface(self) -> None:
        # `/pow/auth` is FUT authentication wearing the POW name, and this
        # console has always called it that. It normalises to `/ut/auth`
        # before anything tests for a `/pow/` prefix -- if that order ever
        # inverts, login breaks and EASFC gains nothing.
        self.assertEqual(SERVER.normalize_route("/pow/auth"), "/ut/auth")
        self.assertFalse(
            SERVER.normalize_route("/pow/auth").startswith(SERVER.POW_ROUTE_PREFIX)
        )
