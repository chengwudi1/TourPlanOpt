"""OpenAI 兼容 chat/completions 的最小客户端（免费模型：智谱 GLM-4-Flash / 本地 Ollama）。

只承担「拿一段 prompt，换回一个 JSON 对象」这一件事，并且**永不抛异常**：调用方
（service.py）在拿到 ``None`` 时会退回规则解析。一个付费与否都无所谓的第三方接口
把整个请求打成 500，是这个功能最不该有的失败形态。

密钥的处理与 ``amap_web_key`` 同：只在进程内使用，不进响应、不进日志。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger("tourplan.assistant")

# 意图列表很小；给足余量只为了不被「模型先客套了一句」截断成半个 JSON。
MAX_TOKENS = 800
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})

_code_fence = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def is_local_endpoint(base_url: str) -> bool:
    """localhost 一律视为免鉴权：本地跑的服务不该逼用户再编一把密钥。"""
    host = (urlparse(base_url).hostname or "").lower()
    return host in LOCAL_HOSTS


def llm_ready() -> bool:
    """能不能调模型。本地端点（Ollama）不要求密钥，那才是真正零成本的一条路。"""
    return bool(settings.llm_api_key.strip()) or is_local_endpoint(settings.llm_base_url)


def endpoint_label() -> str:
    """给 /api/assistant/status 显示的端点标识：只有 host 和模型名，没有密钥。"""
    host = urlparse(settings.llm_base_url).hostname or settings.llm_base_url
    return f"{host} · {settings.llm_model}"


def extract_json(raw: str) -> dict[str, Any] | None:
    """从模型输出里挖出第一个 JSON 对象。

    兼容三种常见脏皮：```json 代码块、前后寒暄、以及被 max_tokens 截断的尾巴（截断的
    直接判失败，交给兜底 —— 半个意图清单比没有清单更危险）。
    """
    text = _code_fence.sub("", raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


class LlmClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=settings.llm_timeout_s)
        return self._client

    async def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        """一次非流式补全。失败一律返回 None，并留下一条不含密钥与全文的告警。"""
        if not llm_ready():
            return None
        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key.strip():
            headers["Authorization"] = f"Bearer {settings.llm_api_key.strip()}"
        payload = {
            "model": settings.llm_model,
            # 解析要的是稳定，不是文采。
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # GLM 等端点认这个；Ollama 会忽略未知字段。失败时下面仍有 extract_json 兜底。
        payload["response_format"] = {"type": "json_object"}

        try:
            resp = await self._http().post(url, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("模型请求失败（%s）：%s", type(exc).__name__, exc)
            return None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 端点千奇百怪，任何一种都不能掀翻请求
            logger.exception("模型请求异常")
            return None

        if resp.status_code != 200:
            # 只截 200 个字符：某些端点会把整份 prompt 回显在错误体里，日志不该收。
            logger.warning("模型返回 HTTP %s：%s", resp.status_code, resp.text[:200])
            return None

        try:
            body = resp.json()
        except ValueError:
            logger.warning("模型响应不是 JSON（%s 字节）", len(resp.content))
            return None

        content = _content_of(body)
        if content is None:
            logger.warning("模型响应缺少 choices/message.content：%s", str(body)[:200])
            return None
        return extract_json(content)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _content_of(body: object) -> str | None:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    # 少数兼容端点把 reasoning 和 content 拆开，或者只在 completion 字段里给文本。
    text = choices[0].get("text") if isinstance(choices[0], dict) else None
    return text if isinstance(text, str) else None


_client: LlmClient | None = None


def get_llm_client() -> LlmClient:
    global _client
    if _client is None:
        _client = LlmClient()
    return _client


async def close_llm_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
