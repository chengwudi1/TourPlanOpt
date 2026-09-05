"""Amap error taxonomy.

The single most important fact about the Amap Web服务 API: it returns **HTTP 200
with the error inside the JSON body**. Callers must branch on ``body["status"]``,
never on the HTTP status code. See ``client.AmapWebClient._get``.

The infocode table below is data-driven so a wrong hint is a one-line fix rather
than a code change.
"""

from __future__ import annotations

from enum import StrEnum


class AmapErrorKind(StrEnum):
    OK = "ok"
    AUTH = "auth"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    PARAM = "param"
    UNREACHABLE = "unreachable"
    TRANSPORT = "transport"
    UNKNOWN = "unknown"


class AmapError(Exception):
    """Base class. ``retryable`` drives the retry policy in the client."""

    kind = AmapErrorKind.UNKNOWN
    retryable = False

    def __init__(self, message: str, *, infocode: str = "", info: str = "", hint: str = ""):
        super().__init__(message)
        self.message = message
        self.infocode = infocode
        self.info = info
        self.hint = hint

    def __str__(self) -> str:
        parts = [self.message]
        if self.infocode:
            parts.append(f"infocode={self.infocode}")
        if self.info:
            parts.append(f"info={self.info}")
        return " ".join(parts)


class AmapAuthError(AmapError):
    kind = AmapErrorKind.AUTH
    retryable = False


class AmapQuotaError(AmapError):
    kind = AmapErrorKind.QUOTA
    retryable = False


class AmapRateLimitError(AmapError):
    kind = AmapErrorKind.RATE_LIMIT
    retryable = True


class AmapParamError(AmapError):
    kind = AmapErrorKind.PARAM
    retryable = False


class AmapUnreachableError(AmapError):
    kind = AmapErrorKind.UNREACHABLE
    retryable = False


class AmapTransportError(AmapError):
    """Network failure, non-200 HTTP, non-JSON body, or Amap-side busy signal."""

    kind = AmapErrorKind.TRANSPORT
    retryable = True


# infocode -> (exception class, user-facing hint).
# 10009 gets an unusually loud hint because it is the exact symptom of the
# two-key confusion: putting the JS API key into AMAP_WEB_KEY.
INFOCODES: dict[str, tuple[type[AmapError], str]] = {
    "10001": (AmapAuthError, "Key 不正确或已过期，请到高德控制台核对 **Web服务** 类型的 Key"),
    "10002": (AmapAuthError, "该 Key 未开通此服务权限，请在控制台为它勾选 Web服务 API"),
    "10003": (AmapQuotaError, "今日免费配额已用完，明天再试（重试无用）"),
    "10004": (AmapRateLimitError, "请求过于频繁，正在自动退避重试"),
    "10005": (AmapAuthError, "IP 白名单不包含本机，请在控制台放开或清空白名单"),
    "10009": (
        AmapAuthError,
        "Key 类型与平台不符 —— 很可能你把「Web端(JS API)」的 Key 填进了 AMAP_WEB_KEY。"
        "后端需要的是「Web服务」类型的 Key",
    ),
    "10012": (AmapAuthError, "请求内容不合规"),
    "10013": (AmapAuthError, "该 Key 已被删除"),
    "10014": (AmapRateLimitError, "每秒请求超限（QPS），已降低并发"),
    "10015": (AmapTransportError, "高德服务超时，正在重试"),
    "10016": (AmapTransportError, "高德服务繁忙，正在重试"),
    "10017": (AmapTransportError, "高德资源不可用，正在重试"),
    "10019": (AmapRateLimitError, "并发请求超限（CQPS），已降低并发"),
    "10020": (AmapRateLimitError, "客户端并发超限（CKQPS），已降低并发"),
    "10021": (AmapRateLimitError, "用户并发超限（CUQPS），已降低并发"),
    "10026": (AmapParamError, "请求非法"),
    "20000": (AmapParamError, "请求参数非法（多为坐标格式错误）"),
    "20001": (AmapParamError, "缺少必填参数"),
    "20002": (AmapParamError, "请求协议非法"),
    "20003": (AmapParamError, "其他服务端未知错误"),
    "20800": (
        AmapUnreachableError,
        "该点对不可达（步行超 5km / 跨海 / 无路网），已写入负缓存不再重试",
    ),
}

OK_INFOCODE = "10000"


def raise_for_infocode(infocode: str, info: str) -> None:
    """Map an Amap error infocode onto the right exception. Never returns."""
    cls, hint = INFOCODES.get(infocode, (AmapError, f"高德返回未知错误码 {infocode}"))
    raise cls(hint, infocode=infocode, info=info, hint=hint)


def hint_for_infocode(infocode: str) -> str:
    """Hint text without raising — used by the health report."""
    if infocode == OK_INFOCODE:
        return ""
    return INFOCODES.get(infocode, (AmapError, f"高德返回未知错误码 {infocode}"))[1]
