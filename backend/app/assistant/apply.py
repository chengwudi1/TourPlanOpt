"""意图 → 指令的兑现：resolve.py 那四条纪律的执行处。

``apply_intents`` 是唯一入口。它只**产出候选**，不执行任何东西：每一条指令都要在确认
卡上被人点一次才会变成 WebSocket op。
"""

from __future__ import annotations

import logging
import re

from app.assistant.context import clock_to_min, day_number, fmt_clock, fmt_duration
from app.assistant.intents import Intent
from app.assistant.resolve import (
    EXPENSE_CATEGORIES,
    PLACE_PATCH_KEYS,
    TRAVEL_MODES,
    TRIP_STATUSES,
    Resolved,
    Resolver,
    parse_iso,
)
from app.assistant.schema import (
    ChecklistAdd,
    ChecklistDelete,
    ChecklistUpdate,
    DayAdd,
    ExpenseAdd,
    PlaceAdd,
    PlaceDelete,
    PlaceLock,
    PlaceMove,
    PlaceUpdate,
    RunOptimize,
    TripUpdate,
)
from app.config import settings
from app.models.domain import Snapshot

logger = logging.getLogger("tourplan.assistant")

_TIME_NOTE = "手填时刻会把地点钉出自动优化范围，本助手只把它记进备注；需要精确时刻请在时间轨上拖动"

_CATEGORY_LABELS = {
    "ticket": "门票",
    "transport": "交通",
    "food": "餐饮",
    "lodging": "住宿",
    "shopping": "购物",
    "activity": "活动",
    "other": "其他",
}

# 确认卡上不许出现 planning / walking 这类枚举值，所以解析层与库里的写法都收在这里。
_TRIP_STATUS_LABELS = {
    "planning": "规划中",
    "booked": "已订妥",
    "ongoing": "进行中",
    "done": "已完成",
    "finished": "已完成",
    "archived": "已归档",
}

_TRAVEL_MODE_LABELS = {"driving": "驾车", "walking": "步行"}


def _clean(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())[:limit]


def _norm_item(text: str) -> str:
    return re.sub(r"[\s。．.，,、！!？?]+", "", text or "").lower()


def _start_clock(intent: Intent) -> int | None:
    return clock_to_min(intent.start_clock)


def _day_title(snapshot: Snapshot, day_id: str | None) -> str:
    for day in snapshot.days:
        if day.id == day_id:
            return day.title or f"第{day_number(day)}天"
    return ""


async def apply_intents(resolver: Resolver, intents: list[Intent]) -> Resolved:
    """把意图逐条兑现成指令。单条失败只影响那一条，其余照常返回。"""
    out = Resolved()
    seen_adds: set[tuple[str, str]] = set()
    state = _State(out, seen_adds)

    for intent in intents:
        if len(out.actions) >= settings.assistant_max_actions:
            limit = settings.assistant_max_actions
            out.warnings.append(f"一条消息里最多处理 {limit} 项改动，其余已忽略")
            break
        handler = _HANDLERS.get(intent.kind)
        if handler is None:  # pragma: no cover - Literal 已封死取值
            out.warnings.append(f"暂不支持的说法：{intent.kind}")
            continue
        try:
            await handler(resolver, intent, state)
        except Exception:  # noqa: BLE001 - 一条烂意图不能带走整次解析
            logger.exception("兑现意图 %s 时异常", intent.kind)
            out.warnings.append(f"「{intent.kind}」这条没能处理")
    return out


class _State:
    """一次 apply 调用的累加器：待办/地点的批内去重与「时刻只记备注」的只警告一次。"""

    def __init__(self, out: Resolved, seen_adds: set[tuple[str, str]]) -> None:
        self.out = out
        self.seen_adds = seen_adds
        self._warned_clock = False

    def push(self, action: object) -> None:
        self.out.actions.append(action)

    def ask(self, day_id: str | None, why: str | None) -> bool:
        """day 引用失败 → 追问；只是降级 → warning。返回 day_id 是否可用。"""
        if why:
            (self.out.warnings if day_id else self.out.questions).append(why)
        return day_id is not None

    def warn_clock(self) -> None:
        if not self._warned_clock:
            self.out.warnings.append(_TIME_NOTE)
            self._warned_clock = True


# -- 各 kind --------------------------------------------------------------------------


async def _add_place(resolver: Resolver, intent: Intent, st: _State) -> None:
    query = _clean(intent.name or intent.place, 120)
    if not query:
        st.out.warnings.append("要加的地点没有名字")
        return

    # 先认锚点再定天：「在鸡鸣寺后面加个 X」没说哪天，说的是鸡鸣寺那天。
    after_id = None
    if intent.after:
        target, ambiguous = resolver.find_place(intent.after)
        if ambiguous:
            st.out.warnings.append(ambiguous)
        elif target is None:
            st.out.warnings.append(f"没找到「{_clean(intent.after, 30)}」，新地点只能排在末尾")
        else:
            after_id = target.id

    if intent.day:
        day_id, why = resolver.resolve_day(intent.day)
    else:
        anchored = resolver.day_of_place(after_id)
        day_id, why = (anchored, None) if anchored else resolver.resolve_day("")
    if not st.ask(day_id, why) or day_id is None:
        return
    err, poi = await resolver.lookup_poi(query)
    if poi is None:
        st.out.warnings.append(err or f"「{query}」没有找到匹配地点")
        return
    name, geo = poi
    if (day_id, name) in st.seen_adds:
        return
    st.seen_adds.add((day_id, name))

    note = _clean(intent.note, 200)
    clock = _start_clock(intent)
    if clock is not None:
        note = f"{note}｜期望 {fmt_clock(clock)} 到达".strip("｜")
        st.warn_clock()

    st.push(
        PlaceAdd(
            day_id=day_id,
            name=name[:120],
            lng=geo["lng"],
            lat=geo["lat"],
            address=geo["address"][:120],
            amap_poi_id=geo["amap_poi_id"],
            photo_url=geo["photo_url"][:500],
            duration_min=resolver.clamp_duration(intent.duration_min),
            note=note,
            after_place_id=after_id,
            label=f"{resolver.day_label(day_id)} · 新增 {name}",
        )
    )


async def _move_place(resolver: Resolver, intent: Intent, st: _State) -> None:
    query = intent.place or intent.name
    place, why = resolver.find_place(query)
    if place is None:
        st.out.warnings.append(why or f"没找到要移动的地点「{_clean(query, 30)}」")
        return
    if not intent.day.strip():
        # 只报了来源没报去处（「把 X 挪走」）。落点交给聚焦那天是替人做主，问一句。
        st.out.questions.append(f"「{place.name}」要挪到哪一天？")
        return
    day_id, day_why = resolver.resolve_day(intent.day)
    if not st.ask(day_id, day_why) or day_id is None:
        return
    if place.day_id == day_id:
        st.out.warnings.append(f"「{place.name}」已经在 {resolver.day_label(day_id)}")
        return
    st.push(
        PlaceMove(
            place_id=place.id,
            name=place.name,
            day_id=day_id,
            from_day_id=place.day_id,
            label=f"{place.name} → {resolver.day_label(day_id)}",
        )
    )


async def _update_place(resolver: Resolver, intent: Intent, st: _State) -> None:
    place, why = resolver.find_place(intent.place or intent.name)
    if place is None:
        st.out.warnings.append(why or f"没找到地点「{_clean(intent.place or intent.name, 30)}」")
        return

    patch: dict[str, object] = {}
    new_name = _clean(intent.name, 120)
    # 只有 "把 A 改名成 B"（place 与 name 都给了且不同）才是改名，否则 name 只是地点引用。
    if new_name and intent.place and new_name != _clean(intent.place, 120):
        patch["name"] = new_name
    if intent.duration_min is not None:
        patch["duration_min"] = resolver.clamp_duration(intent.duration_min)

    clock = _start_clock(intent)
    note = _clean(intent.note, 200)
    if clock is not None:
        note = f"{note}｜期望 {fmt_clock(clock)} 到达".strip("｜")
        st.warn_clock()
    if note:
        patch["note"] = (f"{place.note}｜{note}" if place.note else note)[:200]

    patch = {k: v for k, v in patch.items() if k in PLACE_PATCH_KEYS}
    if not patch:
        st.out.warnings.append(f"「{place.name}」没有要改的内容")
        return
    bits = []
    if "duration_min" in patch:
        bits.append(f"时长 {fmt_duration(int(patch['duration_min']))}")
    if "name" in patch:
        bits.append(f"改名 {patch['name']}")
    if "note" in patch:
        bits.append("更新备注")
    st.push(
        PlaceUpdate(
            place_id=place.id, name=place.name, patch=patch,
            label=f"{place.name} · " + "、".join(bits),
        )
    )


async def _delete_place(resolver: Resolver, intent: Intent, st: _State) -> None:
    query = intent.place or intent.name
    place, why = resolver.find_place(query)
    if place is None:
        # 规则只有一条路：把「删掉雨伞」的雨伞当地点报上来。雨伞其实在清单里，
        # 这时回一句「没找到要删除的地点」就是答非所问 —— 再找一次待办。
        # 只在「行程里根本没这个名字」时兜底：有歧义还去删待办等于偷换靶子。
        item, item_why = (None, None) if why else resolver.find_checklist(query)
        if item is not None:
            st.push(
                ChecklistDelete(
                    item_id=item.id, text=item.text, label=f"删除清单项 {item.text}"
                )
            )
            return
        if item_why:
            st.out.warnings.append(item_why)
            return
        st.out.warnings.append(why or f"没找到要删除的地点「{_clean(query, 30)}」")
        return
    st.push(
        PlaceDelete(
            place_id=place.id, name=place.name, day_id=place.day_id,
            label=f"删除 {resolver.day_label(place.day_id)} · {place.name}",
        )
    )


async def _lock_place(resolver: Resolver, intent: Intent, st: _State) -> None:
    query = intent.place or intent.name
    place, why = resolver.find_place(query)
    if place is None:
        st.out.warnings.append(why or f"没找到要改优化的地点「{_clean(query, 30)}」")
        return
    locked = True if intent.locked is None else bool(intent.locked)
    st.push(
        PlaceLock(
            place_id=place.id, name=place.name, locked=locked,
            clears_time=not locked and place.user_start_min is not None,
            label=f"{place.name} · {'不参与优化' if locked else '重新参与优化，时刻跟随排程'}",
        )
    )


async def _add_day(resolver: Resolver, intent: Intent, st: _State) -> None:
    if len(resolver.days) >= 30:
        st.out.warnings.append("一趟行程最多 30 天")
        return
    raw_date = _clean(intent.date, 10)
    date_iso = None
    if raw_date:
        parsed = parse_iso(raw_date)
        if parsed is None:
            st.out.warnings.append(f"日期 {raw_date} 读不懂，这天先不填日期")
        else:
            date_iso = parsed.isoformat()
    st.push(DayAdd(title=_clean(intent.title, 80), date=date_iso, label="新增一天"))


async def _add_checklist(resolver: Resolver, intent: Intent, st: _State) -> None:
    existing = {_norm_item(i.text) for i in resolver.snapshot.checklist}
    fresh: list[str] = []
    for raw in intent.texts:
        item = _clean(raw, 60)
        if not item or _norm_item(item) in existing:
            continue
        existing.add(_norm_item(item))
        fresh.append(item)
    if not fresh:
        st.out.warnings.append("这些待办已经在清单里")
        return
    tail = "…" if len(fresh) > 3 else ""
    st.push(
        ChecklistAdd(
            texts=fresh,
            label=f"添加清单项 {len(fresh)} 项：" + "、".join(fresh[:3]) + tail,
        )
    )


async def _update_checklist(resolver: Resolver, intent: Intent, st: _State) -> None:
    item, why = resolver.find_checklist(intent.item)
    if item is None:
        st.out.warnings.append(why or f"没找到待办「{_clean(intent.item, 30)}」")
        return
    done = (not item.done) if intent.done is None else bool(intent.done)
    if bool(item.done) == done:
        return  # 已经是这个状态：不值得占用一次确认，静默跳过
    st.push(
        ChecklistUpdate(
            item_id=item.id, text=item.text, patch={"done": done},
            label=f"清单项 {item.text} · {'标记完成' if done else '取消完成'}",
        )
    )


async def _delete_checklist(resolver: Resolver, intent: Intent, st: _State) -> None:
    item, why = resolver.find_checklist(intent.item)
    if item is None:
        st.out.warnings.append(why or f"没找到待办「{_clean(intent.item, 30)}」")
        return
    st.push(ChecklistDelete(item_id=item.id, text=item.text, label=f"删除清单项 {item.text}"))


async def _add_expense(resolver: Resolver, intent: Intent, st: _State) -> None:
    if intent.amount_yuan is None:
        st.out.warnings.append("这笔开销没有金额")
        return
    cents = int(round(float(intent.amount_yuan) * 100))
    if cents < 1 or cents > 1_000_000_000:
        st.out.warnings.append("金额需要一个 0 到 1000 万之间的正数（元），这笔没有记")
        return
    category = intent.category if intent.category in EXPENSE_CATEGORIES else "other"
    title = _clean(intent.title, 80) or _CATEGORY_LABELS[category]
    st.push(
        ExpenseAdd(
            title=title, amount_cents=cents, category=category,
            label=f"添加开销 {title} ¥{cents / 100:g}",
        )
    )


async def _update_trip(resolver: Resolver, intent: Intent, st: _State) -> None:
    trip = resolver.snapshot.trip
    patch: dict[str, object] = {}
    bits: list[str] = []
    if intent.title:
        patch["title"] = _clean(intent.title, 80)
        bits.append("标题")
    if intent.city:
        patch["city"] = _clean(intent.city, 40)
        bits.append("城市")
    if intent.status in TRIP_STATUSES and intent.status != trip.status.value:
        patch["status"] = intent.status
        bits.append(f"状态改为{_TRIP_STATUS_LABELS.get(intent.status, '已改')}")
    if intent.travel_mode in TRAVEL_MODES and intent.travel_mode != trip.travel_mode.value:
        patch["travel_mode"] = intent.travel_mode
        bits.append(f"交通方式改为{_TRAVEL_MODE_LABELS.get(intent.travel_mode, '已改')}")
    if intent.day_start_min is not None:
        minutes = max(0, min(24 * 60 - 1, int(intent.day_start_min)))
        if minutes != trip.day_start_min:
            patch["day_start_min"] = minutes
            bits.append(f"每天 {fmt_clock(minutes)} 出发")
    if intent.budget_yuan is not None:
        cents = int(round(float(intent.budget_yuan) * 100))
        if cents < 0 or cents > 1_000_000_000:
            st.out.warnings.append("预算超出可记录范围，这条没有改")
        else:
            patch["budget_cents"] = cents
            bits.append(f"预算 ¥{cents / 100:g}")
    if not patch:
        st.out.warnings.append("行程没有要改的内容")
        return
    st.push(TripUpdate(patch=patch, label="修改行程 · " + "、".join(bits)))


async def _run_optimize(resolver: Resolver, intent: Intent, st: _State) -> None:
    day_id, why = resolver.resolve_day(intent.day)
    if not st.ask(day_id, why) or day_id is None:
        return
    count = len(resolver.places(day_id))
    if count < 3:
        st.out.warnings.append(f"{resolver.day_label(day_id)} 只有 {count} 个地点，重排没有收益")
        return
    st.push(
        RunOptimize(
            day_id=day_id, day_title=_day_title(resolver.snapshot, day_id),
            label=f"{resolver.day_label(day_id)} · 一键优化",
        )
    )


_HANDLERS = {
    "place_add": _add_place,
    "place_move": _move_place,
    "place_update": _update_place,
    "place_delete": _delete_place,
    "place_lock": _lock_place,
    "day_add": _add_day,
    "checklist_add": _add_checklist,
    "checklist_update": _update_checklist,
    "checklist_delete": _delete_checklist,
    "expense_add": _add_expense,
    "trip_update": _update_trip,
    "run_optimize": _run_optimize,
}
