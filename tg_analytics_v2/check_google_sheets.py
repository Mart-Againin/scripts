"""
check_google_sheets.py — быстрая сквозная проверка: доходят ли настройки
из .env (ключ + обе ссылки) до реальных данных в Google Таблицах.

Не трогает Telegram вообще — только Google API, поэтому быстрый и без
риска обрывов соединения, которые бывают с ботом.

Проверяет по шагам, печатая результат каждого:
  1. Файл ключа существует, читается, это валидный JSON сервис-аккаунта.
  2. Авторизация в Google проходит.
  3. Таблица отчётов (GOOGLE_SHEETS_ID) открывается, показывает список
     вкладок.
  4. Таблица посевов (GOOGLE_PAID_SHEET_URL) открывается, показывает
     список вкладок, ищет вкладку текущего месяца — если нашла, читает
     из неё пару строк, чтобы подтвердить, что доступ реально на чтение
     работает, а не просто "открылась".

ЗАПУСК:
    python check_google_sheets.py
"""

import json
import os
import sys
from datetime import datetime

from config import MONTHS_RU, TZ

OK = "✅"
FAIL = "❌"
WARN = "⚠️ "


def check_credentials_file():
    print("--- 1. Файл ключа (GOOGLE_SHEETS_CREDENTIALS) ---")
    path = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    if not path:
        print(f"{FAIL} GOOGLE_SHEETS_CREDENTIALS не задан в .env")
        return None
    if not os.path.exists(path):
        print(f"{FAIL} Файл не найден по пути: {path}")
        return None
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except Exception as e:
        print(f"{FAIL} Файл есть, но это не валидный JSON: {e}")
        return None
    if data.get("type") != "service_account" or "client_email" not in data:
        print(f"{FAIL} Файл прочитан, но не похож на ключ сервис-аккаунта "
              f"(нет полей type=service_account / client_email)")
        return None
    print(f"{OK} Файл найден и корректен")
    print(f"    client_email: {data['client_email']}")
    print("    (этот email должен быть добавлен как Editor в ОБЕИХ таблицах)")
    return path


def authorize(creds_path):
    print("\n--- 2. Авторизация в Google ---")
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        print(f"{OK} Авторизация прошла")
        return gc
    except Exception as e:
        print(f"{FAIL} Ошибка авторизации: {e}")
        return None


def check_reports_sheet(gc):
    print("\n--- 3. Таблица отчётов (GOOGLE_SHEETS_ID) ---")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not sheet_id:
        print(WARN + "GOOGLE_SHEETS_ID не задан — пропускаю (это нормально, если вы им не пользуетесь)")
        return
    try:
        sh = gc.open_by_key(sheet_id)
        print(f"{OK} Таблица открылась: «{sh.title}»")
        tabs = [w.title for w in sh.worksheets()]
        print(f"    Вкладки ({len(tabs)}): {tabs}")
    except Exception as e:
        print(f"{FAIL} Не удалось открыть: {e}")
        print("    Проверьте: 1) верный ли ID в .env, 2) расшарена ли таблица на client_email выше с правами Editor")


def check_paid_sheet(gc):
    print("\n--- 4. Таблица посевов (GOOGLE_PAID_SHEET_URL) ---")
    url = os.getenv("GOOGLE_PAID_SHEET_URL")
    if not url:
        print(WARN + "GOOGLE_PAID_SHEET_URL не задан — пропускаю")
        return
    try:
        sh = gc.open_by_url(url)
        print(f"{OK} Таблица открылась: «{sh.title}»")
        tabs = [w.title for w in sh.worksheets()]
        print(f"    Вкладки ({len(tabs)}): {tabs}")

        now = datetime.now(TZ)
        month_label = f"{MONTHS_RU.get(now.month,'')} {now.year}"
        print(f"\n    Ищу вкладку текущего месяца: «{month_label}»")
        if month_label in tabs:
            print(f"    {OK} Найдена")
            ws = sh.worksheet(month_label)
            rows = ws.get_all_values()
            print(f"    Строк на вкладке: {len(rows)}")
            if rows:
                print(f"    Первая строка (заголовки): {rows[0]}")
                if len(rows) > 1:
                    print(f"    Вторая строка (пример данных): {rows[1]}")
        else:
            print(f"    {WARN}Вкладки «{month_label}» ещё нет — для дашборда за текущий "
                  f"месяц посевы будут пропущены (это ожидаемо, если вкладку ещё не создали)")
    except Exception as e:
        print(f"{FAIL} Не удалось открыть: {e}")
        print("    Проверьте: 1) верная ли ссылка в .env, 2) расшарена ли таблица на client_email выше с правами Editor")


def main():
    creds_path = check_credentials_file()
    if not creds_path:
        print(f"\n{FAIL} Дальше проверять нет смысла — сначала почините ключ (см. пункт 1)")
        sys.exit(1)

    gc = authorize(creds_path)
    if not gc:
        print(f"\n{FAIL} Дальше проверять нет смысла — авторизация не прошла")
        sys.exit(1)

    check_reports_sheet(gc)
    check_paid_sheet(gc)

    print("\n--- Готово ---")


if __name__ == "__main__":
    main()
