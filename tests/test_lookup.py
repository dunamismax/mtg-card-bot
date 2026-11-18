from typing import Any, cast

import pytest

from mtg_card_bot.errors import ErrorType, MTGError, create_error
from mtg_card_bot.lookup import CardResolver, comma_split_requests
from mtg_card_bot.query import CardRequest, LookupMode
from mtg_card_bot.scryfall import Card, ScryfallClient, ScryfallError, SearchResult


def card(name: str, **extra: Any) -> Card:
    return Card({"object": "card", "id": f"{name}-id", "name": name, **extra})


def not_found(*, ambiguous: bool = False) -> MTGError:
    cause = ScryfallError(
        {"status": 404, "type": "ambiguous" if ambiguous else "", "details": "nope"}
    )
    return create_error(ErrorType.NOT_FOUND, "nope", cause)


class FakeClient:
    """Scryfall stand-in driven by simple dictionaries."""

    def __init__(
        self,
        *,
        fuzzy: dict[str, Card | MTGError] | None = None,
        searches: dict[str, list[Card]] | None = None,
        autocomplete: dict[str, list[str]] | None = None,
        collection: dict[str, Card] | None = None,
    ) -> None:
        self.fuzzy = fuzzy or {}
        self.searches = searches or {}
        self.autocompletes = autocomplete or {}
        self.collection = collection or {}
        self.calls: list[tuple[str, ...]] = []

    async def get_card_by_name(self, name: str, set_code: str | None = None) -> Card:
        self.calls.append(("fuzzy", name, set_code or ""))
        result = self.fuzzy.get(f"{name}|{set_code}" if set_code else name)
        if isinstance(result, Card):
            return result
        raise result or not_found()

    async def search_cards(
        self,
        query: str,
        order: str | None = None,
        direction: str | None = None,
        page: int | None = None,
        unique: str | None = None,
    ) -> SearchResult:
        self.calls.append(("search", query, order or ""))
        cards = self.searches.get(query)
        if not cards:
            raise not_found()
        result = SearchResult({"total_cards": len(cards)})
        result.data = cards
        return result

    async def search_card_first(
        self,
        query: str,
        order: str | None = None,
        direction: str | None = None,
        *,
        unique: str | None = None,
    ) -> Card:
        self.calls.append(("unique", unique or ""))
        return (await self.search_cards(query, order, direction)).data[0]

    async def autocomplete(self, partial: str) -> list[str]:
        self.calls.append(("autocomplete", partial))
        return self.autocompletes.get(partial, [])

    async def get_cards_by_names(self, names: list[str]) -> dict[str, Card]:
        self.calls.append(("collection", *names))
        return {
            name.lower(): self.collection[name.lower()]
            for name in names
            if name.lower() in self.collection
        }

    async def get_random_card(self, query: str = "") -> Card:
        self.calls.append(("random", query))
        return card("Random Card")

    async def get_card_by_set_number(self, set_code: str, number: str) -> Card:
        self.calls.append(("number", set_code, number))
        return card("Sol Ring", set=set_code, collector_number=number)


def resolver_for(client: FakeClient) -> CardResolver:
    return CardResolver(cast(ScryfallClient, client))


async def test_fuzzy_match_is_used_first() -> None:
    client = FakeClient(fuzzy={"ragav": card("Ragavan, Nimble Pilferer")})

    found, note = await resolver_for(client).resolve_name("ragav")

    assert found.name == "Ragavan, Nimble Pilferer"
    assert note is None


async def test_ambiguous_name_picks_the_most_played_card() -> None:
    client = FakeClient(
        fuzzy={"bolt": not_found(ambiguous=True)},
        searches={
            'name:"bolt"': [card("Lightning Bolt"), card("Boltwave"), card("Bolt Bend")]
        },
    )

    found, note = await resolver_for(client).resolve_name("bolt")

    assert found.name == "Lightning Bolt"
    assert note is not None
    assert "matches 3 cards" in note
    assert "Boltwave, Bolt Bend" in note
    assert ("search", 'name:"bolt"', "edhrec") in client.calls


async def test_chatty_input_finds_the_longest_matching_name_prefix() -> None:
    words = ["generous", "pup", "would", "go", "good", "in", "that"]
    client = FakeClient(
        searches={
            'name:"generous" name:"pup"': [card("Generous Pup")],
        }
    )

    found, note = await resolver_for(client).resolve_name(" ".join(words))

    assert found.name == "Generous Pup"
    assert note is not None and "Closest match" in note
    searches = [call for call in client.calls if call[0] == "search"]
    assert len(searches) <= 4  # full query plus a binary search


async def test_unknown_name_raises_with_suggestions() -> None:
    client = FakeClient(autocomplete={"conjurers": ["Conjurer's Closet"]})

    with pytest.raises(MTGError) as exc_info:
        await resolver_for(client).resolve_name("conjurers chest")

    assert exc_info.value.error_type is ErrorType.NOT_FOUND
    assert exc_info.value.suggestions == ["Conjurer's Closet"]


async def test_missing_set_printing_falls_back_to_default_printing() -> None:
    client = FakeClient(fuzzy={"sol ring": card("Sol Ring")})

    found, note = await resolver_for(client).resolve_name("sol ring", "zzz")

    assert found.name == "Sol Ring"
    assert note is not None and "No ZZZ printing" in note


async def test_filtered_query_with_typo_reapplies_filters_to_matched_card() -> None:
    bolt = card("Lightning Bolt", oracle_id="bolt-oracle")
    bolt_2xm = card("Lightning Bolt", set="2xm")
    client = FakeClient(
        fuzzy={"lightnin bolt": bolt},
        searches={"oracleid:bolt-oracle e:2xm": [bolt_2xm]},
    )

    resolution = await resolver_for(client).resolve(CardRequest("lightnin bolt e:2xm"))

    assert resolution.card is bolt_2xm
    assert resolution.note is not None and "Closest match" in resolution.note


async def test_filtered_query_falls_back_to_default_printing() -> None:
    bolt = card("Lightning Bolt", oracle_id="bolt-oracle")
    client = FakeClient(fuzzy={"lightning bolt": bolt})

    resolution = await resolver_for(client).resolve(
        CardRequest("lightning bolt e:zzz order:usd")
    )

    assert resolution.card is bolt
    assert resolution.note is not None and "default printing" in resolution.note
    assert ("search", "lightning bolt e:zzz", "usd") in client.calls


async def test_filter_only_query_failure_is_reported() -> None:
    resolution = await resolver_for(FakeClient()).resolve(CardRequest("t:goblin mv=99"))

    assert not resolution.ok
    assert resolution.not_found


async def test_random_and_collector_number_requests() -> None:
    client = FakeClient()
    resolver = resolver_for(client)

    random_result = await resolver.resolve(CardRequest("t:dragon", random=True))
    numbered = await resolver.resolve(
        CardRequest("", set_code="lea", collector_number="270")
    )

    assert random_result.card is not None
    assert numbered.card is not None and numbered.card.collector_number == "270"
    assert ("random", "t:dragon") in client.calls
    assert ("number", "lea", "270") in client.calls


async def test_resolve_many_batches_exact_names() -> None:
    bolt = card("Lightning Bolt")
    counterspell = card("Counterspell")
    client = FakeClient(
        collection={"lightning bolt": bolt},
        fuzzy={"counterspel": counterspell},
    )

    results = await resolver_for(client).resolve_many(
        [
            CardRequest("lightning bolt"),
            CardRequest("counterspel"),
            CardRequest("t:goblin", random=True),
        ]
    )

    assert [r.card.name if r.card else None for r in results] == [
        "Lightning Bolt",
        "Counterspell",
        "Random Card",
    ]
    assert ("collection", "lightning bolt", "counterspel") in client.calls
    assert ("fuzzy", "lightning bolt", "") not in client.calls


async def test_cheapest_printing_uses_oracle_id() -> None:
    cheap = card("Sol Ring", set="c21")
    client = FakeClient(searches={"oracleid:sol usd>0": [cheap]})

    result = await resolver_for(client).cheapest_printing(
        card("Sol Ring", oracle_id="sol")
    )

    assert result is cheap
    assert ("search", "oracleid:sol usd>0", "usd") in client.calls


def test_comma_split_only_for_plain_card_lookups() -> None:
    assert comma_split_requests(CardRequest("Ore Gorger,Skyfire Kirin")) == [
        CardRequest("Ore Gorger"),
        CardRequest("Skyfire Kirin"),
    ]
    assert comma_split_requests(CardRequest("Sol Ring")) == []
    assert comma_split_requests(CardRequest("a, b", LookupMode.PRICES)) == []
    assert comma_split_requests(CardRequest("a, b e:lea")) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ragav", "Ragavan, Nimble Pilferer"),
        ("Ragavan", "Ragavan, Nimble Pilferer"),
        ("treasure", "Treasure"),
    ],
)
async def test_tokens_give_way_to_real_cards(query: str, expected: str) -> None:
    client = FakeClient(
        fuzzy={
            "ragav": card("Ragavan", layout="token"),
            "Ragavan": card("Ragavan", layout="token"),
            "treasure": card("Treasure", layout="token"),
        },
        searches={
            'name:"ragav"': [card("Ragavan, Nimble Pilferer")],
            'name:"Ragavan"': [
                card("Rashmi and Ragavan"),
                card("Ragavan, Nimble Pilferer"),
            ],
            'name:"treasure"': [card("Treasure Cruise"), card("Treasure Map")],
        },
    )

    found, note = await resolver_for(client).resolve_name(query)

    assert found.name == expected
    assert note is None


async def test_price_orders_consider_every_printing() -> None:
    client = FakeClient(searches={"cultivate": [card("Cultivate", set="slz")]})

    await resolver_for(client).resolve(CardRequest("cultivate order:usd dir:desc"))
    await resolver_for(client).resolve(CardRequest("cultivate order:edhrec"))

    assert [call for call in client.calls if call[0] == "unique"] == [
        ("unique", "prints"),
        ("unique", ""),
    ]


async def test_suggestions_skip_art_series_duplicates() -> None:
    client = FakeClient(
        autocomplete={
            "conjurers chest": [
                "Conjurer's Closet // Conjurer's Closet",
                "Conjurer's Closet",
                "Fire // Ice",
            ]
        }
    )

    assert await resolver_for(client).suggest("conjurers chest") == [
        "Conjurer's Closet",
        "Fire // Ice",
    ]
