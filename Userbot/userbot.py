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

# ---------- кэш дедупликации ----------

_CACHE_DEQUE: deque = deque(maxlen=10_000)
_CACHE_SET: set = set()


def cache_contains(key: str) -> bool:
    return key in _CACHE_SET


def cache_add(key: str) -> None:
    if len(_CACHE_DEQUE) == _CACHE_DEQUE.maxlen:
        evicted = _CACHE_DEQUE[0]
        _CACHE_SET.discard(evicted)
    _CACHE_DEQUE.append(key)
    _CACHE_SET.add(key)


# ---------- конфиг ----------

API_ID    = int(os.getenv("API_ID"))
API_HASH  = os.getenv("API_HASH")
MASTER_ID = int(os.getenv("MASTER_ID"))

# Режим работы: "keyword" | "digest"
# keyword — алерты только по совпадению ключей (классический режим)
# digest  — 100% постов, суммаризация через digest.py
BOT_MODE = os.getenv("BOT_MODE", "keyword").strip().lower()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------- загрузка digest (только в режиме digest) ----------

digest = None

if BOT_MODE == "digest":
    try:
        import digest as _digest_module
        digest = _digest_module
        logging.info("✅ Режим: digest — 100% постов, суммаризация включена")
    except ImportError:
        logging.error(
            "❌ BOT_MODE=digest, но digest.py не найден рядом с userbot.py. "
            "Положите digest.py в ту же папку и перезапустите."
        )
        # Падаем явно — работать в режиме digest без модуля бессмысленно
        raise SystemExit(1)
elif BOT_MODE == "keyword":
    logging.info("✅ Режим: keyword — алерты только по ключевым словам")
else:
    logging.error(f"❌ Неизвестный BOT_MODE='{BOT_MODE}'. Допустимые значения: keyword, digest.")
    raise SystemExit(1)


client = TelegramClient("user", API_ID, API_HASH)


# ---------- структуры ----------

@dataclass
class ProjectConfig:
    name: str
    channels: List[object]
    owner_id: int
    pattern_people: re.Pattern
    pattern_companies: re.Pattern


# ---------- разбор .env ----------

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
        channels_raw = os.getenv(f"CHANNELS_{i}", "")
        owner_raw    = os.getenv(f"OWNER_{i}", "")

        if not channels_raw or not owner_raw:
            continue

        channels: List[object] = []
        for item in channels_raw.split(","):
            item = item.strip()
            if not item:
                continue
            channels.append(int(item) if item.lstrip("-").isdigit() else item)

        owner_id  = int(owner_raw)
        people    = parse_list(f"PEOPLE_{i}")
        companies = parse_list(f"COMPANIES_{i}")

        # В режиме keyword хотя бы одна группа ключей должна быть заполнена
        if BOT_MODE == "keyword" and not people and not companies:
            logging.warning(
                f"PROJECT_{i}: PEOPLE_{i} и COMPANIES_{i} пусты — "
                f"проект не будет генерировать алерты в режиме keyword"
            )

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


# ---------- утилиты ----------

async def safe_call(coro_fn: Callable, *args, max_attempts: int = 5, **kwargs):
    """Повторяет вызов при FloodWait. Создаёт корутин заново на каждой попытке."""
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


def _make_url(event) -> str:
    """Строит прямую ссылку на пост."""
    chat_id_str = str(event.chat_id)
    msg_id = event.id
    if chat_id_str.startswith("-100"):
        return f"https://t.me/c/{chat_id_str[4:]}/{msg_id}"
    username = getattr(event.chat, "username", None) or ""
    return f"https://t.me/{username}/{msg_id}" if username else f"https://t.me/c/{chat_id_str}/{msg_id}"


# ---------- логика режима KEYWORD ----------

def extract_sentences(text: str) -> List[str]:
    text = re.sub(r'\n+', '. ', text)
    sentences = re.split(r'(?<=[\.!?])\s+', text)
    return [s.strip() for s in sentences if len(s.strip()) > 5]


def find_key_contexts(
    text: str,
    pattern_people: re.Pattern,
    pattern_companies: re.Pattern,
) -> Tuple[List[str], List[str]]:
    """
    Возвращает (sentences, contexts).
    contexts — предложения с ключами, выделены жирным.
    Гарантия: contexts не пустой при наличии совпадений.
    """
    sentences = extract_sentences(text)

    if not sentences:
        return [], [text[:500]]

    if len(sentences) <= 3:
        full_text = " ".join(sentences)
        full_text = pattern_people.sub(r'**\g<1>**', full_text)
        full_text = pattern_companies.sub(r'**\g<1>**', full_text)
        return sentences, [full_text]

    used: set = set()
    contexts: List[str] = []

    for sent in sentences:
        if pattern_people.search(sent) or pattern_companies.search(sent):
            clean = re.sub(r'\s+', ' ', sent.strip())
            if clean in used:
                continue
            used.add(clean)
            ctx = pattern_people.sub(r'**\g<1>**', sent)
            ctx = pattern_companies.sub(r'**\g<1>**', ctx)
            contexts.append(ctx)
            if len(contexts) >= 2:
                break

    if not contexts:
        fallback = " ".join(sentences[:2])
        fallback = pattern_people.sub(r'**\g<1>**', fallback)
        fallback = pattern_companies.sub(r'**\g<1>**', fallback)
        contexts = [fallback]
        logging.warning("find_key_contexts: fallback на начало поста")

    return sentences, contexts


def build_keyword_alert(
    text: str,
    title: str,
    url: str,
    tag: str,
    subs: int,
    pattern_people: re.Pattern,
    pattern_companies: re.Pattern,
) -> Optional[str]:
    """
    Режим KEYWORD: формирует алерт с выделенными ключами и контекстом.
    Возвращает None если текст пустой.
    """
    try:
        if not text or not text.strip():
            return None

        sentences, contexts = find_key_contexts(text, pattern_people, pattern_companies)

        all_keys: set = set()
        text_for_keys = " ".join(contexts)
        all_keys.update(pattern_people.findall(text_for_keys))
        all_keys.update(pattern_companies.findall(text_for_keys))
        hashtags = " ".join(f"#{k.replace(' ', '_')}" for k in sorted(all_keys))

        subs_part   = f" (*{subs:,} подписчиков*)" if subs > 0 else ""
        header      = f"**{title or 'Без названия'}**{subs_part}"
        full_header = (f"{tag} " if tag else "") + header
        if hashtags:
            full_header += f" {hashtags}"

        lines = [full_header]

        if len(sentences) > 3:
            lines.extend([" ".join(sentences[:2]), ""])

        for i, ctx in enumerate(contexts, 1):
            if i > 1:
                lines.extend(["", "<...>", ""])
            lines.append(ctx)

        lines.extend(["", f"🔗 Пост ({url})"])
        return "\n".join(lines)

    except Exception as e:
        logging.error(f"build_keyword_alert: ошибка: {e}", exc_info=True)
        try:
            return f"**{title or 'Без названия'}**\n\n{text[:300]}...\n\n🔗 Пост ({url})"
        except Exception:
            return None


# ---------- логика режима DIGEST ----------

def build_digest_alert(
    text: str,
    title: str,
    url: str,
    tag: str,
    subs: int,
) -> Optional[str]:
    """
    Режим DIGEST: шапка канала + суммаризованное тело поста.
    Возвращает None если текст пустой.
    """
    try:
        if not text or not text.strip():
            return None

        subs_part   = f" (*{subs:,} подписчиков*)" if subs > 0 else ""
        header      = f"**{title or 'Без названия'}**{subs_part}"
        full_header = (f"{tag} " if tag else "") + header

        digest_body = digest.process_post(text, url)
        return f"{full_header}\n\n{digest_body}"

    except Exception as e:
        logging.error(f"build_digest_alert: ошибка: {e}", exc_info=True)
        try:
            return f"**{title or 'Без названия'}**\n\n{text[:300]}...\n\n🔗 Пост ({url})"
        except Exception:
            return None


# ---------- метаданные каналов ----------

META_FILE = "channel_meta.json"
META_UPDATE_INTERVAL = 7 * 24 * 60 * 60

CHANNEL_META: Dict[str, object] = {"updated_at": 0, "channels": {}}


def load_channel_tags_from_env() -> Dict[str, str]:
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
    s = str(chat_id)
    if s.startswith('-100'):
        return s
    if chat_id > 0:
        return f"-100{chat_id}"
    return s


def key_from_event(event) -> str:
    username = getattr(event.chat, "username", None)
    if username:
        return username
    return str(event.chat_id)


def get_meta_for_event(event) -> Tuple[str, int]:
    k    = key_from_event(event)
    data = CHANNEL_META.get("channels", {}).get(k)
    if not data:
        return "", 0
    tag  = data.get("tag", "") or ""
    subs = int(data.get("subs", 0) or 0)
    return tag, subs


async def update_channel_meta_if_needed():
    now        = int(time.time())
    updated_at = int(CHANNEL_META.get("updated_at", 0))

    if now - updated_at < META_UPDATE_INTERVAL:
        logging.info("META: обновление не требуется")
        return

    logging.info("META: начинаем недельное обновление")

    tags_from_env = load_channel_tags_from_env()
    all_channels  = get_all_channel_keys_from_projects()
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

        key  = key_from_chat_obj(chat)
        tag  = tags_from_env.get(key, "")
        subs = int(getattr(chat, "participants_count", None) or 0)

        channels_meta[key] = {"tag": tag, "subs": subs}
        await asyncio.sleep(1)

    CHANNEL_META["updated_at"] = now
    CHANNEL_META["channels"]   = channels_meta
    save_meta_cache()
    logging.info("META: обновление завершено")


# ---------- регистрация обработчиков ----------

def register_project_handlers(project: ProjectConfig):
    if not project.channels:
        logging.warning(f"{project.name}: нет валидных каналов, обработчик не регистрируется")
        return

    if BOT_MODE == "keyword":
        _register_keyword_handler(project)
    else:
        _register_digest_handler(project)


def _register_keyword_handler(project: ProjectConfig):
    """
    Режим KEYWORD.
    Слушает канал, реагирует только на посты с совпадением ключей.
    Формат алерта: шапка + контекстные предложения с выделенными ключами.
    """
    @client.on(events.NewMessage(chats=project.channels))
    async def handler(event):
        try:
            msg_key = f"{event.chat_id}:{event.id}"
            if cache_contains(msg_key):
                return
            cache_add(msg_key)

            text = event.message.message or ""
            if not text.strip():
                return

            matches_people    = list(set(project.pattern_people.findall(text)))
            matches_companies = list(set(project.pattern_companies.findall(text)))

            # БЕЗ СОВПАДЕНИЯ — пропускаем
            if not (matches_people or matches_companies):
                return

            logging.info(
                f"🎯 [{project.name}] KEYWORD [{event.chat_id}:{event.id}] "
                f"ключи: {matches_people + matches_companies}"
            )

            url   = _make_url(event)
            tag, subs = get_meta_for_event(event)
            title = getattr(event.chat, "title", None) or "Без названия"

            body = build_keyword_alert(
                text=text,
                title=title,
                url=url,
                tag=tag,
                subs=subs,
                pattern_people=project.pattern_people,
                pattern_companies=project.pattern_companies,
            )

            if body is None:
                logging.error(f"[{project.name}] build_keyword_alert вернул None [{event.chat_id}:{event.id}]")
                return

            result = await safe_call(client.send_message, project.owner_id, body, parse_mode='md')
            if result is None:
                logging.error(f"[{project.name}] не удалось отправить алерт owner={project.owner_id}")

        except Exception as e:
            logging.error(f"[{project.name}] keyword handler error: {e}", exc_info=True)


def _register_digest_handler(project: ProjectConfig):
    """
    Режим DIGEST.
    Слушает канал, забирает 100% постов с текстом.
    Формат: шапка канала + аннотация и тезисы от digest.py.
    Ключи не используются — они игнорируются в этом режиме.
    """
    @client.on(events.NewMessage(chats=project.channels))
    async def handler(event):
        try:
            msg_key = f"{event.chat_id}:{event.id}"
            if cache_contains(msg_key):
                return
            cache_add(msg_key)

            text = event.message.message or ""
            if not text.strip():
                return

            logging.info(f"📥 [{project.name}] DIGEST [{event.chat_id}:{event.id}]")

            url   = _make_url(event)
            tag, subs = get_meta_for_event(event)
            title = getattr(event.chat, "title", None) or "Без названия"

            body = build_digest_alert(
                text=text,
                title=title,
                url=url,
                tag=tag,
                subs=subs,
            )

            if body is None:
                logging.error(f"[{project.name}] build_digest_alert вернул None [{event.chat_id}:{event.id}]")
                return

            result = await safe_call(client.send_message, project.owner_id, body, parse_mode='md')
            if result is None:
                logging.error(f"[{project.name}] не удалось отправить дайджест owner={project.owner_id}")

        except Exception as e:
            logging.error(f"[{project.name}] digest handler error: {e}", exc_info=True)


# ---------- вспомогательная: список разрешённых для digest-команд ----------

def _digest_allowed_users() -> List[int]:
    ids = [MASTER_ID]
    for p in PROJECTS:
        if p.owner_id not in ids:
            ids.append(p.owner_id)
    return ids


# ---------- команды: базовые ----------

@client.on(events.NewMessage(from_users=MASTER_ID, pattern=r"^/status$"))
async def cmd_status(event):
    mode_label = {
        "keyword": "keyword — алерты по ключам",
        "digest":  "digest  — 100% постов + суммаризация",
    }.get(BOT_MODE, BOT_MODE)
    lines = [f"Юзербот запущен.", f"Режим: {mode_label}", ""]
    lines += [
        f"{p.name}: {len(p.channels)} канал(ов), owner={p.owner_id}"
        for p in PROJECTS
    ]
    await event.reply("\n".join(lines))


@client.on(events.NewMessage(from_users=MASTER_ID, pattern=r"^/ping$"))
async def cmd_ping(event):
    await event.reply("pong")


@client.on(events.NewMessage(from_users=MASTER_ID, pattern=r"^/update_meta$"))
async def cmd_update_meta(event):
    await event.reply("Запускаю обновление метаданных...")
    await update_channel_meta_if_needed()
    await event.reply("Обновление завершено.")


# ---------- команды: digest-шкала (только в режиме digest) ----------

@client.on(events.NewMessage(pattern=r"^/digest_status$"))
async def cmd_digest_status(event):
    if event.sender_id not in _digest_allowed_users():
        return
    if BOT_MODE != "digest":
        await event.reply("Команды /digest_* доступны только в режиме BOT_MODE=digest.")
        return
    await event.reply(digest.format_status())


@client.on(events.NewMessage(pattern=r"^/digest_set (short|medium|long) (\d+)$"))
async def cmd_digest_set(event):
    if event.sender_id not in _digest_allowed_users():
        return
    if BOT_MODE != "digest":
        await event.reply("Команды /digest_* доступны только в режиме BOT_MODE=digest.")
        return
    zone  = event.pattern_match.group(1)
    value = int(event.pattern_match.group(2))
    await event.reply(digest.set_limit(zone, value))


@client.on(events.NewMessage(pattern=r"^/digest_theses (medium|long|longread) (\d+)$"))
async def cmd_digest_theses(event):
    if event.sender_id not in _digest_allowed_users():
        return
    if BOT_MODE != "digest":
        await event.reply("Команды /digest_* доступны только в режиме BOT_MODE=digest.")
        return
    zone  = event.pattern_match.group(1)
    value = int(event.pattern_match.group(2))
    await event.reply(digest.set_theses(zone, value))


@client.on(events.NewMessage(pattern=r"^/digest_reset$"))
async def cmd_digest_reset(event):
    if event.sender_id not in _digest_allowed_users():
        return
    if BOT_MODE != "digest":
        await event.reply("Команды /digest_* доступны только в режиме BOT_MODE=digest.")
        return
    digest.reset_config()
    await event.reply("Настройки сброшены к дефолтам.\n\n" + digest.format_status())


# ---------- запуск ----------

async def main():
    await client.start()
    load_meta_cache()
    logging.info(f"Userbot started | режим: {BOT_MODE}")

    if BOT_MODE == "digest":
        digest.load_config()
        digest._ensure_nltk()
        logging.info("digest: конфиг загружен, NLTK готов")

    total_valid = 0
    for project in PROJECTS:
        valid_channels = []
        for ch in project.channels:
            try:
                entity = await client.get_entity(ch)
                valid_channels.append(entity.username if entity.username else ch)
                total_valid += 1
            except Exception as e:
                logging.warning(f"{project.name}: канал {ch} недоступен — {e}")
        project.channels = valid_channels

    logging.info(f"✅ Валидация завершена: {total_valid} каналов")

    await update_channel_meta_if_needed()

    for proj in PROJECTS:
        register_project_handlers(proj)

    logging.info("✅ Готово!")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
