#!/usr/bin/env python3
"""Pull the FIFA 14 card list from wefut's player-database endpoint.

The club inventory was built from the four icebreaker packs this build ships:
92 cards, which is enough to field a side and nothing like enough for a
transfer market, a pack, or a club worth browsing. The game's own
`cards_ng_db.db` would be the right source and still does not decode -- its
chunks report an LZX block type of 7 at the first symbol, and every variant
tried so far produces structurally valid noise.

wefut's ids turn out to be the game's ids, which is what makes this usable at
all. Messi comes back as `base-id 158023` with `club-id 241`, exactly the asset
id and team id the icebreaker fixture carries for him, and columns 13-18 hold
`92 89 84 96 44 69` -- the same six attributes, in the same order. An asset id
that did not match would draw a blank card, so this correspondence is the whole
reason to trust the data.

The page drives a DataTables grid at `/ajax/getPlayers/14`, paged with
`iDisplayStart` and `iDisplayLength`. One request per hundred cards, spaced, so
a full pass is a few minutes rather than a hammering.

    python3 tools/fetch_wefut_cards.py --out server/fifa14_cards.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


ENDPOINT = "https://wefut.com/ajax/getPlayers/14"
REFERER = "https://wefut.com/player-database/14"
AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Verified against the icebreaker fixture: Messi reads 92 89 84 96 44 69 in
# both, in this order.
ATTRIBUTE_COLUMNS = [str(index) for index in range(13, 19)]

FIELDS = {
    "first_name": "1",
    "last_name": "2",
    "rating": "4",
    "position": "8",
    "foot": "9",
    "club": "10",
    "league": "11",
    "nation": "12",
    "rarity": "72",
}

CARD_IDS = re.compile(r'data-([a-z-]+)="([^"]*)"')


def fetch_page(start: int, length: int, timeout: float) -> dict:
    query = f"?sEcho=1&iDisplayStart={start}&iDisplayLength={length}"
    request = urllib.request.Request(
        ENDPOINT + query,
        headers={
            "User-Agent": AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": REFERER,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


# wefut's own per-card id, out of `player_url` -- "player/14/15337/neymar".
#
# This is the only thing in a row that is unique to a **card**. `base-id` is the
# player, and `rareflag` is the family, and a player can hold several cards in
# one family: Neymar has three iMOTMs (15337 at 88, 15376 at 89, and one at 90),
# and a player transferred mid-season has one card per club (Jermaine Jones at
# New England and at Schalke, both Non-Rare Gold).
CARD_URL_ID = re.compile(r"player/\d+/(\d+)/")


def parse_row(row: dict) -> dict | None:
    ids = dict(CARD_IDS.findall(row.get("player_card") or ""))
    asset_id = ids.get("base-id")
    if not asset_id or not asset_id.isdigit() or int(asset_id) == 0:
        # No asset id means no card art, which makes the record useless here
        # however complete the rest of it looks.
        #
        # Zero counts as no asset id. `"0".isdigit()` is True, so two rows with
        # `data-base-id="0"` came through -- no name, rating 0, all attributes
        # zero -- and one of them has been sitting in the shipped catalogue
        # drawing as a blank card.
        return None

    def number(value: str | None) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    first = (row.get(FIELDS["first_name"]) or "").strip()
    last = (row.get(FIELDS["last_name"]) or "").strip()
    card_id = CARD_URL_ID.search(row.get("player_url") or "")
    return {
        # Server-side only. Nothing puts a catalogue row on the wire -- every
        # field is read by name into `_player_item` -- so this cannot reach
        # CardsDLL's parser, which is the rule an extra descriptive member on a
        # pack consumable broke once before. See docs/DUPLICATES.md.
        "cardId": int(card_id.group(1)) if card_id else 0,
        "assetId": int(asset_id),
        "name": " ".join(part for part in (first, last) if part),
        "rating": number(row.get(FIELDS["rating"])),
        "position": (row.get(FIELDS["position"]) or "").strip(),
        "foot": (row.get(FIELDS["foot"]) or "").strip(),
        "club": (row.get(FIELDS["club"]) or "").strip(),
        "league": (row.get(FIELDS["league"]) or "").strip(),
        "nation": (row.get(FIELDS["nation"]) or "").strip(),
        "clubId": number(ids.get("club-id")),
        "leagueId": number(ids.get("league-id")),
        "nationId": number(ids.get("nation-id")),
        "rareflag": number(ids.get("rareflag")),
        "rarity": (row.get(FIELDS["rarity"]) or "").strip(),
        "attributes": [number(row.get(column)) for column in ATTRIBUTE_COLUMNS],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-pages", type=int, default=400)
    args = parser.parse_args()

    cards: dict[object, dict] = {}
    start = 0
    for page in range(args.max_pages):
        try:
            payload = fetch_page(start, args.page_size, args.timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            # One bad page should not throw away everything collected so far.
            print(f"page at {start} failed: {error}", flush=True)
            time.sleep(args.pause * 4)
            continue

        rows = payload.get("aaData") or []
        if not rows:
            break

        for row in rows:
            card = parse_row(row)
            if card is None:
                continue
            # Keyed on wefut's own card id, which is the only value unique to
            # a card.
            #
            # This was `(assetId, rareflag)`, on the reasoning that a player
            # appears once per version and rarity keeps the versions apart. It
            # does not: a player can hold several cards of one rarity, and each
            # one overwrote the last. The catalogue came out with exactly one
            # Team of the Week per player across 768 of them, one iMOTM across
            # 60, and one card for a player who moved club mid-season -- which
            # is not a season, it is a de-duplication.
            #
            # Neymar is the case that found it: three iMOTMs on wefut, one here.
            key = card["cardId"] or (card["assetId"], card["rareflag"], card["rating"],
                                     card["club"], tuple(card["attributes"]))
            cards[key] = card

        print(f"{start + len(rows):>6} rows, {len(cards):>6} cards", flush=True)
        if len(rows) < args.page_size:
            break
        start += args.page_size
        time.sleep(args.pause)

    # wefut lists some cards twice, and a second copy is not a second card.
    #
    # 123 pairs in the 21 August scrape were identical in every field read
    # here -- same asset, rarity, rating, club, position, foot, nation and all
    # six attributes -- and differed only in the card id. 120 of them carried
    # *consecutive* ids (16118/16119, 15978/15979), which is one row emitted
    # twice rather than two cards; they sit in a single block around
    # 15,900-16,350 and are all rated 74 or below.
    #
    # Collapsing on full content is the opposite of the fault this file had.
    # That one keyed on `(assetId, rareflag)`, which is far coarser than a card
    # and threw away real ones. Two rows agreeing on every field this parser
    # reads are indistinguishable to the server by construction: whichever is
    # kept, nothing downstream could tell.
    #
    # The lowest card id wins, so the survivor is the one listed first and the
    # choice does not move between scrapes.
    unique: dict[tuple, dict] = {}
    for card in sorted(cards.values(), key=lambda c: c.get("cardId") or 0):
        content = tuple(
            tuple(value) if isinstance(value, list) else value
            for key, value in sorted(card.items())
            if key != "cardId"
        )
        unique.setdefault(content, card)
    collapsed = len(cards) - len(unique)
    if collapsed:
        print(f"collapsed {collapsed} duplicate listing(s)", flush=True)

    ordered = sorted(unique.values(), key=lambda card: (-card["rating"], card["name"]))
    args.out.write_text(json.dumps({"cards": ordered}, separators=(",", ":")))
    print(f"wrote {len(ordered)} cards to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
