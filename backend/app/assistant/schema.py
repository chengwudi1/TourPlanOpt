"""自然语言 → 结构化指令（M28）。

这个包只做一件事：把一句话翻译成一份**候选指令清单**，交给前端逐条确认后走既有的
WebSocket op 落地。它不写库、不发 op、不消耗 seq —— 这是硬约束，谁改谁负责。

两层 schema 是刻意分开的：

* :mod:`app.assistant.intents` —— 模型输出的**松散意图**。地点写名字、天写「第2天/
  周六/next」，因为模型不该被信任持有真实 id 和经纬度。
* 本模块 —— 回给前端的**严格指令**。id 已经过快照校验，坐标全部来自高德搜索，
  数字全部 clamp 过。模型幻觉在这一层被挡掉，挡不掉的降级成一条 warning。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.config import settings

# -- 请求 ---------------------------------------------------------------------------


class AssistantRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000)
    # 前端当前打开/聚焦的那一天。相对引用（「这天」「再加一个」）在没有别的线索时落到它。
    day_id: str | None = None
    # 同一句话的多轮补全：前端把上一次 ask 回来的问题原样带回来即可，服务端不存会话。
    history: list[str] = Field(default_factory=list, max_length=6)


# -- 指令（前端逐条确认，确认后走既有 op）------------------------------------------------


class _Action(BaseModel):
    """所有指令共用的人话标签。确认卡直接显示它，不再自己拼句子。"""

    label: str = ""


class PlaceAdd(_Action):
    kind: Literal["place_add"] = "place_add"
    day_id: str
    name: str
    lng: float
    lat: float
    address: str = ""
    amap_poi_id: str = ""
    photo_url: str = ""
    duration_min: int = 60
    note: str = ""
    after_place_id: str | None = None


class PlaceMove(_Action):
    kind: Literal["place_move"] = "place_move"
    place_id: str
    name: str
    day_id: str
    from_day_id: str


class PlaceUpdate(_Action):
    kind: Literal["place_update"] = "place_update"
    place_id: str
    name: str
    # 只放真正要改的键：duration_min / name / note。时长与备注不影响锁定，
    # 但 start_min 会把地点钉出自动优化范围，所以本接口只读不写它。
    patch: dict = Field(default_factory=dict)


class PlaceDelete(_Action):
    kind: Literal["place_delete"] = "place_delete"
    place_id: str
    name: str
    day_id: str
    destructive: Literal[True] = True


class PlaceLock(_Action):
    kind: Literal["place_lock"] = "place_lock"
    place_id: str
    name: str
    locked: bool
    # 解锁会连带清掉手填时间（那是求解器眼里的锚点），必须让人看见。
    clears_time: bool = False


class DayAdd(_Action):
    kind: Literal["day_add"] = "day_add"
    title: str = ""
    date: str | None = None


class ChecklistAdd(_Action):
    kind: Literal["checklist_add"] = "checklist_add"
    texts: list[str] = Field(default_factory=list)


class ChecklistUpdate(_Action):
    kind: Literal["checklist_update"] = "checklist_update"
    item_id: str
    text: str
    patch: dict = Field(default_factory=dict)


class ChecklistDelete(_Action):
    kind: Literal["checklist_delete"] = "checklist_delete"
    item_id: str
    text: str
    destructive: Literal[True] = True


class ExpenseAdd(_Action):
    kind: Literal["expense_add"] = "expense_add"
    title: str
    amount_cents: int
    category: str = "other"


class TripUpdate(_Action):
    kind: Literal["trip_update"] = "trip_update"
    patch: dict = Field(default_factory=dict)


class RunOptimize(_Action):
    """让前端去调既有的 optimize 接口。解析接口自己不碰优化。"""

    kind: Literal["run_optimize"] = "run_optimize"
    day_id: str
    day_title: str = ""


Action = Annotated[
    PlaceAdd
    | PlaceMove
    | PlaceUpdate
    | PlaceDelete
    | PlaceLock
    | DayAdd
    | ChecklistAdd
    | ChecklistUpdate
    | ChecklistDelete
    | ExpenseAdd
    | TripUpdate
    | RunOptimize,
    Field(discriminator="kind"),
]

Mood = Literal["idle", "thinking", "happy", "asking", "warn"]


class AssistantReply(BaseModel):
    """一次解析的结果。

    ``actions`` 是候选清单：前端逐条确认后才执行，**没有「整批自动落地」这条路径**。
    ``questions`` 非空时精灵该追问而不是动手；``warnings`` 是需要让人看见的降级说明
    （没搜到地点、名字有歧义、模型不可用已退回规则解析等）。
    """

    reply: str = ""
    mood: Mood = "idle"
    # llm = 模型解析；rules = 规则解析（未配密钥，或模型超时/输出不可用之后的兜底）。
    engine: Literal["llm", "rules"] = "rules"
    actions: list[Action] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AssistantStatus(BaseModel):
    """前端据此决定精灵气泡里那句「AI 未配置」要不要说。密钥只报有无，绝不回显。"""

    llm_ready: bool
    model: str
    endpoint: str
    max_chars: int = settings.assistant_max_chars
