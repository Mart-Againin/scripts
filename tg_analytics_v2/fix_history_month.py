"""
fix_history_month.py — принудительно ПЕРЕСЧИТЫВАЕТ и ПЕРЕЗАПИСЫВАЕТ запись
в history_db.json для указанных месяцев, используя реальные посты из кэша
(та же методика, что и обычная сборка Dashboard).

ДЛЯ ЧЕГО ЭТОТ СКРИПТ:
  Обычный поток (Dashboard) записывает данные в history_db.json только
  ОДИН РАЗ за месяц+канал и больше никогда не перезаписывает (защита от
  случайной потери данных). Если по какой-то причине первая сборка
  месяца прошла с ошибкой и записала неполные/нулевые данные — эта
  защита не даёт им замениться на верные при последующих пересборках.

  Этот скрипт — осознанный, ручной, точечный инструмент именно для
  такого случая: вы явно указываете, какой месяц пересчитать заново, и
  скрипт заменяет запись, используя актуальные реальные данные.

  Другие месяцы и каналы, не указанные в команде, не трогает вообще.

ЗАПУСК (из папки проекта):
    python fix_history_month.py 2026-07 2026-08
"""

import asyncio
import sys
from calendar import monthrange
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, CHANNELS, get_telethon_kwargs
import historical
from registry_manager import get_final_posts_for_period, load_registry
from snapshot import get_month_growth
from history_db import load_db, save_db, ensure_seeded


async def recompute_month(client, channel: str, ym: str):
    """Пересчитывает subscribers/growth/avg_reach/err/vrpost за месяц —
    той же методикой, что build_dashboard(). Возвращает None, если
    реальных данных для расчёта нет (0 постов или 0 подписчиков)."""
    year, month = int(ym[:4]), int(ym[5:7])
    d_from = date(year, month, 1)
    d_to   = date(year, month, monthrange(year, month)[1])

    final_posts = get_final_posts_for_period(channel, d_from, d_to)
    hist_posts, subs = await historical.get_posts_for_period(
        client, channel, d_from, d_to, force=False)

    posts_by_id = {str(p["msg_id"]): p for p in hist_posts}
    for mid, fp in final_posts.items():
        if mid in posts_by_id:
            posts_by_id[mid]["snapshot"] = fp["snapshot"]
        else:
            posts_by_id[mid] = fp
    posts = list(posts_by_id.values())

    if not subs:
        reg = load_registry(channel)
        subs = reg.get("subscribers", 0)

    if not posts or not subs:
        return None

    views_list = [p.get("snapshot", {}).get("views", 0) or 0 for p in posts]
    avg_reach = sum(views_list) / len(views_list)

    err_list, vrpost_list = [], []
    for p in posts:
        sn = p.get("snapshot", {})
        v = sn.get("views", 0) or 0
        act = sn.get("actions", 0) or 0
        if v:
            err_list.append(act / v * 100)
            vrpost_list.append(v / subs * 100)
    avg_err = sum(err_list) / len(err_list) if err_list else 0
    vrpost  = sum(vrpost_list) / len(vrpost_list) if vrpost_list else None

    growth = get_month_growth(channel, ym)

    return {
        "subscribers": subs,
        "growth": growth,
        "avg_reach": round(avg_reach, 1),
        "err": round(avg_err, 2),
        "vrpost": round(vrpost, 2) if vrpost is not None else None,
    }


async def main(months):
    ensure_seeded()
    db = load_db()
    kwargs = get_telethon_kwargs()
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        for ym in months:
            print(f"\n=== {ym} ===")
            for ch in CHANNELS:
                new_entry = await recompute_month(client, ch, ym)
                if new_entry is None:
                    print(f"  {ch}: реальных данных нет (0 постов/подписчиков), пропуск")
                    continue
                old = db.get(ym, {}).get(ch)
                db.setdefault(ym, {})[ch] = new_entry
                print(f"  {ch}: было {old}\n         стало {new_entry}")
    save_db(db)
    print("\nГотово — history_db.json пересчитан заново для указанных месяцев.")


if __name__ == "__main__":
    months = sys.argv[1:]
    if not months:
        print("Укажите хотя бы один месяц, например:")
        print("  python fix_history_month.py 2026-07 2026-08")
        sys.exit(1)
    asyncio.run(main(months))
