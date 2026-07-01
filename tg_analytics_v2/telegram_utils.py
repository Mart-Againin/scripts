"""
telegram_utils.py — общие утилиты для работы с Telegram API.

Вспомогательные функции, используемые несколькими модулями:
  - snapshot.py (сбор постов каждый час)
  - historical.py (ретро-сбор постов из истории)

Содержит:
  - detect_content_type()   — определение типа контента сообщения
  - extract_poll_votes()    — суммирование голосов в опросе
  - collect_messages()      — сбор сообщений с дедупликацией альбомов
  - extract_post_stats()    — извлечение числовой статистики из сообщения
"""

import logging
from datetime import datetime, timezone

from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto, MessageMediaPoll,
    DocumentAttributeVideo, DocumentAttributeAnimated,
)

log = logging.getLogger(__name__)


def detect_content_type(msg) -> str:
    """Определяет тип контента Telegram-сообщения."""
    if msg.media is None:
        return "Текст" if msg.message else "Пустой"
    if isinstance(msg.media, MessageMediaPoll):   return "Опрос"
    if isinstance(msg.media, MessageMediaPhoto):  return "Фото"
    if isinstance(msg.media, MessageMediaDocument):
        for attr in msg.media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):    return "Видео"
            if isinstance(attr, DocumentAttributeAnimated): return "GIF"
        return "Документ"
    if getattr(msg, "web_preview", None): return "Ссылка"
    return "Другое"


def extract_poll_votes(msg) -> int:
    """Суммирует голоса в опросе. Возвращает 0 если пост не является опросом."""
    if not isinstance(msg.media, MessageMediaPoll):
        return 0
    results = msg.media.results
    if not results or not results.results:
        return 0
    return sum(r.voters for r in results.results if r.voters)


def extract_post_stats(msg) -> dict:
    """
    Извлекает числовую статистику из Telegram-сообщения.
    Возвращает словарь: views, reactions, comments, forwards, votes, actions.
    actions = reactions + comments + forwards + votes (без views — по смыслу).
    """
    reactions = 0
    if msg.reactions and msg.reactions.results:
        reactions = sum(r.count for r in msg.reactions.results)
    comments = msg.replies.replies if msg.replies else 0
    forwards = msg.forwards or 0
    votes    = extract_poll_votes(msg)
    views    = msg.views or 0
    actions  = reactions + comments + forwards + votes

    return {
        "views":     views,
        "reactions": reactions,
        "comments":  comments,
        "forwards":  forwards,
        "votes":     votes,
        "actions":   actions,
    }


async def collect_messages(client, entity, offset_date=None,
                           limit=None, stop_before=None) -> list:
    """
    Собирает сообщения из канала с дедупликацией альбомов.

    Из каждого альбома (grouped_id) выбирается сообщение с max(msg.id),
    так как именно к нему в Telegram привязаны реакции и комментарии.

    Параметры:
        client       — TelegramClient (Telethon)
        entity       — канал / чат
        offset_date  — datetime (UTC), получать сообщения до этой даты
        limit        — максимальное число сообщений (None = все)
        stop_before  — datetime (UTC), прекратить итерацию если
                       дата сообщения стала раньше этого момента

    Возвращает список объектов Telethon Message, уже дедуплицированных.
    """
    raw_msgs    = []
    grouped_map: dict = {}

    iter_kwargs: dict = {}
    if offset_date is not None:
        iter_kwargs["offset_date"] = offset_date
    if limit is not None:
        iter_kwargs["limit"] = limit

    async for msg in client.iter_messages(entity, reverse=False, **iter_kwargs):
        # Проверяем условие остановки
        if stop_before is not None:
            msg_date_utc = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            if msg_date_utc < stop_before:
                break

        if getattr(msg, "service", False) or not msg.id:
            continue

        grouped_id = getattr(msg, "grouped_id", None)
        if grouped_id:
            # Накапливаем сообщения группы, потом выбираем нужное
            grouped_map.setdefault(grouped_id, []).append(msg)
        else:
            raw_msgs.append(msg)

    # Из каждого альбома берём сообщение с наибольшим msg.id
    # (именно у него привязаны реакции и комментарии всего альбома)
    for group_msgs in grouped_map.values():
        raw_msgs.append(max(group_msgs, key=lambda m: m.id))

    return raw_msgs
