@echo off
chcp 65001 > nul
echo.
echo === Environment check ===
echo.

if not exist .venv\Scripts\activate.bat (
    echo [ERROR] .venv not found. Run setup_env.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo Python version:
python --version
echo.

echo Installed packages:
pip show telethon python-dotenv sumy nltk numpy 2>nul | findstr "^Name\|^Version"
echo.

echo NumPy import test:
python -c "import numpy; print('[OK] NumPy', numpy.__version__)"
if errorlevel 1 echo [FAIL] NumPy import failed

echo sumy LSA test:
python -c "from sumy.summarizers.lsa import LsaSummarizer; print('[OK] LSA available')"
if errorlevel 1 echo [FAIL] LSA import failed

echo.
pause
