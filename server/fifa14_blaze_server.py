#!/usr/bin/env python3
"""Minimal, observable Blaze 3 server for FIFA 14 on Xbox 360.

This is deliberately a protocol bootstrap rather than a complete FUT server.
It answers the title's redirector, Util, Xbox authentication, UserSessions and
early CardHouse requests while recording every frame as JSON Lines.  Unknown
requests are returned as empty successful replies by default so the next
client request can be discovered without fabricating persistent FUT state.

The request/response layouts are derived from the public BlazeSDK and
ZamboniUltimateTeam projects.  No game data or captured credentials are used.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import signal
import socket
import ssl
import sys
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
TOOLS = REPOSITORY / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from blaze_tdf import (  # noqa: E402
    BINARY,
    INTEGER,
    LIST,
    MAP,
    STRING,
    OBJECT_ID,
    STRUCT,
    UNION,
    VARIABLE,
    Decoder,
    Field,
    decode_frame,
    encode_fields,
    encode_frame,
    encode_tag,
    json_value,
)


REDIRECTOR = 5
UTIL = 9
AUTHENTICATION = 1
AUTHENTICATION2 = 35
USER_SESSIONS = 0x7802
# FIFA 14 has no CardHouse client component, and this is not an inference.
# Four checks agree: no Blaze component constructor for it anywhere in
# default.xex or in CardsDLLzf, no `CardHouse` or `gamerGetInfo` string in
# either module, no vtable, and -- the one that settles it -- **component 2148
# has never appeared in a single frame** across every journal this repo has
# ever written. Its only occurrences are inside the advertised CIDS list,
# which the client ignores anyway: it hammers 2076 by the hundred thousand and
# 2076 is not in that list either.
#
# So the CARDHOUSE_* commands below came from NHL's HUT by way of the Zamboni
# BlazeSDK dump, exactly as this module's own docstring says its layouts did.
# The handlers for them are dead code. They are left in place because removing
# them would prove nothing and cost a diff, but nobody should read them as
# knowledge about this game.
CARDHOUSE = 2148
SPONSORED_EVENTS = 0x081C
STATS = 7
# Advertised in COMPONENT_IDS since the beginning and never once used: until
# 21 August 2026 this server had not seen a single frame on component 4. Then
# a console was taken into Face-à-Face and sent `startMatchmaking`.
# The four-byte locale the console reports, as an integer: "frFR". It was
# written out as 1718765138 in three places before anything else needed it.
LOCALE = 1718765138

GAME_MANAGER = 4
CENSUS_DATA = 10
CLUBS = 11
MESSAGING = 15
ROOMS = 21
ASSOCIATION_LISTS = 25
OSDK_SETTINGS = 2249
OSDK_ONLINE_PASS = 2268
# Read out of the client's own component constructors. OSDKTournaments is
# implemented by the title and has never sent this server a frame: FUT
# tournaments go through the HTTP route at /ut/game/fifa14/tournament/user
# that this server already serves, and 2271 holds only the bracket. Not worth
# building until a console asks for it.
OSDK_TOURNAMENTS = 2271
FIFA_CUPS = 2069
COOP_SEASON = 2070
EASFC_COMPONENT = 2077
# The offline game report a match end submits, and the asynchronous result the
# post-match screen waits on before it will leave.
GAME_REPORTING = 28

GAME_REPORTING_SUBMIT_OFFLINE = 2
USER_SESSIONS_RESUME = 35
# The two lookups. Named from the payloads the title actually sends: command
# 12 carries a single UserIdentification (AID/ALOC/EXBB/EXID/ID/NAME/ORIG/PIDI)
# and command 13 carries LTYP plus a list of them. That is Blaze's lookupUser
# and lookupUsers, and the reply shape is the client's own vocabulary --
# NotifyUserAdded already pairs DATA with USER, so a looked-up user is that
# same pair.
USER_SESSIONS_LOOKUP_USER = 12
USER_SESSIONS_LOOKUP_USERS = 13
GAME_REPORTING_RESULT_NOTIFICATION = 114
REDIRECTOR_GET_SERVER_INSTANCE = 1
UTIL_FETCH_CONFIG = 1
UTIL_PING = 2
UTIL_SET_CLIENT_DATA = 3
UTIL_LOCALIZE_STRINGS = 4
UTIL_GET_TELEMETRY_SERVER = 5
UTIL_PREAUTH = 7
UTIL_POSTAUTH = 8
UTIL_USER_SETTINGS_LOAD = 10
UTIL_USER_SETTINGS_SAVE = 11
UTIL_USER_SETTINGS_LOAD_ALL = 12
UTIL_SET_CLIENT_METRICS = 22
UTIL_SET_CONNECTION_STATE = 23

AUTH_GET_ACCOUNT = 30
AUTH_HAS_ENTITLEMENT = 33
AUTH_LIST_ENTITLEMENTS = 32
AUTH_LIST_USER_ENTITLEMENTS_2 = 29
# Command 48 is command 32's payload with a PID and a fuller filter set, so
# the same list for one named persona. Command 39 carries a whole entitlement
# -- PJID 307354, TYPE 5, STAT 1 -- which is a grant. Both are acknowledged
# without inventing an entitlement: this server has none to give, and
# answering "granted" to a grant it did not perform is exactly the kind of
# fake progress that costs a week to unpick later.
AUTH_LIST_ENTITLEMENTS_FOR_PERSONA = 48
AUTH_GRANT_ENTITLEMENT = 39
AUTH_GET_TOS_INFO = 42
AUTH_LOGOUT = 70
AUTH_XBOX_LOGIN = 170
AUTH_UPDATE_ACCOUNT = 20

AUTH2_LOGIN = 10

USER_UPDATE_HARDWARE_FLAGS = 8
USER_UPDATE_NETWORK_INFO = 20

STATS_GET_STAT_GROUP_LIST = 3
STATS_GET_KEY_SCOPES_MAP = 15
# Leaderboards. Command 10 arrives as LBID plus NAME ("SkillGame41"), which is
# a request for one leaderboard's descriptor; command 13 arrives as CENT (the
# persona to centre on), COUN 100, BOTT, POFF, TIME -- a centred leaderboard
# page. Both are acknowledged and nothing more: the row and column structures
# are not known, and a guessed one reads no better than an empty reply while
# risking a mis-parse. Settling them needs a capture of a retail response.
STATS_GET_LEADERBOARD_GROUP = 10
STATS_GET_CENTERED_LEADERBOARD = 13
STATS_GET_PERIOD_IDS = 20

# Command ids read out of FIFA 14's own `getCommandName` jump table, so they
# are this build's, not another Blaze title's -- and FIFA 14's table does
# differ from the widely published Battlefield 3 one (it has no `listGames` at
# 17, and it adds `joinGameByUserList` at 30). The dozen that matter happen to
# agree, and 13 is confirmed twice over: by the table, and by the payload the
# console actually sent, which carries matchmaking criteria and a duration.
GAME_MANAGER_CREATE_GAME = 1
# The two the console started sending the moment it could find itself in the
# roster. 15 hands over the XNet session it just built; 29 reports, per peer,
# whether it can see them.
GAME_MANAGER_DESTROY_GAME = 2
GAME_MANAGER_JOIN_GAME = 9
GAME_MANAGER_REMOVE_PLAYER_CMD = 11
# Command 22 in the request direction, which is not notification 22. The
# console sends it after "votre adversaire a quitté la partie" -- it is how a
# player leaves, and it carries the reason it left in `REAS`.
GAME_MANAGER_LEAVE_GAME_BY_GROUP = 22
GAME_MANAGER_ADVANCE_GAME_STATE = 3
GAME_MANAGER_REMOVE_PLAYER = 11
GAME_MANAGER_FINALIZE_GAME_CREATION = 15
GAME_MANAGER_UPDATE_MESH_CONNECTION = 29
GAME_MANAGER_START_MATCHMAKING = 13
GAME_MANAGER_CANCEL_MATCHMAKING = 14

# Notification ids from the same binary, and the first of these is a trap
# worth naming. Blaze 2 called notification 10 `NotifyMatchmakingFinished` and
# carried both outcomes on it. In FIFA 14 it is `NotifyMatchmakingFailed` and
# carries only the failure path -- success arrives as `NotifyGameSetup` (20)
# instead. Sending 10 to announce a match would end the search, not start one.
NOTIFY_GAME_SETUP = 20
NOTIFY_PLATFORM_HOST_INITIALIZED = 71
NOTIFY_PLAYER_JOINING = 21
# Same payload class as notification 20 -- the 557-class index holds exactly
# one NotifyGameSetup. The difference is who it goes to: 20 to the player the
# game is being set up for, 22 to a player joining a game that already exists,
# so it starts dialling the mesh rather than waiting to be dialled. That
# routing is the family convention rather than something read out of the
# client's dispatch, so it is the first thing to swap if only one side
# connects.
NOTIFY_JOINING_PLAYER_INITIATE_CONNECTIONS = 22
NOTIFY_PLAYER_JOIN_COMPLETED = 30
NOTIFY_GAME_STATE_CHANGE = 100
NOTIFY_GAME_SESSION_UPDATED = 115
NOTIFY_MATCHMAKING_FAILED = 10
NOTIFY_MATCHMAKING_ASYNC_STATUS = 12

# Blaze's MatchmakingResult. Only the two ends of it are needed here.
# Blaze's GameState, read from the binary's enum pool. The two that matter are
# 130 and 131, not the 3 and 4 a reader would guess from the others.
GAME_STATE_INITIALIZING = 1
GAME_STATE_PRE_GAME = 130
GAME_STATE_IN_GAME = 131

# PlayerState. A player who is in the game and reachable is 4, not 2.
PLAYER_STATE_ACTIVE_CONNECTING = 2
PLAYER_STATE_ACTIVE_CONNECTED = 4

# PlayerNetConnectionStatus, as reported by updateMeshConnection.
MESH_DISCONNECTED = 0
MESH_ESTABLISHING = 1
MESH_CONNECTED = 2

# `GameSetupReason` is a union, and `CREATE_GAME_SETUP_CONTEXT` is not one of
# its indices -- it is a value of the `DCTX` enum inside index 0's
# DatalessSetupContext. A game the client asked for itself is therefore
# union index 0 carrying {DCTX: 0}; a matchmade one would be index 3.
SETUP_REASON_DATALESS = 0
SETUP_REASON_MATCHMAKING = 3

# The made-up opponent's nucleus id. Far from any real one.
SYNTHETIC_PERSONA = 1_000_002
SETUP_CONTEXT_CREATE_GAME = 0

MATCHMAKING_SUCCESS_CREATED_GAME = 0
MATCHMAKING_SUCCESS_JOINED_NEW_GAME = 1
MATCHMAKING_SUCCESS_JOINED_EXISTING_GAME = 2

# JoinGameState. The client asked to join and it did.
JOIN_STATE_JOINED_GAME = 0
MATCHMAKING_SESSION_TIMED_OUT = 3
MATCHMAKING_SESSION_CANCELED = 4

# Census. The two counters at the top of the Face-à-Face screen -- "Joueurs en
# ligne" and "En cours de partie" -- read zero because this server answered the
# subscription with a fieldless success and then never pushed anything. The
# names below are the title's own compiled-in member names, not a reading of
# what a field might mean.
NOTIFY_SERVER_CENSUS_DATA = 1
GAME_MANAGER_CENSUS_TDF_ID = 0x21239231

CENSUS_SUBSCRIBE = 1
CENSUS_UNSUBSCRIBE = 2

CLUBS_GET_INVITATIONS = 1600
CLUBS_GET_COMPONENT_SETTINGS = 2600

MESSAGING_FETCH_MESSAGES = 2
MESSAGING_GET_MESSAGES = 5

ROOMS_SELECT_VIEW_UPDATES = 10
# VWID -- the same shape as SELECT_VIEW_UPDATES one command earlier, so a
# category subscription rather than a view one.
ROOMS_SELECT_CATEGORY_UPDATES = 11
# A single ENBL flag. Which switch it is, is not established; what is
# established is that the title sends it on its way to the main menu and
# carries on regardless of the answer.
ROOMS_SET_ENABLED = 150

ASSOCIATION_GET_LISTS = 6

OSDK_SETTINGS_FETCH_SETTINGS = 1
OSDK_SETTINGS_FETCH_GROUPS = 2
OSDK_ONLINE_PASS_FETCH_GATES = 3

CARDHOUSE_LOGIN = 101
CARDHOUSE_LOGOUT = 102
CARDHOUSE_GAMER_SET_INFO = 103
CARDHOUSE_GAMER_GET_INFO = 104
CARDHOUSE_GET_CONFIG = 106
CARDHOUSE_GET_DECK_INFO = 301
CARDHOUSE_GET_SQUAD_LIST = 709

# The FIFA 14 client stub for command 3 constructs a
# Blaze::SponsoredEvents::URLResponse.  Its sole TDF member is the string URL.
SPONSORED_EVENTS_GET_EVENTS_URL = 3

REPLY = 1
NOTIFICATION = 2
ERROR_REPLY = 3

# Minimal configuration accepted by the retail Xbox 360 FutCfg parser.
#
# The schema and the required non-zero fields were recovered directly from
# default.xex (0x827F68B8 -> 0x827F07B0 -> 0x827EBFA8).  In particular:
#
# * cfgVersion populates FutCfg +0x140;
# * minorVersion populates +0x11C;
# * the matching revision/Language dimeUniqueId populates +0x120;
# * key/dimeUniqueId populates +0x148.
#
# All four values are checked before the native async completion is allowed to
# report success.  bootString names the title's own FUT server-call frontend;
# it does not skip the subsequent CardHouse/Blaze session.
FUT_BOOT_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<FutCfg>
  <cfgVersion>1</cfgVersion>
  <futDlc>
    <fut12>
      <minorVersion>1</minorVersion>
      <bootString>fut12</bootString>
      <futNotAvailable>0</futNotAvailable>
      <revision>
        <futSubVersion>1</futSubVersion>
        <Language>
          <dimeUniqueId>1</dimeUniqueId>
          <size>1</size>
        </Language>
      </revision>
      <key>
        <dimeUniqueId>2</dimeUniqueId>
        <futKeyType>0</futKeyType>
      </key>
    </fut12>
  </futDlc>
</FutCfg>
"""

# Full Blaze value 0x00010864: error ordinal 1 in component 0x0864.
CARDHOUSE_ERR_NO_PLAYER_INFO_HEADER = 1

# Stock FIFA 14 PreAuth information observed from the supported Xbox build.
COMPONENT_IDS = [
    1,
    4,
    6,
    7,
    9,
    10,
    11,
    15,
    21,
    20,
    25,
    27,
    28,
    AUTHENTICATION2,
    2000,
    CARDHOUSE,
    OSDK_SETTINGS,
    OSDK_ONLINE_PASS,
    30720,
    30721,
    30722,
    30723,
    30725,
    30726,
]


@dataclass
class ClientState:
    connection_id: int
    peer: tuple[str, int]
    local_port: int
    gamertag: str = "OfflineFUT"
    # The four-byte locale the client presents in PreAuth, e.g. "frFR".  The
    # EASW gate compares its own locale against a downloaded allow-list before
    # it will even build its authentication request, so echoing back exactly
    # what this console reported is what opens that gate.
    locale: str = ""
    xuid: int = 1
    email: str = "offline@localhost"
    authenticated: bool = False
    request_count: int = 0
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    # The socket this connection is on, so the server can say something the
    # client did not ask for. Every frame until now was a reply, written by
    # the loop that had just read a request -- which is fine for a protocol
    # that only ever answers, and not fine for matchmaking, where the whole
    # point is telling a client something later.
    channel: Any = None

    def push(self, frame: bytes) -> bool:
        """Send an unsolicited frame. False if the connection is gone."""
        channel = self.channel
        if channel is None:
            return False
        with self.send_lock:
            try:
                channel.sendall(frame)
                return True
            except OSError:
                return False


@dataclass
class HostedGame:
    """A game this server has agreed exists.

    Most of it is the client's own `createGame` echoed back rather than
    invented. That is not laziness: `ReplicatedGameData` is about 0x2e0 bytes
    and only fourteen of its members could be read out of the title's own
    reflection tables, so anything this server makes up is a guess where
    anything it repeats is a fact. The host's address in particular has to
    come back byte for byte -- it is what a second console will dial.
    """

    game_id: int
    persona_id: int
    gamertag: str
    # The connection the host is on, so the server can tell it about anybody
    # who joins later.
    host_state: Any = None
    # Who is in this game, by connection group, and which of them have said
    # they can see the mesh. Both are needed: "everything reported is
    # connected" is true of a game with one player in it and nobody to play,
    # which started a match against nobody the first time it was written.
    roster: list = field(default_factory=list)
    mesh: dict = field(default_factory=dict)
    # Everybody in the game, in join order, and the connection each is on so
    # the server can tell one player about another. A peer-to-peer game needs
    # exactly this and nothing more from a server: introductions.
    members: list = field(default_factory=list)
    # Handed over by the host in finalizeGameCreation once its console has
    # built the session. This is the blob a second console needs to dial in.
    xnet_nonce: bytes = b""
    xnet_session: bytes = b""
    state: int = GAME_STATE_INITIALIZING
    presence: int = 1
    voip: int = 2
    max_capacity: int = 2
    queue_capacity: int = 0
    teams: Field | None = None
    name: str = ""
    game_type: str = ""
    status_url: str = ""
    protocol_version: str = ""
    topology: int = 0
    settings: int = 0
    mod_register: int = 0
    attributes: Field | None = None
    criteria: Field | None = None
    capacity: Field | None = None
    host_addresses: Field | None = None
    host_address: Any = None
    connection_group: int = 0


class PersistentAccountStore:
    """Small local persistence layer for client-owned account preferences."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            # FIFA writes this exact value after completing its first-login UI.
            "user_settings": {"FirstTimeFlag": "0"},
            "account": {"OPTQ": 0, "OPTS": 0},
            "identity": {"persona_id": 1_000_001, "persona_name": "OfflineFUT"},
        }
        if path is not None and path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for section in ("user_settings", "account", "identity"):
                    value = loaded.get(section)
                    if isinstance(value, dict):
                        self.data[section].update(value)

    def _save_locked(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load_setting(self, key: str) -> str:
        with self.lock:
            return str(self.data["user_settings"].get(key, ""))

    def load_all_settings(self) -> list[tuple[str, str]]:
        with self.lock:
            return sorted(
                (str(key), str(value))
                for key, value in self.data["user_settings"].items()
            )

    def save_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.data["user_settings"][key] = value
            self._save_locked()

    def save_account_preferences(self, optq: int, opts: int) -> None:
        with self.lock:
            self.data["account"].update({"OPTQ": optq, "OPTS": opts})
            self._save_locked()

    def save_identity(self, persona_id: int, persona_name: str) -> None:
        with self.lock:
            self.data["identity"].update(
                {"persona_id": int(persona_id), "persona_name": str(persona_name)}
            )
            self._save_locked()

    def load_identity(self) -> tuple[int, str]:
        with self.lock:
            identity = self.data["identity"]
            return int(identity["persona_id"]), str(identity["persona_name"])

    def reset(self) -> None:
        """Back to the state a freshly started server has.

        `tools/fut.sh` gets this by clearing `runtime/local-account.json` and
        restarting the server, and it has to be got somehow: the title rewrites
        this state from its in-memory session within seconds, so re-entering
        FUT without a relaunch cannot work. Neither clearing a file nor
        restarting a process is available to someone whose server is a VPS
        across the network, which is what `POST /revival/reset` is for.

        Still one store for the whole server, unlike the club state beside it
        -- so on a shared server this resets everyone's first-login flag, not
        just the caller's. Harmless in the moment it is used (a player
        relaunching their own title), and it has to become per-tenant before an
        open beta; `docs/DEPLOY.md` says so where an operator will read it.
        """
        with self.lock:
            self.data = {
                "user_settings": {"FirstTimeFlag": "0"},
                "account": {"OPTQ": 0, "OPTS": 0},
                "identity": {"persona_id": 1_000_001, "persona_name": "OfflineFUT"},
            }
            self._save_locked()


class AccountStores:
    """One account store per persona, opened the first time it is asked for.

    The club state went per-tenant on 14 August; this did not, and on 20 August
    it showed. A second player logged into the public server and
    `runtime/local-account.json` came back carrying *his* gamertag -- one file,
    last writer wins, for everybody on the machine. Nothing visible broke,
    because the club is what holds the cards and the coins, but the first-login
    flag and the identity were shared: either player relaunching reset the
    other, and `/revival/reset` reset them both.

    Same convention as `club_save_path`: persona 0 keeps the historical file,
    so a single console that never identifies itself -- and the whole test
    suite -- behaves exactly as before. A real nucleus id gets its own file in
    `accounts/` beside the clubs.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.default_path = path
        self._lock = threading.RLock()
        self._stores: dict[int, PersistentAccountStore] = {}

    def path_for(self, persona_id: int) -> Path | None:
        if not persona_id or self.default_path is None:
            return self.default_path
        return self.default_path.parent / "accounts" / f"{int(persona_id)}.json"

    def get(self, persona_id: int = 0) -> PersistentAccountStore:
        key = int(persona_id or 0)
        with self._lock:
            store = self._stores.get(key)
            if store is None:
                store = PersistentAccountStore(self.path_for(key))
                self._stores[key] = store
            return store


# CardsDLL formats its authentication request against OSDK_EASW_AUTH_URL.
# The PC build posts JSON to ``/v2/authenticationNucleusPersona``; this Xbox
# build posts a form to ``/authentication360`` with a ``version`` query and
# its own EASW-* signature headers.  Accept both.
EASW_AUTH_PATHS = ("/authentication360", "/v2/authenticationNucleusPersona")

# The FUT HTTP surface, keyed by exact path.  Every body here is the response
# the corresponding FIFA 14 parser treats as "nothing yet": empty collections
# and absent optional members, so the client keeps its own zeroed defaults
# instead of being handed a fabricated club, inventory, currency or squad.  The
# paths and the parser analysis behind them come from a working local
# implementation of this same title.
# `GET /ut/game/fifa14/trade/<tradeId>/offer`, the bids on one of your own
# listings. The console asks for it by trade id, so it cannot be a fixed route.
TRADE_OFFER_PATH = re.compile(r"^/ut/game/fifa14/trade/(\d+)/offer$")

FUT_ROUTES: dict[str, bytes] = {
    "/ut/delete/auth": b"{}",
    # The header calls the currency "FIFA coins", and CardsDLL's JSON member
    # table carries `coins` alongside GetCoinsBalance and RefreshUserCredit --
    # so the member is very likely `coins`, not `credits`.
    #
    # Both are sent, at the top level. The earlier attempt wrapped them in
    # {"userInfo":{...}} and the header read -842150451, which is 0xCDCDCDCD:
    # the parser did not recognise the shape, never wrote the field, and the
    # header printed uninitialised memory. An unknown *wrapper* breaks the
    # parse; an unknown sibling member at the top level is simply skipped.
    "/ut/game/fifa14/user": b'{"coins":50000,"credits":50000}',
    "/ut/game/fifa14/userdata": b"{}",
    # A club with no coins cannot buy a pack or bid on anything, so the store
    # and market screens render but do nothing. Give the founding club a
    # working balance.
    # The parser reads a currencies array, not a "credits" number. Sending the
    # latter left the balance at whatever the response constructor held, which
    # is what showed up in the club header as a negative figure.
    # Same two spellings here, for the same reason.
    "/ut/game/fifa14/user/credits": (
        b'{"coins":50000,"credits":50000,'
        b'"currencies":[{"name":"COINS","funds":50000,"finalFunds":50000},'
        b'{"name":"POINTS","funds":0,"finalFunds":0}],'
        b'"unopenedPacks":{"preOrderPacks":0,"recoveredPacks":0}}'
    ),
    "/ut/game/fifa14/user/historical": b"{}",
    "/ut/game/fifa14/match": b"{}",
    "/ut/game/fifa14/match/ready": b"{}",
    "/ut/game/fifa14/match/end": b"{}",
    "/ut/game/fifa14/clientdata/tutorialpopups": b"{}",
    "/ut/game/fifa14/clientdata/userHubData": b"{}",
    # Trade pile, watch list and club capacity.
    #
    # Retail reads "TRANSFER LIST 0/30" on the item screen -- capacity 30, and
    # `maximumTradePileSize` in `/settings` says 30 as well -- where this
    # serves 20000. Left alone deliberately: a club already holding more than
    # thirty on its list would be over a limit it has never been under, and
    # that is a decision about the save rather than a correction to a document.
    #
    # Keys 1, 5, 6 and 7 were added here on the theory that the CLUB tab was
    # greyed out for want of a capacity. That theory is dead: retail greys the
    # same tab and still prints "CLUB 352" beside it, so greying is normal and
    # the tab wants a **count**, which now travels on `/club` as `total`.
    "/ut/game/fifa14/clientdata/pileSize": (
        b'{"entries":[{"key":2,"value":20000},{"key":3,"value":20000},'
        b'{"key":4,"value":20000}]}'
    ),
    "/ut/game/fifa14/clientdata/totw": b"{}",
    "/ut/game/fifa14/clientdata/managerquest": b'{"entries":[]}',
    "/ut/game/fifa14/eventfeed": b"{}",
    # The My Club tile reads clubPlayers from here; an empty object is why it
    # showed zero cards while the club held them.
    "/ut/game/fifa14/hub": b'{"auctionCount":0,"clubPlayers":92}',
    "/ut/game/fifa14/leaderboards/options": b"{}",
    "/ut/game/fifa14/utStats": b"{}",
    # FutGetClubUsersServerResponse reads a `user` array, singular, whose
    # entries carry persona/personaId/public. `users` matched nothing.
    "/ut/game/fifa14/clubUser": (
        b'{"user":[{"persona":"Fondateur FUT","personaId":0,"public":false}]}'
    ),
    # The club-creation screen PUTs the chosen name here and treats a 404 as a
    # connection failure -- "une erreur s'est produite lors de la connexion a
    # FIFA 14 Ultimate Team".  The PC revival never saw this route because the
    # PC client posts its club through clubUser instead; this one is the Xbox
    # client's own.  An empty object acknowledges the rename without inventing
    # club, crest, kit or inventory state.
    "/ut/game/fifa14/user/club": b"{}",
    "/ut/game/fifa14/club/stats/staff": b'{"bonus":[]}',
    "/ut/game/fifa14/club/stats/consumables": b'{"entries":[]}',
    "/ut/game/fifa14/club/stats/newcards": b'{"entries":[]}',
    "/ut/game/fifa14/item": b'{"itemData":[]}',
    "/ut/delete/game/fifa14/item": b"{}",
    # The market parser reads all three members; omitting the count and the
    # duplicate list leaves two of them at whatever the constructor held.
    # Polled right after a market search to refresh the state of the bids you
    # have out. A 404 here raises an error popup over a search that otherwise
    # worked. Nothing is bid on yet, so the list is empty.
    "/ut/game/fifa14/trade/status": (
        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}'
    ),
    "/ut/game/fifa14/tradePile": (
        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}'
    ),
    "/ut/game/fifa14/watchlist": (
        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}'
    ),
    # Acknowledges the squad the client saves at the end of club creation; the
    # PC revival answers with the same id it was asked to store.
    "/ut/game/fifa14/squad/1": b'{"id":1}',
    # Entering Saison Joueur Solo asks for this list; a 404 surfaces as "un
    # probleme de communication est survenu avec les serveurs FIFA Ultimate
    # Team".  The PC revival carries the same empty-seasons shape.
    "/ut/game/fifa14/season/list": b'{"seasons":[]}',
    # The season screen asks for the user's own season state straight after the
    # list.  An empty object leaves the native response at its constructor
    # defaults -- no division, no points, no record invented.
    "/ut/game/fifa14/season/user": b"{}",
    # tournament/list, tournament and tournament/user/list are served live
    # from the catalogue and the saved runs; see the mode table below.
    # A visible-but-invalid single entry keeps the store screen constructible
    # without offering anything purchasable.
    "/ut/game/fifa14/store": b'{"purchase":[],"timestamp":2147483647}',
    # One real, buyable gold pack rather than a deliberately invalid entry:
    # the store screen now has something to sell, which is what a club with a
    # balance is for. Same record the PC revival serves.
    "/ut/game/fifa14/store/purchasegroup/all": (
        b'{"purchase":[{"id":304,"assetId":3,"actionType":"CREATEPACK",'
        b'"packType":"CARDPACK","description":"FUT_STORE_PACK_304_DESC",'
        b'"displayGroup":{"priority":3,"value":"gold"},"displayGroupAssetId":3,'
        b'"displayGroupUseDefaultImage":true,"useDefaultImage":true,'
        b'"isPremium":true,"dealType":"REGULAR","saleType":"NONE",'
        b'"state":"active","visible":1,"sortPriority":1,'
        b'"currencies":[{"name":"COINS","funds":7500,"finalFunds":7500}]}],'
        b'"timestamp":2147483647}'
    ),
    "/ut/v2/game/fifa14/store/transaction": b'{"state":"NOTRANSACTION"}',
    # Asked for once, on 11 August, and answered 404. What it carries is not
    # known -- the name suggests several users at once, and this server has
    # exactly one. An empty object is the answer every other unknown FUT route
    # here gets, and it is a better one than a 404: nothing has ever been
    # observed to need a member of it.
    "/ut/game/fifa14/usermassinfo": b"{}",
}

# Routes answered by their own handler rather than from the table above, listed
# here only so the spelling map below covers them too.
HANDLED_ROUTES = (
    "/ut/game/fifa14/auctionhouse",
    "/ut/game/fifa14/club",
    "/ut/game/fifa14/club/consumables",
    "/ut/game/fifa14/clubUser",
    "/ut/game/fifa14/item",
    "/ut/game/fifa14/phishing",
    "/ut/game/fifa14/phishing/question",
    "/ut/game/fifa14/phishing/trusteddevice",
    "/ut/game/fifa14/phishing/validate",
    "/ut/game/fifa14/settings",
    "/ut/game/fifa14/trade",
    "/ut/game/fifa14/user/accountinfo",
    "/ut/game/fifa14/user/action",
    "/ut/game/fifa14/match/end",
    "/ut/game/fifa14/match/reset",
    "/ut/game/fifa14/purchased/items",
    "/ut/game/fifa14/season/list",
    "/ut/game/fifa14/season/user",
    "/ut/game/fifa14/season/user/history",
    "/ut/game/fifa14/squad",
    "/ut/game/fifa14/squad/active",
    "/ut/game/fifa14/squad/list",
    "/ut/game/fifa14/store/purchasegroup/all",
    "/ut/game/fifa14/tournament",
    "/ut/game/fifa14/totw",
    "/ut/game/fifa14/tournament/list",
    "/ut/game/fifa14/tournament/teams",
    "/ut/game/fifa14/tournament/user/list",
    "/ut/game/fifa14/trade/status",
    "/ut/game/fifa14/tradePile",
    "/ut/game/fifa14/transfermarket",
    "/ut/game/fifa14/user/club",
    "/ut/game/fifa14/user/list",
    "/ut/game/fifa14/watchlist",
)

# Lower case to the spelling this server actually registered.
#
# The client camel-cases some of these paths and this server spells them
# however they were first written down. They agreed on `tradePile`, `clubUser`
# and `userHubData` by luck. They did not agree on `watchList`: the client asks
# for it with a capital L, this server registered `watchlist`, and every time
# the watch list was opened it got a 404. Nothing reported it -- a 404 on a FUT
# route just leaves a screen empty, and an empty watch list looks like an empty
# watch list.
#
# `tradePile` and `tradepile` are both registered and both reach the same
# handler, so the collision costs nothing; every other route is distinct in
# lower case, and the only variable segments are numeric ids.
FUT_ROUTE_SPELLINGS: dict[str, str] = {}
for _route in (*FUT_ROUTES, *HANDLED_ROUTES):
    FUT_ROUTE_SPELLINGS.setdefault(_route.lower(), _route)
del _route

EASW_AUTH_PATH = EASW_AUTH_PATHS[0]
ICEBREAKER_PACK_LIST = Path(__file__).resolve().parent / "icebreakerpacklist.json"

# What a quick sell pays when the request does not say which card went. The
# real discardValue travels on the item; this is the floor.
SELL_PRICE_FALLBACK = 200


# The Xbox SKU, and it is in CardsDLL's own string table -- `FFA14XBX`, with
# no `FFA14PCC` or `FFA14PS3` beside it. Impulsum's PC build sends all three;
# this one sends the platform it is running on.
ACCOUNT_SKU = "FFA14XBX"


def account_info_document(persona_id: int, persona_name: str) -> dict:
    """`/ut/game/fifa14/user/accountinfo`, which the console asks 142 times over.

    An empty persona list is what this served from the beginning, and the
    reasoning was sound when it was written:

        A populated list tells the client it already owns a FUT account, so the
        login helper goes looking for that account's club, squad and identity
        -- none of which exist here -- and waits on a completion that never
        arrives.

    None of which exist *here* was the operative clause, and it stopped being
    true. The club holds 1,079 cards, a squad, a 13-0-1 record and a season in
    progress. Telling the client it has no FUT account is now the false half.

    `FIFA14_ACCOUNT_PERSONA=1` states the true one. **Off by default**: the
    empty list is what the login demonstrably walks today, and the failure mode
    the comment describes is a login that never completes.

        FIFA14_ACCOUNT_PERSONA=1 tools/fut.sh

    Two reasons to want it, and they are separate.

    **The gamertag.** This server already knows it -- Blaze carries it in
    `DSNM` and `PersistentAccountStore` writes it down, "Mosebee" against
    persona 2305837508020095216 -- and then this route throws it away and sends
    an empty list. Impulsum's build hardcodes `personaName` to "FUT14" with
    `personaId` 1000; there is no reason to invent a persona when the console
    has already said who it is.

    **EAS FC.** Impulsum's own comment on this route says the persona here
    must match the one authenticated over Blaze "or EASFC can't associate the
    session and shows 'unable to connect'". That is worth trying and it is not
    a fix on its own: `docs/EASFC_NOT_CONNECTED.md` records zero frames on 8094
    and 8080 and zero `/pow/` requests in every journal this project has, so
    the module is not failing to associate -- it is not arriving. Matching the
    persona may well be necessary. It cannot be sufficient.

    The club entry only appears once the club is established, which is what
    Impulsum does too: an unestablished club sends an empty `userClubList`,
    which is the same "no FUT account yet" statement the empty persona list was
    making, one level down and without lying about the persona.
    """
    # On by default from 25 August, after a launch carried it end to end: the
    # login ran to completion and the club loaded. That was the risk worth
    # flagging for -- what the empty list avoided was a login that never
    # finishes -- and one launch has now shown it does finish.
    #
    # It did NOT clear the EAS FC banner, which was the expected result and is
    # recorded in docs/EASFC_NOT_CONNECTED.md: nothing has ever asked this
    # server anything on EAS FC's behalf, so a persona it never reads cannot
    # have been the reason. What it does is stop misrepresenting who the player
    # is.
    #
    # `FIFA14_ACCOUNT_PERSONA=0` goes back to the empty list.
    if os.environ.get("FIFA14_ACCOUNT_PERSONA", "1").strip().lower() in {
        "0", "false", "no"
    }:
        return {"userAccountInfo": {"personas": [], "returningUser": False}}

    club_name = str(CLUB_IDENTITY.name or "").strip()
    club_abbr = str(CLUB_IDENTITY.abbr or "").strip()
    established = bool(club_name)
    persona = {
        "personaId": int(persona_id or 0),
        "personaName": persona_name or "",
        # `isReturningUser` is in CardsDLL (`returningUser` is not). It is the
        # flag `fcc_login1` reads to send `createClub` rather than
        # `iceBreaker` -- see `Wallet.user_info`, which reads the same bit off
        # the user record at +0x8D.
        "isReturningUser": established,
        "returningUser": established,
        "trial": False,
        "userState": "",
        "userClubList": [],
    }
    if established:
        # No `teamId` and no `clubId`. Impulsum sends both from a stored
        # `Club.TeamId`; this server has never had one, and inventing an id is
        # the mistake that drew NOT FOUND on every club item until the four
        # families were measured. The club is identified here by the name and
        # abbreviation the player chose, which are real, and the rest of the
        # login already identifies it by persona.
        #
        # If the launch shows the client wanting a club id, it gets a measured
        # one rather than a plausible one.
        persona["userClubList"].append(
            {
                "year": 2014,
                "clubName": club_name,
                "clubAbbr": club_abbr,
                "platform": "xbox360",
                "seasonId": 1,
                "status": 1,
                "established": 2013,
                # The same divisions `Wallet.user_info` already sends, so the
                # two documents cannot disagree about the club.
                "divisionOnline": 10,
                "divisionOffline": 10,
                "skuAccessList": {ACCOUNT_SKU: 1},
            }
        )
    return {
        "userAccountInfo": {
            "personas": [persona],
            "returningUser": established,
        }
    }


def with_balance(payload: bytes, coins: int) -> bytes:
    """Add the coin total to a response that is known to carry one.

    Do not call this on every FUT route. Adding the total to all of them froze
    the login: the fan-out stopped dead at clientdata/tutorialpopups and went no
    further. So an unrecognised sibling is *not* universally skipped -- some of
    these parsers reject an object carrying members they do not know, and the
    login step waiting on that response never completes.

    Use it only where a balance genuinely belongs: the user and credits
    responses, quick sell, market searches, trade state, and pack purchases.

    Original note, still true of those responses:

    The club header is refreshed from whichever response last carried a
    balance, and these response constructors zero their fields before parsing.
    So a reply that omits the total does not leave the header alone -- it sets
    it to zero. That is why the balance read 0 from the moment FUT started
    while the server held 50000: some response in the login fan-out was
    zeroing it, and finding which one by elimination would have cost a relaunch
    per candidate.

    Naming the total three ways is deliberate. `totalCredits` is the member in
    CardsDLL's own JSON table; `credits` and `coins` ride along because an
    unrecognised sibling at the top level is skipped. A wrapper is not -- when
    these were nested under `userInfo` the header printed 0xCDCDCDCD.
    """
    if not payload.startswith(b"{"):
        return payload
    try:
        document = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(document, dict):
        return payload
    document.setdefault("credits", coins)
    document.setdefault("totalCredits", coins)
    document.setdefault("coins", coins)
    return json.dumps(document, separators=(",", ":")).encode()

# The club's cards. Built once at import from the icebreaker packs this build
# ships, so every screen that asks about the club sees the same inventory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fut_inventory import (  # noqa: E402
    GOLD_PACK_ID,
    TOURNAMENT_NAMES,
    activate_item,
    CLUB_RECORD,
    club_year_response,
    ConsumableRefused,
    CLIENT_DATA,
    CLUB_IDENTITY,
    SAVE_FILE,
    TENANTS,
    TOURNAMENT_PROGRESS,
    TenantView,
    current_tenant,
    use_tenant,
    empty_big_archive,
    trophy_item_response,
    active_tournaments_response,
    SEASON_PROGRESS,
    season_history_response,
    season_user_response,
    seasons_response,
    tournament_teams_response,
    club_stats_response,
    consumable_stats_response,
    club_user_response,
    consumables_response,
    apply_match_items,
    PERSONA,
    match_result,
    match_reward,
    hub_response,
    totw_hub_squad,
    totw_club_info,
    TOTW_PERSONA_ID,
    totw_challenge_entries,
    totw_challenge_response,
    store_catalogue,
    store_pack_descriptions,
    totw_index_with_squad,
    totw_response,
    tournaments_response,
)

# These used to be the one club this server held. They are now views onto
# whichever club the request in hand belongs to -- see `Tenant` and
# `TenantView` in fut_inventory. Every call site below is unchanged, and a
# thread that never identifies itself gets the default club, which is exactly
# the single-club behaviour these names had before.
#
# Opening a club -- loading its save, or seeding it from the icebreaker packs
# on a first run -- moved into `Tenant._open` with its reasoning intact. It
# happens the first time a persona is seen rather than at import.
CLUB_INVENTORY = TenantView("inventory")
CARD_CATALOGUE = TenantView("catalogue")
WALLET = TenantView("wallet")
PACK_SHOP = TenantView("shop")
CARD_ACTIONS = TenantView("actions")
# Applying a contract, a fitness card or a training card. Until this existed
# the club could hold consumables and show them, and nothing could be done
# with one.
CONSUMABLE_RACK = TenantView("rack")
# Entering FUT needs a relaunch, so without this every session started from the
# icebreaker packs again: the club counter back to 92, the pack you opened
# gone, the coins reset.
MANAGER_TASKS = TenantView("tasks")
CLUB_SAVE = TenantView("save")


def club_name() -> str:
    """Whatever the player named his club, or nothing until he has."""
    return CLUB_IDENTITY.name

EASW_TOKEN = "LOCAL-FIFA14-EASW-TOKEN"
EASW_SESSION = "LOCAL-FIFA14-EASW-SESSION"

REQUEST_BODY_PREVIEW_LIMIT = 4096


def request_body_preview(body: bytes) -> str | None:
    """Return a bounded, journal-safe rendering of a client request body.

    The retail Xbox client posts small JSON documents whose exact schema is
    the evidence needed to model a response.  Binary or oversized bodies are
    summarised instead of being decoded so the journal stays readable.
    """
    if not body:
        return None
    truncated = body[:REQUEST_BODY_PREVIEW_LIMIT]
    try:
        text = truncated.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(body)} non-utf8 bytes> {truncated[:64].hex().upper()}"
    if len(body) > len(truncated):
        return text + f"...<truncated, {len(body)} bytes total>"
    return text


def auth_request_identity(body: bytes) -> tuple[int, str] | None:
    """Return the persona the FUT auth request itself presents, when present.

    The retail Xbox client posts its own Nucleus id and profile display name
    to ``pow/auth``.  Answering ``accountinfo`` with a different persona name
    makes the client describe an account it never asked about, so prefer the
    identity carried by the request over any stored placeholder.
    """
    if not body:
        return None
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    nucleus = document.get("nuc")
    display_name = document.get("nucleusPersonaDisplayName")
    if not isinstance(nucleus, int) or isinstance(nucleus, bool) or nucleus <= 0:
        return None
    if not isinstance(display_name, str) or not display_name:
        return None
    persona = document.get("nucleusPersonaId")
    # A zero persona id means the client has no FUT persona yet and expects
    # the server to name one.  Keep it tied to the Nucleus id in that case.
    if isinstance(persona, int) and not isinstance(persona, bool) and persona > 0:
        return persona, display_name
    return nucleus, display_name


# -- which club a request belongs to ---------------------------------------
#
# The obvious candidate is the nucleus id header, and it is the wrong one. On
# a full session into Saison Joueur Solo it appeared on **one** request out of
# forty-nine; `X-UT-SID` appeared on forty-six. The session id is the only
# thing the client puts on requests generally, which is exactly what it is for.
#
# It was one constant for everybody -- `LOCAL-XBOX360-FIFA14-SID` -- so every
# request after the auth was anonymous. Minting it per persona turns the
# client's own echo into the routing key, with no session table to keep.
#
# Derived rather than stored on purpose: `tools/fut.sh` restarts this server
# on every single launch, and a stored table would strand a client still
# holding the session id from a minute ago. A derived one still resolves.

UT_SID_BASE = "LOCAL-XBOX360-FIFA14-SID"
NUCLEUS_HEADER = "Easw-Session-Data-Nucleus-Id"

# Requests that may name their club without a session, because they are asked
# before one exists. `accountinfo` is the whole list: the console sends it with
# the nucleus header a minute and a half before it ever posts `/ut/auth`.
UNAUTHENTICATED_ROUTES = ("/ut/game/fifa14/user/accountinfo",)


def normalize_route(path: str) -> str:
    """The spelling this server routes on, for a raw request path.

    The Xbox client omits the leading `/ut` on Cards operations and calls
    Authentication `pow/auth` where the PC one says `ut/auth`. The handler
    below has always folded those; this pulls the fold out so that deciding
    *who a request is* can happen before the routing, which is where it has to
    happen.
    """
    normalized = path
    if normalized.startswith("/fut/ut/"):
        normalized = normalized[4:]
    if normalized == "/pow/auth":
        normalized = "/ut/auth"
    elif normalized.startswith("/game/fifa14/"):
        normalized = "/ut" + normalized
    return normalized


# EA Sports Football Club, ported from the PS3 line on 28 August.
#
# That console reaches this surface and this server answers it: 1,178 `/pow/`
# requests across sixteen routes in `runtime/blaze-server.jsonl`, with EASFC
# connected. None of it is speculative -- the documents below were shaped
# against a real console re-asking real routes, and the comments that say so
# came across with the code.
#
# The Xbox has never asked for any of it. `docs/EASFC_NOT_CONNECTED.md` counts
# zero, which was read for months as "the module never starts". It does start:
# it reports a server error. Serving the surface is half the answer; the other
# half is the endpoint keys in `fetch_config`, which were sending POW at the
# Blaze core port.

# Every path `powdllzf` builds, read off the dumped module
# (`work/powdllzf.bin`). Not all of them are asked
# for; the three the console asked for on 2026-08-27, in the first session
# where `/pow/auth` succeeded, are marked.
#
#   pow/auth                                        the handshake, handled above
#   pow/v2/activity                                 ASKED
#   pow/lvl/weight/tiergp/businessunit/tiertp/fifa  ASKED
#   pow/bank/user/account                           ASKED
#   pow/lvl/user/tiergp/businessunit/tiertp/fifa
#   pow/bank/currency/pow_funds/cap/info
#   pow/chal/user/prog          pow/communication/all
#   pow/communication/type/alert
#   pow/gamechange/gamechangetype
#   pow/inventory/item          pow/inventory/item/list
#   pow/lb/game/%s/type/...     pow/message
#   pow/mm/game/fifa14/message/list
#   pow/news/opt   pow/news/opt/%s   pow/news/user
#   pow/nucleus/entitlements
#   pow/pfyc/...                pow/store/...
#   pow/user/friends
POW_ROUTE_PREFIX = "/pow/"


def pow_service_document(path: str, query: str = "") -> bytes:
    """The EA Sports Football Club reply for `path`.

    Three sources, and they are not equally good. The comments below say which
    each line came from, because the difference decides what to do when one of
    these turns out wrong.

    **The module's own literals.** `powdllzf` ships canned fake-server
    responses -- `POW::FIFA::FakeServerResponseActivity` and its neighbours --
    and they are complete documents, not fragments:

        {"catalogs":[{"catalogId": 45635,"name":"fifa13 store"},...]}
        {"currencies":[{"currency":"pow_funds","funds":%d,
                        "fundsCapInfo:[{period:"daily","fundsEarned":%d},...]}]}
        { "level":2,"leveledUp":false,"xp":435,"xpGained":230,"xpLoyalty":2,
          "challengesDone":2,"xpCapCurrLevel":400,"xpCapNextLevel":485,
          "funds":[{"currencyName":...,"fundsBalance":2550,"fundsEarned":250}],
          "notifications":[...] }

    That is measurement. The root key and the member spellings are the
    module's. (Its `fundsCapInfo` literal is missing a closing quote -- a typo
    in EA's own debug string, repaired here rather than reproduced.)

    **The member table.** For routes with no literal, the root key is taken
    from `powdllzf`'s JSON dictionary -- `items`, `gifts`, `chals`, `news`,
    `messageList`, `personaList`, `tierweights`, `endOfList`. The dictionary is
    one flat alphabetical list shared by every parser in the module, so it
    proves the word exists and not which document holds it. Inference.

    **Nothing.** Everything else gets `{}` and is journalled until a console
    says otherwise.

    Empty collections throughout. This club has no Football Club store
    history, no gifts and no challenges, and saying so is different from
    failing to answer -- which is the whole point. `/pow/store/.../catalog/list`
    was answered `{}` on 2026-08-27 and the console re-asked it every sixteen
    seconds forever: a datacache retrying a fetch it could not parse. A
    well-formed list with no entries is a fetch that succeeded.
    """
    # -- the module's own literals ------------------------------------------
    if path.endswith("/catalog/list"):
        # One catalogue, not none. `{"catalogs":[]}` was tried on 2026-08-27
        # and the console re-asked this route every sixteen seconds anyway --
        # so the document parsed, and `CatalogListCacheData` treats a cache it
        # filled with nothing as a fetch that produced nothing and goes round
        # again. An empty list is the right answer to "what is in the store"
        # and the wrong answer to "which stores are there".
        #
        # 45635 is EA's own id, out of the canned response in the module. An
        # id this server invented would be a plausible-looking number with
        # nothing behind it, which is the mistake that drew NOT FOUND on every
        # club item until the four families were measured. This one is at
        # least the id the shipping module's test server used.
        return b'{"catalogs":[{"catalogId":45635,"name":"FIFA 14"}]}'
    if path.startswith("/pow/store/catalog/"):
        # The items in one catalogue, asked for by that id. Empty is honest:
        # this club has bought nothing from EA Sports Football Club, and there
        # is nothing to sell it. `POW_CATALOG_ITEM_REQUEST_SIZE` is the module's
        # own paging constant, so `endOfList` belongs here.
        return b'{"items":[],"endOfList":true}'
    if path == "/pow/bank/user/account":
        return (
            b'{"currencies":[{"currency":"pow_funds","funds":0,'
            b'"fundsBalance":0,"fundsEarned":0,'
            b'"fundsCapInfo":[{"period":"daily","fundsEarned":0},'
            b'{"period":"weekly","fundsEarned":0}]}]}'
        )
    if path == "/pow/bank/currency/pow_funds/cap/info":
        return (
            b'{"fundsCapInfo":[{"period":"daily","fundsCap":0,"fundsEarned":0},'
            b'{"period":"weekly","fundsCap":0,"fundsEarned":0}]}'
        )
    if path == "/pow/lvl/user/tiergp/businessunit/tiertp/fifa":
        # `self=true` asks for this console's own level; the POST form carries
        # a `nucIds` list and wants one entry per id, which is what
        # `personaList` is for. An empty club starts at level 1 with no XP.
        if "self=true" in query:
            return (
                b'{"level":1,"leveledUp":false,"xp":0,"xpGained":0,'
                b'"xpLoyalty":0,"challengesDone":0,'
                b'"xpCapCurrLevel":0,"xpCapNextLevel":100,'
                b'"funds":[{"currencyName":"pow_funds","fundsBalance":0,'
                b'"fundsEarned":0}],"notifications":[]}'
            )
        return b'{"personaList":[]}'
    # -- root keys from the member table, contents empty ---------------------
    if path == "/pow/lvl/weight/tiergp/businessunit/tiertp/fifa":
        return b'{"tierweights":[]}'
    if path.startswith("/pow/inventory/item"):
        return b'{"items":[],"endOfList":true}'
    if path.startswith("/pow/store/gift"):
        return b'{"gifts":[],"endOfList":true}'
    if path == "/pow/news/user":
        return b'{"news":[],"endOfList":true}'
    if path.startswith("/pow/communication/"):
        return b'{"notifications":[],"endOfList":true}'
    if path == "/pow/mm/game/fifa14/message/list":
        return b'{"messageList":[],"endOfList":true}'
    if path == "/pow/chal/user/prog":
        return b'{"chals":[],"endOfList":true}'
    if path == "/pow/user/friends":
        return b'{"users":[],"endOfList":true}'
    if path == "/pow/v2/activity":
        return b'{"completedActivity":[]}'
    # -- unmodelled ----------------------------------------------------------
    return b"{}"



class SessionStore:
    """Which club a FUT session id belongs to.

    The session id used to be `LOCAL-XBOX360-FIFA14-SID-<xuid>`, derived from
    the persona it named. On a LAN that is fine and nobody can reach the server
    anyway. Publicly it means the credential *is* the user id: a Xbox XUID is
    not a secret, so anyone who has one takes that club, sells its cards and
    empties its wallet. An open beta cannot ship that.

    So the id is random and the mapping is kept here. It is written to disk
    beside the club saves because `tools/fut.sh` restarts this server on every
    single launch, and an in-memory table would log every console out each
    time -- which is exactly the objection that made the derived id attractive
    in the first place.

    One token per persona: a fresh `/ut/auth` replaces the previous one. The
    client always uses the newest, and a table that only grows is a table that
    eventually has to be pruned.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (SAVE_FILE.parent / "sessions.json")
        self._lock = threading.RLock()
        self._by_token: dict[str, int] = {}
        self._by_persona: dict[int, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        for token, persona in saved.items():
            try:
                self._by_token[str(token)] = int(persona)
                self._by_persona[int(persona)] = str(token)
            except (TypeError, ValueError):
                continue

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._by_token, separators=(",", ":"))
            )
        except OSError:
            pass

    def existing(self, persona_id: int) -> str | None:
        """The session id this persona already holds, without minting one.

        `issue` replaces the token it finds, which is right for a login and
        wrong for anything that merely needs to name the session. EA Sports
        Football Club authenticates on its own -- `POST /pow/auth`, from
        powdllzf rather than CardsDLL -- and answering that by rotating the
        FUT session invalidates the `X-UT-SID` the client is still using for
        every Ultimate Team request.
        """
        with self._lock:
            return self._by_persona.get(int(persona_id))

    def issue(self, persona_id: int) -> str:
        """A new session id for this persona, replacing any it already had."""
        with self._lock:
            previous = self._by_persona.get(int(persona_id))
            if previous:
                self._by_token.pop(previous, None)
            token = f"{UT_SID_BASE}-{secrets.token_urlsafe(24)}"
            self._by_token[token] = int(persona_id)
            self._by_persona[int(persona_id)] = token
            self._save()
            return token

    def persona(self, token: str | None) -> int:
        if not token:
            return 0
        with self._lock:
            return self._by_token.get(token, 0)

    def forget(self, persona_id: int) -> None:
        with self._lock:
            token = self._by_persona.pop(int(persona_id), None)
            if token:
                self._by_token.pop(token, None)
            self._save()


SESSIONS = SessionStore()


def request_persona(headers, body: bytes, path: str = "") -> int:
    """The nucleus id this request belongs to, or 0 if it cannot prove one.

    The session id is the only thing trusted for a request that changes
    anything. The nucleus header is not: it is the user id in plain sight, so
    honouring it would put back exactly the hole the random token closes.

    Two exceptions, and both are bootstrap rather than trust:

      * `/ut/auth` names its persona in its own body. It is the request that
        establishes the session, so it has nothing else to offer.
      * `accountinfo` is asked before `/ut/auth` -- ninety seconds before, on
        the console this was built against -- and only reads. It is allowed the
        nucleus header, and nothing else is.
    """
    token = headers.get("X-UT-SID") if headers is not None else None
    persona = SESSIONS.persona(token)
    if persona:
        return persona
    if path in UNAUTHENTICATED_ROUTES and headers is not None:
        raw = headers.get(NUCLEUS_HEADER)
        try:
            if raw and int(raw) > 0:
                return int(raw)
        except (TypeError, ValueError):
            pass
    presented = auth_request_identity(body)
    return int(presented[0]) if presented else 0


def bind_request_club(headers, body: bytes, path: str = ""):
    """Point this thread at the club this request proves, and return it.

    A request that proves nobody -- the redirector, a resource fetch, a forged
    header -- gets the default club. That club holds no player's cards on a
    server anyone can reach, so the failure mode of an unproven request is
    seeing nothing rather than seeing somebody else's.
    """
    club = TENANTS.get(request_persona(headers, body, path))
    use_tenant(club)
    return club


def find_field(fields: list[Field], label: str) -> Field | None:
    for item in fields:
        if item.label == label:
            return item
        value = item.value
        if item.type == STRUCT and isinstance(value, list):
            nested = find_field(value, label)
            if nested is not None:
                return nested
        if item.type == UNION and isinstance(value, tuple):
            nested_field = value[1]
            if isinstance(nested_field, Field):
                if nested_field.label == label:
                    return nested_field
                if nested_field.type == STRUCT and isinstance(nested_field.value, list):
                    nested = find_field(nested_field.value, label)
                    if nested is not None:
                        return nested
    return None


def normal_header_size(header: bytes) -> int:
    """Return the ProtoFire header size for non-jumbo packets.

    The Xbox requests observed so far use the 12-byte header.  Context-bearing
    frames are accepted as well.  Jumbo payloads are rejected explicitly by
    the stream parser because they have not appeared in this title flow.
    """

    options = header[9] >> 4
    if options & 0x1:
        raise ValueError("Jumbo ProtoFire frames are not supported yet")
    size = 12
    if options & 0x2:
        size += 8 if options & 0x8 else 4
    return size


def response_frame(
    request: bytes,
    payload: bytes = b"",
    *,
    error: int = 0,
    message_type: int = REPLY,
) -> bytes:
    decoded = decode_frame(request[:12] + request[normal_header_size(request):])
    result = bytearray(
        encode_frame(
            decoded["component"],
            decoded["command"],
            error,
            message_type,
            decoded["message_number"],
            payload,
        )
    )
    # Preserve the request's local-user index.
    result[8] |= request[8] & 0x0F
    return bytes(result)


def search_window() -> float:
    """Combien de temps garder une recherche ouverte, en secondes.

    Le client demande vingt secondes dans `DUR`, et ces vingt secondes sont
    une consigne au matchmaker, pas un délai de son côté : la console du 21
    août a attendu des minutes sans rien dire tant que le serveur ne lui
    disait rien. C'est ce qui rend ceci possible.

    Deux amis sur deux consoles ne peuvent pas appuyer dans la même fenêtre de
    vingt secondes. Avec `FIFA14_SEARCH_WINDOW=180` la recherche du premier
    reste ouverte trois minutes, et le second est apparié dès qu'il arrive --
    sans que rien ne mente : il n'y a toujours pas d'adversaire tant qu'il n'y
    en a pas, et l'échec finit par être annoncé s'il n'en vient aucun.

    Zéro ou rien du tout : la durée que le client a demandée.
    """
    raw = os.environ.get("FIFA14_SEARCH_WINDOW", "").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def relay_pairs_path() -> Path | None:
    """Où écrire qui joue contre qui, pour le relais.

    Le relais voit des paquets et une adresse source, rien de plus. C'est ce
    serveur qui sait qui a été apparié avec qui, donc c'est à lui de le dire.
    Deviner à partir du seul trafic marcherait à deux joueurs et casserait au
    troisième.
    """
    raw = os.environ.get("FIFA14_RELAY_PAIRS", "").strip()
    return Path(raw) if raw else None


def peer_relay() -> tuple[str, int] | None:
    """Où faire pointer l'adresse des adversaires, ou rien.

    `FIFA14_PEER_RELAY=87.106.7.87:3074` réécrit, dans le XNADDR que chaque
    console reçoit pour l'autre, l'adresse publique et le port -- de sorte que
    le trafic de match parte vers cette adresse-là au lieu du domicile de
    l'autre joueur.

    C'est le montage classique d'un relais : le trafic pair-à-pair est chiffré
    de bout en bout entre les deux consoles, avec des clés qui voyagent dans
    `XSES`, donc un intermédiaire n'a rien à déchiffrer -- il transporte des
    paquets opaques et croise les flux.

    Ce qui n'est pas établi, c'est si le noyau de la console honore cette
    réécriture. Un XNADDR porte aussi vingt octets d'`abOnline`, la structure
    d'adresse que remplit la passerelle Xbox LIVE, et personne n'a documenté
    si l'association de sécurité s'en sert pour router. D'où la sonde avant le
    relais : réécrire, écouter, et voir s'il arrive quoi que ce soit.
    """
    raw = os.environ.get("FIFA14_PEER_RELAY", "").strip()
    if not raw:
        return None
    host, _, port = raw.partition(":")
    try:
        return host, int(port or 3074)
    except ValueError:
        return None


def relayed_address(address: bytes, relay: tuple[str, int]) -> bytes:
    """Un XNADDR dont l'adresse publique et le port mènent au relais.

    Les 36 octets sont : ina (4), inaOnline (4), wPortOnline (2), abEnet (6),
    abOnline (20). Seuls les deux du milieu changent. Le reste -- la MAC, et
    surtout les vingt octets d'abOnline -- est laissé tel quel : c'est de la
    matière que la console a fabriquée et qu'on ne sait pas lire.
    """
    if len(address) < 10:
        return address
    host, port = relay
    try:
        packed = bytes(int(part) for part in host.split("."))
    except ValueError:
        return address
    if len(packed) != 4:
        return address
    return address[:4] + packed + port.to_bytes(2, "big") + address[10:]


def mirror_test_host_address() -> bool:
    """Faut-il donner à l'hôte inventé une adresse authentique ?

    Le premier essai a donné un résultat net et biaisé : l'hôte inventé
    portait un XNADDR fabriqué, avec ses vingt octets d'`abOnline` à zéro, et
    la console n'a envoyé aucun paquet -- pas même un. C'est exactement ce
    qu'on attend d'un noyau qui rejette une adresse malformée avant d'ouvrir
    une socket. Ça ne dit rien de la question posée.

    Avec `FIFA14_TEST_HOST_ADDRESS=mirror`, l'hôte inventé emprunte le XNADDR
    de la console qui arrive : `abOnline` authentique, celui que la passerelle
    Xbox LIVE a rempli, et seule l'adresse publique change ensuite par le
    relais. Une seule variable bouge alors.

    C'est une approximation, et il faut le dire : ce matériel-là appartient à
    un compte machine, et le réutiliser sous un autre XUID peut être refusé
    pour cette raison-là plutôt que pour l'adresse. Mais c'est le seul vrai
    `abOnline` disponible, et un résultat avec une variable de moins vaut
    mieux qu'un résultat avec deux.
    """
    return os.environ.get("FIFA14_TEST_HOST_ADDRESS", "").strip().lower() == "mirror"


def mirror_invented_address() -> bool:
    """Should an invented player borrow a real XNADDR, whichever side it is on?

    `FIFA14_TEST_HOST_ADDRESS=mirror` was written for the invented *host*, and
    the invented *guest* needs the same thing for the same reason. The guest is
    what `FIFA14_TEST_OPPONENT` produces, and its address is fabricated with
    twenty zero bytes of `abOnline` -- the shape that made a console send
    nothing at all and told us nothing about why.

    `FIFA14_TEST_PEER_ADDRESS=mirror` is the name that does not claim a side.
    The older one keeps working, and now covers both.
    """
    for name in ("FIFA14_TEST_PEER_ADDRESS", "FIFA14_TEST_HOST_ADDRESS"):
        if os.environ.get(name, "").strip().lower() == "mirror":
            return True
    return False


def mirrored_address(real: bytes) -> bytes:
    """Le XNADDR d'une console, porté par quelqu'un d'autre.

    `abOnline` est recopié tel quel -- c'est tout l'intérêt. La MAC, elle,
    est modifiée : deux machines qui annoncent la même sur un même réseau,
    c'est une confusion qu'on peut s'éviter. Le bit d'administration locale
    est levé, ce qui donne une adresse qui ne peut appartenir à aucun
    matériel vendu.
    """
    if len(real) < 36:
        return real
    mac = bytearray(real[10:16])
    mac[0] |= 0x02
    mac[5] ^= 0x01
    return real[:10] + bytes(mac) + real[16:]


def test_host() -> str:
    """Le nom d'un hôte que ce serveur fait attendre dans la file, ou rien.

    `FIFA14_TEST_OPPONENT` met l'adversaire inventé du côté *invité* : la
    console cherche, personne ne vient, et c'est elle qui héberge. Elle joue
    donc toujours le rôle qui fonctionne.

    Le rôle qui ne fonctionne pas est l'autre. Le 22 août, deux consoles ont
    été appariées cinq fois de suite : celle qui hébergeait construisait sa
    session XNet en deux secondes, celle qui rejoignait recevait la partie,
    l'hôte, l'état, la clé -- et ne répondait rien. Elle comprenait tout : à
    00 h 54 elle a quitté la partie proprement. Elle n'avait simplement rien à
    faire de ce qu'on lui donnait.

    Impossible de regarder à l'intérieur de cette console-là : elle est à six
    mille kilomètres et sans débogueur. `FIFA14_TEST_HOST=Sparring` met donc
    l'inventé du côté *hôte*, en le faisant attendre dans la file avant que
    la vraie console n'arrive. Elle arrive seconde, devient invitée, et reçoit
    exactement ce que l'autre recevait -- sur du matériel qu'on peut ouvrir.

    Ça ne donnera jamais un vrai match : personne ne répond à l'adresse de
    l'hôte inventé. Ce n'est pas la question posée. La question est de savoir
    si un invité *essaie*.
    """
    return os.environ.get("FIFA14_TEST_HOST", "").strip()


def test_opponent() -> str:
    """The name of an opponent this server will invent, or nothing.

    There is nobody else here to be matched with, so a search honestly times
    out. That leaves one question unanswerable with a single console: how far
    does the title get into a match before it needs a peer that answers UDP?
    Every Blaze-side layout -- the roster, the setup reason, the two join
    notifications -- can be wrong in ways that look identical to a network
    failure from the outside, and telling those two apart is the entire
    reason this exists.

    So it is deliberate, named, and off by default. `FIFA14_TEST_OPPONENT=Bob`
    makes a search find Bob. Nothing about it pretends to be a real player:
    the address it carries is not reachable and is not meant to be, and every
    frame it causes is journalled as synthetic.
    """
    return os.environ.get("FIFA14_TEST_OPPONENT", "").strip()


def notification_frame(component: int, command: int, payload: bytes) -> bytes:
    return encode_frame(component, command, 0, NOTIFICATION, 0, payload)


def empty_map(label: str) -> Field:
    return Field(label, MAP, (STRING, STRING, []))


class Fifa14Protocol:
    def __init__(
        self,
        advertise: str,
        core_port: int,
        logger: "Journal",
        identity_port: int = 18080,
        accounts: "AccountStores | None" = None,
    ):
        self.advertise = advertise
        self.core_port = core_port
        self.logger = logger
        self.identity_port = identity_port
        # `accounts` is the registry; `account_store` stays as the store for
        # the console that has not named itself, which is what every caller
        # without a persona in hand means.
        self.accounts = accounts if accounts is not None else AccountStores()
        # Matchmaking sessions in flight, by connection. The client is handed
        # an id and refers to it afterwards -- cancelling names the session it
        # is cancelling -- so something has to remember which is whose.
        self.matchmaking: dict[int, int] = {}
        # And the timer that will end each one. A search that is cancelled or
        # replaced must take its timer with it: left running it wakes up
        # twenty seconds later holding a session that no longer exists, and
        # the whole point of the id check inside `expire_matchmaking` is that
        # it then finds nothing to do. Cancelling is better than relying on
        # that -- the test suite caught six of these firing after their own
        # journals had been deleted.
        self.matchmaking_timers: dict[int, threading.Timer] = {}
        self.matchmaking_lock = threading.Lock()
        self.matchmaking_next = 1
        # Games this server has handed out a number for. Non-zero for the
        # same reason a session id is: a fieldless success decodes as 0.
        self.next_game_id = 1
        self.games: dict[int, HostedGame] = {}
        # Who is connected, and who wants the census pushed to them.
        self.live: dict[int, ClientState] = {}
        self.census: dict[int, ClientState] = {}
        # What each search asked for, kept so that a search which finds
        # somebody can build the game out of the client's own parameters
        # rather than out of invented ones.
        self.searches: dict[int, HostedGame] = {}
        # The census is pushed on a heartbeat as well as on change. The
        # numbers were right the first time and the screen still read zero:
        # the one push went out at boot, seconds after the console connected
        # and long before anybody walked into the screen that draws them. A
        # client that caches the last thing it was told needs to be told
        # again while it is looking.
        self.census_pulse: threading.Timer | None = None
        self.census_interval = 10.0

    def remember_connection(self, state: ClientState) -> None:
        self.live[state.connection_id] = state

    def forget_connection(self, state: ClientState) -> None:
        """A connection has gone: it is not online, not searching, not a
        census subscriber, and its games are nobody's."""
        self.live.pop(state.connection_id, None)
        self.census.pop(state.connection_id, None)
        self.forget_matchmaking(state.connection_id)
        with self.matchmaking_lock:
            gone = [
                game_id for game_id, game in self.games.items()
                if game.connection_group == state.connection_id
            ]
            for game_id in gone:
                self.games.pop(game_id, None)
        if gone:
            self.broadcast_census()

    def census_snapshot(self) -> list[Field]:
        """What this server can honestly say about itself.

        Every number here is counted, not invented. With one console online
        `LSN` is 1, and 1 is the truth -- which is the whole difference from
        the 0 the screen has been showing.
        """
        players = {
            state.xuid for state in self.live.values()
            if state.authenticated and state.xuid
        }
        return [
            Field("AGN", INTEGER, len(self.games)),
            Field("GACD", LIST, (STRUCT, [])),
            Field("JPN", INTEGER, sum(1 for _ in self.games)),
            Field("LSN", INTEGER, len(players)),
            Field("MMSN", INTEGER, len(self.matchmaking)),
        ]

    def census_notification(self) -> bytes:
        """`NotifyServerCensusData`, which is a list of variable TDFs.

        The outer list is `TDFL` and each element holds one `TDF` -- a
        variable, meaning it carries the class id of whatever census payload
        it is. Several classes register one: the user manager, the clubs
        component. This one is GameManager's, id 0x21239231, and it is the one
        holding the two numbers the screen shows.
        """
        item = [
            Field(
                "TDF",
                VARIABLE,
                (GAME_MANAGER_CENSUS_TDF_ID, self.census_snapshot()),
            )
        ]
        return notification_frame(
            CENSUS_DATA,
            NOTIFY_SERVER_CENSUS_DATA,
            encode_fields([Field("TDFL", LIST, (STRUCT, [item]))]),
        )

    def pulse_census(self) -> None:
        """Push the census, then arrange to do it again."""
        self.broadcast_census()
        self.schedule_census_pulse()

    def schedule_census_pulse(self) -> None:
        with self.matchmaking_lock:
            if self.census_pulse is not None:
                self.census_pulse.cancel()
                self.census_pulse = None
            if not self.census:
                return
            timer = threading.Timer(self.census_interval, self.pulse_census)
            timer.daemon = True
            self.census_pulse = timer
        timer.start()

    def broadcast_census(self) -> None:
        """Tell every subscriber the numbers moved.

        The screen is push-refreshed -- the title has a census-update UI event
        -- so a number that changes has to be sent, not waited for.
        """
        frame = self.census_notification()
        for state in list(self.census.values()):
            if state.push(frame):
                # Pushed frames were going out unrecorded, which made a
                # heartbeat that was working look like one that had stopped.
                self.logger.frame("notification", state, frame)

    def stop(self) -> None:
        """Stop every timer this protocol still owns.

        It owns threads, so it needs a way to be put down. Without one a
        search armed seconds before shutdown wakes up afterwards and writes
        to a journal whose directory has gone -- which is exactly what the
        test suite saw, seven times, attributed each run to whichever test
        happened to be running when the timer went off.
        """
        with self.matchmaking_lock:
            timers = list(self.matchmaking_timers.values())
            self.matchmaking_timers.clear()
            self.matchmaking.clear()
            if self.census_pulse is not None:
                timers.append(self.census_pulse)
                self.census_pulse = None
            self.census.clear()
        for timer in timers:
            timer.cancel()

    def forget_matchmaking(self, connection_id: int) -> int:
        """Drop a connection's search and stop its timer. Returns the id.

        The pending search goes too. It did not, and a cancelled search stayed
        in the pool for ever: on 22 August a player backed out of a search at
        00:53:55 to let the other host instead, and was paired as host anyway
        a minute later, from an entry that should not have existed.
        """
        with self.matchmaking_lock:
            session = self.matchmaking.pop(connection_id, 0)
            timer = self.matchmaking_timers.pop(connection_id, None)
            self.searches.pop(connection_id, None)
        if timer is not None:
            timer.cancel()
        return session

    @property
    def identity_base(self) -> str:
        return f"http://{self.advertise}:{self.identity_port}"

    @property
    def pow_config_keys(self) -> list[tuple[str, str]]:
        """The six POW/EASFC endpoint overrides. Served -- and they must be.

        **Zamboni serves none of these**, and that is the configuration EA
        Sports Football Club actually connects under. The PS3 line carries the
        same finding as a platform flag, `serves_pow_urls=False`, with the
        reasoning that serving `POW_CUSTOMURL` moved `/pow/auth` somewhere
        nothing answered. That console reaches the hub: 1,178 `/pow/` requests
        across sixteen routes.

        This server served all six, and got further than ever before but no
        further than the handshake. `POST /pow/auth` is answered and accepted;
        POW then reports RECONNECTING and takes notifications 2 and 15 with
        status 1, which is `TXT_EASFC_SERVER_ERROR`. Something after the
        handshake fails, and five of these keys point at parts of the service
        this server has never been asked for -- a content server, a nucleus
        proxy, a message manager.

        With them unserved the module keeps its own built-in endpoints, which
        the launch patch has already rewritten to this server. That is exactly
        the shape Zamboni runs in, and it is the only untried configuration
        that a working reference vouches for.

        Tried, and it does not port. With the keys unserved this console made
        **no `/pow/` request at all** -- not the handshake it had been making
        an hour earlier -- while the module string beside it read
        `http://IP:18080` the whole time.

        Which answers the question the last note left open. POW reads the
        config, and the patched string is not a fallback it ever reaches. The
        PS3 can serve nothing because RPCS3 redirects `pal.gt.easfc.ea.com`
        back to the host; the Xbox has no host redirect in front of it, so an
        unserved key points at EA and the request is never seen again.

        So the keys stay served, which is also the configuration that got
        furthest: `POST /pow/auth` answered and accepted, and POW reporting
        RECONNECTING before it fails. `FIFA14_POW_URLS=0` withholds them, and
        the only thing that proves is the paragraph above.

        Nothing about FUT depends on them either way: these are the `POW_` and
        `FIFA_POW_` names only, and FUT's own endpoints --
        `FUT_RS4_BASE_URL`, `FUTBOOTCFGFILE_URL`, `OSDK_EASW_AUTH_URL` -- sit
        untouched beside them.
        """
        if os.environ.get("FIFA14_POW_URLS") == "0":
            return []
        return [
            ("ONLINE/POW_CUSTOMURL", self.pow_endpoint),
            ("ONLINE/POW_CUSTOMCONTENTURL", self.identity_base),
            ("FIFA_POW_URL", self.pow_endpoint),
            ("FIFA_POW_CONTENT_SERVER_URL", self.identity_base),
            ("FIFA_POW_NUCLEUS_PROXY_URL", self.identity_base),
            # The message manager, found by dumping the module on 27 August
            # and listing every config name in it against what this server
            # answers. Five were served and this one was not; serving it
            # changed nothing, which is recorded as plainly as a fix.
            ("FIFA_POW_MMM_URI", self.identity_base),
        ]

    @property
    def pow_endpoint(self) -> str:
        """Where POW sends `/pow/auth`, scheme included.

        This key wins over the patched module string, which took a while to
        establish and is worth writing down. `docs/EASFC_NOT_CONNECTED.md`
        recorded on 12 August that "the module does not take the configuration"
        -- both were read back holding retail values, so nothing distinguished
        them. Then both were pointed at the Blaze port, which is still nothing
        to distinguish.

        On 28 August they finally disagreed. The launch patch wrote
        `http://IP:18080` into the string; the config still said
        `IP:18080`; and the object built from them held the
        **scheme-less** one. The config is what POW reads.

        The scheme is not optional. Without it the module formats
        `IP:18080/pow/auth`, and default.xex's ProtoHttp will not open
        a socket for a URL with no scheme -- which is why hand-patching this
        value into a live object was what produced the first `POST /pow/auth`
        this project has ever seen.

        There is no length budget here, unlike the module string: this is a
        config value, not an in-place overwrite of twenty-four bytes, so it
        works on any address.
        """
        if os.environ.get("FIFA14_POW_CORE_URL") == "1":
            return f"{self.advertise}:{self.core_port}"
        return f"http://{self.advertise}:{self.identity_port}"

    def fetch_config(
        self,
        request: bytes,
        fields: list[Field],
        state: ClientState | None = None,
    ) -> bytes:
        config_id = find_field(fields, "CFID")
        name = str(config_id.value) if config_id is not None else ""
        values: list[tuple[str, str]] = []
        # Which configuration maps the client asks for, and when, is the only
        # signal that says whether it ever reached the point of loading
        # CardsDLL: DLC_USE_REAL_DLL_LOAD lives in OSDK_CLIENT, and without
        # that fetch the DLC wrapper reports success without mapping anything.
        # A session that never asks looks identical to one that asked and was
        # answered badly, so record the request itself.
        journal_fetch = lambda served: self.logger.event(  # noqa: E731
            "config_fetch",
            connection=state.connection_id if state is not None else None,
            name=name,
            values=dict(served),
        )
        if name == "OSDK_CORE":
            # CardsDLL reads its EASW settings from this map, not OSDK_CLIENT.
            # Two of them decide whether it ever speaks: with
            # OSDK_EASW_ALLOWED_LOCALES absent the native gate falls back to
            # "----" and refuses to build the authentication request at all,
            # and without OSDK_EASW_AUTH_URL it has nowhere to send it.  The
            # allow-list echoes the locale this console reported in PreAuth,
            # so the gate matches without guessing a region.
            fut_base = f"{self.identity_base}/"
            locale = (state.locale if state is not None else "") or "enUS"
            values = [
                # EA Sports Football Club. powdllzf names its own endpoints --
                # pal.gt.easfc.ea.com:8094 for the session and
                # content.lt.easfc.ea.com:8080 for the catalogue -- and neither
                # is among the hostnames the launch patch redirects, which is
                # why the header reads "EAS FC non connecte". These keys are
                # what the module reads in preference to those defaults; the
                # retail values give the host:port form.
                #
                # POWService::PowBlazeDisconnected says the session itself is a
                # Blaze connection, not HTTP, so the session URL points at the
                # Blaze core port and the content URL at the identity server.
                # POW's own endpoint, and it was pointed at the Blaze core
                # port until 28 August.
                #
                # The PS3 line found this the hard way and wrote it down:
                # serving `POW_CUSTOMURL` there moved `POST /pow/auth` to the
                # core port "where nothing speaks HTTP: the request leaves the
                # console and is never seen again". That console's fix was to
                # serve no key at all and let the module keep its built-in
                # `pal.gt.easfc.ea.com:8095`, which a host redirect brings
                # home. EASFC connects there now -- 1,178 `/pow/` requests in
                # its journal.
                #
                # The Xbox cannot take that fix; there is no host redirect in
                # front of it, so an unserved key points at EA. It can take the
                # half that matters: the same host:port shape, aimed at the
                # port that does speak HTTP.
                #
                # This explains all three Xbox observations at once, which is
                # why it is worth trying before anything subtler. POW "never
                # opens a socket" -- 10041 is already open, it is the Blaze
                # connection, so there was never a new one to see. Zero `/pow/`
                # requests in any journal -- they were not going to the HTTP
                # server. And the failure is `TXT_EASFC_SERVER_ERROR` rather
                # than a sign-in gate -- the module ran, sent, and got nothing
                # it could read back.
                #
                # `FIFA14_POW_CORE_URL=1` restores the core port, so the old
                # behaviour is one variable away if this turns out worse.
                *self.pow_config_keys,
                # The sixth key, found by dumping the module on 27 August and
                # listing every config name in it against what this server
                # answers. Five were served and this one was not.
                #
                # `MMM` is the message manager -- powdllzf holds the path
                # `/fifa/fltOnlineAssets/2013/pow/mm` beside it, and Impulsum's
                # working build answers `/pow/mm/message/list`. It gets the
                # identity server, like the other content URLs.
                #
                # Whether a missing key is what stops POW starting is not
                # known. It is a key the module asks for and this server did
                # not answer, which is worth closing whatever the answer.
                ("FUT_ENABLE_MENU", "1"),
                ("OSDK_EASW_ALLOWED_LOCALES", locale),
                ("OSDK_EASW_AUTH_URL", self.identity_base),
                ("FUTBOOTCFGFILE_URL", f"{self.identity_base}/futBoot.xml"),
                # Left unset, CardsDLL falls back to the retired
                # easw.easports.com:8099 and pg.fifa13.test... hosts, both of
                # which are still present as literals in the shipped DLL.
                ("FUT_RS4_BASE_URL", fut_base),
                ("FUTDYNAMICMESSAGES_URL_BASE", self.identity_base),
            ]
        elif name == "IdentityParams":
            # The Xbox Authentication2 bootstrap appends this map to
            # nucleusConnect/connect/auth, then looks redirect_uri up again
            # while parsing the HTTP redirect.
            values = [
                ("client_id", "fifa14-xbox360-offline"),
                ("redirect_uri", f"{self.identity_base}/connect/redirect"),
            ]
        elif name == "OSDK_CLIENT":
            # The retail client asks the OSDK configuration service whether
            # an obsolete EASW asset refresh must complete before EnterFUT2.
            # With the original content host gone, leaving the section empty
            # keeps FutCfg uninitialised (native status 0x0B) and the FUT
            # loader never starts.  This is the title's own offline/no-update
            # switch: it bypasses only the dead asset patcher, while the real
            # Blaze login and subsequent CardHouse session remain mandatory.
            #
            # DLC_USE_REAL_DLL_LOAD is equally important on retail builds.
            # When it is absent/zero, the DLC wrapper reports success without
            # calling the XEX loader.  FUT then waits forever because
            # CardsDLLzf.xex.dll was never mapped and cannot open CardHouse.
            fut_base = f"{self.identity_base}/"
            values = [
                ("ONLINE/NO_ASSET_UPDATE", "1"),
                ("DLC_USE_REAL_DLL_LOAD", "1"),
                # Exact CardsDLLzf.xex.dll configuration names recovered from
                # the active Xbox 360 TU3 image.  The platform formatter uses
                # the literal suffix "XBox360" on this build.
                ("FUTBOOTCFGFILE_URL", f"{self.identity_base}/futBoot.xml"),
                ("FUT_URI", fut_base),
                ("FUT_RS4_BASE_URL", fut_base),
                # These are present in the verified PC flow before CardsDLL
                # accepts its static localization assets and naturally starts
                # the Authentication WebSession.  CardsDLLzf.xex.dll contains
                # the directed-environment key verbatim; the deployment
                # language is consumed by the FIFA-side Cards bridge.
                ("CARDS/DIRECTED_BLAZEENV", "prod"),
                ("FCC/FUT_DEPLOY_LANGUAGE", "en_US"),
                ("FUT/SINGLE_BASEURL_XBox360", fut_base),
                ("FUT_RS4_URL_XBox360", fut_base),
                ("FUT_RS4_APIURL_XBox360", fut_base),
                ("FUT/MODULE_BASEURL_XBox360", fut_base),
                ("FUTDYNAMICMESSAGES_URL_BASE", self.identity_base),
                ("FUTDYNAMICMESSAGES_URL_GET_MESSAGES", "/messages"),
                ("ONLINE/FUTDYNAMICMESSAGES_TUTORIAL_MSG_URL", "/tutorials"),
                ("FUTDYNAMICMESSAGES_REQUEST_TIMEOUT", "5000"),
                ("FUTDYNAMICMESSAGES_REFRESH_INTERVAL", "300000"),
                ("FUT_ENABLE_MENU", "1"),
                ("ONLINE/NO_AUTO_SQUAD", "0"),
                # Every session, however deep, ends on the same two calls:
                # userdata, then the tutorial URL, then silence.  CardsDLL
                # pairs RetrieveShouldShowTutorial with a separate
                # RetrieveShouldShowTutorialComplete, so that retrieval is
                # something DoInitialLoginSteps waits on -- and forcing
                # tutorials on is what sends it there.  Turn the step off at
                # its own switches rather than trying to satisfy a parser whose
                # document shape is unknown.
                ("FUT/FORCE_TUTORIALS", "0"),
                ("FUT/DISABLE_TUTORIALS", "1"),
                ("FUT/ALWAYS_SHOW_SMART_TUTORIALS", "0"),
                ("FUT/IS_RETURNING_USER", "0"),
                ("FUT_SKIP_ICEBREAKER_FLOW", "0"),
            ]
        elif name == "OSDK_ROSTER":
            # FIFA's Xbox retail LoadRosterConfig reads these four exact
            # names.  An empty section never publishes the roster-ready event
            # consumed by helperFunctions::checkForFUTRosters.  Version 1.0
            # denotes the shipped/base roster and avoids fabricating a roster
            # download; URL remains local if this build still elects to check.
            values = [
                ("ROSTER_URL", f"{self.identity_base}/roster"),
                ("ROSTER_VER", "1.0"),
                ("ROSTER_LKR", ""),
                ("ROSTER_CSUM", ""),
            ]
        journal_fetch(values)
        return response_frame(
            request,
            encode_fields(
                [Field("CONF", MAP, (STRING, STRING, values))]
            ),
        )

    def ping_site(self) -> list[Field]:
        return [
            Field("PSA", STRING, self.advertise),
            Field("PSP", INTEGER, 17502),
            Field("SNA", STRING, "ams"),
        ]

    def redirector(self, request: bytes) -> bytes:
        payload = encode_fields(
            [
                Field(
                    "ADDR",
                    UNION,
                    (
                        0,
                        Field(
                            "VALU",
                            STRUCT,
                            [
                                Field("HOST", STRING, self.advertise),
                                Field("IP", INTEGER, 0),
                                Field("PORT", INTEGER, self.core_port),
                            ],
                        ),
                    ),
                ),
                Field("SECU", INTEGER, 0),
                Field("XDNS", INTEGER, 0),
            ]
        )
        return response_frame(request, payload)

    @staticmethod
    def decode_locale(value: object) -> str:
        """Return the printable four-character locale behind PreAuth's LANG."""
        if not isinstance(value, int) or isinstance(value, bool):
            return ""
        if not 0 < value <= 0xFFFFFFFF:
            return ""
        try:
            text = value.to_bytes(4, "big").decode("ascii")
        except (UnicodeDecodeError, OverflowError):
            return ""
        return text if text.isalpha() else ""

    def preauth(self, request: bytes, state: ClientState | None = None) -> bytes:
        if state is not None:
            language = find_field(decode_frame(request)["fields"], "LANG")
            locale = self.decode_locale(language.value if language else None)
            if locale:
                state.locale = locale
        payload = encode_fields(
            [
                Field("ANON", INTEGER, 0),
                Field("ASRC", STRING, "300294"),
                Field("CIDS", LIST, (INTEGER, COMPONENT_IDS)),
                Field("CNGN", STRING, ""),
                Field(
                    "CONF",
                    STRUCT,
                    [
                        Field(
                            "CONF",
                            MAP,
                            (
                                STRING,
                                STRING,
                                [
                                    ("connIdleTimeout", "120s"),
                                    ("defaultRequestTimeout", "80s"),
                                    # Authentication2 reads these directly
                                    # from the PreAuth client-config map.
                                    ("nucleusConnect", self.identity_base),
                                    ("pingPeriod", "20s"),
                                    ("voipHeadsetUpdateRate", "1000"),
                                    # Keep the token audience on EA's historic
                                    # relying-party host while the HTTP target
                                    # itself is our local preservation server.
                                    ("xblTokenUrn", "http://accounts.ea.com"),
                                    ("xlspConnectionIdleTimeout", "300"),
                                ],
                            ),
                        )
                    ],
                ),
                # Stock console-auth bootstrap fields retained for parity with
                # the title's expected PreAuth schema.
                Field("EEFA", INTEGER, 1),
                Field("ESRC", STRING, "fifa-2014-xbl2"),
                Field("INST", STRING, "fifa-2014-xbl2"),
                Field("MINR", INTEGER, 0),
                Field("NASP", STRING, "cem_ea_id"),
                Field("PILD", STRING, "fifa-2014-xbl2"),
                Field("PLAT", STRING, "xbox360"),
                Field("PTAG", STRING, ""),
                Field(
                    "QOSS",
                    STRUCT,
                    [
                        Field("BWPS", STRUCT, self.ping_site()),
                        Field("LNP", INTEGER, 10),
                        Field(
                            "LTPS",
                            MAP,
                            (STRING, STRUCT, [("ams", self.ping_site())]),
                        ),
                        Field("SVID", INTEGER, 1161889797),
                    ],
                ),
                Field("RSRC", STRING, "300294"),
                Field("SVER", STRING, "Blaze 3.15.08.0 (CL# 1060080)"),
            ]
        )
        return response_frame(request, payload)

    def ping(self, request: bytes) -> bytes:
        return response_frame(
            request,
            encode_fields([Field("STIM", INTEGER, int(time.time()))]),
        )

    def postauth(self, request: bytes, state: ClientState) -> bytes:
        # FIFA 14's generated Blaze 3 PostAuthResponse contains four members,
        # in TDF-tag order: PSS, TELE, TICK and UROP.  PSS is a real embedded
        # struct even on Xbox 360 (where its PS3-specific values stay empty),
        # so emit it instead of relying on the client's default constructor.
        # The telemetry/ticker values mirror the working Zamboni legacy
        # implementation; those services are auxiliary, but non-zero ports
        # keep the title's post-auth setup on its normal success path.
        payload = encode_fields(
            [
                Field(
                    "PSS",
                    STRUCT,
                    [
                        Field("ADRS", STRING, ""),
                        Field("CSIG", BINARY, b""),
                        Field("OIDS", LIST, (STRING, [])),
                        Field("PJID", STRING, ""),
                        Field("PORT", INTEGER, 0),
                        Field("RPRT", INTEGER, 0),
                        Field("TIID", INTEGER, 0),
                    ],
                ),
                Field(
                    "TELE",
                    STRUCT,
                    [
                        Field("ADRS", STRING, self.advertise),
                        Field("ANON", INTEGER, 0),
                        Field("DISA", STRING, "disa"),
                        Field("FILT", STRING, "filt"),
                        Field("LOC", INTEGER, 1718765138),
                        Field("NOOK", STRING, "nook"),
                        Field("PORT", INTEGER, 6767),
                        Field("SDLY", INTEGER, 10),
                        Field("SESS", STRING, "id"),
                        Field("SKEY", STRING, "key"),
                        Field("SPCT", INTEGER, 10),
                        Field("STIM", STRING, "true"),
                    ],
                ),
                Field(
                    "TICK",
                    STRUCT,
                    [
                        Field("ADRS", STRING, self.advertise),
                        Field("PORT", INTEGER, 6776),
                        Field("SKEY", STRING, "key"),
                    ],
                ),
                Field(
                    "UROP",
                    STRUCT,
                    [
                        Field("TMOP", INTEGER, 0),
                        Field("UID", INTEGER, state.xuid),
                    ],
                ),
            ]
        )
        return response_frame(request, payload)

    def xbox_login(self, request: bytes, state: ClientState) -> list[bytes]:
        decoded = decode_frame(request)
        gamer = find_field(decoded["fields"], "GTAG")
        xuid = find_field(decoded["fields"], "XUID")
        mail = find_field(decoded["fields"], "MAIL")
        if gamer and isinstance(gamer.value, str) and gamer.value:
            state.gamertag = gamer.value
        if xuid and isinstance(xuid.value, int) and xuid.value:
            state.xuid = xuid.value
        if mail and isinstance(mail.value, str) and mail.value:
            state.email = mail.value
        state.authenticated = True

        now = int(time.time())
        persona = [
            Field("DSNM", STRING, state.gamertag),
            Field("LAST", INTEGER, now),
            Field("PID", INTEGER, state.xuid),
            Field("STAS", INTEGER, 2),
            Field("XREF", INTEGER, state.xuid),
            Field("XTYP", INTEGER, 1),
        ]
        session = [
            Field("BUID", INTEGER, state.xuid),
            Field("FRST", INTEGER, 1),
            Field("KEY", STRING, f"offline-{state.xuid:x}"),
            Field("LLOG", INTEGER, now),
            Field("MAIL", STRING, state.email),
            Field("PDTL", STRUCT, persona),
            Field("UID", INTEGER, state.xuid),
        ]
        login = encode_fields(
            [
                Field("AGUP", INTEGER, 0),
                Field("LDHT", STRING, ""),
                Field("NTOS", INTEGER, 0),
                Field("PRIV", STRING, ""),
                Field("SESS", STRUCT, session),
                Field("SPAM", INTEGER, 1),
                Field("THST", STRING, ""),
                Field("TSUI", STRING, ""),
                Field("TURI", STRING, ""),
            ]
        )

        user_identification = self.user_identification(state.xuid, state.gamertag)

        user_added = notification_frame(
            USER_SESSIONS,
            2,
            encode_fields(
                [
                    Field(
                        "DATA",
                        STRUCT,
                        [
                            *self.session_extended_data(),
                        ],
                    ),
                    Field("USER", STRUCT, user_identification),
                ]
            ),
        )
        return [response_frame(request, login), user_added]

    def authentication2_login(
        self,
        request: bytes,
        state: ClientState,
        fields: list[Field],
    ) -> list[bytes]:
        """Complete FIFA 14's Nucleus-code login on component 35.

        This is a title-side ``Blaze::Authentication2`` component that is not
        present in the public Zamboni BlazeSDK.  Its exact LoginResponse field
        table was recovered from this supported FIFA 14 executable: ANON,
        SESS, SPAM and UNDR.  SESS is the standard Blaze Authentication
        SessionInfo structure.
        """

        external_id = find_field(fields, "EXTI")
        if external_id and isinstance(external_id.value, int) and external_id.value:
            state.xuid = external_id.value
        state.authenticated = True
        # Authentication2 carries no GTAG, so this login knows no display name
        # of its own.  The FUT auth request does carry the console's real one,
        # so reuse the stored persona instead of overwriting it with the
        # placeholder and advertising a name the client never presented.
        store = self.accounts.get(state.xuid)
        stored_id, stored_name = store.load_identity()
        if state.gamertag == ClientState.gamertag and stored_id == state.xuid:
            state.gamertag = stored_name
        store.save_identity(state.xuid, state.gamertag)
        # Named, not bound. The Blaze side touches exactly one piece of club
        # state -- the persona -- so it says which club it means instead of
        # binding the thread to one. Binding here was the first attempt and it
        # was wrong: `Fifa14Protocol.handle` is called directly, without a
        # connection around it, and the binding then outlived the caller.
        TENANTS.get(state.xuid).persona.adopt(state.xuid)

        now = int(time.time())
        persona = [
            Field("DSNM", STRING, state.gamertag),
            Field("PID", INTEGER, state.xuid),
            # Authentication2::PersonaDetails in this FIFA executable has
            # exactly DSNM, PID and PLAT.  It is smaller than the similarly
            # named legacy Authentication::PersonaDetails structure.
            Field("PLAT", INTEGER, 1),  # ExternalSystemId::XBOX
        ]
        session = [
            Field("BUID", INTEGER, state.xuid),
            Field("FRST", INTEGER, 0),
            Field("KEY", STRING, f"offline-{state.xuid:x}"),
            Field("LLOG", INTEGER, now),
            Field("MAIL", STRING, state.email),
            Field("PDTL", STRUCT, persona),
            Field("UID", INTEGER, state.xuid),
        ]
        login = encode_fields(
            [
                Field("ANON", INTEGER, 0),
                Field("SESS", STRUCT, session),
                Field("SPAM", INTEGER, 1),
                Field("UNDR", INTEGER, 0),
            ]
        )

        notifications = self.session_notifications(state)
        self.logger.event(
            "authentication2_login",
            connection=state.connection_id,
            external_id=state.xuid,
        )
        # This console is now one of the players online, so say so.
        self.broadcast_census()
        return [response_frame(request, login), *notifications]

    def open_matchmaking_session(self, state: ClientState) -> int:
        """Hand out a matchmaking session id.

        Non-zero is the whole point. `StartMatchmakingResponse` is a single
        field, `MSID`, and a fieldless success -- which is what this server
        answered on 21 August -- decodes as 0. The client then has no session
        to wait on, no session to cancel, and sits on the search screen with
        nothing to say about it.
        """
        # A search already in flight on this connection is over the moment a
        # new one starts, and its timer goes with it.
        self.forget_matchmaking(state.connection_id)
        with self.matchmaking_lock:
            session = self.matchmaking_next
            self.matchmaking_next += 1
            self.matchmaking[state.connection_id] = session
        return session

    def close_matchmaking_session(self, state: ClientState) -> int:
        return self.forget_matchmaking(state.connection_id)

    def host_info(self, game: HostedGame) -> list[Field]:
        """Which session hosts, in the shape the game data carries it twice.

        `PHST` is the platform host and `THST` the topology host, and on a
        peer-to-peer game with one console they are the same player. With
        neither of them present the client cannot tell whether it is the host
        or a peer, which is one of the two ways this notification can be
        received and quietly discarded.
        """
        return [
            Field("CONG", INTEGER, game.connection_group),
            Field("CSID", INTEGER, 0),
            Field("HPID", INTEGER, game.persona_id),
            Field("HSLT", INTEGER, 0),
        ]

    def replicated_game_data(self, game: HostedGame) -> list[Field]:
        """The game, all thirty-six members of it.

        The first pass sent fourteen, because fourteen was all the title's
        member table appeared to hold. It was not a partial table with the
        rest in generated code: it is half baked into `.data` and half written
        at startup by initialiser code that assembles each tag from a pair of
        instructions. Searching the image for tag words could never have found
        those, which is why `PGSC` and `RGID` looked absent while travelling
        on the wire in this repo's own capture.

        Members go out in ascending tag order, which is the order the client
        sends its own. `XNNC` and `XSES` are members and are still not sent:
        they are the host's XNet nonce and session, and the host hands those
        over later, in `finalizeGameCreation`. Empty ones would claim
        knowledge this server does not have.
        """
        fields = [
            Field("ADMN", LIST, (INTEGER, [game.persona_id])),
            game.attributes or empty_map("ATTR"),
            game.capacity or Field("CAP", LIST, (INTEGER, [2, 0, 0, 0])),
            game.criteria or empty_map("CRIT"),
            Field("GID", INTEGER, game.game_id),
            Field("GMRG", INTEGER, game.mod_register),
            Field("GNAM", STRING, game.name),
            # The protocol version *hash*, which is not the string and is not
            # recoverable from here.
            Field("GPVH", INTEGER, 0),
            Field("GSET", INTEGER, game.settings),
            Field("GSID", INTEGER, game.game_id),
            Field("GSTA", INTEGER, game.state),
            Field("GTYP", STRING, game.game_type),
            Field("GURL", STRING, game.status_url),
            game.host_addresses or Field("HNET", LIST, (STRUCT, [])),
            # The session that hosts the topology.
            Field("HSES", INTEGER, game.persona_id),
            Field("IGNO", INTEGER, 0),
            empty_map("MATR"),
            Field("MCAP", INTEGER, game.max_capacity),
            Field("NQOS", STRUCT, [
                Field("DBPS", INTEGER, 0),
                # NAT_TYPE_OPEN, which is what the console reported for itself.
                Field("NATT", INTEGER, 0),
                Field("UBPS", INTEGER, 0),
            ]),
            Field("NRES", INTEGER, 0),
            Field("NTOP", INTEGER, game.topology),
            Field("PGID", STRING, ""),
            Field("PGSR", BINARY, b""),
            Field("PHST", STRUCT, self.host_info(game)),
            Field("PRES", INTEGER, game.presence),
            Field("PSAS", STRING, ""),
            Field("QCAP", INTEGER, game.queue_capacity),
            Field("RNFO", STRUCT, [empty_map("CRIT"), empty_map("RCRT")]),
            # The shared seed both sides randomise from. Derived from the game
            # number so a replay of the same game is the same game.
            Field("SEED", INTEGER, 0x5EED0000 | (game.game_id & 0xFFFF)),
            Field("THST", STRUCT, self.host_info(game)),
            game.teams or Field("TIDS", LIST, (INTEGER, [65534])),
            Field("UUID", STRING, f"revival-{game.game_id:08x}"),
            Field("VOIP", INTEGER, game.voip),
            Field("VSTR", STRING, game.protocol_version),
        ]
        return sorted(fields, key=lambda field: encode_tag(field.label))

    def member(self, game: HostedGame, persona: int, gamertag: str,
               group: int, address: Any, slot: int, team: int,
               state: ClientState | None = None) -> dict:
        """One player in a game, and how to reach them."""
        return {
            "persona": int(persona),
            "gamertag": str(gamertag),
            "group": int(group),
            "address": address,
            "slot": int(slot),
            "team": int(team),
            "state": state,
        }

    def member_player(self, game: HostedGame, member: dict,
                      viewer: ClientState | None = None) -> list[Field]:
        """A roster entry, from a member record, as one player will read it.

        `viewer` matters only when a relay is configured: a player's own
        address is left alone -- it knows where it lives -- and everybody
        else's is pointed at the relay, because those are the ones it will
        dial.
        """
        fields = [
            Field("CONG", INTEGER, member["group"]),
            Field("CSID", INTEGER, member["slot"]),
            Field("EXID", INTEGER, member["persona"]),
            Field("GID", INTEGER, game.game_id),
            Field("LOC", INTEGER, LOCALE),
            Field("NAME", STRING, member["gamertag"]),
            Field("PID", INTEGER, member["persona"]),
            Field("SID", INTEGER, member["slot"]),
            Field("SLOT", INTEGER, 0),
            # Connected only if they have said so.
            #
            # Every player went out as ACTIVE_CONNECTED, which for a guest
            # that has not reached anybody is not true and is the kind of
            # untrue that stops things happening: a client told the link is
            # already up has no reason to go and put it up. It waits, which is
            # what a guest did through five pairings tonight while receiving
            # the game, the host, the state and the session key and answering
            # none of it.
            #
            # The host is connected to itself by definition. Everybody else
            # starts connecting and is promoted when their mesh says so.
            Field("STAT", INTEGER,
                  PLAYER_STATE_ACTIVE_CONNECTED
                  if member["persona"] == game.persona_id
                  else PLAYER_STATE_ACTIVE_CONNECTING),
            Field("TIDX", INTEGER, member["team"]),
            Field("TIME", INTEGER, int(time.time())),
            Field("UGID", OBJECT_ID, (0, 0, 0)),
            # mPlayerSessionId: how a client recognises itself in a roster.
            Field("UID", INTEGER, member["persona"]),
        ]
        address = member["address"]
        relay = peer_relay()
        if (address is not None and relay is not None and viewer is not None
                and member["persona"] != viewer.xuid):
            active, valu = address
            rewritten = []
            for entry in valu.value:
                if entry.label == "XDDR":
                    rewritten.append(Field(
                        "XDDR", BINARY, relayed_address(bytes(entry.value), relay)
                    ))
                else:
                    rewritten.append(entry)
            address = (active, Field("VALU", STRUCT, rewritten))
        if address is not None:
            fields.append(Field("PNET", UNION, address))
        return sorted(fields, key=lambda field: encode_tag(field.label))

    def replicated_game_player(self, game: HostedGame) -> list[Field]:
        """The host, as a player in its own game. Eighteen members, not sixteen.

        The two that were missing are the ones that matter. `UID` is
        `mPlayerSessionId` -- the user session id -- and it is how the client
        recognises *itself* in a roster. A roster with no `UID` gives it no
        way to match a slot to its own session, so it ends up with a game and
        no local player in it, and drops the setup without an error and
        without a word. Which was the symptom exactly: six frames delivered,
        nothing wrong, nothing happening.

        That reading of what `mPlayerSessionId` is for is a hypothesis, not
        something read out of the client's dispatch. It is a hypothesis with a
        one-field test.
        """
        fields = [
            Field("UGID", OBJECT_ID, (0, 0, 0)),
            # The same id the login notifications gave this session.
            Field("UID", INTEGER, game.persona_id),
            Field("CONG", INTEGER, game.connection_group),
            Field("CSID", INTEGER, 0),
            Field("EXID", INTEGER, game.persona_id),
            Field("GID", INTEGER, game.game_id),
            Field("LOC", INTEGER, LOCALE),
            Field("NAME", STRING, game.gamertag),
            Field("PID", INTEGER, game.persona_id),
            Field("SID", INTEGER, 0),
            Field("SLOT", INTEGER, 0),
            Field("STAT", INTEGER, PLAYER_STATE_ACTIVE_CONNECTED),
            Field("TIDX", INTEGER, 0),
            Field("TIME", INTEGER, int(time.time())),
        ]
        if game.host_address is not None:
            # The address the console gave for itself, handed straight back.
            fields.append(Field("PNET", UNION, game.host_address))
        return sorted(fields, key=lambda field: encode_tag(field.label))

    def setup_reason(self, session: int,
                     result: int = MATCHMAKING_SUCCESS_CREATED_GAME) -> tuple:
        """Why this game exists, in the shape the client asks for.

        A game the console asked for itself is union index 0 -- a dataless
        context whose one member says CREATE_GAME. A game a search found is
        index 3, and carries the session it belongs to and a fit score.
        `USID` is not in either, whatever the published tables say.

        Both halves are settled rather than inferred now: three candidate
        member arrays sit together in the binary and the one at 0x83CDCA98 is
        MatchmakingSetupContext, the six-member one is the indirect variant,
        and the three-member one was never a setup context at all -- it is
        NotifyMatchmakingFailed, which is why it looked like a candidate.
        """
        if not session:
            return (
                SETUP_REASON_DATALESS,
                Field("VALU", STRUCT, [
                    Field("DCTX", INTEGER, SETUP_CONTEXT_CREATE_GAME),
                ]),
            )
        return (
            SETUP_REASON_MATCHMAKING,
            Field("VALU", STRUCT, [
                Field("FIT", INTEGER, 100),
                Field("MAXF", INTEGER, 100),
                Field("MSID", INTEGER, session),
                Field("RSLT", INTEGER, result),
            ]),
        )

    def borrowed_address(self, real) -> tuple | None:
        """A real console's XNADDR, worn by somebody invented.

        `abOnline` is copied as it stands -- that is the whole point, it is the
        only genuine block available. The MAC is changed so two machines do not
        announce one address on one network, and the XUID becomes the invented
        player's, because the roster is keyed on it.

        Lifted out of `waiting_test_host` when the invented guest needed the
        same treatment. See `mirror_invented_address`.
        """
        if real is None:
            return None
        active, valu = real
        worn = []
        for entry in valu.value:
            if entry.label == "XDDR":
                worn.append(Field(
                    "XDDR", BINARY, mirrored_address(bytes(entry.value))
                ))
            elif entry.label == "XUID":
                worn.append(Field("XUID", INTEGER, SYNTHETIC_PERSONA))
            else:
                worn.append(entry)
        return (active, Field("VALU", STRUCT, worn))

    def synthetic_address(self, real=None) -> tuple:
        """A well-formed XNADDR that leads nowhere.

        A LAN address nothing answers on, port 3074, and a MAC in the
        locally-administered range so it cannot collide with real hardware.
        If the console dials it, it fails -- and where it fails is the
        measurement.
        """
        # An invented guest can borrow too, when there is a real address to
        # borrow from. Without it this returns twenty zero bytes of `abOnline`,
        # and a console that says nothing to that has said nothing about the
        # question -- the same confound the invented host had.
        if mirror_invented_address():
            worn = self.borrowed_address(real)
            if worn is not None:
                self.logger.event(
                    "test_peer_borrowed_address", synthetic=True, role="guest"
                )
                return worn

        address = bytes([192, 168, 1, 200]) + bytes(4) + bytes([0x0C, 0x02])
        address += bytes.fromhex("02005e000001") + bytes(20)
        # `FIFA14_PEER_RELAY` has to reach this address too, or the probe it
        # exists for cannot be run with one console.
        #
        # The rewrite in `NotifyGameSetup` walks a real member's PNET; the
        # invented opponent's is built here instead and never passed through
        # it. So a solo relay test told the console the opponent was at
        # 192.168.1.200 -- a LAN that is not this one -- and nothing could ever
        # arrive at the relay, which reads exactly like "the console ignored
        # the rewrite" and means nothing of the kind.
        #
        # With this, one console answers the question: is the rewritten address
        # what it dials, or does the untouched `abOnline` block decide? A
        # second console is still needed for a match; it is not needed for that.
        relay = peer_relay()
        if relay is not None:
            address = relayed_address(address, relay)
        return (0, Field("VALU", STRUCT, [
            Field("MACI", INTEGER, 0),
            Field("XDDR", BINARY, address),
            Field("XUID", INTEGER, SYNTHETIC_PERSONA),
        ]))

    def synthetic_peer_address(self, game: HostedGame) -> tuple:
        """The invented player's address, built once and relayed once.

        `synthetic_player` used to call `synthetic_address()` with no argument,
        so notification 21 rebuilt the fabricated address even when the roster
        already held a borrowed one -- the borrow fired, was logged, and was
        then overwritten on the way out. The address on the wire is the only one
        that matters, and there should be one of it.

        The relay rewrite is applied here because this notification does not go
        through `member_player`, which is where a real member's address is
        pointed at the relay. Applying it to an address that already carries the
        relay is harmless: it writes the same four bytes and the same port.
        """
        stored = None
        for member in game.members or []:
            if member.get("persona") == SYNTHETIC_PERSONA:
                stored = member.get("address")
                break
        address = stored or self.synthetic_address(game.host_address)
        relay = peer_relay()
        if relay is None:
            return address
        active, valu = address
        rewritten = []
        for entry in valu.value:
            if entry.label == "XDDR":
                rewritten.append(Field(
                    "XDDR", BINARY, relayed_address(bytes(entry.value), relay)
                ))
            else:
                rewritten.append(entry)
        return (active, Field("VALU", STRUCT, rewritten))

    def synthetic_player(self, game: HostedGame) -> list[Field]:
        """An opponent this server made up, and says so.

        Its address is a well-formed XNADDR that leads nowhere: a LAN address
        nothing answers on, port 3074, and a MAC in the locally-administered
        range so it cannot collide with real hardware. If the console tries to
        dial it, it will fail -- and *where* it fails is the measurement.
        """
        fields = [
            Field("UGID", OBJECT_ID, (0, 0, 0)),
            Field("UID", INTEGER, SYNTHETIC_PERSONA),
            Field("CONG", INTEGER, SYNTHETIC_PERSONA),
            Field("CSID", INTEGER, 1),
            Field("EXID", INTEGER, SYNTHETIC_PERSONA),
            Field("GID", INTEGER, game.game_id),
            Field("LOC", INTEGER, LOCALE),
            Field("NAME", STRING, test_opponent() or "Sparring"),
            Field("PID", INTEGER, SYNTHETIC_PERSONA),
            Field("PNET", UNION, self.synthetic_peer_address(game)),
            Field("SID", INTEGER, 1),
            Field("SLOT", INTEGER, 0),
            Field("STAT", INTEGER, PLAYER_STATE_ACTIVE_CONNECTED),
            Field("TIDX", INTEGER, 1),
            Field("TIME", INTEGER, int(time.time())),
        ]
        return sorted(fields, key=lambda field: encode_tag(field.label))

    def opponent_notifications(self, game: HostedGame) -> list[bytes]:
        """Somebody joined. Told as two events, because that is how a client
        tracks a player: one that it is happening, one that it is done."""
        player = self.synthetic_player(game)
        joining = notification_frame(
            GAME_MANAGER,
            NOTIFY_PLAYER_JOINING,
            encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("PDAT", STRUCT, player),
            ]),
        )
        joined = notification_frame(
            GAME_MANAGER,
            NOTIFY_PLAYER_JOIN_COMPLETED,
            encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("PID", INTEGER, SYNTHETIC_PERSONA),
            ]),
        )
        return [joining, joined]

    def game_setup_payload(self, game: HostedGame, session: int = 0,
                           result: int = MATCHMAKING_SUCCESS_CREATED_GAME,
                           viewer: ClientState | None = None) -> list[Field]:
        """The five members of NotifyGameSetup.

        Shared by notification 20 and notification 22, because the 557-class
        index of this binary holds exactly one class by that name -- the two
        notifications differ in who they go to and what they make that client
        do, not in what they carry.
        """
        return [
            Field("GAME", STRUCT, self.replicated_game_data(game)),
            Field("LFPJ", INTEGER, 0),
            Field("PROS", LIST, (STRUCT, [
                self.member_player(game, member, viewer) for member in game.members
            ] or [self.replicated_game_player(game)])),
            Field("QUEU", LIST, (STRUCT, [])),
            Field("REAS", UNION, self.setup_reason(session, result)),
        ]

    def game_setup_notifications(
        self, game: HostedGame, session: int = 0,
        viewer: ClientState | None = None,
    ) -> list[bytes]:
        """`NotifyGameSetup`, and then who the host is.

        The five members of notification 20 are certain, including `LFPJ`,
        which is in no published table -- it is a FIFA-14-era addition next to
        the FIFA-only `preferredJoinOptOut` command.

        Notification 71 follows because `ReplicatedGameData`'s own host
        members could not be read, and 71's layout could. Rather than guess a
        tag for the host inside the game, the host is stated separately in a
        message whose shape is known.
        """
        setup = notification_frame(
            GAME_MANAGER,
            NOTIFY_GAME_SETUP,
            encode_fields(self.game_setup_payload(game, session, viewer=viewer)),
        )
        host = notification_frame(
            GAME_MANAGER,
            NOTIFY_PLATFORM_HOST_INITIALIZED,
            encode_fields(
                [
                    Field("GID", INTEGER, game.game_id),
                    Field("PHID", INTEGER, game.persona_id),
                    Field("PHST", INTEGER, 0),
                ]
            ),
        )
        # And then move it out of INITIALIZING.
        #
        # A game that has just been created is initialising, and a game
        # waiting for an opponent is PRE_GAME. Sending the setup alone left
        # the console back on the Face-à-Face settings screen: it had read the
        # game and had no reason to sit in one, because as far as it knew the
        # game was still being built.
        #
        # `{GID, GSTA}` is the whole of notification 100. The state values are
        # certain -- 130 and 131 rather than the 3 and 4 the other members of
        # that enum would suggest -- and `GSTA` is the tag ReplicatedGameData
        # uses for the same member.
        # And say that the host is in.
        #
        # The roster already carries it as ACTIVE_CONNECTED, but a roster is a
        # description and this is an event. Pressing "Créer un match" makes
        # the console drop its A/B prompts -- so it does act on the setup --
        # and then wait, which is what a client does when it is holding a
        # player it has not been told finished joining.
        #
        # `{GID, PID}` is the whole of notification 30 and both tags are
        # certain.
        joined = notification_frame(
            GAME_MANAGER,
            NOTIFY_PLAYER_JOIN_COMPLETED,
            encode_fields(
                [
                    Field("GID", INTEGER, game.game_id),
                    Field("PID", INTEGER, game.persona_id),
                ]
            ),
        )
        game.state = GAME_STATE_PRE_GAME
        pre_game = notification_frame(
            GAME_MANAGER,
            NOTIFY_GAME_STATE_CHANGE,
            encode_fields(
                [
                    Field("GID", INTEGER, game.game_id),
                    Field("GSTA", INTEGER, game.state),
                ]
            ),
        )
        return [setup, host, joined, pre_game]

    def create_game(self, request: bytes, state: ClientState) -> list[bytes]:
        """"Créer un match", once the search has found nobody.

        The whole request is worth reading and it is all in the journal: two
        player slots, `gameType0`, protocol version `qa-only-day45`, the
        match's own settings in `ATTR` (half length, game speed, team level),
        and `HNET` -- one NetworkAddress union carrying this console's XNADDR.
        That address is what a second console will need, unaltered, to dial
        this one.

        `CreateGameResponse` is a single field, `GID`, the same shape as the
        matchmaking session id. Handing one out is not the end of it: the
        client then expects `NotifyGameSetup`, which carries a whole
        ReplicatedGameData, and that is the next thing to build. Until then
        the game exists as far as this server is concerned and the console
        has its number.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]

        def value(label: str, fallback: Any = None) -> Any:
            found = find_field(fields, label)
            return found.value if found is not None else fallback

        with self.matchmaking_lock:
            game_id = self.next_game_id
            self.next_game_id += 1
        host = find_field(fields, "HNET")
        # The host's own address, lifted out of the list so the roster entry
        # can carry it back. In a list a union has no VALU wrapper and its
        # members sit inline, so it is rebuilt into the wrapped form a field
        # needs -- the two spellings are the whole reason the decoder had to
        # be taught the difference.
        host_address = None
        if host is not None and host.value[1]:
            first = host.value[1][0]
            if isinstance(first, tuple):
                active, members = first
                host_address = (active, Field("VALU", STRUCT, members))
        game = HostedGame(
            game_id=game_id,
            persona_id=state.xuid,
            gamertag=state.gamertag,
            name=str(value("GNAM", "")),
            game_type=str(value("GTYP", "")),
            status_url=str(value("GURL", "")),
            protocol_version=str(value("VSTR", "")),
            topology=int(value("NTOP", 0) or 0),
            settings=int(value("GSET", 0) or 0),
            mod_register=int(value("GMRG", 0) or 0),
            attributes=find_field(fields, "ATTR"),
            criteria=find_field(fields, "CRIT"),
            capacity=Field("CAP", LIST, find_field(fields, "PCAP").value)
            if find_field(fields, "PCAP") is not None else None,
            host_addresses=host,
            host_address=host_address,
            connection_group=state.connection_id,
        )
        game.roster = [state.connection_id]
        game.host_state = state
        game.members = [self.member(
            game, state.xuid, state.gamertag, state.connection_id,
            host_address, slot=0, team=0, state=state,
        )]
        # A game created by hand needs an opponent as much as a matchmade one
        # does, and for the same reason: with one player it can never start,
        # so the path cannot be walked to its end.
        opponent = test_opponent()
        if opponent:
            game.members.append(self.member(
                game, SYNTHETIC_PERSONA, opponent, SYNTHETIC_PERSONA,
                self.synthetic_address(game.host_address), slot=1, team=1,
            ))
            game.roster.append(SYNTHETIC_PERSONA)
        with self.matchmaking_lock:
            self.games[game_id] = game
        self.broadcast_census()
        self.logger.event(
            "game_created",
            connection=state.connection_id,
            game=game_id,
            persona=state.xuid,
            topology=value("NTOP"),
            game_type=value("GTYP"),
            protocol_version=value("VSTR"),
            capacity=value("PCAP"),
            settings=json_value(find_field(fields, "ATTR")),
            host_addresses=json_value(host) if host is not None else None,
        )
        return [
            response_frame(request, encode_fields([Field("GID", INTEGER, game_id)])),
            *self.game_setup_notifications(game),
        ]

    def join_game(self, request: bytes, state: ClientState) -> list[bytes]:
        """Somebody entering a game that already exists.

        The joiner brings everything needed with it -- its own `PNET` and its
        own `XSES` -- so nothing about the second console has to have been
        cached beforehand. It is added to the roster, told what it joined, and
        the people already in there are told somebody arrived.

        The response is four members, not the two the published tables give:
        `JEX` and `REX` list external players who came along, and are empty
        here because nobody brings a party to a two-player match.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]
        game_id = find_field(fields, "GID")
        game = self.games.get(int(game_id.value) if game_id is not None else 0)
        if game is None:
            self.logger.event(
                "join_refused",
                connection=state.connection_id,
                game=int(game_id.value) if game_id is not None else 0,
                reason="no such game",
            )
            return [response_frame(request)]

        network = find_field(fields, "PNET")
        address = None
        if network is not None and isinstance(network.value, tuple):
            active, valu = network.value
            if valu is not None:
                address = (active, valu)
        joined = self.member(
            game, state.xuid, state.gamertag, state.connection_id,
            address, slot=len(game.members), team=len(game.members) % 2,
            state=state,
        )
        game.members.append(joined)
        game.roster.append(state.connection_id)
        self.logger.event(
            "player_joined",
            connection=state.connection_id,
            game=game.game_id,
            persona=state.xuid,
            players=len(game.members),
        )
        self.tell_members(game, notification_frame(
            GAME_MANAGER,
            NOTIFY_PLAYER_JOINING,
            encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("PDAT", STRUCT, self.member_player(game, joined)),
            ]),
        ), skip=state)
        self.tell_members(game, notification_frame(
            GAME_MANAGER,
            NOTIFY_PLAYER_JOIN_COMPLETED,
            encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("PID", INTEGER, state.xuid),
            ]),
        ), skip=state)
        self.broadcast_census()
        return [
            response_frame(request, encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("JEX", LIST, (INTEGER, [])),
                Field("JGS", INTEGER, JOIN_STATE_JOINED_GAME),
                Field("REX", LIST, (INTEGER, [])),
            ])),
            notification_frame(
                GAME_MANAGER,
                NOTIFY_JOINING_PLAYER_INITIATE_CONNECTIONS,
                encode_fields(self.game_setup_payload(
                    game, session=0, result=MATCHMAKING_SUCCESS_JOINED_EXISTING_GAME
                )),
            ),
            notification_frame(
                GAME_MANAGER,
                NOTIFY_PLAYER_JOIN_COMPLETED,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("PID", INTEGER, state.xuid),
                ]),
            ),
        ]

    def advance_game_state(self, request: bytes, state: ClientState) -> list[bytes]:
        """The host moves the game on by itself.

        Same shape as notification 100 -- `{GID, GSTA}` -- because it is the
        same change said in the other direction. The server records it and
        tells everybody, which on a peer-to-peer game is the whole of its
        involvement.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]
        game_id = find_field(fields, "GID")
        wanted = find_field(fields, "GSTA")
        game = self.games.get(int(game_id.value) if game_id is not None else 0)
        if game is None or wanted is None:
            return [response_frame(request)]
        game.state = int(wanted.value)
        self.logger.event(
            "game_state_advanced",
            connection=state.connection_id,
            game=game.game_id,
            state=game.state,
        )
        return [
            response_frame(request),
            *self.tell_members(game, notification_frame(
                GAME_MANAGER,
                NOTIFY_GAME_STATE_CHANGE,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("GSTA", INTEGER, game.state),
                ]),
            ), skip=state),
        ]

    def tell_members(self, game: HostedGame, frame: bytes,
                     skip: ClientState | None = None) -> list[bytes]:
        """Push a frame to everybody in a game except the one who caused it.

        The one who caused it gets it in their reply instead, which keeps a
        client from being told twice about something it already knows.
        """
        for member in game.members:
            other = member.get("state")
            if other is None or other is skip:
                continue
            if other.push(frame):
                self.logger.frame("notification", other, frame)
        return []

    def leave_game(self, request: bytes, state: ClientState) -> list[bytes]:
        """A player walking out.

        Sent straight after "votre adversaire a quitté la partie", which is
        the console drawing the conclusion that the peer it was given never
        answered. `REAS` says why it left; the values of that enum are not
        read yet, so it is recorded rather than interpreted.

        A game nobody is in is not a game. The last one out takes it with
        them, which is also what keeps "En cours de partie" from climbing by
        one every time somebody tries.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]
        game_id = find_field(fields, "GID")
        reason = find_field(fields, "REAS")
        game = self.games.get(int(game_id.value) if game_id is not None else 0)
        if game is None:
            return [response_frame(request)]

        before = len(game.members)
        game.members = [
            member for member in game.members if member["persona"] != state.xuid
        ]
        game.roster = [
            peer for peer in game.roster if peer != state.connection_id
        ]
        game.mesh.pop(state.connection_id, None)
        remaining = [m for m in game.members if m.get("state") is not None]
        self.logger.event(
            "player_left",
            connection=state.connection_id,
            game=game.game_id,
            persona=state.xuid,
            reason=int(reason.value) if reason is not None else None,
            was=before,
            now=len(game.members),
        )
        if not remaining:
            with self.matchmaking_lock:
                self.games.pop(game.game_id, None)
            self.logger.event("game_emptied", game=game.game_id)
        self.broadcast_census()
        self.publish_relay_pairs()
        return [response_frame(request)]

    def destroy_game(self, request: bytes, state: ClientState) -> list[bytes]:
        """The host leaves, so the game does."""
        decoded = decode_frame(request)
        game_id = find_field(decoded["fields"], "GID")
        key = int(game_id.value) if game_id is not None else 0
        with self.matchmaking_lock:
            game = self.games.pop(key, None)
        if game is not None:
            self.logger.event(
                "game_destroyed",
                connection=state.connection_id,
                game=key,
                players=len(game.members),
            )
            self.broadcast_census()
        return [response_frame(request)]

    def finalize_game_creation(self, request: bytes, state: ClientState) -> list[bytes]:
        """The host hands over the XNet session it has just built.

        This is the frame that says the Blaze side is done and the network
        side has begun. The console only sends it once it has a game it
        believes in -- it never sent one until the roster carried `UID` and it
        could find itself in there -- and it carries `XNNC`, a sixteen-byte
        nonce, and `XSES`, the XSESSION_INFO holding the session key.

        Those two are not this server's to invent and never were. They are
        kept, echoed back in notification 115, and are exactly what a second
        console will need in order to dial this one.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]
        game_id = find_field(fields, "GID")
        nonce = find_field(fields, "XNNC")
        session = find_field(fields, "XSES")
        game = self.games.get(int(game_id.value) if game_id is not None else 0)
        if game is not None:
            if nonce is not None:
                game.xnet_nonce = bytes(nonce.value)
            if session is not None:
                game.xnet_session = bytes(session.value)
        self.logger.event(
            "game_session_finalised",
            connection=state.connection_id,
            game=int(game_id.value) if game_id is not None else 0,
            nonce_bytes=len(game.xnet_nonce) if game else 0,
            session_bytes=len(game.xnet_session) if game else 0,
        )
        replies = [response_frame(request)]
        if game is None:
            return replies

        updated = notification_frame(
            GAME_MANAGER,
            NOTIFY_GAME_SESSION_UPDATED,
            encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("XNNC", BINARY, game.xnet_nonce),
                Field("XSES", BINARY, game.xnet_session),
            ]),
        )
        # To everybody, not just back to the host that sent it.
        #
        # The host already has these -- it built them. The one who needs them
        # is the guest: XSES is the session key its console dials the host
        # with, and without it there is nothing it can do but wait. On 22
        # August a guest sat through three pairings saying nothing, and the
        # journal showed it leaving game 1 cleanly at the end -- so it had
        # registered the game perfectly well. It simply had no way to reach
        # the other console.
        #
        # And a guest never sends finalizeGameCreation of its own: creating
        # the session is the host's job. Its silence there was correct all
        # along, and was read as a fault for an hour.
        self.tell_members(game, updated, skip=state)
        replies.append(updated)
        return replies

    def update_mesh_connection(self, request: bytes, state: ClientState) -> list[bytes]:
        """"I can see this peer", or "I cannot".

        Sent per target, addressed by connection group rather than by player
        id. The console reported CONNECTED for itself and DISCONNECTED for the
        opponent this server made up -- which is the correct answer, and the
        first time anything here has been told the truth about the network
        rather than about the protocol.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]
        target = find_field(fields, "TCG")
        status = find_field(fields, "STAT")
        game_id = find_field(fields, "GID")
        game = self.games.get(int(game_id.value) if game_id is not None else 0)
        peer = int(target.value[2]) if target is not None else 0
        reported = int(status.value) if status is not None else MESH_DISCONNECTED
        if game is not None and peer:
            game.mesh[peer] = reported
        self.logger.event(
            "mesh_connection",
            connection=state.connection_id,
            game=game.game_id if game is not None else 0,
            # The value alone: the connection group as (component, type, id),
            # which is what a reader wants to compare against a persona.
            target=json_value(target.value) if target is not None else None,
            status=reported,
        )
        replies = [response_frame(request)]
        if game is not None:
            replies.extend(self.mesh_progress(game))
        return replies

    def mesh_progress(self, game: HostedGame) -> list[bytes]:
        """Start the match once everybody can see everybody.

        This is the server's one job in a peer-to-peer game: it is not in the
        data path, it only decides when the players have found each other. The
        real rule is that every peer has reported CONNECTED.

        Against an opponent this server invented, that can never happen -- the
        console correctly reports DISCONNECTED, because there is nothing at
        that address. So under the test-opponent flag the invented peer is
        counted as present, purely to find out what the title does next: a
        game it believes is playable is the only way to see whether it goes to
        the pitch, and that is the last thing Blaze can be asked.
        """
        if game.state != GAME_STATE_PRE_GAME:
            return []
        seen = dict(game.mesh)
        if test_opponent():
            seen[SYNTHETIC_PERSONA] = MESH_CONNECTED
        expected = [peer for peer in game.roster if peer]
        if len(expected) < 2:
            return []  # nobody to play against
        if any(seen.get(peer) != MESH_CONNECTED for peer in expected):
            return []
        peers = expected
        game.state = GAME_STATE_IN_GAME
        self.logger.event(
            "mesh_complete",
            game=game.game_id,
            peers=sorted(peers),
            synthetic=bool(test_opponent()),
        )
        return [
            notification_frame(
                GAME_MANAGER,
                NOTIFY_GAME_STATE_CHANGE,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("GSTA", INTEGER, game.state),
                ]),
            )
        ]

    def expire_matchmaking(self, state: ClientState, session: int) -> None:
        """End a search nobody could be found for.

        The client does not give up on its own. `DUR` says twenty seconds and
        the console honoured none of it: the search sat spinning for minutes
        with the server saying nothing, because in Blaze the duration is an
        instruction to the *matchmaker*, not a client-side timeout. So the
        server has to be the one that ends it.

        Ending it truthfully matters more than ending it quickly. There is
        genuinely no opponent on this server, so the result is SESSION_TIMED_OUT
        and the game gets to say so.
        """
        with self.matchmaking_lock:
            if self.matchmaking.get(state.connection_id) != session:
                return  # cancelled, or replaced by a newer search
            self.matchmaking.pop(state.connection_id, None)
            self.matchmaking_timers.pop(state.connection_id, None)
            pending = self.searches.pop(state.connection_id, None)
            draft = pending["draft"] if pending else None

        opponent = test_opponent()
        if opponent and draft is not None:
            self.find_synthetic_opponent(state, session, draft, opponent)
            return
        frame = notification_frame(
            GAME_MANAGER,
            NOTIFY_MATCHMAKING_FAILED,
            encode_fields(
                [
                    Field("MAXF", INTEGER, 0),
                    Field("MSID", INTEGER, session),
                    Field("RSLT", INTEGER, MATCHMAKING_SESSION_TIMED_OUT),
                    Field("USID", INTEGER, state.xuid),
                ]
            ),
        )
        delivered = state.push(frame)
        if delivered:
            self.logger.frame("notification", state, frame)
        self.broadcast_census()
        self.logger.event(
            "matchmaking_timed_out",
            connection=state.connection_id,
            session=session,
            delivered=delivered,
        )

    def waiting_test_host(self, arriving: HostedGame) -> dict | None:
        """Un hôte inventé, déjà dans la file quand la vraie console arrive.

        Il porte la version de protocole de celui qui arrive -- sans quoi ils
        ne seraient pas compatibles et rien ne se passerait -- et une adresse
        bien formée qui ne mène nulle part. Son `since` est antérieur, donc
        c'est lui qui héberge, et la vraie console est l'invitée.
        """
        name = test_host()
        if not name:
            return None
        pretend = ClientState(-1, ("0.0.0.0", 0), 0)
        pretend.xuid = SYNTHETIC_PERSONA
        pretend.gamertag = name
        pretend.authenticated = True
        # Pas de canal : tout ce qu'on lui pousse tombe dans le vide, ce qui
        # est exactement ce qu'il faut.
        draft = HostedGame(
            game_id=0,
            persona_id=SYNTHETIC_PERSONA,
            gamertag=name,
            protocol_version=arriving.protocol_version,
            topology=arriving.topology,
            settings=arriving.settings,
            connection_group=SYNTHETIC_PERSONA,
        )
        active, valu = self.synthetic_address()
        if mirror_invented_address():
            worn = self.borrowed_address(arriving.host_address)
            if worn is not None:
                active, valu = worn
                self.logger.event(
                    "test_host_borrowed_address",
                    persona=arriving.persona_id,
                    synthetic=True,
                )
        draft.host_address = (active, valu)
        draft.host_addresses = Field("HNET", LIST, (STRUCT, [(active, valu.value)]))
        return {
            "state": pretend,
            "session": 0,
            "draft": draft,
            "since": time.monotonic() - 1.0,
        }

    def publish_relay_pairs(self) -> None:
        """Dire au relais quelles adresses publiques vont ensemble.

        Réécrit en entier à chaque changement plutôt que modifié : le fichier
        est minuscule, et un relais qui lit un fichier à moitié écrit
        renverrait le trafic d'un joueur à un inconnu.
        """
        path = relay_pairs_path()
        if path is None:
            return
        pairs = []
        for game in self.games.values():
            addresses = [
                member["state"].peer[0]
                for member in game.members
                if member.get("state") is not None and member["state"].peer
            ]
            if len(addresses) == 2 and addresses[0] != addresses[1]:
                pairs.append(addresses)
        document = json.dumps({"pairs": pairs}, sort_keys=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(document, encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            self.logger.event("relay_pairs_unwritable", error=str(error))
            return
        self.logger.event("relay_pairs_published", pairs=pairs)

    def pair_searches(self, state: ClientState) -> list[bytes]:
        """Two consoles looking for a game at the same time are each other's.

        This is the whole of real matchmaking here, and it needs nothing that
        has not already been proved on hardware: both consoles sent
        `startMatchmaking` carrying their own address, so the server has both
        addresses and can put them in one game and hand each the other's.

        The one who was already waiting hosts. That is not arbitrary -- the
        host is the one whose XNet session the other will dial, and the one
        who has been waiting has been waiting because nobody was there, so it
        is the one that has to be dialled *into*.

        Compatibility is `GVER`, the game protocol version string. Two builds
        that disagree on it cannot play each other, and letting them try would
        produce a match that fails in the network layer for a reason that has
        nothing to do with the network.
        """
        with self.matchmaking_lock:
            mine = self.searches.get(state.connection_id)
            if mine is None:
                return []
            # Un hôte de test attend déjà, s'il y en a un et qu'il n'y a
            # personne d'autre.
            if len(self.searches) == 1:
                pretend = self.waiting_test_host(mine["draft"])
                if pretend is not None:
                    self.searches[pretend["state"].connection_id] = pretend
            partner = None
            for connection, other in self.searches.items():
                if connection == state.connection_id:
                    continue
                if other["state"].xuid == state.xuid:
                    continue  # the same player on a second connection
                if other["draft"].protocol_version != mine["draft"].protocol_version:
                    continue
                partner = other
                break
            if partner is None:
                return []
            for entry in (mine, partner):
                self.searches.pop(entry["state"].connection_id, None)
            # Whoever was waiting hosts -- by when the search began, not by
            # which of the two pairing timers happened to fire first. Both are
            # armed, and with two consoles pressing within a second of each
            # other they fire together; picking by timer made the host
            # whichever one lost that race.
            host, guest = sorted((mine, partner), key=lambda entry: entry["since"])
            game = host["draft"]
            game.game_id = self.next_game_id
            self.next_game_id += 1
            self.games[game.game_id] = game

        for entry in (host, guest):
            self.forget_matchmaking(entry["state"].connection_id)

        game.host_state = host["state"]
        game.roster = [host["state"].connection_id, guest["state"].connection_id]
        game.members = [
            self.member(game, host["state"].xuid, host["state"].gamertag,
                        host["state"].connection_id, host["draft"].host_address,
                        slot=0, team=0, state=host["state"]),
            self.member(game, guest["state"].xuid, guest["state"].gamertag,
                        guest["state"].connection_id, guest["draft"].host_address,
                        slot=1, team=1, state=guest["state"]),
        ]
        self.logger.event(
            "matchmaking_paired",
            game=game.game_id,
            host=host["state"].xuid,
            guest=guest["state"].xuid,
            protocol_version=game.protocol_version,
        )

        # The guest's setup is built *before* the host's notifications, because
        # building the host's advances the game to PRE_GAME. Built after, the
        # guest was handed a game already in the state it was about to be told
        # to enter, while the host saw it in INITIALIZING and watched it move.
        # Two clients given different games is not a symmetry worth having,
        # whether or not it is what stopped the guest.
        guest_setup = notification_frame(
            GAME_MANAGER,
            NOTIFY_GAME_SETUP,
            encode_fields(self.game_setup_payload(
                game, session=guest["session"],
                result=MATCHMAKING_SUCCESS_JOINED_EXISTING_GAME,
                viewer=guest["state"])),
        )
        for frame in self.game_setup_notifications(
            game, session=host["session"], viewer=host["state"]
        ):
            if host["state"].push(frame):
                self.logger.frame("notification", host["state"], frame)
        # The guest gets notification 20, not 22.
        #
        # 22 was the family convention -- `NotifyJoiningPlayerInitiateConnections`
        # sounds exactly like what a joiner should receive -- and it was never
        # read out of the client's dispatch. On 22 August two consoles were
        # paired across the Atlantic and the journal settled it: the host got
        # 20 and answered two seconds later with its XNet session; the guest
        # got 22 and never said another word. One side connected, and it was
        # the side that got 20.
        #
        # So both sides are set up the same way and differ only in their
        # setup reason, which is the thing that actually says who joined what.
        for frame in (
            guest_setup,
            notification_frame(
                GAME_MANAGER,
                NOTIFY_PLATFORM_HOST_INITIALIZED,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("PHID", INTEGER, game.persona_id),
                    Field("PHST", INTEGER, 0),
                ]),
            ),
            notification_frame(
                GAME_MANAGER,
                NOTIFY_PLAYER_JOIN_COMPLETED,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("PID", INTEGER, guest["state"].xuid),
                ]),
            ),
            notification_frame(
                GAME_MANAGER,
                NOTIFY_GAME_STATE_CHANGE,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("GSTA", INTEGER, game.state),
                ]),
            ),
        ):
            if guest["state"].push(frame):
                self.logger.frame("notification", guest["state"], frame)
        # And the host hears that somebody arrived.
        self.tell_members(game, notification_frame(
            GAME_MANAGER,
            NOTIFY_PLAYER_JOINING,
            encode_fields([
                Field("GID", INTEGER, game.game_id),
                Field("PDAT", STRUCT, self.member_player(game, game.members[1])),
            ]),
        ), skip=guest["state"])

        # Un hôte inventé n'enverra jamais son finalizeGameCreation, puisqu'il
        # n'a pas de console pour fabriquer une session. Le serveur la pose à
        # sa place, sinon l'invité ne recevrait pas la notification 115 et la
        # reproduction serait incomplète là où ça compte le plus.
        if host["state"].xuid == SYNTHETIC_PERSONA:
            game.xnet_nonce = bytes(range(16))
            game.xnet_session = bytes(range(256))
            updated = notification_frame(
                GAME_MANAGER,
                NOTIFY_GAME_SESSION_UPDATED,
                encode_fields([
                    Field("GID", INTEGER, game.game_id),
                    Field("XNNC", BINARY, game.xnet_nonce),
                    Field("XSES", BINARY, game.xnet_session),
                ]),
            )
            self.tell_members(game, updated)
            self.logger.event(
                "synthetic_host_session",
                game=game.game_id,
                synthetic=True,
            )

        self.broadcast_census()
        self.publish_relay_pairs()
        return []

    def find_synthetic_opponent(
        self, state: ClientState, session: int, draft: HostedGame, opponent: str
    ) -> None:
        """End a search by finding somebody who does not exist.

        Everything below the address is real protocol: the game is built from
        the console's own search parameters, the setup reason says a search
        found it, and the opponent arrives as the two events a client tracks
        players by. Only the opponent is made up, and it is made up on purpose
        -- until a second console exists, this is the only way to learn where
        the title stops: in Blaze, or in the network underneath it.
        """
        draft.roster = [draft.connection_group, SYNTHETIC_PERSONA]
        draft.host_state = state
        draft.members = [
            self.member(draft, state.xuid, state.gamertag,
                        draft.connection_group, draft.host_address,
                        slot=0, team=0, state=state),
            self.member(draft, SYNTHETIC_PERSONA, opponent, SYNTHETIC_PERSONA,
                        self.synthetic_address(draft.host_address),
                        slot=1, team=1),
        ]
        with self.matchmaking_lock:
            draft.game_id = self.next_game_id
            self.next_game_id += 1
            self.games[draft.game_id] = draft
        frames = [
            *self.game_setup_notifications(draft, session=session),
            *self.opponent_notifications(draft),
        ]
        delivered = all(state.push(frame) for frame in frames)
        for frame in frames:
            self.logger.frame("notification", state, frame)
        self.logger.event(
            "matchmaking_found_synthetic_opponent",
            connection=state.connection_id,
            session=session,
            game=draft.game_id,
            opponent=opponent,
            persona=SYNTHETIC_PERSONA,
            delivered=delivered,
            synthetic=True,
        )
        self.broadcast_census()

    def schedule_matchmaking_timeout(
        self, state: ClientState, session: int, duration_ms: int
    ) -> threading.Timer:
        # A floor, because a client that asked for a very short search would
        # otherwise be told "no opponent" before its own screen had drawn.
        seconds = max(2.0, float(duration_ms or 20000) / 1000.0)
        # And a window that can outlast what the client asked for, so two
        # people on two consoles do not have to press within the same twenty
        # seconds of each other.
        seconds = max(seconds, search_window())
        timer = threading.Timer(seconds, self.expire_matchmaking, (state, session))
        timer.daemon = True
        with self.matchmaking_lock:
            self.matchmaking_timers[state.connection_id] = timer
        timer.start()
        return timer

    def start_matchmaking(self, request: bytes, state: ClientState) -> list[bytes]:
        """The first thing FIFA 14 ever said to GameManager on this server.

        Captured 21 August 2026 from Face-à-Face, and it volunteers nearly
        everything a matchmaker needs to know:

            NTOP 130    PEER_TO_PEER_FULL_MESH -- the match itself runs
                        console to console. This server is the matchmaker and
                        an address relay; it is not in the game's data path.
            GVER        the game protocol version string both consoles must
                        agree on before they will play each other.
            DUR 20000   the client gives the search twenty seconds.
            PNET        an XboxClientAddress union carrying the console's
                        XNADDR -- LAN address, online address, port 3074 and
                        the machine's MAC -- which is precisely the blob the
                        other console will need, verbatim, to dial it.

        None of that is acted on yet. What is answered here is the session id
        and an async status, which is the smallest reply that turns a silent
        hang into a search the client is actually running -- and it is what
        proves the notification path works before anything is built on it.
        """
        decoded = decode_frame(request)
        fields = decoded["fields"]
        session = self.open_matchmaking_session(state)

        def value(label: str, fallback: Any = None) -> Any:
            found = find_field(fields, label)
            return found.value if found is not None else fallback

        # The address is kept in the journal rather than in memory on purpose:
        # relaying it needs a second console, and there is not one yet. When
        # there is, this line is the record of what has to be relayed.
        network = find_field(fields, "PNET")
        self.logger.event(
            "matchmaking_started",
            connection=state.connection_id,
            session=session,
            persona=state.xuid,
            topology=value("NTOP"),
            mode=value("MODE"),
            duration_ms=value("DUR"),
            game_version=value("GVER"),
            network=json_value(network) if network is not None else None,
        )
        # The search carries everything a game needs: the console's address,
        # the topology, the protocol version, the settings. Kept against the
        # session so that finding somebody does not mean inventing a game.
        network = find_field(fields, "PNET")
        draft = HostedGame(
            game_id=0,
            persona_id=state.xuid,
            gamertag=state.gamertag,
            protocol_version=str(value("GVER", "")),
            topology=int(value("NTOP", 0) or 0),
            settings=int(value("GSET", 0) or 0),
            connection_group=state.connection_id,
        )
        if network is not None and isinstance(network.value, tuple):
            active, valu = network.value
            if valu is not None:
                draft.host_address = (active, valu)
                # In a list a union's members sit inline, with no VALU.
                draft.host_addresses = Field(
                    "HNET", LIST, (STRUCT, [(active, valu.value)])
                )
        with self.matchmaking_lock:
            self.searches[state.connection_id] = {
                "state": state,
                "session": session,
                "draft": draft,
                # When this search began, which is what decides who hosts.
                "since": time.monotonic(),
            }

        self.schedule_matchmaking_timeout(state, session, value("DUR", 20000))
        self.broadcast_census()
        # Somebody else may already be waiting, in which case neither of them
        # has to wait any longer -- but not from inside this handler.
        #
        # Pairing pushes NotifyGameSetup straight down the socket, and this
        # reply has not been written yet: the connection loop writes it after
        # the handler returns. So on 22 August two consoles were paired
        # correctly and both carried on searching, because the guest was told
        # about a game before it had been told which search the game belonged
        # to -- notification 22 and 30 on the wire ahead of its own MSID.
        #
        # Half a second is enough for the reply to be written and is invisible
        # next to a search that runs for minutes.
        pairing = threading.Timer(0.5, self.pair_searches, (state,))
        pairing.daemon = True
        pairing.start()
        return [
            response_frame(request, encode_fields([Field("MSID", INTEGER, session)])),
            notification_frame(
                GAME_MANAGER,
                NOTIFY_MATCHMAKING_ASYNC_STATUS,
                encode_fields(
                    [
                        # No estimates to report -- there is nobody else on
                        # this server to be matched against, so the list of
                        # per-rule status is genuinely empty rather than
                        # omitted.
                        Field("ASIL", LIST, (STRUCT, [])),
                        Field("MSID", INTEGER, session),
                        Field("USID", INTEGER, state.xuid),
                    ]
                ),
            ),
        ]

    def cancel_matchmaking(self, request: bytes, state: ClientState) -> list[bytes]:
        """Back out of the search screen.

        This is the half that can be proved with one console and no opponent.
        A search that starts and then ends cleanly when the player backs out
        exercises the session id, the notification id, the enum encoding and
        the push path all at once -- and the console says whether it worked
        by either returning to the menu or hanging.
        """
        session = self.close_matchmaking_session(state)
        self.broadcast_census()
        self.logger.event(
            "matchmaking_cancelled",
            connection=state.connection_id,
            session=session,
            persona=state.xuid,
        )
        return [
            response_frame(request),
            notification_frame(
                GAME_MANAGER,
                NOTIFY_MATCHMAKING_FAILED,
                encode_fields(
                    [
                        Field("MAXF", INTEGER, 0),
                        Field("MSID", INTEGER, session),
                        Field("RSLT", INTEGER, MATCHMAKING_SESSION_CANCELED),
                        Field("USID", INTEGER, state.xuid),
                    ]
                ),
            ),
        ]

    def user_identification(self, xuid: int, name: str) -> list[Field]:
        """One player, in the shape both sides of this protocol use for them.

        The Xbox login and the session notifications each built this inline,
        and the two lookups below need the same thing. Two spellings of one
        player drifting apart is how a squad screen came back with eleven
        blank cards once already, so there is one.
        """
        return [
            Field("AID", INTEGER, xuid),
            Field("ALOC", INTEGER, LOCALE),
            Field("EXID", INTEGER, xuid),
            Field("ID", INTEGER, xuid),
            Field("NAME", STRING, name),
        ]

    def session_extended_data(self) -> list[Field]:
        """The extended session data that rides along with a user."""
        return [
            Field("BPS", STRING, "ams"),
            Field("CTY", STRING, "FR"),
            Field("HWFG", INTEGER, 0),
            Field("UATT", INTEGER, 0),
        ]

    def user_data(self, xuid: int, name: str) -> list[Field]:
        """A looked-up player: the pair NotifyUserAdded already sends."""
        return [
            Field("DATA", STRUCT, self.session_extended_data()),
            Field("USER", STRUCT, self.user_identification(xuid, name)),
        ]

    def resolve_persona(self, persona_id: int, state: ClientState) -> tuple[int, str]:
        """Who a nucleus id belongs to, or the asking connection itself.

        A lookup for id 0 -- what the title sends when it is asking about
        itself -- resolves to the connection making it. A real id is read
        from the account store that persona owns, so that with a second
        player on this server their name comes back theirs rather than
        whoever logged in last.
        """
        if not persona_id or persona_id == state.xuid:
            return state.xuid, state.gamertag
        stored_id, stored_name = self.accounts.get(persona_id).load_identity()
        if stored_id == persona_id and stored_name:
            return stored_id, stored_name
        return 0, ""

    def lookup_user(self, request: bytes, state: ClientState) -> bytes:
        """One user, by identification.

        The title sends a UserIdentification with everything zeroed but the
        name: it is asking about itself, on the way to a screen that wants to
        print its own gamertag. Of the nine routes this server was answering
        with a fieldless success, this is the one where the empty reply
        plainly threw away something the server had in hand.
        """
        decoded = decode_frame(request)
        asked = find_field(decoded["fields"], "ID")
        persona = int(asked.value) if asked is not None else 0
        xuid, name = self.resolve_persona(persona, state)
        if not xuid:
            return response_frame(request)
        return response_frame(request, encode_fields(self.user_data(xuid, name)))

    def lookup_users(self, request: bytes, state: ClientState) -> bytes:
        """The same, in bulk.

        `ULST` on the way in is a list of identifications, so it is `ULST` on
        the way back carrying user data. Entries this server cannot name are
        left out rather than answered with somebody else's identity: a lookup
        that quietly returns the wrong player is worse than one that returns
        nobody.
        """
        decoded = decode_frame(request)
        requested = find_field(decoded["fields"], "ULST")
        listed = requested.value[1] if requested is not None else []
        entries: list[list[Field]] = []
        for entry in listed or []:
            identifier = find_field(entry, "ID") if isinstance(entry, list) else None
            persona = int(identifier.value) if identifier is not None else 0
            xuid, name = self.resolve_persona(persona, state)
            if xuid:
                entries.append(self.user_data(xuid, name))
        return response_frame(
            request,
            encode_fields([Field("ULST", LIST, (STRUCT, entries))]),
        )

    def session_notifications(self, state: ClientState) -> list[bytes]:
        """The three notifications that tell a connection whose it is.

        Sent after a login, and after a session is resumed by key on a
        second connection -- the EAS FC module opens one of its own and
        asks to be attached to the session the title already has. Without
        these it is acknowledged and then never told who it is, which is
        what "EAS FC non connecté" means from its side.
        """
        now = int(time.time())
        user_identification = self.user_identification(state.xuid, state.gamertag)
        # FIFA 14 maps notification 8 to UserAuthenticated, but its payload is
        # the executable's 0x88-byte UserSessionLoginInfo, not the smaller
        # SUBS/BUID shape found in another legacy Blaze schema.  The native
        # callback at 0x82EE6150 consumes BUID, UID, XREF, DSNM and ALOC
        # directly; omitting them creates an anonymous second user object.
        user_authenticated = notification_frame(
            USER_SESSIONS,
            8,
            encode_fields(
                [
                    Field("ALOC", INTEGER, LOCALE),
                    Field("BUID", INTEGER, state.xuid),
                    Field("DSNM", STRING, state.gamertag),
                    Field("FRST", INTEGER, 0),
                    Field("KEY", STRING, f"offline-{state.xuid:x}"),
                    Field("LAST", INTEGER, 0),
                    Field("LLOG", INTEGER, now),
                    Field("MAIL", STRING, state.email),
                    Field("PID", INTEGER, state.xuid),
                    Field("PLAT", INTEGER, 1),
                    Field("UID", INTEGER, state.xuid),
                    Field("USTP", INTEGER, 1),
                    Field("XREF", INTEGER, state.xuid),
                ]
            ),
        )

        # The supported executable constructs a 0x188-byte NotifyUserAdded
        # containing DATA followed by USER.  DATA must be present even though
        # notification 1 subsequently refreshes the same extended-session
        # state; omitting it leaves the local User identity uncommitted.
        user_added = notification_frame(
            USER_SESSIONS,
            2,
            encode_fields(
                [
                    Field(
                        "DATA",
                        STRUCT,
                        [
                            *self.session_extended_data(),
                        ],
                    ),
                    Field("USER", STRUCT, user_identification),
                ]
            ),
        )
        extended_data = notification_frame(
            USER_SESSIONS,
            1,
            encode_fields(
                [
                    Field(
                        "DATA",
                        STRUCT,
                        [
                            *self.session_extended_data(),
                        ],
                    ),
                    # FIFA 14's retail UserSessionExtendedDataUpdate is a
                    # 0x140-byte structure with DATA, SUBS and USID.  SUBS is
                    # not part of the embedded extended-data object: it is a
                    # top-level boolean at +0x138.  Omitting it leaves the
                    # local user session present but unsubscribed.
                    Field("SUBS", INTEGER, 1),
                    Field("USID", INTEGER, state.xuid),
                ]
            ),
        )
        return [user_authenticated, user_added, extended_data]

    def account(self, request: bytes, state: ClientState) -> bytes:
        payload = encode_fields(
            [
                Field("ANON", INTEGER, 0),
                Field("ASRC", STRING, "300294"),
                Field("CO", STRING, "FR"),
                Field("CTRY", STRING, "FR"),
                Field("DOB", STRING, "1980-01-01"),
                Field("DTCR", STRING, "2013-01-01"),
                Field("MAIL", STRING, state.email),
                Field("STAT", INTEGER, 1),
                Field("STAS", INTEGER, 1),
                Field("UID", INTEGER, state.xuid),
            ]
        )
        return response_frame(request, payload)

    def cardhouse_login(self, request: bytes) -> bytes:
        # Mirrors Zamboni's new LoginResponse(): value fields are emitted while
        # nullable NAME/ABBR/CVER remain absent, marking an uncreated club.
        payload = encode_fields(
            [
                Field("BNUS", INTEGER, 0),
                Field("DRRC", INTEGER, 0),
                Field("DRRL", INTEGER, 0),
                Field("DRRO", INTEGER, 0),
                Field("DRRW", INTEGER, 0),
                Field("RWRD", INTEGER, 0),
                Field("TNOW", INTEGER, 0),
                Field("TRBS", INTEGER, 0),
                Field("UID", INTEGER, 0),
            ]
        )
        return response_frame(request, payload)

    def cardhouse_no_player(self, request: bytes) -> bytes:
        return response_frame(
            request,
            b"",
            error=CARDHOUSE_ERR_NO_PLAYER_INFO_HEADER,
            message_type=ERROR_REPLY,
        )

    def sponsored_events_url(self, request: bytes) -> bytes:
        return response_frame(
            request,
            encode_fields(
                [Field("URL", STRING, f"{self.identity_base}/sponsored-events")]
            ),
        )

    def telemetry_server(self, request: bytes) -> bytes:
        """Return the Blaze 3 GetTelemetryServerResponse used by FIFA 14."""

        return response_frame(
            request,
            encode_fields(
                [
                    Field("ADRS", STRING, self.advertise),
                    Field("ANON", INTEGER, 0),
                    Field("DISA", STRING, "disa"),
                    Field("FILT", STRING, "filt"),
                    Field("LOC", INTEGER, 1718765138),
                    Field("NOOK", STRING, "nook"),
                    Field("PORT", INTEGER, 6767),
                    Field("SDLY", INTEGER, 10),
                    Field("SESS", STRING, "id"),
                    Field("SKEY", STRING, "key"),
                    Field("SPCT", INTEGER, 10),
                    Field("STIM", STRING, "true"),
                ]
            ),
        )

    def clubs_component_settings(self, request: bytes) -> bytes:
        # The nullable lists in Zamboni's default ClubsComponentSettings are
        # absent on the wire.  The six non-nullable scalars are still emitted.
        return response_frame(
            request,
            encode_fields(
                [
                    Field("CLDS", INTEGER, 0),
                    Field("MXEV", INTEGER, 0),
                    Field("MXRV", INTEGER, 0),
                    Field("PUHR", INTEGER, 0),
                    Field("SOVR", INTEGER, 0),
                    Field("STRT", INTEGER, 0),
                ]
            ),
        )

    def period_ids(self, request: bytes) -> bytes:
        # BlazeSDK PeriodIds contains fourteen non-nullable integer fields.
        labels = (
            "DBUF", "DHOU", "DLY", "DRET", "MBUF", "MDAY", "MHOU",
            "MLY", "MRET", "WBUF", "WDAY", "WHOU", "WLY", "WRET",
        )
        return response_frame(
            request,
            encode_fields([Field(label, INTEGER, 0) for label in labels]),
        )

    def osdk_settings(self, request: bytes) -> bytes:
        # ZamboniCommonComponents exposes one string setting used by the
        # legacy ticker.  Default nullable strings are deliberately omitted.
        setting = [
            Field("ID", STRING, "O_TKfilter"),
            Field("LOCF", INTEGER, 0),
            Field("TOGG", INTEGER, 0),
        ]
        return response_frame(
            request,
            encode_fields([Field("LSST", LIST, (STRUCT, [setting]))]),
        )

    def osdk_setting_groups(self, request: bytes) -> bytes:
        group = [
            Field("ID", STRING, "O_SG_TCKR"),
            Field("LSET", LIST, (STRING, ["O_TKfilter"])),
        ]
        return response_frame(
            request,
            encode_fields([Field("LGRP", LIST, (STRUCT, [group]))]),
        )

    def handle(self, request: bytes, state: ClientState) -> list[bytes]:
        decoded = decode_frame(request)
        route = (decoded["component"], decoded["command"])

        if route == (REDIRECTOR, REDIRECTOR_GET_SERVER_INSTANCE):
            return [self.redirector(request)]
        if route == (UTIL, UTIL_PREAUTH):
            return [self.preauth(request, state)]
        if route == (UTIL, UTIL_PING):
            return [self.ping(request)]
        if route == (UTIL, UTIL_POSTAUTH):
            return [self.postauth(request, state)]
        if route == (UTIL, UTIL_GET_TELEMETRY_SERVER):
            return [self.telemetry_server(request)]
        if route == (UTIL, UTIL_USER_SETTINGS_LOAD):
            key_field = find_field(decoded["fields"], "KEY")
            key = str(key_field.value) if key_field is not None else ""
            value = self.accounts.get(state.xuid).load_setting(key)
            self.logger.event(
                "user_setting_load",
                connection=state.connection_id,
                key=key,
                value=value,
            )
            return [
                response_frame(
                    request,
                    encode_fields([Field("DATA", STRING, value)]),
                )
            ]
        if route == (UTIL, UTIL_USER_SETTINGS_SAVE):
            key_field = find_field(decoded["fields"], "KEY")
            data_field = find_field(decoded["fields"], "DATA")
            key = str(key_field.value) if key_field is not None else ""
            value = str(data_field.value) if data_field is not None else ""
            if key:
                self.accounts.get(state.xuid).save_setting(key, value)
            self.logger.event(
                "user_setting_save",
                connection=state.connection_id,
                key=key,
                value=value,
            )
            return [response_frame(request)]
        if route == (UTIL, UTIL_FETCH_CONFIG):
            return [self.fetch_config(request, decoded["fields"], state)]
        if route == (UTIL, UTIL_USER_SETTINGS_LOAD_ALL):
            settings = self.accounts.get(state.xuid).load_all_settings()
            self.logger.event(
                "user_settings_load_all",
                connection=state.connection_id,
                settings=dict(settings),
            )
            return [
                response_frame(
                    request,
                    encode_fields(
                        [Field("SMAP", MAP, (STRING, STRING, settings))]
                    ),
                )
            ]
        if route in {
            (UTIL, UTIL_SET_CLIENT_DATA),
            (UTIL, UTIL_SET_CLIENT_METRICS),
            (UTIL, UTIL_SET_CONNECTION_STATE),
            (USER_SESSIONS, USER_UPDATE_HARDWARE_FLAGS),
            (USER_SESSIONS, USER_UPDATE_NETWORK_INFO),
        }:
            return [response_frame(request)]
        if route == (UTIL, UTIL_LOCALIZE_STRINGS):
            # Returning no entries is sufficient for the bootstrap and avoids
            # making assumptions about the request's list schema.
            return [response_frame(request, encode_fields([empty_map("LOCL")]))]

        if route == (AUTHENTICATION, AUTH_XBOX_LOGIN):
            return self.xbox_login(request, state)
        if route == (AUTHENTICATION2, AUTH2_LOGIN):
            return self.authentication2_login(request, state, decoded["fields"])
        if route == (AUTHENTICATION, AUTH_LOGOUT):
            state.authenticated = False
            return [response_frame(request)]
        if route == (AUTHENTICATION, AUTH_GET_ACCOUNT):
            return [self.account(request, state)]
        if route == (AUTHENTICATION, AUTH_UPDATE_ACCOUNT):
            optq_field = find_field(decoded["fields"], "OPTQ")
            opts_field = find_field(decoded["fields"], "OPTS")
            optq = int(optq_field.value) if optq_field is not None else 0
            opts = int(opts_field.value) if opts_field is not None else 0
            self.accounts.get(state.xuid).save_account_preferences(optq, opts)
            self.logger.event(
                "account_preferences_save",
                connection=state.connection_id,
                optq=optq,
                opts=opts,
            )
            return [response_frame(request)]
        if route in {
            (AUTHENTICATION, AUTH_HAS_ENTITLEMENT),
            (AUTHENTICATION, AUTH_LIST_ENTITLEMENTS),
            (AUTHENTICATION, AUTH_LIST_USER_ENTITLEMENTS_2),
            (AUTHENTICATION, AUTH_GET_TOS_INFO),
        }:
            return [response_frame(request)]

        if route == (CARDHOUSE, CARDHOUSE_LOGIN):
            return [self.cardhouse_login(request)]
        if route == (CARDHOUSE, CARDHOUSE_GAMER_GET_INFO):
            return [self.cardhouse_no_player(request)]
        if route in {
            (CARDHOUSE, CARDHOUSE_LOGOUT),
            (CARDHOUSE, CARDHOUSE_GAMER_SET_INFO),
            (CARDHOUSE, CARDHOUSE_GET_CONFIG),
            (CARDHOUSE, CARDHOUSE_GET_DECK_INFO),
            (CARDHOUSE, CARDHOUSE_GET_SQUAD_LIST),
        }:
            return [response_frame(request)]

        if route == (SPONSORED_EVENTS, SPONSORED_EVENTS_GET_EVENTS_URL):
            return [self.sponsored_events_url(request)]

        if route == (MESSAGING, MESSAGING_FETCH_MESSAGES):
            return [
                response_frame(
                    request,
                    encode_fields([Field("MCNT", INTEGER, 0)]),
                )
            ]
        if route == (MESSAGING, MESSAGING_GET_MESSAGES):
            return [response_frame(request)]
        if route == (ASSOCIATION_LISTS, ASSOCIATION_GET_LISTS):
            return [
                response_frame(
                    request,
                    encode_fields([Field("LMAP", LIST, (STRUCT, []))]),
                )
            ]
        if route == (CLUBS, CLUBS_GET_COMPONENT_SETTINGS):
            return [self.clubs_component_settings(request)]
        if route == (CLUBS, CLUBS_GET_INVITATIONS):
            return [
                response_frame(
                    request,
                    encode_fields([Field("CIST", LIST, (STRUCT, []))]),
                )
            ]
        if route == (STATS, STATS_GET_KEY_SCOPES_MAP):
            return [
                response_frame(
                    request,
                    encode_fields([Field("KSIT", MAP, (STRING, STRUCT, []))]),
                )
            ]
        if route == (STATS, STATS_GET_STAT_GROUP_LIST):
            return [
                response_frame(
                    request,
                    encode_fields([Field("GRPS", LIST, (STRUCT, []))]),
                )
            ]
        if route == (STATS, STATS_GET_PERIOD_IDS):
            return [self.period_ids(request)]
        if route == (GAME_MANAGER, GAME_MANAGER_JOIN_GAME):
            return self.join_game(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_ADVANCE_GAME_STATE):
            return self.advance_game_state(request, state)
        if route in {
            (GAME_MANAGER, GAME_MANAGER_LEAVE_GAME_BY_GROUP),
            (GAME_MANAGER, GAME_MANAGER_REMOVE_PLAYER_CMD),
        }:
            return self.leave_game(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_DESTROY_GAME):
            return self.destroy_game(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_FINALIZE_GAME_CREATION):
            return self.finalize_game_creation(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_UPDATE_MESH_CONNECTION):
            return self.update_mesh_connection(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_CREATE_GAME):
            return self.create_game(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_START_MATCHMAKING):
            return self.start_matchmaking(request, state)
        if route == (GAME_MANAGER, GAME_MANAGER_CANCEL_MATCHMAKING):
            return self.cancel_matchmaking(request, state)
        if route == (USER_SESSIONS, USER_SESSIONS_LOOKUP_USER):
            return [self.lookup_user(request, state)]
        if route == (USER_SESSIONS, USER_SESSIONS_LOOKUP_USERS):
            return [self.lookup_users(request, state)]
        # Routes the title sends and then carries on from whatever comes back.
        #
        # They were already answered exactly like this: the fallback at the
        # bottom of this method returns the same fieldless success. So nothing
        # about the game changes by naming them here. What changes is the
        # journal -- each was written as `unknown_route` several times a
        # session, and that list is meant to be what is left to build. Nine
        # known-and-harmless routes sitting at the top of it would bury the
        # first GameManager line the day it finally appears, and that line is
        # the entire reason for looking at the list.
        if route == (CENSUS_DATA, CENSUS_SUBSCRIBE):
            self.census[state.connection_id] = state
            self.schedule_census_pulse()
            # Pushed with the reply so the first paint already has numbers.
            return [response_frame(request), self.census_notification()]
        if route == (CENSUS_DATA, CENSUS_UNSUBSCRIBE):
            self.census.pop(state.connection_id, None)
            self.schedule_census_pulse()
            return [response_frame(request)]
        if route in {
            (ROOMS, ROOMS_SELECT_VIEW_UPDATES),
            (ROOMS, ROOMS_SELECT_CATEGORY_UPDATES),
            (ROOMS, ROOMS_SET_ENABLED),
            (STATS, STATS_GET_LEADERBOARD_GROUP),
            (STATS, STATS_GET_CENTERED_LEADERBOARD),
            (AUTHENTICATION, AUTH_LIST_ENTITLEMENTS_FOR_PERSONA),
            (AUTHENTICATION, AUTH_GRANT_ENTITLEMENT),
        }:
            return [response_frame(request)]
        if route == (OSDK_SETTINGS, OSDK_SETTINGS_FETCH_SETTINGS):
            return [self.osdk_settings(request)]
        if route == (OSDK_SETTINGS, OSDK_SETTINGS_FETCH_GROUPS):
            return [self.osdk_setting_groups(request)]
        if route == (USER_SESSIONS, USER_SESSIONS_RESUME):
            # A second connection asking to be attached to the session the
            # title already has. The EAS FC module opens one of its own once
            # its endpoints point somewhere reachable, and this is the first
            # thing it says:
            #
            #     component 0x7802 command 35   SKEY "offline-901feefe6a599"
            #
            # which is the key handed out by the login on the first connection.
            # It was answered with a fieldless success and nothing else, so the
            # module was acknowledged and then never told who it was -- and
            # that is what "EAS FC non connecté" means from its side.
            #
            # The three notifications a login sends are what say whose the
            # connection is, so they are sent here too, against the identity
            # the key names.
            key = find_field(decoded["fields"], "SKEY")
            presented = str(key.value) if key is not None else ""
            # The key *is* "offline-<persona in hex>", so it says which
            # account it claims to resume. Reading that first and looking the
            # persona up is the same move the HTTP side makes with X-UT-SID:
            # let the client's own echo be the routing key. Consulting one
            # shared store here would resume whichever player logged in last.
            resumed = 0
            if presented.startswith("offline-"):
                try:
                    resumed = int(presented[len("offline-"):], 16)
                except ValueError:
                    resumed = 0
            stored_id, stored_name = self.accounts.get(resumed).load_identity()
            expected = f"offline-{stored_id:x}" if stored_id else ""
            if not presented or presented != expected:
                self.logger.event(
                    "session_resume_refused",
                    connection=state.connection_id,
                    presented=presented,
                    expected=expected,
                )
                return [response_frame(request)]
            state.xuid = stored_id
            state.gamertag = stored_name or state.gamertag
            state.authenticated = True
            self.logger.event(
                "session_resumed",
                connection=state.connection_id,
                key=presented,
                gamertag=state.gamertag,
            )
            return [
                response_frame(request),
                *self.session_notifications(state),
            ]
        if route == (GAME_REPORTING, GAME_REPORTING_SUBMIT_OFFLINE):
            # The offline game report, submitted when a match ends. Answering
            # the RPC is not the end of it: retail follows with an asynchronous
            # ResultNotification, and the post-match screen waits on that
            # handshake before it will leave. An independently built revival of
            # this game sends the same notification for the same reason.
            #
            # `GRID` is the report id the client put in its own submission, and
            # it goes back in both id members so the notification can be
            # matched to the report that caused it.
            report = find_field(decoded["fields"], "RPRT")
            identifier = 0
            if report is not None and report.type == STRUCT:
                grid = find_field(report.value, "GRID")
                if grid is not None and isinstance(grid.value, int):
                    identifier = max(0, grid.value)
            self.logger.event(
                "game_report_submitted",
                connection=state.connection_id,
                reportId=identifier,
                fields=[field.label for field in decoded["fields"]],
            )
            return [
                response_frame(request),
                notification_frame(
                    GAME_REPORTING,
                    GAME_REPORTING_RESULT_NOTIFICATION,
                    encode_fields(
                        [
                            Field("EROR", INTEGER, 0),
                            Field("FNL", INTEGER, 1),
                            Field("GHID", INTEGER, identifier),
                            Field("GRID", INTEGER, identifier),
                        ]
                    ),
                ),
            ]
        if route == (OSDK_ONLINE_PASS, OSDK_ONLINE_PASS_FETCH_GATES):
            return [
                response_frame(
                    request,
                    encode_fields([Field("LIST", LIST, (STRUCT, []))]),
                )
            ]

        self.logger.event(
            "unknown_route",
            connection=state.connection_id,
            component=route[0],
            command=route[1],
        )
        return [response_frame(request)]


class IdentityHttpService:
    """Minimal local Nucleus OAuth endpoint used by Authentication2."""

    def __init__(
        self,
        listen: str,
        port: int,
        advertise: str,
        journal: "Journal",
        accounts: "AccountStores | None" = None,
    ):
        self.listen = listen
        self.port = port
        self.advertise = advertise
        self.journal = journal
        # `accounts` is the registry; `account_store` stays as the store for
        # the console that has not named itself, which is what every caller
        # without a persona in hand means.
        self.accounts = accounts if accounts is not None else AccountStores()
        self.server: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def public_base(self) -> str:
        port = self.port if self.server is None else self.server.server_address[1]
        return f"http://{self.advertise}:{port}"

    def start(self) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def reply(
                self,
                status: int,
                body: bytes = b"",
                headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD" and body:
                    self.wfile.write(body)

            def account_store(self) -> "PersistentAccountStore":
                """The account state of whoever this request belongs to.

                `bind_request_club` has already decided that, from X-UT-SID or
                from the bootstrap headers, so this asks the bound club rather
                than deciding again. A request that proved nobody gets persona
                0 -- the same default club it already reads -- so an unproven
                request cannot write into a real player's account state.
                """
                club = current_tenant()
                return owner.accounts.get(getattr(club, "persona_id", 0))

            def serve_identity(self) -> None:
                # Bind a club for this request, and give the thread back the
                # way it was found.
                #
                # Connections get their own thread here, so in production the
                # binding could not outlive the request anyway. But it is
                # thread-wide, and anything that does reuse a thread inherits
                # whichever club the previous request was about. The test
                # suite runs an entire file on one thread and caught this
                # immediately: a season test cleaned up through the module
                # view afterwards and cleared the wrong club's table.
                previous = current_tenant()
                try:
                    self._serve_identity()
                finally:
                    use_tenant(previous)

            def _serve_identity(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                content_length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(content_length) if content_length else b""
                # Which club this is about, decided once and before anything
                # reads club state. `club` is also what the cup and season a
                # match belongs to now hang off: they used to be module
                # globals, which is the same bug as the club itself -- two
                # consoles, one in-flight match between them.
                club = bind_request_club(self.headers, body, normalize_route(parsed.path))
                owner.journal.event(
                    "identity_http_request",
                    peer=self.client_address[0],
                    method=self.command,
                    path=parsed.path,
                    # The values, not only the names. Logging the names alone
                    # left every question about what a screen actually asked
                    # for -- which `type`, which `level` -- answerable only by
                    # guessing, and the consumable picker was three guesses
                    # deep before anyone noticed the journal could not say.
                    query=parsed.query,
                    query_keys=sorted(urllib.parse.parse_qs(parsed.query).keys()),
                    bytes=len(body),
                    headers={name: value for name, value in self.headers.items()},
                    body=request_body_preview(body),
                )
                if parsed.path == "/connect/auth":
                    location = (
                        f"{owner.public_base}/connect/redirect"
                        "?code=offline-fifa14-auth"
                    )
                    owner.journal.event(
                        "identity_http_redirect",
                        peer=self.client_address[0],
                        location=location,
                    )
                    self.reply(302, headers={"Location": location})
                    return
                if parsed.path == "/connect/redirect":
                    self.reply(
                        200,
                        b'{"code":"offline-fifa14-auth"}\n',
                        {"Content-Type": "application/json"},
                    )
                    return
                if parsed.path == "/health":
                    self.reply(200, b"ok\n", {"Content-Type": "text/plain"})
                    return
                if parsed.path == "/revival/reset" and self.command == "POST":
                    self.account_store().reset()
                    owner.journal.event(
                        "account_state_reset", peer=self.client_address[0]
                    )
                    self.reply(
                        200,
                        "session réinitialisée\n".encode("utf-8"),
                        {"Content-Type": "text/plain; charset=utf-8"},
                    )
                    return
                if parsed.path == "/roster":
                    owner.journal.event(
                        "roster_endpoint_requested",
                        peer=self.client_address[0],
                        method=self.command,
                    )
                    # The advertised version identifies the already installed
                    # base roster, so no replacement archive is transferred.
                    self.reply(204)
                    return
                if parsed.path == "/futBoot.xml":
                    owner.journal.event(
                        "fut_boot_served",
                        peer=self.client_address[0],
                        method=self.command,
                        bytes=len(FUT_BOOT_XML),
                    )
                    self.reply(
                        200,
                        FUT_BOOT_XML,
                        {
                            "Content-Type": "application/xml; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                normalized_path = parsed.path
                if normalized_path.startswith("/fut/ut/"):
                    normalized_path = normalized_path[4:]
                # The Xbox CardsDLL names Authentication ``pow/auth`` while
                # the PC client uses ``ut/auth``. Both instantiate the same
                # native response parser and must receive the same SID
                # contract. Other Xbox Cards operations omit the leading
                # ``/ut`` from their path, so normalize those here as well.
                if normalized_path == "/pow/auth":
                    normalized_path = "/ut/auth"
                elif normalized_path.startswith("/game/fifa14/"):
                    normalized_path = "/ut" + normalized_path
                # The client camel-cases some of these paths and this server
                # spells them however they were first written down. They agreed
                # on `tradePile`, `clubUser` and `userHubData` by luck; they did
                # not agree on `watchList`, which this server registered as
                # `watchlist` and therefore answered 404 every time the watch
                # list was opened. Nothing reported it -- a 404 on a FUT route
                # just leaves the screen empty.
                #
                # Matching case-insensitively kills the whole class rather than
                # this one instance. It is safe here because every route below
                # is distinct in lower case, and the only variable segments are
                # numeric ids.
                normalized_path = FUT_ROUTE_SPELLINGS.get(
                    normalized_path.lower(), normalized_path
                )
                if normalized_path in EASW_AUTH_PATHS:
                    # The native success parser reads these headers and hands
                    # EASW-Session and EASW-Token to CardsDLL.  Supplying them
                    # here is what the retail flow does; writing them straight
                    # into the JSON builder's registers, as an earlier tool
                    # did, satisfied that one constructor while leaving the
                    # EASW session itself unestablished.
                    owner.journal.event(
                        "easw_auth_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(body),
                        body=request_body_preview(body),
                    )
                    persona_id, _ = self.account_store().load_identity()
                    self.reply(
                        200,
                        b"",
                        {
                            "Content-Type": "text/plain",
                            "Cache-Control": "no-store",
                            "EASW-Token": EASW_TOKEN,
                            "EASW-Session": EASW_SESSION,
                            "EASW-Nucleus-Persona": str(persona_id),
                            "EASW-Userid": str(persona_id),
                        },
                    )
                    return
                if normalized_path == "/ut/auth":
                    # The session id the client will echo on every request
                    # after this one, and therefore what routes them to this
                    # club. `bind_request_club` above has already read the
                    # persona out of this request's own body.
                    # `/pow/auth` is EA Sports Football Club, not FUT.
                    #
                    # `normalize_route` folds it into `/ut/auth` because the
                    # Xbox CardsDLL was believed to call FUT authentication by
                    # that name. On this console both exist and they are
                    # different callers: CardsDLL posted `/ut/auth` with 10,762
                    # bytes at 13:33 and powdllzf posted `/pow/auth` with 373
                    # at 13:57, in the same session.
                    #
                    # Answering the second like the first did real damage. It
                    # rotated the FUT session -- `issue` replaces the token it
                    # finds, so the `X-UT-SID` the client was still using for
                    # every Ultimate Team request stopped resolving -- and it
                    # adopted the persona out of POW's body, which carries
                    # `nucleusPersonaId` 1000001 beside the real `nuc`. The
                    # journal caught both: persona 2533274966877915 adopted at
                    # 13:33, and 1000001 over the top of it at 13:57.
                    #
                    # So POW gets the document it parses -- `sid`, `serverTime`
                    # and `lastOnlineTime` are the three members powdllzf names,
                    # measured in the dump -- naming the session that already
                    # exists, and changes nothing about who the club is.
                    # Told apart by the body, not by the path.
                    #
                    # Both callers use `/pow/auth` -- an older session had
                    # CardsDLL posting FUT authentication there, which
                    # `test_fut_auth_adopts_the_persona_the_client_presents`
                    # pins -- so the path cannot separate them. The EAS FC one
                    # carries an `identification` object holding `EASW-Session`
                    # and `EASW-Token`, and a `priorityLevel`; the FUT one
                    # carries neither. That is the discriminator.
                    from_pow = False
                    if parsed.path.rstrip("/").endswith("/pow/auth"):
                        try:
                            probe = json.loads(body or b"{}")
                        except (ValueError, UnicodeDecodeError):
                            probe = {}
                        from_pow = isinstance(probe, dict) and (
                            isinstance(probe.get("identification"), dict)
                            or "priorityLevel" in probe
                        )
                    if from_pow:
                        sid = SESSIONS.existing(club.persona_id) or SESSIONS.issue(
                            club.persona_id
                        )
                    else:
                        sid = SESSIONS.issue(club.persona_id)
                    presented = None if from_pow else auth_request_identity(body)
                    if presented is not None:
                        persona_id, persona_name = presented
                        self.account_store().save_identity(persona_id, persona_name)
                        PERSONA.adopt(persona_id)
                        owner.journal.event(
                            "fut_auth_identity_adopted",
                            peer=self.client_address[0],
                            persona_id=persona_id,
                            persona_name=persona_name,
                        )
                    document = {
                        "sid": sid,
                        "serverTime": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                        "lastOnlineTime": "1970-01-01T00:00:00Z",
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "easfc_pow_auth_request" if from_pow
                        else "fut_ut_auth_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(body),
                        content_type=self.headers.get("Content-Type"),
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                            "X-UT-SID": sid,
                        },
                    )
                    return
                if (
                    normalized_path == "/ut/game/fifa14/user"
                    and self.command == "GET"
                ):
                    # FutGetUserInfoServerResponse zeroes every account field
                    # and treats all members as optional -- which is why an
                    # empty object here showed a zero balance in the club
                    # header.  The currency belongs in this response, not in
                    # user/credits.  Matching stays method-specific: a later
                    # create-user POST to the same path must remain unhandled
                    # until it is observed.
                    self.reply(
                        200,
                        WALLET.user_info(
                            club_name(),
                            CLUB_IDENTITY.abbr,
                            self.account_store().load_identity()[0],
                        ) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    owner.journal.event(
                        "fut_user_info_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    return
                if normalized_path == "/ut/game/fifa14/match/reset":
                    self.reply(
                        200,
                        b'{"reset":true}\n',
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    owner.journal.event(
                        "fut_match_reset_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    return
                # Anything that describes the club is generated from the
                # inventory rather than answered with an empty fixture: an
                # empty squad is what fcc_login2 treats as fatal, and an empty
                # club leaves nothing to field a match with.
                club_responses = {
                    "/ut/game/fifa14/squad/list": CLUB_INVENTORY.squad_summaries,
                    "/ut/game/fifa14/squad/active": (
                        lambda: CLUB_INVENTORY.squad_document(
                            CLUB_INVENTORY.active_squad_id(), club_name()
                        )
                    ),
                    "/ut/game/fifa14/club": CLUB_INVENTORY.club_response,
                    "/ut/game/fifa14/purchased/items": (
                        CLUB_INVENTORY.purchased_items_response
                    ),
                }
                # A quick sell is what actually writes the header's balance, so
                # its reply has to carry the new total. An empty object here is
                # what left the header printing uninitialised memory.
                # Polled straight after every search, and it refreshes the
                # header too, so it carries the balance as well.
                if normalized_path in (
                    "/ut/game/fifa14/tradePile",
                    "/ut/game/fifa14/tradepile",
                ) and self.command == "GET":
                    # Settle first, then answer. There is no timer here -- the
                    # client polls this screen constantly, so the poll is the
                    # clock. A listing whose buyer has arrived closes, the coins
                    # land less EA's tax, and the card stays in the pile marked
                    # sold.
                    for sale in CARD_ACTIONS.settle_market():
                        WALLET.credit(sale["net"])
                        owner.journal.event(
                            "fut_listing_sold",
                            peer=self.client_address[0],
                            tradeId=sale["listing"].get("tradeId"),
                            price=sale["price"],
                            net=sale["net"],
                            coins=WALLET.coins,
                        )
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                    self.reply(
                        200,
                        CARD_ACTIONS.trade_pile(WALLET.coins) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The client asks about one auction by id before bidding on
                # it; an empty answer reads as "Auction state is invalid for
                # bidding", which is the string CardsDLL carries beside
                # /status?tradeIds=%lld.
                # `GET /trade/<tradeId>/offer` -- what the console asks when a
                # player opens one of his own listings and the screen wants the
                # bids on it. Seen twice on 17 August 2026, on trade ids
                # 2000000001 and 2000000002, and answered 404 both times.
                #
                # This is the route the trade-offers work had no name for.
                # Neither the PC revival nor Kyro's build serves it, so the
                # document below is **not** a known shape: it is the one
                # document this client is already proven to parse for a single
                # auction -- the same `status_for` reply `/trade/status` sends
                # -- scoped to the id asked for. A 404 on a screen the console
                # chose to open is the worse failure of the two; an auction it
                # already understands is the safest 200 available.
                #
                # When real offers exist they belong on the auction object
                # inside this reply, not in a member invented out here.
                # `GET /trade/<tradeId>/offer` -- the View Offer screen.
                #
                # This is left to 404 on purpose, and the 404 is the safe
                # answer rather than a missing one.
                #
                # It was briefly served with the single-auction `status_for`
                # document, on the reasoning that it is the one shape the client
                # is proven to parse. But `status_for` reads CARD_CATALOGUE.served
                # -- the market's own generated listings, ids from 1_900_000_000
                # -- and a player's listing lives in CARD_ACTIONS.listings from
                # 2_000_000_000, so it was never found: the reply was an empty
                # auctionInfo, and the View Offer screen **froze the console** on
                # it, 18 August 2026, trade 2000000001. A power cycle.
                #
                # The route answered 404 for weeks before that with no freeze --
                # the button merely looked dead. A dead button is recoverable
                # and a freeze is not, so until the offer document's real shape
                # is known this stays a 404. The offers members are in CardsDLL
                # (offerState, offerSent, offerReceived, the auction*Offer* set),
                # so the shape can be found -- but it is found on a screen that
                # is not the pack reveal, carefully, not by serving a guess to a
                # live console. See docs/TRADE_PILE.md.
                if TRADE_OFFER_PATH.match(normalized_path) and self.command == "GET":
                    owner.journal.event(
                        "fut_trade_offer_declined",
                        peer=self.client_address[0],
                        path=normalized_path,
                    )
                    self.reply(
                        404,
                        b'{"reason":"not found"}\n',
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                if (
                    normalized_path == "/ut/game/fifa14/trade/status"
                    and self.command == "GET"
                    and parsed.query
                ):
                    asked: list[int] = []
                    for raw in urllib.parse.parse_qs(parsed.query).get("tradeIds", []):
                        for piece in raw.split(","):
                            try:
                                asked.append(int(piece))
                            except ValueError:
                                continue
                    # The clock runs here too. `tradePile` is not the only
                    # screen that polls: the client asks about a listing by id
                    # from the auction screens as well, and a player watching
                    # one of his own auctions there would have seen it frozen
                    # while the same listing advanced on the transfer list.
                    for sale in CARD_ACTIONS.settle_market():
                        WALLET.credit(sale["net"])
                        owner.journal.event(
                            "fut_listing_sold",
                            peer=self.client_address[0],
                            tradeId=sale["listing"].get("tradeId"),
                            price=sale["price"],
                            net=sale["net"],
                            coins=WALLET.coins,
                        )
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                    payload = CARD_CATALOGUE.status_for(asked, WALLET.coins)
                    owner.journal.event(
                        "fut_trade_status",
                        peer=self.client_address[0],
                        asked=len(asked),
                        known=len(CARD_CATALOGUE.served),
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in (
                    "/ut/game/fifa14/trade/status",
                    "/ut/game/fifa14/watchlist",
                ) and self.command == "GET":
                    self.reply(
                        200,
                        WALLET.auction_state() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Buying the one pack the store advertises. A POST here is the
                # purchase; the drawn cards come back in the reply and then
                # again from purchased/items until the client takes them.
                # Buying a pack is a POST to purchased/items, not to /store --
                # the journal shows the client sending it there and getting a
                # 404. /store is only the catalogue.
                # The thirteen manager tasks. They were a fixed empty list,
                # so nothing completed was ever recorded: the bar stayed at
                # 0/13 and every task reset on the next launch.
                if normalized_path == "/ut/game/fifa14/clientdata/managerquest":
                    if self.command in ("PUT", "POST"):
                        try:
                            document = json.loads(body or b"{}")
                        except ValueError:
                            document = {}
                        changed = MANAGER_TASKS.apply(document)
                        if changed:
                            CLUB_SAVE.save(
                                CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                            )
                        owner.journal.event(
                            "fut_tasks_saved",
                            peer=self.client_address[0],
                            entries=changed,
                            body=request_body_preview(body),
                        )
                    self.reply(
                        200,
                        MANAGER_TASKS.response() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The draw for a cup. The module's template is
                # `/teams?groupId=%d&count=%d`; count is how many opponents the
                # tree needs, the club itself taking the remaining slot.
                if (
                    normalized_path == "/ut/game/fifa14/tournament/teams"
                    and self.command == "GET"
                ):
                    query = urllib.parse.parse_qs(parsed.query)

                    def number(key: str, fallback: int) -> int:
                        try:
                            return int(query.get(key, [str(fallback)])[0])
                        except ValueError:
                            return fallback

                    count = number("count", 15)
                    group = number("groupId", 0)
                    # The cup the club is actually in, so the draw is that
                    # cup's own fifteen rather than the Premier League pool
                    # every cup used to share. The route carries only groupId
                    # and count, so it comes from the progress store.
                    open_cups = TOURNAMENT_PROGRESS.active_ids()
                    payload = tournament_teams_response(
                        count, group, open_cups[-1] if open_cups else None
                    )
                    owner.journal.event(
                        "fut_tournament_teams",
                        peer=self.client_address[0],
                        count=count,
                        group=group,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # A season under way. The URL template table carries
                # SEASONUSER_ALTER as `ut/%s/season/%s/user`, and reading that
                # `%s` as the season id was wrong: beside the season
                # serialiser sits `%d/division/%d`, and what the console
                # actually sent on starting a Saison Joueur Solo was
                #
                #     PUT /ut/game/fifa14/season/1/division/10/user
                #
                # which fell through to the blanket 404. A 404 on a FUT route
                # is a hang with nothing to read, and this one lands exactly
                # where the screen stops: right after "Voulez-vous vraiment
                # débuter cette Saison Joueur Solo ?".
                #
                # The division in the path is the division's number, not the
                # position `season/user` reports -- the client reads
                # `divisionId` out of the record it picked -- so both are kept
                # and neither is converted into the other.
                season_alter = re.fullmatch(
                    r"/ut/game/fifa14/season/(\d+)/division/(-?\d+)/(user|reset)",
                    normalized_path,
                )
                if season_alter:
                    season_id = int(season_alter.group(1))
                    division_id = int(season_alter.group(2))
                    action = season_alter.group(3)
                    if action == "reset":
                        SEASON_PROGRESS.reset(season_id, division_id)
                    elif self.command in ("PUT", "POST"):
                        try:
                            document = json.loads(body or b"{}")
                        except ValueError:
                            document = {}
                        SEASON_PROGRESS.apply(season_id, division_id, document)
                    CLUB_SAVE.save(
                        CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                    )
                    payload = SEASON_PROGRESS.response(season_id, division_id)
                    owner.journal.event(
                        "fut_season_alter",
                        peer=self.client_address[0],
                        method=self.command,
                        season=season_id,
                        division=division_id,
                        action=action,
                        body=request_body_preview(body),
                        payload=payload.decode("utf-8", "replace")[:400],
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Seasons already finished. Asked for once per type the
                # moment a season starts -- `?type=offline`, `?type=online`
                # and two World Cup spellings, all four in `.rdata` -- and
                # answered 404 until now.
                if normalized_path == "/ut/game/fifa14/season/user/history":
                    kind = (
                        urllib.parse.parse_qs(parsed.query).get("type")
                        or ["offline"]
                    )[0]
                    payload = season_history_response(kind)
                    owner.journal.event(
                        "fut_season_history",
                        peer=self.client_address[0],
                        method=self.command,
                        history_type=kind,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # A single cup's saved run. The client serialises this itself
                # -- CardsDLL carries the format strings it builds the body
                # from -- so it arrives with `round`, `dataVersion`, `data`,
                # `progressDataVersion` and `progressData`, and is echoed back
                # in the same shape on the next GET.
                if (
                    normalized_path.startswith("/ut/game/fifa14/tournament/user/")
                    and normalized_path.rsplit("/", 1)[-1].isdigit()
                ):
                    tournament_id = int(normalized_path.rsplit("/", 1)[-1])
                    if self.command in ("PUT", "POST"):
                        try:
                            document = json.loads(body or b"{}")
                        except ValueError:
                            document = {}
                        entry = TOURNAMENT_PROGRESS.apply(tournament_id, document)
                        # Which cup a match belongs to is not in the match
                        # payload. The client says so by saving progress into
                        # this cup as it enters it, and that is the only place
                        # it says so at all.
                        club.active_tournament = tournament_id
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                        owner.journal.event(
                            "fut_tournament_saved",
                            peer=self.client_address[0],
                            tournament=tournament_id,
                            round=entry["round"],
                            body=request_body_preview(body),
                        )
                    payload = TOURNAMENT_PROGRESS.response(tournament_id)
                    # Journalled on the way out as well. Resuming a cup froze
                    # the title on the first GET this route ever received, and
                    # nothing recorded what was answered -- the reply had to be
                    # reconstructed from the code rather than read.
                    owner.journal.event(
                        "fut_tournament_progress",
                        peer=self.client_address[0],
                        method=self.command,
                        tournament=tournament_id,
                        entered=tournament_id in TOURNAMENT_PROGRESS.entries,
                        bytes=len(payload),
                        payload=payload.decode("utf-8", "replace")[:400],
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Quitting a cup. The template table carries
                # `ut/delete/%s/tournament/user`, so the run is dropped rather
                # than left half-played.
                if (
                    normalized_path.startswith(
                        "/ut/delete/game/fifa14/tournament/user"
                    )
                    and self.command in ("POST", "PUT", "DELETE")
                ):
                    tail = normalized_path.rsplit("/", 1)[-1]
                    removed = (
                        TOURNAMENT_PROGRESS.delete(int(tail)) if tail.isdigit() else False
                    )
                    if removed:
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                    owner.journal.event(
                        "fut_tournament_deleted",
                        peer=self.client_address[0],
                        tournament=tail,
                        removed=removed,
                    )
                    self.reply(
                        200,
                        b"{}\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Seasons, cups and Team of the Week. Each of these screens
                # treats an empty list as an error rather than as "nothing
                # available" -- the same way fcc_login2 treats an empty squad --
                # so serving a real one is what makes the mode selectable.
                mode_responses = {
                    "/ut/game/fifa14/season/list": seasons_response,
                    "/ut/game/fifa14/season/user": season_user_response,
                    # The template table carries `ut/%s/tournament`; the Xbox
                    # client was journalled asking for `tournament/list`. Both
                    # get the catalogue.
                    "/ut/game/fifa14/tournament": tournaments_response,
                    "/ut/game/fifa14/tournament/list": tournaments_response,
                    "/ut/game/fifa14/tournament/user/list": (
                        active_tournaments_response
                    ),
                    # The URL template table settles what this is. At
                    # 0x89026ED0 the key SQUAD is followed by three templates
                    # with no keys of their own -- ut/%s/squad, ut/%s/club and
                    # ut/%s/user/list -- before the next key, CLUB_USER. So
                    # user/list belongs to the squad family: it is the list of
                    # the user's squads, not a list of users.
                    #
                    # It therefore gets exactly the document squad/list gets.
                    # Three shapes were tried here by guesswork and all three
                    # were rejected; this one comes from the table.
                    "/ut/game/fifa14/user/list": CLUB_INVENTORY.squad_summaries,
                    # The screen fetches this and answers "Il n'y a aucune
                    # Équipe de la semaine disponible". It asks for nothing
                    # else -- no challenge route has ever appeared in any
                    # journal -- so what it is missing is in this document.
                    #
                    # A time window was tried first, on the reading that a
                    # Team of the Week is this week's team: the six members a
                    # cup carries, all of them in the name table. The message
                    # did not change, so that was not it.
                    #
                    # What goes out now is the *list* of Teams of the Week as
                    # well as the squad. "Aucune disponible" reads much more
                    # like an empty list than like a squad it cannot parse,
                    # and this document was written for exactly that a while
                    # ago -- `totw_index_with_squad` -- and then never wired
                    # to a route.
                    # A clientdata route, and its siblings all answer an
                    # entries document. This carried the 27 kB squad index
                    # instead, and the screen refused with "there is no Team of
                    # the Week available at the moment" over a tile that was
                    # drawing the side correctly. Pressing A fired no request:
                    # the refusal is decided from this one reply.
                    "/ut/game/fifa14/clientdata/totw": (
                        lambda: totw_challenge_entries(CARD_CATALOGUE)
                    ),
                    # Never yet requested by any console here -- but a screen
                    # that has decided there is no Team of the Week has no
                    # reason to ask for one.
                    "/ut/game/fifa14/totw": (
                        lambda: totw_challenge_response(CARD_CATALOGUE)
                    ),
                }
                # `/user/list?personaIdList=...` asks who owns a squad, and
                # keys 3 and 4 of the challenge record name the Team of the
                # Week's club. When that persona is the one asked for, the
                # answer is that club -- a squad list, one entry per week,
                # which is what the challenge select screen enumerates.
                if (
                    normalized_path == "/ut/game/fifa14/user/list"
                    and self.command == "GET"
                    and str(TOTW_PERSONA_ID) in (parsed.query or "")
                ):
                    payload = totw_club_info(CARD_CATALOGUE)
                    owner.journal.event(
                        "fut_totw_club_request",
                        peer=self.client_address[0],
                        path=parsed.path,
                        query=parsed.query,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # `GET /item?idList=a,b` -- the cards behind those ids. The
                # duplicate panel asks for the pair it is comparing right
                # before it draws them, and a static empty list is why the
                # owned card read "undefined".
                if (
                    normalized_path == "/ut/game/fifa14/item"
                    and self.command == "GET"
                    and "idlist" in (parsed.query or "").lower()
                ):
                    query = urllib.parse.parse_qs(parsed.query)
                    raw = ",".join(query.get("idList") or query.get("idlist") or [])
                    ids: list[int] = []
                    for piece in raw.split(","):
                        try:
                            ids.append(int(piece.strip()))
                        except ValueError:
                            continue
                    payload = PACK_SHOP.items_by_id(ids)
                    owner.journal.event(
                        "fut_items_by_id",
                        peer=self.client_address[0],
                        asked=ids,
                        found=len(json.loads(payload)["itemData"]),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in mode_responses and self.command == "GET":
                    payload = mode_responses[normalized_path]()
                    owner.journal.event(
                        "fut_mode_request",
                        peer=self.client_address[0],
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The whole catalogue, generated: nine packs with their own
                # prices and groups, rather than the single fixture entry.
                if normalized_path in (
                    "/ut/game/fifa14/store/purchasegroup/all",
                    "/ut/game/fifa14/store",
                ) and self.command == "GET":
                    self.reply(
                        200,
                        store_catalogue() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if (
                    normalized_path
                    in ("/ut/game/fifa14/purchased/items", "/ut/game/fifa14/store")
                    and self.command == "POST"
                ):
                    # The body names which pack, so the 400-coin bronze costs
                    # 400 rather than whatever the default is.
                    try:
                        wanted = json.loads(body or b"{}")
                    except ValueError:
                        wanted = {}
                    try:
                        pack_id = int(
                            wanted.get("packId") or wanted.get("id") or GOLD_PACK_ID
                        )
                    except (TypeError, ValueError):
                        pack_id = GOLD_PACK_ID
                    if not PACK_SHOP.can_afford(pack_id):
                        owner.journal.event(
                            "fut_pack_refused",
                            peer=self.client_address[0],
                            coins=WALLET.coins,
                        )
                        self.reply(
                            409,
                            PACK_SHOP.refused() + b"\n",
                            {"Content-Type": "application/json; charset=utf-8"},
                        )
                        return
                    payload = PACK_SHOP.open_pack(pack_id)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_pack_opened",
                        peer=self.client_address[0],
                        coins=WALLET.coins,
                        pack=pack_id,
                        items=len(PACK_SHOP.pending),
                        # What was actually drawn. Without this a card that
                        # went missing between the pack screen and the club
                        # could not be identified afterwards, let alone
                        # restored -- which is what happened to a TOTS Ruffier.
                        drawn=[
                            {
                                "id": item.get("id"),
                                "assetId": item.get("assetId"),
                                "rating": item.get("rating"),
                                "rarity": item.get("rarity"),
                            }
                            for item in PACK_SHOP.pending[-12:]
                        ],
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if (
                    normalized_path == "/ut/game/fifa14/purchased/items"
                    and self.command == "GET"
                ):
                    self.reply(
                        200,
                        PACK_SHOP.purchased_items() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Anything else under /trade/. CardsDLL composes these from
                # fragments -- "?tradeId=", "/expired", "/status?tradeIds=" --
                # and an unanswered one reads as the listing being gone, which
                # is what "la liste a expiré" says. Answer the ones we know and
                # give the rest an empty, well-formed auction list rather than
                # a 404.
                if normalized_path.startswith(
                    "/ut/game/fifa14/trade"
                ) and normalized_path.endswith("/expired") and self.command == "GET":
                    self.reply(
                        200,
                        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}\n',
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Bidding and buying. Both go through the same endpoint: a bid
                # at or above the buy-now price ends the auction, which is what
                # the Buy Now button does.
                # Buying posts to /offer, not /bid. That 404 is what the
                # screen reports as "cette liste a expiré" -- the timer was
                # showing 23h59 at the time, so the message names the wrong
                # cause and only the journal says which request was missed.
                if (
                    normalized_path.startswith("/ut/game/fifa14/trade/")
                    and normalized_path.rsplit("/", 1)[-1] in ("bid", "offer")
                    and self.command in ("PUT", "POST")
                ):
                    parts = normalized_path.split("/")
                    try:
                        trade_id = int(parts[-2])
                    except (IndexError, ValueError):
                        trade_id = 0
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    try:
                        amount = int(
                            document.get("bid")
                            or document.get("buyNowPrice")
                            or document.get("amount")
                            or 0
                        )
                    except (TypeError, ValueError):
                        amount = 0
                    payload, won = CARD_CATALOGUE.bid(trade_id, amount, WALLET)
                    if won is not None:
                        # A bought card goes to the pending pile, not straight
                        # into the club. That is the route the pack flow takes
                        # and the one that works: purchased/items is what the
                        # assign screen reads, and sending the card directly to
                        # the club left that list empty -- so "Assigner
                        # maintenant" had nothing to offer and backed out.
                        # Into the pending pile *and* into the club. The
                        # pending pile alone lost the card: the journal shows
                        # the assign arriving with pending already empty and
                        # the club unchanged at 55, so a bought player was paid
                        # for and then owned by nobody. Being in both means the
                        # assign screen can still offer it, and it cannot go
                        # missing if that hand-off fails.
                        item = dict(won)
                        item["itemState"] = "new"
                        item["untradeable"] = False
                        # A card bought on the market can repeat one the club
                        # already holds exactly as a packed one can, and it was
                        # going in unmarked -- the pairing existed only on the
                        # pack path. Marked before it is kept, or it would be
                        # found to duplicate itself.
                        pairs = PACK_SHOP._mark_duplicates([item])
                        if pairs:
                            try:
                                document_out = json.loads(payload)
                            except ValueError:
                                document_out = None
                            if isinstance(document_out, dict):
                                document_out["duplicateItemIdList"] = pairs
                                payload = json.dumps(
                                    document_out, separators=(",", ":")
                                ).encode()
                        PACK_SHOP.pending.append(item)
                        CARD_ACTIONS._keep(dict(item, itemState="free"))
                        CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_bid",
                        peer=self.client_address[0],
                        trade=trade_id,
                        amount=amount,
                        won=won is not None,
                        coins=WALLET.coins,
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Putting a card on the market, and taking it back off.
                if normalized_path in (
                    "/ut/game/fifa14/auctionhouse",
                    "/ut/game/fifa14/trade",
                ) and self.command == "POST":
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    payload = CARD_ACTIONS.list_for_sale(document)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_item_listed",
                        peer=self.client_address[0],
                        path=parsed.path,
                        listings=len(CARD_ACTIONS.listings),
                        # Kept so an unexpected body shape can be read back out
                        # of the journal rather than guessed at again.
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path.startswith(
                    "/ut/game/fifa14/auctionhouse/"
                ) and self.command == "DELETE":
                    tail = normalized_path.rsplit("/", 1)[-1]
                    try:
                        trade_id = int(tail)
                    except ValueError:
                        trade_id = 0
                    self.reply(
                        200,
                        CARD_ACTIONS.withdraw(trade_id) + b"\n",
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Saving a squad. The body names the cards and their slots;
                # without this the squad was whatever was built at load time
                # and nothing could ever change it, so a card bought or pulled
                # reached the club and had nowhere to go.
                # Dropping a side.
                if normalized_path.startswith(
                    "/ut/game/fifa14/squad/"
                ) and self.command == "DELETE":
                    tail = normalized_path.rsplit("/", 1)[-1]
                    try:
                        squad_id = int(tail)
                    except ValueError:
                        squad_id = 0
                    removed = CLUB_INVENTORY.delete_squad(squad_id)
                    if removed:
                        CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_squad_deleted",
                        peer=self.client_address[0],
                        squad=squad_id,
                        removed=removed,
                    )
                    self.reply(
                        200,
                        json.dumps({"id": squad_id}).encode() + b"\n",
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # One week of the Team of the Week, by its own club.
                #
                #     GET /ut/game/fifa14/squad/<week>/user/<totw persona>
                #
                # This is what `RequestSquadsLineup` and `GetSquadsLineup` in
                # the ION binding table fetch once the client knows the club
                # exists. It has to be matched before the squad-by-id handler
                # below, which keys on trailing digits and would read the
                # persona as a squad id.
                totw_lineup = re.fullmatch(
                    r"/ut/game/fifa14/squad/(\d+)/user/(\d+)", normalized_path
                )
                if (
                    totw_lineup
                    and self.command == "GET"
                    and int(totw_lineup.group(2)) == TOTW_PERSONA_ID
                ):
                    week = int(totw_lineup.group(1))
                    payload = json.dumps(
                        totw_hub_squad(CARD_CATALOGUE, week),
                        separators=(",", ":"),
                    ).encode()
                    owner.journal.event(
                        "fut_totw_lineup_request",
                        peer=self.client_address[0],
                        week=week,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # One squad by id. Answering every id with the active side is
                # why a newly created team came back holding the first team's
                # players instead of being empty.
                if (
                    normalized_path.startswith("/ut/game/fifa14/squad/")
                    and self.command == "GET"
                    and normalized_path.rsplit("/", 1)[-1].isdigit()
                ):
                    squad_id = int(normalized_path.rsplit("/", 1)[-1])
                    # Loading a side by id is the only signal that it was
                    # chosen; nothing else in the traffic says so.
                    CLUB_INVENTORY.set_active(squad_id)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    self.reply(
                        200,
                        CLUB_INVENTORY.squad_document(squad_id, club_name()) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Creating a side posts to /squad with no id at all -- the
                # body carries "id":0 and the name you typed. Matching only
                # paths that ended in an id is why creation reported failure.
                if (
                    normalized_path == "/ut/game/fifa14/squad"
                    or normalized_path.startswith("/ut/game/fifa14/squad/")
                ) and self.command in ("PUT", "POST"):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    squad = document.get("squad", document)
                    chosen: list[int] = []
                    # Empty slots arrive as itemData id 0. Dropping them
                    # shifted every player after the gap into the wrong
                    # position, so keep them as the gaps they are.
                    for entry in (squad.get("players") or []):
                        data = entry.get("itemData") if isinstance(entry, dict) else None
                        raw = (data or {}).get("id") if isinstance(data, dict) else None
                        try:
                            chosen.append(int(raw or 0))
                        except (TypeError, ValueError):
                            chosen.append(0)
                    tail = normalized_path.rsplit("/", 1)[-1]
                    try:
                        squad_id = int(tail)
                    except ValueError:
                        squad_id = int(squad.get("id") or 0)
                    # The manager the player put in the slot. Every squad PUT
                    # carries `"manager":[{"id":N}]` and this read the players,
                    # the name and the formation out of the same body and
                    # ignored it -- so the slot filled on screen and the next
                    # launch had it empty.
                    #
                    # None means the body did not mention one and the stored
                    # value stands; 0 is the player clearing the slot.
                    manager_id: int | None = None
                    manager_slot = squad.get("manager")
                    if isinstance(manager_slot, list):
                        first = manager_slot[0] if manager_slot else {}
                        try:
                            manager_id = int((first or {}).get("id") or 0)
                        except (TypeError, ValueError):
                            manager_id = 0
                    saved_id = CLUB_INVENTORY.save_squad(
                        squad_id,
                        chosen,
                        name=(squad.get("squadName") or "").strip() or None,
                        formation=(squad.get("formation") or "").strip() or None,
                        manager=manager_id,
                        # The console works chemistry out itself and reports it
                        # here. Keeping it is what stops the squad selector
                        # advertising a number the squad screen then disagrees
                        # with.
                        chemistry=(
                            int(squad["chemistry"])
                            if isinstance(squad.get("chemistry"), (int, float))
                            else None
                        ),
                    )
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_squad_saved",
                        peer=self.client_address[0],
                        path=parsed.path,
                        squad=saved_id,
                        players=len(chosen),
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        json.dumps({"id": saved_id}).encode() + b"\n",
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Applying a consumable. The path names the card's resource,
                # the body names what to apply it to:
                #
                #     POST /ut/game/fifa14/item/resource/5001001
                #     {"apply":[{"id":1600000001}]}
                #
                # `apply` is in CardsDLL's member-name table, next to
                # `applyTo`, so both spellings are accepted. Retail answers
                # this one by status, so success is an empty document.
                #
                # The client addresses the card two ways, and only the first
                # was handled. `item/<itemId>` names one particular card in the
                # club rather than the definition, and a real application on
                # 11 August --
                #
                #     POST /ut/game/fifa14/item/1950000106
                #     {"apply":[{"id":1700000004}]}
                #
                # -- was answered 404 and went into the unhandled journal,
                # where nobody looked. From the player's side the card simply
                # did nothing.
                consumable_apply = re.fullmatch(
                    r"/ut/game/fifa14/item/resource/(\d+)", normalized_path
                )
                consumable_by_item = None
                if consumable_apply is None:
                    consumable_by_item = re.fullmatch(
                        r"/ut/game/fifa14/item/(\d+)", normalized_path
                    )
                # The same path carries two different requests, and the body is
                # what tells them apart:
                #
                #     {"apply":[{"id":N}]}      apply a consumable to a card
                #     {"itemState":"active"}    make this club item the active
                #                               stadium, badge, ball or kit
                #
                # Matching on the path alone sent the second into the consumable
                # rack, which refused it -- "item 1750000250 is not a
                # consumable" -- and the console showed "There was a problem
                # communicating with the FIFA Ultimate Team servers". Journalled
                # 16 August 2026, three times, while a player tried to move his
                # club off Camp Nou.
                if consumable_by_item is not None and self.command in ("POST", "PUT"):
                    try:
                        wanted = json.loads(body or b"{}")
                    except ValueError:
                        wanted = {}
                    state = str(wanted.get("itemState") or "") if isinstance(wanted, dict) else ""
                    if state and not (
                        isinstance(wanted, dict)
                        and (wanted.get("apply") or wanted.get("applyTo"))
                    ):
                        item_id = int(consumable_by_item.group(1))
                        activated = activate_item(CLUB_INVENTORY, item_id, CARD_ACTIONS)
                        owner.journal.event(
                            "fut_item_activated" if activated else "fut_item_activate_failed",
                            peer=self.client_address[0],
                            path=parsed.path,
                            item=item_id,
                            requested=state,
                            became=(activated or {}).get("itemState"),
                            item_type=(activated or {}).get("itemType"),
                        )
                        if activated is None:
                            # 200, not 400.
                            #
                            # A 400 here ejects the player from Ultimate Team
                            # outright -- seen on 19 August 2026 when a kit
                            # packed seconds earlier could not be found, because
                            # the card was still in New Items and only the club
                            # was searched. Being thrown out of the mode is a far
                            # worse answer than a button that does nothing.
                            #
                            # Nothing is lost by saying nothing: activation is a
                            # cosmetic slot, so an unacknowledged one leaves the
                            # club exactly as it was. The failure is journalled
                            # above either way, which is what makes it findable.
                            self.reply(
                                200,
                                b"{}",
                                {"Content-Type": "application/json; charset=utf-8"},
                            )
                            return
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                        self.reply(
                            200,
                            b"{}",
                            {"Content-Type": "application/json; charset=utf-8"},
                        )
                        return
                if (
                    (consumable_apply or consumable_by_item)
                    and self.command in ("POST", "PUT")
                ):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    rows = document.get("apply", document.get("applyTo", []))
                    if isinstance(rows, dict):
                        rows = [rows]
                    targets: list[int] = []
                    for row in rows if isinstance(rows, list) else []:
                        raw = row.get("id", row.get("itemId")) if isinstance(row, dict) else row
                        try:
                            targets.append(int(raw))
                        except (TypeError, ValueError):
                            continue
                    resource_id = (
                        int(consumable_apply.group(1)) if consumable_apply else 0
                    )
                    try:
                        if consumable_by_item is not None:
                            resource_id = CONSUMABLE_RACK.resource_of(
                                int(consumable_by_item.group(1))
                            )
                        result = CONSUMABLE_RACK.apply(resource_id, targets)
                    except ConsumableRefused as refusal:
                        owner.journal.event(
                            "fut_consumable_refused",
                            peer=self.client_address[0],
                            path=parsed.path,
                            resourceId=resource_id,
                            targets=targets,
                            reason=str(refusal),
                            # The play style and position blocks land here.
                            # One of these from the console names the family.
                            unresolved=CONSUMABLE_RACK.refused[-4:],
                        )
                        self.reply(
                            400,
                            json.dumps(
                                {"code": "400", "reason": str(refusal)}
                            ).encode() + b"\n",
                            {"Content-Type": "application/json; charset=utf-8"},
                        )
                        return
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_consumable_applied",
                        peer=self.client_address[0],
                        path=parsed.path,
                        resourceId=resource_id,
                        targets=targets,
                        effect=result["effect"],
                        consumedItemId=result["consumedItemId"],
                    )
                    # Hand the changed card back, so the screen can redraw it.
                    #
                    # This answered `{}` on the reading that retail replies by
                    # status. It does -- but the client then keeps showing the
                    # card it already had: a position modifier applied cleanly
                    # and the card stayed on its old position until it was
                    # taken out of the squad and searched for again, and the
                    # same for fitness. Reported from the console 16-17 August.
                    #
                    # `apply()` has already built the changed cards; they were
                    # being discarded. `itemData` is in CardsDLL's name table
                    # (0x030AB8) and is the shape `club`, `squad/active` and
                    # `transfermarket` all return, so it is a document this
                    # parser reads constantly rather than a new invention.
                    #
                    # `FIFA14_APPLY_ECHO=off` puts the empty reply back. Worth
                    # knowing: sending members the parser *recognises* is what
                    # hung the title after a final on 17 August, so if an apply
                    # ever hangs, this is the first thing to switch off.
                    echo = os.environ.get("FIFA14_APPLY_ECHO", "").strip().lower()
                    changed = result.get("itemData") or []
                    payload = (
                        b"{}"
                        if echo in {"0", "off", "false", "no"} or not changed
                        else json.dumps(
                            {"itemData": changed}, separators=(",", ":")
                        ).encode()
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Send to club, list for transfer: each entry has to be
                # acknowledged. Answering with a club search acknowledges
                # nothing and the button looks dead.
                if normalized_path == "/ut/game/fifa14/item" and self.command in (
                    "PUT",
                    "POST",
                ):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    payload = CARD_ACTIONS.move(document)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_item_move",
                        peer=self.client_address[0],
                        path=parsed.path,
                        club=len(CARD_ACTIONS.club),
                        pending=len(PACK_SHOP.pending),
                        # Ids the client moved that this server never held.
                        # Each one is a card the player saw and lost, and it
                        # used to be answered with success.
                        unmatched=CARD_ACTIONS.unmatched[-24:],
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Removing a listing from the transfer list -- collecting a
                # sold card, or withdrawing an unsold one. The console sends
                # `GET /ut/delete/game/fifa14/trade/<id>`, and it was unhandled,
                # so pressing Y on "A buyer was found for your item!" answered
                # "There was a problem communicating with the FIFA Ultimate Team
                # servers" and the sold card would not clear.
                trade_delete = re.fullmatch(
                    r"/ut/delete/game/fifa14/trade/(\d+)", normalized_path
                )
                if trade_delete is not None:
                    trade_id = int(trade_delete.group(1))
                    payload = CARD_ACTIONS.withdraw(trade_id)
                    CLUB_SAVE.save(
                        CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                    )
                    owner.journal.event(
                        "fut_trade_removed",
                        peer=self.client_address[0],
                        tradeId=trade_id,
                        remaining=len(CARD_ACTIONS.listings),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Deleting a squad. Journalled 16 August as
                # `GET /ut/delete/game/fifa14/squad/1` from a club whose active
                # squad was 3 -- the route was not served at all, so the console
                # got a 404 and the squad stayed. `ClubInventory.delete_squad`
                # existed the whole time and was never wired to anything.
                squad_delete = re.fullmatch(
                    r"/ut/delete/game/fifa14/squad/(\d+)", normalized_path
                )
                if squad_delete is not None:
                    squad_id = int(squad_delete.group(1))
                    dropped = CLUB_INVENTORY.delete_squad(squad_id)
                    if dropped:
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                    owner.journal.event(
                        "fut_squad_deleted" if dropped else "fut_squad_delete_refused",
                        peer=self.client_address[0],
                        squad=squad_id,
                        active=CLUB_INVENTORY.active_squad_id(),
                        remaining=len(CLUB_INVENTORY.squad_ids()),
                    )
                    self.reply(
                        200 if dropped else 400,
                        b"{}" if dropped else json.dumps(
                            {"code": "400", "reason": "cannot delete the active squad"}
                        ).encode(),
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return

                if normalized_path == "/ut/delete/game/fifa14/item":
                    # {"itemId":[...]} -- always a list, twelve long when a
                    # whole pack is sold at once.
                    item_ids: list[int] = []
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    raw = document.get("itemId", document.get("id"))
                    if isinstance(raw, list):
                        candidates = raw
                    elif raw is not None:
                        candidates = [raw]
                    else:
                        candidates = urllib.parse.parse_qs(parsed.query).get("id", [])
                    for candidate in candidates:
                        try:
                            item_ids.append(int(candidate))
                        except (TypeError, ValueError):
                            continue
                    payload = CARD_ACTIONS.discard_many(item_ids)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS)
                    owner.journal.event(
                        "fut_quick_sell",
                        peer=self.client_address[0],
                        path=parsed.path,
                        coins=WALLET.coins,
                        items=len(item_ids),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in (
                    "/ut/game/fifa14/user/credits",
                    "/ut/game/fifa14/user",
                ) and self.command == "GET":
                    payload = (
                        WALLET.credits_response()
                        if normalized_path.endswith("/credits")
                        else WALLET.user_info(
                            club_name(),
                            CLUB_IDENTITY.abbr,
                            self.account_store().load_identity()[0],
                        )
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The market is the one screen whose job is to show players the
                # club does not own, so it is served from the catalogue rather
                # than the inventory.
                if normalized_path in (
                    "/ut/game/fifa14/transfermarket",
                    "/ut/game/fifa14/club",
                ) and self.command == "GET" and (
                    parsed.query or normalized_path.endswith("/transfermarket")
                ):
                    # The market used to need a query to be answered at all,
                    # and a bare request fell through to a 404 -- the club has
                    # its own no-query handler, the market had none. A search
                    # with no filters is a search: it is the first page of
                    # everything, which is what the screen shows before you
                    # type anything.
                    query = {
                        key: values[0]
                        for key, values in urllib.parse.parse_qs(parsed.query).items()
                    }
                    if normalized_path.endswith("/club"):
                        # A club search still searches the club -- but it does
                        # search it now, rather than returning all of it.
                        payload = CLUB_INVENTORY.club_response(query)
                    else:
                        payload = CARD_CATALOGUE.auctions(query, coins=WALLET.coins)
                    owner.journal.event(
                        "fut_market_search",
                        query=parsed.query[:160],
                        peer=self.client_address[0],
                        path=parsed.path,
                        filters=sorted(query),
                        # The values, not just the names: knowing the screen
                        # asks type=consumable is what identified the mismatch.
                        values={k: v for k, v in query.items() if k in
                                ("type", "level", "position", "cat")},
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in club_responses and self.command == "GET":
                    payload = club_responses[normalized_path]()
                    owner.journal.event(
                        "fut_club_response",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The four the FUT home fetches for itself. The header reads
                # its balance from whichever response last carried one, and
                # these are the last ones before it draws -- which is why it
                # showed zero at home while the store, which refetches credits,
                # showed the real figure.
                #
                # Only these three, and the list was found by bisection rather
                # than reasoning. Adding the balance to every FUT route froze
                # the login at clientdata/tutorialpopups; adding it to
                # clientdata/userHubData as well froze it there instead. Both
                # of those parsers reject an object carrying members they do
                # not know, and the login step waiting on the response never
                # completes. Do not extend this list without watching where the
                # fan-out stops.
                # The Consommables tab asks here by category, and it was a
                # 404 -- so the tab looked empty however many the club held.
                if normalized_path.startswith(
                    "/ut/game/fifa14/club/consumables"
                ) and self.command == "GET":
                    # The picker names a category in the path and asks one at a
                    # time: /contracts, /fitness, /development. Answering every
                    # one of them with the whole club's consumables handed it
                    # 242 cards of every family when it asked for contracts.
                    category = normalized_path[
                        len("/ut/game/fifa14/club/consumables"):
                    ].strip("/")
                    payload = consumables_response(CLUB_INVENTORY, category)
                    owner.journal.event(
                        "fut_club_consumables_request",
                        peer=self.client_address[0],
                        path=parsed.path,
                        category=category,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The end of a match. `FutDestroyMatchServerResponse` carries
                # exactly three members -- myMatchStats, opponentMatchStats and
                # matchData -- all three of which are in CardsDLL's own name
                # table. This answered `{}`, which is a document the parser can
                # read and find nothing in.
                #
                # Nothing else goes on the wire. A PC revival of the same game
                # recovered the same three statically and records that its
                # client disconnected immediately after parsing an oversized
                # destroy response, so settlement stays server-side.
                if (
                    normalized_path == "/ut/game/fifa14/match/end"
                    and self.command in ("PUT", "POST")
                ):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    # Settle it. This used to answer three empty members and
                    # throw the result away: no coins for the match, no
                    # progress in the cup, nothing on the award screen. A club
                    # could win a Gold Cup final and finish exactly as poor as
                    # it started.
                    result = match_result(document)
                    reward = match_reward(
                        document.get("myMatchStats"),
                        document.get("opponentMatchStats"),
                        minutes=int(document.get("minutesPlayed") or 90),
                        completed=result in ("WIN", "DRAW", "LOSS"),
                    )
                    # What the match did to the eleven who played. The captured
                    # body carries a per-player `fitness`, and goals and
                    # assists for whoever got them; all of it was discarded, so
                    # nobody ever lost fitness and the whole consumable pile
                    # had nothing to restore.
                    played = apply_match_items(
                        CLUB_INVENTORY, document.get("items") or []
                    )
                    cup = {}
                    if club.active_tournament is not None:
                        cup = TOURNAMENT_PROGRESS.advance(club.active_tournament, result)
                    earned = (
                        reward["totalCoins"]
                        + int(cup.get("roundCoins") or 0)
                        + int(cup.get("prize") or 0)
                    )
                    if earned:
                        WALLET.credit(earned)
                    # The season's own record. Nothing else keeps it: the
                    # client's progress goes up as an opaque blob and the
                    # header asks for the numbers separately, which is why it
                    # read BILAN 0-0-0 over a season won 3-0.
                    # The club's own won-drawn-lost. Nothing kept this, so the
                    # hub header read 0-0-0 over a club that had just won a cup
                    # -- the season counters below only move inside a season,
                    # and a cup match moved nothing at all.
                    CLUB_RECORD.settle(result)
                    season_record = {}
                    if club.active_season is not None:
                        season_record = SEASON_PROGRESS.settle(
                            club.active_season[0], club.active_season[1], result, earned
                        )
                    CLUB_SAVE.save(
                        CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                    )
                    # The award screen, which read zeroes over a credited
                    # wallet until 16 August 2026.
                    #
                    # This reply was three members -- `myMatchStats` and
                    # `opponentMatchStats` as **empty strings**, plus
                    # `matchData` -- held that way deliberately until a real
                    # match end from this console had been read, because a
                    # frontend that hangs after a won final is worse than an
                    # award screen showing zeroes.
                    #
                    # Four have now been read: a Cup 2 run played to the final
                    # and won, 6 349 coins credited across four matches, every
                    # one of them settled correctly server-side. The player saw
                    # none of it, which is the exact symptom the PC revival
                    # records against its own earlier build: "the match award
                    # view was receiving correct stats but difficulty=0 and no
                    # settlement scalars, so it rendered all zeroes while the
                    # DB wallet had already been credited".
                    #
                    # So the scalars go out, and the stats are **echoed** --
                    # the twelve members the client itself submitted, sent back
                    # as integers rather than as the empty string that made the
                    # screen render nothing. Echoing what arrived is the safest
                    # content there is: it invents no member the parser has not
                    # already produced.
                    stat_members = (
                        "goals", "shotsOnTarget", "successfulTackles", "corners",
                        "cleansheets", "passingPercentage", "possessionPercentage",
                        "manOfTheMatch", "fouls", "yellowCards", "redCards", "offsides",
                    )

                    def echoed(block: object) -> dict:
                        source = block if isinstance(block, dict) else {}
                        out = {}
                        for member in stat_members:
                            try:
                                out[member] = int(source.get(member, 0) or 0)
                            except (TypeError, ValueError):
                                out[member] = 0
                        return out

                    settled = {
                        "endReason": result,
                        "matchData": str(document.get("matchData") or ""),
                        "myMatchStats": echoed(document.get("myMatchStats")),
                        "opponentMatchStats": echoed(document.get("opponentMatchStats")),
                    }
                    # FIFA omits `matchDifficulty` from its own /match/end, so
                    # it is taken from the cup round being played when the body
                    # does not carry one -- the same derivation the PC revival
                    # makes from its offline tournament context.
                    difficulty = int(document.get("matchDifficulty") or 0)
                    if not difficulty:
                        difficulty = int(cup.get("difficulty") or 0)
                    settled["matchDifficulty"] = difficulty
                    if result in ("WIN", "DRAW", "LOSS"):
                        prize = int(cup.get("prize") or 0)
                        # The award screen's members, read out of CardsDLL's own
                        # JSON name table on 16 August 2026 rather than borrowed
                        # from the PC revival.
                        #
                        # `completionAward` and `skillAward` are **not in this
                        # binary at all** -- they are the PC build's names, and
                        # sending them is why a 703-byte reply carrying the
                        # right numbers drew 0 / 0 / 0 over a wallet that had
                        # just been credited 3 617 coins.
                        #
                        # What the table does carry, all together in one run:
                        #
                        #     0x0305C0  participationAward
                        #     0x030BEC  gameModeAward
                        #     0x030860  matchDifficulty
                        #     0x030870  matchCoinPartials
                        #     0x030884  matchCoinMultipliers
                        #     0x03089C  matchCoins
                        #
                        # **These hang the frontend, and are off by default.**
                        #
                        # Measured 17 August 2026. A cup final was lost, the
                        # server settled it correctly -- LOSS, 593 credited --
                        # and the title stopped dead on the pitch view with the
                        # crowd still playing. No request followed `match/end`.
                        # XBDM stayed up, so it was a frozen facade, and the
                        # console needed the power button.
                        #
                        # The 703-byte reply carrying the PC revival's names
                        # (`completionAward`, `skillAward`, `rewardCoins`,
                        # `totalCoins`) had been harmless through four match
                        # ends -- because none of those names is in this
                        # binary's table, so the parser skipped every one. The
                        # moment the members were the real ones, it acted on
                        # them, and acting on them is what hangs.
                        #
                        # That is the risk the original note here named exactly,
                        # and it was right: an award screen showing zeroes over
                        # a credited wallet is much cheaper than a freeze after
                        # a final. The wallet, the cup and the record are all
                        # settled server-side before this reply is built, so
                        # nothing is lost by staying quiet.
                        #
                        # `FIFA14_MATCH_AWARDS=1` puts them back, and what it
                        # sends changed on 27 August. It used to be four
                        # members -- participationAward, gameModeAward,
                        # matchCoins, coins -- and that is the set that froze.
                        #
                        # `MarvelcoCode/Impulsum14` has this screen working and
                        # sends a different set. Two differences stand out, and
                        # either could be the hang:
                        #
                        #   * it does **not** send `gameModeAward` at all;
                        #   * it sends `matchCoinPartials` and
                        #     `matchCoinMultipliers` as empty arrays, and this
                        #     server sent `matchCoins` without either. A screen
                        #     that reads a coin total and then walks the two
                        #     lists behind it has nothing to walk.
                        #
                        # Every member below is in CardsDLL's own table --
                        # including `boostConis`, which is EA's own misspelling
                        # and is the reason to believe that build read these off
                        # the real API rather than guessing. Its `allCoins` and
                        # `matchParamsKeyValues` are **not** in the table and
                        # stay out.
                        #
                        # **On by default from 27 August.** The screen showed
                        # the coins after an ordinary match, which is the thing
                        # this has been off for since 17 August.
                        #
                        # `FIFA14_MATCH_AWARDS=0` takes it back out. The freeze
                        # it replaces came after a cup final rather than a
                        # league match, and one clean match is not proof of
                        # every path -- a cup final, a loss and a DNF are all
                        # still first runs. Nothing is risked by finding out:
                        # the wallet, the cup and the record are settled
                        # server-side before this reply is built.
                        if os.environ.get(
                            "FIFA14_MATCH_AWARDS", "1"
                        ).strip().lower() not in {"0", "false", "no"}:
                            settled.update(
                                {
                                    "matchCoins": earned,
                                    "participationAward": int(
                                        reward["completionAward"]
                                    ),
                                    # The two lists the coin total is broken
                                    # down into. Empty, because this server
                                    # awards one figure and does not break it
                                    # down -- but present, which is the half
                                    # that may matter.
                                    "matchCoinPartials": [],
                                    "matchCoinMultipliers": [],
                                    "seasonCoins": WALLET.coins,
                                    "tournamentCoins": prize,
                                    "boostConis": 0,
                                    "boostCountLeft": 0,
                                    "coins": WALLET.coins,
                                    "credits": WALLET.coins,
                                    "userData": {
                                        "coins": WALLET.coins,
                                        "credits": WALLET.coins,
                                    },
                                }
                            )
                        if prize:
                            settled["tournamentPrize"] = prize
                        if cup:
                            settled["tournamentId"] = int(cup.get("tournamentId") or 0)
                            settled["tournamentRound"] = int(cup.get("round") or 1)
                    payload = json.dumps(settled, separators=(",", ":")).encode()
                    owner.journal.event(
                        "fut_match_end",
                        peer=self.client_address[0],
                        result=result,
                        completionAward=reward["completionAward"],
                        skillAward=reward["skillAward"],
                        roundCoins=cup.get("roundCoins", 0),
                        prize=cup.get("prize", 0),
                        credited=earned,
                        coins=WALLET.coins,
                        tournament=cup.get("tournamentId"),
                        round=cup.get("round"),
                        season=club.active_season,
                        seasonRecord=(
                            f"{season_record.get('won', 0)}-"
                            f"{season_record.get('draw', 0)}-"
                            f"{season_record.get('lost', 0)}"
                            if season_record
                            else None
                        ),
                        fitnessWritten=played["fitness"],
                        goals=played["goals"],
                        assists=played["assists"],
                        unknownPlayers=played["unknown"],
                        bytes=len(payload),
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Club creation. The screen sends the name and abbreviation it
                # asked the player for; answering `{}` accepted them and threw
                # them away, so the club had no name on the next load and every
                # other route went on reporting an unnamed club.
                if (
                    normalized_path == "/ut/game/fifa14/user/club"
                    and self.command in ("PUT", "POST")
                ):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    adopted = CLUB_IDENTITY.adopt(document)
                    if adopted:
                        CLUB_INVENTORY.rename_active_squad(CLUB_IDENTITY.name)
                        CLUB_SAVE.save(
                            CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                        )
                    owner.journal.event(
                        "fut_club_created",
                        peer=self.client_address[0],
                        club=CLUB_IDENTITY.name,
                        abbr=CLUB_IDENTITY.abbr,
                        adopted=adopted,
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        b"{}\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # A cup's trophy definition, asked for by its
                # `trophyResourceId`. The blanket empty answer below is what
                # left the console building
                # /fut/items/images/trophies/xbl2/.big with no basename.
                # `-?` because the seasons screen asks for `-1.json`, once per
                # division, and a digits-only pattern let all ten of them fall
                # through to the blanket `{"itemData":[]}` this handler exists
                # to replace. The console then builds
                # /fut/items/images/trophies/xbl2/.big with no basename, which
                # is in the journals eighteen times.
                trophy_item = re.fullmatch(
                    r"/fut/items/xbl2/(-?\d+)\.json", normalized_path
                )
                if trophy_item and self.command == "GET":
                    resource_id = int(trophy_item.group(1))
                    payload = trophy_item_response(resource_id)
                    owner.journal.event(
                        "fut_trophy_item",
                        peer=self.client_address[0],
                        trophy=resource_id,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # BIG archives, not JSON. Answering these from the blanket
                # itemData reply below handed the console sixteen bytes of
                # JSON where it asked for a binary container.
                if (
                    normalized_path.startswith("/fut/items/images/")
                    and normalized_path.lower().endswith(".big")
                    and self.command == "GET"
                ):
                    # 404, not an empty archive.
                    #
                    # The trophy art is **local**: `cards0.big` on the disc
                    # carries seventy trophies under
                    # data/ui/external/ion_fut/artassets/fcctournamenttrophies/
                    # as `trophy_<id>_<tier>.big`. The console asks the server
                    # for them anyway, and this answered 200 with a structurally
                    # valid BIGF holding **no entries** -- which tells the client
                    # the art exists and is empty. It then draws nothing, which
                    # is why no cup has ever shown a trophy.
                    #
                    # A 404 says the server has no copy, which is true, and
                    # leaves the client free to use the one on the disc. It
                    # cannot be worse than the current answer: an empty archive
                    # renders no trophy in every session recorded.
                    #
                    # `FIFA14_TROPHY_ARCHIVE=empty` restores the old reply.
                    if os.environ.get(
                        "FIFA14_TROPHY_ARCHIVE", ""
                    ).strip().lower() == "empty":
                        payload = empty_big_archive()
                        owner.journal.event(
                            "fut_image_archive",
                            peer=self.client_address[0],
                            path=parsed.path,
                            bytes=len(payload),
                        )
                        self.reply(
                            200,
                            payload,
                            {
                                "Content-Type": "application/octet-stream",
                                "Cache-Control": "no-store",
                            },
                        )
                        return
                    owner.journal.event(
                        "fut_image_archive_declined",
                        peer=self.client_address[0],
                        path=parsed.path,
                    )
                    self.reply(
                        404,
                        b"",
                        {"Content-Type": "application/octet-stream"},
                    )
                    return
                # Item definitions the trophy and club tiles resolve against.
                if normalized_path.startswith("/fut/items/") and self.command == "GET":
                    self.reply(
                        200,
                        b'{"itemData":[]}\n',
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Mon Club's counters -- players, rares, staff, stadiums,
                # kits, badges, balls -- all read zero because these answered
                # with an empty entries list, so a club full of cards reported
                # owning nothing.
                if normalized_path.startswith(
                    "/ut/game/fifa14/club/stats/"
                ) and self.command == "GET":
                    # The consumables tab asks here and needs counts per
                    # consumable, not the club's player and stadium counters.
                    # `/year` is different again: it is the hub header's
                    # won-drawn-lost, which answered a static empty list and so
                    # read 0-0-0 over a club that had just won a cup.
                    if normalized_path.endswith("/consumables"):
                        body_out = consumable_stats_response(CLUB_INVENTORY)
                    elif normalized_path.endswith("/year"):
                        body_out = club_year_response(CLUB_RECORD)
                    else:
                        body_out = club_stats_response(CLUB_INVENTORY)
                    self.reply(
                        200,
                        body_out + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The My Club and transfer tiles read their counts here, and
                # both were fixed: the club tile stayed at 92 as cards arrived
                # and the market tile always read zero.
                if normalized_path == "/ut/game/fifa14/hub" and self.command == "GET":
                    self.reply(
                        200,
                        with_balance(
                            hub_response(
                                CLUB_INVENTORY,
                                # The live market plus the club's own active
                                # listings. Sold listings are excluded -- they
                                # are not on the market any more, which is the
                                # whole of the "13 = my sold cards" bug.
                                len(CARD_CATALOGUE.cards)
                                + sum(
                                    1
                                    for l in CARD_ACTIONS.listings.values()
                                    if l.get("tradeState") == "active"
                                ),
                                sum(
                                    1
                                    for l in CARD_ACTIONS.listings.values()
                                    if l.get("tradeState") == "active"
                                ),
                                sum(
                                    1
                                    for l in CARD_ACTIONS.listings.values()
                                    if l.get("tradeState") == "closed"
                                ),
                                # Unlisted: cards on the transfer list not yet
                                # listed for sale. A listed card is not counted
                                # twice -- its id is in `listed`.
                                sum(
                                    1
                                    for card in CARD_ACTIONS.transfer
                                    if card.get("id")
                                    not in {
                                        (l.get("itemData") or {}).get("id")
                                        for l in CARD_ACTIONS.listings.values()
                                    }
                                ),
                                # The Team of the Week the PLAY tile draws.
                                totw=totw_hub_squad(CARD_CATALOGUE),
                            ),
                            WALLET.coins,
                        )
                        + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path == "/ut/game/fifa14/clubUser" and (
                    self.command == "GET"
                ):
                    # The persona is the club's name -- empty until the club is
                    # created -- and the cards are what the Apply Consumable
                    # picker binds against. Answering with the persona alone is
                    # why it offered nothing.
                    payload = club_user_response(CLUB_INVENTORY, club_name())
                    owner.journal.event(
                        "fut_club_user_request",
                        peer=self.client_address[0],
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        with_balance(payload, WALLET.coins) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The last word on the header's balance.
                #
                # `docs/HOME_HEADER_BALANCE.md` establishes two things: the
                # client never asks for `user/credits` during the login fan-out
                # (it only asks when the store opens), and a response that
                # omits the total does not leave the header alone -- these
                # constructors zero their fields before parsing, so a balance
                # already on screen is wiped by the next reply that lacks one.
                #
                # Journalled 16 August, the fan-out ends:
                #
                #     clubUser -> /tutorials -> hub -> leaderboards/options
                #
                # `hub` carries the balance and `leaderboards/options` follows
                # it with `{}`, so the last thing the header hears is a zero.
                # This puts the total on that last reply too.
                #
                # Behind a flag because adding a balance broadly has frozen this
                # login twice -- at `clientdata/tutorialpopups` and at
                # `clientdata/userHubData`. One route, one relaunch to judge it,
                # and `FIFA14_HEADER_BALANCE=off` to put it back.
                if (
                    normalized_path == "/ut/game/fifa14/leaderboards/options"
                    and self.command == "GET"
                    and os.environ.get("FIFA14_HEADER_BALANCE", "").strip().lower()
                    in {"1", "true", "yes", "last"}
                ):
                    self.reply(
                        200,
                        with_balance(FUT_ROUTES[normalized_path], WALLET.coins) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in (
                    "/ut/game/fifa14/hub",
                    "/ut/game/fifa14/eventfeed",
                ) and self.command == "GET":
                    fixture = FUT_ROUTES[normalized_path]
                    self.reply(
                        200,
                        with_balance(fixture, WALLET.coins) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Creating a match says which mode it belongs to. A cup match
                # carries `tournamentId`, a season match carries `seasonId`
                # and `divisionId` -- and for cups that ownership had to be
                # inferred from whichever cup saved its progress last, because
                # nothing else said so. Seasons need no such inference.
                #
                # The reply is unchanged: `{}` is what this route has always
                # answered and the match starts on it.
                if (
                    normalized_path == "/ut/game/fifa14/match"
                    and self.command == "POST"
                ):
                    try:
                        created = json.loads(body or b"{}")
                    except ValueError:
                        created = {}
                    if isinstance(created, dict) and "seasonId" in created:
                        club.active_season = (
                            int(created.get("seasonId") or 0),
                            int(created.get("divisionId") or 0),
                        )
                        club.active_tournament = None
                    elif isinstance(created, dict) and "tournamentId" in created:
                        club.active_season = None
                    owner.journal.event(
                        "fut_match_created",
                        peer=self.client_address[0],
                        season=club.active_season,
                        tournament=club.active_tournament,
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        FUT_ROUTES[normalized_path] + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The client keeps its own hub counters here and reads them
                # back at the start of every session. Answering the fixture
                # `{}` to a GET is what made TRANSFER LIST show "0 ITEMS /
                # Selling: 0 / Sold: 0" over a pile that held twenty-seven: the
                # tile is drawn from what the client itself last wrote, not
                # from `/hub`, whose `selling` and `sold` it does not read.
                #
                # What goes back is the client's own document unchanged. This
                # parser is one of the two that freeze the login on an unknown
                # member, so nothing is added to it -- see `ClientData`.
                client_data = re.fullmatch(
                    r"/ut/game/fifa14/clientdata/(\w+)", normalized_path
                )
                if client_data is not None:
                    name = client_data.group(1)
                    if self.command == "PUT":
                        kept = CLIENT_DATA.save(name, body)
                        if kept:
                            CLUB_SAVE.save(
                                CLUB_INVENTORY, WALLET, CARD_ACTIONS, MANAGER_TASKS
                            )
                            owner.journal.event(
                                "fut_client_data_saved",
                                peer=self.client_address[0],
                                name=name,
                                body=request_body_preview(body),
                            )
                    elif self.command == "GET":
                        held = CLIENT_DATA.read(name)
                        if held is not None:
                            owner.journal.event(
                                "fut_client_data_served",
                                peer=self.client_address[0],
                                name=name,
                                bytes=len(held),
                            )
                            self.reply(
                                200,
                                held + b"\n",
                                {
                                    "Content-Type": "application/json; charset=utf-8",
                                    "Cache-Control": "no-store",
                                },
                            )
                            return
                if normalized_path in FUT_ROUTES:
                    owner.journal.event(
                        "fut_route_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        FUT_ROUTES[normalized_path] + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path == "/ut/game/fifa14/settings":
                    # Field names recovered from FIFA 14's FutSettings parser.
                    # clubCreateThreshold stays at zero so a brand-new account
                    # is allowed to create its club immediately.
                    payload = (
                        json.dumps(
                            {
                                "maximumTradePileSize": 30,
                                "getOperationTimeoutSec": 60,
                                "clubCreateThreshold": 0,
                                "fifaPointsCancelTransactionFix": 1,
                                "tokenRedemptionEnabled": 0,
                                "enableWorldCupMode": 0,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_settings_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if parsed.path.startswith(("/fut/loc/", "/fut/packs/loc/")):
                    # Localisation bundles for the FUT leaderboard and pack
                    # screens.  The client only needs a well-formed document;
                    # an empty string table keeps the retail labels in place.
                    #
                    # This used to shadow the icebreaker/leaderboard locstrings
                    # block further down, which serves the four captain names:
                    # every path it answers begins `/fut/loc/`, so the specific
                    # handler was unreachable and both locales got the empty
                    # table. Named documents get first refusal now.
                    lowered = parsed.path.lower()
                    if lowered.endswith(
                        ("/leaderboards.eng_us.xml", "/icebreaker.eng_us.xml")
                    ):
                        pass                       # fall through to the block below
                    elif "storepackdescriptions" in lowered:
                        # The store's pack tiles key on FUT_STORE_PACK_<id>_DESC
                        # and it resolves here. Answering an empty table left
                        # every tile falling back to its group heading, so the
                        # detail pane read "Gold Packs / Gold Packs".
                        payload = store_pack_descriptions()
                        owner.journal.event(
                            "fut_locstring_request",
                            peer=self.client_address[0],
                            method=self.command,
                            path=parsed.path,
                            bytes=len(payload),
                        )
                        self.reply(
                            200,
                            payload,
                            {
                                "Content-Type": "application/xml; charset=utf-8",
                                "Cache-Control": "no-store",
                            },
                        )
                        return
                    else:
                        owner.journal.event(
                            "fut_locstring_request",
                            peer=self.client_address[0],
                            method=self.command,
                            path=parsed.path,
                        )
                        self.reply(
                            200,
                            b'<?xml version="1.0" encoding="utf-8"?>\n'
                            b"<localization>\n</localization>\n",
                            {
                                "Content-Type": "application/xml; charset=utf-8",
                                "Cache-Control": "no-store",
                            },
                        )
                        return
                if normalized_path == "/ut/game/fifa14/user/accountinfo":
                    persona_id, persona_name = self.account_store().load_identity()
                    # An empty persona list is what the PC revival serves, and
                    # the difference is not cosmetic.  A populated list tells
                    # the client it already owns a FUT account, so the login
                    # helper goes looking for that account's club, squad and
                    # identity -- none of which exist here -- and waits on a
                    # completion that never arrives.  An empty list states the
                    # opposite: no FUT account yet.  That is the NEW_USER path
                    # fcc_login1 already knows how to walk, through the
                    # icebreaker captain selection into club creation.
                    document = account_info_document(persona_id, persona_name)
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_account_info_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        persona_id=persona_id,
                        persona_name=persona_name,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                lowered_path = normalized_path.lower()
                if lowered_path == "/ut/game/fifa14/phishing/trusteddevice":
                    # An empty object leaves the device unknown, so the client
                    # asks its security question on every single launch -- and
                    # answering it is what makes this server persist the flags
                    # that then stop the client authenticating at all.  The
                    # question is not a step of a working FUT login; it is a
                    # detour an unrecognised device is sent on.
                    #
                    # CardsDLL's parser reads exactly four booleans here.
                    # Describing the console as a device we already know, whose
                    # fingerprint has not changed and which is not locked, is
                    # what the working reference implementation of this title
                    # serves. It grants no account, club, inventory or
                    # entitlement -- only that this device has been seen before.
                    payload = (
                        b'{"trusted":true,"changed":false,'
                        b'"exists":true,"locked":false}\n'
                    )
                    owner.journal.event(
                        "fut_trusted_device_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path in (
                    "/ut/game/fifa14/phishing",
                    "/ut/game/fifa14/phishing/question",
                ):
                    document = {
                        "question": 0,
                        "attempts": 5,
                        "recoverAttempts": 20,
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_phishing_question_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path == "/ut/game/fifa14/phishing/validate":
                    document = {
                        "debug": "Answer is correct.",
                        "string": "OK",
                        "code": "200",
                        "reason": "Answer is correct.",
                        "token": "LOCAL-FIFA14-PHISHING",
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_phishing_validation_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                            "Set-Cookie": (
                                "FUTWebPhishing=LOCAL-FIFA14-PHISHING; "
                                "Path=/; HttpOnly"
                            ),
                        },
                    )
                    return
                if lowered_path == "/ut/game/fifa14/user/action":
                    # GetUserActionServerResponse is a collection. A new local
                    # identity has no completed onboarding actions yet.
                    #
                    # The collection is `actions`. `userActionList` appears
                    # nowhere in CardsDLL's member-name table, while `actions`
                    # sits directly beside `actionType` in it -- so the name
                    # served here was one the parser could not read, and an
                    # unreadable list is not the same as an empty one.
                    #
                    # This is the list `FUT_IcebreakerManager` consults through
                    # `RetrieveUserActions` before `HasUserDoneIB` decides
                    # whether the captain selection is owed. Both spellings go
                    # out; an unrecognised sibling is skipped.
                    payload = b'{"actions":[],"userActionList":[]}\n'
                    owner.journal.event(
                        "fut_user_actions_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path.startswith("/ut/game/fifa14/user/action/"):
                    # UpdateUserActionServerResponse has no parsed payload.
                    payload = b"{}\n"
                    owner.journal.event(
                        "fut_user_action_update",
                        peer=self.client_address[0],
                        method=self.command,
                        effective_method=self.headers.get(
                            "X-HTTP-Method-Override", self.command
                        ).upper(),
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path.endswith(
                    "/fut/packs/icebreaker/icebreakerpacklist.json"
                ) or lowered_path.endswith(
                    "/packs/icebreaker/icebreakerpacklist.json"
                ):
                    # id and image alone are enough to draw the four dock
                    # rows, but not to build the cards behind them: the retail
                    # CardsDLL card constructor dereferences a null player
                    # object when the squad resource ids are absent, and the
                    # client restarts its whole bootstrap.  Serve a fixture
                    # that carries the 23-player arrays each pack declares.
                    payload = (
                        ICEBREAKER_PACK_LIST.read_text(encoding="utf-8").strip()
                        + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_icebreaker_packlist_served",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if (
                    lowered_path.endswith("/loc/xbox360/leaderboards.eng_us.xml")
                    or lowered_path.endswith("/loc/xbox360/icebreaker.eng_us.xml")
                ):
                    # The cups' names.
                    #
                    # Every offline tournament tile drew a bare `*` -- the
                    # fallback for a localisation key that resolves to nothing.
                    # `name` was never the answer: it is not in CardsDLL's JSON
                    # table, and the PC revival stores cup names in its own
                    # source and never puts them on the wire either.
                    #
                    # The client builds the key itself. `TOURNY_LOC_%d` sits in
                    # the module at 0x01DDC4, so tournament 1 asks for
                    # `TOURNY_LOC_1`. Serving those three strings here is what
                    # a cup name actually is.
                    #
                    # This document had to be unshadowed first: every path it
                    # answers begins `/fut/loc/`, and the generic handler above
                    # was catching them all and returning an empty table, so
                    # nothing served from here had ever reached the console.
                    cup_names = b"".join(
                        f'  <locstring id="TOURNY_LOC_{cup}">{title}</locstring>\n'.encode()
                        for cup, title in TOURNAMENT_NAMES.items()
                    )
                    # The chemistry style label, probed.
                    #
                    # `FUT_PLAYSTYLE_%d` is in the module at 0x01FE28 -- the
                    # same shape as `TOURNY_LOC_%d`, which turned out to be all
                    # a cup name ever was. So the card may be drawing BASIC not
                    # because `style` is unread, but because nothing answers
                    # `FUT_PLAYSTYLE_9`.
                    #
                    # Naming each index distinctly separates the three
                    # possibilities in one look at a card whose style is known.
                    # Lucas carries style 9 on the server:
                    #
                    #   shows "PS9"     the style is read; this was only ever a
                    #                   missing localisation string
                    #   shows "PS0"     the member is not read and the client
                    #                   believes every card is style 0
                    #   shows "BASIC"   the label does not come from this key at
                    #                   all, and is resolved natively -- the
                    #                   `CARDS_NO_PLAYSTYLE_PLAYER` path
                    #
                    # Off by default: these are deliberately ugly names, and
                    # `FIFA14_STYLE_NAMES=1` is what turns them on.
                    style_names = b""
                    if os.environ.get("FIFA14_STYLE_NAMES", "").strip().lower() in {
                        "1", "true", "yes"
                    }:
                        style_names = b"".join(
                            f'  <locstring id="FUT_PLAYSTYLE_{index}">PS{index}</locstring>\n'.encode()
                            for index in range(19)
                        )
                    payload = (
                        b'<?xml version="1.0" encoding="UTF-8"?>\n'
                        b'<message_set target="fut-locstrings">\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_0">FALCAO</locstring>\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_1">MESSI</locstring>\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_2">EL SHAARAWY</locstring>\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_3">ALABA</locstring>\n'
                        + cup_names + style_names +
                        b'</message_set>\n'
                    )
                    owner.journal.event(
                        "fut_locstrings_served",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload,
                        {"Content-Type": "application/xml; charset=utf-8"},
                    )
                    return
                if normalized_path in ("/messages", "/fut/messages"):
                    self.reply(
                        200,
                        b'<?xml version="1.0" encoding="UTF-8"?>\n<MESSAGES>\n</MESSAGES>\n',
                        {"Content-Type": "application/xml; charset=utf-8"},
                    )
                    return
                if normalized_path in ("/tutorials", "/fut/tutorials"):
                    # Every recorded session ends on this request, whatever
                    # else changes, and disabling FUT/DISABLE_TUTORIALS and
                    # FUT/FORCE_TUTORIALS did not stop the client making it --
                    # so it is not gated by those keys and the only thing left
                    # to vary is the answer.  An empty <MESSAGES> document was
                    # a guess whose shape was never checked against the
                    # parser; 404 is the one answer whose meaning is
                    # unambiguous.  If the client can treat "no tutorials" as
                    # ordinary, this is what tells it so.
                    owner.journal.event(
                        "fut_tutorial_feed_declined",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(404, b"not found\n", {"Content-Type": "text/plain"})
                    return
                if parsed.path == "/sponsored-events":
                    # The title only needs a valid non-empty URL during the
                    # global online bootstrap.  Keep the local target benign
                    # in case a menu later opens it in the embedded browser.
                    self.reply(204)
                    return
                # EA Sports Football Club, everything after the handshake.
                #
                # `/pow/auth` is already folded into `/ut/auth` above -- this
                # console has always called FUT authentication by the POW name
                # and it is answered as FUT. What lands here is the hub itself,
                # which no Xbox journal has ever recorded a single request for.
                #
                # Journalled per request. If the endpoint change works, this is
                # where it shows: routes arriving here at all is the result,
                # before anything about their contents matters.
                if normalized_path.startswith(POW_ROUTE_PREFIX):
                    owner.journal.event(
                        "pow_service_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        query=parsed.query,
                    )
                    self.reply(
                        200,
                        pow_service_document(normalized_path, parsed.query),
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # An unhandled route is the clearest signal that the retail
                # client expects a document this server does not model yet.
                owner.journal.event(
                    "identity_http_unhandled",
                    peer=self.client_address[0],
                    method=self.command,
                    path=parsed.path,
                    normalized_path=normalized_path,
                    # The values, not only the names. Logging the names alone
                    # left every question about what a screen actually asked
                    # for -- which `type`, which `level` -- answerable only by
                    # guessing, and the consumable picker was three guesses
                    # deep before anyone noticed the journal could not say.
                    query=parsed.query,
                    query_keys=sorted(urllib.parse.parse_qs(parsed.query).keys()),
                    body=request_body_preview(body),
                )
                self.reply(404, b"not found\n", {"Content-Type": "text/plain"})

            do_GET = serve_identity
            do_HEAD = serve_identity
            do_POST = serve_identity
            do_PUT = serve_identity
            do_DELETE = serve_identity

        self.server = http.server.ThreadingHTTPServer((self.listen, self.port), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"identity-http-{self.server.server_address[1]}",
            daemon=True,
        )
        self.thread.start()
        self.journal.event(
            "identity_http_listening",
            address=self.listen,
            port=self.server.server_address[1],
            public_base=self.public_base,
        )

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1.0)


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()

    def event(self, kind: str, **values: Any) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": kind,
            **values,
        }
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with self.lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        print(line, flush=True)

    def frame(self, direction: str, state: ClientState, raw: bytes) -> None:
        try:
            decoded = json_value(decode_frame(raw))
            self.event(
                "frame",
                direction=direction,
                connection=state.connection_id,
                peer=f"{state.peer[0]}:{state.peer[1]}",
                local_port=state.local_port,
                frame=decoded,
                hex=raw.hex().upper(),
            )
        except Exception as error:
            self.event(
                "frame_decode_error",
                direction=direction,
                connection=state.connection_id,
                error=str(error),
                hex=raw.hex().upper(),
            )


class BlazeService:
    def __init__(
        self,
        listen: str,
        ports: list[int],
        protocol: Fifa14Protocol,
        journal: Journal,
        tls_context: ssl.SSLContext | None = None,
        tls_ports: set[int] | None = None,
    ):
        self.listen = listen
        self.ports = ports
        self.protocol = protocol
        self.journal = journal
        self.tls_context = tls_context
        self.tls_ports = tls_ports or set()
        self.stop_event = threading.Event()
        self.listeners: list[socket.socket] = []
        self.threads: list[threading.Thread] = []
        self.connection_counter = 0
        self.counter_lock = threading.Lock()

    def next_connection_id(self) -> int:
        with self.counter_lock:
            self.connection_counter += 1
            return self.connection_counter

    def start(self) -> None:
        for port in self.ports:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.listen, port))
            listener.listen(16)
            listener.settimeout(0.5)
            self.listeners.append(listener)
            thread = threading.Thread(
                target=self.accept_loop,
                args=(listener, port),
                name=f"blaze-listen-{port}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)
            self.journal.event(
                "listening",
                address=self.listen,
                port=port,
                transport="tls" if port in self.tls_ports else "plaintext",
            )

    def stop(self) -> None:
        self.stop_event.set()
        # The protocol owns timers of its own -- a matchmaking search armed
        # seconds ago will otherwise wake up after everything it needs is
        # gone.
        self.protocol.stop()
        for listener in self.listeners:
            try:
                listener.close()
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=1.0)

    def accept_loop(self, listener: socket.socket, port: int) -> None:
        while not self.stop_event.is_set():
            try:
                client, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            state = ClientState(self.next_connection_id(), peer, port)
            thread = threading.Thread(
                target=self.client_loop,
                args=(client, state),
                name=f"blaze-client-{state.connection_id}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def client_loop(self, client: socket.socket, state: ClientState) -> None:
        self.journal.event(
            "connected",
            connection=state.connection_id,
            peer=f"{state.peer[0]}:{state.peer[1]}",
            local_port=state.local_port,
        )
        if state.local_port in self.tls_ports:
            if self.tls_context is None:
                self.journal.event(
                    "tls_configuration_error",
                    connection=state.connection_id,
                    local_port=state.local_port,
                )
                client.close()
                return
            try:
                client.settimeout(8.0)
                client = self.tls_context.wrap_socket(client, server_side=True)
                cipher = client.cipher()
                self.journal.event(
                    "tls_connected",
                    connection=state.connection_id,
                    local_port=state.local_port,
                    version=client.version(),
                    cipher=cipher[0] if cipher else None,
                )
            except (OSError, ssl.SSLError) as error:
                self.journal.event(
                    "tls_handshake_error",
                    connection=state.connection_id,
                    local_port=state.local_port,
                    error=f"{type(error).__name__}: {error}",
                )
                try:
                    client.close()
                except OSError:
                    pass
                return

        client.settimeout(0.5)
        # Bound here rather than at accept: on a TLS port the line above
        # replaced `client` with the wrapped socket, and pushing through the
        # raw one would put plaintext frames on an encrypted connection.
        state.channel = client
        self.protocol.remember_connection(state)
        buffer = bytearray()
        try:
            while not self.stop_event.is_set():
                try:
                    block = client.recv(65536)
                except socket.timeout:
                    continue
                if not block:
                    return
                buffer.extend(block)

                # A TLS ClientHello starts with a TLS record byte, not a Blaze
                # payload length.  Record it explicitly so routing/certificate
                # work is not confused with malformed ProtoFire traffic.
                if len(buffer) >= 3 and buffer[0] in (0x14, 0x15, 0x16, 0x17):
                    self.journal.event(
                        "tls_client_hello",
                        connection=state.connection_id,
                        local_port=state.local_port,
                        prefix=bytes(buffer[:64]).hex().upper(),
                    )
                    return

                while len(buffer) >= 12:
                    header_size = normal_header_size(buffer)
                    if len(buffer) < header_size:
                        break
                    payload_size = int.from_bytes(buffer[0:2], "big")
                    frame_size = header_size + payload_size
                    if frame_size > 2 * 1024 * 1024:
                        raise ValueError(f"Implausible Blaze frame size {frame_size}")
                    if len(buffer) < frame_size:
                        break
                    wire = bytes(buffer[:frame_size])
                    del buffer[:frame_size]
                    # The current decoder supports only the common 12-byte
                    # header.  Context bytes are removed for payload decoding.
                    request = wire if header_size == 12 else wire[:12] + wire[header_size:]
                    state.request_count += 1
                    self.journal.frame("request", state, request)
                    for response in self.protocol.handle(request, state):
                        self.journal.frame("response", state, response)
                        with state.send_lock:
                            client.sendall(response)
        except Exception as error:
            self.journal.event(
                "connection_error",
                connection=state.connection_id,
                error=f"{type(error).__name__}: {error}",
                buffered=bytes(buffer).hex().upper(),
            )
        finally:
            state.channel = None
            self.protocol.forget_connection(state)
            try:
                client.close()
            except OSError:
                pass
            self.journal.event(
                "disconnected",
                connection=state.connection_id,
                requests=state.request_count,
            )


def parse_ports(value: str) -> list[int]:
    result = []
    for item in value.split(","):
        port = int(item.strip(), 0)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError(f"Invalid TCP port {port}")
        if port not in result:
            result.append(port)
    return result


def build_redirector_tls_context(cert: Path, key: Path) -> ssl.SSLContext:
    """Build the TLS 1.0 context expected by the retail ProtoSSL client."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.maximum_version = ssl.TLSVersion.TLSv1
    # FIFA 14's ProtoSSL predates modern AEAD suites.  SECLEVEL=0 is required
    # for its TLS 1.0/RSA handshake and the deliberately 1024-bit test key.
    context.set_ciphers("AES128-SHA:AES256-SHA:@SECLEVEL=0")
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--advertise", required=True)
    parser.add_argument("--core-port", type=int, default=10041)
    parser.add_argument("--identity-port", type=int, default=18080)
    parser.add_argument(
        "--ports",
        type=parse_ports,
        default=parse_ports("10041,42124,42126,42127"),
        help="comma-separated Blaze TCP listener ports",
    )
    parser.add_argument(
        "--identity-extra-ports",
        type=parse_ports,
        default=parse_ports("8080"),
        help=(
            "additional HTTP listener ports. 8080 is EAS FC's catalogue port: "
            "the connect hook redirects it here by port, so something has to "
            "be listening or the redirect lands on a closed door"
        ),
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=REPOSITORY / "runtime" / "blaze-server.jsonl",
    )
    parser.add_argument(
        "--account-state",
        type=Path,
        default=REPOSITORY / "runtime" / "local-account.json",
    )
    parser.add_argument(
        "--redirector-tls-ports",
        type=parse_ports,
        default=parse_ports("42127"),
        help=(
            "comma-separated Blaze redirector ports to wrap in native TLS "
            "(default: 42127)"
        ),
    )
    parser.add_argument(
        "--redirector-tls-cert",
        type=Path,
        help="PEM certificate using the old ProtoSSL signature-OID workaround",
    )
    parser.add_argument(
        "--redirector-tls-key",
        type=Path,
        help="PEM private key for --redirector-tls-cert",
    )
    args = parser.parse_args()

    if (args.redirector_tls_cert is None) != (args.redirector_tls_key is None):
        parser.error(
            "--redirector-tls-cert and --redirector-tls-key must be used together"
        )
    tls_context = None
    tls_ports: set[int] = set()
    if args.redirector_tls_cert is not None:
        missing_tls_ports = set(args.redirector_tls_ports).difference(args.ports)
        if missing_tls_ports:
            parser.error(
                "every --redirector-tls-ports value must also be present in "
                f"--ports (missing: {sorted(missing_tls_ports)})"
            )
        tls_context = build_redirector_tls_context(
            args.redirector_tls_cert,
            args.redirector_tls_key,
        )
        tls_ports.update(args.redirector_tls_ports)

    journal = Journal(args.journal)
    accounts = AccountStores(args.account_state)
    protocol = Fifa14Protocol(
        args.advertise,
        args.core_port,
        journal,
        identity_port=args.identity_port,
        accounts=accounts,
    )
    service = BlazeService(
        args.listen,
        args.ports,
        protocol,
        journal,
        tls_context=tls_context,
        tls_ports=tls_ports,
    )
    identity = IdentityHttpService(
        args.listen,
        args.identity_port,
        args.advertise,
        journal,
        accounts,
    )
    # Same service, more doors. EAS FC's catalogue is redirected here by port
    # rather than by hostname, so it arrives on 8080 and must be answered
    # there.
    #
    # Each listener builds its own public_base from the port it is on, so the
    # 8080 one hands out `http://<host>:8080/...` rather than the identity
    # port. That is not a bug to fix: both listeners serve the same routes, so
    # a URL naming either one resolves. Said here because the obvious reading
    # -- that every URL names the identity port -- is wrong, and it is the
    # kind of wrong that costs an hour when a redirect goes somewhere
    # unexpected.
    extra_identity = [
        IdentityHttpService(
            args.listen, port, args.advertise, journal, accounts
        )
        for port in args.identity_extra_ports
        if port != args.identity_port
    ]

    def stop(_signum: int, _frame: object) -> None:
        service.stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    service.start()
    identity.start()
    for extra in extra_identity:
        extra.start()
    journal.event(
        "ready",
        advertise=args.advertise,
        core_port=args.core_port,
        identity_base=protocol.identity_base,
        redirector_transport=("tls" if tls_ports else "plaintext"),
        redirector_tls_ports=(sorted(tls_ports) if tls_ports else []),
        components=COMPONENT_IDS,
    )
    try:
        while not service.stop_event.wait(0.5):
            pass
    finally:
        for extra in extra_identity:
            extra.stop()
        identity.stop()
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
