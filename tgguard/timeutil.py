"""Time helpers: parsing human clock strings, scheduling math, formatting."""

from __future__ import annotations

import datetime as dtm
import re
from zoneinfo import ZoneInfo

# Common mute / ban duration choices (label -> seconds)
DURATIONS: dict[str, int] = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 3600,
    "3h": 3 * 3600,
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "1d": 24 * 3600,
    "3d": 3 * 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
}


def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def parse_clock(text: str) -> tuple[int, int] | None:
    """Parse '8:00 PM', '20:00', '8pm', '08:30 am', '8 PM' -> (hour, minute) 24h."""
    if not text:
        return None
    t = text.strip().lower().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{1,2}))?\s*(am|pm)?", t)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    mer = m.group(3)
    if mer == "pm" and hour < 12:
        hour += 12
    if mer == "am" and hour == 12:
        hour = 0
    if not mer and hour > 23:
        return None
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_duration(text: str) -> int | None:
    """Parse '90', '5m', '2h', '1d', '1h30m', '45 min' -> seconds."""
    if not text:
        return None
    t = text.strip().lower()
    if t in DURATIONS:
        return DURATIONS[t]
    if re.fullmatch(r"\d+", t):  # bare number = minutes
        return int(t) * 60
    total = 0
    found = False
    for num, unit in re.findall(r"(\d+)\s*(d|h|m|s|day|days|hour|hours|min|mins|minutes|sec|secs|seconds)", t):
        found = True
        n = int(num)
        if unit.startswith("d"):
            total += n * 86400
        elif unit.startswith("h"):
            total += n * 3600
        elif unit.startswith("m"):
            total += n * 60
        elif unit.startswith("s"):
            total += n
    return total if found else None


def next_fire(hhmm: str, tzname: str, now: dtm.datetime | None = None) -> dtm.datetime | None:
    """Next timezone-aware datetime for a daily 'HH:MM' schedule."""
    parsed = parse_clock(hhmm)
    if not parsed:
        return None
    tz = get_tz(tzname)
    now = now or dtm.datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dtm.timezone.utc)
    now = now.astimezone(tz)
    fire = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
    if fire <= now:
        fire += dtm.timedelta(days=1)
    return fire


def humanize_seconds(seconds: int | float) -> str:
    seconds = int(seconds)
    if seconds < 0:
        return "0s"
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            parts.append(f"{seconds // size}{label}")
            seconds %= size
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts[:3])


def fmt_ts(ts: float | int | None, tzname: str = "Asia/Kolkata") -> str:
    if not ts:
        return "—"
    tz = get_tz(tzname)
    return dtm.datetime.fromtimestamp(int(ts), tz).strftime("%d %b %Y, %I:%M %p")


def now_ts() -> int:
    return int(dtm.datetime.now(dtm.timezone.utc).timestamp())
