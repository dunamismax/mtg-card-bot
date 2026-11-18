from mtg_card_bot.embeds import (
    EMBED_TOTAL_LIMIT,
    art_embed,
    code,
    failure_text,
    legality_embed,
    prices_embed,
    rulings_embed,
    truncate,
)
from mtg_card_bot.errors import ErrorType, create_error
from mtg_card_bot.lookup import Resolution
from mtg_card_bot.query import CardRequest
from mtg_card_bot.scryfall import Card


def make_card(**extra: object) -> Card:
    return Card(
        {
            "object": "card",
            "id": "id-1",
            "name": "Sol Ring",
            "scryfall_uri": "https://scryfall.com/card/lea/270/sol-ring",
            "set_name": "Limited Edition Alpha",
            "set": "lea",
            "collector_number": "270",
            "rarity": "uncommon",
            "artist": "Mark Tedin",
            "prices": {"usd": "1000"},
            "image_uris": {"small": "small.jpg", "art_crop": "art.jpg"},
            "legalities": {
                "commander": "legal",
                "vintage": "restricted",
                "legacy": "banned",
                "modern": "not_legal",
            },
            **extra,
        }
    )


def test_truncate_and_code_escape() -> None:
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"
    assert code("a`b") == "`a'b`"


def test_rulings_embed_respects_discord_size_limit() -> None:
    rulings = [
        {"source": "wotc", "published_at": "2020-01-01", "comment": "x" * 1500}
        for _ in range(20)
    ]

    embed = rulings_embed(make_card(), rulings)

    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert 0 < len(embed.fields) < 20
    assert all(len(str(field.value)) <= 1024 for field in embed.fields)
    assert embed.footer.text is not None
    assert embed.footer.text.startswith(f"Showing {len(embed.fields)} of 20 rulings")


def test_rulings_embed_without_rulings() -> None:
    embed = rulings_embed(make_card(), [])
    assert embed.title == "No Rulings Found"


def test_prices_embed_mentions_cheapest_printing() -> None:
    cheapest = make_card(
        id="id-2",
        set="c21",
        set_name="Commander 2021",
        collector_number="263",
        prices={"usd": "0.99"},
        scryfall_uri="https://scryfall.com/card/c21/263/sol-ring",
    )

    embed = prices_embed(make_card(), cheapest=cheapest)

    fields = {str(f.name): str(f.value) for f in embed.fields}
    assert fields["This printing — Limited Edition Alpha (LEA) #270"] == "$1000.00"
    assert fields["Cheapest paper printing"] == (
        "$0.99 — [Commander 2021 (C21) #263](https://scryfall.com/card/c21/263/sol-ring)"
    )
    assert embed.thumbnail.url == "small.jpg"


def test_prices_embed_when_default_printing_is_cheapest() -> None:
    card = make_card()

    embed = prices_embed(card, cheapest=card)

    fields = {str(f.name): str(f.value) for f in embed.fields}
    assert fields["Cheapest paper printing"] == "This is the cheapest paper printing."


def test_legality_and_art_embeds() -> None:
    legality = legality_embed(make_card())
    fields = {str(f.name): str(f.value) for f in legality.fields}
    assert fields == {
        "Legal": "Commander",
        "Restricted": "Vintage",
        "Banned": "Legacy",
        "Not legal": "Modern",
    }

    art = art_embed(make_card())
    assert art.image.url == "art.jpg"
    assert art.footer.text == "Art by Mark Tedin • Limited Edition Alpha (LEA) #270"


def test_failure_text_variants() -> None:
    missing = create_error(ErrorType.NOT_FOUND, "missing")
    assert (
        failure_text(Resolution(CardRequest("t:dragon", random=True), error=missing))
        == "No cards match `t:dragon`. Try broader filters."
    )
    assert "Check the filters" in failure_text(
        Resolution(CardRequest("bolt e:zzz"), error=missing)
    )
    assert (
        failure_text(
            Resolution(
                CardRequest("lightnin"),
                error=missing,
                suggestions=["Lightning Bolt", "Lightning Helix"],
            )
        )
        == "No card named `lightnin`. Did you mean: "
        "**Lightning Bolt**, **Lightning Helix**?"
    )
    assert "slow down" in failure_text(
        Resolution(CardRequest("bolt"), error=create_error(ErrorType.RATE_LIMIT, "429"))
    )
    assert "took too long" in failure_text(
        Resolution(CardRequest("bolt"), error=TimeoutError())
    )
