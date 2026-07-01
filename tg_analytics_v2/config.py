"""
config.py — единая точка чтения конфигурации из .env.

Все модули проекта (main.py, snapshot.py, historical.py, stories.py, report.py)
импортируют настройки отсюда вместо того чтобы парсить .env самостоятельно.
Это устраняет дублирование и гарантирует, что все части системы видят
одни и те же значения.
"""

import os
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()

# ── Telegram API ────────────────────────────────────────────────────────
API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_analytics")

# ── Каналы ──────────────────────────────────────────────────────────────
CHANNELS_RAW = os.getenv("CHANNELS", "")
CHANNELS     = [c.strip() for c in CHANNELS_RAW.split(",") if c.strip()]


def _parse_ids(key: str) -> list[int]:
    """Парсит список Telegram ID из .env через запятую."""
    raw = os.getenv(key, "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


# ── Получатели и режим отладки ─────────────────────────────────────────
RECIPIENT_IDS = _parse_ids("REPORT_RECIPIENT_ID")
DEBUG_IDS     = _parse_ids("DEBUG_RECIPIENT_ID")
MODERATOR_IDS = _parse_ids("MODERATOR_IDS")
DEBUG_MODE    = os.getenv("DEBUG", "false").lower() == "true"
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "12:00")

# ── Часовой пояс ────────────────────────────────────────────────────────
TIMEZONE_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
TZ            = pytz.timezone(TIMEZONE_NAME)

# ── Папки ───────────────────────────────────────────────────────────────
OUTPUT_DIR   = Path(os.getenv("OUTPUT_DIR", "output"))
REGISTRY_DIR = Path(os.getenv("REGISTRY_DIR", "registry"))
ARCHIVE_DIR  = Path(os.getenv("ARCHIVE_DIR", "archive"))
LOGS_DIR     = Path(os.getenv("LOGS_DIR", "logs"))

for _d in (OUTPUT_DIR/"daily", OUTPUT_DIR/"weekly", OUTPUT_DIR/"monthly",
           REGISTRY_DIR, ARCHIVE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Веса CQI (Content Quality Index) ───────────────────────────────────
CQI_W = {
    "react":   int(os.getenv("CQI_W_REACT",   "1")),
    "vote":    int(os.getenv("CQI_W_VOTE",     "2")),
    "forward": int(os.getenv("CQI_W_FORWARD",  "4")),
    "comment": int(os.getenv("CQI_W_COMMENT",  "5")),
}

# ── Прокси (опционально) ────────────────────────────────────────────────
PROXY_CFG = None
if os.getenv("PROXY_TYPE"):
    import socks
    _proxy_types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    PROXY_CFG = (
        _proxy_types.get(os.getenv("PROXY_TYPE", "socks5").lower(), socks.SOCKS5),
        os.getenv("PROXY_HOST"),
        int(os.getenv("PROXY_PORT", "1080")),
        True,
        os.getenv("PROXY_USERNAME") or None,
        os.getenv("PROXY_PASSWORD") or None,
    )


def get_telethon_kwargs() -> dict:
    """Возвращает kwargs для TelegramClient (прокси, если настроен)."""
    return {"proxy": PROXY_CFG} if PROXY_CFG else {}
