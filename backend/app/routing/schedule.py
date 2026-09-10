"""Timeline scheduling: turn an optimized order + travel matrix into clock times.

Integer minutes since midnight everywhere (the schema invariant). The recurrence is
    arrive = cursor + travel
    start  = arrive (or the place's locked time)
    cursor = start + duration
A place with a hand-set time is an anchor: the cursor RESETS to it, and if that time is
EARLIER than the projected arrival we emit a conflict warning -- we never try to solve
time windows (VRPTW is a swamp, explicitly out of scope). After 22:00 we nag about
splitting the day.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.util.timefmt import format_min

LATE_NIGHT_MIN = 22 * 60


@dataclass(slots=True)
class ScheduledPlace:
    place_id: str
    travel_min_before: int
    arrive_min: int
    start_min: int


@dataclass(slots=True)
class ScheduleResult:
    places: list[ScheduledPlace]
    warnings: list[str] = field(default_factory=list)
    end_min: int = 0


def fill_schedule(
    ordered_ids: list[str],
    durations: dict[str, int],
    locked_times: dict[str, int | None],
    travel_minutes: list[list[int]],
    day_start_min: int = 540,
) -> ScheduleResult:
    """travel_minutes[i][j] is the cost from position i to position j along the ORDER
    (i.e. indexed by position, not by node) -- the caller extracts the legs.

    A `None` in travel_minutes means the leg is unreachable: we schedule as if it cost
    nothing and warn, rather than refusing to produce a timeline at all.
    """
    result = ScheduleResult(places=[])
    cursor: int | None = None
    for position, place_id in enumerate(ordered_ids):
        if position == 0:
            travel = 0
            arrive = day_start_min
        else:
            raw = travel_minutes[position - 1][position]
            travel = 0 if raw is None else int(raw)
            arrive = (cursor or 0) + travel
        start = arrive
        locked = locked_times.get(place_id)
        if locked is not None:
            if cursor is not None and locked < arrive:
                hh, mm = divmod(locked, 60)
                result.warnings.append(
                    f"第 {position + 1} 站的固定时间早于预计到达（{hh:02d}:{mm:02d}）"
                )
            start = locked
        result.places.append(
            ScheduledPlace(
                place_id=place_id,
                travel_min_before=travel,
                arrive_min=arrive,
                start_min=start,
            )
        )
        cursor = start + durations.get(place_id, 0)

    result.end_min = cursor or 0
    if result.end_min > LATE_NIGHT_MIN:
        result.warnings.append(
            f"行程预计 {format_min(result.end_min)} 才结束，考虑拆到第二天"
        )
    return result
