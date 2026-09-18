"""语音输入的第二条路：浏览器录音 → 后端转写（``POST /api/trips/{id}/assistant/speech``）。

存在的理由写在这三行里：Chrome 自带的 Web Speech 没有可配置的服务端点，音频只能发到境外
那台识别服务，本机网络到不了就永远只会报 ``network``。这条路把转写搬到后端，端点由
``ASR_BASE_URL`` 决定。

钉在这里的纪律，按重要性排：

1. **失败不能长成「没听清」**。服务没配好、上游没应答，都必须是一条带 hint 的 5xx，
   绝不能返回空串让前端读成「用户没说清」——那是把配置问题推给说话的人。
2. **这不是一个匿名文件托管口**。认行程、认 MIME、认大小，三道都在转写之前。
3. **密钥不出进程**：状态接口只报有无与 host，转写失败也不把上游错误体回显给浏览器。

上游全部替身，本文件零外网请求。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.api.routes_assistant as routes
import app.assistant.speech as speech
from app.assistant.speech import AsrError
from app.config import settings
from app.db.database import Database, set_db

AUDIO = b"\x1a\x45\xdf\xa3fake-opus-bytes" * 8


@pytest.fixture()
def trip_client(tmp_path) -> Iterator[tuple[TestClient, str]]:
    set_db(Database(tmp_path / "speech-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        created = testclient.post("/api/trips", json={"title": "南京一日", "city": "南京"}).json()
        yield testclient, created["trip_id"]
    set_db(Database(":memory:"))


@pytest.fixture()
def ready(monkeypatch) -> None:
    """假装 ASR 已配好：路由里的 502/503 分支看的就是这个布尔量。"""
    monkeypatch.setattr(routes, "asr_ready", lambda: True)


def upload(
    testclient: TestClient,
    trip_id: str,
    *,
    mime: str = "audio/webm",
    blob: bytes = AUDIO,
    name: str = "speech.webm",
):
    return testclient.post(
        f"/api/trips/{trip_id}/assistant/speech",
        files={"file": (name, blob, mime)},
    )


# -- 路由：三道守卫 ---------------------------------------------------------------------


def test_speech_returns_the_transcript(trip_client, monkeypatch, ready) -> None:
    testclient, trip_id = trip_client
    seen: dict[str, object] = {}

    async def fake(blob: bytes, *, filename: str, mime: str) -> str:
        seen.update(size=len(blob), filename=filename, mime=mime)
        return "加个玄武湖玩两小时"

    monkeypatch.setattr(routes, "transcribe", fake)
    resp = upload(testclient, trip_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "加个玄武湖玩两小时"}
    assert seen == {"size": len(AUDIO), "filename": "speech.webm", "mime": "audio/webm"}


def test_speech_rejects_unknown_trip(trip_client, monkeypatch, ready) -> None:
    """不认行程的上传口就是匿名文件托管服务。转写替身必须一次都没被调到。"""
    testclient, _ = trip_client
    calls: list[str] = []

    async def fake(blob: bytes, *, filename: str, mime: str) -> str:
        calls.append(filename)
        return "不该走到这里"

    monkeypatch.setattr(routes, "transcribe", fake)
    resp = upload(testclient, "NOSUCHTR")
    assert resp.status_code == 404
    assert calls == []


def test_speech_rejects_non_audio(trip_client, monkeypatch, ready) -> None:
    testclient, trip_id = trip_client

    async def fake(blob: bytes, *, filename: str, mime: str) -> str:
        raise AssertionError("非音频不该转写")

    monkeypatch.setattr(routes, "transcribe", fake)
    resp = upload(testclient, trip_id, mime="text/plain", blob=b"just a script", name="note.txt")
    assert resp.status_code == 400
    assert "音频" in resp.json()["detail"]


def test_speech_rejects_empty_recording(trip_client, monkeypatch, ready) -> None:
    testclient, trip_id = trip_client
    resp = upload(testclient, trip_id, blob=b"")
    assert resp.status_code == 400


def test_speech_caps_upload_size(trip_client, monkeypatch, ready) -> None:
    """大小上限靠「多读一字节」判，所以这条测的是超限，不是恰好等于上限。"""
    testclient, trip_id = trip_client
    monkeypatch.setattr(settings, "asr_max_bytes", 64)
    resp = upload(testclient, trip_id, blob=AUDIO * 8)
    assert resp.status_code == 413
    assert routes._size_label(64) in resp.json()["detail"]


def test_recording_exactly_at_the_cap_is_accepted(trip_client, monkeypatch, ready) -> None:
    """超限判定写成「大于等于」的话，正好卡在上限的那次说话会被无声拒掉。这条钉住边界。"""
    testclient, trip_id = trip_client
    monkeypatch.setattr(settings, "asr_max_bytes", 64)

    async def fake(blob: bytes, *, filename: str, mime: str) -> str:
        return "好"

    monkeypatch.setattr(routes, "transcribe", fake)
    resp = upload(testclient, trip_id, blob=b"x" * 64)
    assert resp.status_code == 200, resp.text


def test_speech_rate_limits_per_trip(trip_client, monkeypatch, ready) -> None:
    testclient, trip_id = trip_client

    async def fake(blob: bytes, *, filename: str, mime: str) -> str:
        return "好"

    monkeypatch.setattr(routes, "transcribe", fake)
    codes = [upload(testclient, trip_id).status_code for _ in range(routes._RATE_CALLS + 1)]
    assert codes[: routes._RATE_CALLS] == [200] * routes._RATE_CALLS
    assert codes[-1] == 429


def test_speech_bucket_is_not_shared_with_parse(trip_client, monkeypatch, ready) -> None:
    """一次说话 = 一次转写 + 一次解析。共用一个窗口的话，第 5 次说话就会被自己限死。"""
    testclient, trip_id = trip_client

    async def fake(blob: bytes, *, filename: str, mime: str) -> str:
        return "加个鸡鸣寺"

    monkeypatch.setattr(routes, "transcribe", fake)
    for _ in range(6):
        assert upload(testclient, trip_id).status_code == 200
        resp = testclient.post(f"/api/trips/{trip_id}/assistant/parse", json={"text": "加个鸡鸣寺"})
        assert resp.status_code == 200, resp.text


# -- 路由：失败形态 ---------------------------------------------------------------------


def test_speech_without_config_says_what_to_fill_in(trip_client, monkeypatch) -> None:
    testclient, trip_id = trip_client
    monkeypatch.setattr(routes, "asr_ready", lambda: False)

    async def boom(blob: bytes, *, filename: str, mime: str) -> str:
        raise AsrError("服务端没有启用语音识别", "在 backend/.env 填 ASR_BASE_URL")

    monkeypatch.setattr(routes, "transcribe", boom)
    resp = upload(testclient, trip_id)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["message"] == "服务端没有启用语音识别"
    assert "ASR_BASE_URL" in detail["hint"]


def test_upstream_failure_is_502_and_never_a_blank_text(trip_client, monkeypatch, ready) -> None:
    """空 text 会被前端读成「没听清」：服务没答上来时绝不能长成用户没说清。"""
    testclient, trip_id = trip_client

    async def boom(blob: bytes, *, filename: str, mime: str) -> str:
        raise AsrError("转写服务没有应答", "确认 127.0.0.1:8100 还在跑")

    monkeypatch.setattr(routes, "transcribe", boom)
    resp = upload(testclient, trip_id)
    assert resp.status_code == 502
    body = resp.json()
    detail = body["detail"]
    assert detail["message"] == "转写服务没有应答"
    assert detail["hint"]
    # 前端看的是 text 字段：失败了却不能给它一个空串，那会被读成「用户没说清」。
    assert "text" not in body


# -- 状态接口与客户端 -------------------------------------------------------------------


def test_status_reports_speech_without_the_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "asr_base_url", "https://asr.example.com/v1")
    monkeypatch.setattr(settings, "asr_api_key", "sk-asr-secret-token")
    monkeypatch.setattr(settings, "asr_model", "paraformer")
    from app.main import app

    with TestClient(app) as testclient:
        resp = testclient.get("/api/assistant/status")
        assert resp.status_code == 200
        assert "sk-asr-secret-token" not in resp.text
        body = resp.json()
        assert body["speech_ready"] is True
        assert body["speech_endpoint"] == "asr.example.com · paraformer"


def test_status_says_not_ready_when_only_a_key_is_given(monkeypatch) -> None:
    """只有密钥没有端点：那条路压根不存在，别让它报「可用」。"""
    monkeypatch.setattr(settings, "asr_base_url", "")
    monkeypatch.setattr(settings, "asr_api_key", "sk-asr-secret-token")
    assert speech.asr_ready() is False


def test_local_endpoint_needs_no_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "asr_base_url", "http://127.0.0.1:8100/v1")
    monkeypatch.setattr(settings, "asr_api_key", "")
    assert speech.asr_ready() is True


def test_filename_is_stripped_to_safe_chars() -> None:
    """filename 要进 multipart 头部：换行、引号、路径都得刮掉。"""
    # 只取最后一段，再刮掉安全字符之外的东西：路径与上跳都出不了头。
    assert speech._filename_of("../../etc/pa ss.webm", "audio/webm") == "pass.webm"
    assert speech._filename_of('..\\..\\windows\\x.webm', "audio/webm") == "x.webm"
    assert "\r" not in speech._filename_of('a"b\r\nwebm', "audio/webm")
    # 认不出扩展名时按 MIME 兜一个，上游才知道这是段什么音频。
    assert speech._filename_of("", "audio/webm") == "speech.webm"
    assert speech._filename_of("noext", "audio/ogg") == "speech.ogg"


class _FakePost:
    def __init__(self, *, status_code: int = 200, payload: object = None, text: str = "") -> None:
        self.status_code = status_code
        self.payload = payload if payload is not None else {"text": "行"}
        self.text = text
        self.calls: list[dict] = []

    async def post(self, url, **kw):
        self.calls.append({"url": url, **kw})
        outer = self

        class _Resp:
            content = b"{}"
            status_code = outer.status_code
            text = outer.text or "upstream said something"

            def json(self):
                if isinstance(outer.payload, Exception):
                    raise outer.payload
                return outer.payload

        return _Resp()


@pytest.fixture()
def http(monkeypatch):
    def _install(fake: _FakePost) -> _FakePost:
        monkeypatch.setattr(speech, "_http", lambda: fake)
        return fake

    return _install


async def test_transcribe_posts_openai_shape_with_bearer(monkeypatch, http) -> None:
    monkeypatch.setattr(settings, "asr_base_url", "https://asr.example.com/v1")
    monkeypatch.setattr(settings, "asr_api_key", "sk-abc")
    monkeypatch.setattr(settings, "asr_model", "whisper-1")
    monkeypatch.setattr(settings, "asr_language", "zh")
    fake = http(_FakePost(payload={"text": "  明天上午去玄武湖  "}))
    text = await speech.transcribe(AUDIO, filename="speech.webm", mime="audio/webm")
    assert text == "明天上午去玄武湖"  # 首尾空白是上游常带的，不该让前端去 trim
    call = fake.calls[0]
    assert call["url"] == "https://asr.example.com/v1/audio/transcriptions"
    assert call["data"] == {"model": "whisper-1", "language": "zh"}
    assert call["headers"]["Authorization"] == "Bearer sk-abc"
    assert call["files"]["file"][0] == "speech.webm"


async def test_transcribe_omits_auth_for_local_endpoint(monkeypatch, http) -> None:
    monkeypatch.setattr(settings, "asr_base_url", "http://127.0.0.1:8100/v1")
    monkeypatch.setattr(settings, "asr_api_key", "")
    monkeypatch.setattr(settings, "asr_language", "")
    fake = http(_FakePost(payload={"text": "行"}))
    assert await speech.transcribe(AUDIO, filename="x", mime="audio/webm") == "行"
    call = fake.calls[0]
    assert "Authorization" not in call["headers"]
    # 语种没配就不发这个字段：让服务端自己判，比硬塞一个 zh 更不容易翻车。
    assert call["data"] == {"model": "whisper-1"}


async def test_transcribe_maps_upstream_status_to_a_readable_error(monkeypatch, http) -> None:
    monkeypatch.setattr(settings, "asr_base_url", "http://127.0.0.1:8100/v1")
    monkeypatch.setattr(settings, "asr_api_key", "")
    http(_FakePost(status_code=401, payload={"error": "bad key"}))
    with pytest.raises(AsrError) as got:
        await speech.transcribe(AUDIO, filename="x", mime="audio/webm")
    assert "HTTP" not in got.value.message
    assert got.value.message == "转写服务没有接受这段录音"
    assert "401" in got.value.hint


async def test_transcribe_rejects_a_body_without_text(monkeypatch, http) -> None:
    """有些兼容端点把结果放在别的字段里。读不懂就说读不懂，别回空串。"""
    monkeypatch.setattr(settings, "asr_base_url", "http://127.0.0.1:8100/v1")
    monkeypatch.setattr(settings, "asr_api_key", "")
    http(_FakePost(payload={"result": "明天去玄武湖"}))
    with pytest.raises(AsrError):
        await speech.transcribe(AUDIO, filename="x", mime="audio/webm")
