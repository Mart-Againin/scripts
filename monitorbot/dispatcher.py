"""
dispatcher.py — единственный процесс который читает getUpdates от Telegram.
Роутит команды по chat_id в файл-очередь каждого проекта.

Запуск: python dispatcher.py
(запускается автоматически через launcher.py)
"""

import os
import sys
import json
import asyncio
import aiohttp
import logging
import ssl
from pathlib import Path

# ── SSL (как в monitor.py) ────────────────────────────────────────────────────
ssl._create_default_https_context = ssl._create_unverified_context
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# ── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
LAUNCHER_ENV = os.path.join(BASE_DIR, "launcher.env")
LOG_FILE     = os.path.join(BASE_DIR, "dispatcher.log")

# ── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [dispatcher] %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
logger = logging.getLogger("dispatcher")

# ── Параметры реконнекта ──────────────────────────────────────────────────────
# При ошибке: ждём BACKOFF_BASE сек, удваиваем при каждой следующей,
# но не больше BACKOFF_MAX. При успехе — сбрасываем на BACKOFF_BASE.
BACKOFF_BASE = 5
BACKOFF_MAX  = 60

# ── Long-poll timeout ─────────────────────────────────────────────────────────
# timeout=30 в getUpdates — Telegram держит соединение до 30 сек если нет апдейтов.
# ClientTimeout(total=...) должен быть чуть больше.
POLL_TIMEOUT_SEC    = 30
SESSION_TIMEOUT_SEC = POLL_TIMEOUT_SEC + 10


# ── Читаем launcher.env ───────────────────────────────────────────────────────
def load_launcher_env(path: str) -> dict:
    """Минимальный парсер KEY=VALUE, игнорирует комментарии и пустые строки."""
    cfg = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                cfg[key.strip()] = val.strip()
    return cfg


def load_project_env(full_env: str) -> dict:
    """Читает один .env проекта, возвращает dict."""
    result = {}
    with open(full_env, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    return result


# ── Строим карту chat_id → queue_file ────────────────────────────────────────
cfg      = load_launcher_env(LAUNCHER_ENV)
ROOT     = cfg.get("PROJECT_ROOT", BASE_DIR)
PROJECTS = [p.strip() for p in cfg.get("PROJECTS", "").split(",") if p.strip()]

chat_to_queue: dict[str, str] = {}
BOT_TOKEN = ""

for env_path in PROJECTS:
    full_env = os.path.join(ROOT, env_path)
    if not os.path.exists(full_env):
        logger.warning(f"ENV not found: {full_env}")
        continue

    project_env = load_project_env(full_env)
    chat_id     = project_env.get("TELEGRAM_CHAT_ID", "").strip()
    token       = project_env.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not BOT_TOKEN and token:
        BOT_TOKEN = token

    if chat_id:
        queue_file = os.path.join(os.path.dirname(full_env), "tg_queue.json")
        chat_to_queue[chat_id] = queue_file
        logger.info(f"Registered: chat_id={chat_id} → {queue_file}")
    else:
        logger.warning(f"No TELEGRAM_CHAT_ID in {full_env}")

if not BOT_TOKEN:
    logger.error("No TELEGRAM_BOT_TOKEN found in any .env")
    sys.exit(1)

if not chat_to_queue:
    logger.error("No chat_id mappings found")
    sys.exit(1)

logger.info(f"Bot token: {BOT_TOKEN[:10]}...")
logger.info(f"Routing {len(chat_to_queue)} chats")


# ── Запись в очередь ──────────────────────────────────────────────────────────
# asyncio.Lock() защищает от одновременной записи внутри этого процесса.
_queue_lock = asyncio.Lock()

async def write_to_queue(queue_file: str, update: dict):
    """Атомарно добавляет апдейт в файл-очередь проекта."""
    async with _queue_lock:
        try:
            if os.path.exists(queue_file):
                with open(queue_file, "r", encoding="utf-8") as f:
                    queue = json.load(f)
            else:
                queue = []

            queue.append(update)

            # Атомарная запись через tmp-файл
            tmp = queue_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(queue, f, ensure_ascii=False)
            os.replace(tmp, queue_file)

        except Exception as e:
            logger.error(f"Queue write error {queue_file}: {e}")


# ── Создание сессии ───────────────────────────────────────────────────────────
def _new_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT, force_close=True)
    timeout   = aiohttp.ClientTimeout(total=SESSION_TIMEOUT_SEC)
    return aiohttp.ClientSession(connector=connector, timeout=timeout)


async def _close_session(session: aiohttp.ClientSession):
    """Закрывает сессию и коннектор, подавляя SSLError при принудительном разрыве."""
    try:
        await session.close()
    except Exception:
        pass
    # Даём event loop время закрыть SSL-сокеты (обходит WinError 10054)
    await asyncio.sleep(0.25)


# ── Поллинг ───────────────────────────────────────────────────────────────────
async def run():
    tg_url  = f"https://api.telegram.org/bot{BOT_TOKEN}"
    offset  = 0
    session = _new_session()
    backoff = BACKOFF_BASE

    logger.info("Dispatcher started — polling Telegram...")

    while True:
        try:
            async with session.get(
                f"{tg_url}/getUpdates",
                params={"offset": offset, "timeout": POLL_TIMEOUT_SEC},
            ) as resp:
                data = await resp.json()

            if not data.get("ok"):
                error_code = data.get("error_code", 0)
                retry_after = data.get("parameters", {}).get("retry_after", backoff)

                if error_code == 429:
                    # Too Many Requests — ждём столько, сколько Telegram просит
                    logger.warning(f"Rate limited by Telegram, retry after {retry_after}s")
                    await asyncio.sleep(retry_after)
                elif error_code == 409:
                    # Conflict: другой процесс держит polling — это серьёзно
                    logger.error("Conflict: another bot instance is polling. Waiting 30s.")
                    await asyncio.sleep(30)
                else:
                    logger.warning(f"getUpdates error: {data}")
                    await asyncio.sleep(backoff)

                continue

            # Успех — сбрасываем backoff
            backoff = BACKOFF_BASE

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue

                chat_id = str(msg.get("chat", {}).get("id", ""))
                if not chat_id:
                    continue

                queue_file = chat_to_queue.get(chat_id)
                if queue_file:
                    logger.info(f"Routed update {update['update_id']} → {chat_id}")
                    await write_to_queue(queue_file, update)
                else:
                    logger.debug(f"Unknown chat_id={chat_id} — ignored")

        except (
            aiohttp.ClientConnectorError,
            aiohttp.ClientConnectorDNSError,
            aiohttp.ServerDisconnectedError,
            asyncio.TimeoutError,
            TimeoutError,
        ) as e:
            # Сетевые ошибки — exponential backoff, пересоздаём сессию
            logger.error(f"Polling error: {type(e).__name__}: {e}")
            await _close_session(session)
            session = None

            logger.info(f"Reconnect in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

            session = _new_session()

        except Exception as e:
            # Неожиданные ошибки — логируем подробно, не роняем процесс
            logger.exception(f"Unexpected error: {e}")
            await _close_session(session)
            session = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            session = _new_session()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Dispatcher stopped by user.")
