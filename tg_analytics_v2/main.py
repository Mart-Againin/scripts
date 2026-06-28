"""
main.py — единая точка запуска TG Analytics.

Просто запустите этот файл:
    python main.py

При первом запуске проведёт авторизацию в Telegram.
Дальше работает сам: собирает статистику каждый час,
отправляет отчёты по расписанию.

Команды в Telegram (писать юзерботу или получателю отчётов):
    /report daily    — суточный отчёт прямо сейчас
    /report weekly   — недельный отчёт прямо сейчас
    /report monthly  — месячный отчёт (спросит за какой месяц)
"""

import asyncio
import importlib.util
import logging
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

load_dotenv()

# ── Конфиг ────────────────────────────────────────────────────────────────
API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_analytics")
DEBUG_MODE   = os.getenv("DEBUG", "false").lower() == "true"
TZ           = pytz.timezone(os.getenv("TIMEZONE", "Europe/Moscow"))
DAILY_TIME   = os.getenv("DAILY_REPORT_TIME", "12:00")
WEEKLY_DAY   = 0   # понедельник
MONTHLY_DAY  = 3   # 3-е число

# Получатели: поддерживаем несколько через запятую
def _parse_ids(key: str) -> list[int]:
    raw = os.getenv(key, "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]

RECIPIENT_IDS  = _parse_ids("REPORT_RECIPIENT_ID")
DEBUG_IDS      = _parse_ids("DEBUG_RECIPIENT_ID")

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

# Кто может отдавать команды — объединяем оба списка
ALLOWED_SENDERS = set(RECIPIENT_IDS + DEBUG_IDS)

# Состояние диалога для команды /report monthly
# { sender_id: "awaiting_month" }
_dialog_state: dict[int, str] = {}


# ── Импорт модулей ────────────────────────────────────────────────────────

def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Авторизация ───────────────────────────────────────────────────────────

async def authorize(client: TelegramClient) -> bool:
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

    if not RECIPIENT_IDS:
        print(f"  ⚠️  Вставьте ваш ID ({me.id}) в .env → REPORT_RECIPIENT_ID")
        input("  Нажмите Enter после сохранения .env...")
        load_dotenv(override=True)

    print("=" * 55)
    print()
    return True


# ── Отправка отчёта ───────────────────────────────────────────────────────

async def run_report(client: TelegramClient, report_type: str,
                     month_str: str = None, force_debug: bool = False):
    """
    Запускает генерацию отчёта и отправляет результат.
    month_str — только для monthly, формат 'MM' (например '05').
    """
    rep = _load_module("report")

    # Для месячного с указанным месяцем — переопределяем период
    if report_type == "monthly" and month_str:
        year = datetime.now(TZ).year
        # Если указанный месяц >= текущего — берём прошлый год
        if int(month_str) >= datetime.now(TZ).month:
            year -= 1
        ym = f"{year}-{month_str}"
        await rep.build_and_send("monthly", debug_override=force_debug, month_override=ym)
    else:
        await rep.build_and_send(report_type, debug_override=force_debug)


async def send_to_all(client: TelegramClient, text: str, is_debug: bool = False):
    """Отправляет сообщение всем получателям."""
    targets = DEBUG_IDS if (is_debug or DEBUG_MODE) else RECIPIENT_IDS
    for uid in targets:
        try:
            await client.send_message(uid, text)
        except Exception as e:
            log.error(f"Не удалось отправить сообщение {uid}: {e}")


# ── Обработчик команд из Telegram ─────────────────────────────────────────

def register_command_handler(client: TelegramClient):

    @client.on(events.NewMessage(pattern=r"^/report(\s+\S+)?$", incoming=True))
    async def handle_report_command(event):
        sender_id = event.sender_id
        if sender_id not in ALLOWED_SENDERS:
            log.warning(f"Команда от неизвестного отправителя {sender_id} — игнорируем")
            return

        args = (event.pattern_match.group(1) or "").strip().lower()
        log.info(f"Команда /report {args!r} от {sender_id}")

        if args == "daily":
            await event.reply("⏳ Генерирую суточный отчёт...")
            try:
                await run_report(client, "daily", force_debug=DEBUG_MODE)
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")
                log.error(f"Ошибка /report daily: {e}", exc_info=DEBUG_MODE)

        elif args == "weekly":
            await event.reply("⏳ Генерирую недельный отчёт...")
            try:
                await run_report(client, "weekly", force_debug=DEBUG_MODE)
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")
                log.error(f"Ошибка /report weekly: {e}", exc_info=DEBUG_MODE)

        elif args == "monthly":
            # Начинаем диалог — спрашиваем месяц
            _dialog_state[sender_id] = "awaiting_month"
            await event.reply(
                "📅 За какой месяц сформировать отчёт?\n"
                "Введите двузначный номер месяца, например:\n"
                "05 — май\n"
                "11 — ноябрь"
            )

        else:
            await event.reply(
                "📊 Доступные команды:\n"
                "/report daily — суточный отчёт\n"
                "/report weekly — недельный отчёт\n"
                "/report monthly — месячный отчёт (спросит период)"
            )

    @client.on(events.NewMessage(incoming=True))
    async def handle_dialog(event):
        sender_id = event.sender_id
        if sender_id not in ALLOWED_SENDERS:
            return
        if _dialog_state.get(sender_id) != "awaiting_month":
            return

        text = event.raw_text.strip()

        # Валидация: двузначное число от 01 до 12
        if not text.isdigit() or not (1 <= int(text) <= 12):
            await event.reply(
                "⚠️ Неверный формат. Введите двузначный номер месяца от 01 до 12.\n"
                "Например: 05"
            )
            return

        month_str = text.zfill(2)  # '5' → '05'
        del _dialog_state[sender_id]

        year = datetime.now(TZ).year
        if int(month_str) >= datetime.now(TZ).month:
            year -= 1

        month_names = {
            "01":"январь","02":"февраль","03":"март","04":"апрель",
            "05":"май","06":"июнь","07":"июль","08":"август",
            "09":"сентябрь","10":"октябрь","11":"ноябрь","12":"декабрь",
        }
        month_name = month_names.get(month_str, month_str)

        await event.reply(f"⏳ Генерирую месячный отчёт за {month_name} {year}...")
        try:
            await run_report(client, "monthly", month_str=month_str, force_debug=DEBUG_MODE)
        except Exception as e:
            await event.reply(f"❌ Ошибка: {e}")
            log.error(f"Ошибка /report monthly {month_str}: {e}", exc_info=DEBUG_MODE)


# ── Расписание ────────────────────────────────────────────────────────────

def should_run_daily_report() -> bool:
    if not DEBUG_MODE:
        return False
    h, m = map(int, DAILY_TIME.split(":"))
    now = datetime.now(TZ)
    return now.hour == h and now.minute < 60


def should_run_weekly_report() -> bool:
    now = datetime.now(TZ)
    return now.weekday() == WEEKLY_DAY and now.hour == 10 and now.minute < 60


def should_run_monthly_report() -> bool:
    now = datetime.now(TZ)
    return now.day == MONTHLY_DAY and now.hour == 10 and now.minute < 60


# Флаги против двойного запуска
_last_snapshot = None
_last_daily    = None
_last_weekly   = None
_last_monthly  = None


async def tick(client: TelegramClient):
    global _last_snapshot, _last_daily, _last_weekly, _last_monthly

    now   = datetime.now(TZ)
    today = now.date()

    # ── Сборщик: каждый час ───────────────────────────────────────────────
    if _last_snapshot is None or (now - _last_snapshot).total_seconds() >= 3600:
        log.info("▶ Запуск сборщика...")
        try:
            snap = _load_module("snapshot")
            channels = [c.strip() for c in os.getenv("CHANNELS","").split(",") if c.strip()]
            for ch in channels:
                await snap.process_channel(client, ch)
            _last_snapshot = now
            log.info("✓ Сборщик завершён")
        except Exception as e:
            log.error(f"Ошибка сборщика: {e}", exc_info=DEBUG_MODE)

    # ── Суточный (только DEBUG) ───────────────────────────────────────────
    if should_run_daily_report() and _last_daily != today:
        log.info("▶ Суточный отчёт по расписанию...")
        try:
            await run_report(client, "daily", force_debug=True)
            _last_daily = today
            log.info("✓ Суточный отчёт отправлен")
        except Exception as e:
            log.error(f"Ошибка суточного отчёта: {e}", exc_info=DEBUG_MODE)

    # ── Недельный (каждый пн) ─────────────────────────────────────────────
    if should_run_weekly_report() and _last_weekly != today:
        log.info("▶ Недельный отчёт по расписанию...")
        try:
            await run_report(client, "weekly")
            _last_weekly = today
            log.info("✓ Недельный отчёт отправлен")
        except Exception as e:
            log.error(f"Ошибка недельного отчёта: {e}", exc_info=DEBUG_MODE)

    # ── Месячный (3-е число) ──────────────────────────────────────────────
    ym = (now.year, now.month)
    if should_run_monthly_report() and _last_monthly != ym:
        log.info("▶ Месячный отчёт по расписанию...")
        try:
            await run_report(client, "monthly")
            _last_monthly = ym
            log.info("✓ Месячный отчёт отправлен")
        except Exception as e:
            log.error(f"Ошибка месячного отчёта: {e}", exc_info=DEBUG_MODE)


# ── Точка входа ───────────────────────────────────────────────────────────

async def main():
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

    recipients_display = ", ".join(str(i) for i in (DEBUG_IDS if DEBUG_MODE else RECIPIENT_IDS))

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║           TG Analytics — запуск                 ║")
    print(f"║  Каналы:      {', '.join(channels)[:34]:<34}║")
    print(f"║  Режим:       {'DEBUG (тестовый)' if DEBUG_MODE else 'Рабочий':<34}║")
    print(f"║  Получатели:  {recipients_display[:34]:<34}║")
    print(f"║  Часовой пояс: {os.getenv('TIMEZONE','Europe/Moscow'):<33}║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    print("  Команды (писать в Telegram этому аккаунту или получателю):")
    print("  /report daily   — суточный отчёт прямо сейчас")
    print("  /report weekly  — недельный отчёт прямо сейчас")
    print("  /report monthly — месячный отчёт (спросит период)")
    print()
    print("  Для остановки нажмите Ctrl+C")
    print()

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs) as client:
        await authorize(client)
        register_command_handler(client)

        log.info("Планировщик и обработчик команд запущены.")
        if DEBUG_MODE:
            log.info(f"DEBUG: суточный отчёт в {DAILY_TIME} МСК → {DEBUG_IDS}")
        if RECIPIENT_IDS:
            log.info(f"Получатели отчётов: {RECIPIENT_IDS}")

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
