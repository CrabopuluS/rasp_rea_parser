from __future__ import annotations

import re
from typing import Iterable

from slugify import slugify


def slugify_group_name(group: str) -> str:
    """Transliterate and slugify group names to filesystem-safe representation."""

    cleaned = group.strip()
    slug = slugify(cleaned, lowercase=True, separator="-")
    slug = re.sub(r"-+", "-", slug)
    return slug or "schedule"


def format_lessons_text(group: str, lessons: Iterable[dict[str, str]]) -> str:
    """Format lessons for human-readable telegram messages."""

    lines = [f"Расписание для группы {group}:"]
    for lesson in lessons:
        lines.append(
            " • ".join(
                part
                for part in (
                    lesson.get("time", ""),
                    lesson.get("title", ""),
                    lesson.get("lesson_type", ""),
                    lesson.get("teacher", ""),
                    lesson.get("room", ""),
                )
                if part
            )
        )
    return "\n".join(lines)


__all__ = ["slugify_group_name", "format_lessons_text"]
