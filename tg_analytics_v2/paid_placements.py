"""
paid_placements.py — чтение платных размещений из Google Sheets и/или
локального файла (registry/paid_placements.json), заполняемого вручную
через import_paid_placements.py.

Структура листа (и в Google Sheets, и в загружаемом xlsx/csv — формат
идентичен):
  Строки с зелёным фоном (или без числовых данных) — заголовок канала.
  Название канала сопоставляется с полем "name" в DASHBOARD_CHANNELS
  (частичное совпадение без учёта регистра — см. _name_to_channel).

  Колонки данных (0-based):
    A(0):  Площадка / название размещения
    B(1):  Ссылка
    C(2):  Дата (ДД.ММ или ДД.ММ.ГГГГ)
    D(3):  Статус
    E(4):  Подписчики
    F(5):  Ср. охват 1 публикации
    G(6):  Стоимость
    H(7):  Охват
    I(8):  CPV    — игнорируется (считаем сами)
    J(9):  CPM    — игнорируется (считаем сами)
    K(10): Приток подписчиков/заявок
    L(11): CPF/CPL — игнорируется (считаем сами)
    M(12): Формат — берём как есть ("папка", "пост", "1 бот" и т.д.)

Строки пропускаются если нет ни стоимости ни охвата (запланированные без результатов).
CPV/CPM/CPF скрипт считает сам из стоимости и охвата/притока.
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Индексы столбцов (0-based)
COL_PLATFORM = 0   # Площадка / название размещения
COL_LINK     = 1   # Ссылка
COL_DATE     = 2   # Дата
# COL_STATUS = 3   # Статус — не используется
# COL_SUBS   = 4   # Подписчики — не используется
# COL_AVG    = 5   # Ср. охват — не используется
COL_BUDGET   = 6   # Стоимость
COL_REACH    = 7   # Охват
# COL_CPV    = 8   # CPV — считаем сами
# COL_CPM    = 9   # CPM — считаем сами
COL_INFLOW   = 10  # Приток подписчиков/заявок
# COL_CPF    = 11  # CPF — считаем сами

LOCAL_PLACEMENTS_PATH = Path(os.getenv("REGISTRY_DIR", "registry")) / "paid_placements.json"
ALIASES_PATH = Path("paid_channel_aliases.csv")


def _get_sheet_url() -> str | None:
    return os.getenv("GOOGLE_PAID_SHEET_URL")


def _parse_num(val) -> float | None:
    if val is None or str(val).strip() in ("", "—", "-"):
        return None
    try:
        return float(str(val).replace(" ", "").replace(",", ".").replace("₽", ""))
    except ValueError:
        return None


def _parse_date(val, year_hint: int = None) -> str | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    for fmt in ("%d.%m", "%d/%m"):
        try:
            d = datetime.strptime(s, fmt)
            year = year_hint or datetime.now().year
            return d.replace(year=year).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _is_header_row(row: list) -> bool:
    """Строка-заголовок канала: есть текст в A, нет даты в C."""
    if not row or not str(row[0]).strip():
        return False
    date = row[2] if len(row) > 2 else None
    return not date


def _placement_type(platform: str) -> str:
    if "папк" in platform.lower():
        return "папка"
    return platform


def _cpv(budget, reach) -> float | None:
    if budget and reach:
        return round(budget / reach, 2)
    return None


def _cpf(budget, inflow) -> float | None:
    if budget and inflow:
        return round(budget / inflow, 2)
    return None


def _load_aliases() -> dict:
    """
    Читает paid_channel_aliases.csv (если есть) — ручные соответствия
    "текст заголовка в таблице" -> "@username канала", для случаев когда
    автоматическое сопоставление по имени не срабатывает (например
    рубрика называется иначе, чем канал в DASHBOARD_CHANNELS).
    Формат файла: alias,channel  (без заголовка или с ним — оба ок)
    """
    if not ALIASES_PATH.exists():
        return {}
    result = {}
    with ALIASES_PATH.open(encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            alias, channel = row[0].strip(), row[1].strip()
            if alias.lower() in ("alias", "название", "заголовок"):
                continue  # похоже на строку заголовка файла
            if alias and channel:
                result[alias.lower()] = channel
    return result


def _name_to_channel(name: str, channels_config: dict) -> str | None:
    """
    Сопоставляет название из таблицы с @username канала.
    Порядок попыток:
      1. Явный алиас из paid_channel_aliases.csv (точное совпадение)
      2. Точное совпадение с DASHBOARD_CHANNELS[ch]["name"]
      3. Частичное совпадение (имя канала входит в заголовок или
         наоборот) — нужно для случаев вида "Геймификация: игра на
         результат" при имени канала "Геймификация"
    """
    name_clean = name.strip().lower()

    aliases = _load_aliases()
    if name_clean in aliases:
        return aliases[name_clean]

    for ch, cfg in channels_config.items():
        cfg_name = cfg.get("name", "").strip().lower()
        if cfg_name and cfg_name == name_clean:
            return ch

    for ch, cfg in channels_config.items():
        cfg_name = cfg.get("name", "").strip().lower()
        if cfg_name and (cfg_name in name_clean or name_clean in cfg_name):
            return ch

    return None


def _parse_rows(rows: list, channels_config: dict,
                date_from_str: str = None, date_to_str: str = None,
                year_hint: int = None) -> tuple[dict, list]:
    """
    Общий парсер строк таблицы (общий для Google Sheets и для
    import_paid_placements.py). Возвращает:
      (result_by_key, unmatched_headers)
    result_by_key: {ключ: [placement, ...]}, где ключ — это либо
      "@username" известного канала, либо, если заголовок не совпал ни
      с одним каналом, ИСХОДНЫЙ ТЕКСТ ЗАГОЛОВКА как есть (например
      "HRTech") — такие "доп. блоки" не отбрасываются, а получают
      отдельный слайд в конце презентации (см. get_extra_paid_groups).
    unmatched_headers: список текстов заголовков, которые не удалось
      сопоставить ни с одним каналом (для отчёта юзеру и чтобы отличать
      "доп. блоки" от обычных каналов в result_by_key).
    """
    result_by_key: dict = {}
    unmatched_headers: list = []
    current_key = None

    for row in rows:
        if not row or all(str(v).strip() == "" for v in row if v is not None):
            continue

        platform_val = str(row[0]).strip() if row and row[0] is not None else ""

        if _is_header_row(row):
            matched = _name_to_channel(platform_val, channels_config)
            if matched:
                current_key = matched
            elif platform_val:
                current_key = platform_val  # доп. блок — используем заголовок как ключ
                unmatched_headers.append(platform_val)
                log.debug(f"Канал не найден для заголовка: '{platform_val}' — "
                          f"будет отдельным блоком '{platform_val}'")
            else:
                current_key = None
            continue

        if not current_key:
            continue

        row_date = _parse_date(row[COL_DATE] if len(row) > COL_DATE else None, year_hint)
        if not row_date:
            continue
        if date_from_str and date_to_str and not (date_from_str <= row_date <= date_to_str):
            continue

        budget = _parse_num(row[COL_BUDGET]) if len(row) > COL_BUDGET else None
        reach  = _parse_num(row[COL_REACH])  if len(row) > COL_REACH  else None
        inflow = _parse_num(row[COL_INFLOW]) if len(row) > COL_INFLOW else None
        link   = str(row[COL_LINK]).strip()  if len(row) > COL_LINK and row[COL_LINK] else ""
        p_type = _placement_type(platform_val)

        placement = {
            "platform":       platform_val,
            "link":           link,
            "date":           row_date,
            "budget":         budget,
            "reach":          reach,
            "inflow":         inflow,
            "placement_type": p_type or "пост",
            "cpv":            _cpv(budget, reach),
            "cpf":            _cpf(budget, inflow),
            "cpm":            round(budget / reach * 1000, 0) if budget and reach else None,
        }
        result_by_key.setdefault(current_key, []).append(placement)

    return result_by_key, unmatched_headers


# ── Локальное хранилище (заполняется import_paid_placements.py) ──────────

def load_local_placements() -> dict:
    """{channel: [placement, ...]}, {} если файла ещё нет."""
    if not LOCAL_PLACEMENTS_PATH.exists():
        return {}
    try:
        return json.loads(LOCAL_PLACEMENTS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Ошибка чтения {LOCAL_PLACEMENTS_PATH}: {e}")
        return {}


def save_local_placements(data: dict):
    LOCAL_PLACEMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PLACEMENTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Google Sheets ──────────────────────────────────────────────────────────

def _fetch_google_rows() -> list | None:
    url = _get_sheet_url()
    if not url:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
        if not creds_path:
            log.warning("GOOGLE_SHEETS_CREDENTIALS не задан")
            return None

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds       = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc          = gspread.authorize(creds)
        spreadsheet = gc.open_by_url(url)
        ws          = spreadsheet.sheet1
        return ws.get_all_values()
    except Exception as e:
        log.error(f"Ошибка чтения платных размещений из Google Sheets: {e}")
        return None


# ── Публичная функция ──────────────────────────────────────────────────────

def get_paid_placements(channel: str, date_from, date_to) -> list[dict]:
    """
    Возвращает платные размещения для канала за период — объединяя
    Google Sheets (если настроен) И локальный файл, заполненный вручную
    через import_paid_placements.py. Источники дополняют друг друга, не
    заменяют один другой.
    """
    from config import DASHBOARD_CHANNELS
    channels_config = DASHBOARD_CHANNELS

    df_str = date_from.strftime("%Y-%m-%d")
    dt_str = date_to.strftime("%Y-%m-%d")
    year_hint = date_from.year

    result = []

    # 1. Google Sheets
    rows = _fetch_google_rows()
    if rows and len(rows) >= 2:
        by_key, _ = _parse_rows(rows[1:], channels_config, df_str, dt_str, year_hint)
        result.extend(by_key.get(channel, []))

    # 2. Локальный файл (ручной импорт)
    local = load_local_placements()
    for p in local.get(channel, []):
        if df_str <= p.get("date", "") <= dt_str:
            result.append(p)

    return result


def get_extra_paid_groups(date_from, date_to) -> dict:
    """
    Возвращает платные размещения из "дополнительных" разделов — тех,
    чьё название не совпало ни с одним каналом из DASHBOARD_CHANNELS
    (например "HRTech"). Для каждого такого раздела дашборд строит
    отдельный слайд в самом конце презентации (см. renderPaidSlide в
    dashboard_report.py), с названием раздела в заголовке слайда — по
    аналогии со слайдом платных размещений канала.

    Возвращает {название_раздела: [placement, ...]}.
    """
    from config import DASHBOARD_CHANNELS
    channels_config = DASHBOARD_CHANNELS
    known_channels = set(channels_config.keys())

    df_str = date_from.strftime("%Y-%m-%d")
    dt_str = date_to.strftime("%Y-%m-%d")
    year_hint = date_from.year

    result: dict = {}

    # 1. Google Sheets
    rows = _fetch_google_rows()
    if rows and len(rows) >= 2:
        by_key, extra_headers = _parse_rows(rows[1:], channels_config, df_str, dt_str, year_hint)
        for name in extra_headers:
            if by_key.get(name):
                result.setdefault(name, []).extend(by_key[name])

    # 2. Локальный файл — любой ключ, который не является известным каналом
    local = load_local_placements()
    for key, placements in local.items():
        if key in known_channels:
            continue
        filtered = [p for p in placements if df_str <= p.get("date", "") <= dt_str]
        if filtered:
            result.setdefault(key, []).extend(filtered)

    return result


def get_channel_paid_summary(placements: list) -> dict:
    """Агрегирует список размещений в суммарные показатели для буллетов."""
    if not placements:
        return {}

    total_budget = sum(p["budget"]  or 0 for p in placements)
    total_reach  = sum(p["reach"]   or 0 for p in placements)
    total_inflow = sum(p["inflow"]  or 0 for p in placements)
    count        = len(placements)

    avg_cpv = round(total_budget / total_reach,  2) if total_reach  else None
    avg_cpf = round(total_budget / total_inflow, 2) if total_inflow else None

    return {
        "count":   count,
        "budget":  total_budget,
        "reach":   total_reach  or None,
        "inflow":  total_inflow or None,
        "avg_cpv": avg_cpv,
        "avg_cpf": avg_cpf,
    }
