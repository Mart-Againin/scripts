"""
refetch_texts.py — пересобирает исторический кэш (historical/<ym>.json) для
указанных месяцев ЗАНОВО из Telegram — это единственный способ добавить
поле "message" (текст поста) в уже закэшированные месяцы, так как раньше
скрипт вообще не сохранял текст ни в одном файле.

ЧТО ДЕЛАЕТ:
  Для каждого месяца и каждого канала из CHANNELS (.env) вызывает
  historical.fetch_and_cache_month(..., force=True) — это полностью
  перечитывает месяц из Telegram и полностью перезаписывает
  registry/<канал>/historical/<ym>.json свежими данными (включая
  теперь и текст поста).

ВАЖНО — ЧТО ИМЕННО ПЕРЕЗАПИСЫВАЕТСЯ:
  - Перезаписывается ТОЛЬКО файл historical/<ym>.json для указанного
    месяца и канала. registry.json (24ч-срезы) и archive/*.json — не
    трогаются вообще, ими управляет только snapshot.py.
  - views/reactions/comments и т.д. в historical-кэше — это статистика
    НА МОМЕНТ ЗАПРОСА (то есть на момент запуска этого скрипта), а не
    архивные 24ч-срезы. Для уже завершённых месяцев (posts публиковались
    давно) эти цифры практически не меняются со временем, так что после
    пересборки они останутся такими же или почти такими же — просто
    добавится текст. Но для ТЕКУЩЕГО, ещё идущего месяца пересборка
    заменит статистику на "текущую" (что и так происходит при каждом
    обычном отчёте по этому месяцу — это нормальное поведение
    historical.py, не что-то новое).

КОМАНДА ЗАПУСКА (из папки проекта, .env настроен, сессия авторизована):

    python refetch_texts.py 2026-07
    python refetch_texts.py 2026-06 2026-07 2026-08     # несколько месяцев сразу
    python refetch_texts.py                              # без аргументов — текущий месяц
"""

import asyncio
import sys
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, CHANNELS, get_telethon_kwargs
import historical


async def main(months: list[str]):
    kwargs = get_telethon_kwargs()
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        for ym in months:
            print(f"\n=== {ym} ===")
            for ch in CHANNELS:
                posts, subs = await historical.fetch_and_cache_month(
                    client, ch, ym, force=True)
                with_text = sum(1 for p in posts if p.get("message"))
                print(f"  {ch}: {len(posts)} постов собрано заново, "
                      f"из них с текстом: {with_text}")
    print("\nГотово. historical/<ym>.json пересобраны с текстом постов.")


if __name__ == "__main__":
    months = sys.argv[1:] or [date.today().strftime("%Y-%m")]
    asyncio.run(main(months))
