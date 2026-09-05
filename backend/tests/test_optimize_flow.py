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
    undo = testclient.get(f"/api/trips/{trip_id}").json()  # placeholder to keep flow clear
    socket_client = testclient
    with socket_client.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        ws.send_json({"v": 1, "type": "hello", "data": {"client_id": "c-1", "name": "小明", "color": "#123456"}})
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
        ws.send_json({"v": 1, "type": "hello", "data": {"client_id": "c-1", "name": "小明", "color": "#123456"}})
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
