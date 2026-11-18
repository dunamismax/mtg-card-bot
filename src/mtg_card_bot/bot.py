"""Main MTG Card Bot Discord bot implementation."""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

import discord
from discord import app_commands

from . import config, embeds, logging
from .grid import GRID_FILENAME, compose_card_faces, compose_card_grid
from .lookup import CardResolver, Resolution, comma_split_requests
from .query import (
    MAX_CARDS_PER_MESSAGE,
    CardRequest,
    LookupMode,
    extract_bracket_requests,
    parse_card_request,
    split_multi_query,
)
from .scryfall import Card, ScryfallClient

DUPLICATE_WINDOW = 2.5
EDIT_WINDOW = 10 * 60
MAX_EMBEDS_PER_MESSAGE = 10
AUTOCOMPLETE_TIMEOUT = 2.5
SLOW_DOWN_REACTION = "⏳"

COMMAND_MODES: dict[str, LookupMode] = {
    "rules": LookupMode.RULINGS,
    "ruling": LookupMode.RULINGS,
    "rulings": LookupMode.RULINGS,
    "price": LookupMode.PRICES,
    "prices": LookupMode.PRICES,
    "$": LookupMode.PRICES,
    "legal": LookupMode.LEGALITY,
    "legality": LookupMode.LEGALITY,
    "legalities": LookupMode.LEGALITY,
    "formats": LookupMode.LEGALITY,
    "art": LookupMode.ART,
    "image": LookupMode.IMAGE,
    "img": LookupMode.IMAGE,
}
HELP_WORDS = frozenset({"help", "h", "?"})


@dataclass(slots=True)
class MessagePlan:
    """What a message asks the bot to do."""

    help: bool = False
    requests: list[CardRequest] = field(default_factory=list)
    truncated: bool = False
    usage_error: str | None = None


@dataclass(slots=True)
class Reply:
    """Embeds and attachments to send back."""

    embeds: list[discord.Embed] = field(default_factory=list)
    files: list[discord.File] = field(default_factory=list)


class Responder(Protocol):
    async def send(self, reply: Reply) -> None: ...


class MessageResponder:
    """Replies to a chat message without pinging its author."""

    def __init__(self, message: discord.Message) -> None:
        self.message = message

    async def send(self, reply: Reply) -> None:
        if not reply.embeds:
            return
        kwargs: dict[str, Any] = {"embeds": reply.embeds}
        if reply.files:
            kwargs["files"] = reply.files
        to_reference = getattr(self.message, "to_reference", None)
        if to_reference is not None:
            kwargs["reference"] = to_reference(fail_if_not_exists=False)
            kwargs["mention_author"] = False
        await self.message.channel.send(**kwargs)


class InteractionResponder:
    """Answers a deferred slash command interaction."""

    def __init__(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction

    async def send(self, reply: Reply) -> None:
        kwargs: dict[str, Any] = {"embeds": reply.embeds}
        if reply.files:
            kwargs["files"] = reply.files
        await self.interaction.followup.send(**kwargs)


class MTGCardBot(discord.Client):
    """Discord bot for Magic: The Gathering card lookups."""

    def __init__(self, cfg: config.MTGConfig) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            intents=intents, allowed_mentions=discord.AllowedMentions.none()
        )
        self.config = cfg
        self.logger = logging.with_component("mtg_card_bot")
        self.scryfall_client = ScryfallClient(cache_ttl=cfg.cache_ttl)
        self.resolver = CardResolver(self.scryfall_client)
        self.tree = app_commands.CommandTree(self)

        # Duplicate suppression: (author, normalized content) -> timestamp
        self._recent_commands: dict[tuple[int, str], float] = {}
        # Discord message IDs already answered (duplicate gateway delivery)
        self._processed_message_ids: dict[int, float] = {}
        # Per-user token buckets: user id -> (tokens, last refill time)
        self._rate_buckets: dict[int, tuple[float, float]] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

        self._register_app_commands()

    # -- lifecycle -------------------------------------------------------------

    async def start(self, token: str | None = None, *, reconnect: bool = True) -> None:
        """Start the Discord client with the configured token by default."""
        await super().start(token or self.config.discord_token, reconnect=reconnect)

    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        self._cleanup_task = asyncio.create_task(
            self._cleanup_duplicates_periodically()
        )
        if self.config.sync_commands:
            try:
                synced = await self.tree.sync()
                self.logger.info("Synced slash commands", count=len(synced))
            except Exception as e:
                self.logger.warning("Slash command sync failed", error=str(e))
        self.logger.info("MTG Card bot setup completed")

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        guild_info = [
            f"{g.name} (id={g.id}, members={g.member_count})" for g in self.guilds
        ]
        self.logger.info(
            "Bot is ready",
            username=str(self.user),
            guild_count=len(self.guilds),
            guilds=", ".join(guild_info) if guild_info else "NO GUILDS",
        )
        if not self.guilds:
            self.logger.warning(
                "Bot is not in any guilds! "
                "Invite it using the OAuth2 URL from the Discord Developer Portal."
            )

    async def on_disconnect(self) -> None:
        """Gateway disconnects are routine; discord.py resumes automatically."""
        self.logger.info("Bot disconnected from Discord gateway")

    async def on_resumed(self) -> None:
        """Called when the bot resumes a session after disconnect."""
        self.logger.info("Bot resumed Discord gateway session")

    async def on_error(
        self, event_method: str, *args: object, **kwargs: object
    ) -> None:
        """Called when an event handler raises an exception."""
        self.logger.error(
            "Unhandled exception in event handler",
            event=event_method,
            error=traceback.format_exc(),
        )

    async def close(self) -> None:
        """Clean shutdown of the bot."""
        self.logger.info("Shutting down MTG Card bot")
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cleanup_task
        try:
            await self.scryfall_client.close()
        except Exception as e:
            self.logger.warning("Error closing scryfall client", error=str(e))
        self._recent_commands.clear()
        self._processed_message_ids.clear()
        self._rate_buckets.clear()
        await super().close()

    # -- message parsing -------------------------------------------------------

    def _plan_message(self, content: str) -> MessagePlan | None:
        """Work out what a message asks for, or None if it isn't for the bot."""
        bracket_requests = extract_bracket_requests(content)
        if bracket_requests:
            return self._limit(bracket_requests)

        prefix = self.config.command_prefix
        if not prefix or not content.startswith(prefix):
            return None
        body = content[len(prefix) :].strip()
        # Ignore "!!!", "! ", and similar chatter that merely starts with the prefix
        if not body or body.startswith(prefix):
            return None

        command, _, args = body.partition(" ")
        command = command.lower()
        args = args.strip()

        if command in HELP_WORDS and not args:
            return MessagePlan(help=True)

        mode = COMMAND_MODES.get(command)
        # "!price of progress" is the card Price of Progress, not a price lookup
        if mode is not None and args.lower().startswith("of "):
            mode = None
        if mode is not None:
            if not args:
                return MessagePlan(
                    usage_error=f"Please provide a card name, e.g. `{prefix}{command} "
                    "lightning bolt`."
                )
            parts = split_multi_query(args)
        else:
            mode = LookupMode.CARD
            parts = split_multi_query(body)

        requests = [
            request
            for part in parts
            if (request := parse_card_request(part, mode=mode)) is not None
        ]
        if not requests:
            return None
        return self._limit(requests)

    @staticmethod
    def _limit(requests: list[CardRequest]) -> MessagePlan:
        return MessagePlan(
            requests=requests[:MAX_CARDS_PER_MESSAGE],
            truncated=len(requests) > MAX_CARDS_PER_MESSAGE,
        )

    # -- abuse protection ------------------------------------------------------

    def _allow_user(self, user_id: int, now: float | None = None) -> bool:
        """Token bucket: a short burst is fine, sustained spam is not."""
        cooldown = self.config.command_cooldown
        if cooldown <= 0:
            return True
        now = time.monotonic() if now is None else now
        burst = float(max(1, self.config.command_burst))
        tokens, last = self._rate_buckets.get(user_id, (burst, now))
        tokens = min(burst, tokens + (now - last) / cooldown)
        if tokens < 1:
            self._rate_buckets[user_id] = (tokens, now)
            return False
        self._rate_buckets[user_id] = (tokens - 1, now)
        return True

    def _is_duplicate(self, user_id: int, content: str, now: float) -> bool:
        key = (user_id, " ".join(content.lower().split()))
        last = self._recent_commands.get(key)
        self._recent_commands[key] = now
        return last is not None and now - last < DUPLICATE_WINDOW

    # -- Discord events --------------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        if message.author.bot or not message.content:
            return
        if message.id in self._processed_message_ids:
            return

        plan = self._plan_message(message.content)
        if plan is None:
            return

        now = time.monotonic()
        if self._is_duplicate(message.author.id, message.content, now):
            self.logger.debug(
                "Suppressed duplicate command", user_id=str(message.author.id)
            )
            return
        if not self._allow_user(message.author.id, now):
            self.logger.debug("Rate limited user", user_id=str(message.author.id))
            with suppress(Exception):
                await message.add_reaction(SLOW_DOWN_REACTION)
            return

        self._processed_message_ids[message.id] = now
        await self._execute_plan(message, plan)

    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        """Look up cards that a user added or fixed by editing their message."""
        if after.author.bot or not after.content or before.content == after.content:
            return
        created_at = getattr(after, "created_at", None)
        if created_at is not None:
            age = (discord.utils.utcnow() - created_at).total_seconds()
            if age > EDIT_WINDOW:
                return

        plan = self._plan_message(after.content)
        if plan is None or plan.help or plan.usage_error:
            return
        previous = self._plan_message(before.content)
        already = previous.requests if previous else []
        fresh = [request for request in plan.requests if request not in already]
        if not fresh:
            return
        if not self._allow_user(after.author.id):
            return
        self.logger.info(
            "Handling edited message",
            user_id=str(after.author.id),
            new_requests=len(fresh),
        )
        await self._execute_plan(after, MessagePlan(requests=fresh))

    async def _execute_plan(self, message: discord.Message, plan: MessagePlan) -> None:
        responder = MessageResponder(message)
        try:
            if plan.help:
                self.logger.info("Showing help", user_id=str(message.author.id))
                await responder.send(
                    Reply([embeds.help_embed(self.config.command_prefix)])
                )
                return
            if plan.usage_error:
                await responder.send(Reply([embeds.error_embed(plan.usage_error)]))
                return
            self.logger.info(
                "Looking up cards",
                user_id=str(message.author.id),
                username=message.author.name,
                queries=" ; ".join(f"{r.mode.value}:{r.label}" for r in plan.requests),
            )
            async with _typing(message.channel):
                reply = await self.build_reply(plan.requests, truncated=plan.truncated)
            await responder.send(reply)
        except discord.HTTPException as e:
            self.logger.error("Failed to send reply", status=e.status, error=str(e))

    # -- slash commands --------------------------------------------------------

    def _register_app_commands(self) -> None:
        bot = self

        async def name_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            try:
                names = await asyncio.wait_for(
                    bot.scryfall_client.autocomplete(current), AUTOCOMPLETE_TIMEOUT
                )
            except Exception:
                return []
            return [
                app_commands.Choice(name=name[:100], value=name[:100])
                for name in names[:25]
            ]

        def lookup_command(
            name: str, description: str, mode: LookupMode
        ) -> app_commands.Command[Any, ..., None]:
            @app_commands.command(name=name, description=description)
            @app_commands.describe(
                card="Card name (typos are fine) or Scryfall syntax like `bolt e:2xm`",
                set_code="Optional set code for a specific printing, e.g. MH3",
            )
            @app_commands.rename(set_code="set")
            @app_commands.autocomplete(card=name_autocomplete)
            async def command(
                interaction: discord.Interaction,
                card: str,
                set_code: str | None = None,
            ) -> None:
                set_value = set_code.strip().lower() if set_code else None
                await bot._run_interaction(
                    interaction, CardRequest(card.strip(), mode, set_value or None)
                )

            return command

        for command in (
            lookup_command("card", "Look up a Magic card", LookupMode.CARD),
            lookup_command("price", "Prices for a Magic card", LookupMode.PRICES),
            lookup_command("rules", "Official rulings for a card", LookupMode.RULINGS),
            lookup_command(
                "legality", "Format legality for a card", LookupMode.LEGALITY
            ),
            lookup_command("art", "A card's illustration", LookupMode.ART),
        ):
            self.tree.add_command(command)

        @self.tree.command(name="random", description="A random Magic card")
        @app_commands.describe(
            query="Optional Scryfall filters, e.g. `t:dragon r:mythic`"
        )
        async def random_command(
            interaction: discord.Interaction, query: str | None = None
        ) -> None:
            await bot._run_interaction(
                interaction, CardRequest((query or "").strip(), random=True)
            )

        @self.tree.command(name="help", description="How to use the MTG Card Bot")
        async def help_command(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                embed=embeds.help_embed(bot.config.command_prefix), ephemeral=True
            )

    async def _run_interaction(
        self, interaction: discord.Interaction, request: CardRequest
    ) -> None:
        if not request.query and not request.random:
            await interaction.response.send_message(
                "Please provide a card name.", ephemeral=True
            )
            return
        if not self._allow_user(interaction.user.id):
            await interaction.response.send_message(
                "You're looking up cards very quickly. Try again in a few seconds.",
                ephemeral=True,
            )
            return
        self.logger.info(
            "Slash command lookup",
            user_id=str(interaction.user.id),
            query=f"{request.mode.value}:{request.label}",
        )
        await interaction.response.defer(thinking=True)
        responder = InteractionResponder(interaction)
        try:
            reply = await self.build_reply([request])
        except Exception as e:
            self.logger.error("Slash command lookup failed", error=str(e))
            reply = Reply([embeds.error_embed("Sorry, something went wrong.")])
        try:
            await responder.send(reply)
        except discord.HTTPException as e:
            self.logger.error("Failed to answer interaction", error=str(e))

    # -- building replies ------------------------------------------------------

    async def build_reply(
        self, requests: list[CardRequest], *, truncated: bool = False
    ) -> Reply:
        """Resolve requests and turn them into embeds and attachments."""
        resolutions = await self.resolver.resolve_many(requests)

        # "Ore Gorger,Skyfire Kirin" — a failed single lookup with commas is
        # usually a list of cards rather than one card name.
        if len(resolutions) == 1 and resolutions[0].not_found:
            split = comma_split_requests(resolutions[0].request)
            if split:
                split_results = await self.resolver.resolve_many(split)
                if sum(result.ok for result in split_results) >= 2:
                    resolutions = split_results

        for resolution in resolutions:
            if not resolution.ok:
                self.logger.info(
                    "Lookup failed",
                    query=resolution.request.label,
                    error=str(resolution.error),
                )

        reply = Reply()
        card_mode = [r for r in resolutions if r.request.mode is LookupMode.CARD]
        found_cards = [r for r in card_mode if r.ok]
        reported: set[int] = set()

        if len(found_cards) >= 2:
            failed_cards = [r for r in card_mode if not r.ok]
            reported.update(id(r) for r in failed_cards)
            await self._add_grid(reply, found_cards, failed_cards)
        elif len(found_cards) == 1:
            await self._add_card(reply, found_cards[0], index=1)

        for index, resolution in enumerate(resolutions, 2):
            if resolution.ok and resolution.request.mode is not LookupMode.CARD:
                await self._add_mode_embed(reply, resolution, index)

        for resolution in resolutions:
            if not resolution.ok and id(resolution) not in reported:
                reply.embeds.append(embeds.error_embed(embeds.failure_text(resolution)))

        if truncated:
            reply.embeds.append(
                embeds.error_embed(
                    f"Only the first {MAX_CARDS_PER_MESSAGE} cards were looked up."
                )
            )
        reply.embeds = reply.embeds[:MAX_EMBEDS_PER_MESSAGE]
        return reply

    async def _add_grid(
        self, reply: Reply, found: list[Resolution], failed: list[Resolution]
    ) -> None:
        embed = embeds.multi_embed(found, failed)
        cards = [r.card for r in found if r.card is not None]
        try:
            buffer = await compose_card_grid(cards)
        except Exception as e:
            self.logger.warning("Grid composition failed", error=str(e))
        else:
            reply.files.append(discord.File(buffer, filename=GRID_FILENAME))
            embed.set_image(url=f"attachment://{GRID_FILENAME}")
        reply.embeds.append(embed)

    async def _card_visual(
        self, card: Card, index: int
    ) -> tuple[str | None, discord.File | None, str | None]:
        """Image URL or attachment for a card, combining both faces of a DFC."""
        faces = card.get_face_image_urls()
        if len(faces) < 2:
            return None, None, None
        try:
            buffer = await compose_card_faces(card)
        except Exception as e:
            self.logger.warning("Face composition failed", error=str(e))
            return faces[0], None, faces[1]
        filename = f"card-{index}.jpg"
        return f"attachment://{filename}", discord.File(buffer, filename=filename), None

    async def _add_card(self, reply: Reply, resolution: Resolution, index: int) -> None:
        card = resolution.card
        assert card is not None
        image_url, file, back_face = await self._card_visual(card, index)
        if file is not None:
            reply.files.append(file)
        reply.embeds.append(
            embeds.card_embed(
                card,
                note=resolution.note,
                query=resolution.request.query,
                image_url=image_url,
                back_face_url=back_face,
            )
        )

    async def _add_mode_embed(
        self, reply: Reply, resolution: Resolution, index: int
    ) -> None:
        card = resolution.card
        assert card is not None
        mode = resolution.request.mode
        note = resolution.note
        try:
            if mode is LookupMode.IMAGE:
                image_url, file, back_face = await self._card_visual(card, index)
                if file is not None:
                    reply.files.append(file)
                reply.embeds.append(
                    embeds.image_embed(
                        card, note=note, image_url=image_url, back_face_url=back_face
                    )
                )
            elif mode is LookupMode.PRICES:
                cheapest = await self.resolver.cheapest_printing(card)
                reply.embeds.append(
                    embeds.prices_embed(card, cheapest=cheapest, note=note)
                )
            elif mode is LookupMode.RULINGS:
                rulings = await self.scryfall_client.get_card_rulings(card.id)
                reply.embeds.append(embeds.rulings_embed(card, rulings, note=note))
            elif mode is LookupMode.LEGALITY:
                reply.embeds.append(embeds.legality_embed(card, note=note))
            elif mode is LookupMode.ART:
                reply.embeds.append(embeds.art_embed(card, note=note))
        except Exception as e:
            self.logger.warning(
                "Lookup detail failed", mode=mode.value, card=card.name, error=str(e)
            )
            failed = Resolution(resolution.request, error=e)
            reply.embeds.append(embeds.error_embed(embeds.failure_text(failed)))

    # -- housekeeping ----------------------------------------------------------

    async def _cleanup_duplicates_periodically(self) -> None:
        """Background task to forget old duplicate and rate limit state."""
        while True:
            try:
                await asyncio.sleep(60)
                self._prune_state(time.monotonic())
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error in duplicate cleanup task", error=str(e))

    def _prune_state(self, now: float) -> None:
        cutoff = now - 300
        self._recent_commands = {
            key: ts for key, ts in self._recent_commands.items() if ts >= cutoff
        }
        self._processed_message_ids = {
            key: ts for key, ts in self._processed_message_ids.items() if ts >= cutoff
        }
        refill = max(self.config.command_cooldown, 0) * max(
            1, self.config.command_burst
        )
        self._rate_buckets = {
            user: state
            for user, state in self._rate_buckets.items()
            if now - state[1] < refill
        }


@asynccontextmanager
async def _typing(channel: Any) -> AsyncIterator[None]:
    """Show "Bot is typing…" while working, ignoring missing permissions."""
    typing: Callable[[], Any] | None = getattr(channel, "typing", None)
    context = None
    if typing is not None:
        try:
            context = typing()
            await context.__aenter__()
        except Exception:
            context = None
    try:
        yield
    finally:
        if context is not None:
            with suppress(Exception):
                await context.__aexit__(None, None, None)
