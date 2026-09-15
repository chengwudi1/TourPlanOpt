"""快照 → 「现状」文本段。

给模型的上下文只到**能指认对象**为止：天序、日期、地点名、时长、待办条目。不塞
坐标、不塞 id、不塞 rev —— 模型用不上，多一个字就多一分幻觉的余地。
"""

from __future__ import annotations

import re

from app.models.domain import PlaceOut, Snapshot

MAX_PLACES_PER_DAY = 20
MAX_CHECKLIST_ITEMS = 30

# '09:00' 这种规范写法，和 '上午9点半' 这种口语写法。两者都要认：模型偶尔会照抄用户原话。
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CLOCK_HINT = re.compile(r"(上午|下午|晚上|早上|中午|傍晚|凌晨)?\s*([0-9]{1,2})\s*点(半|整)?")


def clock_to_min(text: str) -> int | None:
    """'09:00' / '上午9点半' → 自零时起的分钟数。看不懂就返回 None，绝不猜。"""
    raw = (text or "").strip()
    if not raw:
        return None
    match = _CLOCK.match(raw)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = _CLOCK_HINT.search(raw)
    if not match:
        return None
    hour = int(match.group(2))
    if hour > 23:
        return None
    meridiem = match.group(1) or ""
    if meridiem in {"下午", "晚上", "傍晚"} and hour < 12:
        hour += 12
    if meridiem in {"凌晨", "夜里", "半夜"} and hour == 12:
        # 只有凌晨的 12 点是零点；「中午 12 点」要是也走这条规则会折成 00:00。
        hour = 0
    return hour * 60 + (30 if match.group(3) == "半" else 0)


def day_number_of(index: int) -> int:
    """库里的 days.day_index 是 0 基的，界面上的「第 N 天」是 1 基的。

    这个换算只写在这里一次。助手要是拿 day_index 当「第几天」用，第 1 天永远
    解析不到，而最后一句报错会说出一句根本不存在的「第 0 天」。
    """
    return index + 1


def day_number(day) -> int:
    return day_number_of(day.day_index)


def fmt_clock(minutes: int) -> str:
    """整数分钟（自零时起）→ 24 小时读数。越界按一天 1440 分钟折回。"""
    hour, minute = divmod(int(minutes) % (24 * 60), 60)
    return f"{hour:02d}:{minute:02d}"


def fmt_duration(minutes: int) -> str:
    """时长说人话：90 → 「1.5小时」，30 → 「30分钟」。"""
    if minutes % 60 == 0:
        return f"{minutes // 60}小时"
    if minutes > 60 and minutes % 30 == 0:
        return f"{minutes / 60:g}小时"
    return f"{minutes}分钟"


def _place_phrase(place: PlaceOut) -> str:
    bits = [fmt_duration(place.duration_min)]
    if place.user_start_min is not None:
        # 手填时间 = 锚点。写进现状，模型才知道「先九点去 A」这类话已有主。
        bits.append(f"{fmt_clock(place.user_start_min)}")
    if place.locked:
        bits.append("已钉住")
    return f"{place.name}({', '.join(bits)})"


def build_context(snapshot: Snapshot, focus_day_id: str | None = None) -> str:
    trip = snapshot.trip
    head = [f"城市 {trip.city or '未填'}", f"出行方式 {trip.travel_mode.value}"]
    head.append(f"每天从 {fmt_clock(trip.day_start_min)} 开始")
    if trip.budget_cents:
        head.append(f"预算 {trip.budget_cents / 100:g} 元")
    lines = ["，".join(head) + f"，状态 {trip.status.value}", f"标题 {trip.title or '未填'}"]

    places_by_day: dict[str, list[PlaceOut]] = {}
    for place in snapshot.places:
        places_by_day.setdefault(place.day_id, []).append(place)

    for day in snapshot.days:
        places = places_by_day.get(day.id, [])
        marker = " ←当前天" if day.id == focus_day_id else ""
        body = "、".join(_place_phrase(p) for p in places[:MAX_PLACES_PER_DAY])
        if len(places) > MAX_PLACES_PER_DAY:
            body += f"…（另有 {len(places) - MAX_PLACES_PER_DAY} 个）"
        day_head = f"第{day_number(day)}天"
        label = day.title or day_head
        date = day.date or "未定日期"
        lines.append(f"{day_head} {date}「{label}」：{body or '(空)'}{marker}")

    pending = [i for i in snapshot.checklist[:MAX_CHECKLIST_ITEMS]]
    if pending:
        lines.append(
            "待办："
            + "、".join(f"{i.text}{'(已完成)' if i.done else ''}" for i in pending)
        )
    if snapshot.stash:
        lines.append("暂存区：" + "、".join(s.name for s in snapshot.stash[:15]))
    return "\n".join(lines)
