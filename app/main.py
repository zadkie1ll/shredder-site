import hmac
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.email_delivery import send_registration_code
from app.one_click import build_one_click_links
from app.payments import create_payment_url
from app.remnawave import (
    create_remnawave_user,
    ensure_remnawave_user_internal_squads,
    get_remnawave_user,
    legacy_limited_subscription_squads,
    update_remnawave_user_after_telegram_link,
)
from app.repository import (
    authenticate_site_user,
    cancel_autopay,
    consume_pending_registration,
    create_oauth_user,
    create_pending_registration,
    create_telegram_user,
    generate_site_username,
    get_autopay_info,
    get_pending_registration,
    get_referrals,
    get_user_by_id,
    get_user_by_oauth_identity,
    get_user_by_telegram_id,
    get_user_by_username,
    initialize_site_storage,
    link_oauth_identity,
    link_telegram_account,
    normalize_email,
    user_has_site_password,
    verify_pending_registration_code,
)
from app.security import verify_telegram_login
from app.tariffs import get_tariffs
from app.tariffs import get_tariff_by_id
from app.yandex_oauth import (
    YandexOAuthError,
    build_yandex_authorize_url,
    exchange_yandex_code,
    fetch_yandex_user,
    yandex_oauth_enabled,
    yandex_origin,
    yandex_suggest_token_uri,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="Shredder Site")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.environment == "production",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def static_version() -> int:
    return int((STATIC_DIR / "styles.css").stat().st_mtime)


templates.env.globals["static_version"] = static_version


@app.on_event("startup")
def startup() -> None:
    initialize_site_storage()


def current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id is not None:
        return get_user_by_id(user_id)

    username = request.session.get("username")
    if username is None:
        return None
    return get_user_by_username(username)


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return user


def login_context(request: Request, error: str | None = None) -> dict:
    return {
        "request": request,
        "user": current_user(request),
        "error": error or request.session.pop("login_error", None),
        "telegram_bot_username": settings.telegram_bot_username,
        "telegram_auth_url": f"{settings.public_base_url}/auth/telegram/callback",
        "yandex_oauth_enabled": yandex_oauth_enabled(),
        "yandex_client_id": settings.yandex_oauth_client_id,
        "yandex_origin": yandex_origin(),
        "yandex_token_uri": yandex_suggest_token_uri(),
    }


def register_context(
    request: Request,
    error: str | None = None,
    *,
    email: str = "",
) -> dict:
    pending = get_pending_registration(
        request.session.get("pending_registration_token", "")
    )
    return {
        "request": request,
        "user": current_user(request),
        "error": error,
        "email": email or (pending.email if pending else ""),
        "pending_registration": pending,
    }


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
        login_context(request),
    )


@app.get("/register")
def register_page(request: Request):
    if request.query_params.get("reset") == "1":
        request.session.pop("pending_registration_token", None)
    return templates.TemplateResponse(
        "register.html",
        register_context(request),
    )


@app.post("/register")
async def register(
    request: Request,
    email: str = Form(""),
    password: str = Form(...),
    password_repeat: str = Form(...),
):
    normalized_email = normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                "Укажи корректную почту",
                email=normalized_email,
            ),
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                "Пароль должен быть не короче 6 символов",
                email=normalized_email,
            ),
            status_code=400,
        )
    if password != password_repeat:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                "Пароли не совпадают",
                email=normalized_email,
            ),
            status_code=400,
        )
    if get_user_by_username(normalized_email) is not None:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                "Такая почта уже используется",
                email=normalized_email,
            ),
            status_code=400,
        )

    try:
        pending = create_pending_registration(
            None,
            normalized_email,
            password,
        )
        await send_registration_code(pending.email, pending.code)
    except ValueError as exc:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                str(exc),
                email=normalized_email,
            ),
            status_code=400,
        )
    except Exception:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                "Не удалось отправить код на почту. Попробуй позже.",
                email=normalized_email,
            ),
            status_code=502,
        )

    request.session["pending_registration_token"] = pending.token
    return templates.TemplateResponse("register.html", register_context(request))


@app.post("/register/confirm")
async def confirm_registration(request: Request, code: str = Form("")):
    token = request.session.get("pending_registration_token", "")
    try:
        pending = verify_pending_registration_code(token, code)
    except ValueError:
        return templates.TemplateResponse(
            "register.html",
            register_context(request, "Неверный или просроченный код"),
            status_code=400,
        )

    remnawave_user = await create_remnawave_user(pending.username)
    if remnawave_user is None:
        return templates.TemplateResponse(
            "register.html",
            register_context(
                request,
                "Не удалось создать подписку. Попробуй позже.",
            ),
            status_code=502,
        )

    try:
        user = consume_pending_registration(pending, remnawave_user.expire_at)
    except ValueError:
        return templates.TemplateResponse(
            "register.html",
            register_context(request, "Такая почта уже используется"),
            status_code=400,
        )

    request.session.pop("pending_registration_token", None)
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    return RedirectResponse("/cabinet", status_code=303)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    normalized_username = username.lower().strip()
    user = authenticate_site_user(normalized_username, password)
    password_is_valid = user is not None
    if user is None:
        legacy_user = get_user_by_username(normalized_username)
        if legacy_user is not None and not user_has_site_password(legacy_user):
            password_is_valid = hmac.compare_digest(password, settings.login_password)
            if password_is_valid:
                user = legacy_user
    if user is None or not password_is_valid:
        return templates.TemplateResponse(
            "login.html",
            login_context(request, "Неверный логин или пароль"),
            status_code=400,
        )

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    return RedirectResponse("/cabinet", status_code=303)


@app.get("/auth/telegram/callback")
async def telegram_login_callback(request: Request):
    if not settings.telegram_bot_token:
        request.session["login_error"] = "Вход через Telegram пока не настроен."
        return RedirectResponse("/login", status_code=303)

    payload = {key: value for key, value in request.query_params.items()}
    if not verify_telegram_login(
        payload,
        settings.telegram_bot_token,
        settings.telegram_login_max_age_seconds,
    ):
        request.session["login_error"] = "Не удалось проверить вход через Telegram."
        return RedirectResponse("/login", status_code=303)

    try:
        telegram_id = int(payload["id"])
    except (KeyError, ValueError):
        request.session["login_error"] = "Telegram не передал id пользователя."
        return RedirectResponse("/login", status_code=303)

    user = get_user_by_telegram_id(telegram_id)
    if user is None:
        username = str(telegram_id)
        remnawave_user = await get_remnawave_user(username)
        if remnawave_user is None:
            remnawave_user = await create_remnawave_user(
                username,
                telegram_id=telegram_id,
            )
        if remnawave_user is None:
            request.session["login_error"] = (
                "Не удалось создать подписку. Попробуй позже."
            )
            return RedirectResponse("/login", status_code=303)

        try:
            user = create_telegram_user(
                telegram_id,
                remnawave_user.expire_at,
                payload.get("username"),
            )
        except Exception:
            request.session["login_error"] = (
                "Не удалось создать вход через Telegram. Попробуй позже."
            )
            return RedirectResponse("/login", status_code=303)

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    return RedirectResponse("/cabinet", status_code=303)


async def _login_yandex_user(request: Request, yandex_user):
    user = get_user_by_oauth_identity("yandex", yandex_user.provider_user_id)
    if user is None:
        existing_user = get_user_by_username(yandex_user.email)
        if existing_user is not None:
            user = link_oauth_identity(
                existing_user.id,
                "yandex",
                yandex_user.provider_user_id,
                yandex_user.email,
            )
        else:
            username = generate_site_username()
            remnawave_user = await create_remnawave_user(username)
            if remnawave_user is None:
                raise RuntimeError("remnawave user was not created")
            user = create_oauth_user(
                "yandex",
                yandex_user.provider_user_id,
                yandex_user.email,
                username,
                remnawave_user.expire_at,
            )

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    return user


@app.get("/auth/yandex/start")
def yandex_login_start(request: Request):
    if not yandex_oauth_enabled():
        request.session["login_error"] = "Вход через Яндекс пока не настроен."
        return RedirectResponse("/login", status_code=303)

    state = secrets.token_urlsafe(32)
    request.session["yandex_oauth_state"] = state
    try:
        authorize_url = build_yandex_authorize_url(state)
    except YandexOAuthError:
        request.session["login_error"] = "Вход через Яндекс пока не настроен."
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(authorize_url, status_code=303)


@app.get("/auth/yandex/callback")
async def yandex_login_callback(request: Request):
    expected_state = request.session.pop("yandex_oauth_state", None)
    returned_state = request.query_params.get("state")
    if not expected_state or returned_state != expected_state:
        request.session["login_error"] = "Не удалось проверить вход через Яндекс."
        return RedirectResponse("/login", status_code=303)

    if request.query_params.get("error"):
        request.session["login_error"] = "Яндекс не подтвердил вход."
        return RedirectResponse("/login", status_code=303)

    code = request.query_params.get("code", "").strip()
    if not code:
        request.session["login_error"] = "Яндекс не передал код входа."
        return RedirectResponse("/login", status_code=303)

    try:
        access_token = await run_in_threadpool(exchange_yandex_code, code)
        yandex_user = await run_in_threadpool(fetch_yandex_user, access_token)
    except YandexOAuthError:
        request.session["login_error"] = "Не удалось получить профиль Яндекса."
        return RedirectResponse("/login", status_code=303)

    try:
        await _login_yandex_user(request, yandex_user)
    except (RuntimeError, ValueError):
        request.session["login_error"] = (
            "Не удалось создать вход через Яндекс. Попробуй позже."
        )
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/cabinet", status_code=303)


@app.get("/auth/yandex/token")
def yandex_token_page(request: Request):
    if not yandex_oauth_enabled():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "yandex_token.html",
        {
            "request": request,
            "user": current_user(request),
            "yandex_origin": yandex_origin(),
        },
    )


@app.post("/auth/yandex/login")
async def yandex_widget_login(request: Request, access_token: str = Form("")):
    if not yandex_oauth_enabled():
        return JSONResponse(
            {"ok": False, "redirect": "/login", "error": "not_configured"},
            status_code=400,
        )
    if not access_token.strip():
        return JSONResponse(
            {"ok": False, "redirect": "/login", "error": "missing_token"},
            status_code=400,
        )

    try:
        yandex_user = await run_in_threadpool(fetch_yandex_user, access_token.strip())
        await _login_yandex_user(request, yandex_user)
    except (YandexOAuthError, RuntimeError, ValueError):
        request.session["login_error"] = "Не удалось войти через Яндекс."
        return JSONResponse(
            {"ok": False, "redirect": "/login", "error": "login_failed"},
            status_code=400,
        )
    return JSONResponse({"ok": True, "redirect": "/cabinet"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.post("/cabinet/pay")
async def create_payment(request: Request, tariff_id: str = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    tariff = get_tariff_by_id(tariff_id)
    payment_url = await create_payment_url(
        tariff=tariff,
        username=user.username,
        telegram_id=user.telegram_id,
    )
    return RedirectResponse(payment_url, status_code=303)


@app.post("/cabinet/autopay/cancel")
def cancel_autopay_action(request: Request, confirm: str = Form("")):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    if confirm != "yes":
        request.session["autopay_status"] = "cancel_not_confirmed"
        return RedirectResponse("/cabinet#subscription", status_code=303)

    request.session["autopay_status"] = (
        "canceled" if cancel_autopay(user) else "not_found"
    )
    return RedirectResponse("/cabinet#subscription", status_code=303)


@app.get("/cabinet/link/telegram/callback")
async def telegram_link_callback(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    if not settings.telegram_bot_token:
        request.session["telegram_link_status"] = "telegram_not_configured"
        return RedirectResponse("/cabinet#bonuses", status_code=303)

    payload = {key: value for key, value in request.query_params.items()}
    if not verify_telegram_login(
        payload,
        settings.telegram_bot_token,
        settings.telegram_login_max_age_seconds,
    ):
        request.session["telegram_link_status"] = "telegram_invalid"
        return RedirectResponse("/cabinet#bonuses", status_code=303)

    try:
        telegram_id = int(payload["id"])
        result = link_telegram_account(user.id, telegram_id)
    except Exception:
        request.session["telegram_link_status"] = "telegram_failed"
        return RedirectResponse("/cabinet#bonuses", status_code=303)

    remnawave_sync = await update_remnawave_user_after_telegram_link(
        result.user.username,
        result.user.expire_at,
        telegram_id,
        result.remnawave_username_to_disable,
    )
    request.session["user_id"] = result.user.id
    request.session["username"] = result.user.username
    if settings.remnawave_enabled and remnawave_sync is None:
        if result.bonus_days_added:
            request.session["telegram_link_status"] = (
                f"telegram_linked_bonus_sync_failed:{result.bonus_days_added}"
            )
        else:
            request.session["telegram_link_status"] = "telegram_linked_sync_failed"
    elif result.already_linked:
        request.session["telegram_link_status"] = "telegram_already_linked"
    elif result.bonus_days_added:
        request.session["telegram_link_status"] = f"telegram_linked_bonus:{result.bonus_days_added}"
    else:
        request.session["telegram_link_status"] = "telegram_linked"
    return RedirectResponse("/cabinet#bonuses", status_code=303)


async def render_cabinet(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    referrals = get_referrals(user)
    remnawave_user = await get_remnawave_user(user.username)
    legacy_squads = legacy_limited_subscription_squads()
    if remnawave_user and legacy_squads:
        required_squads = set(legacy_squads)
        current_squads = set(remnawave_user.active_internal_squads)
        if not required_squads.issubset(current_squads):
            remnawave_user = (
                await ensure_remnawave_user_internal_squads(user.username)
            ) or remnawave_user
    expire_at = remnawave_user.expire_at if remnawave_user else user.expire_at
    days_left = None
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        days_left = max((expire_at - datetime.now(timezone.utc)).days, 0)

    bonus_days = sum(referral.bonus_days for referral in referrals)
    telegram_link_status = request.session.pop("telegram_link_status", None)
    autopay_status = request.session.pop("autopay_status", None)
    subscription_url = remnawave_user.subscription_url if remnawave_user else None
    autopay_info = get_autopay_info(user)

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
            "subscription_url": subscription_url,
            "one_click_links": build_one_click_links(subscription_url),
            "public_base_url": settings.public_base_url,
            "telegram_bot_username": settings.telegram_bot_username,
            "telegram_link_bonus_days": settings.telegram_link_bonus_days,
            "telegram_link_status": telegram_link_status,
            "autopay_info": autopay_info,
            "autopay_status": autopay_status,
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
