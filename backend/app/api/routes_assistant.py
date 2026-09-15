"""助手接口：一句话换一份候选指令清单。

三条不可让的边界，写在这里而不是散落在实现里：

1. **只读**。快照读进来、指令发出去，全程没有一次写库、没有一条 op、没有一次
   ``next_seq``。落地是前端确认后 WebSocket 的事（见 app/ws/ops.py）。
2. **模型密钥不出进程**。响应里只有 ``llm_ready`` 这种布尔量，与 ``amap_web_key`` 同
   一条纪律（routes_config.py 是另一处例子）。
3. **它坏的时候不能像坏了**。任何内部异常都降级成一条带 warning 的正常响应 —— 助手
   是增强项，一个红色报错会让人以为整趟行程都挂了。但异常一定 ``logger.exception``，
   不吞现场。

限流的存在还有一个更实际的理由：前端的输入框只要 debounce 写错，就会把这里当成
按键回调打，每击一次烧一次模型配额和一次高德搜索。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request, status

from app.assistant.llm import endpoint_label, llm_ready
from app.assistant.schema import AssistantReply, AssistantRequest, AssistantStatus
from app.assistant.service import parse_utterance
from app.config import settings
from app.db.database import get_db
from app.db.repositories import get_snapshot

logger = logging.getLogger("tourplan.assistant")

router = APIRouter(prefix="/api", tags=["assistant"])

# 每个行程一个滑动窗口。10 次/分钟够真人说话，挡得住按键级风暴。
_RATE_CALLS = 10
_RATE_WINDOW_S = 60.0
_buckets: dict[str, list[float]] = {}


def _allow(trip_id: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _buckets.get(trip_id, []) if now - t < _RATE_WINDOW_S]
    if len(hits) >= _RATE_CALLS:
        _buckets[trip_id] = hits
        return False
    hits.append(now)
    _buckets[trip_id] = hits
    if len(_buckets) > 500:
        # 窗口里已经没有记录的行程直接删掉：这个桶是进程内的，不能只增不减。
        for stale in [k for k, v in _buckets.items() if now - v[-1] >= _RATE_WINDOW_S]:
            _buckets.pop(stale, None)
    return True


@router.get("/assistant/status", response_model=AssistantStatus)
async def assistant_status() -> AssistantStatus:
    """前端据此决定精灵的第一句话怎么说。不联网、不查库，可以随便调。"""
    return AssistantStatus(
        llm_ready=llm_ready(),
        model=settings.llm_model,
        endpoint=endpoint_label(),
    )


@router.post("/trips/{trip_id}/assistant/parse", response_model=AssistantReply)
async def assistant_parse(trip_id: str, body: AssistantRequest, request: Request) -> AssistantReply:
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "要说点什么才能解析")
    if not _allow(trip_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"说话太快了，每个行程每分钟最多 {_RATE_CALLS} 次解析",
        )

    snapshot = await get_snapshot(get_db(), trip_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"没有行程 {trip_id}")

    focus = body.day_id if body.day_id in {d.id for d in snapshot.days} else None
    history = [h.strip()[:200] for h in body.history if h and h.strip()][-3:]

    started = time.monotonic()
    try:
        reply = await parse_utterance(snapshot, text, focus, history)
    except Exception:  # noqa: BLE001 - 见模块头第 3 条
        logger.exception("助手解析失败 trip=%s", trip_id)
        return AssistantReply(
            reply="解析服务出了点问题，先用面板操作",
            mood="warn",
            engine="rules",
            warnings=["这一句没有解析成功，行程没有被改动"],
        )
    logger.info(
        "助手解析 %s 条/%s engine=%s %.0fms %s",
        len(reply.actions),
        len(reply.warnings) + len(reply.questions),
        reply.engine,
        (time.monotonic() - started) * 1000,
        request.url.path,
    )
    return reply
