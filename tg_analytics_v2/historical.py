"""
historical.py — модуль работы с историческими данными.

Хранит посты за прошлые периоды в registry/<channel>/historical/YYYY-MM.json.
Каждый файл = полный календарный месяц, собранный один раз из истории канала.

Используется report.py и backfill.py для получения данных когда
24ч срезов в реестре нет.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytz
from dotenv import load_dotenv
from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto, MessageMediaPoll,
    DocumentAttributeVideo, DocumentAttributeAnimated,
)

load_dotenv()

REGISTRY_DIR = Path(os.getenv("REGISTRY_DIR", "registry"))
TZ           = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))

log = logging.getLogger(__name__)


# ── Вспомогательные функции ───────────────────────────────────────────────

def _detect_content_type(msg) -> str:
    if msg.media is None:
        return "Текст" if msg.message else "Пустой"
    if isinstance(msg.media, MessageMediaPoll):   return "Опрос"
    if isinstance(msg.media, MessageMediaPhoto):  return "Фото"
    if isinstance(msg.media, MessageMediaDocument):
        for attr in msg.media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):   return "Видео"
            if isinstance(attr, DocumentAttributeAnimated): return "GIF"
        return "Документ"
    if getattr(msg, "web_preview", None): return "Ссылка"
    return "Другое"


def _extract_poll_votes(msg) -> int:
    if not isinstance(msg.media, MessageMediaPoll): return 0
    results = msg.media.results
    if not results or not results.results: return 0
    return sum(r.voters for r in results.results if r.voters)


# ── Пути хранилища ────────────────────────────────────────────────────────

def _hist_dir(channel_username: str) -> Path:
    ch = channel_username.lstrip("@")
    p  = REGISTRY_DIR / ch / "historical"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hist_path(channel_username: str, ym: str) -> Path:
    """Путь к файлу historical/YYYY-MM.json."""
    return _hist_dir(channel_username) / f"{ym}.json"


# ── Загрузка и сохранение ─────────────────────────────────────────────────

def load_historical_month(channel_username: str, ym: str) -> list | None:
    """
    Загружает исторические посты за месяц из файла.
    Возвращает список постов или None если файл не существует.
    """
    path = _hist_path(channel_username, ym)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        posts = data.get("posts", [])
        log.info(f"  [{channel_username}] Загружено из кэша {ym}: {len(posts)} постов")
        return posts
    except Exception as e:
        log.error(f"  Ошибка чтения {path}: {e}")
        return None


def save_historical_month(channel_username: str, ym: str,
                           posts: list, subscribers: int,
                           channel_title: str = ""):
    """Сохраняет исторические посты за месяц в файл."""
    path = _hist_path(channel_username, ym)
    data = {
        "channel_id":    channel_username,
        "channel_title": channel_title,
        "subscribers":   subscribers,
        "month":         ym,
        "collected_at":  datetime.now(TZ).isoformat(),
        "posts":         posts,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"  [{channel_username}] Сохранено в кэш {ym}: {len(posts)} постов → {path}")


def historical_month_exists(channel_username: str, ym: str) -> bool:
    return _hist_path(channel_username, ym).exists()


def get_historical_info(channel_username: str, ym: str) -> str | None:
    """Возвращает строку с датой сбора или None."""
    path = _hist_path(channel_username, ym)
    if not path.exists(): return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("collected_at", "")[:16].replace("T", " ")
    except Exception:
        return None


# ── Сбор из Telegram ──────────────────────────────────────────────────────

async def fetch_and_cache_month(client, channel_username: str,
                                 ym: str, force: bool = False) -> tuple[list, int]:
    """
    Читает все посты канала за месяц YYYY-MM из Telegram.
    Кэширует результат в historical/YYYY-MM.json.
    Если файл уже есть и force=False — возвращает кэш.

    Возвращает (posts, subscribers).
    """
    # Проверяем кэш
    if not force:
        cached = load_historical_month(channel_username, ym)
        if cached is not None:
            # Читаем subscribers из файла
            path = _hist_path(channel_username, ym)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cached, data.get("subscribers", 0)
            except Exception:
                return cached, 0

    year, month = int(ym[:4]), int(ym[5:7])
    from calendar import monthrange
    d_from = date(year, month, 1)
    d_to   = date(year, month, monthrange(year, month)[1])

    log.info(f"  [{channel_username}] Исторический сбор из Telegram: {ym}...")

    try:
        entity = await client.get_entity(channel_username)
    except Exception as e:
        log.error(f"  Не удалось получить сущность {channel_username}: {e}")
        return [], 0

    username    = getattr(entity, "username", None) or str(entity.id)
    title       = getattr(entity, "title", username)
    subscribers = getattr(entity, "participants_count", 0) or 0

    # Если participants_count не вернулся — делаем полный запрос канала
    if not subscribers:
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(entity))
            subscribers = getattr(full.full_chat, "participants_count", 0) or 0
            log.debug(f"  [{channel_username}] Подписчиков (full): {subscribers}")
        except Exception as e:
            log.warning(f"  [{channel_username}] Не удалось получить подписчиков: {e}")

    # Временные границы в UTC
    day_end   = datetime(d_to.year,   d_to.month,   d_to.day,   23, 59, 59, tzinfo=timezone.utc)
    day_start = datetime(d_from.year, d_from.month, d_from.day,  0,  0,  0, tzinfo=timezone.utc)

    posts = []
    async for msg in client.iter_messages(
        entity,
        offset_date=day_end + timedelta(seconds=1),
        reverse=False,
        limit=None,
    ):
        msg_date_utc = msg.date.replace(tzinfo=timezone.utc)
        if msg_date_utc < day_start:
            break
        if getattr(msg, "service", False) or not msg.id:
            continue

        reactions = 0
        if msg.reactions and msg.reactions.results:
            reactions = sum(r.count for r in msg.reactions.results)
        comments = msg.replies.replies if msg.replies else 0
        forwards = msg.forwards or 0
        votes    = _extract_poll_votes(msg)
        views    = msg.views or 0
        actions  = views + reactions + comments + forwards + votes

        pub_local = msg_date_utc.astimezone(TZ)

        posts.append({
            "msg_id":        msg.id,
            "url":           f"https://t.me/{username}/{msg.id}",
            "date":          pub_local.strftime("%Y-%m-%d"),
            "time":          pub_local.strftime("%H:%M"),
            "hour":          pub_local.hour,
            "content_type":  _detect_content_type(msg),
            "is_final":      True,
            "is_historical": True,
            "snapshot": {
                "views":     views,
                "reactions": reactions,
                "comments":  comments,
                "forwards":  forwards,
                "votes":     votes,
                "actions":   actions,
            },
        })

    posts.sort(key=lambda p: (p["date"], p["time"]))
    log.info(f"  [{channel_username}] Собрано из Telegram: {len(posts)} постов за {ym}")

    # Сохраняем в кэш
    save_historical_month(channel_username, ym, posts, subscribers, title)
    return posts, subscribers


# ── Получение постов за произвольный период ───────────────────────────────

async def get_posts_for_period(client, channel_username: str,
                                date_from: date, date_to: date,
                                force: bool = False) -> tuple[list, int]:
    """
    Возвращает исторические посты за период [date_from, date_to].
    Автоматически определяет нужные месяцы, загружает/кэширует каждый.
    Неделя может захватывать два месяца — обрабатываем оба.

    Возвращает (posts, subscribers).
    """
    # Определяем уникальные месяцы в периоде
    months_needed = set()
    cur = date_from.replace(day=1)
    while cur <= date_to:
        months_needed.add(cur.strftime("%Y-%m"))
        # Следующий месяц
        if cur.month == 12:
            cur = cur.replace(year=cur.year+1, month=1)
        else:
            cur = cur.replace(month=cur.month+1)

    all_posts  = []
    subscribers = 0

    for ym in sorted(months_needed):
        month_posts, subs = await fetch_and_cache_month(
            client, channel_username, ym, force=force)
        if subs: subscribers = subs
        all_posts.extend(month_posts)

    # Фильтруем по точному периоду
    df_str = date_from.strftime("%Y-%m-%d")
    dt_str = date_to.strftime("%Y-%m-%d")
    filtered = [p for p in all_posts
                if df_str <= p.get("date","") <= dt_str]
    filtered.sort(key=lambda p: (p["date"], p["time"]))

    return filtered, subscribers
