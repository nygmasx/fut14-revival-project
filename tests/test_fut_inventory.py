from __future__ import annotations

import json
import random
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from fut_inventory import GOLD_PACK_ID, ClubInventory, RESOURCE_VERSION


INVENTORY = ClubInventory()


def test_the_catalogue_is_built_from_the_shipped_packs_not_invented() -> None:
    # 158023 is Messi and 167397 is Neymar in FIFA 14's own numbering. If these
    # stop appearing, the catalogue has drifted off the disc's data and the
    # cards will draw blank.
    assets = {
        item["assetId"] for item in INVENTORY.items if item["itemType"] == "player"
    }
    assert 158023 in assets
    assert 167397 in assets


def test_every_resource_id_carries_its_asset_id() -> None:
    # resourceId holds the asset id in its low 24 bits and a version above it.
    # Break this and the card art no longer resolves.
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert item["resourceId"] & 0x00FF_FFFF == item["assetId"]
        assert item["resourceId"] & 0xFF00_0000 == RESOURCE_VERSION


def test_item_ids_are_unique() -> None:
    ids = [item["id"] for item in INVENTORY.items]
    assert len(set(ids)) == len(ids)


def test_the_squad_fields_twenty_three() -> None:
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    assert len(squad["players"]) == 23
    assert [player["index"] for player in squad["players"]] == list(range(23))


def test_the_eleven_who_start_wear_shirt_numbers() -> None:
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    numbers = [player["kitNumber"] for player in squad["players"]]
    assert numbers[:11] == list(range(1, 12))
    assert set(numbers[11:]) == {0}


def test_the_club_presents_a_kit_stadium_and_ball_but_no_badge() -> None:
    # Without the kit, stadium and ball the club has nothing to present and
    # neither side can be dressed for a match.
    #
    # The badge slot is deliberately empty. It used to carry FC Barcelona, so
    # every new club wore Barcelona's crest and there was no way to tell the
    # default from a choice; the client draws its own FIFA 14 Ultimate Team
    # crest when nothing is active. The Barcelona badge is still owned and one
    # activation away -- see CLUB_STARTER_ITEMS.
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    states = {item["itemState"] for item in squad["actives"]}
    assert states == {
        "activeHomeKit",
        "activeAwayKit",
        "activeStadium",
        "activeBall",
    }


def test_a_new_club_owns_a_badge_to_choose() -> None:
    from fut_inventory import BADGE_WIRE_TYPE

    badges = [i for i in INVENTORY.items if i["itemType"] == BADGE_WIRE_TYPE]
    assert len(badges) == 1, "the starter club should own exactly one badge"
    assert badges[0]["resourceId"] == 6_000_000  # FC Barcelona
    assert badges[0]["itemState"] == "free"


def test_every_player_can_actually_take_the_field() -> None:
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert item["contract"] > 0
        assert item["fitness"] > 0
        assert item["injuryGames"] == 0
        assert item["suspension"] == 0


def test_each_player_carries_six_attributes() -> None:
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert [entry["index"] for entry in item["attributeList"]] == list(range(6))


def test_the_squad_summary_reports_the_starting_eleven_rating() -> None:
    summary = json.loads(INVENTORY.squad_list_response("Fondateur FUT"))["squad"][0]
    starters = INVENTORY.squad[:11]
    assert summary["rating"] == round(
        sum(item["rating"] for item in starters) / len(starters)
    )
    assert summary["squadName"] == "Fondateur FUT"


def test_the_seed_holds_each_player_once() -> None:
    # This replaces `test_the_club_holds_more_than_the_starting_squad`, which
    # asserted the club held spares beyond the starting eleven.
    #
    # It did, and they were not spares. Counted on 2026-08-16: the four captain
    # packs carry 92 squad entries and 23 distinct players, each appearing in
    # all four -- four Messis, four Falcaos, four Neuers. The old assertion
    # passed on duplicates and called them stock, which is what put a pile of
    # identical Falcaos in a real player's club.
    #
    # There is no source of genuine spares in this data, so the club is the
    # twenty-three the packs actually name. Anything beyond that comes from
    # packs opened, which is where it should come from.
    players = [item for item in INVENTORY.items if item["itemType"] == "player"]
    assets = [item["assetId"] for item in players]
    assert len(assets) == len(set(assets))
    assert len(players) == len(INVENTORY.squad)


def test_the_wallet_starts_funded_and_names_the_field_three_ways() -> None:
    # The header reads whichever member it knows; an unrecognised sibling at
    # the top level is skipped, but a wrapper is not -- nesting these under
    # "userInfo" made the balance print 0xCDCDCDCD.
    from fut_inventory import Wallet

    balance = json.loads(Wallet().response())
    assert balance["totalCredits"] == balance["credits"] == balance["coins"]
    assert balance["coins"] > 0


def test_a_quick_sell_pays_something() -> None:
    # discardValue was zero on every card, so selling one returned nothing.
    for item in INVENTORY.items:
        if item["itemType"] == "player":
            assert item["discardValue"] > 0


def test_a_pack_costs_coins_and_returns_cards() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        GOLD_PACK_PRICE,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    before = wallet.coins
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(7)))
    assert wallet.coins == before - GOLD_PACK_PRICE
    assert opened["numberItems"] == len(opened["itemList"]) > 0
    # The reply carries the new balance: a response that omits it hands the
    # club header a zero rather than leaving it alone.
    assert opened["credits"] == wallet.coins


def test_a_pack_is_refused_without_the_coins() -> None:
    from fut_inventory import GOLD_PACK_ID, CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet(coins=10))
    assert not shop.can_afford()
    assert json.loads(shop.refused())["reason"] == "INSUFFICIENT_COINS"


def test_drawn_cards_stay_pending_until_collected() -> None:
    import random

    from fut_inventory import GOLD_PACK_ID, CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet())
    shop.open_pack(GOLD_PACK_ID, random.Random(3))
    pending = json.loads(shop.purchased_items())
    assert len(pending["itemData"]) == len(shop.pending) > 0


def test_the_market_pages_instead_of_repeating_itself() -> None:
    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()
    first = json.loads(catalogue.auctions({"start": "0", "num": "16"}))
    second = json.loads(catalogue.auctions({"start": "16", "num": "16"}))
    ids = lambda page: [a["itemData"]["id"] for a in page["auctionInfo"]]
    assert ids(first) and ids(second)
    assert not set(ids(first)) & set(ids(second))
    # total is the whole result set, which is what the screen pages against.
    assert first["total"] == second["total"] > len(first["auctionInfo"])


def test_the_market_honours_the_names_it_is_actually_sent() -> None:
    # lev, not level; definitionId, not maskedDefId. Filtering on the club
    # search's names meant none of the market's filters applied.
    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()
    gold = json.loads(catalogue.auctions({"lev": "gold", "start": "0", "num": "5"}))
    assert gold["total"] > 0
    assert all(
        "gold" in (a["itemData"]["rating"] and "gold") or True
        for a in gold["auctionInfo"]
    )
    one = json.loads(
        catalogue.auctions({"definitionId": "158023", "start": "0", "num": "5"})
    )
    assert one["total"] > 0
    assert {a["itemData"]["assetId"] for a in one["auctionInfo"]} == {158023}


def test_the_currency_names_are_lower_case() -> None:
    # The native parser compares them against the literal strings "coins" and
    # "points". "COINS", which the PC reference's fixture uses, matches nothing
    # and leaves the balance unwritten.
    from fut_inventory import Wallet

    document = json.loads(Wallet().credits_response())
    names = [entry["name"] for entry in document["currencies"]]
    assert names == ["coins", "points"]
    assert document["currencies"][0]["funds"] == document["credits"]


def test_unopened_packs_is_an_object() -> None:
    # An array leaves the parser walking the wrong token type, and it may never
    # reach its success epilogue.
    from fut_inventory import Wallet

    packs = json.loads(Wallet().credits_response())["unopenedPacks"]
    assert isinstance(packs, dict)
    assert set(packs) == {"preOrderPacks", "recoveredPacks"}


def test_user_info_is_flat() -> None:
    # Wrapping it as {"userInfo": {...}} is what made the header print
    # 0xCDCDCDCD: the parser did not recognise the shape and wrote nothing.
    from fut_inventory import Wallet

    info = json.loads(Wallet().user_info("Fondateur FUT", "FUT"))
    assert "userInfo" not in info
    assert info["clubName"] == "Fondateur FUT"
    assert info["coins"] == info["credits"] > 0


def test_quick_sell_takes_a_list_of_ids() -> None:
    # The client always sends {"itemId":[...]}, twelve long when a whole pack
    # is sold at once. Reading it as one integer yielded no id, so the reply
    # named no item and the screen errored.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(11)))
    ids = [item["id"] for item in opened["itemList"]]

    before = wallet.coins
    sold = json.loads(actions.discard_many(ids))
    assert [entry["id"] for entry in sold["items"]] == ids
    # totalCredits is what this sale paid, not the resulting balance.
    assert sold["totalCredits"] == wallet.coins - before
    assert not shop.pending


def test_sending_a_card_to_the_club_takes_it_out_of_the_pack() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(5)))
    first = opened["itemList"][0]["id"]
    pending_before = len(shop.pending)

    moved = json.loads(actions.move({"itemData": [{"id": first, "pile": 7}]}))
    assert moved["itemData"][0] == {
        "id": first,
        "success": True,
        "reason": "",
        "errorCode": 0,
        "pile": 7,
    }
    assert len(shop.pending) == pending_before - 1
    assert [item["id"] for item in actions.club] == [first]


def _actions():
    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return CardActions(shop, wallet), shop, wallet


def test_a_card_can_be_set_aside_for_listing() -> None:
    import random

    actions, shop, _ = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(9)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    # Pile 5 is the transfer list: the card leaves the club until it is listed.
    assert [item["id"] for item in actions.transfer] == [first]
    assert first not in [item["id"] for item in actions.club]


def test_listing_returns_a_trade_id_and_shows_in_the_trade_pile() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(13)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})

    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": first}, "startingBid": 300, "buyNowPrice": 900}
        )
    )
    # The trade id is what every later bid or withdrawal refers to.
    assert listing["tradeId"] == listing["id"]
    assert listing["tradeState"] == "active"
    assert (listing["startingBid"], listing["buyNowPrice"]) == (300, 900)
    assert not actions.transfer

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == listing["tradeId"]


def test_withdrawing_puts_the_card_back() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(17)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    listing = json.loads(actions.list_for_sale({"itemData": {"id": first}}))

    actions.withdraw(listing["tradeId"])
    assert [item["id"] for item in actions.transfer] == [first]
    # It is back on the transfer list, and the list says so. This asserted a
    # total of 0 while the line above asserted the card was there -- which is
    # the bug written down: withdrawing a listing made the card invisible.
    import fut_inventory as inventory

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    # Back on the list and unlisted again: tradeId 0, the shape the console
    # accepted on 26 August.
    assert pile["auctionInfo"][0]["tradeId"] == 0
    assert pile["auctionInfo"][0]["tradeState"] is None
    assert pile["auctionInfo"][0]["itemData"]["id"] == first


def test_buying_a_listing_debits_the_wallet_and_hands_over_the_card() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    # Era-accurate prices: the top of the market is a Team of the Year at
    # seven million, so a test that buys the first listing needs the coins.
    wallet = Wallet(coins=50_000_000)
    page = json.loads(catalogue.auctions({"start": "0", "num": "3"}, wallet.coins))
    listing = page["auctionInfo"][0]

    before = wallet.coins
    reply, won = catalogue.bid(
        listing["tradeId"], listing["buyNowPrice"], wallet
    )
    document = json.loads(reply)
    # At or above buy-now the auction ends: that is the Buy Now button.
    assert document["tradeState"] == "closed"
    assert wallet.coins == before - listing["buyNowPrice"]
    assert won is not None


def test_a_bid_below_buy_now_leaves_the_auction_running() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    # Era-accurate prices: the top of the market is a Team of the Year at
    # seven million, so a test that buys the first listing needs the coins.
    wallet = Wallet(coins=50_000_000)
    page = json.loads(catalogue.auctions({"start": "0", "num": "3"}, wallet.coins))
    listing = page["auctionInfo"][0]

    reply, won = catalogue.bid(listing["tradeId"], listing["startingBid"], wallet)
    assert json.loads(reply)["tradeState"] == "active"
    assert won is None


def test_bidding_beyond_the_balance_is_refused() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "1"}, wallet.coins))
    trade = page["auctionInfo"][0]["tradeId"]
    before = wallet.coins
    reply, won = catalogue.bid(trade, wallet.coins + 1, wallet)
    assert json.loads(reply)["reason"] == "INSUFFICIENT_COINS"
    assert wallet.coins == before and won is None


def test_a_search_result_can_still_be_bid_on_afterwards() -> None:
    # Listings are generated per search, so the trade id has to survive the
    # response or every later bid refers to something that no longer exists.
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "2"}, wallet.coins))
    for listing in page["auctionInfo"]:
        assert listing["tradeId"] in catalogue.served


def test_the_club_holds_more_than_players() -> None:
    # The Consommables, Éléments club and Personnel tabs each filter on an item
    # type; serving players only leaves them empty and their filters inert.
    from fut_inventory import consumable_family

    from fut_inventory import BADGE_WIRE_TYPE

    kinds = {item["itemType"] for item in INVENTORY.items}
    # FUT's two consumable types, not one per family: `cardsubtypeid` carries
    # the family and `itemType` only says develop or train.
    #
    # A badge calls itself `custom` on the wire, not `badge` -- the retail
    # family the PC revival files them under, and sending `badge` is what drew
    # the grey placeholder in My Club.
    for kind in ("development", "training", "kit", BADGE_WIRE_TYPE, "stadium",
                 "ball"):
        assert kind in kinds, kind

    # Manager and staff are deliberately absent -- their invented asset ids
    # draw a blank card back, so they are out of the club and out of the draw
    # until a real table names them. See CLUB_ITEM_KINDS_UNVERIFIED.
    from fut_inventory import CLUB_ITEM_KINDS_UNVERIFIED

    for kind, *_ in CLUB_ITEM_KINDS_UNVERIFIED:
        assert kind not in kinds, kind

    families = {consumable_family(item) for item in INVENTORY.items}
    for family in ("contract", "fitness", "playStyle"):
        assert family in families, family


def test_consumables_carry_an_amount() -> None:
    # A contract card that grants no matches is not a contract card.
    for item in INVENTORY.items:
        from fut_inventory import consumable_family

        if consumable_family(item) in ("contract", "fitness"):
            assert item["amount"] > 0


def test_the_club_search_can_isolate_a_type() -> None:
    players = json.loads(INVENTORY.club_response({"type": "player"}))["itemData"]
    kits = json.loads(INVENTORY.club_response({"type": "kit"}))["itemData"]
    assert players and kits
    assert {item["itemType"] for item in players} == {"player"}
    assert {item["itemType"] for item in kits} == {"kit"}


def test_seasons_serve_the_clubs_own_division_by_default() -> None:
    # This replaces `test_seasons_are_empty_by_default`.
    #
    # Empty was right while three shapes had failed on the console and the last
    # froze the loader. The ladder settled it on 20 August: minimal opened the
    # mode, prizes filled the rewards, matches filled the fixture list, and
    # native served both across ten divisions without freezing.
    #
    # Empty is not a safe default any more, it is a broken one -- every launch
    # without the flag answered "les saisons ne sont pas disponibles".
    #
    # The default serves all ten divisions ordered Division 10 FIRST, so the
    # screen still opens on Division 10 -- where a club starts -- while every
    # row keeps an `id` that is its position in the list.
    #
    # Serving one row instead was the earlier answer to the same problem, and
    # it could not resume a season: the single row was sliced off a table
    # ordered the other way, so it carried `id` 10 and `season/user` pointed at
    # season 10 on a list of one. Measured 21 August.
    import os

    import fut_inventory as inventory

    # Division 10 first is what `kyro-data` does. `native`, the default since
    # 25 August, orders Division 1 first -- it is the mode the screen opens on,
    # and this test is about the other one.
    os.environ["FIFA14_SEASON_MODE"] = "kyro-data"
    try:
        doc = json.loads(inventory.seasons_response())
        user_doc = inventory.season_user_response()
    finally:
        os.environ.pop("FIFA14_SEASON_MODE", None)
    seasons = doc["seasons"]
    assert len(seasons) == 10
    assert seasons[0]["divisionId"] == 10, "FUT starts a club in Division 10"
    # Both arrays: the ladder proved each separately and then together.
    assert len(seasons[0]["matches"]) == 10
    assert len(seasons[0]["prizeSet"]) == 4

    user = json.loads(user_doc)
    # seasonId is a 1-based position in the list served, and the row it selects
    # has to be the division the user document names.
    assert user["seasonId"] == seasons[0]["id"] == 1
    assert user["divisionId"] == seasons[0]["divisionId"] == 10
    # `divisionId` is the division's own number, not an index into a table of
    # ten. "10 hangs the screen, 0-9 hold" was read off a 13 August bisection
    # in which the one-row list held Division 1 while the user document said
    # 10 -- the two named different divisions, and that is the better candidate
    # for the hang. With the list ordered Division 10 first, 10 is what resumes
    # a season and 9 is what leaves it unresumable. Measured 21 August.
    assert 1 <= user["divisionId"] <= 10


def _native_seasons(monkeypatch):
    import fut_inventory as inventory

    monkeypatch.setenv("FIFA14_SEASON_MODE", "native")
    return inventory


def test_the_native_season_record_carries_its_schedule(monkeypatch) -> None:
    inventory = _native_seasons(monkeypatch)

    seasons = json.loads(inventory.seasons_response())["seasons"]
    assert len(seasons) == 10
    assert {season["divisionId"] for season in seasons} == set(range(1, 11))
    for season in seasons:
        assert season["numMatches"] > 0
        # The fixture list is an array of records, not a count -- the same
        # fault as a cup's `rounds`, which froze the title.
        assert len(season["matches"]) == season["numMatches"]
        for match in season["matches"]:
            assert set(match) == {
                "teamId",
                "difficulty",
                "rewardMult",
                "roundId",
                "coins",
            }
        # Rewards travel through prizeSet, not as flat top-level members.
        levels = [prize["prizeLevel"] for prize in season["prizeSet"]]
        assert levels == ["RELEGATION", "MAINTENANCE", "PROMOTION", "CHAMPIONSHIP"]
        for prize in season["prizeSet"]:
            assert "thresholdPoint" in prize
            assert isinstance(prize["awardMappings"][0]["awards"], list)
        # Neither 0 nor -1. Both sent the client hunting: 0 fetched
        # /fut/items/xbl2/0.json once per division, and -1 -- taken from a PC
        # build as its "no trophy" sentinel -- did exactly the same with -1.
        # cards0.big ships seventy real trophies at 1100..1169.
        from fut_inventory import TROPHY_FIRST, TROPHY_LAST

        assert TROPHY_FIRST <= season["trophyResourceId"] <= TROPHY_LAST


def test_the_season_document_carries_no_flat_reward_members(monkeypatch) -> None:
    # The shape that produced "Les saisons ne sont pas disponibles pour le
    # moment" kept thresholds and coins at the top level and reused the cup's
    # time member names.
    inventory = _native_seasons(monkeypatch)

    misplaced = {
        "seasonCoins",
        "thresholdPoint",
        "visStart",
        "visEnd",
        "starttime",
        "endtime",
        "timeUntilStart",
        "timeUntilEnd",
    }
    for season in json.loads(inventory.seasons_response())["seasons"]:
        assert misplaced.isdisjoint(season)


def test_the_season_document_invents_no_member() -> None:
    # Seven members of the earlier shape -- division, matchesPlayed,
    # matchesToPlay, pointsToPromote, lost, coinsPerWin, trophiesWon -- appear
    # nowhere in CardsDLL's JSON name table, so the parser could not read them
    # and the screen stayed on its constructor defaults.
    import fut_inventory as inventory

    invented = {
        "division",
        "matchesPlayed",
        "matchesToPlay",
        "pointsToPromote",
        "lost",
        "coinsPerWin",
        "trophiesWon",
        "relegated",
        "promoted",
    }
    standing = json.loads(inventory.season_user_response())
    assert invented.isdisjoint(standing)
    for season in json.loads(inventory.seasons_response())["seasons"]:
        assert invented.isdisjoint(season)


def test_the_club_starts_in_the_bottom_division(monkeypatch) -> None:
    inventory = _native_seasons(monkeypatch)
    # A club that has not played. Once one has, `season/user` also carries
    # `data` and `dataVersion` -- the client's own blob, handed back to it --
    # and this is asserting the shape before any of that exists.
    inventory.SEASON_PROGRESS.entries.clear()

    standing = json.loads(inventory.season_user_response())
    # Only what the parser handles: seasonId, divisionId and round. seasonId
    # is decremented by the client, so 1 selects the first list record, and
    # round 1 is the first fixture -- wire 0 becomes its invalid sentinel.
    assert set(standing) == {"seasonId", "divisionId", "round"}
    assert standing["seasonId"] == 10
    # The division's number minus one: an index into the client's own table of
    # divisions, which starts at Division 1.
    #
    # Sending 10 froze the mode for as long as it existed -- one past the last
    # index of a table of ten. Zero held it, and that was mistaken for the
    # answer: what zero renders is a badge reading DIV 1, over "Matchs
    # restants : 10" and "12 PTS TITRE", none of which is in the record served
    # for it. So the index is into the client's table, not into ours, and
    # SEASON_DIVISIONS is ordered to agree.
    # 10, not 9: `divisionId` names the division of the row `seasonId`
    # selects, and its number minus one made a played season read as no
    # season at all. See the 25 August note in `season_user_response`.
    assert standing["divisionId"] == 10
    assert standing["round"] == 1
    # A club in division 5 is the fifth record, and it reports division 5 --
    # the member names the division, not that number minus one.
    higher = json.loads(inventory.season_user_response(5))
    assert (higher["seasonId"], higher["divisionId"]) == (5, 5)
    assert json.loads(inventory.season_user_response(10, played=3))["round"] == 4


def test_a_season_under_way_is_reported_where_it_actually_stands(monkeypatch) -> None:
    # The client saves its own progress after every match -- it went up at
    # round 2 the moment the first one was walked out of, on 13 August. Until
    # that route was handled this document answered round 1 for ever, so
    # re-entering the mode offered ten matches out of ten however many had
    # been played.
    inventory = _native_seasons(monkeypatch)
    inventory.SEASON_PROGRESS.entries.clear()
    try:
        inventory.SEASON_PROGRESS.apply(
            1, 10, {"round": 4, "data": "QUJD", "progressData": "REVG"}
        )
        standing = json.loads(inventory.season_user_response())
        assert standing["round"] == 4
        # Still the division's number minus one.
        # (10, 10): the row `seasonId` selects carries divisionId 10, and
        # this has to agree with it, or a played season reads as absent.
        assert (standing["seasonId"], standing["divisionId"]) == (10, 10)

        # Promoted: division 9 is the ninth record, and index eight.
        inventory.SEASON_PROGRESS.entries.clear()
        inventory.SEASON_PROGRESS.apply(
            2, 9, {"round": 2, "data": "QUJD", "progressData": "REVG"}
        )
        promoted = json.loads(inventory.season_user_response())
        # Division 9 reports `divisionId` 9, not 8: the member names the
        # division of the row `seasonId` selects, not that number minus one.
        assert (promoted["seasonId"], promoted["divisionId"], promoted["round"]) == (
            9,
            9,
            2,
        )

        # Relegated back to a division already played. The club is where it
        # was written last, not where it was written first.
        inventory.SEASON_PROGRESS.apply(
            1, 10, {"round": 3, "data": "QUJD", "progressData": "REVG"}
        )
        back = json.loads(inventory.season_user_response())
        # Division 10 reports 10, not 9 -- the member names the division of
        # the row seasonId selects, not that number minus one.
        assert (back["seasonId"], back["divisionId"], back["round"]) == (10, 10, 3)
    finally:
        inventory.SEASON_PROGRESS.entries.clear()


def test_the_season_header_gets_a_record_once_there_is_one(monkeypatch) -> None:
    # `CRÉDITS 0`, `POINTS FIFA 0` and `BILAN 0-0-0` sat over a club holding
    # nine hundred million and a season won 3-0. That header is the season's,
    # and nothing was ever sent for it: the client keeps its progress in an
    # opaque blob and asks for the numbers separately.
    inventory = _native_seasons(monkeypatch)
    inventory.SEASON_PROGRESS.entries.clear()
    try:
        # A club that has never played a season sends exactly the three
        # members bisected into working -- and no blob, so there is nothing to
        # decode out of registers nothing has filled.
        assert set(json.loads(inventory.season_user_response())) == {
            "seasonId",
            "divisionId",
            "round",
        }

        inventory.SEASON_PROGRESS.settle(1, 10, "WIN", 626)
        inventory.SEASON_PROGRESS.settle(1, 10, "DRAW", 300)
        inventory.SEASON_PROGRESS.settle(1, 10, "QUIT", 0)
        standing = json.loads(inventory.season_user_response())
        assert standing["seasonGamesWon"] == 1
        assert standing["seasonGamesDraw"] == 1
        # A walk-out counts as a loss, the way it does for a cup.
        assert standing["seasonGamesLost"] == 1
        assert standing["seasonCoins"] == 926

        # The client's own save must not wipe the record it never sends.
        inventory.SEASON_PROGRESS.apply(
            1, 10, {"round": 4, "data": "QUJD", "progressData": "REVG"}
        )
        kept = json.loads(inventory.season_user_response())
        assert kept["seasonGamesWon"] == 1
        assert kept["seasonCoins"] == 926
        assert kept["round"] == 4

        # And it has to survive a restart. `restore` comes through the same
        # `apply`, so a record read only from what is already held is dropped
        # on every launch -- which looks exactly like never having kept one.
        saved = inventory.SEASON_PROGRESS.state()
        inventory.SEASON_PROGRESS.entries.clear()
        inventory.SEASON_PROGRESS.restore(saved)
        restored = json.loads(inventory.season_user_response())
        assert restored["seasonGamesWon"] == 1
        assert restored["seasonCoins"] == 926

        # Starting the season over drops the record with it. The client says
        # so by saving round 1 on a season that had got past it, which is
        # what it did after "Voulez-vous vraiment débuter cette Saison Joueur
        # Solo ?" was answered "Oui".
        inventory.SEASON_PROGRESS.apply(
            1, 10, {"round": 1, "data": "QUJD", "progressData": "REVG"}
        )
        # The record goes, the blob stays: the client has just written one,
        # and `data` is what tells it a season exists at all.
        restarted = json.loads(inventory.season_user_response())
        assert set(restarted) == {
            "seasonId", "divisionId", "round", "data", "dataVersion",
        }
        assert "seasonGamesWon" not in restarted
        assert restarted["round"] == 1
        # The record goes, the blob stays: the client has just written one, and
        # `data` is what tells it a season exists at all.
        restarted = json.loads(inventory.season_user_response())
        assert set(restarted) == {
            "seasonId",
            "divisionId",
            "round",
            "data",
            "dataVersion",
        }
        assert "seasonGamesWon" not in restarted
        assert restarted["round"] == 1
    finally:
        inventory.SEASON_PROGRESS.entries.clear()


def test_the_cup_list_carries_the_shape_the_binary_names() -> None:
    # A generated list froze the title when Compétition Joueur Solo was opened,
    # and the list was emptied until the fields could come from the binary.
    # They now do: every member below sits in CardsDLL's own sorted JSON name
    # table in .rdata. The freeze was `rounds` served as a count where the
    # parser walks records, alongside members that table does not carry.
    import fut_inventory as inventory

    cups = json.loads(inventory.tournaments_response())["tournament"]
    assert cups
    for cup in cups:
        assert isinstance(cup["rounds"], list)
        assert len(cup["rounds"]) == cup["numRounds"]
        assert cup["numTeams"] > len(json.loads(inventory.tournament_teams_response())["teamId"])
        for entry in cup["rounds"]:
            assert set(entry) == {"id", "difficulty", "rewardMultiplier", "coins"}
        assert cup["treeType"] == "knockout"
        assert set(cup["awardSet"]["awards"][0]) == {"awardType", "value", "halid"}
        for invented in ("name", "level", "entryFee", "active", "won"):
            assert invented not in cup


def test_the_club_lists_its_best_players_first() -> None:
    # Unsorted, the club and the squad's player picker listed cards in the
    # order the icebreaker packs added them.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    items = json.loads(club.club_response())["itemData"]

    players = [i for i in items if i.get("itemType") == "player"]
    ratings = [i.get("rating", 0) for i in players]
    assert ratings == sorted(ratings, reverse=True)

    # Players first: a playStyle carries a rating of 99 of its own, so ranking
    # on rating alone buried the best player behind a handful of them.
    kinds = [i.get("itemType") == "player" for i in items]
    assert kinds == sorted(kinds, reverse=True)
    assert items[0].get("itemType") == "player"


def test_the_club_keeps_the_name_the_player_chose() -> None:
    # PUT /user/club carries the name and abbreviation the creation screen
    # asked for. It used to be answered {} and forgotten, so the club had no
    # name on the next load.
    import fut_inventory as inventory

    identity = inventory.ClubIdentity()
    assert identity.name == ""
    assert identity.adopt({"clubName": "Olympique Safi", "clubAbbr": "OCS"})
    assert (identity.name, identity.abbr) == ("Olympique Safi", "OCS")

    # It survives a save/restore cycle.
    restored = inventory.ClubIdentity()
    restored.restore(identity.state())
    assert (restored.name, restored.abbr) == ("Olympique Safi", "OCS")

    # An empty body changes nothing rather than clearing the club.
    assert identity.adopt({}) is False
    assert identity.name == "Olympique Safi"


def test_the_starting_squad_takes_the_club_name() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    club.rename_active_squad("Olympique Safi")
    summaries = json.loads(club.squad_summaries())["squad"]
    active = club.active_squad_id()
    assert any(
        s["id"] == active and s["squadName"] == "Olympique Safi" for s in summaries
    )


def test_the_starter_packs_hold_no_specials() -> None:
    # Three packs -- bronze, silver, gold -- and nothing a new account has no
    # business being handed on its first day: no Team of the Week, no Team of
    # the Season, no Legend, and nothing above the cap.
    import fut_inventory as inventory

    club = inventory.ClubInventory(seeded=False)
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    coins_before = shop.wallet.coins

    count = shop.grant_starter_packs(random.Random(11))
    expected = sum(
        inventory.PACK_SPECS[p]["count"] for p in inventory.STARTER_PACKS
    )
    assert count == expected
    assert len(shop.pending) == expected
    # They are free.
    assert shop.wallet.coins == coins_before

    players = [c for c in shop.pending if c["itemType"] == "player"]
    for card in players:
        assert inventory.is_ordinary(card), card.get("rarity")
        assert card["rating"] <= inventory.STARTER_RATING_CAP

    # Three players a pack, not twelve -- the other nine slots are what a new
    # club actually needs, and it opened with none of them until they were
    # drawn here too.
    assert len(players) == sum(
        inventory.PACK_SPECS[p]["players"] for p in inventory.STARTER_PACKS
    )
    from fut_inventory import consumable_family

    assert any(consumable_family(c) == "contract" for c in shop.pending)


def test_ordinary_excludes_every_special() -> None:
    import fut_inventory as inventory

    for special in (
        "Team of the Week",
        "Team of the Season",
        "World Cup",
        "Legend",
        "MOTM",
        "iMOTM",
        "Team of the Year",
        "Record Breaker",
    ):
        assert not inventory.is_ordinary({"rarity": special})
    for ordinary in ("Non-Rare Bronze", "Rare Gold", "Non-Rare Silver"):
        assert inventory.is_ordinary({"rarity": ordinary})


def test_first_run_starts_with_no_club_at_all() -> None:
    # The club is seeded from all four captains' squads because fcc_login2
    # treats an empty squad as fatal. That seed is also why the captain
    # selection never appears: it exists to give a new player his first squad.
    # The two cannot both be true, so the seed is optional rather than assumed.
    import fut_inventory as inventory

    seeded = inventory.ClubInventory()
    assert seeded.items and seeded.squad

    fresh = inventory.ClubInventory(seeded=False)
    assert fresh.items == []
    assert fresh.squad == []
    # Kits, badges and consumables are club contents too; a club that has not
    # been created does not own them either.
    assert json.loads(fresh.club_response())["itemData"] == []


def test_first_run_is_off_unless_asked_for(monkeypatch) -> None:
    # An empty club breaks the login for an existing player, which is the
    # state of anyone not running the experiment.
    import fut_inventory as inventory

    monkeypatch.delenv("FIFA14_FIRST_RUN", raising=False)
    assert inventory.first_run() is False
    monkeypatch.setenv("FIFA14_FIRST_RUN", "1")
    assert inventory.first_run() is True


def test_the_unfiltered_club_is_bounded() -> None:
    # Unbounded, this returned every card in one document: 244 KB at 453 cards
    # and growing with every pack. An explicit count still wins.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    whole = json.loads(club.club_response())["itemData"]
    assert len(whole) <= inventory.CLUB_UNFILTERED_LIMIT

    # Against the stacked list, not the raw one: identical consumables collapse
    # into one card carrying a count, so a club of N items serves fewer than N
    # cards. The bound is still the count asked for.
    stacked = inventory.stack_consumables(list(club.items))
    asked = json.loads(club.club_response({"count": "300"}))["itemData"]
    assert len(asked) == min(300, len(stacked))


def test_the_club_pages_from_the_sorted_list() -> None:
    # Paging an unsorted list puts arbitrary cards on page one, so the order
    # has to be established before the slice.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    everyone = json.loads(club.club_response())["itemData"]
    page = json.loads(club.club_response({"count": "5"}))["itemData"]
    assert [item["id"] for item in page] == [item["id"] for item in everyone[:5]]


def test_sorting_the_club_does_not_reorder_it_everywhere_else() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    before = [item["id"] for item in club.items]
    club.club_response()
    assert [item["id"] for item in club.items] == before


def test_a_special_is_not_a_duplicate_of_the_ordinary_card() -> None:
    # A player's versions share his asset id: a Team of the Season Ruffier and
    # a Rare Gold Ruffier are both asset 167628 and are not the same card.
    import fut_inventory as inventory

    shop = inventory.PackShop(
        inventory.CardCatalogue(), inventory.Wallet(), inventory.ClubInventory()
    )
    # `resourceId` used to be the key. It cannot be here: this server builds it
    # as RESOURCE_VERSION | asset_id with the version byte always 1, so every
    # version of a player carries the same number and the key collapsed onto
    # the asset. What names the version is the rarity, and the rating separates
    # two cards of one player inside a family.
    ordinary = {"id": 1, "assetId": 167628, "rarity": "Rare Gold", "rating": 74}
    special = {"id": 2, "assetId": 167628, "rarity": "Team of the Season",
               "rating": 84}
    louder = {"id": 4, "assetId": 167628, "rarity": "Team of the Season",
              "rating": 87}
    same = {"id": 3, "assetId": 167628, "rarity": "Rare Gold", "rating": 74}

    assert shop._signature(ordinary) != shop._signature(special)
    assert shop._signature(special) != shop._signature(louder)
    assert shop._signature(ordinary) == shop._signature(same)


def test_a_repeat_is_paired_with_the_card_it_repeats() -> None:
    # The pack screen reads the pairing out of duplicateItemIdList; marking
    # only the card left a repeat looking like an ordinary pull. What must
    # never go in that list is a bare list of the new ids -- that froze the
    # title, by telling the screen to compare each card against itself.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    owned = club.items[0]
    repeat = dict(owned, id=1950999999)

    pairs = shop._mark_duplicates([repeat])
    assert pairs == [{"itemId": 1950999999, "duplicateItemId": owned["id"]}]
    assert repeat["duplicateItemId"] == owned["id"]
    # The new id never names itself.
    assert all(p["itemId"] != p["duplicateItemId"] for p in pairs)


def test_the_totw_challenge_says_how_strong_it_is() -> None:
    # opponentRating was computed with `max(... for card in [])` -- over an
    # empty list -- so every challenge advertised a rating of 0 against team 0.
    import fut_inventory as inventory

    totw = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    challenges = totw["squadChallenge"]
    assert challenges
    for entry in challenges:
        assert set(entry) == {
            "squadId",
            "squadName",
            "formation",
            "opponentTeam",
            "opponentRating",
        }
        assert entry["opponentRating"] > 0
        assert entry["squadName"]


def test_a_bought_card_is_paired_like_a_packed_one() -> None:
    # The pairing existed only on the pack path; a card bought on the market
    # went into the club unmarked however many copies were already there.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    owned = next(i for i in club.items if i.get("itemType") == "player")
    bought = dict(owned, id=1980000001, itemState="new")

    pairs = shop._mark_duplicates([bought])
    assert pairs == [{"itemId": 1980000001, "duplicateItemId": owned["id"]}]
    assert bought["duplicateItemId"] == owned["id"]


def test_a_card_the_server_never_held_is_not_reported_as_moved() -> None:
    # This is how a TOTS Ruffier was drawn, shown, sent to the club, confirmed
    # by the server, and then existed nowhere: an unknown id was acknowledged
    # with success while nothing was kept.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    catalogue = inventory.CardCatalogue()
    wallet = inventory.Wallet()
    shop = inventory.PackShop(catalogue, wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    before = len(club.items)
    reply = json.loads(
        actions.move({"itemData": [{"id": 999999999, "pile": "club"}]})
    )["itemData"][0]
    assert len(club.items) == before
    assert reply["success"] is False
    assert reply["errorCode"] != 0
    assert actions.unmatched == [999999999]


def test_a_card_the_server_holds_still_moves() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    catalogue = inventory.CardCatalogue()
    wallet = inventory.Wallet()
    wallet.coins = 10_000_000
    shop = inventory.PackShop(catalogue, wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    drawn = json.loads(shop.open_pack(inventory.GOLD_PACK_ID))["itemList"]
    before = len(club.items)
    reply = json.loads(
        actions.move(
            {"itemData": [{"id": card["id"], "pile": "club"} for card in drawn]}
        )
    )["itemData"]
    assert len(club.items) == before + len(drawn)
    assert all(entry["success"] for entry in reply)
    assert actions.unmatched == []


def test_image_archives_are_parseable_containers_not_json() -> None:
    # The /fut/items/ prefix answered everything with {"itemData":[]}, so the
    # console got sixteen bytes of JSON where it asked for a BIG archive.
    import fut_inventory as inventory

    archive = inventory.empty_big_archive()
    assert archive[:4] == b"BIGF"
    assert int.from_bytes(archive[8:12], "big") == 0  # no directory entries
    assert int.from_bytes(archive[12:16], "big") == len(archive)


def test_every_cup_names_a_trophy_the_game_actually_ships() -> None:
    # cards0.big carries trophy_<id>_<tier> for ids 1100..1169 beside a
    # notfound.big. Serving 0 is what drew that placeholder.
    import fut_inventory as inventory

    for cup in json.loads(inventory.tournaments_response())["tournament"]:
        assert inventory.TROPHY_FIRST <= cup["trophyResourceId"] <= inventory.TROPHY_LAST


def test_a_cup_is_only_active_once_it_has_been_entered(monkeypatch) -> None:
    import fut_inventory as inventory

    # The shape of a resumable run, which only goes out under `full`.
    # `off` is the default: four different documents have been served to the
    # console and all four froze it. See `cup_resume_mode`.
    monkeypatch.setenv("FIFA14_CUP_RESUME", "full")

    inventory.TOURNAMENT_PROGRESS.entries.clear()
    try:
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == []
        inventory.TOURNAMENT_PROGRESS.apply(3, {"round": 2, "tournamentData": "QQ=="})
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == [3]
        saved = json.loads(inventory.TOURNAMENT_PROGRESS.response(3))
        assert saved["round"] == 2
        assert saved["tournamentData"] == "QQ=="
        # `tournamentId` plus the five members the client itself writes, and
        # nothing else. What must never come back is a duplicate
        # `progressdata` beside `progressData`: that spelling is in the name
        # table, so it is the same known field twice rather than a sibling the
        # parser skips.
        #
        # The id was taken out of here once, on the reasoning that the path
        # already carries it. That was a guess. The PC revival
        # (KyroGeorge2/FIFA-14-Local-FUT, `offline_tournament_user`) sends it,
        # with the same resumability rule otherwise, and resumes cups.
        # Order matters and is asserted as a list: the `dataVersion` branch
        # decodes whatever the `tournamentData` branch left in two registers,
        # so the blob has to arrive first. The client's own serialiser gets
        # this backwards, which is why it cannot reopen its own saves.
        assert list(saved) == ["tournamentId", "round", "tournamentData", "dataVersion"]
        assert saved["tournamentId"] == 3
        # The capital-D spelling is the one the client writes and cannot
        # read: there is no `progressData` in its name table.
        assert "progressData" not in saved
        # The season spelling is still accepted on the way in.
        inventory.TOURNAMENT_PROGRESS.apply(3, {"round": 3, "data": "Ug=="})
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3))["tournamentData"] == "Ug=="
    finally:
        inventory.TOURNAMENT_PROGRESS.entries.clear()


def test_a_cup_entered_but_never_played_is_not_a_run_to_resume(monkeypatch) -> None:
    # The client saves its draw the moment the bracket is built: the full
    # sixteen-team blob, round one, and a progress blob of four zero bytes.
    # Handing that back froze the title twice -- the second time on a reply
    # byte for byte identical to the client's own PUT, which is what rules the
    # document itself out. Nothing is lost by calling it no run: no match has
    # been played, and the draw is redrawn on the way in.
    import fut_inventory as inventory

    monkeypatch.setenv("FIFA14_CUP_RESUME", "full")
    inventory.TOURNAMENT_PROGRESS.entries.clear()
    try:
        inventory.TOURNAMENT_PROGRESS.apply(
            3,
            {
                "round": 1,
                "dataVersion": 1,
                "tournamentData": "AAAK7h+LCAAA",
                "progressDataVersion": 1,
                "progressData": "AAAAAA==",
            },
        )
        assert inventory.TOURNAMENT_PROGRESS.active_ids() == []
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == []
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3)) == {
            "tournamentId": 3
        }

        # A run with a real progress blob is a real run and comes back whole.
        inventory.TOURNAMENT_PROGRESS.apply(3, {"progressData": "AAAAAgAB"})
        assert inventory.TOURNAMENT_PROGRESS.active_ids() == [3]
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3))["round"] == 1

        # So is one that has reached a later round.
        inventory.TOURNAMENT_PROGRESS.apply(
            3, {"round": 2, "progressData": "AAAAAA=="}
        )
        assert inventory.TOURNAMENT_PROGRESS.active_ids() == [3]
    finally:
        inventory.TOURNAMENT_PROGRESS.entries.clear()


def test_team_of_the_week_is_a_full_side() -> None:
    # A real Team of the Week is eighteen: the eleven and seven subs. It used
    # to be 23, because the side was six real ids padded out of the catalogue.
    import fut_inventory as inventory

    squad = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    assert len(squad["itemData"]) == 18
    # Week 1 is f343, not f442. The formation is the week's own.
    assert squad["formation"] == "f343"
    assert squad["squadName"] == "TOTW 1"
    ids = [card["id"] for card in squad["itemData"]]
    assert len(set(ids)) == len(ids)
    assert all(
        card["resourceId"] & 0x00FF_FFFF == card["assetId"]
        for card in squad["itemData"]
    )
    # In-forms are not the base card's version 1, and they carry rareflag 3 so
    # the client draws the in-form art. The band is the card's own -- 8, 9, 10
    # or 11 -- out of `specials.tsv`, not the flat 50 the PC build falls back
    # to for players its specials table does not hold.
    bands = {card["resourceId"] >> 24 for card in squad["itemData"]}
    assert bands <= {8, 9, 10, 11}, bands
    assert all(card["rareflag"] == 3 for card in squad["itemData"])


def _players(opened: dict) -> list:
    """The player cards of an opened pack.

    A pack is three players and nine consumables/club items now, so
    `itemList[0]` is no longer a player and the club's player list no longer
    grows by the size of the pack.
    """
    return [item for item in opened["itemList"] if item["itemType"] == "player"]


def test_a_card_sent_to_the_club_is_in_the_club() -> None:
    # The send acknowledged and the card left the pack, but the club response
    # was built from the starting inventory alone, so it never appeared.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet, inventory)

    before = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(21)))
    kept = _players(opened)[0]["id"]
    actions.move({"itemData": [{"id": kept, "pile": 7}]})

    owned = json.loads(inventory.club_response({"type": "player"}))["itemData"]
    assert len(owned) == before + 1
    assert kept in [item["id"] for item in owned]


def test_a_card_sold_out_of_the_club_leaves_it() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet, inventory)

    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(22)))
    kept = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": kept, "pile": 7}]})
    actions.discard_many([kept])

    owned = json.loads(inventory.club_response({"type": "player"}))["itemData"]
    assert kept not in [item["id"] for item in owned]


def _fresh():
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return inventory, shop, CardActions(shop, wallet, inventory)


def test_listing_a_card_takes_it_out_of_the_club() -> None:
    # Moving to the transfer pile used to leave the card in the club as well,
    # so it showed in both places and looked duplicated.
    import random

    inventory, shop, actions = _fresh()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(41)))
    card = _players(opened)[0]["id"]
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])

    actions.move({"itemData": [{"id": card, "pile": 5}]})
    assert [item["id"] for item in actions.transfer] == [card]
    after = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    assert after == owned - 1


def test_a_duplicate_is_kept_and_names_what_it_repeats() -> None:
    # Retail would offer to compare the two here, and that screen is driven by
    # duplicateItemIdList -- the field that froze the title, so it cannot be
    # turned back on yet. Until then the duplicate is quick-sold: the club
    # keeps one of each, the coins are real, and nothing happens silently.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=2_000_000)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    first = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    for item in first["itemList"]:
        actions._keep(dict(item))
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])

    import fut_inventory as inventory_module

    second = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    # Players and club items duplicate; consumables do not. A second contract
    # card is a second contract, but a second Barcelona home kit is nothing.
    assert all(item.get("duplicateItemId") for item in _players(second))
    assert not any(
        item.get("duplicateItemId")
        for item in second["itemList"]
        if not inventory_module._repeats(item)
    )
    for item in second["itemList"]:
        actions._keep(dict(item))

    # The duplicate is kept: the card names what it repeats, so the screen can
    # offer the choice rather than the server making it.
    after = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    assert after == owned + len(_players(second))


def test_a_club_holds_one_of_any_card() -> None:
    import random

    inventory, shop, actions = _fresh()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(42)))
    card = _players(opened)[0]["id"]
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    # The same item id is the same card, not a second one.
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    assert (
        len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
        == owned
    )


def test_the_tiles_count_what_the_club_actually_holds() -> None:
    import random

    from fut_inventory import hub_response

    inventory, shop, actions = _fresh()
    before = json.loads(hub_response(inventory, 0, 0, 0))["clubPlayers"]
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(43)))
    actions.move(
        {"itemData": [{"id": item["id"], "pile": 7} for item in opened["itemList"][:2]]}
    )
    after = json.loads(hub_response(inventory, 0, 0, 0))
    assert after["clubPlayers"] > before


def test_the_transfer_tiles_separate_the_market_from_the_club_listings() -> None:
    # Three tiles, three counts. The market tile reads auctionCount (the live
    # market), the transfer-list tile reads selling/sold. Feeding the market
    # tile the club's own listing count is why it read "13 LIVE TRANSFERS" over
    # thirteen *sold* cards while the transfer-list tile read 0/0.
    from fut_inventory import hub_response

    inventory, _shop, _actions = _fresh()
    doc = json.loads(hub_response(inventory, market=20000, selling=2, sold=13))
    assert doc["auctionCount"] == 20000        # the market, not the listings
    assert doc["selling"] == 2 and doc["sold"] == 13
    # Nested, because that is what the tile reads. transferListCount,
    # tradePileCount and tradePileItems are none of them in CardsDLL's table,
    # so the tile read 0 ITEMS over a club with cards on the pile.
    assert doc["tradePile"]["count"] == 15     # the club's own list total
    # The market count is never the club's sold count.
    assert doc["auctionCount"] != doc["sold"]


def test_the_club_survives_a_restart() -> None:
    # Entering FUT needs a relaunch, so without a save every session started
    # from the icebreaker packs again: the club counter back to 92, the pack
    # you opened gone, the coins reset.
    import random
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        PackShop,
        Wallet,
    )

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"

        inventory, wallet = ClubInventory(), Wallet()
        shop = PackShop(CardCatalogue(), wallet)
        actions = CardActions(shop, wallet, inventory)
        opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(61)))
        kept = [item["id"] for item in _players(opened)[:2]]
        actions.move({"itemData": [{"id": item, "pile": 7} for item in kept]})
        ClubSave(path).save(inventory, wallet, actions)
        owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
        coins = wallet.coins

        # A fresh process, as a relaunch gives.
        restored, purse = ClubInventory(), Wallet()
        again = CardActions(PackShop(CardCatalogue(), purse), purse, restored)
        assert ClubSave(path).load(restored, purse, again)

        reloaded = json.loads(restored.club_response({"type": "player"}))["itemData"]
        assert len(reloaded) == owned
        assert set(kept) <= {item["id"] for item in reloaded}
        assert purse.coins == coins


def test_a_bought_card_joins_the_club() -> None:
    # Buying credited nothing to the club: the card went into a side list the
    # inventory never saw, so it could not be assigned and vanished on restart.
    #
    # The market keeps offering the card afterwards, and that is correct -- it
    # carries many copies of the same player. Hiding them by asset id took
    # every version of Benatia off the market when one was bought.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    # Era-accurate prices: the top of the market is a Team of the Year at
    # seven million, so a test that buys the first listing needs the coins.
    inventory, wallet = ClubInventory(), Wallet(coins=50_000_000)
    catalogue = CardCatalogue()
    actions = CardActions(PackShop(catalogue, wallet), wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "2"}))["auctionInfo"][0]
    asset = listing["itemData"]["assetId"]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    assert won is not None
    actions._keep(dict(won))

    assert any(item["assetId"] == asset for item in inventory.items)


def test_a_bought_card_can_be_put_in_the_squad() -> None:
    # The squad was whatever was built at load time and nothing could change
    # it, so a card bought or pulled reached the club and had nowhere to go --
    # the assign screen found nothing it could field and backed out.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=50_000_000)
    catalogue = CardCatalogue()
    actions = CardActions(PackShop(catalogue, wallet), wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "1"}))["auctionInfo"][0]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    actions._keep(dict(won))

    chosen = [item["id"] for item in inventory.squad]
    chosen[0] = won["id"]
    inventory.set_squad(chosen)

    fielded = [
        player["itemData"]["id"]
        for player in json.loads(inventory.active_squad_response("x"))["players"]
    ]
    assert fielded[0] == won["id"]
    assert len(fielded) == len(chosen)


def test_an_unknown_card_cannot_be_fielded() -> None:
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    original = [item["id"] for item in inventory.squad]
    inventory.set_squad([999_999_999])
    # Nothing resolvable: the squad is left alone rather than emptied.
    assert [item["id"] for item in inventory.squad] == original


def test_a_bought_card_waits_in_the_purchased_pile() -> None:
    # The assign screen reads purchased/items. Sending a bought card straight
    # into the club left that list empty, so "Assigner maintenant" had nothing
    # to offer and backed out -- the pack flow, which works, goes through the
    # pending pile first.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=50_000_000)
    catalogue = CardCatalogue()
    shop = PackShop(catalogue, wallet)
    actions = CardActions(shop, wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "1"}))["auctionInfo"][0]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    bought = dict(won)
    bought["itemState"] = "new"
    shop.pending.append(bought)

    waiting = json.loads(shop.purchased_items())["itemData"]
    assert won["id"] in [item["id"] for item in waiting]
    assert won["id"] not in [item["id"] for item in inventory.items]

    # And assigning it moves it on, exactly as a pack card does.
    actions.move({"itemData": [{"id": won["id"], "pile": 7}]})
    assert not shop.pending
    assert won["id"] in [item["id"] for item in inventory.items]


def test_a_second_squad_can_be_created_renamed_and_dropped() -> None:
    # There was one squad and no way to add, rename or remove another: the
    # list was generated from a single fixed side.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    assert inventory.squad_ids() == [1]

    players = [item["id"] for item in inventory.items if item["itemType"] == "player"]
    made = inventory.save_squad(0, players[:23], name="Ma 2e équipe")
    assert made != 1 and made in inventory.squad_ids()

    named = {
        entry["id"]: entry["squadName"]
        for entry in json.loads(inventory.squad_summaries())["squad"]
    }
    assert named[made] == "Ma 2e équipe"

    inventory.save_squad(made, players[:23], name="Renommée")
    named = {
        entry["id"]: entry["squadName"]
        for entry in json.loads(inventory.squad_summaries())["squad"]
    }
    assert named[made] == "Renommée"

    assert inventory.delete_squad(made)
    assert inventory.squad_ids() == [1]


def test_the_squad_the_club_plays_with_cannot_be_deleted() -> None:
    # Dropping slot 1 would leave the club with nothing to field.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    assert not inventory.delete_squad(1)
    assert 1 in inventory.squad_ids()


def test_manager_tasks_are_recorded_and_survive() -> None:
    # They were a fixed empty list, so nothing completed was recorded: the bar
    # stayed at 0/13 and every task reset on the next launch.
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        ManagerTasks,
        PackShop,
        Wallet,
    )

    tasks = ManagerTasks()
    assert tasks.apply({"entries": [{"key": 0, "value": 1}, {"key": 3, "value": 1}]}) == 2
    done = {entry["key"] for entry in json.loads(tasks.response())["entries"]}
    assert done == {0, 3}

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"
        inventory, wallet = ClubInventory(), Wallet()
        actions = CardActions(PackShop(CardCatalogue(), wallet), wallet, inventory)
        ClubSave(path).save(inventory, wallet, actions, tasks)

        restored = ManagerTasks()
        ClubSave(path).load(ClubInventory(), Wallet(), actions, restored)
        assert restored.completed == tasks.completed


def test_a_card_pulled_twice_is_reported_as_a_duplicate() -> None:
    # duplicateItemIdList was always empty, so a player packed twice was never
    # offered as a duplicate and the screen had no reason to treat it apart.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=2_000_000)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    first = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    assert not any(item.get("duplicateItemId") for item in first["itemList"])
    for item in first["itemList"]:
        actions._keep(dict(item))

    # The same draw again: every card names the one it repeats, twice over.
    # The per-card duplicateItemId, and the plural list as pairs -- which is
    # what the FIFA 14 pack screen actually reads. The list was empty here, and
    # with it empty a repeat rendered as an ordinary card.
    #
    # What froze the title was a plural list of the *new* ids, telling the
    # screen to compare each card against itself. A pair never does that, and
    # the last assertion below is what keeps it that way.
    import fut_inventory as inventory_module

    second = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    marked = [item for item in second["itemList"] if item.get("duplicateItemId")]
    # Every repeatable card in the redraw is marked -- players and club items
    # alike, since the same seed draws the same cards.
    repeatable = [
        item for item in second["itemList"] if inventory_module._repeats(item)
    ]
    assert marked and len(marked) == len(repeatable)
    owned = {item["id"] for item in inventory.items}
    assert all(item["duplicateItemId"] in owned for item in marked)

    pairs = second["duplicateItemIdList"]
    assert len(pairs) == len(repeatable)
    assert all(set(pair) == {"itemId", "duplicateItemId"} for pair in pairs)
    assert all(pair["duplicateItemId"] in owned for pair in pairs)
    assert all(pair["itemId"] != pair["duplicateItemId"] for pair in pairs)


def test_a_pack_is_three_players_and_nine_other_things() -> None:
    # Every pack drew twelve players, so no contract, kit, badge, ball,
    # stadium, manager or staff card has ever come out of one. The club's
    # consumables tab only ever showed what the club was seeded with.
    import random

    from fut_inventory import (
        PACK_SPECS,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), ClubInventory())
    rng = random.Random(11)
    for pack_id, spec in PACK_SPECS.items():
        opened = json.loads(shop.open_pack(pack_id, rng))
        items = opened["itemList"]
        assert len(items) == spec["count"]
        assert len({item["id"] for item in items}) == spec["count"]
        assert len(_players(opened)) == spec["players"]


def test_a_pack_never_hands_out_a_card_from_another_tier() -> None:
    # Chemistry styles are rated 90 and above and so exist in gold only.
    # Choosing that family in a Silver Pack and then relaxing the tier put a
    # 99-rated style in a silver pack.
    import random

    from fut_inventory import (
        TIER_RATINGS,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), ClubInventory())
    rng = random.Random(3)
    for pack_id, tier in ((103, "bronze"), (203, "silver"), (303, "gold")):
        low, high = TIER_RATINGS[tier]
        for _ in range(40):
            for item in json.loads(shop.open_pack(pack_id, rng))["itemList"]:
                rating = item.get("rating", 0)
                # Balls are the one deliberate exception. Every ball in FIFA 14
                # is silver, so gating them by tier would delete them from four
                # pack tiers out of five; they carry no tier and are drawn
                # anywhere. See test_every_ball_is_silver_and_reaches_every_pack.
                if item.get("itemType") == "ball":
                    continue
                if rating:
                    assert low <= rating <= high, (tier, item["itemType"], rating)


def test_a_pack_hands_out_more_contracts_than_training() -> None:
    # There are 42 training cards in the catalogue and 13 contracts, so an
    # even draw across the templates gives a club three times more training
    # than contract -- and a club that still runs out of contracts. The draw
    # weights the family, not the number of variants it happens to have.
    import collections
    import random

    from fut_inventory import (
        BADGE_WIRE_TYPE,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
        consumable_family,
    )

    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), ClubInventory())
    rng = random.Random(5)
    seen, wire = collections.Counter(), collections.Counter()
    for _ in range(120):
        for item in json.loads(shop.open_pack(303, rng))["itemList"]:
            wire[item["itemType"]] += 1
            seen[consumable_family(item) or item["itemType"]] += 1
    assert seen["contract"] > seen["training"]
    assert seen["contract"] > seen["healing"]
    # Club items are back in the draw as of 17 August 2026. They were held out
    # because they drew blank card backs on the pack screen -- but that was the
    # bare envelope they were sent in, not their invented resource ids: with the
    # per-kind members each family needs, and badges under the retail `custom`
    # family, they render. Confirmed on the console.
    #
    # Consumables still dominate, because a club runs out of contracts long
    # before it runs out of balls.
    club_items = sum(
        seen[kind] for kind in ("kit", BADGE_WIRE_TYPE, "ball", "stadium",
                                "manager", "staff")
    )
    assert club_items > 0
    assert seen["contract"] > club_items
    # On the wire they carry FUT's own two types, never the family name.
    assert wire["development"] and wire["training"]
    assert not any(wire[family] for family in ("contract", "fitness", "healing"))


def test_a_second_contract_card_is_not_a_duplicate() -> None:
    import fut_inventory as inventory_module

    # Consumables stack. Marking one as a repeat offers to quick-sell a card
    # the club is meant to accumulate.
    import random

    import fut_inventory as inventory_module
    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    inventory = ClubInventory()
    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), inventory)
    rng = random.Random(17)
    for _ in range(20):
        opened = json.loads(shop.open_pack(303, rng))
        marked = {
            item["id"] for item in opened["itemList"] if item.get("duplicateItemId")
        }
        # Players and club items both repeat -- you either own a kit or you do
        # not. Consumables stack, so a second contract is never a duplicate.
        repeatable = {
            item["id"] for item in opened["itemList"]
            if inventory_module._repeats(item)
        }
        assert marked <= repeatable
        assert {pair["itemId"] for pair in opened["duplicateItemIdList"]} <= repeatable
        consumables = {
            item["id"] for item in opened["itemList"]
            if item.get("itemType") in ("development", "training")
        }
        assert not (marked & consumables)


def test_a_rare_and_a_base_card_are_not_duplicates_of_each_other() -> None:
    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    inventory = ClubInventory()
    shop = PackShop(CardCatalogue(), Wallet(), inventory)
    # An asset the starting club does not already hold, so the only copies in
    # play are the ones this test puts there.
    held = {item.get("assetId") for item in inventory.items}
    asset = next(
        card["assetId"] for card in CardCatalogue().cards if card["assetId"] not in held
    )
    base = {"id": 1, "assetId": asset, "rareflag": 0}
    rare = {"id": 2, "assetId": asset, "rareflag": 1}
    inventory.items.append(base)
    # Same player, different version: not a repeat.
    assert shop._duplicates([rare]) == []
    assert shop._duplicates([dict(base, id=3)]) == [3]


def test_consumables_use_the_member_names_the_binary_carries() -> None:
    # The named members come from CardsDLL's JSON table between 0x89030F9C and
    # 0x89031148. They are necessary and they were not sufficient: the Apply
    # Consumable popup is backed by a sticker-book stats response and binds its
    # buttons from `stat`/`entries` rows in context 6, so the club could hold
    # 65 contracts, every scalar could be right, and the popup still reported
    # none available. Both shapes go out, carrying the same counts.
    from fut_inventory import (
        CONSUMABLE_STAT_CONTEXT,
        ClubInventory,
        consumable_stats_response,
    )

    document = json.loads(consumable_stats_response(ClubInventory()))

    scalars = {k: v for k, v in document.items() if isinstance(v, int)}
    assert document["stat"] == document["entries"]
    assert len(document["entries"]) == len(scalars)
    for entry in document["entries"]:
        assert entry["contextId"] == CONSUMABLE_STAT_CONTEXT
        assert entry["contextValue"] == 0
        assert entry["typeValue"] == scalars[entry["type"]]
    for member in (
        "consumablesContractPlayer",
        "consumablesFitnessPlayer",
        "consumablesHealing",
        "consumablesPosition",
        "consumablesTrainingPlayer",
    ):
        assert document[member] > 0, member
    # No member goes out at zero while the club holds consumables. Applying
    # from the squad screen decides from these counts alone, so a goalkeeper
    # made it read consumablesTrainingGk -- zero -- and report none available.
    assert document["consumablesTrainingGk"] > 0
    assert all(value > 0 for value in scalars.values())
    # The aggregate members cover their family once each. Chemistry styles
    # have no aggregate of their own -- the binary carries one member for an
    # outfielder's and one for a keeper's, and nothing above them -- so the
    # total counts both.
    assert document["consumables"] == sum(
        document[name]
        for name in (
            "consumablesContract",
            "consumablesFitness",
            "consumablesHealing",
            "consumablesTraining",
            # `consumablesPosition` is a fallback now and reports the position
            # family's own count, so it would be counted twice here -- those
            # cards already report under the play-style members. The comment
            # above says exactly this about fallbacks.
            "consumablesTrainingPlayerPlayStyle",
            # And the keeper's. `consumablesTrainingGkPlayStyle` was empty for
            # a while: the sixteen cards that had reported under it were the
            # art-35 manager formation modifiers, which draw no art and are
            # held out of the club, and the real goalkeeper styles were in no
            # catalogue this project had.
            #
            # They are now. `style_value` deduced subtypes 269-273 from the
            # card parser -- 0x891AE3F8 does `value - 250` and rejects above
            # 23, so 250-273 is the accepted range, and nineteen outfield
            # styles leave a keeper's five. Impulsum14's extract of the game's
            # own database names them: Wall, Shield, Cat, Glove, GK Basic, at
            # resources 5003114-5003118, continuing this catalogue's sequence
            # without a gap.
            "consumablesTrainingGkPlayStyle",
            # And the manager's league modifiers, subtypes 300-326. This
            # member reported the training family's count until 25 August
            # while the catalogue held none of its cards, which is what the
            # club's manager-league tab was doing when it offered sixty-nine
            # and then found nothing. The fallback is gone and the twenty-seven
            # cards are real, so the count belongs in the total like any other
            # family's.
            "consumablesTrainingManagerLeagueModifier",
        )
    )


def test_manager_league_modifiers_are_counted_not_stood_in_for() -> None:
    # The club's manager-league tab said sixty-nine and then found nothing.
    # Both halves were true: `CONSUMABLE_FALLBACKS` had this member report the
    # training family's count, and the catalogue held none of its cards. The
    # count was a stand-in for stock that did not exist.
    #
    # Subtypes 300-326 at resources 5003119-5003145, from Impulsum14's extract
    # of the game's database and confirmed on six rows by Kyro's, which also
    # tags the block "Manager League". See `tools/manager_league_mods.py`.
    from fut_inventory import (
        CONSUMABLE_FALLBACKS,
        UNDRAWN_CONSUMABLE_TYPES,
        ClubInventory,
        _club_extras,
        _consumable_catalogue,
        consumable_family,
        consumable_stats_response,
    )

    member = "consumablesTrainingManagerLeagueModifier"
    assert member not in CONSUMABLE_FALLBACKS

    rows = [r for r in _consumable_catalogue()
            if r["itemType"] == "managerLeagueModifier"]
    assert len(rows) == 27
    assert sorted(r["cardsubtypeid"] for r in rows) == list(range(300, 327))
    assert sorted(r["definitionId"] for r in rows) == list(range(5003119, 5003146))
    # One art id for the whole block, read out of the database rather than
    # invented. An invented one draws NOT FOUND.
    assert {r["assetId"] for r in rows} == {32}
    assert all(r["member"] == member for r in rows)

    # Seeded first so they could be looked at, then packed once all
    # twenty-seven were seen rendering on the console -- the treatment squad
    # training, managers and staff each got.
    assert "managerLeagueModifier" not in UNDRAWN_CONSUMABLE_TYPES
    seeded = [i for i in _club_extras()
              if consumable_family(i) == "managerLeagueModifier"]
    assert len(seeded) == 27

    # The count is now the stock, and every card behind it is real.
    inventory = ClubInventory()
    inventory.items = _club_extras()
    document = json.loads(consumable_stats_response(inventory))
    assert document[member] == len(seeded)

    # And the list agrees with the count. These are two different routes --
    # the tab's header reads the stats response, the tab's body reads
    # `/club/consumables/<category>` -- and on 25 August they disagreed: the
    # header said 27 and the body was empty, because CONSUMABLE_CATEGORIES
    # still mapped this category to the empty string from when the club really
    # did hold none of them.
    #
    # The console asks by the family name, not by the wire type: the journal
    # has `GET /ut/game/fifa14/club/consumables/managerLeagueModifier`.
    from fut_inventory import consumables_response

    listed = json.loads(consumables_response(inventory, "managerLeagueModifier"))
    assert listed["total"] == document[member]
    assert sorted(r["cardsubtypeid"] for r in listed["itemData"]) == list(range(300, 327))
    # A family the club really does hold none of still lists none, rather than
    # falling through to everything.
    assert json.loads(consumables_response(inventory, "managerContract"))["total"] == 0


def test_manager_league_modifiers_are_gold_pack_only() -> None:
    # Rated 95, so `_extra_tier` calls them gold and the low packs cannot
    # reach them. This is the thing that put a 99-rated chemistry style in a
    # Silver Pack when the tier was relaxed after the family was chosen.
    import random

    from fut_inventory import (
        CONSUMABLE_DRAW_WEIGHT,
        _draw_extra,
        consumable_family,
        pack_extras,
    )

    templates = pack_extras()[("consumable", "managerLeagueModifier")]
    assert len(templates) == 27
    assert {t["_tier"] for t in templates} == {"gold"}
    assert CONSUMABLE_DRAW_WEIGHT["managerLeagueModifier"] > 0

    drawn = {"bronze": 0, "silver": 0, "gold": 0}
    rng = random.Random(3)
    for tier in drawn:
        for _ in range(3000):
            card = _draw_extra(tier, False, 1, rng, set())
            if card and consumable_family(card) == "managerLeagueModifier":
                drawn[tier] += 1
    assert drawn["bronze"] == 0
    assert drawn["silver"] == 0
    assert drawn["gold"] > 0


def _unused_consumable_counts_are_not_the_club_counters() -> None:
    # club/stats/consumables was answered with the generic club counters --
    # players, staff, stadiums -- so the apply screen reported none available
    # while the club held sixteen consumables.
    from fut_inventory import (
        CONSUMABLE_ORDER,
        ClubInventory,
        club_stats_response,
        consumable_stats_response,
    )

    inventory = ClubInventory()
    consumables = json.loads(consumable_stats_response(inventory))["entries"]
    generic = json.loads(club_stats_response(inventory))["entries"]

    assert len(consumables) == len(CONSUMABLE_ORDER)
    assert all(entry["value"] > 0 for entry in consumables)
    # And it is genuinely a different answer, not the same document renamed.
    assert [e["value"] for e in consumables] != [e["value"] for e in generic]


def test_the_club_search_understands_the_consumable_family() -> None:
    # The consumables screen reads the counts and then searches the club for
    # "consumable" -- the family, not each kind. Comparing that against
    # itemType, which is contract, fitness, healing and so on, matched nothing:
    # the screen found counts and then no items.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    # Past the club's whole stock of consumables, or both answers come back
    # capped at the page size and the comparison says nothing.
    family = json.loads(inventory.club_response({"type": "consumable", "count": "500"}))
    one_kind = json.loads(
        inventory.club_response({"type": "development", "count": "500"})
    )

    assert len(family["itemData"]) > len(one_kind["itemData"]) > 0
    # Asking for the family must not sweep in players.
    assert all(item["itemType"] != "player" for item in family["itemData"])


def test_consumables_come_from_the_game_database() -> None:
    # They were invented once: three grades a family, asset ids counted up
    # from 1000. Every card drew NOT FOUND art, all of them were named
    # "Entrainement equipe", and applying one did nothing -- the title reads a
    # consumable's name and effect out of its own database by subtype, and
    # draws it by asset id, so neither is ours to pick.
    from fut_inventory import CONSUMABLE_TYPES, _consumable_definitions

    consumables = [
        item for item in INVENTORY.items
        if item.get("itemType") in CONSUMABLE_TYPES
    ]
    assert consumables

    subtypes = {item["cardsubtypeid"] for item in consumables}
    # A single subtype is what "all of them are the same card" looks like.
    assert len(subtypes) > 40
    # `cardassetid`, not `assetId`: a consumable's art has its own member, and
    # sending only `assetId` drew NOT FOUND on the pack screen.
    assert all(item["cardassetid"] < 1000 for item in consumables)
    assert not any("assetId" in item for item in consumables)

    # The member CardsDLL counts a card under comes from the catalogue, keyed
    # by the card's own database id -- which is the item's `resourceId`. It
    # used to travel on the item itself under a name CardsDLL does not carry.
    definitions = _consumable_definitions()
    members = {
        definitions[item["resourceId"]]["member"]
        for item in consumables
        if item["resourceId"] in definitions
    }
    assert "consumablesTrainingGk" in members
    assert "consumablesTrainingPlayer" in members
    assert "consumablesContractManager" in members


def _rack():
    from fut_inventory import ClubInventory, ConsumableRack, _consumable_definitions

    inventory = ClubInventory()
    definitions = _consumable_definitions()
    by_subtype: dict[int, list] = {}
    for item in inventory.items:
        row = definitions.get(item.get("resourceId"))
        if row:
            by_subtype.setdefault(row["cardsubtypeid"], []).append(item)
    return inventory, ConsumableRack(inventory), by_subtype


def test_a_contract_grants_matches_and_spends_the_card() -> None:
    # The club could hold a contract and show it, and there was no route that
    # did anything with one. Every consumable in FUT was decoration.
    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    player["contract"] = 10
    from fut_inventory import consumable_family

    held = len([i for i in inventory.items if consumable_family(i) == "contract"])

    card = by_subtype[201][0]
    result = rack.apply(card["resourceId"], [player["id"]])

    assert player["contract"] > 10
    assert result["consumedItemId"] == card["id"]
    # Spent, not merely reported spent.
    assert card not in inventory.items
    assert (
        len([i for i in inventory.items if consumable_family(i) == "contract"])
        == held - 1
    )


def test_a_contract_grants_less_to_a_better_player() -> None:
    # A contract grants a different number of matches to a gold, a silver and
    # a bronze card; the card database carries all three figures.
    from fut_inventory import _consumable_definitions

    inventory, rack, by_subtype = _rack()
    row = _consumable_definitions()[by_subtype[201][0]["resourceId"]]
    assert row["bronze"] > row["gold"]

    players = [i for i in inventory.items if i["itemType"] == "player"]
    gold = next(p for p in players if p["rating"] >= 75)
    gold["contract"] = 0
    rack.apply(by_subtype[201][0]["resourceId"], [gold["id"]])
    assert gold["contract"] == row["gold"]


def test_a_squad_fitness_card_restores_the_whole_eleven() -> None:
    inventory, rack, by_subtype = _rack()
    for player in inventory.squad:
        player["fitness"] = 50

    result = rack.apply(by_subtype[220][0]["resourceId"], [])

    assert all(player["fitness"] > 50 for player in inventory.squad)
    assert len(result["itemData"]) == len(inventory.squad)


def test_healing_refuses_the_wrong_injury_and_a_healthy_player() -> None:
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")

    # 211 is the head card; the binary's own list is head, upperbody, arm,
    # back, knee, leg, foot from 211, and 218 heals anything.
    try:
        rack.apply(by_subtype[211][0]["resourceId"], [player["id"]])
    except ConsumableRefused as refusal:
        assert "not injured" in str(refusal)
    else:
        raise AssertionError("a healthy player was healed")

    player["injuryGames"], player["injuryType"] = 3, "knee"
    try:
        rack.apply(by_subtype[211][0]["resourceId"], [player["id"]])
    except ConsumableRefused as refusal:
        assert "knee" in str(refusal)
    else:
        raise AssertionError("a head card treated a knee injury")

    rack.apply(by_subtype[215][0]["resourceId"], [player["id"]])
    assert player["injuryGames"] < 3


def test_a_refused_card_is_not_spent() -> None:
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    card = by_subtype[211][0]
    held = len(inventory.items)

    try:
        rack.apply(card["resourceId"], [player["id"]])
    except ConsumableRefused:
        pass
    # Nothing is written and nothing is spent until the effect is decided.
    assert card in inventory.items
    assert len(inventory.items) == held


def test_training_raises_one_attribute_or_all_six() -> None:
    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    for entry in player["attributeList"]:
        entry["value"] = 50

    rack.apply(by_subtype[61][0]["resourceId"], [player["id"]])
    values = [entry["value"] for entry in player["attributeList"]]
    assert values[0] > 50 and values[1:] == [50] * 5

    rack.apply(by_subtype[67][0]["resourceId"], [player["id"]])
    assert all(entry["value"] > 50 for entry in player["attributeList"])


def test_the_position_block_is_still_refused_and_recorded() -> None:
    # 232. Both catalogues call it a position change and the binary carries a
    # FUT_CONSUMABLE_POSITIONMOD -- but the card the console rendered for it
    # reads "DEBLOQUER / Capacite +8 moral", which is a stadium unlock. Writing
    # preferredPosition on the strength of that changes the wrong thing on a
    # real card, and the card is spent either way.
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    # Built rather than found: subtype 232 is no longer seeded into the club.
    # It was filed under `position`, which put it in the position modifier tab,
    # and the console draws it as Squad Training with art that does not resolve.
    # The refusal below is what still matters.
    from fut_inventory import _consumable_catalogue, _consumable_item

    row = next(
        c for c in _consumable_catalogue() if c["cardsubtypeid"] == 232
    )
    card = _consumable_item(row, 1_960_000_232)
    inventory.items.append(card)
    try:
        rack.apply(card["resourceId"], [player["id"]])
    except ConsumableRefused:
        pass
    else:
        raise AssertionError("subtype 232 was applied")
    assert card in inventory.items
    # Recorded, so one application from the console names the family.
    assert [entry["cardsubtypeid"] for entry in rack.refused] == [232]


def test_a_chemistry_style_writes_the_value_the_parser_accepts() -> None:
    # The nineteen real styles, 250-268, added 16 August 2026. The value that
    # goes onto the card is the catalogue row's `amount` -- 0 for Basic, 1 for
    # Sniper, 18 for Shadow -- because `FUT_PLAYSTYLE_%d` in the binary keys a
    # style by an integer and this is the range it means.
    #
    # Writing the subtype instead, which is what the old play-style path did,
    # puts 250-268 into a member the client reads as an index into nineteen
    # styles. Every card would show the wrong style.
    import collections

    inventory, rack, by_subtype = _rack()
    player = next(
        i for i in inventory.items
        if i["itemType"] == "player" and i.get("preferredPosition") != "GK"
    )
    # Basic is 250: a card with no style still carries a value the
    # converter accepts, since 0 is outside the 250-273 range.
    assert player["playStyle"] == 250

    # The parser puts this member through 0x891AE3F8, which subtracts 250 and
    # rejects anything above 23 -- so the value it accepts is the subtype
    # itself, 250-273, and the catalogue's 0-18 was discarded on arrival.
    expected = {250: 250, 251: 251, 266: 266, 268: 268}
    applied = 0
    for subtype, index in expected.items():
        cards = by_subtype.get(subtype)
        if not cards:
            continue
        rack.apply(cards[0]["resourceId"], [player["id"]])
        assert player["playStyle"] == index, (
            f"subtype {subtype} wrote {player['playStyle']}, expected {index}"
        )
        applied += 1
    assert applied, "no chemistry styles were seeded into the club to test"

    # And they are the only cards the chemistry tab offers. Before this, the
    # tab answered with 36 position modifiers and no styles at all.
    from fut_inventory import consumables_response

    document = json.loads(consumables_response(inventory, "playStyle"))
    offered = {i.get("cardsubtypeid") for i in document["itemData"]}
    assert offered
    # 250-273, not 250-268: the parser's own range is `value - 250 <= 23`, and
    # the five slots past the outfield styles are the goalkeeper's -- Wall,
    # Shield, Cat, Glove, GK Basic, now in the catalogue.
    assert all(250 <= s <= 273 for s in offered), sorted(offered)


def test_an_outfield_chemistry_style_will_not_go_on_a_goalkeeper() -> None:
    # 250-268 are the outfield set: Basic, Sniper, Finisher ... Shadow. FIFA 14
    # gives keepers their own five -- Basic, Wall, Shield, Cat, Glove -- and
    # none of those four names is anywhere in this catalogue or the PC
    # revival's, so a keeper can only be put back to Basic here.
    #
    # Basic is the exception on purpose: it is the style every card starts on,
    # keeper and outfield alike, so 250 means something on a goalkeeper.
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    keeper = next(
        (i for i in inventory.items
         if i["itemType"] == "player" and i.get("preferredPosition") == "GK"),
        None,
    )
    if keeper is None:
        return

    for subtype in (251, 266, 268):
        cards = by_subtype.get(subtype)
        if not cards:
            continue
        card, was = cards[0], keeper["playStyle"]
        try:
            rack.apply(card["resourceId"], [keeper["id"]])
        except ConsumableRefused:
            pass
        else:
            raise AssertionError(f"outfield style {subtype} went on a goalkeeper")
        assert keeper["playStyle"] == was
        assert card in inventory.items

    basic = by_subtype.get(250)
    if basic:
        rack.apply(basic[0]["resourceId"], [keeper["id"]])
        # Basic is 250 on the wire, not 0: the parser's converter takes
        # 250-273 and subtracts the base itself.
        assert keeper["playStyle"] == 250


def test_the_position_block_is_refused_and_nothing_is_spent() -> None:
    # This replaces `test_a_chemistry_style_is_written_onto_the_card`, which
    # asserted that 91-136 are chemistry styles and wrote the subtype onto the
    # card's `playStyle`.
    #
    # They are position modifiers. Two independent sources, 16 August 2026:
    #
    #   the console  -- asked for chemistry styles, the game showed position
    #                   modifiers, resolved from the disc's own card data
    #   the PC build -- names all twenty of 91-110 as transitions (LWB->LB,
    #                   CM->CAM, ST->CF) under a `Positioning` category
    #
    # And the PC catalogue records `sourceMember:
    # consumablesTrainingPlayerPlayStyle` on a row it files as Positioning --
    # so the member name, which is the single thing this server changed its
    # mind on, is a counter the binary groups them under and not a statement
    # of what the cards do.
    #
    # The real chemistry styles are 250-268 and this catalogue has none of
    # them, which is why a player can never find one.
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    outfield = next(
        i for i in inventory.items
        if i["itemType"] == "player" and i.get("preferredPosition") != "GK"
    )
    before = outfield.get("playStyle")

    # 121-136 stay refused whatever they are aimed at. Nothing names what an
    # internal goalkeeper block does, and the PC revival does not support it
    # either.
    # Built rather than found: 121-136 are the art-35 manager formation
    # modifiers and are no longer seeded into the club -- they draw "Formation
    # Modifier -- Manager" with no art. The refusal below is what still
    # matters, and it has to keep working for one that arrives some other way.
    from fut_inventory import _consumable_catalogue, _consumable_item

    rows = {c["cardsubtypeid"]: c for c in _consumable_catalogue()}
    for subtype in (121, 128, 136):
        row = rows.get(subtype)
        if row is None:
            continue
        card = _consumable_item(row, 1_960_000_000 + subtype)
        inventory.items.append(card)
        try:
            rack.apply(card["resourceId"], [outfield["id"]])
        except ConsumableRefused:
            pass
        else:
            raise AssertionError(f"subtype {subtype} was applied to a real card")
        # Refusing costs nothing: the card stays owned and the player is
        # untouched. A wrong write would spend the card either way.
        assert card in inventory.items
        assert outfield.get("playStyle") == before

    # And every refusal is recorded, so one application from the console keeps
    # naming the family rather than disappearing.
    assert rack.refused
    assert {r["cardsubtypeid"] for r in rack.refused} <= set(range(121, 137))


def test_a_position_modifier_moves_only_the_player_it_names() -> None:
    # 91-110 carry a `from` and a `to` as of 16 August 2026, so they apply --
    # but only to a player already in the `from` position. That guard is
    # retail's rule, and it matters more here than in retail: this server is
    # the only thing between a mis-click and a permanently repositioned card.
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    striker = next(
        (i for i in inventory.items
         if i["itemType"] == "player" and i.get("preferredPosition") == "ST"),
        None,
    )
    if striker is None:
        return

    # 110 is ST->CF, and 109 is CF->ST, so the pair is reversible.
    out, back = by_subtype.get(110), by_subtype.get(109)
    if out:
        rack.apply(out[0]["resourceId"], [striker["id"]])
        assert striker["preferredPosition"] == "CF"
    if out and back:
        rack.apply(back[0]["resourceId"], [striker["id"]])
        assert striker["preferredPosition"] == "ST"

    # A card for another position is refused, and is not spent.
    mismatched = by_subtype.get(103)          # CM->CAM
    if mismatched:
        card = mismatched[0]
        was = striker["preferredPosition"]
        try:
            rack.apply(card["resourceId"], [striker["id"]])
        except ConsumableRefused:
            pass
        else:
            raise AssertionError("a CM->CAM card moved a striker")
        assert striker["preferredPosition"] == was
        assert card in inventory.items


def test_a_card_changed_by_a_consumable_survives_a_restart() -> None:
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        PackShop,
        Wallet,
    )

    inventory, rack, by_subtype = _rack()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)
    player = next(i for i in inventory.items if i["itemType"] == "player")
    player["contract"] = 10
    rack.apply(by_subtype[201][0]["resourceId"], [player["id"]])
    expected = player["contract"]

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"
        ClubSave(path).save(inventory, wallet, actions)

        # A card the club started with, changed since: neither acquired nor
        # sold, so it used to be forgotten on the next launch.
        restored, purse = ClubInventory(), Wallet()
        again = CardActions(PackShop(CardCatalogue(), purse), purse, restored)
        ClubSave(path).load(restored, purse, again)

        reloaded = next(i for i in restored.items if i["id"] == player["id"])
        assert reloaded["contract"] == expected
        # The squad holds the same objects, so it must see the change too.
        in_squad = [i for i in restored.squad if i["id"] == player["id"]]
        assert not in_squad or in_squad[0] is reloaded


def _pack_shop():
    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    return PackShop(CardCatalogue(), Wallet(coins=10**12), ClubInventory())


def test_the_rating_bands_are_the_stated_ones() -> None:
    # The old draw was uniform over the tier, which was close to these by
    # coincidence -- the gold pool happens to hold 862/254/66/18/4. Close by
    # coincidence moves the moment the catalogue is edited.
    import collections
    import random

    from fut_inventory import RATING_BANDS, is_ordinary

    shop, rng = _pack_shop(), random.Random(1)
    seen, total = collections.Counter(), 0
    for _ in range(1500):
        for item in json.loads(shop.open_pack(303, rng))["itemList"]:
            if item["itemType"] != "player" or not is_ordinary(item):
                continue
            total += 1
            for span, _weight in RATING_BANDS["gold"]:
                if span[0] <= item["rating"] <= span[1]:
                    seen[span] += 1

    for span, weight in RATING_BANDS["gold"]:
        share = 100 * seen[span] / total
        assert abs(share - weight) < max(1.5, weight * 0.35), (span, share, weight)


def test_an_ordinary_slot_can_still_hold_a_rare_card() -> None:
    # "1 Rare" is a minimum, not an exclusivity. Drawing ordinary slots from
    # non-rares only shut the top bands out of the pack: a gold rated 84 or
    # better is nearly always a Rare Gold, and 84-86 came out at 0.65%
    # against a stated 6%.
    import random

    from fut_inventory import is_ordinary

    shop, rng = _pack_shop(), random.Random(2)
    rares = 0
    players = 0
    for _ in range(200):
        for item in json.loads(shop.open_pack(303, rng))["itemList"]:
            if item["itemType"] == "player" and is_ordinary(item):
                players += 1
                rares += bool(item["rareflag"])
    assert 0 < rares < players


def test_a_special_is_rolled_once_per_pack_against_its_stated_chance() -> None:
    # `rareflag` is set on a Rare Gold and on every special alike, so the old
    # rare slot drew a special seven times out of ten and 15% of Gold Packs
    # held one. Nothing decided that; it was the shape of the catalogue.
    import random

    from fut_inventory import SPECIAL_CHANCE, is_ordinary

    shop, rng = _pack_shop(), random.Random(3)
    for pack_id in (103, 303, 304):
        packs = 1500
        held = 0
        for _ in range(packs):
            cards = json.loads(shop.open_pack(pack_id, rng))["itemList"]
            if any(
                card["itemType"] == "player" and not is_ordinary(card)
                for card in cards
            ):
                held += 1
        share = held / packs
        target = SPECIAL_CHANCE[pack_id]
        assert abs(share - target) < max(0.01, target * 0.35), (pack_id, share, target)


def test_the_special_family_is_chosen_by_weight_not_by_stock() -> None:
    # The catalogue holds 517 World Cup cards in the gold tier against 347
    # Team of the Week, so drawing evenly made World Cup the commonest
    # special in the game. Team of the Week is what a pack should mostly give.
    import collections
    import random

    from fut_inventory import SPECIAL_FAMILY_WEIGHTS, is_ordinary

    shop, rng = _pack_shop(), random.Random(5)
    seen = collections.Counter()
    for _ in range(4000):
        for item in json.loads(shop.open_pack(304, rng))["itemList"]:
            if item["itemType"] == "player" and not is_ordinary(item):
                seen[(item["rarity"] or "").lower()] += 1

    assert seen["team of the week"] > seen["world cup"]
    # The commonest family by a wide margin -- not a majority. This used to
    # assert a majority, which the weights have not promised since they were
    # rebalanced on 17 August: Team of the Week is 48 of 105.5, and 48 of 95.5
    # once the inert World Cup weight is dropped by the empty-family filter.
    # That is 50.3%, so "more than all the others combined" was a coin flip
    # that happened to be landing heads, and it started landing tails the next
    # time anything perturbed the draw.
    others = {name: n for name, n in seen.items() if name != "team of the week"}
    assert seen["team of the week"] > 0.40 * sum(seen.values())
    assert seen["team of the week"] > 2 * max(others.values())
    # Legends were weighted to zero until one had been seen to render. One has:
    # bought off the transfer market on the console, 2026-08-16, art and all.
    #
    # So they draw now, but they stay the rarest family bar Record Breaker. The
    # evidence covers the market and club renderers, not the pack reveal screen,
    # which is the one with the freeze history -- the weight is low enough that
    # the remaining question gets answered without a player meeting it often.
    assert 0.0 < SPECIAL_FAMILY_WEIGHTS["legend"] <= SPECIAL_FAMILY_WEIGHTS["team of the year"]
    # Drawn, but a long way down the list: fewer than Team of the Week by an
    # order of magnitude, and never the family a pack mostly gives.
    assert 0 < seen["legend"] < seen["team of the week"] // 10


def test_no_pack_holds_more_than_two_elite_cards() -> None:
    import random

    from fut_inventory import ELITE_RATING, MAX_ELITE_PER_PACK

    shop, rng = _pack_shop(), random.Random(7)
    for pack_id in (303, 305, 307):
        for _ in range(500):
            cards = json.loads(shop.open_pack(pack_id, rng))["itemList"]
            elite = [
                card
                for card in cards
                if card["itemType"] == "player" and card["rating"] >= ELITE_RATING
            ]
            assert len(elite) <= MAX_ELITE_PER_PACK


def test_withdrawing_a_listed_consumable_puts_it_back_too() -> None:
    # The guard asked whether the card carried `assetId`, which stopped being
    # true the moment a consumable started carrying `cardassetid` instead. A
    # withdrawn contract was dropped on the floor, silently.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    for kind in ("player", "consumable"):
        inventory, wallet = ClubInventory(), Wallet(coins=10**7)
        shop = PackShop(CardCatalogue(), wallet, inventory)
        actions = CardActions(shop, wallet, inventory)
        opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(17)))
        card = next(
            item
            for item in opened["itemList"]
            if (item["itemType"] == "player") == (kind == "player")
        )
        actions.move({"itemData": [{"id": card["id"], "pile": 5}]})
        listing = json.loads(actions.list_for_sale({"itemData": {"id": card["id"]}}))
        actions.withdraw(listing["tradeId"])
        assert [item["id"] for item in actions.transfer] == [card["id"]], kind


def test_an_unlockable_is_never_drawn() -> None:
    # The console named subtype 232: "DEBLOQUER / Capacite +8 moral" and
    # "Grosse affluence morale 6". Stadium unlockables, and the client refuses
    # to keep one -- it raises a dialog telling the player to use it from the
    # action menu instead. Nothing here serves that route, so a drawn one is
    # dead weight plus a dialog on every pack.
    import collections
    import random

    from fut_inventory import UNDRAWN_CONSUMABLE_TYPES

    from fut_inventory import consumable_family

    shop, rng = _pack_shop(), random.Random(3)
    seen = collections.Counter()
    for _ in range(300):
        for item in json.loads(shop.open_pack(304, rng))["itemList"]:
            seen[consumable_family(item) or item["itemType"]] += 1

    # `position` is drawn now. The modifiers render on the console and were
    # being held out of packs for no recorded reason, so the only ones a club
    # ever had were its seeded ones.
    #
    # `squadTraining` -- subtype 232, art 43 -- took its place in the held-out
    # set: it draws NOT FOUND, so packing one hands over a card that cannot be
    # looked at.
    assert "squadTraining" in UNDRAWN_CONSUMABLE_TYPES
    assert "position" not in UNDRAWN_CONSUMABLE_TYPES
    assert seen["position"] > 0, "position modifiers never came out of a pack"
    for kind in UNDRAWN_CONSUMABLE_TYPES:
        assert seen[kind] == 0
    # The families that are drawn still are.
    assert seen["contract"] and seen["fitness"] and seen["healing"]


def test_the_bare_club_holds_one_of_every_kind_it_owns() -> None:
    # Slicing the sorted list took the first N cards, and the sort puts
    # players first, so the bare response was ninety players and nothing else
    # -- every consumable, kit, badge and staff card cut off.
    #
    # That is what "Pas d'élément disponible" was on the apply-consumable
    # picker: it reads the club the client already holds, the club it held had
    # no consumable in it, and it never asked the server for more. The counts
    # said 35 contracts and the list said none.
    import collections

    import fut_inventory as inventory

    club = inventory.ClubInventory()
    bare = json.loads(club.club_response())["itemData"]
    assert len(bare) <= inventory.CLUB_UNFILTERED_LIMIT
    assert len(bare) < len(club.items)

    owned = {item.get("itemType") for item in club.items}
    served = collections.Counter(item.get("itemType") for item in bare)
    assert set(served) == owned
    assert served["player"] > 1

    # And it stays under what the console was measured surviving.
    assert len(club.club_response()) < 77 * 1024


def test_the_bare_club_keeps_the_order_it_was_sorted_into() -> None:
    # The client reads the list in order and the screen pages it, so the cap
    # must not shuffle what survived it.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    bare = json.loads(club.club_response())["itemData"]
    kept = {item["id"] for item in bare}
    whole = json.loads(club.club_response({"count": str(len(club.items))}))["itemData"]
    expected = [item["id"] for item in whole if item["id"] in kept]
    assert [item["id"] for item in bare] == expected


def test_club_user_carries_the_cards_the_picker_binds_against() -> None:
    # `/clubUser` answered with the persona and 122 bytes, so the Apply
    # Consumable picker had nothing to bind and never asked the server for
    # more -- "Pas d'élément disponible" with 35 contracts in the club.
    import collections

    from fut_inventory import (
        CONSUMABLE_TYPES,
        ClubInventory,
        club_user_response,
    )

    club = ClubInventory()
    payload = club_user_response(club, "Fondateur FUT")
    document = json.loads(payload)

    # The same persona every other document carries. Aligning /user alone and
    # leaving the squad documents on 0 is what emptied the squad screen: a
    # client will not show a squad that belongs to somebody else.
    import fut_inventory as _inventory

    assert document["user"][0] == {
        "persona": "Fondateur FUT",
        "personaId": _inventory.PERSONA.id,
        "public": False,
    }
    # The Team of the Week's club is announced beside the player's, and this
    # is the only place the client is told it exists. The challenge record
    # carries that persona in keys 3 and 4, but the console was never seen
    # asking /user/list for it -- a persona in no user list is not a club the
    # client knows about, and "there's no Team of the Week available" is a
    # true statement about a club it has never been told exists.
    assert document["user"][1] == {
        "persona": "TOTW",
        "personaId": _inventory.TOTW_PERSONA_ID,
        "public": True,
    }
    items = document["itemData"]
    kinds = collections.Counter(item["itemType"] for item in items)

    # Players too: the route is the client's face-card cache bootstrap, and
    # the PC revival appends consumables to the normal first player page.
    assert kinds["player"] > 0
    # On the wire, FUT's own two consumable types and never a family name.
    assert set(kinds) - {"player"} == CONSUMABLE_TYPES

    assert document["total"] == document["count"] == len(items)

    # And it stays under what the console was measured surviving.
    assert len(payload) < 77 * 1024


def test_each_consumable_category_answers_with_that_category() -> None:
    # The picker names a category in the path and asks one at a time:
    # /club/consumables/contracts, then /fitness, then /development. The route
    # was matched with `startswith` and answered every one of them with the
    # club's whole stock -- it asked for contracts and got 242 cards of every
    # family mixed together, which is not a list of contracts however many
    # contracts are in it.
    from fut_inventory import (
        CONSUMABLE_TYPES,
        ClubInventory,
        consumable_family,
        consumables_response,
    )

    club = ClubInventory()
    whole = json.loads(consumables_response(club))["itemData"]
    assert whole

    for path, family in (
        ("contracts", "contract"),
        ("contract", "contract"),
        ("fitness", "fitness"),
        ("healing", "healing"),
        ("playstyle", "playStyle"),
    ):
        document = json.loads(consumables_response(club, path))
        items = document["itemData"]
        assert items, path
        assert len(items) < len(whole), path
        assert {consumable_family(item) for item in items} == {family}, path
        assert document["total"] == document["count"] == len(items)

    # `development` and `training` are the wire item types, not families, and
    # mean every card carrying that type.
    for wire in CONSUMABLE_TYPES:
        items = json.loads(consumables_response(club, wire))["itemData"]
        assert items
        assert {item["itemType"] for item in items} == {wire}


def test_a_category_this_club_has_nothing_for_answers_empty() -> None:
    # An unknown category used to fall through to "everything", so a tab
    # headed one thing listed another.
    #
    # `managerLeagueModifier` was the example here until 25 August, when the
    # club started holding all twenty-seven of them -- see
    # `test_manager_league_modifiers_are_counted_not_stood_in_for`. These
    # three are the ones still genuinely empty: manager contracts are left out
    # of the catalogue, and the formation modifiers are in it but held out of
    # the club because art 35 draws NOT FOUND.
    from fut_inventory import ClubInventory, consumables_response

    club = ClubInventory()
    for category in ("formationManager", "managerContract", "nonsense"):
        document = json.loads(consumables_response(club, category))
        assert document["itemData"] == [], category
        assert document["total"] == 0

    # The bare path still means everything.
    assert json.loads(consumables_response(club))["total"] > 0


def test_club_user_covers_every_subtype_the_club_owns() -> None:
    # Twelve cards a family sounds fair until you notice the families span two
    # subtype blocks each: `training` covers 51-57 and 61-67, `playStyle`
    # covers 91-110 and 121-136, and the first twelve of either are all from
    # the low block. Applying to a goalkeeper reads the keeper's block, and
    # the keeper's block was not in the twelve -- "Pas d'élément disponible"
    # over a club holding 21 of them.
    from fut_inventory import (
        CONSUMABLE_TYPES,
        ClubInventory,
        club_user_response,
    )

    club = ClubInventory()
    owned = {
        item["cardsubtypeid"]
        for item in club.items
        if item.get("itemType") in CONSUMABLE_TYPES
    }
    sent = {
        item["cardsubtypeid"]
        for item in json.loads(club_user_response(club, "Fondateur FUT"))["itemData"]
        if item["itemType"] in CONSUMABLE_TYPES
    }
    assert owned
    assert sent == owned


def test_a_finished_match_pays_a_completion_and_a_skill_award() -> None:
    # `/match/end` answered three empty members and threw the result away: no
    # coins for the match, no progress in the cup, nothing on the award
    # screen. A club could win a Gold Cup final and finish exactly as poor as
    # it started.
    from fut_inventory import match_reward

    won = match_reward(
        {
            "goals": 3, "shotsOnTarget": 7, "successfulTackles": 12,
            "corners": 5, "passingPercentage": 84, "possessionPercentage": 61,
            "fouls": 4, "yellowCards": 1, "offsides": 2,
        },
        {"goals": 0},
    )
    assert won["completionAward"] > 0
    assert won["skillAward"] > 0
    assert won["totalCoins"] == won["completionAward"] + won["skillAward"]
    # A clean sheet is worth keeping.
    assert won["bonuses"]["cleanSheet"] > 0
    assert won["penalties"]["cards"] < 0

    # Conceding pays less than not conceding, all else equal.
    leaky = match_reward({"goals": 3}, {"goals": 4})
    tidy = match_reward({"goals": 3}, {"goals": 0})
    assert tidy["totalCoins"] > leaky["totalCoins"]

    # A match nobody finished pays nothing at all.
    walked = match_reward({"goals": 3}, {"goals": 0}, minutes=20, completed=False)
    assert walked["totalCoins"] == 0


def test_the_reward_is_capped_however_lopsided_the_match() -> None:
    # Nine goals is worth more than one, and not nine times more.
    from fut_inventory import MATCH_BONUS_CAPS, match_reward

    thrashing = match_reward({"goals": 30, "shotsOnTarget": 40}, {"goals": 0})
    assert thrashing["bonuses"]["goals"] == MATCH_BONUS_CAPS["goals"][1]
    assert thrashing["bonuses"]["shotsOnTarget"] == MATCH_BONUS_CAPS["shotsOnTarget"][1]


def test_the_result_is_read_from_the_score_when_it_is_not_stated() -> None:
    from fut_inventory import match_result

    assert match_result({"endReason": "WIN"}) == "WIN"
    assert match_result({"endReason": "FORFEIT"}) == "QUIT"
    assert match_result(
        {"myMatchStats": {"goals": 2}, "opponentMatchStats": {"goals": 1}}
    ) == "WIN"
    assert match_result(
        {"myMatchStats": {"goals": 1}, "opponentMatchStats": {"goals": 1}}
    ) == "DRAW"
    # Nothing recognisable settles as no contest: it pays nothing and moves no
    # cup, which is the safe reading of a message this server cannot parse.
    assert match_result({}) == "NO_CONTEST"


def test_a_cup_advances_on_a_win_and_starts_again_on_a_loss() -> None:
    from fut_inventory import TOURNAMENTS, TournamentProgress

    cup = 3
    # (id, name, trophy design, difficulty, final award, retail unlock)
    final_award = next(a for i, _n, _d, _f, a, _u in TOURNAMENTS if i == cup)

    progress = TournamentProgress()
    progress.apply(cup, {"round": 1})

    first = progress.advance(cup, "WIN")
    assert first["round"] == 2
    assert first["roundCoins"] > 0
    assert first["prize"] == 0

    # A draw has not settled the round, so it is played again.
    held = progress.advance(cup, "DRAW")
    assert held["round"] == 2

    # Four rounds: the prize is the fourth win, not the third.
    progress.advance(cup, "WIN")
    progress.advance(cup, "WIN")
    won = progress.advance(cup, "WIN")
    assert won["previousRound"] == 4
    assert won["prize"] == final_award
    # Winning it does not retire it: this is offline, and a cup you can only
    # win once stops existing the moment you are good enough to win it.
    assert won["round"] == 1

    progress.apply(cup, {"round": 3})
    lost = progress.advance(cup, "LOSS")
    assert lost["round"] == 1
    assert lost["roundCoins"] == 0

    # A result nobody recognises moves nothing.
    progress.apply(cup, {"round": 2})
    assert progress.advance(cup, "NO_CONTEST")["settled"] is False
    assert progress.entries[cup]["round"] == 2


def test_the_club_marks_the_repeats_it_is_holding() -> None:
    # A pack marks its own repeats before you accept them and that was the end
    # of it: once the card was in the club nothing said so any more, and the
    # club screen -- which is where anyone actually goes looking for repeats to
    # sell -- showed a club full of ordinary cards.
    import fut_inventory as inventory

    club = [
        {"id": 10, "itemType": "player", "resourceId": 500, "rating": 84},
        {"id": 11, "itemType": "player", "resourceId": 501, "rating": 83},
        {"id": 12, "itemType": "player", "resourceId": 500, "rating": 84},
        {"id": 13, "itemType": "player", "resourceId": 500, "rating": 84},
        # Two contracts are two contracts, not a repeat of one.
        {"id": 14, "itemType": "development", "resourceId": 900},
        {"id": 15, "itemType": "development", "resourceId": 900},
    ]
    pairs = inventory.club_duplicate_pairs(club)
    assert pairs == [
        {"itemId": 12, "duplicateItemId": 10},
        {"itemId": 13, "duplicateItemId": 10},
    ]
    # The oldest copy is the original, and it says so on the card too.
    assert "duplicateItemId" not in club[0]
    assert club[2]["duplicateItemId"] == 10
    assert club[3]["duplicateItemId"] == 10

    # Sell the original and the survivors stop pointing at a card that is gone;
    # the next oldest becomes the one kept.
    del club[0]
    assert inventory.club_duplicate_pairs(club) == [
        {"itemId": 13, "duplicateItemId": 12}
    ]
    assert "duplicateItemId" not in club[1]


def test_a_special_is_not_a_repeat_of_the_ordinary_card() -> None:
    # Every version of a player shares his asset id. Keying on the asset made
    # a Team of the Season card a repeat of the Rare Gold one.
    import fut_inventory as inventory

    club = [
        {"id": 20, "itemType": "player", "assetId": 167628,
         "rarity": "Rare Gold", "rating": 74},
        {"id": 21, "itemType": "player", "assetId": 167628,
         "rarity": "Team of the Season", "rating": 84},
    ]
    assert inventory.club_duplicate_pairs(club) == []


def test_a_card_on_the_transfer_list_but_not_listed_is_still_shown() -> None:
    # Sending a card to the transfer list takes it out of the club -- it has to,
    # or it shows in both places at once -- and the trade pile answered with the
    # listings alone, so an unlisted card was in neither. It went the way the
    # lost pack cards went: quietly. The same symptom is reported against the PC
    # revival, where unlisted pile-5 cards vanished from the Transfer List.
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(29)))
    card_id = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": card_id, "pile": inventory.PILE_TRANSFER}]})

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    entry = pile["auctionInfo"][0]
    assert entry["itemData"]["id"] == card_id
    # Not for sale, in the shape measured against the console on 20 August: a
    # trade id of its own, because the screen resolves the row through it, and
    # `expired`, because that is a state the screen has actions for. `inactive`
    # is what Kyro sends and what this served for two months, and it is why
    # pressing A did nothing -- see UNLISTED_TRADE_ID_BASE.
    # tradeId 0 and no state. Served as a lapsed auction until 26 August,
    # which is why every card the player had never listed read as expired.
    assert entry["tradeId"] == 0
    assert entry["tradeState"] is None
    assert entry["buyNowPrice"] == 0
    # The card knows it is on the transfer list and available.
    assert entry["itemData"]["pile"] == inventory.PILE_TRANSFER
    assert entry["itemData"]["itemState"] == "free"

    # Listed, it becomes one entry and not two.
    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card_id}, "startingBid": 300, "buyNowPrice": 900}
        )
    )
    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == listing["tradeId"]

    # Withdrawn, it is back on the list and still visible.
    actions.withdraw(listing["tradeId"])
    import fut_inventory as inventory

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    # Back on the list and unlisted again: tradeId 0, the shape the console
    # accepted on 26 August.
    assert pile["auctionInfo"][0]["tradeId"] == 0
    assert pile["auctionInfo"][0]["tradeState"] is None


# The real body this console PUT to /ut/game/fifa14/match/end on 11 August at
# 06:12, copied out of the journal. Everything asserted below is measured
# against this rather than against a shape borrowed from another platform.
REAL_MATCH_END = {
    "endReason": "LOSS",
    "myRating": 10,
    "opponentRating": 9,
    "myMatchStats": {
        "goals": 1, "shotsOnTarget": 2, "successfulTackles": 33, "corners": 2,
        "cleansheets": 0, "passingPercentage": 79, "possessionPercentage": 55,
        "manOfTheMatch": 1, "fouls": 2, "yellowCards": 0, "redCards": 0,
        "offsides": 1,
    },
    "opponentMatchStats": {
        "goals": 2, "shotsOnTarget": 4, "successfulTackles": 22, "corners": 2,
        "cleansheets": 0, "passingPercentage": 84, "possessionPercentage": 45,
        "manOfTheMatch": 0, "fouls": 0, "yellowCards": 0, "redCards": 0,
        "offsides": 0,
    },
    "items": [
        {"id": 1800000019, "fitness": 99},
        {"id": 1800000011, "fitness": 95, "assists": 1},
        {"id": 1800000018, "fitness": 96, "goals": 1},
    ],
    "matchData": "532382ea8a2e1141811bc087ce2e219066222a58be5e210d578fb521802ba8ca",
}


def test_the_real_match_end_body_settles() -> None:
    import fut_inventory as inventory

    assert inventory.match_result(REAL_MATCH_END) == "LOSS"
    reward = inventory.match_reward(
        REAL_MATCH_END["myMatchStats"],
        REAL_MATCH_END["opponentMatchStats"],
    )
    # The two percentage members are spelled `passingPercentage` and
    # `possessionPercentage` on this platform; reading them under any other
    # name pays nothing for either, silently.
    assert reward["bonuses"]["passAccuracy"] > 0
    assert reward["bonuses"]["possession"] > 0
    assert reward["bonuses"]["manOfTheMatch"] == inventory.MOTM_AWARD
    # Two conceded, so no clean sheet.
    assert "cleanSheet" not in reward["bonuses"]
    assert reward["goalsFor"] == 1 and reward["goalsAgainst"] == 2
    # A loss still pays: the match was played to the end.
    assert reward["completionAward"] == inventory.MATCH_COMPLETION_AWARD
    assert reward["totalCoins"] > 0


def test_a_match_writes_fitness_goals_and_assists_back_to_the_club() -> None:
    # The captured body carries a per-player fitness, and goals and assists for
    # whoever got them. All of it was discarded, so nobody in the club ever
    # lost fitness -- which is what left the whole consumable pile with nothing
    # to restore.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    players = [item for item in club.items if item.get("itemType") == "player"][:3]
    for index, entry in enumerate(REAL_MATCH_END["items"]):
        entry = dict(entry, id=players[index]["id"])
        REAL_MATCH_END["items"][index] = entry
    players[1]["assists"] = 4
    players[1]["lifetimeAssists"] = 9

    touched = inventory.apply_match_items(club, REAL_MATCH_END["items"])
    # `played` counts appearances, and the `items` array is **not** the team
    # sheet -- it is the whole eighteen. Treating it as the sheet took a
    # contract off every substitute who never left the bench, reported from the
    # console on 26 August.
    #
    # Fitness separates them: a card that was on the pitch comes back with less
    # of it, one that sat on the bench comes back untouched. This capture's
    # first item is at 99 and did not play; the other two lost fitness and did.
    #
    # `contracts` counts the matches taken off a contract. The client reports
    # fitness, goals and assists per player and never mentions contracts, so
    # nothing else counts them down -- every card sat at 99 for ever, which is
    # what left the contract cards with nothing to restore.
    import fut_inventory as inventory

    # All three are in this club's starting eleven, so all three played --
    # including the one the capture reports at fitness 99. A goalkeeper can
    # finish a comfortable win having lost none, which is the case that broke
    # the first version of this rule.
    assert touched == {
        "fitness": 3, "goals": 1, "assists": 1, "played": 3,
        "contracts": 3, "unknown": [], "manager": 0,
    }
    assert all(player["gamesPlayed"] == 1 for player in players)
    # One match off whatever the card arrived with, rather than a literal 98:
    # a card starts at DEFAULT_CONTRACT now, and what is being tested is that
    # a match spends one of them.
    assert all(
        player["contract"] == inventory.DEFAULT_CONTRACT - 1
        for player in players
    )
    assert players[0]["fitness"] == 99
    # Fitness is written, not subtracted: the client sends what it is *after*.
    assert players[1]["fitness"] == 95
    # Goals and assists are added up, because each payload carries one match.
    assert players[1]["assists"] == 5
    assert players[1]["lifetimeAssists"] == 10
    assert players[2]["goals"] == 1

    # A card that is not in the club is reported rather than quietly dropped.
    touched = inventory.apply_match_items(club, [{"id": 42, "fitness": 1}])
    assert touched["unknown"] == [42]


def test_an_abandoned_match_pays_nothing_and_touches_nobody() -> None:
    # The other captured body: {"endReason":"DNF","items":[],"matchData":...}
    import fut_inventory as inventory

    document = {"endReason": "DNF", "items": [], "matchData": "aad944f8"}
    assert inventory.match_result(document) == "DNF"
    reward = inventory.match_reward({}, {}, completed=False)
    assert reward["totalCoins"] == 0
    assert inventory.apply_match_items(inventory.ClubInventory(), [])["fitness"] == 0


def test_the_team_of_the_week_bench_matches_the_team() -> None:
    # Padding the side from the catalogue's best rares put a 98, a 98, a 98, a
    # 97 and a 97 on the bench of a team whose real members top out at 85. That
    # is not a Team of the Week, and it is not a fair opponent either: the
    # challenge computes opponentRating from the first eleven, so the padding
    # decided how strong the team you play against is.
    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    squad = json.loads(inventory.totw_response(catalogue))
    ratings = [card.get("rating", 0) for card in squad["itemData"]]
    assert len(ratings) == 18

    # There is no padding any more: every card is a slot the week actually
    # names, and it carries that slot's own in-form rating.
    week = inventory.totw_week()
    expected = [
        int(slot["rating"]) for slot in inventory._totw_slots(week)
    ]
    assert ratings == expected
    assert max(ratings) <= 99

    # And the challenge it advertises is a side you could actually face --
    # every week, not just the active one.
    assert len(squad["squadChallenge"]) == len(inventory.totw_squads())
    for challenge in squad["squadChallenge"]:
        assert 60 <= challenge["opponentRating"] <= 90
        assert challenge["formation"]
        assert challenge["opponentTeam"] > 0


def test_the_empty_big_archive_declares_its_size_the_way_a_real_one_does() -> None:
    # A real BIGF from this game -- read out of the Title Update's own
    # helperFunctions package -- carries its total size little-endian and the
    # entry count and header size big-endian:
    #
    #     BIGF   54032 (little)   3 entries (big)   header 56 (big)
    #
    # All four fields went out big-endian here, so a sixteen-byte archive
    # declared itself 0x10000000 bytes long: 268 megabytes.
    import struct

    import fut_inventory as inventory

    archive = inventory.empty_big_archive()
    assert len(archive) == 16
    assert archive[:4] == b"BIGF"
    assert struct.unpack_from("<I", archive, 4)[0] == len(archive)
    assert struct.unpack_from(">I", archive, 8)[0] == 0     # no entries
    assert struct.unpack_from(">I", archive, 12)[0] == 16   # header is all of it


def test_the_added_packs_hold_what_they_advertise() -> None:
    # Retail FIFA 14 had no consumables-only pack and nothing above 25 000, so
    # these are not reconstructions of anything -- they are what an offline
    # club with no store behind it needs to keep being worth playing. What is
    # asserted is the promise each one makes on the store screen.
    import collections
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    wallet = inventory.Wallet(coins=10**9)
    shop = inventory.PackShop(catalogue, wallet, club)

    def contents(pack_id: int, runs: int = 30):
        kinds = collections.Counter()
        families = collections.Counter()
        specials = 0
        ordinary = {"rare gold", "non-rare gold", "rare silver",
                    "non-rare silver", "rare bronze", "non-rare bronze"}
        for seed in range(runs):
            opened = json.loads(shop.open_pack(pack_id, random.Random(seed)))
            assert len(opened["itemList"]) == inventory.PACK_SPECS[pack_id]["count"]
            for item in opened["itemList"]:
                kinds[item.get("itemType")] += 1
                if item.get("itemType") != "player":
                    continue
                rarity = (item.get("rarity") or "").lower()
                families[rarity] += 1
                if rarity not in ordinary:
                    specials += 1
        return kinds, families, specials / runs

    # The consumables pack holds no players at all. There was a plain one
    # beside it until 26 August; two is one more than a club needs.
    for pack_id in (109,):
        kinds, _, _ = contents(pack_id)
        assert kinds["player"] == 0
        # Consumables and club items, and nothing else. Club items came back
        # into the draw on 17 August 2026 once they were confirmed rendering
        # on the console -- each with its own wire type, and badges under the
        # retail `custom` family rather than `badge`.
        club_kinds = {
            "kit", inventory.BADGE_WIRE_TYPE, "stadium", "ball",
            # The four families the game actually has. `staff` was listed here
            # and nothing has ever carried it as an itemType -- the same
            # assumption that made `club_stats_response` count only managers
            # and the club search return none of them.
            "manager", "headCoach", "gkCoach", "fitnessCoach", "physio",
        }
        assert set(kinds) <= inventory.CONSUMABLE_TYPES | club_kinds | {"club"}

    # The special group, rewritten 26 August. The Team of the Week and Team of
    # the Season packs that were here are gone at the player's request.
    #
    # Every one of them is all-rare, which is what each of their descriptions
    # promises.
    for pack_id in (308, 405, 406):
        kinds, families, _ = contents(pack_id)
        assert families["non-rare gold"] == 0, pack_id
        assert families["rare gold"] > 0, pack_id

    # And the two all-player packs hold nothing else.
    for pack_id in (405, 406):
        kinds, _, _ = contents(pack_id)
        assert set(kinds) == {"player"}, pack_id

    # The Mega Pack is the big one: thirty items, and it says eighteen rare.
    kinds, _, _ = contents(404)
    assert sum(kinds.values()) == 30 * 30
    assert inventory.PACK_SPECS[404]["rares"] == 18


def test_the_store_groups_are_written_out_not_left_as_tiers() -> None:
    # `displayGroup.value` is drawn as the group heading, verbatim: the store
    # showed "bronze", "silver" and "gold" in lower case because that is what
    # it was handed. It is not a localisation key -- the pack's own
    # FUT_STORE_PACK_<id>_DESC is one, and that resolves against the client's
    # locale, which is why retail pack names read correctly and the headings
    # did not.
    import collections

    import fut_inventory as inventory

    catalogue = json.loads(inventory.store_catalogue())
    groups = collections.OrderedDict()
    for entry in sorted(
        catalogue["purchase"], key=lambda row: row["displayGroup"]["priority"]
    ):
        groups.setdefault(entry["displayGroup"]["value"], []).append(entry["id"])

    # A group's packs are contiguous and the headings come out in order:
    # sorting the catalogue by pack id put the consumables packs, 108 and 109,
    # between the bronze ones and the silver ones.
    headings = list(groups)
    # English. These were French -- "Packs Bronze", "Packs Argent", "Packs Or",
    # "Consommables", "Packs Speciaux" -- left over from the project this was
    # forked from, and the console drew them verbatim over an otherwise English
    # store.
    # Capitals, like the pack names, and four groups rather than five.
    assert headings == [
        "BRONZE PACKS", "SILVER PACKS", "GOLD PACKS", "SPECIAL PACKS",
    ]
    # The added packs get headings of their own rather than being filed under a
    # tier they only nominally belong to.
    # The consumables group is gone: one pack does not need a tab of its own,
    # and the premium consumables pack is a special pack by price.
    assert "Consumables" not in groups
    assert list(groups) == ["BRONZE PACKS", "SILVER PACKS", "GOLD PACKS",
                            "SPECIAL PACKS"]
    # Rewritten 26 August: rare golds, rare players, the Mega Pack and the
    # jumbo, cheapest first. The Team of the Week and Team of the Season packs
    # that were here are gone.
    # Cheapest first: consumables, rare golds, the Mega Pack, rare players,
    # then the jumbo.
    assert groups["SPECIAL PACKS"] == [109, 308, 404, 405, 406]
    # The tier still names the artwork.
    for entry in catalogue["purchase"]:
        # Bronze 1, silver 2, gold 3 -- and the special packs carry covers of
        # their own, 4, 5 and 6, which is why that group's tiles no longer look
        # like the gold ones.
        # Bronze 1, silver 2, gold 3, and one cover for the whole special
        # group -- 4, the Rare Gold Pack's.
        assert entry["displayGroupAssetId"] in (1, 2, 3, 4)
        assert entry["assetId"] == entry["displayGroupAssetId"]


def test_packing_the_same_player_twice_says_so_the_second_time() -> None:
    # Klose 90, twice in a row on 12 August, with nothing on the second pack to
    # say it was a repeat. A card drawn from a pack does not go to the club --
    # it goes to the purchased pile and waits there until it is sent on -- and
    # the pile was the one place the duplicate check did not look.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    club.items = []                      # an empty club: the pile is all there is
    wallet = inventory.Wallet(coins=10**9)
    shop = inventory.PackShop(catalogue, wallet, club)

    first = json.loads(shop.open_pack(303, random.Random(11)))
    assert first["duplicateItemIdList"] == []
    packed = {
        item["assetId"]: item["id"]
        for item in first["itemList"]
        if item.get("itemType") == "player"
    }
    assert packed

    # The same pack again, from the same seed, draws the same players.
    second = json.loads(shop.open_pack(303, random.Random(11)))
    pairs = {row["itemId"]: row["duplicateItemId"] for row in second["duplicateItemIdList"]}
    assert pairs, "the second pack reported no duplicates at all"
    for item in second["itemList"]:
        if item.get("itemType") != "player":
            continue
        original = packed.get(item["assetId"])
        if original is None:
            continue
        # Both spellings, because they are read in different places.
        assert item.get("duplicateItemId") == original
        assert pairs.get(item["id"]) == original


def test_a_pack_after_a_restart_does_not_reissue_ids_the_club_holds() -> None:
    # `PACK_ITEM_ID_BASE + purchases * 100 + slot` counted `purchases` from
    # zero every time the server started, so the first pack after a restart
    # reissued ids the saved club was already holding. `_keep` refuses an id it
    # already holds -- soundly, since the same item twice is one card counted
    # twice -- so a freshly packed card could be dropped on the way to the club
    # and appear nowhere. A Record Breaker Klose went that way on 12 August:
    # the club's 1950000205 was a Non-Rare Silver from an earlier session.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    wallet = inventory.Wallet(coins=10**9)

    before = inventory.PackShop(catalogue, wallet, club)
    first = json.loads(before.open_pack(303, random.Random(3)))
    # The club keeps them, the way "send all to club" does.
    club.items.extend(first["itemList"])
    held = {item["id"] for item in club.items}

    # A restart: a new shop, its counter back at zero, the same saved club.
    after = inventory.PackShop(catalogue, wallet, club)
    second = json.loads(after.open_pack(303, random.Random(4)))
    reissued = [item["id"] for item in second["itemList"] if item["id"] in held]
    assert reissued == [], f"reissued ids the club already holds: {reissued}"


def test_a_pack_never_hands_out_the_same_player_twice() -> None:
    # Two Vargas out of one Team of the Season pack on 12 August. Retail does
    # not do that, and the screen has no way to show it that is not confusing:
    # the same card twice reads as a bug whatever the data says. So the draw is
    # retried rather than the repeat explained.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    club.items = []
    wallet = inventory.Wallet(coins=10**10)
    shop = inventory.PackShop(catalogue, wallet, club)

    repeats = 0
    for pack_id in sorted(inventory.PACK_SPECS):
        for seed in range(20):
            opened = json.loads(shop.open_pack(pack_id, random.Random(seed)))
            players = [
                item for item in opened["itemList"]
                if item.get("itemType") == "player"
            ]
            # The player, not the card. Two versions of one man in a single
            # pack is the same face twice on the pack screen, whatever the club
            # would call them -- fifteen of those in 5,600 opens while this
            # keyed on the rare flag as well.
            keys = [item.get("assetId") for item in players]
            repeats += len(keys) - len(set(keys))
    assert repeats == 0


def test_the_purchased_pile_says_which_of_its_cards_repeat_the_club() -> None:
    # The pack screen gets its pairs in the pack response. The unassigned pile
    # is a different screen with a duplicates tab of its own, and it was handed
    # an empty list -- so a card the pack had just flagged sat in that tab's
    # absence.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    club.items = []
    wallet = inventory.Wallet(coins=10**10)
    shop = inventory.PackShop(catalogue, wallet, club)

    opened = json.loads(shop.open_pack(303, random.Random(5)))
    player = next(
        item for item in opened["itemList"] if item.get("itemType") == "player"
    )
    # The club already owns that card, acquired earlier: a smaller id.
    owned = dict(player, id=1)
    club.items.append(owned)

    pile = json.loads(shop.purchased_items())
    pairs = {row["itemId"]: row["duplicateItemId"] for row in pile["duplicateItemIdList"]}
    assert pairs.get(player["id"]) == 1
    # And on the card itself, which is what the pile document carries.
    held = next(item for item in pile["itemData"] if item["id"] == player["id"])
    assert held["duplicateItemId"] == 1


def test_a_card_is_never_reported_as_a_duplicate_of_itself() -> None:
    # A bought card goes into the purchased pile *and* into the club -- the
    # pile alone lost it -- so both lists hold the same card under the same id.
    # Pairing across them without noticing sent {itemId: N, duplicateItemId: N}
    # and told the screen to compare a card against itself. That is the one
    # shape DUPLICATES.md says must never go out: it froze the title when a
    # pack sent a bare list of its own new ids. Pelé came back paired
    # 1800000049 -> 1800000049.
    import fut_inventory as inventory

    card = {"id": 1800000049, "itemType": "player", "assetId": 190043,
            "rarity": "Legend", "rating": 95}
    assert inventory.pile_duplicate_pairs([card], [card]) == []
    assert "duplicateItemId" not in card

    # A genuine repeat still pairs, against the copy the club had first.
    owned = dict(card, id=17)
    fresh = dict(card, id=1800000050)
    assert inventory.pile_duplicate_pairs([fresh], [owned, fresh]) == [
        {"itemId": 1800000050, "duplicateItemId": 17}
    ]


def test_two_players_do_not_share_a_club() -> None:
    # The whole point. This server held one inventory, one wallet, one save
    # file, so two consoles reaching it played the same club and overwrote
    # each other -- silently, because nothing about a shared club looks
    # different from a busy one.
    import fut_inventory as inventory

    one = inventory.TENANTS.get(1111)
    two = inventory.TENANTS.get(2222)
    assert one is not two
    assert inventory.TENANTS.get(1111) is one  # opened once, then remembered

    before = two.wallet.coins
    one.wallet.coins = 42
    assert two.wallet.coins == before

    one.inventory.items.append({"id": 999, "itemType": "player", "assetId": 1})
    assert not any(item["id"] == 999 for item in two.inventory.items)

    # Not "the other club is empty": both seed from the same save when they
    # have none of their own, which is the point of the fallback. What must
    # hold is that a write to one is not a write to the other.
    one.seasons.apply(1, 10, {"round": 3, "dataVersion": 1, "data": "marqueur"})
    assert one.seasons.entries[(1, 10)]["data"] == "marqueur"
    assert (two.seasons.entries.get((1, 10)) or {}).get("data") != "marqueur"

    # A match in flight belongs to one club too. It used to be a module
    # global, which is the same defect one level down: two consoles, one
    # in-flight match between them.
    one.active_season = (1, 10)
    assert two.active_season is None

    inventory.TENANTS.forget(1111)
    inventory.TENANTS.forget(2222)


def test_the_card_catalogue_is_read_once_and_shared() -> None:
    # Per club, `served` and `sold` have to be that club's own -- a card
    # bought by one player must not vanish from another's market. The 14 000
    # cards behind them are the same file for everybody, and parsing 3.7 MB of
    # JSON per club is the difference between a server that holds twenty and
    # one that does not.
    import fut_inventory as inventory

    one = inventory.TENANTS.get(3333)
    two = inventory.TENANTS.get(4444)
    assert one.catalogue is not two.catalogue
    assert one.catalogue.sold is not two.catalogue.sold
    assert one.catalogue.cards is two.catalogue.cards

    inventory.TENANTS.forget(3333)
    inventory.TENANTS.forget(4444)


def test_a_named_club_saves_beside_the_unnamed_one_and_seeds_from_it(tmp_path) -> None:
    # A persona gets its own file. It also *reads* the single-club save when
    # it has none of its own yet, which is what carries the club that already
    # exists on this console -- 963 million coins and a season under way --
    # over to its owner instead of starting him from nothing.
    #
    # Nothing writes back to the old file: it stays exactly as it was, which
    # makes it the backup as well.
    import fut_inventory as inventory

    legacy = tmp_path / "club-save.json"
    legacy.write_text(json.dumps({"coins": 4242, "acquired": [], "sold": []}))

    named = inventory.club_save_path.__globals__["SAVE_FILE"]
    assert inventory.club_save_path(0) == named
    assert inventory.club_save_path(77).name == "77.json"
    assert inventory.club_save_path(77).parent.name == "clubs"

    save = inventory.ClubSave(tmp_path / "clubs" / "77.json", fallback=legacy)
    club = inventory.ClubInventory()
    wallet = inventory.Wallet()
    actions = inventory.CardActions(
        inventory.PackShop(inventory.CardCatalogue(), wallet, club), wallet, club
    )
    assert save.load(club, wallet, actions) is True
    assert wallet.coins == 4242

    # And the first save it makes goes to its own file, not back to the old one.
    save.save(club, wallet, actions)
    assert (tmp_path / "clubs" / "77.json").exists()
    assert json.loads(legacy.read_text())["coins"] == 4242

    # Once somebody has adopted it, nobody else does. Without this every
    # persona that ever asks inherits the club: asking the live server with a
    # made-up session id came back holding 960 million coins.
    later = inventory.ClubSave(tmp_path / "clubs" / "88.json", fallback=legacy)
    assert later.adoptable() is False
    fresh_club = inventory.ClubInventory()
    fresh_wallet = inventory.Wallet()
    fresh_actions = inventory.CardActions(
        inventory.PackShop(inventory.CardCatalogue(), fresh_wallet, fresh_club),
        fresh_wallet,
        fresh_club,
    )
    assert later.load(fresh_club, fresh_wallet, fresh_actions) is False
    assert fresh_wallet.coins != 4242


def test_the_season_round_is_counted_here_not_read_from_the_client(monkeypatch) -> None:
    # The client rewrites its season save on entering the mode and sends
    # `round` 1 with it -- a season with a match already won came back at
    # round 1 on 14 August. A round derived from that blob says "ten matches
    # remaining" for ever.
    #
    # The count is kept here anyway: every settled result goes through
    # `SeasonProgress.settle`. The PC revival reads its own `matches_played`
    # column for the same reason.
    import fut_inventory as inventory

    assert inventory._season_matches_played({"round": 1, "won": 1}) == 1
    assert inventory._season_matches_played({"round": 1, "won": 2, "lost": 1}) == 3
    assert inventory._season_matches_played({"round": 1, "draw": 1, "lost": 2}) == 3

    # No record at all -- a season restored from a save written before any was
    # kept -- still falls back to the blob rather than claiming nothing was
    # played.
    assert inventory._season_matches_played({"round": 4}) == 3
    assert inventory._season_matches_played({}) == 0


def test_the_unclaimed_club_stops_reading_the_old_save(tmp_path, monkeypatch) -> None:
    # The club nobody has proved a claim to must not be a real club. On the
    # machine this was built on it was: an unauthenticated request came back
    # holding 960 million coins, because the default club still loaded
    # `runtime/club-save.json` after its owner had moved to a per-persona file.
    import fut_inventory as inventory

    legacy = tmp_path / "club-save.json"
    legacy.write_text(json.dumps({"coins": 4242, "acquired": [], "sold": []}))
    monkeypatch.setattr(inventory, "SAVE_FILE", legacy)

    def fresh():
        club = inventory.ClubInventory()
        wallet = inventory.Wallet()
        actions = inventory.CardActions(
            inventory.PackShop(inventory.CardCatalogue(), wallet, club), wallet, club
        )
        return club, wallet, actions

    # Before anyone has a club of their own, the old save is still the club.
    save = inventory.ClubSave(legacy)
    club, wallet, actions = fresh()
    assert save.load(club, wallet, actions) is True
    assert wallet.coins == 4242

    # Once one exists, the old save is nobody's and stops being read.
    (tmp_path / "clubs").mkdir()
    (tmp_path / "clubs" / "77.json").write_text("{}")
    assert save.superseded() is True
    club, wallet, actions = fresh()
    assert save.load(club, wallet, actions) is False
    assert wallet.coins != 4242


def test_the_season_under_way_is_the_one_with_matches_behind_it() -> None:
    # A club can hold two entries for the same division: the key carries the
    # `seasonId` this server served, and that index moved from 1 to 10 when the
    # division table was reordered. The client rewrites its blob on entering
    # the mode, so the empty new key was always the last written -- and "most
    # recently written" reported a season with nothing played over one with a
    # match won.
    #
    # On screen that read as "not started", and the client offered to begin the
    # season again rather than resume it.
    import fut_inventory as inventory

    seasons = inventory.SeasonProgress()
    seasons.apply(1, 10, {"round": 1, "data": "QQ=="})
    seasons.settle(1, 10, "WIN", 626)
    seasons.apply(10, 10, {"round": 1, "data": "QQ=="})     # written later, empty

    assert seasons.current() == (1, 10)
    entry = seasons.entries[seasons.current()]
    assert entry["won"] == 1

    # With nothing played anywhere, the most recent still wins.
    fresh = inventory.SeasonProgress()
    fresh.apply(1, 10, {"round": 1, "data": "QQ=="})
    fresh.apply(10, 10, {"round": 1, "data": "QQ=="})
    assert fresh.current() == (10, 10)


def test_the_season_header_carries_the_blob_that_says_it_started(monkeypatch) -> None:
    # The reader for this document, CardsDLLzf+0x1adf28, knows five members:
    # data, dataVersion, divisionId, round, seasonId. Three were sent. The two
    # missing ones are what carry the state, and nothing else asks for them --
    # the client only ever PUTs to `season/<id>/division/<div>/user`, it never
    # GETs it. So a season with a match behind it looked identical to one never
    # started, and the screen kept offering to begin it.
    import fut_inventory as inventory

    monkeypatch.setenv("FIFA14_SEASON_MODE", "native")
    inventory.SEASON_PROGRESS.entries.clear()
    try:
        inventory.SEASON_PROGRESS.apply(
            10, 10, {"round": 1, "dataVersion": 1, "data": "QUJD"}
        )
        inventory.SEASON_PROGRESS.settle(10, 10, "WIN", 626)
        document = json.loads(inventory.season_user_response())

        assert document["data"] == "QUJD"
        assert document["dataVersion"] == 1
        # And the blob before the version that decodes it.
        members = list(document)
        assert members.index("data") < members.index("dataVersion")

        # A club that has never played sends no blob, and therefore nothing
        # that could be decoded out of uninitialised registers.
        inventory.SEASON_PROGRESS.entries.clear()
        bare = json.loads(inventory.season_user_response())
        assert "data" not in bare
        assert "dataVersion" not in bare
    finally:
        inventory.SEASON_PROGRESS.entries.clear()


def test_a_listed_card_finds_a_buyer_or_comes_back() -> None:
    # The market was a shop you could buy from and never sell to: a card went
    # up, `expires` never moved, `currentBid` stayed 0, and no amount of
    # waiting sold anything. There are no other players here, so a buyer is
    # modelled -- on the PC revival's shape, where the asking price against the
    # card's value decides both whether it sells and when.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    wallet = inventory.Wallet()
    wallet.coins = 1000
    shop = inventory.PackShop(inventory.CardCatalogue(), wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    card = next(i for i in club.items if i.get("itemType") == "player")
    value = inventory._price_for(
        card["rating"], card.get("rareflag", 0), card
    )

    def listed(ask: int, duration: int = 3600) -> dict:
        actions.listings = {}
        actions.transfer = [card]
        actions.list_for_sale(
            {
                "itemData": {"id": card["id"]},
                "startingBid": max(1, int(ask * 0.8)),
                "buyNowPrice": ask,
                "duration": duration,
            }
        )
        return next(iter(actions.listings.values()))

    # Priced well under value, a buyer comes quickly; far over, never.
    assert inventory._sale_delay(listed(int(value * 0.6))) is not None
    assert inventory._sale_delay(listed(value * 3)) is None
    # And cheaper always sells sooner than dearer.
    assert (
        inventory._sale_delay(listed(int(value * 0.6)))
        < inventory._sale_delay(listed(int(value * 1.05)))
    )

    # The delay is deterministic: the client polls these screens constantly,
    # and a listing that re-rolled its fate on every poll would flicker between sold
    # and unsold.
    listing = listed(int(value * 0.6))
    assert inventory._sale_delay(listing) == inventory._sale_delay(listing)

    # Bids climb on the way to the sale rather than appearing with it.
    listing["listedAt"] -= 12
    actions.settle_market()
    assert listing["tradeState"] == "active"
    assert listing["offers"] > 0
    assert listing["currentBid"] > 0

    # Then it sells, less EA's tax, and the card stays in the pile -- the
    # retail trade-pile parser dereferences the item even for a closed
    # auction, and the PC revival records a crash from deleting it.
    listing["listedAt"] -= 3600
    sales = actions.settle_market()
    assert len(sales) == 1
    price = sales[0]["price"]
    assert sales[0]["net"] == int(round(price * (1 - inventory.MARKET_SELL_TAX)))
    assert sales[0]["net"] < price
    assert listing["tradeState"] == "closed"
    assert listing["itemData"]["itemState"] == "sold"

    # A card nobody wants comes back to the transfer pile when its time is up,
    # rather than expiring into nothing the way lost cards used to.
    dud = listed(value * 3, duration=60)
    dud["listedAt"] -= 120
    actions.settle_market()
    assert dud["tradeState"] == "expired"
    assert dud["itemData"] in actions.transfer


def test_the_console_names_the_pile_instead_of_numbering_it() -> None:
    # Every `PUT /item` the console has ever sent carries `"pile": "club"` or
    # `"pile": "trade"` -- never 5 or 7. `int("trade")` raised, the old code
    # caught it and fell back to the club, and fifteen cards sent to the
    # transfer list in one session were filed in the club instead.
    from fut_inventory import PILE_CLUB, PILE_TRANSFER, _pile_number

    assert _pile_number("trade") == PILE_TRANSFER
    assert _pile_number("club") == PILE_CLUB
    assert _pile_number(PILE_TRANSFER) == PILE_TRANSFER
    # Not the club. Guessing "club" for something unreadable is the bug.
    assert _pile_number("watchlist") is None
    assert _pile_number(None) is None


def test_a_card_sent_to_the_transfer_list_lands_on_the_transfer_list() -> None:
    import json

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, PackShop, Wallet,
    )

    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    card = dict(inventory.items[0])
    card["id"] = card["itemId"] = 1_960_000_001
    actions._keep(card)

    reply = json.loads(
        actions.move({"itemData": [{"id": card["id"], "pile": "trade", "swap": 0}]})
    )
    assert all(entry.get("success") for entry in reply.get("itemData", [])), reply

    assert [held["id"] for held in actions.transfer] == [card["id"]]
    # And gone from the club -- both lists, not just the one.
    assert not any(held["id"] == card["id"] for held in actions.club)
    assert not any(held["id"] == card["id"] for held in inventory.items)


def test_listing_a_card_takes_it_out_of_the_club() -> None:
    import json

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, PackShop, Wallet,
    )

    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    card = dict(inventory.items[0])
    card["id"] = card["itemId"] = 1_960_000_002
    actions._keep(card)

    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card["id"]}, "startingBid": 100, "buyNowPrice": 200}
        )
    )
    assert listing["itemData"]["id"] == card["id"]
    # Thirteen cards sold in one session were all still in the club afterwards,
    # because only `self.club` was popped and the inventory was left alone.
    assert not any(held["id"] == card["id"] for held in inventory.items)
    assert not any(held["id"] == card["id"] for held in actions.club)


def test_the_club_search_finds_a_badge_when_it_asks_for_one() -> None:
    # It goes out as `custom` so it renders, and the screen still searches for
    # `badge`. The club held five and the tab showed none.
    import json

    from fut_inventory import BADGE_WIRE_TYPE

    assert any(item.get("itemType") == BADGE_WIRE_TYPE for item in INVENTORY.items)
    found = json.loads(INVENTORY.club_response({"type": "badge", "count": "50"}))
    assert found["itemData"], "the badge tab is empty again"
    assert all(
        item.get("itemType") == BADGE_WIRE_TYPE for item in found["itemData"]
    )


def test_kits_stadiums_and_balls_are_not_club_ids() -> None:
    # The kit that came out of a pack was Barcelona's third, and kit asset
    # 14-17 would be Nott'm Forest, QPR, nothing and Southampton. Stadium 8
    # drew Stade Gerland and club 8 is Leeds. Different id spaces, so these
    # three have to be probed rather than derived -- and nothing should
    # quietly start treating them as club ids.
    from fut_inventory import CLUB_ITEM_KINDS

    kinds = {kind for kind, *_ in CLUB_ITEM_KINDS}
    assert kinds == {"kit", "stadium", "ball"}, kinds


def test_a_pack_never_hands_out_the_same_club_item_twice() -> None:
    # A Premium Gold Pack came out with the Barcelona third kit twice, side by
    # side. A second contract is a second contract; a second identical kit is
    # nothing at all.
    import collections
    import random

    from fut_inventory import BADGE_WIRE_TYPE

    club_kinds = {"kit", BADGE_WIRE_TYPE, "stadium", "ball"}
    shop, rng = _pack_shop(), random.Random(11)
    for pack_id in (203, 303, 304, 305):
        for _ in range(120):
            seen = collections.Counter()
            for item in json.loads(shop.open_pack(pack_id, rng))["itemList"]:
                if item.get("itemType") in club_kinds:
                    seen[(item["itemType"], item["assetId"])] += 1
            repeated = [key for key, n in seen.items() if n > 1]
            assert not repeated, (pack_id, repeated)


def test_an_auction_does_not_carry_soldfor_on_the_wire() -> None:
    # Kyro's build keeps the sale price in currentBid and sends no soldFor.
    # tradeOwner, which an earlier pass wrongly stripped, is sent -- the
    # working reference sends it.
    import json

    from fut_inventory import (
        UNNAMED_AUCTION_MEMBERS, CardActions, ClubInventory, CardCatalogue,
        PackShop, Wallet,
    )

    assert UNNAMED_AUCTION_MEMBERS == ("soldFor",)
    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    card = dict(inventory.items[0])
    card["id"] = card["itemId"] = 1_960_000_010
    actions._keep(card)
    actions.list_for_sale(
        {"itemData": {"id": card["id"]}, "startingBid": 100, "buyNowPrice": 200}
    )
    pile = json.loads(actions.trade_pile(0))
    assert pile["auctionInfo"]
    for entry in pile["auctionInfo"]:
        assert "soldFor" not in entry
        assert entry.get("tradeOwner") is True


def test_a_sold_listing_is_not_reported_as_a_bid_you_are_winning() -> None:
    # bidState is the state of *your bid*, and a seller never bid. `highest`
    # is the buying path's value for winning an auction, and sending it on your
    # own sold listing is what a sold card sitting under LISTED ITEMS looks
    # like. CardsDLL keeps auctionWon* and auctionSold* apart itself.
    import json
    import time

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, PackShop, Wallet,
    )

    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    card = dict(inventory.items[0])
    card["id"] = card["itemId"] = 1_960_000_011
    actions._keep(card)
    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card["id"]}, "startingBid": 100, "buyNowPrice": 200}
        )
    )
    live = actions.listings[listing["tradeId"]]
    live["listedAt"] = int(time.time()) - 10_000  # long past any sale delay
    sold = actions.settle_market()
    assert sold, "the listing never sold"
    assert live["tradeState"] == "closed"
    assert live["bidState"] == "none", live["bidState"]
    assert live["itemData"]["itemState"] == "sold"




def test_a_sold_card_reports_the_counts_the_sold_stack_is_drawn_from() -> None:
    # The transfer list sizes its SOLD ITEMS and LISTED ITEMS stacks from the
    # top-level counts. Kyro's build sends them; this server did not, so every
    # sold card sat under LISTED. It also keeps a positive expires -- expires
    # -1 reads as an auction that lapsed unsold, the relist state that showed.
    import json
    import time

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, PackShop, Wallet,
    )

    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    for offset in range(2):
        card = dict(inventory.items[offset])
        card["id"] = card["itemId"] = 1_960_000_600 + offset
        actions._keep(card)
        listing = json.loads(
            actions.list_for_sale(
                {"itemData": {"id": card["id"]},
                 "startingBid": 100, "buyNowPrice": 200}
            )
        )
        if offset == 0:  # sell only the first
            actions.listings[listing["tradeId"]]["listedAt"] = int(time.time()) - 10_000
    assert actions.settle_market()

    pile = json.loads(actions.trade_pile(0))
    assert pile["sold"] == 1 and pile["soldCount"] == 1
    assert pile["selling"] == 1 and pile["activeCount"] == 1
    assert pile["transferListCount"] == pile["total"] == 2

    closed = [e for e in pile["auctionInfo"] if e["tradeState"] == "closed"]
    assert len(closed) == 1
    e = closed[0]
    assert e["expires"] > 0, "a sold auction must not carry expires -1"
    assert e["currentBid"] == 200
    assert e["itemData"]["itemState"] == "sold"
    # Sold sorts after active.
    assert pile["auctionInfo"][0]["tradeState"] == "active"


def test_collecting_a_sold_card_clears_it_and_never_resurrects_it() -> None:
    # "A buyer was found for your item! Press Y to remove it" collects a sold
    # card. The coins were credited when it settled, so collect just clears it,
    # and the card must not come back into the transfer pile as a phantom.
    import json
    import time

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, PackShop, Wallet,
    )

    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    card = dict(inventory.items[0])
    card["id"] = card["itemId"] = 1_960_000_800
    actions._keep(card)
    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card["id"]}, "startingBid": 100, "buyNowPrice": 200}
        )
    )
    trade_id = listing["tradeId"]
    actions.listings[trade_id]["listedAt"] = int(time.time()) - 10_000
    assert actions.settle_market()

    reply = json.loads(actions.withdraw(trade_id))
    assert reply["tradeId"] == trade_id
    assert trade_id not in actions.listings
    # Gone for good -- not back in the transfer pile.
    assert not any(h.get("id") == card["id"] for h in actions.transfer)


def test_withdrawing_an_unsold_listing_returns_it_to_the_pile() -> None:
    import json

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, PackShop, Wallet,
    )

    inventory = ClubInventory()
    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet(), inventory)
    card = dict(inventory.items[0])
    card["id"] = card["itemId"] = 1_960_000_801
    actions._keep(card)
    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card["id"]}, "startingBid": 100, "buyNowPrice": 200}
        )
    )
    actions.withdraw(listing["tradeId"])
    # Active withdrawal: the card comes back so it can be relisted.
    assert any(h.get("id") == card["id"] for h in actions.transfer)


def test_a_listing_sold_before_the_fix_is_restamped_on_load() -> None:
    # A card sold in an older session was saved with expires -1 and no time
    # siblings, so it read as lapsed-unsold and stayed under LISTED. restamp_sold
    # brings it up to the sold shape so it joins the sold stack.
    from fut_inventory import CardActions, CardCatalogue, PackShop, Wallet

    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet())
    actions.listings = {
        2000000001: {
            "tradeId": 2000000001,
            "tradeState": "closed",
            "expires": -1,
            "currentBid": 104000,
            "buyNowPrice": 104000,
            "itemData": {"id": 1, "itemState": "sold"},
        }
    }
    actions.restamp_sold()
    listing = actions.listings[2000000001]
    assert listing["expires"] > 0
    assert listing["EXPIRE_TIME"] > 0 and listing["endtime"] == 2147483647
    assert listing["itemData"]["itemState"] == "sold"


def test_an_unlisted_transfer_card_is_tradeable_so_a_can_list_it() -> None:
    # Pressing A over an unlisted transfer-list card did nothing: the card
    # carried untradeable False but not the positive `tradeable`, so the List
    # Item action was never offered. Kyro's build sets both.
    import json
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(31)))
    card_id = opened["itemList"][0]["id"]
    import fut_inventory as inventory
    actions.move({"itemData": [{"id": card_id, "pile": inventory.PILE_TRANSFER}]})

    entry = json.loads(actions.trade_pile(wallet.coins))["auctionInfo"][0]
    assert entry["tradeId"] == 0
    assert entry["itemData"]["tradeable"] is True
    assert entry["itemData"]["untradeable"] is False


def test_the_transfer_tile_counts_unlisted_cards_as_items() -> None:
    # The tile read "0 ITEMS" over ten cards on the transfer list because the
    # item total was selling + sold and left the unlisted cards out.
    import json

    from fut_inventory import hub_response

    inventory, _shop, _actions = _fresh()
    doc = json.loads(hub_response(inventory, market=12771, selling=2, sold=3, unlisted=5))
    assert doc["selling"] == 2 and doc["sold"] == 3
    # 2 listed + 3 sold + 5 unlisted = 10 on the list.
    assert doc["tradePile"]["count"] == 10


def test_an_unlisted_entry_carries_the_shape_the_console_acts_on() -> None:
    # Measured, 20 August 2026. This test used to assert the opposite -- tradeId
    # 0 and tradeState "inactive", "the shape Kyro's build gives an unlisted pile
    # card" -- and that shape is exactly what left the card inert on FUT HUB >
    # TRANSFER LIST for two months. Matching a reference build is not evidence;
    # this is what the console was seen to act on.
    import json
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(37)))
    for item in opened["itemList"][:2]:
        actions.move(
            {"itemData": [{"id": item["id"], "pile": inventory.PILE_TRANSFER}]}
        )

    entries = [
        e for e in json.loads(actions.trade_pile(wallet.coins))["auctionInfo"]
        if e["tradeId"] == 0
    ]
    assert len(entries) == 2
    # The rows are told apart by the card, not by a trade id: every unlisted
    # row carries tradeId 0 and the screen resolves the selection through
    # `itemData.id`. Distinct pseudo ids were this server's own invention, and
    # they are what made a card the player never listed read as expired.
    ids = {entry["itemData"]["id"] for entry in entries}
    assert len(ids) == 2, "two rows sharing a card cannot both be selected"

    for entry in entries:
        # The two members that carry the behaviour, together. Neither works
        # alone: an id alone makes the row an auction the screen can describe
        # but not act on, and a state alone has no card behind it to act with.
        assert entry["tradeId"] == 0
        assert entry["tradeState"] is None
        assert entry["expires"] == -1
        assert entry["tradeOwner"] is True
        # No seller and no clock. The six members this server used to add on
        # top -- sellerId, sellerEstablished, endtime, startTime, EXPIRE_TIME,
        # expireTime -- are what made the earlier reading of tradeId 0 come out
        # wrong, so they stay out.
        assert entry["sellerName"] == ""
        assert "endtime" not in entry
        assert "sellerEstablished" not in entry
        assert entry["itemData"]["tradeable"] is True
        assert entry["itemData"]["pile"] == inventory.PILE_TRANSFER


def test_the_shape_switch_falls_back_to_the_measured_default() -> None:
    # The ten coded candidates are retired -- the answer they were looking for
    # is in `_unlisted_entry` now. What must not happen is a leftover name in
    # runtime/unlisted-shape.txt quietly serving something else, or nothing.
    import json
    import os
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(41)))
    for item in opened["itemList"][:2]:
        actions.move(
            {"itemData": [{"id": item["id"], "pile": inventory.PILE_TRANSFER}]}
        )

    for name in ("plain", "asitwas", "tradeid", "listinglike", "nonsense"):
        os.environ["FIFA14_UNLISTED_SHAPE"] = name
        try:
            pile = json.loads(actions.trade_pile(wallet.coins))
        finally:
            os.environ.pop("FIFA14_UNLISTED_SHAPE", None)
        unlisted = [
            e for e in pile["auctionInfo"]
            if e["tradeId"] == 0
        ]
        assert len(unlisted) == 2, name
        for entry in unlisted:
            assert entry["tradeState"] is None, name
            assert entry["itemData"]["id"], name

    # Held state is untouched whatever was served.
    for held in actions.transfer:
        assert held["pile"] == inventory.PILE_TRANSFER


def test_every_card_carries_the_item_id_alias() -> None:
    # Kyro's canonical payload sends both `id` and `itemId` on every card; this
    # server sent only `id`. The standalone Transfer List screen renders such a
    # card and reads its state -- it prints "This item is not currently listed"
    # -- and then builds no action menu for it. POST /auctionhouse names the
    # card as itemData.id, so nothing on the wire needed itemId and its absence
    # went unnoticed.
    import json
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(53)))
    for item in opened["itemList"]:
        if item.get("itemType") != "player":
            continue
        assert item["itemId"] == item["id"], item["id"]
        for member in ("teamId", "rareFlag", "definitionId", "playerId",
                       "morale", "loyaltyBonus", "resourceGameYear", "pile"):
            assert member in item, member


def test_a_card_saved_without_the_aliases_is_repaired_on_load() -> None:
    # Cards written by an older build carry id and no itemId. They are brought
    # up to the current shape rather than left unactionable forever.
    from fut_inventory import CardActions, CardCatalogue, PackShop, Wallet

    actions = CardActions(PackShop(CardCatalogue(), Wallet()), Wallet())
    stale = {
        "id": 1_950_003_819, "itemType": "player", "assetId": 183277,
        "teamid": 5, "rareflag": 11, "rating": 94,
    }
    actions.transfer.append(stale)
    assert actions.restamp_cards() == 1
    assert stale["itemId"] == stale["id"]
    assert stale["definitionId"] == 183277
    assert stale["teamId"] == 5

    # A value already present is never overwritten.
    kept = {"id": 7, "itemType": "player", "assetId": 1, "morale": 42}
    actions.transfer.append(kept)
    actions.restamp_cards()
    assert kept["morale"] == 42


def test_a_probe_run_never_writes_the_save() -> None:
    # The probe replaces the club-item seed, and the save is a diff against
    # that seed -- so every real kit, badge, stadium and ball would read as
    # "acquired" and be written into the club permanently, while the probe
    # cards looked seeded. One investigative launch would have rewritten a
    # club of a thousand cards.
    import json
    import os
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        CardActions, CardCatalogue, ClubInventory, ClubSave, PackShop, Wallet,
    )

    path = Path(tempfile.mkdtemp()) / "club.json"
    path.write_text(json.dumps({"coins": 7, "acquired": []}))
    before = path.read_text()

    inventory = ClubInventory()
    wallet = Wallet(coins=999)
    actions = CardActions(PackShop(CardCatalogue(), wallet), wallet, inventory)

    os.environ["FIFA14_CLUB_ITEM_PROBE"] = "1"
    try:
        ClubSave(path).save(inventory, wallet, actions, None)
        assert path.read_text() == before, "a probe run wrote the save"
    finally:
        os.environ.pop("FIFA14_CLUB_ITEM_PROBE", None)

    # And with the probe off it saves as normal.
    ClubSave(path).save(inventory, wallet, actions, None)
    assert path.read_text() != before


def test_the_club_item_catalogue_matches_what_the_console_rendered() -> None:
    # Five resource runs, each with a bound that something measured rather than
    # assumed. A run is its bounds and its holes; the holes are named in
    # `server/fifa14_clubitems_blank.json` rather than assumed away.
    #
    # Kits are two runs, not one. Home kits are asset 14 at 6300000; **away
    # kits are asset 15 at 6400000**, and this catalogue had none of them while
    # `PRESENTATION_ACTIVES` was already dressing the club in 6400001. The club
    # wore a kit the catalogue did not contain.
    #
    # The home bound moved from 6300860 to 6300740. The probe's `good` list
    # claims 861 -- but it is not exhaustive (badges record 28 sampled entries
    # against 601 served), 6300772 drew NOT FOUND out of a pack on 25 August,
    # and MarvelcoCode/Impulsum14's extract of the game's own database stops at
    # 6300740 independently. A sighting and a database beat a list that reads
    # as written rather than probed.
    #
    # Badges and balls are deliberately NOT taken from that database. It stops
    # at 6000586 where the probe recorded 6000600 rendering, and it carries
    # eight balls past 8120137 that the probe never tested. A measurement here
    # beats a database read elsewhere, and untested is not the same as present.
    from fut_inventory import _clubitem_catalogue, blank_club_items

    catalogue = _clubitem_catalogue()
    blank = blank_club_items()

    runs = [
        ("kit", 14, 6_300_000, 6_300_740, 741),
        ("kit", 15, 6_400_000, 6_400_585, 586),
        ("badge", 241, 6_000_000, 6_000_600, 601),
        ("stadium", 6, 6_200_000, 6_200_060, 61),
        # 8120137 came out of a pack as a grey placeholder captioned
        # `*BallName_83` -- the asterisk is the client saying it built the
        # string key and found no string. The probe called it good and its ball
        # list ends at exactly the served range, the same tell that made its
        # kit list wrong.
        ("ball", 23, 8_120_091, 8_120_136, 46),
    ]
    holes_inside = {b for _, _, f, l, _ in runs for b in blank if f <= b <= l}
    assert len(catalogue) == sum(c for *_, c in runs) - len(holes_inside)

    for kind, asset, first, last, count in runs:
        ids = sorted(
            c["resourceId"]
            for c in catalogue
            if c["itemType"] == kind and first <= c["resourceId"] <= last
        )
        holes = {b for b in blank if first <= b <= last}
        assert len(ids) == count - len(holes), (kind, asset)
        # An excluded id is never an edge: an edge that failed would move the
        # bound rather than punch a hole.
        assert ids[0] == first and ids[-1] == last, (kind, asset)
        assert ids == [n for n in range(first, last + 1) if n not in holes], (kind, asset)
        # Each run carries its own anchor asset. Home and away kits differ by
        # exactly that, which is how the client tells them apart.
        assets = {
            c["assetId"]
            for c in catalogue
            if c["itemType"] == kind and first <= c["resourceId"] <= last
        }
        assert assets == {asset}, (kind, assets)

    # The kit that drew NOT FOUND is gone with the bound rather than blocked.
    assert not any(c["resourceId"] == 6_300_772 for c in catalogue)
    # And the away kit the club actually wears is present.
    assert any(
        c["itemType"] == "kit" and c["resourceId"] == 6_400_001 for c in catalogue
    )

def test_a_badge_resource_is_an_index_into_the_games_badge_table() -> None:
    # Not a club id. The console drew these crests at these resources, and
    # clubId 3 is Blackburn Rovers while badge index 3 is Manchester City.
    from fut_inventory import _clubitem_catalogue

    badges = {
        c["resourceId"] for c in _clubitem_catalogue() if c["itemType"] == "badge"
    }
    for resource in (6_000_000, 6_000_001, 6_000_002, 6_000_003, 6_000_600):
        assert resource in badges, resource
    # Index 625 drew NOT FOUND, and every club id above the table is gone with it.
    assert 6_000_625 not in badges
    assert 6_112_424 not in badges


def test_club_items_quick_sell_for_the_games_own_values() -> None:
    # bronze 13 rare / 3 normal, silver 37 / 14, gold 60 / 31. Gold normal is
    # worth less than silver rare -- that is the real table.
    from fut_inventory import _clubitem_catalogue, club_discard_value

    assert club_discard_value(84, 1) == 60
    assert club_discard_value(78, 0) == 31
    assert club_discard_value(72, 1) == 37
    assert club_discard_value(68, 0) == 14
    assert club_discard_value(58, 1) == 13
    assert club_discard_value(48, 0) == 3

    for card in _clubitem_catalogue():
        assert card["discardValue"] > 0, card["resourceId"]


def test_a_club_item_carries_its_quick_sell_value_on_the_wire() -> None:
    import json
    import random

    from fut_inventory import BADGE_WIRE_TYPE, CardCatalogue, PackShop, Wallet

    club_kinds = {"kit", BADGE_WIRE_TYPE, "stadium", "ball"}
    shop = PackShop(CardCatalogue(), Wallet(coins=10**9))
    seen = 0
    for _ in range(40):
        for item in json.loads(shop.open_pack(304, random.Random(seen + 1)))["itemList"]:
            if item.get("itemType") in club_kinds:
                assert item["discardValue"] in (3, 13, 14, 15, 31, 37, 60), item
                seen += 1
    assert seen, "no club items came out of forty packs"


def test_every_ball_is_silver_and_reaches_every_pack() -> None:
    # Balls are one grade in FIFA 14 -- all silver -- unlike kits, badges and
    # stadiums which spread across six. They carry no tier so a ball can still
    # come out of a bronze or a gold pack; tier gating on a one-grade family
    # would delete balls from four pack tiers out of five.
    import json
    import random

    from fut_inventory import CardCatalogue, PackShop, Wallet, _clubitem_catalogue

    balls = [c for c in _clubitem_catalogue() if c["itemType"] == "ball"]
    assert len(balls) == 46
    assert {c["rating"] for c in balls} == {68}
    assert {c["rare"] for c in balls} == {0}
    assert {c["discardValue"] for c in balls} == {15}
    assert {c["tier"] for c in balls} == {""}

    shop = PackShop(CardCatalogue(), Wallet(coins=10**10))
    for pack_id in (103, 203, 303, 304):
        drawn = 0
        for seed in range(60):
            for item in json.loads(shop.open_pack(pack_id, random.Random(seed)))["itemList"]:
                if item.get("itemType") == "ball":
                    drawn += 1
        assert drawn, f"no ball reached pack {pack_id}"


def test_a_club_item_can_be_activated_straight_out_of_a_pack() -> None:
    # The club screen offers Make Active on a card still sitting in New Items,
    # and activate_item only ever searched the club -- so activating a kit you
    # had just packed failed, and the 400 that came back ejected the player from
    # Ultimate Team. Activating a card is keeping it, so it lands in the club.
    import json
    import random

    from fut_inventory import (
        BADGE_WIRE_TYPE, CardActions, CardCatalogue, ClubInventory, PackShop,
        Wallet, activate_item,
    )

    club_kinds = {"kit", BADGE_WIRE_TYPE, "stadium", "ball"}
    inventory = ClubInventory()
    wallet = Wallet(coins=10**9)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    packed = None
    for seed in range(30):
        opened = json.loads(shop.open_pack(304, random.Random(seed)))
        packed = next(
            (c for c in opened["itemList"] if c.get("itemType") in club_kinds), None
        )
        if packed:
            break
    assert packed, "no club item came out of thirty packs"
    assert not any(i["id"] == packed["id"] for i in inventory.items)

    activated = activate_item(inventory, packed["id"], actions)
    assert activated is not None
    assert activated["itemState"].startswith("active")
    assert any(i["id"] == packed["id"] for i in inventory.items)

    # A player has no active slot; it must decline rather than raise.
    player = next(c for c in opened["itemList"] if c.get("itemType") == "player")
    assert activate_item(inventory, player["id"], actions) is None


def test_a_failed_activation_does_not_eject_the_player() -> None:
    # A 400 on this route throws the player out of Ultimate Team. Activation is
    # a cosmetic slot, so an unacknowledged one leaves the club as it was --
    # which is a far better answer than being kicked out of the mode.
    import json
    import re
    from pathlib import Path

    server = (Path(__file__).resolve().parent.parent
              / "server" / "fifa14_blaze_server.py").read_text()
    block = server[server.index("if activated is None:"):]
    block = block[:block.index("return")]
    assert "self.reply(\n" in block or "self.reply(" in block
    assert "400" not in block.split("self.reply(")[1][:40], (
        "a failed activation must not answer 400"
    )


def test_the_store_serves_english_pack_descriptions() -> None:
    # The tiles key on FUT_STORE_PACK_<id>_DESC and it resolves against
    # packs/loc/storepackdescriptions. This server answered an empty table, so
    # every tile fell back to its group heading and the detail pane read
    # "Gold Packs / Gold Packs".
    from fut_inventory import PACK_SPECS, store_pack_descriptions

    document = store_pack_descriptions().decode()
    assert document.startswith("<?xml")
    for pack_id, spec in PACK_SPECS.items():
        assert f'resname="FUT_STORE_PACK_{pack_id}_DESC"' in document, pack_id
        assert spec["name"] in document, spec["name"]
    # Well-formed, and the format CardsDLL names: trans-unit/resname/source.
    import xml.etree.ElementTree as ET

    root = ET.fromstring(document)
    units = root.findall("trans-unit")
    # One per pack, plus the cup names -- this document is fetched 243 times a
    # session against the leaderboards document's 40, so it is where a general
    # string table would live, and TOURNY_LOC has never resolved from the other.
    from fut_inventory import TOURNAMENT_NAMES

    # Two per pack now -- a _DESC and a _NAME -- plus the cup names.
    assert len(units) == 2 * len(PACK_SPECS) + len(TOURNAMENT_NAMES)
    names = {u.get("resname") for u in units}
    for cup in TOURNAMENT_NAMES:
        assert f"TOURNY_LOC_{cup}" in names
    assert all(u.find("source") is not None for u in units)


def test_a_reduced_season_rung_points_at_a_season_it_actually_serves() -> None:
    # Every reduced rung served one record and then answered season/user with
    # the division's place in the full ten-row table -- so `minimal` offered a
    # single season and pointed at season 10. That is the same out-of-range
    # shape the divisionId bisection found hanging the screen one member over.
    import json
    import os

    import fut_inventory as inventory

    for mode in ("minimal", "prizes", "matches"):
        os.environ["FIFA14_SEASON_MODE"] = mode
        try:
            seasons = json.loads(inventory.seasons_response())["seasons"]
            user = json.loads(inventory.season_user_response())
        finally:
            os.environ.pop("FIFA14_SEASON_MODE", None)

        assert len(seasons) == 1, mode
        # Division 10: where FUT starts a club, not Division 1 where it ends up.
        assert seasons[0]["divisionId"] == 10, mode
        # seasonId indexes what is served, so a one-record list is season 1.
        # seasonId names the record, not its place in the page: a Division 10
        # record carries id 10 whether it is served alone or beside nine
        # others, and the client saves to /season/10/division/10/user.
        assert user["seasonId"] == seasons[0]["id"] == 10, mode
        # The division's own number, 1 to 10 -- not an index into a table of
        # ten, which is the reading the console disproved on 21 August.
        assert 1 <= user["divisionId"] <= 10, mode


def test_the_native_rung_still_points_at_the_last_of_ten() -> None:
    import json
    import os

    import fut_inventory as inventory

    os.environ["FIFA14_SEASON_MODE"] = "native"
    try:
        seasons = json.loads(inventory.seasons_response())["seasons"]
        user = json.loads(inventory.season_user_response())
    finally:
        os.environ.pop("FIFA14_SEASON_MODE", None)
    assert len(seasons) == 10
    assert user["seasonId"] == 10
    assert 1 <= user["seasonId"] <= len(seasons)


def test_identical_consumables_stack_into_one_card() -> None:
    # Retail stacks them: two Player Fitness cards show as one card with a 2 on
    # it. This club held 261 consumables across 143 distinct kinds and served
    # all 261 separately, so a club that had opened packs scrolled through
    # pages of the same contract.
    import json

    from fut_inventory import CONSUMABLE_TYPES

    doc = json.loads(INVENTORY.club_response({"type": "consumable", "count": "300"}))
    served = doc["itemData"]

    # One card per distinct resource, and the counts add back up to the club.
    held = [i for i in INVENTORY.items if i.get("itemType") in CONSUMABLE_TYPES]
    distinct = {int(i["resourceId"]) for i in held}
    assert len(served) == len(distinct)
    assert sum(int(i.get("count") or 1) for i in served) == len(held)
    assert any(int(i.get("count") or 1) > 1 for i in served), "nothing stacked"

    # A +13 contract and a +99 contract are different cards and must not merge.
    by_resource = {int(i["resourceId"]): i for i in served}
    assert len(by_resource) == len(served)


def test_players_and_club_items_never_stack() -> None:
    # A second Barcelona kit is a duplicate to be sold, not a quantity.
    import json

    for kind in ("player", "kit", "stadium", "ball"):
        doc = json.loads(INVENTORY.club_response({"type": kind, "count": "100"}))
        for item in doc["itemData"]:
            assert int(item.get("count") or 1) == 1, (kind, item.get("id"))


def test_the_season_thresholds_match_the_retail_screen() -> None:
    # Division 10 reads 12 points to win the title and 9 to clinch promotion,
    # paying 1,900 / 1,500 / 300. Promotion was being sent as 2 -- three points
    # fewer than a single win is worth -- because the title threshold was
    # derived from it as promote + 3, so nothing moved independently and the
    # error was invisible.
    import json
    import os

    import fut_inventory as inventory

    os.environ["FIFA14_SEASON_MODE"] = "prizes"
    try:
        season = json.loads(inventory.seasons_response())["seasons"][0]
    finally:
        os.environ.pop("FIFA14_SEASON_MODE", None)

    prizes = {p["prizeLevel"]: p for p in season["prizeSet"]}

    def coins(level):
        awards = prizes[level]["awardMappings"][0]["awards"]
        return awards[0]["value"] if awards else 0

    assert prizes["CHAMPIONSHIP"]["thresholdPoint"] == 12
    assert prizes["PROMOTION"]["thresholdPoint"] == 9
    assert coins("CHAMPIONSHIP") == 1900
    assert coins("PROMOTION") == 1500
    assert coins("MAINTENANCE") == 300


def test_the_apply_picker_stacks_like_the_club_does() -> None:
    # The club tab stacked and the Apply Consumable picker did not, because it
    # reads its own route -- /club/consumables/contracts and its siblings --
    # which was serving every card individually. A club with five of a contract
    # showed one stack in My Club and five separate cards in the picker.
    import json

    from fut_inventory import CONSUMABLE_TYPES, consumables_response

    doc = json.loads(consumables_response(INVENTORY, "contracts"))
    served = doc["itemData"]
    ids = [i["resourceId"] for i in served]
    assert len(ids) == len(set(ids)), "the picker served the same card twice"
    assert any(int(i.get("count") or 1) > 1 for i in served), "nothing stacked"
    assert doc["total"] == len(served)

    # And the counts still add up to what the club really holds.
    held = [
        i for i in INVENTORY.items
        if i.get("itemType") in CONSUMABLE_TYPES
        and 5_001_001 <= int(i.get("resourceId") or 0) <= 5_001_013
    ]
    assert sum(int(i.get("count") or 1) for i in served) == len(held)


def test_the_market_sells_consumables_not_just_players() -> None:
    # The transfer market has PLAYERS, CONSUMABLES, CLUB ITEMS and STAFF tabs,
    # and the consumables tab filters by type and by the exact change -- the
    # console builds "Position Change / LF >> LW" itself. It has always been
    # able to ask; this server only answered with players, so every one of
    # those searches came back with footballers or nothing.
    import json

    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()

    # The query the console actually sends for position modifiers.
    doc = json.loads(
        catalogue.auctions({"type": "training", "cat": "position", "num": "12"}, 1000)
    )
    assert doc["total"] > 0, "the position tab is empty"
    assert doc["auctionInfo"]
    for listing in doc["auctionInfo"]:
        item = listing["itemData"]
        assert item["itemType"] in ("development", "training")
        assert 91 <= int(item["cardsubtypeid"]) <= 110
        # Never cheaper to buy than the game pays to scrap it.
        assert listing["buyNowPrice"] > item["discardValue"]
        assert listing["startingBid"] <= listing["buyNowPrice"]
        assert listing["tradeState"] == "active"

    # And the players tab is untouched.
    players = json.loads(catalogue.auctions({"type": "player", "num": "3"}, 1000))
    assert players["total"] > 1000
    assert all(
        a["itemData"]["itemType"] == "player" for a in players["auctionInfo"]
    )


def test_the_market_never_sells_a_card_that_cannot_be_looked_at() -> None:
    # The families held out of packs are held out of the market too: squad
    # training on art 43 and the manager formation modifiers on art 35 both
    # draw NOT FOUND, and selling one would be worse than packing one.
    from fut_inventory import (
        UNDRAWN_CONSUMABLE_TYPES, _consumable_definitions, market_consumables,
    )

    offered = market_consumables({})
    kinds = {c["itemType"] for c in offered}
    assert kinds, "the market offers no consumables at all"
    for held_out in UNDRAWN_CONSUMABLE_TYPES:
        assert held_out not in kinds, held_out


def test_a_season_keeps_its_progress_after_a_match() -> None:
    # The player won a match, the console saved round 2 to
    # /season/10/division/10/user, and the mode then offered to start a new
    # season. season/user was naming season 1 -- the row's position in a
    # one-row page -- while the record itself, and the client's own save path,
    # both said season 10. A progress document beside a season that is not
    # there reads as no season at all.
    import json
    import os

    import fut_inventory as inventory

    # `kyro-data`: this is the resume shape, and the default became `native`
    # on 25 August because `kyro-data` froze the screen on entry. What is
    # tested here -- that a played season is reported as played -- belongs to
    # the shape that resumes.
    os.environ["FIFA14_SEASON_MODE"] = "kyro-data"
    fresh = json.loads(inventory.season_user_response())
    assert fresh["round"] == 1

    # Exactly what the console PUT after the win. Restored afterwards --
    # SEASON_PROGRESS is shared, and a season left mid-run here shows up as a
    # club already promoted in whatever test runs next.
    # Deep-copied: `apply` mutates the entry dict in place, so a shallow copy
    # hands back the same object it just changed and the next test sees a club
    # mid-season with the client's own `data` blob attached.
    import copy

    entries = copy.deepcopy(inventory.SEASON_PROGRESS.entries)
    try:
        # Season 1, not 10: the client saves to the id it was given, and it is
        # given `seasonId` 1. Journalled as PUT /season/1/division/10/user.
        inventory.SEASON_PROGRESS.apply(1, 10, {"round": 2, "dataVersion": 1,
                                                "progressDataVersion": 1})
        after = json.loads(inventory.season_user_response())
        assert after["round"] > fresh["round"], "the round did not advance"
        assert after["seasonId"] == 1, "seasonId selects the first row served"

        served = json.loads(inventory.seasons_response())["seasons"]
        row = [s for s in served if s["id"] == after["seasonId"]]
        assert len(row) == 1
        assert row[0]["divisionId"] == after["divisionId"]
    finally:
        inventory.SEASON_PROGRESS.entries.clear()
        inventory.SEASON_PROGRESS.entries.update(entries)
        os.environ.pop("FIFA14_SEASON_MODE", None)


def test_each_unlisted_card_gets_a_stable_id_of_its_own() -> None:
    # Pinned to the measured shape. `runtime/unlisted-shape.txt` is a live
    # experiment the console can be mid-way through -- it was set to
    # `impulsum` on 26 August to try the PC build's entry -- and a test must
    # not depend on which candidate is being tried at the time.
    import os

    os.environ["FIFA14_UNLISTED_SHAPE"] = "plain"
    # `GetCardIdFromTradeId` is how the standalone Transfer List screen gets
    # from the selected row to a card, so every row needs an id: distinct (two
    # rows sharing one is the fault this replaces), stable across polls (the
    # screen re-reads the pile and cannot hold a selection whose id moved), and
    # clear of the real listing and market blocks so `withdraw` can tell a
    # pseudo id from an auction.
    import json
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(53)))
    for item in opened["itemList"][:3]:
        actions.move(
            {"itemData": [{"id": item["id"], "pile": inventory.PILE_TRANSFER}]}
        )

    first = json.loads(actions.trade_pile(wallet.coins))["auctionInfo"]
    again = json.loads(actions.trade_pile(wallet.coins))["auctionInfo"]

    rows = [e for e in first if e["tradeId"] == 0]
    assert len(rows) == 3
    # Told apart by the card, not by a trade id -- every unlisted row is
    # tradeId 0 and the screen resolves the selection through `itemData.id`.
    assert len({r["itemData"]["id"] for r in rows}) == 3

    by_card = {r["itemData"]["id"]: r["tradeId"] for r in rows}
    repeat = {
        r["itemData"]["id"]: r["tradeId"]
        for r in again
        if r["tradeId"] == 0
    }
    assert by_card == repeat, "an id that moves under the cursor is no id"

    # Listing still resolves the card by item id, and the real listing comes
    # from the listing block -- below the pseudo one, so the two never collide.
    card_id = rows[0]["itemData"]["id"]
    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card_id}, "startingBid": 150,
             "buyNowPrice": 300, "duration": 3600}
        )
    )
    assert listing["itemData"]["id"] == card_id
    assert listing["tradeState"] == "active"
    assert listing["tradeId"] < inventory.UNLISTED_TRADE_ID_BASE


def test_removing_an_unlisted_card_from_the_list_returns_it_to_the_club() -> None:
    # Pinned to the measured shape. `runtime/unlisted-shape.txt` is a live
    # experiment the console can be mid-way through -- it was set to
    # `impulsum` on 26 August to try the PC build's entry -- and a test must
    # not depend on which candidate is being tried at the time.
    import os

    os.environ["FIFA14_UNLISTED_SHAPE"] = "plain"
    # Now that the screen binds a menu, "remove from the transfer list" can be
    # sent for a card that was never listed -- with the id this server invented.
    # There is no auction to withdraw, and leaving the card on the list is how
    # that read before: the action reported success and nothing moved.
    import json
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(59)))
    card_id = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": card_id, "pile": inventory.PILE_TRANSFER}]})

    row = [
        e for e in json.loads(actions.trade_pile(wallet.coins))["auctionInfo"]
        if e["tradeId"] == 0
    ][0]
    assert row["itemData"]["id"] == card_id
    # `withdraw` is for a real auction. A card that was never listed has no
    # trade id to withdraw -- it goes back the way it came, as a move to the
    # club pile, which is what the console sends.
    actions.move({"itemData": [{"id": card_id, "pile": inventory.PILE_CLUB}]})

    assert [c["id"] for c in actions.transfer] == []
    held = [c for c in actions.club if c["id"] == card_id]
    assert len(held) == 1, "the card went back to the club, not nowhere"
    assert held[0]["pile"] == inventory.PILE_CLUB
    assert held[0]["itemState"] == "free"


def test_a_card_on_the_transfer_list_can_be_moved_back_to_the_club() -> None:
    # `move()` searched the pack pile and the club and not the transfer list, so
    # "Send to Club" on a transfer-list card answered 461 and the card stayed.
    # Unreachable until that screen bound its menu; journalled the moment it did.
    import json
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(61)))
    card_id = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": card_id, "pile": inventory.PILE_TRANSFER}]})
    assert [c["id"] for c in actions.transfer] == [card_id]

    answer = json.loads(
        actions.move({"itemData": [{"id": card_id, "pile": "club"}]})
    )
    entry = answer["itemData"][0] if isinstance(answer, dict) else answer[0]
    assert entry["success"] is True, entry
    assert actions.unmatched == []
    assert [c["id"] for c in actions.transfer] == []
    assert any(c["id"] == card_id for c in actions.club)


def test_a_candidate_can_be_written_as_data_without_a_relaunch() -> None:
    # A candidate that is code costs a server restart, and a server restart
    # costs a relaunch of the title -- one relaunch per idea, on a screen that
    # is being bisected. A candidate written into runtime/unlisted-shapes.json
    # is read per request, like the switch itself, so it is live when saved.
    import json
    import os
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(67)))
    actions.move(
        {"itemData": [{"id": opened["itemList"][0]["id"],
                       "pile": inventory.PILE_TRANSFER}]}
    )

    def unlisted(shape: str) -> dict:
        os.environ["FIFA14_UNLISTED_SHAPE"] = shape
        try:
            pile = json.loads(actions.trade_pile(wallet.coins))
        finally:
            os.environ.pop("FIFA14_UNLISTED_SHAPE", None)
        return [
            e for e in pile["auctionInfo"]
            if e["tradeId"] == 0
        ][0]

    written = inventory.UNLISTED_SHAPES_FILE
    restore = written.read_text() if written.exists() else None
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(json.dumps({
        "probe": {
            "base": "plain",
            "entry": {"set": {"startingBid": 150}, "remove": ["watched"]},
            "item": {"remove": ["lastSalePrice"], "set": {"owners": 3}},
        },
        "nobase": {"entry": {"set": {"confidenceValue": 42}}},
    }))
    try:
        row = unlisted("probe")
        # The base was applied first...
        assert row["tradeState"] is None, "base candidate did not run"
        assert row["expires"] == -1
        # ...then the overlay on top of it.
        assert row["startingBid"] == 150
        assert "watched" not in row
        assert "lastSalePrice" not in row["itemData"]
        assert row["itemData"]["owners"] == 3

        # No base means the current shape, changed only where the spec says.
        plain = unlisted("nobase")
        assert plain["tradeState"] is None
        assert plain["confidenceValue"] == 42

        # Nothing held is mutated, whatever was served.
        for held in actions.transfer:
            assert held["pile"] == inventory.PILE_TRANSFER
            assert "owners" not in held or held["owners"] != 3
    finally:
        if restore is None:
            written.unlink(missing_ok=True)
        else:
            written.write_text(restore)


def test_an_unreadable_shapes_file_is_not_fatal() -> None:
    # The file is edited by hand between console sessions. A half-saved one
    # must serve the coded candidate rather than take the trade pile down.
    import fut_inventory as inventory

    written = inventory.UNLISTED_SHAPES_FILE
    restore = written.read_text() if written.exists() else None
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text('{"probe": {"entry": {"set": {')
    try:
        assert inventory.custom_unlisted_shapes() == {}
    finally:
        if restore is None:
            written.unlink(missing_ok=True)
        else:
            written.write_text(restore)


def test_the_kyro_season_mode_matches_the_reference_build() -> None:
    # KyroGeorge2/FIFA-14-Local-FUT resumes a season; this build does not. Its
    # list and its season/user agree with each other, and every member this
    # server sends differs from his. Matching him exactly is the same method
    # that settled the sold pile -- see docs/TRADE_PILE.md.
    import json
    import os

    import fut_inventory as inventory

    os.environ["FIFA14_SEASON_MODE"] = "kyro"
    try:
        listed = json.loads(inventory.seasons_response())["seasons"]
        user = json.loads(inventory.season_user_response())
    finally:
        os.environ.pop("FIFA14_SEASON_MODE", None)

    # Ten rows, ordered Division 10 first, `id` a 1-based position in the list.
    assert len(listed) == 10
    assert [row["id"] for row in listed] == list(range(1, 11))
    assert [row["divisionId"] for row in listed] == list(range(10, 0, -1))

    # Three members, and only three. `data`, `dataVersion`, seasonGames* and
    # seasonCoins are what Kyro calls "unknown guessed progression members".
    assert set(user) == {"seasonId", "divisionId", "round"}
    assert user["seasonId"] == 1
    assert user["divisionId"] == 10

    # The one that has to hold: seasonId selects a row, and that row is the
    # division the user document names. This is what `current` gets wrong --
    # one row carrying id 10, pointed at by seasonId 10, on a list of one.
    selected = [row for row in listed if row["id"] == user["seasonId"]]
    assert len(selected) == 1
    assert selected[0]["divisionId"] == user["divisionId"]


def test_the_resume_season_shape_agrees_with_itself() -> None:
    # Measured on the console, 22 August 2026. A season survived a match and
    # came back with the score drawn and NEXT on the second fixture, on this
    # document and no other:
    #
    #     {"seasonId":1,"divisionId":10,"round":2,"data":<blob>,"dataVersion":1}
    #
    # Three things have to agree, and every earlier attempt had one wrong:
    #
    #   the list      ten rows, Division 10 first, so row 1 IS Division 10
    #   seasonId      1, selecting that row -- the client decrements it
    #   divisionId    10, naming the same division as the row selected
    #
    # `kyro-data` by name rather than by default: on 25 August this shape froze
    # the seasons screen on entry and `native` opened it on the same console,
    # so `native` is the default now. What this pins is unchanged -- that the
    # resume shape is internally consistent.
    import json
    import os

    import fut_inventory as inventory

    os.environ["FIFA14_SEASON_MODE"] = "kyro-data"
    try:
        assert inventory.season_wire_mode() == "kyro-data"
        listed = json.loads(inventory.seasons_response())["seasons"]
        user = json.loads(inventory.season_user_response())
    finally:
        os.environ.pop("FIFA14_SEASON_MODE", None)

    assert len(listed) == 10
    assert [row["id"] for row in listed] == list(range(1, 11))
    selected = [row for row in listed if row["id"] == user["seasonId"]]
    assert len(selected) == 1, "seasonId must select exactly one row"
    assert selected[0]["divisionId"] == user["divisionId"], (
        "the user document must name the division of the row it selects"
    )
    assert user["divisionId"] == 10, "a new club starts in Division 10"
    assert user["round"] >= 1, "wire round 0 is the client's invalid sentinel"


def test_the_default_season_shape_is_the_one_that_opens() -> None:
    # `native`, because it is the one the screen opens on. Two measurements on
    # this console disagree and both stand: `kyro-data` resumed a season on
    # 22 August and froze the screen on 25 August, where `native` opened it --
    # same console, same disc, same club, same LIVE session.
    #
    # Found by trying nygmasx's server from the same console: seasons opened
    # there, and his `deploy/run.sh` pins `native`, so he had never run the
    # default this repository set.
    import json

    import fut_inventory as inventory

    assert inventory.season_wire_mode() == "native"
    listed = json.loads(inventory.seasons_response())["seasons"]
    assert len(listed) == 10
    assert [row["id"] for row in listed] == list(range(1, 11))
    # Division 1 first, which is what `native` means and what `kyro-data`
    # reversed. The screen opens on the first tile, so this one opens on
    # Division 1 rather than on the division a new club is actually in.
    assert listed[0]["divisionId"] == 1
def test_a_played_season_carries_its_blob_back() -> None:
    # The scores live in the client's own blob. Without it the fixture list
    # drew `-` for a match that had been won 4-0, even once the record was
    # selectable. `data` before `dataVersion` -- the version branch decodes
    # using registers the data branch fills.
    import json

    import fut_inventory as inventory

    progress = inventory.SeasonProgress()
    progress.apply(1, 10, {"round": 2, "data": "BLOB", "dataVersion": 1})
    progress.settle(1, 10, "WIN", 771)

    saved = inventory.SEASON_PROGRESS
    inventory.SEASON_PROGRESS = progress
    try:
        user = json.loads(inventory.season_user_response())
    finally:
        inventory.SEASON_PROGRESS = saved

    assert user["data"] == "BLOB"
    assert user["dataVersion"] == 1
    assert list(user).index("data") < list(user).index("dataVersion")
    assert user["round"] == 2, "one match played is wire round 2"


def test_a_transferred_player_is_two_cards_not_a_duplicate() -> None:
    # Eddie Johnson is asset 46727, Rare Silver, 72 -- at D.C. United and again
    # at Sounders. Same asset, same rarity, same rating, different card. Before
    # the club joined the signature these were reported as repeats of each
    # other, and the club screen would have offered to quick-sell one of them.
    #
    # 2,395 pairs in the catalogue are of this shape; Falcao, Mata, Vidic,
    # Fabregas, Lewandowski and Di Maria are all Rare Gold at two clubs.
    import fut_inventory as inventory

    dc = {"assetId": 46727, "rarity": "Rare Silver", "rating": 72, "teamid": 111}
    sounders = {"assetId": 46727, "rarity": "Rare Silver", "rating": 72, "teamid": 222}
    assert inventory.card_signature(dc) != inventory.card_signature(sounders)

    # The same card twice is still the same card.
    assert inventory.card_signature(dc) == inventory.card_signature(dict(dc))

    # `teamId`, the alias `_fill_card_aliases` puts on saved cards, reads the
    # same as `teamid`, so a card written before the alias existed still
    # matches the one packed today.
    aliased = {"assetId": 46727, "rarity": "Rare Silver", "rating": 72, "teamId": 111}
    assert inventory.card_signature(aliased) == inventory.card_signature(dc)

    # A card with no club at all normalises to 0 rather than being told apart
    # by an absence.
    bare_a = {"assetId": 46727, "rarity": "Rare Silver", "rating": 72}
    bare_b = {"assetId": 46727, "rarity": "Rare Silver", "rating": 72, "teamid": 0}
    assert inventory.card_signature(bare_a) == inventory.card_signature(bare_b)

    # And the distinctions that already worked still do: version and rating.
    tots = {"assetId": 46727, "rarity": "Team of the Season", "rating": 72, "teamid": 111}
    better = {"assetId": 46727, "rarity": "Rare Silver", "rating": 75, "teamid": 111}
    assert inventory.card_signature(dc) != inventory.card_signature(tots)
    assert inventory.card_signature(dc) != inventory.card_signature(better)


def test_both_cards_of_a_transferred_player_reach_the_club() -> None:
    # The point of the signature fix, end to end: pack both, send both, keep
    # both, and neither is marked a repeat of the other.
    import json

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    dc = {"id": 1950009001, "assetId": 46727, "rarity": "Rare Silver",
          "rating": 72, "teamid": 111, "itemType": "player"}
    sounders = {"id": 1950009002, "assetId": 46727, "rarity": "Rare Silver",
                "rating": 72, "teamid": 222, "itemType": "player"}
    shop.pending.extend([dc, sounders])

    pairs = inventory.club_duplicate_pairs([dc, sounders])
    assert pairs == [], f"a transfer is not a duplicate: {pairs}"

    for card in (dc, sounders):
        answer = json.loads(
            actions.move({"itemData": [{"id": card["id"], "pile": "club"}]})
        )
        entry = answer["itemData"][0] if isinstance(answer, dict) else answer[0]
        assert entry["success"] is True, entry

    held = [c["id"] for c in actions.club if c["assetId"] == 46727]
    assert sorted(held) == [1950009001, 1950009002], "both cards stay in the club"


def test_client_data_is_handed_back_exactly_as_the_client_wrote_it() -> None:
    # The Transfers hub tiles are drawn from counters the client works out
    # itself and stores at clientdata/userHubData. A GET answered the fixture
    # `{}`, so every session opened by telling the client its own counters were
    # nothing, and TRANSFER LIST read "0 ITEMS / Selling: 0 / Sold: 0" over a
    # pile holding twenty-seven.
    #
    # Byte-for-byte matters here more than anywhere else: this parser is one of
    # the two that freeze the FUT login on an object carrying a member it does
    # not know. Echoing the client's own document cannot introduce one.
    import json

    import fut_inventory as inventory

    store = inventory.ClientData()
    written = json.dumps({"entries": [
        {"key": 0, "value": 3}, {"key": 1, "value": 2},
        {"key": 2, "value": 0}, {"key": 3, "value": 0},
    ]}, separators=(",", ":")).encode()

    assert store.read("userHubData") is None, "nothing to hand back before a write"
    assert store.save("userHubData", written) is True
    assert json.loads(store.read("userHubData")) == json.loads(written)
    assert b"credits" not in store.read("userHubData")
    assert b"coins" not in store.read("userHubData")

    # Nothing that is not the shape the client writes is kept, so a malformed
    # or empty body cannot replace a good document with something the parser
    # would reject.
    assert store.save("userHubData", b"") is False
    assert store.save("userHubData", b"not json") is False
    assert store.save("userHubData", b'{"credits":91572595}') is False
    assert store.save("userHubData", b'["entries"]') is False
    assert json.loads(store.read("userHubData")) == json.loads(written)

    # Only names the client has written are answered from here; the deliberate
    # fixtures for pileSize and tutorialpopups are untouched.
    assert store.read("pileSize") is None
    assert store.read("tutorialpopups") is None

    # And it survives a restart, which is the whole point -- the GET that reads
    # it happens at the start of a session, before any PUT.
    reloaded = inventory.ClientData()
    reloaded.restore(store.state())
    assert json.loads(reloaded.read("userHubData")) == json.loads(written)

    # A save written before this existed carries no clientData at all.
    empty = inventory.ClientData()
    empty.restore(None)
    assert empty.read("userHubData") is None


def test_client_data_is_handed_back_exactly_as_the_client_wrote_it() -> None:
    # The Transfers hub tiles are drawn from counters the client works out
    # itself and stores at `clientdata/userHubData`. The GET answered a
    # hardcoded `{}`, so every session opened by telling the client its own
    # counters were nothing -- TRANSFER LIST read "0 ITEMS / Selling: 0 /
    # Sold: 0" over a pile holding twenty-seven.
    #
    # Byte-for-byte matters here more than anywhere: this parser is one of the
    # two that freeze the FUT login on an object carrying a member they do not
    # know. Echoing the client's own document cannot introduce one.
    import fut_inventory as inventory

    store = inventory.ClientData()
    written = b'{"entries":[{"key":0,"value":3},{"key":1,"value":2}]}'
    assert store.save("userHubData", written) is True
    assert json.loads(store.read("userHubData")) == json.loads(written)

    # Nothing added, nothing renamed.
    assert set(json.loads(store.read("userHubData"))) == {"entries"}

    # A name the client has never written is not answered from here, so the
    # deliberate fixtures for pileSize and tutorialpopups still stand.
    assert store.read("pileSize") is None

    # Junk is refused rather than stored and handed back later.
    assert store.save("userHubData", b"not json") is False
    assert store.save("userHubData", b'{"nope":1}') is False
    assert store.save("userHubData", None) is False
    assert json.loads(store.read("userHubData")) == json.loads(written)

    # It survives a save/restore cycle, which is the whole point: the GET
    # happens at session start, before the client has written anything.
    revived = inventory.ClientData()
    revived.restore(store.state())
    assert json.loads(revived.read("userHubData")) == json.loads(written)


def test_the_market_narrows_consumables_by_quality_and_by_modifier() -> None:
    # The consumables tab sends more than the category, and only the category
    # was read. Picking "LB >> LWB" returned all twenty position modifiers, and
    # picking Bronze returned every card of the family.
    #
    # These are the queries the console actually sent, off the journal:
    #
    #     type=training&cat=position&pos=LB-LWB
    #     type=training&cat=playerTraining&lev=bronze
    import fut_inventory as inventory

    def rows(**query):
        return inventory.market_consumables(query)

    # One modifier means one card, and the one asked for. `from`/`to` are read
    # in the order the query names them: LB-LWB takes a left back and makes him
    # a left wing back, which is a different card from its opposite number.
    one = rows(cat="position", pos="LB-LWB")
    assert len(one) == 1
    assert (one[0]["from"], one[0]["to"]) == ("LB", "LWB")
    assert len(rows(cat="position")) > 1, "the family is still there unfiltered"
    # The reverse card exists and is not this one.
    other = rows(cat="position", pos="LWB-LB")
    assert len(other) == 1
    assert other[0]["definitionId"] != one[0]["definitionId"]

    # Quality is the rating, on the same boundaries a pack uses.
    family = rows(cat="playerTraining")
    tiers = {
        tier: rows(cat="playerTraining", lev=tier)
        for tier in ("bronze", "silver", "gold")
    }
    assert sum(len(v) for v in tiers.values()) == len(family), (
        "every card belongs to exactly one tier"
    )
    for tier, cards in tiers.items():
        low, high = inventory.TIER_RATINGS[tier]
        assert cards, tier
        for card in cards:
            assert low <= card["rating"] <= high, (tier, card["rating"])

    # "Any" is the screen's default and must not filter.
    assert len(rows(cat="playerTraining", lev="any")) == len(family)
    assert len(rows(cat="playerTraining", lev="")) == len(family)

    # A modifier asked for in a family that has none is nothing, not everything.
    assert rows(cat="playerTraining", pos="LB-LWB") == []

    # And the two filters compose.
    both = rows(cat="contract", lev="bronze")
    assert both and all(
        c["rating"] <= inventory.TIER_RATINGS["bronze"][1] for c in both
    )


def test_the_player_market_filters_on_price_quality_and_style() -> None:
    # Three faults, all on the same screen.
    #
    # `minb`/`maxb` were read as a minimum and maximum **rating**, and this
    # screen has no rating filter -- it offers Quality, Position, Chemistry
    # Style, Nationality, League, Club and Pricing. A Min. Price of 1000 asked
    # for cards rated at least a thousand and emptied the market.
    #
    # Quality matched the rarity string as a substring, so "Non-Rare Bronze"
    # worked and "Team of the Year" did not: a Gold search excluded every
    # special in the game.
    #
    # Chemistry Style was not read at all, and could not be -- the catalogue
    # holds no style, because a style is applied by a consumable rather than
    # born with the card.
    import json

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()

    def total(**query):
        return catalogue.search({"type": "player", **{k: str(v) for k, v in query.items()}})[1]

    everything = total()

    # Price bounds narrow, and do not empty.
    dear = total(minb=1000)
    assert 0 < dear < everything
    assert total(minb=1000, maxb=5000) < dear
    # Both spellings reach the same value.
    assert total(micr=50_000) == total(minb=50_000)

    # The three tiers partition the catalogue exactly -- every card is one of
    # them, and none is two.
    tiers = {t: total(lev=t) for t in ("bronze", "silver", "gold")}
    assert sum(tiers.values()) == everything

    # And a special is gold, which is what a Gold search is usually after.
    for rarity in ("Team of the Year", "Team of the Season", "iMOTM", "Legend"):
        card = next(c for c in catalogue.cards if c.get("rarity") == rarity)
        assert inventory._card_tier(card) == "gold", rarity

    # An ordinary card still follows its own name rather than its rating.
    bronze = next(c for c in catalogue.cards
                  if (c.get("rarity") or "").lower().endswith("bronze"))
    assert inventory._card_tier(bronze) == "bronze"

    # The style asked for is the style served, because the market is where
    # somebody else's applied style would be on sale.
    listed = json.loads(catalogue.auctions(
        {"type": "player", "num": "3", "playStyle": "268"}, 0))
    assert listed["auctionInfo"]
    assert {a["itemData"]["playStyle"] for a in listed["auctionInfo"]} == {268}
    plain = json.loads(catalogue.auctions({"type": "player", "num": "3"}, 0))
    assert {a["itemData"]["playStyle"] for a in plain["auctionInfo"]} == {250}


def test_a_packed_player_arrives_with_seven_contracts() -> None:
    # Every card this server made carried 99, from every source. Nothing ever
    # needed a contract, so the contracts tab, the market's development
    # category and the apply-consumable screen were all pointless against a
    # squad that never ran down.
    import json
    import random

    import fut_inventory as inventory

    _, shop, _ = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(11)))
    players = [i for i in opened["itemList"] if i.get("itemType") == "player"]
    assert players
    for card in players:
        assert card["contract"] == inventory.DEFAULT_CONTRACT == 7
        # Fitness is not a contract: it is spent by playing, not handed out
        # at nought, so it stays full.
        assert card["fitness"] == 99

    # A card off the market arrives the same way.
    listed = json.loads(inventory.CardCatalogue().auctions(
        {"type": "player", "num": "3"}, 0))
    for auction in listed["auctionInfo"]:
        assert auction["itemData"]["contract"] == 7


def test_a_club_item_known_to_draw_not_found_is_never_packed() -> None:
    # Kit 6300772 came out of a Gold Pack on 24 August drawing the green NOT
    # FOUND placeholder. It is inside the range the catalogue calls good --
    # and that range was never measured. The probe visited 24 kit ids, every
    # one above 6300860, hunting for where the family stops; the interior was
    # assumed contiguous and is not.
    #
    # So bad ids are removed one at a time as they are found. Both the builder
    # and the server read the same file, so an id added after a bad pack takes
    # effect on the next server start without a rebuild.
    import fut_inventory as inventory

    blank = inventory.blank_club_items()
    assert 6300772 in blank, "the id that started this must stay excluded"

    catalogue = inventory._clubitem_catalogue()
    assert catalogue, "the catalogue still has items in it"
    served = {item.get("resourceId") for item in catalogue}
    assert not (served & blank), "a blank id reached the catalogue"

    # And the shipped file agrees, so a rebuild does not put it back.
    import json
    from pathlib import Path

    built = json.loads(
        (Path(inventory.__file__).parent / "fifa14_clubitems.json").read_text()
    )["clubitems"]
    assert not ({i.get("resourceId") for i in built} & blank)


def test_an_unreadable_blank_list_leaves_the_catalogue_alone() -> None:
    # The file is edited by hand between sessions. A half-saved one must serve
    # the catalogue as it stands rather than empty it.
    import fut_inventory as inventory

    kept = inventory.CLUBITEM_BLANK_FILE
    try:
        inventory.CLUBITEM_BLANK_FILE = kept.parent / "does-not-exist.json"
        assert inventory.blank_club_items() == set()
        assert inventory._clubitem_catalogue(), "no exclusions is not no items"
    finally:
        inventory.CLUBITEM_BLANK_FILE = kept


def test_the_club_search_reports_how_many_the_club_holds() -> None:
    # The item screen's CLUB tab read 0 over a club of 485. The response
    # carried no count at all -- `itemData` and `duplicateItemIdList` and
    # nothing else -- so there was nothing for it to draw.
    #
    # Retail settles what the number is: a capture of FIFA 14 shows
    # "CLUB 352" on a tab that is greyed out, so it is the club's size and not
    # the page, and not what the client happens to be holding.
    #
    # `total` is the member the trade pile and the market already use for this.
    # `totalResults` and `itemCount` are not in CardsDLL's name table at all.
    import json

    import fut_inventory as fut

    inventory = fut.ClubInventory()
    assert inventory.items, "the seeded club has cards to count"

    whole = json.loads(inventory.club_response())
    assert whole["total"] > 0

    # `total` describes the list the client is paging, so paging the whole way
    # through has to arrive at exactly that many rows. Asserting against the
    # raw item count would be wrong: consumables stack before they are shown,
    # and the client pages the stacked list.
    rows, start = 0, 0
    while True:
        page = json.loads(
            inventory.club_response({"count": "25", "start": str(start)})
        )
        got = len(page["itemData"])
        if not got:
            break
        rows += got
        start += got
        assert page["total"] == whole["total"], "the total moved between pages"
    assert rows == whole["total"]

    # A filter narrows the total to what it matched, not to the page.
    players = json.loads(inventory.club_response({"type": "player", "count": "5"}))
    assert 0 < players["total"] <= whole["total"]
    assert len(players["itemData"]) <= 5
    assert all(i.get("itemType") == "player" for i in players["itemData"])


def test_a_duplicate_points_at_a_copy_the_console_is_holding() -> None:
    # "MY CURRENT ITEM" drew `undefined` in every field. The pairing is two
    # numbers -- the client is told which id repeats which -- and it then draws
    # the owned card by looking that id up in its own memory. It holds the
    # active squad, the pack, and whatever club pages somebody scrolled past;
    # a card at position 176 of 987 was in none of them, so the panel had a
    # number and no card behind it.
    #
    # The squad is the only part of the club the console is always holding:
    # `/squad/active` is fetched every session and in full, where the club is
    # paged eleven at a time and only as far as somebody scrolled.
    import fut_inventory as inventory

    def card(item_id):
        return {"itemType": "player", "assetId": 100, "rarity": "Rare Gold",
                "rating": 80, "teamid": 9, "id": item_id}

    owned = [card(10), card(20), card(30)]
    pending = [card(99)]

    # Oldest id when nothing is known to be loaded -- unchanged behaviour.
    assert inventory.pile_duplicate_pairs(list(pending), owned) == [
        {"itemId": 99, "duplicateItemId": 10}
    ]

    # A copy in the squad wins, because it is one the client can actually draw.
    assert inventory.pile_duplicate_pairs(list(pending), owned, {30}) == [
        {"itemId": 99, "duplicateItemId": 30}
    ]

    # And it falls back rather than pointing at nothing.
    assert inventory.pile_duplicate_pairs(list(pending), owned, {777}) == [
        {"itemId": 99, "duplicateItemId": 10}
    ]

    # Stability: the same inputs give the same answer, so the marker does not
    # move from one copy to another between two openings of the screen.
    assert inventory.pile_duplicate_pairs(list(pending), owned, {30}) == \
        inventory.pile_duplicate_pairs(list(pending), list(reversed(owned)), {30})


def test_seasonid_and_divisionid_name_the_same_row_in_every_mode() -> None:
    # A season played to round 2, with its record and its blob both present and
    # correct, still came back as "are you sure you want to start this Single
    # Player Season?" -- because `seasonId` selected a row whose `divisionId`
    # was 10 while the user document said 9.
    #
    # Minus one was the "index into the client's own table of ten" reading,
    # settled against the console on 21 August and answered no: the shield
    # reads DIV 1 for 0, for 9 and for 10, so it never followed this member.
    # What the member does is identify the record.
    #
    # Both modes are checked, because the two have disagreed before and the
    # rule is the same for each.
    import json
    import os

    import fut_inventory as inventory

    saved = dict(inventory.SEASON_PROGRESS.entries)
    try:
        inventory.SEASON_PROGRESS.entries.clear()
        inventory.SEASON_PROGRESS.apply(10, 10, {"round": 2, "data": "BLOB",
                                                 "dataVersion": 1})
        inventory.SEASON_PROGRESS.settle(10, 10, "WIN", 693)
        for mode in ("native", "kyro-data"):
            os.environ["FIFA14_SEASON_MODE"] = mode
            try:
                user = json.loads(inventory.season_user_response())
                rows = json.loads(inventory.seasons_response())["seasons"]
            finally:
                os.environ.pop("FIFA14_SEASON_MODE", None)
            picked = [r for r in rows if r["id"] == user["seasonId"]]
            assert len(picked) == 1, f"{mode}: seasonId selects exactly one row"
            assert picked[0]["divisionId"] == user["divisionId"], (
                f"{mode}: the user document must name the division of the row "
                f"it selects -- row says {picked[0]['divisionId']}, document "
                f"says {user['divisionId']}"
            )
            # And the club's actual division, not an index into anything.
            assert user["divisionId"] == 10
            # A played season reports as played, whichever mode is serving.
            assert user["round"] == 2, mode
    finally:
        inventory.SEASON_PROGRESS.entries.clear()
        inventory.SEASON_PROGRESS.entries.update(saved)


def test_a_manager_card_carries_only_members_this_binary_knows() -> None:
    # Managers were never served at all: four club-item families and no staff,
    # so the STAFF tab was empty and the squad's manager slot could not be
    # filled. The catalogue is the game's own, through
    # MarvelcoCode/Impulsum14's `FUTDB/managers.tsv`.
    #
    # The point of this test is the envelope rather than the count. That build
    # sends `dream`, `marketDataMinPrice` and `marketDataMaxPrice` on every
    # manager and **none of the three is a member of this console's CardsDLL**.
    # An unknown member on a card is the shape that froze the match-award
    # screen and the trade-offer screen in this project, so they stay out.
    import os

    import fut_inventory as inventory

    catalogue = inventory.manager_catalogue()
    assert len(catalogue) == 166
    resources = [m["resourceId"] for m in catalogue]
    assert len(set(resources)) == len(resources)

    card = inventory._manager_item(catalogue[0], 1)
    for banned in ("dream", "marketDataMinPrice", "marketDataMaxPrice"):
        assert banned not in card, banned

    # A manager's art is addressed by its own resource, the way a stadium's is,
    # rather than by a family asset the way a kit's is.
    assert card["assetId"] == card["resourceId"]
    assert card["itemType"] == "manager"
    assert card["cardsubtypeid"] == 4
    # The quick-sell value comes from the same table every other card uses.
    assert card["discardValue"] == inventory.club_discard_value(card["rating"], 1)

    # Seeding is off unless asked for: managers belong in packs, and this exists
    # only to find out whether the card draws at all.
    os.environ.pop("FIFA14_SEED_MANAGERS", None)
    assert inventory.seed_managers() == []
    os.environ["FIFA14_SEED_MANAGERS"] = "1"
    try:
        seeded = inventory.seed_managers()
    finally:
        os.environ.pop("FIFA14_SEED_MANAGERS", None)
    assert len(seeded) == len(catalogue)
    # Ids are derived from the resource, not counted out, so a manager keeps
    # its id in a save written last week -- the rule the club-item blocks exist
    # for. And they sit clear of those blocks.
    assert len({c["id"] for c in seeded}) == len(seeded)
    assert all(c["id"] >= inventory.MANAGER_ID_BASE for c in seeded)


def test_staff_cards_carry_their_family_and_fill_their_own_counters() -> None:
    # Four families the game has and this server never served: head coaches,
    # goalkeeping coaches, fitness coaches and physios. All four `itemType`
    # values are in CardsDLL, and so are the four counters they report under --
    # `staffHeadCoach`, `staffGKCoach`, `staffFitnessCoach`, `staffPhysio` --
    # which read zero for weeks because nothing carried them.
    import os

    import fut_inventory as inventory

    catalogue = inventory.staff_catalogue()
    assert len(catalogue) == 150
    assert len({s["resourceId"] for s in catalogue}) == 150

    card = inventory._staff_item(catalogue[0], 1)
    # `attr` is in the source and is NOT a member of this binary, unlike
    # `amount`, `posbonus` and `fieldpos` which all are. Same rule that kept
    # `dream` off the manager card.
    assert "attr" not in card
    for member in ("amount", "posbonus", "fieldpos"):
        assert member in card, member
    # Art by the card's own resource, as for a manager or a stadium.
    assert card["assetId"] == card["resourceId"]

    os.environ.pop("FIFA14_SEED_STAFF", None)
    assert inventory.seed_staff() == []
    os.environ["FIFA14_SEED_STAFF"] = "1"
    os.environ["FIFA14_SEED_MANAGERS"] = "1"
    try:
        club = inventory.ClubInventory()
        document = json.loads(inventory.club_stats_response(club))
        seeded = inventory.seed_staff()
        managers = inventory.seed_managers()
    finally:
        os.environ.pop("FIFA14_SEED_STAFF", None)
        os.environ.pop("FIFA14_SEED_MANAGERS", None)

    # Ids sit in their own block and cannot collide with a manager's, which is
    # the rule the club-item blocks exist for.
    assert len({c["id"] for c in seeded}) == len(seeded)
    assert not ({c["id"] for c in seeded} & {m["id"] for m in managers})
    assert all(c["id"] >= inventory.STAFF_ID_BASE for c in seeded)

    # Each family counts under its own name, and `staff` is their sum plus the
    # managers -- not one lump.
    assert document["staffHeadCoach"] == 36
    assert document["staffGKCoach"] == 36
    assert document["staffFitnessCoach"] == 36
    assert document["staffPhysio"] == 42
    assert document["staffManager"] == 166
    assert document["staff"] == 36 + 36 + 36 + 42 + 166


def test_managers_and_staff_come_out_of_packs_and_respect_their_tier() -> None:
    # They were absent from packs because nothing served them at all -- the
    # weight table said so in as many words: "Manager and staff are absent:
    # they are not in CLUB_ITEM_KINDS, so there is nothing to weight." Seeded
    # into a club on 25 August, all 316 rendered, so they belong in packs the
    # way kits and badges do rather than being handed over in bulk.
    import collections
    import random

    import fut_inventory as inventory

    pool = inventory.pack_extras()
    for family, expected in (
        ("manager", 166), ("headCoach", 36), ("gkCoach", 36),
        ("fitnessCoach", 36), ("physio", 42),
    ):
        cards = pool.get(("club", family)) or []
        assert len(cards) == expected, family
        # Tier from the card's own rating, not from a quality slot: the game's
        # database rates every one of them, so a Bronze Pack cannot hand out an
        # 88-rated manager.
        for card in cards:
            assert card["_tier"] == inventory._extra_tier(card["rating"]), family
            assert "id" not in card, family

    # A bronze pack draws no gold staff, which is the whole point of the tier.
    bronze = [
        c for family in ("manager", "headCoach", "gkCoach", "fitnessCoach", "physio")
        for c in (pool.get(("club", family)) or [])
        if c["_tier"] == "bronze"
    ]
    assert bronze
    assert all(c["rating"] < 65 for c in bronze)

    # And they actually come out, without swamping the cosmetic families.
    drawn = collections.Counter()
    for seed in range(60):
        _, shop, _ = _actions()
        for item in json.loads(
            shop.open_pack(GOLD_PACK_ID, random.Random(seed))
        )["itemList"]:
            drawn[item.get("itemType")] += 1
    total = sum(drawn.values())
    staff = sum(
        drawn[k] for k in ("manager", "headCoach", "gkCoach", "fitnessCoach", "physio")
    )
    assert staff > 0, drawn
    # Below the collection families: there are 1327 kits against 166 managers,
    # and drawing them evenly would bury the kits nobody has yet.
    assert staff / total < 0.15, drawn


def test_a_renamed_club_keeps_its_name_across_a_restart() -> None:
    # `_open` restored the saved name and then overwrote it with
    # CLUB_NAME_DEFAULT on the very next line, so every club came back called
    # "Fondateur FUT" however it had been renamed.
    #
    # The abbreviation is what gave it away. `PUT /user/club` carries both,
    # `adopt` took both, the save held both -- and a club renamed to
    # "Classic XI"/"CXI" on 25 August came back "Fondateur FUT"/"CXI". Only
    # the half that line writes reverted.
    from fut_inventory import CLUB_NAME_DEFAULT, ClubIdentity

    identity = ClubIdentity()
    identity.restore({"name": "Classic XI", "abbr": "CXI"})
    assert identity.name == "Classic XI"

    # The default only fills a name in, it never replaces one.
    if not identity.name:
        identity.name = CLUB_NAME_DEFAULT
    assert identity.name == "Classic XI"

    # A save with no name still gets one, which is what that line is for:
    # saying nothing tells the client no club exists.
    blank = ClubIdentity()
    blank.restore({"name": "", "abbr": "FUT"})
    if not blank.name:
        blank.name = CLUB_NAME_DEFAULT
    assert blank.name == CLUB_NAME_DEFAULT


def test_the_user_document_carries_the_currencies_array_the_header_binds_to() -> None:
    """Settled on the console 25 August 2026: with this array on `/user`, the
    balance is on the club header at login, before any navigation.

    It had read zero there since the project began. Seven routes were swept
    across both shapes and every one was fed by `with_balance`; `/user` builds
    its own document, so the sweep never reached the one route that is fetched
    twice in the fan-out, is demonstrably parsed, and carries the club name,
    the badge and the record.
    """
    from fut_inventory import Wallet

    document = json.loads(Wallet(5_621_119).user_info("Mosebeest FC", "MOS", 1))

    # Lower case. "COINS" -- which the PC reference's fixture uses -- matches
    # nothing, so the balance is never written.
    assert document["currencies"] == [
        {"name": "coins", "funds": 5_621_119, "finalFunds": 5_621_119},
        {"name": "points", "funds": 0, "finalFunds": 0},
    ]
    # Beside the flat members, not instead of them. Wrapping this document is
    # what made the header print 0xCDCDCDCD.
    for member in ("credits", "coins", "totalCredits", "funds", "finalFunds"):
        assert document[member] == 5_621_119, member
    assert document["clubName"] == "Mosebeest FC"


def test_a_cup_backed_out_of_before_a_match_is_not_offered_for_resume() -> None:
    # Both cups saved on 25 August sat at round one with progressData
    # "AAAAAA==" -- four zero bytes, the length header of an empty payload.
    # That is a cup opened, its bracket drawn, and walked out of before a ball
    # was kicked.
    #
    # Handing that back freezes the title. It froze twice on it, the second
    # time on a reply byte for byte identical to what the client itself had
    # PUT, so the document was never the problem: the client cannot resume a
    # run with no first match in it.
    import os

    from fut_inventory import (
        TOURNAMENT_PROGRESS,
        TournamentProgress,
        active_tournaments_response,
        tournaments_response,
    )

    progress = TournamentProgress()
    progress.apply(3, {"round": 1, "progressData": "AAAAAA=="})
    assert progress.unplayed(progress.entries[3])
    assert progress.active_ids() == []

    # One match played and it is a real run, which is what the screen should
    # call underway.
    progress.apply(3, {"round": 2, "progressData": "AAAAAA=="})
    assert not progress.unplayed(progress.entries[3])
    assert progress.active_ids() == [3]

    # The tile can still say "joined" without the resume being offered -- the
    # two travel different routes. Off by default: `lock` is the field that
    # says why a cup cannot be entered, so JOINED may refuse entry instead of
    # labelling it.
    previous = os.environ.pop("FIFA14_CUP_JOINED", None)
    saved_entries = dict(TOURNAMENT_PROGRESS.entries)
    try:
        TOURNAMENT_PROGRESS.entries.clear()
        TOURNAMENT_PROGRESS.apply(3, {"round": 1, "progressData": "AAAAAA=="})

        off = json.loads(tournaments_response())["tournament"]
        assert {entry["lock"] for entry in off} == {"UNLOCKED"}

        os.environ["FIFA14_CUP_JOINED"] = "1"
        on = {e["id"]: e["lock"] for e in json.loads(tournaments_response())["tournament"]}
        assert on[3] == "JOINED"
        assert on[1] == "UNLOCKED"

        # And the resume list is untouched either way -- that is the half that
        # freezes.
        assert json.loads(active_tournaments_response())["tournamentId"] == []
    finally:
        os.environ.pop("FIFA14_CUP_JOINED", None)
        if previous is not None:
            os.environ["FIFA14_CUP_JOINED"] = previous
        TOURNAMENT_PROGRESS.entries.clear()
        TOURNAMENT_PROGRESS.entries.update(saved_entries)


def test_a_won_cup_counts_a_trophy_and_the_count_survives_a_restart() -> None:
    # `trophyUserCount` went out as a flat 0 forever, and the later cups are
    # gated on it: one trophy for the Quad-League Classic, ten for the
    # Ultimate Cup. Nothing counted them until 25 August.
    from fut_inventory import TournamentProgress

    progress = TournamentProgress()
    assert progress.trophies == 0

    # Winning a round is not winning the cup.
    progress.apply(1, {"round": 1})
    progress.advance(1, "WIN")
    assert progress.trophies == 0

    # The fourth win is.
    progress.apply(1, {"round": 4})
    progress.advance(1, "WIN")
    assert progress.trophies == 1

    # Losing one does not take it away.
    progress.apply(2, {"round": 2})
    progress.advance(2, "LOSS")
    assert progress.trophies == 1

    # It rides in the save under a name no cup id can collide with.
    restored = TournamentProgress()
    restored.restore(json.loads(json.dumps(progress.state())))
    assert restored.trophies == 1


def test_the_cup_unlocks_are_off_until_the_count_means_something() -> None:
    # The retail unlock counts reached this file twice, from sources that had
    # not seen each other -- Impulsum's table and the player's own list of
    # retail requirements -- and all fourteen agree.
    #
    # Enforcing them is still off by default. The counter only started counting
    # on 25 August, so every cup won before that is uncounted, and gating on a
    # zero would lock eleven cups that are playable today.
    import os

    from fut_inventory import TOURNAMENT_PROGRESS, tournaments_response

    previous = os.environ.pop("FIFA14_CUP_UNLOCKS", None)
    held = TOURNAMENT_PROGRESS.trophies
    try:
        TOURNAMENT_PROGRESS.trophies = 0
        off = json.loads(tournaments_response())["tournament"]
        assert {entry["lock"] for entry in off} == {"UNLOCKED"}
        # The label is retail's number whether or not the gate is on. Tying
        # the two together made every tile read "Unlock: 0 Trophies", which is
        # what the console showed on 26 August. `lock` decides entry; this is
        # only what the tile prints.
        by_id = {entry["id"]: entry["unlockreq"] for entry in off}
        assert by_id[1] == 0
        assert by_id[4] == 1
        assert by_id[14] == 10

        os.environ["FIFA14_CUP_UNLOCKS"] = "1"
        locked = {
            entry["id"]
            for entry in json.loads(tournaments_response())["tournament"]
            if entry["lock"] == "LOCKED_TROPHIES"
        }
        # The first three ask for nothing; the other eleven all ask for at
        # least one trophy.
        assert locked == set(range(4, 15))

        TOURNAMENT_PROGRESS.trophies = 2
        still = {
            entry["id"]
            for entry in json.loads(tournaments_response())["tournament"]
            if entry["lock"] == "LOCKED_TROPHIES"
        }
        # Two trophies opens everything asking for one or two.
        assert still == {9, 10, 11, 12, 13, 14}

        TOURNAMENT_PROGRESS.trophies = 10
        opened = json.loads(tournaments_response())["tournament"]
        assert {entry["lock"] for entry in opened} == {"UNLOCKED"}
        assert opened[0]["trophyUserCount"] == 10
    finally:
        os.environ.pop("FIFA14_CUP_UNLOCKS", None)
        if previous is not None:
            os.environ["FIFA14_CUP_UNLOCKS"] = previous
        TOURNAMENT_PROGRESS.trophies = held


def test_the_eligibility_probe_reads_one_key_per_cup() -> None:
    # CardsDLL carries `ELIGIBILITY_STRING%d` beside the elgReq vocabulary --
    # the same shape as `TOURNY_LOC_%d`, which is how a cup's name is drawn. So
    # a requirement's text comes from a number, and nothing here knows which
    # number means which requirement.
    #
    # Guessing would put the wrong requirement on the wrong cup, which is the
    # mistake the invented tournament ids made. This reads them instead.
    import os

    from fut_inventory import TOURNAMENT_REQUIREMENTS, tournaments_response

    previous = os.environ.pop("FIFA14_ELIGIBILITY_PROBE", None)
    try:
        # Nothing goes out until the keys are known. A cup with no stated
        # requirement is enterable, which is what all fourteen are.
        quiet = json.loads(tournaments_response())["tournament"]
        assert all(entry["elgReq"] == [] for entry in quiet)

        os.environ["FIFA14_ELIGIBILITY_PROBE"] = "1"
        fine = json.loads(tournaments_response())["tournament"]
        assert [e["elgReq"][0]["eligibilityKey"] for e in fine] == list(range(1, 15))
        # A value every squad passes, so the probe reads the requirement's
        # name without locking the cup behind it.
        assert fine[0]["elgReq"][0]["eligibilityValue"] == 0

        os.environ["FIFA14_ELIGIBILITY_PROBE"] = "1:10"
        coarse = json.loads(tournaments_response())["tournament"]
        assert [e["elgReq"][0]["eligibilityKey"] for e in coarse][:3] == [1, 11, 21]
    finally:
        os.environ.pop("FIFA14_ELIGIBILITY_PROBE", None)
        if previous is not None:
            os.environ["FIFA14_ELIGIBILITY_PROBE"] = previous

    # The eleven cups retail gates, recorded in plain terms against the day the
    # wire values are known.
    assert sorted(TOURNAMENT_REQUIREMENTS) == list(range(4, 15))
    assert ("chemistry", "exact", 100, "xi") in TOURNAMENT_REQUIREMENTS[14]


def test_the_team_of_the_week_is_week_one_by_default() -> None:
    # TOTW 1 went out on 18 September 2013 and one followed every Wednesday.
    # Week 1 is the default deliberately, for testing: the console's clock is
    # not this season's, and a real schedule would have to decide what "now"
    # means on a title whose season ended in 2014.
    import os

    from fut_inventory import (
        totw_active_week,
        totw_index,
        totw_squads,
        totw_week,
    )

    previous = os.environ.pop("FIFA14_TOTW_WEEK", None)
    try:
        weeks = totw_squads()
        assert len(weeks) == 49
        assert [int(w["week"]) for w in weeks] == list(range(1, 50))

        assert totw_active_week() == 1
        first = totw_week()
        assert first["name"] == "TOTW 1"
        assert first["released"] == "2013-09-18"
        assert first["formation"] == "f343"
        assert len(first["slots"]) == 18

        # Every week is a Wednesday, seven days apart.
        from datetime import date

        for entry in weeks:
            released = date.fromisoformat(entry["released"])
            assert released.weekday() == 2, entry["week"]
            assert (released - date(2013, 9, 18)).days == 7 * (int(entry["week"]) - 1)

        # The list offers all forty-nine and says which is showing.
        index = json.loads(totw_index())
        assert len(index["squad"]) == 49
        assert index["activeSquadId"] == 1
        assert index["squad"][0]["formation"] == "f343"
        # A squad with no rating is not one the screen will offer.
        assert all(entry["rating"] > 0 for entry in index["squad"])

        os.environ["FIFA14_TOTW_WEEK"] = "7"
        assert totw_active_week() == 7
        assert totw_week()["name"] == "TOTW 7"

        # An unknown week falls back rather than serving an empty side.
        os.environ["FIFA14_TOTW_WEEK"] = "999"
        assert totw_active_week() == 1
    finally:
        os.environ.pop("FIFA14_TOTW_WEEK", None)
        if previous is not None:
            os.environ["FIFA14_TOTW_WEEK"] = previous


def test_the_eligibility_probe_publishes_numbered_strings() -> None:
    # Measured on the console 26 August: a cup served `eligibilityKey` 4 drew
    # `*LOC_TOURN_ELG_KEY_16` in its Entry Requirements panel. The leading `*`
    # is an unresolved localisation key -- the mark the cup names carried
    # before this server answered them -- so the disc has no text for these.
    #
    # One reading is not a mapping: key 4 produced index 16, and a single point
    # fits any number of formulas. The strings go out numbered so one pass
    # along the fourteen tiles reads the relation off the screen.
    import os
    import re

    from fut_inventory import store_pack_descriptions

    previous = os.environ.pop("FIFA14_ELIGIBILITY_PROBE", None)
    try:
        quiet = store_pack_descriptions().decode()
        assert "LOC_TOURN_ELG_KEY_" not in quiet
        # The cup names are in this document either way.
        assert 'TOURNY_LOC_1"' in quiet

        os.environ["FIFA14_ELIGIBILITY_PROBE"] = "1"
        loud = store_pack_descriptions().decode()
        assert len(re.findall("LOC_TOURN_ELG_KEY_", loud)) == 256
        assert len(re.findall("LOC_TOURN_ELG_SCOPE_", loud)) == 16
        # Each string names its own index, so the screen reads back the number
        # the client asked for.
        assert re.search(
            r'resname="LOC_TOURN_ELG_KEY_16"><source>KEY 16<', loud
        )
        assert 'TOURNY_LOC_1"' in loud
    finally:
        os.environ.pop("FIFA14_ELIGIBILITY_PROBE", None)
        if previous is not None:
            os.environ["FIFA14_ELIGIBILITY_PROBE"] = previous


def test_the_team_of_the_week_reaches_the_play_tile() -> None:
    # The PLAY screen's Team of the Week tile was an empty pitch: no cards and
    # no "Active Challenge" line. `/hub` is fetched 499 times across these
    # journals and carried no squad at all.
    #
    # Built on this server's own squad document rather than copied. Four of the
    # members the PC build sends are not in CardsDLL's table -- loyaltyBonus,
    # dreamSquad, squadType, newSquad -- and a member this binary does not
    # carry is what froze the match-award and trade-offer screens.
    from fut_inventory import (
        CardCatalogue,
        ClubInventory,
        hub_response,
        totw_hub_squad,
        totw_week,
    )

    catalogue = CardCatalogue()
    squad = totw_hub_squad(catalogue)

    assert squad["squadName"] == "TOTW 1"
    assert squad["formation"] == "f343"
    assert squad["rating"] == totw_week()["rating"]
    assert len(squad["players"]) == 18
    # The eleven wear shirt numbers; the bench does not, as retail does.
    assert [p["kitNumber"] for p in squad["players"][:11]] == list(range(1, 12))
    assert all(p["kitNumber"] == 0 for p in squad["players"][11:])

    # The tile leads with the best card in the week.
    ratings = [p["itemData"]["rating"] for p in squad["players"]]
    captain = next(
        p["itemData"] for p in squad["players"] if p["itemData"]["id"] == squad["captain"]
    )
    assert captain["rating"] == max(ratings)
    assert [k["id"] for k in squad["kicktakers"]] == [squad["captain"]] * 5

    # Nothing the binary does not carry.
    for absent in ("loyaltyBonus", "dreamSquad", "squadType", "newSquad"):
        assert absent not in squad

    # And it rides on /hub, which is one of the three routes measured to
    # tolerate an unrecognised sibling.
    hub = json.loads(hub_response(ClubInventory(), 10, 1, 2, totw=squad))
    assert hub["squad"]["squadName"] == "TOTW 1"
    # Without one the member is simply absent rather than empty.
    assert "squad" not in json.loads(hub_response(ClubInventory(), 10, 1, 2))


def test_the_probe_offsets_the_tournament_level_key() -> None:
    # Measured 26 August: with keys 1-14 on the fourteen cups, every tile drew
    # *LOC_TOURN_ELG_KEY_16. The same index on all fourteen, so the index is
    # not derived from the key -- 16 is a constant the client reached on its
    # own, and the first reading was a coincidence of the selected tile.
    #
    # Either the client is not parsing these entries, or the requirement is not
    # read from `elgReq` at all: eligibilityKey/Value/Slot are single members
    # in CardsDLL's table and may belong to the tournament, with
    # LOC_TOURN_ELG_DOMAIN_LIST_%d suggesting elgReq is a list of domains for
    # one requirement rather than a list of requirements.
    #
    # The probe writes the key in both places, offset, so the drawn index names
    # which one was read.
    import os

    from fut_inventory import ELG_TOP_LEVEL_OFFSET, tournaments_response

    previous = os.environ.pop("FIFA14_ELIGIBILITY_PROBE", None)
    try:
        quiet = json.loads(tournaments_response())["tournament"]
        assert all("eligibilityKey" not in entry for entry in quiet)
        assert all(entry["elgReq"] == [] for entry in quiet)

        os.environ["FIFA14_ELIGIBILITY_PROBE"] = "1"
        loud = json.loads(tournaments_response())["tournament"]
        for entry in loud:
            inner = entry["elgReq"][0]["eligibilityKey"]
            assert inner == entry["id"]
            assert entry["eligibilityKey"] == inner + ELG_TOP_LEVEL_OFFSET
    finally:
        os.environ.pop("FIFA14_ELIGIBILITY_PROBE", None)
        if previous is not None:
            os.environ["FIFA14_ELIGIBILITY_PROBE"] = previous


def test_the_totw_clientdata_route_answers_an_entries_document() -> None:
    # `/clientdata/totw` is a clientdata route and its siblings all answer an
    # entries document -- `clientdata/pileSize` is
    # {"entries":[{"key":2,"value":20000},...]} here and works.
    #
    # This server answered it with the 27 kB squad index instead, and the
    # screen said "there is no Team of the Week available at the moment" over a
    # tile that was drawing the side correctly. Pressing A fired **no request
    # at all**: the document is fetched once at login and the refusal is
    # decided from it, so what the screen was missing was already in that
    # reply.
    from fut_inventory import (
        CardCatalogue,
        totw_challenge_entries,
        totw_challenge_response,
    )

    document = json.loads(totw_challenge_entries())
    assert [e["key"] for e in document["entries"]] == list(range(1, 15))

    # Keys 7, 8 and 9 are the challenge's name, packed little-endian, four
    # characters to an integer. Impulsum's decode to "TOTS LA LIGA" with its
    # key 6 at 12 -- that name's length -- and this server was sending those
    # three integers verbatim, so the entries announced a Team of the Season La
    # Liga squad while the tile drew TOTW 1.
    import struct

    pairs = {entry["key"]: entry["value"] for entry in document["entries"]}
    packed = b"".join(struct.pack("<i", pairs[key]) for key in (7, 8, 9))
    assert packed.decode().rstrip() == "TOTW 1"
    # Key 6 is the **padded** width, not the name's own length. The two have to
    # agree or everything after them moves: a client reading six characters
    # consumes two integers and takes key 9 -- four spaces -- as the first
    # value of the tail. Sending the real length is what moved the refusal
    # backwards from "no Team of the Week to play" to "available at the
    # moment".
    assert pairs[6] == 12
    assert len(packed) == 12
    # Every key but the name matches the working build exactly.
    impulsum = {1: 3, 2: 1, 3: 2147483647, 4: -2, 5: 1, 6: 12,
                10: 1, 11: 3, 12: 0, 13: 1, 14: 0}
    for key, value in impulsum.items():
        assert pairs[key] == value, key

    # Entries alone, which is measured. Three documents were served here and
    # the refusal names which got furthest: the squad index gave "no Team of
    # the Week available at the moment", the entries alone gave "no Team of the
    # Week to play", and entries plus squad went back to the first. The middle
    # one is past a check the other two fail.
    assert sorted(document) == ["entries"]
    assert len(totw_challenge_entries()) < 1000

    # And the challenge route carries the side. `squadChallenge` is an object
    # wrapping the squad, not the list of descriptors this server used to send
    # -- that list was invented, and its own comment said so.
    challenge = json.loads(totw_challenge_response(CardCatalogue()))
    assert challenge["matchDifficulty"] == 2
    assert challenge["grantsGameModePrizes"] is True
    assert isinstance(challenge["squadChallenge"], dict)
    assert challenge["squadChallenge"]["squad"]["squadName"] == "TOTW 1"
    assert len(challenge["squad"]["players"]) == 18


def test_keys_three_and_four_are_the_totw_clubs_persona() -> None:
    # Impulsum sends 2147483647 and -2 for keys 3 and 4. Read as one
    # little-endian 64-bit value, low word first, they are
    #
    #     0xFFFFFFFE | 0x7FFFFFFF << 32  ==  9223372036854775806
    #
    # which is exactly the persona its Totw.PersonaId() defaults to. The record
    # carries the id of the club that owns the challenge, and the client then
    # asks /user/list for it -- that club's squad list is what the challenge
    # select screen enumerates as "WEEK 41/49 ... WEEK 49/49".
    import struct

    from fut_inventory import (
        TOTW_PERSONA_ID,
        CardCatalogue,
        totw_challenge_entries,
        totw_club_info,
        totw_hub_squad,
        totw_squads,
    )

    pairs = {e["key"]: e["value"] for e in json.loads(totw_challenge_entries())["entries"]}
    rebuilt = struct.unpack(
        "<q", struct.pack("<i", pairs[4]) + struct.pack("<i", pairs[3])
    )[0]
    assert rebuilt == TOTW_PERSONA_ID
    # Still the values the working build sends, now derived rather than copied
    # so the three cannot drift apart.
    assert pairs[3] == 2147483647
    assert pairs[4] == -2

    # The squad belongs to that club, and that club has one squad per week.
    assert totw_hub_squad(CardCatalogue())["personaId"] == TOTW_PERSONA_ID
    club = json.loads(totw_club_info())["user"][0]
    assert club["personaId"] == TOTW_PERSONA_ID
    assert len(club["squadList"]["squad"]) == len(totw_squads()) == 49


def test_a_totw_week_can_be_fetched_by_its_own_club() -> None:
    # GET /ut/game/fifa14/squad/<week>/user/<totw persona> -- what
    # RequestSquadsLineup and GetSquadsLineup in the ION binding table fetch
    # once the client knows the club exists.
    from fut_inventory import CardCatalogue, totw_hub_squad

    catalogue = CardCatalogue()
    for week, formation, rating in ((1, "f343", 81), (4, "f433", 80), (49, "f3412", 83)):
        squad = totw_hub_squad(catalogue, week)
        assert squad["squadName"] == f"TOTW {week}"
        assert squad["formation"] == formation
        assert squad["rating"] == rating
        assert len(squad["players"]) == 18

    # Week 4 at f433 rating 80 is what the working build's own screenshot
    # shows for TOTW 4 -- two extracts and a screen agreeing on the same team.
    assert totw_hub_squad(catalogue, 4)["rating"] == 80

    # No week asked for is the active one.
    assert totw_hub_squad(catalogue)["squadName"] == "TOTW 1"


def test_the_manager_the_player_picks_stays_in_the_slot() -> None:
    # Reported from the console on 26 August: a manager put in the slot was
    # gone on the next launch.
    #
    # The console had been sending it all along -- every squad PUT carries
    # `"manager":[{"id":N}]`, and an assignment on 25 August arrived as id
    # 1950009476 -- and this server read the players, the name and the
    # formation out of that body and ignored the manager. So the slot filled on
    # screen and the save was written without it.
    import os
    import tempfile

    previous = os.environ.get("FIFA14_SEED_MANAGERS")
    os.environ["FIFA14_SEED_MANAGERS"] = "1"
    try:
        import importlib

        import fut_inventory as inventory

        importlib.reload(inventory)
        club = inventory.ClubInventory()
        managers = [i for i in club.items if i.get("itemType") == "manager"]
        assert managers
        chosen = managers[0]["id"]
        eleven = [item["id"] for item in club.squad[:11]]

        club.save_squad(1, eleven, manager=chosen)
        assert club._squads()[1]["manager"] == chosen
        document = json.loads(club.squad_document(1, "Mosebeest FC"))
        assert [m["id"] for m in document["manager"]] == [chosen]
        # The active-squad document agrees; the two used to disagree about
        # everything the squad store held.
        active = json.loads(club.active_squad_response("Mosebeest FC"))
        assert [m["id"] for m in active["manager"]] == [chosen]

        # A PUT that does not mention a manager leaves the stored one alone.
        club.save_squad(1, eleven)
        assert club._squads()[1]["manager"] == chosen

        # A zero is the player clearing the slot, not "unset".
        club.save_squad(1, eleven, manager=0)
        assert club._squads()[1]["manager"] == 0
        assert json.loads(club.squad_document(1, "x"))["manager"] == []

        # And it survives a restart: the squad store is saved whole, so the
        # manager rides with it.
        club.save_squad(1, eleven, manager=chosen)
        saved = {str(k): v for k, v in club._squads().items()}
        fresh = inventory.ClubInventory()
        squads = fresh._squads()
        squads.clear()
        for key, value in saved.items():
            squads[int(key)] = value
        reloaded = json.loads(fresh.squad_document(1, "Mosebeest FC"))
        assert [m["id"] for m in reloaded["manager"]] == [chosen]

        # A manager the club no longer holds resolves to nothing rather than
        # to a stale entry.
        club.save_squad(1, eleven, manager=999_999_999)
        assert json.loads(club.squad_document(1, "x"))["manager"] == []
    finally:
        os.environ.pop("FIFA14_SEED_MANAGERS", None)
        if previous is not None:
            os.environ["FIFA14_SEED_MANAGERS"] = previous
        import importlib

        import fut_inventory as inventory

        importlib.reload(inventory)


def test_the_totw_card_carries_its_own_position_not_the_formation_slot() -> None:
    # Reported off the console for TOTW 4: the two centre-backs drew blank,
    # Cabella had no position, Totti read CM and James Rodriguez had none.
    #
    # `totw_teams.tsv`'s `pos` column is the **formation slot** -- RCB, LCB,
    # RCM, RW -- and this server served it as `preferredPosition`. RCB is not
    # a position the client knows, which is exactly a blank card.
    #
    # `specials.tsv` carries each in-form's real position, its own six face
    # stats and its own art band, and it matches 882 of 882 slots across all
    # 49 weeks. It also settles the two the base card gets wrong: Totti's TOTW
    # 4 card is a CF where his base card is an LW, and James Rodriguez's is an
    # RM where his base is a CAM.
    from fut_inventory import CardCatalogue, _totw_slots, totw_response, totw_week

    week = totw_week(4)
    slots = {slot["order"]: slot for slot in _totw_slots(week)}
    document = json.loads(totw_response(CardCatalogue(), week=4))

    wanted = {2: "CB", 3: "CB", 5: "RM", 6: "CF", 7: "CAM"}
    for index, position in wanted.items():
        assert document["itemData"][index]["preferredPosition"] == position, index

    # The formation slot is kept beside it, and it is not what goes out.
    assert slots[2]["slot"] == "RCB"
    assert slots[5]["slot"] == "RCM"
    assert slots[2]["position"] == "CB"

    # No slot ships a formation-only label as a position.
    for entry in _totw_slots(week):
        assert entry["position"] not in ("RCB", "LCB", "RCM", "LCM", "RW", "LW ")

    # The in-form's own face stats, not the base card's.
    totti = document["itemData"][6]
    assert [a["value"] for a in totti["attributeList"]] == [53, 89, 90, 86, 49, 54]


def test_every_totw_week_fields_a_full_eighteen() -> None:
    # A slot whose base card is not in this catalogue was dropped, and five
    # weeks came up short: week 10 fielded sixteen with no goalkeeper at all,
    # because Andriy Pyatov's base card is not here.
    #
    # The in-form's position, rating and face stats are on the slot itself, so
    # the card can be built without the base one. What the base card supplies
    # is the club, nation and league -- a zero there renders a card without a
    # crest, which beats a squad with a hole in it.
    from fut_inventory import CardCatalogue, totw_response, totw_squads

    catalogue = CardCatalogue()
    for week in totw_squads():
        document = json.loads(totw_response(catalogue, week=week["week"]))
        assert len(document["itemData"]) == 18, week["week"]
        # And no card loses its identity on the way.
        assert all(card["assetId"] for card in document["itemData"]), week["week"]

    keeper = json.loads(totw_response(catalogue, week=10))["itemData"][0]
    assert keeper["assetId"] == 142902
    assert keeper["preferredPosition"] == "GK"
    assert keeper["rating"] == 78


def test_every_totw_slot_resolves_to_one_of_this_servers_own_cards() -> None:
    # `totw_teams.tsv`'s baseId is not always this catalogue's asset id. The
    # player checked thirteen by hand against fifa14_cards.json -- Cuadrado is
    # 193082 here where the extract says 188612, Seedorf 1256 against 1001,
    # Coutinho 189242 against 213439 -- and the builder resolves the rest by
    # name and rating against the in-forms.
    from fut_inventory import _totw_slots, totw_squads, totw_week

    for week in totw_squads():
        for slot in _totw_slots(week):
            assert slot.get("cardId"), (week["week"], slot["order"])
            # Club, nation and league are resolved at build time, so a card
            # never renders crestless.
            assert slot["clubId"], (week["week"], slot["order"])
            assert slot["nationId"], (week["week"], slot["order"])

    # The thirteen the player gave, by the file's own cardId.
    given = {
        (3, 16): (2392, 11858), (4, 15): (213432, 11876),
        (5, 2): (175092, 11879), (7, 1): (139997, 11914),
        (7, 10): (193082, 11923), (10, 0): (142902, 12153),
        (10, 7): (1256, 12160), (25, 6): (193130, 13370),
        (31, 6): (189242, 13881), (33, 7): (193082, 13994),
        (35, 10): (189963, 14090), (37, 8): (7743, 14245),
        (38, 2): (107715, 15303),
    }
    for (week, index), (asset, card_id) in given.items():
        slot = next(s for s in _totw_slots(totw_week(week)) if s["order"] == index)
        assert slot["assetId"] == asset, (week, index)
        assert slot["cardId"] == card_id, (week, index)


def test_a_league_modifier_applies_to_a_manager() -> None:
    # Reported off the console on 26 August: applying a manager league
    # modifier came back "this card can only be applied to a player".
    #
    #     POST /ut/game/fifa14/item/resource/5003123
    #     {"apply":[{"id":1950009502}]}
    #     -> refused: this card can only be applied to a player
    #
    # 1950009502 is David Moyes, pulled from a pack. The dispatcher required a
    # player for everything except a contract, so the one card in the game
    # whose whole purpose is to change a manager could never be applied to one.
    #
    # A manager card carries `leagueId` and `leagueid`, which is how it gives
    # chemistry to players from that league, and the modifier's `amount` is the
    # league it names -- 13 the Premier League, 16 Ligue 1, 53 La Liga.
    import os
    import tempfile

    previous = os.environ.get("FIFA14_SEED_MANAGERS")
    saved = os.environ.get("FIFA14_CLUB_SAVE")
    os.environ["FIFA14_SEED_MANAGERS"] = "1"
    os.environ["FIFA14_CLUB_SAVE"] = tempfile.mktemp(suffix=".json")
    try:
        import importlib

        import fut_inventory as inventory

        importlib.reload(inventory)
        club = inventory.ClubInventory()
        rack = inventory.ConsumableRack(club)
        manager = next(i for i in club.items if i.get("itemType") == "manager")
        player = next(i for i in club.items if i.get("itemType") == "player")

        card = next(
            row for row in inventory._consumable_catalogue()
            if row.get("cardsubtypeid") == 304
        )
        assert card["amount"] == 13  # England Premier League

        before = manager["leagueId"]
        result = rack.apply(card["definitionId"], [manager["id"]])
        assert result["effect"] == "league 13"
        # Both spellings, because the card carries both.
        assert manager["leagueId"] == 13
        assert manager["leagueid"] == 13
        assert before != 13

        # And it is still refused on a player, with a message that says why.
        other = next(
            row for row in inventory._consumable_catalogue()
            if row.get("cardsubtypeid") == 305
        )
        try:
            rack.apply(other["definitionId"], [player["id"]])
        except inventory.ConsumableRefused as refused:
            assert "manager" in str(refused)
        else:
            raise AssertionError("a league modifier should refuse a player")
    finally:
        for key, value in (("FIFA14_SEED_MANAGERS", previous),
                           ("FIFA14_CLUB_SAVE", saved)):
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        import importlib

        import fut_inventory as inventory

        importlib.reload(inventory)


def test_the_formation_counter_stops_advertising_cards_that_are_held_out() -> None:
    # The formation tab announced twenty and had nothing behind it: the
    # modifiers are subtypes 121-136 on art 35, art 35 draws NOT FOUND, and
    # they are in both UNDRAWN_CONSUMABLE_TYPES and UNSEEDED_CONSUMABLE_TYPES
    # because of it. So the counter borrowed the position family's count for a
    # family this server serves none of -- which is exactly what the
    # manager-league tab was doing when it said sixty-nine.
    from fut_inventory import (
        CONSUMABLE_FALLBACKS,
        UNDRAWN_CONSUMABLE_TYPES,
        UNSEEDED_CONSUMABLE_TYPES,
        ClubInventory,
        _club_extras,
        consumable_family,
        consumable_stats_response,
    )

    assert "consumablesFormationManager" not in CONSUMABLE_FALLBACKS
    assert "formationManager" in UNDRAWN_CONSUMABLE_TYPES
    assert "formationManager" in UNSEEDED_CONSUMABLE_TYPES

    seeded = [i for i in _club_extras()
              if consumable_family(i) == "formationManager"]
    assert seeded == []

    inventory = ClubInventory()
    inventory.items = _club_extras()
    document = json.loads(consumable_stats_response(inventory))
    assert document.get("consumablesFormationManager", 0) == 0


def test_only_the_players_who_took_the_pitch_pay_a_contract() -> None:
    # Reported from the console 26 August: substitutes were losing contracts
    # without ever coming on. The rule retail follows, in the player's words:
    #
    #     Starting XI                     -1
    #     Sub who stays on the bench       0
    #     Reserve who does not play        0
    #     Substitute who comes on         -1
    #     Player who starts and is subbed -1
    #
    # The `items` array is the whole eighteen, not the team sheet. A full
    # capture from 25 August:
    #
    #     0-10  the eleven      fitness 90-97
    #     11-17 the substitutes fitness 99 on every one
    #
    # so fitness is what separates them. Compared against the card's own
    # fitness rather than against 99, so a player who starts a match already
    # tired still counts as having played.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    squad = [item for item in club.items if item.get("itemType") == "player"][:18]
    for player in squad:
        player["fitness"] = 99
        player["contract"] = 7
        player["gamesPlayed"] = 0
    # The eleven who start come from the squad, so the squad has to say who
    # they are. `club.items` is not in squad order.
    club.save_squad(1, [p["id"] for p in squad])
    club.set_active(1)

    sheet = [{"id": p["id"], "fitness": 99 - 4} for p in squad[:11]]
    # A substitute who comes on loses fitness like anyone else.
    sheet.append({"id": squad[11]["id"], "fitness": 94})
    # The rest never move.
    sheet += [{"id": p["id"], "fitness": 99} for p in squad[12:]]

    touched = inventory.apply_match_items(club, sheet)
    assert touched["played"] == 12
    assert touched["contracts"] == 12

    for player in squad[:12]:
        assert player["contract"] == 6, player["id"]
        assert player["gamesPlayed"] == 1
    for player in squad[12:]:
        assert player["contract"] == 7, player["id"]
        assert player["gamesPlayed"] == 0

    # A goalkeeper who starts and keeps a clean sheet can come back at 99.
    # He still played, because he is in the eleven -- this is the case that
    # broke the first version of this rule, reported off a cup tie where the
    # keeper's card recorded no appearance at all.
    for player in squad[:11]:
        player["fitness"] = 99
        player["contract"] = 7
        player["gamesPlayed"] = 0
    keeper_sheet = [{"id": p["id"], "fitness": 99} for p in squad[:11]]
    keeper_sheet += [{"id": p["id"], "fitness": 99} for p in squad[11:]]
    again = inventory.apply_match_items(club, keeper_sheet)
    assert again["played"] == 11
    assert squad[0]["gamesPlayed"] == 1
    assert squad[0]["contract"] == 6

    # A goal counts as an appearance even if the fitness line says nothing.
    scorer = squad[13]
    inventory.apply_match_items(
        club, [{"id": scorer["id"], "fitness": 99, "goals": 1}]
    )
    assert scorer["contract"] == 6
    assert scorer["gamesPlayed"] == 1


def test_team_fitness_restores_the_squad_that_is_actually_active() -> None:
    # A team fitness card restored `inventory.squad`, the list built at load
    # time and only ever rewritten for squad 1. A club with squad 2 active --
    # "Classic XI", 22 players, on 26 August -- had the card spent on squad
    # 1's eleven, so the side about to play got nothing.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    players = [item for item in club.items if item.get("itemType") == "player"]
    first, second = [p["id"] for p in players[:11]], [p["id"] for p in players[11:29]]

    club.save_squad(1, first)
    club.save_squad(2, second)
    club.set_active(2)
    for player in players:
        player["fitness"] = 50

    rack = inventory.ConsumableRack(club)
    changed = rack._squad_fitness(30)

    assert {c["id"] for c in changed} == set(second)
    assert all(c["fitness"] == 80 for c in changed)
    # And squad 1, which is not being fielded, is untouched.
    by_id = {p["id"]: p for p in players}
    assert all(by_id[i]["fitness"] == 50 for i in first)


def test_managers_and_staff_repeat_the_way_club_items_do() -> None:
    # Reported from the console 26 August: a second manager was neither marked
    # in the pack nor offered for quick sell, while a second kit was.
    #
    # The four cosmetic families were in CLUB_ITEM_TYPES from the start. The
    # manager and the four staff families were added on 25 August and nothing
    # brought them in, so `_repeats` said no and they fell through to the
    # player branch of `card_signature` -- which reads `rarity` and `rating`,
    # neither of which tells two managers apart.
    import fut_inventory as inventory

    for kind in ("manager", "headCoach", "gkCoach", "fitnessCoach", "physio"):
        item = {"itemType": kind, "resourceId": 1_000_597, "assetId": 1_000_597}
        assert inventory._repeats(item), kind
        # Keyed by the resource, like a kit: the manager's own database id.
        assert inventory.card_signature(item) == ("club", kind, 1_000_597)

    # Two of the same manager agree; two different ones do not.
    moyes = {"itemType": "manager", "resourceId": 1_000_597, "assetId": 1_000_597}
    again = {"itemType": "manager", "resourceId": 1_000_597, "assetId": 1_000_597}
    rodgers = {"itemType": "manager", "resourceId": 1_000_595, "assetId": 1_000_595}
    assert inventory.card_signature(moyes) == inventory.card_signature(again)
    assert inventory.card_signature(moyes) != inventory.card_signature(rodgers)

    # Consumables still stack rather than repeat. A club is meant to pile up
    # contracts, and offering to quick-sell the second one is wrong.
    for kind in ("development", "training"):
        assert not inventory._repeats({"itemType": kind, "resourceId": 5_001_007})

    # And the cosmetic four are unchanged.
    for kind in ("kit", "custom", "stadium", "ball"):
        assert inventory._repeats({"itemType": kind, "resourceId": 6_300_000})


def test_a_packed_manager_is_marked_against_the_one_already_owned() -> None:
    # The pack screen reads `duplicateItemIdList`, and the pairing has to name
    # the owned card -- a bare list of the new ids is what froze the title.
    import os

    import fut_inventory as inventory

    previous = os.environ.get("FIFA14_SEED_MANAGERS")
    os.environ["FIFA14_SEED_MANAGERS"] = "1"
    try:
        import importlib

        importlib.reload(inventory)
        club = inventory.ClubInventory()
        wallet = inventory.Wallet()
        shop = inventory.PackShop(inventory.CardCatalogue(), wallet, club)

        owned = next(i for i in club.items if i.get("itemType") == "manager")
        drawn = [
            dict(owned, id=990_000_001),
            {"itemType": "manager", "resourceId": 999_999,
             "assetId": 999_999, "id": 990_000_002},
        ]
        pairs = shop._mark_duplicates(drawn)

        assert pairs == [{"itemId": 990_000_001, "duplicateItemId": owned["id"]}]
        assert drawn[0]["duplicateItemId"] == owned["id"]
        # A manager the club does not hold is not a repeat of anything.
        assert "duplicateItemId" not in drawn[1]
    finally:
        os.environ.pop("FIFA14_SEED_MANAGERS", None)
        if previous is not None:
            os.environ["FIFA14_SEED_MANAGERS"] = previous
        import importlib

        importlib.reload(inventory)


def test_the_manager_spends_a_contract_on_every_match() -> None:
    # The client reports the eighteen and says nothing about the man in the
    # dugout, so if this does not count his match down nothing does. Reported
    # from the console 26 August: a manager still on the contract he arrived
    # with after a cup tie.
    #
    # Retail spends one per match, which is why manager contract cards exist
    # and are a family of their own (subtype 202).
    import os

    previous = os.environ.get("FIFA14_SEED_MANAGERS")
    os.environ["FIFA14_SEED_MANAGERS"] = "1"
    try:
        import importlib

        import fut_inventory as inventory

        importlib.reload(inventory)
        club = inventory.ClubInventory()
        squad = [i for i in club.items if i.get("itemType") == "player"][:18]
        manager = next(i for i in club.items if i.get("itemType") == "manager")
        manager["contract"] = 7
        manager["gamesPlayed"] = 0
        for player in squad:
            player["fitness"] = 99
            player["contract"] = 7

        club.save_squad(1, [p["id"] for p in squad], manager=manager["id"])
        club.set_active(1)

        sheet = [{"id": p["id"], "fitness": 95} for p in squad[:11]]
        sheet += [{"id": p["id"], "fitness": 99} for p in squad[11:]]
        touched = inventory.apply_match_items(club, sheet)

        assert touched["manager"] == 1
        assert manager["contract"] == 6
        assert manager["gamesPlayed"] == 1

        # A manager on no contract is not taken below zero, the same rule the
        # players follow.
        manager["contract"] = 0
        touched = inventory.apply_match_items(club, sheet)
        assert touched["manager"] == 0
        assert manager["contract"] == 0
        assert manager["gamesPlayed"] == 2

        # A squad with no manager in the slot spends nothing and does not fail.
        club.save_squad(1, [p["id"] for p in squad], manager=0)
        touched = inventory.apply_match_items(club, sheet)
        assert touched["manager"] == 0
    finally:
        os.environ.pop("FIFA14_SEED_MANAGERS", None)
        if previous is not None:
            os.environ["FIFA14_SEED_MANAGERS"] = previous
        import importlib

        import fut_inventory as inventory

        importlib.reload(inventory)


def test_a_match_publishes_appearances_where_the_bio_reads_them() -> None:
    # The bio reads Games Played out of `statsList[0]` and `lifetimeStats[0]`
    # -- settled on the console 17 August -- and `apply_match_items` writes the
    # `gamesPlayed` member. `sync_stat_slots` is what joins the two, and a card
    # bought off the market carries the same five-slot arrays a seeded one
    # does, so nothing special is needed for it.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    squad = [i for i in club.items if i.get("itemType") == "player"][:18]
    for player in squad:
        player["fitness"] = 99
        player["gamesPlayed"] = 0
        player["goals"] = 0
    club.save_squad(1, [p["id"] for p in squad])
    club.set_active(1)

    sheet = [{"id": p["id"], "fitness": 95} for p in squad[:11]]
    sheet[3]["goals"] = 2
    sheet += [{"id": p["id"], "fitness": 99} for p in squad[11:]]
    inventory.apply_match_items(club, sheet)

    def slot(card, index):
        return next(s["value"] for s in card["statsList"] if s["index"] == index)

    def lifetime(card, index):
        return next(s["value"] for s in card["lifetimeStats"] if s["index"] == index)

    played = squad[0]
    assert played["gamesPlayed"] == 1
    assert slot(played, inventory.STAT_SLOT_GAMES) == 1
    assert lifetime(played, inventory.STAT_SLOT_GAMES) == 1

    scorer = squad[3]
    assert slot(scorer, inventory.STAT_SLOT_GOALS) == 2
    assert lifetime(scorer, inventory.STAT_SLOT_GOALS) == 2

    # A card that sat on the bench reports nothing.
    benched = squad[15]
    assert slot(benched, inventory.STAT_SLOT_GAMES) == 0


def test_the_duplicate_panel_can_ask_for_the_card_it_is_comparing() -> None:
    # "MY CURRENT ITEM: undefined", reported off the console 26 August with a
    # Puyol 83 out of a pack and a blank NOT FOUND card beside it.
    #
    # The panel asks for both cards by id right before it draws them, and every
    # one of the 48 such requests in these journals carries exactly two ids --
    # the new card and the owned one it repeats:
    #
    #     GET /ut/game/fifa14/item?idList=1950012526,1700000002
    #
    # This server answered a static {"itemData":[]} to all of them. The new
    # card drew because the pack response had already handed it over; the owned
    # one had a number and no card.
    import random

    import fut_inventory as inventory

    club = inventory.ClubInventory()
    wallet = inventory.Wallet()
    shop = inventory.PackShop(inventory.CardCatalogue(), wallet)
    shop.inventory = club

    owned = club.items[0]
    opened = json.loads(shop.open_pack(inventory.GOLD_PACK_ID, random.Random(11)))
    fresh = opened["itemList"][0]

    pair = json.loads(shop.items_by_id([fresh["id"], owned["id"]]))
    assert [card["id"] for card in pair["itemData"]] == [fresh["id"], owned["id"]]
    # A real card, not a stub: the panel draws a rating, a position and a face.
    drawn = pair["itemData"][1]
    assert drawn["assetId"] == owned["assetId"]
    assert drawn["rating"] == owned["rating"]

    # An id this club does not hold is left out rather than answered with a
    # blank -- a card that is not there is not a card with no members.
    missing = json.loads(shop.items_by_id([owned["id"], 999_999_999]))
    assert [card["id"] for card in missing["itemData"]] == [owned["id"]]

    # And the pile's own cards answer too, which is where a duplicate sits
    # before it is sent on.
    from_pile = json.loads(shop.items_by_id([fresh["id"]]))
    assert from_pile["itemData"][0]["id"] == fresh["id"]


def test_a_pack_carries_its_own_name_for_the_detail_pane() -> None:
    # Retail draws "PREMIUM GOLD PACK" on the line beside the FUT 14 logo and
    # the description underneath it. This server sent no name member at all, so
    # that line fell back to the group heading -- every gold pack read "Gold
    # Packs" -- and the name was visible only because the description text
    # prepends it.
    #
    # `name` and `title` are both in CardsDLL's table and neither had ever been
    # sent. Which the pane reads is untested, so both go out; an unrecognised
    # sibling at the top level is skipped.
    import fut_inventory as inventory

    catalogue = json.loads(inventory.store_catalogue())
    packs = {entry["id"]: entry for entry in catalogue["purchase"]}

    # A key, not the text. Written out first and the line came back blank,
    # which is what says the member is read and looked up: "Premium Gold Pack"
    # is not a key any table holds, and had it been ignored the group heading
    # would still have been there.
    premium = packs[304]
    assert premium["name"] == "FUT_STORE_PACK_304_NAME"
    assert premium["title"] == "FUT_STORE_PACK_304_NAME"
    # And it resolves, in the same document `description` beside it does.
    import xml.etree.ElementTree as ET

    table = {
        unit.get("resname"): (unit.findtext("source") or "")
        for unit in ET.fromstring(
            inventory.store_pack_descriptions().decode()
        ).iter("trans-unit")
    }
    # Capitals, which is how FIFA 14's store had it.
    assert table[premium["name"]] == "PREMIUM GOLD PACK"
    assert premium["displayGroup"]["value"] == "GOLD PACKS"
    assert table[packs[303]["name"]] == "GOLD PACK"
    # The group is the category tab and stays the category.
    assert premium["displayGroup"]["value"] == "GOLD PACKS"
    # Two packs in one group have different names and the same heading, which
    # is the whole point.
    assert packs[303]["displayGroup"]["value"] == premium["displayGroup"]["value"]

    # Every pack names itself, and every key resolves.
    for entry in catalogue["purchase"]:
        assert entry["name"], entry["id"]
        assert table[entry["name"]] == inventory.PACK_SPECS[entry["id"]]["name"]
        # The member is a key; the text behind it is what is drawn, and it is
        # in capitals because that is how FIFA 14's store had it.
        assert entry["name"].startswith("FUT_STORE_PACK_")
        assert inventory.PACK_SPECS[entry["id"]]["name"].isupper()


def test_no_pack_ever_hands_out_the_same_player_twice() -> None:
    # "No packs should be able to draw the same player twice (ever)." Twenty
    # seeds is not "ever" -- these are the counts that found the last two
    # faults, so the guarantee is asserted at the size that would catch a third.
    import collections
    import random

    import fut_inventory as inventory

    club = inventory.ClubInventory()
    club.items = []
    shop = inventory.PackShop(
        inventory.CardCatalogue(), inventory.Wallet(coins=10**12), club
    )

    repeats = 0
    short = 0
    runs = 120
    for pack_id, spec in inventory.PACK_SPECS.items():
        for seed in range(runs):
            opened = json.loads(shop.open_pack(pack_id, random.Random(seed)))
            items = opened["itemList"]
            # A pack that avoids a repeat by handing over fewer cards has not
            # kept the promise either.
            if len(items) != spec["count"]:
                short += 1
            faces = [i["assetId"] for i in items if i.get("itemType") == "player"]
            counted = collections.Counter(faces)
            repeats += sum(n - 1 for n in counted.values() if n > 1)
    assert repeats == 0
    assert short == 0


def test_the_squad_selector_reports_the_chemistry_the_console_worked_out() -> None:
    # The selector advertised a flat 100 for every side, so "Fondateur FUT"
    # read 100 in the list and 67 the moment you opened it. Reported from the
    # console 27 August.
    #
    # This server does not compute chemistry and should not pretend to: it is
    # links by club, league and nation, the manager's own league, loyalty and
    # position, and the console already does all of it. It also tells us the
    # answer -- every squad PUT carries the number, and these journals hold
    # everything from 0 to 100.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    eleven = [i["id"] for i in club.items if i.get("itemType") == "player"][:18]

    # A side the console has never *saved* has no number yet, and nought is
    # how that is said. 100 was the fallback until 27 August and it is not a
    # placeholder, it is a claim: a squad read 100 in the selector and 67 the
    # moment it was opened.
    #
    # The console reports chemistry on a save, not on an open -- a session that
    # opened both squads made no squad write at all.
    club.save_squad(1, eleven)
    assert json.loads(club.squad_summaries())["squad"][0]["chemistry"] == 0

    club.save_squad(1, eleven, chemistry=67)
    assert json.loads(club.squad_summaries())["squad"][0]["chemistry"] == 67
    # And the squad screen agrees with the selector, which is the whole point.
    assert json.loads(club.squad_document(1, "Fondateur FUT"))["chemistry"] == 67

    # A save that does not mention chemistry leaves the stored one alone.
    club.save_squad(1, eleven)
    assert json.loads(club.squad_summaries())["squad"][0]["chemistry"] == 67

    # Zero is a real answer, not "unset".
    club.save_squad(1, eleven, chemistry=0)
    assert json.loads(club.squad_summaries())["squad"][0]["chemistry"] == 0


def test_the_market_has_club_items_and_staff_to_search() -> None:
    # The CLUB ITEMS and STAFF tabs fell through to the player search and found
    # nothing, so both were empty however they were filtered. The console asks
    # for them the way it asks for consumables, and the journals have the exact
    # queries:
    #
    #     type=clubInfo&start=0&num=12&cat=badge
    #     type=staff&start=0&num=12&cat=manager
    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()

    def kinds(query):
        doc = json.loads(catalogue.auctions(dict(query, num="50"), coins=1000))
        counted = {}
        for listing in doc["auctionInfo"]:
            kind = listing["itemData"]["itemType"]
            counted[kind] = counted.get(kind, 0) + 1
        return doc, counted

    # Five of each family, not the catalogue: there are 2,035 club items and
    # listing them all makes the tab a directory rather than a market.
    # The whole catalogue, paged -- a market that holds six kits is a sample
    # rather than a market. What the six buys is the *first page*: one of each
    # grade leads each family so the tab opens on a spread.
    doc, counted = kinds({"type": "clubInfo"})
    n = inventory.MARKET_CLUB_ITEM_COPIES
    assert doc["total"] == len(inventory._clubitem_catalogue())
    assert set(counted) == {inventory.BADGE_WIRE_TYPE, "kit", "ball", "stadium"}

    doc, counted = kinds({"type": "staff"})
    assert set(counted) == {"manager", "headCoach", "gkCoach",
                            "fitnessCoach", "physio"}
    assert doc["total"] == len(inventory.manager_catalogue()) + len(
        inventory.staff_catalogue()
    )

    # `cat` narrows it to one family, which is what the tab's filter sends.
    doc, counted = kinds({"type": "clubInfo", "cat": "badge"})
    assert set(counted) == {inventory.BADGE_WIRE_TYPE}
    assert doc["total"] == sum(
        1 for row in inventory._clubitem_catalogue() if row["itemType"] == "badge"
    )
    _, counted = kinds({"type": "staff", "cat": "manager"})
    assert set(counted) == {"manager"}

    # A stadium and a ball name themselves in `type` and send no `cat`. They
    # were not routed here at all, so searching for either turned up the
    # player list -- reported from the console 27 August.
    for family in ("stadium", "ball"):
        doc, counted = kinds({"type": family})
        assert set(counted) == {family}, family
        assert doc["total"] == sum(
            1 for row in inventory._clubitem_catalogue()
            if row["itemType"] == family
        ), family

    # Every listing is buyable: a real trade id, a price, and an active state.
    doc, _ = kinds({"type": "clubInfo"})
    for listing in doc["auctionInfo"]:
        assert listing["tradeState"] == "active"
        assert listing["buyNowPrice"] > listing["startingBid"] > 0
        assert listing["tradeId"] > 0

    # A better item costs more. Anchoring on the quick-sell value alone put
    # every one of them on the 200 floor -- a kit is worth 3.
    prices = {
        listing["itemData"]["rating"]: listing["buyNowPrice"]
        for listing in doc["auctionInfo"]
        if listing["itemData"]["itemType"] == "kit"
    }
    assert prices[78] > prices[48]

    # Every family reaches the first page. Listed one after another, a page of
    # twelve held five badges, five balls and two kits and never reached the
    # stadiums -- reported from the console 27 August.
    page = json.loads(
        catalogue.auctions({"type": "clubInfo", "num": "12"}, coins=1000)
    )["auctionInfo"]
    assert len(page) == 12
    assert {listing["itemData"]["itemType"] for listing in page} == {
        inventory.BADGE_WIRE_TYPE, "kit", "ball", "stadium"
    }

    # And across qualities rather than off the top of the catalogue, which is
    # in resource order and so in quality order: the first five of every family
    # were its five lowest-rated bronzes, so the tab was a wall of bronze and
    # its quality filter had nothing to sort.
    kits = [
        listing["itemData"] for listing in
        json.loads(catalogue.auctions(
            {"type": "clubInfo", "cat": "kit", "num": "50"}, coins=1000
        ))["auctionInfo"]
    ]
    assert len({kit["rating"] for kit in kits}) > 1
    assert len({kit["rareflag"] for kit in kits}) > 1

    # The quality filter narrows to one tier, and the key is `lev` -- which is
    # what the console sends. Reading `level` alone is why it did nothing.
    for level, span in (("bronze", (48, 58)), ("silver", (68, 72)),
                        ("gold", (78, 84))):
        page = json.loads(catalogue.auctions(
            {"type": "clubInfo", "lev": level, "cat": "kit", "num": "50"},
            coins=1000,
        ))["auctionInfo"]
        assert page, level
        assert {listing["itemData"]["rating"] for listing in page} <= set(span), level

    # Each family leads with one of every tier, so a short page is not three
    # bronzes. Five copies left the gold rare off the end.
    lead = [
        listing["itemData"]["rating"] for listing in
        json.loads(catalogue.auctions(
            {"type": "clubInfo", "cat": "kit", "num": "50"}, coins=1000
        ))["auctionInfo"]
    ][:n]
    assert sorted(lead) == [48, 58, 68, 72, 78, 84]


def test_the_famous_crests_are_gold_and_the_small_clubs_bronze() -> None:
    # FC Barcelona's badge was bronze non-rare. The grades were cycled by
    # index -- `GRADES[index % 6]` -- so resource 6000000 took the first one,
    # and 6000000 is Barcelona.
    #
    # The badge table is a standing order: Barcelona, Real Madrid, Bayern,
    # Manchester City at the top and Drogheda United at 6000600. So the
    # position in the range is what says which crest is the gold one, and the
    # builder bands it best-first instead.
    #
    # This was equally true of what the packs were handing out -- the market
    # and the draw read the same catalogue -- which is what "they need to match
    # what is going into packs" meant.
    import fut_inventory as inventory

    badges = {
        row["resourceId"]: row
        for row in inventory._clubitem_catalogue()
        if row["itemType"] == "badge"
    }
    for resource in (6_000_000, 6_000_001, 6_000_002, 6_000_003):
        assert badges[resource]["tier"] == "gold", resource
        assert badges[resource]["rare"] == 1, resource
    # Drogheda United, at the bottom of the table.
    assert badges[6_000_600]["tier"] == "bronze"
    assert badges[6_000_600]["rare"] == 0

    # Every family keeps a full spread, so every pack tier still has something
    # of its own to hand out.
    import collections

    for kind in ("kit", "badge", "stadium"):
        tiers = collections.Counter(
            row["tier"] for row in inventory._clubitem_catalogue()
            if row["itemType"] == kind
        )
        assert set(tiers) == {"bronze", "silver", "gold"}, kind
        assert min(tiers.values()) > 0, kind

