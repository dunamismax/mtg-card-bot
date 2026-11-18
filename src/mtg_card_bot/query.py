"""Parsing of card lookup requests from Discord messages.

Everything here is pure string handling so it can be tested without Discord or
Scryfall. The bot understands three request shapes:

* bracket lookups anywhere in a message: ``[[Lightning Bolt]]``
* prefix commands: ``!lightning bolt``, ``!rules counterspell``
* semicolon-separated lists: ``!bolt; counterspell; doom blade``

Bracket lookups accept the modifiers players know from other card bots:
``[[!name]]`` image only, ``[[$name]]`` prices, ``[[?name]]`` rulings,
``[[#name]]`` format legality, and ``[[name|SET]]`` or ``[[name|SET|NUMBER]]``
for a specific printing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

MAX_CARDS_PER_MESSAGE = 10

RANDOM_WORDS = frozenset({"random", "rand", "r"})

# Every keyword Scryfall's search syntax accepts (https://scryfall.com/docs/syntax),
# plus ``sort`` and ``direction`` which the bot maps onto order/dir itself.
FILTER_KEYS = frozenset(
    {
        "a",
        "art",
        "artist",
        "artists",
        "atag",
        "arttag",
        "b",
        "banned",
        "block",
        "border",
        "c",
        "cheapest",
        "cmc",
        "cn",
        "color",
        "commander",
        "cube",
        "date",
        "def",
        "defense",
        "devotion",
        "dir",
        "direction",
        "display",
        "e",
        "edition",
        "eur",
        "f",
        "flavor",
        "fo",
        "format",
        "frame",
        "ft",
        "fulloracle",
        "function",
        "game",
        "has",
        "id",
        "identity",
        "illustrations",
        "in",
        "include",
        "is",
        "keyword",
        "kw",
        "lang",
        "language",
        "legal",
        "loy",
        "loyalty",
        "m",
        "mana",
        "manavalue",
        "mv",
        "name",
        "new",
        "not",
        "number",
        "oracleid",
        "o",
        "oracle",
        "oracletag",
        "order",
        "otag",
        "paperprints",
        "papersets",
        "pow",
        "power",
        "powtou",
        "prefer",
        "prints",
        "produces",
        "pt",
        "r",
        "rarity",
        "restricted",
        "s",
        "set",
        "sets",
        "sort",
        "st",
        "stamp",
        "t",
        "tix",
        "tou",
        "toughness",
        "type",
        "unique",
        "usd",
        "wm",
        "watermark",
        "year",
    }
)

_TOKEN_RE = re.compile(r'(?:[^\s"]+|"[^"]*"?)+')
_FILTER_RE = re.compile(r"^[-(]*([a-z]+)(?::|!=|<=|>=|=|<|>)(.+)$", re.IGNORECASE)
_BRACKET_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_SET_CODE_RE = re.compile(r"^[a-z0-9]{2,6}$", re.IGNORECASE)
_COLLECTOR_NUMBER_RE = re.compile(r"^[0-9a-z★†-]{1,10}$", re.IGNORECASE)
_SORT_VALUE_STRIP = "()[]{}.,;'\""


class LookupMode(StrEnum):
    """What the user wants to see for a card."""

    CARD = "card"
    IMAGE = "image"
    PRICES = "prices"
    RULINGS = "rulings"
    LEGALITY = "legality"
    ART = "art"


BRACKET_MODIFIERS = {
    "!": LookupMode.IMAGE,
    "$": LookupMode.PRICES,
    "?": LookupMode.RULINGS,
    "#": LookupMode.LEGALITY,
}


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    """A free-text query split into card-name words and Scryfall filters."""

    name: str
    filters: tuple[str, ...] = ()
    order: str | None = None
    direction: str | None = None

    @property
    def has_filters(self) -> bool:
        return bool(self.filters) or self.order is not None

    @property
    def search_text(self) -> str:
        """The query for Scryfall's search endpoint, without order/dir hints."""
        return " ".join(part for part in (self.name, *self.filters) if part)


@dataclass(frozen=True, slots=True)
class CardRequest:
    """One card the user asked for."""

    query: str
    mode: LookupMode = LookupMode.CARD
    set_code: str | None = None
    collector_number: str | None = None
    random: bool = False
    parsed: ParsedQuery = field(init=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parsed", parse_query(self.query))

    @property
    def label(self) -> str:
        """How to refer to this request in replies."""
        if self.random:
            return f"random {self.query}".strip()
        parts = [self.query]
        if self.set_code:
            parts.append(self.set_code.upper())
        if self.collector_number:
            parts.append(self.collector_number)
        return " | ".join(part for part in parts if part)


def tokenize(query: str) -> list[str]:
    """Split on whitespace while keeping quoted phrases such as ``o:"draw a card"``."""
    return _TOKEN_RE.findall(query)


def is_filter_token(token: str) -> bool:
    """Return True when a token is Scryfall search syntax rather than a name word."""
    if token.startswith('!"'):
        return True  # exact-name syntax: !"Lightning Bolt"
    match = _FILTER_RE.match(token)
    return bool(match and match.group(1).lower() in FILTER_KEYS)


def _clean_sort_value(token: str) -> str:
    return token.split(":", 1)[1].strip().strip(_SORT_VALUE_STRIP).lower()


def parse_query(query: str) -> ParsedQuery:
    """Split a query into name words, filters, and order/dir hints."""
    name_tokens: list[str] = []
    filters: list[str] = []
    order: str | None = None
    direction: str | None = None

    for token in tokenize(query.strip()):
        lower = token.lower()
        if lower.startswith(("order:", "sort:")):
            order = _clean_sort_value(token) or order
            continue
        if lower.startswith(("dir:", "direction:")):
            value = _clean_sort_value(token)
            if value in {"asc", "desc", "auto"}:
                direction = value
            continue
        if is_filter_token(token):
            filters.append(token)
        else:
            name_tokens.append(token)

    return ParsedQuery(" ".join(name_tokens), tuple(filters), order, direction)


def has_filter_parameters(query: str) -> bool:
    """Return True when the query uses Scryfall search syntax."""
    return parse_query(query).has_filters


def parse_card_request(
    text: str, *, mode: LookupMode = LookupMode.CARD, allow_modifiers: bool = False
) -> CardRequest | None:
    """Parse ``name``, ``name|SET`` or ``name|SET|NUMBER`` into a request."""
    text = " ".join(text.split())
    if allow_modifiers and text[:1] in BRACKET_MODIFIERS:
        mode = BRACKET_MODIFIERS[text[0]]
        text = text[1:].strip()

    set_code: str | None = None
    collector_number: str | None = None
    if "|" in text:
        name, *extras = (part.strip() for part in text.split("|"))
        if extras and _SET_CODE_RE.match(extras[0]):
            set_code = extras[0].lower()
            if len(extras) > 1 and _COLLECTOR_NUMBER_RE.match(extras[1]):
                collector_number = extras[1].lower()
        text = name

    words = text.split()
    if words and words[0].lower() in RANDOM_WORDS:
        return CardRequest(" ".join(words[1:]), mode, set_code, random=True)

    if not text and not (set_code and collector_number):
        return None
    return CardRequest(text, mode, set_code, collector_number)


def extract_bracket_requests(content: str) -> list[CardRequest]:
    """Find every ``[[...]]`` lookup outside of code spans, without duplicates."""
    visible = _CODE_RE.sub(" ", content)
    requests: list[CardRequest] = []
    for inner in _BRACKET_RE.findall(visible):
        request = parse_card_request(inner, allow_modifiers=True)
        if request is not None and request not in requests:
            requests.append(request)
    return requests


def split_multi_query(content: str) -> list[str]:
    """Split a semicolon-separated lookup into its non-empty parts."""
    return [part.strip() for part in content.split(";") if part.strip()]


def name_search_query(words: list[str]) -> str:
    """Build a Scryfall query that matches card names containing every word."""
    terms = []
    for word in words:
        cleaned = word.replace('"', "").replace("\\", "")
        if cleaned:
            terms.append(f'name:"{cleaned}"')
    return " ".join(terms)
