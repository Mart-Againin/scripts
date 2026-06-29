"""
auth.py — авторизация Telegram через QR-код или номер телефона.

Запустить один раз:
    python auth.py

Способ 1 (рекомендуется): QR-код
  — скрипт нарисует QR прямо в консоли
  — откройте Telegram → Настройки → Устройства → Подключить устройство
  — наведите камеру на QR-код в консоли

Способ 2: номер телефона + код из Telegram
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_analytics")


def print_qr_terminal(url: str):
    """Рисует QR-код прямо в консоли символами ASCII."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        # Печатаем в консоль
        qr.print_ascii(invert=True)
    except ImportError:
        # Если qrcode не установлен — просто показываем ссылку
        print(f"  QR URL: {url}")
        print("  (установите qrcode для отображения: pip install qrcode)")


async def auth_qr(client: TelegramClient) -> bool:
    """Авторизация через QR-код. Возвращает True если успешно."""
    print()
    print("  Способ 1: QR-код")
    print("  ─────────────────────────────────────────────")
    print("  1. Откройте Telegram на телефоне")
    print("  2. Настройки → Устройства → Подключить устройство")
    print("  3. Наведите камеру на QR-код ниже")
    print()

    try:
        qr_login = await client.qr_login()

        # Показываем QR
        print_qr_terminal(qr_login.url)
        print()
        print("  Ожидаю подтверждения", end="", flush=True)

        # Ждём пока пользователь отсканирует (до 60 сек, потом обновляем)
        while True:
            try:
                await qr_login.wait(timeout=20)
                print()
                print()
                # Проверяем 2FA
                if not await client.is_user_authorized():
                    return False
                me = await client.get_me()
                if me:
                    return True
                return False
            except asyncio.TimeoutError:
                # QR-код истёк — обновляем
                print(".", end="", flush=True)
                try:
                    await qr_login.recreate()
                    print()
                    print()
                    print("  QR-код обновлён — наведите камеру заново:")
                    print()
                    print_qr_terminal(qr_login.url)
                    print()
                    print("  Ожидаю подтверждения", end="", flush=True)
                except Exception:
                    print()
                    return False
            except Exception as e:
                print()
                if "2FA" in str(e) or "password" in str(e).lower():
                    return "need_2fa"
                print(f"  Ошибка QR: {e}")
                return False

    except Exception as e:
        print(f"\n  Ошибка запуска QR: {e}")
        return False


async def auth_phone(client: TelegramClient) -> bool:
    """Авторизация через номер телефона."""
    print()
    print("  Способ 2: номер телефона")
    print("  ─────────────────────────────────────────────")

    phone = input("  Введите номер телефона (+79001234567): ").strip()
    if not phone:
        return False

    try:
        await client.send_code_request(phone)
    except Exception as e:
        print(f"  ❌ Не удалось отправить код: {e}")
        return False

    code = input("  Введите код из Telegram: ").strip()
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        password = input("  Введите пароль двухфакторной аутентификации: ").strip()
        try:
            await client.sign_in(password=password)
        except Exception as e:
            print(f"  ❌ Неверный пароль: {e}")
            return False
    except Exception as e:
        print(f"  ❌ Ошибка входа: {e}")
        return False

    return True


async def main():
    if not API_ID or not API_HASH:
        print()
        print("  ❌ API_ID и API_HASH не заданы в .env")
        print("  Получите их на https://my.telegram.org → API development tools")
        print()
        input("  Нажмите Enter для выхода...")
        sys.exit(1)

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║        TG Analytics — авторизация               ║")
    print("╚══════════════════════════════════════════════════╝")

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:

        # Уже авторизован?
        if await client.is_user_authorized():
            me = await client.get_me()
            print()
            print(f"  ✅ Сессия уже активна!")
            print(f"  Аккаунт: {me.first_name} (@{me.username})")
            print(f"  ID: {me.id}")
            print()
            input("  Нажмите Enter для выхода...")
            return

        # Выбор способа
        print()
        print("  Выберите способ авторизации:")
        print("  1 — QR-код (рекомендуется, не нужен код из SMS)")
        print("  2 — Номер телефона + код из Telegram")
        print()
        choice = input("  Введите 1 или 2: ").strip()

        success = False

        if choice == "1":
            result = await auth_qr(client)
            if result == "need_2fa":
                print("  Требуется пароль двухфакторной аутентификации")
                password = input("  Введите пароль: ").strip()
                try:
                    await client.sign_in(password=password)
                    success = True
                except Exception as e:
                    print(f"  ❌ Неверный пароль: {e}")
            else:
                success = result
        elif choice == "2":
            success = await auth_phone(client)
        else:
            print("  ❌ Неверный выбор")
            sys.exit(1)

        if not success:
            print()
            print("  ❌ Авторизация не удалась")
            print("  Попробуйте ещё раз или выберите другой способ")
            input("  Нажмите Enter для выхода...")
            sys.exit(1)

        me = await client.get_me()
        print()
        print("  ╔════════════════════════════════════════╗")
        print(f"  ║  ✅ Авторизация успешна!              ║")
        print(f"  ║  Аккаунт: {me.first_name[:25]:<25}  ║")
        print(f"  ║  ID: {str(me.id):<35}║")
        print("  ╚════════════════════════════════════════╝")
        print()
        print(f"  Вставьте ваш ID в .env:")
        print(f"  REPORT_RECIPIENT_ID={me.id}")
        print()
        input("  Нажмите Enter для выхода...")


if __name__ == "__main__":
    asyncio.run(main())
