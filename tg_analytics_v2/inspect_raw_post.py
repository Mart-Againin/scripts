"""
inspect_raw_post.py — печатает СЫРУЮ структуру сообщения(й) из Telegram,
без какой-либо обработки нашим кодом — чтобы понять, что Telegram
реально возвращает для постов вида "картинка + текст + опрос", и
почему detect_content_type() помечает их как "Другое" вместо "Опрос".

На каждом шаге печатает, что делает ПРЯМО СЕЙЧАС, и ограничивает шаг
таймаутом — если скрипт "висит", вы сразу увидите, на каком именно
шаге, а не будете гадать глядя на пустой экран.

ЗАПУСК:
    python inspect_raw_post.py @metaclass 428
"""

import asyncio
import sys

from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME, get_telethon_kwargs

STEP_TIMEOUT = 25  # секунд на каждый отдельный шаг


async def _step(label: str, coro):
    """Печатает, что делаем, засекает время, ограничивает таймаутом."""
    print(f"... {label}")
    try:
        result = await asyncio.wait_for(coro, timeout=STEP_TIMEOUT)
        print(f"    готово: {label}")
        return result
    except asyncio.TimeoutError:
        print(f"    !!! ЗАВИСЛО на шаге «{label}» — не ответило за {STEP_TIMEOUT} сек.")
        raise
    except Exception as e:
        print(f"    !!! ОШИБКА на шаге «{label}»: {type(e).__name__}: {e}")
        raise


async def main(channel: str, msg_id: str):
    # ВАЖНО: использует тот же файл сессии, что и main.py (бот). Если
    # main.py сейчас запущен — ОБЯЗАТЕЛЬНО остановите его (Ctrl+C в его
    # окне) перед запуском этого скрипта, иначе два процесса будут
    # конфликтовать за один SQLite-файл сессии.
    kwargs = get_telethon_kwargs()
    print(f"Файл сессии: {SESSION_NAME}")
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs)

    try:
        await _step("подключаюсь к Telegram (client.connect)", client.connect())

        authorized = await _step("проверяю авторизацию", client.is_user_authorized())
        print(f"    авторизован: {authorized}")
        if not authorized:
            print("!!! Сессия НЕ авторизована. Диагностировать нечего — "
                  "нужно сначала авторизоваться (например, запустив main.py один раз).")
            return

        entity = await _step(f"получаю сущность канала {channel}", client.get_entity(channel))
        print(f"    id канала: {entity.id}")

        msg = await _step(f"получаю сообщение {msg_id}", client.get_messages(entity, ids=int(msg_id)))
        if msg is None:
            print(f"Сообщение {msg_id} не найдено (удалено?)")
            return

        print()
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
        print("--- Соседние сообщения (id ±6) ---")

        async def _collect_neighbors():
            out = []
            async for m in client.iter_messages(entity, min_id=int(msg_id) - 6, max_id=int(msg_id) + 6, limit=20, reverse=True):
                out.append(m)
            return out

        neighbors = await _step("собираю соседние сообщения", _collect_neighbors())
        for m in neighbors:
            marker = "  <-- ЗАПРОШЕННОЕ" if m.id == int(msg_id) else ""
            mtype = type(m.media).__name__ if m.media else "нет media"
            text_preview = (m.message or "")[:40]
            print(f"  id={m.id}  grouped_id={m.grouped_id}  media={mtype}  text={text_preview!r}{marker}")

    finally:
        print("... отключаюсь")
        await client.disconnect()
        print("готово, скрипт завершён")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python inspect_raw_post.py @channel msg_id")
        print("Например:      python inspect_raw_post.py @metaclass 428")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
