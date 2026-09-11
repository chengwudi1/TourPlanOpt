"""M22 出行清单、费用账本与行程状态/预算。

三层各测各的：数据层管「脏输入怎么降级、批次去重、排列守卫」；WebSocket 层管「批量一条
op、回广播落地、拒绝时附上权威顺序」；REST 层管首页那三件事——改状态、设预算、取关——
以及首页卡片读到的聚合值。

金额一律整数分：这里断言的也都是分，前端换算不参与。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import repositories as repo
from app.db.database import Database, set_db
from app.models import protocol
from app.models.domain import ExpenseCreate
from app.models.protocol import Ops
from tests.frames import join, read_op


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "m22-test.db")
    await database.init()
    return database


@pytest.fixture()
def client(tmp_path) -> Iterator[tuple[TestClient, str]]:
    """App on a temp database with one fresh trip. Yields (client, trip_id)."""
    set_db(Database(tmp_path / "m22-http.db"))
    from app.main import app

    with TestClient(app) as testclient:
        resp = testclient.post("/api/trips", json={"title": "南京两日", "city": "南京"})
        assert resp.status_code == 201
        yield testclient, resp.json()["trip_id"]
    set_db(Database(":memory:"))


# -- 数据层：清单 -----------------------------------------------------------------------


async def test_checklist_add_dedupes_inside_the_batch_and_against_the_trip(
    db: Database,
) -> None:
    """「一键补全」按第二次：一条都不该新增，但那不是错误，不能回一个 rejection。"""
    trip_id, _ = await repo.create_trip(db, title="甲")

    first = await repo.checklist_add(db, trip_id, ["充电宝", " 充电宝 ", "身份证", "", "  "])
    assert [i.text for i in first] == ["充电宝", "身份证"]  # 批内去重 + 丢空，保持来序

    again = await repo.checklist_add(db, trip_id, ["充电宝", "雨伞"])
    assert [i.text for i in again] == ["雨伞"]
    assert await repo.checklist_add(db, trip_id, ["充电宝"]) == []

    ids = await repo.checklist_ids(db, trip_id)
    assert len(ids) == 3
    assert [i.sort_index for i in await _all_checklist(db, trip_id)] == [0, 1, 2]


async def test_checklist_update_rejects_blank_text_and_unknown_fields(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db, title="甲")
    item = (await repo.checklist_add(db, trip_id, ["身份证"]))[0]

    done = await repo.update_checklist(db, item.id, {"done": True})
    assert done is not None and done.done is True and done.rev == item.rev + 1

    # 清空文本是删除的意思，不该由改名通道悄悄做掉。
    assert await repo.update_checklist(db, item.id, {"text": "   "}) is None
    # 认不出的字段拒绝整份 patch：默默丢掉会让客户端以为保存成功了。
    assert await repo.update_checklist(db, item.id, {"note": "x"}) is None
    assert await repo.update_checklist(db, "GHOST", {"done": True}) is None


async def test_checklist_delete_is_trip_scoped(db: Database) -> None:
    mine, _ = await repo.create_trip(db, title="我的")
    yours, _ = await repo.create_trip(db, title="别人的")
    item = (await repo.checklist_add(db, yours, ["代拍"]))[0]

    assert await repo.delete_checklist(db, mine, item.id) is False
    assert await repo.delete_checklist(db, yours, item.id) is True
    assert await repo.delete_checklist(db, yours, item.id) is False


async def test_checklist_reorder_needs_the_full_current_permutation(db: Database) -> None:
    """守卫与 day_reorder 同一条：数组不是当前这套 id 就整个拒绝，并附上权威顺序。"""
    trip_id, _ = await repo.create_trip(db, title="甲")
    items = await repo.checklist_add(db, trip_id, ["A", "B", "C"])
    a, b, c = (i.id for i in items)

    stale = await repo.reorder_checklist(db, trip_id, [a, b])
    assert stale.ok is False
    assert stale.place_ids == [a, b, c]  # 原样回来，一行都没动

    dup = await repo.reorder_checklist(db, trip_id, [c, c, c])
    assert dup.ok is False

    ok = await repo.reorder_checklist(db, trip_id, [c, a, b])
    assert ok.ok is True
    assert await repo.checklist_ids(db, trip_id) == [c, a, b]


# -- 数据层：账本 -----------------------------------------------------------------------


async def test_expense_add_defaults_to_the_payer_and_repairs_junk(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db, title="甲")

    solo = await repo.expense_add(
        db, trip_id, ExpenseCreate(title="打车", amount_cents=4200, category="出租车")
    )
    assert solo.category == "other"  # 认不出的分类不报错，归到 other
    assert solo.split_ids == []  # 既没付款人也没分摊名单：不拿空串凑一个「无名氏」

    mine = await repo.expense_add(
        db,
        trip_id,
        ExpenseCreate(
            title="门票",
            amount_cents=18000,
            category="TICKET",
            paid_by="c-1",
            split_ids=["c-2", "c-1", "c-2", ""],
        ),
    )
    assert mine.category == "ticket"  # 大小写归一
    assert mine.split_ids == ["c-2", "c-1"]  # 去重保序，空串丢掉


async def test_expense_update_validates_the_amount(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db, title="甲")
    expense = await repo.expense_add(
        db, trip_id, ExpenseCreate(title="午饭", amount_cents=8800, paid_by="c-1")
    )

    for amount in (0, -100, "abc", None):
        assert await repo.update_expense(db, expense.id, {"amount_cents": amount}) is None
    assert await repo.update_expense(db, expense.id, {"title": "  "}) is None

    renamed = await repo.update_expense(db, expense.id, {"title": " 晚饭 ", "category": "food"})
    assert renamed is not None
    assert (renamed.title, renamed.category, renamed.amount_cents) == ("晚饭", "food", 8800)
    assert renamed.rev == expense.rev + 1


# -- WebSocket：批量一条 op、回广播、拒绝带权威 ------------------------------------------


def test_checklist_batch_reaches_both_windows_once(client: tuple[TestClient, str]) -> None:
    testclient, trip_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join(ws1, "c-1", "小明")
        with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws2:
            join(ws2, "c-2", "小红")

            ws1.send_json(
                protocol.op_frame(
                    Ops.CHECKLIST_ADD, "o-1", {"texts": ["充电宝", "身份证"], "added_by": "小明"}
                )
            )
            for ws in (ws1, ws2):
                frame = read_op(ws, "checklist_added")
                assert frame["type"] == "op"
                assert [i["text"] for i in frame["data"]["items"]] == ["充电宝", "身份证"]
                assert len(frame["data"]["item_ids"]) == 2
                assert frame["seq"]  # 整批只吃掉一个 seq

            # 别人那一端的临时行不存在，权威 items 直接落地；再补一条重复的不会新增。
            ws1.send_json(protocol.op_frame(Ops.CHECKLIST_ADD, "o-2", {"texts": ["充电宝"]}))
            frame = read_op(ws1, "checklist_added")
            assert frame["data"]["items"] == []
            assert len(frame["data"]["item_ids"]) == 2


def test_checklist_reorder_rejection_carries_the_authority(
    client: tuple[TestClient, str],
) -> None:
    testclient, trip_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws, "c-1", "小明")
        ws.send_json(protocol.op_frame(Ops.CHECKLIST_ADD, "a", {"texts": ["A", "B", "C"]}))
        ids = read_op(ws, "checklist_added")["data"]["item_ids"]

        ws.send_json(protocol.op_frame(Ops.CHECKLIST_REORDER, "r", {"item_ids": ids[::-1]}))
        assert read_op(ws, "checklist_reordered")["data"]["item_ids"] == ids[::-1]

        # 拿一份过期顺序（只有一条）再拖：拒绝，且附上服务端当前的完整顺序。
        ws.send_json(protocol.op_frame(Ops.CHECKLIST_REORDER, "r2", {"item_ids": [ids[0]]}))
        reject = read_op(ws, "op_reject")
        assert reject["reason"] == "checklist_stale"
        assert reject["data"]["item_ids"] == ids[::-1]


def test_expense_ops_and_rejections(client: tuple[TestClient, str]) -> None:
    testclient, trip_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws, "c-1", "小明")
        ws.send_json(
            protocol.op_frame(
                Ops.EXPENSE_ADD,
                "e-1",
                {
                    "title": "高铁",
                    "amount_cents": 29700,
                    "category": "transport",
                    "paid_by": "c-1",
                    "paid_by_name": "小明",
                    "split_ids": ["c-1", "c-2"],
                },
            )
        )
        added = read_op(ws, "expense_added")["data"]["expense"]
        assert added["amount_cents"] == 29700
        assert added["split_ids"] == ["c-1", "c-2"]

        ws.send_json(
            protocol.op_frame(Ops.EXPENSE_UPDATE, "e-2", {"id": added["id"], "patch": {
                "amount_cents": 100
            }})
        )
        assert read_op(ws, "expense_updated")["data"]["expense"]["amount_cents"] == 100

        ws.send_json(
            protocol.op_frame(Ops.EXPENSE_ADD, "e-3", {"title": "零元", "amount_cents": 0})
        )
        assert read_op(ws, "op_reject")["reason"] == "bad_expense"

        ws.send_json(protocol.op_frame(Ops.EXPENSE_DELETE, "e-4", {"id": "GHOST"}))
        assert read_op(ws, "op_reject")["reason"] == "expense_not_found"

        snapshot = testclient.get(f"/api/trips/{trip_id}").json()
        assert [e["id"] for e in snapshot["expenses"]] == [added["id"]]
        assert snapshot["checklist"] == []


# -- REST：首页要动的三件事 ---------------------------------------------------------------


def test_trip_patch_status_and_budget(client: tuple[TestClient, str]) -> None:
    testclient, trip_id = client

    patched = testclient.patch(
        f"/api/trips/{trip_id}", json={"status": "finished", "budget_cents": 300000}
    )
    assert patched.status_code == 200
    body = patched.json()
    assert (body["status"], body["budget_cents"]) == ("finished", 300000)

    # 快照读回来也是这份值：首页写的，行程页下次读得到。
    assert testclient.get(f"/api/trips/{trip_id}").json()["trip"]["budget_cents"] == 300000

    clamped = testclient.patch(f"/api/trips/{trip_id}", json={"budget_cents": -5}).json()
    assert clamped["budget_cents"] == 0  # 负预算没有意义，夹到 0（= 还没设）


def test_trip_patch_rejects_an_unknown_status_rather_than_storing_it(
    client: tuple[TestClient, str],
) -> None:
    testclient, trip_id = client

    assert testclient.patch(f"/api/trips/{trip_id}", json={}).status_code == 400
    assert testclient.patch(f"/api/trips/{trip_id}", json={"status": "去他喵的"}).status_code == 400
    # TripPatch 不认的字段被 Pydantic 丢掉，剩下空 patch：宁可 400，也不默默存一半。
    assert testclient.patch(f"/api/trips/{trip_id}", json={"seq": 99}).status_code == 400
    assert testclient.patch("/api/trips/GHOST", json={"status": "archived"}).status_code == 404
    assert testclient.get(f"/api/trips/{trip_id}").json()["trip"]["status"] == "planning"


def test_trip_patch_shouts_into_an_open_room(client: tuple[TestClient, str]) -> None:
    """首页没有 socket，写完必须朝房间广播一条 trip_updated。

    少了这一条，同时开着的行程页会停在旧状态，而且它下次自己发 trip_update 时按 LWW
    会把首页刚写下的值盖回去——用户看到的是「我明明归档了」。
    """
    testclient, trip_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws, "c-1", "小明")
        archived = testclient.patch(f"/api/trips/{trip_id}", json={"status": "archived"})
        assert archived.status_code == 200

        frame = read_op(ws, "trip_updated")
        assert frame["data"]["trip"]["status"] == "archived"
        assert frame["data"]["trip"]["id"] == trip_id


def test_summary_card_carries_readiness_numbers(client: tuple[TestClient, str]) -> None:
    testclient, trip_id = client
    testclient.patch(f"/api/trips/{trip_id}", json={"budget_cents": 100000})

    def card() -> dict:
        return testclient.get("/api/trips/summary", params={"ids": trip_id}).json()["trips"][0]

    assert (card()["checklist_total"], card()["checklist_done"], card()["spent_cents"]) == (0, 0, 0)

    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws:
        join(ws, "c-1", "小明")
        # 没填出发日的行程：日期区间就是 null，首页据此收起倒计时而不是显示「NaN 天后」。
        assert (card()["start_date"], card()["end_date"]) == (None, None)
        day_id = testclient.get(f"/api/trips/{trip_id}").json()["days"][0]["id"]
        ws.send_json(
            protocol.op_frame(
                Ops.DAY_UPDATE, "d", {"day_id": day_id, "patch": {"date": "2026-10-01"}}
            )
        )
        read_op(ws, "day_updated")
        ws.send_json(protocol.op_frame(Ops.CHECKLIST_ADD, "a", {"texts": ["A", "B"]}))
        ids = read_op(ws, "checklist_added")["data"]["item_ids"]
        ws.send_json(
            protocol.op_frame(Ops.CHECKLIST_UPDATE, "u", {"id": ids[0], "patch": {"done": True}})
        )
        read_op(ws, "checklist_updated")
        ws.send_json(
            protocol.op_frame(
                Ops.EXPENSE_ADD, "e", {"title": "高铁", "amount_cents": 29700, "paid_by": "c-1"}
            )
        )
        read_op(ws, "expense_added")

    fresh = card()
    assert (fresh["checklist_total"], fresh["checklist_done"]) == (2, 1)
    assert fresh["spent_cents"] == 29700
    assert fresh["budget_cents"] == 100000
    assert fresh["status"] == "planning"
    # M18c 起 days.date 真有人填：一次建 N 天会带上日期，卡片才敢显示倒计时。
    assert (fresh["start_date"], fresh["end_date"]) == ("2026-10-01", "2026-10-01")


def test_unfollow_drops_the_visit_row_but_never_the_trip_itself(
    client: tuple[TestClient, str],
) -> None:
    testclient, trip_id = client

    assert testclient.delete(f"/api/auth/trips/{trip_id}").status_code == 401

    testclient.post("/api/auth/register", json={"name": "小明", "password": "secret1"})
    assert testclient.get(f"/api/trips/{trip_id}").status_code == 200  # 打开即足迹
    assert [t["id"] for t in testclient.get("/api/auth/trips").json()["trips"]] == [trip_id]

    gone = testclient.delete(f"/api/auth/trips/{trip_id}")
    assert gone.status_code == 200
    assert gone.json() == {"trip_id": trip_id, "removed": True}
    assert testclient.get("/api/auth/trips").json()["trips"] == []
    # 连着取关两次：第二次没得删，也不报错，前端按 removed=false 收起菜单项就行。
    assert testclient.delete(f"/api/auth/trips/{trip_id}").json()["removed"] is False
    # 取关不是删除：行程还在，链接还能开——而重新打开就是重新关注，足迹会再写一条。
    assert testclient.get(f"/api/trips/{trip_id}").status_code == 200
    assert [t["id"] for t in testclient.get("/api/auth/trips").json()["trips"]] == [trip_id]


async def _all_checklist(db: Database, trip_id: str) -> list:
    rows = await db.fetch_all(
        "SELECT * FROM checklist_items WHERE trip_id = ? ORDER BY sort_index", (trip_id,)
    )
    return [repo.ChecklistItemOut.model_validate(dict(r)) for r in rows]
