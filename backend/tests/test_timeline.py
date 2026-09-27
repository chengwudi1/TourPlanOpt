"""M14: the day's timeline follows every edit, not just the optimize button.

Two properties hold this together and both are cheap to break:

- zero Amap quota. The recompute runs on every keystroke-level edit, so its cost matrix
  is haversine estimates overlaid with cache entries an earlier precise run already paid
  for. Nothing here may reach the network -- hence the stub cache.
- only the user's own input is a fixed time. persist_schedule writes start_min for every
  row, so if the scheduler read start_min back as an anchor, the second optimize (or any
  recompute at all) would be inert.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.amap.cache import CacheEntry, DistanceCache, round_key
from app.db.database import Database, set_db
from app.models import protocol
from app.models.domain import AMAP_MODE_BY_TRAVEL, TravelMode
from app.routing.matrix import cached_or_estimate_matrix, haversine_seconds_matrix
from tests.frames import join, read_op

DRIVING = AMAP_MODE_BY_TRAVEL[TravelMode.DRIVING]

# Three points on a line, ~1.1 km apart.
COORDS = [(121.0, 31.0), (121.01, 31.0), (121.02, 31.0)]


def _hit(origin, destination, seconds: int, ok: bool = True) -> tuple[tuple, CacheEntry]:
    key = (*round_key(origin), *round_key(destination), DRIVING)
    return key, CacheEntry(distance_m=1 if ok else None, duration_s=seconds if ok else None,
                           ok=ok, infocode=None)


class _StubCache(DistanceCache):
    """A cache that only ever returns what the test hands it, and records the lookups."""

    def __init__(self, hits: dict | None = None) -> None:
        self.hits = hits or {}
        self.looked_up: list = []
        self.put_calls = 0

    async def get(self, pairs, mode):  # type: ignore[override]
        self.looked_up.append((list(pairs), mode))
        return self.hits

    async def put(self, rows):  # type: ignore[override]
        self.put_calls += 1  # pragma: no cover - a recompute must never write
        return 0


@pytest.fixture()
def client(tmp_path) -> Iterator[tuple[TestClient, str, str]]:
    """App on a temp database with one trip. Yields (client, trip_id, day_id)."""
    set_db(Database(tmp_path / "timeline.db"))
    from app.main import app

    with TestClient(app) as testclient:
        resp = testclient.post("/api/trips", json={"title": "时间轴", "city": "上海"})
        assert resp.status_code == 201
        body = resp.json()
        yield testclient, body["trip_id"], body["day_id"]
    set_db(Database(":memory:"))


# -- the zero-call matrix ------------------------------------------------------------


async def test_estimate_matrix_never_touches_the_network() -> None:
    stub = _StubCache()
    nodes = list(COORDS)
    result = await cached_or_estimate_matrix(nodes, cache=stub)

    assert result.seconds == haversine_seconds_matrix(nodes, TravelMode.DRIVING)
    assert result.api_calls == 0 and result.cache_hits == 0
    assert stub.put_calls == 0


async def test_estimate_matrix_overlays_cached_real_seconds() -> None:
    """A leg a precise run already paid for is reused exactly, not re-estimated."""
    nodes = list(COORDS)
    key, entry = _hit(nodes[0], nodes[2], 900)
    result = await cached_or_estimate_matrix(nodes, cache=_StubCache({key: entry}))

    estimate = haversine_seconds_matrix(nodes, TravelMode.DRIVING)
    assert result.seconds[0][2] == 900 != estimate[0][2]
    assert result.seconds[0][1] == estimate[0][1]  # untouched legs stay estimates
    assert result.cache_hits == 1


async def test_negative_cache_entries_do_not_overwrite_the_estimate() -> None:
    """ok=0 means "we already asked and it failed" -- keep the estimate, don't re-ask."""
    nodes = list(COORDS)
    key, entry = _hit(nodes[0], nodes[1], 0, ok=False)
    result = await cached_or_estimate_matrix(nodes, cache=_StubCache({key: entry}))

    assert result.seconds[0][1] == haversine_seconds_matrix(nodes, TravelMode.DRIVING)[0][1]
    assert result.cache_hits == 0


@pytest.mark.parametrize("nodes", [[], [(121.0, 31.0)]])
async def test_matrix_of_a_day_too_short_to_travel(nodes) -> None:
    result = await cached_or_estimate_matrix(nodes, cache=_StubCache())
    assert result.seconds == [[0] * len(nodes) for _ in nodes]
    assert result.api_calls == 0


# -- the auto-recompute path ---------------------------------------------------------


def add_place(ws, day_id: str, name: str, lng: float, lat: float) -> tuple[dict, dict]:
    """Apply one place_add and hand back (the row, the schedule frame it triggered)."""
    ws.send_json(
        protocol.op_frame(
            protocol.Ops.PLACE_ADD,
            f"op-add-{name}",
            {"day_id": day_id, "name": name, "lng": lng, "lat": lat},
        )
    )
    place = read_op(ws, "place_added")["data"]["place"]
    return place, read_op(ws, "timeline_updated")


def timelines(frame: dict) -> dict[str, dict]:
    return {t["day_id"]: t for t in frame["data"]["timelines"]}


def test_adding_a_place_schedules_without_optimizing(client) -> None:
    """The complaint M14 answers: a freshly added place used to read 路程未知 until
    someone pressed 优化."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        first, _ = add_place(ws, day_id, "甲", *COORDS[0])
        second, frame = add_place(ws, day_id, "乙", *COORDS[2])

        day = timelines(frame)[day_id]
        places = day["places"]
        assert [p["id"] for p in places] == [first["id"], second["id"]]
        assert places[0]["travel_min_before"] == 0
        assert places[0]["arrive_min"] == 540  # 09:00
        assert places[1]["travel_min_before"] > 0
        assert places[1]["arrive_min"] == (
            places[0]["start_min"] + places[0]["duration_min"] + places[1]["travel_min_before"]
        )
        assert day["travel_min"] == places[1]["travel_min_before"]
        assert day["end_min"] == places[1]["start_min"] + places[1]["duration_min"]
        assert day["exact"] is False  # nothing was ever fetched for this pair


def test_one_place_day_schedules_and_empty_day_says_nothing(client) -> None:
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        place, frame = add_place(ws, day_id, "独苗", *COORDS[0])
        day = timelines(frame)[day_id]
        assert day["places"][0]["arrive_min"] == 540
        assert day["travel_min"] == 0
        assert day["end_min"] == 540 + place["duration_min"]

        ws.send_json(protocol.op_frame(protocol.Ops.DAY_ADD, "op-day2", {"title": "第 2 天"}))
        assert read_op(ws, "day_added")["type"] == "op"

        ws.send_json(
            protocol.op_frame(protocol.Ops.PLACE_DELETE, "op-del", {"place_id": place["id"]})
        )
        ws.send_json(protocol.ping_frame())
        assert read_op(ws, "place_deleted")["type"] == "op"
        # An emptied day has no timeline to recompute: the pong proves nothing followed.
        assert ws.receive_json()["type"] == "pong"


def test_rename_does_not_retime(client) -> None:
    """A name patch must not bump every rev in the day -- other clients would redraw a
    timeline that did not change."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        place, frame = add_place(ws, day_id, "甲", *COORDS[0])
        # place_add's own row is already stale by one rev: persist_schedule bumped it.
        rev = timelines(frame)[day_id]["places"][0]["rev"]

        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-rename",
                {"place_id": place["id"], "patch": {"name": "甲乙丙"}},
            )
        )
        ws.send_json(protocol.ping_frame())
        renamed = ws.receive_json()
        assert renamed["op"] == "place_updated"
        assert renamed["data"]["place"]["rev"] == rev + 1
        assert ws.receive_json()["type"] == "pong", "a rename triggered a schedule frame"


def test_moving_a_place_retimes_both_days(client) -> None:
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        kept, _ = add_place(ws, day_id, "甲", *COORDS[0])
        moved, _ = add_place(ws, day_id, "乙", *COORDS[1])
        ws.send_json(protocol.op_frame(protocol.Ops.DAY_ADD, "op-day2", {"title": "第 2 天"}))
        day2 = read_op(ws, "day_added")["data"]["day"]["id"]

        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_MOVE, "op-move", {"place_id": moved["id"], "day_id": day2}
            )
        )
        assert read_op(ws, "place_moved")["type"] == "op"
        days = timelines(read_op(ws, "timeline_updated"))
        assert set(days) == {day_id, day2}
        assert [p["id"] for p in days[day_id]["places"]] == [kept["id"]]
        assert [p["id"] for p in days[day2]["places"]] == [moved["id"]]
        # Both days restart their clock: the leg into a day's first place is always 0.
        assert all(t["places"][0]["travel_min_before"] == 0 for t in days.values())


def test_trip_day_start_change_retimes_every_day(client) -> None:
    """day_start_min lives on the trip but shows up in each day's clock."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        add_place(ws, day_id, "甲", *COORDS[0])

        ws.send_json(
            protocol.op_frame(
                protocol.Ops.TRIP_UPDATE, "op-start", {"patch": {"day_start_min": 480}}
            )
        )
        assert read_op(ws, "trip_updated")["type"] == "op"
        day = timelines(read_op(ws, "timeline_updated"))[day_id]
        assert day["places"][0]["arrive_min"] == 480


def test_hand_set_time_is_the_only_fixed_point(client) -> None:
    """The anchor split, end to end: the user's 19:00 dinner holds its clock while an
    earlier place is added, and the unlocked places keep flowing."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        dinner, _ = add_place(ws, day_id, "晚饭", *COORDS[0])
        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-dinner-time",
                {"place_id": dinner["id"], "patch": {"start_min": 19 * 60}},
            )
        )
        pinned = read_op(ws, "place_updated")["data"]["place"]
        assert pinned["user_start_min"] == 19 * 60
        day = timelines(read_op(ws, "timeline_updated"))[day_id]
        assert day["places"][0]["start_min"] == 19 * 60
        assert day["end_min"] == 19 * 60 + dinner["duration_min"]

        tea, _ = add_place(ws, day_id, "下午茶", *COORDS[2])
        ws.send_json(
            protocol.op_frame(
                protocol.Ops.DAY_REORDER,
                "op-reorder",
                {"day_id": day_id, "place_ids": [tea["id"], dinner["id"]]},
            )
        )
        assert read_op(ws, "day_reordered")["type"] == "op"
        day = timelines(read_op(ws, "timeline_updated"))[day_id]
        by_id = {p["id"]: p for p in day["places"]}
        assert [p["id"] for p in day["places"]] == [tea["id"], dinner["id"]]
        assert by_id[tea["id"]]["start_min"] == 540, "a free place follows the day start"
        assert by_id[dinner["id"]]["start_min"] == 19 * 60, "the anchor drifted"
        assert day["end_min"] == 19 * 60 + dinner["duration_min"]


def test_unlocking_releases_the_time_anchor(client) -> None:
    """「解锁」 has to actually free the place: a leftover hand-set time would keep it
    pinned even though the row now says unlocked."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        place, _ = add_place(ws, day_id, "晚饭", *COORDS[0])
        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-time",
                {"place_id": place["id"], "patch": {"start_min": 19 * 60}},
            )
        )
        read_op(ws, "place_updated")
        read_op(ws, "timeline_updated")

        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_LOCK, "op-unlock", {"place_id": place["id"], "locked": False}
            )
        )
        assert read_op(ws, "place_locked")["type"] == "op"
        day = timelines(read_op(ws, "timeline_updated"))[day_id]
        assert day["places"][0]["user_start_min"] is None
        assert day["places"][0]["start_min"] == 540  # back on the schedule, not at 19:00


def test_joining_a_populated_trip_gets_its_schedule(client) -> None:
    """Reload keeps 结束时间: the snapshot is a pure read, so the day-level numbers ride
    in on a unicast dry-run frame right after the welcome."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        assert join(ws) is None, "an empty trip has nothing to schedule"
        first, _ = add_place(ws, day_id, "甲", *COORDS[0])
        _, frame = add_place(ws, day_id, "乙", *COORDS[2])
    last = timelines(frame)[day_id]

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
        joined = timelines(join(ws2))[day_id]

    assert [p["id"] for p in joined["places"]] == [first["id"], last["places"][1]["id"]]
    assert joined["end_min"] == last["end_min"]
    assert joined["travel_min"] == last["travel_min"]
    assert joined["warnings"] == last["warnings"]
    # A join is a read: a rev bump here would make every other client redraw a timeline
    # that did not change.
    assert [p["rev"] for p in joined["places"]] == [p["rev"] for p in last["places"]]


def test_join_frame_carries_the_late_night_warning(client) -> None:
    """The reminder used to exist only inside the optimize card. It must survive a
    reload, which is exactly what the join frame is for."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        place, _ = add_place(ws, day_id, "夜宵", *COORDS[0])
        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-late",
                {"place_id": place["id"], "patch": {"start_min": 23 * 60}},
            )
        )
        read_op(ws, "place_updated")
        broadcast = timelines(read_op(ws, "timeline_updated"))[day_id]
    assert any("才结束" in w for w in broadcast["warnings"]), broadcast["warnings"]
    # 23:00 + 1 小时 = 正好午夜：以前这里印的是 "24:00"。
    assert any("次日 00:00" in w for w in broadcast["warnings"]), broadcast["warnings"]

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
        joined = timelines(join(ws2))[day_id]
    assert joined["warnings"] == broadcast["warnings"]


# -- 天级交通方式覆盖（行程页每一天那个选择器）-------------------------------------------


def set_day_mode(ws, day_id: str, mode, op_id: str) -> dict:
    """发一笔 day_update 只改这一天的交通方式，返回 ``day_updated`` 帧。"""
    ws.send_json(
        protocol.op_frame(
            protocol.Ops.DAY_UPDATE,
            op_id,
            {"day_id": day_id, "patch": {"travel_mode": mode}},
        )
    )
    return read_op(ws, "day_updated")


def day_after_mode_change(ws, day_id: str, mode, op_id: str) -> tuple[dict, dict]:
    """改这一天的交通方式，返回 (落库的 day 行, 重排后的这一天)。

    重排帧用封顶读取，不用 ``read_op``：后者在「这一笔改动压根没触发重排」时的表现是
    永久阻塞在 ``receive_json`` 上——测试不红，只是把整条 CI 挂住，比红更难查。所以
    这一笔之后紧跟一帧 ping，pong 先于重排帧到达就说明没有重排，当场判红。
    """
    day = set_day_mode(ws, day_id, mode, op_id)["data"]["day"]
    ws.send_json(protocol.ping_frame())
    for _ in range(8):
        frame = ws.receive_json()
        if frame.get("op") == "timeline_updated":
            return day, timelines(frame)[day_id]
        if frame.get("type") == "pong":
            break
    raise AssertionError(f"改交通方式 {mode} 之后没有重排帧")


def test_day_mode_override_retimes_the_day(client) -> None:
    """改了这一天怎么走，这一天的时刻必须跟着重算。

    这是那个选择器唯一的意义。``travel_mode`` 一旦从 ``_TIMELINE_DAY_FIELDS`` 里被拿掉，
    用户看到的是图标换了、时间一分没动——而时间才是这一路的主角，图标只是它的说明。
    """
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        add_place(ws, day_id, "甲", *COORDS[0])
        _, frame = add_place(ws, day_id, "乙", *COORDS[2])
        by_car = timelines(frame)[day_id]

        day, by_foot = day_after_mode_change(ws, day_id, "walking", "op-mode")
        assert day["travel_mode"] == "walking"

        # 同一段路：30km/h 与 4.5km/h，差的是七倍量级，不是四舍五入能抹平的。
        assert by_foot["travel_min"] > by_car["travel_min"] * 3, by_foot
        assert by_foot["end_min"] > by_car["end_min"]


def test_clearing_the_day_mode_falls_back_to_trip_default(client) -> None:
    """「跟随默认」在天里存的是 null，重算之后这一天要回到行程默认那一档。

    null 不是「没交通方式」，它是「这一级不作主」——所以它必须和没设过时一模一样。
    """
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        add_place(ws, day_id, "甲", *COORDS[0])
        _, frame = add_place(ws, day_id, "乙", *COORDS[2])
        by_car = timelines(frame)[day_id]

        _, by_foot = day_after_mode_change(ws, day_id, "walking", "op-mode")
        assert by_foot["travel_min"] > by_car["travel_min"]

        day, back = day_after_mode_change(ws, day_id, None, "op-clear")
        assert day["travel_mode"] is None
        assert back["travel_min"] == by_car["travel_min"]
        assert back["end_min"] == by_car["end_min"]


def test_bad_day_mode_is_rejected_and_the_socket_survives(client) -> None:
    """认不出的交通方式要回一帧 op_reject，而不是让 SQLite 的 CHECK 掀掉整条连接。

    抛出去的 symptoms 是同伴那边「重连中」而刚改的东西原地消失——那一笔到底有没有生效，
    界面上一个字都没有。
    """
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        add_place(ws, day_id, "甲", *COORDS[0])

        rejected = set_day_mode(ws, day_id, "boat", "op-boat")
        assert rejected["type"] == "op_reject", rejected
        assert rejected["reason"] == "bad_patch" and rejected["op_id"] == "op-boat"

        ws.send_json(protocol.ping_frame())
        assert ws.receive_json()["type"] == "pong"
        assert set_day_mode(ws, day_id, "walking", "op-after-bad")["data"]["day"][
            "travel_mode"
        ] == "walking", "坏值之后这条连接再也写不进任何东西"
