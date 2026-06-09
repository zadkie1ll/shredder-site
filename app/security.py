import base64
import hashlib
import hmac
import os
import time


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 260_000
    )
    return (
        "pbkdf2_sha256$260000$"
        + base64.b64encode(salt).decode("ascii")
        + "$"
        + base64.b64encode(password_hash).decode("ascii")
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def verify_telegram_login(
    payload: dict[str, str],
    bot_token: str,
    max_age_seconds: int,
) -> bool:
    received_hash = payload.get("hash")
    auth_date = payload.get("auth_date")
    if not received_hash or not auth_date:
        return False

    try:
        auth_timestamp = int(auth_date)
    except ValueError:
        return False
    age_seconds = time.time() - auth_timestamp
    if age_seconds < -30 or age_seconds > max_age_seconds:
        return False

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(payload.items())
        if key != "hash" and value is not None
    )
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_hash, received_hash)
