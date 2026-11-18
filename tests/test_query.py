import pytest

from mtg_card_bot.query import (
    MAX_CARDS_PER_MESSAGE,
    CardRequest,
    LookupMode,
    extract_bracket_requests,
    has_filter_parameters,
    is_filter_token,
    name_search_query,
    parse_card_request,
    parse_query,
    split_multi_query,
    tokenize,
)


def test_tokenize_keeps_quoted_phrases_together() -> None:
    assert tokenize('t:creature o:"draw a card" !"Lightning Bolt"') == [
        "t:creature",
        'o:"draw a card"',
        '!"Lightning Bolt"',
    ]


@pytest.mark.parametrize(
    "token",
    [
        "e:mh3",
        "SET:lea",
        "-is:reprint",
        "mana>={5}",
        "usd<1",
        "pow>=4",
        "mv=3",
        "(t:goblin",
        '!"Sol Ring"',
        'o:"draw a card"',
        "oracleid:abc",
    ],
)
def test_is_filter_token_recognises_scryfall_syntax(token: str) -> None:
    assert is_filter_token(token)


@pytest.mark.parametrize(
    "token", ["Summon:", "Protection:", "bolt", "Urza's", "100,000", "Ach!", "x:y"]
)
def test_is_filter_token_ignores_name_words(token: str) -> None:
    assert not is_filter_token(token)


def test_parse_query_splits_name_filters_and_sort() -> None:
    parsed = parse_query("cultivate order:usd, direction:DESC e:m10 usd<1")

    assert parsed.name == "cultivate"
    assert parsed.filters == ("e:m10", "usd<1")
    assert parsed.order == "usd"
    assert parsed.direction == "desc"
    assert parsed.has_filters
    assert parsed.search_text == "cultivate e:m10 usd<1"


def test_card_names_with_colons_are_not_filters() -> None:
    assert not has_filter_parameters("Summon: Leviathan")
    assert not has_filter_parameters("Circle of Protection: Red")
    assert has_filter_parameters("lightning bolt order:usd")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Lightning Bolt", CardRequest("Lightning Bolt")),
        ("!Lightning Bolt", CardRequest("Lightning Bolt", LookupMode.IMAGE)),
        ("$ Sol Ring", CardRequest("Sol Ring", LookupMode.PRICES)),
        ("?Counterspell", CardRequest("Counterspell", LookupMode.RULINGS)),
        ("#Sol Ring", CardRequest("Sol Ring", LookupMode.LEGALITY)),
        ("Sol Ring|LEA", CardRequest("Sol Ring", set_code="lea")),
        (
            "Sol Ring | lea | 270",
            CardRequest("Sol Ring", set_code="lea", collector_number="270"),
        ),
        ("Sol Ring|not a set!", CardRequest("Sol Ring")),
        ("random t:dragon", CardRequest("t:dragon", random=True)),
        ("random", CardRequest("", random=True)),
    ],
)
def test_parse_card_request_with_modifiers(text: str, expected: CardRequest) -> None:
    assert parse_card_request(text, allow_modifiers=True) == expected


def test_parse_card_request_without_modifiers_keeps_exact_name_syntax() -> None:
    request = parse_card_request('!"Fire // Ice"')
    assert request is not None
    assert request == CardRequest('!"Fire // Ice"')
    assert request.parsed.has_filters


def test_parse_card_request_rejects_empty_text() -> None:
    assert parse_card_request("   ") is None
    assert parse_card_request("!", allow_modifiers=True) is None


def test_extract_bracket_requests_dedupes_and_skips_code() -> None:
    content = (
        "Compare [[Bolt]] with [[$Counterspell]] and [[bolt]] "
        "but not `[[Ignored]]` or ```\n[[Also Ignored]]\n```"
    )

    assert extract_bracket_requests(content) == [
        CardRequest("Bolt"),
        CardRequest("Counterspell", LookupMode.PRICES),
        CardRequest("bolt"),
    ]


def test_request_label_mentions_printing() -> None:
    request = CardRequest("Sol Ring", set_code="lea", collector_number="270")
    assert request.label == "Sol Ring | LEA | 270"
    assert CardRequest("t:dragon", random=True).label == "random t:dragon"


def test_split_multi_query_drops_empty_parts() -> None:
    assert split_multi_query("bolt; ; counterspell;") == ["bolt", "counterspell"]


def test_name_search_query_quotes_every_word() -> None:
    assert name_search_query(["fire", "or", 'i"ce', ""]) == (
        'name:"fire" name:"or" name:"ice"'
    )


def test_max_cards_per_message_matches_discord_embed_limit() -> None:
    assert MAX_CARDS_PER_MESSAGE == 10
