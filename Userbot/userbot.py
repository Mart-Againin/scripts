import os
import re
import json
import time
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Callable

from dotenv import load_dotenv
from telethon import TelegramClient, events, errors

load_dotenv()

# ---------- глобальный кэш сообщений (ограниченный) ----------

_CACHE_DEQUE: deque = deque(maxlen=10_000)
_CACHE_SET: set = set()


def cache_contains(key: str) -> bool:
    return key in _CACHE_SET


def cache_add(key: str) -> None:
    if len(_CACHE_DEQUE) == _CACHE_DEQUE.maxlen:
        # deque вытолкнет старый элемент — удаляем его из set
        evicted = _CACHE_DEQUE[0]
        _CACHE_SET.discard(evicted)
    _CACHE_DEQUE.append(key)
    _CACHE_SET.add(key)


# ---------- конфиг ----------

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MASTER_ID = int(os.getenv("MASTER_ID"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

client = TelegramClient("user", API_ID, API_HASH)


# ---------- структуры ----------

@dataclass
class ProjectConfig:
    name: str
    channels: List[object]
    owner_id: int
    pattern_people: re.Pattern
    pattern_companies: re.Pattern


# ---------- разбор .env по проектам ----------

def parse_list(env_name: str) -> List[str]:
    raw = os.getenv(env_name, "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_pattern(values: List[str]) -> re.Pattern:
    if not values:
        return re.compile(r"^\b$", re.IGNORECASE)
    escaped = [re.escape(v) for v in values]
    return re.compile("(" + "|".join(escaped) + ")", re.IGNORECASE)


def load_projects(max_projects: int = 10) -> List[ProjectConfig]:
    projects: List[ProjectConfig] = []

    for i in range(1, max_projects + 1):
        ch_key = f"CHANNELS_{i}"
        owner_key = f"OWNER_{i}"
        people_key = f"PEOPLE_{i}"
        companies_key = f"COMPANIES_{i}"

        channels_raw = os.getenv(ch_key, "")
        owner_raw = os.getenv(owner_key, "")

        if not channels_raw or not owner_raw:
            continue

        channels: List[object] = []
        for item in channels_raw.split(","):
            item = item.strip()
            if not item:
                continue
            if item.lstrip("-").isdigit():
                channels.append(int(item))
            else:
                channels.append(item)

        owner_id = int(owner_raw)

        people = parse_list(people_key)
        companies = parse_list(companies_key)

        projects.append(
            ProjectConfig(
                name=f"PROJECT_{i}",
                channels=channels,
                owner_id=owner_id,
                pattern_people=build_pattern(people),
                pattern_companies=build_pattern(companies),
            )
        )

    return projects


PROJECTS = load_projects(max_projects=10)

if not PROJECTS:
    logging.error("Нет ни одного проекта в .env (CHANNELS_N / OWNER_N).")
else:
    for p in PROJECTS:
        logging.info(f"{p.name}: channels={p.channels}, owner={p.owner_id}")


# ---------- базовые утилиты ----------

async def safe_call(coro_fn: Callable, *args, max_attempts: int = 5, **kwargs):
    """
    Принимает callable (функцию/метод), создаёт корутин заново при каждой попытке.
    Это позволяет корректно повторять вызов после FloodWait.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except errors.FloodWaitError as e:
            wait = e.seconds + 2
            logging.warning(f"FloodWait {wait}s (попытка {attempt}/{max_attempts}), ждём...")
            await asyncio.sleep(wait)
        except Exception as e:
            logging.error(f"safe_call error (попытка {attempt}/{max_attempts}): {e}")
            return None
    logging.error(f"safe_call: превышено число попыток ({max_attempts})")
    return None


def extract_sentences(text: str) -> List[str]:
    # \n → .
    text = re.sub(r'\n+', '. ', text)
    # Разбиваем после .!? + пробел
    sentences = re.split(r'(?<=[\.!?])\s+', text)
    # Фильтр коротких
    return [s.strip() for s in sentences if len(s.strip()) > 5]


def find_key_contexts(
    text: str,
    pattern_people: re.Pattern,
    pattern_companies: re.Pattern,
) -> Tuple[List[str], List[str]]:
    """
    Возвращает (sentences, contexts).
    sentences — все предложения (нужны для build_alert_body).
    contexts  — выделенные фрагменты с ключами.

    Гарантия: contexts никогда не будет пустым, если в тексте есть совпадения.
    Fallback: если совпадений нет среди предложений — возвращаем первые 2 предложения.
    """
    sentences = extract_sentences(text)

    if not sentences:
        return [], [text[:500]]  # крайний случай: текст есть, предложений нет

    # ≤ 3 предложений → весь пост целиком
    if len(sentences) <= 3:
        full_text = " ".join(sentences)
        full_text = pattern_people.sub(r'**\g<1>**', full_text)
        full_text = pattern_companies.sub(r'**\g<1>**', full_text)
        return sentences, [full_text]

    # > 3 → выбираем предложения с ключами (до 2 штук, без дублей)
    used_sentences: set = set()
    contexts: List[str] = []

    for sent in sentences:
        if pattern_people.search(sent) or pattern_companies.search(sent):
            clean = re.sub(r'\s+', ' ', sent.strip())
            if clean in used_sentences:
                continue
            used_sentences.add(clean)
            ctx = pattern_people.sub(r'**\g<1>**', sent)
            ctx = pattern_companies.sub(r'**\g<1>**', ctx)
            contexts.append(ctx)
            if len(contexts) >= 2:
                break

    # Fallback: ключи есть в тексте, но не попали в предложения после разбивки
    if not contexts:
        fallback = " ".join(sentences[:2])
        fallback = pattern_people.sub(r'**\g<1>**', fallback)
        fallback = pattern_companies.sub(r'**\g<1>**', fallback)
        contexts = [fallback]
        logging.warning("find_key_contexts: ключи не найдены в предложениях, использован fallback")

    return sentences, contexts


# ---------- метаданные каналов (теги + подписчики) ----------

META_FILE = "channel_meta.json"
META_UPDATE_INTERVAL = 7 * 24 * 60 * 60  # 1 неделя

CHANNEL_META: Dict[str, object] = {
    "updated_at": 0,
    "channels": {}
}


def load_channel_tags_from_env() -> Dict[str, str]:
    """CHANNEL_TAGS=key:[tag];key:[None]"""
    raw = os.getenv("CHANNEL_TAGS", "")
    result: Dict[str, str] = {}
    if not raw:
        return result
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, val = pair.split(":", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        result[key] = "" if val == "[None]" else val
    return result


def load_meta_cache():
    global CHANNEL_META
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE, "r", encoding="utf-8") as f:
                CHANNEL_META = json.load(f)
        except Exception as e:
            logging.warning(f"Не удалось прочитать {META_FILE}: {e}")
            CHANNEL_META = {"updated_at": 0, "channels": {}}
    else:
        CHANNEL_META = {"updated_at": 0, "channels": {}}


def save_meta_cache():
    try:
        with open(META_FILE, "w", encoding="utf-8") as f:
            json.dump(CHANNEL_META, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning(f"Не удалось записать {META_FILE}: {e}")


def get_all_channel_keys_from_projects() -> List[object]:
    seen = set()
    result: List[object] = []
    for proj in PROJECTS:
        for ch in proj.channels:
            if ch in seen:
                continue
            seen.add(ch)
            result.append(ch)
    return result


def key_from_chat_obj(chat) -> str:
    username = getattr(chat, "username", None)
    if username:
        return username
    chat_id = getattr(chat, 'id', 0)
    # Каналы Telegram всегда отрицательные и начинаются с -100
    s = str(chat_id)
    if s.startswith('-100'):
        return s
    # На случай голого положительного ID (не должно быть для каналов)
    if chat_id > 0:
        return f"-100{chat_id}"
    return s


def key_from_event(event) -> str:
    username = getattr(event.chat, "username", None)
    if username:
        return username
    return str(event.chat_id)


def get_meta_for_event(event) -> Tuple[str, int]:
    """Возвращает (tag, subs) для канала из кэша. Иначе ("", 0)."""
    k = key_from_event(event)
    data = CHANNEL_META.get("channels", {}).get(k)
    if not data:
        return "", 0
    tag = data.get("tag", "") or ""
    subs = int(data.get("subs", 0) or 0)
    return tag, subs


async def update_channel_meta_if_needed():
    now = int(time.time())
    updated_at = int(CHANNEL_META.get("updated_at", 0))

    if now - updated_at < META_UPDATE_INTERVAL:
        logging.info("META: обновление не требуется")
        return

    logging.info("META: начинаем недельное обновление тегов и подписчиков")

    tags_from_env = load_channel_tags_from_env()
    all_channels = get_all_channel_keys_from_projects()
    channels_meta: Dict[str, Dict[str, object]] = {}

    for ch in all_channels:
        try:
            chat = await safe_call(client.get_entity, ch)
            if chat is None:
                logging.warning(f"META: get_entity вернул None для {ch}, пропускаем")
                continue
        except Exception as e:
            logging.warning(f"META: не удалось получить чат {ch}: {e}")
            continue

        key = key_from_chat_obj(chat)
        tag = tags_from_env.get(key, "")
        subs = int(getattr(chat, "participants_count", None) or 0)

        channels_meta[key] = {"tag": tag, "subs": subs}
        await asyncio.sleep(1)

    CHANNEL_META["updated_at"] = now
    CHANNEL_META["channels"] = channels_meta
    save_meta_cache()
    logging.info("META: обновление завершено")


# ---------- формирование алерта ----------

def build_alert_body(
    text: str,
    title: str,
    url: str,
    tag: str,
    subs: int,
    pattern_people: re.Pattern,
    pattern_companies: re.Pattern,
) -> Optional[str]:
    """
    Собирает текст алерта. Возвращает None если текст пустой или сборка упала.
    Все внутренние ошибки перехватываются — алерт не должен падать из-за
    форматирования.
    """
    try:
        if not text or not text.strip():
            logging.warning("build_alert_body: пустой текст, алерт пропущен")
            return None

        sentences, contexts = find_key_contexts(text, pattern_people, pattern_companies)

        # Хэштеги из найденных ключей
        all_keys: set = set()
        text_for_keys = " ".join(contexts)
        all_keys.update(pattern_people.findall(text_for_keys))
        all_keys.update(pattern_companies.findall(text_for_keys))
        hashtags = " ".join(f"#{k.replace(' ', '_')}" for k in sorted(all_keys))

        subs_part = f" (*{subs:,} подписчиков*)" if subs > 0 else ""
        header = f"**{title or 'Без названия'}**{subs_part}"
        full_header = (f"{tag} " if tag else "") + header
        if hashtags:
            full_header += f" {hashtags}"

        lines = [full_header]

        # Если > 3 предложений — показываем начало поста (первые 2)
        if len(sentences) > 3:
            first_part = " ".join(sentences[:2])
            lines.extend([first_part, ""])

        # Контексты с ключами
        for i, ctx in enumerate(contexts, 1):
            if i > 1:
                lines.extend(["", "<...>", ""])
            lines.append(ctx)

        lines.extend(["", f"🔗 Пост ({url})"])
        return "\n".join(lines)

    except Exception as e:
        logging.error(f"build_alert_body: ошибка сборки алерта: {e}", exc_info=True)
        # Минимальный fallback — хоть что-то отправить
        try:
            fallback = f"**{title or 'Без названия'}**\n\n{text[:300]}...\n\n🔗 Пост ({url})"
            logging.warning("build_alert_body: использован аварийный fallback")
            return fallback
        except Exception:
            return None


# ---------- регистрация обработчиков проектов ----------

def register_project_handlers(project: ProjectConfig):
    if not project.channels:
        logging.warning(f"{project.name}: нет валидных каналов, обработчик не регистрируется")
        return

    @client.on(events.NewMessage(chats=project.channels))
    async def handler(event):
        try:
            # Дедупликация
            msg_key = f"{event.chat_id}:{event.id}"
            if cache_contains(msg_key):
                return
            cache_add(msg_key)

            text = event.message.message or ""
            if not text.strip():
                return

            # Проверяем совпадения
            matches_people = list(set(project.pattern_people.findall(text)))
            matches_companies = list(set(project.pattern_companies.findall(text)))

            if not (matches_people or matches_companies):
                return

            logging.info(
                f"🎯 {project.name} АЛЕРТ [{event.chat_id}:{event.id}] "
                f"ключи: {matches_people + matches_companies}"
            )

            # URL поста
            chat_id = event.chat_id
            msg_id = event.id
            chat_id_str = str(chat_id)
            if chat_id_str.startswith("-100"):
                url = f"https://t.me/c/{chat_id_str[4:]}/{msg_id}"
            else:
                username = getattr(event.chat, "username", None) or ""
                url = f"https://t.me/{username}/{msg_id}" if username else f"https://t.me/c/{chat_id_str}/{msg_id}"

            tag, subs = get_meta_for_event(event)
            title = getattr(event.chat, "title", None) or "Без названия"

            body = build_alert_body(
                text=text,
                title=title,
                url=url,
                tag=tag,
                subs=subs,
                pattern_people=project.pattern_people,
                pattern_companies=project.pattern_companies,
            )

            if body is None:
                logging.error(
                    f"{project.name}: build_alert_body вернул None, "
                    f"алерт пропущен [{event.chat_id}:{event.id}]"
                )
                return

            result = await safe_call(
                client.send_message,
                project.owner_id,
                body,
                parse_mode='md',
            )
            if result is None:
                logging.error(
                    f"{project.name}: не удалось отправить алерт "
                    f"owner={project.owner_id} [{event.chat_id}:{event.id}]"
                )

        except Exception as e:
            logging.error(f"{project.name} handler error: {e}", exc_info=True)


# ---------- команды владельца ----------

@client.on(events.NewMessage(from_users=MASTER_ID, pattern=r"^/status$"))
async def cmd_status(event):
    lines = [
        f"{p.name}: {len(p.channels)} канал(ов), owner={p.owner_id}"
        for p in PROJECTS
    ]
    await event.reply("Юзербот запущен.\n" + "\n".join(lines))


@client.on(events.NewMessage(from_users=MASTER_ID, pattern=r"^/ping$"))
async def cmd_ping(event):
    await event.reply("pong")


@client.on(events.NewMessage(from_users=MASTER_ID, pattern=r"^/update_meta$"))
async def cmd_update_meta(event):
    await event.reply("Запускаю обновление метаданных...")
    await update_channel_meta_if_needed()
    await event.reply("Обновление завершено.")


# ---------- запуск ----------

async def main():
    await client.start()
    load_meta_cache()
    logging.info("Userbot started")

    # Валидация каналов: сохраняем username если есть, иначе оригинальный ключ
    total_valid = 0
    for project in PROJECTS:
        valid_channels = []
        for ch in project.channels:
            try:
                entity = await client.get_entity(ch)
                # username предпочтительнее, но числовой ID тоже валиден
                valid_channels.append(entity.username if entity.username else ch)
                total_valid += 1
            except Exception as e:
                logging.warning(f"{project.name}: канал {ch} недоступен — {e}")
        project.channels = valid_channels

    logging.info(f"✅ Валидация завершена: {total_valid} каналов")

    await update_channel_meta_if_needed()

    # Регистрируем обработчики ОДИН РАЗ — только здесь, после валидации
    for proj in PROJECTS:
        register_project_handlers(proj)

    logging.info("✅ Готово!")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
