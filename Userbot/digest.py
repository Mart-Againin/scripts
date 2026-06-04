"""
digest.py — модуль суммаризации постов для tg-channel-monitor.

Логика:
  - SHORT  (≤ short_limit)   → текст целиком
  - MEDIUM (≤ medium_limit)  → аннотация + N тезисов
  - LONG   (≤ long_limit)    → аннотация + N тезисов
  - LONGREAD (> long_limit)  → аннотация + N тезисов

Настройки хранятся в digest_config.json и перезаписываются командами из Telegram.
Дефолты берутся из .env при первом запуске (или если JSON отсутствует).

Зависимости: sumy, nltk (punkt_tab скачивается один раз при первом запуске)
"""

import os
import re
import json
import logging
import nltk

from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer
from sumy.nlp.stemmers import Stemmer

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Русские стоп-слова (sumy не включает их в пакет)
# ──────────────────────────────────────────────

RU_STOPWORDS = frozenset([
    'и','в','во','не','что','он','на','я','с','со','как','а','то','все','она','так',
    'его','но','да','ты','к','у','же','вы','за','бы','по','только','её','мне','было',
    'вот','от','меня','ещё','нет','о','из','ему','теперь','когда','даже','ну','вдруг',
    'ли','если','уже','или','ни','быть','был','него','до','вас','нибудь','опять','уж',
    'вам','ведь','там','потом','себя','ничего','ей','может','они','тут','где','есть',
    'надо','ней','для','мы','тебя','их','чем','была','сам','чтоб','без','будто','чего',
    'раз','тоже','себе','под','будет','ж','тогда','кто','этот','того','потому','этого',
    'какой','совсем','ним','здесь','этом','один','почти','мой','тем','чтобы','нее',
    'были','куда','зачем','всех','никогда','можно','при','наконец','об','другой',
    'хоть','после','над','больше','тот','через','эти','нас','про','всего','них',
    'много','разве','три','эту','моя','хорошо','свою','этой','перед','иногда',
    'лучше','чуть','том','нельзя','такой','им','более','всегда','конечно','всю','между',
    'это','их','также','которые','который','которая','которое','при',
])

CONFIG_FILE = "digest_config.json"
LANGUAGE = "russian"

# ──────────────────────────────────────────────
# Зоны шкалы
# ──────────────────────────────────────────────

ZONES = ("short", "medium", "long", "longread")

# Дефолты (читаются из .env, если там не заданы — используются эти)
DEFAULT_CONFIG = {
    "short_limit":   int(os.getenv("DIGEST_SHORT_LIMIT",   "800")),
    "medium_limit":  int(os.getenv("DIGEST_MEDIUM_LIMIT",  "1500")),
    "long_limit":    int(os.getenv("DIGEST_LONG_LIMIT",    "3000")),
    # число тезисов для каждой зоны (short не используется)
    "medium_theses": int(os.getenv("DIGEST_MEDIUM_THESES", "2")),
    "long_theses":   int(os.getenv("DIGEST_LONG_THESES",   "3")),
    "longread_theses": int(os.getenv("DIGEST_LONGREAD_THESES", "4")),
}

# Активная конфигурация (загружается при импорте)
_config: dict = {}


# ──────────────────────────────────────────────
# Загрузка / сохранение конфига
# ──────────────────────────────────────────────

def load_config() -> None:
    """Загружает digest_config.json поверх дефолтов. Вызывать при старте."""
    global _config
    _config = dict(DEFAULT_CONFIG)          # дефолт из .env
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            _config.update(saved)           # JSON перезаписывает дефолт
            logger.info(f"digest: конфиг загружен из {CONFIG_FILE}")
        except Exception as e:
            logger.warning(f"digest: не удалось прочитать {CONFIG_FILE}: {e}, используем дефолт")
    else:
        logger.info("digest: digest_config.json не найден, используем дефолты из .env")


def save_config() -> None:
    """Сохраняет текущий конфиг в JSON."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"digest: не удалось сохранить {CONFIG_FILE}: {e}")


def reset_config() -> None:
    """Сбрасывает конфиг к дефолтам из .env и сохраняет."""
    global _config
    _config = dict(DEFAULT_CONFIG)
    save_config()
    logger.info("digest: конфиг сброшен к дефолтам")


def get_config() -> dict:
    return dict(_config)


def set_limit(zone: str, value: int) -> str:
    """
    Устанавливает границу зоны в символах.
    zone: 'short' | 'medium' | 'long'
    Возвращает строку с результатом для ответа пользователю.
    """
    key = f"{zone}_limit"
    if key not in _config:
        return f"Неизвестная зона: {zone}. Доступны: short, medium, long."
    if value < 100:
        return "Минимальная граница — 100 символов."

    # Проверяем монотонность: short < medium < long
    limits = {
        "short":  _config["short_limit"],
        "medium": _config["medium_limit"],
        "long":   _config["long_limit"],
    }
    limits[zone] = value
    if not (limits["short"] < limits["medium"] < limits["long"]):
        return (
            f"Границы должны быть строго возрастающими: "
            f"short < medium < long. "
            f"Сейчас: short={limits['short']}, medium={limits['medium']}, long={limits['long']}."
        )

    _config[key] = value
    save_config()
    return f"Граница {zone} установлена: {value} символов."


def set_theses(zone: str, value: int) -> str:
    """
    Устанавливает число тезисов для зоны.
    zone: 'medium' | 'long' | 'longread'
    """
    key = f"{zone}_theses"
    if key not in _config:
        return f"Неизвестная зона: {zone}. Доступны: medium, long, longread."
    if value < 1:
        return "Минимум 1 тезис."
    if value > 10:
        return "Максимум 10 тезисов."
    _config[key] = value
    save_config()
    return f"Тезисов для зоны {zone}: {value}."


def format_status() -> str:
    """Форматирует текущую конфигурацию для вывода в Telegram."""
    c = _config
    lines = [
        "📊 Текущая шкала дайджеста:",
        "",
        f"🟢 Короткий  ≤ {c['short_limit']} симв.     → текст целиком",
        f"🟡 Средний   ≤ {c['medium_limit']} симв.    → аннотация + {c['medium_theses']} тезиса",
        f"🟠 Длинный   ≤ {c['long_limit']} симв.   → аннотация + {c['long_theses']} тезиса",
        f"🔴 Лонгрид   > {c['long_limit']} симв.   → аннотация + {c['longread_theses']} тезиса",
        "",
        "Команды:",
        "/digest_set short|medium|long <символов>",
        "/digest_theses medium|long|longread <N>",
        "/digest_reset — сброс к дефолтам",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────
# NLTK: одноразовая загрузка punkt
# ──────────────────────────────────────────────

def _ensure_nltk() -> None:
    """Скачивает punkt_tab один раз. На Windows данные кэшируются в ~/AppData."""
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        logger.info("digest: скачиваем NLTK punkt_tab (один раз)...")
        nltk.download("punkt_tab", quiet=True)
        nltk.download("punkt", quiet=True)


# ──────────────────────────────────────────────
# Суммаризатор (создаётся один раз)
# ──────────────────────────────────────────────

_stemmer = Stemmer(LANGUAGE)
_summarizer = LsaSummarizer(_stemmer)
_summarizer.stop_words = RU_STOPWORDS


# ──────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────

def _get_zone(length: int) -> str:
    if length <= _config["short_limit"]:
        return "short"
    if length <= _config["medium_limit"]:
        return "medium"
    if length <= _config["long_limit"]:
        return "long"
    return "longread"


def _get_theses_count(zone: str) -> int:
    return _config.get(f"{zone}_theses", 3)


def _clean_text(text: str) -> str:
    """Убирает URL и лишние пробелы перед подачей в суммаризатор."""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _extract_annotation(text: str) -> str:
    """
    Аннотация = первый непустой абзац.
    Если он слишком длинный (> 400 симв.) — первое предложение.
    """
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return text[:300]

    first = paragraphs[0]
    if len(first) <= 400:
        return first

    # Первый абзац длинный — берём первое предложение
    sentences = re.split(r'(?<=[.!?])\s+', first)
    return sentences[0] if sentences else first[:300]


def _summarize(text: str, n: int) -> list[str]:
    """
    Возвращает список из n тезисов (строк).
    При любой ошибке — пустой список.
    """
    try:
        clean = _clean_text(text)
        if not clean:
            return []
        parser = PlaintextParser.from_string(clean, Tokenizer(LANGUAGE))
        sentences = _summarizer(parser.document, n)
        result = [str(s) for s in sentences if str(s).strip()]
        return result
    except Exception as e:
        logger.error(f"digest: ошибка суммаризации: {e}", exc_info=True)
        return []


# ──────────────────────────────────────────────
# Основная функция
# ──────────────────────────────────────────────

def process_post(text: str, url: str) -> str:
    """
    Принимает текст поста и ссылку на него.
    Возвращает отформатированный дайджест (строка для отправки в Telegram).

    Никогда не падает — при любой ошибке возвращает исходный текст с ссылкой.
    """
    try:
        if not text or not text.strip():
            return f"🔗 {url}"

        length = len(text)
        zone = _get_zone(length)

        # Короткий пост — целиком
        if zone == "short":
            return f"{text}\n\n🔗 {url}"

        # Длинный пост — аннотация + тезисы
        n = _get_theses_count(zone)
        annotation = _extract_annotation(text)
        theses = _summarize(text, n)

        lines = []

        # Аннотация
        lines.append("📌 " + annotation)
        lines.append("")

        # Тезисы
        if theses:
            lines.append("Тезисы:")
            for i, t in enumerate(theses, 1):
                lines.append(f"{i}. {t}")
        else:
            # Fallback: если суммаризация не дала результата — первые 500 симв.
            logger.warning(f"digest: суммаризация вернула пустой список для поста {url}")
            lines.append(text[:500] + "...")

        lines.append("")
        lines.append(f"🔗 {url}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"digest: критическая ошибка в process_post: {e}", exc_info=True)
        # Аварийный fallback — не теряем пост
        try:
            return f"{text[:600]}...\n\n🔗 {url}"
        except Exception:
            return f"🔗 {url}"
