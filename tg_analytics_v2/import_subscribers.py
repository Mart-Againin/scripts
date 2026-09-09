"""
import_subscribers.py — ОТДЕЛЬНЫЙ инструмент для ручной записи/исправления
числа подписчиков за прошлые месяцы. Намеренно вынесен из
import_manual_stats.py, чтобы подписчиков нельзя было перезаписать
случайно вместе с обычными показателями (avg_reach/err/vrpost) — это
единственное поле в history_db.json, которое "замораживается" и в норме
не меняется вообще ничем, кроме этого скрипта.

ЧТО ДЕЛАЕТ:
  1. Записывает указанное число подписчиков как дневную запись на
     ПОСЛЕДНИЙ ДЕНЬ месяца в subscribers_history.json (тот же файл и тот
     же формат, что использует обычный сбор — снапшот раз в день).
  2. Пересчитывает и записывает subscribers + growth в history_db.json
     для этого месяца — здесь, и только здесь, подписчики намеренно
     ПЕРЕЗАПИСЫВАЮТСЯ, даже если запись уже была (это единственная цель
     существования этого скрипта — сознательно поправить именно эту
     защищённую цифру).
  3. growth пересчитывается автоматически по формуле "текущий месяц
     минус предыдущий" — вводить его вручную не нужно.

КАК ПОЛЬЗОВАТЬСЯ:
  1. Откройте subscribers.csv — колонки:

         month,channel,subscribers

     Одна строка = число подписчиков НА КОНЕЦ указанного месяца.

  2. Запустите:

         python import_subscribers.py
         python import_subscribers.py путь\к\файлу.csv

  3. Скрипт покажет полный список изменений и спросит ОДНО общее
     подтверждение в конце.
"""

import csv
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

from history_db import load_db, save_db, ensure_seeded
from snapshot import (load_subscribers_history, save_subscribers_history,
                       get_month_growth)


def main(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    ensure_seeded()

    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("В CSV нет строк с данными.")
        return

    planned = []  # (ym, ch, subscribers, last_day)
    for row in rows:
        ym = (row.get("month") or "").strip()
        ch = (row.get("channel") or "").strip()
        subs_raw = (row.get("subscribers") or "").strip()
        if not ym or not ch or not subs_raw:
            print(f"  ⚠️  Пропускаю неполную строку: {row}")
            continue
        try:
            subs = int(subs_raw)
        except ValueError:
            print(f"  ⚠️  Не могу разобрать число подписчиков: {subs_raw!r} — пропуск строки")
            continue
        year, month = int(ym[:4]), int(ym[5:7])
        last_day = date(year, month, monthrange(year, month)[1])
        planned.append((ym, ch, subs, last_day))

    if not planned:
        print("Нечего записывать.")
        return

    db = load_db()
    print(f"\nБудет применено изменений: {len(planned)}\n")
    for ym, ch, subs, last_day in planned:
        old = db.get(ym, {}).get(ch, {})
        old_subs = old.get("subscribers")
        print(f"  {ch} {ym}: подписчики {old_subs} -> {subs} "
              f"(дата снимка: {last_day})")

    answer = input(f"\nЭто ПЕРЕЗАПИШЕТ подписчиков (обычно защищённое поле) "
                   f"для {len(planned)} записей. Продолжить? (да/нет): ").strip().lower()
    if answer not in ("да", "yes", "y", "д"):
        print("Отменено, ничего не записано.")
        return

    for ym, ch, subs, last_day in planned:
        # 1. Дневная запись — тот же файл, что и обычный автосбор
        hist = load_subscribers_history(ch)
        hist[last_day.strftime("%Y-%m-%d")] = subs
        save_subscribers_history(ch, hist)

        # 2. Прямая запись в history_db.json — здесь сознательно обходим
        # обычную защиту "не перезаписывать", это и есть цель скрипта
        growth = get_month_growth(ch, ym)
        db.setdefault(ym, {}).setdefault(ch, {})
        db[ym][ch]["subscribers"] = subs
        db[ym][ch]["growth"] = growth
        print(f"  ✅ {ch} {ym}: subscribers={subs}, growth={growth}")

    save_db(db)
    print(f"\nГотово — обновлено записей: {len(planned)}.")


if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "subscribers.csv"
    main(csv_file)
