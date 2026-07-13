"""
formatter.py — форматирование алертов для отправки в Telegram.

Отвечает за:
- escape_md(), md_bold(), md_link() — MarkdownV2 хелперы
- format_post_message() — формирует готовое сообщение алерта
- _highlight_keys_in_fragment() — выделяет ключи жирным в цитате
"""

import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── MarkdownV2 helpers ────────────────────────────────────────────────────────

def escape_md(text: str) -> str:
    return re.sub(r'([\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!\\])', r'\\\1', text)


def md_bold(text: str) -> str:
    return f"*{text}*"


def md_link(label: str, url: str) -> str:
    safe_url = url.replace('\\', '\\\\').replace(')', '\\)')
    return f"[{escape_md(label)}]({safe_url})"


# Сепаратор фрагментов
_FRAGMENT_SEP = f"\n{escape_md('<...')}{escape_md('>')}\n"


# ── Выделение ключей в цитате ─────────────────────────────────────────────────

def highlight_keys_in_fragment(context: str, keys: list[str]) -> str:
    """Выделяет ключевые слова жирным в цитате."""
    escaped = escape_md(context)
    for key in keys:
        key_display = key.replace('_', ' ')
        escaped_key = escape_md(key_display)
        escaped = re.sub(
            re.escape(escaped_key),
            lambda m: md_bold(escape_md(m.group(0).replace('\\', '').upper())),
            escaped,
            flags=re.IGNORECASE,
        )
    return escaped


# ── Главная функция форматирования ────────────────────────────────────────────

def format_post_message(
    post: dict,
    post_url: str,
    channel_title: str,
    subs: int,
    views: int,
    key_mentions: list[dict],
    fragment_max_words: int,
    label: str = "",
    is_repeat: bool = False,
    repeat_info: dict | None = None,
) -> str:
    """
    Формирует MarkdownV2 сообщение для отправки в Telegram.

    Args:
        post:               Словарь поста из TGStat
        post_url:           URL поста
        channel_title:      Название канала
        subs:               Количество подписчиков
        views:              Количество просмотров
        key_mentions:       Результат find_key_mentions()
        fragment_max_words: Максимум слов в цитате
        label:              Дополнительная метка (например "🔁 ПОСЕВ")
        is_repeat:          Пост является повтором
        repeat_info:        Информация об оригинале для посева

    Returns:
        str: Готовое сообщение в MarkdownV2
    """
    from text_filter import _strip_html

    post_text = _strip_html(post.get("text", "") or "")
    if len(post_text) < 5:
        post_text = "[только медиа]"

    clean_sentences = [_strip_html(s) for s in _split_sentences(post_text)]
    first_sentence = clean_sentences[0] if clean_sentences else _strip_html(post_text[:200]) or "—"

    unique_tags = list(dict.fromkeys(t for m in key_mentions for t in m["tags"]))
    url_clean = re.sub(r'^https?://', '', post_url)

    # ── Заголовок ─────────────────────────────────────────────────────────────
    header_parts = [md_bold(escape_md(channel_title))]
    if label:
        header_parts.append(escape_md(f" {label}"))
    elif is_repeat and repeat_info:
        orig_ch = repeat_info.get("channel", "")
        orig_subs = repeat_info.get("subs", 0)
        header_parts.append(escape_md(f" 🔁 посев из {orig_ch} ({orig_subs:,} подп.)"))

    # Теги
    if unique_tags:
        tags_str = " ".join(f"#{escape_md(t)}" for t in unique_tags)
        header_parts.append(f" {tags_str}")

    # Метрики
    header_parts.append(
        f" \\(___{escape_md(f'{subs:,}')} подп\\. \\| {escape_md(f'{views:,}')} просм\\.__\\)"
    )
    header = "".join(header_parts)

    # ── Фрагменты с выделением ────────────────────────────────────────────────
    fragments = []
    seen_indices: set[int] = set()

    for mention in key_mentions:
        idx = mention["index"]
        if idx in seen_indices:
            continue
        seen_indices.add(idx)

        context = mention["context"]
        words = context.split()
        if len(words) > fragment_max_words:
            context = " ".join(words[:fragment_max_words]) + "…"

        highlighted = highlight_keys_in_fragment(context, [mention["key"]])
        fragments.append(f"_{highlighted}_")

    # ── Первое предложение (если нет фрагментов) ─────────────────────────────
    if not fragments:
        preview = escape_md(first_sentence[:300])
        body = f"_{preview}_"
    else:
        body = _FRAGMENT_SEP.join(fragments)

    # ── Ссылка ────────────────────────────────────────────────────────────────
    link = md_link(url_clean, post_url)

    return f"{header}\n{body}\n🔗 {link}"


def _split_sentences(text: str) -> list[str]:
    """Разбивает текст на предложения."""
    parts = re.split(r'(?<=[.!?])\s+|[\n]{2,}', text)
    return [p.strip() for p in parts if p.strip()]
