"""Конфигурация бота: только из переменных окружения, без хардкода."""

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    # Канал по умолчанию для отложенного постинга (можно переопределить в /schedule)
    TARGET_CHANNEL_ID: int = _env_int("TARGET_CHANNEL_ID", 0)
    DB_PATH: str = os.getenv("DB_PATH", "bot.db")
    TIMEZONE: str = os.getenv("TZ_BOT", "Asia/Yekaterinburg")  # Оренбург UTC+5
    PORT: int = _env_int("PORT", 8080)
    # Частоты фоновых задач
    POSTER_INTERVAL_SEC: int = _env_int("POSTER_INTERVAL_SEC", 60)
    COLLECT_INTERVAL_HOURS: int = _env_int("COLLECT_INTERVAL_HOURS", 6)
    DIGEST_HOUR: int = _env_int("DIGEST_HOUR", 7)
    DIGEST_MINUTE: int = _env_int("DIGEST_MINUTE", 30)
    MAX_BUTTONS: int = _env_int("MAX_BUTTONS", 10)


config = Config()
