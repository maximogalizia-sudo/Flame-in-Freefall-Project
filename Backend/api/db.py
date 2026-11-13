"""Postgres access: a small connection pool and JSON-safe query helpers."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras
import psycopg2.pool

from .settings import get_settings

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 8, url, options="-c lc_messages=C")
    return _pool


@contextmanager
def connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()  # read-only API: never keep a transaction open
    finally:
        pool.putconn(conn)


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with connection() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [{k: _jsonable(v) for k, v in row.items()} for row in cur.fetchall()]


def query_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def ping() -> bool:
    try:
        return query_one("SELECT 1 AS ok")["ok"] == 1
    except Exception:  # noqa: BLE001 - health check must never raise
        return False
