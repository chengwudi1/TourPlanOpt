"""Auth REST endpoints. Cookie-session based, optional for every existing flow."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth import accounts
from app.db.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

NAME_MAX = 20
PASSWORD_MIN = 6


def _session_token(request: Request) -> str | None:
    return request.cookies.get(accounts.COOKIE_NAME)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        accounts.COOKIE_NAME,
        token,
        max_age=accounts.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=False,  # LAN/http deployment; flip when behind TLS
    )


async def current_user(request: Request) -> dict | None:
    """Dependency-free helper other routers can reuse for optional auth."""
    token = _session_token(request)
    if not token:
        return None
    return await accounts.user_for_token(get_db(), token)


@router.post("/register")
async def register(body: dict, response: Response) -> dict:
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    if not name or len(name) > NAME_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"昵称需要 1-{NAME_MAX} 个字")
    if len(password) < PASSWORD_MIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"密码至少 {PASSWORD_MIN} 位")

    db = get_db()
    if await accounts.find_user_by_name(db, name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个名字已被注册")
    user = await accounts.create_user(db, name, password)
    token = await accounts.create_session(db, user["id"])
    _set_cookie(response, token)
    return {"user": {"id": user["id"], "name": user["name"]}}


@router.post("/login")
async def login(body: dict, response: Response) -> dict:
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    db = get_db()
    user = await accounts.find_user_by_name(db, name)
    # Same error for unknown name and wrong password: never leak which names exist.
    if user is None or not accounts.verify_password(password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "昵称或密码不对")
    token = await accounts.create_session(db, user["id"])
    _set_cookie(response, token)
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
