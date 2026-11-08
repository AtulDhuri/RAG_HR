from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from app.config import get_settings


@dataclass(frozen=True)
class AdminPrincipal:
    username: str
    role: str = "admin"


_tokens: dict[str, tuple[AdminPrincipal, float]] = {}


def authenticate_admin(username: str, password: str) -> tuple[str, int] | None:
    settings = get_settings()
    expected_username = settings.admin_username
    expected_password = settings.admin_password.get_secret_value()
    if not secrets.compare_digest(username, expected_username):
        return None
    if not secrets.compare_digest(password, expected_password):
        return None
    token = secrets.token_urlsafe(32)
    expires_in = settings.auth_token_ttl_seconds
    _tokens[token] = (AdminPrincipal(username=username), time.time() + expires_in)
    _prune_expired_tokens()
    return token, expires_in


def require_admin(authorization: str | None = Header(default=None)) -> AdminPrincipal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    record = _tokens.get(token)
    if not record or record[1] <= time.time():
        _tokens.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record[0]


def current_tenant(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> str:
    """Keep the development UI single-tenant and prevent caller-selected namespaces."""
    tenant_id = get_settings().default_tenant_id
    if x_tenant_id and x_tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unknown tenant")
    return tenant_id


def _prune_expired_tokens() -> None:
    now = time.time()
    for token, (_, expires_at) in list(_tokens.items()):
        if expires_at <= now:
            _tokens.pop(token, None)
