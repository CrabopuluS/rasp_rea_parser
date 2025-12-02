from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - exercised indirectly
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None

LOGGER = logging.getLogger(__name__)

MOSCOW_TZ_NAME = "Europe/Moscow"


def get_moscow_tz() -> timezone:
    """Return Moscow timezone with a safe fallback to UTC+3."""

    if ZoneInfo is not None:
        try:
            return ZoneInfo(MOSCOW_TZ_NAME)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - depends on OS tzdata
            LOGGER.warning("Falling back to fixed UTC+3 timezone: %s", exc)
    return timezone(timedelta(hours=3))


def now_moscow() -> datetime:
    """Return current datetime in Moscow timezone."""

    return datetime.now(tz=get_moscow_tz())


__all__ = ["get_moscow_tz", "now_moscow", "MOSCOW_TZ_NAME"]
