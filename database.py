import aiosqlite
from datetime import datetime

DB_PATH = "posts.db"

# ==============================================================================
# 1. ФУНКЦИИ ДЛЯ CHRONO-POSTER (ОТЛОЖЕННЫЙ ПОСТИНГ)
# ==============================================================================

async def init_db():
    """Инициализация таблиц для отложенного постинга"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                text TEXT,
                photo_file_id TEXT,
                buttons TEXT,
                schedule_time TIMESTAMP
            )
        """)
        await db.commit()

async def add_post(user_id, chat_id, text, photo_file_id, buttons, schedule_time):
    """Добавление поста в очередь на отправку"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scheduled_posts (user_id, chat_id, text, photo_file_id, buttons, schedule_time) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, text, photo_file_id, buttons, schedule_time)
        )
        await db.commit()

async def get_due_posts(current_time):
    """Получение постов, время которых настало"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM scheduled_posts WHERE schedule_time <= ?", (current_time,))
        posts = await cursor.fetchall()
        if posts:
            await db.execute("DELETE FROM scheduled_posts WHERE schedule_time <= ?", (current_time,))
            await db.commit()
        return posts

# ==============================================================================
# 2. ФУНКЦИИ ДЛЯ DIGEST LORD (СБОРЩИК КАНАЛОВ)
# ==============================================================================

async def init_digest_db():
    """Инициализация таблиц для дайджестов"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица подписок пользователей на каналы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_username TEXT UNIQUE,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица кэша постов для расчета топов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS post_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_username TEXT,
                message_link TEXT UNIQUE,
                text_preview TEXT,
                has_media INTEGER DEFAULT 0,
                text_length INTEGER DEFAULT 0,
                score REAL GENERATED ALWAYS AS (text_length * 0.5 + has_media * 10) STORED,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def add_subscription(user_id: int, channel_username: str):
    """Добавление канала в отслеживание"""
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
    """Получение списка каналов пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT channel_username FROM channel_subs WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def save_post_data(channel_username: str, link: str, text: str, has_media: bool):
    """Сохранение данных поста в кэш"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO post_cache 
               (channel_username, message_link, text_preview, has_media, text_length) 
               VALUES (?, ?, ?, ?, ?)""",
            (channel_username, link, text[:100], int(has_media), len(text))
        )
        await db.commit()

async def get_top_posts_per_channel(limit: int = 3):
    """Получение топ-N постов из каждого канала за последние 24 часа"""
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
