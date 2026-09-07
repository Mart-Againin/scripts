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
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto, MessageMediaPoll,
    DocumentAttributeVideo, DocumentAttributeAnimated,
)

from config import (
    API_ID, API_HASH, SESSION_NAME, CHANNELS,
    REGISTRY_DIR, LOGS_DIR, DEBUG_MODE, TZ, get_telethon_kwargs,
)

log_level = logging.DEBUG if DEBUG_MODE else logging.INFO
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
    actions   = reactions + comments + forwards + votes  # без охвата
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


# ── Динамика подписчиков ───────────────────────────────────────────────────

def subscribers_history_path(channel_username: str) -> Path:
    ch = channel_username.lstrip("@")
    p  = REGISTRY_DIR / ch
    p.mkdir(parents=True, exist_ok=True)
    return p / "subscribers_history.json"


def load_subscribers_history(channel_username: str) -> dict:
    path = subscribers_history_path(channel_username)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"Ошибка чтения истории подписчиков {path}: {e}")
    return {}


def save_subscribers_history(channel_username: str, history: dict):
    path = subscribers_history_path(channel_username)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def record_subscribers(channel_username: str, subscribers: int):
    """
    Записывает число подписчиков КАЖДЫЙ ДЕНЬ (ключ YYYY-MM-DD), обновляя
    значение при каждом часовом проходе в течение дня — так в истории
    остаётся счётчик на последний прогон каждого дня.

    Это нужно для точной формулы прироста (см. get_month_growth ниже):
    "текущая численность минус численность на последнее число прошлого
    месяца" требует знать число подписчиков именно НА ГРАНИЦЕ месяца, а
    не "первое что попалось в этом месяце".

    Старые записи в формате "YYYY-MM" (без дня), сделанные до этого
    изменения, НЕ удаляются и не трогаются — они остаются в файле как
    есть и используются как запасной вариант для периодов ДО перехода на
    ежедневную запись (см. get_subscribers_on_or_before).
    """
    today = datetime.now(TZ)
    day_key = today.strftime("%Y-%m-%d")

    history = load_subscribers_history(channel_username)

    if history.get(day_key) != subscribers:
        history[day_key] = subscribers
        save_subscribers_history(channel_username, history)
        log.debug(f"  [{channel_username}] Подписчики на {day_key}: {subscribers}")


def get_subscribers_on_or_before(channel_username: str, target_date: date) -> int | None:
    """
    Возвращает число подписчиков на указанную дату (или ближайшую
    предыдущую, если записи ровно на эту дату нет — например скрипт не
    работал именно в этот день).

    Использует ежедневные записи (ключ YYYY-MM-DD). Если для нужного
    периода их ещё нет (данные собраны ДО перехода на ежедневную
    точность) — подстраховывается старой месячной записью (YYYY-MM) того
    же месяца, если она есть.
    """
    history = load_subscribers_history(channel_username)
    target_str = target_date.strftime("%Y-%m-%d")

    daily_keys = sorted(k for k in history if len(k) == 10 and k[4] == "-" and k[7] == "-")
    candidates = [k for k in daily_keys if k <= target_str]
    if candidates:
        return history[candidates[-1]]

    # Фолбэк на старую месячную запись (данные до перехода на ежедневную точность)
    month_key = target_str[:7]
    return history.get(month_key)


def get_month_growth(channel_username: str, ym: str) -> int | None:
    """
    Прирост за месяц СТРОГО по формуле:
        численность на последнее число месяца ym
        минус
        численность на последнее число предыдущего месяца
    Число может быть положительным, отрицательным или нулём.
    Если для одной из границ данных нет вообще — возвращает None
    (не подставляет 0, чтобы не выдавать отсутствие данных за "нулевой рост").
    """
    year, month = int(ym[:4]), int(ym[5:7])
    last_day_this = date(year, month, monthrange(year, month)[1])
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    last_day_prev = date(prev_year, prev_month, monthrange(prev_year, prev_month)[1])

    subs_now  = get_subscribers_on_or_before(channel_username, last_day_this)
    subs_prev = get_subscribers_on_or_before(channel_username, last_day_prev)
    if subs_now is None or subs_prev is None:
        return None
    return subs_now - subs_prev


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

    # Если participants_count не вернулся — полный запрос
    if not subscribers:
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(entity))
            subscribers = getattr(full.full_chat, "participants_count", 0) or 0
        except Exception as e:
            log.warning(f"  Не удалось получить подписчиков {channel_id}: {e}")

    now           = now_utc()

    registry = load_registry(username)
    registry["channel_title"]    = title
    registry["channel_numeric"]  = entity.id
    registry["subscribers"]      = subscribers
    registry["last_checked"]     = now.isoformat()

    # Записываем динамику подписчиков (раз в день — функция сама не дублирует)
    record_subscribers(username, subscribers)

    posts = registry.setdefault("posts", {})

    # ── Шаг 1: сканируем последние посты канала (до 200 за раз) ──────────
    # Берём посты за последние 25 часов (с запасом)
    cutoff = now - timedelta(hours=25)
    new_count = 0

    # Сначала собираем все сообщения за 25ч
    raw_msgs = []
    grouped_map_snap = {}
    async for msg in client.iter_messages(entity, limit=200):
        if msg.date < cutoff:
            break
        if getattr(msg, "service", False) or not msg.id:
            continue
        grouped_id = getattr(msg, "grouped_id", None)
        if grouped_id:
            grouped_map_snap.setdefault(grouped_id, []).append(msg)
        else:
            raw_msgs.append(msg)

    # Из каждого альбома берём первое фото (min msg_id)
    for group_msgs in grouped_map_snap.values():
        raw_msgs.append(min(group_msgs, key=lambda m: m.id))

    for msg in raw_msgs:
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
            "message":      msg.message or "",
            "registered_at": now.isoformat(),
            "is_final":     False,
            "snapshot":     None,
        }
        new_count += 1
        log.info(f"  Зарегистрирован пост {msg_id} | deadline: {deadline.isoformat()}")

    if new_count:
        log.info(f"  Новых постов зарегистрировано: {new_count}")
    else:
        log.debug("  Новых постов нет")

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

    # Архивируем посты старше 90 дней (финальные переезжают в archive/)
    try:
        from registry_manager import archive_old_posts
        archive_old_posts(username)
    except Exception as e:
        log.warning(f"  Ошибка архивирования {username}: {e}")

    return final_count


async def main():
    if not API_ID or not API_HASH:
        log.error("API_ID / API_HASH не заданы в .env")
        sys.exit(1)

    channels = CHANNELS
    if not channels:
        log.error("CHANNELS не заданы в .env")
        sys.exit(1)

    log.info(f"=== Запуск snapshot | {now_utc().isoformat()} ===")
    log.info(f"Каналы: {channels}")

    kwargs = get_telethon_kwargs()
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        total_final = 0
        for ch in channels:
            result = await process_channel(client, ch)
            total_final += result or 0

    log.info(f"=== Готово | Финальных срезов за запуск: {total_final} ===")


if __name__ == "__main__":
    asyncio.run(main())
