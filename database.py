import aiosqlite
from datetime import datetime

DB_PATH = "posts.db" # Используем ту же базу, что и для отложки

async def init_digest_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_username TEXT UNIQUE, -- username без @
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS post_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_username TEXT,
                message_link TEXT UNIQUE, -- ссылка на пост как уникальный ID
                text_preview TEXT,
                has_media INTEGER DEFAULT 0,
                text_length INTEGER DEFAULT 0,
                score REAL GENERATED ALWAYS AS (text_length * 0.5 + has_media * 10) STORED,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def add_subscription(user_id: int, channel_username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO channel_subs (user_id, channel_username) VALUES (?, ?)",
                (user_id, channel_username)
            )
            await db.commit()
            return True
        except Exception:
            return False

async def get_user_subscriptions(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT channel_username FROM channel_subs WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def save_post_data(channel_username: str, link: str, text: str, has_media: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO post_cache 
               (channel_username, message_link, text_preview, has_media, text_length) 
               VALUES (?, ?, ?, ?, ?)""",
            (channel_username, link, text[:100], int(has_media), len(text))
        )
        await db.commit()

async def get_top_posts_per_channel(limit: int = 3):
    """Топ-N постов из каждого канала за последние 24 часа по скорингу"""
    async with aiosqlite.connect(DB_PATH) as db:
        query = """
            SELECT channel_username, message_link, text_preview, score 
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY channel_username ORDER BY score DESC) as rn
                FROM post_cache
                WHERE fetched_at >= datetime('now', '-1 day')
            ) ranked
            WHERE rn <= ?
            ORDER BY channel_username, score DESC
        """
        cursor = await db.execute(query, (limit,))
        return await cursor.fetchall()
