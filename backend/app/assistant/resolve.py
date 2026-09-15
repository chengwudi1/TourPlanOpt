"""意图 → 指令：兑现、校验、降级。

这一层是「模型说什么都不能把脏数据带进界面」的唯一防线，四条纪律：

1. **id 只来自快照**。模型给的是名字，名字要经过匹配才能变成 place_id；匹配不唯一
   就放弃这条指令并留一句 warning，不猜。
2. **坐标只来自高德**。模型给的经纬度（本 schema 里根本没有这两个字段，防的是有人
   以后加）一律不信。
3. **数字有边界**。时长、金额、分钟数全部 clamp 到 domain 层的合法区间，越界即丢。
4. **不写 start_min**。那是自动优化的锚点开关。用户口中的「上午九点」降级成备注，
   并附一条 warning 说明想要精确时刻请在时间轨上拖 —— 一句话把一天钉死，代价是这
   天之后再也不会被优化，这个代价该由看得见时间轨的手势来付，不该由一句话来付。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from app.amap.client import get_amap_client
from app.amap.errors import AmapError
from app.assistant.context import day_number, day_number_of
from app.config import settings
from app.models.domain import Snapshot

logger = logging.getLogger("tourplan.assistant")

EXPENSE_CATEGORIES = frozenset(
    {"ticket", "transport", "food", "lodging", "shopping", "activity", "other"}
)
TRIP_STATUSES = frozenset({"planning", "booked", "ongoing", "done", "archived"})
TRAVEL_MODES = frozenset({"driving", "walking"})
PLACE_PATCH_KEYS = frozenset({"name", "duration_min", "note"})

_CN_DIGIT = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CN_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
# removed(r"(?:第\s*)?([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*(?:天|日)")
_REL_DAY = re.compile(r"^([+-])([0-9]{1,2})$")


@dataclass(slots=True)
class Resolved:
    actions: list[object] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def day_index_of(text: str) -> int | None:
    """「第3天」/「3」/「D3」/「十二」→ 3。"""
    raw = text.strip().upper().lstrip("D").replace("第", "").replace("天", "").replace("日", "")
    if not raw:
        return None
    if raw.isdigit():
        return int(raw) or None
    if raw in _CN_DIGIT:
        return _CN_DIGIT[raw]
    # 十一 ~ 二十九这种两位中文数字，够用到三十天以内的行程。
    if len(raw) == 2 and raw[0] == "十" and raw[1] in _CN_DIGIT:
        return 10 + _CN_DIGIT[raw[1]]
    if len(raw) == 2 and raw[1] == "十" and raw[0] in _CN_DIGIT:
        return _CN_DIGIT[raw[0]] * 10
    return None


def _normalize(name: str) -> str:
    return re.sub(r"[\s·・，,。.、\-—()（）]+", "", name or "").lower()


def parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except (TypeError, ValueError):
        return None


class Resolver:
    """一次请求一个实例：持有快照索引与 POI 查询缓存，跨请求绝不复用。"""

    def __init__(self, snapshot: Snapshot, focus_day_id: str | None) -> None:
        self.snapshot = snapshot
        self.days = list(snapshot.days)
        self.day_by_id = {d.id: d for d in self.days}
        self.focus = self._initial_focus(focus_day_id)
        self._poi_cache: dict[str, tuple | None] = {}
        self._poi_calls = 0

    # -- 索引 ------------------------------------------------------------------------

    def _initial_focus(self, focus_day_id: str | None) -> str | None:
        if focus_day_id and focus_day_id in self.day_by_id:
            return focus_day_id
        if self.days:
            return self.days[-1].id
        return None

    def _by_number(self, number: int) -> list:
        """「第 N 天」（1 基）→ 那几天。换算只走 day_number_of 一处。"""
        return [d for d in self.days if d.day_index == number - 1]

    def places(self, day_id: str | None = None) -> list:
        return [p for p in self.snapshot.places if day_id is None or p.day_id == day_id]

    def day_of_place(self, place_id: str | None) -> str | None:
        """地点 → 它所在的那天。用户只报了锚点、没报哪天时的默认落点。"""
        if not place_id:
            return None
        return next((p.day_id for p in self.snapshot.places if p.id == place_id), None)

    def find_place(self, query: str) -> tuple[object | None, str | None]:
        """名字 → 地点。返回 (place, 歧义说明)。"""
        name = _normalize(query)
        if not name:
            return None, None
        candidates = self.places()
        exact = [p for p in candidates if _normalize(p.name) == name]
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, f"「{query}」在这趟行程里出现 {len(exact)} 次，需要指定是哪一个"
        partial = [
            p for p in candidates
            if name in _normalize(p.name) or _normalize(p.name) in name
        ]
        if len(partial) == 1:
            return partial[0], None
        if len(partial) > 1:
            names = "、".join(p.name for p in partial[:4])
            return None, f"「{query}」匹配到多个地点（{names}），需要更具体的名字"
        return None, None

    def find_checklist(self, query: str) -> tuple[object | None, str | None]:
        text = _normalize(query)
        if not text:
            return None, None
        items = self.snapshot.checklist
        exact = [i for i in items if _normalize(i.text) == text]
        if len(exact) == 1:
            return exact[0], None
        partial = [i for i in items if text in _normalize(i.text) or _normalize(i.text) in text]
        if len(partial) == 1:
            return partial[0], None
        if len(partial) > 1:
            return None, f"「{query}」匹配到多条待办，需要更具体的说法"
        return None, None

    # -- 天 ---------------------------------------------------------------------------

    def resolve_day(self, ref: str) -> tuple[str | None, str | None]:
        """天引用 → day_id。返回 (day_id, 说明/追问)。"""
        if self.focus is None:
            return None, "这趟行程还没有安排日期，请先加一天"
        raw = (ref or "").strip()
        if not raw:
            return self.focus, None

        low = raw.lower()
        if low in {"this", "这天", "当前", "当前天", "今天"}:
            return self.focus, None
        if low in {"next", "下一天", "后一天", "最后一天"}:
            focus_index = self.day_by_id[self.focus].day_index
            if low == "最后一天":
                return self.days[-1].id, None
            follow = self._by_number(day_number_of(focus_index + 1))
            if follow:
                return follow[0].id, None
            return None, f"第 {day_number_of(focus_index + 1)} 天还不存在"

        relative = _REL_DAY.match(low)
        if relative:
            base = self.day_by_id[self.focus].day_index
            delta = int(relative.group(2)) * (1 if relative.group(1) == "+" else -1)
            hit = self._by_number(day_number_of(base + delta))
            if not hit:
                return None, f"第 {day_number_of(base + delta)} 天还不存在"
            return hit[0].id, None

        if low in {"最后一天之后", "末尾", "append"}:
            return self.days[-1].id, None

        weekday = _weekday_of(low)
        if weekday is not None:
            return self._by_weekday(weekday, raw)

        parsed = parse_iso(raw)
        if parsed is not None:
            hit = [d for d in self.days if d.date and parse_iso(d.date) == parsed]
            if len(hit) == 1:
                return hit[0].id, None
            if not hit:
                return None, f"行程里没有 {parsed.isoformat()} 这天"
            return hit[0].id, f"{parsed.isoformat()} 对应两天，按第 {day_number(hit[0])} 天处理"

        index = day_index_of(raw)  # 界面上的「第 N 天」，1 基
        if index is not None:
            hit = self._by_number(index)
            if hit:
                return hit[0].id, None
            if index == len(self.days) + 1:
                return None, f"需要新建第 {index} 天"
            return None, f"没有第 {index} 天（当前共 {len(self.days)} 天）"
        return None, f"没听懂「{raw}」指的是哪天"

    def _by_weekday(self, weekday: int, raw: str) -> tuple[str | None, str | None]:
        dated = [(d, parse_iso(d.date or "")) for d in self.days]
        hit = [d for d, dt in dated if dt is not None and dt.weekday() == weekday]
        if len(hit) == 1:
            return hit[0].id, None
        if not hit:
            return None, f"行程的日期里没有 {'周' + '一二三四五六日'[weekday]}（{raw}）"
        focus_index = self.day_by_id[self.focus].day_index
        nearest = min(hit, key=lambda d: abs(d.day_index - focus_index))
        name = "周" + "一二三四五六日"[weekday]
        return nearest.id, f"{name} 有两天，按离当前天最近的第 {day_number(nearest)} 天处理"

    # -- 地点兑现 -----------------------------------------------------------------------

    async def lookup_poi(self, query: str) -> tuple[str | None, dict | None]:
        """名字 → 高德 POI。返回 (错误说明, poi)。结果按名字缓存，一次请求内不重复烧配额。"""
        key = _normalize(query)
        if not key:
            return "地点名为空", None
        if key in self._poi_cache:
            cached = self._poi_cache[key]
            return (None, cached) if cached else ("这个地点之前已确认搜不到", None)
        if self._poi_calls >= settings.assistant_max_poi_lookups:
            return (
                f"这条里的地点太多（单次请求最多搜索 {settings.assistant_max_poi_lookups} 次）",
                None,
            )
        self._poi_calls += 1
        city = self.snapshot.trip.city or None
        try:
            page = await get_amap_client().place_text(query.strip(), city, page_size=5)
            pois = page.pois
            if not pois and city and self._poi_calls < settings.assistant_max_poi_lookups:
                # 城市名可能是「南京」而 POI 挂在「南京市…」，也可能用户压根没说城市。
                # 换城市再试一次很值钱，但它是另一次真配额，预算见底就不试了。
                self._poi_calls += 1
                page = await get_amap_client().place_text(query.strip(), None, page_size=5)
                pois = page.pois
        except AmapError as exc:
            logger.warning("助手 POI 搜索失败：%s", exc)
            self._poi_cache[key] = None
            return f"地点搜索暂不可用（{exc.message}）", None
        except Exception:  # noqa: BLE001
            logger.exception("助手 POI 搜索异常")
            self._poi_cache[key] = None
            return "地点搜索出了点问题，请手动添加", None
        if not pois:
            self._poi_cache[key] = None
            return f"「{query}」没有找到匹配地点", None
        poi = pois[0]
        found = (
            poi.name,
            {
                "lng": float(poi.lng),
                "lat": float(poi.lat),
                "address": poi.address or "",
                "amap_poi_id": poi.id or "",
                "photo_url": poi.photo or "",
            },
        )
        self._poi_cache[key] = found
        return None, found

    # -- 数字 ---------------------------------------------------------------------------

    def clamp_duration(self, minutes: int | None) -> int:
        if minutes is None:
            return 60
        return max(0, min(24 * 60, int(minutes)))

    def day_label(self, day_id: str | None) -> str:
        day = self.day_by_id.get(day_id or "")
        return f"第 {day_number(day)} 天" if day else "这天"


def _weekday_of(text: str) -> int | None:
    for prefix in ("周", "星期", "礼拜", "本周", "这周", "下周"):
        if text.startswith(prefix):
            tail = text[len(prefix) :].strip()
            return _CN_WEEKDAY.get(tail if tail in _CN_WEEKDAY else tail[:1])
    return None
