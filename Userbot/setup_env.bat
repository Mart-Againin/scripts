@echo off
chcp 65001 > nul
echo.
echo === Feedparsproject: setup virtual environment ===
echo.

REM Проверяем что Python 3.11 доступен
py -3.11 --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11 not found.
    echo.
    echo Download: https://www.python.org/downloads/release/python-3119/
    echo File: Windows installer (64-bit) - python-3.11.9-amd64.exe
    echo.
    echo IMPORTANT during install:
    echo   - Check "Add Python to PATH"
    echo   - Check "Install for all users" (optional)
    echo.
    pause
    exit /b 1
)

echo [OK] Python 3.11 found
py -3.11 --version
echo.

REM Создаём виртуальное окружение в папке .venv
if exist .venv (
    echo [INFO] .venv already exists, skipping creation
) else (
    echo [INFO] Creating virtual environment...
    py -3.11 -m venv .venv
    echo [OK] .venv created
)
echo.

REM Активируем и устанавливаем зависимости
echo [INFO] Installing dependencies from requirements.txt...
call .venv\Scripts\activate.bat

python -m pip install --upgrade pip --quiet
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed. Check internet connection and try again.
    pause
    exit /b 1
)

echo.
echo [INFO] Downloading NLTK punkt_tab (one time, ~2 MB)...
python -c "import nltk; nltk.download('punkt_tab', quiet=True); nltk.download('punkt', quiet=True)"

echo.
echo ================================================
echo   Setup complete. Run the bot with: run.bat
echo ================================================
echo.
pause
