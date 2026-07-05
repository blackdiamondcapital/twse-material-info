import re
from datetime import date, time


def roc_to_date(roc_str: str | None) -> date | None:
    """民國日期 YYYMMDD -> date"""
    if not roc_str or len(roc_str) != 7:
        return None
    try:
        year = int(roc_str[:3]) + 1911
        month = int(roc_str[3:5])
        day = int(roc_str[5:7])
        return date(year, month, day)
    except ValueError:
        return None


def roc_time_to_time(time_str: str | None) -> time | None:
    """HHMMSS -> time"""
    if not time_str or len(time_str) != 6:
        return None
    try:
        return time(
            int(time_str[:2]),
            int(time_str[2:4]),
            int(time_str[4:6]),
        )
    except ValueError:
        return None


def date_to_roc(d: date | None) -> str | None:
    if d is None:
        return None
    roc_year = d.year - 1911
    return f"{roc_year:03d}{d.month:02d}{d.day:02d}"


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").strip()


def parse_slash_roc_date(value: str | None) -> date | None:
    """民國日期 114/07/02 -> date"""
    if not value:
        return None
    match = re.match(r"^(\d{2,3})/(\d{1,2})/(\d{1,2})$", value.strip())
    if not match:
        return None
    try:
        year = int(match.group(1)) + 1911
        month = int(match.group(2))
        day = int(match.group(3))
        return date(year, month, day)
    except ValueError:
        return None


def parse_colon_time(value: str | None) -> time | None:
    """17:30:51 -> time"""
    if not value:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", value.strip())
    if not match:
        return None
    try:
        return time(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
