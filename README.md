# rasp_rea_parser

Утилита для загрузки расписания с сайта [rasp.rea.ru](https://rasp.rea.ru/) и сохранения занятий группы в формате `.ics` для импорта в мобильный календарь.

## Запуск

```bash
python schedule_parser.py \
  --url "https://rasp.rea.ru/?q=15.14%D0%B4-%D0%B3%D0%B301%2F24%D0%BC" \
  --group "15.14д-гг01/24м" \
  --output "schedule_15_14.ics"
```

Флаги `--url`, `--group` и `--output` имеют значения по умолчанию и могут не указываться.

## Зависимости

```bash
pip install requests beautifulsoup4 tzdata  # tzdata обязательно на Windows
```
