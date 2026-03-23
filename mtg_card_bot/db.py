"""PostgreSQL connection pool management using asyncpg with raw SQL."""

from __future__ import annotations

from typing import Any

import asyncpg


_pool: asyncpg.Pool[asyncpg.Record] | None = None


async def create_pool(dsn: str) -> asyncpg.Pool[asyncpg.Record]:
    """Initialize the global connection pool from a DSN.

    Call once at startup before any database operations.
    """
    global _pool
    pool: asyncpg.Pool[asyncpg.Record] | None = await asyncpg.create_pool(dsn=dsn)
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    _pool = pool
    return _pool


async def close_pool() -> None:
    """Close the global connection pool gracefully."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool[asyncpg.Record]:
    """Return the active connection pool.

    Raises RuntimeError if create_pool has not been called.
    """
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call create_pool first.")
    return _pool


def is_connected() -> bool:
    """Return True if the pool has been initialized."""
    return _pool is not None


async def init_schema() -> None:
    """Create tables if they do not already exist.

    Schema:
      lookup_history -- one row per card lookup, used for analytics and history.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lookup_history (
                id           BIGSERIAL    PRIMARY KEY,
                guild_id     BIGINT       NOT NULL,
                user_id      BIGINT       NOT NULL,
                card_name    TEXT         NOT NULL,
                looked_up_at TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS lookup_history_user_idx
                ON lookup_history (user_id, looked_up_at DESC)
            """
        )


async def record_lookup(guild_id: int, user_id: int, card_name: str) -> None:
    """Insert one row into lookup_history.

    No-op if the pool is not initialized (bot running without a database).
    """
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO lookup_history (guild_id, user_id, card_name)
            VALUES ($1, $2, $3)
            """,
            guild_id,
            user_id,
            card_name,
        )


async def fetch_recent_lookups(
    user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the most recent card lookups for a user across all guilds.

    Returns an empty list if the pool is not initialized.
    """
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, card_name, looked_up_at
            FROM lookup_history
            WHERE user_id = $1
            ORDER BY looked_up_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
    return [dict(row) for row in rows]
