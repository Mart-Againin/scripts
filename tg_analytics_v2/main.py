"""
main.py — единая точка запуска TG Analytics.

Просто запустите этот файл:
    python main.py

При первом запуске проведёт авторизацию в Telegram.
Дальше работает сам: собирает статистику каждый час,
отправляет отчёты по расписанию.

Команды в Telegram:
    /report daily      — суточный отчёт (лично вам)
    /report weekly     — недельный отчёт
    /report monthly    — месячный отчёт (спросит период)
    /report dashboard  — управленческий дашборд (PPTX)
    /upload monthly    — выгрузить месяц в Google Sheets
    /backfill          — ретро-сбор исторических данных
"""

import asyncio
import logging
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

from config import (
    API_ID, API_HASH, SESSION_NAME, CHANNELS,
    RECIPIENT_IDS, DEBUG_IDS, MODERATOR_IDS, DEBUG_MODE,
    DAILY_REPORT_TIME as DAILY_TIME, TZ, LOGS_DIR, get_telethon_kwargs,
    MONTHS_RU,
)
import report as rep
import snapshot as snap
import historical as hist
import stories as stories_mod
import dashboard_report as dash_rep

WEEKLY_DAY  = 0   # понедельник
MONTHLY_DAY = 3   # 3-е число

logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "main.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

ALLOWED_SENDERS = set(RECIPIENT_IDS + DEBUG_IDS + MODERATOR_IDS)
_dialog_state: dict = {}


# ── Авторизация ───────────────────────────────────────────────────────────

def _print_qr(url: str):
    try:
        import qrcode
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        print(f"QR URL: {url}")


async def authorize(client: TelegramClient):
    if await client.is_user_authorized():
        me = await client.get_me()
        log.info(f"Авторизован: {me.first_name} (@{me.username}) ID={me.id}")
        return
    print("\nВыберите способ авторизации:\n1 — QR-код\n2 — Номер телефона")
    choice = input("Ваш выбор: ").strip()
    if choice == "1":
        async with client.qr_login() as qr:
            _print_qr(qr.url)
            print("Отсканируйте QR: Telegram → Настройки → Устройства → Подключить")
            await qr.wait()
    else:
        phone = input("Номер телефона (с +): ").strip()
        await client.send_code_request(phone)
        code = input("Код из Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            pwd = input("Пароль 2FA: ").strip()
            await client.sign_in(password=pwd)
    me = await client.get_me()
    log.info(f"Авторизация успешна: {me.first_name} (@{me.username}) ID={me.id}")


# ── Запуск отчётов ────────────────────────────────────────────────────────

def _recipients_for_request(sender_id: int) -> list:
    """Любой отчёт по команде уходит только запросившему."""
    return [sender_id]


async def run_report(client: TelegramClient, report_type: str,
                     force_debug: bool = False,
                     month_override: str = None,
                     week_offset: int = 1,
                     week_date=None,
                     force_rebuild: bool = False,
                     override_recipients: list = None):
    await rep.build_and_send(report_type, debug_override=force_debug,
                              month_override=month_override, tg_client=client,
                              week_offset=week_offset, week_date=week_date,
                              force_rebuild=force_rebuild,
                              override_recipients=override_recipients)


async def send_cached_report(client: TelegramClient, ym: str, sender_id: int):
    cached = rep.get_cached_report_path(ym)
    if not cached:
        await client.send_message(sender_id, "❌ Файл не найден, генерирую заново...")
        return False
    recipients = _recipients_for_request(sender_id)
    for uid in recipients:
        try:
            await client.send_file(uid, str(cached), caption=f"📎 {cached.name}")
            log.info(f"Отправлен кэш {cached.name} → {uid}")
        except Exception as e:
            log.error(f"Ошибка отправки → {uid}: {e}")
    return True


async def _run_dashboard(client, event, sender_id: int, ym: str):
    """Запускает генерацию дашборда с проверкой кэша."""
    from calendar import monthrange as _mr
    cached = dash_rep.get_cached_dashboard(ym)
    if cached:
        await event.reply(
            f"📁 Dashboard за {ym} уже существует.\n\n"
            f"1 — отправить готовый\n"
            f"2 — пересчитать"
        )
        _dialog_state[sender_id] = {"state": "awaiting_dashboard_cache", "ym": ym}
    else:
        await event.reply(f"⏳ Генерирую Dashboard за {ym}...")
        try:
            year, month = int(ym[:4]), int(ym[5:7])
            d_from = date(year, month, 1)
            d_to   = date(year, month, _mr(year, month)[1])
            await dash_rep.build_dashboard(
                client, ym, d_from, d_to,
                override_recipients=[sender_id])
        except Exception as e:
            await event.reply(f"❌ Ошибка: {e}")
            log.error(f"Ошибка dashboard {ym}: {e}", exc_info=DEBUG_MODE)


# ── Расписание ────────────────────────────────────────────────────────────

def should_run_daily_report() -> bool:
    now = datetime.now(TZ)
    h, m = map(int, DAILY_TIME.split(":"))
    return now.hour == h and now.minute == m


_last_snapshot = None
_last_daily    = None
_last_weekly   = None
_last_monthly  = None


async def tick(client: TelegramClient):
    global _last_snapshot, _last_daily, _last_weekly, _last_monthly

    # Переподключение если соединение потеряно
    if not client.is_connected():
        log.warning("Соединение потеряно, переподключаемся...")
        try:
            await client.connect()
            log.info("Переподключение успешно")
        except Exception as e:
            log.error(f"Ошибка переподключения: {e}")
            return

    now   = datetime.now(TZ)
    today = now.date()

    # ── Сборщик: каждый час ───────────────────────────────────────────────
    if _last_snapshot is None or (now - _last_snapshot).total_seconds() >= 3600:
        log.info("▶ Запуск сборщика постов...")
        try:
            for ch in CHANNELS:
                await snap.process_channel(client, ch)
            log.info("✓ Сборщик постов завершён")
        except Exception as e:
            log.error(f"Ошибка сборщика постов: {e}", exc_info=DEBUG_MODE)

        try:
            for ch in CHANNELS:
                await stories_mod.process_channel_stories(client, ch)
            log.info("✓ Сборщик сторис завершён")
        except Exception as e:
            log.error(f"Ошибка сборщика сторис: {e}", exc_info=DEBUG_MODE)

        _last_snapshot = now

    # ── Суточный отчёт ────────────────────────────────────────────────────
    if should_run_daily_report() and _last_daily != today:
        try:
            await run_report(client, "daily", force_debug=True,
                             override_recipients=DEBUG_IDS if DEBUG_IDS else RECIPIENT_IDS)
            _last_daily = today
        except Exception as e:
            log.error(f"Ошибка суточного отчёта: {e}", exc_info=DEBUG_MODE)

    # ── Недельный отчёт (понедельник) ────────────────────────────────────
    if (today.weekday() == WEEKLY_DAY and _last_weekly != today):
        try:
            await run_report(client, "weekly", override_recipients=RECIPIENT_IDS)
            _last_weekly = today
        except Exception as e:
            log.error(f"Ошибка недельного отчёта: {e}", exc_info=DEBUG_MODE)

    # ── Месячный отчёт (3-е число) ───────────────────────────────────────
    if (today.day == MONTHLY_DAY and _last_monthly != today):
        try:
            await run_report(client, "monthly", override_recipients=RECIPIENT_IDS)
            _last_monthly = today
            # Записываем историю после месячного отчёта
            try:
                from history_db import record_month_from_report
                ym = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
                # Данные возьмём из реестра
                from registry_manager import get_final_posts_for_period, load_registry
                from calendar import monthrange as _mr
                year, month = int(ym[:4]), int(ym[5:7])
                d_from = date(year, month, 1)
                d_to   = date(year, month, _mr(year, month)[1])
                cdata = []
                for ch in CHANNELS:
                    reg   = load_registry(ch)
                    subs  = reg.get("subscribers", 0)
                    posts = list(get_final_posts_for_period(ch, d_from, d_to).values())
                    cdata.append({"channel_id": ch, "subscribers": subs, "posts": posts})
                record_month_from_report(ym, cdata)
            except Exception as e:
                log.warning(f"Ошибка записи истории: {e}")
        except Exception as e:
            log.error(f"Ошибка месячного отчёта: {e}", exc_info=DEBUG_MODE)


# ── Обработчики команд ────────────────────────────────────────────────────

def register_command_handler(client: TelegramClient):

    @client.on(events.NewMessage(pattern=r"^/report", incoming=True))
    async def handle_report(event):
        sender_id = event.sender_id
        if sender_id not in ALLOWED_SENDERS:
            return
        args = event.raw_text.strip().split(maxsplit=1)
        args = args[1].strip() if len(args) > 1 else ""
        log.info(f"Команда /report {args} от {sender_id}")

        if args == "daily":
            await event.reply("⏳ Генерирую суточный отчёт...")
            try:
                await run_report(client, "daily", override_recipients=[sender_id])
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")
                log.error(f"Ошибка /report daily: {e}", exc_info=DEBUG_MODE)

        elif args == "weekly":
            today = date.today()
            mon1  = today - timedelta(days=today.weekday() + 7)
            sun1  = mon1  + timedelta(days=6)
            mon2  = mon1  - timedelta(days=7)
            sun2  = mon2  + timedelta(days=6)
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
                "05 — май\n11 — ноябрь"
            )

        elif args == "dashboard":
            today = datetime.now(TZ)
            year, month = today.year, today.month
            if month == 1:
                prev_m, prev_y = 12, year - 1
            else:
                prev_m, prev_y = month - 1, year
            _dialog_state[sender_id] = {"state": "awaiting_dashboard_month"}
            await event.reply(
                f"📊 Dashboard — выберите период:\n\n"
                f"1 — {MONTHS_RU.get(prev_m,'')} {prev_y}\n"
                f"2 — {MONTHS_RU.get(month,'')} {year}\n"
                f"3 — указать вручную (ММ.ГГГГ)"
            )

        else:
            await event.reply(
                "📊 Доступные команды:\n"
                "/report daily      — суточный отчёт (лично вам)\n"
                "/report weekly     — недельный отчёт\n"
                "/report monthly    — месячный отчёт\n"
                "/report dashboard  — управленческий дашборд (PPTX)\n"
                "/upload monthly    — выгрузить месяц в Google Sheets\n"
                "/backfill          — ретро-сбор исторических данных"
            )

    @client.on(events.NewMessage(pattern=r"^/upload", incoming=True))
    async def handle_upload(event):
        sender_id = event.sender_id
        if sender_id not in ALLOWED_SENDERS:
            return
        args = event.raw_text.strip().split()

        if len(args) >= 2 and args[1] == "monthly":
            # Определяем месяц
            if len(args) >= 3:
                ym = args[2]
            else:
                today = datetime.now(TZ)
                if today.month == 1:
                    ym = f"{today.year-1}-12"
                else:
                    ym = f"{today.year}-{today.month-1:02d}"

            await event.reply("⏳ Проверяю доступность Google Sheets...")
            try:
                import google_sheets as gs
                ok, err_msg = await gs.check_connection()
                if not ok:
                    await event.reply(f"❌ Google Sheets недоступен:\n{err_msg}")
                    return

                await event.reply(f"⏳ Выгружаю отчёт за {ym} в Google Sheets...")
                from calendar import monthrange as _mr
                from registry_manager import get_final_posts_for_period, load_registry

                year, month = int(ym[:4]), int(ym[5:7])
                d_from = date(year, month, 1)
                d_to   = date(year, month, _mr(year, month)[1])

                channels_data = []
                for ch in CHANNELS:
                    reg   = load_registry(ch)
                    subs  = reg.get("subscribers", 0)
                    posts = list(get_final_posts_for_period(ch, d_from, d_to).values())
                    channels_data.append({"channel_id": ch, "subscribers": subs, "posts": posts})

                stories_data = {}
                for ch in CHANNELS:
                    ch_stories = stories_mod.get_stories_for_period(ch, d_from, d_to)
                    if ch_stories:
                        stories_data[ch.lstrip("@")] = ch_stories

                month_name = MONTHS_RU.get(month, str(month))
                await gs.upload_monthly_report(
                    channels_data, stories_data,
                    month_label=f"{month_name} {year}", ym=ym)
                await event.reply(f"✅ Данные за {month_name} {year} выгружены в Google Sheets.")

            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")
                log.error(f"Ошибка /upload monthly: {e}", exc_info=DEBUG_MODE)
        else:
            await event.reply(
                "📤 Команды выгрузки:\n"
                "/upload monthly          — выгрузить прошлый месяц в Google Sheets\n"
                "/upload monthly 2026-07  — выгрузить конкретный месяц"
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
                    await run_report(client, "weekly", week_offset=1,
                                     override_recipients=_recipients_for_request(sender_id))
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")
            elif text == "2":
                del _dialog_state[sender_id]
                await event.reply("⏳ Генерирую недельный отчёт за позапрошлую неделю...")
                try:
                    await run_report(client, "weekly", week_offset=2,
                                     override_recipients=_recipients_for_request(sender_id))
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
                day, month = int(parts[0]), int(parts[1])
                year = datetime.now(TZ).year
                if month > datetime.now(TZ).month:
                    year -= 1
                week_date = date(year, month, day)
            except (ValueError, IndexError):
                await event.reply("⚠️ Неверный формат. Введите дату в формате ДД.ММ, например: 15.05")
                return
            mon = week_date - timedelta(days=week_date.weekday())
            sun = mon + timedelta(days=6)
            del _dialog_state[sender_id]
            await event.reply(f"⏳ Генерирую недельный отчёт за {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')}...")
            try:
                await run_report(client, "weekly", week_date=week_date,
                                 override_recipients=_recipients_for_request(sender_id))
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")

        # ── Ожидаем номер месяца ─────────────────────────────────────────
        elif state == "awaiting_month":
            if not text.isdigit() or not (1 <= int(text) <= 12):
                await event.reply("⚠️ Введите номер месяца от 01 до 12.\nНапример: 05")
                return
            month_str  = text.zfill(2)
            year       = datetime.now(TZ).year
            if int(month_str) >= datetime.now(TZ).month:
                year -= 1
            ym         = f"{year}-{month_str}"
            month_name = MONTH_NAMES.get(month_str, month_str)
            cached_info = rep.get_cached_report_info(ym)
            if cached_info:
                _dialog_state[sender_id] = {
                    "state": "awaiting_cache", "ym": ym,
                    "month_name": month_name, "year": year,
                }
                await event.reply(
                    f"📁 Отчёт за {month_name} {year} уже сформирован ({cached_info}).\n\n"
                    f"1 — отправить готовый\n"
                    f"2 — пересчитать заново"
                )
            else:
                del _dialog_state[sender_id]
                await event.reply(f"⏳ Генерирую месячный отчёт за {month_name} {year}...")
                try:
                    await run_report(client, "monthly", month_override=ym,
                                     override_recipients=_recipients_for_request(sender_id))
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
                        await run_report(client, "monthly", month_override=ym,
                                         override_recipients=_recipients_for_request(sender_id))
                    except Exception as e:
                        await event.reply(f"❌ Ошибка: {e}")
            elif text == "2":
                del _dialog_state[sender_id]
                await event.reply(f"⏳ Пересчитываю отчёт за {month_name} {year} (кэш сбрасывается)...")
                try:
                    await run_report(client, "monthly", month_override=ym,
                                     force_rebuild=True,
                                     override_recipients=_recipients_for_request(sender_id))
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")
            else:
                await event.reply("⚠️ Введите 1 (готовый) или 2 (пересчитать)")

        # ── Dashboard: выбор месяца ───────────────────────────────────────
        elif state == "awaiting_dashboard_month":
            today = datetime.now(TZ)
            year, month = today.year, today.month
            if month == 1:
                prev_m, prev_y = 12, year - 1
            else:
                prev_m, prev_y = month - 1, year

            if text == "1":
                ym = f"{prev_y}-{prev_m:02d}"
            elif text == "2":
                ym = f"{year}-{month:02d}"
            elif text == "3":
                _dialog_state[sender_id] = {"state": "awaiting_dashboard_manual"}
                await event.reply("Введите месяц в формате ММ.ГГГГ, например: 07.2026")
                return
            else:
                await event.reply("⚠️ Введите 1, 2 или 3")
                return
            del _dialog_state[sender_id]
            await _run_dashboard(client, event, sender_id, ym)

        # ── Dashboard: ручной ввод месяца ─────────────────────────────────
        elif state == "awaiting_dashboard_manual":
            try:
                parts = text.split(".")
                mo, yr = int(parts[0]), int(parts[1])
                ym = f"{yr}-{mo:02d}"
            except Exception:
                await event.reply("⚠️ Неверный формат. Введите ММ.ГГГГ, например: 07.2026")
                return
            del _dialog_state[sender_id]
            await _run_dashboard(client, event, sender_id, ym)

        # ── Dashboard: кэш (отправить / пересчитать) ─────────────────────
        elif state == "awaiting_dashboard_cache":
            ym_d = state_data["ym"]
            if text == "1":
                del _dialog_state[sender_id]
                cached = dash_rep.get_cached_dashboard(ym_d)
                if cached:
                    await event.reply(f"📤 Отправляю Dashboard за {ym_d}...")
                    await client.send_file(sender_id, str(cached), caption=f"📎 {cached.name}")
                else:
                    await event.reply("⏳ Файл не найден, генерирую заново...")
                    await _run_dashboard(client, event, sender_id, ym_d)
            elif text == "2":
                del _dialog_state[sender_id]
                await event.reply(f"⏳ Пересчитываю Dashboard за {ym_d}...")
                try:
                    from calendar import monthrange as _mr
                    year, month = int(ym_d[:4]), int(ym_d[5:7])
                    d_from = date(year, month, 1)
                    d_to   = date(year, month, _mr(year, month)[1])
                    await dash_rep.build_dashboard(client, ym_d, d_from, d_to,
                                                   override_recipients=[sender_id])
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")
            else:
                await event.reply("⚠️ Введите 1 или 2")

        # ── Backfill: выбор типа периода ──────────────────────────────────
        elif state == "awaiting_backfill_type":
            if text == "1":
                _dialog_state[sender_id] = {"state": "awaiting_backfill_month"}
                await event.reply("📅 Введите номер месяца (формат ММ):\nНапример: 05 — май")
            elif text == "2":
                _dialog_state[sender_id] = {"state": "awaiting_backfill_week"}
                await event.reply("📅 Введите любой день нужной недели (формат ДД.ММ):\nНапример: 15.05")
            elif text == "3":
                del _dialog_state[sender_id]
                year = datetime.now(TZ).year
                await event.reply(f"⏳ Запускаю ретро-сбор с января по текущий месяц {year}...")
                try:
                    cur_month = datetime.now(TZ).month
                    for mo in range(1, cur_month):
                        ym = f"{year}-{mo:02d}"
                        await event.reply(f"  Собираю {ym}...")
                        for ch in CHANNELS:
                            await hist.fetch_and_cache_month(client, ch, ym, force=False)
                    await event.reply(f"✅ Ретро-сбор завершён. Данные за {year} доступны.")
                except Exception as e:
                    await event.reply(f"❌ Ошибка: {e}")
            else:
                await event.reply("⚠️ Введите 1, 2 или 3")

        # ── Backfill: конкретный месяц ────────────────────────────────────
        elif state == "awaiting_backfill_month":
            if not text.isdigit() or not (1 <= int(text) <= 12):
                await event.reply("⚠️ Введите номер месяца от 01 до 12.")
                return
            month_str = text.zfill(2)
            year      = datetime.now(TZ).year
            if int(month_str) >= datetime.now(TZ).month:
                year -= 1
            ym        = f"{year}-{month_str}"
            month_name = MONTH_NAMES.get(month_str, month_str)
            del _dialog_state[sender_id]
            await event.reply(f"⏳ Ретро-сбор за {month_name} {year}...")
            try:
                for ch in CHANNELS:
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
            mon = week_date - timedelta(days=week_date.weekday())
            sun = mon + timedelta(days=6)
            del _dialog_state[sender_id]
            await event.reply(f"⏳ Ретро-сбор за {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')}...")
            try:
                months = set()
                d = mon
                while d <= sun:
                    months.add(d.strftime("%Y-%m"))
                    d += timedelta(days=1)
                for ym in sorted(months):
                    for ch in CHANNELS:
                        await hist.fetch_and_cache_month(client, ch, ym, force=False)
                await event.reply(f"✅ Данные за {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')} сохранены.")
            except Exception as e:
                await event.reply(f"❌ Ошибка: {e}")


# ── Главный цикл ──────────────────────────────────────────────────────────

async def main():
    kwargs = get_telethon_kwargs()

    recipients_display = ", ".join(str(i) for i in (DEBUG_IDS if DEBUG_MODE else RECIPIENT_IDS))
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║           TG Analytics — запуск                 ║")
    print(f"║  Каналы:      {', '.join(CHANNELS)[:34]:<34}║")
    print(f"║  Режим:       {'DEBUG (тестовый)' if DEBUG_MODE else 'Рабочий':<34}║")
    print(f"║  Получатели:  {recipients_display[:34]:<34}║")
    if MODERATOR_IDS:
        mod_display = ", ".join(str(i) for i in MODERATOR_IDS)
        print(f"║  Модераторы:  {mod_display[:34]:<34}║")
    from config import TIMEZONE_NAME
    print(f"║  Часовой пояс: {TIMEZONE_NAME:<33}║")
    print("╚══════════════════════════════════════════════════╝")
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
            log.info(f"DEBUG: суточный отчёт в {DAILY_TIME} → {DEBUG_IDS}")
        if RECIPIENT_IDS:
            log.info(f"Получатели отчётов: {RECIPIENT_IDS}")
        if MODERATOR_IDS:
            log.info(f"Модераторы: {MODERATOR_IDS}")

        while True:
            await tick(client)
            await asyncio.sleep(60)

    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
