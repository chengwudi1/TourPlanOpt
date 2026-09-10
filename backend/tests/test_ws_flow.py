"""The collaboration guarantees, exercised over a real socket loop.

These tests are the backend half of M3/M4 verification: hello/welcome handshake, seq
monotonicity without gaps, op broadcast to everyone including the originator, the
order_stale rejection carrying the authoritative array, and op_id dedupe.

Seq invariant under test: welcome carries the CURRENT seq without consuming one, and
every seq-consuming broadcast reaches every member -- so no member ever observes a gap.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database, set_db
from app.models import protocol
from tests.frames import read_op


@pytest.fixture()
def client(tmp_path) -> Iterator[tuple[TestClient, str, str]]:
    """App with a temp database and one freshly created trip. Yields (client, trip_id, day_id)."""
    set_db(Database(tmp_path / "ws-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        resp = testclient.post("/api/trips", json={"title": "WS 测试", "city": "上海"})
        assert resp.status_code == 201
        body = resp.json()
        yield testclient, body["trip_id"], body["day_id"]
    set_db(Database(":memory:"))  # detach the temp db from global state


def hello(client_id: str, name: str) -> dict:
    return protocol.hello_frame(client_id, name, "#123456")


def join_and_sync(ws, client_id: str, name: str) -> tuple[dict, int]:
    """Say hello, read the welcome and our own presence_join echo. Returns (welcome, seq).

    A joiner whose trip already has places gets one extra frame in between: the
    unicast schedule summary that lets a freshly loaded page show 结束时间 before it
    touches anything (app/ws/handlers.py). It reuses the welcome's seq and consumes none,
    so the +1 below still holds.
    """
    ws.send_json(hello(client_id, name))
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome"
    assert welcome["data"]["you"]["client_id"] == client_id
    own_join = ws.receive_json()
    while own_join.get("op") == "timeline_updated":
        own_join = ws.receive_json()
    assert own_join["type"] == "presence_join"
    assert own_join["data"]["client_id"] == client_id
    assert own_join["seq"] == welcome["seq"] + 1
    return welcome, own_join["seq"]


def test_two_windows_see_each_other(client: tuple[TestClient, str, str]):
    testclient, trip_id, _ = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        w1, _seq = join_and_sync(ws1, "c-1", "小明")
        assert w1["data"]["you"]["client_id"] == "c-1"

        with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
            w2, _seq2 = join_and_sync(ws2, "c-2", "小红")
            # snapshot presence shows both participants to the late joiner
            presence_ids = {p["client_id"] for p in w2["data"]["snapshot"]["presence"]}
            assert presence_ids == {"c-1", "c-2"}

            # c-1 sees the join; the joiner got its own copy above (same seq)
            join = ws1.receive_json()
            assert join["type"] == "presence_join"
            assert join["data"]["client_id"] == "c-2"
            assert join["seq"] == w2["seq"] + 1

        # c-2 left: c-1 is told within the frame loop, no polling
        leave = ws1.receive_json()
        assert leave["type"] == "presence_leave"
        assert leave["data"]["client_id"] == "c-2"


def test_seq_strictly_increasing_no_gaps(client: tuple[TestClient, str, str]):
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        w1, join_seq = join_and_sync(ws1, "c-1", "小明")

        seqs = [w1["seq"], join_seq]
        ws1.send_json(protocol.ping_frame())
        assert ws1.receive_json()["type"] == "pong"
        for i in range(3):
            ws1.send_json(
                protocol.op_frame(
                    protocol.Ops.PLACE_ADD,
                    f"op-seq-{i}",
                    {"day_id": day_id, "name": f"点{i}", "lng": 121.47, "lat": 31.23},
                )
            )
            added = read_op(ws1, "place_added")
            assert added["type"] == "op"
            assert added["op"] == "place_added"
            seqs.append(added["seq"])
            # Every op that moves or re-times a place owns a second frame: the
            # recomputed schedule. It consumes a seq too, so the run stays gapless.
            timeline = ws1.receive_json()
            assert timeline["op"] == "timeline_updated"
            seqs.append(timeline["seq"])

        assert seqs == sorted(seqs)
        assert all(b - a == 1 for a, b in zip(seqs, seqs[1:], strict=False)), seqs


def test_place_add_broadcast_and_echo_filter(client: tuple[TestClient, str, str]):
    """The originator receives its own op back (that IS the ack); the other window sees
    the same authoritative row."""
    testclient, trip_id, day_id = client
    with (
        testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1,
        testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2,
    ):
        join_and_sync(ws1, "c-1", "小明")
        join_and_sync(ws2, "c-2", "小红")
        ws1.receive_json()  # c-2's join as seen by c-1

        op_id = "op-add-1"
        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_ADD,
                op_id,
                {"day_id": day_id, "name": "外滩", "lng": 121.49, "lat": 31.23, "added_by": "c-1"},
            )
        )
        echo = ws1.receive_json()
        assert echo["type"] == "op" and echo["op"] == "place_added" and echo["op_id"] == op_id
        assert echo["origin"] == "c-1"

        other = ws2.receive_json()
        assert other["type"] == "op" and other["op"] == "place_added"
        assert other["data"]["place"]["name"] == "外滩"
        assert other["data"]["place_ids"] == [other["data"]["place"]["id"]]


def test_duplicate_op_id_is_deduped(client: tuple[TestClient, str, str]):
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join_and_sync(ws1, "c-1", "小明")

        frame = protocol.op_frame(
            protocol.Ops.PLACE_ADD,
            "op-dup",
            {"day_id": day_id, "name": "重复", "lng": 121.47, "lat": 31.23},
        )
        ws1.send_json(frame)
        first = read_op(ws1, "place_added")
        assert first["type"] == "op"
        assert ws1.receive_json()["op"] == "timeline_updated"  # the place_added's schedule

        # Same op_id again: silent no-op -- not even a schedule frame. The very next
        # frame is the pong, proving nothing else was broadcast.
        ws1.send_json(frame)
        ws1.send_json(protocol.ping_frame())
        assert ws1.receive_json()["type"] == "pong"

        # And the database holds exactly one row.
        snapshot = testclient.get(f"/api/trips/{trip_id}").json()
        assert [p["name"] for p in snapshot["places"]] == ["重复"]


def test_day_reorder_non_permutation_rejected_with_authority(
    client: tuple[TestClient, str, str],
):
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join_and_sync(ws1, "c-1", "小明")

        for name in ("甲", "乙", "丙"):
            ws1.send_json(
                protocol.op_frame(
                    protocol.Ops.PLACE_ADD,
                    f"op-{name}",
                    {"day_id": day_id, "name": name, "lng": 121.47, "lat": 31.23},
                )
            )
            read_op(ws1, "place_added")

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.DAY_REORDER,
                "op-reorder-bad",
                {"day_id": day_id, "place_ids": ["nope-1", "nope-2"]},
            )
        )
        reject = read_op(ws1, "day_reordered")
        assert reject["type"] == "op_reject"
        assert reject["reason"] == "order_stale"
        authoritative = reject["data"]["place_ids"]
        assert len(authoritative) == 3 and all(len(pid) == 8 for pid in authoritative)

        # A valid permutation of the authoritative array is accepted.
        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.DAY_REORDER,
                "op-reorder-good",
                {"day_id": day_id, "place_ids": list(reversed(authoritative))},
            )
        )
        applied = read_op(ws1, "day_reordered")
        assert applied["type"] == "op" and applied["op"] == "day_reordered"
        assert applied["data"]["place_ids"] == list(reversed(authoritative))


def test_place_update_field_patch_lww(client: tuple[TestClient, str, str]):
    """Two clients patch DIFFERENT fields of the same row; both survive (the C1
    property from the plan, backend half)."""
    testclient, trip_id, day_id = client
    with (
        testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1,
        testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2,
    ):
        join_and_sync(ws1, "c-1", "小明")
        join_and_sync(ws2, "c-2", "小红")
        ws1.receive_json()  # c-2's join as seen by c-1

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_ADD,
                "op-new",
                {"day_id": day_id, "name": "餐厅", "lng": 121.47, "lat": 31.23},
            )
        )
        place = read_op(ws1, "place_added")["data"]["place"]
        read_op(ws2, "place_added")

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-note",
                {"place_id": place["id"], "patch": {"note": "不吃辣"}},
            )
        )
        from_1 = read_op(ws1, "place_updated")
        assert from_1["data"]["place"]["note"] == "不吃辣"
        read_op(ws2, "place_updated")

        ws2.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-duration",
                {"place_id": place["id"], "patch": {"duration_min": 45}},
            )
        )
        from_2 = read_op(ws2, "place_updated")
        assert from_2["data"]["place"]["duration_min"] == 45
        # duration feeds the schedule, so c-1 gets the timeline frame that note does not.
        timeline = read_op(ws1, "timeline_updated")
        assert timeline["data"]["timelines"][0]["places"][0]["duration_min"] == 45

        final = testclient.get(f"/api/trips/{trip_id}").json()["places"][0]
        assert final["note"] == "不吃辣"
        assert final["duration_min"] == 45


def test_resync_returns_full_snapshot(client: tuple[TestClient, str, str]):
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join_and_sync(ws1, "c-1", "小明")

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_ADD,
                "op-resync",
                {"day_id": day_id, "name": "外滩", "lng": 121.49, "lat": 31.23},
            )
        )
        read_op(ws1, "place_added")
        ws1.receive_json()  # its timeline_updated frame

        ws1.send_json(protocol.resync_frame(0))
        frame = ws1.receive_json()
        assert frame["type"] == "welcome"
        places = frame["data"]["snapshot"]["places"]
        assert [p["name"] for p in places] == ["外滩"]


def test_setting_time_auto_locks_and_clearing_unlocks(client):
    """修正 2: a hand-set start_min is an anchor -- the server pins the place in the
    same atomic patch; clearing the time releases it."""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join_and_sync(ws1, "c-1", "小明")

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_ADD,
                "op-lock-time",
                {"day_id": day_id, "name": "餐厅", "lng": 121.47, "lat": 31.23},
            )
        )
        place = read_op(ws1, "place_added")["data"]["place"]

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-set-time",
                {"place_id": place["id"], "patch": {"start_min": 19 * 60}},
            )
        )
        updated = read_op(ws1, "place_updated")["data"]["place"]
        assert updated["start_min"] == 19 * 60
        assert updated["user_start_min"] == 19 * 60  # the anchor is the user's own value
        assert updated["locked"] is True

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-clear-time",
                {"place_id": place["id"], "patch": {"start_min": None}},
            )
        )
        cleared = read_op(ws1, "place_updated")["data"]["place"]
        assert cleared["user_start_min"] is None
        assert cleared["locked"] is False
        # start_min is derived: clearing the anchor only detaches it, and the schedule
        # frame that follows writes a computed time back.
        timeline = read_op(ws1, "timeline_updated")["data"]["timelines"][0]
        assert timeline["places"][0]["user_start_min"] is None
        assert timeline["places"][0]["start_min"] is not None


# -- M12：跨天移动 / 删空天 / 想去清单 / 起点锚点 ---------------------------------------


def _add_place_rest(testclient: TestClient, trip_id: str, day_id: str, name: str) -> dict:
    resp = testclient.post(
        f"/api/trips/{trip_id}/days/{day_id}/places",
        json={"name": name, "lng": 121.47, "lat": 31.23},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_place_move_cross_day_broadcasts_both_orders(client):
    """place_move 是纯跨天移动：广播带回两天各自的权威顺序。"""
    testclient, trip_id, day1 = client
    place = _add_place_rest(testclient, trip_id, day1, "外滩")
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join_and_sync(ws, "c-1", "小明")

        ws.send_json(protocol.op_frame(protocol.Ops.DAY_ADD, "op-day2", {}))
        frame = ws.receive_json()
        assert frame["op"] == "day_added"
        day2 = frame["data"]["day"]["id"]

        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_MOVE,
                "op-move-1",
                {"place_id": place["id"], "day_id": day2},
            )
        )
        frame = ws.receive_json()
        assert frame["type"] == "op" and frame["op"] == "place_moved"
        data = frame["data"]
        assert data["place"]["id"] == place["id"]
        assert data["old_day_id"] == day1
        assert data["old_place_ids"] == []
        assert data["day_id"] == day2
        assert data["place_ids"] == [place["id"]]


def test_place_move_unknown_target_day_rejected(client):
    testclient, trip_id, day1 = client
    place = _add_place_rest(testclient, trip_id, day1, "外滩")
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join_and_sync(ws, "c-1", "小明")
        ws.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_MOVE,
                "op-move-bad",
                {"place_id": place["id"], "day_id": "day-nope"},
            )
        )
        frame = ws.receive_json()
        assert frame["type"] == "op_reject"
        assert frame["reason"] == "day_not_found"


def test_day_delete_empty_day_ok_and_non_empty_rejected(client):
    """空天直接删；有内容的天拒绝（day_not_empty），数据一个都不丢。"""
    testclient, trip_id, day1 = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join_and_sync(ws, "c-1", "小明")

        ws.send_json(protocol.op_frame(protocol.Ops.DAY_ADD, "op-day2", {}))
        day2 = ws.receive_json()["data"]["day"]["id"]

        ws.send_json(protocol.op_frame(protocol.Ops.DAY_DELETE, "op-del-1", {"day_id": day2}))
        frame = ws.receive_json()
        assert frame["type"] == "op" and frame["op"] == "day_deleted"
        assert frame["data"]["day_id"] == day2

        _add_place_rest(testclient, trip_id, day1, "外滩")
        ws.send_json(protocol.op_frame(protocol.Ops.DAY_DELETE, "op-del-2", {"day_id": day1}))
        frame = ws.receive_json()
        assert frame["type"] == "op_reject"
        assert frame["reason"] == "day_not_empty"

        snap = testclient.get(f"/api/trips/{trip_id}").json()
        assert [d["id"] for d in snap["days"]] == [day1]
        assert len(snap["places"]) == 1


def test_stash_add_and_remove_round_trip(client):
    """想去清单：stash_add 广播权威条目并进快照；stash_remove 删掉。"""
    testclient, trip_id, _day = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join_and_sync(ws, "c-1", "小明")

        ws.send_json(
            protocol.op_frame(
                protocol.Ops.STASH_ADD,
                "op-stash-1",
                {"name": "城隍庙", "lng": 121.49, "lat": 31.23, "address": "方浜中路"},
            )
        )
        frame = ws.receive_json()
        assert frame["type"] == "op" and frame["op"] == "stash_added"
        item = frame["data"]["item"]
        assert item["name"] == "城隍庙"
        assert item["added_by"] == "c-1"

        snap = testclient.get(f"/api/trips/{trip_id}").json()
        assert [s["id"] for s in snap["stash"]] == [item["id"]]

        ws.send_json(protocol.op_frame(protocol.Ops.STASH_REMOVE, "op-stash-2", {"id": item["id"]}))
        frame = ws.receive_json()
        assert frame["type"] == "op" and frame["op"] == "stash_removed"
        assert frame["data"]["id"] == item["id"]

        snap = testclient.get(f"/api/trips/{trip_id}").json()
        assert snap["stash"] == []


def test_stash_remove_unknown_id_rejected(client):
    testclient, trip_id, _day = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join_and_sync(ws, "c-1", "小明")
        ws.send_json(
            protocol.op_frame(protocol.Ops.STASH_REMOVE, "op-stash-bad", {"id": "nope"})
        )
        frame = ws.receive_json()
        assert frame["type"] == "op_reject"
        assert frame["reason"] == "stash_not_found"


def test_day_update_sets_start_place_anchor(client):
    """设为起点：day_update 白名单接受 start_place_id 并回权威行。"""
    testclient, trip_id, day1 = client
    place = _add_place_rest(testclient, trip_id, day1, "人民广场")
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join_and_sync(ws, "c-1", "小明")
        ws.send_json(
            protocol.op_frame(
                protocol.Ops.DAY_UPDATE,
                "op-start-1",
                {"day_id": day1, "patch": {"start_place_id": place["id"]}},
            )
        )
        frame = ws.receive_json()
        assert frame["type"] == "op" and frame["op"] == "day_updated"
        assert frame["data"]["day"]["start_place_id"] == place["id"]
