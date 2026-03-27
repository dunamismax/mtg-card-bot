# Contributing to MTG Card Bot

## Dev Setup

```bash
git clone https://github.com/dunamismax/mtg-card-bot.git
cd mtg-card-bot

uv python install 3.13
uv sync
uv run pre-commit install
```

## Quality Gates

Every change should pass the local verification flow:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

## Project Layout

```text
src/mtg_card_bot/
  __main__.py
  bot.py
  config.py
  errors.py
  logging.py
  scryfall.py
tests/
  test_bot.py
  test_config.py
  test_errors.py
  test_scryfall.py
manage_bot.py
```

## Notes

- Keep runtime behavior stable unless the change explicitly calls for new bot behavior.
- Prefer focused tests with mocks over live Discord or Scryfall calls.
- Keep packaging and tooling configuration in `pyproject.toml`.
