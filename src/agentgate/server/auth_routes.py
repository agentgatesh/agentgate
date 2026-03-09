"""User authentication routes — email/password + Google/GitHub OAuth."""

import hashlib
import hmac
import json
import os
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Organization
from agentgate.server.auth import hash_api_key

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_EXPIRY = 86400 * 7  # 7 days
COOKIE_NAME = "session"


# ---------------------------------------------------------------------------
# Password helpers (PBKDF2-SHA256, stdlib only)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return key.hex() == key_hex


# ---------------------------------------------------------------------------
# Session cookie helpers (HMAC-SHA256, same pattern as admin panel)
# ---------------------------------------------------------------------------


def _make_session(org_id: str, email: str) -> str:
    payload = json.dumps({
        "org_id": org_id,
        "email": email,
        "exp": int(time.time()) + SESSION_EXPIRY,
    })
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_session(token: str) -> dict | None:
    try:
        payload_str, sig = token.rsplit("|", 1)
    except ValueError:
        return None
    expected = hmac.new(
        settings.secret_key.encode(), payload_str.encode(), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    payload = json.loads(payload_str)
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def _set_session_cookie(response: Response, org_id: str, email: str) -> None:
    token = _make_session(org_id, email)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_EXPIRY,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        path="/",
    )


async def get_current_user(request: Request) -> Organization | None:
    """Read session cookie and return the Organization, or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = _verify_session(token)
    if not payload:
        return None
    org_id = payload.get("org_id")
    if not org_id:
        return None
    async with async_session() as session:
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Email / password login
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(request: Request):
    from agentgate.server.ratelimit import auth_limiter

    client_ip = request.client.host if request.client else "unknown"
    if not auth_limiter.allow(f"login:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    async with async_session() as session:
        result = await session.execute(
            select(Organization).where(Organization.email == email)
        )
        org = result.scalar_one_or_none()

    if not org or not org.password_hash or not verify_password(password, org.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    response = Response(
        content=json.dumps({
            "message": "Login successful",
            "org_id": str(org.id),
            "org_name": org.name,
        }),
        media_type="application/json",
    )
    _set_session_cookie(response, str(org.id), org.email or "")
    return response


@router.post("/logout")
async def logout():
    response = Response(
        content=json.dumps({"message": "Logged out"}),
        media_type="application/json",
    )
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def get_me(request: Request):
    """Return current user info from session cookie."""
    org = await get_current_user(request)
    if not org:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "org_id": str(org.id),
        "org_name": org.name,
        "email": org.email,
        "tier": org.tier,
    }


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/google")
async def google_login_redirect(request: Request):
    if not settings.google_client_id:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    redirect_uri = f"{settings.base_url}/auth/google/callback"

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        key="oauth_state", value=state, max_age=600,
        httponly=True, secure=not settings.debug, samesite="lax", path="/",
    )
    return response


@router.get("/google/callback")
async def google_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    stored_state = request.cookies.get("oauth_state")

    if not code or not state or state != stored_state:
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    redirect_uri = f"{settings.base_url}/auth/google/callback"

    try:
        async with httpx.AsyncClient() as client:
            # Exchange code for token
            token_resp = await client.post(GOOGLE_TOKEN_URL, data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            })
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return RedirectResponse(url="/login?error=token_failed", status_code=302)

            # Get user info
            user_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_info = user_resp.json()
    except Exception:
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    email = user_info.get("email", "").lower()
    name = user_info.get("name", "")
    google_id = user_info.get("sub", "")

    if not email:
        return RedirectResponse(url="/login?error=no_email", status_code=302)

    org = await _find_or_create_oauth_org(email, name, "google", google_id)

    response = RedirectResponse(url="/account", status_code=302)
    _set_session_cookie(response, str(org.id), org.email or "")
    response.delete_cookie("oauth_state", path="/")
    return response


# ---------------------------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------------------------

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


@router.get("/github")
async def github_login_redirect(request: Request):
    if not settings.github_client_id:
        raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

    state = secrets.token_urlsafe(32)
    redirect_uri = f"{settings.base_url}/auth/github/callback"

    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",
        "state": state,
    }
    url = f"{GITHUB_AUTH_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        key="oauth_state", value=state, max_age=600,
        httponly=True, secure=not settings.debug, samesite="lax", path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    stored_state = request.cookies.get("oauth_state")

    if not code or not state or state != stored_state:
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    redirect_uri = f"{settings.base_url}/auth/github/callback"

    try:
        async with httpx.AsyncClient() as client:
            # Exchange code for token
            token_resp = await client.post(GITHUB_TOKEN_URL, data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }, headers={"Accept": "application/json"})
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return RedirectResponse(url="/login?error=token_failed", status_code=302)

            # Get user info
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            user_resp = await client.get(GITHUB_USER_URL, headers=headers)
            user_info = user_resp.json()

            # Get primary email
            email = user_info.get("email", "")
            if not email:
                emails_resp = await client.get(GITHUB_EMAILS_URL, headers=headers)
                emails = emails_resp.json()
                for e in emails:
                    if e.get("primary") and e.get("verified"):
                        email = e["email"]
                        break
    except Exception:
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    email = (email or "").lower()
    name = user_info.get("login", "") or user_info.get("name", "")
    github_id = str(user_info.get("id", ""))

    if not email:
        return RedirectResponse(url="/login?error=no_email", status_code=302)

    org = await _find_or_create_oauth_org(email, name, "github", github_id)

    response = RedirectResponse(url="/account", status_code=302)
    _set_session_cookie(response, str(org.id), org.email or "")
    response.delete_cookie("oauth_state", path="/")
    return response


# ---------------------------------------------------------------------------
# Shared OAuth helper
# ---------------------------------------------------------------------------


async def _find_or_create_oauth_org(
    email: str, name: str, provider: str, provider_id: str,
) -> Organization:
    """Find existing org by email or OAuth ID, or create a new one."""
    async with async_session() as session:
        # First try to find by OAuth provider + ID
        result = await session.execute(
            select(Organization).where(
                Organization.oauth_provider == provider,
                Organization.oauth_id == provider_id,
            )
        )
        org = result.scalar_one_or_none()
        if org:
            return org

        # Try to find by email (link existing account)
        result = await session.execute(
            select(Organization).where(Organization.email == email)
        )
        org = result.scalar_one_or_none()
        if org:
            # Link OAuth to existing account
            org.oauth_provider = provider
            org.oauth_id = provider_id
            await session.commit()
            await session.refresh(org)
            return org

        # Create new org
        api_key = secrets.token_urlsafe(32)
        # Generate unique org name from email or provider username
        org_name = name.lower().replace(" ", "-")[:50] or email.split("@")[0]
        # Ensure unique name
        existing = await session.execute(
            select(Organization).where(Organization.name == org_name)
        )
        if existing.scalar_one_or_none():
            org_name = f"{org_name}-{secrets.token_hex(3)}"

        org = Organization(
            name=org_name,
            email=email,
            api_key_hash=hash_api_key(api_key),
            oauth_provider=provider,
            oauth_id=provider_id,
            tier="free",
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org
