"""Shared auth dependencies for API routes."""

import hashlib
import hmac

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentgate.core.config import settings

bearer_scheme = HTTPBearer()
bearer_scheme_optional = HTTPBearer(auto_error=False)


def hash_api_key(key: str) -> str:
    """Hash an API key with SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def is_admin_key(candidate: str | None, admin_key: str | None = None) -> bool:
    """Timing-safe compare against the admin API key.

    `admin_key` lets callers pass their own copy of `settings.api_key`, which
    is important when the caller's module is the one being mocked in tests.
    Falls back to the module's own `settings.api_key` otherwise.
    """
    key = admin_key if admin_key is not None else settings.api_key
    if not key or not candidate:
        return False
    return hmac.compare_digest(candidate, key)


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API key not configured on server")
    if not is_admin_key(credentials.credentials, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
