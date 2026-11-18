import asyncio
import itertools

import httpx
import pytest

from mtg_card_bot import errors
from mtg_card_bot.scryfall import Card, ScryfallClient, is_ambiguous_error


async def _make_mock_client(handler):
    """Create a ScryfallClient with a mock transport and zero retry backoff."""
    client = ScryfallClient()
    client.RETRY_BACKOFF = 0.0  # Eliminate sleep in tests
    client.strict_interval = 0.0
    client.default_interval = 0.0
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    return client


async def test_get_card_by_name_uses_fuzzy_named_endpoint() -> None:
    captured_url: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_url["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "object": "card",
                "id": "card-id",
                "name": "Lightning Bolt",
                "scryfall_uri": "https://scryfall.com/card/card-id",
            },
        )

    client = ScryfallClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        card = await client.get_card_by_name("Lightning Bolt")
    finally:
        await client.close()

    assert card.name == "Lightning Bolt"
    assert captured_url["url"] == (
        "https://api.scryfall.com/cards/named?fuzzy=Lightning%20Bolt"
    )


async def test_get_card_by_name_maps_not_found_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "object": "error",
                "status": 404,
                "code": "not_found",
                "details": "No cards found matching that query.",
                "type": "ambiguous",
            },
        )

    client = ScryfallClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        with pytest.raises(errors.MTGError) as exc_info:
            await client.get_card_by_name("Missing Card")
    finally:
        await client.close()

    assert exc_info.value.error_type is errors.ErrorType.NOT_FOUND
    assert exc_info.value.message == "No cards found matching that query."


async def test_request_retries_on_503_then_succeeds() -> None:
    """Verify that a 503 is retried and eventually succeeds."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            json={
                "object": "card",
                "id": "random-id",
                "name": "Black Lotus",
            },
        )

    client = await _make_mock_client(handler)

    try:
        card = await client.get_random_card()
    finally:
        await client.close()

    assert card.name == "Black Lotus"
    assert call_count == 2


async def test_request_exhausts_retries_on_persistent_503() -> None:
    """Verify that persistent 503s raise after all retries are exhausted."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = await _make_mock_client(handler)

    try:
        with pytest.raises(errors.MTGError) as exc_info:
            await client.get_random_card()
    finally:
        await client.close()

    assert exc_info.value.error_type is errors.ErrorType.API
    assert "temporarily unavailable" in exc_info.value.message.lower()


async def test_request_retries_on_network_error() -> None:
    """Verify that network errors are retried."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(
            200,
            json={
                "object": "card",
                "id": "card-id",
                "name": "Sol Ring",
            },
        )

    client = await _make_mock_client(handler)

    try:
        card = await client.get_random_card()
    finally:
        await client.close()

    assert card.name == "Sol Ring"
    assert call_count == 2


async def test_request_does_not_retry_404() -> None:
    """404 is a client error and should not be retried."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            404,
            json={
                "object": "error",
                "status": 404,
                "code": "not_found",
                "details": "Card not found",
            },
        )

    client = await _make_mock_client(handler)

    try:
        with pytest.raises(errors.MTGError) as exc_info:
            await client.get_card_by_name("doesntexist")
    finally:
        await client.close()

    assert exc_info.value.error_type is errors.ErrorType.NOT_FOUND
    assert call_count == 1  # No retries for 404


def _card_json(name: str, **extra: object) -> dict[str, object]:
    return {"object": "card", "id": f"{name}-id", "name": name, **extra}


async def test_strict_endpoints_are_spaced_to_scryfall_limits() -> None:
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import time

        times.append(time.monotonic())
        return httpx.Response(200, json=_card_json("Opt"))

    client = await _make_mock_client(handler)
    client.strict_interval = 0.05
    client.default_interval = 0.0
    try:
        await asyncio.gather(*(client.get_random_card() for _ in range(3)))
    finally:
        await client.close()

    gaps = [later - earlier for earlier, later in itertools.pairwise(times)]
    assert len(times) == 3
    assert all(gap >= 0.045 for gap in gaps)


async def test_429_pauses_requests_instead_of_retrying() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "60"})

    client = await _make_mock_client(handler)
    try:
        with pytest.raises(errors.MTGError) as first:
            await client.get_card_by_name("Opt")
        with pytest.raises(errors.MTGError) as second:
            await client.get_card_by_name("Shock")
    finally:
        await client.close()

    assert first.value.error_type is errors.ErrorType.RATE_LIMIT
    assert second.value.error_type is errors.ErrorType.RATE_LIMIT
    assert call_count == 1  # the lockout stops the second request locally


async def test_named_lookups_are_cached_but_random_cards_are_not() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=_card_json("Opt"))

    client = await _make_mock_client(handler)
    try:
        await client.get_card_by_name("Opt")
        await client.get_card_by_name("Opt")
        await client.get_random_card()
        await client.get_random_card()
    finally:
        await client.close()

    assert paths == ["/cards/named", "/cards/random", "/cards/random"]


async def test_fuzzy_lookup_passes_set_code() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_card_json("Sol Ring"))

    client = await _make_mock_client(handler)
    try:
        await client.get_card_by_name("sol ring", "lea")
        await client.get_card_by_set_number("LEA", "270")
    finally:
        await client.close()

    assert urls == [
        "https://api.scryfall.com/cards/named?fuzzy=sol%20ring&set=lea",
        "https://api.scryfall.com/cards/lea/270",
    ]


async def test_collection_lookup_matches_full_and_face_names() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "not_found": [{"name": "nope"}],
                "data": [
                    _card_json("Lightning Bolt"),
                    _card_json(
                        "Delver of Secrets // Insectile Aberration",
                        card_faces=[
                            {"name": "Delver of Secrets"},
                            {"name": "Insectile Aberration"},
                        ],
                    ),
                ],
            },
        )

    client = await _make_mock_client(handler)
    try:
        found = await client.get_cards_by_names(
            ["lightning  BOLT", "Delver of Secrets", "nope"]
        )
        # Second call is answered from the cache
        again = await client.get_cards_by_names(["Lightning Bolt"])
    finally:
        await client.close()

    assert len(bodies) == 1
    assert found["lightning bolt"].name == "Lightning Bolt"
    assert found["delver of secrets"].name.startswith("Delver of Secrets")
    assert "nope" not in found
    assert again["lightning bolt"].name == "Lightning Bolt"


async def test_ambiguous_fuzzy_errors_are_detectable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "object": "error",
                "status": 404,
                "code": "not_found",
                "type": "ambiguous",
                "details": "Too many cards match ambiguous name “bolt”.",
            },
        )

    client = await _make_mock_client(handler)
    try:
        with pytest.raises(errors.MTGError) as exc_info:
            await client.get_card_by_name("bolt")
    finally:
        await client.close()

    assert is_ambiguous_error(exc_info.value)


async def test_autocomplete_skips_short_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "catalog", "data": ["Opt"]})

    client = await _make_mock_client(handler)
    try:
        assert await client.autocomplete("o") == []
        assert await client.autocomplete("op") == ["Opt"]
    finally:
        await client.close()


def test_card_price_summary_and_display() -> None:
    card = Card(
        {
            "object": "card",
            "name": "Opt",
            "prices": {"usd": "0.1", "usd_foil": "0.5", "eur": None, "tix": "0.02"},
        }
    )
    assert card.get_price_display() == "$0.10"
    assert card.get_price_summary() == "$0.10 · $0.50 foil · 0.02 tix"

    foil_only = Card({"object": "card", "name": "X", "prices": {"usd_foil": "2"}})
    assert foil_only.get_price_display() == "$2.00 (foil)"
    assert Card({"object": "card", "name": "Y"}).get_price_display() == ""


def test_card_legality_groups() -> None:
    card = Card(
        {
            "object": "card",
            "name": "Sol Ring",
            "legalities": {
                "commander": "legal",
                "vintage": "restricted",
                "legacy": "banned",
                "modern": "not_legal",
            },
        }
    )
    groups = card.get_legality_groups()
    assert groups["legal"] == ["Commander"]
    assert groups["restricted"] == ["Vintage"]
    assert groups["banned"] == ["Legacy"]
    assert card.get_format_legalities() == "Commander"


def test_double_faced_card_images_and_rules_text() -> None:
    card = Card(
        {
            "object": "card",
            "name": "Delver of Secrets // Insectile Aberration",
            "set_name": "Innistrad",
            "set": "isd",
            "collector_number": "51",
            "card_faces": [
                {
                    "name": "Delver of Secrets",
                    "mana_cost": "{U}",
                    "type_line": "Creature — Human Wizard",
                    "oracle_text": "Transform it.",
                    "power": "1",
                    "toughness": "1",
                    "image_uris": {"normal": "front.jpg", "art_crop": "art.jpg"},
                },
                {
                    "name": "Insectile Aberration",
                    "type_line": "Creature — Human Insect",
                    "oracle_text": "Flying",
                    "power": "3",
                    "toughness": "2",
                    "image_uris": {"normal": "back.jpg"},
                },
            ],
        }
    )

    assert card.get_face_image_urls(("normal",)) == ["front.jpg", "back.jpg"]
    assert card.get_best_image_url() == "front.jpg"
    assert card.get_art_crop_url() == "art.jpg"
    assert card.name_keys() == {
        "delver of secrets // insectile aberration",
        "delver of secrets",
        "insectile aberration",
    }
    assert card.get_printing_label() == "Innistrad (ISD) #51"
    rules = card.get_rules_text()
    assert "**Delver of Secrets {U}**" in rules
    assert "**3/2**" in rules
