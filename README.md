# MTG Card Bot

Discord bot for fast Magic: The Gathering card lookups with live pricing, legality, rulings, and embed-first responses powered by the Scryfall API.

## What It Does

- Card lookups anywhere in a message with `[[Lightning Bolt]]`, prefix commands
  like `!lightning bolt`, or slash commands (`/card`) with live name autocomplete
- Typo-tolerant matching that still answers when Scryfall's fuzzy match gives
  up: ambiguous names (`bolt`) return the most-played match, chatty input
  (`generous pup would go good in that`) finds the card, comma lists split into
  several cards, and true misses get "did you mean" suggestions
- Prices (with the cheapest paper printing), format legality including bans
  and restrictions, official rulings, art crops, and specific printings by set
  code or collector number
- Double-faced cards show both faces; multi-card lookups return one composite
  image of up to 10 cards
- Replies thread to the request, editing a message re-runs a fixed lookup, and
  per-user burst limits keep chats friendly
- Respects Scryfall's per-endpoint rate limits, backs off on HTTP 429, batches
  multi-card lookups into a single request, and caches responses

## Stack

| Component | Choice |
| --- | --- |
| Language | Python 3.13 |
| Package manager | `uv` |
| Discord client | `discord.py` |
| HTTP client | `httpx` |
| Packaging | `pyproject.toml` + Hatchling |
| Lint / format | Ruff |
| Type checking | Pyright |
| Tests | pytest |

## Quick Start

### Requirements

- [uv](https://docs.astral.sh/uv/) installed locally
- Python 3.13 available through `uv python`
- A Discord bot token with message content intent enabled

### Install

```bash
git clone https://github.com/dunamismax/mtg-card-bot.git
cd mtg-card-bot

uv python install 3.13
uv sync

cp .env.example .env
# edit .env and set MTG_DISCORD_TOKEN
```

### Run

```bash
# direct package entrypoint
uv run mtg-card-bot

# or use the local manager helper
uv run python manage_bot.py start
```

## Configuration

Set environment variables in `.env` or in the process environment:

| Variable | Description | Default |
| --- | --- | --- |
| `MTG_DISCORD_TOKEN` | Discord bot token | required |
| `MTG_COMMAND_PREFIX` | Command prefix | `!` |
| `MTG_LOG_LEVEL` | `debug`, `info`, `warning`, `error` | `info` |
| `MTG_JSON_LOGGING` | Emit JSON logs | `false` |
| `MTG_COMMAND_COOLDOWN` | Seconds per lookup once a user's burst is spent (`0` disables) | `2.0` |
| `MTG_COMMAND_BURST` | Lookups a user can make back to back | `3` |
| `MTG_CACHE_TTL` | Seconds to cache Scryfall responses (`0` disables) | `21600` |
| `MTG_SYNC_COMMANDS` | Register slash commands at startup | `true` |

Slash commands appear in servers where the bot was invited with the
`applications.commands` scope. Message lookups need the Message Content intent.

## Usage

### Looking up cards

- `[[Lightning Bolt]]` anywhere in a message, or several: `[[Bolt]] [[Counterspell]]`
- `!lightning bolt`, or a list: `!bolt; counterspell; doom blade` (up to 10 cards)
- `/card`, `/price`, `/rules`, `/legality`, `/art`, `/random`, `/help`
- `!random` or `!random t:dragon r:mythic` for a random card
- `!help` (aliases `!h`, `!?`) for the in-Discord guide

### Choosing what to show

| Bracket | Prefix | Shows |
| --- | --- | --- |
| `[[name]]` | `!name` | Card image, mana cost, prices, legality |
| `[[!name]]` | `!image name` | Card image only |
| `[[$name]]` | `!price name` | Prices for the printing and the cheapest paper printing |
| `[[?name]]` | `!rules name` | Official rulings |
| `[[#name]]` | `!legal name` | Legal, restricted, banned, and not-legal formats |
| | `!art name` | The illustration without the frame |

### Printings and search syntax

- `[[Sol Ring|LEA]]` picks a set; `[[Sol Ring|LEA|270]]` picks a collector number
- Any Scryfall search filter works, including `e:who`, `type:legendary`,
  `mana>={5}` and `usd<1`
- `order:` and `dir:` rank results; price orders such as `order:usd dir:desc`
  compare every printing
- Unordered searches pick a random match from the first page for variety

## Development

```bash
uv sync
uv run pre-commit install
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The same four quality gates run in CI. The test suite uses mocks and fake Discord/Scryfall interactions so local verification stays fast and deterministic.

## Project Layout

```text
src/mtg_card_bot/
  __main__.py   - package entrypoint
  bot.py        - Discord client, message routing, slash commands
  config.py     - environment loading and config validation
  embeds.py     - Discord embed builders
  errors.py     - bot-specific error types
  grid.py       - composite images for multi-card and double-faced lookups
  logging.py    - structured logging wrapper
  lookup.py     - card resolution with typo and ambiguity fallbacks
  query.py      - parsing of brackets, modifiers, and Scryfall syntax
  scryfall.py   - rate-limited, caching Scryfall client and card models
tests/
  test_*.py     - focused tests per module, all offline
manage_bot.py   - local process manager for start/stop/status/logs
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and repo conventions.

## Deployment Notes

The [Ubuntu VM runbook](deploy/README.md) covers pinned releases and
verification for `mtg-card-bot.service`. For a container build, install `uv`,
copy the repo, run `uv sync --locked`, and start the package entrypoint.

## License

MIT. See [LICENSE](LICENSE).

## Production verification and maintenance

CI retains Ruff, Pyright and pytest and now uses `uv sync --frozen`, locked
advisory scanning and weekly verification. uv lock and GitHub Action updates
are reviewed and made directly on `main`; no CI job deploys them. Runtime dependencies and the
supported Python version remain unchanged. Test in a clean source export with a
fresh venv: historical ignored packages at the checkout root can shadow src and
invalidate coverage. The production service loads credentials through systemd
and its effective dedicated-user drop-in; inspect `systemctl show` and drop-ins
before making service changes. Host resource and encrypted-backup procedures are
owned by ubuntu-vm-backup. Never use the live token in CI or synthetic tests.
