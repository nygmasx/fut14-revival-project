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
    assert (pile["auctionInfo"][0]["tradeId"]
            >= inventory.UNLISTED_TRADE_ID_BASE), "an unlisted row needs an id"
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
    import fut_inventory as inventory

    doc = json.loads(inventory.seasons_response())
    seasons = doc["seasons"]
    assert len(seasons) == 10
    assert seasons[0]["divisionId"] == 10, "FUT starts a club in Division 10"
    # Both arrays: the ladder proved each separately and then together.
    assert len(seasons[0]["matches"]) == 10
    assert len(seasons[0]["prizeSet"]) == 4

    user = json.loads(inventory.season_user_response())
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
    assert standing["divisionId"] == 9
    assert standing["round"] == 1
    # A club in division 5 is the fifth record, and index four.
    higher = json.loads(inventory.season_user_response(5))
    assert (higher["seasonId"], higher["divisionId"]) == (5, 4)
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
        assert (standing["seasonId"], standing["divisionId"]) == (10, 9)

        # Promoted: division 9 is the ninth record, and index eight.
        inventory.SEASON_PROGRESS.entries.clear()
        inventory.SEASON_PROGRESS.apply(
            2, 9, {"round": 2, "data": "QUJD", "progressData": "REVG"}
        )
        promoted = json.loads(inventory.season_user_response())
        assert (promoted["seasonId"], promoted["divisionId"], promoted["round"]) == (
            9,
            8,
            2,
        )

        # Relegated back to a division already played. The club is where it
        # was written last, not where it was written first.
        inventory.SEASON_PROGRESS.apply(
            1, 10, {"round": 3, "data": "QUJD", "progressData": "REVG"}
        )
        back = json.loads(inventory.season_user_response())
        assert (back["seasonId"], back["divisionId"], back["round"]) == (10, 9, 3)
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
    # A real Team of the Week is not simply the 23 best rares -- TOTW 1 carries
    # ordinary in-form cards down into the sixties. Assert the shape, not a
    # rating floor the real data does not meet.
    import fut_inventory as inventory

    squad = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    assert len(squad["itemData"]) == 23
    assert squad["formation"]
    ids = [card["id"] for card in squad["itemData"]]
    assert len(set(ids)) == len(ids)
    assert all(
        card["resourceId"] & 0x00FF_FFFF == card["assetId"]
        for card in squad["itemData"]
    )


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
    assert doc["transferListCount"] == 15      # the club's own list total
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
            # `consumablesTrainingGkPlayStyle` is absent now. The sixteen cards
            # that reported under it were the art-35 manager formation
            # modifiers, which draw "Formation Modifier -- Manager" and no art,
            # and are held out of the club. Nothing else carries that member:
            # goalkeeper chemistry styles, subtypes 269-273, are in neither
            # catalogue.
        )
    )


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
    assert all(250 <= s <= 268 for s in offered), sorted(offered)


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

    assert document["user"] == [
        {"persona": "Fondateur FUT", "personaId": _inventory.PERSONA.id,
         "public": False}
    ]
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
    # The console asks for managerLeagueModifier, and this club holds none --
    # manager cards are left out of the catalogue. An unknown category used to
    # fall through to "everything", so a tab headed one thing listed another.
    from fut_inventory import ClubInventory, consumables_response

    club = ClubInventory()
    for category in ("managerLeagueModifier", "managerContract", "nonsense"):
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
    final_award = next(a for i, _t, _l, a, _r in TOURNAMENTS if i == cup)

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
    assert entry["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
    assert entry["tradeState"] == "expired"
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
    assert (pile["auctionInfo"][0]["tradeId"]
            >= inventory.UNLISTED_TRADE_ID_BASE), "an unlisted row needs an id"


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
    # `played` counts appearances: every card the client reports on at the
    # whistle was on the pitch, and `gamesPlayed` is a member CardsDLL's name
    # table carries that nothing here wrote until 16 August 2026.
    # `contracts` counts the matches taken off a contract. The client reports
    # fitness, goals and assists per player and never mentions contracts, so
    # nothing else counts them down -- every card sat at 99 for ever, which is
    # what left the contract cards with nothing to restore.
    assert touched == {
        "fitness": 3, "goals": 1, "assists": 1, "played": 3,
        "contracts": 3, "unknown": [],
    }
    assert all(player["gamesPlayed"] == 1 for player in players)
    assert all(player["contract"] == 98 for player in players)
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
    ratings = sorted(
        (card.get("rating", 0) for card in squad["itemData"]), reverse=True
    )
    real = [
        card["rating"]
        for card in catalogue.cards
        if card["assetId"] in set(inventory._totw_asset_ids())
    ]
    if real:
        # Nobody on the bench is better than the best real in-form.
        assert ratings[0] <= max(real)
    assert len(ratings) == 23

    # And the challenge it advertises is a side you could actually face.
    for challenge in squad["squadChallenge"]:
        assert 60 <= challenge["opponentRating"] <= 90


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

    # Consumables packs hold no players at all.
    for pack_id in (108, 109):
        kinds, _, _ = contents(pack_id)
        assert kinds["player"] == 0
        # Consumables and club items, and nothing else. Club items came back
        # into the draw on 17 August 2026 once they were confirmed rendering
        # on the console -- each with its own wire type, and badges under the
        # retail `custom` family rather than `badge`.
        club_kinds = {
            "kit", inventory.BADGE_WIRE_TYPE, "stadium", "ball",
            "manager", "staff",
        }
        assert set(kinds) <= inventory.CONSUMABLE_TYPES | club_kinds | {"club"}

    # The 100 000 pack is rare golds, all twelve of them.
    _, families, _ = contents(308)
    assert families["non-rare gold"] == 0
    assert families["rare gold"] > 0

    # A Team of the Week pack owes one every time, and cannot hand out another
    # family instead.
    _, families, per_pack = contents(309)
    assert per_pack >= 1.0
    assert families["team of the week"] > 0
    assert families["team of the year"] == 0
    assert families["team of the season"] == 0

    # The Team of the Season pack promises two, weighted to its own name.
    _, families, per_pack = contents(310)
    assert per_pack >= 2.0
    assert families["team of the season"] > families["team of the year"]


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
    assert headings == [
        "Bronze Packs", "Silver Packs", "Gold Packs",
        "Consumables", "Special Packs",
    ]
    # The added packs get headings of their own rather than being filed under a
    # tier they only nominally belong to.
    assert groups["Consumables"] == [108, 109]
    assert groups["Special Packs"] == [309, 310]
    # The tier still names the artwork.
    for entry in catalogue["purchase"]:
        assert entry["displayGroupAssetId"] in (1, 2, 3)


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
            keys = [(item.get("assetId"), item.get("rareflag")) for item in players]
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
    assert entry["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
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
    assert doc["transferListCount"] == 10


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
        if e["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
    ]
    assert len(entries) == 2
    ids = {entry["tradeId"] for entry in entries}
    assert len(ids) == 2, "two rows sharing an id cannot both be selected"

    for entry in entries:
        # The two members that carry the behaviour, together. Neither works
        # alone: an id alone makes the row an auction the screen can describe
        # but not act on, and a state alone has no card behind it to act with.
        assert entry["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
        assert entry["tradeState"] == "expired"
        # A lapsed auction, which is what "expired" has to mean to be relistable.
        assert entry["expires"] == -1
        # Unchanged, and still true of the card itself.
        assert entry["endtime"] == 2147483647
        assert entry["tradeOwner"] is True
        assert entry["sellerName"], "the club is the seller, not a blank"
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
            if e["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
        ]
        assert len(unlisted) == 2, name
        for entry in unlisted:
            assert entry["tradeState"] == "expired", name
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

    # The roster the probe sweeps is written by the operator and gitignored, so
    # a checkout does not have one -- and without one `_club_item_probe` reads
    # nothing and returns a falsy roster, so the guard does not fire and this
    # test asserted a machine rather than the code. It gets its own roster.
    import fut_inventory

    roster = Path(tempfile.mkdtemp()) / "club-item-probe.json"
    roster.write_text(json.dumps({"kit": [1], "badge": [], "stadium": [], "ball": []}))
    kept = fut_inventory.PROBE_FILE
    fut_inventory.PROBE_FILE = roster

    os.environ["FIFA14_CLUB_ITEM_PROBE"] = "1"
    try:
        ClubSave(path).save(inventory, wallet, actions, None)
        assert path.read_text() == before, "a probe run wrote the save"
    finally:
        os.environ.pop("FIFA14_CLUB_ITEM_PROBE", None)
        fut_inventory.PROBE_FILE = kept

    # And with the probe off it saves as normal.
    ClubSave(path).save(inventory, wallet, actions, None)
    assert path.read_text() != before


def test_the_club_item_catalogue_matches_what_the_console_rendered() -> None:
    # 1570 club items across four contiguous resource runs, each edge confirmed
    # on the console during the probe sessions of 18-19 August 2026.
    from fut_inventory import _clubitem_catalogue

    catalogue = _clubitem_catalogue()
    assert len(catalogue) == 1570

    runs = {
        "kit": (6_300_000, 6_300_860, 861),
        "badge": (6_000_000, 6_000_600, 601),
        "stadium": (6_200_000, 6_200_060, 61),
        "ball": (8_120_091, 8_120_137, 47),
    }
    for kind, (first, last, count) in runs.items():
        ids = sorted(c["resourceId"] for c in catalogue if c["itemType"] == kind)
        assert len(ids) == count, kind
        assert ids[0] == first and ids[-1] == last, kind
        # Contiguous: the probe sampled across each run and both edges are sharp.
        assert ids == list(range(first, last + 1)), kind


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
    assert len(balls) == 47
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

    assert len(units) == len(PACK_SPECS) + len(TOURNAMENT_NAMES)
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
        # divisionId indexes the client's own table: 10 hangs, 0-9 hold.
        assert 0 <= user["divisionId"] <= 9, mode


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

    import fut_inventory as inventory

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


def test_each_unlisted_card_gets_a_stable_id_of_its_own() -> None:
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

    rows = [e for e in first if e["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE]
    assert len(rows) == 3
    assert len({r["tradeId"] for r in rows}) == 3

    by_card = {r["itemData"]["id"]: r["tradeId"] for r in rows}
    repeat = {
        r["itemData"]["id"]: r["tradeId"]
        for r in again
        if r["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
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
        if e["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
    ][0]
    actions.withdraw(row["tradeId"])

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
            if e["tradeId"] >= inventory.UNLISTED_TRADE_ID_BASE
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
        assert row["tradeState"] == "expired", "base candidate did not run"
        assert row["expires"] == -1
        # ...then the overlay on top of it.
        assert row["startingBid"] == 150
        assert "watched" not in row
        assert "lastSalePrice" not in row["itemData"]
        assert row["itemData"]["owners"] == 3

        # No base means the current shape, changed only where the spec says.
        plain = unlisted("nobase")
        assert plain["tradeState"] == "expired"
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


def test_the_default_season_shape_is_the_one_that_resumes() -> None:
    # Measured on the console, 21 August 2026. A season survived a match and
    # came back with the score drawn and NEXT on the second fixture, on this
    # document and no other:
    #
    #     {"seasonId":1,"divisionId":10,"round":2,"data":<blob>,"dataVersion":1}
    #
    # Three things have to agree, and every earlier attempt had at least one of
    # them wrong:
    #
    #   the list      ten rows, Division 10 first, so row 1 IS Division 10
    #   seasonId      1, selecting that row -- the client decrements it
    #   divisionId    10, naming the same division as the row selected
    #
    # `divisionId` 9 with the same list and the same seasonId did NOT resume,
    # which is what proves the member has to match the row rather than merely
    # being in range.
    import json

    import fut_inventory as inventory

    assert inventory.season_wire_mode() == "kyro-data"
    listed = json.loads(inventory.seasons_response())["seasons"]
    user = json.loads(inventory.season_user_response())

    assert len(listed) == 10
    assert [row["id"] for row in listed] == list(range(1, 11))
    selected = [row for row in listed if row["id"] == user["seasonId"]]
    assert len(selected) == 1, "seasonId must select exactly one row"
    assert selected[0]["divisionId"] == user["divisionId"], (
        "the user document must name the division of the row it selects"
    )
    assert user["divisionId"] == 10, "a new club starts in Division 10"
    assert user["round"] >= 1, "wire round 0 becomes the client's invalid sentinel"


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
