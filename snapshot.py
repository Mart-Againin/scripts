"""
snapshot.py — почасовой сборщик статистики постов.

Логика:
  • Каждый час сканирует каналы на новые посты → регистрирует их
  • По зарегистрированным постам у которых now >= deadline (publish_time + 24ч)
    снимает финальный срез статистики и закрывает пост (is_final=True)
  • Данные хранятся в registry/<channel>/registry.json
  • Финальные данные за закрытый месяц переносятся в archive/ после
    генерации месячного отчёта (через report.py)

Запуск вручную:    python snapshot.py
Через планировщик: каждый час (см. README.md)
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto, MessageMediaPoll,
    DocumentAttributeVideo, DocumentAttributeAnimated,
)

load_dotenv()

# ── Конфиг ────────────────────────────────────────────────────────────────
API_ID        = int(os.getenv("API_ID", "0"))
API_HASH      = os.getenv("API_HASH", "")
SESSION_NAME  = os.getenv("SESSION_NAME", "tg_analytics")
CHANNELS_RAW  = os.getenv("CHANNELS", "")
REGISTRY_DIR  = Path(os.getenv("REGISTRY_DIR", "registry"))
LOGS_DIR      = Path(os.getenv("LOGS_DIR", "logs"))
DEBUG         = os.getenv("DEBUG", "false").lower() == "true"
TZ            = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))

PROXY_CFG = None
if os.getenv("PROXY_TYPE"):
    import socks
    _pt = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    PROXY_CFG = (
        _pt.get(os.getenv("PROXY_TYPE","socks5").lower(), socks.SOCKS5),
        os.getenv("PROXY_HOST"), int(os.getenv("PROXY_PORT","1080")),
        True,
        os.getenv("PROXY_USERNAME") or None,
        os.getenv("PROXY_PASSWORD") or None,
    )

LOGS_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "snapshot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── Вспомогательные функции ───────────────────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def detect_content_type(msg) -> str:
    if msg.media is None:
        return "Текст" if msg.message else "Пустой"
    if isinstance(msg.media, MessageMediaPoll):
        return "Опрос"
    if isinstance(msg.media, MessageMediaPhoto):
        return "Фото"
    if isinstance(msg.media, MessageMediaDocument):
        for attr in msg.media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return "Видео"
            if isinstance(attr, DocumentAttributeAnimated):
                return "GIF"
        return "Документ"
    if getattr(msg, "web_preview", None):
        return "Ссылка"
    return "Другое"


def extract_poll_votes(msg) -> int:
    if not isinstance(msg.media, MessageMediaPoll):
        return 0
    results = msg.media.results
    if not results or not results.results:
        return 0
    return sum(r.voters for r in results.results if r.voters)


def collect_msg_stats(msg) -> dict:
    reactions = 0
    if msg.reactions and msg.reactions.results:
        reactions = sum(r.count for r in msg.reactions.results)
    comments  = msg.replies.replies if msg.replies else 0
    forwards  = msg.forwards or 0
    votes     = extract_poll_votes(msg)
    views     = msg.views or 0
    actions   = views + reactions + comments + forwards + votes
    return {
        "views": views, "reactions": reactions,
        "comments": comments, "forwards": forwards,
        "votes": votes, "actions": actions,
    }


# ── Реестр ────────────────────────────────────────────────────────────────

def registry_path(channel_username: str) -> Path:
    ch = channel_username.lstrip("@")
    p = REGISTRY_DIR / ch
    p.mkdir(parents=True, exist_ok=True)
    return p / "registry.json"


def load_registry(channel_username: str) -> dict:
    path = registry_path(channel_username)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"Ошибка чтения реестра {path}: {e}")
    return {"channel_id": channel_username, "posts": {}}


def save_registry(channel_username: str, data: dict):
    path = registry_path(channel_username)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.debug(f"Реестр сохранён: {path}")


# ── Основная логика ───────────────────────────────────────────────────────

async def process_channel(client, channel_id: str):
    log.info(f"Обработка канала: {channel_id}")

    try:
        entity = await client.get_entity(channel_id)
    except Exception as e:
        log.error(f"Не удалось получить сущность {channel_id}: {e}")
        return

    username      = getattr(entity, "username", None) or str(entity.id)
    title         = getattr(entity, "title", username)
    subscribers   = getattr(entity, "participants_count", 0) or 0
    now           = now_utc()

    registry = load_registry(username)
    registry["channel_title"]    = title
    registry["channel_numeric"]  = entity.id
    registry["subscribers"]      = subscribers
    registry["last_checked"]     = now.isoformat()

    posts = registry.setdefault("posts", {})

    # ── Шаг 1: сканируем последние посты канала (до 200 за раз) ──────────
    # Берём посты за последние 25 часов (с запасом)
    cutoff = now - timedelta(hours=25)
    new_count = 0

    async for msg in client.iter_messages(entity, limit=200):
        if msg.date < cutoff:
            break
        if msg.service or not msg.id:
            continue

        msg_id = str(msg.id)
        if msg_id in posts:
            log.debug(f"  Пост {msg_id} уже в реестре")
            continue

        # Новый пост — регистрируем
        pub_utc  = msg.date.replace(tzinfo=timezone.utc)
        deadline = pub_utc + timedelta(hours=24)
        posts[msg_id] = {
            "msg_id":       msg.id,
            "url":          f"https://t.me/{username}/{msg.id}",
            "published_at": pub_utc.isoformat(),
            "deadline":     deadline.isoformat(),
            "content_type": detect_content_type(msg),
            "registered_at": now.isoformat(),
            "is_final":     False,
            "snapshot":     None,
        }
        new_count += 1
        log.info(f"  Зарегистрирован пост {msg_id} | deadline: {deadline.isoformat()}")

    if new_count:
        log.info(f"  Новых постов зарегистрировано: {new_count}")
    else:
        log.debug(f"  Новых постов нет")

    # ── Шаг 2: финальные срезы по постам у которых вышло 24 ч ───────────
    final_count = 0
    pending = [p for p in posts.values()
               if not p["is_final"]
               and datetime.fromisoformat(p["deadline"]) <= now]

    if pending:
        log.info(f"  Постов для финального среза: {len(pending)}")

    for post in pending:
        msg_id = post["msg_id"]
        try:
            msg = await client.get_messages(entity, ids=msg_id)
            if msg is None:
                log.warning(f"  Пост {msg_id} не найден (удалён?), пропускаем")
                continue

            stats = collect_msg_stats(msg)
            post["snapshot"]      = stats
            post["is_final"]      = True
            post["finalized_at"]  = now.isoformat()

            # Дата публикации в локальном времени (МСК) для отчётов
            pub_local = datetime.fromisoformat(post["published_at"]).astimezone(TZ)
            post["date"] = pub_local.strftime("%Y-%m-%d")
            post["time"] = pub_local.strftime("%H:%M")
            post["hour"] = pub_local.hour

            final_count += 1
            log.info(
                f"  ✓ Финальный срез пост {msg_id}: "
                f"views={stats['views']} react={stats['reactions']} "
                f"fwd={stats['forwards']} comments={stats['comments']}"
            )
        except Exception as e:
            log.error(f"  Ошибка финального среза поста {msg_id}: {e}")

    if final_count:
        log.info(f"  Финальных срезов снято: {final_count}")

    save_registry(username, registry)
    return final_count


async def main():
    if not API_ID or not API_HASH:
        log.error("API_ID / API_HASH не заданы в .env")
        sys.exit(1)

    channels = [c.strip() for c in CHANNELS_RAW.split(",") if c.strip()]
    if not channels:
        log.error("CHANNELS не заданы в .env")
        sys.exit(1)

    log.info(f"=== Запуск snapshot | {now_utc().isoformat()} ===")
    log.info(f"Каналы: {channels}")

    kwargs = {"proxy": PROXY_CFG} if PROXY_CFG else {}
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        total_final = 0
        for ch in channels:
            result = await process_channel(client, ch)
            total_final += result or 0

    log.info(f"=== Готово | Финальных срезов за запуск: {total_final} ===")


if __name__ == "__main__":
    asyncio.run(main())
