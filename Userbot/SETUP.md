# Установка виртуального окружения — Feedparsproject

Инструкция для Windows 10 с доступом в интернет.

---

## Зачем это нужно

Скрипт использует модуль суммаризации LSA, которому требуется NumPy.
На вашем процессоре (QEMU Virtual CPU 2.5+) современный NumPy 2.x не работает —
он требует инструкций X86_V2, которых нет в этом процессоре.

Решение: отдельное виртуальное окружение с Python 3.11 и NumPy 1.26.4,
которые собраны под более широкий круг процессоров.

Это окружение изолировано — другие скрипты на сервере не затронуты.

---

## Шаг 1 — Установить Python 3.11

Скачайте установщик:
```
https://www.python.org/downloads/release/python-3119/
```
Файл: **Windows installer (64-bit)** → `python-3.11.9-amd64.exe`

При установке обязательно отметьте:
- ✅ **Add Python 3.11 to PATH**
- ✅ **Install launcher for all users**

После установки проверьте в CMD:
```cmd
py -3.11 --version
```
Должно вывести: `Python 3.11.9`

---

## Шаг 2 — Создать окружение и установить зависимости

Перейдите в папку скрипта и запустите:
```cmd
cd C:\Python_scripts\Feedpars
setup_env.bat
```

Скрипт сделает всё автоматически:
1. Создаст папку `.venv` с Python 3.11
2. Установит все зависимости из `requirements.txt`
3. Скачает данные NLTK (~2 МБ, один раз)

Время выполнения: 2–5 минут (зависит от скорости интернета).

---

## Шаг 3 — Проверить установку

```cmd
check_env.bat
```

Ожидаемый вывод:
```
Python version:
Python 3.11.9

Installed packages:
Name: telethon        Version: 1.36.0
Name: python-dotenv   Version: 1.0.1
Name: sumy            Version: 0.9.0
Name: nltk            Version: 3.8.1
Name: numpy           Version: 1.26.4

[OK] NumPy 1.26.4
[OK] LSA available
```

---

## Шаг 4 — Запуск

Вместо `python userbot.py` теперь всегда запускать через:
```cmd
run.bat
```

Или вручную:
```cmd
cd C:\Python_scripts\Feedpars
.venv\Scripts\activate.bat
python userbot.py
```

---

## Структура файлов после установки

```
C:\Python_scripts\Feedpars\
    userbot.py
    digest.py
    .env
    requirements.txt
    setup_env.bat       ← запустить один раз для установки
    run.bat             ← запускать бота
    check_env.bat       ← проверить что всё работает
    .venv\              ← виртуальное окружение (создаётся автоматически)
        Scripts\
            python.exe      ← Python 3.11
            activate.bat
        Lib\
            site-packages\
                numpy\      ← 1.26.4 (совместимый)
                sumy\
                telethon\
                ...
```

---

## Если что-то пошло не так

**`py -3.11` не найден после установки:**
- Закройте и откройте CMD заново
- Если не помогает: Панель управления → Система → Переменные среды →
  убедитесь что в PATH есть `C:\Python311\` и `C:\Python311\Scripts\`

**Ошибка при установке пакетов:**
```cmd
.venv\Scripts\activate.bat
pip install -r requirements.txt --retries 5
```

**NumPy всё равно не работает:**
Попробуйте ещё более старую версию:
```cmd
.venv\Scripts\activate.bat
pip install numpy==1.24.4
```

**Удалить окружение и начать заново:**
```cmd
rmdir /s /q .venv
setup_env.bat
```

---

## Обновление зависимостей

Если нужно обновить конкретный пакет не трогая остальные:
```cmd
.venv\Scripts\activate.bat
pip install telethon==1.37.0
```

Версии NumPy не трогать — менять только в крайнем случае.
