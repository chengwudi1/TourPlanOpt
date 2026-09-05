"""Time helpers. DISPLAY ONLY.

Every time in the database and on the wire is an integer count of minutes since
midnight. Nothing here may be used to store or compute a schedule -- the recurrence in
app/routing/schedule.py is integer arithmetic on purpose. Parsing "HH:MM" at each step
of that recurrence is the single easiest way to introduce an off-by-one.
"""

from __future__ import annotations

from datetime import UTC, datetime

MIN_PER_DAY = 24 * 60
# A day whose cursor passes this is worth warning about ("考虑拆到第二天").
LATE_FINISH_MIN = 22 * 60


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clamp_min(value: int) -> int:
    return max(0, min(MIN_PER_DAY - 1, int(value)))


def format_min(minutes: int | None) -> str:
    """540 -> '09:00'. Past-midnight values are labelled rather than silently wrapped."""
    if minutes is None:
        return ""
    m = int(minutes)
    if m < 0:
        return ""
    day, rem = divmod(m, MIN_PER_DAY)
    text = f"{rem // 60:02d}:{rem % 60:02d}"
    if day == 0:
        return text
    return f"次日 {text}" if day == 1 else f"+{day}天 {text}"


def parse_hhmm(text: str) -> int | None:
    """'9:30' / '09:30' -> 570. Returns None rather than raising on junk input."""
    s = (text or "").strip()
    if not s or ":" not in s:
        return None
    hh, _, mm = s.partition(":")
    if not (hh.isdigit() and mm.isdigit()):
        return None
    hours, mins = int(hh), int(mm)
    if not (0 <= hours <= 23 and 0 <= mins <= 59):
        return None
    return hours * 60 + mins


def format_duration(minutes: int | None) -> str:
    """90 -> '1 小时 30 分'. Used for stay durations and totals."""
    if not minutes:
        return "0 分钟"
    m = abs(int(minutes))
    hours, mins = divmod(m, 60)
    if hours and mins:
        return f"{hours} 小时 {mins} 分"
    if hours:
        return f"{hours} 小时"
    return f"{mins} 分钟"
