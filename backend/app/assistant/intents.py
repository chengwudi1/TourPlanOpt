"""模型的输出契约：一份**松散意图**列表。

这里刻意不要求模型给出 id 或经纬度。免费小模型会在长上下文里编造 id、把「玄武湖」
的坐标猜成一个合理数字，两种错都会静默落地成一条脏数据。所以：

* 地点、天、清单条目一律用**名字/序数**引用，由 :mod:`app.assistant.resolve` 拿快照
  和高德搜索结果去兑现；兑现不了的降级成一条 warning，绝不猜。
* 这个模型**没有任何一个字段会原样显示给用户**。所有上界面的句子由服务端模板生成
  （见 resolve.py）。既挡住了提示注入顺着模型的话上界面，也让文案口吻保持在 M24
  定的那一套里 —— 模型的自由发挥不参与。

``extra="ignore"`` 是必需的：小模型会自作主张多写 ``reason``、``confidence`` 之类
的键，这些一律丢掉而不是报 422。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IntentKind = Literal[
    "place_add",
    "place_move",
    "place_update",
    "place_delete",
    "place_lock",
    "day_add",
    "checklist_add",
    "checklist_update",
    "checklist_delete",
    "expense_add",
    "trip_update",
    "run_optimize",
]


class Intent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: IntentKind

    # -- 已有对象的引用：只收名字，never id ------------------------------------------------
    place: str = Field(default="", max_length=120)
    item: str = Field(default="", max_length=120)

    # -- 目标天：'1' / 'D1' / 'next' / '+1' / '周六' / '2026-10-03' / '这天' ---------------
    day: str = Field(default="", max_length=40)

    name: str = Field(default="", max_length=120)
    after: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=200)
    duration_min: int | None = Field(default=None, ge=0, le=24 * 60)
    # 用户嘴里的到达时刻（"09:00" 或 "上午9点"都行）。服务端只降级成备注：写进
    # start_min 等于把地点钉出自动优化范围，这个代价不该由一句话来付。
    start_clock: str = Field(default="", max_length=12)
    locked: bool | None = None
    done: bool | None = None

    texts: list[str] = Field(default_factory=list, max_length=40)

    title: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=40)
    date: str = Field(default="", max_length=10)
    travel_mode: Literal["driving", "walking"] | None = None
    day_start_min: int | None = Field(default=None, ge=0, le=24 * 60)
    status: Literal["planning", "booked", "ongoing", "done", "archived"] | None = None

    amount_yuan: float | None = Field(default=None, ge=0, le=10_000_000)
    budget_yuan: float | None = Field(default=None, ge=0, le=100_000_000)
    category: str = Field(default="", max_length=20)


class IntentList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intents: list[Intent] = Field(default_factory=list, max_length=40)


SYSTEM_PROMPT = """\
你是行程协同工具里的解析器。把用户的中文指令拆成结构化意图，只输出 JSON，不要解释、不要代码块。

意图对象的字段：
- kind 取值：place_add 加地点 / place_move 把地点移到另一天 / place_update 改地点的时长或备注 / \
place_delete 删地点 / place_lock 钉住或解锁地点 / day_add 新加一天 / checklist_add 加待办 / \
checklist_update 勾选或取消勾选待办 / checklist_delete 删待办 / expense_add 记一笔开销 / \
trip_update 改行程本身的字段 / run_optimize 重排某一天的顺序
- place：被操作的地点名，必须照抄下面「行程现状」里出现过的名字
- day：目标天。写序数数字（第3天→"3"）、"next"（最后一天之后）、"+1"（当前天的后一天）、\
"这天"（当前天）、星期（"周六"）、或完整日期 "YYYY-MM-DD"
- name：新增地点的名字，或改名后的名字；用户只说「去吃日料」时 name 填能拿去搜索的地点关键词
- after：把新地点排在哪个地点之后（照抄已有地点名）
- duration_min：游玩时长，整数分钟（"两小时"→120，"半小时"→30）
- start_clock：用户顺带提到的到达时刻，标准化成 "HH:MM"（24 小时制）。没提到达时刻就省略
- texts：一次新增的多条待办，字符串数组
- item：被操作的待办条目名，照抄现状里已有的条目
- done：true 勾选完成，false 取消勾选
- locked：true 钉住（不参与自动优化），false 解锁
- title/city/date：day_add 或 trip_update 时的新标题/城市/日期 "YYYY-MM-DD"
- travel_mode：driving 自驾打车 / walking 步行地铁；day_start_min：每天开始时刻，整数分钟
- status：planning 规划中 / booked 已订 / ongoing 进行中 / done 已完成 / archived 已归档
- amount_yuan：金额，单位元，数字；budget_yuan：预算，单位元
- category：ticket 门票 / transport 交通 / food 餐饮 / lodging 住宿 / shopping 购物 / \
activity 活动 / other 其他

规则：
1. 只拆用户明确说出的动作，不要替他补安排，不要推荐地点。
2. 一句话里有几个动作就给几个意图，保持先后顺序。
3. 不确定的字段留空或省略，不要编造时长、日期、金额。
4. 现状里查不到的地点名照样放进 place/name，由服务端去搜索核对。
5. 表达疑问、闲聊、或缺少必要信息时返回 {"intents":[]}。

输出格式：{"intents": [{"kind": "place_add", "name": "玄武湖", "day": "1", "duration_min": 120}]}
"""

EXAMPLES = """\
现状：
城市 南京，第1天 2026-10-01：鸡鸣寺(1小时)、明城墙(1.5小时)
第2天 2026-10-02：(空)
待办：身份证、充电宝
当前天：第1天

指令：周六上午加个玄武湖，骑车环湖两小时，然后去鸡鸣寺隔壁的阅江楼
{"intents": [{"kind": "place_add", "name": "玄武湖", "day": "周六", "duration_min": 120, \
"note": "骑车环湖"}, {"kind": "place_add", "name": "阅江楼", "after": "鸡鸣寺"}]}

指令：把明城墙挪到第二天，玩的时间改成两小时
{"intents": [{"kind": "place_move", "place": "明城墙", "day": "2"}, \
{"kind": "place_update", "place": "明城墙", "duration_min": 120}]}

指令：记得带雨伞、驱蚊液和充电宝
{"intents": [{"kind": "checklist_add", "texts": ["雨伞", "驱蚊液", "充电宝"]}]}

指令：门票一共花了 240，两个人平摊
{"intents": [{"kind": "expense_add", "title": "门票", "amount_yuan": 240, "category": "ticket"}]}

指令：预算改到三千，第二天帮我顺一下路
{"intents": [{"kind": "trip_update", "budget_yuan": 3000}, \
{"kind": "run_optimize", "day": "2"}]}
"""


def build_user_prompt(context: str, text: str, history: list[str]) -> str:
    """示例在前、真实指令在尾。

    例子放在 user 段而不是多条 message：部分兼容端点（本地 Ollama 的小模型）对多轮
    system/user 交替的遵循度明显低于单轮，而这一调用本来就没有对话状态。
    EXAMPLES 自带一份虚构「现状」，所以必须显式标成示例，否则模型会把南京当成输入。
    """
    parts = ["下列为示例，其中的地点与当前请求无关：", EXAMPLES, "下列为真实请求："]
    if history:
        parts.append("此前几条指令：" + " / ".join(history[-3:]))
    parts.append(f"现状：\n{context}")
    parts.append(f"指令：{text}")
    return "\n\n".join(parts)
