"""
telegram_utils.py — общие утилиты для работы с Telegram API.

detect_content_type() и extract_poll_votes() — единственный источник этой
логики; snapshot.py раньше импортировал их отсюда и тут же затирал
одноимёнными локальными копиями (импорт был мёртвым), historical.py вёл
свою отдельную копию с префиксом "_". Обе копии убраны в пользу этого
модуля.

extract_post_stats() — сбор статистики поста (views/reactions/comments/
forwards/votes/actions) в одну функцию; использовалась под другим именем
(collect_msg_stats) в snapshot.py — консолидировано сюда.
"""
import logging
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
