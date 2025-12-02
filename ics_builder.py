from __future__ import annotations

import logging
from datetime import timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, Iterable

from icalendar import Alarm, Calendar, Event

from models import Lesson, WeekSchedule
from text_utils import slugify_group_name
from time_utils import MOSCOW_TZ_NAME, get_moscow_tz

LOGGER = logging.getLogger(__name__)

DEFAULT_COLOR = "#1d9bf0"


def build_ics(schedule: WeekSchedule) -> Dict[str, bytes]:
    """Build two ICS files (mobile and Google) and return their contents."""

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        mobile_path = temp_path / f"{slugify_group_name(schedule.group)}-mobile.ics"
        google_path = temp_path / f"{slugify_group_name(schedule.group)}-google.ics"

        mobile_calendar = _build_calendar(schedule.lessons, mobile=True)
        google_calendar = _build_calendar(schedule.lessons, mobile=False)

        mobile_path.write_bytes(mobile_calendar.to_ical())
        google_path.write_bytes(google_calendar.to_ical())

        return {
            "mobile": mobile_path.read_bytes(),
            "google": google_path.read_bytes(),
        }


def _build_calendar(lessons: Iterable[Lesson], mobile: bool) -> Calendar:
    calendar = Calendar()
    calendar.add("prodid", "-//rea-telegram-bot//rasp//RU")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("X-WR-CALNAME", "Расписание РЭУ")
    if mobile:
        calendar.add("X-WR-TIMEZONE", MOSCOW_TZ_NAME)

    for lesson in lessons:
        calendar.add_component(_build_event(lesson, mobile=mobile))

    return calendar


def _build_event(lesson: Lesson, mobile: bool) -> Event:
    tz = get_moscow_tz()
    event = Event()
    event.add("summary", lesson.title)
    description_parts = [lesson.lesson_type]
    if lesson.teacher:
        description_parts.append(lesson.teacher)
    if lesson.room:
        description_parts.append(lesson.room)
    event.add("description", " | ".join(filter(None, description_parts)))
    if mobile:
        event.add("dtstart", lesson.start.astimezone(tz))
        event.add("dtend", lesson.end.astimezone(tz))
    else:
        event.add("dtstart", lesson.start.astimezone(timezone.utc))
        event.add("dtend", lesson.end.astimezone(timezone.utc))
    event.add("location", lesson.room or "")
    event.add("uid", f"rea-{lesson.week_day}-{lesson.pair_number}-{lesson.start.timestamp()}")
    if mobile:
        event.add("color", DEFAULT_COLOR)
        for alarm in _build_alarms(lesson):
            event.add_component(alarm)
    return event


def _build_alarms(lesson: Lesson) -> Iterable[Alarm]:
    lead_times = [timedelta(minutes=-70), timedelta(minutes=-10)]
    if lesson.pair_number in {2, 3}:
        lead_times = [timedelta(minutes=-10)]

    for lead in lead_times:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", f"Скоро {lesson.title}")
        alarm.add("trigger", lead)
        yield alarm


__all__ = ["build_ics"]
