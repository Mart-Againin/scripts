"""
telegram_utils.py — общие утилиты для работы с Telegram API.
Используется snapshot.py и historical.py.
"""
import logging
from datetime import timezone
from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto, MessageMediaPoll,
    DocumentAttributeVideo, DocumentAttributeAnimated,
)

log = logging.getLogger(__name__)


def detect_content_type(msg) -> str:
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
    if not isinstance(msg.media, MessageMediaPoll):
        return 0
    results = msg.media.results
    if not results or not results.results:
        return 0
    return sum(r.voters for r in results.results if r.voters)


def extract_post_stats(msg) -> dict:
    reactions = 0
    if msg.reactions and msg.reactions.results:
        reactions = sum(r.count for r in msg.reactions.results)
    comments = msg.replies.replies if msg.replies else 0
    forwards = msg.forwards or 0
    votes    = extract_poll_votes(msg)
    views    = msg.views or 0
    actions  = reactions + comments + forwards + votes
    return {"views":views,"reactions":reactions,"comments":comments,
            "forwards":forwards,"votes":votes,"actions":actions}


async def collect_messages(client, entity, offset_date=None,
                           limit=None, stop_before=None) -> list:
    raw_msgs    = []
    grouped_map = {}
    iter_kwargs = {}
    if offset_date is not None:
        iter_kwargs["offset_date"] = offset_date
    if limit is not None:
        iter_kwargs["limit"] = limit
    async for msg in client.iter_messages(entity, reverse=False, **iter_kwargs):
        if stop_before is not None:
            msg_date_utc = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            if msg_date_utc < stop_before:
                break
        if getattr(msg, "service", False) or not msg.id:
            continue
        grouped_id = getattr(msg, "grouped_id", None)
        if grouped_id:
            grouped_map.setdefault(grouped_id, []).append(msg)
        else:
            raw_msgs.append(msg)
    for group_msgs in grouped_map.values():
        raw_msgs.append(max(group_msgs, key=lambda m: m.id))
    return raw_msgs
