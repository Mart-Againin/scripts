"""
rate_limiter.py — shared file-based rate limiter for TGStat API.

Все процессы monitor.py обращаются к одному файлу tgstat_lock.json
и соблюдают минимальный интервал запросов к TGStat, чтобы не получать 429.

Файл блокировки: {"last_request_ts": 1234567890.123, "project": "name"}
Каждый процесс перед запросом вызывает acquire() — он ждёт нужное время,
затем атомарно обновляет временну́ю метку.
"""

import asyncio
import json
import os
import time
import logging

logger = logging.getLogger(__name__)

# Путь по умолчанию — рядом со скриптом
DEFAULT_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tgstat_lock.json")


class TGStatRateLimiter:
    """
    Межпроцессный rate limiter через файл-блокировку.

    Защита двухуровневая:
      - asyncio.Lock()  — между корутинами внутри одного процесса (O(1))
      - файл JSON       — между разными процессами monitor.py (project_a, project_b, ...)

    MIN_INTERVAL_SEC читается при создании экземпляра, а не при импорте модуля —
    это гарантирует что load_dotenv() уже отработал к этому моменту.
    """

    def __init__(self, lock_file: str, project_name: str = "default"):
        self.lock_file    = lock_file
        self.project_name = project_name
        self._local_lock  = asyncio.Lock()

        # Читаем здесь, не на уровне модуля — load_dotenv() уже выполнен
        self.min_interval = float(os.getenv("TGSTAT_MIN_INTERVAL_SEC", "1.5"))
        logger.debug(f"[{project_name}] RateLimiter min_interval={self.min_interval}s, lock={lock_file}")

    def _read(self) -> dict:
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_request_ts": 0.0, "project": ""}

    def _write(self, ts: float):
        """Атомарная запись через tmp-файл (защита от повреждения при сбое)."""
        tmp = self.lock_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"last_request_ts": ts, "project": self.project_name}, f)
            os.replace(tmp, self.lock_file)
        except Exception as e:
            logger.warning(f"[{self.project_name}] RateLimiter write error: {e}")

    async def acquire(self):
        """
        Ждёт, пока с последнего запроса (любого проекта) не пройдёт min_interval секунд,
        затем атомарно обновляет метку и возвращает управление.
        """
        async with self._local_lock:
            while True:
                last_ts = self._read().get("last_request_ts", 0.0)
                elapsed = time.time() - last_ts
                if elapsed >= self.min_interval:
                    self._write(time.time())
                    return
                wait = self.min_interval - elapsed
                logger.debug(f"[{self.project_name}] RateLimiter wait {wait:.2f}s")
                await asyncio.sleep(wait)
