"""
report.py — генератор Excel-отчётов на основе реестра финальных срезов.

Типы отчётов:
  daily   — посты у которых вышло 24 ч за вчерашний день
  weekly  — посты за прошлую календарную неделю (пн–вс)
  monthly — посты за прошлый календарный месяц + сводка по каналам

Все три типа строятся одними и теми же функциями Excel.
Поправил суточный шаблон → недельный и месячный подтягиваются автоматически.

Запуск:
  python report.py --type daily          # вчерашний день
  python report.py --type weekly         # прошлая неделя
  python report.py --type monthly        # прошлый месяц
  python report.py --type daily --debug  # принудительный debug-режим

Расписание (см. README.md):
  daily   — каждый день в 12:00 МСК (только при DEBUG=true)
  weekly  — каждый понедельник
  monthly — каждое 3-е число
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytz
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from telethon import TelegramClient

load_dotenv()

# ── Конфиг ────────────────────────────────────────────────────────────────
API_ID        = int(os.getenv("API_ID", "0"))
API_HASH      = os.getenv("API_HASH", "")
SESSION_NAME  = os.getenv("SESSION_NAME", "tg_analytics")
CHANNELS_RAW  = os.getenv("CHANNELS", "")
def _parse_ids(key):
    raw = os.getenv(key, "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]

RECIPIENT_IDS = _parse_ids("REPORT_RECIPIENT_ID")
DEBUG_MODE    = os.getenv("DEBUG", "false").lower() == "true"
DEBUG_IDS     = _parse_ids("DEBUG_RECIPIENT_ID")
OUTPUT_DIR    = Path(os.getenv("OUTPUT_DIR", "output"))
REGISTRY_DIR  = Path(os.getenv("REGISTRY_DIR", "registry"))
ARCHIVE_DIR   = Path(os.getenv("ARCHIVE_DIR", "archive"))
LOGS_DIR      = Path(os.getenv("LOGS_DIR", "logs"))
TZ            = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))

CQI_W = {
    "react":   int(os.getenv("CQI_W_REACT",   "1")),
    "vote":    int(os.getenv("CQI_W_VOTE",     "2")),
    "forward": int(os.getenv("CQI_W_FORWARD",  "4")),
    "comment": int(os.getenv("CQI_W_COMMENT",  "5")),
}

PROXY_CFG = None
if os.getenv("PROXY_TYPE"):
    import socks
    _pt = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    PROXY_CFG = (
        _pt.get(os.getenv("PROXY_TYPE","socks5").lower(), socks.SOCKS5),
        os.getenv("PROXY_HOST"), int(os.getenv("PROXY_PORT","1080")),
        True,
        os.getenv("PROXY_USERNAME") or None,
        os.getenv("PROXY_PASSWORD") or None,
    )

for d in [OUTPUT_DIR/"daily", OUTPUT_DIR/"weekly", OUTPUT_DIR/"monthly",
          ARCHIVE_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "report.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

DAYS_RU   = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота","Воскресенье"]
MONTHS_RU = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
             7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

# ── Цвета и стили ─────────────────────────────────────────────────────────
C = dict(dark="1F3864", mid="2E75B6", lite="BDD7EE", yellow="FFF2CC",
         green="E2EFDA", gray="F2F2F2", white="FFFFFF", red="FCE4D6",
         orange="F4B942", purple="D9E1F2")

def _f(bold=False, sz=10, color="000000", italic=False):
    return Font(name="Arial", bold=bold, size=sz, color=color, italic=italic)

def _fill(h): return PatternFill("solid", start_color=h)
def _align(h="center", v="center", wrap=True): return Alignment(horizontal=h, vertical=v, wrap_text=wrap)
def _b(): t=Side(style="thin"); return Border(left=t,right=t,top=t,bottom=t)

def hdr(cell, text, bg=C["dark"], fg=C["white"], sz=10, bold=True, align="center"):
    cell.value=text; cell.font=_f(bold=bold,sz=sz,color=fg)
    cell.fill=_fill(bg); cell.alignment=_align(h=align); cell.border=_b()

def dat(cell, text, bg=C["white"], bold=False, align="center", italic=False, color="000000"):
    cell.value=text; cell.font=_f(bold=bold,color=color,italic=italic)
    cell.fill=_fill(bg); cell.alignment=_align(h=align); cell.border=_b()

def brd(ws, r1, r2, c1, c2):
    for row in ws.iter_rows(min_row=r1,max_row=r2,min_col=c1,max_col=c2):
        for cell in row: cell.border=_b()

def title_block(ws, text, sub, cols, note=True):
    last = get_column_letter(cols)
    ws.merge_cells(f"A1:{last}1")
    ws["A1"].value=text; ws["A1"].font=_f(bold=True,sz=13,color=C["white"])
    ws["A1"].fill=_fill(C["dark"]); ws["A1"].alignment=_align(); ws.row_dimensions[1].height=30

    ws.merge_cells(f"A2:{last}2")
    ws["A2"].value=sub; ws["A2"].font=_f(sz=9,color=C["white"])
    ws["A2"].fill=_fill(C["mid"]); ws["A2"].alignment=_align(); ws.row_dimensions[2].height=16

    if note:
        ws.merge_cells(f"A3:{last}3")
        ws["A3"].value="📌 Описание всех показателей и формул — во вкладке «Пояснения к показателям»"
        ws["A3"].font=_f(sz=9,italic=True,color="595959")
        ws["A3"].fill=_fill(C["lite"]); ws["A3"].alignment=_align(h="left")
        ws.row_dimensions[3].height=15

    ws.sheet_view.showGridLines=False
    return 4  # первая строка данных после заголовков


# ── Метрики ───────────────────────────────────────────────────────────────

def safe_div(a, b, pct=False):
    if not b: return None
    return round((a/b)*(100 if pct else 1), 2)

def calc(snap: dict, subscribers: int) -> dict:
    v=snap.get("views",0); r=snap.get("reactions",0)
    c=snap.get("comments",0); f=snap.get("forwards",0)
    vt=snap.get("votes",0); act=snap.get("actions",0)
    sub=subscribers or 0
    cqi_raw = r*CQI_W["react"]+vt*CQI_W["vote"]+f*CQI_W["forward"]+c*CQI_W["comment"]
    return {
        "views":v,"reactions":r,"comments":c,"forwards":f,"votes":vt,"actions":act,
        "err":   safe_div(act, v, pct=True),
        "er":    safe_div(r+c+f+vt, sub, pct=True),
        "erview":safe_div(r+c+f+vt, v,   pct=True),
        "vrpost":safe_div(v, sub, pct=True),
        "vf":    safe_div(f, v, pct=True),
        "reply": safe_div(c, v, pct=True),
        "poll":  safe_div(vt, v, pct=True) if vt>0 else None,
        "rm":    safe_div(v, sub),
        "cqi":   safe_div(cqi_raw, v),
    }

def lavg(lst):
    vals=[x for x in lst if x is not None]
    return round(sum(vals)/len(vals),2) if vals else None

def fv(cell, v, pct=False, flt=False):
    if v is None: cell.value="—"; return
    if pct:  cell.number_format="0.00%"; cell.value=v/100
    elif flt: cell.number_format="0.00"; cell.value=v
    else:    cell.number_format="#,##0"; cell.value=v


# ── Определения столбцов (единые для всех типов отчётов) ──────────────────
# (label, key_in_snap_or_metric, width, is_pct, is_float)
POST_COLS = [
    ("Дата публ.",   "date",         11, False, False),
    ("Время",        "time",         10, False, False),
    ("Ссылка",       "url",          28, False, False),
    ("Тип контента", "content_type", 14, False, False),
    ("Охват",        "views",        12, False, False),
    ("Реакции",      "reactions",    11, False, False),
    ("Комменты",     "comments",     11, False, False),
    ("Пересылки",    "forwards",     12, False, False),
    ("Голоса",       "votes",        10, False, False),
    ("Действия",     "actions",      12, False, False),
    ("ERR (%)",      "err",          10, True,  False),
    ("ER (%)",       "er",           10, True,  False),
    ("ERview (%)",   "erview",       11, True,  False),
    ("VRpost (%)",   "vrpost",       11, True,  False),
    ("Viral F. (%)", "vf",           12, True,  False),
    ("Reply R. (%)", "reply",        12, True,  False),
    ("Poll R. (%)",  "poll",         11, True,  False),
    ("Reach Mult.",  "rm",           12, False, True),
    ("CQI",          "cqi",          10, False, True),
    ("Примечание",   "_note",        20, False, False),
]

# Столбцы агрегатного листа (По дням / По неделям — одна функция)
AGG_COLS = [
    ("Период",            "_period",   20, False, False),
    ("День недели",       "_dow",      14, False, False),  # только для daily
    ("Постов",            "_count",     9, False, False),
    ("Охват (сумм.)",     "views",     13, False, False),
    ("Ср. охват/пост",    "_avg_views",13, False, True),
    ("Реакции (сумм.)",   "reactions", 13, False, False),
    ("Комменты (сумм.)",  "comments",  13, False, False),
    ("Пересылки (сумм.)", "forwards",  14, False, False),
    ("Голоса (сумм.)",    "votes",     12, False, False),
    ("Действия (сумм.)",  "actions",   13, False, False),
    ("Ср. ERR (%)",       "err",       11, True,  False),
    ("Ср. ER (%)",        "er",        11, True,  False),
    ("Ср. VRpost (%)",    "vrpost",    12, True,  False),
    ("Ср. Viral F. (%)",  "vf",        13, True,  False),
    ("Ср. CQI",           "cqi",       10, False, True),
    ("Лучший пост",       "_best",     28, False, False),
]


# ── Загрузка данных из реестра ────────────────────────────────────────────

def load_registry(channel_username: str) -> dict:
    ch = channel_username.lstrip("@")
    path = REGISTRY_DIR / ch / "registry.json"
    if not path.exists():
        log.warning(f"Реестр не найден: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Ошибка чтения реестра {path}: {e}")
        return {}


def posts_for_period(registry: dict, date_from: date, date_to: date) -> list:
    """Возвращает финальные посты за период [date_from, date_to] включительно."""
    result = []
    for p in registry.get("posts", {}).values():
        if not p.get("is_final") or not p.get("snapshot"):
            continue
        try:
            d = datetime.strptime(p["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if date_from <= d <= date_to:
            result.append(p)
    return sorted(result, key=lambda x: (x.get("date",""), x.get("time","")))


# ── Лист постов (универсальный) ───────────────────────────────────────────

def build_posts_sheet(ws, posts: list, subscribers: int,
                      title: str, subtitle: str) -> dict:
    """
    Строит лист с постами. Используется для daily/weekly/monthly одинаково.
    Возвращает агрегаты для сводного листа.
    """
    hdr_row = title_block(ws, title, subtitle, len(POST_COLS))

    for i, (label, _, width, _, _) in enumerate(POST_COLS, 1):
        hdr(ws[f"{get_column_letter(i)}{hdr_row}"], label, bg=C["mid"])
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[hdr_row].height = 30

    totals  = {k:0 for k in ["views","reactions","comments","forwards","votes","actions"]}
    mlists  = {k:[] for k in ["err","er","erview","vrpost","vf","reply","poll","rm","cqi"]}
    best    = None

    SNAP_KEYS = {"views","reactions","comments","forwards","votes","actions"}

    for idx, p in enumerate(posts):
        r   = hdr_row + 1 + idx
        bg  = C["white"] if idx%2==0 else C["gray"]
        sn  = p.get("snapshot", {})
        m   = calc(sn, subscribers)

        for k in totals:  totals[k] += sn.get(k, 0)
        for k in mlists:
            if m[k] is not None: mlists[k].append(m[k])

        if best is None or (m["cqi"] or 0) > (calc(best.get("snapshot",{}), subscribers)["cqi"] or 0):
            best = p

        for i, (_, key, _, is_pct, is_flt) in enumerate(POST_COLS, 1):
            cell = ws.cell(row=r, column=i)
            cell.fill=_fill(bg); cell.font=_f(sz=10); cell.border=_b()
            cell.alignment=_align(h="left" if i<=4 else "center")
            if key == "_note":
                cell.value = ""
            elif key in SNAP_KEYS:
                fv(cell, sn.get(key), pct=is_pct, flt=is_flt)
            elif key in p:
                cell.value = p[key]
            else:
                fv(cell, m.get(key), pct=is_pct, flt=is_flt)
        ws.row_dimensions[r].height = 18

    # Строка итогов
    rt = hdr_row + 1 + len(posts)
    ws.merge_cells(f"A{rt}:{get_column_letter(4)}{rt}")
    c = ws.cell(row=rt, column=1)
    c.value="ИТОГО / СРЕДНЕЕ"; c.font=_f(bold=True,sz=10)
    c.fill=_fill(C["yellow"]); c.alignment=_align(); c.border=_b()
    for j in range(2,5):
        ws.cell(row=rt,column=j).fill=_fill(C["yellow"]); ws.cell(row=rt,column=j).border=_b()

    SUM_KEYS = {"views","reactions","comments","forwards","votes","actions"}
    for i, (_, key, _, is_pct, is_flt) in enumerate(POST_COLS, 1):
        if i<=4: continue
        cell=ws.cell(row=rt,column=i)
        cell.fill=_fill(C["yellow"]); cell.font=_f(bold=True,sz=10)
        cell.border=_b(); cell.alignment=_align()
        if key in SUM_KEYS: fv(cell, totals[key])
        elif key in mlists: fv(cell, lavg(mlists[key]), pct=is_pct, flt=is_flt)
        else: cell.value="—"
    ws.row_dimensions[rt].height=22

    if best:
        rb = rt+2; n=len(POST_COLS)
        ws.merge_cells(f"A{rb}:{get_column_letter(n)}{rb}")
        ws[f"A{rb}"].value=(f"🏆 Лучший пост по CQI: {best.get('url','')} "
                             f"({best.get('date','')} {best.get('time','')} | "
                             f"Тип: {best.get('content_type','')})")
        ws[f"A{rb}"].font=_f(sz=9,italic=True,color=C["dark"])
        ws[f"A{rb}"].fill=_fill(C["green"]); ws[f"A{rb}"].alignment=_align(h="left")
        ws.row_dimensions[rb].height=18

    return {"totals":totals, "avgs":{k:lavg(v) for k,v in mlists.items()},
            "best":best, "count":len(posts)}


# ── Агрегатный лист (По дням / По неделям — одна функция) ────────────────

def build_agg_sheet(ws, buckets: list, subscribers: int,
                    title: str, subtitle: str,
                    show_dow: bool = False) -> None:
    """
    buckets = [{"label": "01.05.2025", "dow": "Понедельник", "posts": [...]}, ...]
    show_dow=True для суточного листа (показывает день недели).
    """
    cols = AGG_COLS if show_dow else [c for c in AGG_COLS if c[1] != "_dow"]
    n = len(cols)
    hdr_row = title_block(ws, title, subtitle, n)

    for i, (label, _, width, _, _) in enumerate(cols, 1):
        hdr(ws[f"{get_column_letter(i)}{hdr_row}"], label, bg=C["mid"])
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[hdr_row].height = 32

    SNAP_KEYS = {"views","reactions","comments","forwards","votes","actions"}
    all_errs, all_cqis = [], []

    for idx, bucket in enumerate(buckets):
        r   = hdr_row + 1 + idx
        bp  = bucket["posts"]
        is_we = bucket.get("is_weekend", False)
        bg  = C["red"] if is_we else (C["white"] if idx%2==0 else C["gray"])

        metrics = [calc(p.get("snapshot",{}), subscribers) for p in bp]
        views_s = sum(p["snapshot"].get("views",0) for p in bp)
        best_p  = max(bp, key=lambda p: calc(p.get("snapshot",{}),subscribers)["cqi"] or 0) if bp else None

        if bp:
            e=lavg([m["err"] for m in metrics])
            q=lavg([m["cqi"] for m in metrics])
            if e is not None: all_errs.append(e)
            if q is not None: all_cqis.append(q)

        row_vals = {}
        for label, key, _, is_pct, is_flt in cols:
            if   key=="_period":    row_vals[key]=(bucket["label"],False,False)
            elif key=="_dow":       row_vals[key]=(bucket.get("dow",""),False,False)
            elif key=="_count":     row_vals[key]=(len(bp),False,False)
            elif key=="_avg_views": row_vals[key]=(round(views_s/len(bp),1) if bp else None,False,True)
            elif key=="_best":      row_vals[key]=(best_p["url"] if best_p else "—",False,False)
            elif key in SNAP_KEYS:
                row_vals[key]=(sum(p["snapshot"].get(key,0) for p in bp) or None,False,False)
            else:
                row_vals[key]=(lavg([m.get(key) for m in metrics]),is_pct,is_flt)

        for i, (_, key, _, _, _) in enumerate(cols, 1):
            cell=ws.cell(row=r, column=i)
            cell.fill=_fill(bg); cell.font=_f(sz=10); cell.border=_b()
            cell.alignment=_align(h="left" if i<=2 else "center")
            v, is_pct2, is_flt2 = row_vals.get(key,(None,False,False))
            if v is None or (isinstance(v,int) and v==0 and key not in {"_count","_period","_dow"}):
                cell.value = "—" if key not in {"_period","_dow"} else (v or "")
            elif is_pct2: fv(cell, v, pct=True)
            elif is_flt2: fv(cell, v, flt=True)
            else: cell.value=v
        ws.row_dimensions[r].height=18

    # Итого
    rt=hdr_row+1+len(buckets)
    ws.merge_cells(f"A{rt}:{get_column_letter(2 if show_dow else 1)}{rt}")
    ws[f"A{rt}"].value="ИТОГО / СРЕДНЕЕ"
    ws[f"A{rt}"].font=_f(bold=True,sz=10); ws[f"A{rt}"].fill=_fill(C["yellow"])
    ws[f"A{rt}"].alignment=_align(); ws[f"A{rt}"].border=_b()
    if show_dow:
        ws.cell(row=rt,column=2).fill=_fill(C["yellow"]); ws.cell(row=rt,column=2).border=_b()

    all_posts=[p for b in buckets for p in b["posts"]]
    SNAP_KEYS2={"views","reactions","comments","forwards","votes","actions"}
    for i,(_, key, _, is_pct, is_flt) in enumerate(cols,1):
        if i<=(2 if show_dow else 1): continue
        cell=ws.cell(row=rt,column=i)
        cell.fill=_fill(C["yellow"]); cell.font=_f(bold=True,sz=10)
        cell.border=_b(); cell.alignment=_align()
        if key=="_count":     fv(cell, len(all_posts))
        elif key in SNAP_KEYS2:
            fv(cell, sum(p["snapshot"].get(key,0) for p in all_posts))
        elif key=="err":      fv(cell, lavg(all_errs), pct=True)
        elif key=="cqi":      fv(cell, lavg(all_cqis), flt=True)
        elif key=="_avg_views":
            tv=sum(p["snapshot"].get("views",0) for p in all_posts)
            fv(cell, round(tv/len(all_posts),1) if all_posts else None, flt=True)
        else: cell.value="—"
    ws.row_dimensions[rt].height=22
    brd(ws, hdr_row, rt, 1, n)

    # Заметка о выходных (только для суточного)
    if show_dow:
        rn=rt+2
        ws.merge_cells(f"A{rn}:{get_column_letter(n)}{rn}")
        ws[f"A{rn}"].value="🔴 Красным выделены выходные дни (суббота, воскресенье)"
        ws[f"A{rn}"].font=_f(sz=9,italic=True,color="C00000")
        ws[f"A{rn}"].alignment=_align(h="left")
        ws.row_dimensions[rn].height=14

    # Графики
    cats=Reference(ws,min_col=1,min_row=hdr_row+1,max_row=hdr_row+len(buckets))
    col_views=4  # Охват (сумм.)
    col_err  =11 if show_dow else 10  # Ср. ERR

    ch1=BarChart(); ch1.type="col"; ch1.title="Охват по периодам"
    ch1.y_axis.title="Просмотры"; ch1.height=10; ch1.width=20
    dr1=Reference(ws,min_col=col_views,min_row=hdr_row,max_row=hdr_row+len(buckets))
    ch1.add_data(dr1,titles_from_data=True); ch1.set_categories(cats)
    ch1.series[0].graphicalProperties.solidFill=C["mid"]
    ws.add_chart(ch1, f"A{rt+4}")

    ch2=LineChart(); ch2.title="Средний ERR (%) по периодам"
    ch2.y_axis.title="ERR %"; ch2.height=10; ch2.width=20
    dr2=Reference(ws,min_col=col_err,min_row=hdr_row,max_row=hdr_row+len(buckets))
    ch2.add_data(dr2,titles_from_data=True); ch2.set_categories(cats)
    ch2.series[0].graphicalProperties.line.solidFill=C["orange"]
    ws.add_chart(ch2, f"K{rt+4}")


# ── Сводный лист (только для monthly) ────────────────────────────────────

SUMMARY_COLS = [
    ("Канал",              22), ("Подписчики",     13), ("Постов",         10),
    ("Охват (сумм.)",      13), ("Реакции (сумм.)",13), ("Комменты (сумм.)",13),
    ("Пересылки (сумм.)",  14), ("Голоса (сумм.)", 12), ("Действия (сумм.)",14),
    ("Ср. ERR (%)",        11), ("Ср. ER (%)",     11), ("Ср. ERview (%)",  12),
    ("Ср. VRpost (%)",     12), ("Ср. Viral F.(%)",13), ("Ср. Reply R.(%)", 13),
    ("Ср. Reach Mult.",    13), ("Ср. CQI",        10), ("Топ формат",      14),
    ("Лучший пост (CQI)",  28),
]
PCT_COLS_SUM   = {10,11,12,13,14,15}
FLOAT_COLS_SUM = {16,17}

def build_summary_sheet(ws, results: list, label: str, subtitle: str):
    n=len(SUMMARY_COLS)
    hdr_row=title_block(ws, f"📊 СВОДКА — {label}", subtitle, n)

    for i,(lbl,width) in enumerate(SUMMARY_COLS,1):
        hdr(ws[f"{get_column_letter(i)}{hdr_row}"], lbl, bg=C["mid"])
        ws.column_dimensions[get_column_letter(i)].width=width
    ws.row_dimensions[hdr_row].height=30

    for idx,cr in enumerate(results):
        r=hdr_row+1+idx
        bg=C["white"] if idx%2==0 else C["gray"]
        t=cr["totals"]; a=cr["avgs"]
        tf=Counter(p.get("content_type","") for p in cr["posts"]).most_common(1)
        top_fmt=tf[0][0] if tf else "—"
        best_url=cr["best"]["url"] if cr.get("best") else "—"
        row_v=[
            cr["channel_id"], cr["subscribers"], cr["count"],
            t.get("views"), t.get("reactions"), t.get("comments"),
            t.get("forwards"), t.get("votes"), t.get("actions"),
            a.get("err"),a.get("er"),a.get("erview"),a.get("vrpost"),
            a.get("vf"),a.get("reply"),a.get("rm"),a.get("cqi"),
            top_fmt, best_url,
        ]
        for i,v in enumerate(row_v,1):
            cell=ws.cell(row=r,column=i)
            cell.fill=_fill(bg); cell.font=_f(sz=10); cell.border=_b()
            cell.alignment=_align(h="left" if i==1 else "center")
            if v is None: cell.value="—"
            elif i in PCT_COLS_SUM:   fv(cell,v,pct=True)
            elif i in FLOAT_COLS_SUM: fv(cell,v,flt=True)
            elif i in {2,3,4,5,6,7,8,9}: fv(cell,v)
            else: cell.value=v
        ws.row_dimensions[r].height=22

    rt=hdr_row+1+len(results)
    ws.merge_cells(f"A{rt}:C{rt}")
    ws[f"A{rt}"].value="ИТОГО / СРЕДНЕЕ"; ws[f"A{rt}"].font=_f(bold=True,sz=10)
    ws[f"A{rt}"].fill=_fill(C["yellow"]); ws[f"A{rt}"].alignment=_align(); ws[f"A{rt}"].border=_b()
    for j in [2,3]:
        ws.cell(row=rt,column=j).fill=_fill(C["yellow"]); ws.cell(row=rt,column=j).border=_b()
    SUM_J={4,5,6,7,8,9}
    for j in range(4,n+1):
        cell=ws.cell(row=rt,column=j); cell.fill=_fill(C["yellow"])
        cell.font=_f(bold=True,sz=10); cell.border=_b(); cell.alignment=_align()
        keys=["","","","views","reactions","comments","forwards","votes","actions",
              "err","er","erview","vrpost","vf","reply","rm","cqi"]
        key=keys[j-1] if j-1<len(keys) else ""
        if j in SUM_J:
            fv(cell, sum(cr["totals"].get(key,0) for cr in results))
        elif j in PCT_COLS_SUM:
            fv(cell, lavg([cr["avgs"].get(key) for cr in results]), pct=True)
        elif j in FLOAT_COLS_SUM:
            fv(cell, lavg([cr["avgs"].get(key) for cr in results]), flt=True)
        else: cell.value="—"
    ws.row_dimensions[rt].height=22
    brd(ws,hdr_row,rt,1,n)


# ── Лист пояснений ────────────────────────────────────────────────────────

LEGEND = [
    ("Охват (Views)",        "Telegram API: message.views",
     "Суммарное число просмотров поста через 24 ч после публикации", "Базовые данные"),
    ("Реакции (React)",      "Telegram API: сумма всех реакций",
     "Все эмодзи-реакции: 👍 ❤️ 🔥 и т.д.", "Базовые данные"),
    ("Комментарии",          "Telegram API: replies.replies",
     "Число комментариев в linked-группе канала", "Базовые данные"),
    ("Пересылки (Forwards)", "Telegram API: message.forwards",
     "Сколько раз пост был переслан другим пользователям", "Базовые данные"),
    ("Голоса (Votes)",       "Telegram API: сумма voters по вариантам опроса",
     "Сумма всех голосов в опросе. Для постов без опроса = 0", "Базовые данные"),
    ("Число действий",       "Views + React + Comments + Forwards + Votes",
     "Совокупная активность вокруг поста", "Базовые данные"),
    ("ERR (%)",              "Actions / Views × 100%",
     "Доля просмотревших, совершивших хоть какое-то действие. Норма: 3–8%", "Коэффициенты"),
    ("ER (%)",               "(React+Comments+Fwd+Votes) / Подписчики × 100%",
     "Классический ER. Не зависит от алгоритмов показа", "Коэффициенты"),
    ("ERview (%)",           "(React+Comments+Fwd+Votes) / Views × 100%",
     "Насколько аудитория, увидевшая пост, реагирует на него", "Коэффициенты"),
    ("VRpost (%)",           "Views / Подписчики × 100%",
     "Какой % подписчиков увидел пост", "Коэффициенты"),
    ("Viral Factor (%)",     "Forwards / Views × 100%",
     "Доля зрителей, поделившихся постом", "Виральность"),
    ("Reply Rate (%)",       "Comments / Views × 100%",
     "Насколько пост провоцирует диалог", "Виральность"),
    ("Poll Rate (%)",        "Votes / Views × 100%",
     "Только для опросов. Доля проголосовавших среди просмотревших", "Виральность"),
    ("Reach Multiplier",     "Views / Подписчики",
     "Коэффициент > 1: пост вышел за пределы базы подписчиков", "Виральность"),
    ("CQI",
     f"(React×{CQI_W['react']}+Votes×{CQI_W['vote']}+Fwd×{CQI_W['forward']}+Comments×{CQI_W['comment']}) / Views × 100",
     f"Взвешенный индекс качества. Веса настраиваются в .env", "Индексы"),
]

BLOCK_C={"Базовые данные":C["lite"],"Коэффициенты":C["purple"],"Виральность":C["green"],"Индексы":C["yellow"]}

def build_legend_sheet(wb: Workbook):
    ws=wb.create_sheet("Пояснения к показателям")
    ws.sheet_view.showGridLines=False
    ws.merge_cells("A1:D1")
    ws["A1"].value="📖 ПОЯСНЕНИЯ К ПОКАЗАТЕЛЯМ И ФОРМУЛАМ"
    ws["A1"].font=_f(bold=True,sz=13,color=C["white"])
    ws["A1"].fill=_fill(C["dark"]); ws["A1"].alignment=_align(); ws.row_dimensions[1].height=30

    for col_l,text,width in [("A","Показатель",26),("B","Формула / Источник",40),
                               ("C","Описание",60),("D","Блок",20)]:
        hdr(ws[f"{col_l}2"], text, bg=C["mid"])
        ws.column_dimensions[col_l].width=width
    ws.row_dimensions[2].height=20

    for i,(name,formula,desc,block) in enumerate(LEGEND):
        r=3+i; bg=C["white"] if i%2==0 else C["gray"]
        dat(ws[f"A{r}"],name,bg=bg,bold=True,align="left")
        dat(ws[f"B{r}"],formula,bg=bg,italic=True,color=C["dark"],align="left")
        dat(ws[f"C{r}"],desc,bg=bg,align="left")
        ws[f"D{r}"].value=block; ws[f"D{r}"].font=_f(sz=9,bold=True)
        ws[f"D{r}"].fill=_fill(BLOCK_C.get(block,C["white"]))
        ws[f"D{r}"].alignment=_align(); ws[f"D{r}"].border=_b()
        ws.row_dimensions[r].height=32
    brd(ws,2,2+len(LEGEND),1,4)

    # Веса CQI
    rc=3+len(LEGEND)+2
    ws.merge_cells(f"A{rc}:D{rc}")
    ws[f"A{rc}"].value="⚖️  ВЕСА CQI — настраиваются в .env (CQI_W_REACT / CQI_W_VOTE / CQI_W_FORWARD / CQI_W_COMMENT)"
    ws[f"A{rc}"].font=_f(bold=True,sz=10,color=C["white"])
    ws[f"A{rc}"].fill=_fill(C["dark"]); ws[f"A{rc}"].alignment=_align(h="left")
    ws.row_dimensions[rc].height=22
    for j,(act,w,reason) in enumerate([
        ("Реакция (лайк, эмодзи)",f"× {CQI_W['react']}","Минимальное усилие"),
        ("Голос в опросе",f"× {CQI_W['vote']}","Требует прочтения вариантов"),
        ("Пересылка (Forward)",f"× {CQI_W['forward']}","Решение поделиться = высокая оценка"),
        ("Комментарий",f"× {CQI_W['comment']}","Максимальное усилие — написать текст"),
    ]):
        r2=rc+1+j; bg=C["white"] if j%2==0 else C["gray"]
        dat(ws[f"A{r2}"],act,bg=bg,align="left")
        dat(ws[f"B{r2}"],w,bg=bg,bold=True)
        dat(ws[f"C{r2}"],reason,bg=bg,align="left")
        ws[f"D{r2}"].fill=_fill(bg); ws[f"D{r2}"].border=_b()
        ws.row_dimensions[r2].height=20
    brd(ws,rc,rc+4,1,4)


# ── Построение периодов ───────────────────────────────────────────────────

def make_daily_buckets(posts: list, date_from: date, date_to: date) -> list:
    by_date={}
    for p in posts:
        by_date.setdefault(p["date"],[]).append(p)
    buckets=[]
    cur=date_from
    while cur<=date_to:
        ds=cur.strftime("%Y-%m-%d")
        dow=cur.weekday()
        buckets.append({
            "label":    cur.strftime("%d.%m.%Y"),
            "dow":      DAYS_RU[dow],
            "is_weekend": dow>=5,
            "posts":    by_date.get(ds,[]),
        })
        cur+=timedelta(days=1)
    return buckets


def make_weekly_buckets(posts: list, date_from: date, date_to: date) -> list:
    by_date={}
    for p in posts:
        by_date.setdefault(p["date"],[]).append(p)
    # Разбиваем на недели пн–вс
    buckets=[]; cur=date_from
    # Откат до ближайшего пн
    cur-=timedelta(days=cur.weekday())
    while cur<=date_to:
        wend=min(cur+timedelta(days=6), date_to)
        wp=[]
        d=cur
        while d<=wend:
            wp.extend(by_date.get(d.strftime("%Y-%m-%d"),[]))
            d+=timedelta(days=1)
        buckets.append({
            "label": f"{cur.strftime('%d.%m')} – {wend.strftime('%d.%m.%Y')}",
            "posts": wp,
        })
        cur+=timedelta(days=7)
    return buckets


# ── Сборка и отправка отчёта ─────────────────────────────────────────────

def determine_period(report_type: str) -> tuple[date, date, str]:
    """Возвращает (date_from, date_to, label)."""
    today=date.today()
    if report_type=="daily":
        d=today-timedelta(days=1)
        return d, d, d.strftime("%d.%m.%Y")
    elif report_type=="weekly":
        # Прошлая неделя (пн–вс)
        mon=today-timedelta(days=today.weekday()+7)
        sun=mon+timedelta(days=6)
        return mon, sun, f"{mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')}"
    elif report_type=="monthly":
        first=today.replace(day=1)-timedelta(days=1)
        d_from=first.replace(day=1)
        return d_from, first, first.strftime("%B %Y")
    raise ValueError(f"Неизвестный тип: {report_type}")


def archive_monthly(channel_username: str, ym: str):
    """Переносит финальные записи за месяц в архив, оставляя незакрытые в реестре."""
    ch=channel_username.lstrip("@")
    reg_path=REGISTRY_DIR/ch/"registry.json"
    if not reg_path.exists(): return

    registry=json.loads(reg_path.read_text(encoding="utf-8"))
    posts=registry.get("posts",{})

    to_archive={mid:p for mid,p in posts.items()
                if p.get("is_final") and p.get("date","")[:7]==ym}
    remaining  ={mid:p for mid,p in posts.items() if mid not in to_archive}

    if to_archive:
        arch_dir=ARCHIVE_DIR/ch; arch_dir.mkdir(parents=True,exist_ok=True)
        arch_path=arch_dir/f"archive_{ym}.json"
        arch_data={"channel_id":channel_username,"month":ym,"posts":to_archive}
        arch_path.write_text(json.dumps(arch_data,ensure_ascii=False,indent=2),encoding="utf-8")
        log.info(f"Архивировано {len(to_archive)} постов → {arch_path}")

    registry["posts"]=remaining
    reg_path.write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding="utf-8")
    log.info(f"Реестр {ch} очищен за {ym}, осталось {len(remaining)} постов")


async def build_and_send(report_type: str, debug_override: bool = False, month_override: str = None):
    is_debug = DEBUG_MODE or debug_override
    recipients = DEBUG_IDS if is_debug else RECIPIENT_IDS
    channels = [c.strip() for c in CHANNELS_RAW.split(",") if c.strip()]

    if month_override and report_type == "monthly":
        year, mo = int(month_override[:4]), int(month_override[5:7])
        from calendar import monthrange
        d_from = date(year, mo, 1)
        d_to   = date(year, mo, monthrange(year, mo)[1])
        period_label = f"{MONTHS_RU.get(mo, str(mo))} {year}"
        date_from, date_to = d_from, d_to
    else:
        date_from, date_to, period_label = determine_period(report_type)
    ym = date_from.strftime("%Y-%m")
    month_name = MONTHS_RU.get(date_from.month, str(date_from.month))

    log.info(f"=== Генерация отчёта | тип={report_type} | период={period_label} | debug={is_debug} ===")

    out_dir  = OUTPUT_DIR / {"daily":"daily","weekly":"weekly","monthly":"monthly"}[report_type]
    out_dir.mkdir(parents=True, exist_ok=True)

    kwargs = {"proxy": PROXY_CFG} if PROXY_CFG else {}
    _own_client = False
    if "client" not in dir():
        _own_client = True
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:

        channel_results = []

        for ch in channels:
            registry = load_registry(ch)
            if not registry:
                log.warning(f"Пустой реестр для {ch}, пропускаем")
                continue

            subscribers = registry.get("subscribers", 0)
            ch_title    = registry.get("channel_title", ch)
            ch_username = registry.get("channel_id", ch).lstrip("@")
            posts = posts_for_period(registry, date_from, date_to)

            log.info(f"  {ch}: постов за период={len(posts)}, подписчиков={subscribers}")
            if not posts:
                log.warning(f"  {ch}: нет финальных постов за период, пропускаем")
                continue

            wb = Workbook(); wb.remove(wb.active)

            # Лист 1: Посты (универсальный)
            type_labels = {
                "daily":   (f"📋 {ch} — ПОСТЫ | {period_label}",
                            "Статистика через 24 ч после публикации каждого поста"),
                "weekly":  (f"📋 {ch} — ПОСТЫ ЗА НЕДЕЛЮ | {period_label}",
                            "Статистика через 24 ч после публикации каждого поста"),
                "monthly": (f"📋 {ch} — ПОСТЫ ЗА {month_name.upper()} {date_from.year}",
                            "Статистика через 24 ч после публикации каждого поста"),
            }
            ws_posts = wb.create_sheet("Посты")
            stats = build_posts_sheet(ws_posts, posts, subscribers, *type_labels[report_type])

            # Лист 2: По дням
            ws_daily = wb.create_sheet("По дням")
            daily_buckets = make_daily_buckets(posts, date_from, date_to)
            build_agg_sheet(
                ws_daily, daily_buckets, subscribers,
                f"📅 {ch} — ПО ДНЯМ | {period_label}",
                "Каждая строка = один день. Агрегаты по постам за сутки.",
                show_dow=True,
            )

            # Лист 3: По неделям (только для weekly и monthly)
            if report_type in ("weekly","monthly"):
                ws_weekly = wb.create_sheet("По неделям")
                weekly_buckets = make_weekly_buckets(posts, date_from, date_to)
                build_agg_sheet(
                    ws_weekly, weekly_buckets, subscribers,
                    f"📆 {ch} — ПО НЕДЕЛЯМ | {period_label}",
                    "Каждая строка = одна календарная неделя.",
                    show_dow=False,
                )

            build_legend_sheet(wb)

            fname = f"{ch_username}_{ym}_{report_type}.xlsx"
            path  = out_dir / fname
            wb.save(path)
            log.info(f"  Сохранён файл: {path}")

            channel_results.append({
                "channel_id": ch, "subscribers": subscribers,
                "posts": posts, "totals": stats["totals"],
                "avgs": stats["avgs"], "best": stats["best"],
                "count": stats["count"],
            })

        # Сводный файл (только для monthly)
        summary_path = None
        if report_type == "monthly" and channel_results:
            wb_sum = Workbook(); wb_sum.remove(wb_sum.active)
            ws_sum = wb_sum.create_sheet("Сводка по каналам")
            build_summary_sheet(
                ws_sum, channel_results,
                f"{month_name.upper()} {date_from.year}",
                f"Период: {date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')} | "
                f"Собрано: {date.today().strftime('%d.%m.%Y')}",
            )
            # Добавляем посты каждого канала в сводный файл
            for cr in channel_results:
                cu=cr["channel_id"].lstrip("@")
                reg2=load_registry(cr["channel_id"])
                pp=posts_for_period(reg2,date_from,date_to)
                ws_ch=wb_sum.create_sheet(f"{cu[:20]} посты")
                build_posts_sheet(ws_ch, pp, cr["subscribers"],
                    f"📋 {cr['channel_id']} — {month_name.upper()} {date_from.year}",
                    "Статистика через 24 ч после публикации")
            build_legend_sheet(wb_sum)
            summary_path = out_dir / f"monthly_{ym}_сводка.xlsx"
            wb_sum.save(summary_path)
            log.info(f"Сводный файл: {summary_path}")

            # Архивируем данные за месяц
            for ch in channels:
                archive_monthly(ch, ym)

        # Отправка в Telegram
        if not recipients:
            log.info("Получатели не заданы — отправка пропущена")
            return

        files = list(out_dir.glob(f"*_{ym}_{report_type}.xlsx"))
        if summary_path and summary_path.exists():
            files.append(summary_path)

        if not files:
            log.warning("Нет файлов для отправки")
            return

        debug_tag = " [DEBUG]" if is_debug else ""
        caption = (
            f"📊{debug_tag} Отчёт «{report_type}» | {period_label} | "
            f"{len(files)} файл(ов) | каналов: {len(channel_results)}"
        )
        for uid in recipients:
            try:
                await client.send_message(uid, caption)
                for fp in files:
                    await client.send_file(uid, str(fp), caption=f"📎 {fp.name}")
                    log.info(f"Отправлен {fp.name} → {uid}")
            except Exception as e:
                log.error(f"Ошибка отправки → {uid}: {e}")

    log.info("=== Готово ===")


def main():
    parser = argparse.ArgumentParser(description="TG Analytics — генератор отчётов")
    parser.add_argument("--type", required=True,
                        choices=["daily","weekly","monthly"],
                        help="Тип отчёта")
    parser.add_argument("--debug", action="store_true",
                        help="Принудительный debug-режим (отправка на DEBUG_RECIPIENT_ID)")
    args = parser.parse_args()
    asyncio.run(build_and_send(args.type, debug_override=args.debug))

if __name__ == "__main__":
    main()
