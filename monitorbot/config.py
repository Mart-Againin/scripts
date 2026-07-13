"""
config.py — загрузка и сохранение конфигурации проекта.

Отвечает за:
- DEFAULT_CONFIG — значения по умолчанию
- load_config() — читает config.json + .env оверрайды
- save_config() — сохраняет config.json
- parse_key_tags() — парсинг SEARCH_KEY_TAGS из .env
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# ── Значения по умолчанию ─────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "min_subscribers":      0,
    "min_views":            0,
    "rep_threshold":        10000,
    "similarity_threshold": 0.85,
    "blacklist_hard":       False,
    "start_hour":           10,
    "end_hour":             22,
    "excluded_channels":    [],
    "fragment_max_words":   30,
    "keys_filter_enabled":  True,
    "skip_private":         True,
    "seen_ttl_days":        7,
}

# ── ENV → config маппинги ─────────────────────────────────────────────────────

ENV_INT_OVERRIDES: dict[str, str] = {
    "SEARCH_MIN_SUBSCRIBERS": "min_subscribers",
    "SEARCH_MIN_VIEWS":       "min_views",
    "REP_THRESHOLD":          "rep_threshold",
    "START_HOUR":             "start_hour",
    "END_HOUR":               "end_hour",
    "FRAGMENT_MAX_WORDS":     "fragment_max_words",
}

ENV_BOOL_OVERRIDES: dict[str, str] = {
    "KEYS_FILTER_ENABLED":  "keys_filter_enabled",
    "SKIP_PRIVATE":         "skip_private",
    "TEXT_BLACKLIST_HARD":  "blacklist_hard",
}

ENV_FLOAT_OVERRIDES: dict[str, str] = {
    "SIMILARITY_THRESHOLD": "similarity_threshold",
}


# ── Функции ───────────────────────────────────────────────────────────────────

def load_config(config_file: str) -> dict:
    """
    Загружает конфиг из config.json, затем применяет оверрайды из .env.
    .env всегда имеет приоритет над config.json.
    """
    cfg = dict(DEFAULT_CONFIG)

    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load config.json: {e}")

    for env_key, cfg_key in ENV_INT_OVERRIDES.items():
        val = os.getenv(env_key)
        if val not in (None, ""):
            cfg[cfg_key] = int(val)

    for env_key, cfg_key in ENV_BOOL_OVERRIDES.items():
        val = os.getenv(env_key)
        if val not in (None, ""):
            cfg[cfg_key] = val.strip().lower() not in ("0", "false", "no", "off")

    for env_key, cfg_key in ENV_FLOAT_OVERRIDES.items():
        val = os.getenv(env_key)
        if val not in (None, ""):
            cfg[cfg_key] = float(val)

    return cfg


def save_config(cfg: dict, config_file: str):
    """Сохраняет текущий конфиг в config.json."""
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    logger.debug(f"Config saved: {config_file}")


def parse_key_tags(raw: str) -> dict:
    """
    Парсит SEARCH_KEY_TAGS из .env.

    Формат: ключ:тег:минус1,минус2|ключ2:тег2
    Пример: Балаклава:Балаклава:флот,взрыв|Ротенберг:Ротенберг

    Возвращает: {key: {"tag": str, "minus": [str, ...]}}
    """
    if not raw:
        return {}
    entries = raw.split('|') if '|' in raw else raw.split(',')
    result = {}
    for entry in entries:
        parts = entry.strip().split(':')
        if len(parts) < 2:
            continue
        key  = parts[0].strip()
        tag  = parts[1].strip()
        minus = [w.strip().lower() for w in parts[2].split(',') if w.strip()] if len(parts) >= 3 else []
        if key and tag:
            result[key] = {"tag": tag, "minus": minus}
    return result
