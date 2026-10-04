import os
import re
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import config
from database import init_db, add_or_update_item, get_user_items, get_all_items, remove_item
from wb_parser import get_wb_product_info
from ozon_parser import get_ozon_product_info, extract_ozon_id

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Samara")

# --- Фоновая проверка цен (каждые 30 мин) ---
async def check_prices_job():
    items = await get_all_items()
    for item in items:
        if item.platform == "wb":
            data = await get_wb_product_info(item.article)
            buy_url = f"https://www.wildberries.ru/catalog/{item.article}/detail.aspx"
        else:
            data = await get_ozon_product_info(item.article)
            buy_url = f"https://www.ozon.ru/product/{item.article}"

        if not data:
            continue
        
        new_price = data["price"]
        old_price = item.current_price
        
        if new_price < old_price:
            diff = old_price - new_price
            percent = (diff / old_price) * 100
            platform_name = "Wildberries" if item.platform == "wb" else "Ozon"
            text = (
                f"🚨 **ЦЕНА УПАЛА [{platform_name}]!**\n\n"
                f"📦 **{item.title}**\n"
                f"Было: ~{old_price:,.0f} ₽~\n"
                f"Стало: **{new_price:,.0f} ₽**\n"
                f"Скидка: **-{diff:,.0f} ₽** (-{percent:.1f}%)\n\n"
                f"[Перейти к товару]({buy_url})"
            )
            try:
                await bot.send_message(item.user_id, text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка отправки алерта {item.user_id}: {e}")
        
        await add_or_update_item(item.user_id, item.article, data["title"], new_price, item.platform)

# --- Хэндлеры бота ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "🏰 **Око Рынка активировано.**\n\n"
        "Отправь мне:\n"
        "• Артикул или ссылку **Wildberries**\n"
        "• Ссылку на товар **Ozon**\n\n"
        "Команды:\n"
        "• `/tracked` — список твоих товаров на контроле",
        parse_mode="Markdown"
    )

@dp.message(Command("tracked"))
async def cmd_tracked(message: Message):
    items = await get_user_items(message.from_user.id)
    if not items:
        await message.answer("У тебя пока нет товаров на слежке.")
        return

    for item in items:
        is_wb = item.platform == "wb"
        url = f"https://www.wildberries.ru/catalog/{item.article}/detail.aspx" if is_wb else f"https://www.ozon.ru/product/{item.article}"
        badge = "🟣 WB" if is_wb else "🔵 Ozon"

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{item.platform}_{item.article}"),
            InlineKeyboardButton(text="🔗 Купить", url=url)
        ]])
        await message.answer(
            f"{badge} **{item.title}**\n"
            f"ID/Артикул: `{item.article}`\n"
            f"Зафиксированная цена: **{item.current_price:,.0f} ₽**",
            reply_markup=kb,
            parse_mode="Markdown"
        )

@dp.callback_query(F.data.startswith("del_"))
async def handle_delete(call: CallbackQuery):
    parts = call.data.split("_")
    platform = parts[1]
    article = int(parts[2])
    await remove_item(call.from_user.id, article, platform)
    await call.message.edit_text("❌ Товар удален из отслеживания.")
    await call.answer()

@dp.message(F.text)
async def handle_product_link(message: Message):
    text = message.text.strip()
    status_msg = await message.answer("🔍 Распознаю товар и проверяю цену...")

    # 1. Проверяем Ozon
    if "ozon.ru" in text:
        ozon_id = extract_ozon_id(text)
        if not ozon_id:
            await status_msg.edit_text("❌ Не удалось найти ID товара Ozon в ссылке.")
            return

        data = await get_ozon_product_info(ozon_id)
        if not data:
            await status_msg.edit_text("❌ Шлюз Ozon не ответил. Возможно, товар раскуплен или ссылка некорректная.")
            return

        await add_or_update_item(message.from_user.id, ozon_id, data["title"], data["price"], "ozon")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔗 Открыть Ozon", url=f"https://www.ozon.ru/product/{ozon_id}")
        ]])
        await status_msg.edit_text(
            f"🎯 **Товар Ozon взят на слежку!**\n\n"
            f"📦 **{data['title']}**\n"
            f"SKU: `{ozon_id}`\n"
            f"Текущая цена: **{data['price']:,.0f} ₽**",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    # 2. Проверяем Wildberries
    wb_match = re.search(r"(\d{7,11})", text)
    if wb_match:
        article = int(wb_match.group(1))
        data = await get_wb_product_info(article)
        if not data:
            await status_msg.edit_text("❌ Товар на Wildberries не найден.")
            return

        await add_or_update_item(message.from_user.id, article, data["title"], data["price"], "wb")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔗 Открыть WB", url=f"https://www.wildberries.ru/catalog/{article}/detail.aspx")
        ]])
        await status_msg.edit_text(
            f"🎯 **Товар WB взят на слежку!**\n\n"
            f"📦 **{data['title']}**\n"
            f"Артикул: `{article}`\n"
            f"Текущая цена: **{data['price']:,.0f} ₽**",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    await status_msg.edit_text("Не понял ссылку. Отправь ссылку на Ozon или артикул/ссылку Wildberries.")

# --- Серверная заглушка для Render ---
async def handle_ping(request):
    return web.Response(text="Market Eye Alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- Главный запуск ---
async def main():
    await init_db()
    scheduler.add_job(check_prices_job, "interval", minutes=30)
    scheduler.start()
    logging.info("Око Рынка запущено (WB + Ozon)...")
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())