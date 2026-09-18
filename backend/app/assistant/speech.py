"""OpenAI 兼容 ``/audio/transcriptions`` 的最小客户端（语音输入的第二条路）。

为什么要有第二条路：Chrome 的 Web Speech 没有可配置的服务端点，音频只能发到境外那台
识别服务；本机网络到不了那台服务，话筒就永远停在 ``network`` 这一发错误上，用户端一点
补救办法都没有。把转写搬到后端之后，端点由 ``ASR_BASE_URL`` 决定——本地 whisper.cpp
（不出本机、零成本）或国内云的 OpenAI 兼容接口，两条都到得了。

与 ``llm.py`` 的差别只有一处：模型解析失败可以退回规则，转写失败没有可退的地方，所以这里
抛 :class:`AsrError`，由路由换成一条人话报错。**不返回空串**——空串会被前端读成
「没听清」，把服务没配好说成用户没说清，是撒谎。

密钥的处理与 ``amap_web_key`` / ``llm_api_key`` 同一条纪律：只在进程内使用，不进响应、
不进日志（上游的错误体也只截断写进日志，不回显给浏览器）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx

from app.assistant.llm import is_local_endpoint
from app.config import settings

logger = logging.getLogger("tourplan.assistant")

# multipart 里的 filename 会被拼进头部，只留安全字符；认不出来就用兜底名。
_safe_name = re.compile(r"[^A-Za-z0-9._-]")
# 客户端填的 filename 可以带整条路径，Windows 端还是反斜杠，所以先取最后一段。
_last_segment = re.compile(r".*[\\/]", re.DOTALL)

_EXT_BY_MIME = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
}


class AsrError(Exception):
    """一条能直接给人看的失败：message 说清发生了什么，hint 说清下一步做什么。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def asr_enabled() -> bool:
    """配了端点就算启用（至于通不通，等真发请求时再说）。"""
    return bool(settings.asr_base_url.strip())


def asr_ready() -> bool:
    """能不能调：要有端点，而且要么给了密钥、要么是本地服务（本地服务不逼用户编密钥）。"""
    base = settings.asr_base_url.strip()
    if not base:
        return False
    return bool(settings.asr_api_key.strip()) or is_local_endpoint(base)


def asr_label() -> str:
    """给 /api/assistant/status 与启动日志看的标识：只有 host 和模型名，没有密钥。"""
    base = settings.asr_base_url.strip()
    if not base:
        return "未配置（退回浏览器识别）"
    host = urlparse(base).hostname or base
    return f"{host} · {settings.asr_model}"


def _filename_of(raw: str, mime: str) -> str:
    stem = _safe_name.sub("", _last_segment.sub("", raw or "", count=1))[:40]
    if "." not in stem:
        stem = f"speech.{_EXT_BY_MIME.get(mime, 'bin')}"
    return stem


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.asr_timeout_s)
    return _client


async def close_asr_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def transcribe(blob: bytes, *, filename: str, mime: str) -> str:
    """一段录音换一行文字。失败一律抛 :class:`AsrError`，绝不静默返回空串。"""
    if not asr_ready():
        base = settings.asr_base_url.strip()
        if not base:
            raise AsrError(
                "服务端没有启用语音识别",
                "在 backend/.env 填 ASR_BASE_URL（本地 whisper.cpp 或国内云的 OpenAI 兼容端点）。",
            )
        raise AsrError(
            "语音识别缺密钥",
            f"{urlparse(base).hostname or base} 不是本地端点，需要填 ASR_API_KEY。",
        )

    url = settings.asr_base_url.rstrip("/") + "/audio/transcriptions"
    data: dict[str, str] = {"model": settings.asr_model}
    if settings.asr_language.strip():
        data["language"] = settings.asr_language.strip()
    headers = {}
    if settings.asr_api_key.strip():
        headers["Authorization"] = f"Bearer {settings.asr_api_key.strip()}"

    part = (
        _filename_of(filename, mime),
        blob,
        mime or "application/octet-stream",
    )
    try:
        resp = await _http().post(url, headers=headers, data=data, files={"file": part})
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        host = urlparse(settings.asr_base_url).hostname or settings.asr_base_url
        raise AsrError("转写服务没有应答", f"确认 {host} 还在跑（{type(exc).__name__}）。") from exc
    except asyncio.CancelledError:
        raise

    if resp.status_code != 200:
        # 只截 200 个字符写日志：有些端点会把整份请求头回显在错误体里，密钥不能顺着日志出门。
        logger.warning("转写返回 HTTP %s：%s", resp.status_code, resp.text[:200])
        raise AsrError(
            "转写服务没有接受这段录音",
            f"后端日志里那条转写告警写了状态码 HTTP {resp.status_code}。",
        )

    try:
        body = resp.json()
    except ValueError:
        logger.warning("转写响应不是 JSON（%s 字节）", len(resp.content))
        raise AsrError(
            "转写服务返回的内容读不懂",
            "确认这个端点支持 OpenAI 的 /audio/transcriptions。",
        ) from None

    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        logger.warning("转写响应缺少 text 字段：%s", str(body)[:200])
        raise AsrError("转写服务没有给出文字", "确认模型名与音频格式（webm/opus 通常可用）。")
    return text.strip()
