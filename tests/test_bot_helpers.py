from datetime import datetime, timezone

from bot import _format_lesson, _parse_datetime, format_week_message
from models import Lesson, WeekSchedule


def test_format_lesson_renders_time_and_details():
    lesson = Lesson(
        title="Информатика",
        lesson_type="Практика",
        start=datetime(2024, 9, 3, 10, 0, tzinfo=timezone.utc),
        end=datetime(2024, 9, 3, 11, 30, tzinfo=timezone.utc),
        teacher="Петров П.П.",
        room="205",
        week_day="Вторник",
        pair_number=2,
    )
    text = _format_lesson(lesson)
    assert "Информатика" in text
    assert "Практика" in text
    assert "205" in text


def test_parse_datetime_supports_multiple_formats():
    first = _parse_datetime("2024-09-01", "09:00")
    second = _parse_datetime("01.09.2024", "09:00")
    assert first.tzinfo is not None
    assert second.tzinfo is not None
    assert first.hour == 9 and second.hour == 9


def test_format_week_message_groups_lessons():
    lesson = Lesson(
        title="Информатика",
        lesson_type="Лекция",
        start=datetime(2024, 9, 4, 8, 0, tzinfo=timezone.utc),
        end=datetime(2024, 9, 4, 9, 30, tzinfo=timezone.utc),
        teacher=None,
        room=None,
        week_day="Среда",
        pair_number=1,
    )
    schedule = WeekSchedule(group="Тест", source_url="https://example.com", lessons=[lesson])
    text = format_week_message(schedule)
    assert "Группа" in text and "Среда" in text and "Информатика" in text
