import datetime
import io
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
import pytest_asyncio

from mtg_card_bot.bot import MTGCardBot
from mtg_card_bot.config import MTGConfig
from mtg_card_bot.errors import ErrorType, create_error
from mtg_card_bot.lookup import Resolution
from mtg_card_bot.query import CardRequest, LookupMode
from mtg_card_bot.scryfall import Card


class FakeChannel:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> None:
        self.sent_messages.append(kwargs)


class FakeAuthor:
    def __init__(
        self, user_id: int = 1, name: str = "Stephen", *, bot: bool = False
    ) -> None:
        self.id = user_id
        self.name = name
        self.bot = bot


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        message_id: int = 1,
        author: FakeAuthor | None = None,
        channel: FakeChannel | None = None,
        age_seconds: float = 0,
    ) -> None:
        self.content = content
        self.id = message_id
        self.author = author or FakeAuthor()
        self.channel = channel or FakeChannel()
        self.created_at = discord.utils.utcnow() - datetime.timedelta(
            seconds=age_seconds
        )
        self.reactions: list[str] = []

    def to_reference(self, *, fail_if_not_exists: bool = True) -> str:
        assert fail_if_not_exists is False
        return f"reference-{self.id}"

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)


def make_card(**overrides: Any) -> Card:
    data: dict[str, Any] = {
        "object": "card",
        "id": "lightning-bolt-id",
        "oracle_id": "bolt-oracle",
        "name": "Lightning Bolt",
        "scryfall_uri": "https://scryfall.com/card/lea/161/lightning-bolt",
        "image_uris": {
            "png": "https://img.example/lightning-bolt.png",
            "normal": "https://img.example/lightning-bolt.jpg",
        },
        "mana_cost": "{R}",
        "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "set_name": "Limited Edition Alpha",
        "set": "lea",
        "collector_number": "161",
        "rarity": "common",
        "artist": "Christopher Rush",
        "prices": {"usd": "1.25", "usd_foil": "3.40"},
        "legalities": {"modern": "legal", "commander": "legal", "standard": "banned"},
    }
    data.update(overrides)
    return Card(data)


@pytest_asyncio.fixture
async def bot(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MTGCardBot]:
    monkeypatch.setenv("MTG_DISCORD_TOKEN", "test-token")
    for key in ("MTG_COMMAND_COOLDOWN", "MTG_COMMAND_BURST", "MTG_COMMAND_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    instance = MTGCardBot(MTGConfig())
    yield instance
    await instance.close()


def stub_resolver(
    bot: MTGCardBot,
    monkeypatch: pytest.MonkeyPatch,
    cards: dict[str, Card],
    notes: dict[str, str] | None = None,
) -> list[list[CardRequest]]:
    """Resolve requests from a name -> card table; returns the recorded batches."""
    batches: list[list[CardRequest]] = []
    notes = notes or {}

    async def resolve_many(requests: list[CardRequest]) -> list[Resolution]:
        batches.append(list(requests))
        results = []
        for request in requests:
            key = request.query.casefold()
            if key in cards:
                results.append(Resolution(request, cards[key], notes.get(key)))
            else:
                results.append(
                    Resolution(
                        request,
                        error=create_error(ErrorType.NOT_FOUND, "missing"),
                        suggestions=["Lightning Bolt"] if key == "lightnin" else [],
                    )
                )
        return results

    monkeypatch.setattr(bot.resolver, "resolve_many", resolve_many)
    return batches


def field_map(embed: discord.Embed) -> dict[str, str]:
    return {str(field.name): str(field.value) for field in embed.fields}


async def send(bot: MTGCardBot, text: str, **kwargs: Any) -> FakeMessage:
    message = FakeMessage(text, **kwargs)
    await bot.on_message(cast(Any, message))
    return message


# -- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "requests"),
    [
        ("!lightning bolt", [CardRequest("lightning bolt")]),
        ("!rules counterspell", [CardRequest("counterspell", LookupMode.RULINGS)]),
        ("!price sol ring", [CardRequest("sol ring", LookupMode.PRICES)]),
        ("!price of progress", [CardRequest("price of progress")]),
        ("!legal sol ring", [CardRequest("sol ring", LookupMode.LEGALITY)]),
        ("!art bolt", [CardRequest("bolt", LookupMode.ART)]),
        (
            "!rand rarity:mythic e:mh3",
            [CardRequest("rarity:mythic e:mh3", random=True)],
        ),
        (
            "!bolt; counterspell; r",
            [
                CardRequest("bolt"),
                CardRequest("counterspell"),
                CardRequest("", random=True),
            ],
        ),
        (
            "Look: [[Sol Ring|LEA]] and [[?Bolt]]",
            [
                CardRequest("Sol Ring", set_code="lea"),
                CardRequest("Bolt", LookupMode.RULINGS),
            ],
        ),
    ],
)
def test_plan_message(bot: MTGCardBot, text: str, requests: list[CardRequest]) -> None:
    plan = bot._plan_message(text)
    assert plan is not None
    assert plan.requests == requests


@pytest.mark.parametrize("text", ["hello there", "!!!", "!", "! ", "!!bolt", ""])
def test_plan_message_ignores_chatter(bot: MTGCardBot, text: str) -> None:
    assert bot._plan_message(text) is None


def test_plan_message_help_and_usage(bot: MTGCardBot) -> None:
    help_plan = bot._plan_message("!help")
    usage_plan = bot._plan_message("!rules")
    assert help_plan is not None and help_plan.help
    assert usage_plan is not None and usage_plan.usage_error


def test_plan_message_caps_card_count(bot: MTGCardBot) -> None:
    plan = bot._plan_message("!" + "; ".join(f"card {i}" for i in range(15)))
    assert plan is not None
    assert len(plan.requests) == 10
    assert plan.truncated


# -- rate limiting and duplicates -------------------------------------------


def test_token_bucket_allows_bursts_then_throttles(bot: MTGCardBot) -> None:
    bot.config.command_cooldown = 2.0
    bot.config.command_burst = 3

    assert [bot._allow_user(7, now=100.0) for _ in range(4)] == [
        True,
        True,
        True,
        False,
    ]
    assert bot._allow_user(7, now=102.0) is True  # one token refilled
    assert bot._allow_user(8, now=102.0) is True  # other users unaffected


async def test_rate_limited_user_gets_a_reaction_not_silence(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_resolver(bot, monkeypatch, {f"card {i}": make_card() for i in range(5)})
    bot.config.command_burst = 1

    first = await send(bot, "!card 1", message_id=1)
    second = await send(bot, "!card 2", message_id=2)

    assert len(first.channel.sent_messages) == 1
    assert second.channel.sent_messages == []
    assert second.reactions == ["⏳"]


async def test_duplicate_messages_are_suppressed(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = stub_resolver(bot, monkeypatch, {"bolt": make_card()})
    channel = FakeChannel()

    await send(bot, "!bolt", message_id=1, channel=channel)
    await send(bot, "!bolt", message_id=1, channel=channel)  # redelivered
    await send(bot, "!BOLT ", message_id=2, channel=channel)  # double-send

    assert len(batches) == 1
    assert len(channel.sent_messages) == 1


async def test_bot_messages_are_ignored(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = stub_resolver(bot, monkeypatch, {})
    await send(bot, "[[Bolt]]", author=FakeAuthor(bot=True))
    assert batches == []


# -- single card replies ------------------------------------------------------


async def test_bracket_lookup_replies_with_card_embed(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = make_card()
    stub_resolver(bot, monkeypatch, {"lightning bolt": card})

    message = await send(bot, "Could you grab [[Lightning Bolt]] for me?")

    sent = message.channel.sent_messages
    assert len(sent) == 1
    assert sent[0]["reference"] == "reference-1"
    assert sent[0]["mention_author"] is False
    embed = sent[0]["embeds"][0]
    assert embed.title == "Lightning Bolt"
    assert embed.url == card.scryfall_uri
    assert embed.description == "**Mana Cost:** {R} · **Price:** $1.25 · $3.40 foil"
    assert embed.image.url == "https://img.example/lightning-bolt.png"
    fields = field_map(embed)
    assert fields["Legal in"] == "Modern, Commander"
    assert fields["Banned in"] == "Standard"
    assert embed.footer.text == (
        "Limited Edition Alpha (LEA) #161 • Common • Art by Christopher Rush"
    )


async def test_closest_match_note_is_shown(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_resolver(
        bot,
        monkeypatch,
        {"bolt": make_card()},
        notes={"bolt": "`bolt` matches 41 cards — showing the most played"},
    )

    message = await send(bot, "!bolt")

    embed = message.channel.sent_messages[0]["embeds"][0]
    assert (embed.description or "").startswith(
        "*`bolt` matches 41 cards — showing the most played*"
    )


async def test_filtered_result_is_labelled(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_resolver(bot, monkeypatch, {"bolt e:2xm": make_card(rarity="rare")})

    message = await send(bot, "!bolt e:2xm")

    embed = message.channel.sent_messages[0]["embeds"][0]
    assert (embed.description or "").startswith("*Filtered result for: `bolt e:2xm`*")
    assert embed.color == discord.Colour(0xFFD700)


async def test_card_without_image_shows_rules_text(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_resolver(bot, monkeypatch, {"bolt": make_card(image_uris={})})

    message = await send(bot, "!bolt")

    embed = message.channel.sent_messages[0]["embeds"][0]
    assert "*Instant*\nLightning Bolt deals 3 damage to any target." in (
        embed.description or ""
    )
    assert embed.image.url is None


async def test_double_faced_card_shows_both_faces(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    dfc = make_card(
        name="Delver of Secrets // Insectile Aberration",
        image_uris={},
        card_faces=[
            {"name": "Delver of Secrets", "image_uris": {"normal": "front.jpg"}},
            {"name": "Insectile Aberration", "image_uris": {"normal": "back.jpg"}},
        ],
    )
    stub_resolver(bot, monkeypatch, {"delver": dfc})
    compose = AsyncMock(return_value=io.BytesIO(b"jpeg"))
    monkeypatch.setattr("mtg_card_bot.bot.compose_card_faces", compose)

    message = await send(bot, "[[delver]]")

    sent = message.channel.sent_messages[0]
    compose.assert_awaited_once_with(dfc)
    assert sent["files"][0].filename == "card-1.jpg"
    assert sent["embeds"][0].image.url == "attachment://card-1.jpg"


async def test_double_faced_card_links_back_face_when_composition_fails(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    dfc = make_card(
        image_uris={},
        card_faces=[
            {"name": "Front", "image_uris": {"png": "front.png"}},
            {"name": "Back", "image_uris": {"png": "back.png"}},
        ],
    )
    stub_resolver(bot, monkeypatch, {"delver": dfc})
    monkeypatch.setattr(
        "mtg_card_bot.bot.compose_card_faces",
        AsyncMock(side_effect=RuntimeError("cdn down")),
    )

    message = await send(bot, "[[delver]]")

    sent = message.channel.sent_messages[0]
    assert "files" not in sent
    embed = sent["embeds"][0]
    assert embed.image.url == "front.png"
    assert "[View back face](back.png)" in (embed.description or "")


async def test_not_found_offers_suggestions(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_resolver(bot, monkeypatch, {})

    message = await send(bot, "!lightnin")

    embed = message.channel.sent_messages[0]["embeds"][0]
    assert embed.title == "Error"
    assert embed.description == (
        "No card named `lightnin`. Did you mean: **Lightning Bolt**?"
    )


async def test_comma_separated_names_become_a_multi_lookup(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = stub_resolver(
        bot,
        monkeypatch,
        {
            "ore gorger": make_card(name="Ore Gorger"),
            "skyfire kirin": make_card(name="Skyfire Kirin"),
        },
    )
    monkeypatch.setattr(
        "mtg_card_bot.bot.compose_card_grid",
        AsyncMock(return_value=io.BytesIO(b"jpeg")),
    )

    message = await send(bot, "!Ore Gorger,Skyfire Kirin")

    assert len(batches) == 2
    embed = message.channel.sent_messages[0]["embeds"][0]
    assert "Ore Gorger" in (embed.description or "")
    assert "Skyfire Kirin" in (embed.description or "")


# -- multi-card replies ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "grid_available"),
    [
        ("!bolt; counterspell; nope", True),
        ("Compare [[Bolt]] with [[Counterspell]] and [[nope]].", True),
        ("Compare [[Bolt]] with [[Counterspell]] and [[nope]].", False),
    ],
)
async def test_multi_card_sends_grid(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch, text: str, grid_available: bool
) -> None:
    bolt = make_card()
    counterspell = make_card(
        name="Counterspell",
        scryfall_uri="https://scryfall.com/card/7ed/counterspell",
        prices={"usd": "2.50"},
    )
    stub_resolver(
        bot,
        monkeypatch,
        {"bolt": bolt, "counterspell": counterspell},
        notes={"counterspell": "Closest match"},
    )
    compose = AsyncMock(
        return_value=io.BytesIO(b"jpeg"),
        side_effect=None if grid_available else RuntimeError("No card images"),
    )
    monkeypatch.setattr("mtg_card_bot.bot.compose_card_grid", compose)

    message = await send(bot, text)

    compose.assert_awaited_once_with([bolt, counterspell])
    sent = message.channel.sent_messages
    assert len(sent) == 1
    embeds = sent[0]["embeds"]
    assert len(embeds) == 1
    description = embeds[0].description or ""
    assert "[Lightning Bolt](https://scryfall.com/card/lea/161/lightning-bolt)" in (
        description
    )
    assert "Counterspell" in description
    assert "closest match" in description.lower()
    assert "$2.50" in description
    assert "No card named `nope`" in description

    if grid_available:
        assert sent[0]["files"][0].filename == "cards.jpg"
        assert embeds[0].image.url == "attachment://cards.jpg"
    else:
        assert "files" not in sent[0]
        assert embeds[0].image.url is None


async def test_mixed_modes_send_one_message_with_several_embeds(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    bolt = make_card()
    stub_resolver(
        bot, monkeypatch, {"bolt": bolt, "sol ring": make_card(name="Sol Ring")}
    )
    rulings = AsyncMock(
        return_value=[
            {"source": "wotc", "published_at": "2024-01-01", "comment": "Yes."}
        ]
    )
    monkeypatch.setattr(bot.scryfall_client, "get_card_rulings", rulings)
    monkeypatch.setattr(bot.resolver, "cheapest_printing", AsyncMock(return_value=None))

    message = await send(bot, "[[bolt]] [[?bolt]] [[$sol ring]] [[#sol ring]]")

    embeds = message.channel.sent_messages[0]["embeds"]
    assert [embed.title for embed in embeds] == [
        "Lightning Bolt",
        "Rulings for Lightning Bolt",
        "Sol Ring",
        "Sol Ring",
    ]
    assert field_map(embeds[1]) == {"Wizards (2024-01-01)": "Yes."}
    assert "This printing — Limited Edition Alpha (LEA) #161" in field_map(embeds[2])
    assert field_map(embeds[3])["Banned"] == "Standard"


async def test_help_command(bot: MTGCardBot) -> None:
    message = await send(bot, "!help")
    embed = message.channel.sent_messages[0]["embeds"][0]
    assert embed.title == "MTG Card Bot"
    assert "[[Sol Ring|LEA]]" in field_map(embed)["Pick a printing"]


# -- message edits ------------------------------------------------------------


async def test_edit_looks_up_only_new_cards(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = stub_resolver(bot, monkeypatch, {"bolt": make_card()})
    before = FakeMessage("[[Bolt]] and [[lightnin]]")
    after = FakeMessage("[[Bolt]] and [[lightning bolt]]", channel=before.channel)

    await bot.on_message_edit(cast(Any, before), cast(Any, after))

    assert batches == [[CardRequest("lightning bolt")]]


async def test_old_or_unchanged_edits_are_ignored(
    bot: MTGCardBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = stub_resolver(bot, monkeypatch, {})
    same = FakeMessage("[[Bolt]]")
    old_before = FakeMessage("[[Bolt]]", age_seconds=3600)
    old_after = FakeMessage("[[Shock]]", age_seconds=3600)

    await bot.on_message_edit(cast(Any, same), cast(Any, same))
    await bot.on_message_edit(cast(Any, old_before), cast(Any, old_after))

    assert batches == []


# -- housekeeping -----------------------------------------------------------------


def test_prune_state_forgets_old_entries(bot: MTGCardBot) -> None:
    bot._recent_commands[(1, "old")] = 0.0
    bot._recent_commands[(1, "new")] = 1000.0
    bot._processed_message_ids[1] = 0.0
    bot._rate_buckets[1] = (0.0, 0.0)

    bot._prune_state(1000.0)

    assert list(bot._recent_commands) == [(1, "new")]
    assert bot._processed_message_ids == {}
    assert bot._rate_buckets == {}


def test_slash_commands_are_registered(bot: MTGCardBot) -> None:
    names = {command.name for command in bot.tree.get_commands()}
    assert names == {"card", "price", "rules", "legality", "art", "random", "help"}
