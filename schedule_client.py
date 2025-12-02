from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional
from urllib.parse import urljoin

import requests
from requests import RequestException, Timeout

from models import Lesson, WeekSchedule
from time_utils import get_moscow_tz

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10


@dataclass
class ScheduleClient:
    """HTTP client for rasp.rea.ru schedule data."""

    base_url: str

    def __post_init__(self) -> None:
        self.session = requests.Session()

    def close(self) -> None:
        """Close the underlying HTTP session."""

        self.session.close()

    def fetch_week_schedule(self, group: str) -> WeekSchedule:
        """Fetch and parse weekly schedule for the provided group code."""

        normalized_group = self._normalize_group(group)
        raw_lessons = self._retrieve_schedule_payload(normalized_group)
        lessons = list(self._parse_lessons(raw_lessons))
        return WeekSchedule(
            group=normalized_group, source_url=self.base_url, lessons=lessons
        )

    def _normalize_group(self, group: str) -> str:
        """Attempt to normalize group code via search suggestions."""

        candidate_paths = (
            "Schedule/SearchBarSuggestions",
            "schedule/searchbarsuggestions",
        )
        for path in candidate_paths:
            try:
                response = self.session.get(
                    urljoin(self.base_url, path),
                    params={"query": group},
                    timeout=DEFAULT_TIMEOUT,
                )
                if response.status_code != 200:
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    continue
                if isinstance(payload, list) and payload:
                    first = payload[0]
                    name = first.get("name") if isinstance(first, dict) else None
                    if isinstance(name, str) and name.strip():
                        return name.strip()
            except (Timeout, RequestException) as exc:
                LOGGER.warning("Suggestion request failed for %s: %s", path, exc)
        return group

    def _retrieve_schedule_payload(self, group: str) -> List[dict]:
        """Retrieve raw schedule data via the public API or HTML."""

        candidate_paths = (
            "Schedule/Schedule/GetSchedule",
            "schedule/getSchedule",
        )
        for path in candidate_paths:
            try:
                response = self.session.get(
                    urljoin(self.base_url, path),
                    params={"group": group},
                    timeout=DEFAULT_TIMEOUT,
                )
                if response.status_code != 200:
                    LOGGER.warning(
                        "Unexpected status %s for %s", response.status_code, path
                    )
                    continue
                if response.headers.get("content-type", "").startswith("application/json"):
                    return self._ensure_list(response.json())
                # Fallback: try to parse HTML table if JSON not available
                return self._parse_html_table(response.text)
            except (Timeout, RequestException) as exc:
                LOGGER.error("Failed to fetch schedule from %s: %s", path, exc)
        return []

    @staticmethod
    def _ensure_list(payload: object) -> List[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict) and "data" in payload:
            data = payload.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        return []

    def _parse_html_table(self, html: str) -> List[dict]:  # pragma: no cover - HTML varies
        """Parse simplistic HTML schedule tables."""

        try:
            from bs4 import BeautifulSoup  # type: ignore
        except Exception:
            LOGGER.warning("BeautifulSoup is unavailable; returning empty schedule")
            return []

        soup = BeautifulSoup(html, "html.parser")
        lessons: List[dict] = []
        for row in soup.select("table tr"):
            cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            if len(cells) < 6:
                continue
            lessons.append(
                {
                    "title": cells[1],
                    "lessonType": cells[0],
                    "start": cells[2],
                    "end": cells[3],
                    "teacher": cells[4],
                    "room": cells[5],
                    "pairNumber": len(lessons) + 1,
                    "weekDay": cells[2].split()[0] if cells[2] else "",
                }
            )
        return lessons

    def _parse_lessons(self, payload: Iterable[dict]) -> Iterable[Lesson]:
        tz = get_moscow_tz()
        for item in payload:
            try:
                start_raw = item.get("start") or item.get("dateStart")
                end_raw = item.get("end") or item.get("dateEnd")
                day = item.get("weekDay") or item.get("dayOfWeek") or ""
                pair_number = int(item.get("pairNumber", 1))
                start_dt = self._parse_datetime(start_raw, tz)
                end_dt = self._parse_datetime(end_raw, tz)
                if start_dt is None or end_dt is None:
                    continue
                yield Lesson(
                    title=str(item.get("title") or item.get("subject") or ""),
                    lesson_type=str(item.get("lessonType") or item.get("type") or ""),
                    start=start_dt,
                    end=end_dt,
                    teacher=self._optional_str(item.get("teacher")),
                    room=self._optional_str(item.get("room") or item.get("auditory")),
                    week_day=str(day),
                    pair_number=pair_number,
                )
            except Exception as exc:
                LOGGER.error("Failed to parse lesson payload %s: %s", item, exc)

    @staticmethod
    def _parse_datetime(value: object, tz) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value.astimezone(tz)
        if isinstance(value, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
                try:
                    return datetime.strptime(value, fmt).replace(tzinfo=tz)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _optional_str(value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


__all__ = ["ScheduleClient", "DEFAULT_TIMEOUT"]
