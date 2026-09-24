from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

logger = logging.getLogger("tourplan")


def _configure_console_encoding() -> None:
    """Force UTF-8 on stdout/stderr.

    Windows consoles default to the OEM codepage (cp936 here), which renders the
    Chinese diagnostics in the startup banner as mojibake and replaces any
    character outside GBK with a ``\\uXXXX`` escape. Git Bash, Windows Terminal
    and VS Code all read UTF-8, so reconfiguring makes the banner legible
    however uvicorn was launched.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def _guard_single_worker() -> None:
    # The broadcast hub and presence are in-process state. Running more than one
    # worker silently breaks collaboration: clients land on different processes
    # and never see each other.
    concurrency = os.environ.get("WEB_CONCURRENCY")
    if concurrency and concurrency not in ("1", ""):
        logger.warning(
            "WEB_CONCURRENCY=%s 已设置。TourPlanOpt 的协同 hub 是进程内状态，"
            "多 worker 会让客户端互相看不见 —— 请只用单 worker 启动。",
            concurrency,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_console_encoding()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # 高德密钥走 query 参数，而 httpx 在 INFO 级把完整请求 URL 原样打进来 —— 那等于把
    # 密钥抄进日志。第三方库的日志级别不由我们决定，所以把它抬到 WARNING。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _guard_single_worker()
    logger.info("%s v%s starting", settings.app_name, settings.version)

    # Fatal on failure: nothing works without the schema. (Contrast the Amap self-check
    # below, which must never abort startup.)
    from app.db.database import get_db

    await get_db().init()

    # A failed self-check must NEVER abort startup: the app has to boot so the
    # frontend can render its explanatory banner. A blank map plus a silent
    # server is precisely the failure mode this check exists to prevent.
    try:
        from app.amap.client import get_amap_client
        from app.amap.health import full_self_check, log_banner

        log_banner(await full_self_check(get_amap_client()))
    except Exception:
        logger.exception("高德自检本身出错了（不影响启动）")

    # 助手不做启动自检：启动时联网只会把一个可有可无的功能变成开机依赖。但这行必须打
    # 出来 —— 没有它，「没配密钥」和「配错了却没人说」在日志里长得一模一样。
    try:
        from app.assistant.llm import endpoint_label, llm_ready

        logger.info(
            "对话式助手：%s（%s）",
            "走模型解析，规则兜底" if llm_ready() else "未配 LLM_API_KEY，走规则解析",
            endpoint_label(),
        )
    except Exception:
        logger.exception("助手配置读取失败（不影响启动）")

    # 语音识别同理：只报配置成了什么，不联网探活。
    try:
        from app.assistant.speech import asr_label, asr_ready

        logger.info(
            "语音输入：%s（%s）",
            "录音上传后端转写" if asr_ready() else "未配 ASR_BASE_URL，退回浏览器识别",
            asr_label(),
        )
    except Exception:
        logger.exception("语音识别配置读取失败（不影响启动）")

    yield

    try:
        from app.amap.client import close_amap_client

        await close_amap_client()
    except Exception:
        logger.exception("关闭高德客户端时出错")
    try:
        from app.assistant.llm import close_llm_client

        await close_llm_client()
    except Exception:
        logger.exception("关闭模型客户端时出错")
    try:
        from app.assistant.speech import close_asr_client

        await close_asr_client()
    except Exception:
        logger.exception("关闭转写客户端时出错")
    logger.info("%s shutting down", settings.app_name)


def _amap_status(kind: str) -> int:
    return {
        "auth": 500,  # our .env is wrong, not the caller's request
        "quota": 429,
        "rate_limit": 429,
        "param": 400,
        "unreachable": 422,
        "transport": 502,
    }.get(kind, 502)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.amap.errors import AmapError
    from app.api.routes_assistant import router as assistant_router
    from app.api.routes_city import router as city_router
    from app.api.routes_config import router as config_router
    from app.api.routes_health import router as health_router
    from app.api.routes_optimize import router as optimize_router
    from app.api.routes_poi import router as poi_router
    from app.api.routes_trips import router as trips_router
    from app.auth.routes_auth import router as auth_router

    @app.exception_handler(AmapError)
    async def handle_amap_error(request: Request, exc: AmapError) -> JSONResponse:
        logger.warning("高德调用失败 %s %s", request.url.path, exc)
        return JSONResponse(
            status_code=_amap_status(exc.kind),
            content={
                "detail": {
                    "message": exc.message,
                    "hint": exc.hint,
                    "infocode": exc.infocode,
                    "kind": str(exc.kind),
                }
            },
        )

    app.include_router(health_router)
    app.include_router(config_router)
    app.include_router(trips_router)
    app.include_router(poi_router)
    app.include_router(optimize_router)
    app.include_router(city_router)
    app.include_router(auth_router)
    app.include_router(assistant_router)

    # M36 封面文件。必须注册在下面 "/" 那个 SPA catch-all **之前**：Starlette 按注册顺序
    # 匹配 mount，反过来所有 /uploads/... 都会先撞上 SPA 处理器，拿回一个 index.html。
    # StaticFiles 遇到不存在的目录是直接抛的，而抛在这里等于因为一个可选功能拒绝启动。
    from fastapi.staticfiles import StaticFiles

    try:
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")
    except Exception:
        logger.exception("封面目录不可用（上传的图片将读不到）")

    @app.websocket("/ws/trips/{trip_id}")
    async def ws_trip_endpoint(websocket: WebSocket, trip_id: str) -> None:
        from app.db.database import get_db
        from app.db.repositories import get_snapshot
        from app.ws.connection import ClientConnection
        from app.ws.hub import get_hub

        snapshot = await get_snapshot(get_db(), trip_id)
        if snapshot is None:
            # Accept-then-close so the browser sees a clean close code (4404) instead
            # of an opaque handshake failure it cannot tell apart from a dead server.
            await websocket.accept()
            await websocket.close(code=4404, reason="trip not found")
            return

        await websocket.accept()
        conn = ClientConnection(websocket, get_hub(), trip_id)
        try:
            await conn.run()
        finally:
            await conn.finish()

    # A1: serve the built frontend from THIS port -- one origin, one port, zero CORS,
    # and a share link that works for anyone on the LAN. Dev keeps Vite + proxy; this
    # only activates after `vite build` and is skipped (silently) when dist is absent.
    # Registered LAST: the catch-all mount must not shadow /api or the WS endpoint
    # above (Starlette matches in registration order).
    if settings.frontend_dist.is_dir() and (settings.frontend_dist / "index.html").is_file():

        class SPAStaticFiles(StaticFiles):
            """Serve index.html for client-side routes (/trip/xxx) while keeping real
            404s for missing assets -- an asset asking for HTML would otherwise fail
            with a baffling MIME error in the browser."""

            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code != 404 or "." in Path(path).name:
                        raise
                    return await super().get_response("index.html", scope)

        app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="frontend")

    return app


app = create_app()
