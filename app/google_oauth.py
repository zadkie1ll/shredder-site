from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import parse, request
from urllib.error import HTTPError, URLError

from app.config import settings


GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_INFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleUserInfo:
    provider_user_id: str
    email: str
    display_name: str | None = None


def google_oauth_enabled() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def google_redirect_uri() -> str:
    return f"{settings.public_base_url}/auth/google/callback"


def build_google_authorize_url(state: str) -> str:
    if not google_oauth_enabled():
        raise GoogleOAuthError("Google OAuth is not configured.")

    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": settings.google_oauth_scopes,
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{parse.urlencode(params)}"


def exchange_google_code(code: str) -> str:
    if not google_oauth_enabled():
        raise GoogleOAuthError("Google OAuth is not configured.")

    payload = parse.urlencode(
        {
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": google_redirect_uri(),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    token_request = request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    response = _read_json(token_request)
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleOAuthError("Google OAuth did not return an access token.")
    return access_token


def fetch_google_user(access_token: str) -> GoogleUserInfo:
    info_request = request.Request(
        GOOGLE_USER_INFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    response = _read_json(info_request)
    provider_user_id = str(response.get("sub") or "").strip()
    email = str(response.get("email") or "").strip().lower()
    display_name = response.get("name") or response.get("given_name")
    email_verified = response.get("email_verified")

    if not provider_user_id:
        raise GoogleOAuthError("Google did not return a user id.")
    if not email or "@" not in email:
        raise GoogleOAuthError("Google did not return a usable email.")
    if email_verified is False:
        raise GoogleOAuthError("Google did not return a verified email.")

    return GoogleUserInfo(
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
        raise GoogleOAuthError(f"Google OAuth request failed: {description}") from exc
    except URLError as exc:
        raise GoogleOAuthError("Google OAuth request failed.") from exc

    try:
        response_body = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GoogleOAuthError("Google OAuth returned invalid JSON.") from exc
    if not isinstance(response_body, dict):
        raise GoogleOAuthError("Google OAuth returned invalid JSON.")
    return response_body
