"""
stories.py — сборщик статистики сторис (Stories) Telegram-каналов.

Логика аналогична snapshot.py для постов:
  • Каждый час проверяет наличие новых сторис в канале
  • Регистрирует их с deadline = published_at + 24ч (срок жизни сторис)
  • Ровно через 24ч снимает финальную статистику просмотров
  • Реакции доступны только для каналов где вы администратор

Хранение: registry/<channel>/stories.json

Используется main.py в общем почасовом цикле вместе со snapshot.py.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz
from dotenv import load_dotenv
from telethon.tl.functions.stories import (
    GetPinnedStoriesRequest,
    GetStoriesArchiveRequest,
    GetStoriesViewsRequest,
)

load_dotenv()

REGISTRY_DIR = Path(os.getenv("REGISTRY_DIR", "registry"))
TZ           = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))

log = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Хранилище ──────────────────────────────────────────────────────────────

def stories_path(channel_username: str) -> Path:
    ch = channel_username.lstrip("@")
    p  = REGISTRY_DIR / ch
    p.mkdir(parents=True, exist_ok=True)
    return p / "stories.json"


def load_stories_registry(channel_username: str) -> dict:
    path = stories_path(channel_username)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"Ошибка чтения {path}: {e}")
    return {"channel_id": channel_username, "stories": {}}


def save_stories_registry(channel_username: str, data: dict):
    path = stories_path(channel_username)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Сбор данных ────────────────────────────────────────────────────────────

def _extract_story_views(story) -> int:
    """Извлекает число просмотров из объекта StoryItem."""
    views_obj = getattr(story, "views", None)
    if views_obj is None:
        return 0
    return getattr(views_obj, "views_count", 0) or 0


def _extract_story_reactions(story) -> int:
    """
    Извлекает число реакций. Доступно только если вы администратор канала
    с правом edit_stories — иначе вернёт 0.
    """
    views_obj = getattr(story, "views", None)
    if views_obj is None:
        return 0
    reactions_count = getattr(views_obj, "reactions_count", 0) or 0
    return reactions_count


async def process_channel_stories(client, channel_id: str):
    """
    Обрабатывает сторис одного канала:
      1. Получает активные (pinned) сторис — это видимые сейчас
      2. Регистрирует новые
      3. Снимает финальный срез по тем у кого истёк 24ч срок
    """
    try:
        entity = await client.get_entity(channel_id)
    except Exception as e:
        log.error(f"[stories] Не удалось получить сущность {channel_id}: {e}")
        return 0

    username = getattr(entity, "username", None) or str(entity.id)
    now      = now_utc()

    registry = load_stories_registry(username)
    stories  = registry.setdefault("stories", {})

    new_count = 0

    # ── Шаг 1: получаем активные сторис канала ────────────────────────────
    try:
        result = await client(GetPinnedStoriesRequest(
            peer=entity, offset_id=0, limit=100
        ))
        active_stories = getattr(result, "stories", [])
    except Exception as e:
        log.debug(f"[stories] {channel_id}: активных сторис нет или ошибка: {e}")
        active_stories = []

    for story in active_stories:
        story_id = str(story.id)
        if story_id in stories:
            continue

        pub_utc  = story.date.replace(tzinfo=timezone.utc) if story.date.tzinfo is None else story.date
        deadline = pub_utc + timedelta(hours=24)

        stories[story_id] = {
            "story_id":      story.id,
            "published_at":  pub_utc.isoformat(),
            "deadline":      deadline.isoformat(),
            "registered_at": now.isoformat(),
            "is_final":      False,
            "snapshot":      None,
        }
        new_count += 1
        log.info(f"[stories] {channel_id}: зарегистрирована сторис {story_id}")

    # ── Шаг 2: финальные срезы по истёкшим сторис ─────────────────────────
    pending = [s for s in stories.values()
               if not s["is_final"]
               and datetime.fromisoformat(s["deadline"]) <= now]

    final_count = 0
    if pending:
        # Получаем архив сторис (туда попадают истёкшие)
        try:
            archive = await client(GetStoriesArchiveRequest(
                peer=entity, offset_id=0, limit=100
            ))
            archived_stories = {str(s.id): s for s in getattr(archive, "stories", [])}
        except Exception as e:
            log.debug(f"[stories] {channel_id}: архив недоступен: {e}")
            archived_stories = {}

        for story_data in pending:
            sid = str(story_data["story_id"])
            story_obj = archived_stories.get(sid)

            if story_obj is None:
                # Пробуем получить через views запрос напрямую
                try:
                    views_result = await client(GetStoriesViewsRequest(
                        peer=entity, id=[story_data["story_id"]]
                    ))
                    views_count = 0
                    if views_result and hasattr(views_result, "views"):
                        for v in views_result.views:
                            views_count = getattr(v, "views_count", 0) or 0
                            break
                    snapshot = {"views": views_count, "reactions": 0}
                except Exception as e:
                    log.warning(f"[stories] {channel_id}: не удалось получить статистику {sid}: {e}")
                    continue
            else:
                snapshot = {
                    "views":     _extract_story_views(story_obj),
                    "reactions": _extract_story_reactions(story_obj),
                }

            story_data["snapshot"]     = snapshot
            story_data["is_final"]     = True
            story_data["finalized_at"] = now.isoformat()

            pub_local = datetime.fromisoformat(story_data["published_at"]).astimezone(TZ)
            story_data["date"] = pub_local.strftime("%Y-%m-%d")

            final_count += 1
            log.info(f"[stories] {channel_id}: финальный срез {sid} "
                     f"views={snapshot['views']} reactions={snapshot['reactions']}")

    save_stories_registry(username, registry)
    if new_count or final_count:
        log.info(f"[stories] {channel_id}: новых={new_count}, финальных={final_count}")
    return final_count


# ── Получение данных за период (для report.py) ────────────────────────────

def get_stories_for_period(channel_username: str, date_from, date_to) -> list:
    """Возвращает финальные сторис за период."""
    registry = load_stories_registry(channel_username)
    result = []
    for s in registry.get("stories", {}).values():
        if not s.get("is_final") or not s.get("snapshot"):
            continue
        try:
            d = datetime.strptime(s["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if date_from <= d <= date_to:
            result.append(s)
    return result


def get_stories_summary(channel_username: str, date_from, date_to) -> dict:
    """Возвращает агрегаты по сторис за период: количество, охват, реакции."""
    stories = get_stories_for_period(channel_username, date_from, date_to)
    total_views     = sum(s["snapshot"].get("views", 0) for s in stories)
    total_reactions = sum(s["snapshot"].get("reactions", 0) for s in stories)
    return {
        "count":     len(stories),
        "views":     total_views,
        "reactions": total_reactions,
    }
