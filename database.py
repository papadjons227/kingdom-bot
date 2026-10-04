import aiosqlite
import os

DB_PATH = "posts.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                text TEXT,
                photo_file_id TEXT,
                buttons TEXT, -- Храним кнопки как JSON строку
                schedule_time TIMESTAMP
            )
        """)
        await db.commit()

async def add_post(user_id, chat_id, text, photo_file_id, buttons, schedule_time):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scheduled_posts (user_id, chat_id, text, photo_file_id, buttons, schedule_time) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, text, photo_file_id, buttons, schedule_time)
        )
        await db.commit()

async def get_due_posts(current_time):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM scheduled_posts WHERE schedule_time <= ?", (current_time,))
        posts = await cursor.fetchall()
        # Удаляем отправленные посты
        await db.execute("DELETE FROM scheduled_posts WHERE schedule_time <= ?", (current_time,))
        await db.commit()
        return posts
