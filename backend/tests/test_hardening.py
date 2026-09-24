"""上线前加固项的回归测试（第二轮审计 Fix R1–R5）。

每条判据都对应一个修掉的具体漏洞，且在修复前的代码上必红：

- patch 文本长度闸（旧代码 121 字的 name 会直接落库并广播）
- uploads trip_id 的 ``match``+``$`` 换行旁路（旧代码 "AAAA\\n" 能拼进目录名）
- WS Origin 闸门（旧代码根本没有这一层）
- 距离缓存 TTL（旧代码 fetched_at 不参与读取，一条实测永久生效）
- op_id 去重跨空房存活（旧代码房间回收时去重集陪葬，重连补发会落两行）
- _resync 不消耗 seq（旧代码每 resync 烧一个号，给房里其他人留 seq 空洞）
- 密码长度闸（旧代码 10MB 密码 = 一次免费钉死 CPU 的 PBKDF2）
- 距离矩阵 single-flight（旧代码两人同刻 optimize 把同一批坐标烧两遍配额）
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.amap.cache import DistanceCache
from app.amap.client import DistanceResult
from app.config import settings
from app.db.database import Database, set_db
from app.models import protocol
from app.models.domain import MessageIn
from app.uploads import CoverReject, _safe_trip_id
from tests.frames import read_op


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[tuple[TestClient, str, str]]:
    """Temp db + one fresh trip. Yields (testclient, trip_id, day_id)."""
    set_db(Database(tmp_path / "hardening.db"))
    from app.main import app

    with TestClient(app) as testclient:
        resp = testclient.post("/api/trips", json={"title": "加固回归", "city": "上海"})
        assert resp.status_code == 201
        body = resp.json()
        yield testclient, body["trip_id"], body["day_id"]
    set_db(Database(":memory:"))


def hello(client_id: str, name: str) -> dict:
    return protocol.hello_frame(client_id, name, "#123456")


def join_and_sync(ws, client_id: str, name: str) -> dict:
    ws.send_json(hello(client_id, name))
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome"
    frame = ws.receive_json()
    while frame.get("op") == "timeline_updated":
        frame = ws.receive_json()
    assert frame["type"] == "presence_join"
    return welcome


# -- R2 patch 文本长度闸 -----------------------------------------------------------------


def test_oversized_place_name_patch_is_rejected_with_bad_patch(client) -> None:
    """121 字的地点名必须吃到 op_reject(bad_patch)，而不是安静落库广播给全房。

    修复前 `_bounded_text` 不存在，这一笔会走成功路径回 place_updated —— 此测试即红。
    同值 120 字必须照常通过，闸不能比前端 maxlength 更严。
    """
    testclient, trip_id, day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join_and_sync(ws1, "c-1", "小明")
        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_ADD,
                "op-len-add",
                {"day_id": day_id, "name": "短名", "lng": 121.47, "lat": 31.23},
            )
        )
        place = read_op(ws1, "place_added")["data"]["place"]

        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-len-bad",
                {"place_id": place["id"], "patch": {"name": "字" * 121}},
            )
        )
        reject = read_op(ws1, "op_reject")
        assert reject["type"] == "op_reject", reject
        assert reject["reason"] == "bad_patch"

        ws1.send_json(protocol.ping_frame())
        assert ws1.receive_json()["type"] == "pong"

        ok = "字" * 120
        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.PLACE_UPDATE,
                "op-len-ok",
                {"place_id": place["id"], "patch": {"name": ok}},
            )
        )
        applied = read_op(ws1, "place_updated")
        assert applied["type"] == "op", applied
        assert applied["data"]["place"]["name"] == ok


def test_message_layers_text_clamps_in_repo_not_model() -> None:
    """聊天文本的闸在仓储（截断落库），不在模型（拒发）——两层各钉一次。

    模型若在 text 上加 max_length，超长留言会被 op_reject 吞掉，违反「话照发、只剪尾」
    的既有设计（test_messages 里那条截断判据也会红）。锚点 id 则是真闸：它们进查询与广播。
    """
    assert MessageIn(text="字" * 10_000).text  # 不拒发：仓储会截到 MESSAGE_TEXT_MAX
    with pytest.raises(ValidationError):
        MessageIn(text="在的", ref_place_id="x" * 65)


# -- R1 uploads trip_id 换行旁路 ---------------------------------------------------------


def test_safe_trip_id_rejects_trailing_newline(tmp_path, monkeypatch) -> None:
    """`^...$` + re.match 允许尾随换行："ABCD1234\\n" 曾是合法 id，会拼进目录名。

    修复前 _safe_trip_id 用的是 re.match，此断言即红。
    """
    monkeypatch.setattr(settings, "uploads_dir", tmp_path)
    assert _safe_trip_id("ABCD1234") == "ABCD1234"
    with pytest.raises(CoverReject) as exc:
        _safe_trip_id("ABCD1234\n")
    assert exc.value.kind == "id"
    with pytest.raises(CoverReject):
        _safe_trip_id("../../etc")


# -- R4 WS Origin 闸门 -------------------------------------------------------------------


class _FakeWS:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_ws_origin_policy(monkeypatch) -> None:
    """无 Origin 放行（非浏览器客户端）；白名单与同源放行；外站 Origin 拒绝。"""
    from app.main import _ws_origin_allowed

    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:5173"])
    monkeypatch.setattr(settings, "ws_allowed_origins", ["https://trip.example.hk"])

    ok_host = {"host": "trip.example.hk:8000"}
    assert _ws_origin_allowed(_FakeWS(ok_host))  # 浏览器必带 Origin；探针/服务端不带
    assert _ws_origin_allowed(_FakeWS({**ok_host, "origin": "https://trip.example.hk"}))
    assert _ws_origin_allowed(_FakeWS({**ok_host, "origin": "http://localhost:5174"}))
    assert _ws_origin_allowed(
        _FakeWS({"host": "10.0.0.5:8000", "origin": "http://10.0.0.5:8000"})
    )
    assert not _ws_origin_allowed(_FakeWS({**ok_host, "origin": "http://evil.test"}))
    # 白名单不是摆设：没进 ws_allowed_origins 的 https 外站照样拒
    assert not _ws_origin_allowed(_FakeWS({**ok_host, "origin": "https://other.site"}))


def test_docs_endpoints_hidden_by_default() -> None:
    """expose_docs 默认关：/docs 与 /openapi.json 不给公网探路用的地图。

    判据钉在 app 装配上而不是 HTTP 码：frontend/dist 存在时 SPA catch-all 会把 /docs
    兜成 200 的 index.html，HTTP 断言测的是静态目录而不是这道开关。
    """
    from app.main import create_app

    monkeyoff = settings.expose_docs
    try:
        setattr(settings, "expose_docs", False)
        app_off = create_app()
        assert app_off.docs_url is None
        assert app_off.redoc_url is None
        assert app_off.openapi_url is None

        setattr(settings, "expose_docs", True)
        app_on = create_app()
        assert app_on.docs_url == "/docs"
        assert app_on.openapi_url == "/openapi.json"
    finally:
        setattr(settings, "expose_docs", monkeyoff)


# -- R3 距离缓存 TTL ---------------------------------------------------------------------


async def test_expired_cache_row_misses_but_row_survives(tmp_path: Path, monkeypatch) -> None:
    """fetched_at 早于 TTL 的行视同未命中；行不删，等下次实测 upsert 覆盖。

    修复前 get() 的 SQL 没有 fetched_at 条件，过期行照样返回——此断言即红。
    """
    monkeypatch.setattr(settings, "amap_cache_ttl_days", 90)
    db = Database(tmp_path / "ttl.db")
    await db.init()
    set_db(db)
    try:
        cache = DistanceCache(db)
        o, d = (121.4737, 31.2304), (121.5057, 31.2453)
        from app.amap.cache import CacheRow, round_key

        await cache.put(
            [CacheRow(origin=o, destination=d, mode=1, distance_m=2000,
                      duration_s=600, ok=True, infocode=None)]
        )
        keys = (*round_key(o), *round_key(d), 1)
        assert keys in await cache.get([(o, d)], 1)

        stale = (datetime.now(UTC) - timedelta(days=120)).isoformat(timespec="seconds")
        await db.execute(
            "UPDATE amap_distance_cache SET fetched_at = ?"
            " WHERE o_lng_r=? AND o_lat_r=? AND d_lng_r=? AND d_lat_r=? AND mode=?",
            (stale, *keys),
        )
        assert await cache.get([(o, d)], 1) == {}
        # 过期不等于删除：行还在表里，等待下一次实测覆盖。
        assert await cache.count() == 1
    finally:
        set_db(Database(":memory:"))


# -- R4 op_id 去重跨空房存活 ---------------------------------------------------------------


def test_dedupe_history_survives_room_teardown() -> None:
    """最后一人离开 → TripHub 回收；重建的实例仍认得旧 op_id，重连补发不落第二行。

    修复前去重集是 TripHub 的实例属性，随 release_if_empty 一起陪葬。这里直接驱动
    Hub 的生命周期 API——走真 WS 做不到确定性（TestClient 下服务端回收发生在下一次
    进出之后，时序不可控），而这个性质只属于 Hub/TripHub 这一层。
    """
    from app.ws.hub import Hub

    hub = Hub()
    first = hub.get("TEARDOWN")
    first.remember_op("op-1")
    assert first.seen_op("op-1")

    hub.release_if_empty("TEARDOWN")
    assert hub.peek("TEARDOWN") is None  # 房间确实没了

    second = hub.get("TEARDOWN")
    assert second is not first
    assert second.seen_op("op-1")  # 补发的旧 op 仍判重复
    assert not second.seen_op("op-2")


# -- _resync 不消耗 seq -------------------------------------------------------------------


def test_resync_does_not_consume_seq(client) -> None:
    """两次 resync 的 welcome 必须带同一个 seq。

    修复前 _resync 用 next_seq：每 resync 一次凭空烧一个号，房里其他人从没见过它，
    下一帧就被 gap 启发式误判成丢帧而集体 resync。旧代码下第二次 welcome 的 seq 会 +1。
    """
    testclient, trip_id, _day_id = client
    with testclient.websocket_connect(f"/ws/trips/{trip_id}") as ws1:
        join_and_sync(ws1, "c-1", "小明")
        ws1.send_json(protocol.resync_frame(0))
        first = ws1.receive_json()
        assert first["type"] == "welcome"
        ws1.send_json(protocol.resync_frame(0))
        second = ws1.receive_json()
        assert second["type"] == "welcome"
        assert second["seq"] == first["seq"]

        # 真广播仍然照常消耗号：resync 之后下一个 op 是 seq+1。
        ws1.send_json(
            protocol.op_frame(
                protocol.Ops.TRIP_UPDATE,
                "op-after-resync",
                {"patch": {"city": "杭州"}},
            )
        )
        echo = read_op(ws1, "trip_updated")
        assert echo["seq"] == first["seq"] + 1


# -- R1' 密码长度闸 -----------------------------------------------------------------------


def test_password_length_gates(client) -> None:
    """注册 400 / 登录 401，两条路都在进 PBKDF2 之前拦下超长输入。"""
    testclient, _trip_id, _day_id = client
    long_pw = "x" * 5000

    resp = testclient.post("/api/auth/register", json={"name": "长度闸", "password": long_pw})
    assert resp.status_code == 400

    ok = testclient.post("/api/auth/register", json={"name": "长度闸", "password": "abcdef123"})
    assert ok.status_code == 200

    bad = testclient.post("/api/auth/login", json={"name": "长度闸", "password": long_pw})
    assert bad.status_code == 401  # 不是 500，也不是把 5000 字喂给 PBKDF2


# -- R5 single-flight ---------------------------------------------------------------------


class _SlowClient:
    """只实现 build_matrix 用到的那一面：每次 distance 慢 0.25s 并计数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def distance(self, origins, destination, mode):
        self.calls += 1
        await asyncio.sleep(0.25)
        return [
            DistanceResult(origin_index=i, distance_m=1000, duration_s=600 + i, ok=True)
            for i in range(len(origins))
        ]


async def test_concurrent_matrix_builds_share_inflight_calls(tmp_path: Path) -> None:
    """两人同刻 optimize：同一批 (origins, destination, mode) 只许打一次高德。

    cache.put 要等整列 gather 结束才落盘，缓存挡不住这个窗口。旧代码没有 _distance_shared，
    4 节点两连发 = 6 次 HTTP；修复后 = 3 次。
    """
    from app.models.domain import TravelMode
    from app.routing.matrix import _inflight, build_matrix

    db = Database(tmp_path / "sf.db")
    await db.init()
    set_db(db)
    try:
        nodes = [
            (121.4737, 31.2304),
            (121.4900, 31.2360),
            (121.5057, 31.2453),
            (121.5100, 31.2200),
        ]
        cache = DistanceCache(db)
        client = _SlowClient()
        r1, r2 = await asyncio.gather(
            build_matrix(nodes, travel_mode=TravelMode.DRIVING, cost_model="amap",
                         cache=cache, client=client),
            build_matrix(nodes, travel_mode=TravelMode.DRIVING, cost_model="amap",
                         cache=cache, client=client),
        )
        assert client.calls == len(nodes) - 1  # 不是 2*(n-1)
        assert r1.seconds == r2.seconds
        assert _inflight == {}  # 在途表必须清空，不泄漏任务
    finally:
        set_db(Database(":memory:"))
