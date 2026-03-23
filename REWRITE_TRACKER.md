# SQLite to PostgreSQL Rewrite Tracker

## Progress

- [x] Read and understand the full codebase
- [x] Replace aiosqlite with asyncpg in pyproject.toml
- [x] Create mtg_card_bot/db.py with asyncpg connection pool and raw SQL schema
- [x] Add MTG_DATABASE_URL to config.py
- [x] Update .env.example with MTG_DATABASE_URL
- [x] Wire database init and close into __main__.py
- [x] Remove all SQLite references from source and dependencies
- [x] Update README.md to document PostgreSQL requirement and setup
- [x] Regenerate uv.lock (asyncpg 0.31.0 added, aiosqlite 0.21.0 removed)
- [ ] Commit and push to all remotes

## Notes

- aiosqlite was listed as a dependency but was never imported or used anywhere in source code
- All in-memory state (rate limits, duplicate suppression) remains unchanged
- asyncpg is used with raw SQL only -- no ORM, no SQLAlchemy
- Database connection is optional at startup; the bot logs a warning and runs without
  a database if MTG_DATABASE_URL is not set
- Schema: lookup_history table records per-user card lookups for future analytics
