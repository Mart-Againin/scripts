"""
paid_placements.py — чтение платных размещений из Google Sheets.

Структура листа:
  Строки с зелёным фоном (или без числовых данных) — заголовок канала.
  Название канала совпадает с полем "name" в DASHBOARD_CHANNELS.

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

import logging
import os
from datetime import datetime

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
# COL_TYPE removed — тип определяется из COL_PLATFORM (колонка A)


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
    s = str(val).strip()
    # Пробуем форматы с годом
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Формат ДД.ММ без года — добавляем год из подсказки
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
    date = str(row[2]).strip() if len(row) > 2 else ""
    return not date


def _placement_type(platform: str) -> str:
    """Определяет тип размещения из названия платформы (колонка A).
    Если название содержит слово 'папка' — тип 'папка'.
    Иначе — полное название из A.
    """
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


def _name_to_channel(name: str, channels_config: dict) -> str | None:
    """Сопоставляет название из таблицы с @username канала."""
    name_clean = name.strip().lower()
    for ch, cfg in channels_config.items():
        if cfg.get("name", "").strip().lower() == name_clean:
            return ch
    return None


def get_paid_placements(channel: str, date_from, date_to) -> list[dict]:
    """
    Возвращает платные размещения для канала за период.
    Каждый элемент:
    {
        platform, link, date, budget, reach, inflow,
        placement_type,  # из колонки M как есть
        cpv, cpf, cpm
    }
    """
    url = _get_sheet_url()
    if not url:
        log.debug("GOOGLE_PAID_SHEET_URL не задан — платные размещения пропущены")
        return []

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
        if not creds_path:
            log.warning("GOOGLE_SHEETS_CREDENTIALS не задан")
            return []

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds       = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc          = gspread.authorize(creds)
        spreadsheet = gc.open_by_url(url)
        ws          = spreadsheet.sheet1
        rows        = ws.get_all_values()

    except Exception as e:
        log.error(f"Ошибка чтения платных размещений из Google Sheets: {e}")
        return []

    if len(rows) < 2:
        return []

    # Загружаем конфигурацию каналов для сопоставления названий
    try:
        from config import DASHBOARD_CHANNELS
        channels_config = DASHBOARD_CHANNELS
    except Exception:
        channels_config = {}

    df_str   = date_from.strftime("%Y-%m-%d")
    dt_str   = date_to.strftime("%Y-%m-%d")
    year_hint = date_from.year

    result          = []
    current_channel = None  # текущий канал (определяется по заголовочной строке)

    for row in rows[1:]:  # пропускаем строку заголовков таблицы
        if not row or all(str(v).strip() == "" for v in row):
            continue  # пустая строка

        platform_val = str(row[0]).strip() if row else ""

        # Проверяем — это заголовок канала?
        if _is_header_row(row):
            current_channel = _name_to_channel(platform_val, channels_config)
            if not current_channel:
                log.debug(f"Канал не найден для заголовка: '{platform_val}'")
            continue

        # Если канал не определён — пропускаем
        if not current_channel:
            continue

        # Нас интересует только нужный канал
        if current_channel != channel:
            continue

        row_date = _parse_date(row[COL_DATE] if len(row) > COL_DATE else "", year_hint)
        if not row_date or not (df_str <= row_date <= dt_str):
            continue

        budget = _parse_num(row[COL_BUDGET]) if len(row) > COL_BUDGET else None
        reach  = _parse_num(row[COL_REACH])  if len(row) > COL_REACH  else None
        inflow = _parse_num(row[COL_INFLOW]) if len(row) > COL_INFLOW else None
        link   = str(row[COL_LINK]).strip()  if len(row) > COL_LINK   else ""
        p_type = _placement_type(platform_val)

        result.append({
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
        })

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
