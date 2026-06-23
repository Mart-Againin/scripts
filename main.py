"""
main.py — единая точка запуска TG Analytics.

Просто запустите этот файл:
    python main.py

При первом запуске проведёт авторизацию в Telegram.
Дальше работает сам: собирает статистику каждый час,
отправляет отчёты по расписанию.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

# ── Конфиг ────────────────────────────────────────────────────────────────
API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_analytics")
DEBUG_MODE   = os.getenv("DEBUG", "false").lower() == "true"
TZ           = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))
DAILY_TIME   = os.getenv("DAILY_REPORT_TIME", "12:00")   # только при DEBUG=true
WEEKLY_DAY   = 0   # понедельник
MONTHLY_DAY  = 3   # 3-е число

LOGS_DIR = Path(os.getenv("LOGS_DIR", "logs"))
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "main.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── Авторизация ───────────────────────────────────────────────────────────

async def authorize(client: TelegramClient) -> bool:
    """Проверяет сессию. Если нет — проводит авторизацию через консоль."""
    if await client.is_user_authorized():
        me = await client.get_me()
        log.info(f"Сессия активна: {me.first_name} (@{me.username}) ID={me.id}")
        return True

    print()
    print("=" * 55)
    print("  ПЕРВЫЙ ЗАПУСК — необходима авторизация в Telegram")
    print("=" * 55)
    print()

    phone = input("  Введите номер телефона (например +79001234567): ").strip()
    await client.send_code_request(phone)
    code = input("  Введите код из Telegram: ").strip()

    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        password = input("  Введите пароль двухфакторной аутентификации: ").strip()
        await client.sign_in(password=password)

    me = await client.get_me()
    print()
    print(f"  ✅ Авторизован: {me.first_name} (@{me.username})")
    print(f"  Ваш Telegram ID: {me.id}")
    print()

    # Подсказка: если REPORT_RECIPIENT_ID не заполнен
    rid = os.getenv("REPORT_RECIPIENT_ID", "0")
    if rid == "0":
        print(f"  ⚠️  Вставьте ваш ID ({me.id}) в .env → REPORT_RECIPIENT_ID")
        input("  Нажмите Enter после сохранения .env...")
        load_dotenv(override=True)

    print("=" * 55)
    print()
    return True


# ── Расписание ────────────────────────────────────────────────────────────

def now_local() -> datetime:
    return datetime.now(TZ)


def should_run_daily_report() -> bool:
    """Суточный отчёт — каждый день в DAILY_TIME (только при DEBUG=true)."""
    if not DEBUG_MODE:
        return False
    h, m = map(int, DAILY_TIME.split(":"))
    now = now_local()
    return now.hour == h and now.minute < 60


def should_run_weekly_report() -> bool:
    """Недельный отчёт — каждый понедельник в 10:00."""
    now = now_local()
    return now.weekday() == WEEKLY_DAY and now.hour == 10 and now.minute < 60


def should_run_monthly_report() -> bool:
    """Месячный отчёт — каждое 3-е число в 10:00."""
    now = now_local()
    return now.day == MONTHLY_DAY and now.hour == 10 and now.minute < 60


# ── Импорт рабочих модулей ────────────────────────────────────────────────

def import_snapshot():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "snapshot", Path(__file__).parent / "snapshot.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def import_report():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "report", Path(__file__).parent / "report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Главный цикл ──────────────────────────────────────────────────────────

# Флаги — чтобы не запускать одно и то же задание дважды в один час
_last_snapshot  = None   # datetime последнего запуска сборщика
_last_daily     = None   # date последнего суточного отчёта
_last_weekly    = None   # date последнего недельного отчёта
_last_monthly   = None   # (year, month) последнего месячного отчёта


async def tick(client: TelegramClient):
    """Один «тик» планировщика — выполняется каждую минуту."""
    global _last_snapshot, _last_daily, _last_weekly, _last_monthly

    now  = now_local()
    today = now.date()

    # ── Сборщик: каждый час ───────────────────────────────────────────────
    if _last_snapshot is None or (now - _last_snapshot).total_seconds() >= 3600:
        log.info("▶ Запуск сборщика (snapshot)...")
        try:
            snap = import_snapshot()
            channels = [c.strip() for c in os.getenv("CHANNELS","").split(",") if c.strip()]
            for ch in channels:
                await snap.process_channel(client, ch)
            _last_snapshot = now
            log.info("✓ Сборщик завершён")
        except Exception as e:
            log.error(f"Ошибка сборщика: {e}", exc_info=DEBUG_MODE)

    # ── Суточный отчёт (только при DEBUG=true) ────────────────────────────
    if should_run_daily_report() and _last_daily != today:
        log.info("▶ Запуск суточного отчёта...")
        try:
            rep = import_report()
            await rep.build_and_send("daily", debug_override=DEBUG_MODE)
            _last_daily = today
            log.info("✓ Суточный отчёт отправлен")
        except Exception as e:
            log.error(f"Ошибка суточного отчёта: {e}", exc_info=DEBUG_MODE)

    # ── Недельный отчёт (каждый понедельник) ──────────────────────────────
    if should_run_weekly_report() and _last_weekly != today:
        log.info("▶ Запуск недельного отчёта...")
        try:
            rep = import_report()
            await rep.build_and_send("weekly")
            _last_weekly = today
            log.info("✓ Недельный отчёт отправлен")
        except Exception as e:
            log.error(f"Ошибка недельного отчёта: {e}", exc_info=DEBUG_MODE)

    # ── Месячный отчёт (3-е число) ────────────────────────────────────────
    ym = (now.year, now.month)
    if should_run_monthly_report() and _last_monthly != ym:
        log.info("▶ Запуск месячного отчёта...")
        try:
            rep = import_report()
            await rep.build_and_send("monthly")
            _last_monthly = ym
            log.info("✓ Месячный отчёт отправлен")
        except Exception as e:
            log.error(f"Ошибка месячного отчёта: {e}", exc_info=DEBUG_MODE)


async def main():
    # Проверка конфига
    if not API_ID or not API_HASH:
        print()
        print("  ❌ Не заполнен .env файл.")
        print("  Скопируйте .env.example → .env и заполните API_ID и API_HASH.")
        print("  Инструкция: https://my.telegram.org → API development tools")
        print()
        input("  Нажмите Enter для выхода...")
        sys.exit(1)

    channels = [c.strip() for c in os.getenv("CHANNELS","").split(",") if c.strip()]
    if not channels:
        print()
        print("  ❌ Не указаны каналы в .env (параметр CHANNELS).")
        print()
        input("  Нажмите Enter для выхода...")
        sys.exit(1)

    proxy_cfg = None
    if os.getenv("PROXY_TYPE"):
        import socks
        _pt = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
        proxy_cfg = (
            _pt.get(os.getenv("PROXY_TYPE","socks5").lower(), socks.SOCKS5),
            os.getenv("PROXY_HOST"), int(os.getenv("PROXY_PORT","1080")),
            True,
            os.getenv("PROXY_USERNAME") or None,
            os.getenv("PROXY_PASSWORD") or None,
        )

    kwargs = {"proxy": proxy_cfg} if proxy_cfg else {}

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║           TG Analytics — запуск                 ║")
    print(f"║  Каналы: {', '.join(channels)[:38]:<38}║")
    print(f"║  Режим:  {'DEBUG (тестовый)' if DEBUG_MODE else 'Рабочий':<38}║")
    print(f"║  Часовой пояс: {os.getenv('TIMEZONE','Europe/Moscow'):<32}║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    print("  Для остановки нажмите Ctrl+C")
    print()

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        # Авторизация
        await authorize(client)

        log.info("Планировщик запущен. Сборщик работает каждый час.")
        if DEBUG_MODE:
            log.info(f"DEBUG режим: суточный отчёт каждый день в {DAILY_TIME} МСК → на DEBUG_RECIPIENT_ID")

        # Главный цикл — проверка каждую минуту
        while True:
            try:
                await tick(client)
            except Exception as e:
                log.error(f"Ошибка в tick: {e}", exc_info=DEBUG_MODE)
            await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("  Остановлено. До свидания.")
        print()
