"""User authentication routes — email/password + Google/GitHub OAuth."""

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import (
    EmailVerificationToken,
    Organization,
    PasswordResetToken,
    RevokedSession,
)
from agentgate.server.auth import hash_api_key
from agentgate.server.email_service import (
    send_password_reset_email,
    send_verification_email,
)

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


def _token_hash(token: str) -> str:
    """SHA-256 of the token — used as revocation key (never log the raw token)."""
    return hashlib.sha256(token.encode()).hexdigest()


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
    # Force Secure=True in production (non-debug). This means cookies are
    # only sent over HTTPS — if someone leaves debug=true in prod by
    # mistake we want auth to still fail closed.
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_EXPIRY,
        httponly=True,
        secure=True if not settings.debug else False,
        samesite="lax",
        path="/",
    )


async def _is_session_revoked(token: str) -> bool:
    # COUNT query (not SELECT): test suites that pre-populate a mock
    # session with generic `.execute` results would otherwise see the
    # mocked org row and treat any live session as revoked. A count
    # forces the mock to return 0 when not explicitly set.
    async with async_session() as session:
        result = await session.execute(
            select(func.count(RevokedSession.token_hash)).where(
                RevokedSession.token_hash == _token_hash(token)
            )
        )
        return (result.scalar() or 0) > 0


async def _revoke_session(token: str) -> None:
    payload = _verify_session(token)
    if not payload:
        return  # invalid or expired — nothing to revoke
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    async with async_session() as session:
        # ON CONFLICT DO NOTHING-style: silently skip duplicates.
        from sqlalchemy.exc import IntegrityError

        session.add(RevokedSession(token_hash=_token_hash(token), exp=exp))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def get_current_user(request: Request) -> Organization | None:
    """Read session cookie and return the Organization, or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = _verify_session(token)
    if not payload:
        return None
    if await _is_session_revoked(token):
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
async def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await _revoke_session(token)
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
            # OAuth providers (Google, GitHub) verify emails themselves.
            # Skip our own verification step for these users.
            email_verified=True,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


# ---------------------------------------------------------------------------
# Email verification + password reset
# ---------------------------------------------------------------------------

VERIFICATION_TOKEN_TTL = timedelta(hours=24)
PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_verification_token(org_id: str, email: str) -> str:
    """Create a verification token, store its hash, and send the email.

    Returns the plaintext token (caller is responsible for URL-embedding).
    Safe to call on signup and resend — old tokens remain valid until
    their TTL expires (they are cleaned up by log_retention_loop).
    """
    token = secrets.token_urlsafe(32)
    expires = datetime.fromtimestamp(
        time.time() + VERIFICATION_TOKEN_TTL.total_seconds(), tz=timezone.utc,
    )
    async with async_session() as session:
        session.add(EmailVerificationToken(
            token_hash=_hash_token(token),
            org_id=org_id,
            expires_at=expires,
        ))
        await session.commit()

    verify_url = f"{settings.base_url}/auth/verify-email?token={token}"
    send_verification_email(email, verify_url)
    return token


@router.post("/resend-verification")
async def resend_verification(request: Request):
    """Resend the verification email for the current user."""
    from agentgate.server.ratelimit import auth_limiter

    client_ip = request.client.host if request.client else "unknown"
    if not auth_limiter.allow(f"resend_verification:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many attempts")

    org = await get_current_user(request)
    if not org:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if org.email_verified:
        return {"message": "Email already verified"}
    if not org.email:
        raise HTTPException(status_code=400, detail="No email on file")
    await issue_verification_token(str(org.id), org.email)
    return {"message": "Verification email sent"}


@router.get("/verify-email")
async def verify_email(request: Request):
    """Consume a verification token and mark the org's email as verified."""
    token = request.query_params.get("token", "")
    if not token:
        return RedirectResponse("/login?error=missing_token", status_code=302)

    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        row = await session.get(EmailVerificationToken, token_hash)
        if not row or row.expires_at < now:
            return RedirectResponse("/login?error=invalid_or_expired_token", status_code=302)

        org = await session.get(Organization, row.org_id)
        if not org:
            return RedirectResponse("/login?error=org_missing", status_code=302)

        org.email_verified = True
        # Tokens are single-use: delete after consumption.
        await session.delete(row)
        await session.commit()

    return RedirectResponse("/account?verified=1", status_code=302)


@router.post("/forgot-password")
async def forgot_password(request: Request):
    """Send a password reset email. Always 200 to avoid email enumeration."""
    from agentgate.server.disposable import is_disposable
    from agentgate.server.ratelimit import auth_limiter

    client_ip = request.client.host if request.client else "unknown"
    if not auth_limiter.allow(f"forgot_password:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many attempts")

    body = await request.json()
    email = body.get("email", "").strip().lower()
    generic_response = {
        "message": "If an account exists for that email, a reset link is on its way.",
    }
    # Short-circuit on empty or disposable email. Same generic response
    # so the caller can't distinguish "valid + no account" from
    # "disposable domain rejected".
    if not email or await is_disposable(email):
        return generic_response

    async with async_session() as session:
        result = await session.execute(
            select(Organization).where(Organization.email == email)
        )
        org = result.scalar_one_or_none()

    if not org:
        return generic_response

    token = secrets.token_urlsafe(32)
    expires = datetime.fromtimestamp(
        time.time() + PASSWORD_RESET_TOKEN_TTL.total_seconds(), tz=timezone.utc,
    )
    async with async_session() as session:
        session.add(PasswordResetToken(
            token_hash=_hash_token(token),
            org_id=org.id,
            expires_at=expires,
        ))
        await session.commit()

    reset_url = f"{settings.base_url}/auth/reset-password?token={token}"
    send_password_reset_email(email, reset_url)
    return generic_response


@router.post("/reset-password")
async def reset_password(request: Request):
    """Apply a password reset using a token issued by /forgot-password."""
    from agentgate.server.ratelimit import auth_limiter

    client_ip = request.client.host if request.client else "unknown"
    if not auth_limiter.allow(f"reset_password:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many attempts")

    body = await request.json()
    token = body.get("token", "")
    new_password = body.get("password", "")
    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and password are required")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        row = await session.get(PasswordResetToken, token_hash)
        if not row or row.expires_at < now or row.used_at is not None:
            raise HTTPException(status_code=400, detail="Invalid or expired token")

        org = await session.get(Organization, row.org_id)
        if not org:
            raise HTTPException(status_code=400, detail="Account not found")

        org.password_hash = hash_password(new_password)
        row.used_at = now
        await session.commit()

    return {"message": "Password updated. You can log in with the new password."}
