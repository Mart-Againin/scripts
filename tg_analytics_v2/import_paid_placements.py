"""
import_paid_placements.py — загружает данные о платных размещениях
(посевах) из xlsx-файла (скачанная копия Google Таблицы или любой файл
с той же структурой) в registry/paid_placements.json — локальное
хранилище, которое dashboard_report.py читает НАРЯДУ с Google Sheets
(источники дополняют друг друга, см. paid_placements.get_paid_placements).

ФОРМАТ ВХОДНОГО ФАЙЛА — ТОТ ЖЕ, ЧТО И В GOOGLE ТАБЛИЦЕ:
  Строка-заголовок канала: текст в колонке A, дата в колонке C пустая.
  Название должно совпадать (или частично совпадать) с именем канала в
  DASHBOARD_CHANNELS (config.py) — например, "Геймификация: игра на
  результат" совпадёт с каналом "Геймификация", потому что это имя
  входит в текст заголовка.
  Дальше — строки размещений: Площадка | Ссылка | Дата | Статус |
  Подписчики | Ср.охват | Стоимость | Охват | CPV | CPM | Приток | CPF | Формат.

ЕСЛИ ЗАГОЛОВОК НЕ СОПОСТАВИЛСЯ НИ С ОДНИМ КАНАЛОМ:
  Скрипт покажет это отдельным списком и НЕ будет угадывать. Чтобы
  научить его — добавьте строку в paid_channel_aliases.csv (создастся
  автоматически при первом запуске, если его ещё нет):

      alias,channel
      HRTech,@ktsdaily

  и запустите импорт заново.

КАК ПОЛЬЗОВАТЬСЯ:
    python import_paid_placements.py Посевы.xlsx
    python import_paid_placements.py Посевы.xlsx --sheet "Август 2026"
    python import_paid_placements.py посевы.csv

Можно передавать и .xlsx, и .csv — файл определяется по расширению.
Год для дат без года (формат ДД.ММ) берётся из названия листа (если там
есть "2026" и т.п.) либо из текущего года.

Сначала показывает список всех найденных размещений по каналам и
несопоставленные заголовки, потом ОДНО общее подтверждение. Новые
размещения ДОБАВЛЯЮТСЯ к уже сохранённым (дедупликация по
канал+дата+площадка+ссылка) — старые данные не удаляются и не
перезаписываются автоматически.
"""

import csv
import re
import sys
from pathlib import Path

from paid_placements import _parse_rows, load_local_placements, save_local_placements, ALIASES_PATH


def _read_xlsx(path: Path, sheet_name: str = None):
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    year_hint = None
    m = re.search(r"20\d\d", ws.title)
    if m:
        year_hint = int(m.group(0))
    return rows, year_hint


def _read_csv(path: Path):
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    return rows, None


def _ensure_aliases_file():
    if not ALIASES_PATH.exists():
        ALIASES_PATH.write_text(
            "alias,channel\n"
            "# Пример: HRTech,@ktsdaily\n"
            "# Добавляйте сюда строки, если заголовок в таблице не\n"
            "# совпадает с именем канала в config.py (DASHBOARD_CHANNELS)\n",
            encoding="utf-8",
        )


def _dedup_key(p: dict) -> tuple:
    return (p.get("date"), p.get("platform"), p.get("link"))


def main(file_path: str, sheet_name: str = None):
    path = Path(file_path)
    if not path.exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    _ensure_aliases_file()

    if path.suffix.lower() in (".xlsx", ".xlsm"):
        rows, year_hint = _read_xlsx(path, sheet_name)
    elif path.suffix.lower() == ".csv":
        rows, year_hint = _read_csv(path)
    else:
        print(f"Неизвестное расширение файла: {path.suffix} (нужен .xlsx или .csv)")
        sys.exit(1)

    # Первая строка обычно заголовок колонок — но _parse_rows сама
    # пропустит её, если это не строка-заголовок канала и не дата
    from config import DASHBOARD_CHANNELS
    by_channel, unmatched = _parse_rows(rows, DASHBOARD_CHANNELS, year_hint=year_hint)

    if not by_channel and not unmatched:
        print("Не нашёл ни одной строки с размещениями. Проверьте структуру файла.")
        return

    print("\nНайдено размещений:\n")
    total_new = 0
    existing = load_local_placements()
    to_add = {}
    unmatched_set = set(unmatched)

    for key, placements in by_channel.items():
        existing_keys = {_dedup_key(p) for p in existing.get(key, [])}
        new_ones = [p for p in placements if _dedup_key(p) not in existing_keys]
        dup_count = len(placements) - len(new_ones)
        label = f"«{key}» (ДОП. БЛОК — получит отдельный слайд в конце)" if key in unmatched_set else key
        print(f"  {label}: {len(placements)} размещений в файле"
              f"{f', из них уже есть в базе: {dup_count}' if dup_count else ''}"
              f", новых: {len(new_ones)}")
        if new_ones:
            to_add[key] = new_ones
            total_new += len(new_ones)

    if unmatched:
        print(f"\nℹ️  Не совпало с известными каналами ({len(unmatched)}): "
              f"{', '.join(unmatched)}")
        print(f"  По умолчанию каждый из них получит СВОЙ отдельный слайд в конце "
              f"презентации (см. выше — записано в базу как есть). Если на самом деле "
              f"это данные по одному из ваших каналов — допишите соответствие в "
              f"{ALIASES_PATH} и запустите импорт заново.")

    if total_new == 0:
        print("\nНовых размещений для добавления нет (всё уже есть в базе).")
        return

    answer = input(f"\nДобавить {total_new} новых размещений в базу? (да/нет): ").strip().lower()
    if answer not in ("да", "yes", "y", "д"):
        print("Отменено, ничего не записано.")
        return

    for ch, new_ones in to_add.items():
        existing.setdefault(ch, [])
        existing[ch].extend(new_ones)

    save_local_placements(existing)
    print(f"\nГотово — добавлено размещений: {total_new}.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Укажите файл, например: python import_paid_placements.py Посевы.xlsx")
        sys.exit(1)
    sheet = None
    if "--sheet" in args:
        idx = args.index("--sheet")
        sheet = args[idx + 1]
        del args[idx:idx + 2]
    main(args[0], sheet)
