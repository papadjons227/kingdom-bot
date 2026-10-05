"""Хроно-Бот 2.0 — отложенный постинг + ежедневные дайджесты каналов.

Что нового / оптимизировано:
- Черновки постов хранятся в SQLite, а не в dict в памяти (не теряются при рестарте).
- Парсинг Markdown заменён на HTML.parse_mode: нет падений на спецсимволах и обрезки лимитов.
- Формат времени для /schedule: "HH:MM" (и опционально день), а не два отдельных числа.
- Дайджест собирается из топ-постов тех каналов, на которые подписан конкретный пользователь
  (раньше всем присылался общий топ по всем каналам подряд).
- Уважение Telegram rate-limit: FloodWait обрабатывается с паузой, рассылка с троттлингом.
- health-эндпоинт /healthz со статусом задач.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import config
import database as db
from parser import collect_channels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("chronobot")

TZ = ZoneInfo(config.TIMEZONE)

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

# Время запуска (для healthcheck)
_started_at = datetime.now(TZ)


def now_tz() -> datetime:
    return datetime.now(TZ)


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


# ==============================================================================
# КНОПОЧНОЕ МЕНЮ
# ==============================================================================

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📅 Отложка", callback_data="menu:poster"),
         InlineKeyboardButton(text="📰 Дайджесты", callback_data="menu:digest")],
        [InlineKeyboardButton(text="🕒 Очередь", callback_data="queue"),
         InlineKeyboardButton(text="🧹 Очистить черновик", callback_data="cancel_draft")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


POSTER_TEXT = (
    "<b>⏳ Отложка (хроно-постинг)</b>\n\n"
    "1. Скинь фото (необязательно) → напиши текст поста\n"
    "2. <code>/add_button Текст|https://ссылка</code> — кнопка к посту\n"
    f"3. <code>/schedule HH:MM [@channel_id]</code> — в очередь.\n"
    f"   Сейчас {now_tz().strftime('%H:%M')}, ближайшие слоты: "
    + ", ".join(_fmt_time(now_tz().replace(hour=h, minute=0)) for h in (9, 12, 15, 18, 21))
    + "\n4. <code>/draft</code> — черновик, <code>/cancel</code> — очистить\n"
)

DIGEST_TEXT = (
    "<b>📰 Дайджесты каналов</b>\n\n"
    "<code>/add_channel @username</code> — следить за каналом\n"
    "<code>/del_channel @username</code> — убрать канал\n"
    "<code>/my_channels</code> — твои каналы\n\n"
    f"Каждый день в {config.DIGEST_HOUR:02d}:{config.DIGEST_MINUTE:02d} (Оренбург, UTC+5) "
    "пришлю топ постов по твоим каналам. 🚀\n"
    "Добавь канал прямо сейчас: <code>/add_channel durov</code>"
)

WELCOME_TEXT = (
    "⏳📰 <b>Хроно-Бот 2.0</b> — отложенный постинг и ежедневные дайджесты Telegram-каналов.\n\n"
    "Выбери, что интересно 👇"
)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.upsert_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@dp.callback_query(F.data.startswith("menu:"))
async def cb_menu(callback) -> None:
    section = callback.data.split(":", 1)[1]
    text = {"poster": POSTER_TEXT, "digest": DIGEST_TEXT}.get(section, POSTER_TEXT + "\n" + DIGEST_TEXT)
    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "cancel_draft")
async def cb_cancel_draft(callback) -> None:
    await set_draft(callback.from_user.id, None)
    await callback.answer("🗑 Черновик очищен", show_alert=True)


@dp.callback_query(F.data == "queue")
async def cb_queue(callback) -> None:
    count = await db.count_pending_posts()
    await callback.answer(f"🕒 В очереди: {count} пост(ов)", show_alert=True)


# ==============================================================================
# ЧЕРНОВИКИ (SQLite вместо dict в памяти)
# ==============================================================================

async def get_draft(user_id: int) -> dict:
    cur = await db._db().execute("SELECT data FROM drafts WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    if row:
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            pass
    return {"photo": None, "text": "", "buttons": []}


async def set_draft(user_id: int, draft: dict | None) -> None:
    if draft is None:
        await db._db().execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))
    else:
        await db._db().execute(
            "INSERT INTO drafts (user_id, data) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
            (user_id, json.dumps(draft, ensure_ascii=False)),
        )
    await db._db().commit()


# ==============================================================================
# ФОНОВЫЕ ЗАДАЧИ
# ==============================================================================

def _build_keyboard(buttons_json: str | None) -> InlineKeyboardMarkup | None:
    if not buttons_json:
        return None
    try:
        rows = json.loads(buttons_json)
        keyboard = [
            [InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row]
            for row in rows
            if row
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


async def check_posts_job() -> None:
    """Отправляет посты, у которых наступило время."""
    try:
        posts = await db.get_due_posts(now_tz())
        if not posts:
            return
        log.info("[Poster] К отправке: %d", len(posts))

        for post_id, user_id, chat_id, text, photo_id, buttons_json in posts:
            kb = _build_keyboard(buttons_json)
            try:
                if photo_id:
                    await bot.send_photo(chat_id, photo=photo_id, caption=text, reply_markup=kb)
                else:
                    await bot.send_message(chat_id, text=text, reply_markup=kb)
                await bot.send_message(user_id, f"✅ Пост #{post_id} успешно выкачен в канал!")
                log.info("[Poster] Пост #%s отправлен в %s", post_id, chat_id)
            except Exception as e:
                log.error("[Poster] Ошибка отправки #%s: %s", post_id, e)
                try:
                    await bot.send_message(
                        user_id, f"⚠️ Не удалось отправить пост #{post_id}: {e}"
                    )
                except Exception:
                    pass
    except Exception:
        log.exception("[Poster] Критическая ошибка планировщика")


async def collect_posts_job() -> None:
    """Собирает свежие посты из всех отслеживаемых каналов."""
    log.info("[Digest] Начинаю сбор постов...")
    channels = await db.get_all_channels()
    if not channels:
        log.info("[Digest] Нет отслеживаемых каналов.")
        return
    await collect_channels(channels)
    await db.prune_old_posts(days=7)
    log.info("[Digest] Сбор завершён (%d каналов).", len(channels))


def _format_digest(top_posts: list[tuple], date_str: str) -> str:
    channels: dict[str, list[tuple]] = {}
    for ch_name, link, preview, score in top_posts:
        channels.setdefault(ch_name, []).append((link, preview, int(score)))

    parts = [f"<b>📰 Daily Digest [{date_str}]</b>\n"]
    for ch_name, posts in channels.items():
        parts.append(f"<b>🏆 @{ch_name}</b>")
        for i, (link, preview, score) in enumerate(posts, 1):
            safe_preview = db.escape_html(preview or "(без текста)")
            parts.append(f'{i}. <a href="{link}">{safe_preview}…</a> — 🔥 {score}')
        parts.append("")
    return "\n".join(parts)


async def send_digest_job() -> None:
    """Формирует персональный дайджест каждому пользователю."""
    log.info("[Digest] Формирую дайджесты...")
    users = await db.get_all_users()
    date_str = now_tz().strftime("%d.%m.%Y")
    sent = 0

    for uid in users:
        subs = await db.get_user_subscriptions(uid)
        if not subs:
            continue
        top_posts = await db.get_top_posts_for_channels(subs, limit=5)
        if not top_posts:
            continue
        text = _format_digest(top_posts, date_str)
        # Telegram лимит на сообщение — 4096 символов
        if len(text) > 4000:
            text = text[:3990] + "\n…<i>(продолжение завтра)</i>"
        try:
            await bot.send_message(uid, text, disable_web_page_preview=True)
            sent += 1
        except TelegramForbiddenError:
            log.info("[Digest] Пользователь %s отписался от бота.", uid)
        except TelegramRetryAfter as e:
            log.warning("[Digest] FloodWait: ждём %s с", e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            log.error("[Digest] Ошибка отправки %s: %s", uid, e)
        await asyncio.sleep(0.1)  # троттлинг рассылки против flood-limit

    log.info("[Digest] Дайджесты разосланы: %d/%d.", sent, len(users))


# ==============================================================================
# ХЭНДЛЕРЫ КОМАНД
# ==============================================================================

# --- ДАЙДЖЕСТЫ ---

@dp.message(Command("add_channel"))
async def add_channel_cmd(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используй: <code>/add_channel @username_канала</code>")
        return
    username = re.sub(r"[@\s]", "", args[1]).strip("_")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        await message.answer("❌ Некорректное имя канала. 5–32 символа: буквы/цифры/_, старт с буквы.")
        return

    added = await db.add_subscription(message.from_user.id, username)
    if added:
        await message.answer(
            f"✅ Канал <b>@{username}</b> добавлен! "
            f"Посты собираются каждые {config.COLLECT_INTERVAL_HOURS} ч."
        )
        # Первый сбор сразу, чтобы не ждать расписания
        asyncio.create_task(collect_channels([username]))
    else:
        await message.answer(f"ℹ️ Канал @{username} уже отслеживается.")


@dp.message(Command("del_channel"))
async def del_channel_cmd(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используй: <code>/del_channel @username</code>")
        return
    username = args[1].replace("@", "").strip()
    removed = await db.remove_subscription(message.from_user.id, username)
    await message.answer(
        f"🗑 Канал @{username} убран из отслеживания." if removed
        else f"ℹ️ Канал @{username} не найден в твоих подписках."
    )


@dp.message(Command("my_channels"))
async def my_channels_cmd(message: Message) -> None:
    subs = await db.get_user_subscriptions(message.from_user.id)
    if not subs:
        await message.answer("Пока нет отслеживаемых каналов. Добавь: <code>/add_channel @username</code>")
        return
    lines = "\n".join(f"• <b>@{ch}</b>" for ch in subs)
    await message.answer(f"<b>📡 Твои каналы ({len(subs)}):</b>\n{lines}")


# --- ОТЛОЖКА ---

@dp.message(F.photo)
async def handle_photo(message: Message) -> None:
    draft = await get_draft(message.from_user.id)
    draft["photo"] = message.photo[-1].file_id
    await set_draft(message.from_user.id, draft)
    await message.answer("📸 Фото принято. Теперь напиши текст поста (или /add_button …).")


@dp.message(Command("add_button"))
async def add_button_cmd(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or "|" not in args[1]:
        await message.answer(
            "❌ Формат: <code>/add_button Текст кнопки|https://ссылка</code>"
        )
        return
    label, _, url = args[1].partition("|")
    label, url = label.strip(), url.strip()
    if not label or not url.startswith(("http://", "https://", "tg://")):
        await message.answer("❌ Ссылка должна начинаться с http(s):// или tg://")
        return

    draft = await get_draft(message.from_user.id)
    total_buttons = sum(len(row) for row in draft["buttons"])
    if total_buttons >= config.MAX_BUTTONS:
        await message.answer(f"❌ Максимум {config.MAX_BUTTONS} кнопок.")
        return
    draft["buttons"].append([{"text": label, "url": url}])
    await set_draft(message.from_user.id, draft)
    await message.answer(f"🔗 Кнопка «{db.escape_html(label)}» добавлена (всего: {total_buttons + 1}).")


@dp.message(Command("draft"))
async def draft_cmd(message: Message) -> None:
    d = await get_draft(message.from_user.id)
    if not d["text"] and not d["photo"] and not d["buttons"]:
        await message.answer("Черновика нет. Скинь фото/текст — и он появится здесь.")
        return
    preview = db.escape_html(d["text"])[:200] or "(пусто)"
    info = [
        f"Фото: {'есть 📸' if d['photo'] else 'нет'}",
        f"Текст: {preview}",
        f"Кнопок: {sum(len(row) for row in d['buttons'])}",
    ]
    await message.answer("<b>Текущий черновик:</b>\n" + "\n".join(info))


@dp.message(Command("cancel"))
async def cancel_cmd(message: Message) -> None:
    await set_draft(message.from_user.id, None)
    await message.answer("🗑 Черновик очищен.")


@dp.message(Command("schedule"))
async def schedule_post(message: Message) -> None:
    uid = message.from_user.id
    draft = await get_draft(uid)
    if not draft["text"] and not draft["photo"]:
        await message.answer("❌ Сначала пришли фото и/или текст поста.")
        return

    args = message.text.split()[1:]
    if not args:
        await message.answer("❌ Формат: <code>/schedule HH:MM [@channel]</code>\nПример: <code>/schedule 14:30</code>")
        return

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", args[0])
    if not m:
        await message.answer("❌ Время в формате HH:MM, например <code>14:30</code>.")
        return
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        await message.answer("❌ Часы 0–23, минуты 0–59.")
        return

    chat_id = config.TARGET_CHANNEL_ID
    if len(args) > 1:
        try:
            chat_id = int(args[1])
        except ValueError:
            await message.answer("❌ ID канала должен быть числом, например <code>-1001234567890</code>.")
            return
    if not chat_id:
        await message.answer("❌ Не задан канал: укажи ID после времени или настрой TARGET_CHANNEL_ID.")
        return

    now = now_tz()
    when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)

    buttons_json = json.dumps(draft["buttons"], ensure_ascii=False) if draft["buttons"] else None
    post_id = await db.add_post(
        uid, chat_id, draft["text"], draft["photo"], buttons_json,
        when.strftime("%Y-%m-%d %H:%M:%S"),
    )
    await set_draft(uid, None)
    await message.answer(
        f"⏳ Пост #{post_id} запланирован на "
        f"<b>{when.strftime('%d.%m.%Y %H:%M')}</b> (Оренбург). Жди хроно-импульс! 🚀"
    )


@dp.message(Command("queue"))
async def queue_cmd(message: Message) -> None:
    count = await db.count_pending_posts()
    await message.answer(f"🕒 В очереди на отправку: <b>{count}</b> пост(ов).")


# Общий текст — строго после всех команд
@dp.message(F.text & ~F.command)
async def handle_text(message: Message) -> None:
    uid = message.from_user.id
    draft = await get_draft(uid)
    draft["text"] = message.text
    await set_draft(uid, draft)
    await message.answer("📝 Текст сохранён. Добавь кнопки (/add_button) или пиши /schedule HH:MM.")


# ==============================================================================
# WEB (healthcheck)
# ==============================================================================

async def handle_root(request: web.Request) -> web.Response:
    return web.Response(text="Chrono-Bot 2.0 alive")


async def handle_healthz(request: web.Request) -> web.Response:
    pending = await db.count_pending_posts()
    payload = {
        "status": "ok",
        "uptime_seconds": int((now_tz() - _started_at).total_seconds()),
        "pending_posts": pending,
        "jobs": [j.id for j in scheduler.get_jobs()],
    }
    return web.json_response(payload)


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/healthz", handle_healthz)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    log.info("HTTP-сервер запущен на порту %d", config.PORT)
    return runner


# ==============================================================================
# ЗАПУСК
# ==============================================================================

async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан — установи переменную окружения BOT_TOKEN.")

    await db.init_db()

    scheduler.add_job(check_posts_job, "interval", seconds=config.POSTER_INTERVAL_SEC, id="poster")
    scheduler.add_job(collect_posts_job, "interval", hours=config.COLLECT_INTERVAL_HOURS, id="collector")
    scheduler.add_job(
        send_digest_job,
        CronTrigger(hour=config.DIGEST_HOUR, minute=config.DIGEST_MINUTE, timezone=config.TIMEZONE),
        id="digest",
    )
    scheduler.start()
    log.info("Хроно-Бот 2.0 запущен (TZ=%s)...", config.TIMEZONE)

    # Команды в меню Telegram (автоподсказки)
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="schedule", description="Поставить пост в отложку: /schedule HH:MM"),
            BotCommand(command="draft", description="Показать черновик"),
            BotCommand(command="add_button", description="Кнопка: /add_button Текст|https://ссылка"),
            BotCommand(command="cancel", description="Очистить черновик"),
            BotCommand(command="queue", description="Очередь постов"),
            BotCommand(command="add_channel", description="Следить за каналом: /add_channel @username"),
            BotCommand(command="del_channel", description="Убрать канал из отслеживания"),
            BotCommand(command="my_channels", description="Мои каналы"),
            BotCommand(command="help", description="Помощь"),
        ])
    except Exception as e:
        log.warning("Не удалось установить список команд: %s", e)

    runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
