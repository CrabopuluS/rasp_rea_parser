from datetime import datetime, timedelta, timezone

from ics_builder import build_ics
from models import Lesson, WeekSchedule


def _sample_lessons():
    start = datetime(2024, 9, 2, 9, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1, minutes=30)
    return [
        Lesson(
            title="Математика",
            lesson_type="Лекция",
            start=start,
            end=end,
            teacher="Иванов И.И.",
            room="Ауд. 101",
            week_day="Понедельник",
            pair_number=1,
        )
    ]


def test_build_ics_produces_two_calendars(tmp_path):
    schedule = WeekSchedule(
        group="15.14д-гг01/24м",
        source_url="https://rasp.rea.ru/",
        lessons=_sample_lessons(),
    )
    calendars = build_ics(schedule)
    assert set(calendars.keys()) == {"mobile", "google"}
    assert b"BEGIN:VEVENT" in calendars["mobile"]
    assert b"BEGIN:VEVENT" in calendars["google"]
