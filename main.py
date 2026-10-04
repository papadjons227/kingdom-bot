import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Импорты для БД и парсера
from config import config
from database import init_db, add_post, get_due_posts
from database import init_digest_db, add_subscription, get_user_subscriptions, get_top_posts_per_channel
from parser import parse_channel_posts

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
# Часовой пояс Оренбурга (UTC+5)
scheduler = AsyncIOScheduler(timezone="Asia/Yekaterinburg") 

# --- ХРАНИЛИЩЕ ДЛЯ ОТЛОЖКИ ---
user_drafts = {}

# ==============================================================================
# 1. ЛОГИКА CHRONO-POSTER (ОТЛОЖЕННЫЙ ПОСТИНГ)
# ==============================================================================

async def check_posts_job():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f" [Scheduler] Проверка постов. Текущее время сервера: {now}")
    
    try:
        posts = await get_due_posts(now)
        logging.info(f" [Scheduler] Найдено постов для отправки: {len(posts)}")
        
        if not posts:
            return

        for post in posts:
            _, user_id, chat_id, text, photo_id, buttons_json, _ = post
            logging.info(f"🚀 [Scheduler] Отправляю пост в канал {chat_id}")
            
            try:
                kb = None
                if buttons_json:
                    buttons_data = json.loads(buttons_json)
                    keyboard = []
                    for row in buttons_data:
                        btn_row = []
                        for btn in row:
                            btn_row.append(InlineKeyboardButton(text=btn['text'], url=btn['url']))
                        keyboard.append(btn_row)
                    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

                if photo_id:
                    await bot.send_photo(chat_id, photo=photo_id, caption=text, reply_markup=kb)
                else:
                    await bot.send_message(chat_id, text=text, reply_markup=kb)
                
                await bot.send_message(user_id, f"✅ Пост успешно выкачен в канал!")
                logging.info(f"✅ [Scheduler] Пост отправлен!")
            except Exception as e:
                logging.error(f" [Scheduler] Ошибка отправки: {e}")
                await bot.send_message(user_id, f" Ошибка при отправке поста: {e}")
                
    except Exception as e:
        logging.error(f"💀 [Scheduler] Критическая ошибка планировщика: {e}")

# ==============================================================================
# 2. ЛОГИКА DIGEST LORD (СБОРЩИК КАНАЛОВ БЕЗ АДМИНКИ)
# ==============================================================================

async def collect_posts_job():
    logging.info("🕷️ [Digest] Начинаю сбор постов из каналов...")
    # Получаем ВСЕ уникальные каналы из БД
    async with __import__('aiosqlite').connect(__import__('database').DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT channel_username FROM channel_subs")
        channels = [row[0] for row in await cursor.fetchall()]
    
    for ch in channels:
        await parse_channel_posts(ch)
        await asyncio.sleep(2) # Пауза чтобы не забанили
    
    logging.info("✅ [Digest] Сбор постов завершен.")

async def send_digest_job():
    logging.info(" [Digest] Формирую ежедневный дайджест...")
    # ИЗМЕНЕНИЕ: Теперь берем ТОП-10 постов вместо 3
    top_posts = await get_top_posts_per_channel(limit=10)
    
    if not top_posts:
        logging.info("️ [Digest] Нет данных для дайджеста.")
        return
        
    digest_text = f"** DAILY DIGEST [{datetime.now().strftime('%d.%m.%Y')}]**\n\n"
    channels_dict = {}
    for ch_name, link, preview, score in top_posts:
        if ch_name not in channels_dict:
            channels_dict[ch_name] = []
        channels_dict[ch_name].append((link, preview, int(score)))
    
    for ch_name, posts in channels_dict.items():
        digest_text += f"**🏆 @{ch_name}**\n"
        for i, (link, preview, score) in enumerate(posts, 1):
            digest_text += f"{i}. [{preview}...]({link}) — 💀 {score} очков\n"
        digest_text += "\n"
    
    # Отправляем всем подписчикам
    async with __import__('aiosqlite').connect(__import__('database').DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT user_id FROM channel_subs")
        users = [row[0] for row in await cursor.fetchall()]
    
    sent_count = 0
    for uid in users:
        try:
            await bot.send_message(uid, digest_text, parse_mode="Markdown")
            sent_count += 1
        except Exception as e:
            logging.error(f"Не удалось отправить дайджест пользователю {uid}: {e}")
    
    logging.info(f"✅ [Digest] Дайджест разослан {sent_count} пользователям!")


# ==============================================================================
# ХЭНДЛЕРЫ (СТРОГИЙ ПОРЯДОК: КОМАНДЫ ВЫШЕ ОБЩЕГО ТЕКСТА!)
# ==============================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👻 **Chrono-Poster + Digest Lord активированы.**\n\n"
        "**Отложка:**\n"
        "1. Скинь фото → напиши текст\n"
        "2. /add_button [Текст] [Ссылка]\n"
        "3. /schedule [Часы] [Минуты]\n\n"
        "**Дайджесты:**\n"
        "/add_channel @username — добавить канал\n"
        "/my_channels — список каналов\n"
        "Топ-10 постов выкатываются каждый день в 07:30 по Оренбургу."
    )

# --- КОМАНДЫ ДАЙДЖЕСТОВ (ИДУТ ДО ОБЩЕГО ТЕКСТА) ---
@dp.message(Command("add_channel"))
async def add_channel_cmd(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используй: /add_channel @username_канала")
        return
    
    username = args[1].replace("@", "").strip()
    success = await add_subscription(message.from_user.id, username)
    
    if success:
        await message.answer(f"✅ Канал **@{username}** добавлен!\nБот будет собирать топы раз в сутки.", parse_mode="Markdown")
    else:
        await message.answer("️ Этот канал уже отслеживается.")

@dp.message(Command("my_channels"))
async def my_channels_cmd(message: Message):
    subs = await get_user_subscriptions(message.from_user.id)
    if not subs:
        await message.answer("Нет отслеживаемых каналов. Используй /add_channel @username")
        return
    
    # Экранируем спецсимволы Markdown для безопасности
    def escape_md(text: str) -> str:
        return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")
    
    text = "** Твои каналы:**\n\n"
    for ch in subs:
        # Экранируем username, чтобы подчеркивания не ломали разметку
        safe_ch = escape_md(ch)
        text += f"• @{safe_ch}\n"
        
    try:
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        # Если Markdown всё равно глючит, отправляем без форматирования
        logging.warning(f"Ошибка Markdown в /my_channels: {e}")
        await message.answer(text.replace("**", "").replace("\\_", "_"), parse_mode=None)

# --- КОМАНДЫ ОТЛОЖКИ (ТОЖЕ ДО ОБЩЕГО ТЕКСТА) ---
@dp.message(F.photo)
async def handle_photo(message: Message):
    uid = message.from_user.id
    user_drafts[uid] = {"photo": message.photo[-1].file_id, "text": "", "buttons": []}
    await message.answer("📸 Фото принято. Теперь напиши текст для поста.")

@dp.message(Command("add_button"))
async def add_button_cmd(message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Используй: /add_button [Текст кнопки] [Ссылка]")
        return
    
    uid = message.from_user.id
    if uid not in user_drafts:
        user_drafts[uid] = {"photo": None, "text": "", "buttons": []}
        
    user_drafts[uid]["buttons"].append({"text": args[1], "url": args[2]})
    await message.answer(f"🔗 Кнопка '{args[1]}' добавлена.")

@dp.message(Command("schedule"))
async def schedule_post(message: Message):
    uid = message.from_user.id
    if uid not in user_drafts or not user_drafts[uid]["text"]:
        await message.answer("❌ Сначала создай пост (скинь фото + напиши текст).")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Формат: /schedule [Часы] [Минуты]\nПример: /schedule 14 30")
        return

    try:
        h, m = int(args[1]), int(args[2])
        now = datetime.now()
        schedule_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if schedule_time <= now:
            schedule_time += timedelta(days=1) 
            
        draft = user_drafts[uid]
        buttons_json = json.dumps([draft["buttons"]]) if draft["buttons"] else None
        
        await add_post(uid, config.TARGET_CHANNEL_ID, draft["text"], draft["photo"], buttons_json, schedule_time.strftime("%Y-%m-%d %H:%M:%S"))
        await message.answer(f" Пост запланирован на {schedule_time.strftime('%H:%M')}! Жди хроно-импульс.")
        del user_drafts[uid]
    except ValueError:
        await message.answer("❌ Часы и минуты должны быть числами.")

# ️ ВАЖНО: ОБЩИЙ ТЕКСТ ИДЕТ СТРОГО ПОСЛЕДНИМ
@dp.message(F.text & ~F.command)
async def handle_text(message: Message):
    uid = message.from_user.id
    if uid not in user_drafts:
        user_drafts[uid] = {"photo": None, "text": "", "buttons": []}
    user_drafts[uid]["text"] = message.text
    await message.answer("📝 Текст сохранен. Добавь кнопки или пиши /schedule.")


# ==============================================================================
# СЕРВЕРНАЯ ЗАГЛУШКА И ЗАПУСК
# ==============================================================================

async def handle_ping(request):
    return web.Response(text="Chrono-Poster + Digest Lord Alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await init_db()           # Для Chrono-Poster
    await init_digest_db()    # Для Digest Lord
    
    # Задачи Chrono-Poster
    scheduler.add_job(check_posts_job, "interval", minutes=1)
    
    # Задачи Digest Lord
    scheduler.add_job(collect_posts_job, "interval", hours=6) 
    # ИЗМЕНЕНИЕ: Выкатка теперь в 07:30 по Оренбургу
    scheduler.add_job(send_digest_job, CronTrigger(hour=7, minute=30, timezone="Asia/Yekaterinburg")) 
    
    scheduler.start()
    logging.info("Chrono-Poster + Digest Lord запущены (Оренбург UTC+5)...")
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
