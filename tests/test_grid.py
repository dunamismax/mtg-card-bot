import io
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest
from PIL import Image

from mtg_card_bot.grid import (
    CARD_HEIGHT,
    CARD_WIDTH,
    IMAGE_ACCEPT,
    PADDING,
    calculate_grid_layout,
    compose_card_faces,
    compose_card_grid,
)
from mtg_card_bot.scryfall import Card, ScryfallClient

AsyncClient = httpx.AsyncClient


def _make_test_card_image() -> bytes:
    """Create a small test image that mimics a card."""
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_fake_card(**overrides: Any) -> Card:
    """Create a minimal mock Card for grid tests."""
    card = MagicMock()
    card.get_display_name.return_value = overrides.get("name", "Test Card")
    card.get_best_image_url.return_value = overrides.get(
        "image_url", "https://img.example/card.png"
    )
    return cast(Card, card)


def _mock_httpx_client(
    handler: Callable[[httpx.Request], httpx.Response],
):
    """Patch the grid client while preserving its production configuration."""

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return AsyncClient(*args, **kwargs)

    return patch("mtg_card_bot.grid.httpx.AsyncClient", side_effect=factory)


class TestGridLayout:
    def test_two_cards_side_by_side(self) -> None:
        assert calculate_grid_layout(2) == (2, 1)

    def test_three_cards_single_row(self) -> None:
        assert calculate_grid_layout(3) == (3, 1)

    def test_four_cards_two_by_two(self) -> None:
        assert calculate_grid_layout(4) == (2, 2)

    def test_six_cards_three_by_two(self) -> None:
        assert calculate_grid_layout(6) == (3, 2)

    def test_nine_cards_three_by_three(self) -> None:
        assert calculate_grid_layout(9) == (3, 3)

    def test_ten_cards_five_by_two(self) -> None:
        assert calculate_grid_layout(10) == (5, 2)

    @pytest.mark.parametrize("count", range(1, 11))
    def test_layout_fits_all_cards(self, count: int) -> None:
        cols, rows = calculate_grid_layout(count)
        assert cols * rows >= count


class TestGridComposition:
    async def test_compose_produces_valid_png(self) -> None:
        test_image_bytes = _make_test_card_image()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["User-Agent"] == ScryfallClient.USER_AGENT
            assert request.headers["Accept"] == IMAGE_ACCEPT
            return httpx.Response(200, content=test_image_bytes)

        cards = [_make_fake_card(name=f"Card {i}") for i in range(3)]

        with _mock_httpx_client(handler):
            result = await compose_card_grid(cards)

        # Verify it's a valid JPEG
        img = Image.open(result)
        assert img.format == "JPEG"

        # Verify dimensions: 3 columns, 1 row
        expected_width = 3 * CARD_WIDTH + 4 * PADDING
        expected_height = 1 * CARD_HEIGHT + 2 * PADDING
        assert img.size == (expected_width, expected_height)

    async def test_compose_four_cards_is_two_by_two(self) -> None:
        test_image_bytes = _make_test_card_image()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=test_image_bytes)

        cards = [_make_fake_card(name=f"Card {i}") for i in range(4)]

        with _mock_httpx_client(handler):
            result = await compose_card_grid(cards)

        img = Image.open(result)
        expected_width = 2 * CARD_WIDTH + 3 * PADDING
        expected_height = 2 * CARD_HEIGHT + 3 * PADDING
        assert img.size == (expected_width, expected_height)

    async def test_compose_handles_download_failure(self) -> None:
        test_image_bytes = _make_test_card_image()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return httpx.Response(500, text="Server Error")
            return httpx.Response(200, content=test_image_bytes)

        cards = [_make_fake_card(name=f"Card {i}") for i in range(3)]

        with _mock_httpx_client(handler):
            result = await compose_card_grid(cards)

        # Should still produce a valid image with placeholder for failed card
        img = Image.open(result)
        assert img.format == "JPEG"
        assert img.size[0] > 0
        assert img.size[1] > 0

    async def test_compose_raises_when_all_downloads_fail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="Rejected generic User-Agent")

        cards = [_make_fake_card(name=f"Card {i}") for i in range(2)]

        with (
            _mock_httpx_client(handler),
            pytest.raises(RuntimeError, match="All card image downloads failed"),
        ):
            await compose_card_grid(cards)


async def test_compose_card_faces_places_both_faces_side_by_side() -> None:
    test_image_bytes = _make_test_card_image()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=test_image_bytes)

    card = Card(
        {
            "object": "card",
            "name": "Front // Back",
            "card_faces": [
                {
                    "name": "Front",
                    "image_uris": {"normal": "https://img.example/f.jpg"},
                },
                {"name": "Back", "image_uris": {"normal": "https://img.example/b.jpg"}},
            ],
        }
    )

    with _mock_httpx_client(handler):
        result = await compose_card_faces(card)

    img = Image.open(result)
    assert img.size == (2 * CARD_WIDTH + 3 * PADDING, CARD_HEIGHT + 2 * PADDING)
    assert sorted(requested) == [
        "https://img.example/b.jpg",
        "https://img.example/f.jpg",
    ]


async def test_compose_card_faces_requires_two_faces() -> None:
    card = Card({"object": "card", "name": "Opt", "image_uris": {"normal": "x"}})
    with pytest.raises(ValueError, match="fewer than two"):
        await compose_card_faces(card)


async def test_ten_card_grid_stays_small() -> None:
    noisy = Image.effect_noise((CARD_WIDTH, CARD_HEIGHT), 64).convert("RGB")
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    payload = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    cards = [_make_fake_card(name=f"Card {i}") for i in range(10)]
    with _mock_httpx_client(handler):
        result = await compose_card_grid(cards)

    assert len(result.getvalue()) < 8 * 1024 * 1024
