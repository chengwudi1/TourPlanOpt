"""一次解析的编排：模型优先，规则兜底，两路都汇到同一个兑现器。

引擎选择的唯一判据是「有没有拿到可用的意图」，不是「模型是否配了」。模型没配、超时、
返回半个 JSON、返回一个不符合契约的对象，四种情况都会安静地落到规则解析上，并在响应里
如实标 ``engine=rules`` —— 前端据此告诉用户这次是本地规则的结果，别把「没听懂」算到
模型头上。
"""

from __future__ import annotations

import logging

from app.assistant.apply import apply_intents
from app.assistant.context import build_context
from app.assistant.intents import SYSTEM_PROMPT, Intent, IntentList, build_user_prompt
from app.assistant.llm import get_llm_client, llm_ready
from app.assistant.resolve import Resolver
from app.assistant.rules import parse_rules
from app.assistant.schema import AssistantReply
from app.config import settings
from app.models.domain import Snapshot

logger = logging.getLogger("tourplan.assistant")

_NOISE = "这句话没有被识别为可执行的改动"


async def parse_utterance(
    snapshot: Snapshot,
    text: str,
    focus_day_id: str | None = None,
    history: list[str] | None = None,
) -> AssistantReply:
    """一句话 → 候选指令清单。只读，绝不落库、绝不发 op、绝不消耗 seq。"""
    utterance = (text or "").strip()[: settings.assistant_max_chars]

    intents, engine = await _collect_intents(snapshot, utterance, focus_day_id, history or [])
    if not intents:
        # 两路都空：模型/规则谁在场就如实说谁，别让人以为服务坏了。
        return AssistantReply(
            reply=_NOISE if engine == "rules" else "这句话没有识别出改动，换一种说法或用面板操作",
            mood="asking",
            engine=engine,
        )

    resolver = Resolver(snapshot, focus_day_id)
    resolved = await apply_intents(resolver, intents)
    reply = AssistantReply(
        engine=engine,
        actions=resolved.actions,  # type: ignore[arg-type]
        warnings=resolved.warnings,
        questions=resolved.questions,
    )
    reply.reply = _summarize(reply)
    reply.mood = _mood_of(reply)
    return reply


async def _collect_intents(
    snapshot: Snapshot,
    utterance: str,
    focus_day_id: str | None,
    history: list[str],
) -> tuple[list[Intent], str]:
    """(意图列表, 实际出力的引擎)。模型在场但没认出任何东西时，规则还有一次机会。"""
    if not utterance:
        return [], "rules"
    if llm_ready():
        try:
            raw = await get_llm_client().chat_json(
                SYSTEM_PROMPT,
                build_user_prompt(build_context(snapshot, focus_day_id), utterance, history),
            )
            if raw is not None:
                parsed = IntentList.model_validate(raw).intents
                if parsed:
                    return parsed, "llm"
        except Exception:
            # 含 ValidationError：模型契约违约和端点炸机是同一种病 —— 都该由规则接手，
            # 而不是把整次请求打成 500 的兜底回复。
            logger.exception("模型解析不可用，退回规则解析")
    return (
        parse_rules(utterance, [p.name for p in snapshot.places]),
        "rules",
    )


def _summarize(reply: AssistantReply) -> str:
    """上界面的那一句。全部由模板生成，模型的话一个字都不参与 —— 文案口吻只有一个人管。"""
    count = len(reply.actions)
    if count == 0:
        if reply.questions:
            return "需要先确认：" + reply.questions[0]
        return "没有生成可执行的改动" + ("：" + reply.warnings[0] if reply.warnings else "")
    destructive = sum(1 for a in reply.actions if getattr(a, "destructive", False))
    head = f"识别出 {count} 项改动"
    if destructive:
        head += f"，其中 {destructive} 项为删除"
    return head + "，需逐条确认"


def _mood_of(reply: AssistantReply) -> str:
    if reply.questions or not reply.actions:
        return "asking"
    if reply.warnings:
        return "warn"
    return "happy"
