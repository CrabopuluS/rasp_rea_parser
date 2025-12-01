# rasp_rea_parser

Утилита для загрузки расписания с сайта [rasp.rea.ru](https://rasp.rea.ru/) и сохранения занятий группы в формате `.ics` для импорта в мобильный календарь.

## Запуск

```bash
python schedule_parser.py \
  --url "https://rasp.rea.ru/?q=15.14%D0%B4-%D0%B3%D0%B301%2F24%D0%BC" \
  --group "15.14д-гг01/24м" \
  --output "schedule_15_14.ics" \
  --google-output "schedule_15_14_google.ics"
```

Флаги `--url`, `--group`, `--output` и `--google-output` имеют значения по умолчанию и могут не указываться.

## Зависимости

```bash
pip install requests beautifulsoup4 tzdata python-telegram-bot  # tzdata обязательно на Windows
```

## Телеграм-бот

1. Создайте файл `.env` или экспортируйте переменные окружения:

```bash
export TELEGRAM_BOT_TOKEN="<ваш_токен>"
export SCHEDULE_URL="https://rasp.rea.ru/?q=15.14%D0%B4-%D0%B3%D0%B301%2F24%D0%BC"  # опционально
export SCHEDULE_GROUP="15.14д-гг01/24м"  # опционально
```

2. Запустите бота:

```bash
python bot.py
```

3. Добавьте бота в группу или напишите ему в личку:
   - `/ics [url] [group]` — отправит два `.ics` файла (мобильный и Google).
   - `/week [url] [group]` — покажет расписание текущей недели текстом.
   - `/plan <YYYY-MM-DD> <HH:MM> [url] [group]` — запланирует отправку текста в московском времени.
   - В личных сообщениях можно просто написать любое слово: бот ответит расписанием, а по запросу с упоминанием `ics` — файлами.
   - В группах бот отвечает на сообщения с «распис» или на упоминание его имени.
