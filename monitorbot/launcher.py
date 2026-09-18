"""
launcher.py — запуск всех проектов Feedparsproject из одного файла.

Использование:
  python launcher.py              — запустить все проекты из launcher.env
  python launcher.py project_a   — запустить только project_a
  python launcher.py project_a project_b  — запустить несколько

Каждый проект открывается в отдельном CMD-окне с заголовком и автоперезапуском.
Для каждого проекта генерируется run_<project>.bat рядом со скриптом.
"""

import os
import sys
import time
import subprocess


# ── Читаем launcher.env ───────────────────────────────────────────────────────

def load_launcher_env(path: str) -> dict:
    cfg = {}
    if not os.path.exists(path):
        print(f"[ERROR] Файл не найден: {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                cfg[key.strip()] = val.strip()
    return cfg


# ── Генерация .bat и запуск в отдельном CMD ──────────────────────────────────

def launch_project(root: str, script: str, env_path: str, python_cmd: str):
    """
    Генерирует run_<project>.bat с настоящей меткой :loop и запускает его
    в новом CMD-окне. goto loop корректно работает только в .bat файле,
    не в инлайн-команде cmd /k "...".
    """
    project_name = env_path.replace("\\", "/").split("/")[0]
    title        = f"Monitor - {project_name}"
    bat_path     = os.path.join(root, f"run_{project_name}.bat")

    # CMD на Windows читает .bat в cp1251 — используем только ASCII в тексте
    bat_lines = [
        "@echo off",
        f"title {title}",
        f"cd /d {root}",
        ":loop",
        f"echo [%date% %time%] Starting {project_name}...",
        f"{python_cmd} {script} --env {env_path}",
        f"echo [%date% %time%] Stopped (code: %errorlevel%). Restarting in 5 sec...",
        "timeout /t 5 /nobreak",
        "goto loop",
    ]

    with open(bat_path, "w", encoding="ascii", errors="replace") as f:
        f.write("\r\n".join(bat_lines) + "\r\n")

    subprocess.Popen(
        ["cmd.exe", "/k", bat_path],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    print(f"  OK {project_name}  ({bat_path})")


# ── Главная логика ────────────────────────────────────────────────────────────

def main():
    launcher_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.env")
    cfg = load_launcher_env(launcher_env_path)

    root         = cfg.get("PROJECT_ROOT", os.path.dirname(os.path.abspath(__file__)))
    script       = cfg.get("SCRIPT", "monitor.py")
    python_cmd   = cfg.get("PYTHON_CMD", "python")   # новый параметр: py -3.13 / python / python3
    delay        = float(cfg.get("LAUNCH_DELAY", 2))
    all_projects = [p.strip() for p in cfg.get("PROJECTS", "").split(",") if p.strip()]

    if not all_projects:
        print("[ERROR] PROJECTS пустой в launcher.env")
        sys.exit(1)

    # Если переданы аргументы — фильтруем по имени проекта
    filter_args = sys.argv[1:]
    if filter_args:
        normalized = [a if "/.env" in a or "\\.env" in a else f"{a}/.env" for a in filter_args]
        projects   = [p for p in all_projects if p in normalized]
        if not projects:
            print(f"[ERROR] Проекты не найдены: {filter_args}")
            print(f"Доступные: {all_projects}")
            sys.exit(1)
    else:
        projects = all_projects

    print(f"\nFeedparsproject Launcher")
    print(f"Root:     {root}")
    print(f"Script:   {script}")
    print(f"Python:   {python_cmd}")
    print(f"Projects: {len(projects)}\n")

    for env_path in projects:
        launch_project(root, script, env_path, python_cmd)
        if delay > 0 and env_path != projects[-1]:
            time.sleep(delay)

    print(f"\nAll windows launched. Launcher done.")


if __name__ == "__main__":
    main()
