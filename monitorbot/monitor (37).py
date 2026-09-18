"""
monitor.py — TGStat monitor with Telegram bot commands.

Структура проектов:
    projects/
      project_a/
        .env          ← настройки проекта
        config.json   ← сохраняется ботом
        results.json
        monitor.log
        night_report.json
      project_b/
        .env
        ...
    monitor.py        ← один скрипт для всех проектов
    rate_limiter.py   ← рядом
    tgstat_lock.json  ← shared, создаётся автоматически

Запуск:
    python monitor.py --env projects/project_a/.env
    python monitor.py --env projects/project_b/.env
"""

import argparse
import os
import asyncio
import aiohttp
import logging
import logging.handlers
import json
import re
import sys
import io
from collections import deque, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Аргументы — парсим ДО load_dotenv ────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--env", default=".env", help="Path to .env file")
parser.add_argument("--offset", type=int, default=0, help="Initial Telegram offset (used on restart)")
args = parser.parse_args()

ENV_DIR = os.path.dirname(os.path.abspath(args.env))

from dotenv import load_dotenv
load_dotenv(args.env, override=True)

from rate_limiter import TGStatRateLimiter, DEFAULT_LOCK_FILE
from config import load_config, save_config, parse_key_tags, DEFAULT_CONFIG
from storage import Storage
from text_filter import TextFilter, _strip_html, _text_similarity
from formatter import escape_md, md_bold, md_link, _FRAGMENT_SEP

# ── Фикс UTF-8 для Windows консоли ───────────────────────────────────────────
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── SSL фикс для Windows + Telegram API ──────────────────────────────────────
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

import pytz

# ── Лемматизация (pymorphy3 / pymorphy2, с fallback) ─────────────────────────
# Установка: pip install pymorphy3
# Если не установлен — используется простой подстроковый поиск (старое поведение)
try:
    import pymorphy3 as _pymorphy
    _MORPH_AVAILABLE = True
except ImportError:
    try:
        import pymorphy2 as _pymorphy  # type: ignore
        _MORPH_AVAILABLE = True
    except ImportError:
        _MORPH_AVAILABLE = False
        _MORPH_WARNING = True


def project_path(filename: str) -> str:
    if os.path.isabs(filename):
        return filename
    return os.path.join(ENV_DIR, filename)


PROJECT_NAME = os.getenv("PROJECT_NAME", os.path.basename(ENV_DIR))

log_file = project_path(os.getenv("LOG_FILE", "monitor.log"))
_log_handler = logging.handlers.RotatingFileHandler(
    log_file, maxBytes=5*1024*1024, backupCount=2, encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
    handlers=[
        _log_handler,
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(PROJECT_NAME)
if not _MORPH_AVAILABLE and locals().get("_MORPH_WARNING"):
    logger.warning("pymorphy3/pymorphy2 not installed — using substring matching")

# Интервал опроса — читаем один раз
MONITOR_INTERVAL_MIN = float(os.getenv("MONITOR_INTERVAL_MIN", 1.5))

# ── Описание бота ─────────────────────────────────────────────────────────────
BOT_DESCRIPTION = "Я — Мониторинговый помошник V1.5.1. Слежу за постами в Telegram-каналах по ключевым словам. ко мне можно обратиться и с помощью команд внести измеенения в мою работу набрав /help"

# ── Конфиг ────────────────────────────────────────────────────────────────────
CONFIG_FILE = project_path(os.getenv("CONFIG_FILE", "config.json"))

# int-ключи: ENV_VAR → cfg_key (используются в config.py через load_config)
ENV_INT_OVERRIDES = {
    "SEARCH_MIN_SUBSCRIBERS": "min_subscribers",
    "SEARCH_MIN_VIEWS":       "min_views",
    "REP_THRESHOLD":          "rep_threshold",
    "START_HOUR":             "start_hour",
    "END_HOUR":               "end_hour",
    "FRAGMENT_MAX_WORDS":     "fragment_max_words",
}

# bool-ключи
ENV_BOOL_OVERRIDES = {
    "KEYS_FILTER_ENABLED": "keys_filter_enabled",
    "SKIP_PRIVATE":        "skip_private",
    "TEXT_BLACKLIST_HARD":  "blacklist_hard",
}

# float-ключи
ENV_FLOAT_OVERRIDES = {
    "SIMILARITY_THRESHOLD": "similarity_threshold",
}


def normalize_channel(raw: str) -> str:
    s = re.sub(r'^https?://[^/]+/', '', raw.strip().rstrip('/'))
    s = s.lstrip('@')
    return ('@' + s) if s else ''


# ── Состояния диалога ─────────────────────────────────────────────────────────
user_states: dict[str, str] = {}

HELP_TEXT = """Команды бота:

/name — описание бота
/status — текущие настройки проекта
/pause | /resume — приостановить/возобновить мониторинг
/night_report — вкл/выкл утренний отчёт
/test — запустить цикл немедленно
/schedule — рабочий диапазон часов
/help — эта справка

/subscribers — мин. подписчики канала
/views — мин. просмотры поста
/fragment — макс. слов в ключевом фрагменте
/rep_threshold — порог подписчиков для показа посева (похожего текста из другого канала)
/similarity_threshold — порог схожести текстов для определения посева (0.0–1.0, по умолч. 0.85)

/debug — последние RAW-записи из лога
/duplicates <url> — найти дубли поста за 8ч
/block_text <url> — заблокировать текст поста
/text_blacklist — список заблокированных текстов
/reset — сбросить список виденных постов
/last <N> — последние N найденных постов
/skipped [N] — пропущенные посты за 12ч
/seen [канал|url] — виденные URL: топ каналов / по каналу / проверить URL

/exceptions — каналы-исключения
/keys — показать ключи и минус-слова / вкл/выкл фильтр
/keys_edit — редактировать минус-слова
/reverse_digest — срочный отчёт за последние N часов

/restart — перезапустить скрипт
/blacklist_mode — переключить режим блокировки текстов (HARD/SOFT)
/block_tag [тег] — заблокировать тег на N часов / список тегов
/unblock_tag <тег> — разблокировать тег досрочно"""


class TGStatMonitor:
    BASE_URL = "https://api.tgstat.ru/posts/search"

    def __init__(self):
        self.cfg = load_config(CONFIG_FILE)
        self.token = os.getenv("TGSTAT_TOKEN")
        self.tz = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))
        self.telegram_bot = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", 3000))

        self.output_file = project_path(os.getenv("OUTPUT_FILE", "results.json"))
        self.night_report_file = project_path(os.getenv("NIGHT_REPORT_FILE", "night_report.json"))

        if not self.token:
            raise ValueError("TGSTAT_TOKEN required in .env!")

        self._paused: bool = False
        self._force_run: bool = False

        self._log_buffer: deque[str] = deque(maxlen=20)
        self._recent_sent: deque[dict] = deque(maxlen=50)
        self.skipped_links: deque[dict] = deque(maxlen=5000)

        # night_report_enabled: вкл/выкл утренний отчёт
        _nr_env = os.getenv("NIGHT_REPORT_ENABLED", "")
        self.night_report_enabled: bool = (
            _nr_env.strip().lower() not in ("0", "false", "no", "off")
            if _nr_env else True
        )

        self.key_tags: dict[str, dict] = parse_key_tags(os.getenv("SEARCH_KEY_TAGS", ""))
        if self.key_tags:
            logger.debug(f"Key tags: {self.key_tags}")

        # Модули хранения и статистики
        self.storage = Storage(self.output_file)
        self.stats = Stats(self.night_report_file, self.tz)

        # Shortcuts для обратной совместимости внутри класса
        self.seen_urls_by_channel = self.storage.seen_urls_by_channel
        self.text_blacklist        = self.storage.text_blacklist
        self.seen_texts            = self.storage.seen_texts
        self.daily_stats           = self.stats.daily_stats

        # ── Блокировка тегов — загружаем из storage, НЕ переопределяем ──────
        # ВАЖНО: эти поля инициализируются один раз здесь.
        # storage уже загрузил данные из файла в __init__.
        self._tag_blocked:    dict[str, float] = self.storage._tag_blocked
        self._tag_alert_sent: set[str]         = self.storage._tag_alert_sent

        # ── Мульти-запросы ────────────────────────────────────────────────────
        _raw_queries = os.getenv("SEARCH_QUERIES", "")
        if _raw_queries.strip():
            self.search_queries: list[str] = [
                q.strip() for q in _raw_queries.split("|||") if q.strip()
            ]
        else:
            _single = os.getenv("SEARCH_QUERY", "")
            self.search_queries = [_single] if _single.strip() else []

        if not self.search_queries:
            raise ValueError("Нужен SEARCH_QUERIES или SEARCH_QUERY в .env!")
        logger.info(f"Search queries ({len(self.search_queries)}): "
                    + " ||| ".join(f"[{i+1}] {q[:60]}{'…' if len(q)>60 else ''}"
                                   for i, q in enumerate(self.search_queries)))

        # ── Лемматизация ─────────────────────────────────────────────────────
        if _MORPH_AVAILABLE:
            self.morph = _pymorphy.MorphAnalyzer()
            logger.info("Morphology: enabled (pymorphy)")
        else:
            self.morph = None

        self.text_filter = TextFilter(self.key_tags, self.morph)
        # Shortcuts на кэши TextFilter (только чтение — не переопределять)
        self._lemma_cache  = self.text_filter._lemma_cache
        self._key_lemmas   = self.text_filter._key_lemmas
        self._minus_lemmas = self.text_filter._minus_lemmas

        # ── Счётчик упоминаний по тегам (скользящий час) ─────────────────────
        self._tag_mention_counts: dict[str, deque] = {}
        self._tag_alert_threshold: int = int(os.getenv("TAG_ALERT_THRESHOLD", 3))

        self._expire_seen_texts()

        # Offset из аргумента --offset (передаётся при /restart и watchdog)
        self._tg_offset: int = args.offset
        if self._tg_offset:
            logger.info(f"Offset restored from args: {self._tg_offset}")

        lock_file = os.getenv("RATE_LIMITER_FILE") or DEFAULT_LOCK_FILE
        self.rate_limiter = TGStatRateLimiter(lock_file, PROJECT_NAME)
        logger.debug(f"RateLimiter: {lock_file}")

        # Persistent Telegram session для send_telegram
        self._tg_session: aiohttp.ClientSession | None = None

    # ── cfg shortcut ──────────────────────────────────────────────────────────

    def _c(self, key: str):
        return self.cfg.get(key, DEFAULT_CONFIG.get(key))

    @property
    def min_subscribers(self) -> int:        return self._c("min_subscribers")
    @property
    def min_views(self) -> int:              return self._c("min_views")
    @property
    def rep_threshold(self) -> int:          return self._c("rep_threshold")
    @property
    def similarity_threshold(self) -> float: return float(self._c("similarity_threshold"))
    @property
    def blacklist_hard(self) -> bool:        return bool(self._c("blacklist_hard"))
    @property
    def start_hour(self) -> int:             return self._c("start_hour")
    @property
    def end_hour(self) -> int:               return self._c("end_hour")
    @property
    def excluded_channels(self) -> list:     return self._c("excluded_channels")
    @property
    def fragment_max_words(self) -> int:     return self._c("fragment_max_words")
    @property
    def keys_filter_enabled(self) -> bool:   return self._c("keys_filter_enabled")
    @property
    def skip_private(self) -> bool:          return self._c("skip_private")
    @property
    def seen_ttl_days(self) -> int:          return self._c("seen_ttl_days")

    # ── seen_urls helpers ─────────────────────────────────────────────────────

    def _is_seen(self, channel_id: str, url: str) -> bool:
        return self.storage.is_seen(channel_id, url)

    def _mark_seen(self, channel_id: str, url: str):
        self.storage.mark_seen(channel_id, url)
        self.seen_urls_by_channel = self.storage.seen_urls_by_channel

    def _persist_seen(self):
        self.storage.save()

    def _rotate_seen(self):
        self.storage.rotate_seen(self.seen_ttl_days)
        self.seen_urls_by_channel = self.storage.seen_urls_by_channel

    # ── Persist ───────────────────────────────────────────────────────────────

    def save_all_data(self, posts: list):
        for p in posts:
            p.pop("__lemmas", None)

        self._rotate_seen()
        total = sum(len(v) for v in self.seen_urls_by_channel.values())
        data = {
            "meta": {
                "generated_at": datetime.now().isoformat(),
                "project": PROJECT_NAME,
                "queries": self.search_queries,
                "queries_count": len(self.search_queries),
                "key_tags": {k: v["tag"] for k, v in self.key_tags.items()},
                "min_subscribers": self.min_subscribers,
                "min_views": self.min_views,
                "seen_total": total,
            },
            "seen_urls_by_channel": self.seen_urls_by_channel,
            "recent_posts": posts[-100:],
            "text_blacklist": list(self.text_blacklist),
            "seen_texts": {str(k): v for k, v in self.seen_texts.items()},
            "tag_blocked": self._tag_blocked,
            "tag_alert_sent": list(self._tag_alert_sent),
            "night_report_enabled": self.night_report_enabled,
        }
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── TGStat fetch ──────────────────────────────────────────────────────────

    def _build_params(self, q: Optional[str] = None, start_date: Optional[int] = None,
                      end_date: Optional[int] = None, limit: Optional[int] = None) -> dict:
        params = {
            "token":          self.token,
            "q":              q if q is not None else self.search_queries[0],
            "limit":          limit if limit is not None else int(os.getenv("SEARCH_LIMIT", 50)),
            "offset":         0,
            "peerType":       "channel",
            "strongSearch":   0,
            "hideForwards":   1,
            "hideDeleted":    1,
            "extended":       1,
            "extendedSyntax": 1,
            "country":        "ru",
            "language":       "russian",
        }
        if start_date: params["startDate"] = str(start_date)
        if end_date:   params["endDate"]   = str(end_date)
        if self.min_subscribers > 0:
            params["minSubscribers"] = self.min_subscribers
        return params

    async def fetch_posts(self, session: aiohttp.ClientSession,
                          start_date: Optional[int] = None, end_date: Optional[int] = None,
                          q: Optional[str] = None, limit: Optional[int] = None) -> list:
        await self.rate_limiter.acquire()
        params = self._build_params(q=q, start_date=start_date, end_date=end_date, limit=limit)
        try:
            q_str = params.get("q", "")
            q_preview = (q_str[:80] + "…") if len(q_str) > 80 else q_str
            logger.info(f"TGStat query: {q_preview}")
            async with session.get(self.BASE_URL, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"TGStat error: {resp.status}")
                    return []
                data = await resp.json()
                if data.get("status") != "ok":
                    logger.error(f"TGStat error: {data}")
                    return []
                items = data["response"]["items"]
                ch_map = {ch["id"]: ch for ch in data["response"].get("channels", [])}
                for item in items:
                    item["__channel"] = ch_map.get(item.get("channel_id"), {})
                logger.info(f"Items: {len(items)}")
                return items
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return []

    async def fetch_all_queries(self, session: aiohttp.ClientSession,
                                start_date: Optional[int] = None,
                                end_date: Optional[int] = None) -> list:
        """
        Последовательно выполняет все запросы из self.search_queries.
        Дедуплицирует результаты по URL.
        """
        seen_urls: set[str] = set()
        all_items: list[dict] = []
        interval = float(os.getenv("TGSTAT_MIN_INTERVAL_SEC", 1.5))

        for idx, query in enumerate(self.search_queries):
            logger.info(
                f"Query [{idx+1}/{len(self.search_queries)}]: "
                f"{query[:80]}{'…' if len(query) > 80 else ''}"
            )
            items = await self.fetch_posts(session, start_date=start_date,
                                           end_date=end_date, q=query)
            new_count = 0
            for item in items:
                url = item.get("link")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_items.append(item)
                    new_count += 1

            if len(self.search_queries) > 1:
                logger.info(f"Query [{idx+1}] → {len(items)} posts, {new_count} new, total: {len(all_items)}")
            if idx < len(self.search_queries) - 1:
                await asyncio.sleep(interval)

        logger.info(f"Fetched: {len(all_items)} posts")
        return all_items

    async def send_reverse_digest(self, chat_id: str, hours: int):
        """Срочный отчёт за последние N часов."""
        await self.send_plain(f"🔍 Собираю посты за последние {hours} час(ов)...", chat_id)

        end_ts   = int(datetime.now(timezone.utc).timestamp())
        start_ts = end_ts - hours * 3600

        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(connector=connector) as session:
            posts = await self.fetch_all_queries(session, start_ts, end_ts)

        if not posts:
            await self.send_plain(f"📭 За последние {hours} час(ов) ничего не найдено.", chat_id)
            return

        unique_posts = []
        for p in posts:
            url        = p.get("link")
            channel_id = str(p.get("channel_id", "__unknown__"))
            if not url or not self._passes_filters(p):
                continue
            if self._is_seen(channel_id, url):
                continue
            unique_posts.append(p)

        if not unique_posts:
            await self.send_plain(f"📭 После фильтрации постов за {hours} час(ов) не осталось.", chat_id)
            return

        # Заголовок — plain text, без MarkdownV2
        start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M")
        end_str   = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M")
        await self.send_plain(
            f"📊 Срочный отчёт за последние {hours} час(ов)\n"
            f"Найдено уникальных постов: {len(unique_posts)}\n"
            f"Период: {start_str} — {end_str}",
            chat_id
        )

        for post in unique_posts[:20]:
            await self.send_telegram(self.format_post_message(post), chat_id)
            await asyncio.sleep(0.5)

        if len(unique_posts) > 20:
            await self.send_plain(f"... и ещё {len(unique_posts) - 20} постов (не показаны).", chat_id)

        for post in unique_posts:
            channel_id = str(post.get("channel_id", "__unknown__"))
            self._mark_seen(channel_id, post["link"])
        self._persist_seen()

    # ── Текст: ключевые фрагменты ─────────────────────────────────────────────

    def _find_matched_keys(self, post: dict) -> list[str]:
        """Делегируем в TextFilter. Кэш лемм хранится в post["__lemmas"]."""
        return self.text_filter.find_matched_keys(post)

    def extract_sentences(self, text: str) -> list[str]:
        parts = [s.strip() for s in re.split(r'[.!?\n]+', text) if len(s.strip()) > 3]
        return parts or ([_strip_html(text.strip())[:100]] if text.strip() else [])

    def find_key_mentions(self, text: str) -> list[dict]:
        """
        Строит фрагменты с ключами. Дедупликация: два ключа в одних предложениях
        → один фрагмент. Перекрывающиеся группы мержатся.
        """
        sentences = self.extract_sentences(text)
        groups: dict[frozenset, list[str]] = {}

        for i, sent in enumerate(sentences):
            sent_lower = sent.lower()
            for key, meta in self.key_tags.items():
                key_search = key.lower().replace('_', ' ')
                if key_search not in sent_lower:
                    continue
                if any(mw in sent_lower for mw in meta.get("minus", [])):
                    continue
                idxs = frozenset([i] + ([i + 1] if i + 1 < len(sentences) else []))
                if key not in groups.setdefault(idxs, []):
                    groups[idxs].append(key)

        merged: list[tuple[set, list]] = []
        for idxs, keys in groups.items():
            idxs_set = set(idxs)
            for existing in merged:
                if existing[0] & idxs_set:
                    existing[0].update(idxs_set)
                    existing[1].extend(k for k in keys if k not in existing[1])
                    break
            else:
                merged.append((idxs_set, list(keys)))

        mentions = []
        for idxs_set, keys in merged:
            context = _strip_html(' '.join(sentences[i] for i in sorted(idxs_set) if i < len(sentences)))
            words = context.split()
            if len(words) > self.fragment_max_words:
                context = ' '.join(words[:self.fragment_max_words]) + '…'
            mentions.append({
                "context": context,
                "keys": keys,
                "tags": [self.key_tags[k]["tag"] for k in keys if k in self.key_tags],
            })
        return mentions

    def _highlight_keys_in_fragment(self, context: str, keys: list[str]) -> str:
        escaped = escape_md(context)
        for key in keys:
            key_display = key.replace('_', ' ')
            escaped_key = escape_md(key_display)
            escaped = re.sub(
                re.escape(escaped_key),
                lambda m: md_bold(escape_md(m.group(0).replace('\\', '').upper())),
                escaped, flags=re.IGNORECASE
            )
        return escaped

    # ── Форматирование ────────────────────────────────────────────────────────

    def format_post_message(self, post: dict, label: Optional[str] = None,
                            is_repeat: bool = False) -> str:
        ch             = post.get("__channel", {})
        channel_title  = ch.get("title", "No title")
        subscribers    = ch.get("participants_count", 0)
        channel_user   = ch.get("username", "").lstrip("@")
        views          = post.get("views", 0) or 0
        post_url       = post.get("link", "") or ""

        raw_text = post.get("text", "") or ""
        post_text = re.sub(
            r'<a\s+href=[\'"][^\'"]*[\'"][^>]*>(.*?)</a>',
            lambda m: m.group(1).strip(),
            raw_text, flags=re.IGNORECASE | re.DOTALL
        )
        post_text = _strip_html(post_text).strip()
        if len(post_text) < 5:
            post_text = "[только медиа]"

        clean_sentences = [_strip_html(s) for s in self.extract_sentences(post_text)]
        first_sentence  = clean_sentences[0] if clean_sentences else _strip_html(post_text[:200]) or "—"

        key_mentions = self.find_key_mentions(post_text)
        unique_tags  = list(dict.fromkeys(t for m in key_mentions for t in m["tags"]))

        url_clean = re.sub(r'^https?://', '', post_url)

        header_parts = [md_bold(escape_md(channel_title))]
        if label:
            header_parts.append(escape_md(f" {label}"))
        elif is_repeat:
            header_parts.append(escape_md(" ♻️ повтор"))
        if unique_tags:
            header_parts.append(" " + " ".join(f"\\#{escape_md(t)}" for t in unique_tags))
        header_parts.append(
            f" \\(__{escape_md(f'{subscribers:,}')} подп\\. \\| "
            f"{escape_md(f'{views:,}')} просм\\.__\\)"
        )
        header = "".join(header_parts)
        footer = f"\n🔗 {md_link(channel_user or 'Пост', 'https://' + url_clean)}"

        parts = [f"{header}\n\n{escape_md(first_sentence)}\n"]
        if key_mentions:
            parts.append(_FRAGMENT_SEP)
            for i, m in enumerate(key_mentions):
                if i > 0:
                    parts.append(_FRAGMENT_SEP)
                parts.append(f"\n{self._highlight_keys_in_fragment(m['context'], m['keys'])}")

        message = "".join(parts) + footer

        if len(message) > self.MAX_MESSAGE_LENGTH:
            base      = f"{header}\n\n{escape_md(first_sentence)}\n"
            truncated = base + _FRAGMENT_SEP if key_mentions else base
            for i, m in enumerate(key_mentions):
                candidate = truncated + (_FRAGMENT_SEP if i > 0 else "") + \
                            f"\n{self._highlight_keys_in_fragment(m['context'], m['keys'])}"
                if len(candidate + footer) <= self.MAX_MESSAGE_LENGTH:
                    truncated = candidate
                else:
                    break
            message = truncated + footer

        return message

    # ── Telegram send ─────────────────────────────────────────────────────────

    async def _get_tg_session(self) -> aiohttp.ClientSession:
        if self._tg_session is None or self._tg_session.closed:
            connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
            self._tg_session = aiohttp.ClientSession(connector=connector)
        return self._tg_session

    async def _close_tg_session(self):
        """Корректно закрывает _tg_session."""
        if self._tg_session and not self._tg_session.closed:
            try:
                await self._tg_session.close()
            except Exception:
                pass
        self._tg_session = None
        await asyncio.sleep(0.1)

    async def send_telegram(self, message: str, chat_id: Optional[str] = None,
                            parse_mode: Optional[str] = "MarkdownV2"):
        target = chat_id or self.chat_id
        if not self.telegram_bot or not target:
            logger.warning(f"[no tg] bot={bool(self.telegram_bot)} target={target!r} — message not sent")
            return
        logger.debug(f"Sending to {target}: {message[:60]!r}")
        url     = f"https://api.telegram.org/bot{self.telegram_bot}/sendMessage"
        payload = {"chat_id": target, "text": message, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        session = await self._get_tg_session()
        for attempt in range(5):
            try:
                async with session.post(url, json=payload) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        return
                    if resp.status == 429:
                        retry_after = json.loads(body).get("parameters", {}).get("retry_after", 1)
                        logger.warning(f"Telegram 429, wait {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue
                    logger.error(f"Telegram {resp.status}: {body}")
                    logger.debug(f"Failed message:\n{message}")
                    return
            except Exception as e:
                logger.error(f"Telegram attempt {attempt + 1}: {type(e).__name__}: {e}")
                await self._close_tg_session()
                session = await self._get_tg_session()
                await asyncio.sleep(1)

    async def send_plain(self, message: str, chat_id: Optional[str] = None):
        await self.send_telegram(message, chat_id, parse_mode=None)

    # ── Фильтрация ────────────────────────────────────────────────────────────

    def _log_skip(self, url: str, reason: str, subs: int = 0,
                  keys: Optional[list] = None):
        """Записывает скип в лог и в skipped_links для команды /skipped."""
        if keys is None:
            keys = []
        url_short = url.replace("https://", "").replace("http://", "")
        logger.info(f"Skip {reason}: {url_short}")
        self.skipped_links.append({
            "ts":     datetime.now().isoformat(),
            "url":    url,
            "reason": reason,
            "subs":   subs,
            "keys":   keys,
        })

    def _passes_filters(self, post: dict) -> bool:
        ch       = post.get("__channel", {})
        subs     = ch.get("participants_count", 0)
        views    = post.get("views", 0) or 0
        url      = post.get("link", "")
        username = normalize_channel(ch.get("username", ""))

        if self.skip_private and ("t.me/c/" in url or not ch.get("username", "")):
            self._log_skip(url, "private", subs=subs)
            return False
        if subs < self.min_subscribers:
            self._log_skip(url, f"low_subs({subs}<{self.min_subscribers})", subs=subs)
            return False
        if views < self.min_views:
            self._log_skip(url, f"low_views({views}<{self.min_views})", subs=subs)
            return False
        if username and username in self.excluded_channels:
            self._log_skip(url, "excluded", subs=subs)
            return False
        if self.key_tags:
            matched = self._find_matched_keys(post)
            if not matched:
                raw_text = _strip_html(post.get("text", "") or "")
                if self.morph:
                    text_lemmas = post.get("__lemmas") or self.text_filter._lemmatize_text(raw_text)
                    for key, key_lemmas in self._key_lemmas.items():
                        if key_lemmas and key_lemmas.issubset(text_lemmas):
                            for ml in self._minus_lemmas.get(key, []):
                                if ml.issubset(text_lemmas):
                                    self._log_skip(url, f"minus_word({key})", subs=subs, keys=[key])
                                    break
                else:
                    text_lower = raw_text.lower()
                    for key, meta in self.key_tags.items():
                        if key.lower() in text_lower:
                            hit = next((mw for mw in meta.get("minus", []) if mw in text_lower), None)
                            if hit:
                                self._log_skip(url, f"minus_word({key}:{hit})", subs=subs, keys=[key])
                self._log_skip(url, "no_keys", subs=subs)
                return False
            mentions = self.find_key_mentions(post.get("text", "") or "")
            if not mentions:
                self._log_skip(url, "no_key_in_text(lemma_only)", subs=subs, keys=matched)
                return False
        return True

    # ── seen_texts helpers ────────────────────────────────────────────────────

    @staticmethod
    def _text_fingerprint(text: str) -> tuple[int, str]:
        clean = re.sub(r'\s+', ' ', _strip_html(text)).strip().lower()
        return hash(clean[:200]), clean

    def _expire_seen_texts(self):
        now       = datetime.now(self.tz)
        day_start = self.tz.localize(
            datetime(now.year, now.month, now.day, 0, 0, 0)
        ).timestamp()
        expired = [fp for fp, meta in self.seen_texts.items()
                   if meta.get("ts", 0) < day_start]
        for fp in expired:
            del self.seen_texts[fp]
        if expired:
            logger.info(f"Expired {len(expired)} seen_texts entries (new calendar day)")

    def _find_similar_seen(self, text: str) -> Optional[dict]:
        self._expire_seen_texts()
        _, clean = self._text_fingerprint(text)
        if not clean:
            return None
        threshold = self.similarity_threshold
        for fp, meta in self.seen_texts.items():
            stored = meta.get("clean", "")
            if not stored:
                continue
            if clean in stored or stored in clean:
                return meta
            if _text_similarity(clean[:300], stored[:300]) >= threshold:
                return meta
        return None

    def _register_seen_text(self, post: dict):
        text    = post.get("text", "") or ""
        fp, clean = self._text_fingerprint(text)
        ch      = post.get("__channel", {})
        self.seen_texts[fp] = {
            "url":          post.get("link", ""),
            "ts":           datetime.now(self.tz).timestamp(),
            "channel":      ch.get("title", "?"),
            "subs":         ch.get("participants_count", 0),
            "clean":        clean,
            "text_preview": clean[:120],
        }

    def _is_tag_blocked(self, tag: str) -> bool:
        until = self._tag_blocked.get(tag)
        if until and datetime.now(timezone.utc).timestamp() < until:
            return True
        if until:
            del self._tag_blocked[tag]
            self._tag_alert_sent.discard(tag)
        return False

    def _block_tag(self, tag: str, hours: int):
        until    = datetime.now(timezone.utc).timestamp() + hours * 3600
        self._tag_blocked[tag] = until
        self._tag_alert_sent.discard(tag)
        until_dt = datetime.fromtimestamp(until).strftime("%H:%M %d.%m")
        logger.info(f"Tag '{tag}' blocked for {hours}h until {until_dt}")

    async def _track_tag_mentions(self, tags: list[str]):
        now    = datetime.now(timezone.utc).timestamp()
        window = 3600.0

        for tag in tags:
            if self._is_tag_blocked(tag):
                continue
            if tag in self._tag_alert_sent:
                continue

            q = self._tag_mention_counts.setdefault(tag, deque())
            q.append(now)
            while q and now - q[0] > window:
                q.popleft()

            if len(q) >= self._tag_alert_threshold:
                self._tag_alert_sent.add(tag)
                logger.info(f"Tag alert: #{tag} — {len(q)} упоминаний за час")
                await self.send_plain(
                    f"⚠️ Тег #{tag} упомянут {len(q)} раз(а) за последний час.\n"
                    f"Хотите временно заблокировать его?\n\n"
                    f"Ответьте: /block_tag {tag}",
                    self.chat_id
                )

    def _update_daily_stats(self, matched_keys: list[str]):
        if matched_keys:
            for key in matched_keys:
                self.daily_stats[key] = self.daily_stats.get(key, 0) + 1
        else:
            self.daily_stats["__other__"] = self.daily_stats.get("__other__", 0) + 1

    def _log_and_buffer(self, post: dict, label: str, matched_keys: list[str]):
        ch   = post.get("__channel", {})
        url  = post.get("link", "")
        text = post.get("text", "") or ""
        keys_str = ", ".join(matched_keys) if matched_keys else "—"

        self.storage.record_alert(
            url=url,
            channel=ch.get("title", ""),
            keys=matched_keys,
            subs=ch.get("participants_count", 0),
        )
        tags = [self.key_tags[k]["tag"] for k in matched_keys if k in self.key_tags]
        self.stats.record_alert(tags=tags)

        snippet = ""
        if matched_keys:
            idx = text.lower().find(matched_keys[0].lower())
            if idx >= 0:
                start, end = max(0, idx - 30), min(len(text), idx + len(matched_keys[0]) + 50)
                snippet = " | «" + text[start:end].replace("\n", " ").strip() + "»"

        line = (f"[{label}] {ch.get('title','?')} "
                f"(subs={ch.get('participants_count',0)}, views={post.get('views',0) or 0}) | "
                f"keys: {keys_str}{snippet} | {url}")
        logger.info(line)
        self._log_buffer.append(line)

    async def process_posts(self, posts: list):
        new_posts:  list[tuple[dict, list]]        = []
        seed_posts: list[tuple[dict, list, dict]]  = []

        for p in posts:
            ch = p.get("__channel", {})
            logger.debug(
                f"[RAW] url={p.get('link','')} | ch={ch.get('title','?')} | "
                f"subs={ch.get('participants_count','MISSING')} | "
                f"views={p.get('views','MISSING')} | "
                f"text={str(p.get('text',''))[:80].replace(chr(10),' ')!r}"
            )

        for p in posts:
            url = p.get("link")
            if not url or not self._passes_filters(p):
                continue

            _, clean   = self._text_fingerprint(p.get("text", "") or "")
            text_hash  = hash(clean)
            if text_hash in self.text_blacklist:
                subs_bl    = p.get("__channel", {}).get("participants_count", 0)
                channel_id = str(p.get("channel_id", "__unknown__"))
                if self.blacklist_hard or subs_bl < self.rep_threshold:
                    self._log_skip(url, f"text_blacklisted(hard={self.blacklist_hard})", subs=subs_bl)
                    self._mark_seen(channel_id, url)
                    continue

            channel_id = str(p.get("channel_id", "__unknown__"))
            if self._is_seen(channel_id, url):
                continue

            matched_keys = self._find_matched_keys(p)
            similar      = self._find_similar_seen(p.get("text", "") or "")
            if similar:
                seed_posts.append((p, matched_keys, similar))
            else:
                new_posts.append((p, matched_keys))

        logger.info(f"New: {len(new_posts)}, seeds: {len(seed_posts)}")

        for post, matched_keys in new_posts:
            active_tags  = [self.key_tags[k]["tag"] for k in matched_keys
                            if k in self.key_tags and not self._is_tag_blocked(self.key_tags[k]["tag"])]
            blocked_tags = [self.key_tags[k]["tag"] for k in matched_keys
                            if k in self.key_tags and self._is_tag_blocked(self.key_tags[k]["tag"])]
            if self.key_tags and blocked_tags and not active_tags:
                self._log_skip(post.get("link", ""), f"tag_blocked({','.join(blocked_tags)})")
                channel_id = str(post.get("channel_id", "__unknown__"))
                self._mark_seen(channel_id, post["link"])
                self._persist_seen()
                continue

            self._log_and_buffer(post, "NEW", matched_keys)
            await self.send_telegram(self.format_post_message(post))
            channel_id = str(post.get("channel_id", "__unknown__"))
            self._mark_seen(channel_id, post["link"])
            self._register_seen_text(post)
            self._persist_seen()
            self._update_daily_stats(matched_keys)
            self._recent_sent.append(post)
            if active_tags:
                await self._track_tag_mentions(active_tags)
            await asyncio.sleep(0.5)

        for post, matched_keys, original in seed_posts:
            subs       = post.get("__channel", {}).get("participants_count", 0)
            url        = post.get("link", "")
            channel_id = str(post.get("channel_id", "__unknown__"))

            if subs < self.rep_threshold:
                self._log_skip(url, f"seed_low_subs({subs}<{self.rep_threshold})")
                self._mark_seen(channel_id, url)
                self._persist_seen()
                continue

            active_tags = [self.key_tags[k]["tag"] for k in matched_keys
                           if k in self.key_tags and not self._is_tag_blocked(self.key_tags[k]["tag"])]
            if self.key_tags and matched_keys and not active_tags:
                self._log_skip(url, "seed_tag_blocked")
                self._mark_seen(channel_id, url)
                self._persist_seen()
                continue

            orig_info = f"🔁 Посев. Оригинал: {original.get('channel','?')} — {original.get('url','')}"
            self._log_and_buffer(post, "SEED", matched_keys)
            await self.send_telegram(
                self.format_post_message(post, label="🔁 ПОСЕВ") + f"\n\n{orig_info}"
            )
            self._mark_seen(channel_id, url)
            self._persist_seen()
            self._update_daily_stats(matched_keys)
            self._recent_sent.append(post)
            if active_tags:
                await self._track_tag_mentions(active_tags)
            await asyncio.sleep(0.5)

        self.save_all_data(posts)

    # ── /duplicates ───────────────────────────────────────────────────────────

    async def handle_duplicates(self, post_url: str, reply_chat_id: str):
        await self.send_plain(f"🔍 Ищу дубли для {post_url}...", reply_chat_id)

        source_post = next((p for p in self._recent_sent if p.get("link") == post_url), None)
        if not source_post:
            await self.send_plain(
                "Пост не найден в кэше. Дубли ищутся только для постов текущей сессии.",
                reply_chat_id)
            return

        source_text = (source_post.get("text") or "").strip()
        query_text  = _strip_html(source_text)[:80].strip()
        if not query_text:
            await self.send_plain("Не удалось извлечь текст поста.", reply_chat_id)
            return

        start_ts = int((datetime.now(timezone.utc) - timedelta(hours=8)).timestamp())
        end_ts   = int(datetime.now(timezone.utc).timestamp())

        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(connector=connector) as session:
            results = await self.fetch_posts(session, start_date=start_ts, end_date=end_ts,
                                             q=f'"{query_text}"', limit=20)

        source_clean = re.sub(r'\s+', ' ', _strip_html(source_text)).strip().lower()
        dupes = []
        for p in results:
            if p.get("link") == post_url:
                continue
            p_clean = re.sub(r'\s+', ' ', _strip_html(p.get("text", ""))).strip().lower()
            a, b = source_clean[:200], p_clean[:200]
            if a and b and (a in b or b in a or _text_similarity(a, b) >= 0.7):
                dupes.append(p)

        if not dupes:
            await self.send_plain("Дублей не найдено за последние 8ч.", reply_chat_id)
            return

        await self.send_plain(f"🔁 Найдено дублей: {len(dupes)}", reply_chat_id)
        for post in dupes[:5]:
            await self.send_telegram(self.format_post_message(post, label="🔁 ПОСЕВ"), reply_chat_id)
            await asyncio.sleep(0.3)
        if len(dupes) > 5:
            await self.send_plain(f"... и ещё {len(dupes) - 5}", reply_chat_id)

    # ── Статистика ────────────────────────────────────────────────────────────

    async def send_daily_stats(self):
        total = sum(v for k, v in self.daily_stats.items() if k != "__other__")
        if total == 0:
            await self.send_plain("📊 За прошедшие сутки публикаций не было.")
        else:
            lines = ["📊 Статистика за сутки:\n"]
            for key, meta in self.key_tags.items():
                count = self.daily_stats.get(key, 0)
                lines.append(f"• #{meta['tag']} — {count} постов" if count
                             else f"• #{meta['tag']} — постов не было")
            other = self.daily_stats.get("__other__", 0)
            if other:
                lines.append(f"• прочие — {other}")
            await self.send_plain("\n".join(lines))
        self.daily_stats = {}

    # ── Время ─────────────────────────────────────────────────────────────────

    def is_monitoring_time(self) -> bool:
        return self.start_hour <= datetime.now(self.tz).hour < self.end_hour

    def get_night_period(self) -> tuple[int, int]:
        now           = datetime.now(self.tz)
        yesterday_end = self.tz.localize(
            datetime(now.year, now.month, now.day, self.end_hour, 0)
        ) - timedelta(days=1)
        today_start   = self.tz.localize(
            datetime(now.year, now.month, now.day, self.start_hour, 0)
        )
        return int(yesterday_end.timestamp()), int(today_start.timestamp())

    # ── Ночной отчёт ─────────────────────────────────────────────────────────

    async def send_night_report(self):
        start_ts, end_ts = self.get_night_period()
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(connector=connector) as session:
            night_posts = await self.fetch_all_queries(session, start_ts, end_ts)

        unique = []
        for p in night_posts:
            url        = p.get("link")
            channel_id = str(p.get("channel_id", "__unknown__"))
            if not url or self._is_seen(channel_id, url) or not self._passes_filters(p):
                continue
            unique.append(p)
            self._mark_seen(channel_id, url)
            self._update_daily_stats(self._find_matched_keys(p))

        for p in unique:
            p.pop("__lemmas", None)

        if not unique:
            await self.send_plain("Новых публикаций за ночь не было")
        else:
            with open(self.night_report_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "generated_at": datetime.now().isoformat(),
                    "period": f"{datetime.fromtimestamp(start_ts)} — {datetime.fromtimestamp(end_ts)}",
                    "total_posts": len(unique),
                    "posts": unique,
                }, f, ensure_ascii=False, indent=2)
            await self.send_plain(
                f"Публикации за ночь ({len(unique)} постов, {self.end_hour}:00–{self.start_hour}:00)"
            )
            for post in unique[:10]:
                await self.send_telegram(self.format_post_message(post))
                await asyncio.sleep(0.3)
            if len(unique) > 10:
                await self.send_plain(f"... и ещё {len(unique) - 10} постов")

        await self.send_daily_stats()

    # ── Bot polling ───────────────────────────────────────────────────────────

    async def poll_bot_commands(self):
        """Читает команды из файла-очереди который заполняет dispatcher.py."""
        queue_file = os.path.join(os.path.dirname(self.output_file), "tg_queue.json")
        logger.info(f"Queue reader started: {queue_file}")

        while True:
            try:
                if os.path.exists(queue_file):
                    tmp_file = queue_file + ".processing"
                    try:
                        os.rename(queue_file, tmp_file)
                    except FileNotFoundError:
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        with open(tmp_file, "r", encoding="utf-8") as f:
                            queue = json.load(f)
                    finally:
                        os.remove(tmp_file)
                    for update in queue:
                        await self._handle_update(update)
            except Exception as e:
                logger.error(f"Queue read error: {e}")
            await asyncio.sleep(0.5)

    async def _handle_update(self, update: dict):
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat_id  = str(msg["chat"]["id"])
        raw_text = (msg.get("text") or "").strip()
        state    = user_states.get(chat_id)
        text     = re.sub(r'^(/\w+)@\S+', r'\1', raw_text)

        if self.chat_id and chat_id != self.chat_id:
            logger.debug(f"Ignored update from chat_id={chat_id} (expected {self.chat_id})")
            return

        if not text:
            return

        logger.info(f"CMD from {chat_id}: {text[:80]!r}")

        if text.startswith("/name"):
            user_states.pop(chat_id, None)
            await self.send_plain(BOT_DESCRIPTION, chat_id)

        elif text.startswith("/help"):
            user_states.pop(chat_id, None)
            await self.send_plain(HELP_TEXT, chat_id)

        elif text.startswith("/status"):
            user_states.pop(chat_id, None)
            total_seen   = sum(len(v) for v in self.seen_urls_by_channel.values())
            queries_info = "\n".join(
                f"  [{i+1}] {q[:100]}{'…' if len(q) > 100 else ''}"
                for i, q in enumerate(self.search_queries)
            )
            await self.send_plain(
                f"Настройки [{PROJECT_NAME}]:\n\n"
                f"• Запросов: {len(self.search_queries)}\n{queries_info}\n\n"
                f"• Мин. подписчики канала: {self.min_subscribers}\n"
                f"• Мин. просмотры поста: {self.min_views}\n"
                f"• Порог посева: {self.rep_threshold}\n"
                f"• Блокировка текстов: {'HARD' if self.blacklist_hard else 'SOFT (rep>= ' + str(self.rep_threshold) + ')'}\n"
                f"• График: {self.start_hour}:00 — {self.end_hour}:00\n"
                f"• Фильтр по ключам: {'вкл' if self.keys_filter_enabled else 'ВЫКЛ'}\n"
                f"• Пропуск приватных: {'вкл' if self.skip_private else 'выкл'}\n"
                f"• Слов во фрагменте: {self.fragment_max_words}\n"
                f"• Исключения: {', '.join(self.excluded_channels) or 'нет'}\n"
                f"• Виденных постов: {total_seen} (TTL {self.seen_ttl_days}д)\n"
                f"• Seen texts (сутки): {len(self.seen_texts)}\n"
                f"• Порог посева: {int(self.similarity_threshold*100)}% / rep>={self.rep_threshold}\n"
                f"• Мониторинг: {'⏸ пауза' if self._paused else '▶️ активен'}\n"
                f"• Утренний отчёт: {'вкл' if self.night_report_enabled else 'ВЫКЛ'}\n"
                f"• Скипнуто за 12ч: {len(self.skipped_links)}\n"
                f"• Заблок. текстов: {len(self.text_blacklist)}",
                chat_id
            )

        elif text.startswith("/subscribers"):
            user_states[chat_id] = "await_subscribers"
            await self.send_plain(
                f"Мин. подписчики канала: {self.min_subscribers}\nВведи число. 0 — выкл.", chat_id)

        elif text.startswith("/views"):
            user_states[chat_id] = "await_views"
            await self.send_plain(
                f"Мин. просмотры поста: {self.min_views}\nВведи число. 0 — выкл.", chat_id)

        elif text.startswith("/rep_threshold"):
            user_states[chat_id] = "await_rep_threshold"
            await self.send_plain(
                f"Порог подписчиков для показа посева: {self.rep_threshold}\n"
                "Посев из канала с подписчиками >= этого числа — будет показан с 🔁.\nВведи число.",
                chat_id)

        elif text.startswith("/similarity_threshold"):
            user_states[chat_id] = "await_similarity_threshold"
            await self.send_plain(
                f"Текущий порог схожести текстов: {self.similarity_threshold:.0%}\n"
                "Если текст нового поста совпадает с уже отправленным на этот % — считается посевом.\n"
                "Введи число от 50 до 99 (например 85).",
                chat_id)

        elif text.startswith("/schedule"):
            user_states[chat_id] = "await_schedule"
            await self.send_plain(
                f"График: {self.start_hour}:00 — {self.end_hour}:00\nВведи ЧЧ-ЧЧ, например 9-21.",
                chat_id)

        elif text.startswith("/exceptions"):
            user_states[chat_id] = "await_exceptions"
            await self.send_plain(
                f"Исключения: {', '.join(self.excluded_channels) or 'нет'}\n\n"
                "Введи каналы через пробел/запятую.\n"
                "Форматы: @username, username, https://t.me/username\nОчистить: clear",
                chat_id)

        elif text.startswith("/keys_edit"):
            user_states[chat_id] = "await_keys_edit"
            lines = ["Редактирование минус-слов.\n",
                     "Формат: ключ:минус1,минус2   |   Очистить: ключ:-\n",
                     "Текущие ключи:"]
            for key, meta in self.key_tags.items():
                lines.append(f"  {key}: {', '.join(meta.get('minus', [])) or 'нет'}")
            await self.send_plain('\n'.join(lines), chat_id)

        elif text.startswith("/keys"):
            user_states.pop(chat_id, None)
            lines = [f"Фильтр по ключам: {'вкл ✅' if self.keys_filter_enabled else 'ВЫКЛ ❌'}\n"]
            for key, meta in self.key_tags.items():
                lines.append(f"• {key} → #{meta['tag']}  |  минус: {', '.join(meta.get('minus', [])) or 'нет'}")
            lines.append("\nДля редактирования минус-слов — /keys_edit")
            await self.send_plain('\n'.join(lines), chat_id)

        elif text.startswith("/duplicates"):
            user_states.pop(chat_id, None)
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await self.send_plain("Укажи URL: /duplicates https://t.me/channel/123", chat_id)
            else:
                asyncio.create_task(self.handle_duplicates(parts[1].strip(), chat_id))

        elif text.strip().upper() == "DUPLICATES":
            reply = msg.get("reply_to_message")
            if reply:
                urls = re.findall(r'https?://t\.me/\S+', reply.get("text") or "")
                if urls:
                    asyncio.create_task(self.handle_duplicates(urls[0], chat_id))
                else:
                    await self.send_plain("Не нашёл URL. Используй /duplicates <url>", chat_id)
            else:
                await self.send_plain("Ответь на сообщение с постом или /duplicates <url>", chat_id)

        elif text.startswith("/pause"):
            self._paused = True
            await self.send_plain("⏸ Мониторинг приостановлен. /resume — возобновить.", chat_id)

        elif text.startswith("/resume"):
            self._paused = False
            await self.send_plain("▶️ Мониторинг возобновлён.", chat_id)

        elif text.startswith("/test"):
            user_states.pop(chat_id, None)
            self._force_run = True
            await self.send_plain("⚡ Запускаю цикл немедленно...", chat_id)

        elif text.startswith("/last"):
            user_states.pop(chat_id, None)
            parts = text.split()
            try:
                n = max(1, min(int(parts[1]), 20)) if len(parts) > 1 else 5
            except ValueError:
                n = 5
            last = list(self._recent_sent)[-n:]
            if not last:
                await self.send_plain("Ещё ничего не отправлено в эту сессию.", chat_id)
            else:
                await self.send_plain(f"Последние {len(last)} постов:", chat_id)
                for post in last:
                    await self.send_telegram(self.format_post_message(post), chat_id)
                    await asyncio.sleep(0.3)

        elif text.startswith("/debug"):
            user_states.pop(chat_id, None)
            buf = list(self._log_buffer)
            if not buf:
                await self.send_plain("Буфер пуст.", chat_id)
            else:
                await self.send_plain(
                    ("Последние записи из лога:\n\n" + "\n".join(buf[-10:]))[:4000], chat_id)

        elif text.startswith("/fragment"):
            user_states[chat_id] = "await_fragment"
            await self.send_plain(
                f"Макс. слов в фрагменте: {self.fragment_max_words}\nВведи число (мин. 5).", chat_id)

        elif text.startswith("/seen"):
            user_states.pop(chat_id, None)
            parts     = text.split(maxsplit=1)
            arg       = parts[1].strip() if len(parts) > 1 else ""
            total_seen = sum(len(v) for v in self.seen_urls_by_channel.values())

            if not arg:
                if not self.seen_urls_by_channel:
                    await self.send_plain("seen_urls пуст.", chat_id)
                else:
                    sorted_ch  = sorted(self.seen_urls_by_channel.items(), key=lambda x: -len(x[1]))
                    lines_out  = [f"Виденных URL: {total_seen} / {len(self.seen_urls_by_channel)} каналов", ""]
                    for ch_id, urls in sorted_ch[:30]:
                        lines_out.append(f"• {ch_id} — {len(urls)}")
                    if len(sorted_ch) > 30:
                        lines_out.append(f"... и ещё {len(sorted_ch) - 30} каналов")
                    lines_out += ["", "/seen <часть id канала> — URL по каналу",
                                  "/seen <url> — проверить конкретный URL"]
                    await self.send_plain("\n".join(lines_out)[:4000], chat_id)

            elif arg.startswith("http") or arg.startswith("t.me"):
                found = False
                for ch_id, urls in self.seen_urls_by_channel.items():
                    if arg in urls:
                        ts = urls[arg]
                        dt = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")
                        await self.send_plain(
                            f"URL виден\nКанал: {ch_id}\nДобавлен: {dt}\n{arg}", chat_id)
                        found = True
                        break
                if not found:
                    await self.send_plain(f"URL не найден в seen_urls:\n{arg}", chat_id)

            else:
                matches = {ch_id: urls for ch_id, urls in self.seen_urls_by_channel.items()
                           if arg.lower() in ch_id.lower()}
                if not matches:
                    await self.send_plain(
                        f"Каналов с '{arg}' не найдено.\n"
                        "Используй /seen без аргумента чтобы увидеть все channel_id.", chat_id)
                else:
                    lines_out = []
                    for ch_id, urls in matches.items():
                        lines_out.append(f"{ch_id} ({len(urls)} URL):")
                        for url, ts in sorted(urls.items(), key=lambda x: -x[1])[:20]:
                            dt = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")
                            lines_out.append(f"  {dt}  {url}")
                        if len(urls) > 20:
                            lines_out.append(f"  ... и ещё {len(urls) - 20}")
                        lines_out.append("")
                    await self.send_plain("\n".join(lines_out)[:4000], chat_id)

        elif text.startswith("/skipped"):
            user_states.pop(chat_id, None)
            cutoff       = datetime.now() - timedelta(hours=12)
            total_before = len(self.skipped_links)
            fresh        = [s for s in self.skipped_links
                            if datetime.fromisoformat(s["ts"]) > cutoff]
            self.skipped_links = deque(fresh, maxlen=5000)

            parts = text.split()
            try:
                n = max(1, min(int(parts[1]), 100)) if len(parts) > 1 else 20
            except ValueError:
                n = 20

            if not self.skipped_links:
                await self.send_plain(
                    f"За последние 12ч скипов не было.\n(в памяти до прунинга: {total_before})",
                    chat_id)
            else:
                by_reason: dict = defaultdict(list)
                for s in self.skipped_links:
                    by_reason[s["reason"]].append(s)
                lines = [f"Скипнуто за 12ч: {len(self.skipped_links)} постов"]
                for reason, items in sorted(by_reason.items(), key=lambda x: -len(x[1])):
                    lines.append(f"\n{reason} ({len(items)}):")
                    for s in items[-n:]:
                        ts       = s["ts"][11:16]
                        subs_str = f" {s['subs']:,}подп" if s.get("subs") else ""
                        lines.append(f"  {ts}{subs_str} {s['url']}")
                await self.send_plain("\n".join(lines)[:4000], chat_id)

        elif text.startswith("/night_report"):
            user_states.pop(chat_id, None)
            parts = text.split()
            if len(parts) > 1:
                arg = parts[1].lower()
                self.night_report_enabled = arg not in ("off", "0", "false", "выкл")
            else:
                self.night_report_enabled = not self.night_report_enabled
            state_str = "включён ✅" if self.night_report_enabled else "ВЫКЛЮЧЕН ❌"
            self.save_all_data([])
            await self.send_plain(f"Утренний отчёт: {state_str}", chat_id)

        elif text.startswith("/block_text"):
            user_states.pop(chat_id, None)
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await self.send_plain(
                    "Укажи URL поста из последних отправленных:\n/block_text https://t.me/channel/123",
                    chat_id)
            else:
                target_url = parts[1].strip()
                source     = next((p for p in self._recent_sent if p.get("link") == target_url), None)
                if not source:
                    await self.send_plain("Пост не найден в кэше последних отправленных.", chat_id)
                else:
                    fp, clean = self._text_fingerprint(source.get("text", "") or "")
                    self.text_blacklist.add(fp)
                    self.save_all_data([])
                    await self.send_plain(f"✅ Текст заблокирован\nПревью: «{clean[:80]}»", chat_id)

        elif text.startswith("/text_blacklist"):
            user_states.pop(chat_id, None)
            if not self.text_blacklist:
                await self.send_plain("Список заблокированных текстов пуст.", chat_id)
            else:
                hash_to_preview: dict[int, str] = {}
                for p in self._recent_sent:
                    fp, clean = self._text_fingerprint(p.get("text", "") or "")
                    if fp in self.text_blacklist and fp not in hash_to_preview:
                        hash_to_preview[fp] = clean[:60]
                lines = [f"Заблокированных текстов: {len(self.text_blacklist)}"]
                for h in self.text_blacklist:
                    lines.append(f"• «{hash_to_preview.get(h, '—')}»")
                lines += ["", "Для разблокировки: /unblock_text <hash>"]
                await self.send_plain("\n".join(lines)[:4000], chat_id)

        elif text.startswith("/unblock_text"):
            user_states.pop(chat_id, None)
            parts = text.split()
            if len(parts) < 2:
                await self.send_plain("Укажи hash: /unblock_text <hash>", chat_id)
            else:
                try:
                    h = int(parts[1])
                    if h in self.text_blacklist:
                        self.text_blacklist.discard(h)
                        self.save_all_data([])
                        await self.send_plain(f"✅ hash={h} разблокирован.", chat_id)
                    else:
                        await self.send_plain(f"hash={h} не найден в списке.", chat_id)
                except ValueError:
                    await self.send_plain("hash должен быть числом.", chat_id)

        elif text.startswith("/reset"):
            user_states[chat_id] = "await_reset_confirm"
            total_seen = sum(len(v) for v in self.seen_urls_by_channel.values())
            await self.send_plain(
                f"Виденных постов: {total_seen}\n\n"
                "После сброса все посты за последний период придут повторно.\nТочно сбросить? Напиши ДА",
                chat_id)

        elif text.startswith("/reverse_digest"):
            user_states[chat_id] = "await_reverse_hours"
            await self.send_plain("⏳ За сколько часов собрать отчёт? Введи число (1–168):", chat_id)

        elif text.startswith("/restart"):
            user_states.pop(chat_id, None)
            logger.info(f"Restart requested via /restart | offset={self._tg_offset}")
            await self._close_tg_session()
            sys.exit(0)

        elif text.startswith("/blacklist_mode"):
            user_states.pop(chat_id, None)
            new_val = not self.blacklist_hard
            self.cfg["blacklist_hard"] = new_val
            save_config(self.cfg, CONFIG_FILE)
            mode_str = "🔒 HARD — железный скип для всех каналов" if new_val \
                       else "📊 SOFT — скип только если канал < rep_threshold"
            await self.send_plain(
                f"Режим блокировки текстов: {mode_str}\n"
                f"(было: {'HARD' if not new_val else 'SOFT'} → стало: {'HARD' if new_val else 'SOFT'})",
                chat_id)

        elif text.startswith("/block_tag"):
            user_states.pop(chat_id, None)
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                now       = datetime.now(timezone.utc).timestamp()
                tag_lines = ["🏷 Теги и блокировки:\n"]
                for key, meta in self.key_tags.items():
                    tag   = meta["tag"]
                    until = self._tag_blocked.get(tag)
                    if until and now < until:
                        until_str = datetime.fromtimestamp(until).strftime("%H:%M %d.%m")
                        tag_lines.append(f"• #{tag} — 🔒 заблокирован до {until_str}")
                    else:
                        cnt = len(self._tag_mention_counts.get(tag, []))
                        tag_lines.append(f"• #{tag} — {cnt} уп. за час")
                tag_lines += ["\nДля блокировки: /block_tag <тег>",
                              "Для разблокировки: /unblock_tag <тег>"]
                await self.send_plain("\n".join(tag_lines), chat_id)
            else:
                tag_input  = parts[1].strip().lstrip("#")
                known_tags = {meta["tag"] for meta in self.key_tags.values()}
                tag        = next((t for t in known_tags if t.lower() == tag_input.lower()), None)
                if not tag:
                    await self.send_plain(
                        f"Тег #{tag_input} не найден.\nИзвестные теги: "
                        + ", ".join(f"#{t}" for t in known_tags),
                        chat_id)
                else:
                    user_states[chat_id] = f"await_block_tag_hours:{tag}"
                    await self.send_plain(
                        f"На сколько часов заблокировать #{tag}?\nВведи число (1–168):", chat_id)

        elif text.startswith("/unblock_tag"):
            user_states.pop(chat_id, None)
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await self.send_plain("Укажи тег: /unblock_tag <тег>", chat_id)
            else:
                tag_input = parts[1].strip().lstrip("#")
                tag       = next((t for t in self._tag_blocked if t.lower() == tag_input.lower()), None)
                if tag:
                    del self._tag_blocked[tag]
                    self._tag_alert_sent.discard(tag)
                    await self.send_plain(f"✅ Тег #{tag} разблокирован.", chat_id)
                else:
                    await self.send_plain(f"Тег #{tag_input} не был заблокирован.", chat_id)

        elif state == "await_subscribers":
            try:
                val = int(re.sub(r'[^\d]', '', text))
                self.cfg["min_subscribers"] = val
                save_config(self.cfg, CONFIG_FILE)
                user_states.pop(chat_id, None)
                await self.send_plain(f"✅ Мин. подписчики: {val}" + (" (выкл)" if not val else ""), chat_id)
            except ValueError:
                await self.send_plain("Введи целое число.", chat_id)

        elif state == "await_views":
            try:
                val = int(re.sub(r'[^\d]', '', text))
                self.cfg["min_views"] = val
                save_config(self.cfg, CONFIG_FILE)
                user_states.pop(chat_id, None)
                await self.send_plain(f"✅ Мин. просмотры: {val}" + (" (выкл)" if not val else ""), chat_id)
            except ValueError:
                await self.send_plain("Введи целое число.", chat_id)

        elif state == "await_rep_threshold":
            try:
                val = int(re.sub(r'[^\d]', '', text))
                self.cfg["rep_threshold"] = val
                save_config(self.cfg, CONFIG_FILE)
                user_states.pop(chat_id, None)
                await self.send_plain(f"✅ Порог посева: {val} подписчиков", chat_id)
            except ValueError:
                await self.send_plain("Введи целое число.", chat_id)

        elif state == "await_similarity_threshold":
            try:
                val = int(re.sub(r'[^\d]', '', text))
                if not 50 <= val <= 99:
                    await self.send_plain("Введи число от 50 до 99.", chat_id)
                else:
                    self.cfg["similarity_threshold"] = round(val / 100, 2)
                    save_config(self.cfg, CONFIG_FILE)
                    user_states.pop(chat_id, None)
                    await self.send_plain(f"✅ Порог схожести: {val}%", chat_id)
            except ValueError:
                await self.send_plain("Введи целое число от 50 до 99.", chat_id)

        elif state == "await_schedule":
            m = re.match(r'^(\d{1,2})[:\-](\d{1,2})$', text.strip())
            if m:
                sh, eh = int(m.group(1)), int(m.group(2))
                if 0 <= sh < 24 and 0 <= eh < 24 and sh < eh:
                    self.cfg["start_hour"] = sh
                    self.cfg["end_hour"]   = eh
                    save_config(self.cfg, CONFIG_FILE)
                    user_states.pop(chat_id, None)
                    await self.send_plain(f"✅ График: {sh}:00 — {eh}:00", chat_id)
                else:
                    await self.send_plain("Некорректный диапазон. Пример: 9-21", chat_id)
            else:
                await self.send_plain("Формат: ЧЧ-ЧЧ, например 9-21.", chat_id)

        elif state == "await_exceptions":
            user_states.pop(chat_id, None)
            if text.strip().lower() == "clear":
                self.cfg["excluded_channels"] = []
                save_config(self.cfg, CONFIG_FILE)
                await self.send_plain("✅ Исключения очищены.", chat_id)
            else:
                raw_list   = re.split(r'[\s,]+', text.strip())
                normalized = [c for c in (normalize_channel(r) for r in raw_list if r) if len(c) > 1]
                existing   = set(self.excluded_channels)
                added      = [c for c in normalized if c not in existing]
                self.cfg["excluded_channels"] = list(existing | set(normalized))
                save_config(self.cfg, CONFIG_FILE)
                await self.send_plain(
                    f"✅ Добавлено: {', '.join(added) or 'нет новых'}\n"
                    f"Исключено: {', '.join(self.cfg['excluded_channels'])}",
                    chat_id)

        elif state == "await_keys_edit":
            user_states.pop(chat_id, None)
            parts = text.strip().split(':', 1)
            if len(parts) != 2:
                await self.send_plain("Формат: ключ:минус1,минус2", chat_id)
            else:
                key = parts[0].strip()
                if key not in self.key_tags:
                    await self.send_plain(f"Ключ '{key}' не найден.", chat_id)
                else:
                    minus_raw = parts[1].strip()
                    if minus_raw == '-':
                        self.key_tags[key]["minus"] = []
                        await self.send_plain(f"✅ Минус-слова для '{key}' очищены.", chat_id)
                    else:
                        minus_words = [w.strip().lower() for w in minus_raw.split(',') if w.strip()]
                        self.key_tags[key]["minus"] = minus_words
                        await self.send_plain(f"✅ Минус-слова для '{key}': {', '.join(minus_words)}", chat_id)
                    self.cfg["key_tags_runtime"] = {k: {"tag": v["tag"], "minus": v["minus"]}
                                                     for k, v in self.key_tags.items()}
                    save_config(self.cfg, CONFIG_FILE)

        elif state == "await_fragment":
            try:
                val = int(re.sub(r'[^\d]', '', text))
                if val < 5:
                    await self.send_plain("Минимум 5 слов.", chat_id)
                else:
                    self.cfg["fragment_max_words"] = val
                    save_config(self.cfg, CONFIG_FILE)
                    user_states.pop(chat_id, None)
                    await self.send_plain(f"✅ Слов во фрагменте: {val}", chat_id)
            except ValueError:
                await self.send_plain("Введи целое число.", chat_id)

        elif state == "await_reset_confirm":
            user_states.pop(chat_id, None)
            if text.strip().upper() == "ДА":
                total = sum(len(v) for v in self.seen_urls_by_channel.values())
                self.storage.seen_urls_by_channel.clear()
                self.seen_urls_by_channel = self.storage.seen_urls_by_channel
                self.save_all_data([])
                await self.send_plain(
                    f"✅ Сброшено {total} записей. Следующий цикл пришлёт всё заново.", chat_id)
            else:
                await self.send_plain("Отменено.", chat_id)

        elif state == "await_reverse_hours":
            user_states.pop(chat_id, None)
            try:
                hours = int(text.strip())
                if hours <= 0 or hours > 168:
                    await self.send_plain("❌ Число должно быть от 1 до 168 часов (7 суток).", chat_id)
                    return
                asyncio.create_task(self.send_reverse_digest(chat_id, hours))
            except ValueError:
                await self.send_plain("❌ Ошибка: нужно ввести целое число часов.", chat_id)

        elif state and state.startswith("await_block_tag_hours:"):
            tag = state.split(":", 1)[1]
            user_states.pop(chat_id, None)
            try:
                hours = int(text.strip())
                if hours <= 0 or hours > 168:
                    await self.send_plain("❌ Число должно быть от 1 до 168 часов.", chat_id)
                    return
                self._block_tag(tag, hours)
                until_str = datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + hours * 3600
                ).strftime("%H:%M %d.%m")
                await self.send_plain(
                    f"✅ Тег #{tag} заблокирован на {hours} ч. (до {until_str}).\n"
                    f"Посты с этим тегом не будут присылаться.\n"
                    f"Разблокировать: /unblock_tag {tag}",
                    chat_id)
            except ValueError:
                await self.send_plain("❌ Введи целое число часов.", chat_id)

    # ── Watchdog ──────────────────────────────────────────────────────────────

    async def watch_script_file(self):
        """Следит за mtime monitor.py. При изменении — корректно завершает процесс."""
        script_path = os.path.abspath(sys.argv[0])
        try:
            last_mtime = os.path.getmtime(script_path)
        except Exception:
            last_mtime = 0
        logger.info(f"Watchdog started: watching {script_path}")

        while True:
            await asyncio.sleep(10)
            try:
                current_mtime = os.path.getmtime(script_path)
            except Exception:
                continue
            if current_mtime > last_mtime:
                logger.info(f"Script file changed — exiting for clean restart | offset={self._tg_offset}")
                await self._close_tg_session()
                sys.exit(0)

    # ── Главный цикл ──────────────────────────────────────────────────────────

    async def monitor_loop(self):
        logger.info(
            f"Starting [{PROJECT_NAME}] | subs>={self.min_subscribers} views>={self.min_views} "
            f"rep>={self.rep_threshold} | {self.start_hour}:00–{self.end_hour}:00"
        )
        is_first_run = True

        while True:
            if self._paused:
                await asyncio.sleep(30)
                continue

            now = datetime.now(self.tz)

            if now.hour == self.start_hour and 0 <= now.minute < 2:
                if self.night_report_enabled:
                    await self.send_night_report()
                else:
                    logger.info("Night report skipped (disabled)")
                is_first_run = False
                await asyncio.sleep(120)
                continue

            force = self._force_run
            if force:
                self._force_run = False

            if force or self.is_monitoring_time():
                connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
                async with aiohttp.ClientSession(connector=connector) as session:
                    lookback = timedelta(hours=8) if is_first_run \
                               else timedelta(minutes=MONITOR_INTERVAL_MIN + 1)
                    is_first_run = False
                    start_ts = int((datetime.now(timezone.utc) - lookback).timestamp())
                    end_ts   = int(datetime.now(timezone.utc).timestamp())
                    posts    = await self.fetch_all_queries(session, start_ts, end_ts)
                    await self.process_posts(posts)

            await asyncio.sleep(MONITOR_INTERVAL_MIN * 60)


if __name__ == "__main__":
    monitor = TGStatMonitor()

    async def main():
        try:
            await asyncio.gather(
                monitor.monitor_loop(),
                monitor.poll_bot_commands(),
                monitor.watch_script_file(),
            )
        except SystemExit:
            pass

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
