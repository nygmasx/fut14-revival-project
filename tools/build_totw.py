#!/usr/bin/env python3
"""Build the Team of the Week catalogue from Impulsum14's extract.

    python3 tools/build_totw.py

Reads three TSVs out of `runtime/impulsum/`, taken from
`MarvelcoCode/Impulsum14`'s `FUTDB/`:

    totw_teams.tsv       week, order, starter, pos, baseId, rating
    totw_formations.tsv  week, formation
    totw_source.tsv      the same teams with the players' names, for reading

Forty-nine weeks, eighteen slots each, 882 rows. **Every one of those baseIds
resolves against `server/fifa14_cards.json`** -- checked before any of this was
written, because a squad built on ids this catalogue does not have is a screen
full of blanks.

What this replaces was a stub: two squads of six asset ids scraped off wefut,
with the rest of the eleven padded from the catalogue's own rares. That padding
decided how strong the opponent was -- `opponentRating` is computed from the
first eleven -- so a Team of the Week whose real members top out at 85 was
being played against a bench of 98s.

**Release dates.** TOTW 1 went out on 18 September 2013 and a new one followed
every Wednesday, so week N is 18 September 2013 plus 7(N-1) days. That is the
player's own record of the season and it is not in either extract.

**The in-form card is not the base card.** `totw_teams.tsv`'s `pos` column is
the **formation slot** -- `RCB`, `LCB`, `RCM`, `RW` -- and not a position at
all. Serving it as `preferredPosition` is why two centre-backs drew blank on
the console: `RCB` is not a position the client knows.

**`server/fifa14_cards.json` already holds the in-forms**, 897 of them under
the rarity "Team of the Week", and the right one is found by matching the asset
id **and the rating**, not the asset id alone. Totti is asset 1238 twice: 82
LW for his base card and 83 CF for his TOTW 4 card. Keying on the asset alone
took whichever came first, which is the whole bug.

That catalogue covers 831 of the 882 slots and carries the club, nation and
league besides. `specials.tsv` from Impulsum covers all 882 and is used for
the rest, and for the art band, which this server's own cards do not record.

Where both hold a slot they agree on the stats 764 times out of 831 and
disagree on 19 positions. **This server's own catalogue wins**: it is the data
every other screen here is already served from. The disagreements are listed
by `--report` so a card that looks wrong on the console can be checked against
the other source rather than argued about.

The band is 8, 9, 10 or 11 across the set, not the flat 50 Impulsum falls back
to for a player its specials table does not hold. `rareflag` is 3 on every one.
"""

from __future__ import annotations

import csv
import json
import unicodedata
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "runtime" / "impulsum"
# pac, sho, pas, dri, def, phy -- the order this server's own cards carry.
FACE_STATS = ("pac", "sho", "pas", "dri", "def", "phy")
CARDS = REPO / "server" / "fifa14_cards.json"
OUT = REPO / "server" / "fifa14_totw.json"

# TOTW 1, and one a week every Wednesday after it.
FIRST_RELEASE = date(2013, 9, 18)

# The in-form band. `resourceId = assetId + IN_FORM_BAND * 0x1000000`, and the
# card carries `rareflag` 3 so the client draws it as an in-form.
IN_FORM_BAND = 50
IN_FORM_RARE = 3

TOTW_RARITY = "Team of the Week"


def _fold(text: str) -> str:
    """A name with its accents and case removed, for matching."""
    raw = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in raw if not unicodedata.combining(c)).lower().strip()


SOURCE_NOTE = (
    "MarvelcoCode/Impulsum14 FUTDB/totw_teams.tsv, totw_formations.tsv and "
    "totw_source.tsv; release dates from the FIFA 14 season calendar"
)


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            row
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("week") and not row["week"].startswith("#")
        ]


def main() -> int:
    teams = _rows(SOURCE / "totw_teams.tsv")
    formations = {
        int(row["week"]): row["formation"].strip()
        for row in _rows(SOURCE / "totw_formations.tsv")
    }
    # Names, for reading the file and for nothing on the wire.
    names = {
        (int(r["week"]), int(r["idx"])): r.get("name", "").strip()
        for r in _rows(SOURCE / "totw_source.tsv")
    }

    # The in-form cards themselves, keyed by the base card and the rating --
    # the same pair `totw_teams.tsv` names a slot by.
    specials: dict[tuple[int, int], dict] = {}
    with (SOURCE / "specials.tsv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("set") != "totw" or not row.get("baseId"):
                continue
            try:
                specials[(int(row["baseId"]), int(row["rating"]))] = row
            except (TypeError, ValueError):
                continue

    catalogue = json.loads(CARDS.read_text())
    # The **raw** file, not `CardCatalogue`. That class drops any card missing
    # a club, nation or league -- 196 of the 15,766 -- because such a card
    # renders with placeholder text where its badge should be, and offering it
    # on the market is worse than not offering it. A Team of the Week is a
    # fixed side, not something offered, and dropping a member of it left five
    # weeks short: week 10 fielded sixteen with no goalkeeper, because Andriy
    # Pyatov's card is one of the 196.
    cards = (
        catalogue
        if isinstance(catalogue, list)
        else next(v for v in catalogue.values() if isinstance(v, list))
    )
    known = {int(card.get("assetId") or 0) for card in cards}
    # The in-form, by asset **and rating**. Totti is asset 1238 twice -- 82 LW
    # for his base card and 83 CF for his TOTW 4 card -- so the rating is what
    # tells them apart.
    by_pair: dict[tuple[int, int], dict] = {}
    for card in cards:
        key = (int(card.get("assetId") or 0), int(card.get("rating") or 0))
        by_pair.setdefault(key, card)
    in_forms = [c for c in cards if c.get("rarity") == TOTW_RARITY]


    def resolve(asset: int, rating: int, who: str) -> dict | None:
        """This server's own in-form card for one slot.

        `totw_teams.tsv`'s `baseId` is not always this catalogue's asset id --
        41 of the 882 miss, and the player checked thirteen of them by hand
        against `fifa14_cards.json`: Cuadrado is 193082 here where the extract
        says 188612, Seedorf 1256 against 1001, Coutinho 189242 against 213439.
        So the asset is tried first and the **name and rating** settle the rest,
        against the in-forms only.
        """
        card = by_pair.get((asset, rating))
        if card is not None:
            return card
        target = _fold(who)
        if not target:
            return None
        pool = [c for c in in_forms if int(c.get("rating") or 0) == rating]
        exact = [c for c in pool if _fold(c.get("name")) == target]
        if len(exact) == 1:
            return exact[0]
        # Same words in a different order -- the extract writes "Lee Myung Joo"
        # where this catalogue has "Myung Joo Lee".
        words = set(target.split())
        same = [c for c in pool if set(_fold(c.get("name")).split()) == words]
        if len(same) == 1:
            return same[0]
        # One shared surname, and only where it picks out exactly one card.
        near = [
            c for c in pool
            if words & set(_fold(c.get("name")).split())
            or target in _fold(c.get("name"))
        ]
        return near[0] if len(near) == 1 else None

    weeks: dict[int, dict] = {}
    missing = 0
    disagreed: dict[str, list] = {"position": []}
    from_catalogue = from_specials = 0
    corrected: list = []
    unresolved: list = []
    for row in teams:
        week = int(row["week"])
        asset = int(row["baseId"])
        order = int(row["order"])
        who = names.get((week, order), "")
        rating = int(row["rating"])
        inform = resolve(asset, rating, who)
        if inform is None and asset not in known:
            missing += 1
            unresolved.append((week, order, asset, rating, who))
            continue
        entry = weeks.setdefault(
            week,
            {
                "week": week,
                "name": f"TOTW {week}",
                "formation": formations.get(week, "f442"),
                "released": (
                    FIRST_RELEASE + timedelta(days=7 * (week - 1))
                ).isoformat(),
                "slots": [],
            },
        )
        order = int(row["order"])
        special = specials.get((asset, rating))
        band = int(special["band"]) if special else IN_FORM_BAND
        if inform is not None:
            from_catalogue += 1
        elif special is not None:
            from_specials += 1
        # The catalogue's own asset id wins where the two differ: the player
        # checked thirteen of them by hand against `fifa14_cards.json`.
        if inform is not None and int(inform.get("assetId") or 0) != asset:
            corrected.append((week, who, asset, int(inform["assetId"])))
            asset = int(inform["assetId"])
        position = (inform or {}).get("position") or (
            special["pos"].strip() if special else row["pos"].strip()
        )
        attributes = list((inform or {}).get("attributes") or []) or (
            [int(special[key]) for key in FACE_STATS] if special else []
        )
        if inform is not None and special is not None:
            if (inform.get("position") or "") != special["pos"].strip():
                disagreed["position"].append(
                    (week, names.get((week, int(row["order"])), ""),
                     inform.get("position"), special["pos"].strip())
                )
        entry["slots"].append(
            {
                "order": order,
                # Everything the card needs, resolved here rather than at
                # request time -- the running catalogue drops 196 cards that a
                # Team of the Week is entitled to field.
                "cardId": (inform or {}).get("cardId"),
                "clubId": int((inform or {}).get("clubId") or 0),
                "nationId": int((inform or {}).get("nationId") or 0),
                "leagueId": int((inform or {}).get("leagueId") or 0),
                "starter": int(row["starter"]) == 1,
                # The card's own position, from the specials table. The `pos`
                # column beside it in totw_teams.tsv is the formation slot --
                # RCB, LCB, RCM -- which is not a position the client knows.
                "position": position,
                "slot": row["pos"].strip(),
                "assetId": asset,
                # The in-form's own rating, not the base card's.
                "rating": rating,
                "resourceId": asset + band * 0x1000000,
                "band": band,
                "rareflag": IN_FORM_RARE,
                # The in-form's own face stats.
                "attributes": attributes,
                "name": names.get((week, order), ""),
            }
        )

    ordered = []
    for week in sorted(weeks):
        entry = weeks[week]
        entry["slots"].sort(key=lambda slot: slot["order"])

        # The eleven the rating is read from.
        eleven = [s for s in entry["slots"] if s["starter"]][:11]
        entry["rating"] = (
            round(sum(s["rating"] for s in eleven) / len(eleven)) if eleven else 0
        )
        ordered.append(entry)

    OUT.write_text(
        json.dumps(
            {"source": SOURCE_NOTE, "squads": ordered}, separators=(",", ":")
        )
    )
    print(f"  {len(ordered)} weeks, {sum(len(e['slots']) for e in ordered)} slots")
    print(f"  unresolved baseIds skipped: {missing}")
    print(f"  in-forms from this server's catalogue: {from_catalogue}")
    print(f"  in-forms from Impulsum's specials.tsv: {from_specials}")
    print(f"  asset ids corrected against this catalogue: {len(corrected)}")
    for week, who, was, now in corrected[:10]:
        print(f"     week {week:>2}  {who:<24} {was} -> {now}")
    if unresolved:
        print(f"  UNRESOLVED slots: {len(unresolved)}")
        for u in unresolved:
            print(f"     week {u[0]:>2} idx {u[1]:>2} asset {u[2]} rate {u[3]} {u[4]}")
    if disagreed["position"]:
        print(f"  positions the two sources disagree on: "
              f"{len(disagreed['position'])} (ours wins)")
        for week, who, ours, theirs in disagreed["position"][:8]:
            print(f"     week {week:>2}  {who:<24} ours={ours:<5} specials={theirs}")
    first, last = ordered[0], ordered[-1]
    print(f"  week {first['week']} ({first['released']}) {first['formation']} "
          f"rating {first['rating']}")
    print(f"  week {last['week']} ({last['released']}) {last['formation']} "
          f"rating {last['rating']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
