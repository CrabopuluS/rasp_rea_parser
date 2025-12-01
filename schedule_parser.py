"""
Парсер расписания РЭУ им. Г.В. Плеханова для выгрузки занятий в .ics календарь.

Зависимости (pip):
    pip install requests beautifulsoup4 tzdata
    pip install pytz  # для Python < 3.9

Пример запуска:
    python schedule_parser.py \
        --url "https://rasp.rea.ru/?q=15.14%D0%B4-%D0%B3%D0%B301%2F24%D0%BC" \
        --group "15.14д-гг01/24м" \
        --output "schedule_15_14.ics"
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

try:  # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - fallback for older Python
    from pytz import timezone as ZoneInfo  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

DEFAULT_URL = "https://rasp.rea.ru/?q=15.14%D0%B4-%D0%B3%D0%B301%2F24%D0%BC"
DEFAULT_GROUP = "15.14д-гг01/24м"
REQUEST_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://rasp.rea.ru/",
}


def load_moscow_tz() -> dt.tzinfo:
    """Возвращает временную зону Москвы с запасным вариантом.

    На Windows может отсутствовать системная база IANA. В этом случае
    используем фиксированный UTC+3 и выводим предупреждение. Для
    корректных переходов на летнее время рекомендуется установить
    дополнительный пакет `tzdata` (см. зависимостей в шапке).
    """

    try:
        return ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:
        logging.warning(
            "Не найден часовой пояс Europe/Moscow. Используем фиксированный UTC+3. "
            "Установите пакет 'tzdata' для полной поддержки.",
        )
        return dt.timezone(dt.timedelta(hours=3), name="Europe/Moscow")


MOSCOW_TZ = load_moscow_tz()


@dataclass
class ScheduleEvent:
    """Описание одного занятия."""

    date: dt.date
    start_time: dt.time
    end_time: dt.time
    title: str
    lesson_type: Optional[str] = None
    teacher: Optional[str] = None
    location: Optional[str] = None
    extra_info: Optional[str] = None
    element_id: Optional[str] = None


def fetch_html(url: str, group: str, session: requests.Session) -> str:
    """Получает HTML блока расписания для указанной группы.

    Функция повторяет логику фронтенда сайта: сначала уточняет ключ группы
    через `/Schedule/SearchBarSuggestions`, затем запрашивает HTML расписания
    через `/Schedule/ScheduleCard`.
    """

    selection_key = extract_selection_from_url(url) or group
    normalized_key = normalize_selection(selection_key, session)
    try:
        response = session.get(
            "https://rasp.rea.ru/Schedule/ScheduleCard",
            params={"selection": normalized_key},
            headers=REQUEST_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - сеть
        logging.error("Не удалось загрузить расписание: %s", exc)
        return ""
    return response.text


def normalize_selection(selection: str, session: requests.Session) -> str:
    """Ищет точный ключ группы через подсказки поиска.

    Если API подсказок недоступно или не вернуло результатов, возвращается
    исходное значение selection.
    """

    try:
        response = session.get(
            "https://rasp.rea.ru/Schedule/SearchBarSuggestions",
            params={"searchFor": selection},
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:  # pragma: no cover - сеть
        logging.warning("Не удалось уточнить ключ группы: %s", exc)
        return selection
    except ValueError:
        logging.warning("API подсказок вернуло некорректный ответ")
        return selection

    for item in data:
        if item.get("key", "").lower() == selection.lower():
            return item["key"]
    return selection


def extract_selection_from_url(url: str) -> Optional[str]:
    """Возвращает значение параметра `q` из URL, если он указан."""

    match = re.search(r"[?&]q=([^&#]+)", url)
    if not match:
        return None
    return requests.utils.unquote(match.group(1))


def parse_schedule(html: str, session: requests.Session) -> List[ScheduleEvent]:
    """Парсит HTML расписания в список событий."""

    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    events: List[ScheduleEvent] = []
    for table in soup.find_all("table"):
        header = table.find("h5")
        if not header:
            continue
        date = parse_date_from_header(header.get_text(strip=True))
        if not date:
            logging.warning("Не удалось разобрать дату в заголовке: %s", header)
            continue
        for anchor in table.find_all("a", class_="task"):
            event = build_event_from_row(anchor, date, session)
            if event:
                events.append(event)
    return events


def parse_date_from_header(text: str) -> Optional[dt.date]:
    """Извлекает дату формата dd.mm.yyyy из заголовка таблицы."""

    match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if not match:
        return None
    return dt.datetime.strptime(match.group(1), "%d.%m.%Y").date()


def build_event_from_row(
    anchor: BeautifulSoup, date: dt.date, session: requests.Session
) -> Optional[ScheduleEvent]:
    """Собирает данные о занятии из строки таблицы."""

    row = anchor.find_parent("tr")
    if not row:
        return None
    time_cell = row.find("td")
    start_time, end_time = parse_timeslot(time_cell)
    if not start_time or not end_time:
        logging.info("Пропускаем строку без времени: %s", anchor.get_text(strip=True))
        return None

    strings = list(anchor.stripped_strings)
    if not strings:
        return None
    title = strings[0]
    lesson_type = strings[1] if len(strings) > 1 else None
    location = clean_location(strings[2:]) if len(strings) > 2 else None

    teacher, extra_info = fetch_details(anchor.get("data-elementid"), session)
    return ScheduleEvent(
        date=date,
        start_time=start_time,
        end_time=end_time,
        title=title,
        lesson_type=lesson_type,
        teacher=teacher,
        location=location,
        extra_info=extra_info,
        element_id=anchor.get("data-elementid"),
    )


def parse_timeslot(cell: Optional[BeautifulSoup]) -> Tuple[Optional[dt.time], Optional[dt.time]]:
    """Разбирает ячейку времени: ожидается номер пары, время начала и конца."""

    if not cell:
        return None, None
    parts = [part for part in cell.stripped_strings if not part.endswith("пара")]
    if len(parts) < 2:
        return None, None
    try:
        start_time = dt.datetime.strptime(parts[0], "%H:%M").time()
        end_time = dt.datetime.strptime(parts[1], "%H:%M").time()
    except ValueError:
        return None, None
    return start_time, end_time


def clean_location(chunks: Iterable[str]) -> str:
    """Нормализует строку с аудиторией/корпусом."""

    joined = " ".join(chunks)
    return " ".join(joined.replace("\n", " ").split())


def fetch_details(
    element_id: Optional[str], session: requests.Session
) -> Tuple[Optional[str], Optional[str]]:
    """Получает подробности занятия (преподаватель, доп. информация)."""

    if not element_id:
        return None, None
    try:
        response = session.get(
            "https://rasp.rea.ru/Schedule/GetDetailsById",
            params={"id": element_id},
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - сеть
        logging.warning("Не удалось получить детали занятия %s: %s", element_id, exc)
        return None, None

    soup = BeautifulSoup(response.text, "html.parser")
    body = soup.find("div", class_="element-info-body")
    if not body:
        return None, None

    lines = [line.strip() for line in body.get_text("\n").splitlines() if line.strip()]
    teacher = extract_teacher(lines)
    extra_info = extract_extra_info(lines)
    return teacher, extra_info


def extract_teacher(lines: List[str]) -> Optional[str]:
    """Ищет строку с ФИО преподавателя после метки 'Преподаватель:'."""

    for idx, line in enumerate(lines):
        if "Преподаватель" in line:
            for candidate in lines[idx + 1 :]:
                if candidate.lower() != "school":
                    return candidate
    return None


def extract_extra_info(lines: List[str]) -> Optional[str]:
    """Собирает вспомогательные сведения: площадка, кафедра и т.п."""

    extras: List[str] = []
    for line in lines:
        if line.startswith("Площадка") or line.startswith("("):
            extras.append(line)
    return ", ".join(extras) if extras else None


def build_ics(events: List[ScheduleEvent], output_path: Path) -> None:
    """Создает .ics файл со списком занятий."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//REA Schedule Parser//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:Europe/Moscow",
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Moscow",
        "BEGIN:STANDARD",
        "DTSTART:19300101T000000",
        "TZOFFSETFROM:+0300",
        "TZOFFSETTO:+0300",
        "TZNAME:MSK",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for event in sorted(events, key=lambda e: (e.date, e.start_time)):
        dt_start = dt.datetime.combine(event.date, event.start_time, tzinfo=MOSCOW_TZ)
        dt_end = dt.datetime.combine(event.date, event.end_time, tzinfo=MOSCOW_TZ)
        summary = event.title if not event.lesson_type else f"{event.title} ({event.lesson_type})"
        description_parts = []
        if event.teacher:
            description_parts.append(f"Преподаватель: {event.teacher}")
        if event.location:
            description_parts.append(f"Аудитория: {event.location}")
        if event.extra_info:
            description_parts.append(event.extra_info)
        description = "\n".join(description_parts)

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.element_id or hash(summary + str(dt_start))}@rasp.rea.ru",
                f"SUMMARY:{escape_ics(summary)}",
                f"DTSTART;TZID=Europe/Moscow:{dt_start.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID=Europe/Moscow:{dt_end.strftime('%Y%m%dT%H%M%S')}",
                f"DESCRIPTION:{escape_ics(description)}",
                f"LOCATION:{escape_ics(event.location or '')}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def escape_ics(value: str) -> str:
    """Экранирует специальные символы для iCalendar."""

    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def slugify_group_name(group: str) -> str:
    """Создает удобочитаемое имя файла из названия группы."""

    normalized = unicodedata.normalize("NFKD", group.lower())
    transliterated = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    transliterated = transliterated.replace("/", "_").replace(" ", "_")
    transliterated = transliterated.replace("д", "d").replace("г", "g").replace("м", "m")
    transliterated = re.sub(r"[^a-z0-9_.-]", "", transliterated)
    return transliterated or "schedule"


def build_arg_parser() -> argparse.ArgumentParser:
    """Создает аргумент-парсер CLI."""

    parser = argparse.ArgumentParser(
        description="Скачивает расписание и формирует .ics файл для календаря",
    )
    parser.add_argument(
        "-u",
        "--url",
        default=DEFAULT_URL,
        help="URL страницы расписания",
    )
    parser.add_argument(
        "-g",
        "--group",
        default=DEFAULT_GROUP,
        help="Код группы для поиска",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Путь к итоговому .ics файлу",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Показывать отладочную информацию",
    )
    return parser


def main() -> None:
    """Точка входа CLI."""

    parser = build_arg_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    output_path = args.output
    if not output_path:
        group_slug = slugify_group_name(args.group)
        output_path = Path.cwd() / f"schedule_{group_slug}.ics"

    session = requests.Session()
    html = fetch_html(args.url, args.group, session)
    events = parse_schedule(html, session)
    if not events:
        logging.error("Не найдено ни одного занятия для указанной группы")
        sys.exit(1)

    build_ics(events, output_path)
    logging.info("Сохранено занятий: %s", len(events))
    logging.info("Файл: %s", output_path)


if __name__ == "__main__":
    main()
