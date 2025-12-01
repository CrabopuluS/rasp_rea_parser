import datetime as dt
from typing import List

import requests

import bot
import schedule_parser as sp


def test_slugify_group_name_basic():
    assert sp.slugify_group_name("15.14д-гг01/24м") == "15.14d_gg01_24m"


def test_build_event_alarms_varies_by_pair():
    assert sp.build_event_alarms(pair_number=2) == [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER:-PT10M",
        "DESCRIPTION:Напоминание",
        "END:VALARM",
    ]
    assert sp.build_event_alarms(pair_number=None)[0:3] == [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER:-PT70M",
    ]


def test_format_weekly_schedule_limits_to_current_week():
    today = dt.date(2024, 1, 3)  # Wednesday
    events: List[sp.ScheduleEvent] = [
        sp.ScheduleEvent(
            date=today,
            start_time=dt.time(9, 0),
            end_time=dt.time(10, 30),
            title="Информатика",
            lesson_type="Лекция",
            teacher="И.И. Иванов",
            location="101",
        ),
        sp.ScheduleEvent(
            date=today + dt.timedelta(days=10),
            start_time=dt.time(9, 0),
            end_time=dt.time(10, 30),
            title="Философия",
        ),
    ]

    text = sp.format_weekly_schedule(events, reference_date=today)
    assert "Информатика" in text
    assert "Философия" not in text


def test_parse_schedule(monkeypatch):
    html = """
    <table>
        <h5>Понедельник 01.01.2024</h5>
        <tr>
            <td>1 пара\n09:00\n10:30</td>
            <td>
                <a class="task" data-elementid="1">
                    Информатика
                    <span>Лекция</span>
                    <span>Ауд. 101</span>
                </a>
            </td>
        </tr>
    </table>
    """

    def fake_details(element_id: str | None, session: requests.Session):
        return "И.И. Иванов", "Площадка: корпус А"

    monkeypatch.setattr(sp, "fetch_details", fake_details)

    with requests.Session() as session:
        events = sp.parse_schedule(html, session)

    assert len(events) == 1
    event = events[0]
    assert event.title == "Информатика"
    assert event.lesson_type == "Лекция"
    assert event.teacher == "И.И. Иванов"
    assert event.location == "Ауд. 101"


def test_parse_schedule_datetime_valid_and_invalid():
    assert bot.parse_schedule_datetime("2024-02-01", "12:30") == dt.datetime(
        2024, 2, 1, 12, 30, tzinfo=sp.MOSCOW_TZ
    )
    assert bot.parse_schedule_datetime("2024-02-30", "12:30") is None
    assert bot.parse_schedule_datetime("2024-02-01", "not-time") is None
