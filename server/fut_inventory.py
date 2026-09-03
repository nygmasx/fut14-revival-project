"""Build a FUT club inventory out of cards this build already ships.

A FUT match needs eleven real player items, and the screens before it need a
club to look at: an inventory, a squad, a kit, a badge, a stadium, a ball. None
of that can be invented from nothing -- an asset id with no art behind it draws
a blank card, and the squad screen rejects records it cannot resolve.

The card database itself (`data/db/cards_ng_db.db` inside `cards0.big`) does
not decode yet: its chunks report an LZX block type of 7 at the first symbol,
which no window size fixes. But the icebreaker pack list does decode, and it is
the same build's own data -- four starter squads of twenty-three players, each
with a real asset id, rating, rare flag, club and six attributes. 158023 is
Messi, 167397 is Neymar. Those are the cards this disc can actually draw.

So the catalogue here is not synthetic. It is the icebreaker squads, unpacked
into the item shape the FUT endpoints expect.
"""

from __future__ import annotations

import base64
import json
import os
import random
import threading
import time

# How many cards go out when the club is asked for with no count of its own.
#
# The target is 77 KB, the largest club response this console was measured
# surviving -- eighteen times across three sessions.
#
# 90 was calibrated on a response of nothing but players, at about 860 bytes a
# card. The bare response is a mix now -- see `_bounded_club`, which stopped it
# being ninety players and no consumables -- and a consumable costs about 330
# bytes, so the same 77 KB holds 130 mixed cards where it held 90. Measured,
# not scaled: 90 came out at 48.4 KB and 130 at 70.5 KB.
#
# 126 rather than 130 since the response began carrying the club's duplicate
# pairs: 130 cards plus 46 pairs measured 76.2 KB against a ceiling of 77, and
# the pairs grow with the club exactly as the cards do. 126 puts it back at
# 73.7 KB, the headroom the 130 was chosen with. Four cards off a view that is
# already truncated, and which the screen filters rather than scrolls.
#
# FIFA14_CLUB_LIMIT raises or lowers it; 0 restores the unbounded behaviour,
# which is what served 244 KB immediately before a FUT teardown.
try:
    CLUB_UNFILTERED_LIMIT = int(os.environ.get("FIFA14_CLUB_LIMIT", "126"))
except ValueError:
    CLUB_UNFILTERED_LIMIT = 126
if CLUB_UNFILTERED_LIMIT <= 0:
    CLUB_UNFILTERED_LIMIT = 10**9
from pathlib import Path


PACK_LIST = Path(__file__).resolve().parent / "icebreakerpacklist.json"

# resourceId carries the asset id in its low 24 bits and a version in the high
# byte; version 1 is the base card. The PC revival asserts the same invariant.
RESOURCE_VERSION = 0x0100_0000

# Item ids have to be unique and stable across a session, and must not collide
# with the presentation items below.
FIRST_PLAYER_ITEM_ID = 1_600_000_001

FORMATION = "f442"
CLUB_NAME_DEFAULT = "Fondateur FUT"

# Positions come from the card catalogue, keyed by asset id.
#
# Two earlier attempts were wrong and both are worth remembering. Assigning a
# position per squad slot put Messi at right back, because the pack squads are
# not in formation order -- slot 10 of the first pack is Neuer. Sending no
# position at all, on the theory that the title would resolve it from the same
# record it resolves the portrait and club badge from, made every player a
# goalkeeper: it does not fall back, it defaults.
#
# So the position has to be supplied, and it has to be right. The catalogue in
# `fifa14_cards.json` carries one per card, and its asset ids are the game's --
# Messi is 158023 with club 241 there, exactly as in the icebreaker fixture.
CARD_CATALOGUE = Path(__file__).resolve().parent / "fifa14_cards.json"

# Used only when a card is missing from the catalogue. Outfield rather than GK,
# because a wrong outfield position costs chemistry while a wrong goalkeeper
# costs the match.
FALLBACK_POSITION = "CM"

# The eleven slots of f442, in the order the squad screen walks them, each with
# the positions that can fill it from best fit to worst. The pack squads are
# not in this order -- the first pack has its goalkeeper at slot 10 -- so the
# starting eleven has to be arranged rather than taken as it comes.
F442_SLOTS = [
    ("GK", ("GK",)),
    ("LB", ("LB", "LWB", "CB")),
    ("CB", ("CB", "RB", "LB")),
    ("CB", ("CB", "CDM", "RB")),
    ("RB", ("RB", "RWB", "CB")),
    ("LM", ("LM", "LW", "LWB", "CM")),
    ("CM", ("CM", "CDM", "CAM")),
    ("CM", ("CM", "CDM", "CAM")),
    ("RM", ("RM", "RW", "RWB", "CM")),
    ("ST", ("ST", "CF", "LW", "RW")),
    ("ST", ("ST", "CF", "LW", "RW")),
]


def _arrange(players: list[dict]) -> list[dict]:
    """Put the best fit in each slot, then bench whoever is left."""
    remaining = list(players)
    eleven: list[dict] = []
    for _, preferred in F442_SLOTS:
        pick = None
        for position in preferred:
            candidates = [p for p in remaining if p["preferredPosition"] == position]
            if candidates:
                pick = max(candidates, key=lambda p: p["rating"])
                break
        if pick is None:
            # Nobody plays there; take the highest rated rather than leave the
            # slot empty, which the squad screen treats as an incomplete side.
            pick = max(remaining, key=lambda p: p["rating"])
        remaining.remove(pick)
        eleven.append(pick)
    return eleven + sorted(remaining, key=lambda p: -p["rating"])


# Leagues the Xbox build does not appear to carry. A card from one of these
# draws with "Club undefined" and the raw localisation key
# "*leagueName_abbr15_0" instead of a league name -- the title is failing to
# resolve the id, not mis-reading the card.
#
# Which leagues those are cannot be determined from here: the catalogue holds
# 42 and the console knows some subset. This list is the suspect end of that
# range -- lower divisions and regional competitions -- and is meant to be
# corrected from what the screen actually shows rather than trusted as it is.
UNRESOLVED_LEAGUES = {
    2002,   # Nacional B
    2025,   # Liga do Brasil B
    332,    # Ukrayina Liha
    336,    # Liga Postobón
    347,    # South African FL
    371,    # Scotland League
}


def _card_resolves(card: dict) -> bool:
    """Keep only cards the title can actually draw.

    A missing club or league leaves the card showing placeholder text where its
    badge and competition should be, which is worse than the card not being
    offered at all.
    """
    if not (card.get("name") or "").strip():
        return False
    if not card.get("clubId") or not card.get("nationId"):
        return False
    if card.get("leagueId") in UNRESOLVED_LEAGUES:
        return False
    if (card.get("club") or "").strip().lower() in ("", "undefined"):
        return False
    return True


def _cards_by_asset() -> dict[int, dict]:
    """The catalogue keyed by asset id.

    The icebreaker packs carry an asset id, a rating, a rare flag, a club and
    six attributes -- but no position, nation or league. Leaving those at zero
    is why a club search for, say, a Cameroonian centre back returned the whole
    squad: every card matched every nation, because every card had nation 0.
    """
    if not CARD_CATALOGUE.exists():
        return {}
    document = json.loads(CARD_CATALOGUE.read_text())
    by_asset: dict[int, dict] = {}
    for card in document.get("cards", []):
        if _card_resolves(card):
            by_asset.setdefault(int(card["assetId"]), card)
    return by_asset

# Kit, badge, stadium and ball. Without these the club has nothing to present
# and the match cannot dress either side. Asset ids are the retail defaults the
# PC revival also uses.
# What a badge calls itself on the wire.
#
# Not `badge`. The PC revival files them under the retail `custom` family --
# "badges through the retail `custom` family used by My Club statistics" -- and
# its My Club draws them. Ours sent `badge` and drew the grey placeholder a
# club shows for a card it cannot resolve, reported from the console on
# 16 August 2026 for badges and kits alike.
BADGE_WIRE_TYPE = "custom"

# What the club-search screen calls a thing -> what it is called on the wire.
# Badges differ because `custom` is what renders.
CLUB_SEARCH_TYPES = {"badge": BADGE_WIRE_TYPE}

# The staff families a bare `type=staff` search should return. The screen asks
# for each family by name and also asks for all of them at once, and nothing
# has ever carried the itemType `staff` itself.
STAFF_WIRE_TYPES = ("headCoach", "gkCoach", "fitnessCoach", "physio", "manager")


def _club_search_matches(item_type: str, asked: str) -> bool:
    """Whether a card of this type answers a search for that one.

    Case-folded, because **the screen asks in lower case and the cards do not
    carry it that way**. Journalled 25 August: `type=headcoach`,
    `type=gkcoach`, `type=fitnesscoach` against cards reading `headCoach`,
    `gkCoach`, `fitnessCoach`, so a club holding 150 of them reported none.
    CardsDLL carries both spellings of all three, which is why neither side
    looks wrong on its own.

    Same shape as the badge fault this table already existed for: the wire name
    and the search name were simply not the same word.
    """
    wire = (item_type or "").strip().lower()
    want = (asked or "").strip().lower()
    if not want:
        return True
    if want == "staff":
        return wire in {t.lower() for t in STAFF_WIRE_TYPES}
    mapped = CLUB_SEARCH_TYPES.get(want, want)
    return wire == mapped.strip().lower()


# What a new club owns: the five items it presents with, and nothing more.
#
# The Barcelona badge is here so it can be chosen, but it is NOT the club's
# default crest -- see PRESENTATION_ACTIVES.
# Only the badge. The home kit, away kit, stadium and ball are already owned --
# `PRESENTATION_ACTIVES` puts them in the club, which is what dresses it -- and
# seeding them again gave the player two of each.
#
# The badge is here because the club no longer wears one by default, so without
# this there would be no crest to choose.
CLUB_STARTER_ITEMS = [
    ("badge", 241, 6_000_000),   # FC Barcelona
]


PRESENTATION_ACTIVES = [
    {"id": 1700000001, "assetId": 14, "resourceId": 6300000, "rating": 84,
     "itemType": "kit", "itemState": "activeHomeKit", "rareflag": 1,
     "category": 0, "teamkittypetechid": 0},
    {"id": 1700000002, "assetId": 15, "resourceId": 6400001, "rating": 84,
     "itemType": "kit", "itemState": "activeAwayKit", "rareflag": 1,
     "category": 0, "teamkittypetechid": 1},
    # No active badge.
    #
    # This slot used to carry FC Barcelona at resource 6000000, so every new
    # club wore Barcelona's crest and the player had no way to tell the default
    # from a choice. Leaving the slot empty lets the client fall back to its own
    # default, which is the FIFA 14 Ultimate Team crest -- what a real new club
    # starts with, and an honest blank until the player picks.
    #
    # The Barcelona badge is still seeded and ownable, so choosing it is one
    # activation away. If a club with no active badge turns out to present
    # badly, put the entry back: it is a cosmetic slot, not a load-bearing one,
    # and the kit, stadium and ball slots below are untouched.
    {"id": 1700000004, "assetId": 6, "resourceId": 6200004, "rating": 84,
     "itemType": "stadium", "itemState": "activeStadium", "rareflag": 1,
     "category": 4, "stadiumid": 6, "StadiumId": 6},
    {"id": 1700000005, "assetId": 23, "resourceId": 8120091, "rating": 84,
     "itemType": "ball", "itemState": "activeBall", "rareflag": 1},
]


# Which `itemState` marks the active card of each club kind.
#
# `PUT /ut/game/fifa14/item/<id>` with `{"itemState":"active"}` is how the club
# screen changes a stadium, badge, ball or kit -- journalled from the console on
# 16 August 2026 while a player tried to move off Camp Nou. The same path with
# an `apply` body is a consumable application, and this server matched the path
# alone, so every stadium change was answered "item is not a consumable" and the
# screen said the FIFA servers could not be reached.
ACTIVE_STATES = {
    "stadium": "activeStadium",
    "badge": "activeBadge",
    BADGE_WIRE_TYPE: "activeBadge",
    "ball": "activeBall",
    "kit": "activeHomeKit",
}

# Every state that marks a card as the club's active one, the away kit
# included -- `ACTIVE_STATES` maps a kind to the slot activation puts it in,
# and a kit has two slots but only one of them is reachable that way.
ACTIVE_STATE_VALUES = set(ACTIVE_STATES.values()) | {"activeAwayKit"}


def activate_item(
    inventory: "ClubInventory", item_id: int,
    actions: "CardActions | None" = None,
) -> dict | None:
    """Make one club item the active stadium, badge, ball or kit.

    Returns the item, or None if it cannot be found at all or its kind has no
    active slot -- a player card has none.

    With `actions` it also looks in the purchased pile and the transfer list,
    and moves the card into the club before activating it. Activating a card
    is keeping it.

    The previously active card of the same kind goes back to `free`. A club has
    one stadium, and leaving two marked active is how a screen picks whichever
    it met first.

    Kits are the one ambiguity. There are two slots, home and away, and the
    body observed carries only `active` -- so a kit with no slot named lands in
    the home slot. If the console is ever seen sending `activeAwayKit` the
    request's own value is honoured first, below, and this note can go.
    """
    target = next(
        (item for item in inventory.items if int(item.get("id") or 0) == int(item_id)),
        None,
    )
    if target is None and actions is not None:
        # A card straight out of a pack, still in New Items. The club screen
        # offers Make Active there and the console sends the same PUT, and this
        # only ever searched the club -- so activating a kit you had just packed
        # failed, and the 400 that came back ejected the player from Ultimate
        # Team entirely.
        #
        # Activating a card is keeping it, so it moves into the club first
        # rather than being activated where it sits and lost on the next save.
        for pool in (actions.shop.pending, actions.transfer):
            for held in list(pool):
                if int(held.get("id") or 0) == int(item_id):
                    actions.move(
                        {"itemData": [{"id": item_id, "pile": PILE_CLUB}]}
                    )
                    target = next(
                        (
                            item
                            for item in inventory.items
                            if int(item.get("id") or 0) == int(item_id)
                        ),
                        None,
                    )
                    break
            if target is not None:
                break
    if target is None:
        return None
    kind = str(target.get("itemType") or "")
    state = ACTIVE_STATES.get(kind)
    if not state:
        return None
    for item in inventory.items:
        if item is not target and item.get("itemState") == state:
            item["itemState"] = "free"
    target["itemState"] = state
    return target


def squad_manager(
    inventory: "ClubInventory | None" = None, squad_id: int | None = None
) -> list[dict]:
    """What the squad document says sits in the manager slot.

    **The one the player put there.** Every squad PUT the console makes carries
    `"manager":[{"id":N}]`, and this server read the players, the name and the
    formation out of that body and ignored the manager. The slot filled on
    screen, the save was written without it, and the next launch had an empty
    slot -- reported from the console on 26 August.

    The card is looked up in the club by that id, so a manager that has been
    quick-sold or was never owned resolves to nothing rather than to a stale
    entry.

    The old worry here was invented resource ids: the club's managers used to
    carry ids made up in this file, and an invented asset id in a squad
    document is the shape that makes the squad screen reject the whole
    response rather than one item. That is no longer the case -- the 166
    managers come from the game's own database and were seen rendering in the
    club on 25 August before they were ever put in packs.

    `FIFA14_SQUAD_MANAGER=1` still puts the club's first manager in the slot
    when the player has chosen none. It is off by default: an empty slot the
    player has not filled is the truth.
    """
    club = inventory if inventory is not None else INVENTORY
    items = getattr(club, "items", [])

    chosen = 0
    if hasattr(club, "_squads"):
        wanted = squad_id if squad_id is not None else club.active_squad_id()
        entry = club._squads().get(wanted) or {}
        try:
            chosen = int(entry.get("manager") or 0)
        except (TypeError, ValueError):
            chosen = 0
    if chosen:
        for item in items:
            if item.get("id") == chosen:
                return [item]

    if os.environ.get("FIFA14_SQUAD_MANAGER", "").strip().lower() not in {
        "1", "true", "yes"
    }:
        return []
    for item in items:
        if item.get("itemType") == "manager":
            return [item]
    return []


def _presentation_items(inventory: "ClubInventory | None" = None) -> list[dict]:
    """The kit, badge, stadium and ball the club actually presents.

    This used to return `PRESENTATION_ACTIVES` verbatim -- five hardcoded cards
    with FC Barcelona's crest baked in at asset 241 -- on every squad load. So
    `activate_item` worked, the club screen agreed, the item really did become
    `activeBadge`, and the club header went on showing Barcelona forever,
    because the header reads this list and this list had never heard of the
    club. The console asks for `squad/active` immediately after every
    activation, which is how the two were seen to disagree.

    The club's own active card wins per slot. The hardcoded entry is a fallback
    for a slot nothing fills, not a default that outranks the player.
    """
    # Called with no club while the club is still being built -- this is what
    # seeds the five defaults in the first place -- and with one thereafter.
    chosen: dict[str, dict] = {}
    for item in getattr(inventory, "items", []):
        state = str(item.get("itemState") or "")
        if state in ACTIVE_STATE_VALUES:
            chosen.setdefault(state, item)

    items = []
    # The badge slot has no default: a club with no badge chosen presents none,
    # and the client draws its own FIFA 14 Ultimate Team crest. But the slot
    # still has to exist once a badge IS chosen -- dropping it from this list
    # outright meant activation worked, the club agreed, and the header went on
    # showing the FUT crest forever with no way to ever change it.
    slots = list(PRESENTATION_ACTIVES)
    active_badge = chosen.get("activeBadge")
    if active_badge is not None:
        slots.append(active_badge)

    for base in slots:
        item = dict(chosen.get(str(base["itemState"]), base))
        item["itemState"] = base["itemState"]
        item.update(
            {
                "discardValue": 0,
                "lastSalePrice": 0,
                "timestamp": issued_now(),
                "untradeable": True,
            }
        )
        items.append(item)
    return items


def issued_now() -> int:
    """When a card came into the club, as the client's card detail reads it.

    Every item shipped `"timestamp": 1` -- one second past the Unix epoch --
    so every card in the game, however it was obtained, was issued on
    **1 January 1970**. It is one of those fields nothing reads back on the
    server, so nothing here ever noticed; the console prints it.

    Called at build time rather than baked in as a constant, so a card carries
    the moment it was actually drawn, bought or seeded.
    """
    return int(time.time())


# What a quick sell pays.
#
# The value used to be `max(10, (rating - 40) ** 2 // 20)` -- a curve invented
# here, whose only input was the rating. It paid 101 coins for an 85 whatever
# the card was, so a Team of the Week and a common gold of the same rating were
# worth the same, and both were worth a fraction of retail.
#
# These are FIFA 14's published values. Each tier states a rating span and, for
# each class of card, the value at both ends of it; a card is interpolated
# across the span. Retail's own tables are ranges rather than single numbers
# for the same reason: a 75 and a 99 are both gold.
#
# The class comes from `rareflag` -- 0 on an ordinary card, 1 on a rare one, and
# something else on every special (3 Team of the Week, 11 Team of the Season,
# 12 Legend, 14 World Cup; see `server/fifa14_cards.json`). Every in-form shares
# one value regardless of which family it belongs to, which is retail behaviour
# and not a simplification made here.
QUICK_SELL_PLAYER = (
    #  rating span   ordinary       rare           in-form
    ((40, 64), (12, 19), (30, 48), (800, 1280)),
    ((65, 74), (98, 111), (228, 259), (4550, 5180)),
    ((75, 99), (300, 396), (600, 792), (9150, 12078)),
)

# Contracts, fitness, healing, training. Positioning (38) and chemistry styles
# (67) are gold-only and land inside the gold span anyway.
QUICK_SELL_CONSUMABLE = (
    ((0, 64), (3, 12)),
    ((65, 74), (13, 37)),
    ((75, 99), (32, 67)),
)


def _interpolate(span: tuple[int, int], ends: tuple[int, int], rating: int) -> int:
    """One value from a band's two ends, by where the rating falls in the span."""
    low, high = span
    start, stop = ends
    if high <= low:
        return start
    ratio = (min(max(rating, low), high) - low) / (high - low)
    return int(round(start + (stop - start) * ratio))


def discard_value(rating: int, rareflag: int = 0) -> int:
    """What a quick sell pays for one player card."""
    rating = int(rating or 0)
    if rating <= 0:
        return 0
    for span, ordinary, rare, inform in QUICK_SELL_PLAYER:
        if span[0] <= rating <= span[1]:
            if rareflag not in (0, 1):
                return _interpolate(span, inform, rating)
            return _interpolate(span, rare if rareflag else ordinary, rating)
    # Outside every stated span -- clamp to the nearest one rather than return
    # nothing. A card the table does not describe is still a card.
    span, ordinary, rare, inform = (
        QUICK_SELL_PLAYER[-1] if rating > QUICK_SELL_PLAYER[-1][0][1] else QUICK_SELL_PLAYER[0]
    )
    if rareflag not in (0, 1):
        return _interpolate(span, inform, rating)
    return _interpolate(span, rare if rareflag else ordinary, rating)


def consumable_discard_value(rating: int) -> int:
    """What a quick sell pays for one consumable."""
    rating = int(rating or 0)
    for span, ends in QUICK_SELL_CONSUMABLE:
        if span[0] <= rating <= span[1]:
            return _interpolate(span, ends, rating)
    return QUICK_SELL_CONSUMABLE[-1][1][1]


# What each of the five stat slots means, settled on the console 17 August 2026.
#
# Distinct sentinels were written into every slot and one player bio named the
# whole mapping at a glance -- 11/22/33/44/55 in `statsList`, 61..65 in
# `lifetimeStats`, and the screen came back:
#
#     Games Played   61 ( 50 / 11 )
#     Goals scored   62 ( 40 / 22 )
#     Assists         0 (  0 /  0 )
#     Yellow Cards   63 ( 30 / 33 )
#     Red Cards      64 ( 20 / 44 )
#
# So `lifetimeStats[i]` is the total, `statsList[i]` is the "Your Club" column,
# and "Other Clubs" is *computed* as the difference -- 61-11=50, 62-22=40, and
# so on. Assists is the exception: it ignores these arrays entirely and reads
# the `assists` / `lifetimeAssists` members, which is why it was the only row
# that ever worked.
#
# This is what `goals` needed. There is no `goals` member in CardsDLL's JSON
# name table, so a goal could never reach that row by name; it goes in slot 1.
STAT_SLOT_GAMES = 0
STAT_SLOT_GOALS = 1
STAT_SLOT_YELLOW = 2
STAT_SLOT_RED = 3


def style_value(index: int) -> int:
    """What goes into `playStyle` for a chemistry style.

    The member is settled -- id 382, read by the card parser at 0x891B2698 --
    and the value is not. The label the card draws comes from the disc, through
    the key `FUT_PLAYSTYLE_%d` (module 0x01FE28), so whatever goes in here is
    the number that key is built from. No style names exist anywhere in
    CardsDLL, which is why serving them as locstrings did nothing: the strings
    are in the game's own localisation data.

    `index` is the catalogue's own 0-18, from the row's `amount`. Writing that
    produced BASIC, which is the "no play style" case -- so either the disc
    numbers its styles differently, or 0-18 lands somewhere with no string.

    **Settled from the code, 2026-08-17.** The parser does not store this
    member: it passes the value through 0x891AE3F8 first, alone among the
    members around it, and that function begins

        addi   r11, r3, -0xfa      ; value - 250
        cmplwi r11, 0x17           ; must be <= 23
        bgt    ...                 ; otherwise abandon it

    So the only values it accepts are **250-273** -- the subtype ids. The
    catalogue's 0-18 and the 1-19 that followed both fell outside the range and
    were discarded, which is why the card kept drawing BASIC: not a member the
    client ignores, a value it rejects.

    That range is also 24 slots against 19 outfield styles, and a goalkeeper's
    five -- Basic, Wall, Shield, Cat, Glove -- account for the rest. **The GK
    styles are 269-273**, a range neither this catalogue nor the PC revival's
    contains.

    `FIFA14_STYLE_VALUE` keeps the rejected encodings a relaunch away:

        (default)  250-268, the card's own subtype id
        index      0-18, the catalogue's numbering -- rejected by the range
        offset1    1-19 -- likewise

    A wrong value shows the wrong style name or none. Nothing is corrupted --
    another style card overwrites it.
    """
    mode = os.environ.get("FIFA14_STYLE_VALUE", "").strip().lower()
    if mode == "index":
        return int(index)
    if mode == "offset1":
        return int(index) + 1
    return CHEMISTRY_FIRST + int(index)


def _stat_slots(*sentinels: int) -> list[dict]:
    """Five index/value slots, zeroed unless the probe is armed.

    `FIFA14_STAT_PROBE=1` puts a distinct sentinel in each one, which is how
    the mapping above was read. Off, they start at zero and `sync_stat_slots`
    fills them from the card's own counters.
    """
    probing = os.environ.get("FIFA14_STAT_PROBE", "").strip().lower() in {"1", "true", "yes"}
    return [
        {"index": index, "value": (sentinels[index] if probing else 0)}
        for index in range(5)
    ]


def sync_stat_slots(card: dict) -> None:
    """Publish a card's counters into the two arrays the bio reads.

    `statsList` is what the club did and `lifetimeStats` is the career total.
    Nothing here has a career before this club, so the two carry the same
    number and the screen's computed "Other Clubs" column comes out at zero --
    which is the truth for a card that has only ever played for you.

    Yellow and red cards stay at zero: the client's match-end payload reports
    fitness, goals and assists per player and says nothing about bookings, so
    there is no honest number to put in those slots.
    """
    values = {
        STAT_SLOT_GAMES: int(card.get("gamesPlayed") or 0),
        STAT_SLOT_GOALS: int(card.get("goals") or 0),
    }
    for member in ("statsList", "lifetimeStats"):
        slots = card.get(member)
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if isinstance(slot, dict) and slot.get("index") in values:
                slot["value"] = values[slot["index"]]


# Which pile a card sits in. Defined here rather than beside the consumable
# helpers because `_player_item` carries a pile on every card and its default
# is evaluated at definition time.
# What a player card arrives with. Seven, because that is what FIFA 14 gives:
# a card out of a pack or off the market plays seven matches and then needs a
# contract, which is the whole reason contract consumables exist and are the
# commonest card in the game.
#
# This was 99 on every card this server made, from every source. Nothing needed
# a contract, so nothing needed the contracts tab, the market's development
# category, or the apply-consumable screen -- all of which work, and all of
# which were pointless against a squad that never ran down.
DEFAULT_CONTRACT = 7

PILE_TRANSFER = 5
PILE_PURCHASED = 6
PILE_CLUB = 7


def _player_item(
    item_id: int,
    asset_id: int,
    rating: int,
    rare: int,
    play_style: int,
    team_id: int,
    attributes: list[int],
    position: str,
    item_state: str = "free",
    pile: int = PILE_CLUB,
    nation: int = 0,
    league: int = 0,
    rarity: str = "",
    contract: int = DEFAULT_CONTRACT,
    version: int = RESOURCE_VERSION,
) -> dict:
    return {
        "id": item_id,
        "assetId": asset_id,
        # The high byte is the card's version: 1 is the base card, and the
        # Team of the Week's in-forms are band 50. Same encoding, one byte up.
        "resourceId": version | asset_id,
        "rating": rating,
        "preferredPosition": position,
        "teamid": team_id,
        "leagueId": league,
        "nation": nation,
        # Kept for our own filtering; the client ignores members it does not
        # know at the top level of an item.
        "rarity": rarity,
        "itemType": "player",
        "itemState": item_state,
        "formation": FORMATION,
        # Seven matches and full fitness. Fitness stays at 99 for the reason
        # the contract no longer does: a card that cannot take the field is the
        # same as no card at all, and fitness is spent by playing rather than
        # handed out at nought.
        "contract": contract,
        "fitness": 99,
        "injuryGames": 0,
        "injuryType": "none",
        "suspension": 0,
        "training": 0,
        # `style`, not `playStyle`. Read out of CardsDLL's own JSON name
        # table on 16 August 2026: `style` is at 0x02FEB0, next to
        # `styleAttribMods`, and **`playStyle` is not in the table at all** --
        # the `playStyle` string in the binary sits at 0x013A24 beside
        # `GetPlayStyleData` and `PLAY_STYLE_ID`, which are native bindings and
        # not wire members. So every chemistry style this server applied was
        # written into a member the client never reads, which is why a card
        # carrying Hunter still drew BASIC.
        # `playStyle`, id 382 -- and the card parser genuinely reads it, at
        # 0x891B2698, in the same sequential run as `preferredPosition` (383)
        # and `lifetimeAssists` (267). Renaming this to `style` on 16 August
        # was wrong: `style` is id 506 and **nothing in the module compares
        # against it**, nor against `styleAttribMods` (507). The rename came
        # from a name-table scan bounded to 0x02A000-0x031700, and this string
        # lives at 0x013A24, outside that window -- the id table points at
        # strings scattered across the whole module, not one block.
        #
        # `style` is still sent beside it. It costs nothing (no reader consumes
        # it) and it keeps the two spellings together for whoever reads this.
        "playStyle": play_style,
        # Basic is 250, not 0. The converter at 0x891AE3F8 takes 250-273 and
        # abandons anything else, so a card built with the icebreaker's raw
        # play-style number lands outside the range and is discarded. Every
        # card starts on Basic unless a style card says otherwise.
        "playStyle": (
            int(play_style)
            if CHEMISTRY_FIRST <= int(play_style or 0) <= CHEMISTRY_FIRST + 23
            else CHEMISTRY_FIRST
        ),
        # Appearances. `gamesPlayed` is in the table (0x030BE0) and nothing
        # here ever wrote it, so the bio read 0 matches for a player who had
        # just won a cup.
        "gamesPlayed": 0,
        # A quick sell pays this. Zero everywhere meant selling a card returned
        # nothing, which is also how the balance first showed up wrong.
        "discardValue": discard_value(rating, rare),
        # "Bought For" reads `-` and "Number Of Owners" reads 0 on a card that
        # never names an owner, which is how every packed card looked. One
        # owner and no sale price is what the bio renders as "First Owner".
        "owners": 1,
        "lastSalePrice": 0,
        "timestamp": issued_now(),
        "untradeable": True,
        # Every card says which pile it is in. Kyro's canonical payload carries
        # `pile` on every item; this server sent it only on a transfer-list
        # entry, so a card the client had already cached from a pack or the club
        # carried no pile at all -- and the standalone Transfer List screen,
        # which offers "Press (A) to list this item", had nothing on the cached
        # record to act against.
        "pile": pile,
        "rareflag": rare,
        "cardsubtypeid": 1 if rare else 0,
        "assists": 0,
        "lifetimeAssists": 0,
        # The aliases Kyro's canonical payload carries after the native block.
        #
        # `itemId` above all: every card in that build has both `id` and
        # `itemId`, and this server sent only `id`. The standalone Transfer
        # List screen renders such a card and reads its state -- it prints
        # "This item is not currently listed" -- and then builds no action menu
        # for it, which is what an unresolvable item id looks like from
        # outside. `POST /auctionhouse` names the card as `itemData.id`, so
        # nothing on the wire ever needed `itemId` before and its absence went
        # unnoticed.
        #
        # The rest are the same shape: `rareFlag` and `teamId` are capitalised
        # spellings of members already sent, `definitionId` and `playerId` are
        # the base asset, and `morale`, `loyaltyBonus` and `resourceGameYear`
        # are values the build sends on every card. Kyro's file warns that
        # "FIFA 14's player parser is sensitive to this stream" and keeps the
        # native-critical members first with these aliases after, which is the
        # order kept here.
        "itemId": item_id,
        "teamId": team_id,
        "rareFlag": rare,
        "definitionId": asset_id,
        "playerId": asset_id,
        "morale": 99,
        "loyaltyBonus": 1,
        "resourceGameYear": 2014,
        "attributeList": [
            {"index": index, "value": value} for index, value in enumerate(attributes)
        ],
        # The five index/value slots, and what they mean is unknown.
        #
        # The player bio has exactly five stat rows -- Games Played, Goals,
        # Assists, Yellow Cards, Red Cards -- and these two arrays have exactly
        # five entries each, which is suggestive and is not proof. What is
        # certain is that **there is no `goals` member in CardsDLL's JSON name
        # table** (checked 16 August 2026 against the module itself), so the
        # goals a match writes cannot be reaching that row by name. `assists`
        # and `lifetimeAssists` are in the table and do render, which is why
        # assists is the only row that ever worked.
        #
        # `FIFA14_STAT_PROBE=1` fills every slot with a distinct sentinel so a
        # single look at the bio names the whole mapping -- which index feeds
        # which row, and which array feeds the "Other Clubs" column against the
        # "Your Club" one. Guessing an index costs a relaunch each; this costs
        # one, and it writes to nothing but a display array.
        "statsList": _stat_slots(11, 22, 33, 44, 55),
        "lifetimeStats": _stat_slots(61, 62, 63, 64, 65),
    }


# -- one club per player ---------------------------------------------------
#
# This server held exactly one club: one inventory, one wallet, one save file.
# With one console on a LAN that is invisible. On a server two people can
# reach it means they share a club and overwrite each other's cards, coins and
# seasons.
#
# The key was already on the wire, on every FUT request, and had only never
# been read:
#
#     Easw-Session-Data-Nucleus-Id: 2535469248587161
#
# It is the same value as `nuc` in the body of `/ut/auth`, and it names the
# *profile* rather than the console -- which is the right grain, because a FUT
# club belongs to a gamertag. A gamertag can move consoles and a console can
# hold several gamertags.
#
# What that key has to select was in module-level names -- CLUB_INVENTORY,
# WALLET, TOURNAMENT_PROGRESS and a dozen more -- read from about two hundred
# places across two files. Passing a tenant argument through all of them would
# have been a two-hundred-site edit to code where nearly every site is a
# behaviour somebody had to discover from the console first, and where a
# mistake reads as a game bug rather than as a refactor.
#
# So the names stay exactly as they are, and what they point at becomes a view
# onto whichever club the request in hand belongs to. Call sites are unchanged
# and cannot forget.

_CURRENT = threading.local()


def current_tenant() -> "Tenant":
    """The club this thread is serving.

    Falls back to the default club, which is what a single-console setup and
    the whole test suite use. Nothing has to be bound for the server to behave
    exactly as it did when one club was all there was.
    """
    tenant = getattr(_CURRENT, "tenant", None)
    if tenant is None:
        tenant = TENANTS.default()
        _CURRENT.tenant = tenant
    return tenant


def use_tenant(tenant: "Tenant | None") -> None:
    """Bind this thread to a club, or unbind it with None.

    The server is a ThreadingHTTPServer -- a thread per connection -- so "the
    request in hand" is exactly per thread. A thread that is never bound gets
    the default club rather than an error, so a half-converted path degrades
    to the old single-club behaviour instead of failing.
    """
    _CURRENT.tenant = tenant


class TenantView:
    """A module-level name that follows the current club.

    Reads and writes both go to the live object, so `WALLET.coins` and
    `WALLET.coins = 0` land on the right club without the call site knowing
    there is more than one.
    """

    __slots__ = ("_member",)

    def __init__(self, member: str) -> None:
        object.__setattr__(self, "_member", member)

    def _live(self):
        return getattr(current_tenant(), object.__getattribute__(self, "_member"))

    def __getattr__(self, name: str):
        return getattr(self._live(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._live(), name, value)

    def __repr__(self) -> str:
        return repr(self._live())


class Persona:
    """Who this club belongs to, in one place.

    FUT tells a client who it is through four channels and they have to carry
    the same persona: the /user body, /eaid/personas, and the
    EASW-Nucleus-Persona and EASW-Userid headers. The squad documents carry it
    too.

    Correcting one of them and not the others is worse than leaving them all
    wrong, and that is exactly what happened on 12 August: /user was moved off
    a flat 0 onto the console's real nucleus id while every squad document
    still said 0, and the squad screen came back with eleven blank cards. The
    data was untouched -- the server still held all 23 -- but the client will
    not show a squad that belongs to somebody else.

    So there is one value, set once when the identity is known, and every
    document reads it. Agreement by construction rather than by remembering.
    """

    def __init__(self) -> None:
        self.id: int = 0

    def adopt(self, persona_id: int | None) -> None:
        if persona_id:
            self.id = int(persona_id)


PERSONA = TenantView("persona")
CLUB_RECORD = TenantView("record")


class ClubIdentity:
    """The club's name, as the player chose it.

    `PUT /ut/game/fifa14/user/club` carries `{"clubName": ..., "clubAbbr": ...}`
    and used to be answered `{}` and forgotten. The club-creation screen
    therefore worked -- the name was accepted and shown -- and the next load
    had no club again, because every other route still reported an empty name.
    """

    def __init__(self, name: str = "", abbr: str = "FUT") -> None:
        self.name = name
        self.abbr = abbr

    def adopt(self, document: dict) -> bool:
        if not isinstance(document, dict):
            return False
        name = str(document.get("clubName") or "").strip()
        abbr = str(document.get("clubAbbr") or "").strip()
        if not name and not abbr:
            return False
        if name:
            self.name = name
        if abbr:
            self.abbr = abbr
        return True

    def state(self) -> dict:
        return {"name": self.name, "abbr": self.abbr}

    def restore(self, saved: dict | None) -> None:
        if isinstance(saved, dict):
            self.name = str(saved.get("name") or "")
            self.abbr = str(saved.get("abbr") or "FUT")


CLUB_IDENTITY = TenantView("identity")


def first_run() -> bool:
    """Whether to start with no club at all.

    The club is seeded from every captain's squad in the icebreaker pack list
    -- all four, 358 cards -- because `fcc_login2` treats an empty squad as
    fatal. That seed is also why the first-time journey never appears: the
    captain selection exists to give a new player his first squad, and a
    player who already has one has nothing to choose.

    The two cannot both be true, so this makes the seed optional rather than
    guessing which side wins. With FIFA14_FIRST_RUN=1 the club starts empty
    and the answer is whatever the console then does: refuse the login the way
    the documented constraint says, or walk the icebreaker.

    Off by default. An empty club is known to break the login for an existing
    player, which is the state anyone not running this experiment is in.
    """
    return os.environ.get("FIFA14_FIRST_RUN", "").strip().lower() in {"1", "true", "yes"}


CLUB_ITEM_TYPES = frozenset({"kit", "stadium", "ball", "custom"})

# Everything that repeats rather than stacks, which is the four cosmetic
# families plus the manager and the four staff ones.
#
# The staff families were added on 25 August, after this set was written, and
# nothing brought them in: a second David Moyes was neither marked in the pack
# nor offered for quick sell, while a second kit was. Reported from the console
# 26 August.
#
# Consumables stay out on purpose. A club is meant to pile up contracts, and
# offering to quick-sell the second one is wrong.
REPEATING_TYPES = CLUB_ITEM_TYPES | frozenset(STAFF_WIRE_TYPES)


def _repeats(item: dict) -> bool:
    """Whether a second copy of this card is a duplicate rather than a stack.

    Players and club items repeat. Consumables accumulate: a club is meant to
    pile up contracts, and offering to quick-sell the second one is wrong.
    """
    kind = item.get("itemType")
    return kind == "player" or kind in REPEATING_TYPES


def card_signature(item: dict):
    """What makes two cards the same card.

    The asset, the version and the rating. `resourceId` was the whole key, on
    the reasoning that a player's specials each carry their own -- and that is
    true of retail data and false of ours. This server builds it as
    `RESOURCE_VERSION | asset_id` with the version byte always 1, so every
    version of a player resolves to the same number and the key collapsed onto
    the asset. Which is the exact mistake DUPLICATES.md warns about, reached
    from the other end.

    What it looked like: a Team of the Year 98 reported as a repeat of a Team
    of the Year 92, a Team of the Week 74 as a repeat of a Rare Silver 73, a
    Team of the Season 84 as a repeat of a Rare Gold 79. Five such pairs in a
    club of 213. Ibra 96 and Lahm 92 were two of them.

    `rarity` is what names the version here -- "Rare Gold", "Team of the Week",
    "iMOTM" -- and the rating separates two cards of one player inside a single
    family.

    And the **club**, because a player transferred mid-season has a card at
    each one and they are not the same card. Eddie Johnson is asset 46727, Rare
    Silver, 72 at D.C. United and again at Sounders; Jermaine Jones is 112847,
    Non-Rare Gold, 77 at New England and at Schalke. Falcao, Mata, Vidic,
    Fabregas, Lewandowski and Di Maria are all Rare Gold at two clubs. Without
    the club, 2,395 pairs in the catalogue collide -- with it, three do, and
    those three agree on the club as well and are genuinely the same card.

    This became reachable only once the scraper stopped collapsing transfers
    (see `tools/fetch_wefut_cards.py`). Before that only one card of each pair
    was in the catalogue, so no pack could ever hold both.

    The club is read as `teamid`, which is what `_player_item` writes, falling
    back to the `teamId` alias `_fill_card_aliases` adds to saved cards. A card
    with no club at all normalises to 0, so two of those still match each other
    rather than being told apart by an absence.

    Two genuinely identical cards agree on all four.
    """
    # A club item is identified by its resource, not its asset. Every kit in
    # the catalogue holds asset 14 -- the art follows the resourceId -- so
    # keying on the asset would make all 861 kits the same card and report the
    # second kit you ever packed as a duplicate of the first.
    #
    # Managers and staff key the same way. Their resource is the manager's own
    # database id -- David Moyes is 1000597 -- so two of him agree and two
    # different managers do not, which is exactly what is wanted and is not
    # true of the player branch below: it reads `rarity` and `rating`, and a
    # manager carries neither in a form that tells two of them apart.
    if item.get("itemType") in REPEATING_TYPES:
        return ("club", item.get("itemType"), item.get("resourceId"))

    club = item.get("teamid")
    if club is None:
        club = item.get("teamId")
    return (
        item.get("assetId"),
        (item.get("rarity") or "").strip().lower(),
        item.get("rating"),
        int(club or 0),
    )


def club_duplicate_pairs(items: list[dict]) -> list[dict]:
    """Which of these players repeat one the club already had.

    A pack marks its own repeats before you accept them, and that marking was
    the end of it: once the card was in the club nothing said so any more. The
    club screen is where a player actually goes looking for repeats to sell,
    and it showed a club full of ordinary cards.

    The card kept as the original is the one with the smallest id, which is the
    one owned longest -- acquired cards are numbered upwards as they arrive.
    Anything else would move the marker from one copy to the other as the club
    is re-sorted, and offer to sell a different card each time the screen is
    opened.

    Marks are rewritten rather than added to, so a card whose twin has since
    been sold stops claiming to repeat a card that is not there.

    Only players. A second contract card is not a repeat of the first, it is a
    second contract -- consumables are meant to accumulate.
    """
    players = sorted(
        (item for item in items if item.get("itemType") == "player"),
        key=lambda item: item.get("id") or 0,
    )
    first: dict[tuple, int] = {}
    pairs: list[dict] = []
    for item in players:
        key = card_signature(item)
        original = first.get(key)
        if original is None:
            first[key] = item["id"]
            item.pop("duplicateItemId", None)
            continue
        item["duplicateItemId"] = original
        pairs.append({"itemId": item["id"], "duplicateItemId": original})
    return pairs


def pile_duplicate_pairs(
    pending: list[dict], owned: list[dict], loaded: set[int] | None = None
) -> list[dict]:
    """Which cards waiting in the purchased pile repeat one already owned.

    The pack screen gets its pairs in the pack response and shows the repeat
    there. The unassigned pile is a different screen with a duplicates tab of
    its own, and it was handed an empty list -- so a card that the pack itself
    had just flagged sat in that tab's absence, unremarked. Two Vargas out of
    one pack on 12 August: marked on the card, missing from the panel.

    The card kept as the original is whichever was acquired first -- a copy in
    the club always beats one still in the pile, and inside the pile the
    smaller id wins, because ids are issued upwards as cards arrive.

    Except that a copy the client can actually draw beats both. `loaded` names
    the ids the console is known to be holding, and the only set that is always
    true of is the active squad: it is fetched every session, in full, before
    any pack is opened.

    This is what "MY CURRENT ITEM" being `undefined` was. The pairing is two
    numbers -- the client is told which id repeats which -- and it then draws
    the owned card by looking that id up in its own memory. It has the squad,
    it has the pack, and it has whatever club pages somebody scrolled past;
    a card sitting at position 176 of 987 was in none of them, so the panel had
    a number and no card. Pointing at a copy it holds is the only part of that
    this server can decide.

    It also fixes something worth having on its own terms: the copy left marked
    as the duplicate is the one the club screen offers to quick-sell, and that
    should never be the one in the starting eleven.
    """
    # A bought card is put in the pile *and* in the club -- the pile alone lost
    # it, so both hold the same card under the same id. Pairing across the two
    # lists therefore has to ignore a card meeting itself, or the screen is
    # told to compare a card against itself. That is the one shape
    # DUPLICATES.md says must never go out: it froze the title outright when a
    # pack sent a bare list of its own new ids.
    #
    # Pelé, bought on 12 August, came back paired 1800000049 -> 1800000049.
    waiting = {item.get("id") for item in pending}
    held = loaded or set()
    first: dict[tuple, int] = {}
    # A card the client is holding sorts ahead of one it is not, and inside
    # each group the smaller id still wins, so the choice stays stable.
    for item in sorted(
        owned,
        key=lambda row: (row.get("id") not in held, row.get("id") or 0),
    ):
        if not _repeats(item) or item.get("id") in waiting:
            continue
        first.setdefault(card_signature(item), item["id"])
    pairs: list[dict] = []
    for item in sorted(pending, key=lambda row: row.get("id") or 0):
        # Players, club items, managers and staff all repeat. Only players did
        # until 26 August, so a second kit sitting in the pile was flagged on
        # the pack screen and not in the pile's own duplicates tab.
        if not _repeats(item):
            continue
        key = card_signature(item)
        original = first.get(key)
        if original is None or original == item.get("id"):
            first.setdefault(key, item["id"])
            item.pop("duplicateItemId", None)
            continue
        item["duplicateItemId"] = original
        pairs.append({"itemId": item["id"], "duplicateItemId": original})
    return pairs


def _bounded_club(items: list[dict]) -> list[dict]:
    """The bare club, capped, without wiping out a whole kind of card.

    Slicing the sorted list took the first `CLUB_UNFILTERED_LIMIT` cards, and
    the sort puts players first -- so the bare response was **ninety players
    and nothing else**, every consumable, kit, badge and staff card cut off.

    That is what "Pas d'élément disponible" was. Applying a consumable to a
    player reads the club the client already holds; the club it held had no
    consumable in it, so the picker had nothing to offer and never asked the
    server for more. The counts said 35 contracts and the list said none.

    Half the budget goes to players, best rated first. The rest is dealt round
    by round across every other kind the club holds, so each appears, and any
    share a kind cannot fill goes back to the others. A mixed ninety is
    smaller than ninety players, so the byte size stays under what the console
    was measured surviving.
    """
    if len(items) <= CLUB_UNFILTERED_LIMIT:
        return items

    players = [item for item in items if item.get("itemType") == "player"]
    others: dict[str, list[dict]] = {}
    for item in items:
        if item.get("itemType") == "player":
            continue
        others.setdefault(item.get("itemType") or "", []).append(item)

    share = CLUB_UNFILTERED_LIMIT // 2
    kept = players[:share]
    budget = CLUB_UNFILTERED_LIMIT - len(kept)

    queues = [list(group) for group in others.values()]
    while budget > 0 and any(queues):
        for queue in queues:
            if not queue or budget <= 0:
                continue
            kept.append(queue.pop(0))
            budget -= 1
    # Whatever the other kinds could not fill goes back to the players.
    if budget > 0:
        kept.extend(players[share:share + budget])

    # `items` was sorted before the cap and the client reads it in order.
    order = {id(item): index for index, item in enumerate(items)}
    kept.sort(key=lambda item: order[id(item)])
    return kept


def stack_consumables(items: list[dict]) -> list[dict]:
    """Collapse identical consumables into one card carrying a count.

    Retail stacks them: a club holding two Player Fitness cards shows one card
    with a **2** on it and `+20 Fitness` underneath, not two cards. This server
    held 261 consumables across 143 distinct kinds and served all 261 as
    separate cards, each with `count` 1, so a club that had opened a few packs
    scrolled for pages through repeats of the same contract.

    Only consumables stack, and only ones that are genuinely the same card --
    keyed on `resourceId`, which is the card's own database id, so a +13
    contract and a +99 contract stay apart. Players and club items never stack:
    a second Barcelona kit is a duplicate to be sold, not a quantity.

    Done on the way out rather than in the club itself. The stack borrows the
    first card's id, and every route that acts on a consumable addresses it by
    `resourceId` -- `POST /item/resource/<id>` is the apply -- so nothing
    downstream needs to know the difference. The club still holds the real
    cards, which is what keeps quick sell, apply and the save honest.
    """
    stacked: list[dict] = []
    seen: dict[int, dict] = {}
    for item in items:
        if item.get("itemType") not in CONSUMABLE_TYPES:
            stacked.append(item)
            continue
        resource = int(item.get("resourceId") or 0)
        held = seen.get(resource)
        if held is None:
            entry = dict(item)
            _set_stack_size(entry, 1)
            seen[resource] = entry
            stacked.append(entry)
        else:
            _set_stack_size(held, int(held.get("count") or 1) + 1)
    return stacked


# The members a stack size goes out under.
#
# `count` alone was not it: the club was served thirteen contract cards with
# counts of 5, 10, 37 and so on, and every card on screen still read **1**. So
# the badge reads a different member, and `count` is either for something else
# or read somewhere else.
#
# These are the candidates CardsDLL's table actually carries -- `quantity`,
# `untradeableCount` and `useCount` are all in it, alongside `count`. None is
# an invented name, which is what makes sending them together reasonable rather
# than the shotgun that froze this login twice: every one is a member the
# binary can name.
STACK_SIZE_MEMBERS = ("count", "quantity", "untradeableCount")


def _set_stack_size(item: dict, size: int) -> None:
    for member in STACK_SIZE_MEMBERS:
        item[member] = size


class ClubInventory:
    """Every card the club owns, plus the squad that starts."""

    def __init__(self, pack_list: Path = PACK_LIST, seeded: bool | None = None) -> None:
        document = json.loads(pack_list.read_text())
        packs = document["packList"]
        catalogue = _cards_by_asset()
        if seeded is None:
            seeded = not first_run()

        self.items: list[dict] = []
        self.squad: list[dict] = []
        next_id = FIRST_PLAYER_ITEM_ID
        if not seeded:
            packs = []

        # The four captain packs hold the *same* twenty-three players.
        #
        # Measured 2026-08-16: 92 squad entries across the four, 23 distinct,
        # every one of them appearing four times -- four Messis, four Falcaos,
        # four Neuers. Seeding all four therefore did not stock the club with
        # spares, it stocked it with quadruplicates, and that is where a
        # player's "why do I have so many Falcaos" comes from. Packs add their
        # own repeats on top, and none of it is visible as a duplicate on Xbox:
        # the pack screen asks CardsDLL, which answers from its own state, so no
        # server response can mark them (`docs/DUPLICATES.md`).
        #
        # Seeding one of each asset keeps whatever genuine spares the data has
        # -- if a pack ever does carry a different squad, its extra players
        # still come through -- while refusing to mint copies of one that does.
        seeded_assets: set[int] = set()
        for pack_index, pack in enumerate(packs):
            attributes = [pack[f"Attribute{n}"] for n in range(1, 7)]
            for slot, asset_id in enumerate(pack["squad"]):
                if asset_id in seeded_assets:
                    continue
                seeded_assets.add(asset_id)
                item = _player_item(
                    item_id=next_id,
                    asset_id=asset_id,
                    rating=pack["Rating"][slot],
                    rare=pack["Rare"][slot],
                    play_style=pack["playStyle"][slot],
                    team_id=pack["teamId"][slot],
                    attributes=[column[slot] for column in attributes],
                    position=(catalogue.get(asset_id) or {}).get("position")
                    or FALLBACK_POSITION,
                    nation=(catalogue.get(asset_id) or {}).get("nationId", 0),
                    league=(catalogue.get(asset_id) or {}).get("leagueId", 0),
                    rarity=(catalogue.get(asset_id) or {}).get("rarity", ""),
                )
                self.items.append(item)
                # The first pack becomes the starting squad; the rest stay in
                # the club as spares, which is what gives the transfer and club
                # screens something to show.
                if pack_index == 0:
                    self.squad.append(item)
                next_id += 1

        self.squad = _arrange(self.squad) if self.squad else []
        if not seeded:
            # Nothing else either: kits, badges, stadiums and consumables are
            # club contents too, and a club that has not been created does not
            # own them. Serving them would leave the same contradiction one
            # tab further along.
            return
        self.items.extend(_presentation_items())
        # Consumables, kits, badges, stadiums, balls and staff. Without them
        # the Consommables, Elements club and Personnel tabs are empty and
        # their filters have nothing to act on.
        self.items.extend(_club_extras())

    # -- responses -------------------------------------------------------

    def club_response(self, query: dict[str, str] | None = None) -> bytes:
        """The club, filtered the way the club-search screen asks for it.

        Its parameter names are not the market's: `level`, `nation`, `league`,
        `team`, `position`, `count`. Ignoring them returned the whole club for
        every search, so looking for a Cameroonian centre back listed everyone.

        Ordered by rating, best first, the way the market search already is.
        Unsorted, the club and the squad's player picker listed cards in the
        order the icebreaker packs happened to add them, which reads as random.
        Sorting here rather than after the filter matters because the screen
        pages: slicing an unsorted list puts arbitrary cards on page one.

        `sorted` and not `.sort` -- with no query `items` is `self.items`
        itself, and sorting in place would reorder the club everywhere else.

        Players come first. Consumables carry a `rating` of their own -- a
        playStyle sits at 99 -- so ranking the club on rating alone buried the
        best player behind five of them. Within each group the order is still
        rating first, best down.
        """

        def order(item: dict) -> tuple:
            return (
                0 if item.get("itemType") == "player" else 1,
                -item.get("rating", 0),
                item.get("name", ""),
            )

        items = stack_consumables(sorted(self.items, key=order))

        if query:

            def number(key: str) -> int | None:
                try:
                    return int(query[key])
                except (KeyError, TypeError, ValueError):
                    return None

            level = (query.get("level") or "").strip().lower()
            position = (query.get("position") or "").strip()
            nation, league = number("nation"), number("league")
            team = number("team")
            kind = (query.get("type") or "").strip().lower()

            def wanted(item: dict) -> bool:
                if kind and kind not in ("any", ""):
                    # The consumables screen asks for the family, not each
                    # kind: it reads the counts, then searches the club for
                    # "consumable". Comparing that against itemType -- which is
                    # contract, fitness, healing and so on -- matched nothing,
                    # so the screen found the counts and then no items.
                    if kind in ("consumable", "consumables"):
                        if item.get("itemType") not in CONSUMABLE_TYPES:
                            return False
                    # A badge goes out as `custom`, which is the retail family
                    # and what made it render in My Club at all -- but the club
                    # search still asks for `type=badge`, so comparing the two
                    # directly matched nothing and the badge tab was empty
                    # while the club held five. The wire name and the search
                    # name are simply not the same word.
                    elif not _club_search_matches(item.get("itemType"), kind):
                        return False
                if position and position not in ("any", ""):
                    if item.get("preferredPosition") != position:
                        return False
                if level and level not in ("any", ""):
                    if level not in (item.get("rarity") or "").lower():
                        return False
                for value, field in (
                    (nation, "nation"),
                    (league, "leagueId"),
                    (team, "teamid"),
                ):
                    if value not in (None, -1) and item.get(field) != value:
                        return False
                return True

            items = [item for item in items if wanted(item)]
            # The club search pages too, and honouring only `count` meant every
            # page returned the same first results. The parameter it uses is
            # not certain, so accept the spellings it could send.
            # Everything that matched, before the page is cut out of it. The
            # item screen's CLUB tab draws this number, and it draws it while
            # the tab is greyed out -- retail shows "CLUB 352" on a tab you
            # cannot open. So it is not the page and it is not what the client
            # happens to be holding.
            matched = len(items)
            start = 0
            for key in ("start", "skip", "offset", "from"):
                value = number(key)
                if value:
                    start = value
                    break
            count = number("count") or number("num") or 0
            items = items[start:] if start else items
            if count:
                items = items[:count]
        if not query:
            # Asked with no parameters at all, this used to return the whole
            # club in one document: 244 KB of JSON at 453 cards, growing with
            # every pack.
            #
            # Only that case. A filtered request without a count -- the club
            # screen asking for every player, say -- is left whole, because
            # bounding it would change what a search means, and the response
            # measured before the teardown was the bare one.
            #
            # The bound is the largest response the console was measured
            # surviving -- 77 KB, eighteen times across three sessions -- not a
            # guess. The one 244 KB response ever served was followed by the
            # FUT session tearing itself down and CardsDLL being unloaded.
            #
            # That is a correlation on a single observation, so this bounds an
            # unbounded response; it is not established as the fix for that
            # teardown. And it truncates: a club larger than the limit is not
            # shown whole to a screen that asked for all of it.
            matched = len(items)
            items = _bounded_club(items)
        # Worked out over the whole club, reported for what is on this page:
        # the card a repeat points at is often not in the same search result,
        # and picking the original from the page alone would name a different
        # card on every filter.
        served = {item.get("id") for item in items}
        pairs = [
            pair
            for pair in club_duplicate_pairs(self.items)
            if pair["itemId"] in served
        ]
        # How many the club holds, not how many are on this page.
        #
        # The response carried no count at all, so the CLUB tab read 0 over a
        # club of 485 -- and `club/stats` reports 485 correctly, so the tab is
        # not reading that. `total` is the member the trade pile and the market
        # both use for the same job and it is in CardsDLL's name table;
        # `totalResults` and `itemCount` are not in the table at all, so they
        # were never candidates.
        #
        # A truncated response still reports the true size: the bound exists to
        # keep one document small, not to tell the club it is smaller than it
        # is.
        return json.dumps(
            {
                "itemData": items,
                "duplicateItemIdList": pairs,
                "total": matched,
            },
            separators=(",", ":"),
        ).encode()

    def squad_list_response(self, name: str) -> bytes:
        rating = round(sum(item["rating"] for item in self.squad[:11]) / 11)
        return json.dumps(
            {
                "squad": [
                    {
                        "id": 1,
                        "squadName": name,
                        "rating": rating,
                        "chemistry": 100,
                        "formation": FORMATION,
                    }
                ]
            },
            separators=(",", ":"),
        ).encode()

    # Named sides, keyed by the id the client uses. Slot 1 is the one loaded
    # at boot; the rest are whatever has been built since. Without this there
    # was one squad and no way to add, rename or drop another.
    def _squads(self) -> dict[int, dict]:
        if not hasattr(self, "_squad_store"):
            self._squad_store: dict[int, dict] = {
                1: {
                    # Named only once a club exists. In first-run mode the
                    # name is empty here too, or squad/list keeps asserting a
                    # club the rest of the responses say has not been created.
                    "name": "" if first_run() else "Fondateur FUT",
                    "formation": FORMATION,
                    "players": [item["id"] for item in self.squad],
                }
            }
        return self._squad_store

    def rename_active_squad(self, name: str) -> None:
        """Give the side the club's name once the club has one.

        The starting squad is named after the club, so a club created after the
        squad existed left the squad list still announcing nothing.
        """
        squads = self._squads()
        active = self.active_squad_id()
        if active in squads and name:
            squads[active]["name"] = name

    def squad_ids(self) -> list[int]:
        return sorted(self._squads())

    def save_squad(
        self,
        squad_id: int,
        item_ids: list[int],
        name: str | None = None,
        formation: str | None = None,
        manager: int | None = None,
        chemistry: int | None = None,
    ) -> int:
        """Write a side, creating it if the id is new.

        A squad id of zero or one the club has never seen means "make a new
        one" -- that is how the create button asks, and refusing it is why no
        second squad could exist.
        """
        squads = self._squads()
        if not squad_id or squad_id not in squads:
            squad_id = squad_id or (max(squads) + 1 if squads else 1)
        by_id = {item["id"] for item in self.items}
        # 0 is an empty slot, not an unknown card: keep it so the eleven do not
        # shift up into each other's positions.
        kept = [
            item_id if item_id in by_id else 0
            for item_id in item_ids
        ]
        if not any(kept):
            kept = []
        entry = squads.get(squad_id, {})
        squads[squad_id] = {
            "name": name or entry.get("name") or f"Équipe {squad_id}",
            "formation": formation or entry.get("formation") or FORMATION,
            "players": kept or entry.get("players") or [],
            # The manager the player put in the slot.
            #
            # The console has been sending it all along -- every squad PUT
            # carries `"manager":[{"id":N}]`, and an assignment on 25 August
            # arrived as id 1950009476 -- and this dropped it on the floor. So
            # the slot filled on screen, the save was written without it, and
            # the next launch had no manager.
            #
            # `None` means the PUT did not mention one, and the stored value
            # stands. A zero is the player clearing the slot and is kept as a
            # zero, not treated as "unset".
            "manager": (
                int(manager) if manager is not None else entry.get("manager", 0)
            ),
            # The chemistry the **client** worked out for this side.
            #
            # This server does not compute chemistry and should not pretend to:
            # it is links by club, league and nation, the manager's own league,
            # loyalty and position, and the console already does all of it. It
            # also tells us the answer -- every squad PUT carries the number,
            # and the journals hold everything from 0 to 100.
            #
            # It was thrown away and the squad list advertised a flat 100, so
            # "Fondateur FUT" read 100 in the selector and 67 once you opened
            # it. Reported from the console 27 August.
            "chemistry": (
                int(chemistry) if chemistry is not None
                else entry.get("chemistry")
            ),
        }
        if squad_id == 1 and kept:
            self.set_squad(kept)
        return squad_id

    def delete_squad(self, squad_id: int) -> bool:
        """Drop a squad the club is not currently playing with.

        The guard used to be `squad_id == 1`, on the reading that slot 1 is the
        side the club fields. That stopped being true the moment a player built
        a second squad and made it active: on 16 August a club with squad 3
        active asked to delete squad 1 and this refused, while the route was
        not wired at all, so the console got a 404.

        The real constraint is the active squad, whatever its number -- delete
        that and the club has nothing to field. The last remaining squad is
        held for the same reason.
        """
        squads = self._squads()
        if squad_id not in squads or len(squads) <= 1:
            return False
        if squad_id == self.active_squad_id():
            return False
        del squads[squad_id]
        return True

    def squad_summaries(self) -> bytes:
        by_id = {item["id"]: item for item in self.items}
        entries = []
        for squad_id, squad in sorted(self._squads().items()):
            fielded = [by_id[i] for i in squad["players"][:11] if i in by_id]
            rating = (
                round(sum(item["rating"] for item in fielded) / len(fielded))
                if fielded
                else 0
            )
            entries.append(
                {
                    "id": squad_id,
                    "squadName": squad["name"],
                    "formation": squad["formation"],
                    "rating": rating,
                    "starRating": rating,
                    # What the console last worked out for this side.
                    #
                    # **Zero when it has never said.** 100 was the fallback
                    # until 27 August and it is not a placeholder, it is a
                    # claim -- a squad reading 100 in the selector and 67 the
                    # moment you open it, which is what was reported.
                    #
                    # The console only reports chemistry when a squad is
                    # **saved**, not when it is opened: a session that opened
                    # both squads made no squad write at all. So a side that
                    # has not been touched since this started being kept has no
                    # number, and nought is the honest way to say so. Impulsum
                    # lands in the same place -- its `Chemistry` is a plain int
                    # field, so an unsaved squad reads 0 there too.
                    #
                    # It fills in the first time the squad is saved, and any
                    # edit saves it.
                    "chemistry": int(squad.get("chemistry") or 0),
                }
            )
        return json.dumps({"squad": entries}, separators=(",", ":")).encode()

    def set_active(self, squad_id: int) -> None:
        """Remember which side the club is playing with.

        Nothing in the traffic says "make this one active" -- the choice is
        made in the screen and never sent, so it was lost on leaving FUT. What
        the client does do is load the chosen side by id, and that is taken as
        the signal here. It is an inference, not a contract.
        """
        if squad_id in self._squads():
            self.active_id = squad_id
            players = self._squads()[squad_id]["players"]
            if any(players):
                self.set_squad([i for i in players if i])

    def active_squad_id(self) -> int:
        return getattr(self, "active_id", 1)

    def squad_document(self, squad_id: int, name: str = "") -> bytes:
        """One named side, by id.

        Every squad id returned the active side, so a freshly created team came
        back holding the first team's players -- it looked pre-filled when it
        was in fact empty.
        """
        squads = self._squads()
        squad = squads.get(squad_id)
        if squad is None:
            return self.active_squad_response(name or CLUB_NAME_DEFAULT)
        by_id = {item["id"]: item for item in self.items}
        players = []
        for index in range(23):
            item_id = squad["players"][index] if index < len(squad["players"]) else 0
            item = by_id.get(item_id)
            players.append(
                {
                    "index": index,
                    # An empty slot is {"id": 0}, which is how the screen draws
                    # a gap rather than a card.
                    "itemData": item if item else {"id": 0},
                    "kitNumber": index + 1 if item and index < 11 else 0,
                }
            )
        fielded = [
            by_id[i] for i in squad["players"][:11] if i in by_id
        ]
        rating = (
            round(sum(item["rating"] for item in fielded) / len(fielded))
            if fielded
            else 0
        )
        return json.dumps(
            {
                "personaId": PERSONA.id,
                "id": squad_id,
                "squadName": squad["name"],
                "formation": squad["formation"],
                # The squad screen recomputes this for itself; what matters
                # is that the two documents agree on what the server knows.
                "chemistry": int(squad.get("chemistry") or 0),
                "starRating": rating,
                "rating": rating,
                "changed": False,
                "players": players,
                "manager": squad_manager(self, squad_id),
                "actives": _presentation_items(self),
            },
            separators=(",", ":"),
        ).encode()

    def set_squad(self, item_ids: list[int]) -> None:
        """Replace the starting eleven and bench with these cards, in order.

        The squad was the list built at load time and nothing could change it,
        so a card bought or pulled from a pack reached the club and then had
        nowhere to go: the assign screen found it among nothing it could field
        and simply backed out.
        """
        by_id = {item["id"]: item for item in self.items}
        chosen = [by_id[item_id] for item_id in item_ids if item_id in by_id]
        if chosen:
            self.squad = chosen

    def active_squad_response(self, name: str) -> bytes:
        players = []
        for index, item in enumerate(self.squad):
            players.append(
                {
                    "index": index,
                    "itemData": item,
                    # Shirt numbers for the eleven who start; the bench carries
                    # zero, as retail does.
                    "kitNumber": index + 1 if index < 11 else 0,
                }
            )
        starters = self.squad[:11] or self.squad
        rating = (
            round(sum(item["rating"] for item in starters) / len(starters))
            if starters
            else 0
        )
        return json.dumps(
            {
                "personaId": PERSONA.id,
                "id": 1,
                "squadName": name,
                "formation": FORMATION,
                # FutSquadLoadServerResponse keeps these at the root, and the
                # squad screen never emitted a save without them: nothing in
                # the journal, not even a rejected one. `changed` is the one
                # that matters -- a squad that cannot mark itself modified has
                # no reason to be written back.
                "chemistry": 100,
                "starRating": rating,
                "rating": rating,
                "changed": False,
                "players": players,
                "manager": squad_manager(self),
                "actives": _presentation_items(self),
            },
            separators=(",", ":"),
        ).encode()

    def purchased_items_response(self) -> bytes:
        return b'{"duplicateItemIdList":[],"itemData":[]}'


# -- the transfer market ---------------------------------------------------
#
# The club holds 92 cards. The catalogue holds 14019. The market is where the
# difference becomes visible: it is the one screen whose job is to show players
# you do not own.
#
# Listings are generated from the catalogue on demand rather than held, because
# 14019 standing auctions is not a market, it is a phone book. Each search
# returns the best matches for its filters.

AUCTION_DURATION = 3600
# A day, so a listing does not lapse while the screen is being read.
AUCTION_WINDOW = 86400


def _now() -> int:
    return int(time.time())


# What the club calls itself as a seller on its own auctions.
SELLER_NAME = "Fondateur FUT"

MARKET_ITEM_ID_BASE = 1_800_000_000
MARKET_TRADE_ID_BASE = 1_900_000_000


# What a card is worth, anchored rather than computed from its rating.
#
# The old formula was `(rating - 40) ** 2 * 3`, doubled for a rare. That makes
# a 99 worth 10,443 coins and an 89 worth 7,203 -- the whole market fitted
# inside a starting balance, so nothing in it was worth saving for. A card's
# value is not a smooth function of its rating: it doubles somewhere around 84
# and again around 87, and the top of the game is two orders of magnitude
# above the middle of it.
#
# These are FIFA 14-era figures, not modern FUT ones. The same table the PC
# revival uses, which is where an 89 Iniesta listing at 359,000 comes from.
MARKET_ANCHORS = {
    64: 400, 65: 500, 70: 900, 74: 1500, 75: 1600, 76: 2200, 77: 3000,
    78: 4500, 79: 6500, 80: 9000, 81: 13000, 82: 20000, 83: 30000,
    84: 45000, 85: 70000, 86: 110000, 87: 175000, 88: 275000, 89: 425000,
    90: 650000, 91: 850000, 92: 1000000, 93: 1250000, 94: 1500000,
    95: 1800000, 96: 2200000, 97: 2700000, 98: 3300000, 99: 4000000,
}

# A special is worth more than the ordinary card of the same rating, and how
# much more depends on which special it is.
MARKET_SPECIAL_MULTIPLIER = {
    "team of the week": 1.35,
    "team of the season": 1.55,
    "world cup": 1.30,
    "motm": 1.60,
    "imotm": 1.70,
    "team of the year": 2.25,
    "record breaker": 1.80,
    "legend": 2.00,
}

# A non-rare goes for less than the rare version of the same rating.
MARKET_NON_RARE_MULTIPLIER = 0.82


def _round_price(value: float) -> int:
    """To a step the market would actually quote, never below the floor."""
    price = max(150, int(round(value)))
    if price < 1000:
        step = 50
    elif price < 10000:
        step = 100
    elif price < 50000:
        step = 250
    elif price < 100000:
        step = 500
    else:
        step = 1000
    return max(150, int(round(price / step) * step))


# How many sellers are asking for the same card, and how far apart. Every
# listing on the market used to be the only one of its card and every one of
# them was priced identically, so the price-comparison screen compared one
# number with itself.
MARKET_SPREADS = {
    3: (-0.040, 0.000, 0.045),
    4: (-0.050, -0.015, 0.025, 0.065),
    5: (-0.060, -0.030, 0.000, 0.032, 0.070),
    6: (-0.065, -0.040, -0.015, 0.015, 0.045, 0.080),
    7: (-0.070, -0.045, -0.020, 0.000, 0.025, 0.055, 0.090),
}

# An hour to a day. A market where every listing expires at the same moment is
# a market nobody has to decide anything about.
MARKET_DURATIONS = (3600, 10800, 21600, 43200, 86400)

# Somebody has to be selling it.
MARKET_SELLERS = (
    "LegacyFC", "UltimateXI", "TradeKing", "OldSchoolUT",
    "MarketFC", "FootyClub", "RareGoldFC", "FUT",
)


def _market_key(card: dict) -> int:
    return int(card.get("resourceId") or card.get("assetId") or 0)


def _market_copies(card: dict) -> int:
    """How many sellers are asking for this card. Stable per card."""
    return 3 + (_market_key(card) % (max(MARKET_SPREADS) - 2))


def _card_tier(card: dict) -> str:
    """Bronze, silver or gold, the way the market's Quality filter means it.

    The rarity string settles an ordinary card -- "Non-Rare Bronze", "Rare
    Silver" -- and that is what this used to match on, as a substring. It is
    silent about every special: "Team of the Year", "Team of the Season",
    "iMOTM" and "Legend" contain none of the three words, so a search for Gold
    excluded all 296 of them, and the best cards in the game could not be found
    by the filter most likely to be looking for them.

    An in-form is a gold card. So where the name does not say, the rating does,
    on the same boundaries packs use.
    """
    rarity = (card.get("rarity") or "").lower()
    for tier in ("bronze", "silver", "gold"):
        if tier in rarity:
            return tier
    rating = int(card.get("rating") or 0)
    for tier, (low, high) in TIER_RATINGS.items():
        if low <= rating <= high:
            return tier
    return "gold"


def _market_listing_price(card: dict, index: int, count: int) -> int:
    """One seller's asking price, spread around the card's value."""
    value = _price_for(card.get("rating", 0), card.get("rareflag", 0), card)
    spread = MARKET_SPREADS.get(count)
    if not spread:
        return value
    return _round_price(value * (1.0 + spread[min(index, count - 1)]))


def _market_duration(card: dict, index: int) -> int:
    return MARKET_DURATIONS[(_market_key(card) + index * 3) % len(MARKET_DURATIONS)]


def _market_seller(card: dict, index: int) -> str:
    return MARKET_SELLERS[(_market_key(card) + index) % len(MARKET_SELLERS)]


def _price_for(rating: int, rareflag: int, card: dict | None = None) -> int:
    """What one card is worth on the market.

    The jitter is derived from the card's own resource id, not from a random
    number: the same card has to be worth the same thing on the next search,
    or a price that moved between two pages reads as a bug.
    """
    rating = max(1, min(99, int(rating)))
    if rating <= 40:
        base = 150.0
    elif rating < 64:
        base = 150.0 + (rating - 40) * 10
    else:
        base = float(MARKET_ANCHORS[max(k for k in MARKET_ANCHORS if k <= rating)])

    card = card or {}
    rarity = (card.get("rarity") or "").strip().lower()
    if rarity in MARKET_SPECIAL_MULTIPLIER:
        base *= MARKET_SPECIAL_MULTIPLIER[rarity]
    elif not rareflag:
        base *= MARKET_NON_RARE_MULTIPLIER

    resource = int(
        card.get("resourceId") or card.get("assetId") or (rating * 7919)
    )
    jitter = 0.94 + ((resource * 1103515245 + 12345) & 0xFFFF) / 65535.0 * 0.12
    return _round_price(base * jitter)


# The market's consumable tabs.
#
# The transfer market screen has PLAYERS, CONSUMABLES, CLUB ITEMS and STAFF
# across the top, and the consumable tab filters by type and by the exact
# change -- "Position Change / LF >> LW" is a search the client builds. It has
# always been able to ask; this server only ever answered with players, so
# every one of those searches came back with footballers or nothing.
#
# The console names the tab in the query it sends:
#
#     type=player                     the players tab
#     type=training&cat=position      position modifiers
#     type=clubInfo&cat=badge         club items
#     type=staff&cat=manager          staff
#
# `cat` is the family, in the same vocabulary `CONSUMABLE_CATEGORIES` already
# maps for the Apply Consumable picker, so the two screens agree on what a
# category means.
MARKET_CONSUMABLE_TYPES = {"development", "training"}

# The CLUB ITEMS and STAFF tabs. `clubInfo` is what the console asks the market
# for kits, badges, stadiums and balls; `staff` covers the manager and the four
# coach families. Both fell through to the player search and found nothing.
#
# `clubInfo` carries a `cat` naming the family -- but only for badges and kits.
# **Stadiums and balls come as types of their own**, which is why searching for
# either still turned up the player list: they were never routed here. The
# console's own queries, from the journal:
#
#     type=clubInfo&start=0&num=12&cat=badge
#     type=clubInfo&start=0&num=12&lev=bronze&cat=kit
#     type=stadium&start=0&num=12
#     type=ball&start=0&num=12
#     type=staff&start=0&num=12&cat=manager
MARKET_CLUB_ITEM_FAMILIES = {"kit", "badge", "stadium", "ball"}
MARKET_CLUB_ITEM_TYPES = {"clubinfo", "staff"} | MARKET_CLUB_ITEM_FAMILIES

# How many of each family lead the listing -- one per (tier, rare) pair, so the
# first page carries a bronze, a silver and a gold and the rare of each rather
# than a wall of one colour. The rest of the family follows behind them; the tab
# pages, and a market that holds six kits is a sample rather than a market.
MARKET_CLUB_ITEM_COPIES = 6


def _market_consumable_price(card: dict) -> int:
    """What a consumable asks on the market.

    Anchored on the quick-sell value the game itself pays, so a card is never
    worth less to buy than to sell -- multiplied up, because a market that
    priced at the discard value would be a way to launder coins rather than a
    place to shop.
    """
    discard = consumable_discard_value(int(card.get("rating") or 0))
    return max(200, int(discard) * 5)


def market_consumables(query: dict[str, str]) -> list[dict]:
    """Every consumable the market should offer for this search.

    Drawn from the same catalogue packs use, minus the families held out of it
    -- there is no sense selling a card that cannot be looked at.
    """
    wanted = (query.get("cat") or "").strip().lower()
    family = CONSUMABLE_CATEGORIES.get(wanted)

    # Quality, the same tiers a pack uses. The screen offers Bronze, Silver and
    # Gold beside the category and sends `lev`; it was read for players and
    # ignored here, so picking Bronze returned every card of the family.
    #
    # A consumable's tier is its rating, exactly as a player's is -- the
    # catalogue splits cleanly on the existing boundaries, with player training
    # at 55 and 64 bronze, 65 and 74 silver, 85 and 95 gold.
    level = (query.get("lev") or query.get("level") or "").strip().lower()
    span = TIER_RATINGS.get(level) if level not in ("", "any") else None

    # One particular modifier, not the whole family. The screen sends the pair
    # it is showing -- `pos=LB-LWB` for "LB >> LWB" -- and a search for one
    # was answering with all twenty.
    #
    # `from` and `to` are on the card. The query names them in that order, so
    # LB-LWB is the card that takes a left back and makes him a left wing back,
    # and not its opposite number, which is a different card with its own id.
    change = (query.get("pos") or query.get("position") or "").strip().upper()
    pair = None
    if change and change not in ("ANY",) and "-" in change:
        head, _, tail = change.partition("-")
        pair = (head.strip(), tail.strip())

    rows = []
    for card in _consumable_catalogue():
        kind = card["itemType"]
        if kind in UNDRAWN_CONSUMABLE_TYPES:
            continue
        if wanted and family is not None:
            if not family or kind != family:
                continue
        elif wanted and wanted not in CONSUMABLE_TYPES:
            continue
        if span is not None:
            rating = int(card.get("rating") or 0)
            if not (span[0] <= rating <= span[1]):
                continue
        if pair is not None:
            # Only a card that names a change can match one. Anything without
            # `from`/`to` is filtered out rather than let through, because the
            # screen asked for one modifier and means it.
            if (card.get("from"), card.get("to")) != pair:
                continue
        rows.append(card)
    return rows


class CardCatalogue:
    """Every card in the game, searchable."""

    _issued = 0

    @staticmethod
    def read_cards(path: Path) -> list[dict]:
        """Parse the card file. Slow, and the same answer for every club."""
        if not path.exists():
            return []
        return [
            card
            for card in json.loads(path.read_text()).get("cards", [])
            if _card_resolves(card)
        ]

    def __init__(self, path: Path = CARD_CATALOGUE) -> None:
        # Shared, not copied: see `shared_catalogue_cards`. Everything below
        # this line is per club.
        self.cards: list[dict] = shared_catalogue_cards(path)
        # Listings are generated per search, so a bid arriving later refers to
        # a trade id that no longer exists anywhere unless it is remembered.
        self.served: dict[int, dict] = {}
        # Cards already bought. Without this the market regenerates the same
        # listing on the next search and the player you just paid for is still
        # sitting there for sale.
        self.sold: set[int] = set()

    def search(self, query: dict[str, str]) -> tuple[list[dict], int]:
        """Filtered, sorted, paged. Returns the page and the full match count.

        The market's parameter names are its own -- `lev` not `level`,
        `definitionId` for one specific player, `start` and `num` for paging.
        Filtering on the names the club search uses meant none of them applied,
        and ignoring `start` meant every page returned the same forty cards.
        """

        def number(key: str) -> int | None:
            value = query.get(key)
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        definition = number("definitionId") or number("maskedDefId")
        level = (query.get("lev") or query.get("level") or "").strip().lower()
        position = (query.get("pos") or query.get("position") or "").strip()
        nation = number("nat") if query.get("nat") else number("nation")
        league = number("leag") if query.get("leag") else number("league")
        team = number("team")
        # Price, not rating. `minb`/`maxb` were read as a minimum and maximum
        # **rating**, and this screen has no rating filter at all -- it offers
        # Quality, Position, Chemistry Style, Nationality, League, Club and
        # Pricing. So a Min. Price of 1000 asked for cards rated at least a
        # thousand and emptied the market.
        #
        # `micr`/`macr` are the current-price pair and `minb`/`maxb` the buy-now
        # pair. This server does not model a bid separately from a buy now at
        # catalogue level -- both come from `_price_for`, which is stable per
        # card because its jitter comes from the resource id -- so both pairs
        # bound the same value. A card the screen shows at a price is a card
        # the price filter agrees about, which is the part that has to hold.
        min_price = number("micr") if query.get("micr") else number("minb")
        max_price = number("macr") if query.get("macr") else number("maxb")

        def wanted(card: dict) -> bool:
            if definition and card.get("assetId") != definition:
                return False
            if level and level not in ("any", ""):
                if _card_tier(card) != level:
                    return False
            if position and position not in ("any", ""):
                if card.get("position") != position:
                    return False
            for value, field in (
                (nation, "nationId"),
                (league, "leagueId"),
                (team, "clubId"),
            ):
                if value not in (None, -1) and card.get(field) != value:
                    return False
            if min_price is not None or max_price is not None:
                price = _price_for(
                    card.get("rating", 0), card.get("rareflag", 0), card
                )
                if min_price is not None and price < min_price:
                    return False
                if max_price is not None and price > max_price:
                    return False
            return True

        # No exclusion by asset. Removing a bought player took every version
        # of him off the market -- the three Benatias share one asset id, so
        # buying the 90 hid the 86 and the 84 as well. A market carries many
        # copies of the same card; that is what a market is.
        matches = [card for card in self.cards if wanted(card)]
        matches.sort(key=lambda card: (-card.get("rating", 0), card.get("name", "")))

        start = number("start") or 0
        count = number("num") or number("count") or 20
        return matches[start : start + count], len(matches)

    def auctions(self, query: dict[str, str], coins: int | None = None) -> bytes:
        kind = (query.get("type") or "").strip().lower()
        if kind in MARKET_CONSUMABLE_TYPES:
            return self.consumable_auctions(query, coins)
        if kind in MARKET_CLUB_ITEM_TYPES:
            return self.club_item_auctions(query, coins)
        page, total = self.search(query)
        listings = []
        try:
            offset = int(query.get("start") or 0)
        except ValueError:
            offset = 0
        try:
            wanted_style = int(query.get("playStyle") or 0)
        except (TypeError, ValueError):
            wanted_style = 0
        # A search for one particular player is the price-comparison screen,
        # and it expects to see what several sellers are asking. A broad
        # search is a different question and gets one listing a card.
        if len(page) == 1 and (query.get("definitionId") or query.get("maskedDefId")):
            page = [page[0]] * _market_copies(page[0])
            total = len(page)
        for index, card in enumerate(page):
            price = _market_listing_price(card, index, len(page))
            # Unique per listing served, not derived from the page position.
            # Deriving it meant buying the same slot twice produced the same
            # item id, and the club refuses an id it already holds -- which is
            # why one particular player could never be bought again while
            # everyone else could.
            CardCatalogue._issued += 1
            item = _player_item(
                item_id=MARKET_ITEM_ID_BASE + CardCatalogue._issued,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                # The chemistry style the search asked for, when it asked for
                # one. The catalogue holds no style -- a style is applied by a
                # consumable, not born with the card -- so every listing this
                # server built carried Basic, and a search for Hunter matched
                # nothing at all.
                #
                # Serving the style that was asked for is what a real market
                # looks like: those cards are there because somebody applied a
                # style and listed the result. It is the same class of thing as
                # `_market_listing_price`, which invents what a seller wants,
                # and `_market_copies`, which invents how many are selling.
                # Unfiltered, the market stays Basic as before.
                play_style=wanted_style,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                # "forSale" marks a card *you* have listed, and the screen
                # offers no bid or buy on your own listing -- pressing A did
                # nothing at all. A card on someone else's auction is "free".
                item_state="free",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
            item["untradeable"] = False
            item["owners"] = 1
            item["lastSalePrice"] = 0
            duration = _market_duration(card, index)
            listing = {
                    # Order matters. These parsers read a stream of members and
                    # an unrecognised one can end the object early, taking
                    # everything after it with it -- which is consistent with
                    # the prices showing (they come first) while "Temps
                    # restant" stayed blank and the actions panel opened empty.
                    #
                    # So the timing goes near the front, and the stray "id"
                    # member this code used to add -- which is not part of an
                    # auction record -- is gone.
                    "tradeId": MARKET_TRADE_ID_BASE + offset + index,
                    "tradeState": "active",
                    # Buying failed with "la liste a expiré" while the panel
                    # itself worked, so the expiry check is what refuses it.
                    # The bounds were this machine's clock, and the console has
                    # been offline for years -- its clock is not this one's, so
                    # any absolute comparison is a coin toss.
                    #
                    # So: a window that cannot be outside whatever the console
                    # believes the time is, and a long relative countdown.
                    # EXPIRE_TIME is the member CardsDLL names beside
                    # FUT_AUCTION_EXPIRED, and this server had never sent it --
                    # expires, startTime and endtime are none of them. Sent
                    # alongside the others rather than instead of them, since
                    # those are what made the Actions entry appear.
                    "expires": duration,
                    "EXPIRE_TIME": duration,
                    "expireTime": duration,
                    "startTime": 0,
                    "endtime": 2147483647,
                    "buyNowPrice": price,
                    # Retail opens the bidding a little under the buy-now, not
                    # at half of it: a start of half is an invitation nobody
                    # would refuse and it made every auction the same auction.
                    "startingBid": _round_price(price * 0.82),
                    "currentBid": 0,
                    "offers": 0,
                    "watched": False,
                    "bidState": "none",
                    "sellerName": _market_seller(card, index),
                    "sellerEstablished": 2013,
                    "sellerId": 1 + (_market_key(card) + index) % 999999,
                    "confidenceValue": 100,
                    "itemData": item,
            }
            self.served[listing["tradeId"]] = listing
            listings.append(listing)
        # `total` is the size of the whole result set, not of this page: it is
        # what the screen pages against.
        document = {
            "auctionInfo": listings,
            "duplicateItemIdList": [],
            "total": total,
        }
        if coins is not None:
            document.update({"credits": coins, "totalCredits": coins, "coins": coins})
        return json.dumps(document, separators=(",", ":")).encode()

    def club_item_auctions(
        self, query: dict[str, str], coins: int | None = None
    ) -> bytes:
        """The CLUB ITEMS and STAFF tabs of the transfer market.

        Both fell through to the player search and found nothing, so the two
        tabs were empty however they were filtered. The console asks for them
        the same way it asks for consumables:

            type=clubInfo&start=0&num=12&cat=badge
            type=staff&start=0&num=12&cat=manager

        `cat` names the family. A tab with no `cat` gets a few of each, which
        is what the screen shows before anything is chosen.

        **A handful of each, not the catalogue.** There are 2,035 club items
        and listing them all would make the tab a directory rather than a
        market -- the player asked for five apiece, which is enough to search,
        filter and buy against.
        """
        kind = (query.get("type") or "").strip().lower()
        wanted = (query.get("cat") or query.get("category") or "").strip().lower()
        # A stadium or a ball names itself in `type` and sends no `cat`.
        if not wanted and kind in MARKET_CLUB_ITEM_FAMILIES:
            wanted = kind

        def families() -> list[tuple[str, list[dict]]]:
            if kind == "staff":
                out = [("manager", list(manager_catalogue()))]
                by_kind: dict[str, list[dict]] = {}
                for row in staff_catalogue():
                    by_kind.setdefault(row["itemType"], []).append(row)
                out += sorted(by_kind.items())
                return out
            by_kind = {}
            for row in _clubitem_catalogue():
                by_kind.setdefault(row["itemType"], []).append(row)
            return sorted(by_kind.items())

        # `lev`, which is what the console sends -- the quality filter did
        # nothing because this read `level` alone.
        level = (query.get("lev") or query.get("level") or "").strip().lower()

        def spread(members: list[dict]) -> list[dict]:
            """A few of a family, across its qualities rather than off the top.

            Taking the first five handed back the five lowest-rated bronzes of
            every family: the catalogue is in resource order, and resource
            order is quality order. The tab then showed a wall of bronze and
            its quality filter had nothing to sort.

            One of each (tier, rare) pair first -- bronze, silver, gold, and
            the rare of each -- then whatever is left over to make up the
            count.
            """
            if level and level not in ("any", "-1", ""):
                members = [
                    row for row in members
                    if str(row.get("tier") or "").lower() == level
                ]
            seen: dict[tuple, dict] = {}
            for row in members:
                key = (row.get("tier"), row.get("rare"))
                seen.setdefault(key, row)
            # Tier first, then the rare of each, so a short page still shows a
            # bronze, a silver and a gold rather than two bronzes and a silver.
            order = {"bronze": 0, "silver": 1, "gold": 2}
            ranked = sorted(
                seen.items(),
                key=lambda kv: (int(kv[0][1] or 0), order.get(kv[0][0], 3)),
            )
            # **Everything, with one of each grade in front.**
            #
            # This used to stop at six. Six is right for the first page -- a
            # bronze, a silver and a gold, and the rare of each, so the tab
            # opens on a spread rather than a wall of one colour -- and wrong
            # for the tab, which pages: a market that holds six kits is a
            # sample, not a market.
            #
            # So the grades lead and the rest of the family follows behind
            # them, in the table's own order. That order is a standing order --
            # 6000000 is FC Barcelona, 6000600 is Drogheda United -- so page
            # one carries the crests a player is actually looking for.
            lead = [row for _, row in ranked]
            chosen = {id(row) for row in lead}
            return lead + [row for row in members if id(row) not in chosen]

        wanted_families: list[tuple[str, list[dict]]] = []
        for family, members in families():
            # `badge` is what the screen asks for; the wire type is `custom`.
            if wanted and wanted != family.lower():
                continue
            picked = spread(members)
            if picked:
                wanted_families.append((family, picked))

        # Round-robin, not one family after another. Listed in family order,
        # a page of twelve held five badges, five balls and two kits, and never
        # reached the stadiums at all -- which is what "stadiums do not show
        # up" was.
        # Round-robin, not one family after another. Listed in family order, a
        # page of twelve held six badges and six balls and never reached the
        # stadiums at all.
        rows: list[tuple[str, dict]] = []
        longest = max((len(picked) for _, picked in wanted_families), default=0)
        for index in range(longest):
            for family, picked in wanted_families:
                if index < len(picked):
                    rows.append((family, picked[index]))

        def number(key: str, fallback: int) -> int:
            try:
                return int(query.get(key) or fallback)
            except (TypeError, ValueError):
                return fallback

        start = max(0, number("start", 0))
        count = max(1, min(50, number("num", 20)))
        total = len(rows)
        page = rows[start:start + count]

        listings = []
        for family, row in page:
            CardCatalogue._issued += 1
            item_id = MARKET_ITEM_ID_BASE + CardCatalogue._issued
            if family == "manager":
                item = _manager_item(row, item_id)
                rating = int(row.get("rating") or 0)
            elif kind == "staff":
                item = _staff_item(row, item_id)
                rating = int(row.get("rating") or 0)
            else:
                item = _club_item(
                    family, int(row["assetId"]), int(row["resourceId"]), item_id,
                    rating=row.get("rating"), rare=row.get("rare"),
                    discard=row.get("discardValue"),
                )
                rating = int(row.get("rating") or 0)
            # A club item's quick-sell value is tiny -- a kit is worth 3 --
            # so anchoring on it alone priced every kit, badge, stadium and
            # ball at the 200 floor. The rating carries it instead, so an
            # 88-rated stadium costs more than a 48-rated badge, with the
            # discard value still setting the floor the way it does for a
            # consumable. A house choice, like every other price in this file.
            price = max(
                200,
                int(item.get("discardValue") or discard_value(rating)) * 5,
                rating * 10,
            )
            listings.append(
                {
                    "tradeId": MARKET_TRADE_ID_BASE + CardCatalogue._issued,
                    "itemData": item,
                    "tradeState": "active",
                    "startingBid": max(150, int(price * 0.8)),
                    "buyNowPrice": price,
                    "currentBid": 0,
                    "offers": 0,
                    "watched": False,
                    "bidState": "none",
                    "expires": AUCTION_DURATION,
                    "sellerName": "",
                    "sellerEstablished": 2013,
                    "sellerId": 0,
                    "tradeOwner": False,
                }
            )
        document: dict = {
            "auctionInfo": listings,
            "duplicateItemIdList": [],
            "total": total,
        }
        if coins is not None:
            document.update(
                {"credits": coins, "totalCredits": coins, "coins": coins}
            )
        return json.dumps(document, separators=(",", ":")).encode()

    def consumable_auctions(
        self, query: dict[str, str], coins: int | None = None
    ) -> bytes:
        """The consumables tab of the transfer market.

        One listing per catalogue card, so the tab shows what exists rather
        than what a random draw happened to produce. A player hunting a single
        ST -> CF modifier can buy it instead of opening packs until one falls
        out, which is what the screen is for.
        """
        rows = market_consumables(query)

        def number(key: str, fallback: int) -> int:
            try:
                return int(query.get(key) or fallback)
            except (TypeError, ValueError):
                return fallback

        start = max(0, number("start", 0))
        count = max(1, min(50, number("num", 20)))
        total = len(rows)
        page = rows[start:start + count]

        listings = []
        for card in page:
            CardCatalogue._issued += 1
            item_id = MARKET_ITEM_ID_BASE + CardCatalogue._issued
            item = _consumable_item(card, item_id)
            price = _market_consumable_price(card)
            trade_id = MARKET_TRADE_ID_BASE + CardCatalogue._issued
            listing = {
                "tradeId": trade_id,
                "itemData": item,
                "tradeState": "active",
                "startingBid": max(150, int(price * 0.8)),
                "buyNowPrice": price,
                "currentBid": 0,
                "offers": 0,
                "watched": False,
                "bidState": "none",
                "expires": AUCTION_DURATION,
                "sellerName": "",
                "sellerEstablished": 2013,
                "sellerId": 0,
                "confidenceValue": 100,
            }
            self.served[trade_id] = listing
            listings.append(listing)

        return json.dumps(
            {
                "auctionInfo": listings,
                "duplicateItemIdList": [],
                "total": total,
                "credits": coins or 0,
                "totalCredits": coins or 0,
                "coins": coins or 0,
            },
            separators=(",", ":"),
        ).encode()

    def status_for(self, trade_ids: list[int], coins: int) -> bytes:
        """Answer /trade/status?tradeIds=... with those auctions.

        The client polls this for the specific auction it is about to bid on.
        Answering with an empty list -- whatever was asked -- told it nothing
        about that auction, and it refused with "Auction state is invalid for
        bidding". The listing has to come back, by id.
        """
        found = [
            self.served[trade_id]
            for trade_id in trade_ids
            if trade_id in self.served
        ]
        return json.dumps(
            {
                "auctionInfo": found,
                "duplicateItemIdList": [],
                "total": len(found),
                "credits": coins,
                "totalCredits": coins,
                "coins": coins,
            },
            separators=(",", ":"),
        ).encode()


    def bid(self, trade_id: int, amount: int, wallet: "Wallet") -> tuple[bytes, dict | None]:
        """Bid on, or buy outright, a listing this server served earlier.

        Returns the reply and the item won, if the bid took it. A bid at or
        above the buy-now price ends the auction immediately, which is how the
        Buy Now button behaves; anything less is recorded as the standing bid.
        """
        listing = self.served.get(trade_id)
        if listing is None:
            return (
                json.dumps(
                    {"reason": "INVALID_REQUEST", "tradeId": trade_id},
                    separators=(",", ":"),
                ).encode(),
                None,
            )
        buy_now = int(listing.get("buyNowPrice") or 0)
        if amount > wallet.coins:
            return (
                json.dumps(
                    {"reason": "INSUFFICIENT_COINS", "credits": wallet.coins},
                    separators=(",", ":"),
                ).encode(),
                None,
            )
        wallet.debit(amount)
        won = buy_now and amount >= buy_now
        listing = dict(listing)
        listing["currentBid"] = amount
        listing["bidState"] = "highest"
        listing["tradeState"] = "closed" if won else "active"
        listing["offers"] = int(listing.get("offers") or 0) + 1
        listing["credits"] = wallet.coins
        listing["totalCredits"] = wallet.coins
        listing["coins"] = wallet.coins
        self.served[trade_id] = listing
        item = listing.get("itemData") if won else None
        if won and isinstance(item, dict) and item.get("assetId"):
            self.sold.add(int(item["assetId"]))
            # What it went for, and that it has changed hands. Without these
            # the bio said "Bought For: -" over a card bought a minute ago,
            # and the owner count never moved off the seller's.
            item["lastSalePrice"] = int(amount)
            item["owners"] = int(item.get("owners") or 1) + 1
        return json.dumps(listing, separators=(",", ":")).encode(), item



# -- the coin balance ------------------------------------------------------
#
# The header reads its balance from whatever response last carried it, not at
# login: it showed a clean zero until the first quick sell, then uninitialised
# memory, because our quick-sell reply was an empty object and the parser never
# wrote the field.
#
# `totalCredits` is in CardsDLL's JSON member table, next to `total` and
# `totalGames`. `credits` and `coins` go out beside it: an unrecognised sibling
# at the top level is skipped, so naming all three costs nothing and a wrapper
# would have broken the parse, as {"userInfo":{...}} did.

def _user_currencies(coins: int) -> dict:
    """The currencies array on `/user`. This is what fills the club header.

    Settled on the console 25 August 2026: with this array on `/user`, the
    balance is on the header at login, before any navigation. It had read zero
    there since the project began.

    Every balance member `/user` already carried is **flat** -- `credits`,
    `totalCredits`, `coins`, `funds`, `finalFunds` -- and none of them was it.
    That was measured on 16 August: the same reply wrote the win/draw/loss
    record correctly and printed zero coins beside it, so the document was
    parsed, its members were read, and the coin field simply did not bind to
    any of the five.

    It binds to the array. `FutUserCreditsServerResponse` takes lower-case
    currency names -- "COINS", which the PC reference's fixture uses, matches
    nothing -- which is why each entry carries `name`, `funds` and `finalFunds`
    rather than a bare number. `/user/credits` is built this way and is the one
    document that always filled the header; it is fetched on screen
    transitions and never during login, which is why the figure used to appear
    the moment the store was opened and not before.

    **Why it took so long to find.** Seven routes were swept across both
    shapes -- `hub`, `eventfeed`, `clubUser`, `clientdata/tutorialpopups`,
    `clientdata/userHubData`, `userdata`, `store/transaction` -- and every one
    of them is fed by `with_balance`. `/user` builds its own document, so it
    got the flat members by hand and the sweep never reached it. The route that
    was fetched twice in the login fan-out, was demonstrably parsed, and
    carried the club name, the badge and the record was the one route nobody
    tested.

    The array goes **beside** the flat members, not instead of them. Wrapping
    this document is what made the header print 0xCDCDCDCD.
    """
    return {
        "currencies": [
            {"name": "coins", "funds": int(coins), "finalFunds": int(coins)},
            {"name": "points", "funds": 0, "finalFunds": 0},
        ]
    }


STARTING_COINS = 1_000_000


class Wallet:
    """The club's coin balance, held for the life of the server."""

    def __init__(self, coins: int = STARTING_COINS) -> None:
        self.coins = coins

    def credit(self, amount: int) -> int:
        self.coins = max(0, self.coins + int(amount))
        return self.coins

    def debit(self, amount: int) -> int:
        return self.credit(-abs(int(amount)))

    def auction_state(self) -> bytes:
        """An empty trade list that still reports the balance."""
        return json.dumps(
            {
                "auctionInfo": [],
                "duplicateItemIdList": [],
                "total": 0,
                "credits": self.coins,
                "totalCredits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()

    def credits_response(self) -> bytes:
        """FutUserCreditsServerResponse, as the native parser reads it.

        The currency names are compared against the literal lower-case strings
        `coins` and `points`. Sending "COINS" -- which is what the PC
        reference's fixture uses -- matches nothing, so the balance is never
        written and the header keeps whatever it had.

        `unopenedPacks` has to be an object, not an array: the parser descends
        into it for preOrderPacks/recoveredPacks, and an array leaves it walking
        the wrong token type.
        """
        return json.dumps(
            {
                "credits": self.coins,
                "currencies": [
                    {"name": "coins", "funds": self.coins, "finalFunds": self.coins},
                    {"name": "points", "funds": 0, "finalFunds": 0},
                ],
                "unopenedPacks": {"preOrderPacks": 0, "recoveredPacks": 0},
            },
            separators=(",", ":"),
        ).encode()

    def user_info(self, club_name: str, club_abbr: str,
                  persona_id: int = 0) -> bytes:
        """FutGetUserInfo, flat -- there is no `userInfo` wrapper.

        Wrapping it is what made the club header print 0xCDCDCDCD: the parser
        did not recognise the shape and never wrote the fields at all.
        """
        return json.dumps(
            {
                # The same persona the EASW-Nucleus-Persona and EASW-Userid
                # headers carry. This was a flat 0 while both headers carried
                # the console's real nucleus id, so the four channels FUT
                # identifies a client through did not agree.
                #
                # Notes from a deployment with working online play put a name
                # to what that costs: an opponent that loads as a stub with a
                # blank eleven, and sessions that die about nineteen seconds
                # after login. There is no opponent here, but the EAS FC module
                # opens a second session against the same identity, and it has
                # been reporting itself disconnected throughout.
                "personaId": int(persona_id or PERSONA.id),
                "clubName": club_name,
                "clubAbbr": club_abbr,
                # A player who has not named his club must be allowed to. This
                # was flatly False, which tells a brand-new account the one
                # thing it most needs to do is forbidden.
                "clubNameChangeAllowed": not club_name,
                # A club that does not exist was not established in 2013.
                #
                # `fcc_login1` sends `createClub` instead of `iceBreaker` when
                # `SkipIceBreaker() || GetFUT1TeamName().IS_RETURN_USER`.
                # SkipIceBreaker was disassembled and returns 0 always -- `li
                # r3, 0` and a branch to the marshaller, two instructions -- so
                # the second term is the one that was true. The object it reads
                # is built at 0x890EECD0, which writes `CLUB_EST` from +0x5C of
                # the user record and `IS_RETURN_USER` from the byte at +0x8D.
                # Serving a founding year for a club with no name is the same
                # contradiction the club name itself was, one field along.
                "established": 2013 if club_name else 0,
                "divisionOffline": 10,
                "divisionOnline": 10,
                # The club's record, and this is where the dashboard reads it.
                #
                # These three were flat zeros -- so a club that had just won a
                # cup still showed 0-0-0 beside its badge, and the search went
                # looking in `club/stats/year` when the answer was already in
                # this document under the parser's own member names (`won`
                # 0x02F9D4, `draw` 0x030E54, `loss` 0x0308D8, read off
                # CardsDLL on 16 August 2026).
                "won": CLUB_RECORD.won,
                "draw": CLUB_RECORD.draw,
                "loss": CLUB_RECORD.lost,
                "gamesPlayed": CLUB_RECORD.played,
                "seasonTicket": False,
                "fifaPointsFromLastYear": 0,
                "fifaPointsTransferredStatus": 0,
                # `totalCredits` is the member CardsDLL's JSON table actually
                # carries (0x02FD90); `credits` and `coins` ride along because
                # an unrecognised sibling at the top level is skipped. It was
                # missing here alone -- `with_balance` adds it to every other
                # response that carries a balance, and this document builds
                # itself, so it never got one. The header reads this document
                # at login and the store's own fetch is what filled it in
                # afterwards.
                "totalCredits": self.coins,
                "coins": self.coins,
                "credits": self.coins,
                # `funds` and `finalFunds`, at the top level.
                #
                # Measured 16 August 2026: with `won`/`draw`/`loss` wired up,
                # the header printed the record correctly **and still printed
                # zero coins** -- from the same document, in the same reply. So
                # this document is parsed and its members are read; `credits`,
                # `totalCredits` and `coins` are simply not the one the coin
                # field binds to.
                #
                # These two are what is left in CardsDLL's JSON table for a
                # balance (0x030C08, 0x030C94), and this server had only ever
                # sent them nested inside the `currencies` array of
                # `user/credits` -- a document the client does not fetch during
                # login at all.
                "funds": self.coins,
                "finalFunds": self.coins,
                "points": 0,
                "fifaPoints": 0,
                **_user_currencies(self.coins),
            },
            separators=(",", ":"),
        ).encode()

    def response(self) -> bytes:
        return json.dumps(
            {
                "totalCredits": self.coins,
                "credits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()


# -- opening a pack --------------------------------------------------------

# The nine packs FIFA 14 sells. Tier decides which cards can come out, count
# how many, and rares how many of them are rare -- a gold pack that draws
# bronze cards is not a gold pack.
#
# `players` is how many of those slots hold a player. Retail sells twelve
# items and only three of them are players; the other nine are consumables and
# club items. Every pack here drew twelve players instead, which is why no
# contract, kit or manager has ever come out of one, and why the club's
# consumables tab has only ever shown what the club was seeded with. A players
# pack is the exception the name promises: all twelve.
# The store, as the player wrote it out on 26 August against the retail text.
#
# `name` is drawn in **capitals**, which is how FIFA 14's store had it, and
# `blurb` is the pack's own description -- both resolve through
# `packs/loc/storepackdescriptions.<locale>.xml`, keyed `FUT_STORE_PACK_<id>_NAME`
# and `_DESC`. The blurb used to be generated from the counts below, which is
# why it read "12 items, 3 of them players, 1 rare" instead of retail's own
# sentence.
#
# `art` is the tile's cover. Bronze is 1, silver 2, gold 3, and the special
# group has one of its own: **4**, seen rendering on this console on
# 26 August. The 4/5/6 spread came from `MarvelcoCode/Impulsum14` and answers
# the player's question about the special tiles looking different in retail;
# 5 and 6 have never been drawn here, so the whole group wears 4 until they
# are. An asset id that has not been looked at is how a tile draws NOT FOUND.
#
# The prices are a house choice, as they have always been in this file: the
# binary names the fields and says nothing about what a pack should cost.
PACK_SPECS: dict[int, dict] = {
    103: {"name": "BRONZE PACK", "tier": "bronze", "coins": 400, "points": 0,
          "count": 12, "rares": 1, "players": 3, "premium": False,
          "group": "BRONZE PACKS", "art": 1,
          "blurb": "Great value for increasing club depth. A mix of 12 items, "
                   "including players, club items and consumables, at least 10 "
                   "Bronze with 1 rare."},
    104: {"name": "PREMIUM BRONZE PACK", "tier": "bronze", "coins": 750,
          "points": 0, "count": 12, "rares": 3, "players": 3, "premium": True,
          "group": "BRONZE PACKS", "art": 1,
          "blurb": "Triple the rares of a standard bronze pack. A mix of 12 "
                   "items, including players, club items and consumables, at "
                   "least 10 Bronze with 3 rare."},
    203: {"name": "SILVER PACK", "tier": "silver", "coins": 2500, "points": 50,
          "count": 12, "rares": 1, "players": 3, "premium": False,
          "group": "SILVER PACKS", "art": 2,
          "blurb": "Great value for building mid-tiered squads. A mix of 12 "
                   "items, including players, club items and consumables, with "
                   "at least 10 Silver items and 1 rare."},
    204: {"name": "PREMIUM SILVER PACK", "tier": "silver", "coins": 3750,
          "points": 75, "count": 12, "rares": 3, "players": 3, "premium": True,
          "group": "SILVER PACKS", "art": 2,
          "blurb": "Triple the rares of a standard silver pack. A mix of 12 "
                   "items, including players and consumables, with at least 10 "
                   "Silver items and 3 rare items."},
    303: {"name": "GOLD PACK", "tier": "gold", "coins": 5000, "points": 100,
          "count": 12, "rares": 1, "players": 3, "premium": False,
          "group": "GOLD PACKS", "art": 3,
          "blurb": "A mix of 12 items, including players, contracts, stadiums, "
                   "managers, staff, fitness, healing, balls, kits, and badges. "
                   "Gold packs consist mostly of players rated 75+ in-game, and "
                   "include one rare item, such as enhanced player attributes, "
                   "longer contracts, and coveted players."},
    304: {"name": "PREMIUM GOLD PACK", "tier": "gold", "coins": 7500,
          "points": 150, "count": 12, "rares": 3, "players": 3, "premium": True,
          "group": "GOLD PACKS", "art": 3,
          "blurb": "The best pack for finding those elite players to fill out "
                   "your best squad. Includes 12 Items, mostly Gold and 3 rare."},
    305: {"name": "JUMBO PREMIUM GOLD PACK", "tier": "gold", "coins": 15000,
          "points": 300, "count": 24, "rares": 7, "players": 8, "premium": True,
          "group": "GOLD PACKS", "art": 3,
          "blurb": "A mix of 24 items - including players and consumables - "
                   "with at least 20 Gold items and 7 rare items."},
    307: {"name": "PREMIUM GOLD PLAYERS PACK", "tier": "gold", "coins": 25000,
          "points": 350, "count": 12, "rares": 3, "players": 12, "premium": True,
          "group": "GOLD PACKS", "art": 3,
          "blurb": "12 Gold players, including 3 rare player items, with no "
                   "non-player items (purely players to build out your squad)."},

    # Packs this server adds. Retail FIFA 14 had no consumables-only pack, so
    # this is not a reconstruction of anything -- it is what an offline club
    # with no store behind it needs to keep being worth playing.
    #
    # `players` 0 makes a pack all consumables and club items; `guaranteed`
    # promises that many specials rather than rolling for them.
    #
    # The plain Consumables Pack is gone, 26 August, at the player's request:
    # two consumable packs is one more than a club needs and the premium one is
    # the one worth buying.
    109: {"name": "PREMIUM CONSUMABLES PACK", "tier": "gold", "coins": 20000,
          "points": 400, "count": 24, "rares": 8, "players": 0, "premium": True,
          "group": "SPECIAL PACKS", "art": 4,
          "blurb": "24 consumable and club items, 8 of them rare. Contracts, "
                   "fitness, training, healing, kits, badges, stadiums and "
                   "balls."},

    # The special packs, rewritten 26 August. What was here -- a Team of the
    # Week pack and a Team of the Season pack -- is gone at the player's
    # request. Their covers are their own: art 4, 5 and 6 rather than the gold
    # tile the whole group used to wear.
    # `players` was 12 here until 27 August, which made this pack draw twelve
    # players -- byte for byte the Rare Players Pack at half the price, and
    # nothing its own description promises. A Rare Gold Pack is the all-rare
    # version of a Gold Pack, so it takes a Gold Pack's mix: 3 players and 9
    # consumables and club items. `rares` 12 with `count` 12 makes every slot
    # rare whatever kind it holds, extras included, which is the whole point
    # of the pack and what separates it from 307 at the same price -- 307
    # gives you all players and 3 rares, this gives you 12 rares and a mix.
    308: {"name": "RARE GOLD PACK", "tier": "gold", "coins": 25000,
          "points": 500, "count": 12, "rares": 12, "players": 3, "premium": True,
          "group": "SPECIAL PACKS", "art": 4,
          "blurb": "A full mix of 12 gold items, including players and "
                   "consumables, where every single item is rare."},
    405: {"name": "RARE PLAYERS PACK", "tier": "gold", "coins": 50000,
          "points": 1000, "count": 12, "rares": 12, "players": 12, "premium": True,
          "group": "SPECIAL PACKS", "art": 4,
          "blurb": "A pack containing 12 items, and all 12 items are rare gold "
                   "players."},
    404: {"name": "MEGA PACK", "tier": "gold", "coins": 35000,
          "points": 700, "count": 30, "rares": 18, "players": 12, "premium": True,
          "group": "SPECIAL PACKS", "art": 4,
          "blurb": "Includes 18 rare items out of the 30 total items."},
    406: {"name": "JUMBO RARE PLAYERS PACK", "tier": "gold", "coins": 100000,
          "points": 2000, "count": 24, "rares": 24, "players": 24, "premium": True,
          "group": "SPECIAL PACKS", "art": 4,
          "blurb": "The most unique top rated players, all in a single pack! "
                   "Includes 24 items, all gold, all players, and all rare."},
}

# The order the store lists its groups in, cheapest first.
GROUP_ORDER = {
    "BRONZE PACKS": 0,
    "SILVER PACKS": 1,
    "GOLD PACKS": 2,
    # The consumables group is gone, 26 August. One pack does not need a tab of
    # its own, and the premium consumables pack is a special pack by price.
    "SPECIAL PACKS": 3,
}

GOLD_PACK_ID = 304
GOLD_PACK_PRICE = PACK_SPECS[GOLD_PACK_ID]["coins"]
PACK_ITEM_ID_BASE = 1_950_000_000

# Which ratings belong to which tier, so a bronze pack cannot hand you Messi.
TIER_RATINGS = {
    "bronze": (0, 64),
    "silver": (65, 74),
    "gold": (75, 99),
}

# The ordinary cards: what a player actually collects. Everything else in the
# catalogue -- Team of the Week, Team of the Season, World Cup, Legend, MOTM,
# iMOTM, Team of the Year, Record Breaker -- is a special, and specials are
# not what a new account should be handed on its first day.
ORDINARY_RARITIES = {
    "non-rare bronze",
    "rare bronze",
    "non-rare silver",
    "rare silver",
    "non-rare gold",
    "rare gold",
}

# The three packs a new club opens with, poorest first.
STARTER_PACKS = (103, 203, 303)

# A starter gold pack should not be a jackpot. Ratings above this are dropped
# from the draw entirely rather than merely made unlikely, because "unlikely"
# on twelve cards across three packs still hands out a 90 often enough.
STARTER_RATING_CAP = 78


def is_ordinary(card: dict) -> bool:
    return (card.get("rarity") or "").strip().lower() in ORDINARY_RARITIES


# -- what comes out of a pack, and how often --------------------------------
#
# The draw used to be: split the tier's cards into `rareflag` set and unset,
# take the first `rares` slots from the first list and the rest from the
# second, uniformly. That is not a set of odds, it is an accident of what the
# catalogue happens to hold, and the accident was expensive.
#
# `rareflag` is set on a Rare Gold *and* on every special. In the gold tier
# that pool is 453 rare golds against 1120 specials, so a rare slot was a
# special seven times out of ten. Measured over 400 Gold Packs: 15% of them
# contained a special, and the family that came up most often was World Cup --
# because there are 517 World Cup cards in the gold tier and only 347 Team of
# the Week. Nothing decided any of that.
#
# So: rare and special are separated, the special is rolled once per pack
# against a stated chance, and its family is chosen by weight rather than by
# how many of each the database holds.

# Rating bands inside a tier, as weights. A uniform draw over the gold
# ordinaries is close to this by coincidence -- 862/254/66/18/4 -- but close by
# coincidence moves the moment the catalogue is edited.
RATING_BANDS: dict[str, tuple[tuple[tuple[int, int], float], ...]] = {
    "gold": (((75, 79), 72.0), ((80, 83), 20.0), ((84, 86), 6.0),
             ((87, 89), 1.7), ((90, 99), 0.3)),
    "silver": (((65, 69), 72.0), ((70, 72), 22.0), ((73, 74), 6.0)),
    "bronze": (((0, 59), 72.0), ((60, 62), 22.0), ((63, 64), 6.0)),
}

# A rare slot leans higher, and the lean is bounded rather than open.
RARE_BAND_MULTIPLIER = {(84, 86): 1.2, (87, 89): 1.45, (90, 99): 1.8}

# The chance a pack holds a special at all, and a second one having held the
# first. A second is deliberately much rarer than the first.
# Nudged up across the board on 17 August 2026, at the player's request.
# These are a house choice, not a measurement -- the binary names the fields
# and says nothing about what a pack should pay -- and a single-player club
# with nobody to trade against can carry a kinder pack than retail did.
SPECIAL_CHANCE = {
    103: 0.010, 104: 0.020,
    203: 0.025, 204: 0.045,
    303: 0.11, 304: 0.20, 305: 0.30, 307: 0.40,
    # The special group, in the order the store lists it. 109 holds no players
    # at all, so its chance is nought by construction. The odds rise with the
    # price -- they did not when the prices were set on 26 August, and a Mega
    # Pack at 35 000 was drawing better than a Rare Players Pack at 50 000.
    308: 0.45, 404: 0.55, 405: 0.70, 406: 0.80,
}
SECOND_SPECIAL_CHANCE = {303: 0.01, 304: 0.02, 305: 0.03,
                         307: 0.05, 308: 0.10, 404: 0.15, 405: 0.30,
                         406: 0.35}
MAX_SPECIALS_PER_PACK = 2
# A pack that promises more than the house limit gets what it promises;
# see `guaranteed` in PACK_SPECS.

# No pack hands out more than two cards rated 90 or better, however the bands
# fall. Odds are per card; this is the sentence about the pack.
ELITE_RATING = 90
MAX_ELITE_PER_PACK = 2

# How hard a pack tries to avoid handing out the same player twice.
DISTINCT_DRAW_ATTEMPTS = 12


def _pack_identity(card: dict) -> tuple:
    """The player and his version, as a catalogue row spells them.

    Not `card_signature`: that keys on `resourceId`, which the built
    item carries and the catalogue row it was built from does not, so
    comparing the two answered on different keys and let repeats
    through. Inside one pack, the asset and the rare flag are exactly
    "the same player, the same card".
    """
    # **The asset, and nothing else.** One player, once, per pack.
    #
    # This delegated to `card_signature`, which separates two versions of one
    # player -- it has to, or a Team of the Year 98 reads as a repeat of a Team
    # of the Year 92 in the club. Inside a single pack that is the wrong
    # question: a Mega Pack drew an 85 MOTM and an 84 MOTM of the same man, two
    # different cards by the club's reckoning and the same face twice on the
    # pack screen.
    #
    # The rare flag came off too, 26 August. Keying on asset and flag still let
    # a rare gold and a non-rare gold of one player share a pack -- fifteen
    # times in 5,600 opens -- and the player asked for the plain rule: never
    # the same player twice, ever.
    return (card.get("assetId"),)

# Which special, when there is one. By weight, not by how many of each the
# catalogue holds.
#
# Legend is zero. FUT Legends were an Xbox exclusive so they belong in a 360
# pack, but nothing here has ever drawn one and whether the card renders is
# unknown -- an unknown card on the pack screen is how screens freeze. Raise
# it deliberately, with the console in front of you.
SPECIAL_FAMILY_WEIGHTS = {
    # Team of the Week gives up some of its share so the rarer families are
    # worth opening a pack for. It is still what a special usually is.
    "team of the week": 48.0,
    "team of the season": 15.0,
    # World Cup Ultimate Team is a different mode, and this server does not
    # model it. Its 1077 cards were the largest special family in the
    # catalogue and every one of them carried a confederation instead of a
    # club -- blank crest on the console, and no club or league chemistry with
    # anyone. `tools/worldcup_cards.py split` moved them out to
    # `server/fifa14_cards_worldcup.json`; `restore` puts them back.
    #
    # The weight stays. A family with no cards is skipped by `_draw_special`,
    # which selects on `cards and weight > 0`, so this line costs nothing while
    # they are out and is correct again the moment they return.
    "world cup": 10.0,
    # The orange and green cards move furthest. There are 35 MOTM and 60
    # iMOTM in the catalogue against 768 Team of the Week, and at the old
    # weights a player could open a hundred packs without meeting either.
    "motm": 14.0,
    "imotm": 10.0,
    "team of the year": 4.0,
    "record breaker": 1.5,
    # Was 0.0, on the grounds that no Legend had ever been drawn here and
    # whether the card renders on a 360 was unknown -- and an unknown card on
    # the pack screen is how screens freeze.
    #
    # Settled on the console, 2026-08-16: a Legend was bought off the transfer
    # market and rendered correctly, art and all. FUT Legends were an Xbox
    # exclusive, so this build has them and they work.
    #
    # Held low rather than opened up. The evidence covers the market and club
    # renderers; the pack reveal screen is a different one, and it is the one
    # that freezes. 2.0 puts a Legend just above a Record Breaker and below a
    # Team of the Year -- rare enough to be an event, common enough that the
    # remaining question gets answered.
    #
    # Raised to 3.0 with the rest, and deliberately by less than the orange and
    # green cards: a Legend should stay the rarest card anyone actually pulls.
    "legend": 3.0,
}


def store_pack_descriptions() -> bytes:
    """The English text behind `FUT_STORE_PACK_<id>_DESC`.

    The store's pack tiles carry that key and it resolves against
    `packs/loc/storepackdescriptions.<locale>.xml`, which this server answered
    with an empty table -- so every tile fell back to its group heading and the
    detail pane read "Gold Packs / Gold Packs".

    The document format is CardsDLL's own: `trans-unit` elements keyed by
    `resname` with a `source` body. Those three names sit together in the module
    beside the `packs/loc/storepackdescriptions.` path itself, which is what
    says this is the right document rather than a guess at one.

    Whether the console binds it is not yet confirmed on hardware. It is the
    correctly named document for these keys, which is more than could be said
    for the cup-name attempt, but the same caveat applies until it is seen.
    """
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<localization>",
    ]
    for pack_id, spec in sorted(PACK_SPECS.items()):
        # The pack's own words. These were generated from the counts until
        # 26 August -- "12 items, 3 of them players, 1 rare" -- which is a
        # description of a pack rather than the pack's description. The retail
        # text is on the spec now and goes out verbatim.
        #
        # A spec with no blurb still gets a sentence rather than nothing.
        text = spec.get("blurb")
        if not text:
            players = int(spec.get("players", 0))
            count = int(spec.get("count", 12))
            rares = int(spec.get("rares", 0))
            if players >= count:
                text = f"{count} players, {rares} rare."
            elif players:
                text = f"{count} items, {players} of them players, {rares} rare."
            else:
                text = f"{count} consumable and club items, {rares} rare."
        lines.append(
            f'  <trans-unit resname="FUT_STORE_PACK_{pack_id}_DESC">'
            f"<source>{_xml_text(text)}</source></trans-unit>"
        )
        # And the pack's own name, for the line beside the FUT 14 logo. The
        # store entry carries this key in `name` and `title`; see
        # `store_catalogue` for why it is a key rather than the text.
        lines.append(
            f'  <trans-unit resname="FUT_STORE_PACK_{pack_id}_NAME">'
            f"<source>{_xml_text(spec['name'])}</source></trans-unit>"
        )
    # The cup names go in this document too.
    #
    # `TOURNY_LOC_%d` has been served in the leaderboards document since the
    # tiles first drew a bare `*`, and the tiles still draw `*`. This document
    # is fetched 243 times in a session against that one's 40, which is what a
    # general string table looks like rather than a screen-specific one -- so
    # the key may simply have been in the wrong file all along.
    #
    # Costs nothing to find out: the pack descriptions in this same document
    # are themselves untested on hardware, so one launch judges both.
    for cup, title in sorted(TOURNAMENT_NAMES.items()):
        lines.append(
            f'  <trans-unit resname="TOURNY_LOC_{cup}">'
            f"<source>{_xml_text(title)}</source></trans-unit>"
        )
    # The entry-requirement strings, while the probe is reading the key space.
    #
    # Measured on the console 26 August: a cup served `eligibilityKey` 4 drew
    #
    #     *LOC_TOURN_ELG_KEY_16
    #     undefined
    #
    # in its Entry Requirements panel. The leading `*` is an unresolved
    # localisation key -- the same mark the cup names carried before this
    # document answered them -- so the disc has no text for these and the text
    # is this server's to supply.
    #
    # CardsDLL carries four templates: `LOC_TOURN_ELG_KEY_%d`,
    # `LOC_TOURN_ELG_SCOPE_%d`, `LOC_TOURN_ELG_DOMAIN_%d` and
    # `LOC_TOURN_ELG_DOMAIN_LIST_%d`. So a requirement reads as a key, a scope
    # and a domain assembled together, which is exactly the shape of "Max.
    # number of leagues: 4 in the squad's starting 11".
    #
    # One reading is not a mapping. `eligibilityKey` 4 produced index 16 and a
    # single point fits any number of formulas, so these go out numbered: each
    # string names its own index, and one pass along the fourteen tiles reads
    # the whole relation off the screen instead of guessing at it.
    if eligibility_probe()[0]:
        for index in range(ELG_PROBE_STRINGS):
            lines.append(
                f'  <trans-unit resname="LOC_TOURN_ELG_KEY_{index}">'
                f"<source>KEY {index}</source></trans-unit>"
            )
        for index in range(ELG_PROBE_SCOPES):
            lines.append(
                f'  <trans-unit resname="LOC_TOURN_ELG_SCOPE_{index}">'
                f"<source>SCOPE {index}</source></trans-unit>"
            )
            lines.append(
                f'  <trans-unit resname="LOC_TOURN_ELG_DOMAIN_{index}">'
                f"<source>DOMAIN {index}</source></trans-unit>"
            )
    lines.append("</localization>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xml_text(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def store_catalogue(timestamp: int = 2147483647) -> bytes:
    """Every pack, priced, grouped and buyable."""
    purchases = []
    # Listed by group, cheapest first inside each, so a group's packs are
    # contiguous and the headings come out in the order GROUP_ORDER gives.
    # Sorting by pack id put the two consumables packs, 108 and 109, between
    # the bronze and the silver ones.
    ordered = sorted(
        PACK_SPECS.items(),
        key=lambda row: (
            GROUP_ORDER.get(row[1]["group"], 99),
            row[1]["coins"],
            row[0],
        ),
    )
    for index, (pack_id, spec) in enumerate(ordered):
        currencies = [
            {"name": "coins", "funds": spec["coins"], "finalFunds": spec["coins"]}
        ]
        if spec["points"]:
            currencies.append(
                {
                    "name": "points",
                    "funds": spec["points"],
                    "finalFunds": spec["points"],
                }
            )
        # The asset id names the tier's artwork, not the pack: the PC
        # reference ships assetId 3 for the gold pack. Deriving it from the
        # pack id resolved to nothing and the bronze tiles drew NOT FOUND.
        # The pack's own cover. Bronze 1, silver 2, gold 3 -- and the special
        # packs carry 4, 5 and 6, which is why that group's tiles look
        # different from the gold ones they used to share art with.
        tier_asset = int(
            spec.get("art") or {"bronze": 1, "silver": 2, "gold": 3}[spec["tier"]]
        )
        # `value` is drawn as the group's heading, verbatim -- the store showed
        # "bronze", "silver" and "gold" in lower case because that is what it
        # was handed. It is not a localisation key: the packs' own
        # `FUT_STORE_PACK_<id>_DESC` is one, and that resolves against the
        # client's locale, which is why retail pack names read correctly and
        # the group headings did not.
        #
        # So the heading is written out, and the packs added here get headings
        # of their own instead of being filed under a tier they only nominally
        # belong to. `displayGroupAssetId` still names the tier's artwork.
        purchases.append(
            {
                "id": pack_id,
                "assetId": tier_asset,
                "actionType": "CREATEPACK",
                "packType": "CARDPACK",
                "description": f"FUT_STORE_PACK_{pack_id}_DESC",
                # The pack's own name, for the line beside the FUT 14 logo.
                #
                # Retail draws "PREMIUM GOLD PACK" there and the description
                # underneath it. This server had no name member at all, so that
                # line fell back to the group heading -- every gold pack read
                # "Gold Packs" -- and the name was only visible because
                # `store_pack_descriptions` prepends it to the description
                # text. Reported from the console 26 August against a retail
                # screenshot.
                #
                # `name` and `title` are both in CardsDLL's table and neither
                # has ever been sent, so which one the detail pane reads is
                # untested. Both go out: an unrecognised sibling at the top
                # level is skipped, and offering two costs nothing.
                #
                # **A key, not the text.** Written out first, on 26 August, on
                # the reasoning that `displayGroup.value` is drawn verbatim so
                # this screen takes plain text somewhere. The line came back
                # **blank**, which is the useful answer: blank means the member
                # is read and looked up, and "Premium Gold Pack" is not a key
                # any table holds. Had it been ignored the group heading would
                # still have been there.
                #
                # So it resolves the way `description` beside it does, through
                # `packs/loc/storepackdescriptions.<locale>.xml` -- a document
                # this console fetches 1,066 times across these journals and
                # which `store_pack_descriptions` already answers.
                "name": f"FUT_STORE_PACK_{pack_id}_NAME",
                "title": f"FUT_STORE_PACK_{pack_id}_NAME",
                "displayGroup": {
                    # Unique per pack, which is what the document that
                    # rendered had. The client groups by `value`, so the
                    # priority only orders; sharing it between packs was a
                    # change nothing asked for.
                    "priority": index,
                    "value": spec["group"],
                },
                "displayGroupAssetId": tier_asset,
                "displayGroupUseDefaultImage": True,
                "useDefaultImage": True,
                "isPremium": spec["premium"],
                "dealType": "REGULAR",
                "saleType": "NONE",
                "state": "active",
                "visible": 1,
                "sortPriority": index,
                "currencies": currencies,
            }
        )
    return json.dumps(
        {"purchase": purchases, "timestamp": timestamp}, separators=(",", ":")
    ).encode()


class PackShop:
    """Sells any pack in the catalogue, and draws cards that match its tier."""

    def __init__(
        self,
        catalogue: "CardCatalogue",
        wallet: "Wallet",
        inventory: "ClubInventory | None" = None,
    ) -> None:
        self.catalogue = catalogue
        self.wallet = wallet
        # Needed to tell a duplicate from a new card: a duplicate is one the
        # club already holds, which is a question only the club can answer.
        self.inventory = inventory
        self.purchases = 0
        self._item_id_floor: int | None = None
        # Cards drawn but not yet acknowledged by the client. The purchased
        # items endpoint reports these, which is how they reach the club.
        self.pending: list[dict] = []
        self._pools: dict[str, list[dict]] = {}
        # The tier's ordinary cards, grouped by rating band and split on rare,
        # and its specials grouped by family. Built once: the catalogue is
        # 14 000 cards and the draw runs twelve times a pack.
        self._bands: dict[tuple[str, bool], list[tuple[tuple[int, int], float, list[dict]]]] = {}
        self._specials: dict[str, dict[str, list[dict]]] = {}
        for tier, (low, high) in TIER_RATINGS.items():
            pool = [
                card
                for card in catalogue.cards
                if low <= card.get("rating", 0) <= high
            ]
            self._pools[tier] = pool
            # Two pools, not rare against non-rare. A pack advertising "1 Rare"
            # promises at least one, not exactly one and nothing else: an
            # ordinary slot draws from every ordinary card of the tier. Making
            # it draw from non-rares only shut the top bands out of the pack
            # entirely, because a gold rated 84 or better is nearly always a
            # Rare Gold -- 84-86 came out at 0.65% against a stated 6%.
            for rare in (False, True):
                grouped = []
                for span, weight in RATING_BANDS.get(tier, ()):
                    band_low, band_high = span
                    cards = [
                        card
                        for card in pool
                        if is_ordinary(card)
                        and (card.get("rareflag") if rare else True)
                        and band_low <= card.get("rating", 0) <= band_high
                    ]
                    if cards:
                        grouped.append((span, weight, cards))
                self._bands[(tier, rare)] = grouped
            families: dict[str, list[dict]] = {}
            for card in pool:
                if is_ordinary(card):
                    continue
                families.setdefault(
                    (card.get("rarity") or "").strip().lower(), []
                ).append(card)
            self._specials[tier] = families

    # -- choosing a card ----------------------------------------------------

    def _draw_special(
        self, tier: str, rng: random.Random, weights: dict | None = None
    ) -> dict | None:
        """One special, its family chosen by weight rather than by stock.

        `weights` lets a pack promise a kind of special rather than the
        house spread -- a Team of the Week pack that could hand you a
        Team of the Year instead is not a Team of the Week pack.
        """
        table = weights or SPECIAL_FAMILY_WEIGHTS
        families = self._specials.get(tier) or {}
        choices = [
            (name, table.get(name, 0.0))
            for name, cards in families.items()
            if cards and table.get(name, 0.0) > 0
        ]
        if not choices:
            return None
        family = rng.choices(
            [name for name, _ in choices], weights=[w for _, w in choices]
        )[0]
        return rng.choice(families[family])

    def _draw_ordinary(
        self, tier: str, rare: bool, rng: random.Random, elite_left: int
    ) -> dict | None:
        """One ordinary card, its rating band chosen by weight.

        `elite_left` is how many more cards rated `ELITE_RATING` or better this
        pack may still hold. At zero the top bands are dropped from the draw
        rather than redrawn, so the cap costs nothing and cannot loop.
        """
        grouped = self._bands.get((tier, rare)) or self._bands.get((tier, False))
        if not grouped:
            return None
        choices = [
            (cards, weight * (RARE_BAND_MULTIPLIER.get(span, 1.0) if rare else 1.0))
            for span, weight, cards in grouped
            if elite_left > 0 or span[1] < ELITE_RATING
        ]
        if not choices:
            choices = [(cards, weight) for _, weight, cards in grouped]
        return rng.choice(
            rng.choices(
                [cards for cards, _ in choices],
                weights=[weight for _, weight in choices],
            )[0]
        )

    def _special_slots(
        self, pack_id: int, player_slots: list[int], rng: random.Random
    ) -> set[int]:
        """Which player slots of this pack, if any, hold a special.

        Rolled once for the pack, not once per card. Rolling per card made the
        pack's real chance a function of how many players it happened to hold,
        which is not what a stated chance means.
        """
        if not player_slots:
            return set()
        spec = PACK_SPECS.get(int(pack_id)) or {}
        # A pack that promises a special owes one every time, not with a
        # probability. `guaranteed` is that promise.
        guaranteed = max(0, int(spec.get("guaranteed", 0)))
        chosen: set[int] = set()
        for _ in range(min(guaranteed, len(player_slots))):
            remaining = [slot for slot in player_slots if slot not in chosen]
            if not remaining:
                break
            chosen.add(rng.choice(remaining))
        chance = SPECIAL_CHANCE.get(int(pack_id), 0.0)
        if not chosen and (chance <= 0 or rng.random() >= chance):
            return set()
        if not chosen:
            chosen = {rng.choice(player_slots)}
        second = SECOND_SPECIAL_CHANCE.get(int(pack_id), 0.0)
        if (
            len(chosen) < max(MAX_SPECIALS_PER_PACK, guaranteed)
            and second > 0
            and rng.random() < second
        ):
            remaining = [slot for slot in player_slots if slot not in chosen]
            if remaining:
                chosen.add(rng.choice(remaining))
        return chosen

    def grant_starter_packs(self, rng: random.Random | None = None) -> int:
        """Draw the three packs a new club opens with, into the pending pile.

        Bronze, silver, gold. Ordinary cards only -- no Team of the Week, no
        Team of the Season, no Legend -- and nothing above
        `STARTER_RATING_CAP`, so the gold pack is a start rather than a
        jackpot. They cost nothing.

        The pending pile is the route the client already reads through
        purchased/items and sends to the club; the native `unopenedPacks` /
        `starterPack` members exist in CardsDLL but the route that carries
        them is not established here, and guessing one is how screens get
        frozen.
        """
        rng = rng or random.Random()
        drawn_total = 0
        for pack_id in STARTER_PACKS:
            spec = self.spec(pack_id)
            if spec is None:
                continue
            pool = [
                card
                for card in (self._pools.get(spec["tier"]) or self.catalogue.cards)
                if is_ordinary(card)
                and card.get("rating", 0) <= STARTER_RATING_CAP
            ]
            if not pool:
                continue
            rares = [card for card in pool if card.get("rareflag")] or pool
            commons = [card for card in pool if not card.get("rareflag")] or pool
            drawn = []
            # One per pack, like the shop draw: a starter pack should not hand
            # out the same kit twice either.
            already: set = set()
            for slot, kind in enumerate(self._slot_kinds(spec, rng)):
                rare_slot = slot < int(spec["rares"])
                item_id = PACK_ITEM_ID_BASE + 900_000 + drawn_total
                # A new club needs contracts more than it needs a fourth
                # striker, so the starter packs carry the same nine non-player
                # slots the shop packs do.
                if kind == "extra":
                    item = _draw_extra(spec["tier"], rare_slot, item_id, rng, already)
                    if item is not None:
                        drawn.append(item)
                        drawn_total += 1
                        continue
                card = rng.choice(rares if rare_slot else commons)
                item = _player_item(
                    item_id=item_id,
                    asset_id=card["assetId"],
                    rating=card.get("rating", 0),
                    rare=card.get("rareflag", 0),
                    play_style=0,
                    team_id=card.get("clubId", 0),
                    attributes=card.get("attributes", [0] * 6),
                    position=card.get("position") or FALLBACK_POSITION,
                    item_state="new",
                    pile=PILE_PURCHASED,
                    nation=card.get("nationId", 0),
                    league=card.get("leagueId", 0),
                    rarity=card.get("rarity", ""),
                )
                item["untradeable"] = False
                drawn.append(item)
                drawn_total += 1
            self._mark_duplicates(drawn)
            self.pending.extend(drawn)
        return drawn_total

    @staticmethod
    def _slot_kinds(spec: dict, rng: random.Random) -> list[str]:
        """Which slots of a pack hold a player and which hold everything else.

        Shuffled, so the players are not always the first three cards on the
        screen. A spec with no `players` key is all players, which is what
        every pack used to be.
        """
        count = int(spec["count"])
        players = max(0, min(count, int(spec.get("players", count))))
        kinds = ["player"] * players + ["extra"] * (count - players)
        rng.shuffle(kinds)
        return kinds

    def _next_item_id(self, slot: int) -> int:
        """An id no card already owned is using.

        This was `PACK_ITEM_ID_BASE + purchases * 100 + slot`, and `purchases`
        counts from zero every time the server starts. So the first pack after
        a restart reissued 1950000100 and up -- ids the saved club was already
        holding from an earlier session.

        `_keep` refuses an id it already holds, on the sound reasoning that the
        same item arriving twice is one card counted twice rather than two
        cards. Between them, a freshly packed card could be dropped on the way
        to the club and never appear anywhere. That is what happened to a
        Record Breaker Klose on 12 August: the club's 1950000205 was a
        Non-Rare Silver from a previous session, and the 90 went nowhere.

        So the counter is seeded from what is actually owned, once, the first
        time a pack is opened after a start.
        """
        if self._item_id_floor is None:
            owned = [item.get("id") or 0 for item in self.pending]
            if self.inventory is not None:
                owned += [item.get("id") or 0 for item in self.inventory.items]
            highest = max((i for i in owned if i < PACK_ITEM_ID_BASE + 900_000),
                          default=PACK_ITEM_ID_BASE)
            self._item_id_floor = max(PACK_ITEM_ID_BASE, highest) + 1
        return self._item_id_floor + (self.purchases - 1) * 100 + slot

    def spec(self, pack_id: int) -> dict | None:
        return PACK_SPECS.get(int(pack_id))

    def price(self, pack_id: int) -> int:
        spec = self.spec(pack_id)
        return int(spec["coins"]) if spec else GOLD_PACK_PRICE

    def can_afford(self, pack_id: int = GOLD_PACK_ID) -> bool:
        return self.wallet.coins >= self.price(pack_id)

    def open_pack(
        self, pack_id: int = GOLD_PACK_ID, rng: random.Random | None = None
    ) -> bytes:
        rng = rng or random.Random()
        spec = self.spec(pack_id) or PACK_SPECS[GOLD_PACK_ID]
        self.wallet.debit(int(spec["coins"]))
        self.purchases += 1

        tier = spec["tier"]
        pool = self._pools.get(tier) or self.catalogue.cards
        kinds = self._slot_kinds(spec, rng)
        specials = self._special_slots(
            pack_id,
            [slot for slot, kind in enumerate(kinds) if kind == "player"],
            rng,
        )
        elite_left = MAX_ELITE_PER_PACK
        # The signatures this pack has already handed out.
        already: set = set()

        drawn = []
        for slot, kind in enumerate(kinds):
            item_id = self._next_item_id(slot)
            # A rare slot promises a rare card whatever kind of card it holds.
            rare_slot = slot < int(spec["rares"])
            if kind == "extra":
                item = _draw_extra(tier, rare_slot, item_id, rng, already)
                if item is not None:
                    drawn.append(item)
                    continue
                # No consumable catalogue: fall through and draw a player, so
                # the pack is still the size it advertises.
            # A pack never hands out the same player twice. Retail does not,
            # and the screen has no way to show it that is not confusing: two
            # Vargas out of one pack read as a bug whatever the data says. So
            # the draw is retried rather than the repeat explained.
            #
            # Bounded, and it keeps the last card if the pool cannot do better
            # -- a bronze tier with a narrow rare band can run out of distinct
            # cards before twelve slots are filled, and a pack that is short a
            # card is worse than a pack with a repeat in it.
            card = None
            for attempt in range(DISTINCT_DRAW_ATTEMPTS):
                # A special family can be small enough that every card in it
                # is already in this pack -- a Team of the Week pack drawing
                # two in forms out of a short list. After half the attempts the
                # slot stops insisting on a special and takes a rare instead,
                # which is a better card than the same one twice.
                if slot in specials and attempt < DISTINCT_DRAW_ATTEMPTS // 2:
                    card = self._draw_special(tier, rng, spec.get("families"))
                if card is None:
                    # A special is a rare card, so a slot that was going to
                    # hold one and found no family to draw from still owes a
                    # rare.
                    card = self._draw_ordinary(
                        tier, rare_slot or slot in specials, rng, elite_left
                    )
                if card is None:
                    card = rng.choice(pool)
                if _pack_identity(card) not in already:
                    break
                card = None
            if card is None or _pack_identity(card) in already:
                # Last resort: anything in the tier this pack has not already
                # handed out. Only the twelve-player packs ever reach here, and
                # only when the bands and the elite cap between them keep
                # offering the same card back.
                fresh = [
                    other for other in pool
                    if _pack_identity(other) not in already
                ]
                card = rng.choice(fresh) if fresh else (card or rng.choice(pool))
            already.add(_pack_identity(card))
            if card.get("rating", 0) >= ELITE_RATING:
                elite_left -= 1
            item = _player_item(
                item_id=item_id,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                item_state="new",
                pile=PILE_PURCHASED,
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
            item["untradeable"] = False
            drawn.append(item)
        duplicate_pairs = self._mark_duplicates(drawn)
        self.pending.extend(drawn)
        return json.dumps(
            {
                "numberItems": len(drawn),
                "purchasedPackId": int(pack_id),
                "itemList": drawn,
                # Pairs, never bare new ids -- see _mark_duplicates. This was
                # empty, and with it empty the pack screen showed a repeat as
                # an ordinary card however clearly the card itself was marked.
                "duplicateItemIdList": duplicate_pairs,
                "credits": self.wallet.coins,
                "totalCredits": self.wallet.coins,
                "coins": self.wallet.coins,
            },
            separators=(",", ":"),
        ).encode()

    # The club screen needs the same answer, so it lives at module level now.
    _signature = staticmethod(card_signature)

    def _mark_duplicates(self, drawn: list[dict]) -> list[dict]:
        """Pair each new card with the owned card it repeats.

        Two shapes go out, because the singular and the plural are read in
        different places. `duplicateItemId` on the card itself names the one it
        repeats; `duplicateItemIdList` carries the same pairing as records:

            {"itemId": <new>, "duplicateItemId": <owned>}

        The list is what the FIFA 14 pack screen actually reads -- reported
        independently from a build where marking only the card did not show a
        duplicate at all. What must never go back in that list is a bare list
        of the *new* ids, which is what froze the title: it told the screen to
        compare each card against itself.
        """
        # Everything already owned, wherever it is sitting. The club was the
        # only place looked at, and a card drawn from a pack does not go to the
        # club -- it goes to the purchased pile and waits there until it is
        # sent on. So packing the same player twice in a row said nothing the
        # second time: the first copy was in the pile, and the pile was not
        # being read. Klose 90, twice, on 12 August.
        owned: dict[tuple, int] = {}
        pools: list[list[dict]] = [self.pending]
        if self.inventory is not None:
            pools.append(self.inventory.items)
        for pool in pools:
            for item in pool:
                if not _repeats(item):
                    continue
                owned.setdefault(self._signature(item), item["id"])
        pairs: list[dict] = []
        for item in drawn:
            # Players and club items duplicate; consumables do not. A second
            # contract card is not a repeat of the first, it is a second
            # contract -- consumables stack, and marking one as a duplicate
            # offers to quick-sell a card the club is meant to accumulate.
            #
            # A second Barcelona home kit is a different matter: you either own
            # a kit or you do not, and the second copy is worth its quick-sell
            # value and nothing else. With 1570 club items in the draw the
            # player will meet plenty of repeats, and the screen should say so.
            if not _repeats(item):
                continue
            key = self._signature(item)
            existing = owned.get(key)
            if existing and existing != item["id"]:
                item["duplicateItemId"] = existing
                pairs.append({"itemId": item["id"], "duplicateItemId": existing})
            else:
                owned.setdefault(key, item["id"])
        return pairs

    def _duplicates(self, drawn: list[dict]) -> list[int]:
        """The ids among these the club already owns.

        A duplicate is the same asset in the same version -- a rare Messi and a
        base Messi are two different cards, and only one of them is a repeat.
        Cards drawn twice inside a single pack count too, after the first.
        """
        owned = set()
        if self.inventory is not None:
            owned = {
                (item.get("assetId"), item.get("rareflag"))
                for item in self.inventory.items
            }
        duplicates, seen = [], set()
        for item in drawn:
            signature = (item.get("assetId"), item.get("rareflag"))
            if signature in owned or signature in seen:
                duplicates.append(item["id"])
            seen.add(signature)
        return duplicates

    def items_by_id(self, ids: list[int]) -> bytes:
        """`GET /item?idList=a,b` -- the cards behind those ids.

        **This is what "MY CURRENT ITEM: undefined" was.** The duplicate panel
        compares two cards, and it asks for both by id right before it draws
        them: every one of the 48 requests in these journals carries exactly
        two ids, the new card and the owned one it repeats.

            GET /ut/game/fifa14/item?idList=1950012526,1700000002

        This server answered a static `{"itemData":[]}` to all of them, so the
        panel had a number for the owned card and no card. The new one drew
        because the pack response had already handed it over.

        It is not only the duplicate panel: any screen holding an id it has no
        card for can ask here, which is the general answer to a club too large
        for `/clubUser`'s cache. That route sends 146 of this club's 1,597
        cards, and the other 1,451 had no way to be drawn at all.

        Order follows the request, and an id this club does not hold is left
        out rather than answered with a blank -- a card that is not there is
        not a card with no members.
        """
        wanted = [int(item_id) for item_id in ids if item_id]
        pools: list[list[dict]] = [self.pending]
        if self.inventory is not None:
            pools.append(self.inventory.items)
        by_id: dict[int, dict] = {}
        for pool in pools:
            for item in pool:
                item_id = item.get("id")
                if item_id and item_id not in by_id:
                    by_id[int(item_id)] = item
        found = [by_id[item_id] for item_id in wanted if item_id in by_id]
        return json.dumps({"itemData": found}, separators=(",", ":")).encode()

    def _client_cache(self) -> set[int]:
        """Every owned card the console can draw without asking again.

        The active squad, which `/squad/active` fetches every session and in
        full, **and** whatever `/clubUser` sent -- that route is the client's
        face-card cache and it reads it once. A card in neither is a card the
        duplicate panel has a number for and no picture of.
        """
        club = self.inventory
        if club is None:
            return set()
        cached = {
            item["id"] for item in getattr(club, "squad", []) or []
            if isinstance(item, dict) and item.get("id")
        }
        cached |= set(getattr(club, "cached_ids", set()) or set())
        return cached

    def purchased_items(self) -> bytes:
        return json.dumps(
            {
                # Reporting duplicates here froze the title once: after
                # buying a second Chamakh the client fetched squad/active,
                # tradePile and this, and stopped dead. What went out then was
                # a bare list of the *new* ids -- which points the client at
                # the card it is holding, and CardsDLL carrying
                # GetCardDuplicate and HAS_DUPLICATE says it wants the card
                # already owned.
                #
                # What goes out now is the pairing, {itemId: new,
                # duplicateItemId: owned}, which is the shape the pack response
                # and the club search both carry without trouble. The screen
                # has a duplicates tab of its own and it was being handed
                # nothing to put in it -- two Vargas out of one pack, marked on
                # the card and missing from the panel.
                "duplicateItemIdList": pile_duplicate_pairs(
                    self.pending,
                    self.inventory.items if self.inventory else [],
                    # The squad is the one part of the club the console is
                    # always holding: `/squad/active` is fetched every session
                    # and in full, where the club is paged eleven at a time and
                    # only as far as somebody scrolled.
                    self._client_cache(),
                ),
                "itemData": self.pending,
                "credits": self.wallet.coins,
                "totalCredits": self.wallet.coins,
                "coins": self.wallet.coins,
            },
            separators=(",", ":"),
        ).encode()

    def refused(self) -> bytes:
        return json.dumps({"reason": "INSUFFICIENT_COINS"}, separators=(",", ":")).encode()


# -- what you can do with a card -------------------------------------------
#
# Retail pile numbers. Squad membership is not a pile: players in the eleven
# are still owned in the club.
# FUT has exactly two consumable item types, and the family is not one of
# them: `cardsubtypeid` carries the family, `itemType` carries only whether
# the card develops a player or trains one.
#
# This served "contract", "fitness", "healing", "playStyle" and "position" --
# names invented here to match the club screens' own filters. The club tab
# worked because it filtered on the same invented names; the native Apply
# Consumable picker did not, and reported "Pas d'élément disponible" over a
# club holding 35 contracts.
CONSUMABLE_TYPES = {"development", "training"}

# Catalogue family -> the type that goes on the wire.
CONSUMABLE_WIRE_TYPE = {
    "contract": "development",
    "fitness": "development",
    "healing": "development",
    "position": "development",
    "training": "training",
    "playStyle": "training",
    # Squad Training: subtype 232, six cards on art 43, which the console draws
    # as "Squad Training -- Boost attributes for the next match". They were
    # filed under `position`, which is why they turned up in the position
    # modifier tab, and their art does not resolve any more than the training
    # block's does.
    "squadTraining": "training",
    # Subtypes 121-136 on art 35, which the console draws as "Formation
    # Modifier -- Manager, 3-4-1-2" and cannot resolve the art for. Art 34
    # beside them renders fine, so this is the id and not the family.
    "formationManager": "development",
    # Subtypes 300-326, one per league. `training` is what the game's own
    # database calls them and what the member name they count under says --
    # `consumablesTrainingManagerLeagueModifier`. See
    # `tools/manager_league_mods.py` for where the block was named.
    "managerLeagueModifier": "training",
}

# The families themselves, as `tools/build_consumables.py` names them. Used to
# pick and weight cards, never sent.
CONSUMABLE_FAMILIES = set(CONSUMABLE_WIRE_TYPE)


def consumable_family(item: dict) -> str:
    """Which family an owned card belongs to, from the catalogue.

    The item cannot say: its `itemType` is `development` or `training` and
    nothing finer. `resourceId` is the card's own database id, so the
    catalogue answers it.
    """
    row = _consumable_definitions().get(int(item.get("resourceId") or 0))
    return row.get("itemType", "") if row else ""



# The console names the pile, it does not number it: every `PUT /item` this
# server has ever received carries `"pile": "club"` or `"pile": "trade"`,
# never 5 or 7. Measured across a four-hour session on 17 August 2026 -- 198
# "club" and 15 "trade", no integer at all.
#
# This mattered more than a spelling. The old code did `int(pile)` inside a
# try, and fell back to PILE_CLUB when it raised -- so `int("trade")` threw
# ValueError and every card sent to the transfer list was quietly filed in the
# club instead. Fifteen cards went that way in one session, and the player
# reported them as disappearing: they were never on the transfer list to be
# seen, and they were in a club too big to notice one more card in.
#
# Numbers are still accepted. Nothing observed sends them, but a fallback that
# silently means "club" is what caused this, so an unrecognised pile is
# refused rather than guessed at.
PILE_NAMES = {"club": PILE_CLUB, "trade": PILE_TRANSFER, "purchased": PILE_PURCHASED}


def _pile_number(value: object) -> int | None:
    """The pile a move is asking for, or None if it cannot be read."""
    if isinstance(value, str):
        return PILE_NAMES.get(value.strip().lower())
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number in set(PILE_NAMES.values()) else None



# -- applying a consumable --------------------------------------------------
#
# `POST /ut/game/fifa14/item/resource/<resourceId>` with `{"apply":[{"id":N}]}`.
#
# `apply` is in CardsDLL's own member-name table, immediately beside `applyTo`,
# so the request member is the module's and not a guess; the same call is what
# the PC revival serves. The reply is empty: retail answers this one by status.
#
# Until now the club could hold a contract and show it and there was no route
# that did anything with it. Every consumable in FUT was decoration.

# What each card does, keyed by the subtype the game's own card database
# carries. The blocks come from `tools/build_consumables.py`, which reads them
# out of `cards_ng_db.db`.
CONTRACT_PLAYER, CONTRACT_MANAGER = 201, 202
# Chemistry styles, split the way CardsDLL counts them: outfield players under
# `consumablesTrainingPlayerPlayStyle`, goalkeepers under
# `consumablesTrainingGkPlayStyle`.
PLAY_STYLE_OUTFIELD = (91, 110)
PLAY_STYLE_GK = (121, 136)

# The real chemistry styles. Nineteen of them, Basic through Shadow, added by
# `tools/chemistry_styles.py`. The style written onto a card is the row's
# `amount` -- 0 to 18 in subtype order -- and not the subtype: `FUT_PLAYSTYLE_%d`
# in the binary says the style is keyed by an integer in some range, and this is
# the range. See `docs/CONSUMABLES.md`.
CHEMISTRY_FIRST = 250
CHEMISTRY_LAST = 268

# The manager league modifiers, one per league -- see
# `tools/manager_league_mods.py`. The card's `amount` is the league it names.
MANAGER_LEAGUE_FIRST = 300
MANAGER_LEAGUE_LAST = 326
HEALING_FIRST, HEALING_ANY = 211, 218
FITNESS_PLAYER, FITNESS_SQUAD = 219, 220

# 211 through 217 in order. The binary carries exactly this list and in this
# order -- FUT_HEAD_HEALING, FUT_UPPERBODY_HEALING, FUT_ARM_HEALING,
# FUT_BACK_HEALING, FUT_KNEE_HEALING, FUT_LEG_HEALING, FUT_FOOT_HEALING -- and
# 214 (back) is simply not in the card database. 218 heals anything.
HEALING_KINDS = ("head", "upperbody", "arm", "back", "knee", "leg", "foot")

# The two training blocks, six attributes each and a seventh card that raises
# all six. The offset inside the block *is* the attribute index: a player's
# `attributeList` holds six entries indexed 0 to 5, and for a keeper those six
# slots hold the keeper's attributes. So the index is right either way.
#
# Which block is the keeper's is NOT established. `build_consumables.py` calls
# 51-57 the outfield block and 61-67 the keeper's; the PC revival's catalogue
# says the opposite, and nothing in `fcc_trainingcards` names either -- it
# carries no name column, only a card art id (3 against 1). Because the
# attribute index is the same either way, the boost lands correctly whichever
# is which, so this applies the card without checking the target is the right
# kind of player rather than enforcing a rule it cannot prove.
TRAINING_BLOCKS = ((51, 57), (61, 67))

PLAYER_ATTRIBUTES = 6
ATTRIBUTE_CEILING = 99


class ConsumableRefused(Exception):
    """The card cannot be applied, and the reason is worth reporting."""


def _card_quality(rating: int) -> str:
    for tier, (low, high) in TIER_RATINGS.items():
        if low <= int(rating) <= high:
            return tier
    return "gold"


def _consumable_definitions() -> dict[int, dict]:
    """Catalogue rows by `definitionId`, which is the card's own database id."""
    return {int(card["definitionId"]): card for card in _consumable_catalogue()}


def _training_index(subtype: int) -> int | None:
    """Which attribute a training card raises, or None for the all-six card."""
    for first, last in TRAINING_BLOCKS:
        if first <= subtype < last:
            return subtype - first
        if subtype == last:
            return None
    raise ConsumableRefused(f"unsupported training subtype {subtype}")


def _bump_attributes(player: dict, indexes, amount: int) -> None:
    entries = player.get("attributeList") or []
    for entry in entries:
        if entry.get("index") in indexes:
            entry["value"] = min(ATTRIBUTE_CEILING, int(entry.get("value", 0)) + amount)


class ConsumableRack:
    """Applies an owned consumable to an owned card, and spends it."""

    def __init__(self, inventory: "ClubInventory") -> None:
        self.inventory = inventory
        # Every request the client made that could not be honoured, kept so
        # the contested families -- the play style and position blocks below
        # -- can be identified from one real application rather than guessed.
        self.refused: list[dict] = []

    # -- finding the card ---------------------------------------------------

    def _owned(self, resource_id: int) -> tuple[dict, dict]:
        """The club's copy of the card the request names, and its catalogue row.

        The request addresses the *definition*, not one particular card, so any
        owned copy will do. `resourceId` is the card's own database id -- 5001001
        and up -- and identifies exactly one definition, which is why the item
        carries it rather than a value derived from the card art: derived, every
        training card in the club answered to one id and this call could not
        tell a +5 card from a +15 one.
        """
        definitions = _consumable_definitions()
        matches = [
            item
            for item in self.inventory.items
            if item.get("itemType") in CONSUMABLE_TYPES
            and item.get("resourceId") == resource_id
        ]
        if not matches:
            raise ConsumableRefused(f"consumable {resource_id} is not owned")
        row = definitions.get(int(resource_id))
        if row is None:
            raise ConsumableRefused(f"consumable {resource_id} is not in the catalogue")
        return matches[0], row

    def resource_of(self, item_id: int) -> int:
        """The definition behind one owned consumable, by its item id.

        The client addresses a consumable two ways. `item/resource/<id>` names
        the definition and any owned copy will do; `item/<id>` names one
        particular card in the club. Only the first was ever handled, so a
        real application on 11 August at 03:00 --

            POST /ut/game/fifa14/item/1950000106
            {"apply":[{"id":1700000004}]}

        -- was answered 404 and went in the unhandled journal, where nobody
        looked. From the player's side the card simply did nothing.
        """
        for item in self.inventory.items:
            if item.get("id") != item_id:
                continue
            if item.get("itemType") not in CONSUMABLE_TYPES:
                raise ConsumableRefused(f"item {item_id} is not a consumable")
            resource = item.get("resourceId")
            if not resource:
                raise ConsumableRefused(f"item {item_id} carries no resourceId")
            return int(resource)
        raise ConsumableRefused(f"item {item_id} is not in the club")

    def _target(self, item_id: int) -> dict:
        for item in self.inventory.items:
            if item.get("id") == item_id:
                return item
        raise ConsumableRefused(f"target {item_id} is not in the club")

    # -- the effects --------------------------------------------------------

    def apply(self, resource_id: int, target_ids: list[int]) -> dict:
        """Apply one card. Raises ConsumableRefused rather than half-applying.

        Nothing is spent and nothing is written until the effect has been
        decided, so a refusal costs the player neither the card nor the state.
        """
        card, row = self._owned(int(resource_id))
        subtype = int(row.get("cardsubtypeid") or 0)
        amount = int(row.get("amount") or 0)

        if subtype == FITNESS_SQUAD:
            changed = self._squad_fitness(amount)
            effect = f"squad fitness +{amount}"
        else:
            if not target_ids:
                raise ConsumableRefused("this card needs a target")
            target = self._target(int(target_ids[0]))
            changed, effect = self._apply_to(target, row, subtype, amount)

        self.inventory.items.remove(card)
        return {
            "consumedItemId": card["id"],
            "resourceId": int(resource_id),
            "effect": effect,
            "itemData": changed,
        }

    def _apply_to(
        self, target: dict, row: dict, subtype: int, amount: int
    ) -> tuple[list[dict], str]:
        kind = target.get("itemType")

        if subtype in (CONTRACT_PLAYER, CONTRACT_MANAGER):
            if subtype == CONTRACT_PLAYER and kind != "player":
                raise ConsumableRefused("a player contract needs a player")
            if subtype == CONTRACT_MANAGER and kind not in ("manager", "staff"):
                raise ConsumableRefused("a manager contract needs a manager")
            # A contract grants a different number of matches to a gold, a
            # silver and a bronze card, and the card database carries all
            # three. The card is named for the gold figure.
            quality = _card_quality(target.get("rating", 0))
            gain = int(row.get(quality, row.get("amount", 0)) or 0)
            target["contract"] = min(
                ATTRIBUTE_CEILING, int(target.get("contract", 0)) + gain
            )
            return [target], f"contract +{gain}"

        # The manager's league. Subtypes 300-326, one per league, and the
        # card's `amount` is the league it names -- 13 the Premier League, 16
        # Ligue 1, 53 La Liga.
        #
        # A manager card carries `leagueId` and `leagueid`, both of them, which
        # is how it gives chemistry to players from that league. Changing it is
        # the whole point of the card, and it is the one manager consumable
        # whose effect this server can state without guessing.
        if MANAGER_LEAGUE_FIRST <= subtype <= MANAGER_LEAGUE_LAST:
            if kind != "manager":
                raise ConsumableRefused("a league modifier needs a manager")
            league = int(row.get("amount") or 0)
            if not league:
                raise ConsumableRefused("this modifier names no league")
            target["leagueId"] = league
            target["leagueid"] = league
            return [target], f"league {league}"

        if kind != "player":
            raise ConsumableRefused("this card can only be applied to a player")

        if subtype == FITNESS_PLAYER:
            target["fitness"] = min(
                ATTRIBUTE_CEILING, int(target.get("fitness", 0)) + amount
            )
            return [target], f"fitness +{amount}"

        if HEALING_FIRST <= subtype <= HEALING_ANY:
            return self._heal(target, subtype, amount)

        if any(first <= subtype <= last for first, last in TRAINING_BLOCKS):
            index = _training_index(subtype)
            indexes = range(PLAYER_ATTRIBUTES) if index is None else (index,)
            _bump_attributes(target, set(indexes), amount)
            where = "all six" if index is None else f"attribute {index}"
            return [target], f"training {where} +{amount}"

        if CHEMISTRY_FIRST <= subtype <= CHEMISTRY_LAST:
            return self._chemistry_style(target, row)

        if PLAY_STYLE_OUTFIELD[0] <= subtype <= PLAY_STYLE_OUTFIELD[1] and row.get("to"):
            return self._position_modifier(target, row)

        if PLAY_STYLE_OUTFIELD[0] <= subtype <= PLAY_STYLE_GK[1]:
            return self._play_style(target, subtype, row)

        # The position block (232). What each card in it does is contested:
        # this server's catalogue called it a position change, the PC
        # revival's agrees, and the binary carries a FUT_CONSUMABLE_POSITIONMOD
        # -- but the card the console actually rendered for 232 reads
        # "DÉBLOQUER / Capacité +8 moral", which is a stadium unlock and not a
        # position at all. Both catalogues are wrong about it and the client
        # refuses to keep the card.
        #
        # Writing `preferredPosition` on the strength of that would silently
        # change the wrong field on a real card, and a card is spent either
        # way. Refused, and recorded: one application from the console names
        # the family, because the screen shows the player what the card was.
        self.refused.append(
            {
                "resourceId": row.get("definitionId"),
                "cardsubtypeid": subtype,
                "itemType": row.get("itemType"),
                "targetId": target.get("id"),
                "targetPosition": target.get("preferredPosition"),
                "targetPlayStyle": target.get("style"),
            }
        )
        raise ConsumableRefused(
            f"subtype {subtype} has no established effect on this platform"
        )

    def _position_modifier(self, target: dict, row: dict) -> tuple[list[dict], str]:
        """Move a player from one position to the next, per the card.

        Only reachable for a card whose catalogue row carries a `to` --
        `tools/position_modifiers.py add` writes those from the transitions the
        PC revival's catalogue names. Without one the card falls through to the
        refusal below, which is the state this block was in from the moment it
        was identified until the transitions arrived: knowing that 91-110 are
        position modifiers does not tell you where any single one of them moves
        a player.

        The guard is retail's rule. A card names a `from` as well as a `to`, so
        `CM->CAM` goes on a CM and nothing else; anywhere else it is refused and
        the card is not spent. Enforcing it matters more here than in retail,
        because this server is the only thing standing between a mis-click and
        a permanently repositioned card.

        Nothing else on the player changes. A position modifier moves the
        position and leaves the rating, the attributes and the club, league and
        nation that drive chemistry exactly as they were.
        """
        wanted = str(row.get("from") or "").strip().upper()
        becomes = str(row.get("to") or "").strip().upper()
        current = str(target.get("preferredPosition") or "").strip().upper()
        if wanted and current != wanted:
            raise ConsumableRefused(
                f"a {wanted}-{becomes} card needs a {wanted}; that player is a "
                f"{current or 'unknown position'}"
            )
        target["preferredPosition"] = becomes
        return [target], f"position {wanted} to {becomes}"

    def _chemistry_style(self, target: dict, row: dict) -> tuple[list[dict], str]:
        """A real chemistry style, written onto the card's own `playStyle`.

        The value is the catalogue row's `amount`, which runs 0-18 across the
        nineteen styles in subtype order -- Basic 0, Sniper 1, Finisher 2, and
        so on to Shadow 18. Writing the *subtype* here, as the old play-style
        path did, would put 250-268 into a member the client reads as a style
        index and show the wrong style on every card.

        **These nineteen are the outfield set, and a keeper cannot wear them.**
        FIFA 14 gives goalkeepers their own five -- Basic, Wall, Shield, Cat,
        Glove -- and not one of those names appears here: 250-268 runs Basic,
        Sniper, Finisher, Deadeye ... Shadow. Sniper on a goalkeeper is not a
        choice retail offers.

        Basic is the exception, and it is deliberate. It is the style every
        card starts on, outfield and keeper alike, so 250 is the one member of
        this range that means something on a goalkeeper: it puts him back to
        default.

        Where the other four GK styles live is **not known**. The block at
        121-136 carries the binary's own member name for them,
        `consumablesTrainingGkPlayStyle`, which is as close to an answer as
        anything here gets -- but it holds sixteen entries against five styles,
        every one with `amount` 0 and no name in either this catalogue or the
        PC revival's. Sixteen cards, five styles and no index between them is
        not a mapping, and this block has already been guessed at twice. It
        stays refused until something names it.
        """
        style = int(row.get("amount", 0) or 0)
        keeper = str(target.get("preferredPosition") or "").strip().upper() == "GK"
        if keeper and style != 0:
            name = str(row.get("name") or f"style {style}").title()
            raise ConsumableRefused(
                f"{name} is an outfield chemistry style; a goalkeeper takes "
                "Basic, Wall, Shield, Cat or Glove"
            )
        target["playStyle"] = style_value(style)
        target["style"] = style_value(style)
        # `styleAttribMods`, probed.
        #
        # `style` alone is sent and ignored: a card carrying Hunter still draws
        # BASIC, and taking it out of the squad and back does not change that,
        # so it is not a stale card. The only other style member in CardsDLL's
        # JSON table is `styleAttribMods` (0x02FEA0), and a chemistry style in
        # FUT *is* a set of attribute modifiers -- so the label may be derived
        # from the mods rather than from an id.
        #
        # `FIFA14_STYLE_PROBE=1` writes an unmistakable pattern: +25 on the
        # first attribute and nothing elsewhere, in the index/value shape
        # `attributeList` already uses on the same card. One look answers two
        # questions at once -- whether the client reads this member at all
        # (does the first attribute jump by 25?) and whether writing it is what
        # makes a style name appear.
        if os.environ.get("FIFA14_STYLE_PROBE", "").strip().lower() in {"1", "true", "yes"}:
            target["styleAttribMods"] = [
                {"index": index, "value": (25 if index == 0 else 0)}
                for index in range(6)
            ]
        name = str(row.get("name") or f"style {style}").title()
        return [target], f"chemistry style {name}"

    def _play_style(self, target: dict, subtype: int, row: dict) -> tuple[list[dict], str]:
        """91-136 are **not** chemistry styles, and are refused again.

        This block was refused for weeks, then applied from 12 August on the
        strength of the member CardsDLL counts the cards under:

            91-110   consumablesTrainingPlayerPlayStyle
            121-136  consumablesTrainingGkPlayStyle

        That reading is wrong, and two independent sources say so.

        **The console.** Asked for chemistry styles, the game showed position
        modifiers. The client resolves a consumable's name and art from its own
        data, so that is the disc's answer to what these cards are, not ours.

        **The PC revival's catalogue** names all twenty of 91-110 as explicit
        transitions -- `LWB->LB`, `RM->RW`, `CM->CAM`, `CDM->CM`, `CAM->CF`,
        `ST->CF` -- under a `Positioning` category. Nothing in that list is a
        play style under any reading. It files 121-136 as an internal GK block
        it does not support either.

        And it carries the decisive detail: its own row for subtype 91 records
        `sourceMember: consumablesTrainingPlayerPlayStyle` *while categorising
        the card as Positioning*. So the member name -- the single piece of
        evidence this server changed its mind on -- is a counter the binary
        groups these under, not a statement of what they do.

        The real chemistry styles are subtypes 250-268 (Basic, Sniper, Hawk,
        Shadow, Engine, Anchor and the rest). This catalogue does not contain
        them, which is the actual reason a player can never find one.

        So `playStyle` was being written from a position card, and the card was
        spent doing it. Refused and recorded, which is where this block was
        before and what `docs/CONSUMABLES.md` argued for: a refusal costs
        nothing, a wrong write costs a card and corrupts a real player.
        """
        self.refused.append(
            {
                "resourceId": row.get("definitionId"),
                "cardsubtypeid": subtype,
                "itemType": row.get("itemType"),
                "targetId": target.get("id"),
                "targetPosition": target.get("preferredPosition"),
                "targetPlayStyle": target.get("style"),
            }
        )
        family = (
            "a goalkeeper block with no established effect"
            if PLAY_STYLE_GK[0] <= subtype <= PLAY_STYLE_GK[1]
            else "a position modifier, not a chemistry style"
        )
        raise ConsumableRefused(
            f"subtype {subtype} is {family}; chemistry styles are 250-268 and "
            "are not in this catalogue"
        )

    def _heal(self, target: dict, subtype: int, amount: int) -> tuple[list[dict], str]:
        games = int(target.get("injuryGames", 0) or 0)
        if games <= 0:
            raise ConsumableRefused("that player is not injured")
        if subtype != HEALING_ANY:
            wanted = HEALING_KINDS[subtype - HEALING_FIRST]
            injury = str(target.get("injuryType", "")).strip().lower()
            if injury.replace(" ", "") != wanted:
                raise ConsumableRefused(
                    f"a {wanted} healing card does not treat a {injury or 'none'} injury"
                )
        remaining = max(0, games - amount)
        target["injuryGames"] = remaining
        if remaining == 0:
            target["injuryType"] = "none"
        return [target], f"healing -{amount} match(es)"

    def _squad_fitness(self, amount: int) -> list[dict]:
        """A team fitness card restores the **whole active squad**.

        It used to restore `inventory.squad`, which is the list built at load
        time and only ever rewritten for squad 1. A club with squad 2 active --
        "Classic XI", 22 players, on 26 August -- had its card spent on squad
        1's eleven instead, so the side about to play got nothing.

        The squad store is what knows the answer: the active id, and that
        squad's own player ids. The legacy list is the fallback for a club that
        has never saved a squad at all.
        """
        club = self.inventory
        squad: list[dict] = []
        squads = club._squads() if hasattr(club, "_squads") else {}
        entry = squads.get(club.active_squad_id()) if squads else None
        if entry:
            by_id = {item["id"]: item for item in club.items if item.get("id")}
            squad = [
                by_id[item_id]
                for item_id in (entry.get("players") or [])
                if item_id and item_id in by_id
            ]
        if not squad:
            squad = [item for item in club.squad if isinstance(item, dict)]
        if not squad:
            raise ConsumableRefused("there is no active squad to restore")
        for player in squad:
            player["fitness"] = min(
                ATTRIBUTE_CEILING, int(player.get("fitness", 0)) + amount
            )
        return squad


# How a listed card finds a buyer.
#
# Nothing settled a listing. A card went up, `expires` never moved, `currentBid`
# stayed 0, and no amount of waiting sold anything -- the market was a shop you
# could buy from and never sell to. There are no other players here, so a buyer
# has to be modelled.
#
# The model is the PC revival's (`KyroGeorge2/FIFA-14-Local-FUT`), which has
# this working, adapted from its SQLite tables to the structures here. Its
# shape, kept because each part earns its place:
#
#   * **price against value decides everything.** At or under the cheapest
#     listing a card goes almost at once; at or under its market value it goes
#     soon; up to about 110% of value it goes late; above that it never sells,
#     which is what makes pricing a decision rather than a formality.
#   * **the delay is deterministic**, mixed from the trade id and the resource.
#     The client polls `trade/status` and `tradePile` constantly -- 134 times in
#     one session here -- and a listing that re-rolled its fate on every poll
#     would flicker between sold and unsold.
#   * **EA's tax comes off the proceeds.** Retail took 5%; a market without it
#     makes listing strictly better than quick-selling at every price.
#
# One thing in that build is a scar worth respecting: a sold card **stays in the
# transfer pile**. Its note records that deleting it crashed the retail parser,
# "FIFA 14's trade-pile parser dereferences the item even for a closed auction".
# So a sale here closes the listing and leaves the card in place.
MARKET_SELL_TAX = 0.05

# (ceiling as a multiple of value, base delay, spread) -- in that order, first
# match wins. The delays are short by retail standards on purpose: this is a
# single-player club, and an auction nobody else can see has no reason to make
# a player wait an hour to find out it worked.
SALE_TIERS = (
    (0.85, 18, 28),
    (1.00, 40, 55),
    (1.10, 75, 100),
)


def _listing_value(listing: dict) -> int:
    item = listing.get("itemData")
    if not isinstance(item, dict):
        return 0
    return _price_for(
        int(item.get("rating") or 0), int(item.get("rareflag") or 0), item
    )


def _sale_delay(listing: dict) -> int | None:
    """Seconds from listing to sale, or None if nobody ever takes it."""
    value = _listing_value(listing)
    try:
        asking = int(listing.get("buyNowPrice") or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0 or asking <= 0:
        return None
    ratio = asking / value
    for ceiling, base, span in SALE_TIERS:
        if ratio <= ceiling:
            trade_id = int(listing.get("tradeId") or 0)
            item = listing.get("itemData") or {}
            resource = int(item.get("resourceId") or item.get("assetId") or 0)
            mixed = (trade_id * 1103515245 + resource * 12345) & 0x7FFFFFFF
            return base + (mixed % max(1, span))
    return None


def _bid_ladder(listing: dict, elapsed: int, delay: int | None) -> tuple[int, int]:
    """The bidding so far: what stands, and how many bids have been made.

    Bids climb from the starting bid towards the buy-now on the way to a sale.
    A listing nobody wants gets none, which is the difference a player should be
    able to see before it expires rather than after.
    """
    if delay is None or elapsed <= 0:
        return 0, 0
    starting = max(0, int(listing.get("startingBid") or 0))
    buy_now = max(starting, int(listing.get("buyNowPrice") or 0))
    progress = min(1.0, elapsed / max(1, delay))
    offers = min(4, int(progress * 4) + (1 if progress > 0.05 else 0))
    if not offers:
        return 0, 0
    step = (buy_now - starting) / 4 if buy_now > starting else 0
    return int(starting + step * (offers - 1)), offers


# Members this server sends on an auction that CardsDLL has no name for.
#
# -- getting a sold card into the SOLD ITEMS pile ---------------------------
#
# The transfer list draws two stacks, SOLD ITEMS and LISTED ITEMS, and for
# weeks a sold card rendered under LISTED with a relist prompt -- the sale had
# happened, the coins were paid, and the card would not leave the list.
#
# The answer came from Kyro's build (`runtime/kyro/local_identity.py`), which
# renders the sold stack correctly on the console. Two things it does that this
# server did not:
#
#   1. The tradePile document carries top-level counts -- `sold`, `selling`,
#      `soldCount`, `activeCount`, `transferListCount`. The stacks are sized
#      from these; without them the client cannot split the piles and shows
#      everything as listed. This is the load-bearing part.
#   2. A sold listing keeps a **positive** `expires` and the sibling time
#      members (`EXPIRE_TIME`, `expireTime`, `startTime`, `endtime`). This
#      server sent `expires: -1`, which reads as an auction that lapsed
#      unsold -- exactly the relist state that was showing.
#
# `soldFor` is still not sent -- Kyro carries the sale price in `currentBid`,
# which the client reads, and keeps no `soldFor` on the wire either. `bidState`
# stays `none`: Kyro sets it none on every auction, sold ones included.
#
# The switch that used to cycle guessed shapes (pileType, saleType, ...) is
# retired; the shape is known now.
SOLD_EXPIRES_SECONDS = 3600


def _mark_sold(listing: dict, price: int) -> None:
    """Stamp a listing as sold, in the shape Kyro's build proves renders."""
    listing["tradeState"] = "closed"
    listing["currentBid"] = price
    listing["soldFor"] = price  # internal only; stripped from the wire
    listing["bidState"] = "none"
    listing["offers"] = 0
    # Positive, not -1. A closed auction with expires -1 reads as lapsed-unsold.
    listing["expires"] = SOLD_EXPIRES_SECONDS
    listing["EXPIRE_TIME"] = SOLD_EXPIRES_SECONDS
    listing["expireTime"] = SOLD_EXPIRES_SECONDS
    listing["startTime"] = 0
    listing["endtime"] = 2147483647
    item = listing.get("itemData")
    if isinstance(item, dict):
        # Left in the pile deliberately: the retail trade-pile parser
        # dereferences the item even for a closed auction, and both the PC
        # revival and Kyro's build record an access violation from deleting it.
        item["itemState"] = "sold"
        item["lastSalePrice"] = price


# `soldFor` alone. Kyro sends `tradeOwner`, so it is no longer stripped -- the
# previous removal was wrong, and a member the working reference sends stays.
UNNAMED_AUCTION_MEMBERS = ("soldFor",)


def _on_the_wire(listing: dict) -> dict:
    return {k: v for k, v in listing.items() if k not in UNNAMED_AUCTION_MEMBERS}


# -- listing a card from the standalone Transfer List screen ----------------
#
# The screen says "This item is not currently listed. Press (A) to list this
# item." -- so it knows the card is unlisted and it offers the action. Pressing
# A sends **no request at all**, so the client aborts before the network: it is
# refusing to open the price dialog on the data it has.
#
# The entry already matches Kyro's build field for field, so the remaining
# candidates are things Kyro does not send either. Read per request from
# `runtime/unlisted-shape.txt`, so a candidate can be tried by backing out of
# the screen and re-entering rather than relaunching. `tools/unlisted_shape.py`
# drives it.
UNLISTED_SHAPE_FILE = (
    Path(__file__).resolve().parent.parent / "runtime" / "unlisted-shape.txt"
)


def unlisted_shape() -> str:
    override = os.environ.get("FIFA14_UNLISTED_SHAPE")
    if override:
        return override.strip().lower()
    try:
        return UNLISTED_SHAPE_FILE.read_text().strip().lower() or "plain"
    except OSError:
        return "plain"


# -- the trade id an unlisted card is given ---------------------------------
#
# Measured on the console, 20 August 2026. A card on the transfer list that was
# never listed needs **two** things before FUT HUB > TRANSFER LIST will act on
# it, and neither works without the other:
#
#   a real `tradeId`     `GetCardIdFromTradeId` is a native binding in
#                        CardsDLL's ION_CardInventory table, beside
#                        `GetTradePileResults`, `GetCardOptions` and
#                        `GetCardIDsForPile` (registration table at 0x89221040).
#                        That is how the screen gets from the row under the
#                        cursor to a card. With `tradeId` 0 -- which every
#                        unlisted row carried for two months -- the row resolves
#                        to no card at all, and `GetCardOptions` is asked about
#                        nothing. Given an id, the row stops being a bare card
#                        and becomes an auction the screen can describe: the
#                        panel changed from "This item is not currently listed"
#                        to Start Price / Buy Now / Current Bid / Time Remaining
#                        on that change alone.
#
#   `tradeState`         ...but still with no actions, because the panel reads
#   `expired`            the clock and the *actions* read the state, and
#                        `inactive` is not a state this screen has actions for.
#                        `expired` is: an auction that lapsed can be relisted.
#                        With both, the button bar went from `B / RS / RS` to
#                        `A List on Transfer Market / B Back / X Actions /
#                        RB Relist All / RS Views`, and a card was listed and
#                        its sale collected from that screen end to end.
#
# The ids come from a block above the real listings (2_000_000_000+) and the
# market (1_900_000_000+), so a pseudo id can never be mistaken for either, and
# `withdraw` keys on that boundary to tell the two apart. They are stable for as
# long as the server is up, because the screen re-reads the pile on every poll
# and a row whose id moved under it is a row it cannot keep selected.
#
# What this costs: the row is presented as a lapsed auction. The card draws in
# the maroon expired tint, the panel says "No buyer was found for this item",
# and Time Remaining reads Expired -- none of which is true of a card that was
# never listed. Whether another state in CardsDLL's table (`free`, `forSale`,
# `invalid`) binds the same actions without the claim is open, and testable
# through the overlay below without a relaunch.
UNLISTED_TRADE_ID_BASE = 2_100_000_000
_unlisted_trade_ids: dict[int, int] = {}


def _pseudo_trade_id(item_id: int) -> int:
    """A stable, non-zero trade id for one card awaiting listing."""
    if item_id not in _unlisted_trade_ids:
        _unlisted_trade_ids[item_id] = (
            UNLISTED_TRADE_ID_BASE + len(_unlisted_trade_ids) + 1
        )
    return _unlisted_trade_ids[item_id]


# -- candidates that cost no relaunch ---------------------------------------
#
# The named candidates below are code, so adding one means restarting the
# server -- and restarting the server means relaunching the title, because the
# account state is rewritten from the title's own session within seconds of
# being cleared. One relaunch per idea is a bad rate for a screen that is being
# bisected.
#
# So a candidate can also be written as data, in `runtime/unlisted-shapes.json`:
#
#     {"nosaleprice": {"base": "asitwas",
#                      "entry": {"set": {"startingBid": 150}},
#                      "item":  {"remove": ["lastSalePrice"]}}}
#
# `base` names a coded candidate to build on, and may be omitted. The file is
# read per request like the switch itself, so a candidate written this way is
# live the moment it is saved.
UNLISTED_SHAPES_FILE = (
    Path(__file__).resolve().parent.parent / "runtime" / "unlisted-shapes.json"
)


def custom_unlisted_shapes() -> dict:
    """Candidates written as data, or {} if there are none to read."""
    try:
        loaded = json.loads(UNLISTED_SHAPES_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _overlay(entry: dict, spec: dict) -> dict:
    """Apply one data-written candidate to an entry, on the way out."""
    entry = dict(entry)
    item = dict(entry["itemData"])
    entry["itemData"] = item
    for name, node in (("entry", entry), ("item", item)):
        block = spec.get(name)
        if not isinstance(block, dict):
            continue
        for member in block.get("remove") or ():
            node.pop(member, None)
        for member, value in (block.get("set") or {}).items():
            node[member] = value
    return entry


def _apply_unlisted_shape(entry: dict, shape: str) -> dict:
    """One candidate shape for a card awaiting listing.

    The coded candidates are retired. Ten of them were cycled against the
    console -- `prices`, `forsale`, `itemid`, `duration`, `club`, `listinglike`,
    `asitwas`, `barecard`, `emptystate`, `tradeid` -- and the answer they were
    looking for is in `_unlisted_entry` now, so `plain` is the measured shape
    rather than the control it used to be. What remains is the overlay, because
    the presentation is still open: see docs/TRADE_PILE.md.

    Applied on the way out; nothing held is mutated, so a candidate that fails
    leaves nothing to undo.
    """
    custom = custom_unlisted_shapes().get(shape)
    if isinstance(custom, dict):
        base = custom.get("base")
        if isinstance(base, str) and base != shape:
            entry = _apply_unlisted_shape(entry, base)
        return _overlay(entry, custom)
    return entry


def _fill_card_aliases(card: dict) -> bool:
    """Give one saved card the alias members `_player_item` now sends.

    Returns whether anything was added. Absent members only -- a value already
    on the card is its own.
    """
    if card.get("itemType") != "player":
        return False
    item_id = card.get("id") or card.get("itemId")
    asset = card.get("assetId")
    if not item_id:
        return False
    defaults = {
        "itemId": item_id,
        "teamId": card.get("teamid"),
        "rareFlag": card.get("rareflag"),
        "definitionId": asset,
        "playerId": asset,
        "morale": 99,
        "loyaltyBonus": 1,
        "resourceGameYear": 2014,
        "owners": 1,
        "pile": PILE_CLUB,
    }
    added = False
    for member, value in defaults.items():
        if member not in card and value is not None:
            card[member] = value
            added = True
    return added


class CardActions:
    """Moving cards between piles, and quick-selling them."""

    def __init__(
        self,
        shop: "PackShop",
        wallet: "Wallet",
        inventory: "ClubInventory | None" = None,
    ) -> None:
        self.shop = shop
        self.wallet = wallet
        self.inventory = inventory
        # Cards taken out of a pack and kept. When an inventory is attached,
        # this *is* its item list -- otherwise a card sent to the club is held
        # here and never appears in the club, which is exactly how it looked:
        # the send succeeded, the card vanished.
        self.club: list[dict] = []
        # Pile 5: cards set aside to be listed, not yet on the market.
        self.transfer: list[dict] = []
        # Cards actually listed, keyed by trade id.
        self.listings: dict[int, dict] = {}
        # Item ids the client tried to move that this server has never held.
        # Every one of these is a card the player saw and lost.
        self.unmatched: list[int] = []
        self._next_trade_id = 2_000_000_000

    def _take_pending(self, item_id: int) -> dict | None:
        for index, item in enumerate(self.shop.pending):
            if item["id"] == item_id:
                return self.shop.pending.pop(index)
        return None

    def move(self, document: dict) -> bytes:
        """PUT /item -- send to club, or to the transfer list.

        The body is {"itemData":[{"id":N,"pile":"club","swap":0,"tradeId":0}]}
        and each entry has to be acknowledged individually. Answering with a
        club search, which is what the old fixture did, acknowledges nothing:
        the card stays in the pack screen and the button appears dead.

        The pile is a **name**, not the number this docstring used to claim --
        "club" or "trade". See PILE_NAMES for what reading it as a number cost.
        """
        entries = document.get("itemData") if isinstance(document, dict) else None
        results = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            try:
                item_id = int(entry.get("id") or entry.get("itemId") or 0)
            except (TypeError, ValueError):
                continue
            pile = _pile_number(entry.get("pile", PILE_CLUB))
            if pile is None:
                # A pile this server cannot name. Refusing is deliberate: the
                # old code defaulted to the club here, and that default is
                # precisely what swallowed fifteen transfer-list moves without
                # anyone being able to see it happen.
                self.unmatched.append(item_id)
                results.append(
                    {
                        "id": item_id,
                        "success": False,
                        "reason": "unknown pile",
                        "errorCode": 461,
                        "pile": entry.get("pile"),
                    }
                )
                continue
            item = self._take_pending(item_id)
            if item is None:
                for index, owned in enumerate(self.club):
                    if owned["id"] == item_id:
                        item = self.club.pop(index)
                        break
            if item is None:
                # And the transfer list. This was missing, and it went unnoticed
                # for as long as the standalone Transfer List screen offered no
                # actions: a card already on the list could only be moved from
                # the item screen, which reaches it through the club. The moment
                # that screen bound its menu, "Send to Club" on a listed-but-not
                # -sold card answered 461 and the card stayed where it was.
                #
                # Not popped: both branches below manage transfer membership
                # themselves, and popping here would make the transfer-to-
                # transfer case drop the card.
                for held in self.transfer:
                    if held["id"] == item_id:
                        item = held
                        break
            if item is not None:
                if pile == PILE_TRANSFER:
                    # Set aside to be listed. It leaves the club until it is
                    # either listed or moved back -- taking it out of the
                    # inventory too, or the card shows in both places at once,
                    # which is what looked like a duplicate.
                    item["itemState"] = "forSale"
                    item["pile"] = PILE_TRANSFER
                    self._forget(item_id)
                    if not any(held["id"] == item_id for held in self.transfer):
                        self.transfer.append(item)
                else:
                    item["itemState"] = "free"
                    item["pile"] = PILE_CLUB
                    self.transfer[:] = [
                        held for held in self.transfer if held["id"] != item_id
                    ]
                    self._keep(item)
            if item is None:
                # An id neither pending nor already held. This used to be
                # acknowledged as a success anyway, which is how a card could
                # be drawn, shown, sent to the club, confirmed -- and then
                # exist nowhere. A TOTS Ruffier went that way.
                #
                # Reporting the failure is the honest answer and the only one
                # that can be noticed. The card is still gone, but the client
                # is no longer told otherwise.
                self.unmatched.append(item_id)
                results.append(
                    {
                        "id": item_id,
                        "success": False,
                        "reason": "item not found",
                        "errorCode": 461,
                        "pile": pile,
                    }
                )
                continue
            results.append(
                {
                    "id": item_id,
                    "success": True,
                    "reason": "",
                    "errorCode": 0,
                    "pile": pile,
                }
            )
        return json.dumps({"itemData": results}, separators=(",", ":")).encode()

    def discard_many(self, item_ids: list[int]) -> bytes:
        """Quick sell one card or a whole pack at once.

        The client sends {"itemId":[...]} -- always a list, even for one card,
        and twelve entries long when you sell a pack outright. Reading it as a
        single integer produced no id at all, so the reply named no item and the
        screen raised an error.
        """
        sold, awarded = [], 0
        for item_id in item_ids:
            item = self._take_pending(item_id)
            if item is None:
                for index, owned in enumerate(self.club):
                    if owned["id"] == item_id:
                        item = self.club.pop(index)
                        break
            if item is not None:
                self._forget(item_id)
            awarded += int(item["discardValue"]) if item else 200
            sold.append({"id": item_id})
        self.wallet.credit(awarded)
        return json.dumps(
            {"totalCredits": awarded, "items": sold},
            separators=(",", ":"),
        ).encode()

    def discard(self, item_id: int | None = None) -> bytes:
        """Quick sell.

        `totalCredits` here is what this sale paid, not the resulting balance --
        the absolute figure comes from /user/credits. Returning the whole
        balance made the sale appear to pay tens of thousands.
        """
        item = self._take_pending(item_id) if item_id else None
        if item is None:
            for index, owned in enumerate(self.club):
                if item_id and owned["id"] == item_id:
                    item = self.club.pop(index)
                    break
        awarded = int(item["discardValue"]) if item else 200
        self.wallet.credit(awarded)
        return json.dumps(
            {
                "totalCredits": awarded,
                "items": [{"id": item_id}] if item_id else [],
            },
            separators=(",", ":"),
        ).encode()


    def _forget(self, item_id: int) -> None:
        """Drop a card from the club, wherever it is held."""
        self.club = [held for held in self.club if held["id"] != item_id]
        if self.inventory is not None:
            self.inventory.items = [
                held for held in self.inventory.items if held["id"] != item_id
            ]

    def _keep(self, item: dict) -> None:
        """Put a card in the club.

        A duplicate is allowed in. Refusing it here was wrong: owning a second
        copy is legitimate, and it is the moment of keeping it that the game
        offers to compare the two and decide -- sell one, swap, or hold both.
        Silently dropping it made "send to club" do nothing at all for exactly
        the card that needed a decision.

        What is still refused is the same item id arriving twice, which is not
        a second card but the same one counted again.
        """
        if any(held["id"] == item["id"] for held in self.club):
            return

        # Duplicates are allowed in. The card carries duplicateItemId naming
        # the one it repeats, so the screen can offer the comparison itself --
        # which is the retail behaviour, and better than deciding for the
        # player as the auto-sell stopgap did.
        self.club.append(item)
        if self.inventory is not None:
            if any(held["id"] == item["id"] for held in self.inventory.items):
                return
            self.inventory.items.append(item)

    def _find(self, item_id: int) -> dict | None:
        for pool in (self.transfer, self.club, self.shop.pending):
            for item in pool:
                if item["id"] == item_id:
                    return item
        return None

    def list_for_sale(self, document: dict) -> bytes:
        """POST /auctionhouse -- put a card on the market.

        The body names the item and the prices; the reply has to hand back a
        trade id, because that is what the trade pile and every later bid or
        withdrawal refer to.
        """
        item_data = document.get("itemData") if isinstance(document, dict) else None
        if isinstance(item_data, list):
            item_data = item_data[0] if item_data else None
        item_id = None
        if isinstance(item_data, dict):
            item_id = item_data.get("id") or item_data.get("itemId")
        if item_id is None:
            item_id = document.get("itemId") or document.get("id")
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            item_id = None

        def price(key: str, fallback: int) -> int:
            try:
                return int(document.get(key) or fallback)
            except (TypeError, ValueError):
                return fallback

        item = self._find(item_id) if item_id else None
        starting = price("startingBid", 150)
        buy_now = price("buyNowPrice", max(200, starting * 2))
        duration = price("duration", 3600)

        self._next_trade_id += 1
        trade_id = self._next_trade_id
        listing = {
            "tradeId": trade_id,
            "id": trade_id,
            "itemData": item or {"id": item_id},
            "tradeState": "active",
            "startingBid": starting,
            "buyNowPrice": buy_now,
            "currentBid": 0,
            "offers": 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": True,
            "expires": duration,
            # When it went up, and for how long. Without these a listing had no
            # clock at all: `expires` was a number that never moved and nothing
            # could tell a fresh auction from one that had run its course.
            "listedAt": int(time.time()),
            "duration": duration,
            "sellerName": SELLER_NAME,
            "sellerEstablished": 2013,
            "sellerId": 0,
            "confidenceValue": 100,
        }
        self.listings[trade_id] = listing
        if isinstance(listing["itemData"], dict):
            listing["itemData"]["pile"] = PILE_TRANSFER
        self.transfer[:] = [
            held for held in self.transfer if held["id"] != item_id
        ]
        self._take_pending(item_id)
        # `_forget`, not a pop from `self.club`.
        #
        # `self.club` and `self.inventory.items` are two lists holding the same
        # cards -- `_keep` appends to both -- and popping only the first left
        # the card in the club for every screen that reads the inventory. All
        # thirteen cards sold in the session of 17 August 2026 were still in
        # the club afterwards, which is what "the card stays in my club" was,
        # and it is also why sold cards came back as duplicates.
        self._forget(item_id)
        return json.dumps(listing, separators=(",", ":")).encode()

    def _unlisted_entry(self, item: dict) -> dict:
        """A card sent to the transfer list but not put up for sale yet.

        Sending a card to the transfer list takes it out of the club -- it has
        to, or it shows in both places at once -- and until 18 August the trade
        pile answered with the listings alone, so the card was in neither.

        **`tradeId` 0 and no trade state**, which is what
        `MarvelcoCode/Impulsum14` serves and what this console accepted on
        26 August: the cards read as unlisted rather than expired, and A lists
        them. Two listings in that session, at 12500/13000 and 104000/114000.

        This corrects a conclusion in `docs/TRADE_PILE.md` that was held for
        six days and was wrong. It read:

            The panel text and the action are the same member.
            tradeId 0  -- "Press (A) to list this item", and a handler with no
                          card to act on
            a real id  -- the handler has a card ... There is no third option

        There was. The reading behind it was taken on 20 August against an
        entry that carried `sellerId`, `sellerEstablished`, `endtime`,
        `startTime`, `EXPIRE_TIME` and `expireTime` -- six members the PC build
        does not send -- so "tradeId 0 does not work" was measured on a
        document that differed from the working one in more than its trade id.
        The document then wrote "Do not reopen this by trying another
        `tradeState`", which is exactly the instruction that would have kept it
        wrong. The player asked to try it anyway.

        The old shape is still reachable as `expired` in
        `runtime/unlisted-shapes.json`, because it is the one that gives the
        relist menu and RB Relist All has never been traced.
        """
        card = dict(item)
        card["pile"] = PILE_TRANSFER
        card["itemState"] = "free"
        # `tradeable` True is what lets the screen act on the card: pressing A
        # over an unlisted transfer-list card did nothing without it. Kyro's
        # build sets both, and this server sent untradeable False but never the
        # positive `tradeable`, so the List Item action was never offered.
        card["untradeable"] = False
        card["tradeable"] = True
        # Field for field what the PC build sends, and nothing besides. The six
        # members this server used to add on top are the reason the earlier
        # reading of `tradeId` 0 came out wrong, so they stay out.
        return {
            # No auction yet. A pseudo id makes the row an auction, and an
            # auction that was never listed can only read as a lapsed one.
            "tradeId": 0,
            "itemData": card,
            "tradeState": None,
            "startingBid": 0,
            "buyNowPrice": 0,
            "currentBid": 0,
            "offers": 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": True,
            "expires": -1,
            "sellerName": "",
            "seller": 0,
            "confidenceValue": 0,
        }

    def settle_market(self) -> list[dict]:
        """Advance every live listing, and sell the ones a buyer would take.

        Called from the routes the client already polls -- `tradePile` and
        `trade/status` -- rather than from a timer, because there is no timer
        here and the client asks constantly anyway.

        Returns what sold, so the caller can credit the wallet and journal it.
        Nothing is credited in here: this object does not own the coins.
        """
        now = int(time.time())
        sold: list[dict] = []
        for listing in self.listings.values():
            if listing.get("tradeState") != "active":
                continue
            listed_at = int(listing.get("listedAt") or 0)
            if not listed_at:
                listing["listedAt"] = listed_at = now
            duration = max(1, int(listing.get("duration") or 3600))
            elapsed = max(0, now - listed_at)
            delay = _sale_delay(listing)

            if delay is not None and elapsed >= delay:
                price = max(0, int(listing.get("buyNowPrice") or 0))
                net = max(0, int(round(price * (1.0 - MARKET_SELL_TAX))))
                # The shape of a sold listing, taken from Kyro's build, which
                # renders the SOLD ITEMS pile correctly on the console. See
                # `_mark_sold` and `docs/TRADE_PILE.md` -- this is a reference
                # match, not a guess. The switch that used to guess it is gone.
                _mark_sold(listing, price)
                sold.append({"listing": listing, "price": price, "net": net})
                continue

            remaining = duration - elapsed
            if remaining <= 0:
                # Ran its course with no buyer. The card comes back rather than
                # vanishing -- a listing that expires into nothing is how cards
                # were lost before.
                listing["tradeState"] = "expired"
                listing["expires"] = -1
                item = listing.get("itemData")
                if isinstance(item, dict):
                    item["itemState"] = "free"
                    if item not in self.transfer:
                        self.transfer.append(item)
                continue

            listing["expires"] = int(remaining)
            bid, offers = _bid_ladder(listing, elapsed, delay)
            if bid:
                listing["currentBid"] = bid
                listing["offers"] = offers
                listing["bidState"] = "none"
        return sold


    def trade_pile(self, coins: int) -> bytes:
        """The transfer list: what is up for sale, and what is merely on it.

        Sorted the way Kyro's build sorts it -- active first, then closed
        (sold), then everything else -- and carrying the top-level counts the
        SOLD ITEMS and LISTED ITEMS stacks are sized from. Without those counts
        the client cannot tell the two piles apart, which is why every sold
        card sat under LISTED.
        """
        order = {"active": 0, "closed": 1}
        entries = sorted(
            self.listings.values(),
            key=lambda l: (order.get(l.get("tradeState"), 2), l.get("tradeId") or 0),
        )
        listed = {
            listing["itemData"].get("id")
            for listing in self.listings.values()
            if isinstance(listing.get("itemData"), dict)
        }
        shape = unlisted_shape()
        entries += [
            _apply_unlisted_shape(self._unlisted_entry(item), shape)
            for item in self.transfer
            if item.get("id") not in listed
        ]

        active = sum(1 for l in self.listings.values() if l.get("tradeState") == "active")
        sold = sum(1 for l in self.listings.values() if l.get("tradeState") == "closed")
        unlisted = len(entries) - len(self.listings)
        return json.dumps(
            {
                "auctionInfo": [_on_the_wire(entry) for entry in entries],
                "duplicateItemIdList": [],
                "total": len(entries),
                # The counts the stacks are drawn from. Every alias Kyro sends,
                # because which one this console binds to is not established and
                # they are harmless together.
                "selling": active,
                "sold": sold,
                "available": unlisted,
                "unlisted": unlisted,
                "activeCount": active,
                "soldCount": sold,
                "transferListCount": len(entries),
                "tradePileCount": len(entries),
                "tradePileItems": len(entries),
                "credits": coins,
                "totalCredits": coins,
                "coins": coins,
            },
            separators=(",", ":"),
        ).encode()

    def withdraw(self, trade_id: int) -> bytes:
        """Remove a listing from the transfer list.

        Two cases, the way Kyro's build splits them:

          sold (`closed`)  the card is gone -- the coins were credited when it
                           settled, and "remove from Transfer List" just clears
                           the sold card. It is never resurrected into the club;
                           returning it to the transfer pile, as this used to do
                           for every listing, is how a card you had sold came
                           back as a phantom you could list again.
          active           an unsold withdrawal: the card goes back to the
                           transfer pile so it can be relisted or sent to club.

        The reply carries `tradeId` as well as `id`; the collect route reads it.
        """
        if trade_id >= UNLISTED_TRADE_ID_BASE:
            # A card that was never listed: the trade id is the one this server
            # invented so the screen could resolve the row. There is no auction
            # to withdraw, so "remove from the transfer list" means what it says
            # -- the card goes back to the club rather than staying on a list it
            # has just been taken off, which is how it read before.
            for held in list(self.transfer):
                if _pseudo_trade_id(held["id"]) == trade_id:
                    held["itemState"] = "free"
                    held["pile"] = PILE_CLUB
                    self.transfer.remove(held)
                    self._keep(held)
                    break
            return json.dumps(
                {"id": trade_id, "tradeId": trade_id}, separators=(",", ":")
            ).encode()

        listing = self.listings.pop(trade_id, None)
        if listing and isinstance(listing.get("itemData"), dict):
            item = listing["itemData"]
            if listing.get("tradeState") == "closed":
                # Sold and collected. Nothing returns.
                pass
            elif item.get("id"):
                # `id`, not `assetId`: the guard is "is this a card at all", and
                # `assetId` stopped answering that once a consumable carried
                # `cardassetid` instead.
                item["itemState"] = "free"
                item["pile"] = PILE_TRANSFER
                if not any(held["id"] == item["id"] for held in self.transfer):
                    self.transfer.append(item)
        return json.dumps(
            {"id": trade_id, "tradeId": trade_id}, separators=(",", ":")
        ).encode()

    def restamp_cards(self) -> int:
        """Add the alias members to cards saved before they were sent.

        A card written by an older build carries `id` and no `itemId`, and the
        standalone Transfer List screen builds no action menu for such a card --
        it renders it and reads its state, then offers nothing. Every card the
        club holds is brought up to the current shape on load, so a card saved
        months ago behaves like one packed today.

        Only fills what is absent. A card that already carries a member keeps
        its own value, so nothing a match or a consumable wrote is overwritten.
        """
        repaired = 0
        pools = [self.club, self.transfer, self.shop.pending]
        if self.inventory is not None:
            pools.append(self.inventory.items)
        for listing in self.listings.values():
            item = listing.get("itemData")
            if isinstance(item, dict):
                pools.append([item])
        seen: set[int] = set()
        for pool in pools:
            for card in pool:
                if not isinstance(card, dict):
                    continue
                key = id(card)
                if key in seen:
                    continue
                seen.add(key)
                if _fill_card_aliases(card):
                    repaired += 1
        return repaired

    def restamp_sold(self) -> None:
        """Re-apply the sold shape to listings loaded from an older save.

        A card sold before the SOLD-pile fix was saved with `expires: -1` and
        without the sibling time members, so on load it read as an auction that
        lapsed unsold and stayed under LISTED while a freshly sold card moved to
        SOLD. This brings the old ones up to the shape `_mark_sold` writes, so
        every sold card sits in the sold stack regardless of when it sold.
        """
        for listing in self.listings.values():
            if listing.get("tradeState") == "closed":
                price = int(
                    listing.get("soldFor")
                    or listing.get("currentBid")
                    or listing.get("buyNowPrice")
                    or 0
                )
                _mark_sold(listing, price)


# -- everything a club owns that is not a player ---------------------------
#
# The club screens have tabs for consumables, club items and staff, and a
# search that filters on them. Serving players only leaves those tabs empty and
# their filters inert.

CLUB_ITEM_ID_BASE = 1_750_000_000

# Club item ids come from the family and the asset, never from a running
# counter. This is not tidiness, it is a save-compatibility rule.
#
# The seed used to number every club item in sequence, so the ids depended on
# how many of each family happened to exist. Expanding badges from four to 556
# moved stadium and ball down by four -- and a save written before the change
# carried `changed` entries for 1750000269 and 1750000273, which were a stadium
# and a ball then and would have been a ball and a badge after. `ClubSave.load`
# verifies the type before overwriting, so nothing would have been corrupted,
# but the player's activated stadium and ball would have gone quietly missing.
#
# With a block per family, adding a card to one family cannot renumber another,
# and a stadium keeps its id for as long as its asset id means the same thing.
#
# Consumables keep the running counter from CLUB_ITEM_ID_BASE. There are 261 of
# them, they come out of the card database in a fixed order, and every id in a
# real save's `sold` list is one of theirs.
CLUB_ITEM_BLOCKS = {
    "kit": 1_000_000,
    "stadium": 2_000_000,
    "ball": 3_000_000,
    "badge": 4_000_000,
}


def club_item_id(kind: str, asset_id: int) -> int:
    """The stable item id for one club item."""
    return CLUB_ITEM_ID_BASE + CLUB_ITEM_BLOCKS[kind] + int(asset_id)


# The probe sweep gets a block of its own, well clear of everything real, so a
# probe run can never be mistaken for a club item in a save.
PROBE_ID_BASE = 1_790_000_000


def _probe_resource(kind: str, asset: int) -> int:
    """The resourceId a probe card must carry to render.

    A badge's crest resolves from its **resourceId**, not its asset id -- the
    same asset 241 rendered FC Barcelona at resourceId 6000000 and drew NOT
    FOUND at 6900241, 18 August 2026. The first probe put every club item at
    PROBE_RESOURCE_BASE + asset (6_900_000+), outside the badge resource range,
    so all ten showed NOT FOUND and hid the working club behind them.

    So a probe carries the family's **real** resource formula and varies only
    the asset. For a badge that is BADGE_RESOURCE_BASE + club id, exactly what
    the normal seed sends. For kit, stadium and ball it is the family's base
    counted from its first asset -- their art may resolve from the asset or the
    resource, and this leaves that the only variable in the sweep.
    """
    if kind == "badge":
        return BADGE_RESOURCE_BASE + asset
    for name, first_asset, first_resource, _count in CLUB_ITEM_KINDS:
        if name == kind:
            return first_resource + (asset - first_asset)
    return PROBE_RESOURCE_BASE + asset

# The consumables come out of the game's own card database -- see
# tools/build_consumables.py. They used to be invented here: three grades per
# family with asset ids counted up from 1000, which drew NOT FOUND art on every
# card, named all of them "Entraînement equipe" and applied nothing. The title
# looks a consumable up by its subtype and draws it by its asset id, so neither
# is ours to choose.
CONSUMABLE_FILE = Path(__file__).resolve().parent / "fifa14_consumables.json"

# Contracts and fitness are what a club actually runs out of, so it carries a
# stack of each; one of everything else is enough to apply it.
CONSUMABLE_COPIES = {"contract": 5, "fitness": 5, "healing": 3}


def _consumable_catalogue() -> list[dict]:
    try:
        return json.loads(CONSUMABLE_FILE.read_text())["consumables"]
    except (OSError, ValueError, KeyError):
        return []

# The four families whose art ids are known to resolve on the console.
#
# Kits 14-17, badges 241-244, stadiums 6-8 and balls 23-25 were each seen
# rendering with their own artwork -- "Stade Gerland" on a stadium card,
# 17 August 2026 -- so these asset ids address something real.
CLUB_ITEM_KINDS = [
    ("kit", 14, 6300000, 4),
    ("stadium", 6, 6200000, 3),
    ("ball", 23, 8120091, 3),
]

# Managers and staff, which are NOT in the draw or the seed.
#
# Their asset ids were invented here like every other club item's, but unlike
# the four above nothing has ever confirmed they resolve -- and on 17 August
# 2026 a Premium Gold Pack handed out asset 4, staff index 2, and it drew the
# grey FIFA 14 card back with no front at all. The two ranges overlap at asset
# 2, which is on its own enough to say nobody chose them against a table.
#
# A blank card is not merely ugly. The pack reveal is the screen with the
# freeze history in this project, and an unrenderable card is the shape that
# caused it. These stay out until a real table names them; the club held none
# anyway, and `squad_manager()` already declines to field one for the same
# reason (see `manager_slot`).
CLUB_ITEM_KINDS_UNVERIFIED = [
    ("manager", 1, 6100000, 2),
    ("staff", 2, 6150000, 3),
]


# Training cards draw NOT FOUND, and their art ids are the only outliers.
#
# Every consumable family that renders uses an id in a narrow band -- contracts
# 7 and 8, healing 9, fitness 10, position 34/35/43, chemistry styles 50.
# Training uses **1 and 3**, and training is the only family drawing the green
# placeholder. `tools/build_consumables.py` took those two out of
# `fcc_trainingcards`, which carries a card art id and no name, and its own note
# says which block is which is "NOT established".
#
# `FIFA14_TRAINING_ASSET_SWEEP=N` gives each training card a different art id
# counting up from N, so one pass over the Apply Consumable picker reads 42 ids
# at once. A card that renders has a real id; the rest stay NOT FOUND, which
# they already are, so the sweep cannot make the screen worse than it is.
#
# The club-item method: an id the game does not know draws a placeholder, so
# the placeholder is the measurement.
def training_asset_sweep() -> int:
    try:
        return max(0, int(os.environ.get("FIFA14_TRAINING_ASSET_SWEEP", "0")))
    except ValueError:
        return 0


_TRAINING_SWEEP_SEEN: dict[int, int] = {}


def _training_asset(card: dict) -> int:
    """The art id a training card goes out with, swept if a sweep is armed."""
    first = training_asset_sweep()
    if not first or card.get("itemType") != "training":
        return card["assetId"]
    resource = int(card["definitionId"])
    if resource not in _TRAINING_SWEEP_SEEN:
        _TRAINING_SWEEP_SEEN[resource] = first + len(_TRAINING_SWEEP_SEEN)
    return _TRAINING_SWEEP_SEEN[resource]


def _consumable_item(card: dict, item_id: int, item_state: str = "free") -> dict:
    """One consumable card, in the shape the club and a pack both use.

    A card drawn from a pack and a card the club was seeded with have to be
    the same object -- the pack screen, the club list and the consumables tab
    all read the same members, and a card that is shaped one way in the pack
    and another in the club is a card that disappears on the way there.

    Three of the members this used to carry -- `definitionId`, `consumableType`
    and `consumableMember` -- appear **nowhere in CardsDLL**, not in the JSON
    member-name table and not anywhere else in the module. A parser has no
    name for them, so they were never read; `consumableMember` was invented
    here outright. The PC revival hit a freeze inside the purchased-items
    parser from exactly this, an extra descriptive field on a pack consumable,
    and it froze on the *second* pack rather than the first.

    Two more that the reference sends are absent from this build too --
    `resourceGameYear` and the `rareFlag` capitalisation -- so they are not
    sent either. What remains is what the binary has a name for.

    `cardassetid`, not `assetId`: the art id for a consumable has its own
    member, and sending only `assetId` is why five of nine cards in a Premium
    Gold Pack drew NOT FOUND, all labelled "Entraînement", all showing +0.

    `resourceId` is the card's own database id -- 5001001 and up -- not a value
    derived from the art. Derived, every training card in the club shared one
    resource id, which is also the id the apply route addresses.
    """
    rare = 1 if card["rare"] else 0
    item = {
        "id": item_id,
        "itemId": item_id,
        "resourceId": card["definitionId"],
        "cardassetid": _training_asset(card),
        "cardsubtypeid": card["cardsubtypeid"],
        "rating": card["rating"],
        "rareflag": rare,
        # Both spellings. The PC revival sends both because different parsers
        # in the client read different ones, and the Apply Consumable picker
        # is one of the screens that had nothing to show.
        "rareFlag": rare,
        "amount": card["amount"],
        "itemType": CONSUMABLE_WIRE_TYPE.get(card["itemType"], "development"),
        "itemState": item_state,
        "discardValue": consumable_discard_value(card["rating"]),
        "lastSalePrice": 0,
        "timestamp": issued_now(),
        "owners": 1,
        "untradeable": False,
        "tradeable": True,
        # Which pile the card sits in. Absent, the parser left it at whatever
        # its constructor held, and a picker asking "what do I own" has no way
        # to answer. 7 is the club, 6 is the purchased pile a card sits in
        # until it is sent to the club -- the PC revival overwrites its stored
        # 6 with a 7 on everything it preloads for the picker.
        "pile": PILE_PURCHASED if item_state == "new" else PILE_CLUB,
        "resourceGameYear": 2014,
        "count": 1,
    }
    # A contract is worth a different number of matches to a gold, a silver
    # and a bronze card, and the card carries all three figures.
    for tier in ("bronze", "silver", "gold"):
        if tier in card:
            item[tier] = card[tier]
    # The misc cards carry a second art id: `cardassetid` is the family's
    # picture and `assetid` picks the variant inside it. Both are member names
    # the module carries. Without it the club-modifier cards drew NOT FOUND
    # while their names resolved -- the client knew the card and had no
    # picture for it.
    if "assetid" in card:
        item["assetid"] = card["assetid"]
    return item


def _club_item(
    kind: str, asset_id: int, resource_id: int, item_id: int,
    item_state: str = "free", quality: int = 0,
    rating: int | None = None, rare: int | None = None,
    discard: int | None = None,
) -> dict:
    """One kit, badge, stadium or ball card.

    Each kind carries members of its own, and sending only `assetId` and
    `resourceId` is why My Club drew grey placeholders where the badges and
    kits should be. The stadiums rendered on the same screen because a stadium
    resolves from its asset alone; nothing else does.

    The extra members follow the PC revival's proven envelope:

        stadium   stadiumid, StadiumId, category 4
        badge     badgeDBid, badge, badgeId, badgeResourceId,
                  badgeDefinitionId -- and itemType `custom`
        kit       category, teamkittypetechid (0 home, 1 away)

    `category` is 4 for a stadium and 0 otherwise, which is that build's own
    default rather than a number chosen here.
    """
    wire = BADGE_WIRE_TYPE if kind == "badge" else kind
    # A card with no rating has no tier, and the card screen draws a card with
    # no tier as the lowest one -- which is why every kit and badge in the club
    # read bronze non-rare. `quality` indexes CLUB_ITEM_QUALITY.
    if rating is None or rare is None:
        rating, rare = CLUB_ITEM_QUALITY[int(quality) % len(CLUB_ITEM_QUALITY)]
    if discard is None:
        discard = club_discard_value(rating, rare)
    item = {
        "id": item_id,
        "assetId": asset_id,
        "resourceId": resource_id,
        "rating": rating,
        "itemType": wire,
        "itemState": item_state,
        "discardValue": discard,
        "lastSalePrice": 0,
        "timestamp": issued_now(),
        "untradeable": False,
        "rareflag": rare,
    }
    if kind == "stadium":
        item["category"] = 4
        item["stadiumid"] = asset_id
        item["StadiumId"] = asset_id
    elif kind == "badge":
        item["category"] = 0
        item["badgeDBid"] = resource_id
        item["badge"] = asset_id
        item["badgeId"] = asset_id
        item["badgeResourceId"] = resource_id
        item["badgeDefinitionId"] = resource_id
    elif kind == "kit":
        item["category"] = 0
        # Home is the slot the club fields first; away is everything else.
        item["teamkittypetechid"] = 0 if item_state == "activeHomeKit" else 1
    return item


MANAGER_FILE = Path(__file__).resolve().parent / "fifa14_managers.json"
# Managers sit in their own id block, above the club items, for the reason the
# club items have blocks at all: an id has to mean the same card in a save
# written last week as in one written today.
MANAGER_ID_BASE = 1_760_000_000


def manager_catalogue() -> list[dict]:
    """Every manager the game has, or nothing if the file is absent."""
    try:
        return json.loads(MANAGER_FILE.read_text())["managers"]
    except (OSError, ValueError, KeyError):
        return []


def _manager_item(entry: dict, item_id: int, item_state: str = "free") -> dict:
    """One manager card.

    `assetId` **is** the resource id. That is not a shortcut: a manager's art is
    addressed by the same number as the card, the way a stadium's is, and unlike
    a kit or a badge which carry a family asset and vary the resource.
    MarvelcoCode/Impulsum14 builds it the same way.
    
    `cardsubtypeid` 4 is the manager subtype, from that same build.

    What is deliberately **not** here: `dream`, `marketDataMinPrice` and
    `marketDataMaxPrice`. That build sends all three and none of them is a
    member of this console's CardsDLL -- checked by exact match -- and an
    unknown member on a card is the shape that has frozen a parser in this
    project twice. Price ranges are a FIFA 15 idea anyway.

    Nothing about the rating is invented: it comes from the game's own database
    through `fifa14_managers.json`, and the quick-sell value follows the same
    table every other card here uses.
    """
    resource = int(entry["resourceId"])
    rating = int(entry.get("rating") or 0)
    league = int(entry.get("league") or 0)
    return {
        "id": item_id,
        "assetId": resource,
        "resourceId": resource,
        "rating": rating,
        "itemType": "manager",
        "itemState": item_state,
        "cardsubtypeid": 4,
        "discardValue": club_discard_value(rating, 1),
        "lastSalePrice": 0,
        "timestamp": issued_now(),
        "untradeable": False,
        "rareflag": 1,
        "owners": 1,
        "contract": DEFAULT_CONTRACT,
        "formation": FORMATION,
        "morale": 0,
        "fitness": 0,
        "injuryType": "none",
        "injuryGames": 0,
        "suspension": 0,
        "training": 0,
        "preferredPosition": "",
        "playStyle": 0,
        "teamid": 0,
        "leagueId": league,
        "leagueid": league,
        "nation": int(entry.get("nation") or 0),
        "attributeList": [],
        "statsList": [],
        "lifetimeStats": [],
        "name": str(entry.get("name") or ""),
    }


STAFF_FILE = Path(__file__).resolve().parent / "fifa14_staff.json"
# Staff resources run 2000001-9000045 across four families, so the block has to
# be wide enough to hold nine million without reaching the next one. Managers
# sit at 1_760_000_000 and this ends below 1_780_000_000.
STAFF_ID_BASE = 1_770_000_000


def staff_catalogue() -> list[dict]:
    """Every coach and physio the game has, or nothing if the file is absent."""
    try:
        return json.loads(STAFF_FILE.read_text())["staff"]
    except (OSError, ValueError, KeyError):
        return []


def _staff_item(entry: dict, item_id: int, item_state: str = "free") -> dict:
    """One head coach, goalkeeping coach, fitness coach or physio.

    Four families, each with its own subtype and its own resource run:

        headCoach     5    2000001-2000037
        gkCoach       6    9000001-9000045
        physio        7    4000001-4000042
        fitnessCoach  8    3000004-3000039

    All four `itemType` values are in CardsDLL, and so are the counters they
    report under -- `staffHeadCoach`, `staffGKCoach`, `staffFitnessCoach`,
    `staffPhysio`, which `club_stats_response` already sends and which have
    read zero because nothing ever filled them.

    `assetId` is the resource, as it is for a manager and a stadium.

    `attr` is dropped. The source carries it, values 0-6, and **`attr` is not a
    member of this binary** -- unlike `amount`, `posbonus` and `fieldpos`, which
    all are. Same rule that kept `dream` and the two market-price members off
    the manager card.
    """
    resource = int(entry["resourceId"])
    rating = int(entry.get("rating") or 0)
    rare = int(entry.get("rare") or 0)
    return {
        "id": item_id,
        "assetId": resource,
        "resourceId": resource,
        "rating": rating,
        "itemType": str(entry.get("itemType") or ""),
        "itemState": item_state,
        "cardsubtypeid": int(entry.get("cardsubtypeid") or 0),
        "discardValue": club_discard_value(rating, rare),
        "lastSalePrice": 0,
        "timestamp": issued_now(),
        "untradeable": False,
        "rareflag": rare,
        "owners": 1,
        "contract": DEFAULT_CONTRACT,
        "morale": 0,
        "fitness": 0,
        "injuryType": "none",
        "injuryGames": 0,
        "suspension": 0,
        "training": 0,
        "preferredPosition": "",
        "playStyle": 0,
        "teamid": 0,
        "leagueId": 0,
        "nation": 0,
        # What the card is worth to a squad: the bonus it applies, which slot
        # it applies to, and where on the pitch. All three are in the table.
        "amount": int(entry.get("amount") or 0),
        "posbonus": int(entry.get("posbonus") or 0),
        "fieldpos": int(entry.get("fieldpos") or 0),
        "attributeList": [],
        "statsList": [],
        "lifetimeStats": [],
        "name": str(entry.get("name") or ""),
    }


def seed_staff() -> list[dict]:
    """Staff put straight into the club, when asked for.

    `FIFA14_SEED_STAFF=1`, and the same caveat as `seed_managers`: this is a
    way to see whether the cards draw, not how they should arrive.
    """
    if os.environ.get("FIFA14_SEED_STAFF", "").strip().lower() not in {
        "1", "true", "yes"
    }:
        return []
    return [
        _staff_item(entry, STAFF_ID_BASE + int(entry["resourceId"]))
        for entry in staff_catalogue()
    ]


def seed_managers() -> list[dict]:
    """Managers put straight into the club, when asked for.

    `FIFA14_SEED_MANAGERS=1` puts every manager the game has into the club at
    once. That is not how they should arrive -- they belong in packs, like the
    kits and badges -- but it is how to find out whether they draw at all, and
    on which the art fails, without opening a hundred packs first.
    """
    if os.environ.get("FIFA14_SEED_MANAGERS", "").strip().lower() not in {
        "1", "true", "yes"
    }:
        return []
    return [
        _manager_item(entry, MANAGER_ID_BASE + int(entry["resourceId"]))
        for entry in manager_catalogue()
    ]


def _club_extras() -> list[dict]:
    """Consumables, kits, badges, stadiums and balls."""
    items: list[dict] = []
    next_id = CLUB_ITEM_ID_BASE

    for card in _consumable_catalogue():
        if card["itemType"] in UNSEEDED_CONSUMABLE_TYPES:
            continue
        for _ in range(CONSUMABLE_COPIES.get(card["itemType"], 1)):
            items.append(_consumable_item(card, next_id))
            next_id += 1

    probe = _club_item_probe()
    if probe:
        # FIFA14_CLUB_ITEM_PROBE: seed the roster instead of the club items, so
        # the club tab is a numbered sweep of asset ids and what renders can be
        # read straight off the screen. Kept out of packs deliberately -- see
        # tools/club_item_probe.py for why the club screen is the safe one.
        # Each roster entry is an (assetId, resourceId) pair. The pair matters:
        # a club item's ART RESOLVES FROM ITS resourceId, not its asset -- asset
        # 241 rendered FC Barcelona at resource 6000241 and drew NOT FOUND at
        # 6900241, same asset, one resource apart. So a sweep varies the
        # resource and holds the asset at a known-good one.
        #
        # The item id counts up rather than deriving from the asset, because a
        # resource sweep holds the asset constant and derived ids would collide.
        for kind, pairs in sorted(probe.items()):
            for index, (asset, resource) in enumerate(pairs):
                items.append(
                    _club_item(kind, asset, resource,
                               PROBE_ID_BASE + CLUB_ITEM_BLOCKS[kind] + index,
                               quality=3)
                )
        return items

    # The club starts with the five items it presents with, and nothing else.
    #
    # Everything else -- 1570 kits, badges, stadiums and balls -- comes out of
    # packs. Seeding the lot would hand the player every item on day one and
    # leave the club tab 78 pages deep in kits with nothing to collect.
    #
    # These five are the same cards `PRESENTATION_ACTIVES` dresses the club in,
    # so what the club owns and what it wears are one set rather than two that
    # can disagree.
    for kind, asset, resource in CLUB_STARTER_ITEMS:
        items.append(
            _club_item(kind, asset, resource, club_item_id(kind, asset),
                       rating=84, rare=1)
        )

    # Off unless asked for. See `seed_managers`: this is how to find out
    # whether a manager card draws at all, not how managers should arrive.
    items.extend(seed_managers())
    items.extend(seed_staff())

    return items


PROBE_RESOURCE_BASE = 6_900_000
PROBE_FILE = Path(__file__).resolve().parent.parent / "work" / "club-item-probe.json"


def _club_item_probe() -> dict[str, list[int]]:
    """The asset-id sweep to seed instead of the club items, if one is armed."""
    if not os.environ.get("FIFA14_CLUB_ITEM_PROBE"):
        return {}
    try:
        roster = json.loads(PROBE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    def pairs(kind: str, entries: list) -> list[tuple[int, int]]:
        """Roster entries, as (asset, resource). A bare int keeps the old
        meaning -- sweep the asset, derive the resource from the family."""
        out = []
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                out.append((int(entry[0]), int(entry[1])))
            else:
                out.append((int(entry), _probe_resource(kind, int(entry))))
        return out

    return {
        kind: pairs(kind, entries)
        for kind, entries in roster.items()
        if kind in {"kit", "stadium", "ball", "badge"} and entries
    }


# -- what a pack hands out that is not a player -----------------------------
#
# The same two sources the club is seeded from, drawn instead of granted. Each
# template carries the tier it belongs to and whether it counts as a rare, so
# a bronze pack fills its nine non-player slots with bronze consumables and a
# rare slot can actually land on one.

# How often each family comes up, per family and not per card. Drawing evenly
# across the 124 templates would be wrong: there are 42 training cards and 13
# contracts, so an even draw hands out three times more training than contract
# and a club still runs out of contracts. Retail is the other way round.
# Position modifiers are drawable now. They were absent from this table
# entirely, so `position` had no weight and never came out of a pack -- the only
# ones a club ever had were the ones it was seeded with.
CONSUMABLE_DRAW_WEIGHT = {
    "contract": 40, "fitness": 22, "healing": 12, "playStyle": 10,
    "training": 8, "position": 6,
    # Manager league modifiers, in from 25 August once all twenty-seven were
    # seen rendering in the club. Weighted with the thinnest families: there
    # are twenty-seven of them and a club needs one, not a shelf of them.
    #
    # They are rated 95, so `_extra_tier` makes them gold and a Bronze Pack
    # cannot hand one out -- the same thing that keeps chemistry styles out of
    # the low packs.
    "managerLeagueModifier": 6,
}

# Subtype 232 is not drawn, and it is not a position modifier either.
#
# The console named it: "DEBLOQUER / Capacite +8 moral" and "Grosse affluence
# morale 6". They are stadium unlockables, and the client refuses to keep one:
#
#   "Certains des elements ne peuvent etre conserves dans votre club car ils
#    sont deverrouillables. Utilisez-les en faisant s'afficher le menu
#    d'actions et en selectionnant 'Utiliser element'."
#
# That popup is retail behaviour, not a fault -- these do come out of retail
# packs. But nothing here serves the route behind "Utiliser element", so a
# drawn one is dead weight that raises a dialog on every pack. Drawn again
# once that route is known.
#
# This server's catalogue calls the family `position` and the PC revival's
# calls it "Internal Position". Both are wrong.
# Held out of the pack draw.
#
# `position` is gone from this set: position modifiers render correctly on the
# console and were being kept out of packs for no stated reason, so the only
# ones a club ever had were its seeded ones.
#
# `squadTraining` takes its place, and for a reason: art id 43 draws NOT FOUND,
# so packing one hands the player a card that cannot be looked at. It stays out
# until the id is known -- the same treatment manager and staff got.
#
# `managerLeagueModifier` spent one relaunch here and is out again, 25 August.
# Art 32 came out of Kyro's database extract rather than being invented, but
# uniform in an extract is not the same as drawn on a console, and every family
# that skipped that check handed the player a NOT FOUND card. So it was seeded
# into the club first and looked at: all twenty-seven render.
UNDRAWN_CONSUMABLE_TYPES = {"squadTraining", "formationManager"}

# And out of the club seed, so the tab it was polluting is clean.
UNSEEDED_CONSUMABLE_TYPES = {"squadTraining", "formationManager"}

# Kits, badges, stadiums and balls carry a rating now (CLUB_ITEM_QUALITY) and
# so respect a tier.
#
# Managers and staff join them, 25 August, after being seeded into a club and
# rendering: 166 managers and 150 across the four staff families. They were
# absent because nothing served them at all, not because anything was decided
# about them.
#
# Their tier comes from their own rating rather than from a quality slot --
# the game's database gives every one a rating, 54 to 88 for a manager and 55
# to 80 for staff, so a Bronze Pack cannot hand out an 88-rated manager.
#
# The weights are deliberately below the four cosmetic families. A manager or a
# coach is a card you want a few of, where kits and badges are a collection --
# there are 1327 kits and 166 managers, and drawing them evenly would bury the
# kits nobody has yet under staff nobody needs a fifth of.
CLUB_ITEM_DRAW_WEIGHT = {
    "kit": 30, "badge": 30, "stadium": 12, "ball": 18,
    "manager": 10,
    "headCoach": 5, "gkCoach": 5, "fitnessCoach": 5, "physio": 5,
}

# How the non-player slots divide.
#
# Three quarters consumables, one quarter club items.
#
# This was 1.0 -- consumables only -- because kits, badges, balls and stadiums
# drew blank card backs on the pack screen, two of them in a single Premium
# Gold Pack. Their resource ids are still invented here (6000000 and up; no
# table in `cards_ng_db` or `fifa_ng_db` names them), but the blank card was
# never about the id: they were being sent as a bare envelope. With the
# per-kind members each family needs -- and badges under the retail `custom`
# family rather than `badge` -- they render, confirmed on the console
# 17 August 2026.
#
# So they are back in the draw. A quarter rather than a half, because a club
# needs contracts more than it needs a fourth ball.
PACK_CONSUMABLE_SHARE = 0.75


# What quality a club item is.
#
# Everything here was rating 0, which is no tier at all, and the card screen
# draws a card with no tier as the lowest one -- so every kit and badge in the
# club showed as bronze non-rare however good it looked.
#
# The ratings below are a **choice**, not a finding: nothing in the game's data
# says which of four kits is the gold one, because this server invented their
# identities in the first place. Spreading them across the three tiers is the
# useful choice rather than the true one -- it gives bronze and silver packs
# something of their own to hand out, and it stops the club looking like a
# jumble sale.
# What a club item quick-sells for, from the game's own tables.
#
#     bronze  13 rare / 3 normal
#     silver  37 rare / 14 normal
#     gold    60 rare / 31 normal
#
# Gold normal (31) is worth less than silver rare (37). That is the real table,
# not a slip -- FIFA 14 pays for the rare flag more than for the tier.
#
# Balls carry one documented value, bronze normal at 15, and nothing for the
# other grades; `fifa14_clubitems.json` uses it where it applies.
CLUB_DISCARD = {
    ("bronze", 0): 3, ("bronze", 1): 13,
    ("silver", 0): 14, ("silver", 1): 37,
    ("gold", 0): 31, ("gold", 1): 60,
}


CLUBITEM_FILE = Path(__file__).resolve().parent / "fifa14_clubitems.json"
CLUBITEM_BLANK_FILE = (
    Path(__file__).resolve().parent / "fifa14_clubitems_blank.json"
)


def blank_club_items() -> set[int]:
    """Resource ids known to draw the green NOT FOUND placeholder.

    Read here rather than only at build time, so an id added after a bad card
    comes out of a pack takes effect on the next server start without anyone
    regenerating the catalogue. A missing or unreadable file means no
    exclusions, which is the same catalogue as before.
    """
    try:
        listed = json.loads(CLUBITEM_BLANK_FILE.read_text())["blank"]
    except (OSError, ValueError, KeyError):
        return set()
    return {int(x) for x in listed if isinstance(x, (int, str)) and str(x).isdigit()}


def _clubitem_catalogue() -> list[dict]:
    """Every club item the console has actually been seen to render.

    1570 of them across four families, built by `tools/build_clubitems.py`.

    The count is not as measured as it reads. The probe sessions of 18-19
    August established where each family **stops** -- it visited 24 kit ids and
    every one was above 6300860, hunting the boundary -- and the interior of
    each range was then assumed contiguous. Kit 6300772 came out of a pack on
    24 August drawing NOT FOUND, which is the first evidence that the interiors
    have holes. Balls were never probed at all.

    So the ids that fail are removed one at a time as they are found, from
    `fifa14_clubitems_blank.json`. Until somebody sweeps the interiors properly
    this list is how the catalogue gets honest.
    """
    try:
        items = json.loads(CLUBITEM_FILE.read_text())["clubitems"]
    except (OSError, ValueError, KeyError):
        return []
    blank = blank_club_items()
    if not blank:
        return items
    return [item for item in items if item.get("resourceId") not in blank]


def club_discard_value(rating: int, rare: int) -> int:
    return CLUB_DISCARD.get((_extra_tier(rating), 1 if rare else 0), 3)


CLUB_ITEM_QUALITY = ((65, 0), (72, 0), (80, 0), (84, 1))

_PACK_EXTRAS: dict[tuple[str, str], list[dict]] | None = None


def _extra_tier(rating: int) -> str:
    for tier, (low, high) in TIER_RATINGS.items():
        if low <= int(rating) <= high:
            return tier
    return "gold"


# A badge's crest resolves from its resourceId, and the resource is an index
# into the game's own badge table: 6000000 is FC Barcelona, 6000001 Real
# Madrid, 6000002 Bayern, 6000003 Manchester City, 6000600 Drogheda United.
#
# This used to map club ids onto that index -- "a badge asset id is a club id"
# -- which was a coincidence. The four original badges carried resources
# 6000000-6000003, indices 0 to 3, and Barcelona's clubId happens to be 241,
# the asset sitting beside index 0. Two unrelated numbers lining up once.
#
# It made every badge's *name* wrong: the player activated what this server
# called Blackburn Rovers, clubId 3, and the console drew Manchester City,
# badge index 3. He said so at the time and was talked out of it.
#
# The catalogue in `fifa14_clubitems.json` carries the real indices, so club
# ids are out of the badge path entirely.
BADGE_RESOURCE_BASE = 6_000_000


def pack_extras() -> dict[tuple[str, str], list[dict]]:
    """Every non-player card a pack can draw, grouped by family, built once.

    Templates, not items: they carry no id, because the id belongs to the
    draw. `_draw_extra` copies one and stamps it. Grouping by family is what
    lets the draw weight contracts against training rather than against the
    number of training variants that happen to exist.
    """
    global _PACK_EXTRAS
    if _PACK_EXTRAS is not None:
        return _PACK_EXTRAS

    families: dict[tuple[str, str], list[dict]] = {}
    for card in _consumable_catalogue():
        if card["itemType"] in UNDRAWN_CONSUMABLE_TYPES:
            continue
        template = _consumable_item(card, 0, item_state="new")
        # No identifier survives into a template: `id` and `itemId` both
        # belong to the draw, and a stale `itemId` of 0 shipped on every pack
        # consumable until this popped it too.
        template.pop("id")
        template.pop("itemId", None)
        template["_tier"] = _extra_tier(card["rating"])
        template["_rare"] = bool(card["rare"])
        families.setdefault(("consumable", card["itemType"]), []).append(template)

    # Every club item the console rendered, 1570 of them. The catalogue carries
    # the tier and the quick-sell value, so nothing is decided here.
    #
    # A club item respects its tier: a silver kit came out of a Bronze Pack
    # when they were rated 0, which is no tier at all.
    for card in _clubitem_catalogue():
        template = _club_item(
            card["itemType"], card["assetId"], card["resourceId"], 0,
            item_state="new", rating=card["rating"], rare=card["rare"],
            discard=card["discardValue"],
        )
        template.pop("id")
        template.pop("itemId", None)
        template["_tier"] = card["tier"]
        template["_rare"] = bool(card["rare"])
        families.setdefault(("club", card["itemType"]), []).append(template)

    # Managers and the four staff families, from the game's own database.
    # Tiered by their own rating, so a Bronze Pack cannot hand out an 88.
    for entry in manager_catalogue():
        template = _manager_item(entry, 0, item_state="new")
        template.pop("id")
        template["_tier"] = _extra_tier(template["rating"])
        template["_rare"] = bool(template.get("rareflag"))
        families.setdefault(("club", "manager"), []).append(template)

    for entry in staff_catalogue():
        template = _staff_item(entry, 0, item_state="new")
        template.pop("id")
        template["_tier"] = _extra_tier(template["rating"])
        template["_rare"] = bool(template.get("rareflag"))
        families.setdefault(("club", template["itemType"]), []).append(template)

    _PACK_EXTRAS = families
    return families


def _draw_extra(
    tier: str, rare: bool, item_id: int, rng: random.Random,
    taken: set | None = None,
) -> dict | None:
    """One non-player card for a pack slot.

    The family comes first and by weight, and only families that hold a card
    of this tier are eligible -- chemistry styles exist in gold only, and
    choosing that family in a silver pack and then relaxing the tier is how a
    99-rated style landed in a Silver Pack. Inside the family only the rare
    flag relaxes.

    An empty consumable catalogue -- the file is optional -- returns None, and
    the caller draws a player instead, which is what packs used to be.

    `taken` is what this pack has already handed out, and it excludes club
    items only. A Premium Gold Pack came out with the Barcelona third kit
    twice, side by side, because there are four kits and the draw picked from
    them with replacement -- and unlike a second contract, which is a second
    contract, a second identical kit is nothing at all. Consumables are left
    alone deliberately: they stack, and a pack that refused to hand out two
    contracts would be worse than one that did.
    """
    pool = pack_extras()
    if not pool:
        return None

    kind = "consumable" if rng.random() < PACK_CONSUMABLE_SHARE else "club"
    weights = (
        CONSUMABLE_DRAW_WEIGHT if kind == "consumable" else CLUB_ITEM_DRAW_WEIGHT
    )

    def in_tier(templates: list[dict]) -> list[dict]:
        return [t for t in templates if t["_tier"] in ("", tier)]

    def available(key: tuple, templates: list[dict]) -> list[dict]:
        """This family's cards of this tier that the pack has not used.

        Club families are thin once a tier is applied -- one gold stadium, one
        gold ball, two gold kits -- so "do not repeat inside a family" is not
        enough on its own: drawing the stadium family twice in a gold pack has
        nowhere to go. A family with nothing left is dropped from the choice
        instead, and another family carries the slot.
        """
        got = in_tier(templates)
        if key[0] != "club" or taken is None:
            return got
        return [t for t in got if ("club", t.get("assetId")) not in taken]

    # A rare slot picks a family that can actually pay it.
    #
    # `exact` at the bottom of this function relaxes the rare flag when the
    # chosen family holds no card of that rarity, and two gold families hold
    # none at all: there is no rare ball in the catalogue and no rare
    # chemistry style. So a rare slot that picked either handed back a
    # non-rare. That is how the Rare Gold Pack -- twelve slots, every one of
    # them rare -- still came out 7% non-rare on 27 August, against a
    # description that promises every single item is rare.
    #
    # Choosing the family first and relaxing second is the bug. Families that
    # cannot pay are dropped from the choice instead, and the relax below goes
    # back to being the last resort it was written as.
    #
    # Only a rare slot filters. A non-rare slot that draws from an all-rare
    # family -- managers, positions -- keeps handing back the rare card it
    # always did: that is a gift rather than a defect, and a Gold Pack with
    # one rare slot would otherwise almost never show a manager again.
    rare_first: list[tuple] = []
    if rare:
        def pays(key: tuple, templates: list[dict]) -> bool:
            return any(t["_rare"] for t in available(key, templates))

        rare_first = [
            (key, weights.get(key[1], 1))
            for key, templates in pool.items()
            if key[0] == kind and pays(key, templates)
        ]
        if not rare_first:
            # Nothing of the chosen kind holds a rare at this tier: the other
            # kind carries the slot rather than the slot going non-rare.
            rare_first = [
                (key, 1) for key, templates in pool.items()
                if pays(key, templates)
            ]

    choices = rare_first or [
        (key, weights.get(key[1], 1))
        for key, templates in pool.items()
        if key[0] == kind and available(key, templates)
    ]
    if not choices:
        # Nothing of this kind reaches this tier: the other kind carries the
        # slot rather than the slot handing out the wrong tier.
        choices = [
            (key, 1) for key, templates in pool.items()
            if available(key, templates)
        ]
    if not choices:
        # Every family is spent. Fall back to repeating rather than handing
        # back a short pack: a pack that is the size it advertises matters
        # more than a pack with no repeat in it.
        choices = [
            (key, 1) for key, templates in pool.items() if in_tier(templates)
        ]
    if not choices:
        return None

    family = rng.choices(
        [key for key, _ in choices], weights=[w for _, w in choices]
    )[0]
    candidates = available(family, pool[family]) or in_tier(pool[family])
    exact = [t for t in candidates if t["_rare"] == rare]
    chosen = rng.choice(exact or candidates)
    if family[0] == "club" and taken is not None:
        taken.add(("club", chosen.get("assetId")))
    item = {k: v for k, v in chosen.items() if not k.startswith("_")}
    item["id"] = item_id
    if "cardassetid" in item:
        item["itemId"] = item_id
    return item


# -- the game modes --------------------------------------------------------
#
# Seasons, tournaments and Team of the Week. Each of these screens refuses an
# empty list the way fcc_login2 refuses an empty squad, so "none available" is
# not a neutral answer -- it is the error the screen reports.

# Division 1 first, and the order is not cosmetic.
#
# `divisionId` is an index, which is what the freeze on 13 August established:
# 10 hung the screen and 0 held it. What 0 *shows*, though, is a badge reading
# **DIV 1** -- so the index is not into this list, it is into the client's own
# table of divisions, and that table starts at Division 1. The screen proved
# it twice over: beside the DIV 1 badge it read "Matchs restants : 10" and
# "12 PTS TITRE", neither of which is in the record served for it.
#
# So `divisionId` is `division - 1`, and this list is ordered to agree with
# it: position n holds division n + 1, and the same number is right whichever
# table the client is indexing.
#
# Ten fixtures in every division, and that is not a choice either. The client
# keeps its own count: after one match walked out of in Division 10 -- a
# division this table used to give four fixtures -- the result screen read
# "MATCHS RESTANTS 9". It counts ten whatever is served, so serving fewer
# leaves a fixture list that runs out four matches into a ten-match season.
# (division, name, matches, points to promote, championship coins)
#
# Division 10 is measured: the retail screen reads 12 points to win the title,
# **9** to clinch promotion, 1,900 coins for the title, 1,500 for promotion and
# 300 for avoiding relegation. This server was sending 2 to promote, which is
# three points fewer than a single win is worth.
#
# Ten matches at three points a win is a ceiling of 30, so 9 is three wins and
# 12 is four. The other nine divisions are NOT measured -- no screenshot of
# them exists here -- so they carry the same thresholds rather than invented
# ones that would look authoritative. The coin awards keep their ladder, which
# is what makes a higher division worth reaching.
SEASON_DIVISIONS = [
    (1, "Division 1", 10, 9, 5000),
    (2, "Division 2", 10, 9, 3000),
    (3, "Division 3", 10, 9, 2200),
    (4, "Division 4", 10, 9, 1700),
    (5, "Division 5", 10, 9, 1300),
    (6, "Division 6", 10, 9, 1000),
    (7, "Division 7", 10, 9, 800),
    (8, "Division 8", 10, 9, 650),
    (9, "Division 9", 10, 9, 500),
    (10, "Division 10", 10, 9, 1900),
]

# The cups. Every member name below is one CardsDLL carries: they were read
# out of the module's own JSON name table, which is sorted and contiguous
# between `trophiesOffline` and `kitsHome` in `.rdata`. `treeType`, `numTeams`,
# `numRounds`, `matchlength`, `rounds`, `rewardMultiplier`, `awardSet`,
# `awardType`, `halid`, `elgReq`, `eligibilityOperation`, `aigroup`,
# `unlockreq`, `triesMax`, `triesPeriod`, `triesRemaining`, `nextReset`,
# `starttime`, `timeUntilStart`, `timeUntilEnd`, `visStart`, `visEnd`,
# `trophyResourceId` and `trophyUserCount` are all present, as is `knockout`
# for treeType.
#
# `rounds` is an ARRAY of round records. The previous attempt here served it as
# a count -- `"rounds": 5` -- alongside invented `name`/`level`/`entryFee`/
# `active`/`won` members, and opening Competition Joueur Solo froze the title
# outright. That freeze is why this list was emptied and left empty. A number
# where the parser walks an array is the whole explanation, and none of the
# invented members appear in the name table.

# The fourteen single-player cups FIFA 14 ships, from
# `MarvelcoCode/Impulsum14`'s `Tournaments.cs`.
#
# (id, name, trophy design, difficulty, final award, retail unlock requirement)
#
# This replaces three cups invented here -- Founders Cup, Continental Cup,
# Legacy Champions Cup, at trophies 1100-1102 -- which drew a bare `*` for a
# name because nothing on the disc knows those ids.
#
# Two things make this data rather than a guess, and they meet in the middle:
#
#   * `docs/TOURNAMENTS.md` already recorded that retail shows **fourteen**
#     single-player cups with names baked into the disc. Impulsum lists exactly
#     fourteen.
#   * The trophy designs run 1100, 1104, 1108 ... 1152, step four. `cards0.big`
#     on this disc carries `trophy_<id>_<tier>` for **1100..1169** -- measured
#     here, months before this file was read -- and 1152 is inside it. A step
#     of four is one design per cup across the four tiers the archive ships:
#     bronze, silver, gold and dark.
#
# The unlock column is retail's and is **not applied**: `lock` stays UNLOCKED
# and `unlockreq` stays 0 for every cup, because nothing here counts trophies
# won yet and locking ten of fourteen behind a counter that does not exist
# would take away cups that are playable today. It is recorded so the
# progression can be switched on the day the counter is.
TOURNAMENTS = [
    (1,  "Starter Cup",                 1100, 1,   300,  0),
    (2,  "Midlands Invitational",       1104, 2,   500,  0),
    (3,  "Gold Challenge",              1108, 3,   700,  0),
    (4,  "Quad-League Classic",         1112, 2,   600,  1),
    (5,  "Managers Cup",                1116, 3,   700,  1),
    (6,  "Bronze International Shield", 1120, 4,  1000,  2),
    (7,  "Trio Showcase",               1124, 2,   300,  2),
    (8,  "Unified Cup",                 1128, 3,  1000,  2),
    (9,  "Pyramid Invitational",        1132, 4,  1000,  3),
    (10, "Silver Links Cup",            1136, 3,   700,  4),
    (11, "Federation Cup",              1140, 4,   200,  4),
    (12, "Champions Trophy",            1144, 5,  2500,  4),
    (13, "Premier Clash",               1148, 3,  1200,  5),
    (14, "Ultimate Cup",                1152, 3,  3000, 10),
]

# Every fixed cup is a 16-team, four-round bracket.
TOURNAMENT_TEAMS = 16
TOURNAMENT_NUM_ROUNDS = 4
TOURNAMENT_MATCH_LENGTH = 6


def tournament_row(identifier: int) -> tuple:
    return next(
        (row for row in TOURNAMENTS if row[0] == identifier), TOURNAMENTS[0]
    )


def tournament_rounds(identifier: int) -> list[tuple[int, int, int, int]]:
    """(round id, difficulty, reward multiplier, coins) for one cup.

    The final round pays the cup's award, which is the figure the tile shows as
    its Tournament Bonus and the one Impulsum carries.

    The earlier rounds pay a rising share of it -- an eighth, a quarter, three
    eighths -- so the run pays on the way to the final rather than only at it.
    That is this server's own behaviour and it is kept deliberately: Impulsum
    pays nothing before the final, and taking its cup data is not a reason to
    take an economy the player already has working. The amounts follow each
    cup's award, so they moved with the new table without being chosen twice.
    """
    _, _, _, difficulty, award, _ = tournament_row(identifier)
    return [
        (
            index,
            difficulty,
            1,
            award
            if index == TOURNAMENT_NUM_ROUNDS
            else max(1, award * index // (2 * TOURNAMENT_NUM_ROUNDS)),
        )
        for index in range(1, TOURNAMENT_NUM_ROUNDS + 1)
    ]


# The AI opponents each cup draws from -- fifteen per cup, the club itself
# taking the sixteenth slot. Real EA club ids, one pool per tournament, from
# the same source. Cup 14's pool is the Premier League one this server already
# used for everything.
TOURNAMENT_TEAM_POOLS = {
    1:  [422, 1572, 357, 294, 922, 1914, 1744, 873, 697, 689, 1939, 110, 696, 12, 15005],
    2:  [1926, 162, 2023, 433, 94, 298, 165, 1910, 256, 1871, 570, 97, 3, 8, 1880],
    3:  [1887, 1902, 62, 1807, 1915, 417, 614, 191, 91, 665, 200, 459, 95, 14, 1884],
    4:  [57, 378, 605, 2007, 1888, 472, 190, 674, 1913, 10020, 226, 1882, 29, 1795, 71],
    5:  [896, 1906, 1903, 31, 673, 1881, 1808, 58, 1844, 379, 242, 1838, 1901, 4, 1876],
    6:  [203, 1861, 1799, 1793, 78, 171, 453, 1893, 1837, 1909, 10029, 1878, 232, 1908, 15029],
    7:  [229, 1961, 217, 744, 1598, 1952, 231, 246, 1892, 468, 244, 189, 1032, 192, 166],
    8:  [72, 479, 1917, 206, 1970, 1879, 169, 1039, 819, 1853, 1843, 25, 1891, 483, 54],
    9:  [2, 38, 1013, 1719, 109, 450, 485, 1792, 70, 106, 59, 1824, 1809, 28, 1877],
    10: [19, 247, 1028, 15, 1806, 66, 569, 452, 23, 312, 32, 36, 383, 1842, 245],
    11: [17, 39, 1819, 393, 65, 462, 517, 315, 480, 567, 1053, 219, 1860, 50, 9],
    12: [1960, 568, 280, 74, 1629, 598, 237, 1043, 69, 175, 449, 448, 573, 1, 481],
    13: [13, 7, 144, 1896, 34, 1048, 55, 1035, 1041, 461, 22, 18, 457, 44, 48],
    14: [52, 234, 325, 236, 47, 46, 10, 5, 11, 21, 73, 240, 45, 243, 241],
}


# The trophy ids are the game's own. `cards0.big` carries 70 of them under
# data/ui/external/ion_fut/artassets/fcctournamenttrophies/, named
# trophy_<id>_<tier> for ids 1100..1169, each in bronze, silver, gold and dark,
# beside a notfound.big. Serving `trophyResourceId` 0 is what drew that
# notfound placeholder: the cups listed correctly and had no art.
#
# The art is local. The client still fetches
# /fut/items/images/trophies/xbl2/item.big -- the string
# `items/images/trophies/xbl2/` is built into CardsDLL -- but that pack is not
# where these come from.

TROPHY_FIRST = 1100
TROPHY_LAST = 1169
TROPHY_TIER = "gold"


def empty_big_archive() -> bytes:
    """A structurally valid, empty EA BIGF container.

    Everything under /fut/items/images/ that ends in `.big` is a BIG archive,
    and the server answered the whole /fut/items/ prefix with
    `{"itemData":[]}` -- sixteen bytes of JSON where a binary container was
    asked for. The console asks for two of these on the cup screen: the pack
    itself, and a degenerate `/trophies/xbl2/.big` with no basename when the
    trophy definition carries none.

    Magic, declared size, zero directory entries, header size. Empty on
    purpose: the EA trophy CDN is gone and no art is being invented here. It
    makes the response parseable rather than wrong.

    **The size is little-endian and the other two are big-endian.** That is not
    a choice; it is what a real BIGF from this game carries. Read out of the
    Title Update's own helperFunctions package:

        BIGF   54032 (little-endian)   3 entries (big)   header 56 (big)

    All four fields went out big-endian here, so a sixteen-byte archive
    declared its own size as 0x10000000 -- 268 megabytes. What a client does
    with that is its own business, and both screens that freeze after being
    served everything -- resuming a cup, and entering seasons -- ask for this
    archive on the way in. That is a correlation and not a demonstration; the
    field is wrong either way.
    """
    return (
        b"BIGF"
        + (16).to_bytes(4, "little")
        + (0).to_bytes(4, "big")
        + (16).to_bytes(4, "big")
    )


def trophy_item_response(resource_id: int, tier: str = TROPHY_TIER) -> bytes:
    """The definition behind a cup's `trophyResourceId`.

    The journal settles what this endpoint is for. With `trophyResourceId` 0
    the console asked for `/fut/items/xbl2/0.json` once per cup; with a real id
    it asks for that id. So the field drives the request and this document is
    the trophy's definition.

    **The shape changed on 25 August.** It used to be `{"itemData":[{...}]}`
    with `assetName`, `name` and `image` all holding the same basename -- three
    candidates offered at once because the module's name table carries all
    three and nothing said which one the client reads. No cup ever showed a
    trophy under it.

    Impulsum's build has a working trophy screen and answers flat, with two
    members this server was not sending at all:

        {"tournamentId":N,"tournamentType":0,
         "assetName":"trophy_1100_gold","silName":"trophy_1100_dark",
         "locString":[{"lang":"ENG_US","label":"Starter Cup"}]}

    `silName` and `locString` are both in CardsDLL's table, as are `lang` and
    `label`. `silName` is the silhouette drawn for a cup not yet won, which is
    why a screen with only one name had nothing to show in that state.

    And `locString` is the second route a cup's name travels. `TOURNY_LOC_%d`
    is the first and this server already answers it; this one arrives attached
    to the trophy the tile is drawing.

    `tier` still names the won trophy's art. The basename is built from the
    **design** id, which is the resource the cup carries -- 1100, 1104 ... 1152
    -- and `cards0.big` ships `trophy_<design>_<tier>` for 1100..1169 in
    bronze, silver, gold and dark.
    """
    resource_id = int(resource_id)
    row = next((r for r in TOURNAMENTS if r[2] == resource_id), None)
    return json.dumps(
        {
            # The cup this trophy belongs to. A probe id belongs to none, and
            # says so with 0 rather than borrowing the first cup's identity.
            "tournamentId": row[0] if row else 0,
            "tournamentType": 0,
            "assetName": f"trophy_{resource_id}_{tier}",
            # The unwon silhouette.
            "silName": f"trophy_{resource_id}_dark",
            "locString": (
                [{"lang": "ENG_US", "label": row[1]}] if row else []
            ),
        },
        separators=(",", ":"),
    ).encode()


# What each cup is called, from TOURNAMENTS above.
#
# The tiles drew a bare `*` because the client builds its own localisation key
# -- `TOURNY_LOC_%d`, in the module at 0x01DDC4 -- and nothing answered it.
# `name` is not a member of the tournament document at all: it is absent from
# CardsDLL's JSON table, and the PC revival keeps its cup names in source and
# never sends them either.
#
# Two routes carry the name now and neither invents one. This locstring table
# answers `TOURNY_LOC_%d`, and the trophy document carries a `locString` array
# -- see `trophy_item_response`, which is the route Impulsum's build uses and
# the one that has a working screen behind it.
TOURNAMENT_NAMES = {row[0]: row[1] for row in TOURNAMENTS}


# A tournament id sweep, the way the club items were mapped.
#
# The tiles draw a bare `*` for a name and the screen is slow to open, and both
# point the same way. `TOURNY_LOC_%d` is built by the client from the
# tournament id, and `trophyResourceId` addresses trophy art the client fetches
# by id -- `FUTTrophyImages`, `items/images/trophies/xbl2/` in the module. Ids
# 1, 2, 3 and trophies 1100-1102 were invented here.
#
# The club-item work settled the same class of question by serving a numbered
# sweep and reading the screen: a real id names itself, an invented one draws
# NOT FOUND. A cup whose id the game knows should show its own name with no
# locstring from us at all, exactly as a badge resource drew its own crest.
#
# FIFA14_TOURNAMENT_PROBE=1..N serves N cups with ids 1..N so the range can be
# read off the tile list in one pass.
def tournament_probe() -> tuple[int, int, int]:
    """(count, first id, step) for the id sweep, or (0, 0, 0) when off.

        FIFA14_TOURNAMENT_PROBE=12            ids 1..12
        FIFA14_TOURNAMENT_PROBE=60:1:25       60 cups from id 1, step 25

    Ids 1-12 all drew `*`, which does not mean the id space is the wrong idea
    -- it means that range is. Retail shows fourteen single-player cups with
    names baked into the disc, and nothing says their ids start at 1. The kit
    range was found the same way: a coarse step over a wide span first, then a
    fine pass on the edge.
    """
    raw = os.environ.get("FIFA14_TOURNAMENT_PROBE", "0").strip()
    if not raw or raw == "0":
        return (0, 0, 0)
    parts = raw.split(":")
    try:
        count = max(0, min(60, int(parts[0])))
        first = int(parts[1]) if len(parts) > 1 else 1
        step = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        return (0, 0, 0)
    return (count, first, step)


# Kyro's build uses trophy ids 7100001 and up; those are his own, for a PC
# client, and swapping to them here would have replaced a measured range with a
# guess. A test caught exactly that once. Impulsum's designs are 1100..1152,
# which is *inside* the measured range rather than beside it -- that is the
# difference, and it is why these were taken and those were not.

# The AI opponents a cup is drawn from. Real EA club ids -- 1 Arsenal,
# 2 Aston Villa, 5 Chelsea, 7 Everton, 9 Liverpool, 10 Manchester City,
# 11 Manchester United, 13 Newcastle, 18 Tottenham, 21 West Ham -- and the
# draw is one short of numTeams because the club itself takes the last slot.
TOURNAMENT_TEAM_POOL = [
    241, 243, 21, 69, 10, 11, 9, 22, 5, 73, 1, 13, 18, 2, 7, 8,
]


def _season_matches(division: int, count: int) -> list[dict]:
    """A division's fixture list.

    The same shape as a cup's `rounds`: the schedule is an array of records,
    not a count. `roundId` is zero-based here -- the wire `round` in
    season/user is one-based and the client decrements it.
    """
    base = max(1, min(5, 1 + (10 - int(division)) // 2))
    return [
        {
            "teamId": TOURNAMENT_TEAM_POOL[index % len(TOURNAMENT_TEAM_POOL)],
            "difficulty": min(5, base + (1 if index >= 7 else 0)),
            "rewardMult": 1,
            "roundId": index,
            "coins": 250 + (10 - int(division)) * 25 + index * 10,
        }
        for index in range(max(0, int(count)))
    ]


def _season_prize(level: str, threshold: int, coins: int = 0) -> dict:
    awards = (
        []
        if int(coins) <= 0
        else [
            {
                "type": "coin",
                "value": int(coins),
                "assetId": 0,
                "count": 1,
                "halId": 0,
                "teamId": 0,
            }
        ]
    )
    return {
        "prizeLevel": level,
        "thresholdPoint": int(threshold),
        "awardMappings": [{"awards": awards}],
    }


def _season_record(index: int, division: int, matches: int, promote: int, coins: int) -> dict:
    # Twelve to win the title, and it is measured rather than derived: the
    # retail Division 10 screen reads 12. It used to be `promote + 3`, which
    # made the title threshold move with the promotion one and is why nothing
    # noticed the promotion value was wrong.
    title = 12
    holding = 300 if int(division) == 10 else max(300, int(coins) // 5)
    promotion = 1500 if int(division) == 10 else max(500, int(coins) - 400)
    return {
        "id": int(index),
        "type": "OFFLINE",
        "divisionId": int(division),
        "numMatches": int(matches),
        "matchLengthMin": 6,
        "matches": _season_matches(division, matches),
        "prizeSet": [
            _season_prize("RELEGATION", 0, 0),
            _season_prize("MAINTENANCE", 0, holding),
            _season_prize("PROMOTION", int(promote), promotion),
            _season_prize("CHAMPIONSHIP", title, int(coins)),
        ],
        "elgOperation": "AND",
        "elgReq": [],
        # A real trophy, not -1 and not 0.
        #
        # Both sentinels were wrong in the same way. Zero sent the screen to
        # fetch /fut/items/xbl2/0.json once per division; -1 was taken from a
        # PC build as its "no trophy" value and did exactly the same thing here,
        # ten lookups of /fut/items/xbl2/-1.json. `docs/SEASONS.md` records that
        # as a confirmed mistake in the attempt that froze.
        #
        # `cards0.big` ships seventy trophies at 1100..1169. A division is given
        # one of them so the id resolves to something the game actually has,
        # which removes a known-bad variable before the reduction ladder starts
        # -- there is no point bisecting a record that still carries a value
        # already known to send the client hunting.
        "trophyResourceId": TROPHY_FIRST + (int(division) - 1) % (
            TROPHY_LAST - TROPHY_FIRST + 1
        ),
        "trophyUseCount": 0,
        "visStartDays": 3650,
        "visEndDays": 3650,
        "startDateTime": 0,
        "endDateTime": FOREVER,
        "untilStartSeconds": 0,
        "untilEndSeconds": 315360000,
    }


def season_wire_mode() -> str:
    """`empty` unless FIFA14_SEASON_MODE asks for the native shape.

    Three shapes have been served here and all three failed on the console:

    * invented members (`division`, `matchesPlayed`, `coinsPerWin`, ...), none
      of which is in CardsDLL's name table -- the screen read its constructor
      defaults;
    * those names corrected but the thresholds and rewards flat at the top
      level, reusing the *cup's* time names -- "Les saisons ne sont pas
      disponibles pour le moment";
    * the full native shape below, with `matches` and `prizeSet` as arrays --
      the FUT loader froze on entering the mode.

    Empty is the only answer known not to break anything, and it is what the
    PC revival carried here for the same reason. The native shape stays
    reachable for a deliberate test rather than being the default that greets
    anyone who opens the mode.

    Attempt four, on 13 August, changed the asset chain underneath and not the
    document: `/fut/items/xbl2/-1.json` answers with a real definition now
    instead of an empty one, so the console built
    `trophies/xbl2/item.big` with a basename instead of `.big` with none, and
    that archive no longer declares itself 268 MB long. Both were real defects
    on this exact path. **The screen still froze**, in the same place, after
    both documents were served -- so the asset chain was not it, and what is
    wrong is in the record.

    Which is what SEASONS.md prescribes reducing, one variable at a time. The
    ladder is reachable by name:

        empty     no seasons at all -- the only answer known to break nothing
        minimal   one division, no `matches`, no `prizeSet`
        prizes    minimal plus `prizeSet`
        matches   minimal plus `matches`
        native    every division, both arrays -- the shape that freezes

    Each rung costs a server restart and one entry into the mode. Serving the
    whole record at once produces a freeze and no information.
    """
    # `native` is the default now.
    #
    # It was `empty` -- `{"seasons": []}` -- because the full record froze the
    # console on 13 August and empty was the only answer that had never broken
    # anything. The ladder settled it on 20 August: `minimal` opened the mode,
    # `prizes` filled the rewards, `matches` filled the fixture list, and
    # `native` served both across all ten divisions without freezing.
    #
    # Leaving the default at `empty` after that meant every launch without the
    # flag answered "les saisons ne sont pas disponibles" -- which is what the
    # player hit the moment a command went out without it.
    # `kyro-data` is the default, measured 21 August 2026: it is the only shape
    # seen to resume a season on this console. See docs/SEASONS.md.
    #
    # `current` was the default and could not resume. It served one row sliced
    # off a table ordered Division 1 first, so that row carried `id` 10, and
    # `season/user` answered `seasonId` 10 -- an index off the end of a list of
    # one. The record was never selected, so nothing it carried could be read,
    # and every attempt to fix the reset by adding members to it was measured
    # against a document the client was not looking at.
    # `native` is the default, because it is the one that opens.
    #
    # Two measurements, both on this console, and they disagree:
    #
    #   22 Aug  `kyro-data` opened Single Player Seasons and **resumed** a
    #           season after a match -- the fixture list drawn, the score on
    #           it, NEXT on the second fixture. That is what `729c68f` fixed
    #           and why this default was `kyro-data`.
    #   25 Aug  `kyro-data` froze the screen on entry. No spinner, music still
    #           playing, and the console eventually stopped answering XBDM.
    #           `native` opened it immediately on the same console, the same
    #           disc, the same club and the same Xbox LIVE session.
    #
    # What found it was not this repository. nygmasx's server was tried from
    # the same console and seasons opened there -- and his `deploy/run.sh`
    # pins `FIFA14_SEASON_MODE=native`, so he had never been running the
    # default this file sets. Same code, same console, same club: only the
    # mode differed. Three branches here had been eliminated on the grounds
    # that they served identical documents, which was true and useless,
    # because they shared this default.
    #
    # So the trade is: `native` opens and probably does not resume --
    # `seasonId` 10 selects the row whose `divisionId` is 1 while the user
    # document says 9, which is the disagreement `729c68f` was about --
    # and `kyro-data` resumes but froze. A screen that opens beats a screen
    # that resumes, until the freeze is understood.
    #
    # Not understood yet. The four differences between the two are the
    # reversed list order, `seasonId`, `divisionId` and the `data` blob, and
    # nothing says which one costs the freeze.
    raw = os.environ.get("FIFA14_SEASON_MODE", "native").strip().lower()
    if raw in {"current", "one"}:
        return "current"
    if raw == "default":
        return "kyro-data"
    if raw in {"native", "full", "on"}:
        return "native"
    if raw in {"minimal", "min", "bare"}:
        return "minimal"
    if raw in {"prizes", "prizeset"}:
        return "prizes"
    if raw == "matches":
        return "matches"
    if raw in {"kyro", "reference"}:
        return "kyro"
    if raw in {"kyro-div9", "kyrosafe", "kyro-safe"}:
        return "kyro-div9"
    if raw in {"kyro-data", "kyrodata"}:
        return "kyro-data"
    if raw in {"kyro-full", "kyrofull"}:
        return "kyro-full"
    if raw in {"nouser", "listonly"}:
        return "nouser"
    if raw in {"user-id", "userid"}:
        return "user-id"
    if raw in {"user-division", "userdivision"}:
        return "user-division"
    if raw in {"user-round", "userround"}:
        return "user-round"
    return "empty"


def seasons_response() -> bytes:
    """The divisions.

    A season carries its fixture list in `matches` and its rewards in
    `prizeSet`, both arrays of records -- the same fault as a cup's `rounds`
    served as a count, one level deeper. Every member of the native record was
    checked against the module's name table, and the screen still froze, so
    something below is still wrong and the freeze is not worth serving by
    default. See `season_wire_mode`.
    """
    mode = season_wire_mode()
    if mode == "empty":
        return json.dumps({"seasons": []}, separators=(",", ":")).encode()
    if mode.startswith("user-") or mode == "nouser":
        # Every rung above `nouser` serves the same minimal list; what varies
        # is how much of `season/user` goes out beside it.
        mode = "minimal"

    records = [
        _season_record(index, division, matches, promote, coins)
        for index, (division, _name, matches, promote, coins) in enumerate(
            SEASON_DIVISIONS, start=1
        )
    ]
    if mode in ("kyro", "kyro-div9", "kyro-data", "kyro-full"):
        # `offline_seasons_list` from KyroGeorge2/FIFA-14-Local-FUT.
        #
        # He serves all ten divisions, and his table is ordered Division 10
        # first, so the record's `id` -- a 1-based position in the list -- comes
        # out as 1 for Division 10 and 10 for Division 1. `divisionId` stays the
        # division's own number. `offline_season_user` then says seasonId 1,
        # divisionId 10, and all three agree.
        #
        # `current` reaches the same screen a different way: one row, sliced off
        # the end of a table ordered the other way, which leaves that row
        # carrying `id` 10 on a list of one. Same tile, but nothing agrees.
        records = [
            _season_record(index, division, matches, promote, coins)
            for index, (division, _name, matches, promote, coins) in enumerate(
                reversed(SEASON_DIVISIONS), start=1
            )
        ]
        return json.dumps({"seasons": records}, separators=(",", ":")).encode()

    if mode == "current":
        # The club's own division, with both arrays.
        #
        # `native` serves all ten and the screen opens on the first tile --
        # Division 1, the top of the table, which is where a club ends up and
        # not where it starts. FUT begins every club in Division 10.
        #
        # Both arrays go out: the ladder proved each separately and then
        # together, so there is nothing left to withhold. This is `native` with
        # one row rather than a rung of the reduction.
        records = records[-1:]
        return json.dumps({"seasons": records}, separators=(",", ":")).encode()

    if mode != "native":
        # One rung of the ladder: a single division, and only the array the
        # rung is named for. Reducing is the only way through a freeze, which
        # gives no error to read.
        # Division 10, not Division 1.
        #
        # `records[:1]` took the top of the table, which is where a club ends
        # up rather than where it starts: FUT begins every club in Division 10
        # and promotes upward. The rung was therefore testing the one division
        # a new club can never be in, and `season/user` was answering with a
        # different division again -- the list said 1, the user record said 9,
        # and nothing reconciled them.
        #
        # `SEASON_DIVISIONS` is ordered 1..10, so the club's own division is the
        # last entry.
        records = records[-1:]
        keep_matches = mode == "matches"
        keep_prizes = mode == "prizes"
        for record in records:
            if not keep_matches:
                record["matches"] = []
                record["numMatches"] = 0
            if not keep_prizes:
                record["prizeSet"] = []
    return json.dumps({"seasons": records}, separators=(",", ":")).encode()


def _season_matches_played(entry: dict) -> int:
    """How many matches of this season have actually been played.

    Counted here rather than read out of the client's blob. The client
    rewrites its season save on entering the mode and sends `round` 1 with it
    -- that is what came up on 14 August at 01:53, a season with a match
    already won saved back at round 1 -- so a round derived from the blob says
    "ten matches remaining" for ever.

    The count exists already: `SeasonProgress.settle` records every result as
    it is settled. The PC revival keeps a `matches_played` column and sends
    `round = matches_played + 1` for the same reason
    (KyroGeorge2/FIFA-14-Local-FUT, `offline_season_user`).

    The blob stays as the fallback, for a season restored from a save written
    before any record was kept.
    """
    played = sum(int(entry.get(key) or 0) for key in ("won", "draw", "lost"))
    if played:
        return played
    return max(0, int(entry.get("round") or 1) - 1)


def served_season_index(division: int) -> int:
    """`seasonId`: where a division sits in the list actually served.

    NOT `divisionId`. The bisection recorded in `season_user_response` settles
    that one separately: `divisionId` 0 renders a badge reading DIV 1, so it
    indexes the *client's* table of ten divisions and Division 10 is 9. That is
    unaffected by how many records this server sends.

    `seasonId` is the one that depends on the list, and it is the one a reduced
    rung breaks: every rung served a single record while this still answered
    with the division's position in the full ten-row table, so `minimal` served
    one season and pointed at season 10.

    The recorded bisection in `season_user_response` narrowed the freeze to
    `divisionId` and noted what its value also is: "on a list of ten, 10 is one
    past the last index; on a list of one it is far past". That reading could
    not be tested while every reduced rung served Division 1 -- the club's
    division and the served record were different rows, so index and division
    number could never be told apart.

    Serving Division 10 as the single record makes them separable. A list of
    one holding Division 10 needs index 1; a full list of ten needs index 10,
    which is also the division number, which is why the two readings agreed on
    `native` and disagreed nowhere it was looked at.

    So the index is computed from what is served rather than assumed to be the
    division.
    """
    # The record's OWN id, not its position in the page.
    #
    # A record built for Division 10 carries `id` 10 whether it is served
    # alone or beside nine others -- `_season_record` takes the index from
    # `SEASON_DIVISIONS`, and slicing the list to one row does not renumber it.
    # So `seasonId` has to name the record, and the client agrees: it saves to
    # `/season/10/division/10/user`, season ten, on a list holding exactly one
    # season.
    #
    # This was changed to "index into the page" earlier on the reasoning that a
    # one-row list could not hold a season 10. It can: the row *is* season 10.
    # The client accepted the reduced rungs, played a match, saved round 2 --
    # and then reset to round 1, because the document beside its progress named
    # a season that was not there.
    for position, row in enumerate(SEASON_DIVISIONS, start=1):
        if row[0] == int(division):
            return position
    return 1


def season_user_response(division: int = 10, played: int = 0) -> bytes:
    """Where the club currently stands, and nothing more.

    The parser handles `seasonId`, `divisionId` and `round`. `seasonId` is
    decremented by the client, so 1 selects the first record in the list above.
    `round` is decremented too: wire 1 is the first scheduled match, and wire 0
    would become the client's 0xFFFF invalid sentinel.

    Everything else that used to go out here -- points, won, draw, the record,
    the end result -- was guessed, and guessed members are what this document
    is now deliberately without.
    """
    mode = season_wire_mode()
    if mode in ("kyro", "kyro-div9", "kyro-data", "kyro-full"):
        # `offline_season_user` from KyroGeorge2/FIFA-14-Local-FUT, matched
        # member for member. That build resumes a season; this one does not,
        # and the differences are all here:
        #
        #   seasonId 1     not 10. Kyro's comment: "season/user is 1-based;
        #                  native client stores id-1", so 1 selects the first
        #                  record of a one-row list. `b0e4ca7` moved this to 10
        #                  on the opposite reading and the reset survived it.
        #   divisionId 10  not 9. The division's own number, not an index.
        #   nothing else   no `data`, no `dataVersion`, no seasonGames* and no
        #                  seasonCoins. Kyro calls those "unknown guessed
        #                  progression members" and omits them deliberately.
        #                  This server added `data` to fix this very reset, on
        #                  the reasoning that a season without it "looked like
        #                  one that had never started" -- and the reset stayed,
        #                  so that reasoning did not hold. A working build not
        #                  sending them is the evidence against it.
        #
        # This is evidence, not proof: Kyro's is the PC frontend and this is
        # Xbox 360. Same caveat the cup-resume note carries.
        saved = SEASON_PROGRESS.current()
        played = 0
        if saved is not None:
            played = _season_matches_played(
                SEASON_PROGRESS.entries.get(saved) or {}
            )
        # `kyro-div9` is the same document with the one value this console has
        # already been seen to hang on held back.
        #
        # 13 August, bisecting this route: `{"seasonId": 1, "divisionId": 10}`
        # opened the screen and then hung, while 0 and 9 both held. That is the
        # exact pair `kyro` sends. Kyro runs it, but his is the PC frontend and
        # the freeze was measured here -- and it was measured beside a one-row
        # list, so it may have been the disagreement rather than the value.
        #
        # `kyro-div9` separates those: Kyro's list, Kyro's seasonId, and a
        # divisionId already known to hold. If the season survives a match on
        # it, the agreement was the fault and the value never mattered. Only
        # then is `kyro` worth the risk of the freeze.
        # 10 is Kyro's value and the one that agrees with the list: `seasonId`
        # 1 selects row 1, and row 1 *is* Division 10, so a user document saying
        # 9 names a different division from the record it points at. `kyro-div9`
        # held that back to dodge the 13 August freeze and broke the agreement
        # it was meant to test -- and that freeze does not apply here anyway: it
        # was measured when the one-row list held Division **1** (`records[:1]`
        # took the top of the table then), so the list and the user document
        # named different divisions. That mismatch is the candidate for the
        # hang, not the number.
        #
        # `_season_division_id` honours FIFA14_SEASON_DIVISION_ID, so the value
        # can be varied against the console without another relaunch-per-idea.
        document = {
            "seasonId": 1,
            "divisionId": _season_division_id(9 if mode == "kyro-div9" else 10),
            "round": max(0, int(played)) + 1,
        }
        # `kyro-data` and `kyro-full` put back what Kyro omits, on top of the
        # agreement that fixed the reset.
        #
        # 21 August: on `kyro-div9` the season survived a match -- the fixture
        # list opened instead of the start prompt -- but the played match drew
        # Score `-` and the list opened on it rather than on the next fixture.
        # The season resumes; the progress inside it does not.
        #
        # The scores are in the client's own blob, which Kyro never returns and
        # this server used to. That was judged useless before, but it was judged
        # against a document whose `seasonId` pointed off the end of the list --
        # the record was never being selected at all, so nothing it carried
        # could have shown. Worth one more measurement now that it is.
        #
        # `data` before `dataVersion`, always: the version branch decodes using
        # registers the data branch fills. See `cup_resume_mode`.
        entry = SEASON_PROGRESS.entries.get(saved) or {} if saved else {}
        if mode in ("kyro-data", "kyro-full") and entry.get("data"):
            document["data"] = entry["data"]
            document["dataVersion"] = entry.get("dataVersion", 1)
        if mode == "kyro-full":
            # And the header numbers, the last of the five.
            document.update(_season_record_members(entry))
        return json.dumps(document, separators=(",", ":")).encode()
    if mode.startswith("user-"):
        # The halving `nouser` earned: the same minimal list opened the mode
        # when this document was `{}` and froze it when it carried all three
        # members. So the fault is here, in `seasonId`, `divisionId` or
        # `round`, and they go back one at a time.
        document = {"seasonId": 1}
        if mode in {"user-division", "user-round"}:
            # `divisionId` is the member that freezes: `{"seasonId": 1}` alone
            # holds the screen, adding this one hangs it. And the value it was
            # sent, 10, is exactly the `divisionId` the single served record
            # carries -- so "it must name a division in the list" is not the
            # rule, since it did.
            #
            # What 10 also is, on a list of ten, is one past the last index;
            # on a list of one it is far past. FIFA14_SEASON_DIVISION overrides
            # it so the reading can be tested rather than argued.
            # `divisionId` indexes the client's own table, zero-based, so
            # Division 10 is 9. Ten hung the screen because it is one past the
            # last index of a table of ten -- see the note in the full record
            # below. FIFA14_SEASON_DIVISION still overrides for testing.
            override = os.environ.get("FIFA14_SEASON_DIVISION")
            if override:
                try:
                    document["divisionId"] = int(override)
                except ValueError:
                    document["divisionId"] = _season_division_id(int(division) - 1)
            else:
                document["divisionId"] = _season_division_id(int(division) - 1)
            document["seasonId"] = served_season_index(division)
        if mode == "user-round":
            document["round"] = 1
        return json.dumps(document, separators=(",", ":")).encode()
    if mode in {"empty", "nouser"}:
        # What this route carried before any of the guessed shapes: an empty
        # object leaves the native response at its constructor defaults rather
        # than naming a season the list no longer offers.
        #
        # `nouser` serves the minimal list beside this empty answer, which is
        # the halving `minimal` earned: that rung froze with both arrays empty
        # and a single division, so neither array is the fault and the question
        # becomes which of the two documents carries it.
        return b"{}"
    # Where the club actually stands, if it stands anywhere. The client saves
    # its own progress to `season/<id>/division/<div>/user` after every match
    # -- it went up at round 2 the moment the first one was walked out of --
    # and until that was handled this document reported round 1 for ever, so
    # re-entering the mode offered ten matches remaining out of ten however
    # many had been played.
    saved = SEASON_PROGRESS.current()
    entry: dict = {}
    if saved is not None:
        _season, saved_division = saved
        entry = SEASON_PROGRESS.entries.get(saved) or {}
        division = saved_division
        played = _season_matches_played(entry)
    # The position in the list actually served, not in the full ten-row table.
    # A reduced rung serves one record, and this answered with the division's
    # place in all ten -- so `minimal` offered one season and pointed at season
    # ten, which is exactly the out-of-range shape the divisionId bisection
    # found hanging the screen one member over.
    index = served_season_index(division)
    return json.dumps(
        {
            "seasonId": index,
            # The division's number minus one -- an index, counted from zero,
            # into the client's own table of divisions.
            #
            # Bisected down `season/user`'s three members on 13 August:
            # `{}` opened the screen and held, `{"seasonId": 1}` held,
            # `{"seasonId": 1, "divisionId": 10}` opened and then hung. Ten is
            # one past the last index of a table of ten, which is what hung
            # it; it was not failing to name a division the served list holds,
            # since the record beside it carried `divisionId` 10 itself.
            #
            # Zero held the screen, and that was mistaken for the answer.
            # What zero *renders* is a badge reading **DIV 1**, over "Matchs
            # restants : 10" and "12 PTS TITRE" -- none of which is in the
            # record served for it. So the index is into the client's table,
            # not into ours, and that table starts at Division 1.
            #
            # `SEASON_DIVISIONS` is ordered to agree, so this is both the
            # division's number minus one and its position in the list served
            # above.
            # From the division, not from `index`. Those were the same while
            # every rung served the full ten-row list; now that a reduced rung
            # serves Division 10 alone, `index` is 1 and would send 0 -- a
            # different division's id on a record that is not that division.
            #
            # What the member *means* is still open: the shield reads DIV 1 for
            # both 0 and 9, so it does not follow the member, and
            # `_season_division_id` carries the experiment that would settle
            # it. What is settled is the range -- 10 hangs the screen, 0 and 9
            # both hold -- and division minus one stays inside it.
            # The division's own number, not that number minus one.
            #
            # `seasonId` selects a row and this has to name that row's
            # division, or the client reads the pair as a season that is not
            # there and offers to start a fresh one. Measured 25 August: on
            # `native`, `seasonId` 10 selects row 10 whose `divisionId` is 10,
            # while this sent 9 -- so a season played to round 2, with its
            # record and its blob both present and correct, still came back as
            # "are you sure you want to start this Single Player Season?".
            #
            # Minus one was the "index into the client's own table of ten"
            # reading. That was settled against the console on 21 August and
            # the answer was no: the shield reads DIV 1 for 0, for 9 and for
            # 10, so it never followed this member at all, and what the member
            # does is identify the record. `kyro-data` has sent the division's
            # own number since, and it resumed a season on 22 August.
            "divisionId": _season_division_id(int(division)),
            "round": max(0, int(played)) + 1,
            # The season's own blob, and the version that decodes it.
            #
            # The reader for this document is CardsDLLzf+0x1adf28 and it knows
            # five members: data, dataVersion, divisionId, round, seasonId.
            # Three were being sent. The two left out are the ones that carry
            # the state, and nothing else asks for them -- the client never
            # GETs `season/<id>/division/<div>/user`, it only PUTs there. So a
            # season with a match behind it looked, from here, exactly like one
            # that had never started, and the screen offered to begin it again.
            #
            # `data` before `dataVersion`, always: the version branch decodes
            # using registers the data branch fills, and getting that backwards
            # is what asks for a 3 GB buffer. See `cup_resume_mode`.
            **(
                {"data": entry["data"], "dataVersion": entry.get("dataVersion", 1)}
                if entry.get("data")
                else {}
            ),
            **_season_record_members(entry),
        },
        separators=(",", ":"),
    ).encode()


def _season_division_id(computed: int) -> int:
    """`FIFA14_SEASON_DIVISION_ID` overrides what goes out, for one question.

    Settled, 21 August 2026: **the shield is not ours.** It read DIV 1 for
    `divisionId` 0, for 9, and now for 10 -- three values, one of them the
    division the club is actually in, all rendering the same badge. Of the
    three readings below it is the third that stands; the first two are out.

    The member still matters, just not for the badge. It has to name the same
    division as the list row `seasonId` selects: with the row at Division 10,
    sending 9 left the season unresumable and sending 10 resumed it. So it
    identifies the record, and the shield is drawn from somewhere else.

    The readings, kept for the record:

        served 5  ->  shield 6   the client adds one
        served 5  ->  shield 5   the client subtracts from ten
        served 5  ->  shield 1   the shield is not ours at all

    Nothing else in the document moves, so whatever the shield says is about
    this member and nothing else.
    """
    raw = os.environ.get("FIFA14_SEASON_DIVISION_ID")
    if raw is None or not raw.strip():
        return int(computed)
    try:
        return int(raw.strip())
    except ValueError:
        return int(computed)


def _season_record_members(entry: dict) -> dict:
    """What the season header shows, for a season that has one.

    `CRÉDITS 0`, `POINTS FIFA 0` and `BILAN 0-0-0` sat over a club holding
    nine hundred million and a season won 3-0 on 13 August. That header is
    the *season's*, not the club's, and nothing was ever sent for it -- the
    client keeps its own progress in an opaque gzipped blob and asks for the
    numbers separately.

    All four names are CardsDLL's own, read out of the sorted JSON name table
    where they sit contiguously between `seasonId` and `seasonCoins`.

    Nothing goes out for a club that has never played a season. The
    three-member document is the one bisected into working, and a fresh club
    keeps exactly it.
    """
    if not entry:
        return {}
    played = (
        int(entry.get("won") or 0)
        + int(entry.get("draw") or 0)
        + int(entry.get("lost") or 0)
    )
    if not played and not int(entry.get("coins") or 0):
        return {}
    return {
        "seasonGamesWon": int(entry.get("won") or 0),
        "seasonGamesDraw": int(entry.get("draw") or 0),
        "seasonGamesLost": int(entry.get("lost") or 0),
        "seasonCoins": int(entry.get("coins") or 0),
    }


FOREVER = 2147483647


# The entry requirements retail puts on eleven of the fourteen cups, as the
# player recorded them. Kept as plain terms, not as wire values, because the
# wire values are not known yet -- see `eligibility_probe`.
#
# (attribute, operation, value, scope)
#
#   attribute  leagues | chemistry | quality | nationalities | clubs
#              | stars | oneLeague | oneNation
#   operation  min | max | exact
#   scope      "xi" for the starting eleven, "xi+subs" for it and the bench
#
# Stars are held as halves -- the Pyramid Invitational's cap is 2.50 -- so the
# value stays an integer here and nothing has to decide how the wire spells a
# fraction before it is known.
TOURNAMENT_REQUIREMENTS: dict[int, list[tuple[str, str, int, str]]] = {
    4:  [("leagues", "max", 4, "xi")],
    5:  [("chemistry", "min", 80, "xi")],
    6:  [("quality", "max", 1, "xi+subs"),        # 1 = bronze
         ("nationalities", "min", 3, "xi")],
    7:  [("clubs", "max", 3, "xi")],
    8:  [("stars", "max", 8, "xi"),               # 8 halves = 4.0 stars
         ("oneLeague", "min", 11, "xi")],
    9:  [("leagues", "exact", 11, "xi"),
         ("stars", "max", 5, "xi")],              # 5 halves = 2.5 stars
    10: [("chemistry", "min", 90, "xi"),
         ("quality", "max", 2, "xi+subs")],       # 2 = silver
    11: [("oneNation", "exact", 18, "xi+subs")],
    12: [("chemistry", "min", 95, "xi"),
         ("leagues", "min", 3, "xi+subs")],
    13: [("stars", "exact", 10, "xi")],           # 10 halves = 5.0 stars
    14: [("chemistry", "exact", 100, "xi")],
}


# How many numbered strings the probe publishes. `eligibilityKey` 4 drew index
# 16, so the index runs ahead of the key by some factor; 256 covers a key space
# of 64 at that rate and costs a few kilobytes in a document already served.
ELG_PROBE_STRINGS = 256
ELG_PROBE_SCOPES = 16


def eligibility_probe() -> tuple[int, int]:
    """`FIFA14_ELIGIBILITY_PROBE=first[:step]`: read the requirement key space.

    CardsDLL carries the whole vocabulary -- `elgReq`, `elgOperation`,
    `eligibilityKey`, `eligibilityValue`, `eligibilitySlot`,
    `eligibilityOperation` -- and one template beside them:

        ELIGIBILITY_STRING%d

    That is the same shape as `TOURNY_LOC_%d`, which is how a cup's name is
    drawn: the client builds a localisation key from a number and looks it up.
    So a requirement's text comes from a number this server sends, and nothing
    here knows which number means "Min. Team Chemistry" and which means "Max.
    number of clubs".

    `TOURNAMENT_REQUIREMENTS` records what the eleven cups actually require.
    It cannot be served until those numbers are known, and guessing them would
    put the wrong requirement on the wrong cup -- the same mistake the invented
    tournament ids made, which drew a bare `*` on every tile.

    So this reads them the way the club items and the trophy range were read:
    one key per cup, fourteen keys a launch, and the screen names each one.

        FIFA14_ELIGIBILITY_PROBE=1        cups 1-14 carry keys 1-14
        FIFA14_ELIGIBILITY_PROBE=1:10     keys 1, 11, 21 ... a coarse pass first

    A cup whose tile names a real requirement has a key the disc knows. A tile
    that draws nothing, or `*`, does not.
    """
    raw = os.environ.get("FIFA14_ELIGIBILITY_PROBE", "").strip()
    if not raw:
        return (0, 0)
    parts = raw.split(":")
    try:
        first = int(parts[0])
        step = max(1, int(parts[1])) if len(parts) > 1 else 1
    except ValueError:
        return (0, 0)
    return (first, step)


# What the probe puts at the tournament's own level, offset from what it puts
# inside `elgReq`, so the screen says which one it read.
ELG_TOP_LEVEL_OFFSET = 100


def _cup_requirement_key(identifier: int) -> int:
    first, step = eligibility_probe()
    return first + step * (max(1, int(identifier)) - 1) if first else 0


def _cup_requirements(identifier: int) -> list[dict]:
    """`elgReq` for one cup: empty, or the probe's single key.

    Empty is what has always gone out and it costs nothing -- a cup with no
    stated requirement is enterable, which is what every cup here is.

    **Measured 26 August**: with keys 1 to 14 on the fourteen cups, *every* tile
    drew `*LOC_TOURN_ELG_KEY_16` and `undefined`. The same index on all
    fourteen, so the index is not derived from this key -- 16 is a constant the
    client reached on its own, and the first reading (key 4 drawing 16) was a
    coincidence of the tile that happened to be selected.

    Two readings of that are worth separating, and the next probe separates
    them. Either the client is not parsing these entries at all, or it does not
    read the requirement from here: `eligibilityKey`, `eligibilityValue` and
    `eligibilitySlot` are single members in CardsDLL's table, and they may
    belong to the **tournament** rather than to a row of this array --
    `LOC_TOURN_ELG_DOMAIN_LIST_%d` beside them suggests `elgReq` is the list of
    domains a single requirement applies to, not a list of requirements.

    So the probe now writes the key in both places, offset by
    `ELG_TOP_LEVEL_OFFSET`, and the drawn index names the winner: an index that
    tracks the low number came from here, one that tracks the high number came
    from the tournament, and 16 on every tile again means neither is read.
    """
    key = _cup_requirement_key(identifier)
    if not key:
        return []
    return [
        {
            "eligibilityKey": key,
            # A value every squad passes, so the probe reads the *name* of the
            # requirement without also locking the cup behind it.
            "eligibilityValue": 0,
            "eligibilitySlot": 0,
            "eligibilityOperation": 0,
        }
    ]


def _cup_requirement_members(identifier: int) -> dict:
    """The same key at the tournament's own level, offset so it is telling."""
    key = _cup_requirement_key(identifier)
    if not key:
        return {}
    return {
        "eligibilityKey": key + ELG_TOP_LEVEL_OFFSET,
        "eligibilityValue": 0,
        "eligibilitySlot": 0,
    }


def cup_unlocks_enforced() -> bool:
    """`FIFA14_CUP_UNLOCKS=1`: gate the later cups on trophies won. Off by default.

    Retail locks eleven of the fourteen -- one trophy for the Quad-League
    Classic and the Managers Cup, two for the next three, and ten for the
    Ultimate Cup. Those numbers reached this file twice from sources that had
    not seen each other: Impulsum's table, and the player's own list of retail
    requirements. All fourteen agree.

    It is still off by default, because the counter behind it only started
    counting on 25 August. Every cup won before that was paid, and recorded
    nowhere -- there is no count to reconstruct from. Turning this on today
    would lock eleven cups that are playable now on the strength of a counter
    that reads zero, which is the opposite of what the data is for.

    Turn it on once the count means something, or seed the count first.
    """
    return os.environ.get("FIFA14_CUP_UNLOCKS", "").strip().lower() in {
        "1", "true", "yes"
    }


def _cup_lock(identifier: int) -> str:
    """Why a cup cannot be entered, or UNLOCKED.

    `JOINED`, `LOCKED`, `LOCKED_TROPHIES` and `TOO_MANY_TOURNAMENTS` are the
    other values in CardsDLL's table.
    """
    _, _, _, _, _, unlock = tournament_row(identifier)
    if cup_unlocks_enforced() and TOURNAMENT_PROGRESS.trophies < unlock:
        return "LOCKED_TROPHIES"
    if identifier in TOURNAMENT_PROGRESS.entries and os.environ.get(
        "FIFA14_CUP_JOINED", ""
    ).strip().lower() in {"1", "true", "yes"}:
        # See the note on FIFA14_CUP_JOINED in docs/TOURNAMENTS.md: this labels
        # a cup that was opened without offering the resume that freezes.
        return "JOINED"
    return "UNLOCKED"


def tournament_entry(identifier: int, trophy: int | None = None) -> dict:
    """One cup, in the shape the native parser reads.

    `trophy` overrides the trophy resource, which the id sweep uses to read the
    trophy id space and the tournament id space in a single pass.
    """
    _, _, default_trophy, _, award, unlock = tournament_row(identifier)
    teams = TOURNAMENT_TEAMS
    match_length = TOURNAMENT_MATCH_LENGTH
    if trophy is None:
        trophy = default_trophy
    rounds = tournament_rounds(identifier)
    return {
        "id": identifier,
        "type": "offline",
        "treeType": "knockout",
        "aigroup": 0,
        "eligibilityOperation": "AND",
        "elgReq": _cup_requirements(identifier),
        **_cup_requirement_members(identifier),
        "numTeams": teams,
        "numRounds": len(rounds),
        "matchlength": match_length,
        "rounds": [
            {
                "id": round_id,
                "difficulty": difficulty,
                "rewardMultiplier": multiplier,
                "coins": coins,
            }
            for round_id, difficulty, multiplier, coins in rounds
        ],
        "awardSet": {"awards": [{"awardType": 1, "value": award, "halid": 0}]},
        # `JOINED` is in CardsDLL beside `UNLOCKED`, `LOCKED`,
        # `LOCKED_TROPHIES` and `TOO_MANY_TOURNAMENTS`, and it is the state a
        # cup the club is already in would carry.
        #
        # `FIFA14_CUP_JOINED=1`, off by default. A cup opened and backed out of
        # before a ball is kicked saves at round one with `progressData`
        # `AAAAAA==` -- four zero bytes -- and `TournamentProgress.unplayed`
        # keeps it out of `/tournament/user/list` because handing that run back
        # **freezes the title**. It froze twice on it, the second time on a
        # reply byte for byte identical to what the client itself had PUT, so
        # the document was never the problem: the client cannot resume a run
        # with no first match in it.
        #
        # This marks the tile without offering the resume, which is the part
        # that freezes. The risk it carries is the other way: `lock` is the
        # field that says *why a cup cannot be entered* -- TOO_MANY_TOURNAMENTS
        # and LOCKED_TROPHIES are both refusals -- so JOINED may make the tile
        # unenterable, or route its button to the resume this guard exists to
        # avoid. One launch judges it, and a cup that says nothing is better
        # than a cup that cannot be played.
        "lock": _cup_lock(identifier),
        # The number the tile prints under "Unlock", and it is not the same
        # question as whether the cup is playable.
        #
        # Tying it to the gate made every tile read "Unlock: 0 Trophies",
        # which is what the console showed on 26 August. `lock` is what
        # decides entry; this is a label, and retail's number is the true one
        # whether or not this server is enforcing it.
        "unlockreq": unlock,
        # No entry limit: triesMax 0 is what an always-playable offline cup
        # carries, and a nonzero triesRemaining against triesMax 0 is what
        # makes the screen show "0 essais restants" and refuse entry.
        "triesMax": 0,
        "triesPeriod": 0,
        "triesRemaining": 0,
        "nextReset": 0,
        "starttime": 0,
        "endtime": FOREVER,
        "timeUntilStart": 0,
        "timeUntilEnd": 315360000,
        "visStart": 3650,
        "visEnd": 3650,
        "trophyResourceId": trophy,
        # `prize` is in the module's member table and this server has never
        # sent it. The tile shows a Tournament Bonus figure -- 500 Coins on the
        # retail Starter Cup -- and `awardSet` alone was carrying that.
        "prize": award,
        # Cups won, ever. Zero until one is, and it is what retail gates the
        # later cups on.
        "trophyUserCount": TOURNAMENT_PROGRESS.trophies,
    }


def tournaments_response() -> bytes:
    count, first, step = tournament_probe()
    if count:
        # Each cup carries its own id as its trophy, so one pass reads the
        # tournament id space and the trophy id space together. A tile that
        # names itself has an id the disc knows; a tile drawing `*` does not.
        ids = [first + step * n for n in range(count)]
        # A real trophy on every probe tile.
        #
        # The sweep used to set trophy = id, which for ids 1-12 is nowhere near
        # the 1100-1169 range `cards0.big` actually ships -- so those tiles had
        # no trophy art and the sweep was silently testing a dead trophy id at
        # the same time as the name. Cycling through the real range keeps the
        # trophy honest so a blank tile means the *name* is missing and nothing
        # else.
        span = TROPHY_LAST - TROPHY_FIRST + 1
        return json.dumps(
            {
                "tournament": [
                    tournament_entry(n, trophy=TROPHY_FIRST + (i % span))
                    for i, n in enumerate(ids)
                ]
            },
            separators=(",", ":"),
        ).encode()
    return json.dumps(
        {"tournament": [tournament_entry(row[0]) for row in TOURNAMENTS]},
        separators=(",", ":"),
    ).encode()


def tournament_teams_response(
    count: int = 15, group: int = 0, tournament: int | None = None
) -> bytes:
    """The draw. `teamId` is the only member this document carries.

    The query is `/teams?groupId=%d&count=%d` in the module's own template, so
    the group is part of the request even though every cup here declares
    `aigroup` 0. Rotating the pool by it keeps two groups from drawing the same
    side in the same order, without inventing a second pool.

    `tournament` picks that cup's own fifteen from `TOURNAMENT_TEAM_POOLS`.
    The route does not carry it -- the template is groupId and count and
    nothing else -- so the caller supplies the cup the club is actually in, and
    falls back to the flat pool when no cup is open. Impulsum has the same
    problem and solves it the same way, with an `ActiveTournamentId` it sets
    when a cup is joined.
    """
    pool = TOURNAMENT_TEAM_POOLS.get(int(tournament or 0)) or TOURNAMENT_TEAM_POOL
    count = max(0, min(int(count), len(pool)))
    size = len(pool)
    offset = (int(group) % size) if size else 0
    rotated = pool[offset:] + pool[:offset]
    return json.dumps({"teamId": rotated[:count]}, separators=(",", ":")).encode()


def cup_resume_mode() -> str:
    """How much of a saved cup run goes back out.

    Four documents were served to the console and all four froze it. None of
    them was wrong in the way they were guessed to be. Tracing the hung title
    (`tools/fifa14_where_is_it_stuck.py`) and then reading CardsDLL offline
    (`tools/ppc_xref.py` over `work/cardsdll-text.bin`) gave the actual reason,
    and it is an ordering bug **in the game**.

    The reader is a streaming dispatcher at CardsDLLzf+0x1be840 that matches
    members by numeric id against the table at 0x8921E498, and it knows exactly
    three of them:

        id 134  dataVersion
        id 429  round
        id 535  tournamentData

    No progress member at all -- `progressdata` (395) and `progressDataVersion`
    (396) are never compared, so a cup restores from its bracket and its round.

    The fatal part is which branch does the work. The `tournamentData` branch
    fills two registers, a buffer and its length. The `dataVersion` branch
    parses the number and, when it is 1, **decodes using those two registers**.
    And the client's own serialiser, at `.rdata` 0xb9ec, writes

        {"round":%d,"dataVersion":%d,"tournamentData":"...

    with `dataVersion` **before** `tournamentData`. So the decode runs on
    registers nothing has written yet. What it read on 14 August was
    0xbd2e2eb4, a heap pointer, taken as a length: CardsDLL then asked for a
    3.17 GB zeroed buffer and filled it a byte at a time, on a console with 512
    MB. That is the freeze -- not a parse failure, and not something any member
    name could fix. The game cannot read back what it writes.

    The reply therefore puts `tournamentData` **before** `dataVersion`. Member
    order is ours to choose; the client's own is what it cannot survive.

        full     the id, the round, the blob, then the version -- in that order
        off      `{"tournamentId": id}` -- never resume
        round    the id and the round, no blob
        noblob   every member, blob empty
    """
    raw = os.environ.get("FIFA14_CUP_RESUME", "").strip().lower()
    return raw if raw in {"off", "round", "noblob", "full"} else "full"


class TournamentProgress:
    """Where the club stands in each cup, kept across launches.

    The client serialises its own progress and `.rdata` carries the format
    string it builds the body from:

        {"round":%d,"dataVersion":%d,"tournamentData":"

    It sits among the cup constants -- `TOO_MANY_TOURNAMENTS`, `JOINED`,
    `LOCKED_TROPHIES` -- which is what identifies it as the tournament one.
    There is a near-identical string spelling the blob `data` instead, but it
    is followed immediately by `%d/division/%d` and belongs to seasons; reading
    that one as the cup's format was a misidentification. The shared tail is
    `","progressDataVersion":%d,"progressData":"`.

    `data` is still accepted on the way in, and the reply also spells the
    progress blob `progressdata`, which is how the name table carries it beside
    the camel-cased `progressDataVersion`. An unrecognised sibling at the top
    level is skipped, as everywhere else in this protocol.
    """

    def __init__(self) -> None:
        self.entries: dict[int, dict] = {}
        # Cups won, ever. `trophyUserCount` is the member the tile reads it
        # from, and it is what retail gates the later cups on -- one trophy for
        # the Quad-League Classic, ten for the Ultimate Cup.
        #
        # It counts from the day it was added. Runs won before that were never
        # recorded anywhere, so the count starts wherever the save says and not
        # from a reconstruction: `advance` is the only thing that raises it.
        self.trophies: int = 0

    def apply(self, identifier: int, document: dict) -> dict:
        identifier = int(identifier)
        if not isinstance(document, dict):
            document = {}
        current = self.entries.get(identifier, {})

        def pick(*names, default=0):
            for name in names:
                if name in document:
                    return document[name]
            return current.get(names[0], default)

        entry = {
            "round": int(pick("round", default=1) or 1),
            "dataVersion": int(pick("dataVersion", default=1) or 1),
            "tournamentData": pick("tournamentData", "data", default="") or "",
            "progressDataVersion": int(
                pick("progressDataVersion", default=1) or 1
            ),
            "progressData": pick("progressData", "progressdata", default="") or "",
        }
        self.entries[identifier] = entry
        return entry

    def response(self, identifier: int) -> bytes:
        """A saved run is handed back exactly as the client wrote it.

        A cup with no saved progress answers with its id and nothing else.
        That is what the client sends up first, and inventing a round or an
        empty data blob for a cup that was never entered would put the screen
        into a tournament that does not exist.

        A resumable run is answered with `tournamentId` and the five members
        the client itself serialised. The id is in the path already, and this
        file used to argue from that it had no business in the body -- which
        was a guess, and the wrong one.

        Two things had been added here at once: the leading `tournamentId`,
        and a second copy of the progress blob spelled `progressdata`. That
        second spelling is in the name table, so it is not an unrecognised
        sibling the parser skips -- it is the same known field arriving twice,
        decoded twice into one slot, which is a good enough reason on its own
        for a freeze. Both were removed together, the title still froze, and
        that was read as "the shape is not what kills it".

        The experiment could not support that. It was run against an
        *unplayed* run -- the case that has to be refused whatever the shape --
        and it changed two things at once, so `tournamentId` was never tested
        on its own.

        KyroGeorge2/FIFA-14-Local-FUT, the PC revival, keeps `tournamentId`
        here (`offline_tournament_user` in `server/beta_identity.py`) and its
        resumability rule is otherwise the same as `unplayed` below, down to
        the four zero bytes. Its README asks the tester to reopen a cup and
        find round 2 active, so on that build resume works with the id
        present.

        Different frontend -- that is the PC build, this is Xbox 360 -- so
        this is evidence rather than proof. It is still the only concrete
        difference between a reply that resumes and one that froze this title
        on 14 August at 01:53:30, on a run at round 2 with a match won.
        """
        identifier = int(identifier)
        entry = self.entries.get(identifier)
        mode = cup_resume_mode()
        if entry is None or self.unplayed(entry) or mode == "off":
            return json.dumps(
                {"tournamentId": identifier}, separators=(",", ":")
            ).encode()
        document = {"tournamentId": identifier, "round": entry["round"]}
        if mode != "round":
            # The blob first, the version last. `dataVersion` is the branch
            # that triggers the decode, and it decodes whatever the
            # `tournamentData` branch left behind -- so the order is the fix.
            blank = mode == "noblob"
            document["tournamentData"] = "" if blank else entry["tournamentData"]
            document["dataVersion"] = entry["dataVersion"]
        return json.dumps(document, separators=(",", ":")).encode()

    def advance(self, identifier: int, result: str) -> dict:
        """Move a cup on by its result, and say what it paid.

        A win takes the next round; a draw replays the same one; a loss or a
        walk-out ends the run and the cup starts again from round one. The
        final's prize is paid once, and the cup stays playable afterwards --
        this is offline, and a cup you can only ever win once is a cup that
        stops existing the moment you are good enough to win it.
        """
        identifier = int(identifier)
        rounds = tournament_rounds(identifier)
        entry = self.entries.get(identifier) or {}
        played = max(1, int(entry.get("round") or 1))
        final = len(rounds) or 1

        coins = 0
        if rounds:
            # The round table is (id, difficulty, multiplier, coins).
            coins = int(rounds[min(played, final) - 1][3])

        prize = 0
        if result == "WIN":
            if played >= final:
                prize = next(
                    (award
                     for cup, _name, _design, _diff, award, _unlock in TOURNAMENTS
                     if cup == identifier),
                    0,
                )
                nxt = 1                       # won it; the cup is playable again
                self.trophies += 1
            else:
                nxt = played + 1
        elif result == "DRAW":
            nxt = played                      # the round has not been settled
        elif result in ("LOSS", "QUIT", "DNF"):
            nxt = 1
        else:
            return {"tournamentId": identifier, "round": played, "roundCoins": 0,
                    "prize": 0, "settled": False}

        # A run that is over leaves no run behind.
        #
        # Winning the final, losing, or walking out all set the next round to
        # 1 -- and the entry was kept, so `tournament/user/list` went on naming
        # the cup as entered and the tile read EN COURS over a cup that had
        # been won. Reported from the console on 16 August: Cup 2 completed,
        # trophy paid, still showing in progress.
        #
        # Dropping the entry is what says "not entered": a cup with no saved
        # run answers `{"tournamentId": id}` and the screen offers it fresh.
        # It also retires the stale bracket blob, which is the thing a resume
        # would otherwise try to reload.
        if nxt == 1 and result in ("WIN", "LOSS", "QUIT", "DNF"):
            self.entries.pop(identifier, None)
        elif entry:
            entry = dict(entry)
            entry["round"] = nxt
            self.entries[identifier] = entry
        else:
            self.apply(identifier, {"round": nxt})
        return {
            "tournamentId": identifier,
            "previousRound": played,
            "round": nxt,
            "roundCoins": coins if result in ("WIN", "DRAW") else 0,
            "prize": prize,
            "settled": True,
        }

    def delete(self, identifier: int) -> bool:
        return self.entries.pop(int(identifier), None) is not None

    @staticmethod
    def unplayed(entry: dict) -> bool:
        """A cup that was opened and walked out of before a ball was kicked.

        The client saves its draw the moment the bracket is built, before the
        first match. That save carries the full sixteen-team blob and a
        `progressData` of `AAAAAA==` -- four zero bytes, the length header of
        an empty payload -- at round one.

        Handing that back is what freezes the title. It froze twice on it: once
        on a reply carrying two extra members, and again on a reply byte for
        byte identical to what the client itself had PUT, so the document was
        never the problem. What the client cannot do is resume a run that has
        no first match to resume from.

        Nothing is lost by calling it no run: no match has been played, and
        the draw is redrawn on the way in. A run with a round past the first,
        or with a progress blob that actually holds something, is a real run
        and is kept.
        """
        try:
            blob = base64.b64decode(entry.get("progressData") or "", validate=False)
        except Exception:
            blob = b""
        started = len(blob) > 4 or any(blob[:4])
        return int(entry.get("round") or 1) <= 1 and not started

    def active_ids(self) -> list[int]:
        """Only runs the client can actually resume -- see `unplayed`."""
        return sorted(
            identifier
            for identifier, entry in self.entries.items()
            if not self.unplayed(entry)
        )

    def state(self) -> dict:
        # The runs are keyed by cup id, so the count rides under a name no cup
        # id can collide with rather than in a second save member.
        state: dict = {str(key): value for key, value in self.entries.items()}
        if self.trophies:
            state["trophies"] = self.trophies
        return state

    def restore(self, saved: dict | None) -> None:
        for key, value in (saved or {}).items():
            if key == "trophies":
                try:
                    self.trophies = max(0, int(value))
                except (TypeError, ValueError):
                    self.trophies = 0
                continue
            if isinstance(value, dict):
                self.apply(int(key), value)


TOURNAMENT_PROGRESS = TenantView("tournaments")


class SeasonProgress:
    """A season under way, kept across launches.

    The route is one level deeper than the URL template table suggests. The
    table carries `ut/%s/season/%%s/user`, and `%%s` is not the season id: the
    format string beside the season serialiser is `%d/division/%d`, so what
    goes on the wire is

        PUT /ut/game/fifa14/season/1/division/10/user

    and that is exactly what the console sent on 13 August at 13:37:48, on
    starting a Saison Joueur Solo. It answered 404, which is a hang with
    nothing to read -- the same 404 the cups' `tournament/user/<id>` was
    getting before it was handled.

    The division in the path is the division's **number**, not the position
    `season/user` reports. The client reads `divisionId` out of the record it
    picked and puts that in the URL, which is how position 0 becomes
    `division/10`.

    The body is the cup's body with one word changed: `.rdata` carries
    `{"round":%d,"dataVersion":%d,"data":"` for seasons against
    `{"round":%d,"dataVersion":%d,"tournamentData":"` for cups, and they share
    the `","progressDataVersion":%d,"progressData":"` tail. So this is
    `TournamentProgress` keyed by a pair and spelling the blob `data`.
    """

    def __init__(self) -> None:
        self.entries: dict[tuple[int, int], dict] = {}

    @staticmethod
    def _key(season: int, division: int) -> tuple[int, int]:
        return (int(season), int(division))

    def apply(self, season: int, division: int, document: dict) -> dict:
        key = self._key(season, division)
        if not isinstance(document, dict):
            document = {}
        current = self.entries.get(key, {})

        def pick(*names, default=0):
            for name in names:
                if name in document:
                    return document[name]
            return current.get(names[0], default)

        entry = {
            "round": int(pick("round", default=1) or 1),
            "dataVersion": int(pick("dataVersion", default=1) or 1),
            "data": pick("data", "seasonData", default="") or "",
            "progressDataVersion": int(pick("progressDataVersion", default=1) or 1),
            "progressData": pick("progressData", "progressdata", default="") or "",
            # The record, which the client never sends and never gets back
            # from anywhere else. It is carried alongside the client's own
            # blob rather than inside it: the blob is opaque, gzipped and
            # written by the title, and the header wants numbers.
            #
            # Read from the document as well as from what is already held,
            # because `restore` comes through here too -- and reading it from
            # `current` alone silently dropped the whole record on every
            # restart, which looks exactly like never having kept it.
            "won": int(pick("won", default=0) or 0),
            "draw": int(pick("draw", default=0) or 0),
            "lost": int(pick("lost", default=0) or 0),
            "coins": int(pick("coins", default=0) or 0),
        }
        # Starting the season over. The client says so by saving round 1 on a
        # season that had got past it, which is what it did at 15:32 on 13
        # August after answering "Oui" to "Voulez-vous vraiment débuter cette
        # Saison Joueur Solo ?". Carrying the old record into the new season
        # would have the header counting wins from a season that no longer
        # exists.
        if entry["round"] <= 1 < int(current.get("round") or 1):
            entry.update({"won": 0, "draw": 0, "lost": 0, "coins": 0})
        # Re-inserted rather than assigned in place, so `current` can read the
        # most recently written season off the end. A club that is promoted
        # and then relegated writes division 9 and then division 10 again, and
        # a plain assignment would leave 10 sitting where it was first
        # inserted -- reporting the club in a division it has left.
        self.entries.pop(key, None)
        self.entries[key] = entry
        return entry

    def response(self, season: int, division: int) -> bytes:
        """Handed back exactly as the client wrote it, or as no season at all.

        `TournamentProgress` learned this the hard way: a run saved before the
        first match is one the client cannot resume, and answering it with the
        saved blob freezes the title. The season serialiser is the same
        serialiser, so the same rule applies here before it costs another
        evening -- an unplayed season is answered with nothing rather than
        with a bracket that has no first match behind it.
        """
        entry = self.entries.get(self._key(season, division))
        if entry is None or TournamentProgress.unplayed(
            {"round": entry["round"], "progressData": entry["progressData"]}
        ):
            return b"{}"
        # The season reader is CardsDLLzf+0x1adf28 and it is the cup reader's
        # twin. Same five-way dispatch on numeric ids against the table at
        # 0x8921E498, same trap:
        #
        #     133  data         fills the buffer and length registers
        #     134  dataVersion  when it is 1, decodes using those registers
        #     148  divisionId   an int
        #     429  round        an int, stored minus one
        #     445  seasonId     an int
        #
        # So `data` -- the name the client itself writes, not the `seasonData`
        # that sits with the other season members in the table and was guessed
        # here before the reader was read out. And `data` before `dataVersion`,
        # because the version branch decodes what the data branch left behind
        # and the client's own serialiser (0xa35c) writes them the other way
        # round.
        #
        # `seasonId` and `divisionId` are accepted too but not sent: the client
        # has both in the request path already, and nothing unverified goes out
        # on a route that costs a frozen console to test.
        return json.dumps(
            {
                "round": entry["round"],
                "data": entry["data"],
                "dataVersion": entry["dataVersion"],
            },
            separators=(",", ":"),
        ).encode()

    def settle(self, season: int, division: int, result: str,
               coins: int = 0) -> dict:
        """Add a played match to the record.

        Nothing else keeps it. The client sends its own progress up as an
        opaque gzipped blob and asks for the numbers back separately, which is
        why the seasons header read `CRÉDITS 0`, `POINTS FIFA 0` and
        `BILAN 0-0-0` over a club holding nine hundred million and a season
        that had just been won 3-0.
        """
        key = self._key(season, division)
        if key not in self.entries:
            self.apply(season, division, {})
        entry = self.entries[key]
        if result == "WIN":
            entry["won"] = int(entry.get("won") or 0) + 1
        elif result == "DRAW":
            entry["draw"] = int(entry.get("draw") or 0) + 1
        elif result in ("LOSS", "QUIT", "DNF"):
            entry["lost"] = int(entry.get("lost") or 0) + 1
        entry["coins"] = int(entry.get("coins") or 0) + max(0, int(coins))
        return entry

    def reset(self, season: int, division: int) -> bool:
        return self.entries.pop(self._key(season, division), None) is not None

    def current(self) -> tuple[int, int] | None:
        """The season under way, which is the one with matches behind it.

        "Most recently written" was the rule and it picked wrong. A club can
        hold two entries for the same division -- this one holds `1:10` and
        `10:10`, both Division 10 -- because the key carries the `seasonId`
        *this server* served, and that index moved from 1 to 10 when
        `SEASON_DIVISIONS` was reordered. The client rewrites its blob on
        entering the mode, so the empty new key was always the last written and
        the entry holding a real record was never reported.

        The visible cost: `season/user` answered round 1 over a season with a
        match won, the client read that as "not started", and offered to begin
        the season again instead of resuming it.

        So the season with the most matches played wins, and the most recently
        written breaks a tie -- which is the old rule, kept for the ordinary
        case where nothing has been played yet.
        """
        if not self.entries:
            return None
        order = list(self.entries)

        def played(key: tuple[int, int]) -> tuple[int, int]:
            entry = self.entries[key]
            count = sum(int(entry.get(name) or 0)
                        for name in ("won", "draw", "lost"))
            return count, order.index(key)

        return max(order, key=played)

    def state(self) -> dict:
        return {f"{season}:{division}": value
                for (season, division), value in self.entries.items()}

    def restore(self, saved: dict | None) -> None:
        for key, value in (saved or {}).items():
            if not isinstance(value, dict):
                continue
            season, _, division = str(key).partition(":")
            try:
                self.apply(int(season), int(division or 0), value)
            except ValueError:
                continue


SEASON_PROGRESS = TenantView("seasons")


def season_history_response(kind: str = "offline") -> bytes:
    """Seasons already finished, of which there are none.

    Asked for straight after a season is started, once per type -- `.rdata`
    carries `/season/user/history?type=offline`, `?type=online` and the two
    World Cup spellings -- and answered 404 until now.

    An empty object is what goes out, and deliberately: no season has ever
    been completed here, so any list this could carry would be invented. The
    same reading is what `season/user` was reduced to before the divisions
    were understood, and it is the answer the parser reads as "nothing yet"
    rather than as a document it cannot make sense of.
    """
    return b"{}"


# -- settling a match -------------------------------------------------------
#
# `/ut/game/fifa14/match/end` answered with three empty members and threw the
# result away: no coins for the match, no progress in the cup, nothing to show
# on the award screen. A club could play a Gold Cup final and finish it exactly
# as poor as it started.
#
# What the client posts is its own match stats. The names below are the ones
# FIFA 14 puts on the wire -- `goals`, `shotsOnTarget`, `passingPercentage`,
# `possessionPercentage` -- inside `myMatchStats` and `opponentMatchStats`.
# Everything is read defensively: a member that is not there is a zero, never
# an exception, because a settlement that raises loses the match.

# What a finished match pays before anything else. Retail scales it by how
# much of the match was actually played.
MATCH_COMPLETION_AWARD = 325

# Each statistic pays, and each is capped: a 9-0 win is worth more than a 1-0,
# and not nine times more.
MATCH_BONUS_CAPS = {
    "goals": (40, 200),
    "shotsOnTarget": (5, 75),
    "successfulTackles": (1, 20),
    "corners": (5, 50),
    "passAccuracy": (1, 80),
    "possession": (1, 80),
}
MATCH_PENALTY_CAPS = {
    "goalsAgainst": (20, 80),
    "fouls": (1, 20),
    "cards": (10, 80),
    "offsides": (1, 15),
}
CLEAN_SHEET_AWARD = 75
MOTM_AWARD = 15


def _stat(document: dict, *names: str, default: int = 0) -> int:
    """The first of these members the document carries, as an integer."""
    for name in names:
        if name in document:
            try:
                return int(document[name] or 0)
            except (TypeError, ValueError):
                continue
    return default


def match_reward(mine: dict, theirs: dict, minutes: int = 90,
                 multiplier: int = 1, completed: bool = True) -> dict:
    """What a match paid, itemised.

    Itemised because the award screen shows the parts: a completion award and
    a skill award, not one number. Returning the total alone would leave that
    screen with two thirds of it blank.
    """
    mine = mine if isinstance(mine, dict) else {}
    theirs = theirs if isinstance(theirs, dict) else {}
    minutes = max(0, min(90, int(minutes or 0)))

    goals = max(0, _stat(mine, "goals", "goalsFor", "goalsScored"))
    against = max(0, _stat(theirs, "goals", "goalsAgainst", "goalsConceded"))
    values = {
        "goals": goals,
        "shotsOnTarget": max(0, _stat(mine, "shotsOnTarget", "shotsontarget")),
        "successfulTackles": max(0, _stat(mine, "successfulTackles", "tacklesWon")),
        "corners": max(0, _stat(mine, "corners", "cornerKicks")),
        "passAccuracy": max(0, min(100, _stat(mine, "passingPercentage", "passAccuracy"))),
        "possession": max(0, min(100, _stat(mine, "possessionPercentage", "possession"))),
    }
    penalties_seen = {
        "goalsAgainst": against,
        "fouls": max(0, _stat(mine, "fouls")),
        "cards": max(0, _stat(mine, "yellowCards", "cards"))
        + max(0, _stat(mine, "redCards")),
        "offsides": max(0, _stat(mine, "offsides", "offside")),
    }

    bonuses = {
        name: min(values[name] * rate, cap)
        for name, (rate, cap) in MATCH_BONUS_CAPS.items()
    }
    # A clean sheet is only a clean sheet if there was a match to keep it in.
    if against == 0 and minutes >= 45:
        bonuses["cleanSheet"] = CLEAN_SHEET_AWARD
    if _stat(mine, "manOfTheMatch", "motm"):
        bonuses["manOfTheMatch"] = MOTM_AWARD
    penalties = {
        name: -min(penalties_seen[name] * rate, cap)
        for name, (rate, cap) in MATCH_PENALTY_CAPS.items()
    }

    completion = (
        int(round(MATCH_COMPLETION_AWARD * minutes / 90.0)) if completed else 0
    )
    skill_raw = sum(bonuses.values()) + sum(penalties.values())
    # An abandoned match pays nothing for skill. It still cost the contracts.
    skill = max(0, int(skill_raw * max(1, int(multiplier or 1)))) if completed else 0
    return {
        "minutesPlayed": minutes,
        "completed": bool(completed),
        "completionAward": completion,
        "skillAward": skill,
        "bonuses": bonuses,
        "penalties": penalties,
        "goalsFor": goals,
        "goalsAgainst": against,
        "totalCoins": max(0, completion + skill),
    }


# What the client calls the end of a match, and what each one means for a cup.
MATCH_RESULTS = {"WIN", "DRAW", "LOSS", "DNF", "QUIT", "NO_CONTEST", "FORFEIT"}


def match_result(document: dict) -> str:
    """WIN, DRAW, LOSS, DNF or QUIT, however the client spelled it.

    `endReason` is what the observed payload carries. A result nobody
    recognises settles as NO_CONTEST, which pays nothing and moves no cup --
    the safe reading of a message this server does not understand.
    """
    reason = str(document.get("endReason") or "").strip().upper()
    if reason == "FORFEIT":
        return "QUIT"
    if reason in MATCH_RESULTS:
        return reason
    goals = document.get("myMatchStats")
    others = document.get("opponentMatchStats")
    if isinstance(goals, dict) and isinstance(others, dict):
        mine, theirs = _stat(goals, "goals"), _stat(others, "goals")
        if mine > theirs:
            return "WIN"
        if mine < theirs:
            return "LOSS"
        return "DRAW"
    return "NO_CONTEST"


def apply_match_items(inventory: "ClubInventory", items: list) -> dict:
    """Write back what the match did to the eleven who played.

    The real `/match/end` body -- captured from this console on 11 August --
    carries an `items` array beside the two stat blocks:

        {"id": 1800000019, "fitness": 99}
        {"id": 1800000011, "fitness": 95, "assists": 1}
        {"id": 1800000018, "fitness": 96, "goals": 1}

    All of it was thrown away. Nobody ever lost fitness, which is why a fitness
    card had nothing to restore and the whole consumable pile was decoration:
    every player in the club sat at 99 for ever.

    `fitness` is written, not accumulated -- the client sends the value *after*
    the match, not the wear. Goals and assists are added up, because each
    payload carries only what happened in that one match.

    `goals`, `assists` and `lifetimeAssists` are members the cards already
    carry and the name table has. `lifetimeGoals` is in neither, so nothing is
    invented for it. `statsList` and `lifetimeStats` are index/value arrays
    whose indices are not established here, and they are left alone rather
    than written to on a guess about which index means what.
    """
    by_id = {item["id"]: item for item in inventory.items if item.get("id")}
    touched = {"fitness": 0, "goals": 0, "assists": 0, "played": 0,
               "contracts": 0, "unknown": [], "manager": 0}

    # The eleven who started, from the squad this club is actually fielding.
    starters: set = set()
    squads = inventory._squads() if hasattr(inventory, "_squads") else {}
    entry_squad = squads.get(inventory.active_squad_id()) if squads else None
    if entry_squad:
        starters = {i for i in (entry_squad.get("players") or [])[:11] if i}
    elif getattr(inventory, "squad", None):
        starters = {i["id"] for i in inventory.squad[:11] if isinstance(i, dict)}
    for entry in items or []:
        if not isinstance(entry, dict):
            continue
        card = by_id.get(entry.get("id"))
        if card is None:
            touched["unknown"].append(entry.get("id"))
            continue
        # An appearance. Every card the client reports on at the final whistle
        # was on the pitch, so the `items` array is the team sheet -- and
        # `gamesPlayed` is a member CardsDLL's name table carries (0x030BE0)
        # that nothing here had ever written. The bio read 0 matches for a
        # club that had just won a cup.
        # Who actually played.
        #
        # The `items` array is the whole eighteen, not the team sheet, and
        # treating it as the sheet took a contract off every substitute who
        # never left the bench.
        #
        # Fitness alone does not answer it, and a **goalkeeper** is why. On
        # 26 August a cup tie came back with the keeper at 99 -- he started and
        # a comfortable win cost him nothing -- so a rule reading "fitness
        # unchanged means he sat on the bench" left David Seaman with no
        # appearance recorded. That rule was inferred from one sheet where the
        # keeper happened to lose fitness, which is the one case that breaks
        # it.
        #
        # The squad answers it instead: the first eleven of the active squad
        # started, whatever their fitness says. Fitness still catches the
        # substitute who came on, and a goal or an assist still counts, because
        # neither of those can happen from the bench.
        reported = entry.get("fitness")
        held = int(card.get("fitness") or 0)
        try:
            dropped = reported is not None and int(reported) < held
        except (TypeError, ValueError):
            dropped = False

        def scored(member: str) -> bool:
            try:
                return int(entry.get(member) or 0) > 0
            except (TypeError, ValueError):
                return False

        appeared = (
            card.get("id") in starters
            or dropped
            or scored("goals")
            or scored("assists")
        )

        if appeared:
            card["gamesPlayed"] = int(card.get("gamesPlayed") or 0) + 1
            touched["played"] += 1
        # And one match off the contract, for the ones who played.
        #
        # The client reports fitness, goals and assists per player at the
        # whistle and says **nothing** about contracts -- checked across every
        # captured `/match/end` body -- so if this server does not count them
        # down, nothing does. Every card sat at 99 for ever, which left the
        # contract cards with nothing to restore and made the commonest
        # consumable in the game decoration. Reported from the console
        # 17 August 2026: a striker with a cup run behind him still on 99.
        #
        # It stops at zero rather than going negative. Retail will not field a
        # player on an expired contract; this server does not enforce that yet,
        # and a negative number would only make the eventual rule harder to
        # write.
        contract = int(card.get("contract") or 0)
        if appeared and contract > 0:
            card["contract"] = contract - 1
            touched["contracts"] += 1
        if "fitness" in entry:
            try:
                card["fitness"] = max(0, min(99, int(entry["fitness"])))
                touched["fitness"] += 1
            except (TypeError, ValueError):
                pass
        for member, lifetime in (("goals", None), ("assists", "lifetimeAssists")):
            try:
                scored = int(entry.get(member) or 0)
            except (TypeError, ValueError):
                continue
            if scored <= 0:
                continue
            card[member] = int(card.get(member) or 0) + scored
            if lifetime:
                card[lifetime] = int(card.get(lifetime) or 0) + scored
            touched[member] += scored
        # And publish them where the bio actually reads them. The counters
        # above are members the parser has no name for -- `goals` is not in the
        # table at all -- so a match that updated only those wrote numbers
        # nothing would ever display.
        sync_stat_slots(card)

    # And the manager, who is not in the team sheet at all.
    #
    # The client reports the eighteen and says nothing about the man in the
    # dugout, so if this does not count his match down nothing does -- reported
    # from the console 26 August, a manager still on the contract he arrived
    # with after a cup tie. Retail spends one per match, which is why manager
    # contract cards exist and are a family of their own (subtype 202).
    for manager in squad_manager(inventory):
        held = int(manager.get("contract") or 0)
        if held > 0:
            manager["contract"] = held - 1
            touched["manager"] += 1
        manager["gamesPlayed"] = int(manager.get("gamesPlayed") or 0) + 1
    return touched


def active_tournaments_response() -> bytes:
    """Only the cups actually entered.

    This used to name every cup in the catalogue, which told the screen the
    club was mid-run in all of them while no progress existed for any.
    """
    return json.dumps(
        {"tournamentId": TOURNAMENT_PROGRESS.active_ids()}, separators=(",", ":")
    ).encode()


TOTW_FILE = Path(__file__).resolve().parent / "fifa14_totw.json"


# The Team of the Week, forty-nine weeks of it.
#
# TOTW 1 went out on 18 September 2013 and one followed every Wednesday, so
# week N is that date plus 7(N-1) days. `tools/build_totw.py` writes the
# catalogue; the teams and formations come from Impulsum14's extract and every
# one of the 882 asset ids resolves against this server's own card catalogue.
#
# What this replaced was two squads of six ids scraped off wefut, with the rest
# of the eleven padded from the catalogue's best rares. That padding decided
# how strong the opponent was -- `opponentRating` reads the first eleven -- so
# a side whose real members top out at 85 was played against a bench of 98s.

# The in-form band. A base card is version 1; these are version 50, and they
# carry `rareflag` 3 so the client draws the in-form art.
TOTW_RESOURCE_VERSION = 50 * 0x0100_0000
TOTW_RARE = 3

# The Team of the Week's own persona -- and the entries announce it.
#
# Keys 3 and 4 of the challenge record are its two halves. Impulsum sends
# 2147483647 and -2, and read as one little-endian 64-bit value, low word
# first, they are
#
#     0xFFFFFFFE | 0x7FFFFFFF << 32  ==  0x7FFFFFFFFFFFFFFE
#                                    ==  9223372036854775806
#
# which is exactly the persona its `Totw.PersonaId()` defaults to. Not a
# coincidence and not two unrelated sentinels: the record carries the id of the
# club that owns the challenge, and the client then asks
# `/user/list?personaIdList=<that>` for it. That club is the one the challenge
# select screen enumerates -- "WEEK 41/49 ... WEEK 49/49" is its squad list.
#
# So the persona is the source here and the two keys are derived from it,
# rather than three constants copied separately and able to drift apart.
TOTW_PERSONA_ID = 9_223_372_036_854_775_806


def _persona_halves(persona: int) -> tuple[int, int]:
    """(high, low) as signed 32-bit words, which is how the record carries it."""
    raw = int(persona) & 0xFFFF_FFFF_FFFF_FFFF
    high = (raw >> 32) & 0xFFFF_FFFF
    low = raw & 0xFFFF_FFFF
    to_signed = lambda word: word - 0x1_0000_0000 if word >= 0x8000_0000 else word
    return to_signed(high), to_signed(low)



def totw_squads() -> list[dict]:
    """Every week the catalogue holds, in order."""
    if not TOTW_FILE.exists():
        return []
    try:
        return json.loads(TOTW_FILE.read_text()).get("squads", [])
    except (OSError, ValueError):
        return []


def totw_active_week() -> int:
    """Which week the screen is showing.

    `FIFA14_TOTW_WEEK` names one. The default is **week 1**, deliberately, for
    testing: the console's clock is not this season's, and a real release
    schedule would have to decide what "now" means on a title whose season
    ended in 2014.

    An unknown week falls back to the first one the catalogue holds.
    """
    squads = totw_squads()
    if not squads:
        return 1
    known = {int(entry.get("week") or 0) for entry in squads}
    raw = os.environ.get("FIFA14_TOTW_WEEK", "").strip()
    try:
        asked = int(raw) if raw else 1
    except ValueError:
        asked = 1
    return asked if asked in known else min(known)


def totw_week(week: int | None = None) -> dict:
    """One week's squad, or an empty dict when there is no catalogue."""
    squads = totw_squads()
    if not squads:
        return {}
    wanted = int(week if week is not None else totw_active_week())
    return next(
        (entry for entry in squads if int(entry.get("week") or 0) == wanted),
        squads[0],
    )


def _totw_slots(entry: dict) -> list[dict]:
    return sorted(
        entry.get("slots") or [], key=lambda slot: int(slot.get("order") or 0)
    )


def totw_response(
    catalogue: "CardCatalogue", size: int = 23, week: int | None = None
) -> bytes:
    """Team of the Week: the week's own eighteen, in its own formation."""
    by_asset = {card["assetId"]: card for card in catalogue.cards}
    entry = totw_week(week)
    slots = _totw_slots(entry)

    items = []
    for index, slot in enumerate(slots):
        # A slot whose base card is not in the catalogue is still a real card:
        # the in-form's position, rating and face stats are on the slot itself.
        # Dropping it left five weeks a player short -- week 10 fielded sixteen
        # with no goalkeeper at all, because Andriy Pyatov's base card is not
        # in this catalogue.
        #
        # The club, nation and league are on the slot too, resolved at build
        # time against the raw cards file. `CardCatalogue` drops 196 cards that
        # are missing one of those -- right for the market, where such a card
        # renders with placeholder text, and wrong for a fixed side. Andriy
        # Pyatov is one of them, which is why week 10 had no goalkeeper.
        card = by_asset.get(int(slot.get("assetId") or 0)) or {}
        items.append(
            _player_item(
                item_id=1_850_000_000 + int(entry.get("week") or 0) * 100 + index,
                asset_id=int(card.get("assetId") or slot.get("assetId") or 0),
                # The in-form's rating, which is not the base card's -- an
                # in-form is a different card from the player it is based on.
                rating=int(slot.get("rating") or card.get("rating", 0)),
                rare=TOTW_RARE,
                play_style=0,
                team_id=int(slot.get("clubId") or card.get("clubId") or 0),
                # The in-form's own face stats, which are not the base card's.
                attributes=(
                    slot.get("attributes")
                    or card.get("attributes", [0] * 6)
                ),
                # The card's own position. `totw_teams.tsv` carries the
                # formation slot beside it -- RCB, LCB, RCM -- and serving that
                # is why the centre-backs drew blank: RCB is not a position the
                # client knows. It is kept on the slot as `slot` for reading.
                position=slot.get("position") or card.get("position")
                or FALLBACK_POSITION,
                item_state="free",
                nation=int(slot.get("nationId") or card.get("nationId") or 0),
                league=int(slot.get("leagueId") or card.get("leagueId") or 0),
                rarity=card.get("rarity", ""),
                # Gold, silver and bronze in-forms are different art bands --
                # 8, 9, 10 and 11 -- and the card names its own.
                version=int(slot.get("band") or 0) * 0x0100_0000
                or TOTW_RESOURCE_VERSION,
            )
        )

    # The screen wants a challenge, not a squad: CardsDLL names
    # RequestChallengeData, GetChallengeData, GetTotalChallenges and
    # SetSelectedChallengeInfo, and its JSON table carries `squadChallenge`
    # at 0x8902FFD8 beside `squadId`. So a challenge is a squad you play
    # against, and the document lists them.
    #
    # The member names below are the ones the binary actually carries; the
    # arrangement around them is still inferred, which is why this is kept
    # small -- an invented shape froze the title twice.
    def challenge(squad: dict) -> dict:
        """One side to play against, rated from the cards it actually holds.

        `opponentRating` used to be computed as `max(... for card in [])` --
        over an empty list, so every challenge advertised a rating of 0 and an
        opponent of team 0. A side you are invited to beat has to say how
        strong it is.
        """
        eleven = [s for s in _totw_slots(squad) if s.get("starter")][:11]
        rating = int(squad.get("rating") or 0)
        if not rating and eleven:
            rating = round(
                sum(int(s.get("rating") or 0) for s in eleven) / len(eleven)
            )
        clubs = [
            by_asset[asset].get("clubId")
            for asset in (int(s.get("assetId") or 0) for s in eleven)
            if asset in by_asset and by_asset[asset].get("clubId")
        ]
        # The club most of them play for, which is what an opponent team id
        # means here. Zero is "no team" and drew nothing.
        team = max(set(clubs), key=clubs.count) if clubs else 0
        return {
            "squadId": int(squad.get("week") or 0),
            "squadName": squad.get("name", "TOTW"),
            "formation": squad.get("formation") or FORMATION,
            "opponentTeam": int(team),
            "opponentRating": rating,
        }

    return json.dumps(
        {
            "itemData": items,
            "formation": entry.get("formation") or FORMATION,
            "squadName": entry.get("name") or "Équipe de la semaine",
            "squadChallenge": [challenge(week) for week in totw_squads()],
        },
        separators=(",", ":"),
    ).encode()


def hub_response(
    inventory: "ClubInventory", market: int, selling: int, sold: int,
    unlisted: int = 0, totw: dict | None = None,
) -> bytes:
    """The transfer and My Club tiles read their counts from here.

    Three tiles, three counts, and they are not the same number:

      TRANSFER MARKET  `auctionCount` -- how many auctions are live to browse.
                       This is the *market*, not the club's own listings.
                       Feeding it the club's listing count is why the tile read
                       "13 LIVE TRANSFERS" when 13 was the player's own sold
                       cards and the market had thousands. Kyro's build makes
                       the same point in the same words.
      TRANSFER LIST    `selling` and `sold` -- the club's own active and sold
                       listings, and `transferListCount` for the tile's item
                       total. These were absent, so the tile read 0/0 over a
                       list that held thirteen.
      MY CLUB          `clubPlayers`.

    `market` already includes the club's own live listings from the caller, so
    a card the player lists appears on the market tile as well as the list one.
    """
    players = sum(
        1 for item in inventory.items if item.get("itemType") == "player"
    )
    # Everything on the transfer list: active listings, sold-awaiting-collect,
    # and cards sent to the list but not yet listed. The tile read "0 ITEMS"
    # over ten unlisted cards because these were left out.
    user_total = selling + sold + unlisted
    return json.dumps(
        {
            "auctionCount": market,
            "clubPlayers": players,
            "selling": selling,
            "sold": sold,
            # **Nested, because that is what the tile reads.**
            #
            # `transferListCount`, `tradePileCount` and `tradePileItems` are
            # none of them in CardsDLL's table -- three members invented here --
            # so the TRANSFER LIST tile read 0 ITEMS, Selling 0, Sold 0 over a
            # club holding 26 cards on the pile and 5 sold listings. Reported
            # from the console 27 August.
            #
            # `tradePile`, `watchlist`, `count`, `notification`, `winning` and
            # `outbid` are all in the table, and Impulsum's working hub sends
            # exactly this shape.
            "tradePile": {
                "selling": selling,
                "sold": sold,
                "count": user_total,
                # What the tile badges. Sold cards are waiting to be collected,
                # and an outbid card wants attention the same way.
                "notification": sold,
            },
            # No bids are modelled yet, so the targets tile is honestly empty
            # rather than absent -- a tile with no numbers draws nothing.
            "watchlist": {"winning": 0, "count": 0, "outbid": 0},
            # The Team of the Week tile on the PLAY screen. Without it the tile
            # is an empty pitch -- no cards and no "Active Challenge" line.
            **({"squad": totw} if totw else {}),
        },
        separators=(",", ":"),
    ).encode()


# The member names CardsDLL's JSON table carries for consumables, between
# 0x89030F9C and 0x89031148. The response is an object with these members, not
# an entries array -- numbered keys meant nothing to it, which is why the apply
# screen reported none available while the club held sixteen.
#
# Each card names the one it belongs to -- tools/build_consumables.py takes it
# from the subtype block the card sits in, which is how the database and these
# member names line up: 201 against 202 for a player's contract and a
# manager's, 219 against 220 for a player's fitness and the team's.
CONSUMABLE_MEMBERS = {
    "contract": "consumablesContractPlayer",
    "fitness": "consumablesFitnessPlayer",
    "healing": "consumablesHealing",
    "training": "consumablesTrainingPlayer",
    "position": "consumablesPosition",
    "playStyle": "consumablesTrainingPlayerPlayStyle",
}

# The rest of the members. Every one is a real distinction the game makes --
# a keeper's training card is not an outfielder's -- and the club now holds
# cards for each, so the counts are counted rather than shared out.
#
# They used to go out at zero, and that is what "Pas d'élément disponible" was
# reporting when applying from the squad screen: that screen decides from these
# counts alone, and picking a goalkeeper makes it read consumablesTrainingGk.
# The manager's own cards are in the database -- subtypes 250 to 273 and 300
# to 340 -- but nothing in it says which member each block belongs to, and
# guessing would put the wrong card under the wrong name. So these members
# report their family's count, which is what they did before any of this and
# what stopped the popup: a count that is too generous costs nothing, a zero
# refuses to apply anything.
CONSUMABLE_FALLBACKS = {
    "consumablesTrainingManager": "training",
    # `consumablesFormationManager` is gone, 26 August. It reported the
    # position family's count -- twenty -- over a family this server serves
    # **none** of: the formation modifiers are subtypes 121-136 on art 35, art
    # 35 draws NOT FOUND, and they are in both UNDRAWN_CONSUMABLE_TYPES and
    # UNSEEDED_CONSUMABLE_TYPES because of it.
    #
    # So the tab announced twenty and had nothing behind it, which is exactly
    # what the manager-league tab was doing when it said sixty-nine. A family
    # held out on purpose should report nothing, not borrow another's count.
    # The player put it well: he did not remember formation modifiers being in
    # FIFA 14 at all.
    # `consumablesPosition` was carried by the six subtype-232 cards alone, and
    # those are out of the club now -- they drew NOT FOUND in the position
    # modifier tab, which is what the player reported. Reporting the position
    # family's own count keeps the Apply Consumable popup offering the
    # thirty-six cards that do render, instead of refusing on a zero.
    #
    # A fallback rather than a relabel: the catalogue files those thirty-six
    # under the play-style members, and rewriting that to fix a counter would
    # zero the goalkeeper style counter in exchange. The counts are what the
    # popup reads; the members stay as the catalogue has them.
    "consumablesPosition": "position",
}

# The three the game also asks for in aggregate. Each is its family's count
# rather than the sum of the members under it: the members that fall back
# would be counted twice, and a club of 242 consumables would report 300.
# The club stats contexts: 2 is the year, 5 the new cards, 6 the consumables.
CONSUMABLE_STAT_CONTEXT = 6

CONSUMABLE_TOTALS = {
    "consumablesContract": "contract",
    "consumablesFitness": "fitness",
    "consumablesTraining": "training",
}


def consumable_stats_response(inventory: "ClubInventory") -> bytes:
    """How many of each consumable the club holds, under the real member names."""
    held: dict[str, int] = {member: 0 for member in CONSUMABLE_MEMBERS.values()}
    by_kind: dict[str, int] = {}
    # The member a card counts under comes from the catalogue, keyed by the
    # card's own database id. It used to travel on the item as
    # `consumableMember` -- a name CardsDLL does not carry, so the client
    # never read it and it had no business on the wire.
    definitions = _consumable_definitions()

    total = 0
    for item in inventory.items:
        if item.get("itemType") not in CONSUMABLE_TYPES:
            continue
        row = definitions.get(int(item.get("resourceId") or 0))
        if row is None:
            continue
        kind = row["itemType"]
        count = int(item.get("count") or 1)
        total += count
        by_kind[kind] = by_kind.get(kind, 0) + count
        member = row.get("member")
        if member:
            held[member] = held.get(member, 0) + count

    document = dict(held)
    for member, kind in CONSUMABLE_FALLBACKS.items():
        document[member] = by_kind.get(kind, 0)
    for name, kind in CONSUMABLE_TOTALS.items():
        document[name] = by_kind.get(kind, 0)
    document["consumables"] = total

    # The named scalars are not what the Apply Consumable popup reads.
    #
    # That screen is backed by a sticker-book stats response, and it binds its
    # consumable-type buttons from `stat`/`entries` rows in context 6 --
    # `{contextId, contextValue, type, typeValue}` -- not from members sitting
    # at the top level. Serving the scalars alone is why the popup reported
    # "Pas d'élément disponible" over a club holding 65 contracts, with every
    # scalar it could have read non-zero and every card present.
    #
    # All six member names are in CardsDLL, `StickerBook` with them. The
    # scalars stay: other screens do read those, and the two are the same
    # counts twice rather than a choice between them.
    entries = [
        {
            "contextId": CONSUMABLE_STAT_CONTEXT,
            "contextValue": 0,
            "type": member,
            "typeValue": int(count),
        }
        for member, count in sorted(document.items())
    ]
    document["stat"] = entries
    document["entries"] = entries
    return json.dumps(document, separators=(",", ":")).encode()


def club_stats_response(
    inventory: "ClubInventory", context_id: int = 5, context_value: int = 0
) -> bytes:
    """The Mon Club counters: players, rares, staff, stadiums, kits, badges.

    They all read zero because the stats endpoints answered with an empty
    entries list -- a club full of cards reporting nothing owned.

    The key numbers are the screen's own slots; they are recovered from what
    the screen displays rather than from a document, so the mapping is a
    reading and not a certainty. The counts themselves are exact.
    """
    def count(kind: str) -> int:
        return sum(1 for item in inventory.items if item.get("itemType") == kind)

    players = count("player")
    rares = sum(
        1
        for item in inventory.items
        if item.get("itemType") == "player" and item.get("rareflag")
    )
    # Every family that reports as staff. `count("staff")` was the whole of
    # this and nothing has ever carried that itemType -- the game's four
    # families are named, and the counters beside them in CardsDLL are named
    # per family too, so they can be filled rather than summed into one.
    head_coaches = count("headCoach") + count("headcoach")
    gk_coaches = count("gkCoach") + count("gkcoach")
    fitness_coaches = count("fitnessCoach") + count("fitnesscoach")
    physios = count("physio")
    managers = count("manager")
    staff = (
        count("staff")
        + managers
        + head_coaches
        + gk_coaches
        + fitness_coaches
        + physios
    )
    stadia = count("stadium")
    kits = count("kit")
    badges = count("badge") + count(BADGE_WIRE_TYPE)
    balls = count("ball")

    def by_tier(tier: str) -> int:
        low, high = TIER_RATINGS[tier]
        return sum(
            1
            for item in inventory.items
            if item.get("itemType") == "player"
            and low <= int(item.get("rating") or 0) <= high
        )

    # `key` is not a member of this binary.
    #
    # The counters went out as `{"entries":[{"key":0,"value":485}, ...]}` and
    # the MY CLUB screen drew zero in all eight slots. `strings` on
    # `work/cardsdll.bin` settles why: **`key` does not appear in CardsDLL at
    # all** -- no exact match, in any capitalisation; the only near things are
    # `artificialkey`, `eligibilityKey` and `keyid`. The parser was being handed
    # a list whose every row was named after something it has never heard of.
    #
    # What it does carry, all of it exact: `contextId`, `contextValue`, `type`,
    # `typeValue`, `stat`, `entries`, and a name for every counter below.
    #
    # The shape comes from MarvelcoCode/Impulsum14, a separate FIFA 14 server,
    # which emits the same array twice as `stat` and as `entries`. That is a
    # **PC** implementation and its client is a different binary -- the same
    # caveat this project already carries about Kyro's build and about the
    # leaked XNet source. It is not proof about the 360. What makes it more
    # than a guess is the check above: every member it uses is in this
    # console's own table and the one we were using is not.
    #
    # Bronze under 65, silver 65 to 74, gold 75 and over -- the same boundaries
    # `TIER_RATINGS` already carries, arrived at independently there.
    counters = [
        ("playerCount", players),
        ("players", players),
        ("clubPlayers", players),
        ("rarePlayers", rares),
        ("playersBronze", by_tier("bronze")),
        ("playersSilver", by_tier("silver")),
        ("playersGold", by_tier("gold")),
        ("staff", staff),
        ("staffManager", managers),
        ("staffHeadCoach", head_coaches),
        ("staffGKCoach", gk_coaches),
        ("staffFitnessCoach", fitness_coaches),
        ("staffPhysio", physios),
        ("numberItems", len(inventory.items)),
        ("stadia", stadia),
        ("balls", balls),
        ("kits", kits),
        ("badges", badges),
        ("trophies", 0),
        ("legendCount", sum(
            1
            for item in inventory.items
            if (item.get("rarity") or "").strip().lower() == "legend"
        )),
    ]
    rows = [
        {
            "contextId": context_id,
            "contextValue": context_value,
            "type": name,
            "typeValue": value,
        }
        for name, value in counters
    ]
    document = {name: value for name, value in counters}
    # Both names for the same array, as the reference does: which one this
    # screen reads is not established, and they cannot disagree.
    document["stat"] = rows
    document["entries"] = rows
    return json.dumps(document, separators=(",", ":")).encode()





# The category the picker names in the path, mapped to what this server calls
# the family. `/ut/game/fifa14/club/consumables/contracts` -- plural -- and
# `/club/consumables/fitness`, and `/club/consumables/development`, which is
# the wire item type rather than a family and means every card of that type.
#
# Every name below is one the console has actually asked for, read off the
# journal rather than guessed: development, contracts, fitness, playStyle,
# healing, position, training, managerLeagueModifier.
CONSUMABLE_CATEGORIES = {
    "contract": "contract",
    "contracts": "contract",
    "fitness": "fitness",
    "healing": "healing",
    "training": "training",
    "playertraining": "training",
    "gktraining": "training",
    "playstyle": "playStyle",
    "chemistry": "playStyle",
    "chemistrystyle": "playStyle",
    "position": "position",
    "positioning": "position",
    # The club holds these now -- subtypes 300-326, one per league, added
    # 25 August. The console asks for them by the family name rather than by
    # the wire type: `/club/consumables/managerLeagueModifier`.
    #
    # It asked for them before they existed, too, and got an empty tab under a
    # count of sixty-nine, because the count came from a fallback and the list
    # came from here. Both halves are the stock now.
    "managerleaguemodifier": "managerLeagueModifier",
    "managerleague": "managerLeagueModifier",
    # Still none of these. Manager contracts are left out of the catalogue by
    # tools/build_consumables.py, and the formation modifiers are in it but
    # held out of the club -- art 35 draws NOT FOUND. An empty tab is the
    # truth for both.
    "managercontract": "",
    "formationmanager": "",
}


def consumables_response(
    inventory: "ClubInventory", category: str = ""
) -> bytes:
    """The Apply Consumable picker asks here, one category at a time.

    `/ut/game/fifa14/club/consumables/contracts`, then `/fitness`, then
    `/development`. This route was matched with `startswith` and answered with
    every consumable the club owns whatever was asked for -- the picker asked
    for contracts and got 242 cards of every family mixed together, which is
    not a list of contracts however many contracts are in it.

    An unknown category returns everything, which is what the bare path means.
    """
    wanted = (category or "").strip().lower()
    family = CONSUMABLE_CATEGORIES.get(wanted)
    items = []
    for item in inventory.items:
        kind = item.get("itemType")
        if kind not in CONSUMABLE_TYPES:
            continue
        if not wanted:
            pass                                    # the bare path: everything
        elif family is not None:
            # A category this club has no cards for -- a manager contract, say
            # -- maps to the empty string and matches nothing. Falling through
            # to "everything" instead is how a tab headed one thing lists
            # another.
            if not family or consumable_family(item) != family:
                continue
        elif wanted in CONSUMABLE_TYPES:
            # `development` and `training` are the wire types themselves.
            if kind != wanted:
                continue
        else:
            # Named something this server does not know. Nothing, not
            # everything: a wrong list reads as a working screen.
            continue
        items.append(item)
    # Stacked, like the club list. This is the route the Apply Consumable
    # picker actually reads -- `/club/consumables/contracts` and its siblings --
    # and it was serving every card individually while `/club` served them
    # collapsed, so the club tab stacked and the picker did not.
    items = stack_consumables(items)
    return json.dumps(
        {
            "itemData": items,
            "total": len(items),
            "count": len(items),
            "start": 0,
        },
        separators=(",", ":"),
    ).encode()


# What `/clubUser` carries besides the persona.
#
# Every consumable the club owns, individually. Not one row per definition
# with a count -- the PC revival appends "every owned consumable" and its
# picker reports 5 contracts for 5 cards, so the client counts rows -- and not
# a per-family sample either.
#
# The sample was the bug. Twelve cards a family sounds fair until you notice
# the families here span two subtype blocks each: `training` covers 51-57 and
# 61-67, `playStyle` covers 91-110 and 121-136, and the first twelve of either
# are all from the low block. Applying to a goalkeeper reads the keeper's
# block, and the keeper's block was not in the twelve. The screen said "Pas
# d'élément disponible" over a club holding 21 of them.
#
# All 242 come to about 68 KB, which fits under what the console was measured
# surviving. The players share what is left, because the route is the client's
# face-card cache bootstrap and the PC revival appends consumables *to* a
# player page rather than instead of one.
CLUB_USER_BUDGET = 74 * 1024

# How many copies of one subtype are worth sending. The picker offers a card
# to apply, not an inventory: three of a kind is as useful as sixty-five, and
# the room saved is what lets the players travel with them. The real count is
# what `club/stats/consumables` reports, and that stays truthful.
CLUB_USER_COPIES = 3


def club_user_response(inventory: "ClubInventory", name: str) -> bytes:
    """`/clubUser` -- the persona, and the cards the client binds against.

    `FutGetClubUsersServerResponse` reads a `user` array, singular, and that is
    all this used to answer: 122 bytes. The Apply Consumable picker binds here
    and never asks the server for more -- it reads the counts from
    `club/stats/consumables` and the cards from whatever it already holds.
    """
    players: list[dict] = []
    consumables: list[dict] = []
    for item in inventory.items:
        kind = item.get("itemType")
        if kind == "player":
            players.append(item)
        elif kind in CONSUMABLE_TYPES:
            consumables.append(item)

    def document(cards: list[dict]) -> bytes:
        return json.dumps(
            {
                "user": [
                    {"persona": name, "personaId": PERSONA.id, "public": False},
                    # The Team of the Week's club, announced beside the
                    # player's. This is how the client learns it exists.
                    #
                    # Nothing else tells it. The challenge record carries that
                    # persona in keys 3 and 4, but the console was never seen
                    # asking `/user/list` for it -- because it had no reason
                    # to: a persona that appears in no user list is not a club
                    # the client knows about, and "there's no Team of the Week
                    # available" is a true statement about a club it has never
                    # been told exists.
                    #
                    # `persona` and `public` are the ION binding table's
                    # GAMERTAG and PUBLIC, at 0x890125B4 and 0x890125EC, beside
                    # USER_ID and the rest of the fields that screen reads.
                    {
                        "persona": "TOTW",
                        "personaId": TOTW_PERSONA_ID,
                        "public": True,
                    },
                ],
                "itemData": cards,
                "total": len(cards),
                "count": len(cards),
                "start": 0,
            },
            separators=(",", ":"),
        ).encode()

    # Dealt by subtype, one round at a time. Coverage first: every subtype the
    # club owns gets a card before any subtype gets a second, so the keeper's
    # training block cannot be missing while the outfield block is there four
    # times over. All 242 come to 79.7 KB, which is past what the console was
    # measured surviving, so something has to give -- and what gives is the
    # fourth copy of a card, never the only copy of a kind.
    #
    # Players take what is left. Grown by measurement rather than by an
    # assumed cost a card: a consumable runs about 330 bytes and a player
    # about 860, and both move whenever a member is added to either.
    queues: dict[int, list[dict]] = {}
    for item in consumables:
        queues.setdefault(item.get("cardsubtypeid"), []).append(item)

    ordered: list[dict] = []
    for _ in range(CLUB_USER_COPIES):
        for queue in queues.values():
            if queue:
                ordered.append(queue.pop(0))

    cards: list[dict] = []
    payload = document(cards)
    for card in ordered + players:
        candidate = document(cards + [card])
        if len(candidate) > CLUB_USER_BUDGET:
            if card in players:
                break
            continue
        cards.append(card)
        payload = candidate
    # What the console is now holding.
    #
    # This route is the client's face-card cache: it reads these once and never
    # asks for more. So these ids are exactly the cards it can draw without
    # another request, and that is what the duplicate panel needs to know --
    # `pile_duplicate_pairs` pairs a new card against an owned one by id, and
    # the client draws the owned card out of this cache. An id that is not in
    # it has no card behind it, which is what "MY CURRENT ITEM: undefined" is.
    inventory.cached_ids = {
        card["id"] for card in cards if card.get("id")
    }
    return payload


def totw_hub_squad(catalogue: "CardCatalogue", week: int | None = None) -> dict:
    """The Team of the Week as the PLAY tile draws it.

    The tile was an empty pitch: no cards, no "Active Challenge" line. `/hub`
    is fetched 499 times across these journals and carried no squad at all, and
    Impulsum's build puts one there -- that is what fills the tile.

    Built on this server's own squad document rather than copied. Four of the
    members Impulsum sends are **not in CardsDLL's table** -- `loyaltyBonus`,
    `dreamSquad`, `squadType` and `newSquad` -- and a member this binary does
    not carry is what froze the match-award screen on 17 August and the
    trade-offer screen on 18 August. Every member below is one
    `active_squad_response` already sends, plus `captain` and `kicktakers`,
    which are both in the table.

    `/hub` is one of the three routes measured to tolerate an unrecognised
    sibling -- see `with_balance` -- so this is the safe place to put it.
    """
    entry = totw_week(week)
    slots = _totw_slots(entry)
    by_asset = {card["assetId"]: card for card in catalogue.cards}
    squad = json.loads(totw_response(catalogue, week=week))
    items = squad["itemData"]

    players = []
    for index, item in enumerate(items):
        players.append(
            {
                "index": index,
                "itemData": item,
                "kitNumber": index + 1 if index < 11 else 0,
            }
        )

    starters = [s for s in slots if s.get("starter")][:11]
    rating = int(entry.get("rating") or 0)
    if not rating and starters:
        rating = round(
            sum(int(s.get("rating") or 0) for s in starters) / len(starters)
        )

    # The card the tile leads with. Impulsum takes the first starter; the
    # highest-rated one is what the screen is actually advertising -- their own
    # shot leads with an 89 Robben, who is the best card in that week.
    captain = 0
    if items:
        captain = max(items, key=lambda card: card.get("rating", 0))["id"]

    return {
        # The Team of the Week's club, which is what keys 3 and 4 of the
        # challenge record name. The squad is that club's, not the player's.
        "personaId": TOTW_PERSONA_ID,
        "id": int(entry.get("week") or 0),
        "squadName": entry.get("name") or "TOTW",
        "formation": entry.get("formation") or FORMATION,
        "chemistry": 100,
        "starRating": rating,
        "rating": rating,
        "changed": False,
        "players": players,
        "manager": [],
        "captain": captain,
        # Five set-piece slots, all the same card, which is what a side nobody
        # has arranged carries.
        "kicktakers": [{"id": captain, "index": index} for index in range(5)],
    }


def totw_club_info(catalogue: "CardCatalogue | None" = None) -> bytes:
    """The Team of the Week's club, for `/user/list?personaIdList=<totw>`.

    A club, with a squad list -- one entry per week -- rather than a squad.
    That is what the console goes looking for once the squad it was shown
    belongs to a persona that is not the player's.

    `squadType` and `categoryId` are left out: Impulsum sends both and neither
    is in CardsDLL's table. `activeSquadId` is not in it either and is sent
    anyway, because the list has to say which week is showing and nothing else
    in the document can; it is the one unverified member here and it is a
    sibling at the top of `squadList`, where this protocol skips what it does
    not know.

    The crest and kits are this server's own verified art -- badge 6000241 and
    kits 6300000/6400001, all three seen rendering on this console -- rather
    than Impulsum's 6000654/6300815/6400685, which are a PC build's ids and
    have never been drawn here.
    """
    weeks = totw_squads()
    active = totw_active_week()
    return json.dumps(
        {
            "user": [
                {
                    "personaId": TOTW_PERSONA_ID,
                    "clubName": "Team of the Week",
                    "clubAbbr": "TOTW",
                    "teamId": 0,
                    "bidTokens": {},
                    "established": 2013,
                    "squadList": {
                        "squad": [
                            {
                                "id": int(entry.get("week") or index + 1),
                                "squadName": entry.get("name", "TOTW"),
                                "formation": entry.get("formation") or FORMATION,
                                "rating": int(entry.get("rating") or 0),
                                "chemistry": 100,
                            }
                            for index, entry in enumerate(weeks)
                        ],
                        "activeSquadId": active,
                    },
                    "badge": {"resourceId": 6_000_241, "teamId": 241},
                    "homekit": {"resourceId": 6_300_000, "teamId": 241, "year": 0},
                    "awaykit": {"resourceId": 6_400_001, "teamId": 241, "year": 0},
                }
            ]
        },
        separators=(",", ":"),
    ).encode()


def totw_index(catalogue: "CardCatalogue | None" = None) -> bytes:
    """The list of Team of the Week squads available to view.

    The screen asks for the TOTW itself and then for this list, and a 404 here
    is what it reports as "aucune Équipe de la semaine disponible" -- the squad
    had already been served successfully.

    Every entry used to advertise `rating` 0. A squad with no rating is not a
    squad the screen can offer, and "aucune disponible" is what it says about a
    list it will not take. The rating is the week's own now, and so is the
    formation -- these are not all f442, week 1 is f343.
    """
    squads = totw_squads()
    return json.dumps(
        {
            "squad": [
                {
                    "id": int(entry.get("week") or index + 1),
                    "squadName": entry.get("name", f"TOTW {index + 1}"),
                    "formation": entry.get("formation") or FORMATION,
                    "rating": int(entry.get("rating") or 0),
                    "chemistry": 100,
                }
                for index, entry in enumerate(squads)
            ],
            "activeSquadId": totw_active_week(),
            "userInfo": [],
        },
        separators=(",", ":"),
    ).encode()




# -- keeping the club between sessions -------------------------------------
#
# Everything above is rebuilt from the icebreaker packs at every server start,
# so a card sent to the club survived exactly as long as the process did: the
# club counter went back to 92 on the next relaunch and the pack you opened was
# gone. Entering FUT needs a relaunch, so that is every single session.

# FIFA14_CLUB_SAVE points this somewhere else. The test suite sets it, and it
# has to: importing the server module builds a live club from the real save,
# and any route under test that writes wrote *that file*. A cup entered one
# evening was gone by the next launch because a test run in between had saved
# the club with the tournament table a test had just cleared. Nothing warned;
# the file simply came back smaller.
SAVE_FILE = Path(
    os.environ.get("FIFA14_CLUB_SAVE")
    or Path(__file__).resolve().parent.parent / "runtime" / "club-save.json"
)


def club_save_path(persona_id: int) -> Path:
    """Where one club's save lives.

    Persona 0 is the club nobody has named yet -- a single-console setup that
    has not identified itself, and the whole test suite -- and it keeps the
    historical path, so nothing about that case changes. A real nucleus id
    gets its own file beside it.
    """
    if not persona_id:
        return SAVE_FILE
    return SAVE_FILE.parent / "clubs" / f"{int(persona_id)}.json"


class ClientData:
    """What the client stores with the server and reads back.

    `PUT /ut/game/fifa14/clientdata/userHubData` carries the counters the
    Transfers hub tiles are drawn from -- the client works them out itself and
    keeps them here:

        {"entries":[{"key":0,"value":3},{"key":1,"value":2},
                    {"key":2,"value":0},{"key":3,"value":0}]}

    `GET` of the same route answered a hardcoded `{}`, so every session started
    by telling the client its own counters were nothing, and TRANSFER LIST read
    "0 ITEMS / Selling: 0 / Sold: 0" over a pile holding twenty-seven.

    What goes back is the document the client sent, byte for byte. That matters
    here more than anywhere: this parser is one of the two that **freeze the
    login** on an object carrying a member they do not know -- adding a coin
    balance to this route stopped the fan-out dead, and it is written up beside
    the balance work. Echoing the client's own shape cannot introduce an unknown
    member, because the client wrote every member in it.

    Only names the client has actually written are answered from here. The
    fixtures for `pileSize`, `tutorialpopups` and `managerquest` are deliberate
    and are left alone.
    """

    def __init__(self) -> None:
        self.stored: dict[str, str] = {}

    def save(self, name: str, body: bytes | None) -> bool:
        """Keep one client-written document. Returns whether it was kept."""
        if not body:
            return False
        try:
            document = json.loads(body)
        except ValueError:
            return False
        # An object with an `entries` list is the shape every clientdata route
        # uses. Anything else is not something to hand back.
        if not isinstance(document, dict) or not isinstance(
            document.get("entries"), list
        ):
            return False
        self.stored[name] = json.dumps(document, separators=(",", ":"))
        return True

    def read(self, name: str) -> bytes | None:
        held = self.stored.get(name)
        return held.encode() if held else None

    def state(self) -> dict:
        return dict(self.stored)

    def restore(self, saved: dict | None) -> None:
        self.stored = {
            str(k): v for k, v in (saved or {}).items() if isinstance(v, str)
        }


CLIENT_DATA = ClientData()


class ClubSave:
    """The club's own state, written to disk and reloaded."""

    def __init__(self, path: Path = SAVE_FILE, fallback: Path | None = None) -> None:
        self.path = path
        # Read from here when this club has no save of its own yet.
        #
        # Before the server knew about more than one club there was a single
        # `runtime/club-save.json`, and on the console this was built for it
        # holds a real club -- 963 million coins, 218 cards acquired, a cup and
        # a season under way. Keying saves by persona without this would have
        # left that file on disk and started its owner from nothing.
        #
        # Nothing ever writes back to it: the first save this club makes goes
        # to `path`, and the original stays exactly as it was, which also makes
        # it the backup. On a server deployed fresh the file does not exist and
        # this never comes into play.
        self.fallback = fallback

    def adoptable(self) -> bool:
        """Whether the single-club save is still up for adoption.

        Falling back forever means every persona that ever asks inherits this
        club. Asking the live server with a made-up session id proved it:
        `LOCAL-XBOX360-FIFA14-SID-999` came back holding 960 million coins.
        Harmless on one console, wrong anywhere the point of the change is to
        reach -- so the fallback is a migration, not a rule, and it is over as
        soon as one club has a save of its own.
        """
        if self.fallback is None or not self.fallback.exists():
            return False
        clubs = self.path.parent
        if not clubs.exists():
            return True
        return not any(clubs.glob("*.json"))

    def superseded(self) -> bool:
        """Whether this is the single-club save, after the migration is over.

        The club nobody has proved a claim to must not be a real club. On the
        machine this was built on it was: an unauthenticated request came back
        holding 960 million coins, because the default club still loaded
        `runtime/club-save.json` long after its owner had moved to a
        per-persona file. That was only fixed by moving the file by hand, which
        is not a fix anybody else's deployment inherits.

        So the legacy path stops being read as soon as a club of its own exists
        on disk -- the same "migration is over" test as `adoptable`, applied to
        the other end of it.
        """
        if self.path != SAVE_FILE:
            return False
        clubs = SAVE_FILE.parent / "clubs"
        return clubs.exists() and any(clubs.glob("*.json"))

    def load(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions", tasks: "ManagerTasks | None" = None) -> bool:
        if self.superseded():
            return False
        source = self.path
        if not source.exists() and self.adoptable():
            source = self.fallback
        if not source.exists():
            return False
        try:
            saved = json.loads(source.read_text())
        except (ValueError, OSError):
            return False
        wallet.coins = int(saved.get("coins", wallet.coins))
        known = {item["id"] for item in inventory.items}
        for item in saved.get("acquired", []):
            if item["id"] not in known:
                inventory.items.append(item)
                actions.club.append(item)
        for item in saved.get("sold", []):
            inventory.items = [
                held for held in inventory.items if held["id"] != item
            ]
        # Cards the club started with that a consumable has since changed.
        # Written in place, because the squad holds the same objects and
        # replacing them would leave the squad pointing at the old ones.
        for item in saved.get("changed", []):
            if not isinstance(item, dict):
                continue
            for held in inventory.items:
                if held["id"] != item.get("id"):
                    continue
                # Only overwrite a card the saved entry actually describes.
                #
                # Item ids are handed out in catalogue order, so adding cards
                # to the catalogue shifts every id after them. A `changed`
                # entry then names an id that now belongs to a different card,
                # and overwriting in place silently replaces it. On 16 August
                # nineteen chemistry styles were added and nineteen saved kit
                # entries landed exactly on them: a fresh club held all
                # nineteen, a loaded one held none, and the picker told the
                # player none was found.
                #
                # A saved entry that disagrees with the seed about what kind of
                # card this is, is describing the old numbering. Skipping it
                # loses whatever change it recorded -- which is the smaller
                # loss, and the only one that does not corrupt the club.
                if (
                    item.get("itemType") == held.get("itemType")
                    and item.get("cardsubtypeid") == held.get("cardsubtypeid")
                ):
                    held.clear()
                    held.update(item)
                break
        known = {item["id"] for item in actions.shop.pending}
        for item in saved.get("pending", []):
            if item["id"] not in known:
                actions.shop.pending.append(item)
        if tasks is not None:
            for key, value in (saved.get("tasks") or {}).items():
                tasks.complete(int(key), int(value))
        # The saved squads are the whole set, not additions to it.
        #
        # This merged them in, so a deleted squad came back on the next launch:
        # the seed rebuilds its own, the save had no way to say "this one is
        # gone", and `delete_squad` looked like it had failed. Reported from the
        # console 17 August 2026 -- deleted in the squad selector, present again
        # after a relaunch.
        #
        # A save with no `squads` at all is left alone: that is an old save, or
        # a club that has never touched a squad, and the seed's own is right.
        saved_squads = saved.get("squads")
        if saved_squads:
            squads = inventory._squads()
            squads.clear()
            for key, value in saved_squads.items():
                squads[int(key)] = value
        current_tenant().record.adopt(saved.get("record"))
        if saved.get("activeSquad"):
            inventory.set_active(int(saved["activeSquad"]))
        if saved.get("squad"):
            inventory.set_squad([int(x) for x in saved["squad"]])
        actions.transfer = list(saved.get("transfer", []))
        actions.listings = {
            int(key): value for key, value in saved.get("listings", {}).items()
        }
        # Bring listings sold before the SOLD-pile fix up to the current shape,
        # so every sold card sits in the sold stack rather than only the ones
        # sold this session.
        actions.restamp_sold()
        # Cards saved before the alias members existed carry `id` and no
        # `itemId`, and the transfer-list screen builds no action menu for one.
        actions.restamp_cards()
        TOURNAMENT_PROGRESS.restore(saved.get("tournaments"))
        SEASON_PROGRESS.restore(saved.get("seasons"))
        CLIENT_DATA.restore(saved.get("clientData"))
        CLUB_IDENTITY.restore(saved.get("club"))
        return True

    def save(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions", tasks: "ManagerTasks | None" = None) -> None:
        # A probe run never writes the save.
        #
        # The probe replaces the club-item seed with a numbered sweep, and the
        # save is a diff against that seed: every real kit, badge, stadium and
        # ball would read as "acquired" and be written into the club
        # permanently, while the probe cards themselves would look seeded. One
        # investigative launch would have quietly rewritten a club of a
        # thousand cards.
        #
        # Read-only is the right mode for a probe anyway -- nothing it shows is
        # meant to be kept.
        if _club_item_probe():
            return

        starting = ClubInventory()
        original = {item["id"] for item in starting.items}
        current = {item["id"] for item in inventory.items}
        # A card the club started with, changed since. `acquired` cannot carry
        # it -- it was never acquired -- and `sold` cannot either, because it
        # is still owned. Without this a contract applied to a seeded player
        # was spent (the consumable is in `sold`) and the contract it bought
        # was forgotten on the next launch.
        seeded = {item["id"]: item for item in starting.items}

        # `timestamp` is excluded from the comparison, and that exclusion is
        # load-bearing.
        #
        # It is `issued_now()`, so a seeded card differs from a freshly built
        # one by its issue date **every single time**, whatever else is true of
        # it. Comparing whole items therefore put the entire seed into
        # `changed` -- 305 entries, and a save that grew from 42 KB to 575 KB.
        #
        # The damage was worse than the size. `changed` is applied by id and
        # overwrites in place, so a frozen copy of the seed shadows the real
        # one on every load: the nineteen chemistry styles added to the
        # catalogue on 16 August landed on ids the old seed already occupied,
        # and were replaced by the cards that used to hold them. A player
        # looking for a chemistry style was told none was found while nineteen
        # sat in a fresh club.
        #
        # A seeded card's issue date is rebuilt with the seed and is not worth
        # persisting. A real change -- a contract applied, a position moved --
        # differs in a member that is not the clock, and is still caught.
        def altered(item: dict, origin: dict) -> bool:
            if item == origin:
                return False
            left = {k: v for k, v in item.items() if k != "timestamp"}
            right = {k: v for k, v in origin.items() if k != "timestamp"}
            return left != right

        changed = [
            item
            for item in inventory.items
            if item["id"] in seeded and altered(item, seeded[item["id"]])
        ]
        document = {
            "coins": wallet.coins,
            "changed": changed,
            # Only what differs from the starting club, so the save stays small
            # and a change to the icebreaker packs still flows through.
            "acquired": [
                item for item in inventory.items if item["id"] not in original
            ],
            "sold": sorted(original - current),
            "pending": actions.shop.pending,
            "squad": [item["id"] for item in inventory.squad],
            "tasks": {str(k): v for k, v in tasks.completed.items()} if tasks else {},
            "record": current_tenant().record.document(),
            "activeSquad": inventory.active_squad_id(),
            "squads": {
                str(key): value for key, value in inventory._squads().items()
            },
            "transfer": actions.transfer,
            "listings": {str(key): value for key, value in actions.listings.items()},
            "tournaments": TOURNAMENT_PROGRESS.state(),
            "seasons": SEASON_PROGRESS.state(),
            "club": CLUB_IDENTITY.state(),
            "clientData": CLIENT_DATA.state(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document, separators=(",", ":")))


# The Team of the Week challenge's own configuration.
#
# `/clientdata/totw` is a **clientdata** route and its siblings all answer an
# entries document -- `clientdata/pileSize` is `{"entries":[{"key":2,...}]}`
# here and works. It is also the only Team of the Week route any console in
# these journals has ever asked for, fetched once at login, and pressing A on
# the tile fires no request at all. So everything the screen decides from is in
# this one reply.
#
# **Keys 7, 8 and 9 are the challenge's name**, packed little-endian, four
# characters to an integer. Impulsum's values decode to
#
#     1398034260 541150240 1095190860  ->  "TOTS LA LIGA"
#
# and its key 6 is 12, which is that name's length. This server was sending
# those three integers verbatim, so the entries announced a Team of the Season
# La Liga squad while the tile drew TOTW 1. The name is built from the week
# now.
#
# The rest stay as Impulsum has them and remain **opaque**: 3 is int max and 4
# is -2, which read like "no limit" and a sentinel. Nothing here has decoded
# them, and inventing values for a configuration this server does not
# understand is how the match-award screen got hung.
_TOTW_PERSONA_HIGH, _TOTW_PERSONA_LOW = _persona_halves(TOTW_PERSONA_ID)
TOTW_CHALLENGE_HEAD = [
    (1, 3),
    (2, 1),
    # The owning club's persona, high word then low.
    (3, _TOTW_PERSONA_HIGH),
    (4, _TOTW_PERSONA_LOW),
    (5, 1),
]
TOTW_CHALLENGE_TAIL = [(10, 1), (11, 3), (12, 0), (13, 1), (14, 0)]

# The name occupies keys 7, 8 and 9 -- three integers, twelve characters. It is
# padded to that width rather than shortened to fit, so the keys after it stay
# where the working build puts them; `key 6` carries the real length, so a
# shorter name reads as itself and the padding is never shown.
TOTW_NAME_KEYS = (7, 8, 9)
TOTW_NAME_WIDTH = 4 * len(TOTW_NAME_KEYS)


def _packed_name(name: str) -> list[int]:
    """A challenge name as little-endian integers, four characters each."""
    raw = name.encode("ascii", "replace")[:TOTW_NAME_WIDTH]
    raw = raw.ljust(TOTW_NAME_WIDTH, b" ")
    return [
        int.from_bytes(raw[index:index + 4], "little", signed=True)
        for index in range(0, TOTW_NAME_WIDTH, 4)
    ]


def totw_challenge_entries(catalogue: "CardCatalogue | None" = None) -> bytes:
    """`/ut/game/fifa14/clientdata/totw` -- the challenge configuration.

    **Entries alone**, which is measured rather than chosen. Three documents
    have been served on this route and the refusal names which got furthest:

        squad index only      "no Team of the Week available at the moment"
        entries only          "no Team of the Week to play"
        entries + squad       "available at the moment" again

    The middle one is past a check the other two fail, so the squad members do
    not merely fail to help here -- they push the screen backwards. What "to
    play" wants is in the remaining opaque keys, not in a bigger document.

    `catalogue` is unused and kept so the route wiring does not have to change
    again while those keys are being read.
    """
    entry = totw_week()
    name = str(entry.get("name") or "TOTW")

    pairs = list(TOTW_CHALLENGE_HEAD)
    # The **padded** width, not the name's own length.
    #
    # Key 6 is a length and keys 7-9 are the string it describes, so the two
    # have to agree or everything after them moves. Impulsum's name is "TOTS LA
    # LIGA", exactly twelve characters, with key 6 at 12 -- three integers
    # consumed and the tail starting at key 10.
    #
    # This server sent key 6 as the *real* length, 6 for "TOTW 1", while still
    # padding the name across all three integers. A client that reads six
    # characters consumes two integers and then takes key 9 -- four spaces,
    # 538976288 -- as the first value of the tail. The refusal went backwards
    # when that changed, from "no Team of the Week to play" to "no Team of the
    # Week available at the moment", which is what a shifted record looks like.
    #
    # Twelve keeps the record byte-for-byte the shape of the working build's
    # and differs only in the characters. The padding is trailing spaces, so a
    # name shorter than twelve still reads as itself.
    pairs.append((6, TOTW_NAME_WIDTH))
    pairs.extend(zip(TOTW_NAME_KEYS, _packed_name(name)))
    pairs.extend(TOTW_CHALLENGE_TAIL)

    document: dict = {
        "entries": [{"key": key, "value": value} for key, value in pairs]
    }
    # Entries **alone**, and this is measured rather than chosen.
    #
    # Three documents have been served on this route and the refusal names
    # which got furthest:
    #
    #     squad index only      "no Team of the Week available at the moment"
    #     entries only          "no Team of the Week to play"
    #     entries + squad       "available at the moment" again
    #
    # The middle one is past a check the other two fail, so the squad members
    # are not merely unnecessary here -- they push the screen backwards. What
    # is left to find is what "to play" wants, and it is in the remaining
    # opaque keys rather than in a bigger document.
    return json.dumps(document, separators=(",", ":")).encode()


def totw_challenge_response(catalogue: "CardCatalogue") -> bytes:
    """`/ut/game/fifa14/totw` -- the challenge itself, and the side it is against.

    No console has ever asked for this route: every journal here carries
    `/clientdata/totw` and nothing else. It is served anyway, because the
    reason may simply be that the client never got past the clientdata reply --
    a screen that has decided there is no Team of the Week has no reason to ask
    for one.

    `squadChallenge` is an **object** wrapping the squad, not a list of
    descriptors. The list this server used to send was invented, and its own
    comment said so.
    """
    squad = totw_hub_squad(catalogue)
    return json.dumps(
        {
            "matchDifficulty": 2,
            "grantsGameModePrizes": True,
            "squad": squad,
            "squadChallenge": {"squad": squad},
        },
        separators=(",", ":"),
    ).encode()


def totw_index_with_squad(catalogue: "CardCatalogue") -> bytes:
    """The Team of the Week list, carrying the squad itself as well.

    The screen asks clientdata/totw and then this, and rejected the plain list
    it was given -- the squad had already been served successfully, so what it
    refuses is this document. Which member it reads is not known, so the squad
    summary, the cards and an empty user list all go out together; an
    unrecognised sibling at the top level is skipped.
    """
    index = json.loads(totw_index(catalogue))
    squad = json.loads(totw_response(catalogue))
    index["itemData"] = squad["itemData"]
    index["formation"] = squad["formation"]
    index["squadName"] = squad["squadName"]
    index["squadChallenge"] = squad["squadChallenge"]
    return json.dumps(index, separators=(",", ":")).encode()


# -- manager tasks ---------------------------------------------------------
#
# The thirteen tasks on the club hub. They were answered with a fixed empty
# entries list, so nothing you completed was ever recorded: the progress bar
# stayed at 0/13 and every task reset on the next launch.

class ManagerTasks:
    """What the manager has done, kept and reloaded."""

    def __init__(self) -> None:
        self.completed: dict[int, int] = {}

    def complete(self, task: int, value: int = 1) -> None:
        self.completed[int(task)] = int(value)

    def apply(self, document: dict) -> int:
        """Take whatever the client reports and record it.

        The body arrives as an entries array of key/value pairs, the same shape
        it is served in, so a task marked done comes back as its own key.
        """
        entries = document.get("entries") if isinstance(document, dict) else None
        count = 0
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            try:
                self.complete(int(entry.get("key")), int(entry.get("value") or 1))
                count += 1
            except (TypeError, ValueError):
                continue
        return count

    def response(self) -> bytes:
        return json.dumps(
            {
                "entries": [
                    {"key": key, "value": value}
                    for key, value in sorted(self.completed.items())
                ]
            },
            separators=(",", ":"),
        ).encode()


# -- the clubs themselves --------------------------------------------------
#
# Everything one player owns, in one object, so that "which club is this
# request about" has an answer that is not a module global.
#
# What is deliberately *not* in here: the card catalogue's 14 019 parsed
# cards. They are the same file for everybody and cost 3.7 MB of JSON to
# parse, which is the difference between a server that can hold twenty clubs
# and one that cannot. `CardCatalogue` still gets built per club, because
# `served` and `sold` are that club's own -- a card bought by one player must
# not vanish from another's market -- but the card list behind them is read
# once and shared. Nothing mutates it: `self.cards` is read in exactly one
# place, a comprehension in `search`, and the drawing code builds new item
# dicts rather than editing catalogue entries.

_CARDS_LOCK = threading.Lock()
_CARDS_SHARED: dict[str, list[dict]] = {}


def shared_catalogue_cards(path: Path) -> list[dict]:
    """The parsed card list for `path`, read once and shared by every club."""
    key = str(path)
    with _CARDS_LOCK:
        cards = _CARDS_SHARED.get(key)
        if cards is None:
            cards = CardCatalogue.read_cards(path)
            _CARDS_SHARED[key] = cards
        return cards


class ClubRecord:
    """The club's won-drawn-lost, across every match it has ever played.

    Nothing kept this. `/ut/game/fifa14/club/stats/year` answered a static
    `{"entries":[]}` -- an empty list -- so the hub header read 0-0-0 over a
    club that had just won a cup. The season counters are separate and only
    move in a season; a cup match moved nothing at all.

    Reported 16 August 2026 by a player who won Cup 2 in four matches and
    found his record unchanged.
    """

    __slots__ = ("won", "draw", "lost")

    def __init__(self) -> None:
        self.won = 0
        self.draw = 0
        self.lost = 0

    @property
    def played(self) -> int:
        return self.won + self.draw + self.lost

    def settle(self, result: str) -> None:
        """One finished match. A result nobody recognises moves nothing."""
        if result == "WIN":
            self.won += 1
        elif result == "DRAW":
            self.draw += 1
        elif result == "LOSS":
            self.lost += 1

    def document(self) -> dict:
        return {"won": self.won, "draw": self.draw, "lost": self.lost}

    def adopt(self, saved: object) -> None:
        if not isinstance(saved, dict):
            return
        for member in ("won", "draw", "lost"):
            try:
                setattr(self, member, max(0, int(saved.get(member) or 0)))
            except (TypeError, ValueError):
                continue


def club_year_response(record: "ClubRecord") -> bytes:
    """What the hub header reads for the club's record.

    The slot numbers are a **reading, not a certainty** -- the same caveat
    `club_stats_response` carries for its own counters, and for the same
    reason: the screen displays them, no document names them. Played, won,
    drawn, lost in that order is the arrangement to check first.

    One look at the header settles it. If the numbers land in the wrong boxes
    the mapping moves; nothing here writes to a card, so a wrong slot costs a
    glance rather than a club.
    """
    return json.dumps(
        {
            "entries": [
                {"key": 0, "value": record.played},
                {"key": 1, "value": record.won},
                {"key": 2, "value": record.draw},
                {"key": 3, "value": record.lost},
            ],
            # And the same three by name, which beats guessing a slot.
            #
            # `won` (0x02F9D4), `draw` (0x030E54) and `loss` (0x0308D8) are all
            # in CardsDLL's own JSON name table, read off the module on
            # 16 August 2026 -- so these are members the parser can resolve,
            # unlike the key/value arrangement above, which is recovered from
            # what the screen displays. `gamesPlayed` (0x030BE0) is the same
            # member a player card uses for appearances.
            #
            # Both shapes go out together on purpose: an unrecognised sibling
            # at the top level is skipped, so the one the header does not read
            # costs nothing, and whichever it does read is right.
            "won": record.won,
            "draw": record.draw,
            "loss": record.lost,
            "gamesPlayed": record.played,
        },
        separators=(",", ":"),
    ).encode()


class Tenant:
    """One player's club, and everything that belongs to it alone."""

    def __init__(self, persona_id: int = 0, save: "ClubSave | None" = None) -> None:
        self.persona_id = int(persona_id or 0)
        # Two requests from the same console arrive on two threads -- the
        # client closes the connection after each one -- and both of them can
        # save. This serialises the writes for one club without making two
        # clubs wait for each other.
        self.lock = threading.RLock()
        self.persona = Persona()
        self.persona.adopt(self.persona_id)
        self.identity = ClubIdentity()
        self.inventory = ClubInventory()
        self.catalogue = CardCatalogue()
        self.wallet = Wallet()
        self.shop = PackShop(self.catalogue, self.wallet, self.inventory)
        self.actions = CardActions(self.shop, self.wallet, self.inventory)
        self.rack = ConsumableRack(self.inventory)
        self.tasks = ManagerTasks()
        self.tournaments = TournamentProgress()
        self.seasons = SeasonProgress()
        self.record = ClubRecord()
        # The cup, and the season, a match in flight belongs to. Read when the
        # match ends and written when it is created, so neither can be a local.
        self.active_tournament: int | None = None
        self.active_season: tuple[int, int] | None = None
        self.save = save if save is not None else ClubSave(
            club_save_path(self.persona_id),
            fallback=None if not self.persona_id else SAVE_FILE,
        )
        self.loaded = False
        self.granted = 0
        # Loading reaches TOURNAMENT_PROGRESS, SEASON_PROGRESS and
        # CLUB_IDENTITY through the views above, and a view answers with
        # whichever club the thread is currently serving. Without this bind a
        # club restores its cups into the club that happened to be bound
        # already -- silently, and only when a second player connects.
        previous = getattr(_CURRENT, "tenant", None)
        _CURRENT.tenant = self
        try:
            self._open()
        finally:
            _CURRENT.tenant = previous

    def _open(self) -> None:
        """Restore the club, or seed a brand new one.

        This is the block that used to sit at the top of the server module,
        moved here unchanged in behaviour: a club that is not a first run
        loads its save and is called `Fondateur FUT`, and a first run opens
        three starter packs instead.
        """
        if not first_run():
            self.loaded = self.save.load(
                self.inventory, self.wallet, self.actions, self.tasks
            )
            # A club that has been created has a name. Saying nothing here
            # tells the client no club exists, which is what the first-run
            # flag is for -- so outside first-run mode the name is set.
            #
            # Only when the save did not carry one. This line used to run
            # unconditionally, immediately after `save.load` had restored the
            # saved name, so every club was called `Fondateur FUT` again on the
            # next launch however it had been renamed.
            #
            # The abbreviation is what gave it away: `PUT /user/club` carries
            # both, `adopt` took both, the save held both -- and a club renamed
            # to "Classic XI"/"CXI" on 25 August came back as "Fondateur
            # FUT"/"CXI". The half that reverted was the half this line
            # writes.
            if not self.identity.name:
                self.identity.name = CLUB_NAME_DEFAULT
            # Said out loud when a club opens. A cup run that was in the save
            # one evening and gone the next launch left nothing to look at
            # afterwards. With more than one club it also says which one.
            print(
                f"club {self.persona_id}: loaded={self.loaded} "
                f"coins={self.wallet.coins} "
                f"cups={self.tournaments.state()!r:.120} "
                f"resumable={self.tournaments.active_ids()} "
                f"save={self.save.path}",
                flush=True,
            )
        else:
            self.granted = self.shop.grant_starter_packs()
            print(
                f"club {self.persona_id}: first run, {self.granted} cards "
                f"from {len(STARTER_PACKS)} starter packs",
                flush=True,
            )

    def __repr__(self) -> str:
        return (
            f"<Tenant persona={self.persona_id} coins={self.wallet.coins} "
            f"cards={len(self.inventory.items)} save={self.save.path.name}>"
        )


class TenantRegistry:
    """Every club this server is holding, keyed by nucleus id."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clubs: dict[int, Tenant] = {}

    def get(self, persona_id: int | None) -> Tenant:
        """The club for this persona, opened on first sight."""
        key = int(persona_id or 0)
        with self._lock:
            club = self._clubs.get(key)
            if club is None:
                club = Tenant(key)
                self._clubs[key] = club
            return club

    def default(self) -> Tenant:
        """The club a thread that has not identified itself gets."""
        return self.get(0)

    def known(self) -> list[int]:
        with self._lock:
            return sorted(self._clubs)

    def forget(self, persona_id: int | None = None) -> None:
        """Drop one club, or all of them. For tests and for a reset."""
        with self._lock:
            if persona_id is None:
                self._clubs.clear()
            else:
                self._clubs.pop(int(persona_id or 0), None)
        use_tenant(None)


TENANTS = TenantRegistry()
