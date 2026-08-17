"""
google_sheets.py — выгрузка месячного отчёта в Google Таблицы.

Авторизация через Service Account (JSON-ключ).
Настройка в .env:
  GOOGLE_SHEETS_CREDENTIALS = /path/to/service_account.json
  GOOGLE_SHEETS_ID           = 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms

Структура листа (один лист = один месяц, например "Июнь 2026"):
  Название канала (строка-заголовок)
  Дата | Тема | Охват | Реакции | Репосты | Комментарии | Голоса |
  Действия | ERR | VRpost | Ссылка | Комментарий
  Итого по каналу
  [Сторис]
  Дата | Тема | Охват | Реакции | Ссылка | Комментарий
  Следующий канал...
"""

import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Заголовки столбцов
POSTS_HEADERS = [
    "Дата", "Тема", "Охват", "Реакции", "Репосты",
    "Комментарии", "Голоса", "Число действий", "ERR", "VRpost",
    "Ссылка", "Источник данных", "Комментарий"
]
STORIES_HEADERS = [
    "Дата", "Охват", "Реакции", "Источник данных", "Комментарий"
]


def _get_creds_path() -> str | None:
    return os.getenv("GOOGLE_SHEETS_CREDENTIALS")


def _get_sheet_id() -> str | None:
    return os.getenv("GOOGLE_SHEETS_ID")


def _fmt_pct(v) -> str:
    """Форматирует дробное число как процент строкой для Sheets."""
    if v is None:
        return "—"
    return f"{v:.2f}%"


def _post_theme(post: dict, n_words: int = 9) -> str:
    """Берёт первые n_words слов из текста поста."""
    text = post.get("text", "") or post.get("message", "") or ""
    if not text:
        # Если текста нет — тип контента
        return post.get("content_type", "")
    words = text.split()
    result = " ".join(words[:n_words])
    if len(words) > n_words:
        result += "..."
    return result


def _safe_pct(val) -> str:
    if val is None:
        return "—"
    return f"{val:.2f}%"


def _build_sheet_rows(channels_data: list, stories_data: dict,
                       month_label: str) -> list[list]:
    """
    Формирует список строк для записи в Google Sheet.
    channels_data — список dict: channel_id, subscribers, posts
    stories_data  — dict: channel_id -> list of story dicts
    """
    rows = []

    # Заголовок листа
    rows.append([f"Отчёт за {month_label}"] + [""] * (len(POSTS_HEADERS) - 1))
    rows.append([])  # пустая строка

    for cd in channels_data:
        ch_id   = cd["channel_id"]
        subs    = cd["subscribers"]
        posts   = cd["posts"]

        # ── Заголовок канала ─────────────────────────────────────────────
        rows.append([f"{ch_id}  ({subs:,} подписчиков)"] + [""] * (len(POSTS_HEADERS) - 1))
        rows.append(POSTS_HEADERS)

        if not posts:
            rows.append(["Нет данных за период"] + [""] * (len(POSTS_HEADERS) - 1))
        else:
            totals = {k: 0 for k in ["views","reactions","forwards","comments","votes","actions"]}
            err_list   = []
            vrpost_list= []

            for p in posts:
                sn   = p.get("snapshot", {}) or {}
                note = p.get("_note", "")

                views    = sn.get("views", 0)    or 0
                react    = sn.get("reactions", 0) or 0
                fwd      = sn.get("forwards", 0)  or 0
                comments = sn.get("comments", 0)  or 0
                votes    = sn.get("votes", 0)     or 0
                actions  = sn.get("actions", 0)   or 0

                err    = round(actions / views * 100, 2) if views else None
                vrpost = round(views / subs * 100, 2) if subs else None

                for k, v in [("views",views),("reactions",react),("forwards",fwd),
                              ("comments",comments),("votes",votes),("actions",actions)]:
                    totals[k] += v
                if err is not None:    err_list.append(err)
                if vrpost is not None: vrpost_list.append(vrpost)

                rows.append([
                    p.get("date", ""),
                    _post_theme(p),
                    views,
                    react,
                    fwd,
                    comments,
                    votes,
                    actions,
                    _safe_pct(err),
                    _safe_pct(vrpost),
                    p.get("url", ""),
                    note,
                    "",  # Комментарий — заполняется вручную
                ])

            # Итого
            avg_err    = round(sum(err_list)    / len(err_list),    2) if err_list    else None
            avg_vrpost = round(sum(vrpost_list) / len(vrpost_list), 2) if vrpost_list else None
            rows.append([
                "Итого",
                f"{len(posts)} постов",
                totals["views"],
                totals["reactions"],
                totals["forwards"],
                totals["comments"],
                totals["votes"],
                totals["actions"],
                _safe_pct(avg_err),
                _safe_pct(avg_vrpost),
                "", "", "",
            ])

        # ── Сторис канала ────────────────────────────────────────────────
        ch_stories = stories_data.get(ch_id.lstrip("@"), [])
        if ch_stories:
            rows.append([])
            rows.append(["Сторис"] + [""] * (len(POSTS_HEADERS) - 1))
            rows.append(STORIES_HEADERS + [""] * (len(POSTS_HEADERS) - len(STORIES_HEADERS)))

            st_views_total = 0
            st_react_total = 0
            for st in ch_stories:
                sn      = st.get("snapshot", {}) or {}
                views   = sn.get("views", 0)     or 0
                react   = sn.get("reactions", 0) or 0
                err     = round(react / views * 100, 2) if views else None
                st_views_total += views
                st_react_total += react
                rows.append([
                    st.get("date", ""),
                    views,
                    react,
                    st.get("_note", "📸 тек."),
                    "",
                ] + [""] * (len(POSTS_HEADERS) - 5))

            rows.append([
                "Итого сторис",
                st_views_total,
                st_react_total,
                "", "",
            ] + [""] * (len(POSTS_HEADERS) - 5))

        rows.append([])  # пустая строка между каналами

    return rows


def _get_or_create_sheet(spreadsheet, sheet_name: str):
    """Возвращает лист по имени или создаёт новый."""
    try:
        return spreadsheet.worksheet(sheet_name)
    except Exception:
        return spreadsheet.add_worksheet(title=sheet_name, rows=500, cols=20)



async def check_connection() -> tuple[bool, str]:
    """
    Проверяет доступность Google Sheets.
    Возвращает (ok, error_message).
    """
    creds_path = _get_creds_path()
    sheet_id   = _get_sheet_id()

    if not creds_path or not sheet_id:
        return False, "Google Sheets не настроен в .env (GOOGLE_SHEETS_CREDENTIALS / GOOGLE_SHEETS_ID)"
    if not Path(creds_path).exists():
        return False, f"Файл credentials не найден: {creds_path}"

    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc    = gspread.authorize(creds)
        gc.open_by_key(sheet_id)
        return True, ""
    except ImportError:
        return False, "Библиотека gspread не установлена (pip install gspread google-auth)"
    except Exception as e:
        return False, f"Google Sheets недоступен: {e}"

async def upload_monthly_report(channels_data: list, stories_data: dict,
                                 month_label: str, ym: str):
    """
    Выгружает данные месячного отчёта в Google Таблицу.

    channels_data — данные постов (из build_and_send)
    stories_data  — dict channel_username -> [story, ...]
    month_label   — "Июнь 2026"
    ym            — "2026-06"
    """
    creds_path = _get_creds_path()
    sheet_id   = _get_sheet_id()

    if not creds_path or not sheet_id:
        log.warning("Google Sheets не настроен — пропускаем выгрузку. "
                    "Добавьте GOOGLE_SHEETS_CREDENTIALS и GOOGLE_SHEETS_ID в .env")
        return

    if not Path(creds_path).exists():
        log.error(f"Файл credentials не найден: {creds_path}")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.error("Библиотека gspread не установлена. Выполните: pip install gspread google-auth")
        return

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc     = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(sheet_id)
    except Exception as e:
        log.error(f"Ошибка подключения к Google Sheets: {e}")
        return

    # Имя листа = "Июнь 2026"
    sheet_name = month_label
    try:
        ws = _get_or_create_sheet(spreadsheet, sheet_name)
        ws.clear()

        rows = _build_sheet_rows(channels_data, stories_data, month_label)

        if rows:
            ws.update(f"A1", rows, value_input_option="USER_ENTERED")

        log.info(f"Google Sheets: выгружено {len(rows)} строк на лист '{sheet_name}'")

    except Exception as e:
        log.error(f"Ошибка выгрузки в Google Sheets: {e}")
