"""
paid_placements.py — чтение платных размещений из Google Sheets.

Структура листа в Google Sheets (одна строка = одно размещение):
  Канал | Площадка | Дата | Стоимость | Охват | Приток | Тип

Тип: "пост" или "папка"
  - пост: есть Охват, CPV/CPM считаются автоматически
  - папка: Охват не важен (0 или пусто), главное Приток и CPF

Формат даты: ДД.ММ.ГГГГ
"""

import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

# Индексы столбцов (0-based) в Google Sheet
COL_CHANNEL  = 0  # @username канала
COL_PLATFORM = 1  # название площадки размещения
COL_DATE     = 2  # дата размещения
COL_BUDGET   = 3  # стоимость в рублях
COL_REACH    = 4  # охват (для постов)
COL_INFLOW   = 5  # приток подписчиков/заявок
COL_TYPE     = 6  # "пост" или "папка"


def _get_sheet_url() -> str | None:
    return os.getenv("GOOGLE_PAID_SHEET_URL")


def _parse_num(val) -> float | None:
    if val is None or str(val).strip() in ("", "—", "-"):
        return None
    try:
        return float(str(val).replace(" ", "").replace(",", ".").replace("₽", ""))
    except ValueError:
        return None


def _parse_date(val) -> str | None:
    if not val:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(val).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return str(val)


def _cpv(budget, reach) -> float | None:
    if budget and reach:
        return round(budget / reach, 2)
    return None


def _cpf(budget, inflow) -> float | None:
    if budget and inflow:
        return round(budget / inflow, 2)
    return None


def get_paid_placements(channel: str, date_from, date_to) -> list[dict]:
    """
    Возвращает платные размещения для канала за период.
    Каждый элемент:
    {
        platform, date, budget, reach, inflow,
        placement_type,  # "пост" | "папка"
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
        import os

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
        log.error(f"Ошибка чтения платных размещений: {e}")
        return []

    if len(rows) < 2:
        return []

    ch_clean   = channel.lstrip("@").lower()
    df_str     = date_from.strftime("%Y-%m-%d")
    dt_str     = date_to.strftime("%Y-%m-%d")
    result     = []

    for row in rows[1:]:  # пропускаем заголовок
        if len(row) <= COL_TYPE:
            continue
        row_ch = row[COL_CHANNEL].lstrip("@").lower().strip()
        if row_ch != ch_clean:
            continue

        row_date = _parse_date(row[COL_DATE])
        if not row_date or not (df_str <= row_date <= dt_str):
            continue

        budget = _parse_num(row[COL_BUDGET])
        reach  = _parse_num(row[COL_REACH])
        inflow = _parse_num(row[COL_INFLOW])
        p_type = row[COL_TYPE].strip().lower() if len(row) > COL_TYPE else "пост"

        result.append({
            "platform":       row[COL_PLATFORM].strip(),
            "date":           row_date,
            "budget":         budget,
            "reach":          reach,
            "inflow":         inflow,
            "placement_type": "папка" if "папк" in p_type else "пост",
            "cpv":            _cpv(budget, reach),
            "cpf":            _cpf(budget, inflow),
            "cpm":            round(budget / reach * 1000, 0) if budget and reach else None,
        })

    return result


def get_channel_paid_summary(placements: list) -> dict:
    """Агрегирует список размещений в суммарные показатели для буллетов."""
    if not placements:
        return {}

    total_budget  = sum(p["budget"]  or 0 for p in placements)
    total_reach   = sum(p["reach"]   or 0 for p in placements)
    total_inflow  = sum(p["inflow"]  or 0 for p in placements)
    count         = len(placements)

    avg_cpv = round(total_budget / total_reach,  2) if total_reach  else None
    avg_cpf = round(total_budget / total_inflow, 2) if total_inflow else None

    return {
        "count":        count,
        "budget":       total_budget,
        "reach":        total_reach  or None,
        "inflow":       total_inflow or None,
        "avg_cpv":      avg_cpv,
        "avg_cpf":      avg_cpf,
    }
