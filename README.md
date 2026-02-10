# MTG Card Bot

Discord bot for fast Magic: The Gathering lookups with Scryfall-powered card data, rulings, legality, and price details.

![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![Package Manager](https://img.shields.io/badge/Package%20Manager-uv-informational)
![API](https://img.shields.io/badge/Data-Scryfall-green)
![License](https://img.shields.io/badge/License-MIT-brightgreen)

## Quick Start

### Prerequisites

- Python 3.12+ (`pyproject.toml` requires `>=3.12`)
- [uv](https://docs.astral.sh/uv/) package manager
- Discord bot token with Message Content intent enabled

### Run Locally

```bash
git clone https://github.com/dunamismax/mtg-card-bot.git
cd mtg-card-bot
cp .env.example .env
# set MTG_DISCORD_TOKEN in .env
uv sync
uv run python manage_bot.py start
```

Expected result: startup logs stream in the terminal and the bot appears online in Discord.

## Features

- Fuzzy card lookup via prefix commands or bracket syntax (`[[Card Name]]`).
- Rules lookup for official rulings (`rules <card>`).
- Filtered random card lookup with Scryfall query support.
- Multi-card resolution with semicolon-separated queries in one message.
- Per-user command cooldown and duplicate-command suppression.
- Rich embeds with pricing, legality summaries, and card imagery.

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Runtime | Python 3.12+ | Bot runtime |
| Discord Client | [`discord.py`](https://discordpy.readthedocs.io/) | Discord event handling and messaging |
| Card Data API | [Scryfall API](https://scryfall.com/docs/api) | Card search, random pulls, rulings |
| HTTP | [`httpx`](https://www.python-httpx.org/) | Async API and image requests |
| Config/Validation | Environment-based config (`mtg_card_bot/config.py`) | Runtime behavior and secrets |
| Tooling | `uv`, Ruff, MyPy | Dependency, lint/format, and type-check workflows |

## Project Structure

```text
mtg-card-bot/
├── mtg_card_bot/
│   ├── __main__.py              # Main runtime entrypoint
│   ├── bot.py                   # Discord event and command handling
│   ├── scryfall.py              # Scryfall API client and card models
│   ├── config.py                # Environment config loading/validation
│   ├── logging.py               # Structured logging setup
│   └── errors.py                # Error types and wrappers
├── manage_bot.py                # Start/stop/status/log management script
├── pyproject.toml               # Project metadata and tooling config
├── .env.example                 # Required/optional env vars
├── uv.lock
└── README.md
```

## Development Workflow and Common Commands

```bash
# Install/update dependencies
uv sync

# Start bot with manager
uv run python manage_bot.py start

# Bot process management
uv run python manage_bot.py status
uv run python manage_bot.py stop
uv run python manage_bot.py restart
uv run python manage_bot.py logs

# Code quality
uv run ruff format .
uv run ruff check .
uv run mypy mtg_card_bot
```

## Deployment and Operations

Configuration via `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `MTG_DISCORD_TOKEN` | none | Required Discord bot token |
| `MTG_COMMAND_PREFIX` | `!` | Command prefix |
| `MTG_LOG_LEVEL` | `info` | Log verbosity |
| `MTG_JSON_LOGGING` | `false` | Structured JSON logging |
| `MTG_COMMAND_COOLDOWN` | `2.0` | Per-user cooldown (seconds) |

Operational notes:

- Scryfall client enforces a `0.1s` minimum interval between requests (10 req/s max).
- `manage_bot.py` supports graceful stop/restart and process cleanup commands.
- For production hosting, run the start command under a process manager/service wrapper.

## Security and Reliability Notes

- Never commit real bot tokens; keep secrets in `.env` or secure host environment variables.
- Duplicate message suppression prevents repeated handling of rapid duplicate events.
- Network calls use explicit HTTP timeouts to avoid indefinite hangs.
- Current repo does not include automated test files; verify behavior in a controlled Discord server before production use.

## Documentation

| Path | Purpose |
|---|---|
| [pyproject.toml](pyproject.toml) | Dependency and tooling configuration |
| [manage_bot.py](manage_bot.py) | Process manager commands |
| [mtg_card_bot/bot.py](mtg_card_bot/bot.py) | Command handling and embed behavior |
| [mtg_card_bot/scryfall.py](mtg_card_bot/scryfall.py) | Scryfall client and rate limiting |
| [mtg_card_bot/config.py](mtg_card_bot/config.py) | Env config defaults and validation |

## Contributing

Open an issue or pull request with reproducible command examples, expected behavior, and any API/error logs needed to validate the change.

## License

Licensed under the [MIT License](LICENSE).
