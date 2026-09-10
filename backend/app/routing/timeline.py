"""The day's timeline, recomputed after every change that moves or re-times a place.

Optimize used to be the only writer of travel_min_before/arrive_min/start_min. So a
freshly added place showed 路程未知 until someone pressed the button, and dragging two
stops apart kept the old order's clock times -- a timeline that quietly contradicted the
list above it.

Recomputing on every edit rules out spending Amap quota for a cost matrix, so legs come
from cached_or_estimate_matrix: real road seconds wherever a precise run already paid
for them, haversine estimates otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.amap.cache import get_distance_cache
from app.db import repositories
from app.db.database import Database
from app.models.domain import DayOut, PlaceOut, TravelMode
from app.routing.matrix import cached_or_estimate_matrix
from app.routing.schedule import fill_schedule


@dataclass(slots=True)
class DayTimeline:
    """Authoritative schedule rows for one day, plus what the day costs in total."""

    day_id: str
    places: list[PlaceOut] = field(default_factory=list)
    end_min: int = 0
    travel_min: int = 0
    warnings: list[str] = field(default_factory=list)
    exact: bool = False

    def payload(self) -> dict:
        return {
            "day_id": self.day_id,
            "places": [p.model_dump(mode="json") for p in self.places],
            "end_min": self.end_min,
            "travel_min": self.travel_min,
            "warnings": self.warnings,
            "exact": self.exact,
        }


async def apply_timeline(
    db: Database,
    *,
    day: DayOut,
    ordered: Sequence[PlaceOut],
    legs_min: list[list[int | None]],
    day_start_min: int,
    warnings: Sequence[str] = (),
    exact: bool = True,
    persist: bool = True,
) -> DayTimeline:
    """fill_schedule -> persist -> hand back the schedule rows in the given order.

    ``legs_min`` is position-indexed along ``ordered``, the same contract fill_schedule
    already documents, so the optimizer and the auto-recompute path share one function
    and cannot drift apart. ``persist=False`` dry-runs it: the rows come back as copies
    of the inputs, so a preview costs no writes and no rev bumps.
    """
    schedule = fill_schedule(
        [p.id for p in ordered],
        {p.id: p.duration_min for p in ordered},
        # Only the user's own input is a fixed time. start_min is derived output --
        # feeding it back in would pin every place to last run's clock.
        {p.id: p.user_start_min for p in ordered},
        legs_min,
        day_start_min=day_start_min,
    )
    if persist:
        await repositories.persist_schedule(
            db,
            [
                (s.place_id, s.travel_min_before, s.arrive_min, s.start_min)
                for s in schedule.places
            ],
        )
        by_id = {p.id: p for p in await repositories.get_db_places(db, day.id)}
        places = [by_id[p.id] for p in ordered if p.id in by_id]
    else:
        places = [
            p.model_copy(
                update={
                    "travel_min_before": s.travel_min_before,
                    "arrive_min": s.arrive_min,
                    "start_min": s.start_min,
                }
            )
            for p, s in zip(ordered, schedule.places, strict=True)
        ]
    return DayTimeline(
        day_id=day.id,
        places=places,
        end_min=schedule.end_min,
        travel_min=sum(s.travel_min_before for s in schedule.places),
        warnings=[*warnings, *schedule.warnings],
        exact=exact,
    )


async def trip_defaults(db: Database, trip_id: str) -> tuple[TravelMode, int]:
    """Trip-level fallbacks a schedule starts from: travel mode and the day's start.

    A day can override both (days.travel_mode / days.start_min); this is what it falls
    back to.
    """
    row = await db.fetch_one(
        "SELECT travel_mode, day_start_min FROM trips WHERE id = ?", (trip_id,)
    )
    if row is None:  # pragma: no cover - callers check the trip exists first
        return TravelMode.DRIVING, 540
    return TravelMode(row["travel_mode"]), int(row["day_start_min"])


async def reschedule_days(
    db: Database, trip_id: str, day_ids: Sequence[str], persist: bool = True
) -> list[DayTimeline]:
    """Re-time these days with zero Amap calls. Days with no places are skipped.

    ``persist=False`` is the dry run the join path uses: no writes, no rev bumps, so its
    rows arrive with the same rev the client already holds and are a no-op there -- only
    the day-level numbers (end_min / travel_min / warnings / exact) are new information.
    """
    trip_mode, day_start_min = await trip_defaults(db, trip_id)

    timelines: list[DayTimeline] = []
    for day_id in dict.fromkeys(day_ids):  # dedupe, keep order
        day = await repositories.get_day(db, day_id)
        if day is None or day.trip_id != trip_id:
            continue
        ordered = await repositories.get_db_places(db, day_id)
        if not ordered:
            continue

        nodes = [(p.lng, p.lat) for p in ordered]
        mode = day.travel_mode or trip_mode
        matrix = await cached_or_estimate_matrix(
            nodes, travel_mode=TravelMode(mode), cache=get_distance_cache()
        )
        legs_min = [[None if s is None else int(s) // 60 for s in row] for row in matrix.seconds]
        # Column 0 is never fetched (nobody arrives at the day's starting point), so
        # "every pair we asked for came back from cache" is (n-1) squared.
        exact = matrix.cache_hits >= (len(nodes) - 1) ** 2
        timelines.append(
            await apply_timeline(
                db,
                day=day,
                ordered=ordered,
                legs_min=legs_min,
                day_start_min=day.start_min or day_start_min,
                warnings=matrix.warnings,
                exact=exact,
                persist=persist,
            )
        )
    return timelines
