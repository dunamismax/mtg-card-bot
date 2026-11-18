"""Scryfall API client for Magic: The Gathering card data."""

from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict
from contextlib import suppress
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from . import __version__, errors, logging

# Display order and names for the formats shown in card embeds.
FORMAT_NAMES: dict[str, str] = {
    "standard": "Standard",
    "pioneer": "Pioneer",
    "modern": "Modern",
    "legacy": "Legacy",
    "vintage": "Vintage",
    "commander": "Commander",
    "oathbreaker": "Oathbreaker",
    "brawl": "Brawl",
    "historic": "Historic",
    "timeless": "Timeless",
    "pauper": "Pauper",
    "paupercommander": "Pauper Commander",
    "penny": "Penny",
    "duel": "Duel",
}

# Scryfall's documented hard limits (https://scryfall.com/docs/api/rate-limits).
# Exceeding them earns an HTTP 429 and a 30 second lockout for the application.
STRICT_ENDPOINTS = (
    "/cards/search",
    "/cards/named",
    "/cards/random",
    "/cards/collection",
)
STRICT_INTERVAL = 0.5
DEFAULT_INTERVAL = 0.1
LOCKOUT_SECONDS = 30.0
COLLECTION_BATCH = 75


def normalize_name(name: str) -> str:
    """Normalize a card name for case- and spacing-insensitive comparison."""
    return " ".join(name.casefold().split())


def _format_money(value: Any, template: str) -> str | None:
    try:
        return template.format(float(value)) if value else None
    except (TypeError, ValueError):
        return None


class CardFace:
    """Represents one face of a multi-faced card."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.object = data.get("object", "")
        self.name = data.get("name", "")
        self.mana_cost = data.get("mana_cost", "")
        self.type_line = data.get("type_line", "")
        self.oracle_text = data.get("oracle_text", "")
        self.colors = data.get("colors", [])
        self.artist = data.get("artist", "")
        self.power = data.get("power")
        self.toughness = data.get("toughness")
        self.loyalty = data.get("loyalty")
        self.defense = data.get("defense")
        self.image_uris: dict[str, str] = data.get("image_uris", {}) or {}


class Card:
    """Represents a Magic: The Gathering card from the Scryfall API."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.object = data.get("object", "")
        self.id = data.get("id", "")
        self.oracle_id = data.get("oracle_id", "")
        self.name = data.get("name", "")
        self.lang = data.get("lang", "")
        self.released_at = data.get("released_at", "")
        self.uri = data.get("uri", "")
        self.scryfall_uri = data.get("scryfall_uri", "")
        self.layout = data.get("layout", "")
        self.image_uris: dict[str, str] = data.get("image_uris", {}) or {}
        self.card_faces = [CardFace(face) for face in data.get("card_faces", [])]
        self.mana_cost = data.get("mana_cost", "")
        self.cmc = data.get("cmc", 0)
        self.type_line = data.get("type_line", "")
        self.oracle_text = data.get("oracle_text", "")
        self.power = data.get("power")
        self.toughness = data.get("toughness")
        self.loyalty = data.get("loyalty")
        self.defense = data.get("defense")
        self.colors = data.get("colors", [])
        self.set_name = data.get("set_name", "")
        self.set_code = data.get("set", "")
        self.collector_number = data.get("collector_number", "")
        self.rarity = data.get("rarity", "")
        self.artist = data.get("artist", "")
        self.prices: dict[str, str | None] = data.get("prices", {}) or {}
        self.legalities: dict[str, str] = data.get("legalities", {}) or {}
        self.image_status = data.get("image_status", "")
        self.highres_image = data.get("highres_image", False)
        self.edhrec_rank = data.get("edhrec_rank")
        self.digital = data.get("digital", False)

    # -- names -------------------------------------------------------------

    def get_display_name(self) -> str:
        """Get the appropriate display name for the card."""
        if self.name:
            return self.name
        if self.card_faces:
            return " // ".join(face.name for face in self.card_faces)
        return "Unknown Card"

    def name_keys(self) -> set[str]:
        """Normalized names a user might type for this card (full and per face)."""
        keys = {normalize_name(self.name)}
        keys.update(normalize_name(face.name) for face in self.card_faces)
        return {key for key in keys if key}

    def is_valid_card(self) -> bool:
        """Check if the card has valid data for display."""
        return self.object == "card" and bool(self.name or self.card_faces)

    # -- images ------------------------------------------------------------

    def get_best_image_url(self, prefer_formats: tuple[str, ...] | None = None) -> str:
        """Get the highest quality image URL available for the card's front."""
        image_uris = self.image_uris
        if self.card_faces and self.card_faces[0].image_uris:
            image_uris = self.card_faces[0].image_uris
        return self._pick_image(image_uris, prefer_formats)

    def get_face_image_urls(
        self, prefer_formats: tuple[str, ...] | None = None
    ) -> list[str]:
        """Image URLs for every printed face (two for transform and MDFC cards)."""
        if self.card_faces and all(face.image_uris for face in self.card_faces):
            urls = [
                self._pick_image(face.image_uris, prefer_formats)
                for face in self.card_faces
            ]
            return [url for url in urls if url]
        url = self.get_best_image_url(prefer_formats)
        return [url] if url else []

    def get_art_crop_url(self) -> str:
        """URL of the illustration only, without the card frame."""
        return self.get_best_image_url(("art_crop",))

    @staticmethod
    def _pick_image(
        image_uris: dict[str, str], prefer_formats: tuple[str, ...] | None
    ) -> str:
        if not image_uris:
            return ""
        for format_type in prefer_formats or ("png", "large", "normal", "small"):
            if image_uris.get(format_type):
                return image_uris[format_type]
        return next(iter(image_uris.values()), "")

    def has_image(self) -> bool:
        """Check if the card has at least one image available."""
        return bool(self.get_best_image_url())

    # -- rules text ----------------------------------------------------------

    def get_rules_text(self) -> str:
        """Type line, Oracle text, and stats for every face, as Markdown."""
        faces: list[Card | CardFace] = list(self.card_faces) or [self]
        blocks = []
        for face in faces:
            lines = []
            header = " ".join(part for part in (face.name, face.mana_cost) if part)
            if len(faces) > 1 and header:
                lines.append(f"**{header}**")
            if face.type_line or self.type_line:
                lines.append(f"*{face.type_line or self.type_line}*")
            if face.oracle_text:
                lines.append(face.oracle_text)
            stats = _stats_line(face.power, face.toughness, face.loyalty, face.defense)
            if stats:
                lines.append(f"**{stats}**")
            blocks.append("\n".join(lines))
        return "\n\n".join(block for block in blocks if block)

    # -- prices ------------------------------------------------------------

    def get_price_display(self) -> str:
        """The single most relevant price (USD, then foil, EUR, MTGO tix)."""
        for key, template in (
            ("usd", "${:.2f}"),
            ("usd_foil", "${:.2f} (foil)"),
            ("usd_etched", "${:.2f} (etched)"),
            ("eur", "€{:.2f}"),
            ("tix", "{:.2f} tix"),
        ):
            formatted = _format_money(self.prices.get(key), template)
            if formatted:
                return formatted
        return ""

    def get_price_summary(self) -> str:
        """Every known price for this printing, compactly."""
        parts = []
        for key, template in (
            ("usd", "${:.2f}"),
            ("usd_foil", "${:.2f} foil"),
            ("usd_etched", "${:.2f} etched"),
            ("eur", "€{:.2f}"),
            ("eur_foil", "€{:.2f} foil"),
            ("tix", "{:.2f} tix"),
        ):
            formatted = _format_money(self.prices.get(key), template)
            if formatted:
                parts.append(formatted)
        return " · ".join(parts)

    # -- legality ----------------------------------------------------------

    def get_legality_groups(self) -> dict[str, list[str]]:
        """Format names grouped by status: legal, restricted, banned, not_legal."""
        groups: dict[str, list[str]] = {
            "legal": [],
            "restricted": [],
            "banned": [],
            "not_legal": [],
        }
        for key, label in FORMAT_NAMES.items():
            status = self.legalities.get(key)
            if status in groups:
                groups[status].append(label)
        return groups

    def get_format_legalities(self) -> str:
        """Get a formatted string of format legalities."""
        if not self.legalities:
            return ""
        legal = self.get_legality_groups()["legal"]
        return ", ".join(legal) if legal else "Not legal in any major formats"

    def get_printing_label(self) -> str:
        """Set name, set code, and collector number, e.g. ``Alpha (LEA) #161``."""
        label = self.set_name
        if self.set_code:
            label = f"{label} ({self.set_code.upper()})".strip()
        if self.collector_number:
            label = f"{label} #{self.collector_number}".strip()
        return label


def _stats_line(
    power: str | None, toughness: str | None, loyalty: str | None, defense: str | None
) -> str:
    if power is not None and toughness is not None:
        return f"{power}/{toughness}"
    if loyalty is not None:
        return f"Loyalty: {loyalty}"
    if defense is not None:
        return f"Defense: {defense}"
    return ""


class SearchResult:
    """Represents the result of a card search query."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.object = data.get("object", "")
        self.total_cards = data.get("total_cards", 0)
        self.has_more = data.get("has_more", False)
        self.next_page = data.get("next_page", "")
        self.warnings: list[str] = data.get("warnings", []) or []
        self.data = [Card(card_data) for card_data in data.get("data", [])]


class ScryfallError(Exception):
    """Error response from the Scryfall API."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.object = data.get("object", "")
        self.code = data.get("code", "")
        self.status = data.get("status", 0)
        self.details = data.get("details", "")
        self.type = data.get("type", "")
        self.warnings = data.get("warnings", [])
        super().__init__(f"Scryfall API error: {self.details} (status: {self.status})")

    @property
    def is_ambiguous(self) -> bool:
        return self.type == "ambiguous"

    def get_error_type(self) -> errors.ErrorType:
        """Return the error type for metrics tracking."""
        if self.status == 404:
            return errors.ErrorType.NOT_FOUND
        if self.status == 429:
            return errors.ErrorType.RATE_LIMIT
        if 400 <= self.status < 500:
            return errors.ErrorType.VALIDATION
        return errors.ErrorType.API


def is_ambiguous_error(exc: BaseException) -> bool:
    """True when Scryfall said a fuzzy name matched too many cards."""
    cause = getattr(exc, "cause", None)
    return isinstance(cause, ScryfallError) and cause.is_ambiguous


class _TTLCache:
    """A small LRU cache whose entries expire after a fixed time."""

    def __init__(self, max_entries: int, ttl: float) -> None:
        self.max_entries = max_entries
        self.ttl = ttl
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        if self.max_entries <= 0 or self.ttl <= 0:
            return
        self._entries[key] = (time.monotonic() + self.ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class ScryfallClient:
    """Client for interacting with the Scryfall API.

    Requests are spaced to respect Scryfall's per-endpoint hard limits, an HTTP
    429 pauses all traffic for the lockout window instead of retrying into it,
    and successful lookups are cached because Scryfall only refreshes card data
    and prices once a day.
    """

    BASE_URL = "https://api.scryfall.com"
    USER_AGENT = f"MTGCardBot/{__version__}"
    MAX_RETRIES = 2
    RETRY_BACKOFF = 0.5  # Base delay in seconds; doubles each retry
    CACHE_TTL = 6 * 60 * 60
    CACHE_MAX_ENTRIES = 2000

    def __init__(
        self,
        *,
        cache_ttl: float | None = None,
        cache_max_entries: int | None = None,
    ) -> None:
        self.client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json",
            },
        )
        self.logger = logging.with_component("scryfall")
        self.cache = _TTLCache(
            self.CACHE_MAX_ENTRIES if cache_max_entries is None else cache_max_entries,
            self.CACHE_TTL if cache_ttl is None else cache_ttl,
        )
        self.strict_interval = STRICT_INTERVAL
        self.default_interval = DEFAULT_INTERVAL
        self._schedule_lock = asyncio.Lock()
        self._next_slot: dict[str, float] = {}
        self._blocked_until = 0.0

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    # -- transport -----------------------------------------------------------

    @staticmethod
    def _bucket(endpoint: str) -> str:
        path = endpoint.split("?", 1)[0]
        for prefix in STRICT_ENDPOINTS:
            if path.startswith(prefix):
                return prefix
        return "default"

    def _is_retryable(self, status_code: int) -> bool:
        """Check if an HTTP status code indicates a transient server error."""
        return status_code in (500, 502, 503, 504)

    def _rate_limited_error(self) -> errors.MTGError:
        return errors.create_error(
            errors.ErrorType.RATE_LIMIT,
            "Scryfall asked us to slow down. Please try again in a few seconds.",
        )

    async def _wait_for_slot(self, endpoint: str) -> None:
        """Reserve the next request slot allowed for this endpoint and wait for it."""
        bucket = self._bucket(endpoint)
        interval = (
            self.strict_interval if bucket != "default" else self.default_interval
        )
        async with self._schedule_lock:
            now = time.monotonic()
            if self._blocked_until > now:
                raise self._rate_limited_error()
            start = max(
                now,
                self._next_slot.get(bucket, 0.0),
                self._next_slot.get("default", 0.0),
            )
            self._next_slot[bucket] = start + interval
            self._next_slot["default"] = start + self.default_interval
        delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)

    def _handle_429(self, response: httpx.Response, endpoint: str) -> None:
        retry_after = LOCKOUT_SECONDS
        header = response.headers.get("Retry-After")
        if header:
            with suppress(ValueError):
                retry_after = max(retry_after, float(header))
        self._blocked_until = time.monotonic() + retry_after
        self.logger.error(
            "Scryfall rate limit hit; pausing requests",
            endpoint=endpoint,
            pause_seconds=f"{retry_after:.0f}",
        )

    async def _request(
        self, endpoint: str, *, json_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Make a rate-limited request to the Scryfall API with retries."""
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(self.MAX_RETRIES):
            await self._wait_for_slot(endpoint)
            start_time = time.monotonic()
            if attempt > 0:
                self.logger.debug(
                    "Retrying API request", endpoint=endpoint, attempt=attempt + 1
                )

            try:
                if json_body is None:
                    response = await self.client.get(url)
                else:
                    response = await self.client.post(url, json=json_body)
            except httpx.RequestError as e:
                delay = self.RETRY_BACKOFF * (2**attempt)
                self.logger.warning(
                    "Network error talking to Scryfall",
                    endpoint=endpoint,
                    error=str(e) or type(e).__name__,
                    attempt=attempt + 1,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    continue
                raise errors.create_error(
                    errors.ErrorType.NETWORK, f"Request failed after retries: {e}", e
                ) from e

            if response.status_code == 429:
                self._handle_429(response, endpoint)
                raise self._rate_limited_error()

            if self._is_retryable(response.status_code):
                delay = self.RETRY_BACKOFF * (2**attempt)
                self.logger.warning(
                    "Retryable API error",
                    endpoint=endpoint,
                    status=response.status_code,
                    attempt=attempt + 1,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    continue
                raise errors.create_error(
                    errors.ErrorType.API,
                    "Scryfall API temporarily unavailable. Please try again later.",
                )

            if response.status_code >= 400:
                try:
                    scryfall_error = ScryfallError(response.json())
                except ValueError:
                    raise errors.create_error(
                        errors.ErrorType.API, f"HTTP error {response.status_code}"
                    ) from None
                log = (
                    self.logger.debug
                    if response.status_code == 404
                    else self.logger.warning
                )
                log(
                    "API request failed",
                    endpoint=endpoint,
                    status=response.status_code,
                    error=scryfall_error.details,
                )
                raise errors.create_error(
                    scryfall_error.get_error_type(),
                    scryfall_error.details or "Scryfall API error",
                    scryfall_error,
                ) from scryfall_error

            self.logger.debug(
                "API request successful",
                endpoint=endpoint,
                response_time_ms=f"{(time.monotonic() - start_time) * 1000:.0f}",
            )
            return response

        raise errors.create_error(errors.ErrorType.API, "Request failed")

    async def _get_json(self, endpoint: str, *, cache: bool = True) -> dict[str, Any]:
        if cache:
            cached = self.cache.get(endpoint)
            if cached is not None:
                self.logger.debug("Cache hit", endpoint=endpoint)
                return cached
        response = await self._request(endpoint)
        try:
            data = response.json()
        except ValueError as e:
            raise errors.create_error(
                errors.ErrorType.API, "Scryfall returned an unreadable response", e
            ) from e
        if cache:
            self.cache.set(endpoint, data)
        return data

    # -- card lookups ----------------------------------------------------------

    async def _named(self, mode: str, name: str, set_code: str | None) -> Card:
        if not name:
            raise errors.create_error(
                errors.ErrorType.VALIDATION, "Card name cannot be empty"
            )
        params = {mode: name}
        if set_code:
            params["set"] = set_code
        query = urlencode(params, quote_via=quote)
        card = Card(await self._get_json(f"/cards/named?{query}"))
        self.logger.debug("Retrieved card", card_name=card.name, mode=mode)
        return card

    async def get_card_by_name(self, name: str, set_code: str | None = None) -> Card:
        """Look up a card by name using Scryfall's fuzzy matching."""
        return await self._named("fuzzy", name, set_code)

    async def get_card_by_exact_name(
        self, name: str, set_code: str | None = None
    ) -> Card:
        """Look up a card by its exact name."""
        return await self._named("exact", name, set_code)

    async def get_card_by_set_number(self, set_code: str, number: str) -> Card:
        """Look up one printing by set code and collector number."""
        if not set_code or not number:
            raise errors.create_error(
                errors.ErrorType.VALIDATION, "Set code and collector number required"
            )
        endpoint = f"/cards/{quote(set_code.lower())}/{quote(number.lower())}"
        return Card(await self._get_json(endpoint))

    async def get_cards_by_names(self, names: list[str]) -> dict[str, Card]:
        """Resolve exact card names in bulk through ``/cards/collection``.

        Returns found cards keyed by the normalized names they answer to
        (the full name and each face name). Missing names are simply absent.
        """
        wanted = list(dict.fromkeys(name for name in names if name.strip()))
        found: dict[str, Card] = {}
        uncached: list[str] = []
        for name in wanted:
            cached = self.cache.get(f"collection:{normalize_name(name)}")
            if cached is not None:
                found[normalize_name(name)] = Card(cached)
            else:
                uncached.append(name)

        for start in range(0, len(uncached), COLLECTION_BATCH):
            batch = uncached[start : start + COLLECTION_BATCH]
            response = await self._request(
                "/cards/collection",
                json_body={"identifiers": [{"name": name} for name in batch]},
            )
            data = response.json()
            cards = [(Card(item), item) for item in data.get("data", [])]
            for name in batch:
                key = normalize_name(name)
                for card, raw in cards:
                    if key in card.name_keys():
                        found[key] = card
                        self.cache.set(f"collection:{key}", raw)
                        break
        return found

    def _build_search_endpoint(
        self,
        query: str,
        order: str | None = None,
        direction: str | None = None,
        page: int | None = None,
        unique: str | None = None,
    ) -> str:
        params: dict[str, str] = {"q": query}
        if order:
            params["order"] = order
        if direction:
            params["dir"] = direction
        if unique:
            params["unique"] = unique
        if page is not None:
            params["page"] = str(page)
        return f"/cards/search?{urlencode(params)}"

    async def get_random_card(self, query: str = "") -> Card:
        """Get a random Magic card, optionally filtered by search query."""
        endpoint = (
            f"/cards/random?{urlencode({'q': query})}" if query else "/cards/random"
        )
        card = Card(await self._get_json(endpoint, cache=False))
        self.logger.debug("Retrieved random card", card_name=card.name, query=query)
        return card

    async def search_cards(
        self,
        query: str,
        order: str | None = None,
        direction: str | None = None,
        page: int | None = None,
        unique: str | None = None,
    ) -> SearchResult:
        """Perform a full-text search for cards."""
        if not query:
            raise errors.create_error(
                errors.ErrorType.VALIDATION, "Search query cannot be empty"
            )
        endpoint = self._build_search_endpoint(query, order, direction, page, unique)
        result = SearchResult(await self._get_json(endpoint))
        self.logger.debug(
            "Searched cards", query=query, order=order, results=result.total_cards
        )
        return result

    async def search_card_first(
        self,
        query: str,
        order: str | None = None,
        direction: str | None = None,
        *,
        unique: str | None = None,
    ) -> Card:
        """Search and return one result.

        With an explicit order the top result is returned; without one a random
        card from the first page is picked so repeated queries show variety.
        """
        result = await self.search_cards(query, order, direction, unique=unique)
        if result.total_cards == 0 or not result.data:
            raise errors.create_error(
                errors.ErrorType.NOT_FOUND, "No cards found matching query"
            )
        if order is None and len(result.data) > 1:
            return random.choice(result.data)
        return result.data[0]

    async def get_card_rulings(self, card_id: str) -> list[dict[str, Any]]:
        """Get rulings for a card by its Scryfall ID."""
        if not card_id:
            raise errors.create_error(
                errors.ErrorType.VALIDATION, "Card ID cannot be empty"
            )
        data = await self._get_json(f"/cards/{quote(card_id)}/rulings")
        return data.get("data", [])

    async def autocomplete(self, partial: str) -> list[str]:
        """Return up to 20 card names that start with or contain ``partial``."""
        partial = partial.strip()
        if len(partial) < 2:
            return []
        data = await self._get_json(f"/cards/autocomplete?{urlencode({'q': partial})}")
        return [name for name in data.get("data", []) if isinstance(name, str)]
