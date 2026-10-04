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

from config import config
from database import init_db, add_post, get_due_posts

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow") # Поставь свой часовой пояс!

# Временное хранилище черновиков (в памяти)
user_drafts = {}

# --- Фоновая задача: проверка постов каждую минуту ---
async def check_posts_job():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    posts = await get_due_posts(now)
    
    for post in posts:
        _, user_id, chat_id, text, photo_id, buttons_json, _ = post
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
        except Exception as e:
            logging.error(f"Ошибка отправки поста: {e}")
            await bot.send_message(user_id, f"❌ Ошибка при отправке поста: {e}")

# --- Хэндлеры ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👻 **Chrono-Poster активирован.**\n\n"
        "Как пользоваться:\n"
        "1. Отправь фото (опционально)\n"
        "2. Напиши текст поста\n"
        "3. Добавь кнопки через /add_button [Текст] [Ссылка]\n"
        "4. Запланируй через /schedule [Часы] [Минуты] (например: /schedule 14 30)"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid = message.from_user.id
    user_drafts[uid] = {
        "photo": message.photo[-1].file_id,
        "text": "",
        "buttons": []
    }
    await message.answer("📸 Фото принято. Теперь напиши текст для поста.")

@dp.message(F.text & ~F.command)
async def handle_text(message: Message):
    uid = message.from_user.id
    if uid not in user_drafts:
        user_drafts[uid] = {"photo": None, "text": "", "buttons": []}
    
    user_drafts[uid]["text"] = message.text
    await message.answer(" Текст сохранен. Можешь добавить кнопки или сразу планировать (/schedule).")

@dp.message(Command("add_button"))
async def add_button_cmd(message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Используй: /add_button [Текст кнопки] [Ссылка]")
        return
    
    uid = message.from_user.id
    if uid not in user_drafts:
        user_drafts[uid] = {"photo": None, "text": "", "buttons": []}
        
    btn_text = args[1]
    btn_url = args[2]
    
    # Добавляем кнопку в первый ряд (для простоты)
    user_drafts[uid]["buttons"].append({"text": btn_text, "url": btn_url})
    await message.answer(f"🔗 Кнопка '{btn_text}' добавлена.")

@dp.message(Command("schedule"))
async def schedule_post(message: Message):
    uid = message.from_user.id
    
    # Проверяем, есть ли черновик
    if uid not in user_drafts or not user_drafts[uid]["text"]:
        await message.answer("❌ Сначала создай пост (скинь фото + напиши текст).")
        return

    args = message.text.split()
    # Проверяем, что ввели время (должно быть 3 слова: /schedule ЧАС МИНУТА)
    if len(args) < 3:
        await message.answer("❌ Используй формат: /schedule [Часы] [Минуты]\nПример: /schedule 14 30")
        return

    try:
        h, m = int(args[1]), int(args[2])
        now = datetime.now()
        schedule_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        
        # Если время уже прошло сегодня, ставим на завтра
        if schedule_time <= now:
            schedule_time += timedelta(days=1) 
            
        draft = user_drafts[uid]
        # Превращаем кнопки в JSON для базы данных
        buttons_json = json.dumps([draft["buttons"]]) if draft["buttons"] else None
        
        # ID канала берем из конфига
        TARGET_CHANNEL_ID = config.TARGET_CHANNEL_ID 
        
        await add_post(
            uid, 
            TARGET_CHANNEL_ID, 
            draft["text"], 
            draft["photo"], 
            buttons_json, 
            schedule_time.strftime("%Y-%m-%d %H:%M:%S")
        )
        
        await message.answer(f"⏳ Пост успешно запланирован на {schedule_time.strftime('%H:%M')}! Жди хроно-импульс.")
        del user_drafts[uid] # Очищаем черновик после планирования
        
    except ValueError:
        await message.answer("❌ Ошибка: часы и минуты должны быть числами.")

# --- Серверная заглушка для Render ---
async def handle_ping(request):
    return web.Response(text="Chrono-Poster Alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- Главный запуск ---
async def main():
    await init_db()
    scheduler.add_job(check_posts_job, "interval", minutes=1)
    scheduler.start()
    logging.info("Chrono-Poster запущен...")
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
