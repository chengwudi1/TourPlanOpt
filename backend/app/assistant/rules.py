"""规则兜底解析器：没有密钥、没有网络、模型超时也能用的那一条路。

**它是兜底，不是主路。** 输出与 :mod:`app.assistant.intents` 同一种 ``Intent``，所以
下游（兑现、搜索、校验、确认卡）完全共用 —— 这条路的价值在于「永远不会把功能整个
弄丢」，不在于「什么话都能听懂」。它的策略是**减法**：把一个子句里已知的日期、时刻、
时长、触发词全剥掉，剩下的当地点名。所以长句、倒装、生僻说法会失败，失败了就返回空
列表，让上层如实说「没听懂」而不是硬凑一条指令。

宁可少认，不可错认：认错的代价是用户要点一次撤销，漏认的代价只是再打一遍字。
"""

from __future__ import annotations

import re

from app.assistant.context import clock_to_min, fmt_clock
from app.assistant.intents import Intent

_CONNECTORS = re.compile(r"然后|接着|随后|顺便|另外|此外|并且说")
# 小数点不是句点：「花了 88.5」被切一刀就只剩 88，钱数差 5 毛是账目上最不能忍的错。
_CLAUSES = re.compile(r"(?:[，。；！？,;!?]|\.(?![0-9])|\n)+")

# 剥掉的部分按「越具体越先」排列，见 _strip_noise。
_DAY_WORDS = re.compile(
    r"第\s*[一二两三四五六七八九十0-9]{1,3}\s*[天日]"
    r"|周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天]"
    r"|明天|后天|大后天|今天|这天|当天|最后一天|下一天|后一天"
    r"|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s*[月日]"
)
_CLOCK_WORDS = re.compile(
    r"(上午|下午|晚上|早上|中午|傍晚|凌晨)?\s*[0-9]{1,2}\s*[:：]\s*[0-9]{2}"
    r"|(上午|下午|晚上|早上|中午|傍晚|凌晨)\s*[0-9]{1,2}\s*点\s*(半|钟|整)?"
    r"|[0-9]{1,2}\s*点\s*(半|钟|整)?"
)
_DURATION_WORDS = re.compile(
    r"(玩|停留|逛|待|游玩|安排|预计|大概)?\s*"
    r"([0-9]+(?:\.[0-9]{1,2})?|[一二两三四五六七八九十半]{1,3})\s*(个)?\s*(小时|钟头|分钟)"
)
# 「一个半小时」：半个单位夹在量词和单位名称中间，上面那条按顺序拼不出来。
# 「半」以前根本不在时长词表里，于是「玄武湖玩半小时」整截被当地名送进确认卡。
# 前面那个动词也要一起吃掉，理由和上面那条一样——不吞的话「玄武湖玩」会留在名字里。
_AND_HALF = re.compile(
    r"(?:玩|停留|逛|待|游玩|安排|预计|大概)?\s*"
    r"([0-9一二两三四五六七八九十]{1,3})\s*个\s*半\s*(?:小时|钟头)"
)
_FILLER = re.compile(
    r"帮我|给我|麻烦|请|我们|我|你|把|将|再|也|都|就|要|去|到|一下|一个|个|吧|呢|啊|了"
)
_ADD_TRIGGERS = re.compile(r"添加|加入|新增|加上|加|想去|安排|记一下|列一下|排进|插进|增加到行程")
# 「不去了」以前是整条写死的，于是「明天不去中山陵了」一路掉到 place_add 分支（那句里
# 有个「去」字），最后报出一个叫「不中山陵」的地点——漏认只是让人再打一遍，
# 这种错认是要人点撤销的。删了/划掉同理：口语里没人说「删除」。
_REMOVE_TRIGGERS = re.compile(
    r"删除|删掉|删了|剔掉|拿掉|划掉|去掉|移除|取消|不要|(?<![得必需肯])不去|去不了|去不成"
)
# 元词指的是那张清单本身，不是清单里的某一条：「防晒霜的待办」内容是防晒霜。
_META_WORDS = re.compile(r"待办|待办事项|清单|备忘|事项")

_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT_VALUE = {"十": 10, "百": 100, "千": 1_000}


def _cn_number(token: str) -> float | None:
    """三百八 / 三千二百 / 十一 / 两万：中文数字读法。

    末尾那个孤立数字按口语省略规则升一级：「三百八」是 380 不是 308，「一千二」是 1200。
    只有前面出现过 百/千/万 才升，所以「十一」还是 11、「八」还是 8。
    """
    s = token.strip()
    if not s or any(c not in _CN_DIGIT and c not in _CN_UNIT_VALUE and c != "万" for c in s):
        return None
    total = 0
    cur: int | None = None
    last_unit = 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
            continue
        if ch == "万":
            total = (total + (cur if cur is not None else 1)) * 10_000
            cur, last_unit = None, 10_000
            continue
        unit = _CN_UNIT_VALUE[ch]
        total += (cur if cur is not None else 1) * unit
        cur, last_unit = None, unit
    if cur is not None:
        # 省略式：三百八 = 380。只在 百 以上才升一级，「十一」不在此列。
        total += cur * (last_unit // 10 if last_unit >= 100 else 1)
    return float(total)


# 钱数的数字部分。语音里没人会先补一个「元」字，所以中文数字也得能吃。
# 注意这是一个带顶层 `|` 的并联式：嵌进别处必须先用非捕获括号包住，否则拼接点两侧的
# 分支会被它劈开（「元」后缀会只挂在中文数字那一半上，裸数字就成了随时能认的金额）。
_AMOUNT_NUM = r"[0-9]+(?:\.[0-9]{1,2})?|[零一二两三四五六七八九十百千万]{1,6}"
_AMOUNT_ALT = "(?:" + _AMOUNT_NUM + ")"
_AMOUNT = re.compile(_AMOUNT_NUM)
# 紧跟量词的不是钱，是数量：「花了两晚」「买了3张」都要在这一步被挡回去。
_COUNTER_CHARS = "张间份人位只杯瓶夜晚次天日号"
_AMOUNT_TAIL = "(?!" + "[" + _COUNTER_CHARS + "])"
_COUNTER_WORDS = re.compile("[" + _COUNTER_CHARS + "]")

# 中文列举的分隔符。「和」两侧不一定有空格，「雨伞和充电宝」是三种里最常见的一种写法。
_ITEM_SEP = re.compile(r"[、,，]|和|与|加上|以及|还有")

_CATEGORY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ticket", re.compile(r"门票|车票|船票|票价|入园")),
    ("transport", re.compile(
        r"打车|出租|网约车|地铁|公交|高铁|火车|机票|动车|大巴|停车|加油|油费|过路|船费"
    )),
    ("lodging", re.compile(r"酒店|民宿|住宿|开房|房费|客栈|青旅")),
    ("food", re.compile(
        r"午饭|晚饭|早饭|早餐|午餐|晚餐|餐|吃|饭|面|咖啡|奶茶|火锅|烧烤|日料|小吃|饮料|水钱"
    )),
    ("shopping", re.compile(r"买|购物|纪念品|特产|伴手礼|衣服")),
    ("activity", re.compile(r"漂流|索道|演出|展览|项目|游乐|潜水|滑雪|导览|讲解|门票外的")),
)

_EXPENSE = re.compile(
    r"(?:¥|￥)\s*(" + _AMOUNT_ALT + r")"
    r"|" + _AMOUNT_ALT + r"\s*(?:元|块钱|块)"
    r"|(?:花了|花|付了|付|消费|交了|出了|吃了|喝了)\s*(" + _AMOUNT_ALT + r")" + _AMOUNT_TAIL
)
# 裸「带」不做触发词：「带女儿去玩」会往清单里塞一条「女儿去玩」。要有量词或
# 副词跟着（再带 / 带个 / 携带）才算在交代待办。长的写法排在前面，re 取最左分支。
_CHECKLIST_TRIG = re.compile(
    r"需要带|列个清单|别忘了|带一个|再带|还带|得带|需带|要带|带上|带个|带点|带些|携带"
    r"|备好|记得|别忘|待办|备忘|清单"
)
# 「勾掉」「办了」必须比 _CHECKLIST_TRIG 先判：否则「把防晒霜的待办勾掉」会新增一条
# 叫「防晒霜的待办勾掉」的清单 —— 漏认顶多让人再点一次，错认会往清单里塞垃圾。
_CHECKLIST_DONE = re.compile(
    r"(.{1,24}?)(?:已经|已|差不多)?"
    r"(带上了|带好了|准备好了|办好了|搞定了|弄好了|完成了|做好了|买好了|打上勾|打了勾"
    r"|订好了|勾掉|带了|买了|买过|勾了|办掉|办了|定了)"
)
_CHECKLIST_UNDO = re.compile(r"(.{1,24}?)(?:还没|没|尚未)(?:带|买|订|准备|完成)")
_OPTIMIZE = re.compile(r"优化|重排|顺一下|理顺|排一下|最顺路|最短|绕路|省时间|少走弯路")
# 「钉住明城墙」和「把明城墙钉住」都得认：中文里后一种更常说，只写前一种等于锁不上。
_LOCK_WORDS = r"钉住|锁定|固定|别动|不要动|不许动"
_UNLOCK_WORDS = r"解锁|放开|取消锁定|取消钉住|取消钉|取消固定|不用钉了"
_LOCK = re.compile(rf"({_LOCK_WORDS})(.{{0,24}})|(.{{1,24}}?)({_LOCK_WORDS})")
_UNLOCK = re.compile(rf"({_UNLOCK_WORDS})(.{{0,24}})|(.{{1,24}}?)({_UNLOCK_WORDS})")
_MOVE = re.compile(r"(.{1,24}?)(?:挪|移|调|放|换|搬)(?:到|去|至|进)(.{1,20})")
# 「把明城墙从第2天挪走」只说了来源，没有去处。认成 place_move 但 day 留空，
# 由兑现那一层追问「挪到哪一天」—— 静默挪到当前聚焦的那天是错认。
_MOVE_OUT = re.compile(r"(.{1,24}?)(?:从\s*.{0,12}?)?(?:挪|移|调|换|搬)(?:走|出去|移出|出来)")
# 只收复合词和句尾单字：「建设大厦」里有「设」，但不会以「设」结尾。
_EDIT_WORDS = re.compile(
    r"调整成|设置成|更改为|修改为|改成|改为|设为|定为|调成|换成|变成|称作|叫作"
    r"|设置|调整|更改|修改|[改调换设]$"
)
_RENAME = re.compile(r"(.{1,24}?)改名(?:为|成)?(.{1,30})")
_NOTE = re.compile(r"(.{1,24}?)备注(?:一下)?(?:为|成|[:：])?(.{1,60})")
_ADD_DAY = re.compile(
    r"(加|新增|来|补)(?:一)?(?:个)?新的一天|新增一天|再来一天|多一天"
    r"|(?:再|还|另外)?\s*加\s*一\s*[天日]"
    r"|加第([0-9]{1,2})天"
)
_BUDGET = re.compile(
    r"预算[^\d一二两三四五六七八九十百千万]{0,4}"
    r"(" + _AMOUNT_ALT + r")\s*(万|元|块|k|千)?"
)
# 「打车花了 88.5」里的「打车」是花销的描述，不是指令 —— 没有改的口吻就没有资格认成出行方式。
_MODE = re.compile(
    r"(?:出行方式|交通方式|方式|改成|改为|设为|设置成|换成|调成|定为|改用|改走|用|按|走)\s*"
    r"(步行|走路|citywalk|城市漫步|自驾|开车|打车|地铁|公交)",
    re.IGNORECASE,
)
_STATUS = re.compile(
    r"(?:状态)?(?:改|设|标记|置)(?:为|成)?(规划中|规划|已订|预订(?:完|好)?|进行中|已完成|完成|归档|已结束)"
)
_DAY_START = re.compile(
    r"(?:每天|各天|开始时间|出发时间)(?:改|设|定|调|安排)?(?:为|成|到)?(.{2,10})"
)
_TITLE = re.compile(r"(?:标题|名字|行程名)(?:改|设)?(?:为|成|[:：])?(.{1,40})")


def _num(token: str) -> float | None:
    token = token.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    if token == "半":
        # 「半小时」的 30 分钟从这里来，不是 `_CN_NUM` 里那个半。
        return 0.5
    return _cn_number(token)


def _duration_minutes(clause: str) -> int | None:
    match = _AND_HALF.search(clause)
    if match:
        whole = _num(match.group(1))
        return None if whole is None else int(round(whole * 60)) + 30
    match = _DURATION_WORDS.search(clause)
    if not match:
        return None
    value = _num(match.group(2))
    if value is None:
        return None
    unit = match.group(4)
    if unit in {"小时", "钟头"}:
        # 只说「半小时」时中文会把「个」吞掉，值本身已经是 0.5。
        return int(round(value * 60))
    return int(round(value))


def _clock(clause: str) -> int | None:
    match = _CLOCK_WORDS.search(clause)
    return clock_to_min(match.group(0)) if match else None


def _day_ref(clause: str) -> str:
    match = _DAY_WORDS.search(clause)
    return match.group(0).strip() if match else ""


def _category(clause: str) -> str:
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(clause):
            return category
    return "other"


def _strip_noise(clause: str) -> str:
    """剥掉一切已知修饰，剩下的就是地名/条目名。"""
    rest = _DAY_WORDS.sub("", clause)
    rest = _CLOCK_WORDS.sub("", rest)
    # 先剥「一个半小时」：`_DURATION_WORDS` 按顺序拼不出这种半截夹在中间的写法。
    rest = _AND_HALF.sub("", rest)
    rest = _DURATION_WORDS.sub("", rest)
    # 「玄武湖改成玩90分钟」剥完时长还剩「玄武湖改成」。这截尾巴不清掉就匹配不上
    # 行程里的既有地点，于是一次时长编辑会掉进最后的分支变成一颗重复钉。
    rest = _EDIT_WORDS.sub("", rest)
    rest = _ADD_TRIGGERS.sub("", rest)
    rest = _META_WORDS.sub("", rest)
    rest = _FILLER.sub("", rest)
    return re.sub(r"\s+", "", rest).strip("，,。.:：、的")


def _amount(clause: str) -> float | None:
    match = _EXPENSE.search(clause)
    if match:
        for group in match.groups():
            if group:
                value = _num(group)
                if value is not None:
                    return value
        # 「240元」这一支不带捕获组：钱数还在句子里，扫回来。
        digits = _AMOUNT.search(clause)
        return _num(digits.group(0)) if digits else None
    return _bare_amount(clause)


def _bare_amount(clause: str) -> float | None:
    """「住宿一千二」「门票240」：说了花钱的类目，没说「花了」也没说「元」。

    这笔账只在四个数字都无主的时候才认——时长、时刻、日期、数量各自先认领自己的数：
    「玩90分钟」「下午3点」「第2天」「买3张门票」。把那些数记成金额，比漏掉这条贵得多。
    """
    if _DURATION_WORDS.search(clause) or _AND_HALF.search(clause):
        return None
    if _CLOCK_WORDS.search(clause) or _DAY_WORDS.search(clause):
        return None
    if _COUNTER_WORDS.search(clause) or _category(clause) == "other":
        return None
    digits = _AMOUNT.search(clause)
    return _num(digits.group(0)) if digits else None


def parse_rules(text: str, existing_places: list[str] | None = None) -> list[Intent]:
    """一句自然中文 → 意图列表。认不出来就返回空，绝不硬凑。

    ``existing_places`` 是当前行程里已有的地点名：规则没有语义，只能靠「这个名字是否
    已在行程里」来区分 ``place_update`` 与 ``place_add``。
    """
    known = list(existing_places or [])
    intents: list[Intent] = []
    for chunk in _CONNECTORS.split(text or ""):
        for clause in _CLAUSES.split(chunk):
            cleaned = clause.strip()
            if len(cleaned) >= 2:
                intent = _parse_clause(cleaned, known)
                if intent is not None:
                    intents.append(intent)
    return intents


def _parse_clause(clause: str, existing: list[str]) -> Intent | None:  # noqa: C901 - 一条条排队的规则表
    # 待办的三种动作先判：它们的触发词最硬，误判代价也最低。
    if (m := _CHECKLIST_DONE.search(clause)) and not _REMOVE_TRIGGERS.search(clause):
        item = _strip_noise(m.group(1))
        if item:
            return Intent(kind="checklist_update", item=item, done=True)
    if (m := _CHECKLIST_UNDO.search(clause)):
        item = _strip_noise(m.group(1))
        if item:
            return Intent(kind="checklist_update", item=item, done=False)
    if _REMOVE_TRIGGERS.search(clause) and re.search(r"待办|清单|备忘", clause):
        item = _strip_noise(_REMOVE_TRIGGERS.sub("", re.sub(r"待办|清单|备忘", "", clause)))
        if item:
            return Intent(kind="checklist_delete", item=item)
    if _CHECKLIST_TRIG.search(clause):
        body = _CHECKLIST_TRIG.sub("", clause)
        body = re.sub(r"^[:：，,带拿需要]{0,4}", "", body)
        items = [
            _strip_noise(piece)
            for piece in _ITEM_SEP.split(body)
            if len(_strip_noise(piece)) >= 1
        ]
        items = [i for i in items if i]
        if items:
            return Intent(kind="checklist_add", texts=items)

    if _OPTIMIZE.search(clause):
        return Intent(kind="run_optimize", day=_day_ref(clause))

    if (m := _BUDGET.search(clause)):
        value = _num(m.group(1))
        if value is not None:
            if m.group(2) in {"万", "k"}:
                value *= 10_000
            if m.group(2) == "千":
                value *= 1_000
            return Intent(kind="trip_update", budget_yuan=value)

    if (m := _STATUS.search(clause)):
        word = m.group(1)
        status = {
            "规划": "planning", "规划中": "planning", "已订": "booked", "预订完": "booked",
            "预订好": "booked", "进行中": "ongoing", "已完成": "done", "完成": "done",
            "已结束": "done", "归档": "archived",
        }.get(word, "planning")
        return Intent(kind="trip_update", status=status)  # type: ignore[arg-type]

    if (m := _MODE.search(clause)):
        walking = m.group(1) in {"步行", "走路", "citywalk", "城市漫步"}
        return Intent(kind="trip_update", travel_mode="walking" if walking else "driving")

    if (m := _DAY_START.search(clause)):
        minutes = _clock(m.group(1)) or _clock(clause)
        if minutes is not None:
            return Intent(kind="trip_update", day_start_min=minutes)

    if (m := _TITLE.search(clause)):
        title = _strip_noise(m.group(1))
        if title:
            return Intent(kind="trip_update", title=title)

    # 先判解锁：「取消钉住明城墙」里也含「钉住」，反过来判会把它钉上。
    if (m := _UNLOCK.search(clause)):
        place = _strip_noise(m.group(2) or m.group(3) or "")
        if place:
            return Intent(kind="place_lock", place=place, locked=False)
    if (m := _LOCK.search(clause)):
        place = _strip_noise(m.group(2) or m.group(3) or "")
        if place:
            return Intent(kind="place_lock", place=place, locked=True)

    if (m := _ADD_DAY.search(clause)):
        return Intent(kind="day_add", date=_day_ref(clause) if re.search(r"\d{4}-", clause) else "")

    if (m := _RENAME.search(clause)):
        place, name = _strip_noise(m.group(1)), _strip_noise(m.group(2))
        if place and name:
            return Intent(kind="place_update", place=place, name=name)

    if (m := _NOTE.search(clause)):
        place, note = _strip_noise(m.group(1)), m.group(2).strip()
        if place and note:
            return Intent(kind="place_update", place=place, note=note[:200])

    if (m := _MOVE.search(clause)):
        place, day = _strip_noise(m.group(1)), _day_ref(m.group(2)) or m.group(2).strip()
        if place and day:
            return Intent(kind="place_move", place=place, day=day)

    if (m := _MOVE_OUT.search(clause)):
        place = _strip_noise(m.group(1))
        if len(place) >= 2:
            return Intent(kind="place_move", place=place)

    if amount := _amount(clause):
        # 钱数不进标题：`_EXPENSE.sub` 只能剥有「花了/元」包着的那一种，
        # 「住宿一千二」这种裸数字得靠 _AMOUNT 自己扫掉。
        title = _strip_noise(_AMOUNT.sub("", _EXPENSE.sub("", clause)))
        return Intent(
            kind="expense_add",
            title=title[:80],
            amount_yuan=amount,
            category=_category(clause),
        )

    if _REMOVE_TRIGGERS.search(clause):
        place = _strip_noise(_REMOVE_TRIGGERS.sub("", clause))
        if len(place) >= 2:
            return Intent(kind="place_delete", place=place)

    duration = _duration_minutes(clause)
    clock = _clock(clause)
    place = _strip_noise(clause)
    if len(place) < 2:
        return None
    explicit_add = bool(_ADD_TRIGGERS.search(clause))
    known = _known_place(place, existing)
    if known is not None and not explicit_add and (duration is not None or clock is not None):
        return Intent(
            kind="place_update", place=known, duration_min=duration,
            start_clock=fmt_clock(clock) if clock is not None else "",
        )
    if explicit_add or re.search(r"(去|到|玩|想)", clause):
        return Intent(
            kind="place_add",
            name=place[:120],
            day=_day_ref(clause),
            duration_min=duration,
            start_clock=fmt_clock(clock) if clock is not None else "",
        )
    return None


def _known_place(query: str, existing: list[str]) -> str | None:
    """规则解析分不清「改这个地点」还是「再加一个同名的」，所以只认已存在的确切名字。"""
    norm = re.sub(r"[\s·・()（）]+", "", query).lower()
    if not norm:
        return None
    for name in existing:
        target = re.sub(r"[\s·・()（）]+", "", name).lower()
        if target == norm or (len(norm) >= 2 and (norm in target or target in norm)):
            return name
    return None
