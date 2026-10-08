"""整站搬进 `/tourplanopt` 前缀之后的地址契约。

这条决定的全部风险都在「什么该进前缀、什么必须留在根上」，而那恰好是编译和单测都
看不见的那一类坏法：接口进了前缀，前端那 40 处 `/api/...` 字面量还是打根上发；
`/covers` 进了前缀，库里已有的封面绝对路径全 404。两种都不会报错，只会白屏。

所以这里钉三件事：

1. **老链接必须活着**——改动前发出去的 `/trip/{id}` 和首页书签，要 301 进前缀，
   这是这次搬家的验收底线（旧代码在根上兜 index.html，跑不出 301，这批判据必红）。
2. **根上那些端点不许被那条兜底 301 吞掉**——`/api`、`/covers`、根 `/sw.js`。
3. **前后端两处前缀不许各写一份**——vite 的 `base` 与后端的默认值必须相等。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.db.database import Database, set_db

PREFIX = "/tourplanopt"
# 搬家前真实发出去的链接形状：朋友手机里存的就是这个，它坏了就是这次上线的事故。
OLD_LINK = "/trip/4HBVAOF7"


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    """现场捏一份最小前端构建产物。

    不读仓库里那个真 `dist`：它存在与否取决于这台机器上次有没有跑过 `vite build`，
    一次构建失败会把整批地址判据染红，而那是和路由规则毫无关系的原因。
    """
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "covers").mkdir(parents=True)
    (root / "icons").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>TourPlanOpt</title>", encoding="utf-8")
    # 前缀下那份是真正管缓存的脚本；根上那份是自毁版，两者内容必须不同。
    (root / "sw.js").write_text("const CACHE = 'tourplanopt-v3'\n", encoding="utf-8")
    (root / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (root / "assets" / "index-abc1234.js").write_text("console.log(1)\n", encoding="utf-8")
    (root / "covers" / "lake.jpg").write_bytes(b"fake jpeg")
    (root / "icons" / "icon-192.png").write_bytes(b"fake png")
    return root


@pytest.fixture()
def client(tmp_path: Path, dist: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "url-prefix.db"))
    monkeypatch.setattr(settings, "frontend_dist", dist)
    monkeypatch.setattr(settings, "frontend_prefix", PREFIX)
    # 现造一个 app：模块级那个 app 是在 import 时用当时的 settings 建好的，
    # 改完 monkeypatch 再用它就等于测了一份没被改过的路由表。
    from app.main import create_app

    with TestClient(create_app()) as testclient:
        yield testclient
    set_db(Database(":memory:"))


# --- 1. 老链接保命 --------------------------------------------------------------


def test_the_link_shared_before_the_move_now_301s_into_the_prefix(client: TestClient) -> None:
    res = client.get(OLD_LINK, follow_redirects=False)
    assert res.status_code == 301, res.text
    assert res.headers["location"] == f"{PREFIX}/trip/4HBVAOF7"


def test_the_bare_domain_301s_into_the_prefix(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 301, res.text
    assert res.headers["location"] == f"{PREFIX}/"


def test_the_redirect_carries_the_query_string(client: TestClient) -> None:
    """登录页把 next 挂在 query 上，丢了参数的 301 会把人扔回首页当没登录。"""
    res = client.get("/trip/4HBVAOF7?next=%2Flogin", follow_redirects=False)
    assert res.status_code == 301
    assert res.headers["location"] == f"{PREFIX}/trip/4HBVAOF7?next=%2Flogin"


def test_following_the_old_link_lands_on_the_app_shell(client: TestClient) -> None:
    """301 只是桥，桥对面必须真的能打开——这一条才是「朋友点开能用」。"""
    res = client.get(OLD_LINK)
    assert res.status_code == 200, res.text
    assert "TourPlanOpt" in res.text


# --- 2. 留在根上的那些，一条都不许被兜底 301 吞掉 ----------------------------------


def test_api_stays_at_root_and_is_not_redirected(client: TestClient) -> None:
    """前端 40 处 fetch 打的是根绝对路径；接口一旦进前缀，症状是「界面在、数据全无」。"""
    res = client.get("/api/health")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True


def test_builtin_cover_urls_stay_resolvable_at_root(client: TestClient) -> None:
    """库里存的封面是 `/covers/...` 绝对路径，挪进前缀 = 线上已有封面全 404。"""
    res = client.get("/covers/lake.jpg")
    assert res.status_code == 200, res.text
    assert res.content == b"fake jpeg"


def test_post_is_never_redirected(client: TestClient) -> None:
    """兜底 301 只接 GET/HEAD：对 POST 发 301，浏览器会改方法重发，那是另一种坏法。"""
    res = client.post("/api/trips", json={})
    assert res.status_code == 201, res.text


# --- 3. 前缀内部：静态资源与 SPA 兜底 --------------------------------------------


def test_hashed_asset_and_deep_link_serve_under_the_prefix(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/assets/index-abc1234.js").status_code == 200
    # 深链在路由表里没有对应文件，必须由前缀内的 SPA 兜住
    deep = client.get(f"{PREFIX}/trip/4HBVAOF7")
    assert deep.status_code == 200
    assert "TourPlanOpt" in deep.text


def test_missing_asset_under_the_prefix_is_a_real_404(client: TestClient) -> None:
    """带扩展名的缺失资源不能回 index.html——拿 HTML 当 JS 加载是一个 MIME 迷宫。"""
    assert client.get(f"{PREFIX}/assets/nope-abc1234.js").status_code == 404


# --- 4. 已装在手机里的旧 service worker ------------------------------------------


def test_root_serves_a_self_deleting_sw_for_the_old_registration(client: TestClient) -> None:
    """搬家前每台手机都在根作用域注册了 /sw.js。它 404 之后仍会拦截导航并在离线时
    吐旧壳——症状只出现在部分人手机上，看起来像「这次上线把站搬坏了」。
    """
    res = client.get("/sw.js")
    assert res.status_code == 200, res.text
    assert "javascript" in res.headers["content-type"]
    assert "registration.unregister()" in res.text
    assert f'const home = "{PREFIX}/";' in res.text
    # 边缘缓存住这份脚本 = 自毁永远送不到手机上（Cloudflare 默认就把 .js 当可缓存资源）
    assert "no-cache" in res.headers["cache-control"]


def test_the_prefixed_sw_is_a_different_script(client: TestClient) -> None:
    root_sw = client.get("/sw.js").text
    prefixed_sw = client.get(f"{PREFIX}/sw.js").text
    assert "unregister()" not in prefixed_sw
    assert root_sw != prefixed_sw


# --- 5. 两处前缀不许各写一份 ------------------------------------------------------


def test_a_newly_created_trip_shares_a_prefixed_link(client: TestClient) -> None:
    """分享链接是后端拼的。它不带前缀时朋友点开要先吃一次 301——能补救不等于设计对，
    而且将来那条兜底 301 一旦撤掉，这些链接就静默坏在根上。
    """
    res = client.post(
        "/api/trips", json={"title": "周末"}, headers={"origin": "https://chengrm.online"}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["share_url"] == f"https://chengrm.online{PREFIX}/trip/{body['trip_id']}"


def test_vite_base_equals_the_backend_prefix_default() -> None:
    """改一边忘另一边是这次搬家唯一会「看起来正常、其实白屏」的错法：
    资源 404 或路由被 catch-all 静默兜回首页，都不报错。
    """
    vite = Path(__file__).resolve().parents[2] / "frontend" / "vite.config.ts"
    match = re.search(r"""base:\s*['"]([^'"]+)['"]""", vite.read_text(encoding="utf-8"))
    assert match, "vite.config.ts 里找不到 base，这条判据就没意义了"
    # 比的是声明的默认值，不是 settings 上的运行时值：谁的 .env 里写了 FRONTEND_PREFIX
    # 都不该把这条判据说成红的。
    declared = Settings.model_fields["frontend_prefix"].default
    assert match.group(1) == f"{declared}/"
