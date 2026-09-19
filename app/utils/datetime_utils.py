"""Date/time helpers.

All appointment datetimes are stored as *naive* datetimes in the clinic's local time
(`CLINIC_TIMEZONE`). SQLite has no timezone support, and a clinic only ever needs one
timezone, so "17:30 on 21 Sep" always means 17:30 on the clinic's wall clock.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_USER_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")


def clinic_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().clinic_timezone)


def now_local() -> datetime:
    """Current clinic wall-clock time as a naive datetime."""
    return datetime.now(clinic_tz()).replace(tzinfo=None)


def to_clinic_naive(value: datetime) -> datetime:
    """Convert an aware datetime to clinic-local naive time; naive values are assumed local."""
    if value.tzinfo is None:
        return value
    return value.astimezone(clinic_tz()).replace(tzinfo=None)


def format_time(value: datetime | time) -> str:
    """5:30 PM (no leading zero; avoids platform-specific strftime flags)."""
    hour12 = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour12}:{value.minute:02d} {suffix}"


def format_date(value: date) -> str:
    """21 September 2026"""
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def format_date_short(value: date) -> str:
    """Mon, 21 Sep"""
    return f"{_WEEKDAYS[value.weekday()][:3]}, {value.day} {_MONTHS[value.month - 1][:3]}"


def format_date_full(value: date) -> str:
    """Monday, 21 September 2026"""
    return f"{_WEEKDAYS[value.weekday()]}, {format_date(value)}"


def format_slot_id(value: datetime) -> str:
    """HH:MM, the representation used by the REST API."""
    return value.strftime("%H:%M")


def parse_user_date(text: str) -> date | None:
    """Parse a date typed by a patient (e.g. 25/09/2026, 2026-09-25, 25 Sep 2026)."""
    cleaned = " ".join(text.strip().split())
    for fmt in _USER_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)
