"""
restart_helper.py — перезапуск процесса бота с подтверждением.

Почему не os.execv + client.disconnect() (как было раньше):
  client.disconnect() внутри обработчика команды отменяет сам этот
  обработчик (так задокументировано в Telethon), поэтому следующая строка
  с os.execv никогда не выполнялась. Новый процесс не запускался, а старый
  через минуту переподключался в tick() и продолжал работать на СТАРОМ
  коде — перезапуск был только видимостью.

Как устроено сейчас:
  1. Старый процесс пишет маркер (restart_marker.json), запускает НОВЫЙ
     процесс (subprocess.Popen) и ждёт несколько секунд: если новый
     процесс сразу упал (ошибка в коде/импорте) — перезапуск отменяется,
     старый продолжает работать, в Telegram приходит ❌ с причиной.
  2. Если новый жив — старый корректно отключается и завершается.
  3. НОВЫЙ процесс при старте видит маркер, ждёт, пока старый реально
     исчезнет из списка процессов, подключается к Telegram и сам присылает
     ✅ (или ⚠️, если старый не завершился). Подтверждение приходит именно
     от нового процесса, поэтому получить его без реального перезапуска
     невозможно.
  Все шаги пишутся в лог с префиксом [RESTART] / [START].
"""

import asyncio
import inspect
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("restart")

MARKER_PATH = Path(__file__).resolve().parent / "restart_marker.json"
MARKER_MAX_AGE_SEC = 300      # маркер старше — считаем устаревшим
OLD_EXIT_WAIT_SEC = 30        # сколько новый процесс ждёт завершения старого
CHILD_HEALTH_CHECK_SEC = 6    # сколько старый ждёт, что новый не упал сразу

# True с момента начала перезапуска. tick() в main.py при этом не должен
# переподключаться к Telegram (иначе старый процесс "оживёт" рядом с новым).
RESTARTING = False

# Файлы, версии которых показываем в подтверждении (по дате изменения на
# диске в момент старта) — чтобы сразу видеть, какой код загружен.
WATCHED_FILES = [
    "main.py", "google_sheets.py", "report.py", "snapshot.py",
    "historical.py", "dashboard_report.py", "dashboard_metrics.py",
]


# ── Проверка процесса ────────────────────────────────────────────────────
def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ── Маркер ───────────────────────────────────────────────────────────────
def _write_marker(data: dict) -> None:
    MARKER_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_marker() -> dict | None:
    if not MARKER_PATH.exists():
        return None
    try:
        return json.loads(MARKER_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[RESTART] Маркер повреждён ({e}) — удаляю")
        clear_marker()
        return None


def clear_marker() -> None:
    try:
        MARKER_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"[RESTART] Не удалось удалить маркер: {e}")


# ── Запуск нового процесса ───────────────────────────────────────────────
def _build_spawn_argv() -> list[str]:
    # Абсолютный путь к скрипту — не зависит от того, как запускали раньше.
    return [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]


def _spawn(argv: list[str]) -> subprocess.Popen:
    kwargs: dict = {"cwd": os.getcwd()}
    if os.name == "nt":
        # Новое окно консоли — видно лог нового процесса. Отключается
        # RESTART_NEW_CONSOLE=0 в .env (тогда новый процесс пишет в ту же
        # консоль). CREATE_NEW_PROCESS_GROUP НЕ используем: он отключает
        # Ctrl+C в новом процессе.
        if os.getenv("RESTART_NEW_CONSOLE", "1") == "1":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def _file_versions(base_dir: Path | None = None) -> list[str]:
    base = base_dir or MARKER_PATH.parent
    lines = []
    for name in WATCHED_FILES:
        p = base / name
        if p.exists():
            ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%y %H:%M")
            lines.append(f"{name} — {ts}")
        else:
            lines.append(f"{name} — не найден")
    return lines


async def _safe_send(send, text: str) -> None:
    try:
        res = send(text)
        if inspect.isawaitable(res):
            await res
    except Exception as e:
        log.error(f"[RESTART] Не удалось отправить сообщение в Telegram: {e}")


async def _safe_disconnect(disconnect) -> None:
    try:
        res = disconnect()
        if inspect.isawaitable(res):
            await res
    except Exception as e:
        log.warning(f"[RESTART] Ошибка при отключении клиента: {e}")


# ── Старый процесс: инициатор перезапуска ────────────────────────────────
async def perform_restart(chat_id, send, disconnect, spawn_argv=None,
                           health_check_sec: float = CHILD_HEALTH_CHECK_SEC) -> None:
    """
    Запускать как ОТДЕЛЬНУЮ задачу (asyncio.create_task), не внутри самого
    обработчика команды: disconnect() отменяет задачи обработчиков.
    """
    global RESTARTING
    old_pid = os.getpid()
    proc = None
    RESTARTING = True
    log.info(f"[RESTART] ══ Перезапуск инициирован: старый PID {old_pid}")
    try:
        _write_marker({
            "old_pid": old_pid,
            "chat_id": chat_id,
            "ts": time.time(),
            "started_at": datetime.now().strftime("%d.%m.%y %H:%M:%S"),
        })
        argv = spawn_argv or _build_spawn_argv()
        proc = _spawn(argv)
        log.info(f"[RESTART] Запущен новый процесс PID {proc.pid}: {argv}")

        await asyncio.sleep(health_check_sec)
        code = proc.poll()
        if code is not None:
            raise RuntimeError(
                f"новый процесс сразу завершился (код {code}) — вероятно, ошибка "
                f"в коде или импортах; подробности в окне/логе нового процесса")

        log.info(f"[RESTART] Новый процесс PID {proc.pid} жив спустя "
                 f"{health_check_sec} с — завершаю старый (PID {old_pid})")
        await _safe_send(
            send,
            f"🔄 Новый процесс запущен (PID {proc.pid}). Завершаю старый "
            f"(PID {old_pid}). Подтверждение «✅» придёт от нового процесса — "
            f"если его нет в течение минуты, смотрите окно/лог нового процесса.")
        await _safe_disconnect(disconnect)
        log.info("[RESTART] Старый процесс завершает работу")
        logging.shutdown()
        os._exit(0)

    except Exception as e:
        RESTARTING = False
        clear_marker()
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        log.error(f"[RESTART] ✗ Перезапуск НЕ выполнен: {e}", exc_info=True)
        await _safe_send(
            send,
            f"❌ Перезапуск НЕ выполнен: {e}\n"
            f"Бот продолжает работать на старом процессе (PID {old_pid}), "
            f"код не обновился.")


# ── Новый процесс: ожидание старого и подтверждение ──────────────────────
def prepare_after_restart() -> dict | None:
    """
    Вызывать в самом начале main(), ДО подключения к Telegram. Если это
    запуск после /restart — ждёт, пока старый процесс исчезнет (иначе два
    процесса на одной сессии Telegram), и возвращает данные маркера.
    """
    marker = _read_marker()
    if not marker:
        return None

    age = time.time() - marker.get("ts", 0)
    if age > MARKER_MAX_AGE_SEC:
        log.warning(f"[RESTART] Найден устаревший маркер перезапуска "
                    f"({age:.0f} с назад) — игнорирую")
        clear_marker()
        return None

    old_pid = marker.get("old_pid")
    log.info(f"[RESTART] Запуск после перезапуска (новый PID {os.getpid()}): "
             f"жду завершения старого процесса PID {old_pid}")
    deadline = time.time() + OLD_EXIT_WAIT_SEC
    while pid_alive(old_pid) and time.time() < deadline:
        time.sleep(0.5)

    old_gone = not pid_alive(old_pid)
    if old_gone:
        log.info(f"[RESTART] Старый процесс PID {old_pid} завершён")
    else:
        log.error(f"[RESTART] ⚠ Старый процесс PID {old_pid} НЕ завершился "
                  f"за {OLD_EXIT_WAIT_SEC} с")
    marker["old_gone"] = old_gone
    marker["new_pid"] = os.getpid()
    return marker


async def report_restart_result(send, marker: dict | None) -> None:
    """Вызывать после подключения и авторизации. Шлёт итог в Telegram и в лог."""
    if not marker:
        return
    took = time.time() - marker.get("ts", time.time())
    versions = "\n".join(f"  • {l}" for l in _file_versions())
    if marker.get("old_gone"):
        text = (f"✅ Перезапуск выполнен успешно\n"
                f"Новый процесс: PID {marker['new_pid']}\n"
                f"Старый процесс: PID {marker['old_pid']} завершён\n"
                f"Заняло: {took:.0f} с\n"
                f"Файлы на диске при старте (дата изменения):\n{versions}")
    else:
        text = (f"⚠️ Бот перезапущен (PID {marker['new_pid']}), НО старый процесс "
                f"PID {marker['old_pid']} не завершился за {OLD_EXIT_WAIT_SEC} с.\n"
                f"Завершите его вручную (taskkill /F /PID {marker['old_pid']}), "
                f"иначе два процесса будут конфликтовать за сессию Telegram.\n"
                f"Файлы на диске при старте:\n{versions}")
    log.info("[RESTART] " + text.replace("\n", " | "))
    await _safe_send(send, text)
    clear_marker()


def log_start_banner() -> None:
    """Строка в лог при каждом старте процесса — для контроля версий."""
    log.info(f"[START] Процесс запущен: PID {os.getpid()}")
    for line in _file_versions():
        log.info(f"[START]   {line}")
