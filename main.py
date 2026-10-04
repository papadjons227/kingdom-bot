import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from config import config

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("🏰 Цитадель Королевства на связи! Каркас работает.")

async def main():
    bot = Bot(token=config.BOT_TOKEN)
    logging.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())