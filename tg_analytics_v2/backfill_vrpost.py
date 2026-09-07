"""
backfill_vrpost.py — одноразовый скрипт: досчитывает РЕАЛЬНЫЙ VRpost для
прошлых месяцев в history_db.json (там, где сейчас стоит null, потому что
исходный Excel не содержал этот показатель).

Никакой отсебятины и приближений: скрипт берёт настоящие посты за нужный
месяц (из кэша registry/<канал>/historical/YYYY-MM.json, либо — если кэша
ещё нет — дособирает их из Telegram точно так же, как обычный /backfill) и
считает VRpost по формуле:

    VRpost канала за месяц = среднее по постам от (views_поста / подписчики × 100)

— это ТА ЖЕ методика, что и колонка "Ср. VRpost (%)" в Excel-отчёте
(report.py), просто применённая к прошлым месяцам напрямую из кэша, а не
через генерацию отчёта.

Если для канала за месяц вообще нет постов (пустой месяц) — оставляет null,
никаких выдуманных чисел.

Меняет ТОЛЬКО поле "vrpost" в history_db.json для указанных месяцев.
subscribers/growth/avg_reach/err (взятые из предоставленного Excel) не
трогает.

ЗАПУСК (из папки проекта, .env должен быть настроен, сессия авторизована):
    python backfill_vrpost.py
    python backfill_vrpost.py 2026-02 2026-03      # можно указать конкретные месяцы
"""

import asyncio
import sys
from calendar import monthrange
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, CHANNELS, get_telethon_kwargs
import historical
from history_db import load_db, save_db, ensure_seeded, SEED_DATA

DEFAULT_MONTHS = sorted(SEED_DATA.keys())  # все месяцы, что были в исходном Excel


async def real_vrpost_for_month(client, channel: str, ym: str):
    """Возвращает (vrpost, posts_count) или (None, 0), если данных нет."""
    year, month = int(ym[:4]), int(ym[5:7])
    d_from = date(year, month, 1)
    d_to   = date(year, month, monthrange(year, month)[1])

    posts, subs = await historical.get_posts_for_period(
        client, channel, d_from, d_to, force=False,  # берём кэш, если есть
    )
    if not subs or not posts:
        return None, 0

    ratios = []
    for p in posts:
        views = (p.get("snapshot") or {}).get("views", 0) or 0
        ratios.append(views / subs * 100)

    if not ratios:
        return None, 0
    return round(sum(ratios) / len(ratios), 2), len(posts)


async def main():
    months = sys.argv[1:] or DEFAULT_MONTHS
    ensure_seeded()
    db = load_db()

    kwargs = get_telethon_kwargs()
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        for ym in months:
            print(f"\n=== {ym} ===")
            for ch in CHANNELS:
                real_vrpost, n_posts = await real_vrpost_for_month(client, ch, ym)
                old = db.get(ym, {}).get(ch, {}).get("vrpost")
                if real_vrpost is None:
                    print(f"  {ch}: реальных данных нет (0 постов/подписчиков) — оставляю как есть ({old})")
                    continue
                db.setdefault(ym, {}).setdefault(ch, {})["vrpost"] = real_vrpost
                print(f"  {ch}: {n_posts} постов | было {old} -> стало {real_vrpost} (реальные данные)")

    save_db(db)
    print("\nГотово — history_db.json обновлён реальными значениями VRpost.")


if __name__ == "__main__":
    asyncio.run(main())
