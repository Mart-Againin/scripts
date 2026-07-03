"""
registry_manager.py — управление реестром постов и архивом.

Логика хранения:
  registry/<channel>/registry.json   — активные посты (все, до 90 дней)
  archive/<channel>/YYYY-MM.json     — архив финальных постов старше 90 дней

Логика получения данных для отчёта:
  1. Запрашиваем исторические данные за период из Telegram (текущая статистика)
  2. Поверх накладываем is_final=True посты из реестра/архива (константа 24ч)
  В итоге 100% постов имеют данные.

Пометки в отчёте (колонка "Примечание"):
  ✅ 24ч  — финальный 24-часовой срез (константа)
  📸 тек. — текущая статистика на момент отчёта
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from config import REGISTRY_DIR, ARCHIVE_DIR, TZ

log = logging.getLogger(__name__)

ARCHIVE_AFTER_DAYS = 90  # финальные посты старше 90 дней переезжают в архив


# ── Пути ──────────────────────────────────────────────────────────────────

def registry_path(channel_username: str) -> Path:
    ch = channel_username.lstrip("@")
    p  = REGISTRY_DIR / ch
    p.mkdir(parents=True, exist_ok=True)
    return p / "registry.json"


def archive_path(channel_username: str, ym: str) -> Path:
    ch = channel_username.lstrip("@")
    p  = ARCHIVE_DIR / ch
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{ym}.json"


# ── Загрузка / сохранение реестра ─────────────────────────────────────────

def load_registry(channel_username: str) -> dict:
    path = registry_path(channel_username)
    if not path.exists():
        return {"posts": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Ошибка чтения реестра {path}: {e}")
        return {"posts": {}}


def save_registry(channel_username: str, data: dict):
    path = registry_path(channel_username)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Загрузка / сохранение архива ──────────────────────────────────────────

def load_archive_month(channel_username: str, ym: str) -> dict:
    """Загружает архивный файл за месяц. Возвращает dict постов {msg_id: post}."""
    path = archive_path(channel_username, ym)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("posts", {})
    except Exception as e:
        log.error(f"Ошибка чтения архива {path}: {e}")
        return {}


def save_archive_month(channel_username: str, ym: str, posts: dict):
    path = archive_path(channel_username, ym)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8")).get("posts", {})
        except Exception:
            pass
    existing.update(posts)
    path.write_text(
        json.dumps({"channel_id": channel_username, "month": ym, "posts": existing},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ── Архивирование старых постов ────────────────────────────────────────────

def archive_old_posts(channel_username: str):
    """
    Перемещает финальные посты старше ARCHIVE_AFTER_DAYS дней из
    registry.json в archive/<channel>/YYYY-MM.json.
    Данные не удаляются — только переезжают.
    """
    registry = load_registry(channel_username)
    posts    = registry.get("posts", {})
    cutoff   = (datetime.now(TZ) - timedelta(days=ARCHIVE_AFTER_DAYS)).date()

    to_archive: dict[str, list] = {}  # ym -> [(mid, post)]
    remaining = {}

    for mid, post in posts.items():
        if not post.get("is_final"):
            remaining[mid] = post
            continue
        post_date_str = post.get("date", "")
        try:
            post_date = datetime.strptime(post_date_str, "%Y-%m-%d").date()
        except ValueError:
            remaining[mid] = post
            continue

        if post_date <= cutoff:
            ym = post_date_str[:7]
            to_archive.setdefault(ym, []).append((mid, post))
        else:
            remaining[mid] = post

    if not to_archive:
        return

    for ym, items in to_archive.items():
        posts_dict = {mid: post for mid, post in items}
        save_archive_month(channel_username, ym, posts_dict)
        log.info(f"[{channel_username}] Архивировано {len(items)} постов → archive/{ym}.json")

    registry["posts"] = remaining
    save_registry(channel_username, registry)


# ── Получение финальных постов за период (реестр + архив) ─────────────────

def get_final_posts_for_period(channel_username: str,
                                date_from: date, date_to: date) -> dict:
    """
    Возвращает все финальные (is_final=True) посты за период.
    Ищет в registry.json и в архивных файлах.
    Ключ: str(msg_id), значение: post dict.
    """
    result = {}

    # 1. Из реестра
    registry = load_registry(channel_username)
    for mid, post in registry.get("posts", {}).items():
        if not post.get("is_final") or not post.get("snapshot"):
            continue
        try:
            d = datetime.strptime(post["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if date_from <= d <= date_to:
            result[mid] = post

    # 2. Из архива (нужные месяцы)
    months = set()
    cur = date_from.replace(day=1)
    while cur <= date_to:
        months.add(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    for ym in months:
        arch_posts = load_archive_month(channel_username, ym)
        for mid, post in arch_posts.items():
            if not post.get("is_final") or not post.get("snapshot"):
                continue
            try:
                d = datetime.strptime(post["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            if date_from <= d <= date_to:
                result[mid] = post

    return result
