"""Timezone-aware run gate. GitHub Actions cron is UTC-only, so we gate here."""

from datetime import datetime
from zoneinfo import ZoneInfo


def should_run_now(tz_name: str, run_hour: int, now_utc: datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(tz_name))
    return local.hour == run_hour
