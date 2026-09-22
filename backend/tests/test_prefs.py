"""M31 personal preferences: the two endpoints and the whitelist behind them.

偏好是「我自己的界面」，所以判据集中在三件事：匿名不能写（写进谁头上都不对）、未知键
进不了库（否则会堆没人读的垃圾）、两次 PUT 不相食（同一账号在手机调暗、在电脑调大字号，
后发的那次不能把前一次抹掉）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import accounts
from app.db.database import Database, get_db, set_db


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "prefs-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


def _register(client: TestClient, name: str) -> dict:
    resp = client.post("/api/auth/register", json={"name": name, "password": "secret1"})
    assert resp.status_code == 200
    return resp.json()["user"]


def _put(client: TestClient, payload) -> httpx.Response:
    return client.put("/api/auth/prefs", json=payload)


# ---------- 登录态边界 ----------


def test_anonymous_cannot_write_but_reading_is_not_an_error(client: TestClient):
    """GET 在匿名时给空对象而不是 401：前端在登录态确认前就要开设置面板，那份空偏好
    正是「全部用默认值」的正常渲染路径。写入没有这种解释，所以照旧 401。"""
    assert client.get("/api/auth/prefs").json() == {"prefs": {}}
    assert _put(client, {"theme": "dark"}).status_code == 401


def test_prefs_do_not_outlive_a_logout(client: TestClient):
    _register(client, "小蓝")
    assert _put(client, {"theme": "dark"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/prefs").json() == {"prefs": {}}
    assert _put(client, {"theme": "light"}).status_code == 401


def test_prefs_are_per_account(client: TestClient):
    _register(client, "甲")
    _put(client, {"theme": "dark"})
    # 换账号即换 cookie，读到的必须是新账号自己那份（空的），而不是上一个用户的。
    _register(client, "乙")
    assert client.get("/api/auth/prefs").json() == {"prefs": {}}


# ---------- 白名单 ----------


def test_a_saved_preference_reads_back(client: TestClient):
    _register(client, "小绿")
    assert _put(client, {"theme": "dark"}).json() == {"prefs": {"theme": "dark"}}
    assert client.get("/api/auth/prefs").json() == {"prefs": {"theme": "dark"}}


def test_unknown_keys_and_values_are_dropped_not_rejected(client: TestClient):
    """丢掉而不是报错是刻意的：一个还没升级的前端多传了键，不该让整个保存失败。"""
    _register(client, "小红")
    resp = _put(
        client,
        {
            "theme": "dark",
            "font_size": "huge",  # 认不下的取值
            "pet_visible": "yes",  # 认不下的类型
            "session_token": "leak",  # 根本没这个设置项
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"prefs": {"theme": "dark"}}
    assert client.get("/api/auth/prefs").json() == {"prefs": {"theme": "dark"}}


def test_every_documented_option_round_trips(client: TestClient):
    """把白名单五个键各存一个合法值，一次读回全见。

    这条是给「加设置项时忘了同步白名单」准备的：新键在前端能点、在库里存不下，症状是
    调了没反应，而前端看不出任何错。
    """
    _register(client, "小黄")
    payload = {
        "theme": "auto",
        "font_size": "xl",
        "motion": "reduce",
        "basemap": "light",
        "pet_visible": False,
    }
    assert _put(client, payload).status_code == 200
    assert client.get("/api/auth/prefs").json() == {"prefs": payload}


def test_a_false_flag_survives_the_whitelist(client: TestClient):
    """pet_visible=False 是这条判据的意义所在：`if raw.get(key)` 那种写法会把 False
    和缺失混为一谈，用户关掉的精灵就会在下次登录后自己爬起来。"""
    _register(client, "小紫")
    assert _put(client, {"pet_visible": False}).json() == {"prefs": {"pet_visible": False}}
    assert client.get("/api/auth/prefs").json() == {"prefs": {"pet_visible": False}}


# ---------- 坏输入 ----------


def test_a_non_object_body_is_a_400(client: TestClient):
    _register(client, "小青")
    for bad in (["theme", "dark"], "dark", 5, True):
        assert _put(client, bad).status_code == 400


def test_malformed_json_is_a_400_not_a_500(client: TestClient):
    _register(client, "小朱")
    resp = client.put(
        "/api/auth/prefs",
        content=b'{"theme": "dark",}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_an_oversized_body_is_refused_before_parsing(client: TestClient):
    """那条 junk 键本来会被白名单丢掉，所以「先解析再量」的话这里会是 200：413 本身就
    证明了上限量的是实际到达的字节，而不是过滤后的结果。"""
    _register(client, "小吴")
    fat = {"theme": "dark", "junk": "x" * (accounts.PREF_MAX_BYTES + 1)}
    assert client.put("/api/auth/prefs", json=fat).status_code == 413
    # 被拒的那一趟什么都没留下
    assert client.get("/api/auth/prefs").json() == {"prefs": {}}


def test_an_empty_body_is_a_no_op(client: TestClient):
    _register(client, "小周")
    _put(client, {"theme": "dark"})
    resp = client.put("/api/auth/prefs")
    assert resp.status_code == 200
    assert resp.json() == {"prefs": {"theme": "dark"}}


# ---------- 逐键合并 ----------


def test_two_devices_each_setting_one_key_keep_both(client: TestClient):
    """整块覆盖的话后发那次会把前一次抹掉，而且用户完全看不出发生过什么。"""
    _register(client, "小郑")
    _put(client, {"theme": "dark"})
    _put(client, {"font_size": "lg"})
    assert client.get("/api/auth/prefs").json() == {"prefs": {"theme": "dark", "font_size": "lg"}}


def test_the_later_writer_wins_on_one_key(client: TestClient):
    _register(client, "小冯")
    _put(client, {"theme": "dark"})
    _put(client, {"theme": "light"})
    assert client.get("/api/auth/prefs").json() == {"prefs": {"theme": "light"}}


async def test_a_corrupt_stored_blob_reads_as_empty_and_heals_on_next_write(client: TestClient):
    """库里那一份坏掉就当没有：界面照样能开，下一次保存顺手写回一份干净的。"""
    user = _register(client, "小陈")
    db = get_db()

    def _break(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE users SET prefs = ? WHERE id = ?", ("{not json", user["id"]))

    await db.run(_break)

    assert client.get("/api/auth/prefs").json() == {"prefs": {}}
    assert _put(client, {"theme": "dark"}).json() == {"prefs": {"theme": "dark"}}
    assert client.get("/api/auth/prefs").json() == {"prefs": {"theme": "dark"}}


def test_a_blob_the_whitelist_empties_out_keeps_what_was_already_stored(client: TestClient):
    """全被丢掉的输入等于没写：合并进来的是 {}，库里那一份要原样不动。"""
    _register(client, "小卫")
    _put(client, {"theme": "dark"})
    _put(client, {"nope": "whatever"})
    assert client.get("/api/auth/prefs").json() == {"prefs": {"theme": "dark"}}
