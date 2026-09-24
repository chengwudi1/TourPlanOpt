"""Repository tests.

The renumbering cases matter most: `places` carries UNIQUE(day_id, sort_index), and
SQLite validates that index row-by-row *during* an UPDATE. The naive
`SET sort_index = sort_index + 1` therefore dies on a transient collision. These tests
insert into the middle of a list, which is exactly the shape that triggers it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db import repositories as repo
from app.db.database import Database
from app.models.domain import PlaceCreate
from app.util.ids import new_id


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


def make_place(
    name: str, after: str | None = None, lng: float = 121.47, position: int | None = None
) -> PlaceCreate:
    return PlaceCreate(
        name=name,
        lng=lng,
        lat=31.23,
        after_place_id=after,
        position=position,
        duration_min=45,
    )


async def new_day(db: Database, trip_id: str, day_index: int) -> str:
    """The day_add op is M4's, so tests create extra days directly."""
    day_id = new_id()
    await db.execute(
        "INSERT INTO days (id, trip_id, day_index, title, rev) VALUES (?, ?, ?, ?, 1)",
        (day_id, trip_id, day_index, f"第 {day_index + 1} 天"),
    )
    return day_id


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_place_and_stash_reject_non_finite_coords(bad: float) -> None:
    """NaN/inf 坐标一旦落库，WS 广播的 json.dumps 会写出裸 Infinity/NaN，浏览器 JSON.parse
    抛错、该行程对所有协作者永久打不开。校验只可能出现在输入模型上——旧代码放行，此测试即红。"""
    from app.models.domain import StashCreate

    with pytest.raises(ValidationError):
        PlaceCreate(name="x", lng=bad, lat=31.23)
    with pytest.raises(ValidationError):
        PlaceCreate(name="x", lng=121.47, lat=bad)
    with pytest.raises(ValidationError):
        StashCreate(name="x", lng=bad, lat=31.23)


def test_place_rejects_out_of_range_but_keeps_valid_coords() -> None:
    with pytest.raises(ValidationError):
        PlaceCreate(name="x", lng=181.0, lat=31.23)
    with pytest.raises(ValidationError):
        PlaceCreate(name="x", lng=121.47, lat=-91.0)
    # 合法坐标一律照常通过，别把正常加地点挡在门外
    assert PlaceCreate(name="x", lng=121.47, lat=31.23).lng == 121.47


async def test_create_trip_makes_a_trip_and_its_first_day(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db, title="上海三日游", city="上海")
    snapshot = await repo.get_snapshot(db, trip_id)
    assert snapshot is not None
    assert snapshot.trip.title == "上海三日游"
    assert snapshot.trip.cost_model == "haversine"
    assert snapshot.trip.seq == 0
    assert [d.id for d in snapshot.days] == [day_id]
    assert snapshot.days[0].day_index == 0


async def test_get_snapshot_of_unknown_trip_is_none(db: Database) -> None:
    assert await repo.get_snapshot(db, "NOPE1234") is None


async def test_add_place_appends_with_dense_indices(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db)
    for name in ("外滩", "豫园", "南京路"):
        await repo.add_place(db, day_id, make_place(name))

    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["外滩", "豫园", "南京路"]
    assert [p.sort_index for p in places] == [0, 1, 2]
    assert all(p.trip_id == trip_id for p in places)


async def test_add_place_in_the_middle_keeps_indices_dense(db: Database) -> None:
    """The transient-UNIQUE-collision case: everything at and after the insert point has
    to move up by one."""
    trip_id, day_id = await repo.create_trip(db)
    first = await repo.add_place(db, day_id, make_place("外滩"))
    await repo.add_place(db, day_id, make_place("南京路"))
    middle = await repo.add_place(db, day_id, make_place("豫园", after=first.id))

    assert middle is not None
    assert middle.sort_index == 1
    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["外滩", "豫园", "南京路"]
    assert [p.sort_index for p in places] == [0, 1, 2]


async def test_add_place_after_unknown_id_appends(db: Database) -> None:
    _, day_id = await repo.create_trip(db)
    await repo.add_place(db, day_id, make_place("外滩"))
    placed = await repo.add_place(db, day_id, make_place("豫园", after="GHOST000"))
    assert placed.sort_index == 1


async def test_add_place_at_position_zero_lands_at_the_head(db: Database) -> None:
    """撤销删除要能把行放回原位。一天里的第一个地点没有 `after_place_id` 可指，
    只能按绝对下标插——旧代码没有这个入口，只能追加到末尾。"""
    trip_id, day_id = await repo.create_trip(db)
    await repo.add_place(db, day_id, make_place("外滩"))
    await repo.add_place(db, day_id, make_place("豫园"))
    head = await repo.add_place(db, day_id, make_place("南京路", position=0))

    assert head is not None and head.sort_index == 0
    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["南京路", "外滩", "豫园"]
    assert [p.sort_index for p in places] == [0, 1, 2]


async def test_add_place_position_is_clamped_into_range(db: Database) -> None:
    """位置是个建议，不是承诺：越界就夹到端点，绝不因为「下标没了」而拒掉整笔添加。
    5 秒撤销窗口里别人可能已经删掉了后面的行。"""
    trip_id, day_id = await repo.create_trip(db)
    await repo.add_place(db, day_id, make_place("外滩"))
    await repo.add_place(db, day_id, make_place("豫园"))
    far = await repo.add_place(db, day_id, make_place("南京路", position=99))
    neg = await repo.add_place(db, day_id, make_place("陆家嘴", position=-5))

    assert far.sort_index == 2  # 夹到末尾
    assert neg.sort_index == 0  # 夹到开头
    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["陆家嘴", "外滩", "豫园", "南京路"]


async def test_add_place_to_unknown_day_returns_none(db: Database) -> None:
    assert await repo.add_place(db, "GHOST000", make_place("外滩")) is None


async def test_delete_place_renumbers_the_tail(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db)
    ids = [(await repo.add_place(db, day_id, make_place(n))).id for n in ("A", "B", "C", "D")]

    day_id_out, remaining = await repo.delete_place(db, ids[1])
    assert day_id_out == day_id
    assert remaining == [ids[0], ids[2], ids[3]]

    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["A", "C", "D"]
    assert [p.sort_index for p in places] == [0, 1, 2]


async def test_delete_unknown_place_returns_none(db: Database) -> None:
    assert await repo.delete_place(db, "GHOST000") is None


async def test_reorder_accepts_a_permutation(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db)
    ids = [(await repo.add_place(db, day_id, make_place(n))).id for n in ("A", "B", "C")]

    result = await repo.reorder_day(db, day_id, [ids[2], ids[0], ids[1]])
    assert result.ok is True
    assert result.place_ids == [ids[2], ids[0], ids[1]]

    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["C", "A", "B"]
    assert [p.sort_index for p in places] == [0, 1, 2]


async def test_reorder_rejects_a_non_permutation_and_returns_the_truth(db: Database) -> None:
    """A stale or truncated array must be refused, and the authoritative order handed
    back -- this is what keeps two concurrent drags from diverging permanently."""
    trip_id, day_id = await repo.create_trip(db)
    ids = [(await repo.add_place(db, day_id, make_place(n))).id for n in ("A", "B", "C")]

    result = await repo.reorder_day(db, day_id, [ids[1], ids[0]])  # C is missing
    assert result.ok is False
    assert result.place_ids == ids

    places = (await repo.get_snapshot(db, trip_id)).places
    assert [p.name for p in places] == ["A", "B", "C"]


async def test_reorder_rejects_duplicate_ids(db: Database) -> None:
    _, day_id = await repo.create_trip(db)
    ids = [(await repo.add_place(db, day_id, make_place(n))).id for n in ("A", "B")]

    result = await repo.reorder_day(db, day_id, [ids[0], ids[0]])
    assert result.ok is False
    assert result.place_ids == ids


async def test_snapshot_orders_places_by_day_then_sort_index(db: Database) -> None:
    trip_id, day0 = await repo.create_trip(db, title="两日")
    day1_id = await new_day(db, trip_id, 1)

    await repo.add_place(db, day0, make_place("第一天 A"))
    await repo.add_place(db, day1_id, make_place("第二天 B"))
    await repo.add_place(db, day0, make_place("第一天 C"))

    snapshot = await repo.get_snapshot(db, trip_id)
    assert [p.name for p in snapshot.places] == ["第一天 A", "第一天 C", "第二天 B"]


async def test_next_seq_is_monotonic(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db)
    assert [await repo.next_seq(db, trip_id) for _ in range(3)] == [1, 2, 3]


async def test_upsert_participant_is_idempotent(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db)
    first = await repo.upsert_participant(db, trip_id, "c1", "小明", "#E8564A")
    second = await repo.upsert_participant(db, trip_id, "c1", "小明改名", "#E8564A")

    snapshot = await repo.get_snapshot(db, trip_id)
    assert len(snapshot.participants) == 1
    assert snapshot.participants[0].name == "小明改名"
    # A reconnect must not look like a brand-new participant.
    assert second.joined_at == first.joined_at


async def test_upsert_participant_keeps_existing_color_when_blank(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db)
    await repo.upsert_participant(db, trip_id, "c1", "小明", "#E8564A")
    again = await repo.upsert_participant(db, trip_id, "c1", "小明", "")
    assert again.color == "#E8564A"


# -- M13 地点照片：photo_url 必须完整往返 --------------------------------------------
#
# The stored photo_url is the only copy the client has (it is never re-fetched), so a
# column dropped on write would silently render every card photo-less without failing
# anything else.


async def test_add_place_persists_photo_url(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db)
    await repo.add_place(
        db,
        day_id,
        PlaceCreate(
            name="外滩",
            lng=121.49,
            lat=31.24,
            amap_poi_id="B001540WRR",
            photo_url="https://a.amap.com/外滩.jpg",
        ),
    )

    snapshot = await repo.get_snapshot(db, trip_id)
    assert snapshot.places[0].photo_url == "https://a.amap.com/外滩.jpg"


async def test_add_place_defaults_photo_url_to_empty(db: Database) -> None:
    """No POI id (map-picked place) means no photo, and empty must not become NULL."""
    trip_id, day_id = await repo.create_trip(db)
    await repo.add_place(db, day_id, make_place("街角咖啡店"))

    snapshot = await repo.get_snapshot(db, trip_id)
    assert snapshot.places[0].photo_url == ""


async def test_stash_add_persists_photo_url(db: Database) -> None:
    """想去清单的图片要能跟着搬到正式地点，所以两边都得存下来。"""
    trip_id, _ = await repo.create_trip(db)
    await repo.stash_add(
        db,
        trip_id,
        name="城隍庙",
        lng=121.49,
        lat=31.23,
        amap_poi_id="B001540WRR",
        photo_url="https://a.amap.com/城隍庙.jpg",
    )

    snapshot = await repo.get_snapshot(db, trip_id)
    assert snapshot.stash[0].photo_url == "https://a.amap.com/城隍庙.jpg"
