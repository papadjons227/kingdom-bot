"""Парсер публичных Telegram-каналов через веб-превью t.me/s/<channel>.

Оптимизации:
- одна переиспользуемая aiohttp-сессия (пул соединений) вместо новой на каждый канал;
- параллельный сбор сразу по всем каналам с ограничением одновременности;
- таймауты и корректная обработка ошибок.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from bs4 import BeautifulSoup

from database import save_post_data

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_SEMAPHORE_LIMIT = 4  # не более 4 каналов одновременно


def _extract_posts(html: str, take_last: int = 10) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for post in soup.find_all("div", class_="tgme_widget_message")[-take_last:]:
        link_tag = post.find("a", class_="tgme_widget_message_date")
        if not link_tag or "href" not in link_tag.attrs:
            continue
        text_tag = post.find("div", class_="tgme_widget_message_text")
        text = text_tag.get_text(strip=True) if text_tag else ""
        has_media = bool(
            post.find("a", class_="tgme_widget_message_photo_wrap")
            or post.find("video")
            or post.find("audio")
            or post.find("a", class_="tgme_widget_message_video")
        )
        results.append({"link": link_tag["href"], "text": text, "has_media": has_media})
    return results


async def parse_channel_posts(session: aiohttp.ClientSession, channel_username: str) -> int:
    """Парсит последние посты канала и сохраняет их в БД. Возвращает число сохранённых."""
    url = f"https://t.me/s/{channel_username}"
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                log.warning("Парсинг %s: HTTP %s", channel_username, resp.status)
                return 0
            html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Парсинг %s: сетевая ошибка: %s", channel_username, e)
        return 0

    saved = 0
    for p in _extract_posts(html):
        await save_post_data(channel_username, p["link"], p["text"], p["has_media"])
        saved += 1
    log.info("Канал @%s: собрано постов: %d", channel_username, saved)
    return saved


async def collect_channels(channels: list[str]) -> None:
    """Собирает посты по списку каналов параллельно, но без перегруза t.me."""
    if not channels:
        return
    sem = asyncio.Semaphore(_SEMAPHORE_LIMIT)

    async def worker(session: aiohttp.ClientSession, ch: str) -> None:
        async with sem:
            await parse_channel_posts(session, ch)
            await asyncio.sleep(1)  # вежливая пауза между запросами

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        await asyncio.gather(*(worker(session, ch) for ch in channels))
