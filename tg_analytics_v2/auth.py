"""
auth.py — авторизация через QR-код или номер телефона.
Запускайте отдельно если нужна первичная авторизация: python auth.py
"""
import asyncio
import os
import sys
from pathlib import Path
import qrcode
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_analytics")


def _print_qr(url: str):
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


async def authorize():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Уже авторизован: {me.first_name} (@{me.username}) ID={me.id}")
        await client.disconnect()
        return
    print("Выберите способ авторизации:\n1 — QR-код\n2 — Номер телефона")
    choice = input("Ваш выбор: ").strip()
    if choice == "1":
        async with client.qr_login() as qr:
            _print_qr(qr.url)
            print("Отсканируйте QR в Telegram: Настройки → Устройства → Подключить")
            await qr.wait()
    else:
        phone = input("Номер телефона (с +): ").strip()
        await client.send_code_request(phone)
        code = input("Код из Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            pwd = input("Пароль двухфакторной аутентификации: ").strip()
            await client.sign_in(password=pwd)
    me = await client.get_me()
    print(f"Авторизация успешна: {me.first_name} (@{me.username}) ID={me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(authorize())
