"""
inspect_raw_post.py — печатает СЫРУЮ структуру сообщения(й) из Telegram,
без какой-либо обработки нашим кодом — чтобы понять, что Telegram
реально возвращает для постов вида "картинка + текст + опрос", и
почему detect_content_type() помечает их как "Другое" вместо "Опрос".

ЗАПУСК:
    python inspect_raw_post.py @metaclass 428
"""

import asyncio
import sys

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, get_telethon_kwargs


async def main(channel: str, msg_id: str):
    kwargs = get_telethon_kwargs()
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        entity = await client.get_entity(channel)
        msg = await client.get_messages(entity, ids=int(msg_id))
        if msg is None:
            print(f"Сообщение {msg_id} не найдено (удалено?)")
            return

        print(f"=== msg_id {msg.id} ===")
        print(f"date:        {msg.date}")
        print(f"grouped_id:  {msg.grouped_id}")
        print(f"message:     {msg.message!r}")
        print(f"media type:  {type(msg.media).__name__ if msg.media else None}")
        print()
        print("--- Полное содержимое msg.media ---")
        print(msg.media.stringify() if msg.media else "(нет media)")

        # Если сообщение — часть группы (альбома), смотрим соседей:
        # возможно, опрос лежит в другом msg_id рядом с этим.
        print()
        print("--- Соседние сообщения (id ±5) ---")
        async for m in client.iter_messages(entity, min_id=int(msg_id) - 6, max_id=int(msg_id) + 6, limit=20, reverse=True):
            marker = "  <-- ЗАПРОШЕННОЕ" if m.id == int(msg_id) else ""
            mtype = type(m.media).__name__ if m.media else "нет media"
            text_preview = (m.message or "")[:40]
            print(f"  id={m.id}  grouped_id={m.grouped_id}  media={mtype}  text={text_preview!r}{marker}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python inspect_raw_post.py @channel msg_id")
        print("Например:      python inspect_raw_post.py @metaclass 428")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
