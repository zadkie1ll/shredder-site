from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import parse, request
from urllib.error import HTTPError, URLError

from app.config import settings


YANDEX_AUTHORIZE_URL = "https://oauth.yandex.com/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.com/token"
YANDEX_USER_INFO_URL = "https://login.yandex.ru/info"


class YandexOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class YandexUserInfo:
    provider_user_id: str
    email: str
    display_name: str | None = None


def yandex_oauth_enabled() -> bool:
    return bool(settings.yandex_oauth_client_id and settings.yandex_oauth_client_secret)


def yandex_origin() -> str:
    parsed = parse.urlparse(settings.public_base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def yandex_redirect_uri() -> str:
    return f"{settings.public_base_url}/auth/yandex/callback"


def yandex_suggest_token_uri() -> str:
    return f"{settings.public_base_url}/auth/yandex/token"


def build_yandex_authorize_url(state: str) -> str:
    if not settings.yandex_oauth_client_id:
        raise YandexOAuthError("Yandex OAuth is not configured.")

    params = {
        "response_type": "code",
        "client_id": settings.yandex_oauth_client_id,
        "redirect_uri": yandex_redirect_uri(),
        "scope": settings.yandex_oauth_scopes,
        "state": state,
    }
    return f"{YANDEX_AUTHORIZE_URL}?{parse.urlencode(params)}"


def exchange_yandex_code(code: str) -> str:
    if not settings.yandex_oauth_client_id or not settings.yandex_oauth_client_secret:
        raise YandexOAuthError("Yandex OAuth is not configured.")

    payload = parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.yandex_oauth_client_id,
            "client_secret": settings.yandex_oauth_client_secret,
        }
    ).encode("utf-8")
    token_request = request.Request(
        YANDEX_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    response = _read_json(token_request)
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise YandexOAuthError("Yandex OAuth did not return an access token.")
    return access_token


def fetch_yandex_user(access_token: str) -> YandexUserInfo:
    url = f"{YANDEX_USER_INFO_URL}?{parse.urlencode({'format': 'json'})}"
    info_request = request.Request(
        url,
        headers={"Authorization": f"OAuth {access_token}"},
        method="GET",
    )
    response = _read_json(info_request)
    provider_user_id = str(response.get("id") or "").strip()
    email = str(response.get("default_email") or "").strip().lower()
    display_name = (
        response.get("display_name")
        or response.get("real_name")
        or response.get("login")
    )

    if not provider_user_id:
        raise YandexOAuthError("Yandex ID did not return a user id.")
    if not email or "@" not in email:
        raise YandexOAuthError("Yandex ID did not return a usable email.")

    return YandexUserInfo(
        provider_user_id=provider_user_id,
        email=email,
        display_name=str(display_name).strip() if display_name else None,
    )


def _read_json(http_request: request.Request) -> dict:
    try:
        with request.urlopen(http_request, timeout=10) as response:
            raw_body = response.read()
    except HTTPError as exc:
        raw_body = exc.read()
        try:
            error_body = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_body = {}
        description = (
            error_body.get("error_description")
            or error_body.get("error")
            or f"HTTP {exc.code}"
        )
        raise YandexOAuthError(f"Yandex OAuth request failed: {description}") from exc
    except URLError as exc:
        raise YandexOAuthError("Yandex OAuth request failed.") from exc

    try:
        response_body = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise YandexOAuthError("Yandex OAuth returned invalid JSON.") from exc
    if not isinstance(response_body, dict):
        raise YandexOAuthError("Yandex OAuth returned invalid JSON.")
    return response_body
