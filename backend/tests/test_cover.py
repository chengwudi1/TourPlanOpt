"""M36/M37 行程封面：`trips.cover_url` 一列、一条上传端点、一个公开可读的静态目录。

这一批用例守的是「后端第一次真往磁盘上写用户内容」带来的四件事：

1. **类型判定只看字节。** `content_type` 由浏览器自己填，改一个字符串就能把任意文件标成
   `image/jpeg`，所以下面每一条都把假类型当真类型送进来。
2. **`trip_id` 会拼进文件路径。** 它今天由生成侧保证是 8 位 base32，但那是别处的事实，
   所以路径穿越必须在写文件之前挡住。
3. **换图要删旧图**，否则传十次留十份；而**删的只能是自己写过的形状**。
4. **摘要只带用户亲手那张**（M37：默认档是前端打包的六张，后端一份都不猜），而空串要能
   真的回到默认档——那就是「恢复默认封面」在接口层的含义。

`client` 那个 fixture 的顺序是有意为之：`/uploads` 的挂载点在 `create_app()` 里读
`settings.uploads_dir`，所以 monkeypatch 必须发生在建 app **之前**，否则测试读的是真
`backend/data/uploads`。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.db import repositories as repo
from app.db.database import Database, set_db
from app.uploads import CoverReject, drop_cover_file, save_cover, sniff_image
from tests.test_repositories import make_place

# 三种被接受的类型各一个真头（补齐到识别所需的长度即可，内容无关）。
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 24
NOT_AN_IMAGE = b"%PDF-1.4\nthis is not a picture at all\n"


@pytest.fixture
def uploads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把封面目录钉进 tmp_path：测试不许往 backend/data 里写东西。"""
    directory = tmp_path / "uploads"
    monkeypatch.setattr(settings, "uploads_dir", directory)
    return directory


@pytest.fixture
def app(uploads_dir: Path) -> FastAPI:
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app: FastAPI, tmp_path: Path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "cover-test.db"))
    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


def upload(client: TestClient, trip_id: str, data: bytes, filename: str = "cover.jpg"):
    """`content_type` 一律谎报成 image/jpeg：判据只能来自字节。"""
    return client.post(
        f"/api/trips/{trip_id}/cover",
        files={"file": (filename, data, "image/jpeg")},
    )


def new_trip(client: TestClient, title: str = "南宁一日游") -> str:
    return client.post("/api/trips", json={"title": title, "city": "南宁"}).json()["trip_id"]


# ---------- 迁移与列 ----------


async def test_booting_an_old_database_adds_cover_url_without_touching_rows(
    tmp_path: Path,
) -> None:
    """老库补列：`CREATE TABLE IF NOT EXISTS` 对已存在的表是空操作，所以走 ALTER。

    补出来的列必须是**空串**而不是 NULL：摘要那一条把它原样读成 `cover_photo`，一旦是
    None，卡片封面就变成一个谁也没渲染过的值。第二次 init 不能再 ALTER。
    """
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE trips (
            id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            travel_mode TEXT NOT NULL DEFAULT 'driving',
            cost_model TEXT NOT NULL DEFAULT 'haversine',
            day_start_min INTEGER NOT NULL DEFAULT 540,
            seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        INSERT INTO trips (id, title, created_at) VALUES ('T0', '上海三日游', 'x');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    await db.init()
    await db.init()

    row = await db.fetch_one("SELECT cover_url, title FROM trips WHERE id = 'T0'")
    assert row["cover_url"] == ""
    assert row["title"] == "上海三日游"


def test_cover_url_is_a_whitelisted_trip_patch_field() -> None:
    """列必须在写入口里：`trip_update` op 的 patch 走的就是这张白名单，
    少一个键等于「恢复默认封面」发出去静默不生效。"""
    assert "cover_url" in repo.TRIP_PATCH_FIELDS


def test_the_http_patch_route_carries_the_same_column(client: TestClient) -> None:
    """白名单之外还有第二道关口：请求体模型。字段没在那儿声明，pydantic 就把它当多余键丢掉，
    `exclude_unset` 于是交出空 patch，route 回 400 或 200 而库里一个字没动——上面那条测试查不到
    这一层。首页与任何没有 WebSocket 的客户端只能走这条路。"""
    trip_id = new_trip(client)
    url = upload(client, trip_id, JPEG).json()["cover_url"]

    got = client.patch(f"/api/trips/{trip_id}", json={"cover_url": "https://example.com/a.jpg"})
    assert got.status_code == 200
    assert got.json()["cover_url"] == "https://example.com/a.jpg"

    cleared = client.patch(f"/api/trips/{trip_id}", json={"cover_url": ""})
    assert cleared.status_code == 200
    assert cleared.json()["cover_url"] == ""
    # 值仍然是刚写进去的那一张：`url` 已被上传端点写过一次，换直链时被回收。
    assert client.get(url).status_code == 404

    bad = client.patch(f"/api/trips/{trip_id}", json={"cover_url": "data:image/png;base64,AAAA"})
    assert bad.status_code == 400


@pytest.mark.parametrize(
    ("data", "expected"), [(JPEG, "jpg"), (PNG, "png"), (WEBP, "webp")]
)
def test_sniff_accepts_the_three_real_headers(data: bytes, expected: str) -> None:
    assert sniff_image(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        NOT_AN_IMAGE,
        b"",
        b"GIF89a" + b"\x00" * 30,  # 没人收 GIF
        b"RIFF\x00\x00\x00\x00AAAA",  # RIFF 家族里 WAV/AVI 那一支
        b"\xff\xd7\xff" + b"\x00" * 30,  # 差一个字节就不是 JPEG
    ],
)
def test_sniff_rejects_everything_else(data: bytes) -> None:
    with pytest.raises(CoverReject) as info:
        sniff_image(data)
    assert info.value.kind == "type"


# ---------- 上传端点 ----------


def test_upload_lands_a_file_and_points_the_column_at_it(
    client: TestClient, uploads_dir: Path
) -> None:
    trip_id = new_trip(client)
    resp = upload(client, trip_id, JPEG)
    assert resp.status_code == 200, resp.text
    url = resp.json()["cover_url"]
    assert url.startswith(f"/uploads/{trip_id}/")
    name = url.rsplit("/", 1)[1]
    # 名字不可猜：`/uploads` 公开可读（决策 12），随机名是它唯一的访问控制。
    assert name.endswith(".jpg") and len(name.split(".")[0]) == 20
    on_disk = uploads_dir / trip_id / name
    assert on_disk.is_file() and on_disk.read_bytes() == JPEG
    # 静态挂载必须读得到，且读到的不能是 SPA 的 index.html——两个 mount 抢的就是同一条路径。
    got = client.get(url)
    assert got.status_code == 200
    assert got.content == JPEG
    assert got.headers["content-type"].startswith("image/")


async def test_the_trip_reads_its_own_cover_back(tmp_path: Path) -> None:
    """`TripOut.cover_url` 必须真从库里出来：快照与广播用的都是这份 dump。"""
    db = Database(tmp_path / "read.db")
    await db.init()
    trip_id, _ = await repo.create_trip(db, title="甲")
    await repo.update_trip(db, trip_id, {"cover_url": "/uploads/AAAAA/x.jpg"})
    snapshot = await repo.get_snapshot(db, trip_id)
    assert snapshot is not None
    assert snapshot.trip.cover_url == "/uploads/AAAAA/x.jpg"


def test_a_disguised_content_type_still_gets_rejected(client: TestClient) -> None:
    trip_id = new_trip(client)
    resp = upload(client, trip_id, NOT_AN_IMAGE)
    assert resp.status_code == 400
    assert "图片" in resp.json()["detail"]


@pytest.mark.parametrize("trip_id", ["../../etc", "a/b", "..", "x" * 40, "id;semicolons"])
async def test_a_path_shaped_trip_id_is_refused_before_any_write(
    uploads_dir: Path, trip_id: str
) -> None:
    """`trip_id` 来自 URL 又拼进路径，这一层必须自己挡住，不能指望生成侧。"""
    with pytest.raises(CoverReject) as info:
        await save_cover(trip_id, JPEG)
    assert info.value.kind == "id"
    assert not uploads_dir.exists()


def test_unknown_trip_gets_404_and_no_file(client: TestClient, uploads_dir: Path) -> None:
    assert upload(client, "GHOST000", JPEG).status_code == 404
    # 目录本身是启动时挂载 /uploads 建的，判据只能是「里面一个文件都没有」。
    assert not uploads_dir.exists() or not any(p.is_file() for p in uploads_dir.rglob("*"))


def test_an_oversized_body_is_rejected_without_touching_disk(
    client: TestClient, uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = new_trip(client)
    monkeypatch.setattr(settings, "cover_max_bytes", 16)
    resp = upload(client, trip_id, JPEG)
    assert resp.status_code == 413
    assert not (uploads_dir / trip_id).exists()


def test_a_missing_file_field_is_a_validation_error(client: TestClient) -> None:
    """少了 `file` 是用错了接口，要回 422 而不是「读到一个空文件」再判成类型非法——
    后者会把一句没人看的 422 变成一句误导人的话。"""
    trip_id = new_trip(client)
    assert client.post(f"/api/trips/{trip_id}/cover", data={}).status_code == 422


@pytest.mark.parametrize(("data", "suffix"), [(PNG, ".png"), (WEBP, ".webp")])
def test_the_stored_extension_follows_the_sniffed_type(
    client: TestClient, uploads_dir: Path, data: bytes, suffix: str
) -> None:
    """文件名由判出来的类型决定：`cover.jpg` 里装着 PNG，落盘就得是 .png，
    否则静态服务给出的 content-type 与内容不符。"""
    trip_id = new_trip(client)
    url = upload(client, trip_id, data, filename="cover.jpg").json()["cover_url"]
    assert url.endswith(suffix)
    assert (uploads_dir / trip_id / url.rsplit("/", 1)[1]).read_bytes() == data


# ---------- 换图、恢复自动与孤儿清理 ----------


def test_replacing_a_cover_deletes_the_previous_file(
    client: TestClient, uploads_dir: Path
) -> None:
    trip_id = new_trip(client)
    first = upload(client, trip_id, JPEG).json()["cover_url"]
    second = upload(client, trip_id, PNG).json()["cover_url"]
    assert first != second
    assert not (uploads_dir / trip_id / first.rsplit("/", 1)[1]).exists()
    assert (uploads_dir / trip_id / second.rsplit("/", 1)[1]).is_file()


async def test_restoring_the_default_cover_clears_the_column_and_frees_the_file(
    tmp_path: Path, uploads_dir: Path
) -> None:
    """「恢复默认封面」= 写空串（硬约束 1）。写完之后那张上传图再没人引用，必须删。

    放在仓储层测而不是打 REST：行程页有 socket，这条路径实际走的是 `trip_update` op，
    而 op 与 REST 共用 `update_trip`——只有这里能一次覆盖两条传输。
    """
    db, trip_id = await _trip_with_photo(tmp_path, "restore.db")
    stored = uploads_dir / trip_id / "mine.jpg"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(JPEG)
    await repo.update_trip(db, trip_id, {"cover_url": f"/uploads/{trip_id}/mine.jpg"})
    assert stored.is_file()  # 换成另一张时不能顺手把没被引用的目录清空

    updated = await repo.update_trip(db, trip_id, {"cover_url": ""})
    assert updated is not None and updated.cover_url == ""
    assert not stored.exists()


def test_an_older_orphan_is_swept_by_the_next_upload(
    client: TestClient, uploads_dir: Path
) -> None:
    """孤儿只来自「文件写成了、库还没写」那一小段窗口，下一次上传顺手清掉该目录。"""
    trip_id = new_trip(client)
    kept = upload(client, trip_id, JPEG).json()["cover_url"]
    stale = uploads_dir / trip_id / "deadbeefdeadbeef0000.jpg"
    stale.write_bytes(b"leftover")
    fresh_orphan = uploads_dir / trip_id / "ffffbeeffffebeef0000.jpg"
    fresh_orphan.write_bytes(b"someone else's in-flight upload")
    # 判据是「够老」，不是「不是当前这张」：并发上传时另一个请求刚写下的图不能算孤儿。
    # 所以这里必须先把时间拨老，否则下面那条「老孤儿没了」是假的绿。
    old = os.stat(stale).st_atime - 3600
    os.utime(stale, (old, old))
    fresh = upload(client, trip_id, PNG).json()["cover_url"]
    assert not stale.exists()
    assert fresh_orphan.is_file()
    assert (uploads_dir / trip_id / fresh.rsplit("/", 1)[1]).is_file()
    assert kept != fresh  # 换图本身照旧生效


@pytest.mark.parametrize(
    "foreign",
    [
        "https://store.is.autonavi.com/showpic/x",  # 外链，压根不是本站文件
        "/uploads/OTHER000/mine.jpg",  # 形状完全合法，但属于别的行程
        "/uploads/../keep.jpg",  # 想往上跳一级
        "/uploads/AAAAAAAA",  # 少一段
        "/uploads/AAAAAAAA/a.jpg/b.jpg",  # 多一段
        "/uploads/",
        "",
        "uploads/AAAAAAAA/a.jpg",  # 少了开头的斜杠就不是本站路径
    ],
)
def test_cleanup_only_ever_touches_what_this_endpoint_wrote(
    uploads_dir: Path, foreign: str
) -> None:
    """/uploads 里躺着别人的图，清自己旧图时一根手指都不能碰。

    `OTHER000` 那一条是重点：它的形状合法，只认形状的旧实现会直接删掉它。
    """
    keep = uploads_dir / "keep.jpg"
    other = uploads_dir / "OTHER000" / "mine.jpg"
    keep.parent.mkdir(parents=True, exist_ok=True)
    other.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"x")
    other.write_bytes(b"y")
    drop_cover_file(foreign, "AAAAAAAA")
    assert keep.is_file()
    assert other.is_file()


def test_drop_removes_a_file_the_endpoint_actually_wrote(uploads_dir: Path) -> None:
    target = uploads_dir / "AAAAAAAA" / "abc.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    drop_cover_file("/uploads/AAAAAAAA/abc.jpg", "AAAAAAAA")
    assert not target.exists()
    drop_cover_file("/uploads/AAAAAAAA/abc.jpg", "AAAAAAAA")  # 已经不在了也不能炸


async def test_pointing_at_another_trip_and_clearing_cannot_delete_its_cover(
    tmp_path: Path, uploads_dir: Path
) -> None:
    """越程删除的真实走法：把甲的封面指到乙的文件上，再「恢复默认封面」。

    指过去这一步是允许的（`/uploads/...` 是个合法值，最多是甲自己看到一张不属于它的图），
    但**清理**不能跟着这个值走——否则任何人都能借一次换图删掉别人上传的封面。
    """
    db = Database(tmp_path / "cross.db")
    await db.init()
    mine, _ = await repo.create_trip(db, title="甲")
    theirs, _ = await repo.create_trip(db, title="乙")
    their_cover = await save_cover(theirs, JPEG)
    await save_cover(mine, PNG)

    await repo.update_trip(db, mine, {"cover_url": their_cover})
    await repo.update_trip(db, mine, {"cover_url": ""})
    assert (uploads_dir / their_cover[len("/uploads/") :]).is_file()


# ---------- 首页摘要优先自定义 ----------


async def _trip_with_photo(tmp_path: Path, name: str = "cover.db"):
    db = Database(tmp_path / name)
    await db.init()
    trip_id, day_id = await repo.create_trip(db, title="南宁一日游", city="南宁")
    place = make_place("青秀山").model_copy(update={"photo_url": "https://a/Q.jpg"})
    await repo.add_place(db, day_id, place)
    return db, trip_id


async def test_summary_prefers_the_custom_cover_over_the_first_photo(tmp_path: Path) -> None:
    db, trip_id = await _trip_with_photo(tmp_path)
    await repo.update_trip(db, trip_id, {"cover_url": "/uploads/AAAAA/mine.jpg"})
    card = (await repo.get_trip_summaries(db, [trip_id]))[0]
    assert card.cover_photo == "/uploads/AAAAA/mine.jpg"


async def test_summary_sends_nothing_when_the_user_has_no_cover(tmp_path: Path) -> None:
    """清空 `cover_url` 之后摘要就是空串：M37 起地点首图不再自动上首页。

    这正是「恢复默认封面」在接口层的含义——后端什么都不猜，前端拿到空串去挑内置的那一张。
    """
    db, trip_id = await _trip_with_photo(tmp_path, "fallback.db")
    await repo.update_trip(db, trip_id, {"cover_url": ""})
    card = (await repo.get_trip_summaries(db, [trip_id]))[0]
    assert card.cover_photo == ""


async def test_a_rejected_cover_url_never_reaches_the_database(tmp_path: Path) -> None:
    """白名单里的强制器是唯一写入口。`javascript:` 之类的值一旦被存下，前端
    `<img src>` 会照原样用它。``/covers/`` 在 M37 之后是合法前缀，所以穿越串也得进来：
    前缀放行不等于路径放行。"""
    db, trip_id = await _trip_with_photo(tmp_path, "reject.db")
    for bad in (
        "javascript:alert(1)",
        "data:image/svg+xml,<svg/>",
        "..\\a.jpg",
        "//x/a.jpg",
        "/covers/../../etc/passwd",
        "/covers",
    ):
        assert await repo.update_trip(db, trip_id, {"cover_url": bad}) is None
    row = await db.fetch_one("SELECT cover_url FROM trips WHERE id = ?", (trip_id,))
    assert row["cover_url"] == ""  # 一次都没写进去


async def test_a_valid_cover_url_survives_the_round_trip(tmp_path: Path) -> None:
    db, trip_id = await _trip_with_photo(tmp_path, "round.db")
    # `/covers/` 那一条是 M37 的内置海报：面板里点中一张要能固定住，写得进才谈得上固定。
    for good in ("/uploads/AAAAA/x.jpg", "/covers/mountain.jpg", "https://a/b.jpg", ""):
        updated = await repo.update_trip(db, trip_id, {"cover_url": good})
        assert updated is not None and updated.cover_url == good
