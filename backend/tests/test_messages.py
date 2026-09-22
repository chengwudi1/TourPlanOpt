"""M30 同行聊天。

分三层，各管各的：数据层管「什么样的话落得了库、窗口怎么裁、软删的行回不回得来」，
WebSocket 层管「双方都看得见、越权删不掉、按得太快被拒」。

最容易写错的是**位置**。聊天跟清单、费用不是一类东西：那两样各自独立，一句被删了放回哪儿
都行；聊天的位置就是语义——「换成早上去吧」指的是它上面那句。所以撤销比的是 `pos`（SQLite
rowid）而不是时间戳，测试也就反复断言「回到原来那一格」，而不是「时间还留着」。

另一处只有测试能钉住的是**级联**：这是项目里第一次存「用户说了什么」。行程没了不能留一份
没有主人的对话，软删的那几行同样是内容，必须一起清掉。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import repositories as repo
from app.db.database import Database, set_db
from app.models import protocol
from app.models.domain import MessageIn
from app.models.protocol import Ops
from tests.frames import read_op


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "m30-test.db")
    await database.init()
    return database


@pytest.fixture()
def client(tmp_path) -> Iterator[tuple[TestClient, str, str]]:
    """App on a temp database with one fresh trip. Yields (client, trip_id, day_id)."""
    set_db(Database(tmp_path / "m30-http.db"))
    from app.main import app

    with TestClient(app) as testclient:
        resp = testclient.post("/api/trips", json={"title": "成都两日", "city": "成都"})
        assert resp.status_code == 201
        body = resp.json()
        yield testclient, body["trip_id"], body["day_id"]
    set_db(Database(":memory:"))


def say(op_id: str, text: str, **ref) -> dict:
    return protocol.op_frame(Ops.MESSAGE_ADD, op_id, {"text": text, **ref})


def by_id(op_id: str, message_id: str) -> dict:
    return protocol.op_frame(Ops.MESSAGE_DELETE, op_id, {"id": message_id})


def back_again(op_id: str, message_id: str) -> dict:
    return protocol.op_frame(Ops.MESSAGE_RESTORE, op_id, {"id": message_id})


async def count(db: Database, sql: str, params: tuple = ()) -> int:
    row = await db.fetch_one(sql, params)
    return int(list(row.values())[0]) if row else 0


def hello(ws, client_id: str, name: str) -> dict:
    """Hello and consume the handshake, returning the welcome frame.

    `tests/frames.join` stops at the presence echo and throws the welcome away; the chat
    tests read `welcome.data.snapshot`, so this one keeps it.
    """
    ws.send_json(protocol.hello_frame(client_id, name, "#123456"))
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome"
    own = ws.receive_json()
    while own.get("op") == "timeline_updated":
        own = ws.receive_json()
    assert own["type"] == "presence_join", own
    return welcome


# -- 数据层 ---------------------------------------------------------------------------


async def test_blank_never_lands_and_overflow_is_clipped(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db, title="甲")

    for blank in ("", "   ", "\n\t "):
        assert await repo.message_add(db, trip_id, "c-1", MessageIn(text=blank)) is None, blank

    long = await repo.message_add(
        db, trip_id, "c-1", MessageIn(text="话" * (repo.MESSAGE_TEXT_MAX + 50))
    )
    assert long is not None and len(long.text) == repo.MESSAGE_TEXT_MAX

    trimmed = await repo.message_add(db, trip_id, "c-1", MessageIn(text="  换成早上去吧  "))
    assert trimmed is not None and trimmed.text == "换成早上去吧"


async def test_a_message_holds_at_most_one_anchor_and_drops_foreign_ones(db: Database) -> None:
    """挂不上就不挂，话照发：同伴刚删掉那张卡，不该让这句话因此发不出去。"""
    trip_id, day_id = await repo.create_trip(db, title="甲")
    other_trip, other_day = await repo.create_trip(db, title="别人的")
    assert other_trip != trip_id
    place = await repo.add_place(
        db, day_id, repo.PlaceCreate(name="宽窄巷子", lng=104.0, lat=30.6)
    )
    assert place is not None

    both = await repo.message_add(
        db,
        trip_id,
        "c-1",
        MessageIn(text="两点", ref_place_id=place.id, ref_day_id=day_id),
    )
    assert both is not None
    # 地点比「某一天」具体，两个都给就只留地点
    assert (both.ref_place_id, both.ref_day_id) == (place.id, "")

    cross = await repo.message_add(
        db, trip_id, "c-1", MessageIn(text="串台", ref_place_id=place.id, ref_day_id=other_day)
    )
    assert cross is not None and (cross.ref_place_id, cross.ref_day_id) == (place.id, "")

    ghost = await repo.message_add(
        db, trip_id, "c-1", MessageIn(text="这一站刚没了", ref_place_id="GHOST")
    )
    assert ghost is not None and ghost.ref_place_id == ""
    assert ghost.text == "这一站刚没了"  # 锚点丢了，内容一个字都不该少


async def test_snapshot_windows_to_the_recent_60_and_keeps_them_in_order(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db, title="甲")
    for i in range(65):
        assert await repo.message_add(db, trip_id, "c-1", MessageIn(text=f"第{i}句"))

    snap = await repo.get_snapshot(db, trip_id)
    assert snap is not None
    assert [m.text for m in snap.messages] == [f"第{i}句" for i in range(5, 65)]
    # 正序交给客户端：倒着渲染的话，最新一句会跑到最上面
    assert [m.pos for m in snap.messages] == sorted(m.pos for m in snap.messages)
    # 窗口是读取窗口，不是删除策略：库里那 65 条一条没少
    assert await count(db, "SELECT COUNT(*) FROM messages WHERE trip_id = ?", (trip_id,)) == 65


async def test_deleted_lines_leave_the_window_and_come_back_to_their_slot(db: Database) -> None:
    """撤销的全部实现就是「pos 没变」——放回末尾会让那句话接不上上文。"""
    trip_id, _ = await repo.create_trip(db, title="甲")
    a = await repo.message_add(db, trip_id, "c-1", MessageIn(text="这家要排队吗"))
    b = await repo.message_add(db, trip_id, "c-2", MessageIn(text="要，早点去"))
    c = await repo.message_add(db, trip_id, "c-1", MessageIn(text="换成早上去吧"))
    assert a and b and c

    status, row = await repo.message_delete(db, trip_id, b.id, "c-2")
    assert status == "ok" and row is not None and row.pos == b.pos
    snap = await repo.get_snapshot(db, trip_id)
    assert [m.id for m in snap.messages] == [a.id, c.id]

    status, row = await repo.message_restore(db, trip_id, b.id, "c-2")
    assert status == "ok" and row is not None and row.pos == b.pos
    snap = await repo.get_snapshot(db, trip_id)
    assert [m.id for m in snap.messages] == [a.id, b.id, c.id]

    # 一人删掉一大批也掏不空历史：窗口只数没删的
    for m in (a, c):
        await repo.message_delete(db, trip_id, m.id, "c-1")
    snap = await repo.get_snapshot(db, trip_id)
    assert [m.id for m in snap.messages] == [b.id]


async def test_only_the_author_may_delete_and_only_a_deleted_line_may_return(
    db: Database,
) -> None:
    mine, _ = await repo.create_trip(db, title="我的")
    yours, _ = await repo.create_trip(db, title="别人的")
    row = await repo.message_add(db, yours, "c-2", MessageIn(text="这句不是你的"))
    assert row is not None

    # 越权与不存在分开回：前者要提示用户，后者只需静默收掉自己那条
    assert await repo.message_delete(db, yours, row.id, "c-1") == ("not_owner", None)
    assert await repo.message_delete(db, mine, row.id, "c-2") == ("not_found", None)
    assert await repo.message_delete(db, yours, "GHOST", "c-2") == ("not_found", None)
    assert await repo.message_restore(db, yours, row.id, "c-2") == ("not_found", None)

    assert (await repo.message_delete(db, yours, row.id, "c-2"))[0] == "ok"
    assert await repo.message_delete(db, yours, row.id, "c-2") == ("not_found", None)
    assert await repo.message_restore(db, yours, row.id, "c-1") == ("not_owner", None)


async def test_deleting_a_trip_clears_the_chat_including_the_soft_deleted_rows(
    db: Database,
) -> None:
    """项目里第一次存「用户说了什么」：行程没了不能留一份没有主人的对话。

    今天还没有「删除行程」这个入口，所以这条测的是外键本身：级联写进 schema，将来那个入口
    一出现就自动是对的；漏了 `ON DELETE CASCADE` 则当场红。
    """
    trip_id, _ = await repo.create_trip(db, title="要没了的")
    keep, _ = await repo.create_trip(db, title="还在的")
    a = await repo.message_add(db, trip_id, "c-1", MessageIn(text="那家酒店叫啥"))
    b = await repo.message_add(db, trip_id, "c-1", MessageIn(text="叫青旅"))
    assert a and b
    await repo.message_delete(db, trip_id, b.id, "c-1")  # 软删的那行也是内容
    await repo.message_add(db, keep, "c-1", MessageIn(text="别把我带走"))

    await db.run(lambda conn: conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,)))

    assert await count(db, "SELECT COUNT(*) FROM messages") == 1
    assert await count(db, "SELECT COUNT(*) FROM messages WHERE trip_id = ?", (keep,)) == 1


# -- WebSocket 层 ----------------------------------------------------------------------


def test_both_windows_see_the_same_message(client) -> None:
    """这一条就是「双方发消息都可以看到」。广播若只回发起者，它必须红。"""
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        hello(ws1, "c-1", "小明")
        with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
            hello(ws2, "c-2", "小红")

            ws1.send_json(say("op-m1", "宽窄巷子要不要排队", ref_day_id=day_id))
            theirs = read_op(ws2, "message_added")
            assert theirs["type"] == "op" and theirs["op"] == "message_added"
            assert theirs["origin"] == "c-1"
            assert theirs["data"]["message"]["text"] == "宽窄巷子要不要排队"
            assert theirs["data"]["message"]["client_id"] == "c-1"
            # 小明也收到自己那句：回声是「落库了」的确认，两边看到的是同一个 seq
            mine = read_op(ws1, "message_added")
            assert mine["seq"] == theirs["seq"] and mine["op_id"] == "op-m1"

            ws2.send_json(say("op-m2", "要，早点去"))
            assert read_op(ws1, "message_added")["data"]["message"]["text"] == "要，早点去"
            assert read_op(ws2, "message_added")["origin"] == "c-2"


def test_welcome_and_resync_carry_what_was_spoken(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """刷新之后话还在，顺序还是说的那个顺序；seq 对不上时整份快照重来一次。

    「65 条只带最近 60 条」那条判据留在数据层——这里是同一份 SQL，用 socket 再测一遍只会
    逼着同一个 client 连发 65 句，撞上的其实是频控。
    """
    import app.ws.ops as ops_module

    clock = [100.0]
    monkeypatch.setattr(ops_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    testclient, trip_id, _ = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        hello(ws, "c-1", "小明")
        for i in range(3):
            clock[0] += 3.0
            ws.send_json(say(f"op-w{i}", f"第{i}句"))
            assert read_op(ws, "message_added")["type"] == "op"

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
        welcome = hello(ws2, "c-2", "小红")
        messages = welcome["data"]["snapshot"]["messages"]
        assert [m["text"] for m in messages] == ["第0句", "第1句", "第2句"]
        assert [m["pos"] for m in messages] == sorted(m["pos"] for m in messages)
        assert [m["client_id"] for m in messages] == ["c-1"] * 3

        ws2.send_json(protocol.resync_frame(0))
        back = ws2.receive_json()
        while back.get("type") != "welcome":  # 自己的 presence 广播可能先来
            back = ws2.receive_json()
        assert [m["text"] for m in back["data"]["snapshot"]["messages"]] == [
            "第0句",
            "第1句",
            "第2句",
        ]


def test_a_second_message_within_the_window_is_rejected_and_keeps_its_own_slot(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """被拒的那一次**不烧 2 秒窗口**：否则一直按着回车就永远出不了窗口。"""
    import app.ws.ops as ops_module

    testclient, trip_id, _ = client
    clock = [100.0]
    monkeypatch.setattr(ops_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        hello(ws, "c-1", "小明")
        ws.send_json(say("op-t0", "第一句"))
        assert read_op(ws, "message_added")["op"] == "message_added"

        clock[0] += 1.0
        ws.send_json(say("op-t1", "太快了"))
        rejected = ws.receive_json()
        assert rejected["type"] == "op_reject" and rejected["reason"] == "message_too_fast"
        assert rejected["op_id"] == "op-t1"
        assert rejected["data"]["retry_after_ms"] == 2000

        # 距第一句满 2 秒就该放行——窗口没被那次拒绝挪走
        clock[0] += 1.0
        ws.send_json(say("op-t2", "现在可以了"))
        assert read_op(ws, "message_added")["data"]["message"]["text"] == "现在可以了"


def test_blank_and_delete_paths_reject_without_killing_the_socket(client) -> None:
    testclient, trip_id, _ = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        hello(ws1, "c-1", "小明")
        with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
            hello(ws2, "c-2", "小红")

            ws1.send_json(say("op-b", "   "))
            assert read_op(ws1, "never")["reason"] == "bad_message"

            ws1.send_json(say("op-ok", "这句是真的"))
            added = read_op(ws1, "message_added")
            message_id = added["data"]["message"]["id"]

            # 小红删不掉小明那句，收到的理由是「不是你的」而不是「没有了」
            ws2.send_json(by_id("op-d1", message_id))
            assert read_op(ws2, "never")["reason"] == "message_not_owner"

            # 小明自己删：两边都收到，小红的屏幕上那句跟着消失
            ws1.send_json(by_id("op-d2", message_id))
            assert read_op(ws2, "message_deleted")["data"]["id"] == message_id
            assert read_op(ws1, "message_deleted")["op"] == "message_deleted"

            ws1.send_json(by_id("op-d3", message_id))
            assert read_op(ws1, "never")["reason"] == "message_not_found"
            ws1.send_json(back_again("op-r1", "GHOST"))
            assert read_op(ws1, "never")["reason"] == "message_not_found"

            # 原位放回：广播带整行，pos 与删之前一致，客户端才插得回那一格
            ws1.send_json(back_again("op-r2", message_id))
            restored = read_op(ws1, "message_restored")
            assert restored["data"]["message"]["pos"] == added["data"]["message"]["pos"]
            assert read_op(ws2, "message_restored")["data"]["message"]["id"] == message_id

            ws1.send_json(protocol.ping_frame())
            assert ws1.receive_json()["type"] == "pong"


def test_a_replayed_op_does_not_say_it_twice(client) -> None:
    """弱网重连会重发同一笔 op；一句话说两遍是社交事故。"""
    testclient, trip_id, _ = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        hello(ws, "c-1", "小明")
        ws.send_json(say("op-dup", "就一句"))
        assert read_op(ws, "message_added")["op"] == "message_added"

        ws.send_json(say("op-dup", "就一句"))
        ws.send_json(protocol.ping_frame())
        while (frame := ws.receive_json())["type"] != "pong":
            assert frame.get("op") != "message_added", frame
        assert frame["type"] == "pong"
