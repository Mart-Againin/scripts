"""
audit_snapshot_coverage.py — сверяет, что реально видит snapshot.py
(registry.json + archive/) с тем, что действительно есть в Telegram
(через historical.py), и показывает КОНКРЕТНЫЕ посты, которые снапшот
пропустил — с датой и ссылкой на каждый.

ЗАЧЕМ ЭТО НУЖНО: часовой сборщик сканирует последние 25 часов сообщений
канала — этого окна с запасом хватает, чтобы пережить обычный рестарт
сервера. Если посты всё-таки систематически пропадают из registry.json —
это не "так устроено", а сигнал о реальном сбое (простой скрипта дольше
суток) либо о баге в самом сборе. Этот скрипт даёт точный ответ вместо
предположений.

ЗАПУСК (из папки проекта):
    python audit_snapshot_coverage.py 2026-08
    python audit_snapshot_coverage.py 2026-08 2026-09        # несколько месяцев
    python audit_snapshot_coverage.py 2026-08 --channel @ktsdaily
"""

import asyncio
import sys
from calendar import monthrange
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, CHANNELS, get_telethon_kwargs
import historical
from registry_manager import get_final_posts_for_period


async def audit_month(client, channel: str, ym: str):
    """
    Возвращает (telegram_posts, missing_posts):
      telegram_posts — ВСЕ посты канала за месяц напрямую из Telegram
                        (источник истины, historical.py)
      missing_posts  — те из них, которых НЕТ ни в registry.json, ни в
                        archive/ (то есть snapshot.py их не зарегистрировал)
    """
    year, month = int(ym[:4]), int(ym[5:7])
    d_from = date(year, month, 1)
    d_to   = date(year, month, monthrange(year, month)[1])

    telegram_posts, _ = await historical.get_posts_for_period(
        client, channel, d_from, d_to, force=False)
    telegram_ids = {str(p["msg_id"]) for p in telegram_posts}

    registered_ids = set(get_final_posts_for_period(channel, d_from, d_to).keys())

    missing_ids = telegram_ids - registered_ids
    missing_posts = [p for p in telegram_posts if str(p["msg_id"]) in missing_ids]
    missing_posts.sort(key=lambda p: (p.get("date", ""), p.get("time", "")))

    return telegram_posts, missing_posts


async def main(months: list, only_channel: str = None):
    channels = [only_channel] if only_channel else CHANNELS
    kwargs = get_telethon_kwargs()
    total_missing = 0

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        for ym in months:
            print(f"\n=== {ym} ===")
            for ch in channels:
                telegram_posts, missing = await audit_month(client, ch, ym)
                if not telegram_posts:
                    print(f"  {ch}: нет постов за месяц")
                    continue
                if not missing:
                    print(f"  {ch}: OK — все {len(telegram_posts)} постов есть в registry/archive")
                else:
                    total_missing += len(missing)
                    print(f"  {ch}: пропущено {len(missing)} из {len(telegram_posts)} постов:")
                    for p in missing:
                        print(f"      {p.get('date','?')} {p.get('time','?')}  {p.get('url','')}")

    print()
    if total_missing == 0:
        print("Итог: пропусков не найдено. Снапшот работает как положено.")
    else:
        print(f"Итог: пропущено постов всего: {total_missing}.")
        print("Проверьте logs/snapshot.log за перечисленные выше даты — "
              "скорее всего там был простой скрипта дольше 24-25 часов "
              "(смотрите последнюю запись перед пропуском и первую после).")


if __name__ == "__main__":
    args = sys.argv[1:]
    only_channel = None
    if "--channel" in args:
        idx = args.index("--channel")
        only_channel = args[idx + 1]
        del args[idx:idx + 2]

    months = args
    if not months:
        print("Укажите хотя бы один месяц, например:")
        print("  python audit_snapshot_coverage.py 2026-08")
        sys.exit(1)

    asyncio.run(main(months, only_channel))
