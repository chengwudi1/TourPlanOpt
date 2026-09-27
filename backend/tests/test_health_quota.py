"""`/api/amap/health` 配额闸的回归测试（上线前唯一不能延后的项）。

公网把这一发探活变成了可被任意人消耗的开销：每个陌生访客首次进页面各打一发，
无鉴权的 GET 还能被直接刷。修复给结果加了全局 TTL 缓存 + 并发锁 + fresh 下限。

每条判据都在「修复前那份代码」上必红（旧代码每次请求都 `await full_self_check`）：

- 重复请求只打一发（旧代码 N 次 = N 发）
- 并发风暴只打一发（旧代码没有锁，N 个并发在缓存填上前各打一发）
- fresh 早于下限仍走缓存（否则 ?fresh=1 就成了新的刷量口子）
- 越过 TTL 会重新探（证明不是「一次探活永久冻结」，缓存真的会老化）
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.amap.health import AmapHealthReport, KeyCheck
from app.api import routes_health
from app.db.database import Database, set_db


def _ok_report() -> AmapHealthReport:
    web = KeyCheck(name="AMAP_WEB_KEY", ok=True, present=True, detail="ok", hint="")
    js = KeyCheck(name="AMAP_JS_KEY", ok=True, present=True, detail="ok", hint="")
    return AmapHealthReport(web_key=web, js_key=js)


class ProbeCounter:
    """替掉 full_self_check 的假探活：每打一发 n += 1，可选让出一拍以逼出并发问题。"""

    def __init__(self, yield_once: bool = False) -> None:
        self.n = 0
        self._yield = yield_once

    async def __call__(self, client, cache_stats=None) -> AmapHealthReport:
        if self._yield:
            await asyncio.sleep(0)  # 无锁时，其余协程会在这发探活填缓存前穿过判空
        self.n += 1
        return _ok_report()


@pytest.fixture()
def app_client(tmp_path: Path) -> Iterator[TestClient]:
    routes_health._amap_health_cache = None  # 每条用例从冷缓存起步，测试顺序不串味
    set_db(Database(tmp_path / "health.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))
    routes_health._amap_health_cache = None


def _install(monkeypatch: pytest.MonkeyPatch, *, yield_once: bool = False) -> ProbeCounter:
    counter = ProbeCounter(yield_once=yield_once)
    monkeypatch.setattr(routes_health, "full_self_check", counter)
    monkeypatch.setattr(routes_health, "get_amap_client", lambda: None)
    return counter


def test_repeated_requests_cost_one_probe(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _install(monkeypatch)
    for _ in range(3):
        resp = app_client.get("/api/amap/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
    # 坏版本：旧代码这里会是 3。
    assert counter.n == 1, f"TTL 窗口内 3 次请求应只打 1 发，实际 {counter.n}"


def test_fresh_within_floor_still_cached(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _install(monkeypatch)
    app_client.get("/api/amap/health")
    # 紧接着用 ?fresh=1 绕缓存——但距上次不足下限间隔，仍应走缓存，否则这参数就是刷量口子。
    resp = app_client.get("/api/amap/health", params={"fresh": True})
    assert resp.status_code == 200
    assert counter.n == 1, f"fresh 早于下限不该再打，实际 {counter.n}"


def test_fresh_after_floor_probes_again(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _install(monkeypatch)
    app_client.get("/api/amap/health")
    # 把缓存时间戳拨回下限之前，模拟「用户隔了一会儿再点重新检查」。
    ts, data = routes_health._amap_health_cache
    routes_health._amap_health_cache = (ts - routes_health._AMAP_FRESH_MIN_INTERVAL_S - 1, data)
    app_client.get("/api/amap/health", params={"fresh": True})
    assert counter.n == 2, f"越过下限的 fresh 应重新探，实际 {counter.n}"


def test_ttl_expiry_refreshes_probe(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _install(monkeypatch)
    app_client.get("/api/amap/health")
    ts, data = routes_health._amap_health_cache
    routes_health._amap_health_cache = (ts - routes_health._AMAP_HEALTH_TTL_S - 1, data)
    app_client.get("/api/amap/health")
    # 坏版本之外的另一种坏：把缓存做成永久不老化，这里会是 1。
    assert counter.n == 2, f"越过 TTL 的普通请求应重新探，实际 {counter.n}"


def test_concurrent_burst_single_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # 直接打协程、不经 TestClient：并发风暴是这条锁要挡的场景。
    routes_health._amap_health_cache = None
    counter = _install(monkeypatch, yield_once=True)

    async def burst() -> list[dict]:
        # 显式传 fresh=False：直调协程时 FastAPI 不参与，默认值会是 Query(False) 对象（真值）。
        return await asyncio.gather(*(routes_health.amap_health(fresh=False) for _ in range(20)))

    results = asyncio.run(burst())
    routes_health._amap_health_cache = None
    assert all(r["ok"] for r in results)
    # 坏版本：旧代码没有锁，20 个协程会在缓存填上前各打一发 → n == 20。
    assert counter.n == 1, f"20 路并发应只打 1 发，实际 {counter.n}"
