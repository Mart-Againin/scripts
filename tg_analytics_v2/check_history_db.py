"""
check_history_db.py — только СМОТРИТ, что лежит в registry/history_db.json
за указанные месяцы, ничего не меняет. Нужен, чтобы понять, чего именно не
хватает, прежде чем что-то чинить.

ЗАПУСК:
    python check_history_db.py 2026-07 2026-08
    python check_history_db.py                   # без аргументов — последние 3 месяца
"""

import sys
from datetime import date

from config import CHANNELS
from history_db import load_db


def main(months: list[str]):
    db = load_db()
    for ym in months:
        print(f"\n=== {ym} ===")
        entry = db.get(ym)
        if not entry:
            print("  В history_db.json ВООБЩЕ НЕТ записи за этот месяц (ни по одному каналу).")
            continue
        for ch in CHANNELS:
            data = entry.get(ch)
            if not data:
                print(f"  {ch}: записи НЕТ")
                continue
            missing = [k for k in ("subscribers", "growth", "avg_reach", "err", "vrpost")
                       if data.get(k) is None]
            status = "всё на месте" if not missing else f"ПУСТО: {', '.join(missing)}"
            print(f"  {ch}: {data}  ->  {status}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        months = sys.argv[1:]
    else:
        today = date.today()
        months = []
        y, m = today.year, today.month
        for _ in range(3):
            months.append(f"{y}-{m:02d}")
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        months.reverse()
    main(months)
