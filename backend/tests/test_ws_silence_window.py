"""WS 静默判死窗口必须容得下「被浏览器节流的心跳」——线上实测换来的回归。

公网日志里的形状是：某个后台标签页每 60 秒被踢一次（app 日志
`ws ... timed out after 60.0s silence`），而 Caddy 侧那条 101 活了 236 秒才重连。
根因是两个时间参数互相打架：前端心跳 25 秒，Chrome 却把隐藏页面的定时器压到约
1 次/分钟，于是 60 秒的判死窗口**必然**先咬到——切到后台的端收不到同伴的改动。

每条判据在旧值（60.0）上必红：

- 判死窗口要严格大于「节流后心跳的最小到达间隔」60 秒（旧值刚好相等，踩线）
- 窗口要容得下若干个心跳周期，缺拍不等于断线（旧值 60 = 2.4 拍）
- hub 对外暴露的取值与 settings 一致（连接循环与房间回收读的是同一个数）
- 若前端源码在位，窗口必须 >= 5 × 前端实际心跳（把两个仓库的参数钉在一起）
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import settings
from app.ws import hub

# 浏览器对隐藏页面定时器的节流下限：约 1 次/分钟。心跳晚于这个间隔到达是常态，不是异常。
THROTTLED_HEARTBEAT_FLOOR_S = 60.0
# 前端 HEARTBEAT_MS = 25_000（frontend/src/stores/socket.ts），此处是它的镜像常量。
CLIENT_HEARTBEAT_S = 25.0
MIN_HEARTBEAT_CYCLES_COVERED = 5

SOCKET_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "stores" / "socket.ts"


def test_silence_window_is_longer_than_throttled_heartbeat() -> None:
    assert settings.ws_silence_timeout_s > THROTTLED_HEARTBEAT_FLOOR_S, (
        f"判死窗口 {settings.ws_silence_timeout_s}s 不超过浏览器节流后的心跳间隔，"
        "后台标签页必被踢"
    )


def test_silence_window_covers_several_heartbeat_cycles() -> None:
    covered = settings.ws_silence_timeout_s / CLIENT_HEARTBEAT_S
    assert covered >= MIN_HEARTBEAT_CYCLES_COVERED, (
        f"窗口只容得下 {covered:.1f} 个心跳周期，缺拍会被误判成断线"
    )


def test_hub_exposes_the_same_value_as_settings() -> None:
    assert hub.silence_timeout_s() == settings.ws_silence_timeout_s


def test_window_stays_ahead_of_the_frontend_heartbeat_constant() -> None:
    if not SOCKET_TS.is_file():  # 生产镜像里只有 frontend/dist，没有源码
        return
    raw = re.search(r"HEARTBEAT_MS\s*=\s*([\d_]+)", SOCKET_TS.read_text("utf-8")).group(1)
    heartbeat_ms = int(raw.replace("_", ""))  # 源码写成 25_000，只取 \d+ 会截成 25，判据恒真
    assert settings.ws_silence_timeout_s * 1000 >= MIN_HEARTBEAT_CYCLES_COVERED * heartbeat_ms, (
        f"前端心跳改到了 {heartbeat_ms}ms，后端窗口没跟着放大"
    )
