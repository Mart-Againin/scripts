# TG Analytics

Инструмент для автоматического сбора и анализа статистики постов в Telegram-каналах.
Собирает данные через 24 часа после публикации каждого поста и формирует Excel-отчёты с ключевыми метриками.

---

## Возможности

- Автоматический сбор статистики по постам через ровно 24 часа после публикации
- Суточные, недельные и месячные Excel-отчёты
- Отправка отчётов прямо в Telegram
- Поддержка нескольких каналов одновременно
- Режим отладки с ежедневными тестовыми отчётами
- Один файл для запуска — никаких внешних планировщиков

## Метрики

| Метрика | Описание |
|---|---|
| Views | Охват поста |
| Reactions | Сумма всех реакций |
| Comments | Количество комментариев |
| Forwards | Количество пересылок |
| Votes | Сумма голосов в опросах |
| Actions | Суммарная активность (Views + React + Comments + Forwards + Votes) |
| ERR % | Engagement Rate by Reach — Actions / Views × 100% |
| ER % | Классический ER — (React+Comments+Fwd+Votes) / Subscribers × 100% |
| ERview % | Вовлечённость среди просмотревших |
| VRpost % | View Rate — Views / Subscribers × 100% |
| Viral Factor % | Forwards / Views × 100% |
| Reply Rate % | Comments / Views × 100% |
| Reach Multiplier | Views / Subscribers |
| CQI | Content Quality Index — взвешенный индекс качества контента |

**CQI** считается по формуле:
```
(Reactions × 1 + Votes × 2 + Forwards × 4 + Comments × 5) / Views × 100
```
Веса настраиваются в `.env`.

---

## Требования

- Python 3.11+
- Telegram-аккаунт с доступом к анализируемым каналам
- API-ключи с [my.telegram.org](https://my.telegram.org)

## Установка

```bash
git clone https://github.com/your-username/tg-analytics.git
cd tg-analytics
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env`:

```env
API_ID=your_api_id
API_HASH=your_api_hash
CHANNELS=@channel_one,@channel_two
REPORT_RECIPIENT_ID=your_telegram_id
DEBUG_RECIPIENT_ID=test_account_telegram_id
DEBUG=false
TIMEZONE=Europe/Moscow
```

## Запуск

```bash
python main.py
```

При первом запуске скрипт проведёт авторизацию через номер телефона.
После этого работает автономно.

**Расписание:**
- каждый час — сбор новых постов и финальных срезов
- каждый понедельник 10:00 — недельный отчёт
- 3-е число каждого месяца 10:00 — месячный отчёт
- ежедневно в заданное время — суточный отчёт (только при `DEBUG=true`)

## Структура проекта

```
main.py         — единая точка запуска и планировщик
snapshot.py     — сборщик статистики (регистрация постов, финальные срезы)
report.py       — генератор Excel-отчётов и отправка в Telegram
auth.py         — авторизация (вызывается из main.py автоматически)
.env.example    — шаблон конфигурации
requirements.txt
```

## Архитектура хранения данных

Каждый пост регистрируется в `registry/<channel>/registry.json` с меткой `deadline = published_at + 24h`.

Скрипт каждый час проверяет: если `now >= deadline` и финальный срез ещё не снят — снимает статистику и помечает пост как `is_final: true`.

После генерации месячного отчёта данные за закрытый месяц переносятся в `archive/` и удаляются из рабочего реестра.

Все три типа отчётов (суточный, недельный, месячный) строятся одними и теми же функциями — правка шаблона в одном месте применяется ко всем.

## Режим отладки

```env
DEBUG=true
DEBUG_RECIPIENT_ID=123456789
DAILY_REPORT_TIME=12:00
```

В режиме отладки все отчёты уходят на `DEBUG_RECIPIENT_ID`, логи расширены до уровня DEBUG, суточный отчёт генерируется ежедневно.

## Зависимости

```
telethon>=1.36.0
openpyxl>=3.1.0
python-dotenv>=1.0.0
aiohttp>=3.9.0
pytz>=2024.1
```

## Лицензия

MIT
