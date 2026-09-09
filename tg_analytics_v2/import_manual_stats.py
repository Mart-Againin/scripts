"""
import_manual_stats.py — загружает вручную собранную статистику ЗА
ПОСТЫ за прошлые месяцы (avg_reach, err, vrpost) в registry/history_db.json.

ВАЖНО: этот инструмент НЕ трогает подписчиков и прирост — для них есть
ОТДЕЛЬНЫЙ инструмент import_subscribers.py. Это сделано специально, чтобы
подписчиков нельзя было случайно перезаписать через "обычный" импорт —
если строка вашего CSV содержит колонки subscribers/growth, они будут
проигнорированы (с предупреждением), а не записаны.

КАК ПОЛЬЗОВАТЬСЯ:
  1. Откройте manual_stats.csv — колонки:

         month,channel,avg_reach,err,vrpost

     month   — месяц в формате ГГГГ-ММ
     channel — @username канала точно как в CHANNELS в .env
     avg_reach, err, vrpost — то, что у вас есть; пустая ячейка = "этого
     числа у меня нет", скрипт не будет его выдумывать

  2. Запустите (из папки проекта):

         python import_manual_stats.py
         python import_manual_stats.py путь\к\файлу.csv

  3. Скрипт сначала покажет ВЕСЬ список изменений (что было -> что
     станет), и только ОДИН РАЗ в конце спросит общее подтверждение —
     не по каждой строке отдельно.

БЕЗОПАСНОСТЬ ДАННЫХ:
  - Меняются только avg_reach/err/vrpost, и только для строк из CSV.
  - subscribers/growth не трогаются никогда, даже если такие колонки
    есть в файле.
  - Если вы передумали — на общем вопросе в конце просто ответьте "нет",
    ничего не запишется.
"""

import csv
import sys
from pathlib import Path

from history_db import load_db, save_db, ensure_seeded

FIELDS_ALLOWED = ["avg_reach", "err", "vrpost"]
FIELDS_FORBIDDEN = ["subscribers", "growth"]  # только через import_subscribers.py


def parse_row(row: dict) -> dict:
    result = {}
    for f in FIELDS_ALLOWED:
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
        rows = list(csv.DictReader(f))

    if not rows:
        print("В CSV нет строк с данными.")
        return

    warned_forbidden = False
    planned = []  # (ym, ch, new_fields, existing_or_None)

    for row in rows:
        ym = (row.get("month") or "").strip()
        ch = (row.get("channel") or "").strip()
        if not ym or not ch:
            print(f"  ⚠️  Пропускаю строку без месяца/канала: {row}")
            continue

        if not warned_forbidden and any((row.get(f) or "").strip() for f in FIELDS_FORBIDDEN):
            print("  ⚠️  В файле есть колонки subscribers/growth — они игнорируются. "
                  "Для подписчиков используйте import_subscribers.py")
            warned_forbidden = True

        new_fields = parse_row(row)
        if not new_fields:
            print(f"  ⚠️  {ch} {ym}: нет ни одного заполненного поля (avg_reach/err/vrpost), пропуск")
            continue

        existing = db.get(ym, {}).get(ch)
        planned.append((ym, ch, new_fields, existing))

    if not planned:
        print("Нечего записывать.")
        return

    print(f"\nБудет применено изменений: {len(planned)}\n")
    for ym, ch, new_fields, existing in planned:
        if existing:
            changed = {k: v for k, v in new_fields.items() if existing.get(k) != v}
            if not changed:
                print(f"  {ch} {ym}: без изменений (уже такие же значения)")
            else:
                old_vals = {k: existing.get(k) for k in changed}
                print(f"  {ch} {ym}: {changed}  (было: {old_vals})")
        else:
            print(f"  {ch} {ym}: новая запись {new_fields}")

    answer = input(f"\nПрименить все {len(planned)} изменений? (да/нет): ").strip().lower()
    if answer not in ("да", "yes", "y", "д"):
        print("Отменено, ничего не записано.")
        return

    applied = 0
    for ym, ch, new_fields, existing in planned:
        db.setdefault(ym, {})
        if existing:
            existing.update(new_fields)
        else:
            db[ym][ch] = new_fields
        applied += 1

    save_db(db)
    print(f"\nГотово — записано изменений: {applied}.")


if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "manual_stats.csv"
    main(csv_file)
