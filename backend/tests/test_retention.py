"""未登录行程 7 天清理的判据。政策与字段清单在 app/retention.py 头部。

判据全是「最后一列时间戳有多老」，测试不能等 7 天，所以用例自己把库里的时间列改老。
这里最要紧的一类是「活跃救命」的参数化：任一时间列漏进 SQL，真跑时就有一类行程被误删
（比如只挂了清单没加过地点的），而不是报错——误删不响，所以每一列都要指名道姓地试一遍。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import retention
from app.auth import accounts
from app.config import settings
from app.db import repositories as repo
from app.db.database import Database
from app.models.domain import ExpenseCreate, MessageIn, PlaceCreate


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


@pytest.fixture
def uploads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "uploads"
    monkeypatch.setattr(settings, "uploads_dir", root)
    return root


def ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


async def seed_guest_trip(db: Database, *, title: str = "游客的行程") -> str:
    """一份内容齐全的未登录行程：每个带时间的子表都留下至少一行。"""
    trip_id, day_id = await repo.create_trip(db, title=title)
    await repo.add_place(db, day_id, PlaceCreate(name="外滩", lng=121.49, lat=31.24))
    await repo.upsert_participant(db, trip_id, "c1", "游客")
    await repo.stash_add(db, trip_id, name="豫园", lng=121.49, lat=31.23)
    await repo.checklist_add(db, trip_id, ["充电宝"])
    await repo.expense_add(db, trip_id, ExpenseCreate(title="午餐", amount_cents=3200))
    await repo.message_add(db, trip_id, "c1", MessageIn(text="几点出发？"))
    return trip_id


_CHILD_TABLES = ("days", "places", "participants", "stash", "checklist_items", "expenses", "messages")


async def age_trip(db: Database, trip_id: str, days: float) -> None:
    """把所有带时间的行一律改老。只改一部分列的话，其余列会把行程「救」回去，测不出过期。"""
    when = ago(days)
    await db.execute("UPDATE trips SET created_at = ? WHERE id = ?", (when, trip_id))
    await db.execute("UPDATE participants SET last_seen = ? WHERE trip_id = ?", (when, trip_id))
    await db.execute("UPDATE places SET updated_at = ? WHERE trip_id = ?", (when, trip_id))
    await db.execute("UPDATE stash SET created_at = ? WHERE trip_id = ?", (when, trip_id))
    await db.execute("UPDATE checklist_items SET updated_at = ? WHERE trip_id = ?", (when, trip_id))
    await db.execute("UPDATE expenses SET updated_at = ? WHERE trip_id = ?", (when, trip_id))
    await db.execute("UPDATE messages SET created_at = ? WHERE trip_id = ?", (when, trip_id))


async def count(db: Database, table: str, trip_id: str) -> int:
    row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE trip_id = ?", (trip_id,))
    assert row is not None
    return int(row["n"])


async def trip_exists(db: Database, trip_id: str) -> bool:
    return await db.fetch_one("SELECT 1 AS x FROM trips WHERE id = ?", (trip_id,)) is not None


async def test_stale_guest_trip_is_purged_with_all_content(db: Database, uploads_dir: Path) -> None:
    trip_id = await seed_guest_trip(db)
    await age_trip(db, trip_id, 8)
    cover = uploads_dir / trip_id
    cover.mkdir(parents=True)
    (cover / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xe0junk")

    removed = await retention.purge_stale_guest_trips(db)

    assert removed == [trip_id]
    assert not await trip_exists(db, trip_id)
    for table in _CHILD_TABLES:
        assert await count(db, table, trip_id) == 0, f"{table} 没跟着行程一起走"
    # 库里的行清了，磁盘上那份封面也不该留成孤儿
    assert not cover.exists()


async def test_fresh_trip_survives(db: Database) -> None:
    trip_id = await seed_guest_trip(db)
    assert await retention.purge_stale_guest_trips(db) == []
    assert await trip_exists(db, trip_id)


@pytest.mark.parametrize(
    "table, column",
    [
        ("participants", "last_seen"),
        ("places", "updated_at"),
        ("stash", "created_at"),
        ("checklist_items", "updated_at"),
        ("expenses", "updated_at"),
        ("messages", "created_at"),
    ],
)
async def test_any_recent_activity_rescues_an_old_trip(
    db: Database, table: str, column: str
) -> None:
    """一列刚被写过，整份行程就不该进清理名单——「7 天无人打开/改动」的「或」在这。"""
    trip_id = await seed_guest_trip(db)
    await age_trip(db, trip_id, 30)
    await db.execute(f"UPDATE {table} SET {column} = ? WHERE trip_id = ?", (ago(0.04), trip_id))

    assert await retention.purge_stale_guest_trips(db) == []
    assert await trip_exists(db, trip_id)
    assert await count(db, "places", trip_id) == 1


async def test_claimed_trip_survives_even_when_stale(db: Database) -> None:
    """有登录用户打开过 = 它躺在那个人的「我的行程」里，删它就不是省空间了。"""
    trip_id = await seed_guest_trip(db)
    user = await accounts.create_user(db, "阿明", "pw123456")
    await accounts.record_visit(db, trip_id, user["id"])
    await age_trip(db, trip_id, 30)

    assert await retention.purge_stale_guest_trips(db) == []
    assert await trip_exists(db, trip_id)
    assert await count(db, "messages", trip_id) == 1


async def test_logged_in_creation_survives_even_when_stale(db: Database) -> None:
    trip_id, _ = await repo.create_trip(db, title="登录建的", created_by="u_owner")
    await age_trip(db, trip_id, 30)
    assert await retention.purge_stale_guest_trips(db) == []
    assert await trip_exists(db, trip_id)


async def test_idle_just_under_retention_survives(db: Database) -> None:
    trip_id = await seed_guest_trip(db)
    await age_trip(db, trip_id, 6.5)
    assert await retention.purge_stale_guest_trips(db) == []
    assert await trip_exists(db, trip_id)


async def test_sweep_only_takes_the_stale_ones(db: Database) -> None:
    """一趟扫描里三种行程混着：过期的未登录、没过期的未登录、过期的已认领。"""
    stale = await seed_guest_trip(db, title="过期")
    await age_trip(db, stale, 30)
    fresh = await seed_guest_trip(db, title="新鲜")
    claimed = await seed_guest_trip(db, title="被认领")
    user = await accounts.create_user(db, "小李", "pw123456")
    await accounts.record_visit(db, claimed, user["id"])
    await age_trip(db, claimed, 30)

    assert await retention.purge_stale_guest_trips(db) == [stale]
    assert not await trip_exists(db, stale)
    assert await trip_exists(db, fresh)
    assert await trip_exists(db, claimed)


async def test_days_argument_overrides_the_setting(db: Database) -> None:
    trip_id = await seed_guest_trip(db)
    await age_trip(db, trip_id, 3)
    assert await retention.purge_stale_guest_trips(db, days=7) == []
    assert await retention.purge_stale_guest_trips(db, days=2) == [trip_id]
