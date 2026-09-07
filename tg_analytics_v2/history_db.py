"""
history_db.py — база данных исторических метрик по месяцам.

Хранит агрегаты за каждый месяц по каждому каналу:
  - подписчики (абсолютное число на конец месяца)
  - прирост подписчиков за месяц
  - средний охват поста
  - ERR (%)
  - VRpost (%)

Данные не перезаписываются — только дополняются.
Хранение: registry/history_db.json

Начальные данные (февраль–июнь 2026) зашиты из предоставленного Excel.
Новые данные добавляются автоматически после генерации месячного отчёта.

Формат хранения:
{
  "2026-02": {
    "@ktsdaily":      {"subscribers": 3920, "growth": 16,  "avg_reach": 1642, "err": 2.39, "vrpost": null},
    "@metaclass":     {"subscribers": 1226, "growth": 15,  "avg_reach": 1418, "err": 2.5,  "vrpost": null},
    ...
  },
  "2026-03": { ... }
}
"""

import json
import logging
from datetime import datetime

from config import REGISTRY_DIR, TZ

log = logging.getLogger(__name__)

DB_PATH = REGISTRY_DIR / "history_db.json"

# ── Начальные данные из Excel (февраль–июнь 2026) ─────────────────────────
# vrpost для сид-данных неизвестен (в исходном Excel не было) — храним None,
# график динамики VRpost покажет для этих месяцев пропуск (null), а не 0.
SEED_DATA = {
    "2026-02": {
        "@ktsdaily":      {"subscribers": 3920, "growth": 16,   "avg_reach": 1642, "err": 2.39, "vrpost": None},
        "@metaclass":     {"subscribers": 1226, "growth": 15,   "avg_reach": 1418, "err": 2.5,  "vrpost": None},
        "@inside_ai_tech":{"subscribers": 2510, "growth": 290,  "avg_reach": 1059, "err": 2.8,  "vrpost": None},
        "@kts_specials":  {"subscribers": 331,  "growth": 3,    "avg_reach": 389,  "err": 10.6, "vrpost": None},
        "@kod_v_kaske":   {"subscribers": 236,  "growth": 30,   "avg_reach": 635,  "err": 3.0,  "vrpost": None},
        "@smartbotpro":   {"subscribers": 631,  "growth": 52,   "avg_reach": 278,  "err": 3.9,  "vrpost": None},
    },
    "2026-03": {
        "@ktsdaily":      {"subscribers": 3968, "growth": 48,   "avg_reach": 1568, "err": 2.42, "vrpost": None},
        "@metaclass":     {"subscribers": 1222, "growth": -4,   "avg_reach": 1213, "err": 2.33, "vrpost": None},
        "@inside_ai_tech":{"subscribers": 2979, "growth": 469,  "avg_reach": 998,  "err": 2.52, "vrpost": None},
        "@kts_specials":  {"subscribers": 336,  "growth": 4,    "avg_reach": 758,  "err": 4.48, "vrpost": None},
        "@kod_v_kaske":   {"subscribers": 293,  "growth": 27,   "avg_reach": 391,  "err": 3.2,  "vrpost": None},
        "@smartbotpro":   {"subscribers": 605,  "growth": -26,  "avg_reach": 288,  "err": 3.97, "vrpost": None},
    },
    "2026-04": {
        "@ktsdaily":      {"subscribers": 3949, "growth": -19,  "avg_reach": 1548, "err": 2.15, "vrpost": None},
        "@metaclass":     {"subscribers": 1197, "growth": -25,  "avg_reach": 1068, "err": 2.49, "vrpost": None},
        "@inside_ai_tech":{"subscribers": 3011, "growth": 32,   "avg_reach": 744,  "err": 3.27, "vrpost": None},
        "@kts_specials":  {"subscribers": 367,  "growth": 31,   "avg_reach": 348,  "err": 9.91, "vrpost": None},
        "@kod_v_kaske":   {"subscribers": 315,  "growth": 22,   "avg_reach": 375,  "err": 5.6,  "vrpost": None},
        "@smartbotpro":   {"subscribers": 598,  "growth": -7,   "avg_reach": 201,  "err": 5.4,  "vrpost": None},
    },
    "2026-05": {
        "@ktsdaily":      {"subscribers": 3963, "growth": 14,   "avg_reach": 1310, "err": 2.6,  "vrpost": None},
        "@metaclass":     {"subscribers": 1207, "growth": 10,   "avg_reach": 1151, "err": 3.6,  "vrpost": None},
        "@inside_ai_tech":{"subscribers": 3540, "growth": 529,  "avg_reach": 1126, "err": 1.9,  "vrpost": None},
        "@kts_specials":  {"subscribers": 374,  "growth": 7,    "avg_reach": 397,  "err": 8.9,  "vrpost": None},
        "@kod_v_kaske":   {"subscribers": 328,  "growth": 13,   "avg_reach": 504,  "err": 4.0,  "vrpost": None},
        "@smartbotpro":   {"subscribers": 604,  "growth": 6,    "avg_reach": 198,  "err": 6.0,  "vrpost": None},
    },
    "2026-06": {
        "@ktsdaily":      {"subscribers": 3990, "growth": 27,   "avg_reach": 1249, "err": 2.79, "vrpost": None},
        "@metaclass":     {"subscribers": 1299, "growth": 92,   "avg_reach": 1103, "err": 3.35, "vrpost": None},
        "@inside_ai_tech":{"subscribers": 3191, "growth": 379,  "avg_reach": 820,  "err": 2.8,  "vrpost": None},
        "@kts_specials":  {"subscribers": 374,  "growth": 60,   "avg_reach": 450,  "err": 9.58, "vrpost": None},
        "@kod_v_kaske":   {"subscribers": 336,  "growth": 8,    "avg_reach": 201,  "err": 8.0,  "vrpost": None},
        "@smartbotpro":   {"subscribers": 608,  "growth": 4,    "avg_reach": 0,    "err": 6.29, "vrpost": None},
    },
}


# ── Загрузка / сохранение ─────────────────────────────────────────────────

def load_db() -> dict:
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"Ошибка чтения history_db: {e}")
    return {}


def save_db(db: dict):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_seeded():
    """Записывает начальные данные если база пустая."""
    db = load_db()
    changed = False
    for ym, channels in SEED_DATA.items():
        if ym not in db:
            db[ym] = channels
            changed = True
            log.info(f"history_db: инициализированы данные за {ym}")
    if changed:
        save_db(db)


# ── Чтение ────────────────────────────────────────────────────────────────

def get_history(channel: str, months: int = 6, end_ym: str = None) -> list[dict]:
    """
    Возвращает данные по каналу за последние N месяцев.
    end_ym — последний месяц включительно (например "2026-07").
              Если не указан — текущий месяц.
    Возвращает список dict: {ym, month_label, subscribers, growth, avg_reach, err, vrpost}
    """
    db = load_db()
    if end_ym is None:
        end_ym = datetime.now(TZ).strftime("%Y-%m")

    # Генерируем список месяцев от end_ym назад на months штук
    year, month = int(end_ym[:4]), int(end_ym[5:7])
    month_list = []
    for _ in range(months):
        month_list.append(f"{year}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_list.reverse()

    MONTHS_RU_SHORT = {
        1:"Янв",2:"Фев",3:"Мар",4:"Апр",5:"Май",6:"Июн",
        7:"Июл",8:"Авг",9:"Сен",10:"Окт",11:"Ноя",12:"Дек"
    }

    result = []
    for ym in month_list:
        m = int(ym[5:7])
        entry = db.get(ym, {}).get(channel, {})
        result.append({
            "ym":          ym,
            "month_label": MONTHS_RU_SHORT.get(m, ym),
            "subscribers": entry.get("subscribers"),
            "growth":      entry.get("growth"),
            "avg_reach":   entry.get("avg_reach"),
            "err":         entry.get("err"),
            "vrpost":      entry.get("vrpost"),
        })
    return result


def get_all_channels_history(channels: list, months: int = 6,
                              end_ym: str = None) -> dict:
    """Возвращает историю для всех каналов. {channel: [entries]}"""
    return {ch: get_history(ch, months, end_ym) for ch in channels}


# ── Запись новых данных ───────────────────────────────────────────────────

def record_month(ym: str, channel: str, subscribers: int, growth: int | None,
                 avg_reach: float, err: float, vrpost: float = None):
    """
    Записывает данные за месяц для одного канала.
    Если запись уже есть — не перезаписывает (данные константа).

    growth и vrpost могут быть None (нет данных для точного расчёта) —
    в этом случае пишем None, а не 0, чтобы не путать "нет данных" с
    "прирост/видимость равны нулю" (0 — вполне реальное значение и для
    прироста, и в редких случаях для VRpost).
    """
    db = load_db()
    if ym not in db:
        db[ym] = {}
    if channel in db[ym]:
        log.debug(f"history_db: {channel} {ym} уже записан, пропуск")
        return
    db[ym][channel] = {
        "subscribers": subscribers,
        "growth":      growth,
        "avg_reach":   round(avg_reach, 1) if avg_reach else 0,
        "err":         round(err, 2) if err else 0,
        "vrpost":      round(vrpost, 2) if vrpost is not None else None,
    }
    save_db(db)
    log.info(f"history_db: записан {channel} {ym} — sub={subscribers} growth={growth} vrpost={vrpost}")


def record_month_from_report(ym: str, channels_data: list):
    """
    Вызывается после генерации месячного отчёта (и Dashboard).
    Извлекает нужные агрегаты из channels_data и записывает в историю.

    channels_data: список dict {channel_id, subscribers, posts}.
    Если posts пуст — запись пропускается (нечего агрегировать), поэтому
    вызывающий код обязан передавать реальный список постов канала,
    а не заглушку.
    """
    for cd in channels_data:
        ch    = cd["channel_id"]
        subs  = cd.get("subscribers", 0)
        posts = cd.get("posts", [])

        if not posts:
            log.warning(f"history_db: {ch} {ym} — посты не переданы, "
                        f"запись истории пропущена (нет данных для агрегации)")
            continue

        # Средний охват
        views_list = [p.get("snapshot", {}).get("views", 0) for p in posts
                      if p.get("snapshot")]
        avg_reach = sum(views_list) / len(views_list) if views_list else 0

        # ERR — среднее по постам
        err_list = []
        for p in posts:
            sn = p.get("snapshot", {})
            v  = sn.get("views", 0)
            act= sn.get("actions", 0)
            if v:
                err_list.append(act / v * 100)
        avg_err = sum(err_list) / len(err_list) if err_list else 0

        # VRpost (%) — среднее по постам (views_поста / подписчики × 100),
        # та же методика, что и колонка "Ср. VRpost (%)" в Excel-отчёте
        # (report.py: calc() на каждый пост → среднее по каналу) и на
        # слайде показателей канала в Dashboard (dashboard_metrics.py).
        # Раньше здесь стояло avg_reach/subs*100 (средний охват вместо
        # среднего по постам) — другая методика, из-за которой цифра на
        # графике динамики отличалась от того же показателя на слайде и в
        # Excel за тот же период.
        vrpost_list = []
        for p in posts:
            sn = p.get("snapshot", {})
            v  = sn.get("views", 0)
            if subs:
                vrpost_list.append(v / subs * 100)
        vrpost = sum(vrpost_list) / len(vrpost_list) if vrpost_list else None

        # Прирост — строго по границе месяца (текущая численность на
        # конец ym минус численность на конец предыдущего месяца), а не
        # "две последние попавшиеся записи в истории".
        from snapshot import get_month_growth
        growth = get_month_growth(ch, ym)

        record_month(ym, ch, subs, growth, avg_reach, avg_err, vrpost)
