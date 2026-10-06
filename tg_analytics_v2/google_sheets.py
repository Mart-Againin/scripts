"""
google_sheets.py — выгрузка месячного отчёта в Google Таблицы.

Авторизация через Service Account (JSON-ключ).
Настройка в .env:
  GOOGLE_SHEETS_CREDENTIALS = /path/to/service_account.json
  GOOGLE_SHEETS_ID           = 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms

Структура листа (один лист = один месяц, например "Июнь 2026"):
  Общая шапка (одна на весь лист, не повторяется по каналам)
  ── Канал 1 (ярко-зелёная строка-разделитель) ──
  строки постов (светло-серые)
  Итого (жирным)
  Сторис (светло-зелёная строка-разделитель) — только если есть сторис
  строки сторис (светло-серые)
  Итого (жирным)
  ── Канал 2 ──
  ...

Лист целиком перезаписывается при каждой выгрузке (clear + запись
заново) — реальный исторический след хранится в самом проекте
(registry/archive), а не в этой таблице, поэтому формат можно спокойно
переделывать, не беспокоясь о «миграции» старых листов.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Единая, общая шапка на весь лист — без колонки "Источник данных"
# (убрана по новой спецификации), с добавленными "Переход" и "Достижение
# цели" (ручные поля — как и "Коммент", эти два столбца не заполняются
# скриптом автоматически: таких данных в проекте нет ни в одном
# источнике, заполнение оставлено на оператора, как уже было для
# "Коммент").
POSTS_HEADERS = [
    "Дата", "Тема", "Охват", "Реакции", "Репосты", "Комментарии", "Голоса",
    "Число действий", "ERR", "VRpost", "Ссылка", "Переход", "Достижение цели", "Коммент",
]
N_COLS = len(POSTS_HEADERS)  # 14

STORIES_HEADERS = ["Дата", "Охват", "Реакции", "Ссылка", "Коммент"]

# ── Цвета (0..1, формат Google Sheets API) ──────────────────────────────
GREEN_BRIGHT = {"red": 0.20, "green": 0.66, "blue": 0.33}   # канал
GREEN_LIGHT  = {"red": 0.85, "green": 0.93, "blue": 0.83}   # сторис-разделитель
GRAY_LIGHT   = {"red": 0.96, "green": 0.96, "blue": 0.96}   # строки данных
WHITE        = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
BLACK        = {"red": 0.0,  "green": 0.0,  "blue": 0.0}

# Ширина колонок в пикселях. Числовые столбцы (Охват..VRpost) — узкие и
# одинаковые; Тема — широкая; Дата — компактная.
COLUMN_WIDTHS = [
    70,   # Дата
    320,  # Тема
    75, 75, 75, 85, 75, 90, 65, 75,  # Охват Реакции Репосты Комментарии Голоса Число действий ERR VRpost
    150,  # Ссылка
    90,   # Переход
    120,  # Достижение цели
    150,  # Коммент
]


def _get_creds_path() -> str | None:
    return os.getenv("GOOGLE_SHEETS_CREDENTIALS")


def _get_sheet_id() -> str | None:
    return os.getenv("GOOGLE_SHEETS_ID")


def _fmt_date_short(d: str) -> str:
    """ГГГГ-ММ-ДД -> ДД.ММ.ГГ — тот же формат, что и в Excel-отчётах."""
    if not d or len(d) != 10 or d[4] != "-":
        return d or ""
    y, m, dd = d.split("-")
    return f"{dd}.{m}.{y[2:]}"


def _safe_pct(val) -> str:
    if val is None:
        return "—"
    return f"{val:.2f}%"


def _post_theme(post: dict, n_words: int = 9) -> str:
    """Берёт первые n_words слов из текста поста."""
    text = post.get("text_short") or post.get("text", "") or post.get("message", "") or ""
    if not text:
        return post.get("content_type", "")
    if post.get("text_short"):
        return text  # уже готовое превью (report.py/dashboard_metrics уже обрезали)
    words = text.split()
    result = " ".join(words[:n_words])
    if len(words) > n_words:
        result += "..."
    return result


def _pad(row: list) -> list:
    """Дополняет строку пустыми ячейками до общей ширины листа N_COLS."""
    return row + [""] * (N_COLS - len(row))


def _build_sheet_plan(channels_data: list, stories_data: dict) -> tuple[list, list]:
    """
    Строит (rows, fmt_blocks).

    rows — список строк для записи значений.
    fmt_blocks — список {"row_1based": int, "kind": "header"|"channel"|"stories"|"total"|"data"}
    описывающих, что за строка и как её нужно оформить — формат
    применяется отдельным пакетным вызовом после записи значений (сам
    .update() со значениями формат не трогает).
    """
    rows: list[list] = []
    fmt_blocks: list[dict] = []

    def add(row: list, kind: str):
        rows.append(_pad(row))
        fmt_blocks.append({"row_1based": len(rows), "kind": kind})

    # Общая шапка — одна на весь лист
    add(list(POSTS_HEADERS), "header")

    for cd in channels_data:
        ch_id = cd["channel_id"]
        subs  = cd["subscribers"]
        posts = cd["posts"]

        add([f"{ch_id}  ({subs:,} подписчиков)".replace(",", " ")], "channel")

        if not posts:
            add(["Нет данных за период"], "data")
        else:
            totals = {k: 0 for k in ["views", "reactions", "forwards", "comments", "votes", "actions"]}
            err_list, vrpost_list = [], []

            for p in posts:
                sn = p.get("snapshot", {}) or {}
                views    = sn.get("views", 0)    or 0
                react    = sn.get("reactions", 0) or 0
                fwd      = sn.get("forwards", 0)  or 0
                comments = sn.get("comments", 0)  or 0
                votes    = sn.get("votes", 0)     or 0
                actions  = sn.get("actions", 0)   or 0

                err    = round(actions / views * 100, 2) if views else None
                vrpost = round(views / subs * 100, 2) if subs else None

                for k, v in [("views", views), ("reactions", react), ("forwards", fwd),
                             ("comments", comments), ("votes", votes), ("actions", actions)]:
                    totals[k] += v
                if err is not None:
                    err_list.append(err)
                if vrpost is not None:
                    vrpost_list.append(vrpost)

                add([
                    _fmt_date_short(p.get("date", "")),
                    _post_theme(p),
                    views, react, fwd, comments, votes, actions,
                    _safe_pct(err), _safe_pct(vrpost),
                    p.get("url", ""),
                    "", "", "",  # Переход, Достижение цели, Коммент — вручную
                ], "data")

            avg_err    = round(sum(err_list) / len(err_list), 2) if err_list else None
            avg_vrpost = round(sum(vrpost_list) / len(vrpost_list), 2) if vrpost_list else None
            add([
                "Итого", f"{len(posts)} постов",
                totals["views"], totals["reactions"], totals["forwards"],
                totals["comments"], totals["votes"], totals["actions"],
                _safe_pct(avg_err), _safe_pct(avg_vrpost),
                "", "", "", "",
            ], "total")

        # ── Сторис канала ──────────────────────────────────────────────
        ch_stories = stories_data.get(ch_id.lstrip("@"), [])
        if ch_stories:
            add(["Сторис"], "stories")
            st_views_total = st_react_total = 0
            for st in ch_stories:
                sn    = st.get("snapshot", {}) or {}
                views = sn.get("views", 0)     or 0
                react = sn.get("reactions", 0) or 0
                st_views_total += views
                st_react_total += react
                add([
                    _fmt_date_short(st.get("date", "")),
                    views, react, st.get("url", ""), "",
                ], "data")
            add(["Итого", st_views_total, st_react_total, "", ""], "total")

    return rows, fmt_blocks


def _apply_formatting(ws, fmt_blocks: list) -> None:
    """Один пакетный вызов на всю раскраску/жирность листа."""
    last_col_letter = _col_letter(N_COLS)

    # Сброс формата всего использованного диапазона (на случай повторной
    # выгрузки того же месяца поверх уже отформатированного листа).
    max_row = max((b["row_1based"] for b in fmt_blocks), default=1)
    ws.format(f"A1:{last_col_letter}{max_row}", {
        "backgroundColor": WHITE,
        "textFormat": {"bold": False, "foregroundColor": BLACK},
    })

    formats = []
    for b in fmt_blocks:
        r = b["row_1based"]
        rng = f"A{r}:{last_col_letter}{r}"
        if b["kind"] == "header":
            formats.append({"range": rng, "format": {
                "backgroundColor": {"red": 0.12, "green": 0.20, "blue": 0.35},
                "textFormat": {"bold": True, "foregroundColor": WHITE},
            }})
        elif b["kind"] == "channel":
            formats.append({"range": rng, "format": {
                "backgroundColor": GREEN_BRIGHT,
                "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 11},
            }})
        elif b["kind"] == "stories":
            formats.append({"range": rng, "format": {
                "backgroundColor": GREEN_LIGHT,
                "textFormat": {"bold": True, "foregroundColor": BLACK},
            }})
        elif b["kind"] == "total":
            formats.append({"range": rng, "format": {
                "textFormat": {"bold": True},
            }})
        elif b["kind"] == "data":
            formats.append({"range": rng, "format": {
                "backgroundColor": GRAY_LIGHT,
            }})

    if formats:
        ws.batch_format(formats)


def _col_letter(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _apply_column_widths(spreadsheet, ws) -> None:
    """Точная ширина колонок в пикселях — через сырой запрос Sheets API,
    у gspread нет прямой обёртки set_column_width."""
    requests = []
    for i, width in enumerate(COLUMN_WIDTHS):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": i,
                    "endIndex": i + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })
    spreadsheet.batch_update({"requests": requests})


def _get_or_create_sheet(spreadsheet, sheet_name: str):
    try:
        return spreadsheet.worksheet(sheet_name)
    except Exception:
        return spreadsheet.add_worksheet(title=sheet_name, rows=500, cols=N_COLS)


async def check_connection() -> tuple[bool, str]:
    creds_path = _get_creds_path()
    sheet_id   = _get_sheet_id()

    if not creds_path or not sheet_id:
        return False, "Google Sheets не настроен в .env (GOOGLE_SHEETS_CREDENTIALS / GOOGLE_SHEETS_ID)"
    if not Path(creds_path).exists():
        return False, f"Файл credentials не найден: {creds_path}"

    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        gc.open_by_key(sheet_id)
        return True, ""
    except ImportError:
        return False, "Библиотека gspread не установлена (pip install gspread google-auth)"
    except Exception as e:
        return False, f"Google Sheets недоступен: {e}"


async def upload_monthly_report(channels_data: list, stories_data: dict,
                                 month_label: str, ym: str):
    creds_path = _get_creds_path()
    sheet_id   = _get_sheet_id()

    if not creds_path or not sheet_id:
        log.warning("Google Sheets не настроен — пропускаем выгрузку.")
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
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(sheet_id)
    except Exception as e:
        log.error(f"Ошибка подключения к Google Sheets: {e}")
        return

    sheet_name = month_label
    try:
        ws = _get_or_create_sheet(spreadsheet, sheet_name)
        ws.clear()

        rows, fmt_blocks = _build_sheet_plan(channels_data, stories_data)

        if rows:
            ws.update("A1", rows, value_input_option="USER_ENTERED")
            _apply_formatting(ws, fmt_blocks)
            _apply_column_widths(spreadsheet, ws)

        log.info(f"Google Sheets: выгружено {len(rows)} строк на лист '{sheet_name}'")

    except Exception as e:
        log.error(f"Ошибка выгрузки в Google Sheets: {e}")
