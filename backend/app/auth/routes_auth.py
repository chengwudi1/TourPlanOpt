"""Auth REST endpoints. Cookie-session based, optional for every existing flow."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth import accounts
from app.db.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

NAME_MAX = 20
PASSWORD_MIN = 6
# PBKDF2 的开销随输入线性涨：不设上限时一段 10MB 的「密码」就是一次免费的 CPU 钉死攻击，
# 而且注册与登录两条路都会走到哈希。正常密码碰不到 128，这条闸只挡灌包。
PASSWORD_MAX = 128


def _session_token(request: Request) -> str | None:
    return request.cookies.get(accounts.COOKIE_NAME)


def _is_https(request: Request) -> bool:
    # 公网部署走 Caddy 反代，到后端这一跳永远是 http，协议真相在转发头里。
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        accounts.COOKIE_NAME,
        token,
        max_age=accounts.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,  # https 访问才置位：本机 http://localhost 下 Secure 会直接把会话丢掉
    )


async def current_user(request: Request) -> dict | None:
    """Dependency-free helper other routers can reuse for optional auth."""
    token = _session_token(request)
    if not token:
        return None
    return await accounts.user_for_token(get_db(), token)


@router.post("/register")
async def register(body: dict, request: Request, response: Response) -> dict:
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    if not name or len(name) > NAME_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"昵称需要 1-{NAME_MAX} 个字")
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"密码长度需要在 {PASSWORD_MIN}-{PASSWORD_MAX} 位之间"
        )

    db = get_db()
    if await accounts.find_user_by_name(db, name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个名字已被注册")
    user = await accounts.create_user(db, name, password)
    token = await accounts.create_session(db, user["id"])
    _set_cookie(response, token, secure=_is_https(request))
    return {"user": {"id": user["id"], "name": user["name"]}}


@router.post("/login")
async def login(body: dict, request: Request, response: Response) -> dict:
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    # 上限要在进 PBKDF2 之前拦：验证路径同样要迭代整段输入，来者是不是正确密码不重要。
    if len(password) > PASSWORD_MAX:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "昵称或密码不对")
    db = get_db()
    user = await accounts.find_user_by_name(db, name)
    # Same error for unknown name and wrong password: never leak which names exist.
    if user is None or not accounts.verify_password(password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "昵称或密码不对")
    token = await accounts.create_session(db, user["id"])
    _set_cookie(response, token, secure=_is_https(request))
    return {"user": {"id": user["id"], "name": user["name"]}}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = _session_token(request)
    if token:
        await accounts.drop_session(get_db(), token)
    response.delete_cookie(accounts.COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict:
    user = await current_user(request)
    return {"user": user}  # {"user": null} is a valid, non-error answer


@router.get("/trips")
async def my_trips(request: Request) -> dict:
    user = await current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "先登录")
    trips = await accounts.my_trips(get_db(), user["id"])
    return {"trips": trips}


@router.delete("/trips/{trip_id}")
async def unfollow_trip(trip_id: str, request: Request) -> dict:
    """把一段行程从「我的活动」里移走：只删我自己的那条足迹，行程照旧存在，别人手里的
    链接照样能开。匿名用户没有服务端足迹可删（本机那份「移出首页」是本地清单），所以这里
    直接 401，而不是假装成功。"""
    user = await current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "先登录")
    removed = await accounts.unfollow(get_db(), trip_id, user["id"])
    return {"trip_id": trip_id, "removed": removed}


@router.get("/prefs")
async def get_prefs(request: Request) -> dict:
    """读我自己的偏好。未登录返回空对象而不是 401：前端在登录态确认前就要开面板，
    拿一份空偏好渲染默认值是正常路径，不是错误。"""
    user = await current_user(request)
    if user is None:
        return {"prefs": {}}
    return {"prefs": await accounts.get_prefs(get_db(), user["id"])}


@router.put("/prefs")
async def put_prefs(request: Request) -> dict:
    """存偏好。这里刻意先读原始请求体再自己解析：FastAPI 的 `body: dict` 会在 JSON
    坏掉时抛 400 之外的 500，而超长要在解析前就拦住——解析一兆垃圾再量长度没意义。"""
    user = await current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "先登录")

    raw = await request.body()
    if len(raw) > accounts.PREF_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "偏好数据太长了")
    try:
        parsed = json.loads(raw or b"{}")
        prefs = accounts.sanitize_prefs(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    merged = await accounts.put_prefs(get_db(), user["id"], prefs)
    return {"prefs": merged}
