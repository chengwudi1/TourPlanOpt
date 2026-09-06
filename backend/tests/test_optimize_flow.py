"""End-to-end optimize flow over HTTP: dry-run, apply, schedule persistence, undo.

Uses the keyless haversine cost model -- the whole optimization pipeline must be
usable (and testable) with zero Amap quota.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database, set_db


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
        ws.send_json(
            {
                "v": 1,
                "type": "hello",
                "data": {"client_id": "c-1", "name": "小明", "color": "#123456"},
            }
        )
        ws.receive_json()  # welcome
        ws.receive_json()  # own presence_join
        ws.send_json(
            {
                "v": 1,
                "type": "op",
                "op": "day_reorder",
                "op_id": "undo-1",
                "data": {"day_id": day, "place_ids": body["prev_place_ids"]},
            }
        )
        frame = ws.receive_json()
        assert frame["type"] == "op" and frame["op"] == "day_reordered"
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
                "op": "place_lock",
                "op_id": "lock-1",
                "data": {"place_id": locked_id, "locked": True},
            }
        )
        frame = ws.receive_json()
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
        assert ws.receive_json()["op"] == "day_updated"
        ws.send_json(
            {
                "v": 1,
                "type": "op",
                "op": "place_update",
                "op_id": "time-1",
                "data": {"place_id": timed_id, "patch": {"start_min": 600}},
            }
        )
        assert ws.receive_json()["op"] == "place_updated"

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
