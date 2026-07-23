"""Postgres access layer (async, psycopg 3 + pool). All SQL lives here."""
from __future__ import annotations

import json

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None

# One statement per list element — psycopg's extended protocol won't run
# multiple statements in a single execute().
_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS carts (
        group_id   BIGINT PRIMARY KEY,
        status     TEXT NOT NULL DEFAULT 'open',   -- open | building
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cart_items (
        id                  BIGSERIAL PRIMARY KEY,
        group_id            BIGINT NOT NULL REFERENCES carts(group_id),
        raw_text            TEXT NOT NULL,
        resolved_product_id TEXT,
        resolved_name       TEXT,
        qty                 INT NOT NULL DEFAULT 1,
        status              TEXT NOT NULL DEFAULT 'pending',
                            -- pending | resolved | added | not_found | ambiguous | error
        candidates          JSONB,
        added_by            TEXT,
        deleted             BOOLEAN NOT NULL DEFAULT FALSE,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aliases (
        group_id     BIGINT NOT NULL,
        phrase       TEXT NOT NULL,
        product_id   TEXT NOT NULL,
        product_name TEXT,
        hits         INT NOT NULL DEFAULT 1,
        PRIMARY KEY (group_id, phrase)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_group_status ON cart_items (group_id, status) WHERE deleted = FALSE",
]


async def init(dsn):
    global _pool
    _pool = AsyncConnectionPool(
        dsn,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    await _pool.open()
    async with _pool.connection() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)


async def close():
    if _pool is not None:
        await _pool.close()


# ---------- carts ----------

async def get_or_create_cart(group_id):
    async with _pool.connection() as conn:
        await conn.execute(
            "INSERT INTO carts (group_id) VALUES (%s) ON CONFLICT (group_id) DO NOTHING",
            (group_id,),
        )


async def set_cart_status(group_id, status):
    async with _pool.connection() as conn:
        await conn.execute(
            "UPDATE carts SET status = %s, updated_at = now() WHERE group_id = %s",
            (status, group_id),
        )


# ---------- items ----------

async def add_item(group_id, raw_text, qty=1, added_by=None):
    await get_or_create_cart(group_id)
    async with _pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO cart_items (group_id, raw_text, qty, added_by) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (group_id, raw_text, qty, added_by),
        )
        row = await cur.fetchone()
        return row["id"]


async def list_items(group_id, statuses=None, include_deleted=False):
    query = "SELECT * FROM cart_items WHERE group_id = %s"
    params = [group_id]
    if not include_deleted:
        query += " AND deleted = FALSE"
    if statuses:
        query += " AND status = ANY(%s)"
        params.append(list(statuses))
    query += " ORDER BY created_at"
    async with _pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchall()


async def get_pending_items(group_id):
    return await list_items(group_id, statuses=["pending"])


async def get_active_items(group_id):
    """Every non-deleted item — the full list to rebuild the cart from."""
    return await list_items(group_id)


async def get_item(item_id):
    async with _pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM cart_items WHERE id = %s", (item_id,))
        return await cur.fetchone()


async def remove_items(group_id, text):
    """Soft-delete every active item whose raw_text contains `text`."""
    async with _pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE cart_items SET deleted = TRUE "
            "WHERE group_id = %s AND deleted = FALSE AND raw_text ILIKE %s RETURNING id",
            (group_id, f"%{text}%"),
        )
        return len(await cur.fetchall())


async def clear_cart(group_id):
    async with _pool.connection() as conn:
        await conn.execute(
            "UPDATE cart_items SET deleted = TRUE WHERE group_id = %s AND deleted = FALSE",
            (group_id,),
        )
        await conn.execute(
            "UPDATE carts SET status = 'open', updated_at = now() WHERE group_id = %s",
            (group_id,),
        )


async def set_item_status(item_id, status, product_id=None, product_name=None, candidates=None):
    async with _pool.connection() as conn:
        await conn.execute(
            "UPDATE cart_items SET status = %s, "
            "resolved_product_id = COALESCE(%s, resolved_product_id), "
            "resolved_name = COALESCE(%s, resolved_name), "
            "candidates = %s WHERE id = %s",
            (
                status,
                product_id,
                product_name,
                json.dumps(candidates) if candidates is not None else None,
                item_id,
            ),
        )


async def get_next_ambiguous(group_id):
    """Oldest unresolved ambiguous item (the one whose 1/2/3 prompt is live)."""
    async with _pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM cart_items "
            "WHERE group_id = %s AND deleted = FALSE AND status = 'ambiguous' "
            "ORDER BY created_at LIMIT 1",
            (group_id,),
        )
        return await cur.fetchone()


# ---------- aliases ----------

async def get_alias(group_id, phrase):
    async with _pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM aliases WHERE group_id = %s AND phrase = %s",
            (group_id, phrase),
        )
        return await cur.fetchone()


async def upsert_alias(group_id, phrase, product_id, product_name):
    async with _pool.connection() as conn:
        await conn.execute(
            "INSERT INTO aliases (group_id, phrase, product_id, product_name, hits) "
            "VALUES (%s, %s, %s, %s, 1) "
            "ON CONFLICT (group_id, phrase) DO UPDATE SET "
            "product_id = EXCLUDED.product_id, "
            "product_name = EXCLUDED.product_name, "
            "hits = aliases.hits + 1",
            (group_id, phrase, product_id, product_name),
        )
