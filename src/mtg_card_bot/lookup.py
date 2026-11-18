"""Resolve card requests into cards, with layered fallbacks.

A plain name goes through these steps, stopping at the first that finds a card:

1. Scryfall fuzzy name match (handles typos and partial names like ``ragav``).
2. A name search ranked by EDHREC popularity, so an ambiguous name such as
   ``bolt`` returns Lightning Bolt instead of an error.
3. The longest leading run of words that still matches a card name, so chatty
   input like ``generous pup would go good in that`` finds Generous Pup.
4. Otherwise a not-found error carrying "did you mean" suggestions.

Filtered queries (``bolt e:2xm``) use Scryfall search and fall back to the
fuzzy-matched card with the same filters, then to its default printing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from . import errors, logging
from .query import CardRequest, LookupMode, ParsedQuery, name_search_query
from .scryfall import (
    Card,
    ScryfallClient,
    SearchResult,
    is_ambiguous_error,
    normalize_name,
)

RESOLVE_TIMEOUT = 30.0
# Fuzzy matching can land on these; players almost always mean a real card.
NON_GAME_LAYOUTS = frozenset({"token", "double_faced_token", "emblem", "art_series"})
# Sorting by price should consider every printing, not one per card.
PRICE_ORDERS = frozenset({"usd", "eur", "tix"})
MAX_SUGGESTIONS = 5


@dataclass(slots=True)
class Resolution:
    """The outcome of resolving one request."""

    request: CardRequest
    card: Card | None = None
    note: str | None = None
    error: Exception | None = None
    suggestions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.card is not None

    @property
    def not_found(self) -> bool:
        return (
            isinstance(self.error, errors.MTGError)
            and self.error.error_type == errors.ErrorType.NOT_FOUND
        )


def _code(text: str) -> str:
    """Wrap user text in inline code without letting backticks break out."""
    return "`" + text.replace("`", "'") + "`"


def _is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, errors.MTGError) and exc.error_type in (
        errors.ErrorType.NOT_FOUND,
        errors.ErrorType.VALIDATION,
    )


class CardResolver:
    """Turns :class:`CardRequest` objects into Scryfall cards."""

    def __init__(self, client: ScryfallClient) -> None:
        self.client = client
        self.logger = logging.with_component("lookup")

    async def resolve(self, request: CardRequest) -> Resolution:
        """Resolve one request; failures are returned, never raised."""
        try:
            card, note = await asyncio.wait_for(
                self.resolve_card(request), timeout=RESOLVE_TIMEOUT
            )
        except TimeoutError as e:
            self.logger.warning("Card resolution timed out", query=request.label)
            return Resolution(request, error=e)
        except Exception as e:
            return Resolution(
                request, error=e, suggestions=list(getattr(e, "suggestions", []))
            )
        if not card.is_valid_card():
            return Resolution(
                request,
                error=errors.create_error(
                    errors.ErrorType.NOT_FOUND, "No card found for query"
                ),
            )
        return Resolution(request, card, note)

    async def resolve_many(self, requests: list[CardRequest]) -> list[Resolution]:
        """Resolve several requests, batching exact names into one API call."""
        batchable = [
            request
            for request in requests
            if not request.random
            and not request.set_code
            and not request.parsed.has_filters
            and request.parsed.name
        ]
        found: dict[str, Card] = {}
        if len(batchable) >= 2:
            try:
                found = await self.client.get_cards_by_names(
                    [_plain_name(request.parsed.name) for request in batchable]
                )
            except Exception as e:
                self.logger.warning("Batch name lookup failed", error=str(e))

        async def resolve_one(request: CardRequest) -> Resolution:
            if request in batchable:
                card = found.get(normalize_name(_plain_name(request.parsed.name)))
                if card is not None:
                    return Resolution(request, card)
            return await self.resolve(request)

        return list(await asyncio.gather(*(resolve_one(r) for r in requests)))

    async def resolve_card(self, request: CardRequest) -> tuple[Card, str | None]:
        """Resolve one request, raising MTGError when nothing matches."""
        if request.random:
            return await self.client.get_random_card(request.query), None
        if request.set_code and request.collector_number:
            return (
                await self.client.get_card_by_set_number(
                    request.set_code, request.collector_number
                ),
                None,
            )
        parsed = request.parsed
        if parsed.has_filters:
            return await self._resolve_filtered(parsed, request.set_code)
        return await self.resolve_name(parsed.name, request.set_code)

    async def _resolve_filtered(
        self, parsed: ParsedQuery, set_code: str | None
    ) -> tuple[Card, str | None]:
        filters = list(parsed.filters)
        if set_code:
            filters.append(f"e:{set_code}")
        query = " ".join([parsed.name, *filters]).strip()
        unique = "prints" if parsed.order in PRICE_ORDERS else None
        try:
            card = await self.client.search_card_first(
                query, parsed.order, parsed.direction, unique=unique
            )
            return card, None
        except errors.MTGError as e:
            name = _plain_name(parsed.name)
            if not _is_not_found(e) or len(name) < 2:
                raise

        # The name words probably have a typo: find the card, then re-apply
        # the filters to that exact card so the requested printing still wins.
        card, _ = await self.resolve_name(name, None)
        if not filters:
            return card, f"Closest match for {_code(name)}"
        exact = f"oracleid:{card.oracle_id}" if card.oracle_id else f'!"{card.name}"'
        try:
            filtered = await self.client.search_card_first(
                f"{exact} {' '.join(filters)}",
                parsed.order,
                parsed.direction,
                unique=unique,
            )
            return filtered, f"Closest match for {_code(name)}"
        except errors.MTGError as e:
            if not _is_not_found(e):
                raise
        return card, (
            f"No printing of {card.get_display_name()} matches "
            f"{_code(' '.join(filters))} — showing the default printing"
        )

    async def resolve_name(
        self, name: str, set_code: str | None = None
    ) -> tuple[Card, str | None]:
        """Resolve a card name through the fuzzy → search → suggestion chain."""
        name = _plain_name(name)
        if not name:
            raise errors.create_error(
                errors.ErrorType.VALIDATION, "Card name cannot be empty"
            )
        try:
            card = await self.client.get_card_by_name(name, set_code)
        except errors.MTGError as e:
            if not _is_not_found(e):
                raise
            ambiguous = is_ambiguous_error(e)
        else:
            if card.layout in NON_GAME_LAYOUTS:
                return await self._prefer_real_card(name, card), None
            return card, None

        if set_code:
            try:
                card = await self.client.get_card_by_name(name)
            except errors.MTGError as e:
                if not _is_not_found(e):
                    raise
            else:
                return card, (
                    f"No {set_code.upper()} printing of {card.get_display_name()} "
                    "— showing the default printing"
                )

        words = name.split()
        result = await self._name_search(words)
        if result is not None:
            card = result.data[0]
            others = [c.name for c in result.data[1:] if c.name != card.name]
            if ambiguous:
                note = (
                    f"{_code(name)} matches {result.total_cards} cards — "
                    "showing the most played"
                )
            else:
                note = f"Closest match for {_code(name)}"
            if others:
                note += ". Also: " + ", ".join(others[:MAX_SUGGESTIONS])
            return card, note

        best = await self._longest_matching_prefix(words)
        if best is not None:
            return best.data[0], f"Closest match for {_code(name)}"

        suggestions = await self.suggest(name)
        raise errors.create_error(
            errors.ErrorType.NOT_FOUND,
            f"No card found matching “{name}”",
            suggestions=suggestions,
        )

    async def _prefer_real_card(self, name: str, token: Card) -> Card:
        """Swap a fuzzy-matched token (``ragav`` → Ragavan token) for the card.

        Exact token names (``treasure``) keep the token unless a legendary card
        carries that name with an epithet (``Ragavan`` → Ragavan, Nimble Pilferer).
        """
        result = await self._name_search(name.split())
        if result is None:
            return token
        if normalize_name(name) != normalize_name(token.name):
            return result.data[0]
        epithet_prefix = normalize_name(token.name) + ","
        for candidate in result.data:
            if normalize_name(candidate.name).startswith(epithet_prefix):
                return candidate
        return token

    async def _name_search(self, words: list[str]) -> SearchResult | None:
        query = name_search_query(words)
        if not query:
            return None
        try:
            result = await self.client.search_cards(query, order="edhrec")
        except errors.MTGError as e:
            if _is_not_found(e):
                return None
            raise
        return result if result.data else None

    async def _longest_matching_prefix(self, words: list[str]) -> SearchResult | None:
        """Binary-search the longest leading run (>= 2 words) matching a name.

        Matching is monotonic — dropping trailing words only widens the search —
        so a 7-word message needs at most three searches.
        """
        if len(words) < 3:
            return None
        low, high = 2, len(words) - 1
        best: SearchResult | None = None
        while low <= high:
            middle = (low + high) // 2
            result = await self._name_search(words[:middle])
            if result is not None:
                best, low = result, middle + 1
            else:
                high = middle - 1
        return best

    async def suggest(self, name: str) -> list[str]:
        """Card names that look like what the user meant, best first."""
        candidates = [name]
        words = [word for word in name.split() if len(word) >= 3]
        if len(name.split()) > 1 and words:
            candidates.append(max(words, key=len))
        for candidate in candidates:
            try:
                names = await self.client.autocomplete(candidate)
            except Exception as e:
                self.logger.debug("Autocomplete failed", error=str(e))
                return []
            # Art series entries ("X // X") duplicate the real card name
            names = [
                n for n in names if len(set(n.split(" // "))) == len(n.split(" // "))
            ]
            if names:
                return names[:MAX_SUGGESTIONS]
        return []

    async def cheapest_printing(self, card: Card) -> Card | None:
        """The paper printing of this card with the lowest USD price."""
        exact = f"oracleid:{card.oracle_id}" if card.oracle_id else f'!"{card.name}"'
        try:
            result = await self.client.search_cards(
                f"{exact} usd>0", order="usd", direction="asc", unique="prints"
            )
        except errors.MTGError as e:
            if _is_not_found(e):
                return None
            raise
        return result.data[0] if result.data else None


def _plain_name(name: str) -> str:
    return " ".join(name.replace('"', " ").split())


def comma_split_requests(request: CardRequest) -> list[CardRequest]:
    """Split ``Ore Gorger, Skyfire Kirin`` into two lookups (empty if n/a)."""
    if (
        request.mode is not LookupMode.CARD
        or request.random
        or request.parsed.has_filters
        or "," not in request.query
    ):
        return []
    parts = [part.strip() for part in request.query.split(",") if part.strip()]
    if len(parts) < 2:
        return []
    return [CardRequest(part, request.mode) for part in parts]
