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
import calendar
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

# Состояние диалога
# { sender_id: {"state": "awaiting_month"|"awaiting_cache", "month_str": "06", "ym": "2026-06"} }
_dialog_state: dict = {}


# ── Импорт модулей ────────────────────────────────────────────────────────

def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Авторизация ───────────────────────────────────────────────────────────

def _print_qr(url: str):
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print(f"  QR URL: {url}")


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
    print("  Выберите способ:")
    print("  1 — QR-код (рекомендуется)")
    print("      Telegram → Настройки → Устройства → Подключить")
    print("  2 — Номер телефона + код из Telegram")
    print()
    choice = input("  Введите 1 или 2: ").strip()

    success = False

    if choice == "1":
        print()
        print("  Сканируйте QR-код в Telegram:")
        print("  (Настройки → Устройства → Подключить устройство)")
        print()
        try:
            qr_login = await client.qr_login()
            _print_qr(qr_login.url)
            print()
            print("  Ожидаю сканирования", end="", flush=True)

            while True:
                try:
                    await qr_login.wait(timeout=20)
                    print()
                    success = True
                    break
                except asyncio.TimeoutError:
                    print(".", end="", flush=True)
                    try:
                        await qr_login.recreate()
                        print()
                        print("  QR обновлён:")
                        _print_qr(qr_login.url)
                        print("  Ожидаю сканирования", end="", flush=True)
                    except Exception:
                        print()
                        break
                except Exception as e:
                    print()
                    if "password" in str(e).lower() or "2fa" in str(e).lower():
                        password = input("  Введите пароль 2FA: ").strip()
                        try:
                            await client.sign_in(password=password)
                            success = True
                        except Exception as e2:
                            log.error(f"Ошибка 2FA: {e2}")
                    else:
                        log.error(f"Ошибка QR: {e}")
                    break
        except Exception as e:
            log.error(f"QR недоступен: {e}")

    else:
        phone = input("  Введите номер телефона (+79001234567): ").strip()
        try:
            await client.send_code_request(phone)
            code = input("  Введите код из Telegram: ").strip()
            try:
                await client.sign_in(phone, code)
                success = True
            except SessionPasswordNeededError:
                password = input("  Введите пароль 2FA: ").strip()
                await client.sign_in(password=password)
                success = True
        except Exception as e:
            log.error(f"Ошибка авторизации: {e}")

    if not success:
        print()
        print("  ❌ Авторизация не удалась. Перезапустите скрипт.")
        sys.exit(1)

    me = await client.get_me()
    print()
    print(f"  ✅ Авторизован: {me.first_name} (@{me.username}) ID={me.id}")
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
                     force_debug: bool = False,
                     month_override: str = None,
                     week_offset: int = 1,
                     week_date=None,
                     force_rebuild: bool = False):
    """Запускает генерацию отчёта. Передаёт уже открытый клиент."""
    rep = _load_module("report")
    await rep.build_and_send(report_type, debug_override=force_debug,
                             month_override=month_override, tg_client=client,
                             week_offset=week_offset, week_date=week_date,
                             force_rebuild=force_rebuild)


async def send_cached_report(client: TelegramClient, ym: str, sender_id: int):
    """Отправляет уже готовый файл отчёта из кэша."""
    rep = _load_module("report")
    path = rep.get_cached_report_path(ym)
    if not path:
        await client.send_message(sender_id, "❌ Файл не найден, генерирую заново...")
        return False
    is_debug = DEBUG_MODE
    recipients = DEBUG_IDS if is_debug else RECIPIENT_IDS
    for uid in recipients:
        try:
            await client.send_file(uid, str(path), caption=f"📎 {path.name}")
            log.info(f"Отправлен кэш {path.name} → {uid}")
        except Exception as e:
            log.error(f"Ошибка отправки кэша → {uid}: {e}")
    return True


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
            from datetime import date as _date, timedelta as _td
            today = _date.today()
            mon1  = today - _td(days=today.weekday() + 7)
            sun1  = mon1  + _td(days=6)
            mon2  = mon1  - _td(days=7)
            sun2  = mon2  + _td(days=6)
            _dialog_state[sender_id] = {"state": "awaiting_week"}
            await event.reply(
                f"📆 За какую неделю сформировать отчёт?\n\n"
                f"1 — прошлая неделя ({mon1.strftime('%d.%m')} – {sun1.strftime('%d.%m')})\n"
                f"2 — позапрошлая ({mon2.strftime('%d.%m')} – {sun2.strftime('%d.%m')})\n"
                f"3 — указать вручную (любой день нужной недели)"
            )

        elif args == "monthly":
            _dialog_state[sender_id] = {"state": "awaiting_month"}
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

    @client.on(events.NewMessage(pattern=r"^/backfill$", incoming=True))
    async def handle_backfill(event):
        sender_id = event.sender_id
        if sender_id not in ALLOWED_SENDERS:
            return
        log.info(f"Команда /backfill от {sender_id}")
        _dialog_state[sender_id] = {"state": "awaiting_backfill_type"}
        await event.reply(
            "📥 Ретро-сбор исторических данных.\n"
            "Выберите период:\n\n"
            "1 — конкретный месяц\n"
            "2 — конкретная неделя\n"
            "3 — с января по текущий месяц"
        )

    @client.on(events.NewMessage())
    async def handle_dialog(event):
        sender_id = event.sender_id
        if sender_id is None or event.out:
            me = await client.get_me()
            sender_id = me.id

        if sender_id not in ALLOWED_SENDERS:
            return
        state_data = _dialog_state.get(sender_id)
        if not state_data:
            return
        if event.raw_text.strip().startswith("/"):
            return

        text  = event.raw_text.strip()
        state = state_data.get("state")

        MONTH_NAMES = {
            "01":"январь","02":"февраль","03":"март","04":"апрель",
            "05":"май","06":"июнь","07":"июль","08":"август",
            "09":"сентябрь","10":"октябрь","11":"ноябрь","12":"декабрь",
        }

        # ── Выбор недели ─────────────────────────────────────────────────
        if state == "awaiting_week":
            if text == "1":
                del _dialog_state[sender_id]
                await event.reply("⏳ Генерирую недельный отчёт за прошлую неделю...")
                try:
                    await run_report(client, "weekly", force_debug=DEBUG_MODE, week_offset=1)
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")

            elif text == "2":
                del _dialog_state[sender_id]
                await event.reply("⏳ Генерирую недельный отчёт за позапрошлую неделю...")
                try:
                    await run_report(client, "weekly", force_debug=DEBUG_MODE, week_offset=2)
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")

            elif text == "3":
                _dialog_state[sender_id] = {"state": "awaiting_week_date"}
                await event.reply(
                    "📅 Введите любой день нужной недели в формате ДД.ММ\n"
                    "Например: 15.05"
                )
            else:
                await event.reply("⚠️ Введите 1, 2 или 3")

        # ── Ввод даты для недели ─────────────────────────────────────────
        elif state == "awaiting_week_date":
            try:
                parts = text.split(".")
                if len(parts) == 2:
                    day, month = int(parts[0]), int(parts[1])
                    year = datetime.now(TZ).year
                    if month > datetime.now(TZ).month:
                        year -= 1
                    week_date = date(year, month, day)
                else:
                    raise ValueError()
            except (ValueError, IndexError):
                await event.reply("⚠️ Неверный формат. Введите дату в формате ДД.ММ, например: 15.05")
                return

            from datetime import timedelta as _td
            mon = week_date - _td(days=week_date.weekday())
            sun = mon + _td(days=6)
            del _dialog_state[sender_id]
            await event.reply(f"⏳ Генерирую недельный отчёт за {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')}...")
            try:
                await run_report(client, "weekly", force_debug=DEBUG_MODE, week_date=week_date)
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")

        # ── Ожидаем номер месяца ─────────────────────────────────────────
        elif state == "awaiting_month":
            if not text.isdigit() or not (1 <= int(text) <= 12):
                await event.reply("⚠️ Неверный формат. Введите номер месяца от 01 до 12.\nНапример: 05")
                return

            month_str  = text.zfill(2)
            year       = datetime.now(TZ).year
            if int(month_str) >= datetime.now(TZ).month:
                year -= 1
            ym         = f"{year}-{month_str}"
            month_name = MONTH_NAMES.get(month_str, month_str)

            rep         = _load_module("report")
            cached_info = rep.get_cached_report_info(ym)

            if cached_info:
                _dialog_state[sender_id] = {
                    "state": "awaiting_cache", "ym": ym,
                    "month_name": month_name, "year": year,
                }
                await event.reply(
                    f"📁 Отчёт за {month_name} {year} уже сформирован ({cached_info}).\n\n"
                    f"Что сделать?\n"
                    f"1 — отправить готовый\n"
                    f"2 — пересчитать заново"
                )
            else:
                del _dialog_state[sender_id]
                await event.reply(f"⏳ Генерирую месячный отчёт за {month_name} {year}...")
                try:
                    await run_report(client, "monthly", force_debug=DEBUG_MODE, month_override=ym)
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")

        # ── Готовый или пересчитать ───────────────────────────────────────
        elif state == "awaiting_cache":
            ym         = state_data["ym"]
            month_name = state_data["month_name"]
            year       = state_data["year"]

            if text == "1":
                del _dialog_state[sender_id]
                await event.reply(f"📤 Отправляю готовый отчёт за {month_name} {year}...")
                ok = await send_cached_report(client, ym, sender_id)
                if not ok:
                    await event.reply("⏳ Файл не найден, генерирую заново...")
                    try:
                        await run_report(client, "monthly", force_debug=DEBUG_MODE, month_override=ym)
                    except Exception as e:
                        await event.reply(f"❌ Ошибка: {e}")
            elif text == "2":
                del _dialog_state[sender_id]
                await event.reply(f"⏳ Пересчитываю отчёт за {month_name} {year}...")
                try:
                    await run_report(client, "monthly", force_debug=DEBUG_MODE, month_override=ym)
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")
            else:
                await event.reply("⚠️ Введите 1 (готовый) или 2 (пересчитать)")

        # ── Backfill: выбор типа периода ─────────────────────────────────
        elif state == "awaiting_backfill_type":
            if text == "1":
                _dialog_state[sender_id] = {"state": "awaiting_backfill_month"}
                await event.reply(
                    "📅 Введите номер месяца для ретро-сбора (формат ММ):\n"
                    "Например: 05 — май"
                )
            elif text == "2":
                _dialog_state[sender_id] = {"state": "awaiting_backfill_week"}
                await event.reply(
                    "📅 Введите любой день нужной недели (формат ДД.ММ):\n"
                    "Например: 15.05"
                )
            elif text == "3":
                del _dialog_state[sender_id]
                year = datetime.now(TZ).year
                await event.reply(f"⏳ Запускаю ретро-сбор с января по текущий месяц {year}...")
                try:
                    hist = _load_module("historical")
                    channels = [c.strip() for c in os.getenv("CHANNELS","").split(",") if c.strip()]
                    cur_month = datetime.now(TZ).month
                    for mo in range(1, cur_month):
                        ym = f"{year}-{mo:02d}"
                        await event.reply(f"  Собираю {ym}...")
                        for ch in channels:
                            await hist.fetch_and_cache_month(client, ch, ym, force=False)
                    await event.reply(f"✅ Ретро-сбор завершён. Данные за {year} доступны.")
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")
            else:
                await event.reply("⚠️ Введите 1, 2 или 3")

        # ── Backfill: конкретный месяц ────────────────────────────────────
        elif state == "awaiting_backfill_month":
            if not text.isdigit() or not (1 <= int(text) <= 12):
                await event.reply("⚠️ Неверный формат. Введите номер месяца от 01 до 12.")
                return
            month_str  = text.zfill(2)
            year       = datetime.now(TZ).year
            if int(month_str) >= datetime.now(TZ).month:
                year -= 1
            ym         = f"{year}-{month_str}"
            month_name = MONTH_NAMES.get(month_str, month_str)
            del _dialog_state[sender_id]
            await event.reply(f"⏳ Ретро-сбор за {month_name} {year}...")
            try:
                hist     = _load_module("historical")
                channels = [c.strip() for c in os.getenv("CHANNELS","").split(",") if c.strip()]
                for ch in channels:
                    await hist.fetch_and_cache_month(client, ch, ym, force=False)
                await event.reply(f"✅ Данные за {month_name} {year} сохранены в кэш.")
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")

        # ── Backfill: конкретная неделя ───────────────────────────────────
        elif state == "awaiting_backfill_week":
            try:
                parts = text.split(".")
                day, month = int(parts[0]), int(parts[1])
                year = datetime.now(TZ).year
                if month > datetime.now(TZ).month:
                    year -= 1
                week_date = date(year, month, day)
            except (ValueError, IndexError):
                await event.reply("⚠️ Неверный формат. Введите дату в формате ДД.ММ")
                return

            from datetime import timedelta as _td
            mon = week_date - _td(days=week_date.weekday())
            sun = mon + _td(days=6)
            del _dialog_state[sender_id]
            await event.reply(f"⏳ Ретро-сбор за {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')}...")
            try:
                hist     = _load_module("historical")
                channels = [c.strip() for c in os.getenv("CHANNELS","").split(",") if c.strip()]
                # Неделя может захватить два месяца
                months = set()
                d = mon
                while d <= sun:
                    months.add(d.strftime("%Y-%m"))
                    d += _td(days=1)
                for ym in sorted(months):
                    for ch in channels:
                        await hist.fetch_and_cache_month(client, ch, ym, force=False)
                await event.reply(f"✅ Данные за {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')} сохранены.")
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")


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

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH, **kwargs)
    await client.connect()
    try:
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
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("  Остановлено. До свидания.")
        print()
