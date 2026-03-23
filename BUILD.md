# BUILD

This file tracks the development phases for the MTG Card Bot, a Python Discord bot that performs
Magic: The Gathering card lookups against the Scryfall API.

## Stack

| Concern | Choice |
| --- | --- |
| Language | Python 3.13 |
| Package manager | uv |
| Formatter | ruff format |
| Linter | ruff check |
| Type checker | mypy (strict) |
| Tests | pytest + pytest-asyncio |
| HTTP client | httpx (async) |
| Discord client | discord.py |
| Logging | stdlib logging with structured wrapper |
| Config | environment variables via os.getenv |

## Phase 1: Bootstrap

- [x] Initialize project with pyproject.toml and uv
- [x] Pin Python 3.13 via uv python
- [x] Add runtime dependencies: discord.py, httpx
- [x] Add dev dependencies: ruff, mypy, pytest, pytest-asyncio
- [x] Configure ruff (line length, rule selection, ignore list)
- [x] Configure mypy strict mode
- [x] Configure pytest (asyncio_mode, testpaths)
- [x] Add package entrypoint via project.scripts
- [x] Write LICENSE (Apache 2.0)
- [x] Write README.md with quick start and configuration table

## Phase 2: Core API Integration

- [x] Implement ScryfallClient with rate limiting (100 ms between requests)
- [x] Add Card and CardFace data models parsed from Scryfall JSON
- [x] Add SearchResult model for paginated search responses
- [x] Implement get_card_by_name (fuzzy), get_card_by_exact_name, get_random_card
- [x] Implement search_cards and search_card_first with order/dir parameters
- [x] Implement get_card_rulings
- [x] Map ScryfallError HTTP status codes to MTGError / ErrorType
- [x] Add MTGConfig with all MTG_* environment variables
- [x] Add load_env_file helper for .env support
- [x] Add structured logging wrapper (with_component, Logger)

## Phase 3: Discord Bot and Features

- [x] Implement MTGCardBot extending discord.Client
- [x] Add message content intent and on_message dispatch
- [x] Support command prefix (default: !) and bracket syntax ([[card name]])
- [x] Implement card lookup command with filter and sort parameter extraction
- [x] Implement random card command with optional Scryfall filter query
- [x] Implement rules lookup command using get_card_rulings
- [x] Implement help command with embed showing all commands and examples
- [x] Implement multi-card lookup via semicolon-separated queries
- [x] Send card grid with image attachments capped at 4 per message
- [x] Add per-user rate limiting via _user_rate_limits dict
- [x] Add duplicate suppression via _recent_commands and _processed_message_ids
- [x] Background cleanup task for duplicate suppression state
- [x] Graceful shutdown with SIGTERM/SIGINT handling and asyncio.Event
- [x] Implement manage_bot.py CLI manager (start, stop, restart, status, logs, kill)
- [x] Wire MTG_COMMAND_COOLDOWN into per-user rate limiter
- [x] Fix filtered random to use /cards/random?q=... endpoint
- [x] Fix multi-card image attachments to skip oversized PNG format

## Phase 4: Tech Stack Alignment

Audit findings from 2026-03-23. The items below correct dead dependencies, a Python version
mismatch, missing test infrastructure, and type annotation gaps identified by strict mypy.

- [ ] Remove `pydantic-settings` from runtime dependencies (config.py uses plain os.getenv; the
      dependency is never imported)
- [ ] Remove `structlog` from runtime dependencies (logging.py wraps stdlib logging and never
      imports structlog)
- [ ] Remove `aiosqlite` from runtime dependencies (no database layer exists in the project)
- [ ] Remove `pillow` from runtime dependencies (image bytes are piped directly to discord.File
      without any image processing)
- [ ] Run `uv sync` after removing dead deps and verify the lockfile updates cleanly
- [ ] Align Python version to 3.13 across pyproject.toml: set `requires-python = ">=3.13"`,
      `target-version = "py313"` in ruff, and `python_version = "3.13"` in mypy
- [ ] Fix Optional annotation in errors.py: change `cause: Exception = None` to
      `cause: Exception | None = None` in both MTGError.__init__ and create_error (lines 22 and 29)
- [ ] Create `tests/` directory with at least one test file covering MTGConfig validation,
      MTGError construction, and a mocked ScryfallClient request path
- [ ] Add `pytest-cov` to dev dependencies and set `addopts = "--cov=mtg_card_bot"` in
      pytest.ini_options
- [ ] Add `pip-audit` to dev dependencies for dependency vulnerability scanning
- [ ] Add `.env.example` with all documented environment variables (README references
      `cp .env.example .env` but the file does not exist in the repo)
- [ ] Add a `Makefile` with targets: `fmt` (ruff format), `lint` (ruff check), `typecheck` (mypy),
      `test` (pytest), `audit` (pip-audit), and `all` running them in sequence
- [ ] If durable state is introduced in a future phase, use PostgreSQL with asyncpg (no ORM);
      do not add asyncpg or aiosqlite until there is a concrete schema to migrate
