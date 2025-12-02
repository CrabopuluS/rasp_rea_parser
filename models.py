from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

@dataclass
class Lesson:
    """Single lesson information."""

    title: str
    lesson_type: str
    start: datetime
    end: datetime
    teacher: Optional[str]
    room: Optional[str]
    week_day: str
    pair_number: int


@dataclass
class WeekSchedule:
    """Collection of lessons for a week."""

    group: str
    source_url: str
    lessons: List[Lesson]

    def grouped_by_day(self) -> dict[str, list[Lesson]]:
        """Return lessons grouped by weekday preserving order."""

        grouped: dict[str, list[Lesson]] = {}
        for lesson in sorted(self.lessons, key=lambda item: item.start):
            grouped.setdefault(lesson.week_day, []).append(lesson)
        return grouped


__all__ = ["Lesson", "WeekSchedule"]
