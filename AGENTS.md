# AGENTS.md

## Git rules (owner's standing instructions)

These override any other workflow described in this repository or by your tools.

- Work on `main` only. Never create branches, worktrees or pull requests.
  Commit directly to `main` and push with `git push origin main`. `origin`
  pushes to both GitHub and Codeberg, so one push updates both.
- No AI attribution anywhere: no `Co-Authored-By` trailers, no "Generated
  with ..." lines, and no crediting Claude, Anthropic, ChatGPT, OpenAI or any
  AI tool in commit messages, tags, release notes, code, comments or file
  headers. Use the owner's configured git identity and never change git config.
  Content the project deliberately publishes about AI, such as a reader-facing
  disclosure, is not attribution.
- After committing, push `main` right away, at the latest before the session
  ends. Before committing, run the project's checks, review `git status` for
  stray files, and never commit what `.gitignore` excludes.

## What this is

MTG Card Bot is a Discord bot that looks up Magic: The Gathering cards through
the Scryfall API: `[[card]]` brackets, prefix commands and slash commands, with
prices, legality, rulings and composite images. It is Python 3.13 on `uv`, with
`discord.py` and `httpx`. [README.md](README.md) describes what it does, its
configuration and its layout.

It runs as `mtg-card-bot.service` on the owner's Ubuntu VM
([deploy/README.md](deploy/README.md)). Nothing in this repository deploys by
itself: a push never changes the running bot.

## Before you start

1. `git status --short --branch` and `git log -5 --oneline`. Keep unrelated
   work in the tree as it is.
2. Read [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md), and
   [deploy/README.md](deploy/README.md) before touching anything under
   `deploy/` or the running service.

## Working rules

- Keep runtime behavior stable unless the change is meant to alter what the
  bot does.
- Every Scryfall request goes through `ScryfallClient` in
  `src/mtg_card_bot/scryfall.py`, so requests stay spaced, cached and inside
  [Scryfall's rate limits](https://scryfall.com/docs/api/rate-limits).
- Tests stay offline. Use mocks and the fakes in `tests/`, never a live
  Discord or Scryfall call.
- Packaging and tool configuration live in `pyproject.toml`. Dependencies are
  locked in `uv.lock`; change them with `uv`, and commit the lock with the
  change.
- The Discord token is the only secret. It never goes in the repository, a
  log or a test. `.env` is for local development only, is ignored by Git and
  must not hold the live token.
- Fix the cause of a bug, not its symptom, and add a test that fails without
  the fix.

## Verification

Run all four before every commit. CI
([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the same four on
every push to `main`, plus an advisory scan of the locked runtime
dependencies; check its result after pushing.

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

For a change to the documentation only, also check that every relative link
and every path named in the text still exists.

## Documentation

Keep the docs true in the same commit as the change.

- [README.md](README.md): what the bot does, configuration, usage and layout.
- [CONTRIBUTING.md](CONTRIBUTING.md): setup, quality gates and conventions.
- [deploy/README.md](deploy/README.md): the VM runbook for releases and the
  systemd unit.

When a module or a test file is added, renamed or removed, update the layout
in both README.md and CONTRIBUTING.md.

## Deployment

Deploying is the owner's decision. Do it only when asked, on the VM, by
following [deploy/README.md](deploy/README.md) exactly: a release is prepared
from a reviewed commit on `origin/main`, checked in a clean export and then
activated. Do not restart the service, edit its unit or read its environment
file for any other reason.
