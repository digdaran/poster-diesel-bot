"""Хеширование паролей (argon2id) и JWT (access + refresh), без cookie (п.11.1, 18.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.permissions import PanelRole

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


@dataclass(frozen=True)
class DecodedToken:
    user_id: int
    role: PanelRole
    token_type: TokenType
    jti: str
    expires_at: dt.datetime


class TokenError(Exception):
    """Невалидный, просроченный или неверного типа токен."""


def _encode(
    *,
    user_id: int,
    role: PanelRole,
    token_type: TokenType,
    ttl: dt.timedelta,
    secret: str,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "type": token_type,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def create_access_token(*, user_id: int, role: PanelRole, secret: str, ttl_minutes: int) -> str:
    return _encode(
        user_id=user_id,
        role=role,
        token_type="access",
        ttl=dt.timedelta(minutes=ttl_minutes),
        secret=secret,
    )


def create_refresh_token(*, user_id: int, role: PanelRole, secret: str, ttl_days: int) -> str:
    return _encode(
        user_id=user_id,
        role=role,
        token_type="refresh",
        ttl=dt.timedelta(days=ttl_days),
        secret=secret,
    )


def decode_token(
    token: str, *, secret: str, expected_type: TokenType | None = None
) -> DecodedToken:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Токен просрочен") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Невалидный токен") from exc

    raw_token_type = payload.get("type")
    if raw_token_type not in ("access", "refresh"):
        raise TokenError(f"Некорректный тип токена: {raw_token_type!r}")
    token_type: TokenType = raw_token_type
    if expected_type is not None and token_type != expected_type:
        raise TokenError(f"Ожидался токен типа {expected_type}, получен {token_type}")

    try:
        role = PanelRole(payload["role"])
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Некорректная полезная нагрузка токена") from exc

    return DecodedToken(
        user_id=user_id,
        role=role,
        token_type=token_type,
        jti=payload["jti"],
        expires_at=dt.datetime.fromtimestamp(payload["exp"], tz=dt.timezone.utc),
    )
