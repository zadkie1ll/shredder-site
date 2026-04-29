import hmac
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.remnawave import create_remnawave_user, get_remnawave_user
from app.repository import (
    create_user,
    generate_site_username,
    get_referrals,
    get_user_by_username,
)
from app.tariffs import get_tariffs


app = FastAPI(title="Shredder Site")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.environment == "production",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


def current_user(request: Request):
    username = request.session.get("username")
    if username is None:
        return None
    return get_user_by_username(username)


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return user


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": current_user(request),
            "tariffs": get_tariffs(),
        },
    )


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "user": current_user(request), "error": None},
    )


@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "user": current_user(request), "error": None},
    )


@app.post("/register")
async def register(
    request: Request,
    username: str = Form(""),
    password: str = Form(...),
):
    normalized_username = username.lower().strip()
    if not hmac.compare_digest(password, settings.login_password):
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "user": None,
                "error": "Неверный пароль доступа",
            },
            status_code=400,
        )

    if normalized_username and get_user_by_username(normalized_username) is not None:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "user": None,
                "error": "Такой логин уже занят",
            },
            status_code=400,
        )

    subscription_username = normalized_username or generate_site_username()
    remnawave_user = await create_remnawave_user(subscription_username)
    if remnawave_user is None:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "user": None,
                "error": "Не удалось создать подписку. Попробуй позже.",
            },
            status_code=502,
        )

    user = create_user(subscription_username, remnawave_user.expire_at)
    request.session["username"] = user.username
    return RedirectResponse("/cabinet", status_code=303)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    normalized_username = username.lower().strip()
    user = get_user_by_username(normalized_username)
    password_is_valid = hmac.compare_digest(password, settings.login_password)
    if user is None or not password_is_valid:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "user": None,
                "error": "Неверный логин или пароль",
            },
            status_code=400,
        )

    request.session["username"] = user.username
    return RedirectResponse("/cabinet", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


async def render_cabinet(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    referrals = get_referrals(user)
    remnawave_user = await get_remnawave_user(user.username)
    expire_at = remnawave_user.expire_at if remnawave_user else user.expire_at
    days_left = None
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        days_left = max((expire_at - datetime.now(timezone.utc)).days, 0)

    bonus_days = sum(referral.bonus_days for referral in referrals)

    return templates.TemplateResponse(
        "cabinet.html",
        {
            "request": request,
            "user": user,
            "referrals": referrals,
            "expire_at": expire_at,
            "days_left": days_left,
            "invited_count": len(referrals),
            "bonus_days": bonus_days,
            "tariffs": get_tariffs(),
            "subscription_url": remnawave_user.subscription_url if remnawave_user else None,
            "public_base_url": settings.public_base_url,
        },
    )


@app.get("/cabinet")
async def cabinet(request: Request):
    return await render_cabinet(request)


@app.get("/cabinet/setup")
def cabinet_setup(request: Request):
    return RedirectResponse("/cabinet#setup", status_code=303)


@app.get("/cabinet/profile")
def cabinet_profile(request: Request):
    return RedirectResponse("/cabinet#referrals", status_code=303)


@app.get("/cabinet/support")
def cabinet_support(request: Request):
    return RedirectResponse("/cabinet", status_code=303)
