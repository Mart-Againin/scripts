"""
force_recompute_month.py — полностью пересчитывает запись за указанный месяц
в history_db.json ИЗ РЕАЛЬНЫХ ДАННЫХ (подписчики, прирост, охват, ERR,
VRpost), даже если запись за этот месяц уже существует.

ЗАЧЕМ ЭТОТ СКРИПТ НУЖЕН:
  У record_month() (обычный путь записи, через дашборд) есть защита:
  если запись за (месяц, канал) уже есть — она НЕ перезаписывается.
  Это защищает от случайных перезаписей, но означает, что если запись
  когда-то была создана ДО того, как код починили (например, до фикса
  формулы прироста, или до того как файлы были заменены на сервере) —
  она "замораживается" неточной НАВСЕГДА и просто повторный пересчёт
  дашборда её не исправит.

  Этот скрипт — осознанный, ручной способ сказать "да, я знаю что запись
  уже есть, пересчитай её заново из реальных данных прямо сейчас".

ЧТО СЧИТАЕТ:
  - subscribers — текущее число подписчиков (из реестра/historical)
  - growth      — строго по границе месяца (snapshot.get_month_growth)
  - avg_reach, err, vrpost — из настоящих постов канала за этот месяц
    (тот же кэш historical/<ym>.json, что использует и сам дашборд)

ЗАПУСК:
    python force_recompute_month.py 2026-07
    python force_recompute_month.py 2026-07 2026-08

Перед перезаписью КАЖДОЙ существующей записи скрипт покажет старое и
новое значение и спросит подтверждение — ничего не меняется молча.
"""

import asyncio
import sys
from calendar import monthrange
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, CHANNELS, get_telethon_kwargs
import historical
import snapshot
from history_db import load_db, save_db, ensure_seeded


async def compute_month(client, channel: str, ym: str) -> dict | None:
    year, month = int(ym[:4]), int(ym[5:7])
    d_from = date(year, month, 1)
    d_to   = date(year, month, monthrange(year, month)[1])

    posts, subs = await historical.get_posts_for_period(
        client, channel, d_from, d_to, force=False)

    if not subs:
        from registry_manager import load_registry
        reg = load_registry(channel)
        subs = reg.get("subscribers", 0)

    if not posts:
        return None

    views_list = [(p.get("snapshot") or {}).get("views", 0) or 0 for p in posts]
    actions_list = [(p.get("snapshot") or {}).get("actions", 0) or 0 for p in posts]
    avg_reach = round(sum(views_list) / len(views_list), 1)
    err = round(sum(actions_list) / sum(views_list) * 100, 2) if sum(views_list) else 0

    vrpost = None
    if subs:
        ratios = [v / subs * 100 for v in views_list]
        vrpost = round(sum(ratios) / len(ratios), 2)

    growth = snapshot.get_month_growth(channel, ym)

    return {
        "subscribers": subs,
        "growth": growth,
        "avg_reach": avg_reach,
        "err": err,
        "vrpost": vrpost,
    }


async def main(months: list[str]):
    ensure_seeded()
    db = load_db()

    kwargs = get_telethon_kwargs()
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        for ym in months:
            print(f"\n=== {ym} ===")
            for ch in CHANNELS:
                new_data = await compute_month(client, ch, ym)
                if new_data is None:
                    print(f"  {ch}: нет постов за этот месяц, пропуск")
                    continue

                old_data = db.get(ym, {}).get(ch)
                if old_data:
                    print(f"  {ch}: сейчас в базе: {old_data}")
                    print(f"       пересчитано:   {new_data}")
                    answer = input("  Заменить на пересчитанные данные? (да/нет): ").strip().lower()
                    if answer not in ("да", "yes", "y", "д"):
                        print("  Пропущено по вашему решению.")
                        continue
                else:
                    print(f"  {ch}: записи не было, записываю: {new_data}")

                db.setdefault(ym, {})[ch] = new_data

    save_db(db)
    print("\nГотово.")


if __name__ == "__main__":
    months = sys.argv[1:]
    if not months:
        print("Укажите месяц(ы): python force_recompute_month.py 2026-07 2026-08")
        sys.exit(1)
    asyncio.run(main(months))
