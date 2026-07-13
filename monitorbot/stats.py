"""
stats.py — статистика, отчёты и аналитика.

Отвечает за:
- Дневная статистика (daily_stats) — количество алертов по тегам за сутки
- Утренний отчёт (night_report) — итоги ночного мониторинга
- Аналитические агрегации для дашборда:
    - Топ каналов по алертам
    - Динамика по ключу (всплески активности)
    - Тепловая карта активности по часам
    - Посевная карта (какие каналы репостят друг друга)

Заглушки дашборда помечены # DASHBOARD
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class Stats:
    """Управляет статистикой и отчётами одного проекта."""

    def __init__(self, night_report_file: str, tz):
        self.night_report_file = night_report_file
        self.tz = tz

        # Счётчики алертов по тегам за текущие сутки
        # {"тег": int}
        self.daily_stats: dict[str, int] = {}

        # Буфер ночного отчёта — посты найденные вне рабочего времени
        # Загружается из night_report.json при старте
        self._night_buffer: list[dict] = []
        self.load_night_report()

    # ── Дневная статистика ────────────────────────────────────────────────────

    def record_alert(self, tags: list[str]):
        """Увеличивает счётчик для каждого тега из алерта."""
        for tag in tags:
            self.daily_stats[tag] = self.daily_stats.get(tag, 0) + 1

    def reset_daily(self):
        """Сбрасывает дневную статистику (вызывается в начале нового дня)."""
        self.daily_stats = {}

    def format_daily_summary(self) -> str:
        """Форматирует итоговую строку дневной статистики."""
        if not self.daily_stats:
            return "За прошедшие сутки публикаций не было."
        lines = ["📊 За прошедшие сутки:"]
        for tag, count in sorted(self.daily_stats.items(), key=lambda x: -x[1]):
            lines.append(f"  #{tag} — {count} упом.")
        return "\n".join(lines)

    # ── Ночной отчёт ─────────────────────────────────────────────────────────

    def load_night_report(self):
        """Загружает буфер ночного отчёта из файла."""
        if not os.path.exists(self.night_report_file):
            return
        try:
            with open(self.night_report_file, 'r', encoding='utf-8') as f:
                self._night_buffer = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load night_report.json: {e}")

    def save_night_report(self):
        """Сохраняет буфер ночного отчёта в файл."""
        try:
            with open(self.night_report_file, 'w', encoding='utf-8') as f:
                json.dump(self._night_buffer, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save night_report.json: {e}")

    def add_to_night_buffer(self, message: str):
        """Добавляет сообщение в буфер ночного отчёта."""
        self._night_buffer.append({
            "ts":      datetime.now(timezone.utc).timestamp(),
            "message": message,
        })
        self.save_night_report()

    def flush_night_buffer(self) -> list[dict]:
        """Возвращает и очищает буфер ночного отчёта."""
        buf = list(self._night_buffer)
        self._night_buffer = []
        self.save_night_report()
        return buf

    def has_night_buffer(self) -> bool:
        return bool(self._night_buffer)

    # ── Аналитика (для дашборда) ─────────────────────────────────────────────
    # DASHBOARD: эти методы будут вызываться дашбордом

    def get_key_dynamics(self, alert_history: list[dict], key: str, days: int = 7) -> list[dict]:
        """
        DASHBOARD: Динамика упоминаний ключа за последние N дней.
        Возвращает список {"date": str, "count": int} по дням.
        Позволяет обнаруживать всплески активности.
        """
        now = datetime.now(timezone.utc)
        result = []
        for day_offset in range(days - 1, -1, -1):
            day = now - timedelta(days=day_offset)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            day_end   = day_start + 86400
            count = sum(
                1 for a in alert_history
                if day_start <= a["ts"] < day_end
                and key in a.get("keys", [])
            )
            result.append({
                "date":  day.strftime("%d.%m"),
                "count": count,
            })
        return result

    def get_seed_map(self, alert_history: list[dict]) -> list[dict]:
        """
        DASHBOARD: Посевная карта — какие каналы публикуют одинаковые тексты.
        Возвращает список пар каналов с количеством совпадений.
        """
        # TODO: реализовать на основе seen_texts из Storage
        # Пока возвращает заглушку
        return []

    def get_hourly_heatmap(self, alert_history: list[dict], days: int = 7) -> list[list[int]]:
        """
        DASHBOARD: Тепловая карта активности.
        Возвращает матрицу [день][час] — количество алертов.
        """
        now = datetime.now(timezone.utc)
        matrix = [[0] * 24 for _ in range(days)]
        for a in alert_history:
            dt = datetime.fromtimestamp(a["ts"], tz=timezone.utc)
            day_offset = (now.date() - dt.date()).days
            if 0 <= day_offset < days:
                matrix[days - 1 - day_offset][dt.hour] += 1
        return matrix
