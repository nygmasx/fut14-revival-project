#!/usr/bin/env python3
"""A read-only dashboard over a running revival server.

Deliberately a *second* process, not another listener inside
`fifa14_blaze_server.py`. Restarting the Blaze server ejects whoever is in a
FUT session at that moment -- the title drops to the FIFA main menu and the
club's unsaved half-hour goes with it. A dashboard is the kind of thing that
gets restarted twenty times in an afternoon while its layout is being worked
on, so it must not share a process with the game.

The price of that separation is that this can see the clubs only as they are
on disk, not the live objects in the server's memory. That turns out to be
almost everything: `runtime/clubs/*.json` carries coins, the squad, the cards
acquired, the cups and the seasons, and `runtime/blaze-server.jsonl` carries
what everybody did and when. What it cannot see is the few seconds between a
change and the save that follows it.

It also means this cannot *write*. Anything it changed in a club file would be
overwritten by the live server's next save, silently, so nothing here writes:
every route is a GET and the runtime directory is opened read-only.
"""

from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import os
import re
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game_records import RecordStore  # noqa: E402
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB = ROOT / "web"

# The journal is append-only and grows for as long as the server runs; on the
# VPS it passed a megabyte in a day. Reading all of it on every poll would make
# the dashboard slower the longer the server stayed up, so only the tail is
# read -- enough for days of activity, bounded either way.
JOURNAL_TAIL_BYTES = 8 * 1024 * 1024

# A player counts as online this long after their last request. FUT is quiet
# between screens -- a squad screen can sit still for a minute -- so a short
# window would blink them off while they are plainly playing.
ONLINE_WINDOW = 240.0


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def parse_time(value: str | None) -> float | None:
    """Journal timestamps, as epoch seconds.

    They are written `%Y-%m-%dT%H:%M:%S%z`, and the offset is real: the Mac
    writes `+0200` and the VPS `+0000`, so the same feed can hold both and
    comparing the strings would order them wrongly.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return None


class Catalogue:
    """assetId -> who the card actually is.

    The saves carry `assetId` and a rating and nothing else readable; without
    this the squad screen is a wall of numbers.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._by_asset: dict[int, dict] = {}
        self._mtime = 0.0
        self._lock = threading.Lock()

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime == self._mtime and self._by_asset:
            return
        document = read_json(self.path, {}) or {}
        cards = document.get("cards") if isinstance(document, dict) else document
        table: dict[int, dict] = {}
        for card in cards or []:
            try:
                table[int(card["assetId"])] = card
            except (KeyError, TypeError, ValueError):
                continue
        with self._lock:
            self._by_asset = table
            self._mtime = mtime

    def get(self, asset_id: Any) -> dict:
        self._load()
        try:
            return self._by_asset.get(int(asset_id), {})
        except (TypeError, ValueError):
            return {}

    def describe(self, item: dict) -> dict:
        """One card, as a row the page can draw without further lookups."""
        card = self.get(item.get("assetId"))
        return {
            "id": item.get("id"),
            "assetId": item.get("assetId"),
            "name": card.get("name") or "",
            "rating": item.get("rating") or card.get("rating") or 0,
            "position": item.get("preferredPosition") or card.get("position") or "",
            "club": card.get("club") or "",
            "league": card.get("league") or "",
            "nation": card.get("nation") or "",
            "rarity": item.get("rarity") or card.get("rarity") or "",
            "itemType": item.get("itemType") or "",
            "contract": item.get("contract"),
            "fitness": item.get("fitness"),
            "playStyle": item.get("playStyle") or 0,
            "untradeable": bool(item.get("untradeable")),
            "discardValue": item.get("discardValue") or 0,
        }


class BaseClub:
    """The cards every club starts with.

    A save is a diff -- `acquired` is only what the club gained -- so the squad
    it names cannot be resolved without the starting inventory. Built once,
    from the same code the server uses, and never written back.
    """

    def __init__(self) -> None:
        self._items: dict[int, dict] | None = None
        self._lock = threading.Lock()

    def items(self) -> dict[int, dict]:
        with self._lock:
            if self._items is None:
                self._items = self._build()
            return self._items

    def _build(self) -> dict[int, dict]:
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import fut_inventory  # noqa: PLC0415 -- optional, and slow to import
            return {int(item["id"]): item for item in fut_inventory.ClubInventory().items}
        except Exception:
            # Without it the squad shows as unresolved ids rather than nothing
            # at all, which is a better failure than refusing to serve a page.
            return {}


# What each journal event means, in one table: the section it belongs to, how
# loud it is, and the sentence to draw. `noise` events are the ones the title
# emits by the hundred -- config fetches, locstrings, decoded frames -- kept in
# the counts and out of the feed, because a feed they are in shows nothing else.
EVENTS: dict[str, dict] = {
    "connected": {"cat": "session", "verb": "Connexion", "noise": True},
    "disconnected": {"cat": "session", "verb": "Déconnexion", "noise": True},
    "authentication2_login": {"cat": "session", "verb": "Login Blaze accepté"},
    "fut_auth_identity_adopted": {"cat": "session", "verb": "Identifié"},
    "fut_ut_auth_request": {"cat": "session", "verb": "Ouverture de session FUT"},
    "fut_boot_served": {"cat": "session", "verb": "Démarrage FUT servi"},
    "fut_account_info_request": {"cat": "session", "verb": "Compte lu", "noise": True},
    "fut_club_created": {"cat": "club", "verb": "Club créé"},
    "fut_item_move": {"cat": "club", "verb": "Cartes envoyées au club"},
    "fut_consumable_applied": {"cat": "club", "verb": "Consommable appliqué"},
    "fut_trophy_item": {"cat": "club", "verb": "Trophée reçu"},
    "fut_club_consumables_request": {"cat": "club", "verb": "Consommables consultés", "noise": True},
    "fut_tasks_saved": {"cat": "club", "verb": "Objectifs enregistrés"},
    # Le matchmaking en ligne, arrivé le 21 août 2026 -- la première fois que
    # le composant 4 a servi à quelque chose. Ces lignes sont l'histoire d'un
    # match qui se cherche, se trouve, et se met en place.
    "matchmaking_started": {"cat": "match", "verb": "Recherche d'adversaire"},
    "matchmaking_cancelled": {"cat": "match", "verb": "Recherche annulée"},
    "matchmaking_timed_out": {"cat": "match", "verb": "Aucun adversaire trouvé"},
    "matchmaking_found_synthetic_opponent": {"cat": "match", "verb": "Adversaire de test trouvé", "level": "warn"},
    "game_created": {"cat": "match", "verb": "Partie créée"},
    "game_session_finalised": {"cat": "match", "verb": "Session XNet reçue"},
    "mesh_connection": {"cat": "match", "verb": "État du maillage", "noise": True},
    "mesh_complete": {"cat": "match", "verb": "Match lancé — tout le monde se voit"},
    "fut_pack_opened": {"cat": "economy", "verb": "Pack ouvert"},
    "fut_quick_sell": {"cat": "economy", "verb": "Vente rapide"},
    "fut_market_search": {"cat": "market", "verb": "Recherche sur le marché"},
    "fut_squad_saved": {"cat": "squad", "verb": "Équipe enregistrée"},
    "fut_match_created": {"cat": "match", "verb": "Match lancé"},
    "fut_match_end": {"cat": "match", "verb": "Match terminé"},
    "fut_match_reset_request": {"cat": "match", "verb": "Match réinitialisé", "noise": True},
    "fut_tournament_progress": {"cat": "match", "verb": "Coupe"},
    "fut_tournament_saved": {"cat": "match", "verb": "Coupe enregistrée"},
    "fut_season_alter": {"cat": "match", "verb": "Saison"},
    "fut_season_history": {"cat": "match", "verb": "Historique de saison", "noise": True},
    "game_report_submitted": {"cat": "match", "verb": "Rapport de match"},
    "account_state_reset": {"cat": "system", "verb": "État du compte réinitialisé"},
    "listening": {"cat": "system", "verb": "Port ouvert", "noise": True},
    "identity_http_listening": {"cat": "system", "verb": "Port HTTP ouvert", "noise": True},
    "ready": {"cat": "system", "verb": "Serveur prêt"},
    "unknown_route": {"cat": "system", "verb": "Route inconnue", "level": "warn", "noise": True},
    "identity_http_unhandled": {"cat": "system", "verb": "Requête non gérée", "level": "warn"},
    "frame_decode_error": {"cat": "system", "verb": "Trame illisible", "level": "warn"},
    "frame": {"cat": "system", "verb": "Trame Blaze", "noise": True},
    "identity_http_request": {"cat": "system", "verb": "Requête HTTP", "noise": True},
    "identity_http_redirect": {"cat": "system", "verb": "Redirection", "noise": True},
    "config_fetch": {"cat": "system", "verb": "Configuration lue", "noise": True},
    "fut_route_request": {"cat": "system", "verb": "Route FUT", "noise": True},
    "fut_locstring_request": {"cat": "system", "verb": "Textes localisés", "noise": True},
    "fut_club_response": {"cat": "system", "verb": "Réponse club", "noise": True},
    "fut_tutorial_feed_declined": {"cat": "system", "verb": "Tutoriel refusé", "noise": True},
    "user_setting_load": {"cat": "system", "verb": "Réglage lu", "noise": True},
    "user_setting_save": {"cat": "system", "verb": "Réglage écrit", "noise": True},
    "user_settings_load_all": {"cat": "system", "verb": "Réglages lus", "noise": True},
    "fut_user_info_request": {"cat": "system", "verb": "Profil lu", "noise": True},
    "fut_mode_request": {"cat": "system", "verb": "Mode demandé", "noise": True},
    "fut_settings_request": {"cat": "system", "verb": "Réglages FUT", "noise": True},
    "fut_trusted_device_request": {"cat": "system", "verb": "Appareil de confiance", "noise": True},
    "fut_image_archive": {"cat": "system", "verb": "Archive d'images", "noise": True},
    "easw_auth_request": {"cat": "session", "verb": "Authentification EASW", "noise": True},
    "fut_trade_status": {"cat": "market", "verb": "Statut des enchères", "noise": True},
    "fut_bid": {"cat": "market", "verb": "Enchère placée"},
    "fut_item_listed": {"cat": "market", "verb": "Carte mise en vente"},
    "fut_club_user_request": {"cat": "club", "verb": "Club consulté", "noise": True},
    "fut_user_actions_request": {"cat": "club", "verb": "Actions du joueur", "noise": True},
    "fut_consumable_refused": {"cat": "club", "verb": "Consommable refusé", "level": "warn"},
    "fut_icebreaker_packlist_served": {"cat": "economy", "verb": "Boutique servie", "noise": True},
    "fut_tournament_teams": {"cat": "match", "verb": "Équipes de coupe", "noise": True},
    "fut_tournament_deleted": {"cat": "match", "verb": "Coupe abandonnée"},
    "fut_phishing_question_request": {"cat": "session", "verb": "Question de sécurité", "noise": True},
    "fut_phishing_validation_request": {"cat": "session", "verb": "Validation de sécurité", "noise": True},
    "fut_empty_object_request": {"cat": "system", "verb": "Objet vide servi", "noise": True},
    "session_resumed": {"cat": "session", "verb": "Session reprise"},
    "connection_error": {"cat": "system", "verb": "Connexion coupée", "level": "warn"},
    "tls_handshake_error": {"cat": "system", "verb": "Échec TLS", "level": "warn"},
    "bridge_ready": {"cat": "system", "verb": "Pont XBDM prêt", "noise": True},
    "bridge_reply_queued": {"cat": "system", "verb": "Réponse mise en file", "noise": True},
    "bridge_error": {"cat": "system", "verb": "Erreur du pont", "level": "warn"},
    "tls_client_hello": {"cat": "system", "verb": "Handshake TLS", "level": "warn"},
}


def _best_pull(drawn: list) -> dict | None:
    best = None
    for card in drawn or []:
        if not isinstance(card, dict):
            continue
        if best is None or (card.get("rating") or 0) > (best.get("rating") or 0):
            best = card
    return best


# The identity port is open to the internet on the VPS and scanners find it
# within hours. They are not players, and counting their 404s as things the
# game asked for and did not get would bury the handful that really are.
# Everything FIFA 14 itself asks the identity port for. A blacklist of scanner
# paths was tried first and was hopeless -- they arrive with a new shape every
# hour -- where the game's own vocabulary is short and does not change.
GAME_PREFIXES = (
    "/ut/", "/fut/", "/game/", "/eaid", "/connect", "/proxy/", "/sdk/",
    "/authentication", "/nucleus", "/easw", "/core/",
)


def is_scan(path: str | None) -> bool:
    """Whether a 404 came from the internet rather than from the console."""
    if not path:
        return True
    lowered = str(path).lower()
    return not lowered.startswith(GAME_PREFIXES)


# Blaze component numbers, named. The server's own constants are the source
# for every one of these -- `fifa14_blaze_server.py` defines AUTHENTICATION,
# STATS, ROOMS and the rest -- except GameManager, which this server has no
# constant for precisely because it does not implement it. That is the one
# that matters: an online match is negotiated there, so component 4 turning up
# in the gaps below is the online modes asking to exist.
COMPONENTS = {
    1: "Authentication",
    4: "GameManager",
    5: "Redirector",
    7: "Stats",
    9: "Util",
    10: "CensusData",
    11: "Clubs",
    15: "Messaging",
    21: "Rooms",
    25: "AssociationLists",
    28: "GameReporting",
    35: "Authentication2",
    2148: "CardHouse",
    2249: "OSDK Settings",
    2268: "OSDK Online Pass",
    30722: "UserSessions",
}


def _season_label(season: Any) -> str:
    """`season` is written as a [division, round] pair, not a number."""
    if not season:
        return ""
    if isinstance(season, (list, tuple)) and len(season) == 2:
        return f"division {season[0]}, tour {season[1]}"
    return f"saison {season}"


def describe_event(record: dict) -> dict:
    """One journal line, as a row of the activity feed."""
    kind = record.get("event") or "?"
    meta = EVENTS.get(kind)
    if meta is None:
        # The server gains events faster than this table does. An unknown one
        # is shown, spelled out, rather than dropped -- a feed that silently
        # omits what it has not been taught about is worse than a clumsy line.
        meta = {"cat": "system", "verb": kind.replace("_", " ").capitalize()}
    detail = ""

    if kind == "fut_pack_opened":
        drawn = record.get("drawn") or []
        best = _best_pull(drawn)
        detail = f"{len(drawn)} cartes"
        if best:
            detail += f" — meilleure {best.get('rating')} ({best.get('rarity') or '?'})"
    elif kind == "fut_quick_sell":
        detail = f"{record.get('items')} cartes vendues"
        if record.get("coins") is not None:
            detail += f" — solde {int(record['coins']):,}".replace(",", " ")
    elif kind == "fut_club_created":
        detail = f"{record.get('club')} ({record.get('abbr')})"
    elif kind == "fut_auth_identity_adopted":
        detail = str(record.get("persona_name") or record.get("persona_id") or "")
    elif kind == "authentication2_login":
        detail = str(record.get("external_id") or "")
    elif kind == "fut_consumable_applied":
        detail = str(record.get("effect") or "")
        if record.get("targets"):
            detail += f" sur {len(record['targets'])} carte(s)"
    elif kind == "fut_item_move":
        detail = f"{record.get('club')} au club, {record.get('pending')} en attente"
    elif kind == "fut_squad_saved":
        detail = f"équipe {record.get('squad')}, {record.get('players')} joueurs"
    elif kind == "fut_match_created":
        detail = ", ".join(
            part for part in (
                f"coupe {record['tournament']}" if record.get("tournament") else "",
                _season_label(record.get("season")),
            ) if part
        ) or "match libre"
    elif kind == "fut_match_end":
        body = record.get("body")
        reason = ""
        if isinstance(body, str):
            match = re.search(r'"endReason"\s*:\s*"([^"]+)"', body)
            reason = match.group(1) if match else ""
        detail = reason or "terminé"
    elif kind == "fut_season_alter":
        detail = f"division {record.get('division')}, tour {record.get('season')}"
    elif kind == "fut_tournament_progress":
        detail = f"tour {record.get('round')}"
    elif kind == "fut_market_search":
        detail = str(record.get("query") or "")[:80]
    elif kind == "fut_trophy_item":
        detail = f"trophée {record.get('trophy')}"
    elif kind == "unknown_route":
        # A Blaze component/command pair this server does not implement. Not
        # noise: this is the list of everything the title asked for and did
        # not get, which is where the next mode to support comes from.
        component = record.get("component")
        named = COMPONENTS.get(component)
        detail = (
            f"{named or 'composant ' + str(component)}, commande {record.get('command')}"
        )
    elif kind == "identity_http_unhandled":
        detail = f"{record.get('method') or ''} {record.get('path') or ''}".strip()
        if is_scan(record.get("path")):
            meta = {**meta, "cat": "scan", "level": "info"}
            return {
                "event": kind,
                "time": record.get("time"),
                "at": parse_time(record.get("time")),
                "category": "scan",
                "level": "info",
                "title": "Scan depuis Internet",
                "detail": detail,
                "peer": record.get("peer") or "",
                "noise": True,
            }
    elif kind == "ready":
        detail = f"advertise {record.get('advertise')}, port {record.get('core_port')}"
    elif kind == "matchmaking_started":
        detail = f"topologie {record.get('topology')}, {record.get('duration_ms', 0) // 1000} s"
    elif kind == "matchmaking_found_synthetic_opponent":
        # Said out loud, always. A server that invented an opponent and did
        # not say so would be a server nobody could trust the rest of.
        detail = f"{record.get('opponent')} — inventé par le serveur"
    elif kind == "game_created":
        detail = f"{record.get('game_type') or 'partie'}, topologie {record.get('topology')}"
    elif kind == "game_session_finalised":
        detail = (
            f"{record.get('session_bytes')} octets de session XNet, "
            f"{record.get('nonce_bytes')} de nonce"
        )
    elif kind == "mesh_complete":
        detail = f"{len(record.get('peers') or [])} joueurs"

    return {
        "event": kind,
        "time": record.get("time"),
        "at": parse_time(record.get("time")),
        "category": meta.get("cat", "system"),
        "level": meta.get("level", "info"),
        "title": meta.get("verb", kind),
        "detail": detail,
        "peer": record.get("peer") or "",
        "noise": bool(meta.get("noise")),
    }


class Runtime:
    """Everything the dashboard knows, read from one install's runtime/.

    The journal is re-read when it grows and the club files when they change,
    so a page that polls sees the server move without this holding anything
    open. Nothing here writes.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtime = root / "runtime"
        self.catalogue = Catalogue(root / "server" / "fifa14_cards.json")
        self.base = BaseClub()
        self._lock = threading.Lock()
        self._journal: list[dict] = []
        self._journal_key: tuple = ()
        self._journal_read = 0.0
        self._truncated = False
        self._attribution_cache: dict | None = None
        self._attribution_key: tuple = ()

    # -- sources ---------------------------------------------------------

    @property
    def journal_path(self) -> Path:
        return self.runtime / "blaze-server.jsonl"

    def journal(self) -> list[dict]:
        """The tail of the journal, parsed, oldest first."""
        path = self.journal_path
        try:
            stat = path.stat()
        except OSError:
            return []
        key = (stat.st_size, stat.st_mtime)
        now = time.monotonic()
        with self._lock:
            fresh = key == self._journal_key and now - self._journal_read < 30.0
            if fresh:
                return self._journal
        records: list[dict] = []
        truncated = False
        try:
            with path.open("rb") as stream:
                if stat.st_size > JOURNAL_TAIL_BYTES:
                    stream.seek(stat.st_size - JOURNAL_TAIL_BYTES)
                    stream.readline()  # drop the half line the seek landed in
                    truncated = True
                for raw in stream:
                    try:
                        record = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        records.append(record)
        except OSError:
            return []
        with self._lock:
            self._journal = records
            self._journal_key = key
            self._journal_read = now
            self._truncated = truncated
        return records

    def club_paths(self) -> list[Path]:
        clubs = self.runtime / "clubs"
        if not clubs.is_dir():
            return []
        return sorted(p for p in clubs.glob("*.json") if p.stem.isdigit())

    def club(self, persona_id: int) -> dict | None:
        path = self.runtime / "clubs" / f"{int(persona_id)}.json"
        saved = read_json(path)
        return saved if isinstance(saved, dict) else None

    # -- who did what ----------------------------------------------------

    def _attribution(self) -> dict:
        """Tie journal lines to the player who caused them.

        Most events carry only `peer`, the client's address, and only a few
        carry the nucleus id. But the ones that carry both -- the identity
        adoption, the account read -- come early in a session, so walking the
        journal forward and remembering the last persona seen at each address
        names almost every line after them. Blaze frames carry a connection
        number instead, which `connected` ties back to an address.
        """
        records = self.journal()
        by_peer: dict[str, int] = {}
        by_connection: dict[Any, str] = {}
        names: dict[int, str] = {}
        first: dict[int, float] = {}
        last: dict[int, float] = {}
        last_peer: dict[int, str] = {}
        owners: list[int] = []

        for record in records:
            peer = str(record.get("peer") or "")
            address = peer.split(":")[0]
            connection = record.get("connection")
            if record.get("event") == "connected" and address:
                by_connection[connection] = address
            elif connection is not None and not address:
                address = by_connection.get(connection, "")

            persona = record.get("persona_id") or record.get("external_id")
            try:
                persona = int(persona) if persona else 0
            except (TypeError, ValueError):
                persona = 0
            if persona and address:
                by_peer[address] = persona
            elif not persona and address:
                persona = by_peer.get(address, 0)

            name = record.get("persona_name")
            if persona and name:
                names[int(persona)] = str(name)

            at = parse_time(record.get("time"))
            if persona and at:
                first.setdefault(persona, at)
                last[persona] = at
                if address:
                    last_peer[persona] = address
            owners.append(int(persona or 0))

        return {
            "owners": owners,
            "names": names,
            "first": first,
            "last": last,
            "peer": last_peer,
        }

    def attribution(self) -> dict:
        """`_attribution`, computed once per journal.

        Every view needs it and each one used to walk the whole tail again --
        five passes over ten thousand lines to draw one page. Keyed on the
        journal's size and mtime, so it is rebuilt the moment the server
        writes another line and not before.
        """
        self.journal()
        with self._lock:
            if self._attribution_cache is not None and self._attribution_key == self._journal_key:
                return self._attribution_cache
        computed = self._attribution()
        with self._lock:
            self._attribution_cache = computed
            self._attribution_key = self._journal_key
        return computed

    def names(self) -> dict[int, str]:
        """The name each persona goes by, journal first then account files."""
        found = dict(self.attribution()["names"])
        accounts = self.runtime / "accounts"
        if accounts.is_dir():
            for path in accounts.glob("*.json"):
                if not path.stem.isdigit():
                    continue
                identity = (read_json(path, {}) or {}).get("identity") or {}
                name = identity.get("persona_name")
                if name and int(path.stem) not in found:
                    found[int(path.stem)] = str(name)
        return found

    # -- views -----------------------------------------------------------

    def _pool(self, saved: dict) -> dict[int, dict]:
        """Every card a club holds: the starting inventory plus its save."""
        pool = dict(self.base.items())
        for item in saved.get("acquired") or []:
            if isinstance(item, dict) and "id" in item:
                pool[int(item["id"])] = item
        for item in saved.get("changed") or []:
            if isinstance(item, dict) and "id" in item:
                pool[int(item["id"])] = item
        for sold in saved.get("sold") or []:
            pool.pop(int(sold), None)
        return pool

    def players(self) -> list[dict]:
        marks = self.attribution()
        names = self.names()
        now = time.time()
        counts = self.per_player_counts()
        rows = []
        for path in self.club_paths():
            persona = int(path.stem)
            saved = read_json(path, {}) or {}
            pool = self._pool(saved)
            squad = [int(x) for x in (saved.get("squad") or [])]
            starters = [pool[i] for i in squad[:11] if i in pool]
            rating = round(sum(i.get("rating") or 0 for i in starters) / len(starters)) if starters else 0
            identity = saved.get("club") or {}
            seen = marks["last"].get(persona)
            rows.append({
                "persona_id": persona,
                "name": names.get(persona) or f"Joueur {persona}",
                "club": identity.get("name") or "",
                "abbr": identity.get("abbr") or "",
                "coins": int(saved.get("coins") or 0),
                "cards": len(pool),
                "acquired": len(saved.get("acquired") or []),
                "squad_size": len(squad),
                "squad_rating": rating,
                "divisions": len(saved.get("seasons") or {}),
                "cups": len(saved.get("tournaments") or {}),
                "listings": len(saved.get("listings") or {}),
                "first_seen": marks["first"].get(persona),
                "last_seen": seen,
                "online": bool(seen and now - seen < ONLINE_WINDOW),
                "peer": marks["peer"].get(persona, ""),
                "saved_at": path.stat().st_mtime,
                "packs": counts["packs"].get(persona, 0),
                "matches": counts["matches"].get(persona, 0),
                "events": counts["events"].get(persona, 0),
            })
        rows.sort(key=lambda row: (not row["online"], -(row["last_seen"] or 0)))
        return rows

    def per_player_counts(self) -> dict:
        marks = self.attribution()
        records = self.journal()
        packs: Counter = Counter()
        matches: Counter = Counter()
        events: Counter = Counter()
        for record, persona in zip(records, marks["owners"]):
            if not persona:
                continue
            kind = record.get("event")
            if not EVENTS.get(kind, {}).get("noise"):
                events[persona] += 1
            if kind == "fut_pack_opened":
                packs[persona] += 1
            elif kind == "fut_match_created":
                matches[persona] += 1
        return {"packs": packs, "matches": matches, "events": events}

    def player(self, persona_id: int) -> dict | None:
        saved = self.club(persona_id)
        if saved is None:
            return None
        pool = self._pool(saved)
        squad_ids = [int(x) for x in (saved.get("squad") or [])]
        squad = [self.catalogue.describe(pool[i]) for i in squad_ids if i in pool]
        players = [i for i in pool.values() if i.get("itemType") == "player"]
        players.sort(key=lambda item: -(item.get("rating") or 0))
        kinds = Counter(item.get("itemType") or "?" for item in pool.values())
        base = next((row for row in self.players() if row["persona_id"] == int(persona_id)), None)
        return {
            "summary": base,
            "squad": squad,
            "starters": squad[:11],
            "bench": squad[11:],
            "best": [self.catalogue.describe(item) for item in players[:24]],
            "inventory": [{"type": k, "count": v} for k, v in kinds.most_common()],
            "seasons": [
                {"key": key, **(value if isinstance(value, dict) else {})}
                for key, value in (saved.get("seasons") or {}).items()
            ],
            "tournaments": [
                {"key": key, "round": (value or {}).get("round")}
                for key, value in (saved.get("tournaments") or {}).items()
            ],
            "listings": len(saved.get("listings") or {}),
            "pending": len(saved.get("pending") or []),
            "tasks": saved.get("tasks") or {},
            "activity": self.feed(limit=60, persona_id=int(persona_id)),
        }

    def feed(self, limit: int = 120, category: str = "", persona_id: int = 0,
             verbose: bool = False) -> list[dict]:
        marks = self.attribution()
        names = self.names()
        records = self.journal()
        rows = []
        for record, persona in zip(records, marks["owners"]):
            if persona_id and persona != persona_id:
                continue
            row = describe_event(record)
            # `row` rather than the table: whether a 404 is a scan or a real
            # missing route is only knowable from the path, so the row decides.
            if not verbose and row["noise"]:
                continue
            if category and row["category"] != category:
                continue
            row["persona_id"] = persona
            row["player"] = names.get(persona) or (f"Joueur {persona}" if persona else "")
            row["count"] = 1
            # The title asks for the same thing several times in a row -- the
            # trophy route fired ten times on one screen -- and ten identical
            # lines is not ten things happening. Fold them and count instead.
            last = rows[-1] if rows else None
            if (
                last is not None
                and last["event"] == row["event"]
                and last["detail"] == row["detail"]
                and last["persona_id"] == row["persona_id"]
            ):
                last["count"] += 1
                last["time"] = row["time"]
                last["at"] = row["at"]
                continue
            rows.append(row)
        return rows[-limit:][::-1]

    def leaderboards(self, limit: int = 20) -> dict:
        """Les classements, et les matchs qui les ont produits.

        Ils ne sont pas stockés : ils sont recalculés à chaque appel, à partir
        du fichier de matchs. Un classement figé quelque part serait une
        deuxième vérité à tenir à jour, et le jour où elle diverge de la
        première c'est elle qu'on croit -- parce que c'est elle qui s'affiche.

        Les noms viennent de la liste des joueurs quand elle en a un : un
        rapport de match porte souvent un `NAME` vide, et un classement de
        numéros ne se lit pas.
        """
        store = self.record_store()
        boards = store.leaderboards(limit=limit)
        known = {
            player.get("persona_id"): player.get("name")
            for player in self.players()
            if player.get("name")
        }
        for rows in boards.values():
            for row in rows:
                if not row.get("name"):
                    row["name"] = known.get(row["persona_id"], "")
        matches = store.matches()
        return {
            "boards": boards,
            "matches": matches[-limit:][::-1],
            "played": len(matches),
        }

    def record_store(self) -> "RecordStore":
        return RecordStore(
            Path(os.environ.get("FIFA14_GAME_RECORDS", "runtime/game-records.jsonl"))
        )

    def overview(self) -> dict:
        players = self.players()
        records = self.journal()
        now = time.time()
        day = now - 86400.0
        counts: Counter = Counter()
        recent: Counter = Counter()
        cards_pulled = 0
        packs = 0
        for record in records:
            kind = record.get("event") or "?"
            counts[kind] += 1
            at = parse_time(record.get("time"))
            if at and at >= day:
                recent[kind] += 1
            if kind == "fut_pack_opened":
                packs += 1
                cards_pulled += len(record.get("drawn") or [])
        started = next(
            (parse_time(r.get("time")) for r in reversed(records) if r.get("event") == "ready"),
            None,
        )
        return {
            "now": now,
            "players": len(players),
            "online": sum(1 for row in players if row["online"]),
            "coins": sum(row["coins"] for row in players),
            "cards": sum(row["cards"] for row in players),
            "packs": packs,
            "cards_pulled": cards_pulled,
            "matches": counts.get("fut_match_created", 0),
            "matches_today": recent.get("fut_match_created", 0),
            "logins": counts.get("authentication2_login", 0),
            "events_today": sum(
                value for kind, value in recent.items()
                if not EVENTS.get(kind, {}).get("noise")
            ),
            "warnings": sum(
                value for kind, value in counts.items()
                if EVENTS.get(kind, {}).get("level") == "warn"
            ),
            "gaps": counts.get("unknown_route", 0) + counts.get("identity_http_unhandled", 0),
            "started": started,
            "uptime": (now - started) if started else None,
            "journal_bytes": self.journal_path.stat().st_size if self.journal_path.exists() else 0,
            "journal_truncated": self._truncated,
            "last_event": parse_time(records[-1].get("time")) if records else None,
        }

    def economy(self) -> dict:
        """Where the cards came from and what they were worth."""
        records = self.journal()
        rarity: Counter = Counter()
        ratings: Counter = Counter()
        best: list[dict] = []
        sold = 0
        # Packs hold kits, badges and consumables as well as players, and the
        # journal writes those with a null assetId. Counting them as cards of
        # unknown rarity put a "?" bar above every real one and drew a dozen
        # nameless gold cards, so they are counted apart.
        objects = 0
        for record in records:
            if record.get("event") == "fut_pack_opened":
                for card in record.get("drawn") or []:
                    if not isinstance(card, dict):
                        continue
                    if not card.get("assetId"):
                        objects += 1
                        continue
                    rarity[card.get("rarity") or "Commune"] += 1
                    band = (int(card.get("rating") or 0) // 5) * 5
                    ratings[band] += 1
                    best.append(card)
            elif record.get("event") == "fut_quick_sell":
                sold += int(record.get("items") or 0)
        best.sort(key=lambda card: -(card.get("rating") or 0))
        top = []
        for card in best[:15]:
            described = self.catalogue.describe({
                "assetId": card.get("assetId"),
                "rating": card.get("rating"),
                "rarity": card.get("rarity"),
                "id": card.get("id"),
            })
            top.append(described)
        return {
            "rarity": [{"rarity": k, "count": v} for k, v in rarity.most_common()],
            "ratings": [{"band": k, "count": v} for k, v in sorted(ratings.items())],
            "top": top,
            "quick_sold": sold,
            "players_pulled": len(best),
            "objects_pulled": objects,
        }

    def timeline(self, hours: int = 48) -> list[dict]:
        """Activity per hour, so the page can draw when people played."""
        records = self.journal()
        now = time.time()
        floor = now - hours * 3600.0
        buckets: dict[int, Counter] = defaultdict(Counter)
        for record in records:
            at = parse_time(record.get("time"))
            if not at or at < floor:
                continue
            kind = record.get("event") or "?"
            meta = EVENTS.get(kind, {})
            bucket = int(at // 3600) * 3600
            buckets[bucket]["all"] += 1
            if not meta.get("noise"):
                buckets[bucket][meta.get("cat", "system")] += 1
                buckets[bucket]["signal"] += 1
        start = int(floor // 3600) * 3600
        end = int(now // 3600) * 3600
        return [
            {"hour": hour, **buckets.get(hour, Counter())}
            for hour in range(start, end + 3600, 3600)
        ]

    def gaps(self) -> dict:
        """What the title asked for and did not get.

        Two lists, and they are the reason this page exists as much as the
        club views are. `unknown_route` is a Blaze component and command the
        server has no handler for; `identity_http_unhandled` is an HTTP path
        it answered 404. Both are written every time the title tries something
        unsupported, so between them they are a to-do list the game itself
        keeps -- and the next thing to implement is usually just the top row.
        """
        blaze: Counter = Counter()
        http_paths: Counter = Counter()
        for record in self.journal():
            kind = record.get("event")
            if kind == "unknown_route":
                blaze[(record.get("component"), record.get("command"))] += 1
            elif kind == "identity_http_unhandled" and not is_scan(record.get("path")):
                path = str(record.get("path") or "")
                # Ids in a path would make every request its own row.
                path = re.sub(r"/\d{3,}", "/{id}", path)
                http_paths[(record.get("method") or "GET", path)] += 1
        return {
            "blaze": [
                {
                    "component": component,
                    "name": COMPONENTS.get(component, ""),
                    "command": command,
                    "count": count,
                }
                for (component, command), count in blaze.most_common(25)
            ],
            "http": [
                {"method": method, "path": path, "count": count}
                for (method, path), count in http_paths.most_common(25)
            ],
        }

    def server_info(self) -> dict:
        records = self.journal()
        # Each start writes one `listening` line per port and then one
        # `ready`, in that order, so the ports of the run in progress are the
        # ones banked when the last `ready` went by -- not the ones after it,
        # which is what an earlier version collected and why this showed none.
        pending: list[dict] = []
        ports: list[dict] = []
        ready = None
        identity = None
        for record in records:
            kind = record.get("event")
            if kind == "listening":
                pending.append({"port": record.get("port"), "transport": record.get("transport")})
            elif kind == "identity_http_listening":
                pending.append({"port": record.get("port"), "transport": "http"})
                identity = record
            elif kind == "ready":
                ready = record
                ports, pending = pending, []
        ports = ports or pending
        seen = set()
        unique = [p for p in ports if not (p["port"] in seen or seen.add(p["port"]))]
        return {
            "ready": ready,
            "identity": identity,
            "ports": unique,
            "root": str(self.root),
            "journal": str(self.journal_path),
            "clubs": len(self.club_paths()),
        }


class Dashboard(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, runtime: Runtime, token: str) -> None:
        self.runtime = runtime
        self.token = token
        super().__init__(address, handler)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FUT14Dashboard/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    # -- plumbing --------------------------------------------------------

    def send(self, status: int, body: bytes, content_type: str,
             headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Admin-Token")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send(status, body, "application/json; charset=utf-8")

    def authorised(self, query: dict) -> bool:
        expected = self.server.token
        if not expected:
            return True
        presented = self.headers.get("X-Admin-Token") or (query.get("k") or [""])[0]
        return secrets.compare_digest(str(presented), expected)

    def do_OPTIONS(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        self.send(204, b"", "text/plain")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path.startswith("/api"):
                self.serve_api(path, query)
            else:
                self.serve_static(path)
        except BrokenPipeError:
            return
        except Exception as error:  # a dashboard that 500s is still a dashboard
            self.send_json({"error": type(error).__name__, "detail": str(error)}, 500)

    # -- routes ----------------------------------------------------------

    def serve_api(self, path: str, query: dict) -> None:
        runtime: Runtime = self.server.runtime
        if path == "/api/hello":
            # Deliberately open: the page has to be able to ask whether it
            # needs a code before it has one.
            self.send_json({
                "name": "FUT 14 Revival",
                "guarded": bool(self.server.token),
                "authorised": self.authorised(query),
            })
            return
        if not self.authorised(query):
            self.send_json({"error": "unauthorised"}, 401)
            return

        def number(key: str, fallback: int) -> int:
            try:
                return int((query.get(key) or [fallback])[0])
            except (TypeError, ValueError):
                return fallback

        if path == "/api/overview":
            self.send_json({
                "overview": runtime.overview(),
                "server": runtime.server_info(),
                "players": runtime.players(),
                "feed": runtime.feed(limit=number("limit", 40)),
                "timeline": runtime.timeline(),
            })
        elif path == "/api/leaderboards":
            self.send_json(runtime.leaderboards(limit=number("limit", 20)))
        elif path == "/api/players":
            self.send_json({"players": runtime.players()})
        elif path.startswith("/api/players/"):
            try:
                persona = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self.send_json({"error": "bad persona"}, 400)
                return
            detail = runtime.player(persona)
            if detail is None:
                self.send_json({"error": "unknown club"}, 404)
                return
            self.send_json(detail)
        elif path == "/api/feed":
            self.send_json({"feed": runtime.feed(
                limit=number("limit", 200),
                category=(query.get("category") or [""])[0],
                persona_id=number("player", 0),
                verbose=(query.get("verbose") or ["0"])[0] in {"1", "true", "yes"},
            )})
        elif path == "/api/economy":
            self.send_json(runtime.economy())
        elif path == "/api/server":
            self.send_json({
                "server": runtime.server_info(),
                "gaps": runtime.gaps(),
                "overview": runtime.overview(),
                "timeline": runtime.timeline(hours=number("hours", 48)),
                "feed": runtime.feed(limit=number("limit", 120), verbose=True),
            })
        else:
            self.send_json({"error": "no such route"}, 404)

    def serve_static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        # `web/` holds three files and no directories; refusing anything with a
        # separator in it is the whole of the traversal defence needed here.
        if "/" in name or "\\" in name or name.startswith("."):
            self.send(404, b"not found\n", "text/plain")
            return
        target = WEB / name
        if not target.is_file():
            self.send(404, b"not found\n", "text/plain")
            return
        kind, _ = mimetypes.guess_type(target.name)
        self.send(200, target.read_bytes(), kind or "application/octet-stream")


def resolve_token(runtime_dir: Path, given: str | None) -> str:
    """The code that opens the dashboard.

    `--token ''` turns the guard off, which is right on a laptop on a home
    network and wrong on a VPS with a public address. Otherwise a code is
    generated once and kept in runtime/, so a restart does not lock the owner
    out of their own dashboard.
    """
    if given is not None:
        return given
    path = runtime_dir / "dashboard-token.txt"
    existing = ""
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if existing:
        return existing
    token = secrets.token_urlsafe(9)
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="install root -- the folder holding server/ and runtime/")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--token", default=None,
                        help="access code; empty string to serve without one")
    arguments = parser.parse_args(argv)

    root = arguments.root.resolve()
    runtime = Runtime(root)
    token = resolve_token(root / "runtime", arguments.token)
    server = Dashboard((arguments.listen, arguments.port), Handler, runtime, token)
    where = f"http://{arguments.listen}:{server.server_address[1]}/"
    print(f"dashboard: {where}", flush=True)
    if token:
        print(f"dashboard: code d'accès {token}", flush=True)
        print(f"dashboard: lien direct {where}?k={token}", flush=True)
    else:
        print("dashboard: aucun code -- ouvert à qui trouve le port", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
