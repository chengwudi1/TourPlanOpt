"""End-to-end optimize flow over HTTP: dry-run, apply, schedule persistence, undo.

Uses the keyless haversine cost model -- the whole optimization pipeline must be
usable (and testable) with zero Amap quota.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database, set_db
from app.models import protocol
from tests.frames import join, read_op


@pytest.fixture()
def client(tmp_path) -> Iterator[tuple[TestClient, str, str, list[str]]]:
    set_db(Database(tmp_path / "opt-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        trip = testclient.post(
            "/api/trips", json={"title": "优化测试", "travel_mode": "driving"}
        ).json()
        day = trip["day_id"]
        # Four points on a line: x = 0,1,2,3 km east. Optimal visit order is
        # monotonic; the seeded order below is deliberately wasteful (zigzag).
        coords = [(121.0, 31.0), (121.01, 31.0), (121.02, 31.0), (121.03, 31.0)]
        seeded = [0, 2, 1, 3]  # zigzag: end, middle-left, middle-right... wasted travel
        place_ids = []
        for k in seeded:
            resp = testclient.post(
                f"/api/trips/{trip['trip_id']}/days/{day}/places",
                json={"name": f"P{k}", "lng": coords[k][0], "lat": coords[k][1]},
            )
            place_ids.append(resp.json()["id"])
        yield testclient, trip["trip_id"], day, place_ids
    set_db(Database(":memory:"))


def _leg_minutes(seconds: list[list[int | None]], ids: list[str]) -> int:
    index = {pid: i for i, pid in enumerate(ids)}
    return sum(
        seconds[index[a]][index[b]] or 0 for a, b in zip(ids, ids[1:], strict=False)
    ) // 60


def run_ws_op(
    testclient: TestClient, trip_id: str, op: str, applied_op: str, op_id: str, data: dict
) -> dict:
    """Apply one op over a throwaway socket and return its broadcast.

    Reordering and day/trip patches only exist as WS ops while optimize is HTTP, so a
    test that needs both pays this handshake. ``applied_op`` is spelled out because the
    broadcast is past tense ("day_update" -> "day_updated"), not a derivable suffix.
    """
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        ws.send_json(protocol.op_frame(op, op_id, data))
        return read_op(ws, applied_op)


def test_optimize_dry_run_does_not_reorder(client) -> None:
    testclient, trip_id, day, place_ids = client
    before = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    resp = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["saved_min"] > 0, "zigzag must be strictly improvable"
    after = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    assert [p["id"] for p in after] == [p["id"] for p in before], "dry-run changed nothing"


def test_optimize_apply_reorders_and_schedules(client) -> None:
    testclient, trip_id, day, place_ids = client
    resp = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": True},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["after_min"] < body["summary"]["before_min"]
    assert len(body["place_ids"]) == 4

    # Authoritative order in the DB matches, and the schedule recurrence holds.
    places = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    by_id = {p["id"]: p for p in places}
    ordered = [by_id[pid] for pid in body["place_ids"]]
    assert [p["sort_index"] for p in ordered] == [0, 1, 2, 3]
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        expected = prev["start_min"] + prev["duration_min"] + cur["travel_min_before"]
        assert cur["arrive_min"] == expected
        assert cur["start_min"] == cur["arrive_min"]
    assert ordered[0]["arrive_min"] == 540  # day starts 09:00

    # Undo: sending the previous order back through day_reorder restores it.
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        ws.send_json(
            {
                "v": 1,
                "type": "op",
                "op": "day_reorder",
                "op_id": "undo-1",
                "data": {"day_id": day, "place_ids": body["prev_place_ids"]},
            }
        )
        frame = read_op(ws, "day_reordered")
        assert frame["data"]["place_ids"] == body["prev_place_ids"]

    restored = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    assert [p["id"] for p in restored] == body["prev_place_ids"]


def test_optimize_respects_locked_anchor(client) -> None:
    testclient, trip_id, day, place_ids = client
    # The current (zigzag) day order; the lock must keep its place in THIS sequence.
    current = [p["id"] for p in testclient.get(f"/api/trips/{trip_id}").json()["places"]]
    locked_id = place_ids[1]
    current_position = current.index(locked_id)

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws)
        ws.send_json(
            {
                "v": 1,
                "type": "op",
                "op": "place_lock",
                "op_id": "lock-1",
                "data": {"place_id": locked_id, "locked": True},
            }
        )
        frame = read_op(ws, "place_locked")
        assert frame["op"] == "place_locked"

    resp = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": False},
    )
    assert resp.status_code == 200
    locked_order = resp.json()["place_ids"]

    # 修正 2's guarantee: the anchor stays exactly where it was in the sequence, while
    # the free places around it may be reordered.
    assert locked_order.index(locked_id) == current_position
    assert sorted(locked_order) == sorted(current)


def test_optimize_start_place_rotation_keeps_timed_anchor(client) -> None:
    """起点锚旋转的回归测试。

    「设为起点」把 start_place_id 旋到序列首位再求解。旋转实现里 has_time
    曾被错误复制（has_time[idx:]+has_time[idx:]）：起点选在序列尾部时新表
    比地点短，is_anchor 越界直接 500；起点在中部时手设时间锚的语义整体错位。
    """
    testclient, trip_id, day, _place_ids = client
    current = [p["id"] for p in testclient.get(f"/api/trips/{trip_id}").json()["places"]]
    start_id = current[-1]  # 起点设在序列最后一个：踩长度不足的分支
    timed_id = current[1]  # 头段里的一个点手设时间 → 旋转后成为中段锚

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "hello",
                "data": {"client_id": "c-1", "name": "小明", "color": "#123456"},
            }
        )
        ws.receive_json()
        ws.receive_json()
        ws.send_json(
            {
                "v": 1,
                "type": "op",
                "op": "day_update",
                "op_id": "start-1",
                "data": {"day_id": day, "patch": {"start_place_id": start_id}},
            }
        )
        assert read_op(ws, "day_updated")["op"] == "day_updated"
        ws.send_json(
            {
                "v": 1,
                "type": "op",
                "op": "place_update",
                "op_id": "time-1",
                "data": {"place_id": timed_id, "patch": {"start_min": 600}},
            }
        )
        assert read_op(ws, "place_updated")["op"] == "place_updated"

    resp = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": False},
    )
    assert resp.status_code == 200
    result = resp.json()["place_ids"]

    rotated = current[current.index(start_id):] + current[: current.index(start_id)]
    assert result[0] == start_id, "起点必须钉在首位"
    assert result.index(timed_id) == rotated.index(timed_id), "手设时间的锚不能被旋转挤动"
    assert sorted(result) == sorted(current)


def test_optimize_is_not_inert_after_the_first_apply(client) -> None:
    """M14's regression: applying writes a derived start_min onto every row.

    If the solver or the schedule recurrence read that back as a hand-set anchor, every
    place would become a nail after the first optimize and the second would return the
    order it was given -- the day would quietly stop being optimizable.
    """
    testclient, trip_id, day, _place_ids = client
    first = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": True},
    ).json()
    assert first["summary"]["saved_min"] > 0

    scheduled = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    assert all(p["start_min"] is not None for p in scheduled), "apply must schedule every row"
    assert all(not p["locked"] for p in scheduled), "derived times must not lock anything"
    assert [p["id"] for p in scheduled] == first["place_ids"]

    run_ws_op(
        testclient, trip_id, "day_reorder", "day_reordered", "revert-1",
        {"day_id": day, "place_ids": first["prev_place_ids"]},
    )

    reverted = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    assert [p["id"] for p in reverted] == first["prev_place_ids"], "the revert didn't take"

    second = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": True},
    ).json()
    assert second["summary"]["saved_min"] > 0, "第二次优化空转了：推导时间被当成了锚点"
    assert second["place_ids"] == first["place_ids"]


def test_optimize_pins_the_end_anchor(client) -> None:
    """终点锚：days.end_place_id 以前是一列死数据，现在优化必须把它留在最后一站。

    酒店设在序列头部是最能暴露问题的位置：既要被搬到末位，又要在那儿钉住不让开放路径
    挪回来。这是求解器内部的锚，不是用户自己钉的，所以行的 locked 必须保持 False。
    """
    testclient, trip_id, day, _place_ids = client
    current = [p["id"] for p in testclient.get(f"/api/trips/{trip_id}").json()["places"]]
    end_id = current[0]
    run_ws_op(
        testclient, trip_id, "day_update", "day_updated", "end-1",
        {"day_id": day, "patch": {"end_place_id": end_id}},
    )

    body = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": True},
    ).json()
    assert body["place_ids"][-1] == end_id, "终点没有被钉在最后一站"
    assert body["summary"]["saved_min"] > 0, "钉住终点后剩下的点仍应优化"
    rows = testclient.get(f"/api/trips/{trip_id}").json()["places"]
    end_row = next(p for p in rows if p["id"] == end_id)
    assert end_row["locked"] is False


def test_optimize_uses_the_trips_day_start(client) -> None:
    """The endpoint used to hardcode 09:00; trip.day_start_min is what the schedule
    starts from now -- the same resolution the auto-recompute path uses."""
    testclient, trip_id, day, _place_ids = client
    run_ws_op(
        testclient, trip_id, "trip_update", "trip_updated", "start-1",
        {"patch": {"day_start_min": 8 * 60}},
    )

    result = testclient.post(
        f"/api/trips/{trip_id}/days/{day}/optimize",
        json={"cost_model": "haversine", "apply": True},
    ).json()
    by_id = {p["id"]: p for p in testclient.get(f"/api/trips/{trip_id}").json()["places"]}
    first_place = by_id[result["place_ids"][0]]
    last_place = by_id[result["place_ids"][-1]]
    assert first_place["arrive_min"] == 8 * 60
    assert result["end_min"] == last_place["start_min"] + last_place["duration_min"]
