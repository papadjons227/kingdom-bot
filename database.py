"""Слой работы с SQLite (aiosqlite).

Оптимизации относительно прошлой версии:
- одно общее соединение на весь процесс вместо connect() на каждый запрос;
- WAL-режим и PRAGMA настройки для скорости и надёжности;
- атомарное извлечение постов "на отправку" через DELETE ... RETURNING
  (раньше SELECT + DELETE были гонкой: два прохода планировщика могли
  отправить один пост дважды);
- параметризованные запросы, единый schema, индексы.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

from config import config

log = logging.getLogger(__name__)

DB_PATH = config.DB_PATH

_conn: aiosqlite.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    chat_id       INTEGER NOT NULL,
    text          TEXT    NOT NULL,
    photo_file_id TEXT,
    buttons       TEXT,
    schedule_time TIMESTAMP NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_posts_due ON scheduled_posts (schedule_time);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channel_subs (
    user_id          INTEGER NOT NULL,
    channel_username TEXT    NOT NULL,
    added_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, channel_username)
);

CREATE TABLE IF NOT EXISTS post_cache (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_username TEXT NOT NULL,
    message_link     TEXT NOT NULL UNIQUE,
    text_preview     TEXT,
    has_media        INTEGER NOT NULL DEFAULT 0,
    text_length      INTEGER NOT NULL DEFAULT 0,
    score            REAL GENERATED ALWAYS AS (text_length * 0.5 + has_media * 10) STORED,
    fetched_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cache_fetched ON post_cache (channel_username, fetched_at);

CREATE TABLE IF NOT EXISTS drafts (
    user_id    INTEGER PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    """Открывает общее соединение и применяет схему."""
    global _conn
    _conn = await aiosqlite.connect(DB_PATH)
    await _conn.execute("PRAGMA journal_mode = WAL")
    await _conn.execute("PRAGMA synchronous = NORMAL")
    await _conn.execute("PRAGMA foreign_keys = ON")
    await _conn.executescript(_SCHEMA)
    await _conn.commit()
    log.info("БД инициализирована: %s (WAL)", DB_PATH)


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def _db() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("init_db() не вызвана")
    return _conn


# ==============================================================================
# ХРОНО-ПОСТЕР: отложенные посты
# ==============================================================================

async def add_post(
    user_id: int,
    chat_id: int,
    text: str,
    photo_file_id: str | None,
    buttons_json: str | None,
    schedule_time: datetime | str,
) -> int:
    ts = (
        schedule_time.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(schedule_time, datetime)
        else schedule_time
    )
    cur = await _db().execute(
        "INSERT INTO scheduled_posts (user_id, chat_id, text, photo_file_id, buttons, schedule_time)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, chat_id, text, photo_file_id, buttons_json, ts),
    )
    await _db().commit()
    return cur.lastrowid


async def get_due_posts(now: datetime) -> list[tuple]:
    """Атомарно забирает посты, у которых наступило время отправки.

    DELETE ... RETURNING гарантирует, что пост будет извлечён ровно один раз,
    даже если задача планировщика выполнится параллельно.
    """
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    cur = await _db().execute(
        "DELETE FROM scheduled_posts WHERE schedule_time <= ?"
        " RETURNING id, user_id, chat_id, text, photo_file_id, buttons",
        (ts,),
    )
    rows = await cur.fetchall()
    await _db().commit()
    return rows


async def count_pending_posts() -> int:
    cur = await _db().execute("SELECT COUNT(*) FROM scheduled_posts")
    row = await cur.fetchone()
    return row[0] if row else 0


# ==============================================================================
# ПОЛЬЗОВАТЕЛИ (для дайджеста: слать всем активным, а не только подписчикам)
# ==============================================================================

async def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    await _db().execute(
        "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,"
        " first_name=excluded.first_name",
        (user_id, username, first_name),
    )
    await _db().commit()


async def get_all_users() -> list[int]:
    cur = await _db().execute("SELECT user_id FROM users")
    return [row[0] for row in await cur.fetchall()]


# ==============================================================================
# ДАЙДЖЕСТ: подписки на каналы и топ постов
# ==============================================================================

async def add_subscription(user_id: int, channel_username: str) -> bool:
    """Возвращает True, если подписка реально добавлена, False — если уже была."""
    before = await _db().execute("SELECT 1 FROM channel_subs WHERE user_id=? AND channel_username=?",
                                 (user_id, channel_username))
    if await before.fetchone():
        return False
    await _db().execute(
        "INSERT OR IGNORE INTO channel_subs (user_id, channel_username) VALUES (?, ?)",
        (user_id, channel_username),
    )
    await _db().commit()
    return True


async def remove_subscription(user_id: int, channel_username: str) -> bool:
    cur = await _db().execute(
        "DELETE FROM channel_subs WHERE user_id=? AND channel_username=?",
        (user_id, channel_username),
    )
    await _db().commit()
    return cur.rowcount > 0


async def get_user_subscriptions(user_id: int) -> list[str]:
    cur = await _db().execute(
        "SELECT channel_username FROM channel_subs WHERE user_id = ? ORDER BY added_at",
        (user_id,),
    )
    return [row[0] for row in await cur.fetchall()]


async def get_all_channels() -> list[str]:
    cur = await _db().execute("SELECT DISTINCT channel_username FROM channel_subs")
    return [row[0] for row in await cur.fetchall()]


async def save_post_data(channel_username: str, link: str, text: str, has_media: bool) -> None:
    await _db().execute(
        "INSERT OR REPLACE INTO post_cache"
        " (channel_username, message_link, text_preview, has_media, text_length)"
        " VALUES (?, ?, ?, ?, ?)",
        (channel_username, link, text[:100], int(has_media), len(text)),
    )
    await _db().commit()


async def prune_old_posts(days: int = 7) -> None:
    """Чистит старые записи кэша, чтобы база не разрасталась бесконечно."""
    await _db().execute(
        "DELETE FROM post_cache WHERE fetched_at < datetime('now', ?)", (f"-{days} days",)
    )
    await _db().commit()


def escape_html(text: str) -> str:
    """Экранирует спецсимволы для HTML parse_mode Telegram."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


async def get_top_posts_for_channels(
    channels: list[str], limit: int = 5
) -> list[tuple]:
    """Топ-N постов за сутки по каждой канальной подписке конкретного пользователя."""
    if not channels:
        return []
    placeholders = ",".join("?" * len(channels))
    cur = await _db().execute(
        f"""
        SELECT channel_username, message_link, text_preview, score
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY channel_username ORDER BY score DESC
            ) AS rn
            FROM post_cache
            WHERE fetched_at >= datetime('now', '-1 day')
              AND channel_username IN ({placeholders})
        ) ranked
        WHERE rn <= ?
        ORDER BY channel_username, score DESC
        """,
        (*channels, limit),
    )
    return await cur.fetchall()
