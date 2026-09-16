"""
audit_poll_votes.py — сканирует ВСЕ посты за месяц, находит те, что
Telegram классифицирует как "Опрос", и показывает по каждому:
  - сколько голосов зафиксировано в 24ч-срезе (registry.json/archive) —
    то, что попадает в отчёт;
  - сколько голосов реально в historical-кэше (более поздний сбор,
    если отчёт/дашборд для этого месяца собирался позже);
  - опционально — сколько голосов ПРЯМО СЕЙЧАС, живым запросом к Telegram.

ЗАЧЕМ: один пост (check_post.py) показывает конкретный случай. Этот
скрипт — чтобы понять МАСШТАБ: это единичные случаи "голосование
продолжилось после 24ч", или голоса не считаются вообще ни у одного
опроса (тогда дело не во времени, а в самом извлечении данных).

ЗАПУСК:
    python audit_poll_votes.py 2026-07 2026-08
    python audit_poll_votes.py 2026-08 --channel @metaclass
    python audit_poll_votes.py 2026-08 --live     # + живой запрос к Telegram по каждому опросу
"""

import asyncio
import sys
from calendar import monthrange
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, CHANNELS, get_telethon_kwargs
import historical
from registry_manager import get_final_posts_for_period


async def audit_month(client, channel: str, ym: str, live: bool):
    year, month = int(ym[:4]), int(ym[5:7])
    d_from = date(year, month, 1)
    d_to   = date(year, month, monthrange(year, month)[1])

    # Полный список из Telegram (то, что видно на момент сбора historical)
    telegram_posts, _ = await historical.get_posts_for_period(
        client, channel, d_from, d_to, force=False)
    polls = [p for p in telegram_posts if p.get("content_type") == "Опрос"]
    if not polls:
        return []

    # Финальные 24ч-срезы (то, что реально попадёт в отчёт)
    finalized = get_final_posts_for_period(channel, d_from, d_to)

    rows = []
    for p in polls:
        mid = str(p["msg_id"])
        hist_votes = (p.get("snapshot") or {}).get("votes", 0)
        final_entry = finalized.get(mid)
        final_votes = (final_entry.get("snapshot") or {}).get("votes") if final_entry else None

        live_votes = None
        if live:
            try:
                entity = await client.get_entity(channel)
                msg = await client.get_messages(entity, ids=int(mid))
                if msg and msg.media and hasattr(msg.media, "results") and msg.media.results and msg.media.results.results:
                    live_votes = sum(r.voters or 0 for r in msg.media.results.results)
                else:
                    live_votes = 0
            except Exception as e:
                live_votes = f"ошибка: {e}"

        rows.append({
            "msg_id": mid,
            "date": p.get("date"),
            "time": p.get("time"),
            "url": p.get("url"),
            "text": (p.get("message") or "")[:50],
            "hist_votes": hist_votes,
            "final_votes": final_votes,
            "live_votes": live_votes,
        })
    return rows


async def main(months: list, only_channel: str = None, live: bool = False):
    channels = [only_channel] if only_channel else CHANNELS
    kwargs = get_telethon_kwargs()

    total_polls = 0
    zero_everywhere = 0
    only_stale_final = 0

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        for ym in months:
            print(f"\n=== {ym} ===")
            for ch in channels:
                rows = await audit_month(client, ch, ym, live)
                if not rows:
                    continue
                for r in rows:
                    total_polls += 1
                    print(f"\n  {ch} {r['date']} {r['time']}  {r['url']}")
                    print(f"    текст: {r['text']!r}")
                    print(f"    голосов в historical (сбор напрямую из Telegram): {r['hist_votes']}")
                    print(f"    голосов в 24ч-срезе (то, что видит отчёт):        {r['final_votes']}")
                    if live:
                        print(f"    голосов ПРЯМО СЕЙЧАС (живой запрос):              {r['live_votes']}")

                    if r["hist_votes"] == 0 and (live is False or r["live_votes"] in (0, None)):
                        zero_everywhere += 1
                        print("    -> 0 ВЕЗДЕ, включая свежий сбор — похоже на реальную проблему извлечения, а не на тайминг")
                    elif r["final_votes"] is not None and r["final_votes"] < r["hist_votes"]:
                        only_stale_final += 1
                        print("    -> в 24ч-срезе МЕНЬШЕ, чем в свежем сборе — голосование продолжалось после фиксации")

    print("\n\n=== ИТОГО ===")
    print(f"Опросов найдено: {total_polls}")
    print(f"0 голосов везде (включая свежий/живой сбор): {zero_everywhere}")
    print(f"24ч-срез устарел (голоса пришли позже):        {only_stale_final}")
    if total_polls:
        if zero_everywhere == total_polls:
            print("\nВЫВОД: голоса не видны НИГДЕ, даже при свежем сборе из Telegram — "
                  "это не про тайминг 24ч-среза, дело в самом извлечении данных. "
                  "Пришлите этот вывод — разберу код извлечения предметно.")
        elif zero_everywhere == 0 and only_stale_final > 0:
            print("\nВЫВОД: данные извлекаются корректно, но 24ч-срез фиксируется раньше, "
                  "чем завершается голосование. Это архитектурная особенность (см. предыдущее "
                  "объяснение), не баг извлечения.")
        else:
            print("\nВЫВОД: смешанная картина — часть опросов страдает от тайминга, "
                  "часть — от чего-то ещё. Смотрите построчно выше.")


if __name__ == "__main__":
    args = sys.argv[1:]
    only_channel = None
    live = False
    if "--channel" in args:
        idx = args.index("--channel")
        only_channel = args[idx + 1]
        del args[idx:idx + 2]
    if "--live" in args:
        live = True
        args.remove("--live")

    months = args
    if not months:
        print("Укажите хотя бы один месяц, например:")
        print("  python audit_poll_votes.py 2026-08")
        sys.exit(1)

    asyncio.run(main(months, only_channel, live))
