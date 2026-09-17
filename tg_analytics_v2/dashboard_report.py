"""
dashboard_report.py — генератор Dashboard-презентации (.pptx).

Структура презентации:
  Слайд 1:  Обложка
  Слайд 2:  Exec Summary по всем каналам
  Слайд 3:  Динамика подписчиков — 6 месяцев (Line chart)
  Слайд 4:  Динамика среднего охвата — 6 месяцев (Line chart)
  Слайд 5:  Прирост подписчиков — 6 месяцев (Bar chart)
  Слайд 6:  Динамика ER — 6 месяцев (Line chart)
  Слайд 7:  Контентная активность за месяц (два Bar chart)

  Блок на канал (повторяется для каждого канала):
  Слайд A:  Executive Summary канала
  Слайд B:  Динамика канала за 6 месяцев
  Слайд C:  Показатели месяца + лучшие/худшие посты
  Слайд D:  Платные посевы (только если есть данные)

Цвета каналов фиксированы из DASHBOARD_CHANNELS в config.py.
"""

import logging
import os
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

# ── Вспомогательные функции ───────────────────────────────────────────────
# (форматирование чисел/процентов/дат теперь делается на стороне JS-шаблона —
#  см. fmtDate() и toLocaleString() внутри _make_pptx_script; отдельные
#  Python-хелперы _fmt_num/_fmt_pct/_fmt_money/_fmt_growth/_footer_text были
#  дублирующим мёртвым кодом и удалены)

def _channel_color(ch: str, cfg: dict) -> str:
    """Возвращает hex-цвет без # для pptxgenjs."""
    color = cfg.get(ch, {}).get("color", "#333333")
    return color.lstrip("#")


def _channel_name(ch: str, cfg: dict) -> str:
    return cfg.get(ch, {}).get("name", ch)


MONTHS_PREP_RU = {  # предложный падеж — "в августе", а не "в август"
    1:"январе", 2:"феврале", 3:"марте", 4:"апреле", 5:"мае", 6:"июне",
    7:"июле", 8:"августе", 9:"сентябре", 10:"октябре", 11:"ноябре", 12:"декабре",
}


def _delta_status(delta: float | None) -> str:
    if delta is None:
        return "stable"
    if delta > 10:
        return "up"
    if delta < -10:
        return "down"
    return "stable"


def build_activity_summary_text(month_num: int,
                                 posts_current: int, reactions_current: int,
                                 comments_current: int, forwards_current: int,
                                 actions_current: int,
                                 month_activities: list) -> str | None:
    """
    Короткий текстовый вывод под графиком "Активность и реакции
    аудитории" — сравнение СУММАРНЫХ показателей текущего месяца со
    средним суммарным за предыдущие 5 полных месяцев (без деления на
    количество публикаций — так попросили сравнивать).

    month_activities — список из 5 dict {posts_count, total_react,
    total_comments, total_fwd, total_actions} за предыдущие 5 месяцев
    (не обязательно в каком-то порядке).

    Возвращает None, если сравнивать не с чем (нет ни одного из 5
    предыдущих месяцев).
    """
    if not month_activities:
        return None

    avg_posts     = sum(m.get("posts_count", 0)    for m in month_activities) / len(month_activities)
    avg_reactions = sum(m.get("total_react", 0)     for m in month_activities) / len(month_activities)
    avg_comments  = sum(m.get("total_comments", 0)  for m in month_activities) / len(month_activities)
    avg_forwards  = sum(m.get("total_fwd", 0)        for m in month_activities) / len(month_activities)
    avg_actions   = sum(m.get("total_actions", 0)    for m in month_activities) / len(month_activities)

    def _delta(curr, avg):
        return (curr - avg) / avg * 100 if avg else None

    posts_delta     = _delta(posts_current, avg_posts)
    reactions_delta = _delta(reactions_current, avg_reactions)
    comments_delta  = _delta(comments_current,  avg_comments)
    forwards_delta  = _delta(forwards_current,  avg_forwards)
    actions_delta   = _delta(actions_current,   avg_actions)

    posts_status     = _delta_status(posts_delta)
    actions_status   = _delta_status(actions_delta)
    reactions_status = _delta_status(reactions_delta)
    comments_status  = _delta_status(comments_delta)
    forwards_status  = _delta_status(forwards_delta)

    month_word = MONTHS_PREP_RU.get(month_num, "")

    # А. Публикации
    if posts_status == "up":
        s1 = f"В {month_word} вышло на {round(abs(posts_delta))}% больше публикаций, чем в среднем за предыдущие 5 месяцев."
    elif posts_status == "down":
        s1 = f"В {month_word} вышло на {round(abs(posts_delta))}% меньше публикаций, чем в среднем за предыдущие 5 месяцев."
    else:
        s1 = f"В {month_word} количество публикаций осталось примерно на уровне среднего за предыдущие 5 месяцев."

    # Б. Действия — суммарно за месяц, без деления на число публикаций
    if actions_status == "up":
        s2 = f"Общее количество действий выросло на {round(abs(actions_delta))}% по сравнению со средним за предыдущие 5 месяцев."
    elif actions_status == "down":
        s2 = f"Общее количество действий снизилось на {round(abs(actions_delta))}% по сравнению со средним за предыдущие 5 месяцев."
    else:
        s2 = "Общее количество действий осталось примерно на среднем уровне."

    # В. Реакции / Комментарии / Репосты — тоже суммарно
    def _phrase(status, delta, verb_word, stable_text):
        if status == "up":
            return f"{verb_word} на {round(abs(delta))}% больше"
        elif status == "down":
            return f"{verb_word} на {round(abs(delta))}% меньше"
        return stable_text

    react_phrase = _phrase(reactions_status, reactions_delta,
                            "реакций стало",
                            "реакции остались примерно на среднем уровне")
    comments_phrase = _phrase(comments_status, comments_delta,
                               "комментариев стало",
                               "комментарии остались примерно на среднем уровне")
    forwards_phrase = _phrase(forwards_status, forwards_delta,
                               "репостов стало",
                               "репосты остались примерно на среднем уровне")

    s3 = f"{react_phrase[0].upper()}{react_phrase[1:]}, {comments_phrase}, а {forwards_phrase}."

    # Г. Итоговая интерпретация — по суммарной активности, а не по
    # эффективности отдельной публикации (раз считаем теперь суммарно).
    if actions_status == "stable":
        s4 = "В целом активность аудитории осталась на уровне предыдущих месяцев."
    elif posts_status == "down" and actions_status == "up":
        s4 = "Несмотря на меньший объём контента, суммарная активность аудитории оказалась выше обычного."
    elif posts_status == "up" and actions_status == "up":
        s4 = "Рост объёма контента сопровождался ростом суммарной активности аудитории."
    elif posts_status == "up" and actions_status == "down":
        s4 = "Публикаций стало больше, однако суммарная активность аудитории снизилась."
    elif posts_status == "down" and actions_status == "down":
        s4 = "Снижение объёма контента сопровождалось снижением активности аудитории."
    elif posts_status == "stable" and actions_status == "up":
        s4 = "При сопоставимом объёме контента активность аудитории была выше обычного."
    else:  # posts_status == "stable" and actions_status == "down"
        s4 = "При сопоставимом объёме контента активность аудитории снизилась."

    return " ".join([s1, s2, s3, s4])


# ── Генератор PPTX через pptxgenjs ────────────────────────────────────────

def _build_pptx(output_path: Path, all_data: dict, ym: str,
                period_label: str, history_range: str):
    """
    all_data = {
        "global":           dict (build_global_metrics),
        "channels":         list of dict (build_channel_metrics),
        "history":          dict channel -> list (get_all_channels_history),
        "paid":             dict channel -> list (get_paid_placements),
        "month_label":      str,
        "channels_config":  dict,
    }
    """
    import subprocess
    import tempfile

    NODE_PATH = r"C:\Users\admin\AppData\Roaming\npm\node_modules"
    
    script = _make_pptx_script(all_data, str(output_path), ym,
                                period_label, history_range)

    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False, encoding="utf-8") as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            ["node", script_path],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "NODE_PATH": NODE_PATH}  # <-- ДОБАВИТЬ
        )
        if result.returncode != 0:
            log.error(f"pptxgenjs error: {result.stderr}")
            raise RuntimeError(result.stderr)
        log.info(f"Dashboard PPTX сохранён: {output_path}")
    finally:
        os.unlink(script_path)


def _make_pptx_script(all_data: dict, output_path: str,
                       ym: str, period_label: str, history_range: str) -> str:
    """Генерирует Node.js скрипт для pptxgenjs."""
    import json

    g       = all_data["global"]
    chs     = all_data["channels"]
    history = all_data["history"]
    paid    = all_data["paid"]
    cfg     = all_data["channels_config"]
    mlabel  = all_data["month_label"]

    channels_order = [m["channel"] for m in chs]

    # Формируем данные для графиков истории
    month_labels = [h["month_label"] for h in (history.get(channels_order[0]) or [{}]*6)]

    def hist_series(key, nullable=False):
        """
        nullable=False (по умолчанию) — отсутствие данных превращает в 0
        (для метрик, где 0 — не бывает валидным реальным значением при
        наличии подписчиков, так что 0 однозначно читается как "нет
        данных" по факту работы графика).
        nullable=True — отсутствие данных остаётся None, pptxgenjs рисует
        разрыв линии вместо ложного проседания в 0. Используем для всех
        показателей, где реальный 0 возможен (VRpost, подписчики, охват,
        ER) — иначе месяц без данных выглядит как настоящий обвал
        показателя, а не как "данные ещё не собраны".
        """
        series = []
        for ch in channels_order:
            if nullable:
                vals = [h.get(key) for h in (history.get(ch) or [])]
            else:
                vals = [h.get(key) or 0 for h in (history.get(ch) or [])]
            series.append({
                "name":   _channel_name(ch, cfg),
                "color":  _channel_color(ch, cfg),
                "values": vals,
            })
        return series

    # Все 4 показателя динамики — nullable=True: отсутствие данных за месяц
    # (например месяц ещё не пересчитан) должно быть разрывом линии, а НЕ
    # ложным провалом в ноль (0 подписчиков/охвата — это неправда и вводит
    # в заблуждение сильнее, чем видимый разрыв в графике).
    subs_series   = hist_series("subscribers", nullable=True)
    reach_series  = hist_series("avg_reach",   nullable=True)
    vrpost_series = hist_series("vrpost",      nullable=True)
    err_series    = hist_series("err",         nullable=True)

    # Контентная активность (слайд 7)
    posts_counts  = [m["posts_count"]  for m in chs]
    reach_avgs    = [m["avg_reach"] or 0 for m in chs]
    ch_names      = [_channel_name(m["channel"], cfg) for m in chs]
    ch_colors     = [_channel_color(m["channel"], cfg) for m in chs]

    data_json = json.dumps({
        "output_path":   output_path,
        "ym":            ym,
        "period_label":  period_label,
        "history_range": history_range,
        "month_label":   mlabel,
        "title_month_name": all_data.get("title_month_name", ""),
        "title_year":       all_data.get("title_year", ""),
        "channels_count":   all_data.get("channels_count", len(chs)),
        "month_labels":  month_labels,
        "global":        g,
        "channels":      chs,
        "channels_order":channels_order,
        "ch_names":      ch_names,
        "ch_colors":     ch_colors,
        "posts_counts":  posts_counts,
        "reach_avgs":    reach_avgs,
        "subs_series":   subs_series,
        "reach_series":  reach_series,
        "vrpost_series": vrpost_series,
        "err_series":    err_series,
        "paid":          paid,
        "extra_paid":    all_data.get("extra_paid", {}),
        "cfg":           cfg,
    }, ensure_ascii=False)

    return f"""
const pptxgen = require('pptxgenjs');
const fs = require('fs');

const DATA = {data_json};

// ── Константы ─────────────────────────────────────────────────────────────
const NAVY   = "1F3864";
const GRAY   = "595959";
const LGRAY  = "F2F2F2";
const WHITE  = "FFFFFF";
const FOOTER_H = 0.28;
const SLIDE_W  = 13.3;
const SLIDE_H  = 7.5;
const FOOTER_Y = SLIDE_H - FOOTER_H - 0.05;

function footer(slide) {{
    slide.addText(DATA.period_label + "  ·  " + DATA.footer_text,
        {{ x:0.3, y:FOOTER_Y, w:SLIDE_W-0.6, h:FOOTER_H,
           fontSize:8, color:GRAY, align:"center" }});
    slide.addShape(pres.shapes.LINE,
        {{ x:0.3, y:FOOTER_Y-0.05, w:SLIDE_W-0.6, h:0,
           line:{{ color:"CCCCCC", width:0.5 }} }});
}}

// Единый формат даты для всей презентации: "YYYY-MM-DD" -> "дд.мм.гг"
function fmtDate(d) {{
    if (!d) return "—";
    const s = String(d);
    const parts = s.split("-");
    if (parts.length === 3 && parts[0].length === 4) {{
        return parts[2] + "." + parts[1] + "." + parts[0].slice(2);
    }}
    return s;
}}

function kicker(slide, text) {{
    slide.addText(text.toUpperCase(), {{
        x:0.5, y:0.18, w:10, h:0.28,
        fontSize:10, bold:true, color:"5B8DEF", charSpacing:2
    }});
}}

function title(slide, text, sub) {{
    slide.addText(text, {{
        x:0.5, y:0.5, w:SLIDE_W-1, h:0.7,
        fontSize:26, bold:true, color:NAVY
    }});
    if (sub) slide.addText(sub, {{
        x:0.5, y:1.2, w:SLIDE_W-1, h:0.35,
        fontSize:13, color:GRAY
    }});
}}

function statBox(slide, x, y, w, h, label, value, sub) {{
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE,
        {{ x, y, w, h, rectRadius:0.1, fill:{{ color:LGRAY }} }});
    slide.addText(String(value), {{
        x, y:y+0.08, w, h:h*0.55, align:"center",
        fontSize:22, bold:true, color:NAVY
    }});
    slide.addText(label, {{
        x, y:y+h*0.55, w, h:0.28, align:"center",
        fontSize:10, bold:true, color:GRAY
    }});
    if (sub) slide.addText(sub, {{
        x, y:y+h*0.55+0.25, w, h:0.22, align:"center",
        fontSize:8, color:GRAY
    }});
}}

function lineChart(slide, x, y, w, h, series, cats, title, showLegend, minY) {{
    const chartData = series.map(s => ({{
        name: s.name,
        labels: cats,
        values: s.values   // null внутри values — pptxgenjs рисует разрыв линии, не 0
    }}));
    const colors = series.map(s => s.color);
    const opts = {{
        x, y, w, h,
        chartColors: colors,
        lineDataSymbol: "circle",
        lineDataSymbolSize: 6,
        showTitle: !!title,
        title: title || "",
        titleFontSize: 12,
        titleColor: NAVY,
        showLegend: showLegend !== false,
        legendPos: "b",
        legendFontSize: 9,
        catAxisLabelColor: GRAY,
        valAxisLabelColor: GRAY,
        valGridLine: {{ color:"E2E8F0", size:0.5 }},
        catGridLine: {{ style:"none" }},
        dataLabelColor: GRAY,
        chartArea: {{ fill:{{ color:WHITE }} }},
    }};
    if (minY !== undefined && minY !== null) {{
        opts.valAxisMinVal = minY;
    }}
    slide.addChart(pres.charts.LINE, chartData, opts);
}}

function groupedBarChart(slide, x, y, w, h, labels, series, chartTitle) {{
    // series: [{{name, values, color}}, ...] — используется для сравнения
    // текущего месяца с предыдущим на одном графике (см. слайд
    // "Показатели месяца" каждого канала).
    const chartData = series.map(s => ({{ name: s.name, labels: labels, values: s.values }}));
    const colors = series.map(s => s.color);
    slide.addChart(pres.charts.BAR, chartData, {{
        x, y, w, h,
        barDir: "col",
        chartColors: colors,
        barGapWidthPct: 150,
        showTitle: !!chartTitle,
        title: chartTitle || "",
        titleFontSize: 11,
        titleColor: NAVY,
        showLegend: true,
        legendPos: "b",
        legendFontSize: 8,
        showValue: true,
        dataLabelFontSize: 7,
        dataLabelColor: GRAY,
        dataLabelFormatCode: "#,##0;;",
        catAxisLabelColor: GRAY,
        valAxisLabelColor: GRAY,
        valGridLine: {{ color:"E2E8F0", size:0.5 }},
        catGridLine: {{ style:"none" }},
        chartArea: {{ fill:{{ color:WHITE }} }},
    }});
}}

function barChart(slide, x, y, w, h, labels, values, colors, chartTitle) {{
    // ВАЖНО: одна серия с массивом категорий (labels) и массивом значений
    // (values), а НЕ отдельная серия на каждый канал. Прежняя реализация
    // создавала N серий, каждая со своим "name" — из-за этого pptxgenjs
    // путал подписи/легенду и везде показывал имя первого канала
    // ("Метакласс"), а по бокам от реального столбца рисовались лишние
    // нулевые столбцы других серий.
    const chartData = [{{
        name:   chartTitle || "Значение",
        labels: labels,
        values: values,
    }}];
    slide.addChart(pres.charts.BAR, chartData, {{
        x, y, w, h,
        barDir: "col",
        chartColors: colors,          // цвет каждой точки — по каналу
        barGapWidthPct: 220,          // widget: узкие столбцы, широкий зазор (правило 35-45% / 55-65%)
        showTitle: !!chartTitle,
        title: chartTitle || "",
        titleFontSize: 11,
        titleColor: NAVY,
        showLegend: false,
        showValue: true,
        dataLabelFontSize: 9,
        dataLabelColor: GRAY,
        dataLabelFormatCode: "#,##0;;",   // не показывать подпись для 0/пусто
        catAxisLabelColor: GRAY,
        valAxisLabelColor: GRAY,
        valGridLine: {{ color:"E2E8F0", size:0.5 }},
        catGridLine: {{ style:"none" }},
        chartArea: {{ fill:{{ color:WHITE }} }},
    }});
}}

function hBarChart(slide, x, y, w, h, labels, values, color, chartTitle) {{
    const chartData = [{{ name: "Охват", labels, values }}];
    slide.addChart(pres.charts.BAR, chartData, {{
        x, y, w, h,
        barDir: "bar",
        chartColors: [color],
        barGapWidthPct: 220,
        showTitle: !!chartTitle,
        title: chartTitle || "",
        titleFontSize: 11,
        titleColor: NAVY,
        showLegend: false,
        showValue: true,
        dataLabelFontSize: 9,
        dataLabelColor: GRAY,
        dataLabelPosition: "outEnd",
        dataLabelFormatCode: "#,##0;;",
        catAxisLabelColor: GRAY,
        valAxisLabelColor: GRAY,
        valGridLine: {{ style:"none" }},
        catGridLine: {{ style:"none" }},
        chartArea: {{ fill:{{ color:WHITE }} }},
    }});
}}

// Универсальный слайд платных размещений — используется и для блока
// каждого канала (paid_ch != null), и для "дополнительных" разделов
// таблицы посевов, чьё название не совпало ни с одним каналом (см. цикл
// по DATA.extra_paid ниже) — там вместо названия канала просто
// подставляется название раздела как есть.
function paidKpiCard(slide, x, y, w, h, label, value, color) {{
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {{
        x, y, w, h, rectRadius:0.06,
        fill:{{ color:"FFFFFF" }}, line:{{ color:"E6E6EA", width:1 }},
    }});
    // акцентная полоса слева, в цвете канала
    slide.addShape(pres.shapes.RECTANGLE, {{ x, y:y+0.07, w:0.06, h:h-0.14, fill:{{ color }} }});
    slide.addText(label, {{
        x:x+0.2, y:y+0.13, w:w-0.32, h:0.24,
        fontSize:8, bold:true, color:GRAY, align:"left",
    }});
    slide.addText(String(value), {{
        x:x+0.2, y:y+0.4, w:w-0.32, h:h-0.5,
        fontSize:17, bold:true, color:"#"+color, align:"left", valign:"top",
    }});
}}

// Слайд-обложка блока: название по центру страницы, крупным шрифтом,
// цветом, присвоенным каналу (или нейтральным для доп. блоков без
// канала); ниже — период отчёта, мельче. Используется и перед блоком
// каждого канала, и перед "доп. блоками" платных размещений (если они
// есть — если нет, обложка для них просто не вызывается).
function renderCoverSlide(title, color, subtitle) {{
    const s = pres.addSlide();
    s.addText(title, {{
        x:0.6, y:SLIDE_H/2 - 0.7, w:SLIDE_W-1.2, h:1.1,
        fontSize:40, bold:true, color:"#"+color, align:"center", valign:"middle",
    }});
    s.addText(subtitle, {{
        x:0.6, y:SLIDE_H/2 + 0.45, w:SLIDE_W-1.2, h:0.5,
        fontSize:16, color:GRAY, align:"center",
    }});
    s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
    slideNum++;
}}

function renderPaidSlide(title, color, paid_ch) {{
    const s = pres.addSlide();

    // ── Шапка — БЕЗ ИЗМЕНЕНИЙ ───────────────────────────────────────────
    s.addShape(pres.shapes.RECTANGLE, {{ x:0, y:0, w:0.18, h:SLIDE_H, fill:{{ color }} }});
    kicker(s, title + " · платные размещения");
    s.addText(title + ": платные посевы", {{
        x:0.5, y:0.45, w:SLIDE_W-0.7, h:0.65, fontSize:22, bold:true, color:NAVY
    }});
    s.addText(DATA.period_label, {{ x:0.5, y:1.1, w:SLIDE_W-0.7, h:0.3, fontSize:12, color:GRAY }});

    // ── Агрегаты (расчёты не менялись) ──────────────────────────────────
    const total_budget = paid_ch.reduce((a,p) => a+(p.budget||0), 0);
    const total_reach  = paid_ch.reduce((a,p) => a+(p.reach||0), 0);
    const total_inflow = paid_ch.reduce((a,p) => a+(p.inflow||0), 0);
    const avg_cpv       = total_reach  ? Math.round(total_budget/total_reach*100)/100 : null;
    const avg_cpf       = total_inflow ? Math.round(total_budget/total_inflow*100)/100 : null;

    const bodyX = 0.5, bodyW = SLIDE_W - 1.0;

    // ── KPI-карточки: один ряд, шесть штук, одинаковый размер ──────────
    const kpis = [
        {{ label:"РАЗМЕЩЕНИЙ",  value: paid_ch.length }},
        {{ label:"БЮДЖЕТ",       value: total_budget ? total_budget.toLocaleString("ru")+" ₽" : "—" }},
        {{ label:"ОХВАТ",        value: total_reach   ? total_reach.toLocaleString("ru")       : "—" }},
        {{ label:"СРЕДНИЙ CPV",  value: avg_cpv ? avg_cpv+" ₽" : "—" }},
        {{ label:"ПРИТОК",       value: total_inflow  ? total_inflow.toLocaleString("ru")      : "—" }},
        {{ label:"СРЕДНИЙ CPF",  value: avg_cpf ? avg_cpf+" ₽" : "—" }},
    ];
    const kpiY = 1.6, kpiH = 1.0, kpiGap = 0.15;
    const kpiW = (bodyW - kpiGap*(kpis.length-1)) / kpis.length;
    kpis.forEach((k, i) => {{
        paidKpiCard(s, bodyX + i*(kpiW+kpiGap), kpiY, kpiW, kpiH, k.label, k.value, color);
    }});

    // ── Графики: одна строка, одинаковый размер, выровнены между собой ──
    const chartsY = kpiY + kpiH + 0.4;
    const chartsH = 2.3;
    const chartGap = 0.3;
    const chartW = (bodyW - chartGap) / 2;
    const hasCharts = paid_ch.length > 1;

    if (hasCharts) {{
        const plat_names = paid_ch.map(p => p.platform);
        const reach_vals = paid_ch.map(p => p.reach || 0);
        const cpf_vals   = paid_ch.map(p => p.cpf   || 0);
        hBarChart(s, bodyX, chartsY, chartW, chartsH, plat_names, reach_vals, color, "Охват по размещениям");
        hBarChart(s, bodyX+chartW+chartGap, chartsY, chartW, chartsH, plat_names, cpf_vals, color, "CPF по размещениям, ₽");
    }}

    // ── Таблица: под графиками, во всю ширину, шапка — в цвете канала ──
    const tableY = hasCharts ? (chartsY + chartsH + 0.3) : chartsY;
    const cols       = ["Площадка","Дата","Стоимость","Охват","Приток","CPV","CPF"];
    const colRatios  = [3.5, 1.3, 1.5, 1.2, 1.2, 1.2, 1.2];
    const ratioSum   = colRatios.reduce((a,b) => a+b, 0);
    const colW2      = colRatios.map(r => r/ratioSum*bodyW);   // растянуто на всю ширину

    let tx = bodyX;
    cols.forEach((c, i) => {{
        s.addShape(pres.shapes.RECTANGLE, {{ x:tx, y:tableY, w:colW2[i], h:0.32, fill:{{ color }} }});
        s.addText(c, {{ x:tx, y:tableY, w:colW2[i], h:0.32, align:"center", valign:"middle", fontSize:9, bold:true, color:WHITE }});
        tx += colW2[i];
    }});
    paid_ch.forEach((p, ri) => {{
        let tx2 = bodyX;
        const row_y = tableY + 0.32 + ri*0.27;
        const bg2   = ri%2 === 0 ? "FFFFFF" : "F6F7FA";
        const vals2 = [
            p.platform,
            fmtDate(p.date),
            p.budget ? p.budget.toLocaleString("ru")+" ₽" : "—",
            p.reach  ? p.reach.toLocaleString("ru") : "—",
            p.inflow ? p.inflow.toLocaleString("ru") : "—",
            p.cpv    ? p.cpv+" ₽"  : "—",
            p.cpf    ? p.cpf+" ₽"  : "—",
        ];
        vals2.forEach((v, i) => {{
            s.addShape(pres.shapes.RECTANGLE, {{ x:tx2, y:row_y, w:colW2[i], h:0.27, fill:{{ color:bg2 }} }});
            s.addText(String(v), {{ x:tx2, y:row_y, w:colW2[i], h:0.27, align: i===0?"left":"center", valign:"middle", fontSize:8.5, color:"444444", margin:3 }});
            tx2 += colW2[i];
        }});
    }});

    footer(s);
    s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
    slideNum++;
}}

// ── Начало ────────────────────────────────────────────────────────────────
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
DATA.footer_text = "Данные Telegram фиксируются через ~24 часа после публикации; платные размещения вводятся отдельно.";

// ════════ СЛАЙД 1: ТИТУЛЬНЫЙ (светлая аналитическая композиция) ══════════
{{
    const s = pres.addSlide();
    s.background = {{ color: "FCFCFD" }};

    // ── Левая часть: заголовок + параметры отчёта ─────────────────────────
    s.addText("Telegram-каналы", {{
        x:0.6, y:0.7, w:6.6, h:0.75,
        fontSize:32, bold:true, color:NAVY, align:"left"
    }});
    s.addText("Dashboard-презентация: визуальная структура для автоматизации отчёта", {{
        x:0.6, y:1.45, w:6.6, h:0.5,
        fontSize:12, color:GRAY, align:"left"
    }});

    const infoLines = [
        "Отчётный месяц: " + DATA.title_month_name + " " + DATA.title_year,
        "Динамика: " + DATA.history_range,
        "Каналов в отчёте: " + DATA.channels_count,
    ];
    infoLines.forEach((line, i) => {{
        s.addText(line, {{
            x:0.6, y:2.25 + i*0.42, w:6.6, h:0.38,
            fontSize:14, color:"222222", align:"left"
        }});
    }});

    // ── Правая часть: вертикальный список каналов ─────────────────────────
    const chList = DATA.channels.map(m => {{
        const name  = DATA.cfg[m.channel] ? DATA.cfg[m.channel].name : m.channel;
        const color = DATA.cfg[m.channel] ? DATA.cfg[m.channel].color.replace("#","") : "333333";
        return {{ name, color, subscribers: m.subscribers }};
    }});
    const rx = 7.6, rw = 5.2, rowH = 0.62, rowGap = 0.14;
    chList.forEach((ch, i) => {{
        const ry = 0.7 + i * (rowH + rowGap);
        // карточка
        s.addShape(pres.shapes.RECTANGLE, {{
            x:rx, y:ry, w:rw, h:rowH,
            fill:{{ color:"FFFFFF" }},
            line:{{ color:"E4E4E7", width:1 }}
        }});
        // цветная полоса слева
        s.addShape(pres.shapes.RECTANGLE, {{
            x:rx, y:ry, w:0.08, h:rowH, fill:{{ color:ch.color }}
        }});
        s.addText(ch.name, {{
            x:rx+0.25, y:ry, w:rw*0.55, h:rowH, valign:"middle",
            fontSize:12, bold:true, color:NAVY
        }});
        const subsLabel = ch.subscribers
            ? (ch.subscribers >= 1000 ? (ch.subscribers/1000).toFixed(1)+"K подписчиков" : String(ch.subscribers)+" подписчиков")
            : "— подписчиков";
        s.addText(subsLabel, {{
            x:rx+rw*0.5, y:ry, w:rw*0.48, h:rowH, valign:"middle", align:"right",
            fontSize:11, color:GRAY
        }});
    }});
    s.addText("Цвет канала сохраняется на всех общих графиках.", {{
        x:rx, y:0.7 + chList.length*(rowH+rowGap) + 0.1, w:rw, h:0.3,
        fontSize:9, color:GRAY, italic:true
    }});

    // ── Нижняя часть ───────────────────────────────────────────────────────
    s.addText(DATA.footer_text, {{
        x:0.6, y:SLIDE_H-0.5, w:8.0, h:0.3,
        fontSize:8, color:GRAY, align:"left"
    }});
    s.addText("01", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.5, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
}}

// ════════ СЛАЙДЫ 2-5: ОБЩАЯ ДИНАМИКА ═════════════════════════════════════
// Подписчики → Средний охват → VRpost → ER
// (слайд "Прирост подписчиков" убран и заменён на "Динамику VRpost";
//  общий Exec Summary тоже убран — он дублировал данные из блоков каналов)
{{
    // Слайд 2: Подписчики
    const s2 = pres.addSlide();
    kicker(s2, "Динамика · 6 месяцев");
    title(s2, "Динамика подписчиков по всем каналам", DATA.history_range);
    lineChart(s2, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.subs_series, DATA.month_labels, null, true);
    footer(s2);
    s2.addText("02", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});

    // Слайд 3: Средний охват
    const s3 = pres.addSlide();
    kicker(s3, "Динамика · 6 месяцев");
    title(s3, "Динамика среднего охвата публикации", DATA.history_range);
    lineChart(s3, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.reach_series, DATA.month_labels, null, true);
    footer(s3);
    s3.addText("03", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});

    // Слайд 4: VRpost (заменяет "Динамику прироста подписчиков")
    const s4 = pres.addSlide();
    kicker(s4, "Динамика · 6 месяцев");
    title(s4, "Динамика VRpost (средний коэффициент видимости)", DATA.history_range);
    lineChart(s4, 0.5, 1.7, SLIDE_W-1, 4.6, DATA.vrpost_series, DATA.month_labels, null, true, 0);
    s4.addText("VRpost показывает, какую долю аудитории в среднем охватывают публикации канала. Чем выше показатель, тем лучше видимость контента среди подписчиков.", {{
        x:0.5, y:6.35, w:SLIDE_W-1, h:0.4, fontSize:9, color:GRAY, italic:true
    }});
    footer(s4);
    s4.addText("04", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});

    // Слайд 5: ER
    const s5 = pres.addSlide();
    kicker(s5, "Динамика · 6 месяцев");
    title(s5, "Динамика ER по каналам", DATA.history_range);
    lineChart(s5, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.err_series, DATA.month_labels, null, true);
    s5.addText("ER рассчитывается из ERR (%) — действия / охват × 100%.", {{
        x:0.5, y:6.4, w:SLIDE_W-1, h:0.3, fontSize:9, color:GRAY, italic:true
    }});
    footer(s5);
    s5.addText("05", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
}}

// ════════ СЛАЙД 6: КОНТЕНТНАЯ АКТИВНОСТЬ ═════════════════════════════════
{{
    const s = pres.addSlide();
    kicker(s, "Отчётный месяц");
    title(s, "Контентная активность каналов", DATA.period_label);
    barChart(s, 0.5, 1.8, 6.0, 4.5, DATA.ch_names, DATA.posts_counts, DATA.ch_colors, "Количество публикаций");
    barChart(s, 6.9, 1.8, 6.0, 4.5, DATA.ch_names, DATA.reach_avgs,   DATA.ch_colors, "Средний охват публикации");
    footer(s);
    s.addText("06", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
}}

// ════════ БЛОКИ ПО КАНАЛАМ ════════════════════════════════════════════════
let slideNum = 7;

DATA.channels.forEach(m => {{
    const ch      = m.channel;
    const cfg_ch  = DATA.cfg[ch] || {{}};
    const color   = (cfg_ch.color || "#333333").replace("#","");
    const name    = cfg_ch.name || ch;
    const hist    = DATA.history_data ? DATA.history_data[ch] : null;
    const bw_data = m.best_worst || {{}};
    const paid_ch = DATA.paid[ch] || [];

    // Обложка блока канала — название по центру, цветом канала, ниже
    // период отчёта.
    renderCoverSlide(name, color, DATA.month_label);

    // Данные истории для этого канала из subs/reach серий
    const ch_idx  = DATA.channels_order.indexOf(ch);
    const ch_subs_hist  = ch_idx >= 0 ? DATA.subs_series[ch_idx]   : null;
    const ch_reach_hist = ch_idx >= 0 ? DATA.reach_series[ch_idx]  : null;
    const ch_hist_series_subs  = ch_subs_hist  ? [{{ name, color, values: ch_subs_hist.values  }}] : [];
    const ch_hist_series_reach = ch_reach_hist ? [{{ name, color, values: ch_reach_hist.values }}] : [];

    // ── СЛАЙД A: Executive Summary канала ─────────────────────────────────
    {{
        const s = pres.addSlide();
        s.addShape(pres.shapes.RECTANGLE, {{ x:0, y:0, w:0.18, h:SLIDE_H, fill:{{ color }} }});
        kicker(s, name + " · обзор");
        s.addText(name + ": Executive Summary", {{
            x:0.5, y:0.45, w:SLIDE_W-0.7, h:0.65, fontSize:24, bold:true, color:NAVY
        }});
        s.addText(DATA.period_label + " · краткий обзор канала", {{
            x:0.5, y:1.1, w:SLIDE_W-0.7, h:0.3, fontSize:12, color:GRAY
        }});

        const vals = [
            {{ l:"Подписчики",  v: m.subscribers >= 1000 ? (m.subscribers/1000).toFixed(1)+"K" : String(m.subscribers||"—"), s:"на конец месяца" }},
            {{ l:"Прирост",     v: m.growth !== null && m.growth !== undefined ? (m.growth > 0 ? "+"+m.growth : String(m.growth)) : "—", s:"за месяц" }},
            {{ l:"Постов",      v: String(m.posts_count||"—"), s:"выбранный месяц" }},
            {{ l:"Сторис",      v: String(m.stories_count||0), s:"выбранный месяц" }},
            {{ l:"Ср. охват",   v: m.avg_reach ? Math.round(m.avg_reach) : "—", s:"на 1 пост" }},
            {{ l:"ER",          v: m.err ? m.err.toFixed(1)+"%" : "—", s:"из ERR (%)" }},
            {{ l:"VRpost",      v: (m.vrpost !== null && m.vrpost !== undefined) ? m.vrpost.toFixed(1)+"%" : "—", s:"коэфф. видимости" }},
        ];
        const bw2 = 1.7, bh2 = 1.3, bx0 = 0.5, by2 = 1.55, gap2 = 0.15;
        vals.forEach((b,i) => statBox(s, bx0+i*(bw2+gap2), by2, bw2, bh2, b.l, b.v, b.s));

        s.addText("Реакции: "+(m.total_react||"—")+"   Комменты: "+(m.total_comments||"—")+"   Репосты: "+(m.total_fwd||"—"), {{
            x:0.5, y:3.05, w:SLIDE_W-0.7, h:0.3, fontSize:11, color:GRAY
        }});

        // Два мини-графика
        if (ch_hist_series_subs.length)
            lineChart(s, 0.5, 3.5, 6.0, 3.0, ch_hist_series_subs, DATA.month_labels, "Динамика подписчиков за 6 месяцев", false);
        if (ch_hist_series_reach.length)
            lineChart(s, 6.9, 3.5, 6.0, 3.0, ch_hist_series_reach, DATA.month_labels, "Динамика среднего охвата за 6 месяцев", false);

        footer(s);
        s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
        slideNum++;
    }}

    // ── СЛАЙД B: Показатели месяца ────────────────────────────────────────
    {{
        const s = pres.addSlide();
        s.addShape(pres.shapes.RECTANGLE, {{ x:0, y:0, w:0.18, h:SLIDE_H, fill:{{ color }} }});
        kicker(s, name + " · показатели");
        s.addText(name + ": показатели выбранного месяца", {{
            x:0.5, y:0.45, w:SLIDE_W-0.7, h:0.65, fontSize:22, bold:true, color:NAVY
        }});
        s.addText(DATA.period_label, {{ x:0.5, y:1.1, w:SLIDE_W-0.7, h:0.3, fontSize:12, color:GRAY }});

        // Активность (сгруппированный bar chart: текущий месяц vs предыдущий)
        const act_labels = ["Посты","Сторис","Реакции","Репосты","Комменты","Действия"];
        const act_values = [
            m.posts_count||0, m.stories_count||0, m.total_react||0,
            m.total_fwd||0, m.total_comments||0, m.total_actions||0
        ];
        const prevA = m.prev_activity || {{}};
        const prev_values = [
            prevA.posts_count||0, prevA.stories_count||0, prevA.total_react||0,
            prevA.total_fwd||0, prevA.total_comments||0, prevA.total_actions||0
        ];
        const avg5A = m.avg5_activity || {{}};
        const avg5_values = [
            avg5A.posts_count||0, avg5A.stories_count||0, avg5A.total_react||0,
            avg5A.total_fwd||0, avg5A.total_comments||0, avg5A.total_actions||0
        ];
        groupedBarChart(s, 0.5, 1.6, 6.0, 4.0, act_labels, [
            {{ name: DATA.month_label,                  values: act_values,  color: color }},
            {{ name: m.prev_month_label || "Пред. месяц", values: prev_values, color: "D9D9D9" }},
            {{ name: "Средн. 5 мес.",                    values: avg5_values, color: "8EA9DB" }},
        ], "Активность и реакции аудитории");

        // Короткий текстовый вывод под графиком — автоматически
        // формируется в Python (см. build_activity_summary_text), здесь
        // только отображается.
        if (m.activity_summary) {{
            s.addText(m.activity_summary, {{
                x:0.5, y:5.68, w:6.0, h:1.45,
                fontSize:8.5, color:"333333", align:"left", valign:"top",
                lineSpacingMultiple: 1.15,
            }});
        }}

        // Метрики справа
        const metrics2 = [
            {{ l:"ER (ERR %)",     v: m.err         ? m.err.toFixed(1)+"%"    : "—" }},
            {{ l:"VRpost",         v: (m.vrpost !== null && m.vrpost !== undefined) ? m.vrpost.toFixed(1)+"%" : "—" }},
            {{ l:"Viral Factor",   v: m.viral_factor? m.viral_factor.toFixed(1)+"%" : "—" }},
            {{ l:"Reply Rate",     v: m.reply_rate  ? m.reply_rate.toFixed(1)+"%"  : "—" }},
            {{ l:"Reach Mult.",    v: m.reach_mult  ? m.reach_mult.toFixed(2)+"x"  : "—" }},
        ];
        const mx = 7.0, my0 = 1.6, mw = 2.8, mh = 0.8, mgap = 0.05;
        metrics2.forEach((mt, i) => {{
            const row = Math.floor(i/2), col = i%2;
            const mx2 = mx + col*(mw+mgap+0.1);
            const my2 = my0 + row*(mh+mgap);
            s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{ x:mx2, y:my2, w:mw, h:mh, rectRadius:0.08, fill:{{ color:"F4F6FB" }} }});
            s.addText(mt.v, {{ x:mx2, y:my2+0.05, w:mw, h:mh*0.55, align:"center", fontSize:18, bold:true, color:"#"+color }});
            s.addText(mt.l, {{ x:mx2, y:my2+mh*0.55, w:mw, h:0.25, align:"center", fontSize:9, color:GRAY }});
        }});
        // Расшифровка всех показателей (не только VRpost) — под сеткой метрик
        const metricsRows = Math.ceil(metrics2.length/2);
        const legendItems = [
            ["ER (ERR %) ≥ 2%",     "доля аудитории, которая взаимодействует с контентом"],
            ["VRpost ≥ 20%",        "доля аудитории, которая увидела публикацию"],
            ["Viral Factor ≥ 1.2",  "показатель распространения контента за пределы основной аудитории"],
            ["Reply Rate ≥ 10%",    "доля аудитории, которая отвечает или вступает в диалог"],
            ["Reach Mult. ≥ 1.3x",  "во сколько раз фактический охват отличается от базовой аудитории"],
        ];
        const legendY0 = my0 + metricsRows*(mh+mgap) + 0.08;
        legendItems.forEach((item, i) => {{
            const ly = legendY0 + i*0.24;
            s.addText([
                {{ text: item[0] + " — ", options: {{ bold:true, color:GRAY, fontSize:7.5 }} }},
                {{ text: item[1],         options: {{ color:GRAY, fontSize:7.5 }} }},
            ], {{
                x:mx, y:ly, w:mw*2+mgap+0.1, h:0.22, align:"left"
            }});
        }});

        footer(s);
        s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
        slideNum++;
    }}

    // ── СЛАЙД C: Лучшие и худшие посты ───────────────────────────────────
    {{
        const s = pres.addSlide();
        s.addShape(pres.shapes.RECTANGLE, {{ x:0, y:0, w:0.18, h:SLIDE_H, fill:{{ color }} }});
        kicker(s, name + " · публикации");
        s.addText(name + ": лучшие и худшие публикации", {{
            x:0.5, y:0.45, w:SLIDE_W-0.7, h:0.65, fontSize:22, bold:true, color:NAVY
        }});
        s.addText(DATA.period_label + " · оценка через 24 часа после выхода", {{
            x:0.5, y:1.1, w:SLIDE_W-0.7, h:0.3, fontSize:12, color:GRAY
        }});

        // Топ-3 карточки (лучший по охвату, реакциям, ER)
        const bests = [
            {{ title:"Лучший по охвату",    data: bw_data.best_reach }},
            {{ title:"Лучший по реакциям",  data: bw_data.best_react }},
            {{ title:"Лучший по ER",        data: bw_data.best_er }},
            {{ title:"Лучший по репостам",  data: bw_data.best_forwards }},
        ];
        // Порядок в карточке строго: Дата·Тип → Охват·ER·Реакции → Ссылка/Открыть → текст поста (post_preview)
        const cw = 3.0;
        bests.forEach((b, i) => {{
            const x = 0.5 + i*(cw+0.08);
            s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{ x, y:1.55, w:cw, h:1.95, rectRadius:0.08, fill:{{ color:"F4F6FB" }} }});
            s.addText(b.title, {{ x, y:1.6, w:cw, h:0.28, align:"center", fontSize:10, bold:true, color:NAVY }});
            if (b.data) {{
                s.addText(fmtDate(b.data.date) + " · " + b.data.content_type,
                    {{ x, y:1.9, w:cw, h:0.24, align:"center", fontSize:9, color:GRAY }});
                s.addText((b.data.views||"—")+" просм. · ER "+(b.data.err ? b.data.err.toFixed(1)+"%" : "—")+" · "+(b.data.reactions||"—")+" реакц.",
                    {{ x, y:2.14, w:cw, h:0.24, align:"center", fontSize:9, color:GRAY }});
                if (b.data.url) s.addText("Открыть", {{ x, y:2.38, w:cw, h:0.2, align:"center", fontSize:8, color:"5B8DEF", hyperlink:{{ url: b.data.url||"#" }} }});
                s.addText(b.data.post_preview || b.data.text_short || "Без текста",
                    {{ x:x+0.15, y:2.62, w:cw-0.3, h:0.8, align:"center", fontSize:8, color:GRAY, italic:true }});
            }} else {{
                s.addText("—", {{ x, y:2.0, w:cw, h:0.5, align:"center", fontSize:14, color:GRAY }});
            }}
        }});

        // TOP-5 по охвату (горизонтальный бар) — по правилу больше НЕ показываем
        // тип публикации в подписи, только дату в едином формате дд.мм.гг.
        // reverse() — PowerPoint/pptxgenjs рисует горизонтальные столбцы
        // снизу вверх (первый элемент массива внизу), поэтому чтобы 1-е
        // место оказалось СВЕРХУ, а 5-е — снизу, передаём данные в
        // обратном порядке.
        if (bw_data.top5_reach && bw_data.top5_reach.length > 0) {{
            const top5_pairs = bw_data.top5_reach.map((p,i) => ({{
                label: (i+1)+". "+fmtDate(p.date),
                value: p.views||0,
            }})).reverse();
            const top5labels = top5_pairs.map(x => x.label);
            const top5vals   = top5_pairs.map(x => x.value);
            hBarChart(s, 0.5, 3.65, 6.3, 3.05, top5labels, top5vals, color, "TOP-5 по охвату");
        }}

        // Худшие посты (3 карточки справа)
        if (bw_data.worst3 && bw_data.worst3.length > 0) {{
            s.addText("Ниже среднего / точки внимания", {{ x:7.1, y:3.65, w:5.8, h:0.3, fontSize:11, bold:true, color:NAVY }});
            bw_data.worst3.forEach((p, i) => {{
                if (!p) return;
                const wy = 4.0 + i*1.05;
                s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{ x:7.1, y:wy, w:5.8, h:0.95, rectRadius:0.08, fill:{{ color:"FFF5F5" }} }});
                s.addText(fmtDate(p.date) + " · " + p.content_type, {{ x:7.2, y:wy+0.05, w:5.6, h:0.24, fontSize:10, bold:true, color:NAVY }});
                s.addText((p.views||"—")+" просм. · ER "+(p.err ? p.err.toFixed(1)+"%" : "—")+
                    (p.deviation ? "  (" + (p.deviation > 0 ? "+" : "")+p.deviation+"% к среднему)" : ""),
                    {{ x:7.2, y:wy+0.29, w:4.6, h:0.22, fontSize:9, color:GRAY }});
                if (p.url) s.addText("Открыть", {{ x:11.3, y:wy+0.29, w:1.5, h:0.22, fontSize:8, color:"5B8DEF", align:"right", hyperlink:{{ url: p.url }} }});
                s.addText(p.post_preview || p.text_short || "Без текста",
                    {{ x:7.2, y:wy+0.53, w:5.6, h:0.38, fontSize:8, color:GRAY, italic:true }});
            }});
        }}

        footer(s);
        s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
        slideNum++;
    }}

    // ── СЛАЙД D: Платные посевы (только если есть данные) ─────────────────
    if (paid_ch && paid_ch.length > 0) {{
        renderPaidSlide(name, color, paid_ch);
    }}
}});

// ════════ ДОПОЛНИТЕЛЬНЫЕ БЛОКИ ПЛАТНЫХ РАЗМЕЩЕНИЙ ════════════════════════
// Разделы таблицы посевов, чьё название не совпало ни с одним из ваших
// каналов (например "HRTech") — не привязываются к случайному каналу и
// не теряются молча, а получают СОБСТВЕННЫЙ слайд в самом конце
// презентации, по одному на каждое такое название.
const EXTRA_PAID_COLOR = "5B8DEF";
Object.keys(DATA.extra_paid || {{}}).forEach(name => {{
    const paid_ch = DATA.extra_paid[name] || [];
    if (paid_ch.length > 0) {{
        renderCoverSlide(name, EXTRA_PAID_COLOR, DATA.month_label);
        renderPaidSlide(name, EXTRA_PAID_COLOR, paid_ch);
    }}
}});

// ── Сохранение ────────────────────────────────────────────────────────────
pres.writeFile({{ fileName: DATA.output_path }})
    .then(() => console.log("OK: " + DATA.output_path))
    .catch(e => {{ console.error("ERROR: " + e.message); process.exit(1); }});
"""


# ── Основная функция ──────────────────────────────────────────────────────

async def build_dashboard(client, ym: str, date_from: date, date_to: date,
                           override_recipients: list = None):
    """
    Генерирует Dashboard-презентацию за месяц и отправляет в Telegram.
    """
    from config import (CHANNELS, DASHBOARD_CHANNELS, OUTPUT_DIR,
                        RECIPIENT_IDS)
    from history_db import ensure_seeded, get_all_channels_history
    from dashboard_metrics import (build_channel_metrics, build_global_metrics)
    from paid_placements import get_paid_placements, get_extra_paid_groups

    ensure_seeded()

    import historical as hist_mod
    import stories as stories_mod
    from registry_manager import get_final_posts_for_period

    MONTHS_RU = {
        1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
        7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"
    }

    year_n = int(ym[:4]); month_n = int(ym[5:7])
    month_label   = f"{MONTHS_RU.get(month_n,'')} {year_n}"
    period_label  = f"Отчётный месяц: {month_label}"
    title_month_name = MONTHS_RU.get(month_n, "").lower()  # "июль" для титульного слайда

    # История за 6 месяцев
    history = get_all_channels_history(CHANNELS, months=6, end_ym=ym)

    # Метки диапазона истории (корректно обрабатываем переход через год,
    # например "Авг 2026 – Янв 2027")
    all_months = list(list(history.values())[0]) if history else []
    if all_months:
        first_entry, last_entry = all_months[0], all_months[-1]
        first_label = first_entry["month_label"]
        last_label  = last_entry["month_label"]
        first_year  = int(first_entry["ym"][:4])
        last_year   = int(last_entry["ym"][:4])
        if first_year == last_year:
            history_range = f"{first_label}–{last_label} {last_year}"
        else:
            history_range = f"{first_label} {first_year} – {last_label} {last_year}"
    else:
        history_range = ym

    # Данные по каналам
    channel_metrics  = []
    paid_data        = {}

    # Для сравнения на графике "Активность и реакции аудитории" —
    # предыдущий календарный месяц (для отчёта за август — июль и т.д.)
    _prev_year, _prev_month = (year_n, month_n - 1) if month_n > 1 else (year_n - 1, 12)
    from calendar import monthrange as _monthrange
    prev_d_from = date(_prev_year, _prev_month, 1)
    prev_d_to   = date(_prev_year, _prev_month, _monthrange(_prev_year, _prev_month)[1])

    for ch in CHANNELS:
        # Посты — сначала 24ч срезы, остальное историческое
        final_posts = get_final_posts_for_period(ch, date_from, date_to)
        hist_posts, subs = await hist_mod.get_posts_for_period(
            client, ch, date_from, date_to, force=False)

        # Накладываем 24ч срезы
        posts_by_id = {str(p["msg_id"]): p for p in hist_posts}
        for mid, fp in final_posts.items():
            if mid in posts_by_id:
                posts_by_id[mid]["snapshot"] = fp["snapshot"]
            else:
                posts_by_id[mid] = fp

        # Опросы — голосование может продолжаться дольше 24ч/дольше
        # кэша, поэтому досчитываем актуальные голоса свежим запросом
        # (та же логика, что в report.py get_channel_posts).
        poll_ids = [mid for mid, p in posts_by_id.items() if p.get("content_type") == "Опрос"]
        if poll_ids:
            try:
                entity = await client.get_entity(ch)
                for mid in poll_ids:
                    try:
                        msg = await client.get_messages(entity, ids=int(mid))
                        if msg and msg.media and getattr(msg.media, "results", None) and msg.media.results.results:
                            fresh_votes = sum(r.voters or 0 for r in msg.media.results.results if r.voters)
                            sn = posts_by_id[mid].setdefault("snapshot", {})
                            sn["votes"] = fresh_votes
                            sn["actions"] = (sn.get("reactions", 0) + sn.get("comments", 0)
                                              + sn.get("forwards", 0) + fresh_votes)
                    except Exception as e:
                        log.warning(f"  [{ch}] не удалось досчитать голоса опроса {mid}: {e}")
            except Exception as e:
                log.warning(f"  [{ch}] не удалось получить сущность канала для пересчёта опросов: {e}")

        posts = sorted(posts_by_id.values(),
                       key=lambda x: (x.get("date",""), x.get("time","")))

        # Сторис
        ch_stories = stories_mod.get_stories_for_period(ch, date_from, date_to)

        # text_short уже заполняется в get_channel_posts / report.py
        # Для постов из historical напрямую — добавляем здесь как fallback
        import re as _re
        for p in posts:
            if "text_short" not in p:
                text = p.get("message", "") or p.get("text", "") or ""
                text_clean = _re.sub(r'https?://\S+', '', text).replace('\n', ' ').strip()
                words = text_clean.split()
                p["text_short"] = " ".join(words[:9]) + ("..." if len(words) > 9 else "")

        if not subs:
            from registry_manager import load_registry
            reg = load_registry(ch)
            subs = reg.get("subscribers", 0)

        ch_hist = history.get(ch, [])
        metrics = build_channel_metrics(ch, posts, ch_stories, subs, ch_hist)

        # Приоритет — данные из history_db.json (их пишет report.py при
        # генерации месячного Excel-отчёта), а не собственный пересчёт
        # дашборда. ch_hist[-1] — это как раз запись за ТЕКУЩИЙ ym (т.к.
        # get_all_channels_history(..., end_ym=ym) заканчивается на нём).
        # Если запись есть — берём её как более авторитетную; если нет
        # (по этому каналу ещё ни разу не собирался месячный отчёт) —
        # остаётся то, что дашборд посчитал сам только что (и НЕ
        # записывается обратно в базу — писать в history_db.json теперь
        # может только report.py, дашборд — только читает).
        if ch_hist and ch_hist[-1].get("ym") == ym:
            db_month = ch_hist[-1]
            if db_month.get("subscribers"):
                metrics["subscribers"] = db_month["subscribers"]
            if db_month.get("avg_reach") is not None:
                metrics["avg_reach"] = db_month["avg_reach"]
            if db_month.get("err") is not None:
                metrics["err"] = db_month["err"]
            if db_month.get("vrpost") is not None:
                metrics["vrpost"] = db_month["vrpost"]

        channel_metrics.append(metrics)

        # Активность за ПРЕДЫДУЩИЙ месяц — для сравнения на графике
        # "Активность и реакции аудитории" (светло-серые столбцы рядом с
        # текущими). Данные не берём из history_db.json (там таких
        # разрезов нет, только агрегаты avg_reach/err/vrpost) — считаем
        # напрямую из кэша, точно так же, как для текущего месяца.
        prev_final = get_final_posts_for_period(ch, prev_d_from, prev_d_to)
        prev_hist_posts, _prev_subs = await hist_mod.get_posts_for_period(
            client, ch, prev_d_from, prev_d_to, force=False)
        prev_by_id = {str(p["msg_id"]): p for p in prev_hist_posts}
        for mid, fp in prev_final.items():
            if mid in prev_by_id:
                prev_by_id[mid]["snapshot"] = fp["snapshot"]
            else:
                prev_by_id[mid] = fp
        prev_posts = list(prev_by_id.values())
        prev_stories = stories_mod.get_stories_for_period(ch, prev_d_from, prev_d_to)

        prev_react = sum((p.get("snapshot") or {}).get("reactions", 0) or 0 for p in prev_posts)
        prev_fwd   = sum((p.get("snapshot") or {}).get("forwards", 0)  or 0 for p in prev_posts)
        prev_comm  = sum((p.get("snapshot") or {}).get("comments", 0)  or 0 for p in prev_posts)
        prev_act   = sum((p.get("snapshot") or {}).get("actions", 0)   or 0 for p in prev_posts)

        metrics["prev_month_label"] = f"{MONTHS_RU.get(_prev_month,'')} {_prev_year}"
        metrics["prev_activity"] = {
            "posts_count":   len(prev_posts),
            "stories_count": len(prev_stories),
            "total_react":   prev_react,
            "total_fwd":     prev_fwd,
            "total_comments":prev_comm,
            "total_actions": prev_act,
        }

        # Среднее за 5 месяцев ДО текущего (для августа — март-июль
        # включительно). Сохраняем данные ПО КАЖДОМУ месяцу отдельно
        # (month_activities) — нужно для текстового вывода под графиком:
        # там среднее считается как "среднее из показателей НА ПОСТ по
        # каждому месяцу", а не "сумма за 5 месяцев / сумма постов" —
        # это разные числа (см. приложенное ТЗ, п.5).
        month_activities = [{
            "posts_count":    len(prev_posts),
            "stories_count":  len(prev_stories),
            "total_react":    prev_react,
            "total_fwd":      prev_fwd,
            "total_comments": prev_comm,
            "total_actions":  prev_act,
        }]
        _ay, _am = _prev_year, _prev_month
        for _ in range(4):  # ещё 4 месяца назад от "предыдущего" (итого 5)
            if _am == 1:
                _ay, _am = _ay - 1, 12
            else:
                _am -= 1
            _a_from = date(_ay, _am, 1)
            _a_to   = date(_ay, _am, _monthrange(_ay, _am)[1])
            _a_final = get_final_posts_for_period(ch, _a_from, _a_to)
            _a_hist, _ = await hist_mod.get_posts_for_period(client, ch, _a_from, _a_to, force=False)
            _a_by_id = {str(p["msg_id"]): p for p in _a_hist}
            for mid, fp in _a_final.items():
                if mid in _a_by_id:
                    _a_by_id[mid]["snapshot"] = fp["snapshot"]
                else:
                    _a_by_id[mid] = fp
            _a_posts = list(_a_by_id.values())
            _a_stories = stories_mod.get_stories_for_period(ch, _a_from, _a_to)

            month_activities.append({
                "posts_count":    len(_a_posts),
                "stories_count":  len(_a_stories),
                "total_react":    sum((p.get("snapshot") or {}).get("reactions", 0) or 0 for p in _a_posts),
                "total_fwd":      sum((p.get("snapshot") or {}).get("forwards", 0)  or 0 for p in _a_posts),
                "total_comments": sum((p.get("snapshot") or {}).get("comments", 0)  or 0 for p in _a_posts),
                "total_actions":  sum((p.get("snapshot") or {}).get("actions", 0)   or 0 for p in _a_posts),
            })

        # Для графика (суммы, делённые на 5 — простое среднее по объёму)
        metrics["avg5_activity"] = {
            k: round(sum(m[k] for m in month_activities) / 5, 1)
            for k in ("posts_count","stories_count","total_react","total_fwd","total_comments","total_actions")
        }

        # Текстовый вывод под графиком — отдельная методика (см. ТЗ):
        # среднее считается из показателей "на публикацию" по каждому
        # месяцу, а не из суммы за 5 месяцев.
        metrics["activity_summary"] = build_activity_summary_text(
            month_n,
            metrics.get("posts_count", 0),
            metrics.get("total_react", 0),
            metrics.get("total_comments", 0),
            metrics.get("total_fwd", 0),
            metrics.get("total_actions", 0),
            month_activities,
        )


        # Платные размещения
        paid_data[ch] = get_paid_placements(ch, date_from, date_to)

    # Глобальные метрики
    global_metrics = build_global_metrics(channel_metrics)

    # Формат cfg для JS
    cfg_for_js = {
        ch: {
            "name":  DASHBOARD_CHANNELS.get(ch, {}).get("name", ch),
            "color": DASHBOARD_CHANNELS.get(ch, {}).get("color", "#333333"),
        }
        for ch in CHANNELS
    }

    # История для JS (flat dict)
    history_for_js = {}
    for ch, entries in history.items():
        history_for_js[ch] = entries

    # Путь к файлу
    out_dir = OUTPUT_DIR / "dashboard"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"dashboard_{ym}.pptx"
    out_path = out_dir / fname

    all_data = {
        "global":          global_metrics,
        "channels":        channel_metrics,
        "history":         history,
        "history_data":    history_for_js,
        "paid":            {ch: paid_data.get(ch, []) for ch in CHANNELS},
        "extra_paid":      get_extra_paid_groups(date_from, date_to),
        "month_label":     month_label,
        "title_month_name":title_month_name,
        "title_year":      year_n,
        "channels_count":  len(channel_metrics),
        "channels_config": cfg_for_js,
    }

    log.info(f"=== Dashboard {ym} — генерация PPTX ===")
    try:
        _build_pptx(out_path, all_data, ym, period_label, history_range)
    except Exception as e:
        log.error(f"Ошибка генерации Dashboard PPTX: {e}")
        if client and override_recipients:
            for uid in override_recipients:
                try:
                    await client.send_message(uid,
                        f"❌ Ошибка генерации Dashboard за {ym}: {e}\n"
                        f"Проверьте что node.js установлен: node --version")
                except Exception:
                    pass
        return None

    # Dashboard больше не пишет в history_db.json — это делает
    # report.py при генерации месячного Excel-отчёта. Если для этого
    # месяца отчёт ещё ни разу не собирался — дашборд использует то,
    # что только что посчитал сам (см. цикл по каналам выше), но эти
    # цифры нигде не сохраняются; при следующей генерации Excel-отчёта
    # за этот месяц они лягут в базу правильным путём.

    # Отправка
    recipients = override_recipients or RECIPIENT_IDS
    if client and recipients:
        for uid in recipients:
            try:
                await client.send_message(uid, f"📊 Dashboard {month_label} готов")
                await client.send_file(uid, str(out_path), caption=f"📎 {fname}")
                log.info(f"Dashboard отправлен → {uid}")
            except Exception as e:
                log.error(f"Ошибка отправки dashboard → {uid}: {e}")

    return out_path


def get_cached_dashboard(ym: str) -> Path | None:
    from config import OUTPUT_DIR
    p = OUTPUT_DIR / "dashboard" / f"dashboard_{ym}.pptx"
    return p if p.exists() else None
