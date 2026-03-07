"""Shared auth dependencies for API routes."""

import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentgate.core.config import settings

bearer_scheme = HTTPBearer()
bearer_scheme_optional = HTTPBearer(auto_error=False)


def hash_api_key(key: str) -> str:
    """Hash an API key with SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API key not configured on server")
    if credentials.credentials != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
