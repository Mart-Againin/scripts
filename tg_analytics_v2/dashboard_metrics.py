"""
dashboard_metrics.py — расчёт агрегатов для дашборда.

Возвращает нормализованные структуры данных.
Не занимается отрисовкой презентации.
"""

import logging
from datetime import date

log = logging.getLogger(__name__)

MONTHS_RU = {
    1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
    7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"
}


def metric_or_na(value):
    """Возвращает '—' если значение None или 0 без смысла."""
    if value is None:
        return "—"
    return value


def _safe_div(a, b, pct=False, decimals=2):
    if not b:
        return None
    result = a / b
    if pct:
        result *= 100
    return round(result, decimals)


def calc_post_metrics(posts: list, subscribers: int) -> dict:
    """Считает агрегатные метрики по списку постов."""
    if not posts:
        return {}

    views_list    = []
    react_list    = []
    comments_list = []
    fwd_list      = []
    votes_list    = []
    actions_list  = []

    for p in posts:
        sn = p.get("snapshot") or {}
        if not sn:
            continue
        views_list.append(sn.get("views", 0) or 0)
        react_list.append(sn.get("reactions", 0) or 0)
        comments_list.append(sn.get("comments", 0) or 0)
        fwd_list.append(sn.get("forwards", 0) or 0)
        votes_list.append(sn.get("votes", 0) or 0)
        actions_list.append(sn.get("actions", 0) or 0)

    if not views_list:
        return {}

    total_views    = sum(views_list)
    total_react    = sum(react_list)
    total_comments = sum(comments_list)
    total_fwd      = sum(fwd_list)
    total_votes    = sum(votes_list)
    total_actions  = sum(actions_list)
    n              = len(views_list)

    avg_reach = round(total_views / n, 1) if n else 0

    # ERR = actions / views (по всему периоду, не среднее процентов)
    err = _safe_div(total_actions, total_views, pct=True)

    # CQI period = Σ(R*1 + V*2 + F*4 + C*5) / ΣViews * 100
    from config import CQI_W
    cqi_num = (total_react   * CQI_W["react"] +
               total_votes   * CQI_W["vote"]  +
               total_fwd     * CQI_W["forward"] +
               total_comments* CQI_W["comment"])
    cqi = _safe_div(cqi_num, total_views, decimals=2)

    vrpost      = _safe_div(total_views,    subscribers, pct=True)
    viral_factor= _safe_div(total_fwd,      total_views, pct=True)
    reply_rate  = _safe_div(total_comments, total_views, pct=True)
    reach_mult  = _safe_div(total_views,    subscribers, decimals=2)

    return {
        "posts_count":   n,
        "total_views":   total_views,
        "total_react":   total_react,
        "total_comments":total_comments,
        "total_fwd":     total_fwd,
        "total_votes":   total_votes,
        "total_actions": total_actions,
        "avg_reach":     avg_reach,
        "err":           err,
        "cqi":           cqi,
        "vrpost":        vrpost,
        "viral_factor":  viral_factor,
        "reply_rate":    reply_rate,
        "reach_mult":    reach_mult,
    }


def get_best_worst_posts(posts: list, subscribers: int, top_n: int = 5) -> dict:
    """Определяет лучшие и худшие посты."""
    if not posts:
        return {"best_reach": None, "best_react": None, "best_er": None,
                "top5_reach": [], "worst3": []}

    scored = []
    for p in posts:
        sn = p.get("snapshot") or {}
        views   = sn.get("views", 0) or 0
        react   = sn.get("reactions", 0) or 0
        actions = sn.get("actions", 0) or 0
        err     = round(actions / views * 100, 2) if views else 0
        scored.append({**p, "_views": views, "_react": react, "_err": err})

    by_views  = sorted(scored, key=lambda x: x["_views"],  reverse=True)
    by_react  = sorted(scored, key=lambda x: x["_react"],  reverse=True)
    by_err    = sorted(scored, key=lambda x: x["_err"],    reverse=True)
    by_views_asc = sorted(scored, key=lambda x: x["_views"])

    def _fmt(p):
        if not p:
            return None
        sn = p.get("snapshot") or {}
        return {
            "date":         p.get("date", ""),
            "content_type": p.get("content_type", ""),
            "url":          p.get("url", ""),
            "views":        sn.get("views", 0) or 0,
            "reactions":    sn.get("reactions", 0) or 0,
            "err":          p["_err"],
            "text_short":   p.get("text_short", ""),
        }

    # Средний охват для отклонения
    all_views = [p["_views"] for p in scored if p["_views"]]
    avg = sum(all_views) / len(all_views) if all_views else 0

    worst = []
    for p in by_views_asc[:3]:
        pf = _fmt(p)
        if pf and avg:
            pf["deviation"] = round((pf["views"] - avg) / avg * 100, 1)
        worst.append(pf)

    return {
        "best_reach": _fmt(by_views[0])  if by_views  else None,
        "best_react": _fmt(by_react[0])  if by_react  else None,
        "best_er":    _fmt(by_err[0])    if by_err    else None,
        "top5_reach": [_fmt(p) for p in by_views[:top_n]],
        "worst3":     worst,
    }


def build_channel_metrics(channel: str, posts: list, stories: list,
                           subscribers: int, history: list) -> dict:
    """
    Собирает полный набор метрик по каналу для дашборда.
    history — список из get_history() (6 месяцев)
    """
    from config import DASHBOARD_CHANNELS
    ch_cfg = DASHBOARD_CHANNELS.get(channel, {})

    metrics = calc_post_metrics(posts, subscribers)
    bw      = get_best_worst_posts(posts, subscribers)

    # Прирост из истории (последний месяц)
    growth = None
    if history:
        last = history[-1]
        growth = last.get("growth")

    # Сторис
    stories_count = len(stories)
    stories_views = sum((s.get("snapshot") or {}).get("views", 0) or 0 for s in stories)
    stories_react = sum((s.get("snapshot") or {}).get("reactions", 0) or 0 for s in stories)

    return {
        "channel":        channel,
        "channel_name":   ch_cfg.get("name", channel),
        "color":          ch_cfg.get("color", "333333").lstrip("#"),
        "subscribers":    subscribers,
        "growth":         growth,
        "history":        history,
        "posts_count":    metrics.get("posts_count", 0),
        "stories_count":  stories_count,
        "stories_views":  stories_views,
        "stories_react":  stories_react,
        "avg_reach":      metrics.get("avg_reach"),
        "err":            metrics.get("err"),
        "cqi":            metrics.get("cqi"),
        "vrpost":         metrics.get("vrpost"),
        "viral_factor":   metrics.get("viral_factor"),
        "reply_rate":     metrics.get("reply_rate"),
        "reach_mult":     metrics.get("reach_mult"),
        "total_react":    metrics.get("total_react"),
        "total_comments": metrics.get("total_comments"),
        "total_fwd":      metrics.get("total_fwd"),
        "total_actions":  metrics.get("total_actions"),
        "best_worst":     bw,
    }


def build_global_metrics(channel_metrics: list) -> dict:
    """Агрегат по всем каналам для слайда Executive Summary."""
    total_subs    = sum(m["subscribers"] or 0 for m in channel_metrics)
    total_growth  = sum(m["growth"] or 0 for m in channel_metrics)
    total_posts   = sum(m["posts_count"] or 0 for m in channel_metrics)
    total_react   = sum(m["total_react"] or 0 for m in channel_metrics)
    total_comments= sum(m["total_comments"] or 0 for m in channel_metrics)
    total_fwd     = sum(m["total_fwd"] or 0 for m in channel_metrics)

    reach_list  = [m["avg_reach"] for m in channel_metrics if m.get("avg_reach")]
    err_list    = [m["err"] for m in channel_metrics if m.get("err")]
    avg_reach   = round(sum(reach_list) / len(reach_list), 0) if reach_list else None
    avg_err     = round(sum(err_list)   / len(err_list),   2) if err_list   else None

    growth_pct = None
    if total_growth and total_subs:
        base = total_subs - total_growth
        if base:
            growth_pct = round(total_growth / base * 100, 1)

    return {
        "total_subscribers": total_subs,
        "total_growth":      total_growth,
        "growth_pct":        growth_pct,
        "total_posts":       total_posts,
        "avg_reach":         avg_reach,
        "avg_err":           avg_err,
        "total_react":       total_react,
        "total_comments":    total_comments,
        "total_fwd":         total_fwd,
    }
