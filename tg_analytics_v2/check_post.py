"""
check_post.py — точечная диагностика одного поста: что о нём знает
скрипт прямо сейчас (тип контента, финализирован ли, что в снимке
статистики) — без гаданий, по фактическим данным на диске/в Telegram.

ЗАПУСК:
    python check_post.py @metaclass 428

Проверяет:
  1. registry.json — зарегистрирован ли пост вообще, финализирован ли
     (is_final), что в снимке (включая votes), когда снят финальный срез.
  2. archive/ — если пост уже старше 90 дней и мог переехать туда.
  3. historical-кэш — как Telegram/Telethon классифицировал тип контента
     (content_type) на момент последнего сбора этого месяца — если там
     "Опрос", значит это точно настоящий Telegram-poll.
  4. Живой запрос к Telegram (опционально) — текущее состояние голосов
     ПРЯМО СЕЙЧАС, чтобы сравнить с тем, что зафиксировано в отчёте.
"""

import asyncio
import sys
from datetime import date

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, get_telethon_kwargs
from registry_manager import load_registry, load_archive_month


async def main(channel: str, msg_id: str):
    print(f"=== Диагностика поста {channel}/{msg_id} ===\n")

    # 1. registry.json
    reg = load_registry(channel)
    post = reg.get("posts", {}).get(msg_id)
    if post:
        print("В registry.json: НАЙДЕН")
        print(f"  is_final:      {post.get('is_final')}")
        print(f"  finalized_at:  {post.get('finalized_at', '—')}")
        print(f"  content_type:  {post.get('content_type')}")
        print(f"  message:       {post.get('message', '')[:80]!r}")
        print(f"  snapshot:      {post.get('snapshot')}")
    else:
        print("В registry.json: НЕ найден (возможно, уже в archive/, или не был зарегистрирован никогда)")

    # 2. archive/ — проверяем последние ~4 месяца на всякий случай
    print()
    today = date.today()
    y, m = today.year, today.month
    for _ in range(4):
        arch_posts = load_archive_month(channel, f"{y}-{m:02d}")
        if msg_id in arch_posts:
            ap = arch_posts[msg_id]
            print(f"В archive/{y}-{m:02d}.json: НАЙДЕН")
            print(f"  snapshot: {ap.get('snapshot')}")
            break
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    else:
        print("В archive/ (последние 4 месяца): не найден")

    # 3. historical-кэш — что там записано на момент последнего сбора
    print()
    # ищем среди всех кэшированных месяцев этого канала
    from config import REGISTRY_DIR
    hist_dir = REGISTRY_DIR / channel.lstrip("@") / "historical"
    found_in_hist = False
    if hist_dir.exists():
        import json
        for f in sorted(hist_dir.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            for p in data.get("posts", []):
                if str(p.get("msg_id")) == str(msg_id):
                    found_in_hist = True
                    print(f"В historical/{f.name}: НАЙДЕН")
                    print(f"  content_type: {p.get('content_type')}")
                    print(f"  message:      {p.get('message', '')[:80]!r}")
                    print(f"  snapshot:     {p.get('snapshot')}")
                    if p.get("content_type") == "Опрос":
                        print("  -> Telegram подтверждает: это НАСТОЯЩИЙ опрос (MessageMediaPoll)")
                    else:
                        print(f"  -> Это НЕ опрос в терминах Telegram API (тип: {p.get('content_type')}) — "
                              f"голоса и не должны были считаться, это не баг")
    if not found_in_hist:
        print("В historical-кэше: не найден ни в одном закэшированном месяце")

    # 4. Живой запрос к Telegram — текущее состояние прямо сейчас
    print()
    answer = input("Сделать живой запрос к Telegram и посмотреть ТЕКУЩЕЕ состояние голосов? (да/нет): ").strip().lower()
    if answer in ("да", "yes", "y", "д"):
        kwargs = get_telethon_kwargs()
        async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
            entity = await client.get_entity(channel)
            msg = await client.get_messages(entity, ids=int(msg_id))
            if msg is None:
                print("Telegram: пост не найден (удалён?)")
                return
            print(f"Тип media: {type(msg.media).__name__ if msg.media else 'нет (текст)'}")
            if msg.media and hasattr(msg.media, "poll"):
                print(f"Это опрос: {msg.media.poll.question if hasattr(msg.media.poll, 'question') else '?'}")
                if msg.media.results and msg.media.results.results:
                    total_now = sum(r.voters or 0 for r in msg.media.results.results)
                    print(f"Голосов ПРЯМО СЕЙЧАС: {total_now}")
                    print("(сравните с тем, что зафиксировано в snapshot выше — "
                          "если сейчас больше, значит голосование продолжалось после 24ч-среза)")
                else:
                    print("Данных о результатах голосования нет вообще")
            else:
                print("Это не опрос (media не содержит poll) — подтверждает пункт 1: "
                      "нечего было засчитывать как 'Голоса'")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python check_post.py @channel msg_id")
        print("Например:      python check_post.py @metaclass 428")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
