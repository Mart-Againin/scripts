"""
import_manual_stats.py — загружает вручную собранную статистику за прошлые
месяцы (там, где скрипт физически не мог их собрать сам — например, до
запуска проекта) в registry/history_db.json, чтобы Dashboard мог строить по
ним графики динамики.

КАК ПОЛЬЗОВАТЬСЯ:
  1. Откройте manual_stats.csv любым способом — Excel, Google Таблицы,
     Блокнот. Это обычный CSV с колонками:

         month,channel,subscribers,growth,avg_reach,err,vrpost

     month       — месяц в формате ГГГГ-ММ, например 2026-01
     channel     — @username канала ТОЧНО как в CHANNELS в .env
     subscribers — число подписчиков на конец месяца
     growth      — прирост за месяц (можно оставить пустым, если не знаете)
     avg_reach   — средний охват публикации за месяц
     err         — ERR (%) за месяц
     vrpost      — VRpost (%) за месяц, если он у вас есть; если нет —
                   оставьте пустым, скрипт не будет его выдумывать

  2. Пустая ячейка = "этого числа у меня нет". Скрипт запишет только те
     поля, для которых ячейка заполнена — не подставляет 0 и не трогает
     то, что уже могло быть записано другим способом для этого же месяца.

  3. Добавьте столько строк, сколько нужно — по одной строке на
     канал+месяц.

  4. Запустите (из папки проекта):

         python import_manual_stats.py

     Или, если файл называется по-другому / лежит в другом месте:

         python import_manual_stats.py путь\к\вашему\файлу.csv

  5. Скрипт выведет построчно, что именно записал, и завершится словом
     "Готово".

ВАЖНО ПРО БЕЗОПАСНОСТЬ ДАННЫХ:
  - Меняются ТОЛЬКО те (месяц, канал), что перечислены в CSV, и только те
    поля, что в них заполнены. Все остальные месяцы/каналы/поля в
    history_db.json остаются как есть, байт в байт.
  - Если для (месяц, канал) из вашего CSV в history_db.json уже ЕСТЬ
    запись — скрипт СПРОСИТ подтверждение перед перезаписью (выведет
    старое и новое значение и попросит ввести "да"). Просто чтобы вы не
    затёрли случайно то, что уже было честно собрано скриптом.
"""

import csv
import sys
from pathlib import Path

from history_db import load_db, save_db, ensure_seeded

FIELDS_NUMERIC = ["subscribers", "growth", "avg_reach", "err", "vrpost"]


def parse_row(row: dict) -> dict:
    """Оставляет только непустые числовые поля."""
    result = {}
    for f in FIELDS_NUMERIC:
        val = (row.get(f) or "").strip()
        if val == "":
            continue
        try:
            result[f] = float(val) if "." in val else int(val)
        except ValueError:
            print(f"  ⚠️  Не могу разобрать число в поле '{f}': {val!r} — пропускаю это поле")
    return result


def main(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    ensure_seeded()
    db = load_db()

    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("В CSV нет строк с данными.")
        return

    for row in rows:
        ym = (row.get("month") or "").strip()
        ch = (row.get("channel") or "").strip()
        if not ym or not ch:
            print(f"  ⚠️  Пропускаю строку без месяца/канала: {row}")
            continue

        new_fields = parse_row(row)
        if not new_fields:
            print(f"  ⚠️  {ch} {ym}: в строке нет ни одного заполненного числового поля, пропуск")
            continue

        db.setdefault(ym, {})
        existing = db[ym].get(ch)

        if existing:
            print(f"\n  Для {ch} {ym} уже ЕСТЬ запись: {existing}")
            print(f"  Новые значения из CSV:          {new_fields}")
            answer = input("  Перезаписать эти поля? (да/нет): ").strip().lower()
            if answer not in ("да", "yes", "y", "д"):
                print("  Пропущено по вашему решению.")
                continue
            existing.update(new_fields)
        else:
            db[ym][ch] = new_fields

        print(f"  ✅ {ch} {ym}: записано {new_fields}")

    save_db(db)
    print("\nГотово.")


if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "manual_stats.csv"
    main(csv_file)
