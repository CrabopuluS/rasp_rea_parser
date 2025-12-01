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

SUBJECT_SHORTCUTS = {
    "Организация предоставления государственных услуг": "ОПГУ",
    "Организация экспертных, общественных советов, проведение экспертиз": "ОргСоветов",
    "Партийно-политические элиты и электоральные процессы": "ППЭЭП",
    "Репутационный менеджмент в государственном управлении": "РМГУ",
    "Политическая имиджелогия": "ПИ",
    "Кризис-менеджмент: лидерство в условиях кризиса или конфликта": "КМ",
    "Общественное мнение и политическое лидерство": "ОМПЛ",
    "Технологии государственного контроля и аудита": "ТГКА",
    "Проектное управление в государственном секторе": "ПУГС",
}

GOOGLE_COLOR_MAP = {
    "seminar": "#F4511E",  # tangerine
    "lecture": "#F6BF26",  # banana
    "exam": "#D50000",  # tomato
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
    pair_number: Optional[int] = None


def fetch_html(url: str, group: str, session: requests.Session) -> str:
    """Получает HTML блока расписания для указанной группы.

    Функция повторяет логику фронтенда сайта: сначала уточняет ключ группы
    через `/Schedule/SearchBarSuggestions`, затем запрашивает HTML расписания
    через `/Schedule/ScheduleCard`.
    """
    if not url or not group:
        logging.error("fetch_html: пустые url или group")
        return ""

    try:
        selection_key = extract_selection_from_url(url) or group
        normalized_key = normalize_selection(selection_key, session)
        response = session.get(
            "https://rasp.rea.ru/Schedule/ScheduleCard",
            params={"selection": normalized_key},
            headers=REQUEST_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
    except requests.Timeout:
        logging.error("Timeout при загрузке расписания (url=%s)", url)
        return ""
    except requests.ConnectionError as exc:
        logging.error("Ошибка соединения: %s", exc)
        return ""
    except requests.RequestException as exc:
        logging.error("Не удалось загрузить расписание: %s", exc)
        return ""
    return response.text


def normalize_selection(selection: str, session: requests.Session) -> str:
    """Ищет точный ключ группы через подсказки поиска.

    Если API подсказок недоступно или не вернуло результатов, возвращается
    исходное значение selection.
    """
    if not selection:
        return ""

    try:
        response = session.get(
            "https://rasp.rea.ru/Schedule/SearchBarSuggestions",
            params={"searchFor": selection},
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        logging.warning("Timeout при уточнении ключа группы")
        return selection
    except requests.RequestException as exc:
        logging.warning("Не удалось уточнить ключ группы: %s", exc)
        return selection
    except ValueError:
        logging.warning("API подсказок вернуло некорректный ответ")
        return selection

    if not isinstance(data, list):
        return selection
    
    for item in data:
        if isinstance(item, dict) and item.get("key", "").lower() == selection.lower():
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

    if not html or not isinstance(html, str):
        logging.debug("parse_schedule: пустой или невалидный HTML")
        return []
    
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logging.error("Ошибка парсинга HTML: %s", exc)
        return []
    
    events: List[ScheduleEvent] = []
    for table in soup.find_all("table"):
        header = table.find("h5")
        if not header:
            continue
        date = parse_date_from_header(header.get_text(strip=True))
        if not date:
            logging.debug("Не удалось разобрать дату в заголовке: %s", header)
            continue
        for anchor in table.find_all("a", class_="task"):
            try:
                event = build_event_from_row(anchor, date, session)
                if event:
                    events.append(event)
            except Exception as exc:
                logging.warning("Ошибка при разборе события: %s", exc)
                continue
    return events


def fetch_events(url: str, group: str, session: requests.Session) -> List[ScheduleEvent]:
    """Загружает и разбирает расписание в список событий."""

    html = fetch_html(url, group, session)
    return parse_schedule(html, session)


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
    start_time, end_time, pair_number = parse_timeslot(time_cell)
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
        pair_number=pair_number,
    )


def parse_timeslot(
    cell: Optional[BeautifulSoup],
) -> Tuple[Optional[dt.time], Optional[dt.time], Optional[int]]:
    """Разбирает ячейку времени: номер пары, время начала и конца."""

    if not cell:
        return None, None, None
    parts = list(cell.stripped_strings)
    tokens: list[str] = []
    for part in parts:
        tokens.extend(part.replace("\xa0", " ").split())

    pair_number = extract_pair_number(parts + tokens)
    time_tokens = [token for token in tokens if re.fullmatch(r"\d{2}:\d{2}", token)]
    if len(time_tokens) < 2:
        return None, None, pair_number
    try:
        start_time = dt.datetime.strptime(time_tokens[0], "%H:%M").time()
        end_time = dt.datetime.strptime(time_tokens[1], "%H:%M").time()
    except ValueError:
        return None, None, pair_number
    return start_time, end_time, pair_number


def extract_pair_number(parts: List[str]) -> Optional[int]:
    """Извлекает порядковый номер пары из сырой ячейки времени."""

    candidates = parts + [" ".join(parts)]
    for part in candidates:
        match = re.search(r"(\d+)\s*пара", part.lower())
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


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
    except requests.Timeout:
        logging.debug("Timeout при получении деталей элемента %s", element_id)
        return None, None
    except requests.RequestException as exc:
        logging.debug("Не удалось получить детали занятия %s: %s", element_id, exc)
        return None, None

    try:
        soup = BeautifulSoup(response.text, "html.parser")
        body = soup.find("div", class_="element-info-body")
    except Exception as exc:
        logging.warning("Ошибка при парсинге деталей: %s", exc)
        return None, None
    
    if not body:
        return None, None

    lines = [line.strip() for line in body.get_text("\n").splitlines() if line.strip()]
    teacher = extract_teacher(lines)
    extra_info = extract_extra_info(lines)
    return teacher, extra_info
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


def build_event_summary(
    event: ScheduleEvent, lesson_counters: dict[str, int]
) -> Tuple[str, str]:
    """Формирует название события и цвет по правилам Google Calendar."""

    lesson_kind = detect_lesson_kind(event.lesson_type)
    letter = resolve_lesson_letter(event.lesson_type, lesson_kind)
    lesson_counters[letter] = lesson_counters.get(letter, 0) + 1
    shortcut = SUBJECT_SHORTCUTS.get(event.title, event.title)
    summary = f"{letter}{lesson_counters[letter]} {shortcut}"
    color = GOOGLE_COLOR_MAP.get(lesson_kind, "")
    return summary, color


def detect_lesson_kind(lesson_type: Optional[str]) -> str:
    """Определяет категорию занятия для цвета и буквенного кода."""

    if not lesson_type:
        return "other"
    normalized = lesson_type.lower()
    if "лекц" in normalized:
        return "lecture"
    if "практичес" in normalized or "лаборатор" in normalized:
        return "seminar"
    if "зач" in normalized or "экзам" in normalized:
        return "exam"
    return "other"


def resolve_lesson_letter(lesson_type: Optional[str], lesson_kind: str) -> str:
    """Возвращает буквенный код занятия (Л, С, Э, З, прочие)."""

    if not lesson_type:
        return "Д"
    normalized = lesson_type.lower()
    if lesson_kind == "lecture":
        return "Л"
    if lesson_kind == "seminar":
        return "С"
    if "экзам" in normalized:
        return "Э"
    if "зач" in normalized:
        return "З"
    return lesson_type[0].upper()


def build_event_alarms(pair_number: Optional[int]) -> List[str]:
    """Создает блоки напоминаний с учетом номера пары."""

    reminders = [-10] if pair_number in {2, 3} else [-70, -10]
    alarms: List[str] = []
    for minutes in reminders:
        trigger = f"-PT{abs(minutes)}M"
        alarms.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"TRIGGER:{trigger}",
                "DESCRIPTION:Напоминание",
                "END:VALARM",
            ]
        )
    return alarms


def build_ics(events: List[ScheduleEvent], output_path: Path, target: str) -> None:
    """Создает .ics файл со списком занятий.

    "target" определяет особенности формата:
    - "mobile": локальная временная зона, цвета событий, напоминания.
    - "google": время в UTC, без нестандартных полей.
    """
    if not events:
        logging.warning("build_ics: список событий пуст")
        return

    if target not in ("mobile", "google"):
        logging.error("build_ics: неизвестный target='%s'", target)
        return

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logging.error("Не удалось создать директорию %s: %s", output_path.parent, exc)
        return

    try:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//REA Schedule Parser//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
        ]
        if target == "mobile":
            lines.extend(
                [
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
            )
        else:
            lines.append("X-WR-TIMEZONE:UTC")

        lesson_counters: dict[str, int] = {}
        dtstamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for event in sorted(events, key=lambda e: (e.date, e.start_time)):
            start_dt = dt.datetime.combine(event.date, event.start_time, tzinfo=MOSCOW_TZ)
            end_dt = dt.datetime.combine(event.date, event.end_time, tzinfo=MOSCOW_TZ)
            summary, color = build_event_summary(event, lesson_counters)
            description_parts = []
            if event.teacher:
                description_parts.append(f"Преподаватель: {event.teacher}")
            if event.location:
                description_parts.append(f"Аудитория: {event.location}")
            if event.extra_info:
                description_parts.append(event.extra_info)
            description = "\n".join(description_parts)
            alarms = build_event_alarms(event.pair_number) if target == "mobile" else []

            if target == "google":
                dt_start_str = start_dt.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                dt_end_str = end_dt.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                dtstart_line = f"DTSTART:{dt_start_str}"
                dtend_line = f"DTEND:{dt_end_str}"
            else:
                dtstart_line = f"DTSTART;TZID=Europe/Moscow:{start_dt.strftime('%Y%m%dT%H%M%S')}"
                dtend_line = f"DTEND;TZID=Europe/Moscow:{end_dt.strftime('%Y%m%dT%H%M%S')}"

            event_block = [
                "BEGIN:VEVENT",
                f"UID:{event.element_id or hash(summary + str(start_dt))}@rasp.rea.ru",
                f"DTSTAMP:{dtstamp}",
                f"SUMMARY:{escape_ics(summary)}",
                dtstart_line,
                dtend_line,
                f"DESCRIPTION:{escape_ics(description)}",
                f"LOCATION:{escape_ics(event.location or '')}",
            ]
            if target == "mobile" and color:
                event_block.append(f"COLOR:{color}")
            event_block.extend(alarms)
            event_block.append("END:VEVENT")
            lines.extend(event_block)

        lines.append("END:VCALENDAR")
        output_path.write_text("\n".join(lines), encoding="utf-8")
        logging.info("Файл %s создан успешно (%d событий)", output_path, len(events))
    except Exception as exc:
        logging.error("Ошибка при создании .ics файла: %s", exc)
        raise


def format_weekly_schedule(events: List[ScheduleEvent], reference_date: Optional[dt.date] = None) -> str:
    """Возвращает текстовое расписание на неделю, начиная с понедельника."""

    if not events:
        return "Расписание не найдено."

    try:
        ref_date = reference_date or dt.date.today()
        start_of_week = ref_date - dt.timedelta(days=ref_date.weekday())
        end_of_week = start_of_week + dt.timedelta(days=6)
        weekly_events = [
            event for event in events if start_of_week <= event.date <= end_of_week
        ]
        if not weekly_events:
            return "На эту неделю занятий нет."

        lines: List[str] = []
        for date, day_events in _group_events_by_date(weekly_events):
            lines.append(date.strftime("%A, %d.%m.%Y"))
            for event in sorted(day_events, key=lambda e: e.start_time):
                lesson_type = f" ({event.lesson_type})" if event.lesson_type else ""
                teacher = f" — {event.teacher}" if event.teacher else ""
                location = f" [{event.location}]" if event.location else ""
                lines.append(
                    f"  {event.start_time.strftime('%H:%M')}–{event.end_time.strftime('%H:%M')}"
                    f" {event.title}{lesson_type}{teacher}{location}"
                )
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as exc:
        logging.error("Ошибка при форматировании расписания: %s", exc)
        return "Ошибка при обработке расписания."


def _group_events_by_date(events: List[ScheduleEvent]) -> Iterable[Tuple[dt.date, List[ScheduleEvent]]]:
    """Группирует события по дате."""

    events_by_date: dict[dt.date, List[ScheduleEvent]] = {}
    for event in events:
        events_by_date.setdefault(event.date, []).append(event)
    for date in sorted(events_by_date.keys()):
        yield date, events_by_date[date]


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
    transliterated = (
        transliterated.replace("/", "_")
        .replace(" ", "_")
        .replace("-", "_")
        .replace("д", "d")
        .replace("г", "g")
        .replace("м", "m")
    )
    transliterated = re.sub(r"[^a-z0-9_.]", "_", transliterated)
    transliterated = re.sub(r"_+", "_", transliterated).strip("._")
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
        help="Путь к .ics для стандартного мобильного календаря",
    )
    parser.add_argument(
        "--google-output",
        type=Path,
        help="Путь к .ics файлу совместимому с Google Calendar",
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

    if not args.url or not args.group:
        logging.error("URL и код группы не могут быть пустыми")
        sys.exit(1)

    group_slug = slugify_group_name(args.group)
    mobile_output = args.output or Path.cwd() / f"schedule_{group_slug}.ics"
    google_output = args.google_output or Path.cwd() / f"schedule_{group_slug}_google.ics"

    logging.info("Загрузка расписания для группы: %s", args.group)
    try:
        with requests.Session() as session:
            html = fetch_html(args.url, args.group, session)
            if not html:
                logging.error("Не удалось загрузить HTML расписания")
                sys.exit(1)
            
            events = parse_schedule(html, session)
            if not events:
                logging.error("Не найдено ни одного занятия для указанной группы")
                sys.exit(1)

            build_ics(events, mobile_output, target="mobile")
            build_ics(events, google_output, target="google")
        
        logging.info("Успешно загружено занятий: %s", len(events))
        logging.info("Мобильный календарь: %s", mobile_output)
        logging.info("Google Calendar: %s", google_output)
    except Exception as exc:
        logging.error("Критическая ошибка: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
