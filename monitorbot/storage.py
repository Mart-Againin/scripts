"""
storage.py — персистентное хранение состояния проекта.

Отвечает за:
- seen_urls_by_channel  — виденные URL (дедупликация постов)
- text_blacklist        — заблокированные тексты (хэши)
- seen_texts            — fingerprints отправленных текстов (посевы)
- tag_blocked           — заблокированные теги с временем разблокировки
- tag_alert_sent        — теги по которым уже предложена блокировка
- alert_history         — история алертов (для аналитики / дашборда)

Все данные хранятся в results.json рядом с .env проекта.

Заглушки для дашборда:
- get_stats_for_dashboard() — возвращает агрегированную статистику
"""

import os
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Storage:
    """Управляет персистентным состоянием одного проекта."""

    def __init__(self, output_file: str):
        self.output_file = output_file

        self.seen_urls_by_channel: dict[str, dict[str, float]] = {}
        self.text_blacklist: set[int] = set()
        self.seen_texts: dict[int, dict] = {}
        self._tag_blocked: dict[str, float] = {}
        self._tag_alert_sent: set[str] = set()

        # История алертов: список {"ts", "url", "channel", "keys", "subs"}
        # Используется для аналитики и дашборда
        self.alert_history: list[dict] = []
        self._alert_history_max = 1000  # максимум записей в памяти

        self.load()

    # ── Загрузка / сохранение ─────────────────────────────────────────────────

    def load(self):
        """Загружает всё состояние из results.json."""
        if not os.path.exists(self.output_file):
            return
        try:
            with open(self.output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.seen_urls_by_channel = data.get("seen_urls_by_channel", {})
            self.text_blacklist = set(data.get("text_blacklist", []))

            raw_st = data.get("seen_texts", {})
            self.seen_texts = {int(k): v for k, v in raw_st.items()}

            now = datetime.now(timezone.utc).timestamp()
            raw_tb = data.get("tag_blocked", {})
            self._tag_blocked = {k: v for k, v in raw_tb.items() if v > now}
            self._tag_alert_sent = set(data.get("tag_alert_sent", []))

            self.alert_history = data.get("alert_history", [])

            total_seen = sum(len(v) for v in self.seen_urls_by_channel.values())
            logger.info(f"Loaded {total_seen} seen urls / {len(self.seen_urls_by_channel)} channels")
            logger.info(f"Loaded {len(self.seen_texts)} seen_texts entries")
            if self._tag_blocked:
                logger.info(f"Restored tag blocks: {list(self._tag_blocked.keys())}")

        except Exception as e:
            logger.warning(f"Failed to load results.json: {e}")

    def save(self):
        """Сохраняет полное состояние в results.json."""
        try:
            existing = {}
            if os.path.exists(self.output_file):
                with open(self.output_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)

            existing.update({
                "seen_urls_by_channel": self.seen_urls_by_channel,
                "text_blacklist":       list(self.text_blacklist),
                "seen_texts":           {str(k): v for k, v in self.seen_texts.items()},
                "tag_blocked":          self._tag_blocked,
                "tag_alert_sent":       list(self._tag_alert_sent),
                "alert_history":        self.alert_history[-self._alert_history_max:],
            })

            with open(self.output_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"Storage save failed: {e}")

    # ── seen_urls ─────────────────────────────────────────────────────────────

    def is_seen(self, channel_id: str, url: str) -> bool:
        return url in self.seen_urls_by_channel.get(channel_id, {})

    def mark_seen(self, channel_id: str, url: str):
        self.seen_urls_by_channel.setdefault(channel_id, {})[url] = (
            datetime.now(timezone.utc).timestamp()
        )

    def rotate_seen(self, ttl_days: int):
        """Удаляет виденные URL старше ttl_days дней."""
        cutoff = datetime.now(timezone.utc).timestamp() - ttl_days * 86400
        for ch in list(self.seen_urls_by_channel):
            self.seen_urls_by_channel[ch] = {
                u: t for u, t in self.seen_urls_by_channel[ch].items() if t > cutoff
            }
            if not self.seen_urls_by_channel[ch]:
                del self.seen_urls_by_channel[ch]

    # ── alert_history ─────────────────────────────────────────────────────────

    def record_alert(self, url: str, channel: str, keys: list[str], subs: int):
        """
        Записывает факт отправки алерта в историю.
        Используется для аналитики: топ каналов, динамика по ключу, тепловая карта.
        """
        self.alert_history.append({
            "ts":      datetime.now(timezone.utc).timestamp(),
            "url":     url,
            "channel": channel,
            "keys":    keys,
            "subs":    subs,
        })
        # Обрезаем в памяти
        if len(self.alert_history) > self._alert_history_max:
            self.alert_history = self.alert_history[-self._alert_history_max:]

    # ── Аналитика (заглушки для дашборда) ────────────────────────────────────

    def get_stats_for_dashboard(self) -> dict:
        """
        Возвращает агрегированную статистику по проекту.
        Будет использоваться дашбордом.

        Возвращает:
            {
                "alerts_today":     int,       # алертов за сегодня
                "top_channels":     list[dict], # топ каналов по алертам
                "top_keys":         list[dict], # топ ключей по срабатываниям
                "hourly_activity":  list[int],  # 24 значения — алерты по часам
                "tag_blocked":      dict,       # активные блокировки тегов
            }
        """
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        today_alerts = [a for a in self.alert_history if a["ts"] >= today_start]

        # Топ каналов
        channel_counts: dict[str, int] = {}
        for a in today_alerts:
            channel_counts[a["channel"]] = channel_counts.get(a["channel"], 0) + 1
        top_channels = sorted(
            [{"channel": k, "count": v} for k, v in channel_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        # Топ ключей
        key_counts: dict[str, int] = {}
        for a in today_alerts:
            for key in a.get("keys", []):
                key_counts[key] = key_counts.get(key, 0) + 1
        top_keys = sorted(
            [{"key": k, "count": v} for k, v in key_counts.items()],
            key=lambda x: -x["count"]
        )[:10]

        # Активность по часам (UTC)
        hourly = [0] * 24
        for a in today_alerts:
            hour = datetime.fromtimestamp(a["ts"], tz=timezone.utc).hour
            hourly[hour] += 1

        return {
            "alerts_today":    len(today_alerts),
            "top_channels":    top_channels,
            "top_keys":        top_keys,
            "hourly_activity": hourly,
            "tag_blocked":     dict(self._tag_blocked),
        }
