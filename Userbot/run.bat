@echo off
chcp 65001 > nul

REM Проверяем что окружение создано
if not exist .venv\Scripts\activate.bat (
    echo [ERROR] Virtual environment not found.
    echo Run setup_env.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python userbot.py
pause
