"""Amap key self-check.

This module exists to convert the single most likely failure mode — a blank map
and a silent server — into a self-diagnosing one. It distinguishes the four
states that look identical to a confused user: key missing, key invalid, key of
the wrong type, and quota exhausted.

The backend can only validate the **Web服务** key. A JS API key is checked
in-browser against a domain whitelist, so ``AmapKeyCheck.vue`` owns that half.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

from app.amap.client import AmapWebClient
from app.amap.errors import AmapError
from app.config import PROBE_DESTINATION, PROBE_ORIGIN, settings

logger = logging.getLogger("tourplan.amap")

# mode=1 (driving) between two ordinary Beijing points. Costs one call.
PROBE_MODE = 1


@dataclass(slots=True)
class KeyCheck:
    name: str
    ok: bool
    present: bool
    detail: str
    hint: str
    infocode: str = ""
    latency_ms: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class AmapHealthReport:
    web_key: KeyCheck
    js_key: KeyCheck
    cache: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.web_key.ok and self.js_key.ok

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "web_key": asdict(self.web_key),
            "js_key": asdict(self.js_key),
            "cache": self.cache,
        }


async def check_web_service_key(client: AmapWebClient) -> KeyCheck:
    name = "AMAP_WEB_KEY"
    if not client.has_key:
        return KeyCheck(
            name=name,
            ok=False,
            present=False,
            detail="未配置",
            hint=(
                "backend/.env 缺少 AMAP_WEB_KEY。请到 https://console.amap.com 添加一个"
                "「Web服务」类型的 Key（注意不是「Web端(JS API)」）"
            ),
        )

    started = time.monotonic()
    try:
        results = await client.distance([PROBE_ORIGIN], PROBE_DESTINATION, PROBE_MODE)
    except AmapError as exc:
        return KeyCheck(
            name=name,
            ok=False,
            present=True,
            detail=exc.message,
            hint=exc.hint or "请核对 Key 是否正确、类型是否为「Web服务」",
            infocode=exc.infocode,
            latency_ms=round((time.monotonic() - started) * 1000, 1),
        )
    latency = round((time.monotonic() - started) * 1000, 1)

    if not results or not results[0].ok or results[0].distance_m is None:
        infocode = results[0].infocode if results else "20800"
        return KeyCheck(
            name=name,
            ok=False,
            present=True,
            detail=f"探活调用成功但结果不可用（infocode={infocode}）",
            hint=(
                "Key 能通过鉴权，但这对坐标算不出距离。通常是配额或参数问题，"
                "请到控制台查看该 Key 的调用量与权限"
            ),
            infocode=infocode,
            latency_ms=latency,
        )

    return KeyCheck(
        name=name,
        ok=True,
        present=True,
        detail=f"探活成功：{results[0].distance_m} 米 / {results[0].duration_s} 秒",
        hint="",
        infocode="10000",
        latency_ms=latency,
    )


def check_js_key() -> KeyCheck:
    """Config-presence check only. Real validation happens in the browser."""
    name = "AMAP_JS_KEY"
    has_key = bool(settings.amap_js_key.strip())
    has_scode = bool(settings.amap_js_scode.strip())

    if not has_key:
        return KeyCheck(
            name=name,
            ok=False,
            present=False,
            detail="未配置",
            hint=(
                "backend/.env 缺少 AMAP_JS_KEY。请到 https://console.amap.com 添加一个"
                "「Web端(JS API)」类型的 Key，并把它的「安全密钥」填进 AMAP_JS_SCODE"
            ),
        )
    if not has_scode:
        return KeyCheck(
            name=name,
            ok=False,
            present=True,
            detail="缺少安全密钥",
            hint=(
                "backend/.env 缺少 AMAP_JS_SCODE。2021-12-02 之后创建的 JS API Key 必须"
                "配套安全密钥，否则前端会报 INVALID_USER_SCODE"
            ),
            extra={"scode_present": False},
        )

    return KeyCheck(
        name=name,
        ok=True,
        present=True,
        detail="已配置（真实校验在浏览器中完成）",
        hint=(
            "域名白名单请在高德控制台留空：若绑定了 localhost，用手机或局域网 IP 访问会看到灰色地图"
        ),
        extra={"scode_present": True},
    )


async def full_self_check(
    client: AmapWebClient, cache_stats: dict | None = None
) -> AmapHealthReport:
    web = await check_web_service_key(client)
    js = check_js_key()
    return AmapHealthReport(web_key=web, js_key=js, cache=cache_stats or {})


def log_banner(report: AmapHealthReport) -> None:
    """Print an impossible-to-miss banner at startup. Never raises."""
    bar = "=" * 74
    if report.ok:
        logger.info(
            "\n%s\n  高德 Key 自检通过\n    %s: %s\n    %s: %s\n%s",
            bar,
            report.web_key.name,
            report.web_key.detail,
            report.js_key.name,
            report.js_key.detail,
            bar,
        )
        return

    lines = [bar, "  [!] 高德 Key 自检未通过 —— 应用仍会启动，但相关功能不可用", ""]
    for check in (report.web_key, report.js_key):
        if check.ok:
            continue
        lines.append(f"  [{check.name}] {check.detail}")
        if check.infocode:
            lines.append(f"      infocode: {check.infocode}")
        lines.append(f"      怎么办: {check.hint}")
        lines.append("")
    lines.append("  修好后重启，或访问 GET /api/amap/health 重新检查。")
    lines.append(bar)
    logger.warning("\n%s", "\n".join(lines))
