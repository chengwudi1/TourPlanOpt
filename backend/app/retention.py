"""未登录行程的保留期清理。

政策（2026-10-08 定案）：**未登录创建的行程，7 天内无人打开/改动就自动清理**，免得
沉底的测试数据白占空间。两条判据同时成立才进清理名单：

- ``trips.created_by IS NULL`` —— 创建时没登录；
- ``trip_visits`` 里没有任何一行 —— 从来没有已登录用户打开过它。

只要有一个已登录用户打开过（它就在那个人的「我的行程」里），这份行程就不再是清理
对象：删它等于删别人正在用的东西，那不是省空间，是丢数据。

「7 天」从**最后一次活跃**算，不是创建时间：``participants.last_seen``（有人进过房间）
与各内容表的最新时间戳取最大值，一趟刚建好就一直在用的行程不会被误伤。时间列一律是
``now_iso()`` 写下的同格式 UTC 字符串（秒精度），可以直接做字符串比较。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db.database import Database

logger = logging.getLogger("tourplan.retention")

# 每天扫一次。清不清理由「7 天没动静」那条判据决定，跑多勤只决定过期多久之后被收走。
SWEEP_INTERVAL_S = 24 * 3600

# 「最后一次活跃」= 下面这些时间戳里最大的那个。COALESCE 到空串是必要的：SQLite 的多参数
# max() 只要有一个参数是 NULL 就整体返回 NULL，而空串比任何 ISO 时间串都小，不参与竞争。
_LAST_ACTIVITY_SQL = """
    max(
        t.created_at,
        COALESCE((SELECT MAX(last_seen) FROM participants WHERE trip_id = t.id), ''),
        COALESCE((SELECT MAX(updated_at) FROM places WHERE trip_id = t.id), ''),
        COALESCE((SELECT MAX(created_at) FROM stash WHERE trip_id = t.id), ''),
        COALESCE((SELECT MAX(updated_at) FROM checklist_items WHERE trip_id = t.id), ''),
        COALESCE((SELECT MAX(updated_at) FROM expenses WHERE trip_id = t.id), ''),
        COALESCE((SELECT MAX(created_at) FROM messages WHERE trip_id = t.id), '')
    )
"""


def _cutoff_iso(days: int, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return (moment - timedelta(days=days)).isoformat(timespec="seconds")


async def purge_stale_guest_trips(
    db: Database, *, days: int | None = None, now: datetime | None = None
) -> list[str]:
    """删掉过期且没人认领的未登录行程，返回被删的 id。

    行与内容的删除走同一个事务（子表靠 ON DELETE CASCADE 一起走）；封面文件在提交之后
    单独收——文件系统不归事务管，删不掉只留日志，不影响「行程已经删掉了」这个事实。
    """
    retention = settings.guest_trip_retention_days if days is None else days
    cutoff = _cutoff_iso(retention, now)

    def _purge(conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            f"""SELECT t.id AS id, {_LAST_ACTIVITY_SQL} AS last_activity
                FROM trips t
                WHERE t.created_by IS NULL
                  AND NOT EXISTS (SELECT 1 FROM trip_visits v WHERE v.trip_id = t.id)"""
        ).fetchall()
        stale = [row["id"] for row in rows if str(row["last_activity"] or "") < cutoff]
        for trip_id in stale:
            conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        return stale

    removed = await db.run(_purge)
    if removed:
        logger.info(
            "清理未登录行程 %d 份（%d 天无动静）：%s", len(removed), retention, ", ".join(removed)
        )
    from app.uploads import remove_trip_uploads

    for trip_id in removed:
        await remove_trip_uploads(trip_id)
    return removed


async def run_retention_loop(interval_s: float = SWEEP_INTERVAL_S) -> None:
    """启动时先扫一遍，之后每隔 interval 一次。任何异常只记日志——清理不该拖垮服务。"""
    from app.db.database import get_db

    while True:
        try:
            await purge_stale_guest_trips(get_db())
        except Exception:
            logger.exception("未登录行程清理失败（下一轮再试）")
        await asyncio.sleep(interval_s)
