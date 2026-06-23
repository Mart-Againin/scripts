"""
auth.py — первичная авторизация и создание сессии Telegram.

Запустить один раз перед первым использованием:
    python auth.py

Скрипт авторизуется в Telegram через номер телефона и сохраняет
сессию в файл SESSION_NAME.session — он используется всеми
остальными скриптами без повторного входа.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_analytics")


async def main():
    if not API_ID or not API_HASH:
        print("❌ API_ID и API_HASH не заданы в .env")
        print("   Получите их на https://my.telegram.org → API development tools")
        sys.exit(1)

    print(f"📱 Авторизация Telegram | Сессия: {SESSION_NAME}.session")
    print()

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Сессия уже активна: {me.first_name} (@{me.username}) ID={me.id}")
            print(f"   Файл сессии: {SESSION_NAME}.session")
            print()
            print("💡 Ваш Telegram ID (для REPORT_RECIPIENT_ID в .env):", me.id)
            return

        phone = input("Введите номер телефона (с кодом страны, например +79001234567): ").strip()
        await client.send_code_request(phone)

        code = input("Введите код из Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = input("Введите пароль двухфакторной аутентификации: ").strip()
            await client.sign_in(password=password)

        me = await client.get_me()
        print()
        print(f"✅ Успешно авторизован: {me.first_name} (@{me.username})")
        print(f"   Файл сессии: {SESSION_NAME}.session")
        print()
        print(f"💡 Ваш Telegram ID (для REPORT_RECIPIENT_ID в .env): {me.id}")


if __name__ == "__main__":
    asyncio.run(main())
