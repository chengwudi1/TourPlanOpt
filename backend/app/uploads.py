"""M36 封面文件：识别、落盘、清理。

这是后端今天**唯一真正写进磁盘的用户内容**，所以三条纪律集中在这里：

1. **不信任客户端报的任何东西。** `UploadFile.content_type` 由浏览器自己填，改一个字符串
   就能把任意字节标成 `image/jpeg`。类型一律靠 magic bytes 判，扩展名由判出来的类型决定。
2. **`trip_id` 来自 URL，会拼进文件路径。** 它今天是 8 位 base32，但那是生成侧的事实，
   不是这一层的输入约束——`../../` 之类的值必须在这一层就挡住，否则就是一个写文件的口子。
3. **只删自己写过的文件。** 清旧图时要求那条 URL 确实是本站 `/uploads/<本程>/...`，
   否则「恢复默认封面」写个别人的路径进来就能借这个端点删文件。
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from pathlib import Path

import anyio

from app.config import settings

logger = logging.getLogger("tourplan.uploads")

# 只收这三种：前端 canvas 预缩只产 JPEG，PNG / WebP 留给直接打接口的情况。
# (前缀, 第 8 字节要再验的一段, 扩展名)——空 bytes 表示没有第二段。
_SIGNATURES: tuple[tuple[bytes, bytes, str], ...] = (
    (b"\xff\xd8\xff", b"", "jpg"),
    (b"\x89PNG\r\n\x1a\n", b"", "png"),
    (b"RIFF", b"WEBP", "webp"),  # 光看 RIFF 是 WAV/AVI，必须再验第 8 字节那四个字母
)

TRIP_ID_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")


class CoverReject(Exception):
    """一张被拒的封面。`kind` 决定端点回哪个状态码与哪句人话。"""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def sniff_image(data: bytes) -> str:
    """返回扩展名，认不出就抛 CoverReject('type')。"""
    for prefix, nested, ext in _SIGNATURES:
        if not data.startswith(prefix):
            continue
        if nested and data[8 : 8 + len(nested)] != nested:
            continue
        return ext
    raise CoverReject("type")


def _safe_trip_id(trip_id: str) -> str:
    if not TRIP_ID_RE.match(trip_id):
        raise CoverReject("id")
    return trip_id


def cover_dir(trip_id: str) -> Path:
    return settings.uploads_dir / _safe_trip_id(trip_id)


def _write_sync(trip_id: str, data: bytes, ext: str) -> str:
    directory = cover_dir(trip_id)
    directory.mkdir(parents=True, exist_ok=True)
    # 随机名而不是 <trip_id>.jpg：同一程换图会留旧文件，但更重要的是名字不可猜——
    # /uploads 是公开可读的（决策 12），路径不可枚举是它唯一的访问控制。
    name = f"{secrets.token_hex(10)}.{ext}"
    (directory / name).write_bytes(data)
    return f"/uploads/{trip_id}/{name}"


async def save_cover(trip_id: str, data: bytes) -> str:
    if len(data) > settings.cover_max_bytes:
        raise CoverReject("too_large")
    ext = sniff_image(data)
    _safe_trip_id(trip_id)
    return await anyio.to_thread.run_sync(_write_sync, trip_id, data, ext)


def _unlink_sync(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # 删不掉旧图不是用户的错，也不该让一次成功的上传回成失败：宁可留一个孤儿文件。
        pass


def drop_cover_file(cover_url: str, trip_id: str) -> None:
    """删掉本站为这一程写过的旧封面文件。

    两道闸缺一不可：形状（外链、`..`、缺段一律不动）与**归属**。后者才是重点——
    `cover_url` 允许任意 `/uploads/...` 直链，少了归属检查，客户端就能把别人行程的封面路径
    PATCH 进来、再改回空串，借这个清理动作删掉别人的图。
    """
    prefix = "/uploads/"
    if not cover_url.startswith(prefix):
        return
    parts = Path(cover_url[len(prefix) :]).parts
    if len(parts) != 2 or parts[0] != trip_id or not TRIP_ID_RE.match(trip_id):
        return
    if parts[1] in ("", ".", ".."):
        return
    _unlink_sync(settings.uploads_dir.joinpath(*parts))


# 孤儿只可能来自「文件写成了、库还没写」那一小段窗口，所以「够老」就是足够的判据。
_ORPHAN_MIN_AGE_SECONDS = 600


def _sweep_sync(trip_id: str, keep_url: str) -> None:
    directory = cover_dir(trip_id)
    keep = f"/uploads/{trip_id}/"
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    now = time.time()
    for entry in entries:
        # 只删本目录顶层的常规文件：子目录、别人手放进去的东西、以及 keep 之外形状不对的
        # 路径都不碰——这一层清理的是自己写出来的随机名文件，不是通用目录清理器。
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if f"{keep}{entry.name}" == keep_url:
            continue
        try:
            if now - entry.stat().st_mtime < _ORPHAN_MIN_AGE_SECONDS:
                continue
            entry.unlink(missing_ok=True)
        except OSError:
            pass


async def sweep_covers(trip_id: str, keep_url: str) -> None:
    """上传成功后顺手清掉该程目录里的孤儿（决策文档里承诺的那一步）。"""
    try:
        await anyio.to_thread.run_sync(_sweep_sync, trip_id, keep_url)
    except Exception:
        logger.warning("清理封面孤儿文件失败（不影响本次上传）", exc_info=True)
