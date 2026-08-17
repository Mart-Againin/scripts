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
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ── Вспомогательные функции ───────────────────────────────────────────────

def _fmt_num(v, decimals=0) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if decimals == 0:
            v = int(round(v))
        else:
            v = round(v, decimals)
    if isinstance(v, int) and abs(v) >= 1000:
        return f"{v:,}".replace(",", " ")
    return str(v)


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{round(v, 1)}%"


def _fmt_money(v) -> str:
    if v is None:
        return "—"
    return f"{int(v):,}".replace(",", " ") + " ₽"


def _fmt_growth(v) -> str:
    if v is None:
        return "—"
    return f"+{v}" if v > 0 else str(v)


def _channel_color(ch: str, cfg: dict) -> str:
    """Возвращает hex-цвет без # для pptxgenjs."""
    color = cfg.get(ch, {}).get("color", "#333333")
    return color.lstrip("#")


def _channel_name(ch: str, cfg: dict) -> str:
    return cfg.get(ch, {}).get("name", ch)


def _footer_text() -> str:
    return "Данные Telegram фиксируются через ~24 часа после публикации; платные размещения вводятся отдельно."


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
    import json as _json
    import subprocess
    import tempfile

    script = _make_pptx_script(all_data, str(output_path), ym,
                                period_label, history_range)

    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False, encoding="utf-8") as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            ["node", script_path],
            capture_output=True, text=True, timeout=120
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

    def hist_series(key):
        series = []
        for ch in channels_order:
            vals = [h.get(key) or 0 for h in (history.get(ch) or [])]
            series.append({
                "name":   _channel_name(ch, cfg),
                "color":  _channel_color(ch, cfg),
                "values": vals,
            })
        return series

    subs_series   = hist_series("subscribers")
    reach_series  = hist_series("avg_reach")
    growth_series = hist_series("growth")
    err_series    = hist_series("err")

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
        "growth_series": growth_series,
        "err_series":    err_series,
        "paid":          paid,
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

function lineChart(slide, x, y, w, h, series, cats, title, showLegend) {{
    const chartData = series.map(s => ({{
        name: s.name,
        labels: cats,
        values: s.values
    }}));
    const colors = series.map(s => s.color);
    slide.addChart(pres.charts.LINE, chartData, {{
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
    }});
}}

function barChart(slide, x, y, w, h, labels, values, colors, chartTitle) {{
    const chartData = labels.map((name, i) => ({{
        name,
        labels: [name],
        values: [values[i]]
    }}));
    slide.addChart(pres.charts.BAR, chartData, {{
        x, y, w, h,
        barDir: "col",
        chartColors: colors,
        showTitle: !!chartTitle,
        title: chartTitle || "",
        titleFontSize: 11,
        titleColor: NAVY,
        showLegend: false,
        showValue: true,
        dataLabelFontSize: 9,
        dataLabelColor: GRAY,
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
        showTitle: !!chartTitle,
        title: chartTitle || "",
        titleFontSize: 11,
        titleColor: NAVY,
        showLegend: false,
        showValue: true,
        dataLabelFontSize: 9,
        dataLabelColor: GRAY,
        dataLabelPosition: "outEnd",
        catAxisLabelColor: GRAY,
        valAxisLabelColor: GRAY,
        valGridLine: {{ style:"none" }},
        catGridLine: {{ style:"none" }},
        chartArea: {{ fill:{{ color:WHITE }} }},
    }});
}}

// ── Начало ────────────────────────────────────────────────────────────────
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
DATA.footer_text = "Данные Telegram фиксируются через ~24 часа после публикации; платные размещения вводятся отдельно.";

// ════════ СЛАЙД 1: ОБЛОЖКА ════════════════════════════════════════════════
{{
    const s = pres.addSlide();
    s.background = {{ color: NAVY }};
    s.addText("Telegram-каналы", {{
        x:0.5, y:1.8, w:SLIDE_W-1, h:0.8,
        fontSize:36, bold:true, color:WHITE, align:"center"
    }});
    s.addText("Аналитический отчёт", {{
        x:0.5, y:2.6, w:SLIDE_W-1, h:0.5,
        fontSize:20, color:"CADCFC", align:"center"
    }});
    s.addText(DATA.period_label, {{
        x:0.5, y:3.4, w:SLIDE_W-1, h:0.4,
        fontSize:16, color:"CADCFC", align:"center"
    }});
    s.addText("Динамика: " + DATA.history_range, {{
        x:0.5, y:3.85, w:SLIDE_W-1, h:0.35,
        fontSize:13, color:"8C9BC4", align:"center", italic:true
    }});
    // Каналы и цвета
    const chList = DATA.channels.map((m,i) => {{
        const name = DATA.cfg[m.channel] ? DATA.cfg[m.channel].name : m.channel;
        const color = DATA.cfg[m.channel] ? DATA.cfg[m.channel].color.replace("#","") : "FFFFFF";
        return {{ name, color, subscribers: m.subscribers }};
    }});
    const colW = (SLIDE_W - 1.0) / chList.length;
    chList.forEach((ch, i) => {{
        const x = 0.5 + i * colW;
        s.addShape(pres.shapes.OVAL, {{ x: x+colW/2-0.12, y:5.1, w:0.24, h:0.24, fill:{{ color:ch.color }} }});
        s.addText(ch.name, {{ x, y:5.4, w:colW, h:0.3, align:"center", fontSize:11, color:WHITE }});
        s.addText(ch.subscribers ? (ch.subscribers >= 1000 ? (ch.subscribers/1000).toFixed(1)+"K" : String(ch.subscribers))+" подп." : "—",
            {{ x, y:5.72, w:colW, h:0.25, align:"center", fontSize:9, color:"8C9BC4" }});
    }});
    s.addText("Цвет канала сохраняется на всех общих графиках.", {{
        x:0.5, y:6.4, w:SLIDE_W-1, h:0.28,
        fontSize:9, color:"8C9BC4", align:"center", italic:true
    }});
    s.addText("01", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:"8C9BC4", align:"right" }});
}}

// ════════ СЛАЙД 2: EXEC SUMMARY ОБЩИЙ ════════════════════════════════════
{{
    const s = pres.addSlide();
    const g = DATA.global;
    kicker(s, "Общий обзор");
    title(s, "Executive Summary", DATA.period_label);
    const boxes = [
        {{ label:"Общая аудитория",    value: g.total_subscribers >= 1000 ? (g.total_subscribers/1000).toFixed(1)+"K" : String(g.total_subscribers||"—"), sub:"на конец месяца" }},
        {{ label:"Прирост аудитории",  value: g.total_growth > 0 ? "+"+g.total_growth : String(g.total_growth||"—"), sub:"за месяц" }},
        {{ label:"Темп роста",         value: g.growth_pct ? g.growth_pct+"%" : "—", sub:"к прошлому периоду" }},
        {{ label:"Публикаций",         value: String(g.total_posts||"—"), sub:"за период" }},
        {{ label:"Средний охват",      value: g.avg_reach ? Math.round(g.avg_reach) : "—", sub:"на публикацию" }},
        {{ label:"ER (ERR)",           value: g.avg_err ? g.avg_err.toFixed(1)+"%" : "—", sub:"вовлечённость" }},
    ];
    const bw = 2.0, bh = 1.4, bx0 = 0.5, by = 1.7, gap = 0.15;
    boxes.forEach((b,i) => statBox(s, bx0 + i*(bw+gap), by, bw, bh, b.label, b.value, b.sub));

    const ry = 3.4;
    s.addText("Реакции: " + (g.total_react||"—") + "   Комментарии: " + (g.total_comments||"—") + "   Пересылки: " + (g.total_fwd||"—"), {{
        x:0.5, y:ry, w:SLIDE_W-1, h:0.35,
        fontSize:12, color:GRAY, align:"center"
    }});

    // Мини-бары по каналам
    const bary = 3.9;
    DATA.channels.forEach((m, i) => {{
        const x = 0.5 + i*(bw+gap);
        const color = DATA.cfg[m.channel] ? DATA.cfg[m.channel].color.replace("#","") : "5B8DEF";
        const name  = DATA.cfg[m.channel] ? DATA.cfg[m.channel].name : m.channel;
        s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{ x, y:bary, w:bw, h:1.8, rectRadius:0.08, fill:{{ color:"F4F6FB" }} }});
        s.addShape(pres.shapes.OVAL, {{ x:x+0.15, y:bary+0.15, w:0.18, h:0.18, fill:{{ color }} }});
        s.addText(name, {{ x:x+0.4, y:bary+0.1, w:bw-0.5, h:0.3, fontSize:10, bold:true, color:NAVY }});
        s.addText("Подп.: " + (m.subscribers||"—"), {{ x:x+0.15, y:bary+0.45, w:bw-0.3, h:0.25, fontSize:9, color:GRAY }});
        s.addText("Охват: " + (m.avg_reach ? Math.round(m.avg_reach) : "—"), {{ x:x+0.15, y:bary+0.7, w:bw-0.3, h:0.25, fontSize:9, color:GRAY }});
        s.addText("ER: " + (m.err ? m.err.toFixed(1)+"%" : "—"), {{ x:x+0.15, y:bary+0.95, w:bw-0.3, h:0.25, fontSize:9, color:GRAY }});
        s.addText("Постов: " + (m.posts_count||0), {{ x:x+0.15, y:bary+1.2, w:bw-0.3, h:0.25, fontSize:9, color:GRAY }});
    }});
    footer(s);
    s.addText("02", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
}}

// ════════ СЛАЙДЫ 3-6: ОБЩАЯ ДИНАМИКА ════════════════════════════════════
{{
    // Слайд 3: Подписчики
    const s3 = pres.addSlide();
    kicker(s3, "Динамика · 6 месяцев");
    title(s3, "Динамика подписчиков по всем каналам", DATA.history_range);
    lineChart(s3, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.subs_series, DATA.month_labels, null, true);
    footer(s3);
    s3.addText("03", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});

    // Слайд 4: Средний охват
    const s4 = pres.addSlide();
    kicker(s4, "Динамика · 6 месяцев");
    title(s4, "Динамика среднего охвата публикации", DATA.history_range);
    lineChart(s4, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.reach_series, DATA.month_labels, null, true);
    footer(s4);
    s4.addText("04", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});

    // Слайд 5: Прирост подписчиков (bar)
    const s5 = pres.addSlide();
    kicker(s5, "Динамика · 6 месяцев");
    title(s5, "Динамика прироста подписчиков", DATA.history_range);
    lineChart(s5, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.growth_series, DATA.month_labels, null, true);
    s5.addText("Нулевая линия: значения выше — рост, ниже — отток аудитории.", {{
        x:0.5, y:6.4, w:SLIDE_W-1, h:0.3, fontSize:9, color:GRAY, italic:true
    }});
    footer(s5);
    s5.addText("05", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});

    // Слайд 6: ER
    const s6 = pres.addSlide();
    kicker(s6, "Динамика · 6 месяцев");
    title(s6, "Динамика ER по каналам", DATA.history_range);
    lineChart(s6, 0.5, 1.7, SLIDE_W-1, 4.8, DATA.err_series, DATA.month_labels, null, true);
    s6.addText("ER рассчитывается из ERR (%) — действия / охват × 100%.", {{
        x:0.5, y:6.4, w:SLIDE_W-1, h:0.3, fontSize:9, color:GRAY, italic:true
    }});
    footer(s6);
    s6.addText("06", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
}}

// ════════ СЛАЙД 7: КОНТЕНТНАЯ АКТИВНОСТЬ ═════════════════════════════════
{{
    const s = pres.addSlide();
    kicker(s, "Отчётный месяц");
    title(s, "Контентная активность каналов", DATA.period_label);
    barChart(s, 0.5, 1.8, 6.0, 4.5, DATA.ch_names, DATA.posts_counts, DATA.ch_colors, "Количество публикаций");
    barChart(s, 6.9, 1.8, 6.0, 4.5, DATA.ch_names, DATA.reach_avgs,   DATA.ch_colors, "Средний охват публикации");
    footer(s);
    s.addText("07", {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
}}

// ════════ БЛОКИ ПО КАНАЛАМ ════════════════════════════════════════════════
let slideNum = 8;

DATA.channels.forEach(m => {{
    const ch      = m.channel;
    const cfg_ch  = DATA.cfg[ch] || {{}};
    const color   = (cfg_ch.color || "#333333").replace("#","");
    const name    = cfg_ch.name || ch;
    const hist    = DATA.history_data ? DATA.history_data[ch] : null;
    const bw_data = m.best_worst || {{}};
    const paid_ch = DATA.paid[ch] || [];

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
            {{ l:"CQI",         v: m.cqi ? m.cqi.toFixed(2) : "—", s:"качество контента" }},
        ];
        const bw2 = 1.7, bh2 = 1.3, bx0 = 0.5, by2 = 1.55, gap2 = 0.15;
        vals.forEach((b,i) => statBox(s, bx0+i*(bw2+gap2), by2, bw2, bh2, b.l, b.v, b.s));

        s.addText("Реакции: "+(m.total_react||"—")+"   Комменты: "+(m.total_comments||"—")+"   Пересылки: "+(m.total_fwd||"—"), {{
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

        // Активность (bar chart)
        const act_labels = ["Посты","Сторис","Реакции","Комменты","Пересылки","Действия"];
        const act_values = [
            m.posts_count||0, m.stories_count||0, m.total_react||0,
            m.total_comments||0, m.total_fwd||0, m.total_actions||0
        ];
        barChart(s, 0.5, 1.6, 6.0, 4.0, act_labels, act_values, [color], "Активность и реакции аудитории");

        // Метрики справа
        const metrics2 = [
            {{ l:"ER (ERR %)",     v: m.err         ? m.err.toFixed(1)+"%"    : "—" }},
            {{ l:"CQI",            v: m.cqi         ? m.cqi.toFixed(2)        : "—" }},
            {{ l:"VRpost",         v: m.vrpost      ? m.vrpost.toFixed(1)+"%"  : "—" }},
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
        ];
        const cw = 4.1;
        bests.forEach((b, i) => {{
            const x = 0.5 + i*(cw+0.1);
            s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{ x, y:1.55, w:cw, h:1.5, rectRadius:0.08, fill:{{ color:"F4F6FB" }} }});
            s.addText(b.title, {{ x, y:1.6, w:cw, h:0.3, align:"center", fontSize:10, bold:true, color:NAVY }});
            if (b.data) {{
                s.addText(b.data.date + " · " + b.data.content_type, {{ x, y:1.92, w:cw, h:0.25, align:"center", fontSize:9, color:GRAY }});
                if (b.data.text_short) s.addText(b.data.text_short, {{ x:x+0.1, y:2.17, w:cw-0.2, h:0.28, align:"center", fontSize:8, color:GRAY, italic:true }});
                s.addText((b.data.views||"—")+" просм. · "+(b.data.reactions||"—")+" реакц. · ER "+(b.data.err ? b.data.err.toFixed(1)+"%" : "—"),
                    {{ x, y:b.data.text_short ? 2.46 : 2.17, w:cw, h:0.25, align:"center", fontSize:9, color:GRAY }});
                if (b.data.url) s.addText(b.data.url||"", {{ x, y:b.data.text_short ? 2.72 : 2.44, w:cw, h:0.2, align:"center", fontSize:8, color:"5B8DEF", hyperlink:{{ url: b.data.url||"#" }} }});
            }} else {{
                s.addText("—", {{ x, y:2.0, w:cw, h:0.5, align:"center", fontSize:14, color:GRAY }});
            }}
        }});

        // TOP-5 по охвату (горизонтальный бар)
        if (bw_data.top5_reach && bw_data.top5_reach.length > 0) {{
            const top5labels = bw_data.top5_reach.map((p,i) => (i+1)+". "+p.date.slice(5)+" "+p.content_type);
            const top5vals   = bw_data.top5_reach.map(p => p.views||0);
            hBarChart(s, 0.5, 3.2, 6.3, 3.5, top5labels, top5vals, color, "TOP-5 по охвату");
        }}

        // Худшие посты (3 карточки справа)
        if (bw_data.worst3 && bw_data.worst3.length > 0) {{
            s.addText("Ниже среднего / точки внимания", {{ x:7.1, y:3.2, w:5.8, h:0.3, fontSize:11, bold:true, color:NAVY }});
            bw_data.worst3.forEach((p, i) => {{
                if (!p) return;
                const wy = 3.6 + i*1.1;
                s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{ x:7.1, y:wy, w:5.8, h:1.0, rectRadius:0.08, fill:{{ color:"FFF5F5" }} }});
                s.addText(p.date + " · " + p.content_type, {{ x:7.2, y:wy+0.05, w:5.6, h:0.28, fontSize:10, bold:true, color:NAVY }});
                s.addText((p.views||"—")+" просм. · ER "+(p.err ? p.err.toFixed(1)+"%" : "—")+
                    (p.deviation ? "  (" + (p.deviation > 0 ? "+" : "")+p.deviation+"% к среднему)" : ""),
                    {{ x:7.2, y:wy+0.33, w:5.6, h:0.25, fontSize:9, color:GRAY }});
                if (p.url) s.addText("открыть", {{ x:7.2, y:wy+0.6, w:2, h:0.22, fontSize:8, color:"5B8DEF", hyperlink:{{ url: p.url }} }});
            }});
        }}

        footer(s);
        s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
        slideNum++;
    }}

    // ── СЛАЙД D: Платные посевы (только если есть данные) ─────────────────
    if (paid_ch && paid_ch.length > 0) {{
        const s = pres.addSlide();
        s.addShape(pres.shapes.RECTANGLE, {{ x:0, y:0, w:0.18, h:SLIDE_H, fill:{{ color }} }});
        kicker(s, name + " · платные размещения");
        s.addText(name + ": платные посевы", {{
            x:0.5, y:0.45, w:SLIDE_W-0.7, h:0.65, fontSize:22, bold:true, color:NAVY
        }});
        s.addText(DATA.period_label, {{ x:0.5, y:1.1, w:SLIDE_W-0.7, h:0.3, fontSize:12, color:GRAY }});

        // Агрегаты
        const total_budget = paid_ch.reduce((a,p) => a+(p.budget||0), 0);
        const total_reach  = paid_ch.reduce((a,p) => a+(p.reach||0), 0);
        const total_inflow = paid_ch.reduce((a,p) => a+(p.inflow||0), 0);
        const avg_cpv      = total_reach  ? Math.round(total_budget/total_reach*100)/100 : null;
        const avg_cpf      = total_inflow ? Math.round(total_budget/total_inflow*100)/100 : null;

        const bullets = [
            "Размещений: " + paid_ch.length,
            "Бюджет: " + (total_budget ? total_budget.toLocaleString("ru")+" ₽" : "—"),
            "Охват: " + (total_reach  ? total_reach.toLocaleString("ru") : "—"),
            "Приток: " + (total_inflow ? total_inflow.toLocaleString("ru") : "—"),
            "Средний CPV: " + (avg_cpv  ? avg_cpv+" ₽"  : "—"),
            "Средний CPF: " + (avg_cpf  ? avg_cpf+" ₽"  : "—"),
        ];
        s.addText(bullets.join("   ·   "), {{
            x:0.5, y:1.5, w:SLIDE_W-0.7, h:0.35,
            fontSize:11, color:NAVY, bold:false
        }});

        // Два графика
        const plat_names   = paid_ch.map(p => p.platform);
        const reach_vals   = paid_ch.map(p => p.reach  || 0);
        const cpf_vals     = paid_ch.map(p => p.cpf    || 0);

        if (paid_ch.length > 1) {{
            hBarChart(s, 0.5, 2.0, 5.8, 2.8, plat_names, reach_vals, color, "Охват по размещениям");
            hBarChart(s, 6.9, 2.0, 5.8, 2.8, plat_names, cpf_vals,   color, "CPF по размещениям, ₽");
        }}

        // Таблица
        const tby = 4.95;
        const cols = ["Площадка","Дата","Стоимость","Охват","Приток","CPV","CPF"];
        const colW2 = [3.5, 1.3, 1.5, 1.2, 1.2, 1.2, 1.2];
        let tx = 0.5;
        cols.forEach((c, i) => {{
            s.addShape(pres.shapes.RECTANGLE, {{ x:tx, y:tby, w:colW2[i], h:0.3, fill:{{ color:"1F3864" }} }});
            s.addText(c, {{ x:tx, y:tby, w:colW2[i], h:0.3, align:"center", fontSize:8, bold:true, color:WHITE }});
            tx += colW2[i];
        }});
        paid_ch.forEach((p, ri) => {{
            let tx2 = 0.5;
            const row_y = tby + 0.3 + ri*0.28;
            const bg2   = ri%2 === 0 ? "FFFFFF" : "F4F6FB";
            const vals2 = [
                p.platform,
                p.date ? p.date.slice(5).split("-").reverse().join(".") : "—",
                p.budget ? p.budget.toLocaleString("ru")+" ₽" : "—",
                p.reach  ? p.reach.toLocaleString("ru") : "—",
                p.inflow ? p.inflow.toLocaleString("ru") : "—",
                p.cpv    ? p.cpv+" ₽"  : "—",
                p.cpf    ? p.cpf+" ₽"  : "—",
            ];
            vals2.forEach((v, i) => {{
                s.addShape(pres.shapes.RECTANGLE, {{ x:tx2, y:row_y, w:colW2[i], h:0.27, fill:{{ color:bg2 }} }});
                s.addText(String(v), {{ x:tx2, y:row_y, w:colW2[i], h:0.27, align: i===0?"left":"center", fontSize:8, color:GRAY, margin:3 }});
                tx2 += colW2[i];
            }});
        }});

        footer(s);
        s.addText(String(slideNum).padStart(2,"0"), {{ x:SLIDE_W-0.8, y:SLIDE_H-0.4, w:0.5, h:0.3, fontSize:10, color:GRAY, align:"right" }});
        slideNum++;
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
                        RECIPIENT_IDS, DEBUG_IDS, TZ, CQI_W)
    from history_db import (ensure_seeded, get_all_channels_history,
                             record_month_from_report)
    from dashboard_metrics import (build_channel_metrics, build_global_metrics)
    from paid_placements import get_paid_placements

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

    # История за 6 месяцев
    history = get_all_channels_history(CHANNELS, months=6, end_ym=ym)

    # Метки диапазона истории
    all_months = list(list(history.values())[0]) if history else []
    if all_months:
        first = all_months[0]["month_label"]
        last  = all_months[-1]["month_label"]
        history_range = f"{first}–{last} {year_n}"
    else:
        history_range = ym

    # Данные по каналам
    channel_metrics = []
    paid_data       = {}

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
        channel_metrics.append(metrics)

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
        "month_label":     month_label,
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

    # Записываем в историю
    record_month_from_report(ym, [
        {"channel_id": m["channel"], "subscribers": m["subscribers"],
         "posts": []} for m in channel_metrics
    ])

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
