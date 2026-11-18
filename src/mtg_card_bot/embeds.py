"""Discord embed builders for card lookups.

All builders are pure functions of Scryfall data so they can be tested without
a gateway connection. Every text field is clamped to Discord's embed limits.
"""

from __future__ import annotations

from typing import Any

import discord

from . import errors
from .lookup import Resolution
from .query import has_filter_parameters
from .scryfall import Card

COLOR_DEFAULT = 0x9B59B6
COLOR_MULTI = 0x5865F2
COLOR_ERROR = 0xE74C3C

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FOOTER_LIMIT = 2048
EMBED_TOTAL_LIMIT = 6000
MAX_RULINGS = 12

RARITY_COLORS = {
    "mythic": 0xFF8C00,  # Dark orange
    "rare": 0xFFD700,  # Gold
    "uncommon": 0xC0C0C0,  # Silver
    "common": 0x1A1A1A,  # Near black (pure black renders as "no color")
    "special": 0xFF1493,  # Deep pink
    "bonus": 0x9370DB,  # Medium purple
}


def truncate(text: str, limit: int) -> str:
    """Clamp text to a Discord limit, marking the cut with an ellipsis."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def code(text: str) -> str:
    """Show user text as inline code without letting backticks break out."""
    return "`" + truncate(text.replace("`", "'"), 200) + "`"


def rarity_color(rarity: str) -> int:
    return RARITY_COLORS.get(rarity.lower(), COLOR_DEFAULT)


def _base_embed(card: Card, description: str | None = None) -> discord.Embed:
    return discord.Embed(
        title=truncate(card.get_display_name(), TITLE_LIMIT),
        url=card.scryfall_uri or None,
        color=rarity_color(card.rarity),
        description=truncate(description, DESCRIPTION_LIMIT) if description else None,
    )


def _footer(card: Card) -> str:
    parts = [card.get_printing_label(), card.rarity.title()]
    if card.artist:
        parts.append(f"Art by {card.artist}")
    return truncate(" • ".join(part for part in parts if part), FOOTER_LIMIT)


def _cost_line(card: Card) -> str:
    parts = []
    if card.mana_cost:
        parts.append(f"**Mana Cost:** {card.mana_cost}")
    prices = card.get_price_summary()
    if prices:
        parts.append(f"**Price:** {prices}")
    return " · ".join(parts)


def _add_legality_fields(embed: discord.Embed, card: Card) -> None:
    legality_text = card.get_format_legalities()
    if legality_text:
        embed.add_field(
            name="Legal in",
            value=truncate(legality_text, FIELD_VALUE_LIMIT),
            inline=False,
        )
    groups = card.get_legality_groups()
    for status, label in (("restricted", "Restricted in"), ("banned", "Banned in")):
        if groups[status]:
            embed.add_field(name=label, value=", ".join(groups[status]), inline=False)


def card_embed(
    card: Card,
    *,
    note: str | None = None,
    query: str | None = None,
    image_url: str | None = None,
    back_face_url: str | None = None,
) -> discord.Embed:
    """The main lookup result: card image, cost, prices, and legality."""
    lines: list[str] = []
    if note:
        lines.append(f"*{note}*")
    elif query and query != "random" and has_filter_parameters(query):
        lines.append(f"*Filtered result for: {code(query)}*")

    has_image = bool(image_url) or card.has_image()
    if not has_image:
        rules_text = card.get_rules_text()
        if rules_text:
            lines.append(rules_text)

    cost_line = _cost_line(card)
    if cost_line:
        lines.append(cost_line)
    if back_face_url:
        lines.append(f"[View back face]({back_face_url})")

    embed = _base_embed(card, "\n".join(lines) if lines else None)
    if has_image:
        embed.set_image(url=image_url or card.get_best_image_url())
    _add_legality_fields(embed, card)
    embed.set_footer(text=_footer(card))
    return embed


def image_embed(
    card: Card,
    *,
    note: str | None = None,
    image_url: str | None = None,
    back_face_url: str | None = None,
) -> discord.Embed:
    """Just the card image (``[[!name]]``)."""
    lines = [f"*{note}*"] if note else []
    if back_face_url:
        lines.append(f"[View back face]({back_face_url})")
    embed = _base_embed(card, "\n".join(lines) if lines else None)
    url = image_url or card.get_best_image_url()
    if url:
        embed.set_image(url=url)
    else:
        embed.description = truncate(card.get_rules_text(), DESCRIPTION_LIMIT)
    return embed


def art_embed(card: Card, *, note: str | None = None) -> discord.Embed:
    """The illustration without the frame."""
    embed = _base_embed(card, f"*{note}*" if note else None)
    art = card.get_art_crop_url()
    if art:
        embed.set_image(url=art)
    else:
        embed.description = "No art crop is available for this printing."
    embed.set_footer(
        text=truncate(
            f"Art by {card.artist or 'unknown'} • {card.get_printing_label()}",
            FOOTER_LIMIT,
        )
    )
    return embed


def prices_embed(
    card: Card, *, cheapest: Card | None = None, note: str | None = None
) -> discord.Embed:
    """Prices for this printing and the cheapest paper printing (``[[$name]]``)."""
    embed = _base_embed(card, f"*{note}*" if note else None)
    embed.add_field(
        name=truncate(f"This printing — {card.get_printing_label()}", FIELD_NAME_LIMIT),
        value=card.get_price_summary() or "No price data",
        inline=False,
    )
    if cheapest is not None and cheapest.id == card.id:
        embed.add_field(
            name="Cheapest paper printing",
            value="This is the cheapest paper printing.",
            inline=False,
        )
    elif cheapest is not None:
        price = cheapest.get_price_display() or "No price data"
        link = (
            f"[{cheapest.get_printing_label()}]({cheapest.scryfall_uri})"
            if cheapest.scryfall_uri
            else cheapest.get_printing_label()
        )
        embed.add_field(
            name="Cheapest paper printing",
            value=truncate(f"{price} — {link}", FIELD_VALUE_LIMIT),
            inline=False,
        )
    thumbnail = card.get_best_image_url(("small", "normal"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text="Prices from Scryfall, updated daily")
    return embed


def legality_embed(card: Card, *, note: str | None = None) -> discord.Embed:
    """Format legality grouped by status (``[[#name]]``)."""
    embed = _base_embed(card, f"*{note}*" if note else None)
    groups = card.get_legality_groups()
    for status, label in (
        ("legal", "Legal"),
        ("restricted", "Restricted"),
        ("banned", "Banned"),
        ("not_legal", "Not legal"),
    ):
        if groups[status]:
            embed.add_field(name=label, value=", ".join(groups[status]), inline=False)
    if not embed.fields:
        embed.description = "No legality data available."
    thumbnail = card.get_best_image_url(("small", "normal"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def rulings_embed(
    card: Card, rulings: list[dict[str, Any]], *, note: str | None = None
) -> discord.Embed:
    """Official rulings (``!rules`` / ``[[?name]]``), within Discord's size limit."""
    name = card.get_display_name()
    if not rulings:
        return discord.Embed(
            title="No Rulings Found",
            description=f"No official rulings found for **{name}**.",
            color=COLOR_DEFAULT,
            url=card.scryfall_uri or None,
        )

    embed = discord.Embed(
        title=truncate(f"Rulings for {name}", TITLE_LIMIT),
        url=card.scryfall_uri or None,
        color=rarity_color(card.rarity),
        description=f"*{note}*" if note else None,
    )
    footer_reserve = 120
    budget = EMBED_TOTAL_LIMIT - len(embed) - footer_reserve
    shown = 0
    for ruling in rulings[:MAX_RULINGS]:
        source = "Wizards" if ruling.get("source") == "wotc" else "Scryfall"
        field_name = f"{source} ({ruling.get('published_at', 'unknown date')})"
        value = truncate(ruling.get("comment") or "No ruling text", FIELD_VALUE_LIMIT)
        if len(field_name) + len(value) > budget:
            break
        embed.add_field(name=field_name, value=value, inline=False)
        budget -= len(field_name) + len(value)
        shown += 1

    if shown < len(rulings):
        embed.set_footer(
            text=f"Showing {shown} of {len(rulings)} rulings. "
            "Open the title link for the rest on Scryfall."
        )
    else:
        embed.set_footer(text=f"{len(rulings)} ruling(s) found.")
    return embed


def failure_text(resolution: Resolution) -> str:
    """A friendly explanation of why a request failed."""
    request = resolution.request
    exc = resolution.error
    label = request.label
    if isinstance(exc, errors.MTGError):
        if exc.error_type == errors.ErrorType.NOT_FOUND:
            if request.random:
                if request.query:
                    return f"No cards match {code(request.query)}. Try broader filters."
                return "Scryfall couldn't return a random card right now."
            if request.parsed.has_filters:
                return (
                    f"No cards found for {code(label)}. Check the filters "
                    "(for example `e:mh3`, `is:foil`, `t:goblin`) and spelling."
                )
            message = f"No card named {code(label)}."
            if resolution.suggestions:
                message += (
                    " Did you mean: "
                    + ", ".join(f"**{name}**" for name in resolution.suggestions)
                    + "?"
                )
            return message
        if exc.error_type == errors.ErrorType.VALIDATION:
            return f"Scryfall couldn't understand {code(label)}: {exc.message}"
        if exc.error_type == errors.ErrorType.RATE_LIMIT:
            return "Scryfall asked us to slow down. Please try again in a few seconds."
        if exc.error_type == errors.ErrorType.NETWORK:
            return "Could not reach Scryfall. Please try again in a moment."
        if exc.error_type == errors.ErrorType.API:
            return "Scryfall is temporarily unavailable. Please try again in a moment."
    if isinstance(exc, TimeoutError):
        return f"Looking up {code(label)} took too long. Please try again."
    return "Sorry, something went wrong. Please try again."


def multi_embed(resolved: list[Resolution], failed: list[Resolution]) -> discord.Embed:
    """Numbered card list shown above the composite grid image."""
    lines: list[str] = []
    for index, item in enumerate(resolved, 1):
        card = item.card
        assert card is not None
        name = card.get_display_name()
        entry = (
            f"**{index}.** [{name}]({card.scryfall_uri})"
            if card.scryfall_uri
            else (f"**{index}.** {name}")
        )
        if item.note:
            entry += " *(closest match)*"
        price = card.get_price_display()
        if price:
            entry += f" · {price}"
        lines.append(entry)

    if failed:
        lines.append("")
        for item in failed:
            lines.append(f"❌ {failure_text(item)}")

    return discord.Embed(
        description=truncate("\n".join(lines), DESCRIPTION_LIMIT), color=COLOR_MULTI
    )


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="Error",
        description=truncate(message, DESCRIPTION_LIMIT),
        color=COLOR_ERROR,
    )


def help_embed(prefix: str) -> discord.Embed:
    embed = discord.Embed(
        title="MTG Card Bot",
        description=(
            "**Fast card lookups with live prices, legality, and rulings.** "
            "Typos and partial names are fine."
        ),
        color=COLOR_MULTI,
    )
    embed.add_field(
        name="Look up cards",
        value=(
            "`[[Lightning Bolt]]` • anywhere in a message\n"
            f"`{prefix}lightning bolt` • prefix lookup\n"
            "`/card` • slash command with name autocomplete\n"
            f"`{prefix}random` • random card (filters work: `{prefix}random t:dragon`)"
        ),
        inline=False,
    )
    embed.add_field(
        name="What to show",
        value=(
            f"`[[!name]]` image only • `[[$name]]` or `{prefix}price` prices\n"
            f"`[[?name]]` or `{prefix}rules` rulings • "
            f"`[[#name]]` or `{prefix}legal` legality\n"
            f"`{prefix}art name` illustration only"
        ),
        inline=False,
    )
    embed.add_field(
        name="Pick a printing",
        value=(
            "`[[Sol Ring|LEA]]` set code • `[[Sol Ring|LEA|270]]` collector number\n"
            "Any Scryfall syntax works: `e:mh3`, `is:showcase`, `t:goblin mv=1`\n"
            f"Sort with `order:usd dir:desc` • `{prefix}cultivate order:edhrec`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Several at once",
        value=(
            "`[[Lightning Bolt]] [[Counterspell]]` or "
            f"`{prefix}bolt; counterspell; doom blade` (up to 10)\n"
            "Edit a message to fix a typo and the bot looks it up again."
        ),
        inline=False,
    )
    embed.set_footer(text="Powered by Scryfall • github.com/dunamismax/mtg-card-bot")
    return embed
