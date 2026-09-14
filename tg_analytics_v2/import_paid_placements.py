"""
import_paid_placements.py — загружает данные о платных размещениях
(посевах) из xlsx/csv-файла в registry/paid_placements.json — локальное
хранилище, которое dashboard_report.py читает НАРЯДУ с Google Sheets
(источники дополняют друг друга, см. paid_placements.get_paid_placements).

Модуль устроен в два слоя:
  - "движок" (analyze_file, apply_plan и т.д.) — без print/input, можно
    дёргать откуда угодно, в том числе из бота (см. main.py, команда
    /import_paid — там пользователь присылает файл прямо в Telegram, без
    консоли);
  - main() — обёртка для запуска из командной строки.

ФОРМАТ ВХОДНОГО ФАЙЛА — ТОТ ЖЕ, ЧТО И В GOOGLE ТАБЛИЦЕ:
  Строка-заголовок канала: текст в колонке A, дата в колонке C пустая.
  Название должно совпадать (или частично совпадать) с именем канала в
  DASHBOARD_CHANNELS (config.py) — например, "Геймификация: игра на
  результат" совпадёт с каналом "Геймификация", потому что это имя
  входит в текст заголовка.
  Дальше — строки размещений: Площадка | Ссылка | Дата | Статус |
  Подписчики | Ср.охват | Стоимость | Охват | CPV | CPM | Приток | CPF | Формат.

ЕСЛИ ЗАГОЛОВОК НЕ СОПОСТАВИЛСЯ НИ С ОДНИМ КАНАЛОМ:
  Не отбрасывается — становится "доп. блоком", получает отдельный
  название-слайд в конце презентации. Если это ошибка и на самом деле
  данные по одному из ваших каналов — допишите соответствие в
  paid_channel_aliases.csv (создастся автоматически при первом запуске)
  и запустите импорт заново.

КАК ПОЛЬЗОВАТЬСЯ ИЗ КОНСОЛИ:
    python import_paid_placements.py Посевы.xlsx
    python import_paid_placements.py Посевы.xlsx --sheet "Август 2026"
    python import_paid_placements.py посевы.csv

Можно передавать и .xlsx, и .csv — файл определяется по расширению.
Если в xlsx несколько листов — лист нужно указать явно через --sheet
(скрипт не угадывает "первый" или "последний").

ИЛИ ИЗ TELEGRAM — команда /import_paid (см. main.py).
"""

import csv
import re
import sys
from pathlib import Path

from paid_placements import _parse_rows, load_local_placements, save_local_placements, ALIASES_PATH


class MultipleSheetsError(Exception):
    """xlsx с несколькими листами, а нужный не указан явно."""
    def __init__(self, sheetnames):
        self.sheetnames = sheetnames
        super().__init__(f"Несколько листов: {sheetnames}")


# ── "Движок" — без print/input, используется и CLI, и ботом ───────────────

def read_file_rows(path: Path, sheet_name: str = None):
    """
    Возвращает (rows, year_hint).
    Кидает MultipleSheetsError, если это xlsx с несколькими листами и
    sheet_name не указан — вызывающий код должен сам решить, как
    получить нужное имя листа от пользователя (консольный ввод, вопрос
    в Telegram и т.д.), а не гадать.
    """
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)

        if sheet_name:
            ws = wb[sheet_name]
        elif len(wb.sheetnames) == 1:
            ws = wb[wb.sheetnames[0]]
        else:
            raise MultipleSheetsError(wb.sheetnames)

        rows = list(ws.iter_rows(values_only=True))
        year_hint = None
        m = re.search(r"20\d\d", ws.title)
        if m:
            year_hint = int(m.group(0))
        return rows, year_hint

    elif suffix == ".csv":
        with path.open(encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        return rows, None

    else:
        raise ValueError(f"Неизвестное расширение файла: {suffix} (нужен .xlsx или .csv)")


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


def analyze_rows(rows: list, year_hint: int = None) -> dict:
    """
    Разбирает строки и сравнивает с уже сохранённым в базе.
    Возвращает:
      {
        "plan": {key: [новые_placement, ...]},   # что реально добавится
        "total_new": int,
        "summary_lines": [(label, is_extra, total_in_file, dup_count, new_count), ...],
        "unmatched": [список названий "доп. блоков"],
      }
    Ничего не печатает и не пишет на диск — это делает apply_plan().
    """
    from config import DASHBOARD_CHANNELS
    _ensure_aliases_file()

    by_channel, unmatched = _parse_rows(rows, DASHBOARD_CHANNELS, year_hint=year_hint)
    unmatched_set = set(unmatched)
    existing = load_local_placements()

    plan = {}
    summary_lines = []
    total_new = 0

    for key, placements in by_channel.items():
        existing_keys = {_dedup_key(p) for p in existing.get(key, [])}
        new_ones = [p for p in placements if _dedup_key(p) not in existing_keys]
        dup_count = len(placements) - len(new_ones)
        is_extra = key in unmatched_set
        summary_lines.append((key, is_extra, len(placements), dup_count, len(new_ones)))
        if new_ones:
            plan[key] = new_ones
            total_new += len(new_ones)

    return {
        "plan": plan,
        "total_new": total_new,
        "summary_lines": summary_lines,
        "unmatched": unmatched,
    }


def apply_plan(plan: dict) -> int:
    """Сохраняет план в registry/paid_placements.json. Возвращает число добавленных записей."""
    existing = load_local_placements()
    total = 0
    for key, new_ones in plan.items():
        existing.setdefault(key, [])
        existing[key].extend(new_ones)
        total += len(new_ones)
    save_local_placements(existing)
    return total


def format_summary_text(analysis: dict) -> str:
    """Человекочитаемый текст сводки — общий для консоли и для Telegram."""
    lines = []
    for key, is_extra, total_in_file, dup_count, new_count in analysis["summary_lines"]:
        label = f"«{key}» (доп. блок — получит отдельный слайд)" if is_extra else key
        dup_part = f", уже есть: {dup_count}" if dup_count else ""
        lines.append(f"• {label}: {total_in_file} в файле{dup_part}, новых: {new_count}")

    if analysis["unmatched"]:
        lines.append("")
        lines.append(f"ℹ️ Не совпало с известными каналами: {', '.join(analysis['unmatched'])}")
        lines.append("Каждый получит свой слайд в конце презентации. Если это ошибка — "
                      f"допишите соответствие в {ALIASES_PATH.name} и повторите импорт.")

    return "\n".join(lines)


# ── CLI-обёртка ─────────────────────────────────────────────────────────

def main(file_path: str, sheet_name: str = None):
    path = Path(file_path)
    if not path.exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    try:
        rows, year_hint = read_file_rows(path, sheet_name)
    except MultipleSheetsError as e:
        print(f"В файле {path.name} несколько листов: {e.sheetnames}")
        print("Укажите нужный явно: --sheet \"Название\"")
        sys.exit(1)
    except ValueError as e:
        print(str(e))
        sys.exit(1)

    analysis = analyze_rows(rows, year_hint)

    if not analysis["summary_lines"] and not analysis["unmatched"]:
        print("Не нашёл ни одной строки с размещениями. Проверьте структуру файла.")
        return

    print("\nНайдено размещений:\n")
    print(format_summary_text(analysis))

    if analysis["total_new"] == 0:
        print("\nНовых размещений для добавления нет (всё уже есть в базе).")
        return

    answer = input(f"\nДобавить {analysis['total_new']} новых размещений в базу? (да/нет): ").strip().lower()
    if answer not in ("да", "yes", "y", "д"):
        print("Отменено, ничего не записано.")
        return

    total = apply_plan(analysis["plan"])
    print(f"\nГотово — добавлено размещений: {total}.")


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
