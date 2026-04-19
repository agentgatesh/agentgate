"""Shared FastAPI dependencies (auth resolvers).

Uses late access to `routes.async_session` and `routes.settings` so that
tests patching those attributes on the routes module still reach the
dependency.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import or_, select

from agentgate.db.models import Organization
from agentgate.server.auth import bearer_scheme, hash_api_key, is_admin_key


async def verify_api_key_or_org(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Organization | None:
    """Check admin key or org key. Returns org if org-scoped, None if admin.

    Matches both the primary api_key_hash and the grace-period
    secondary_api_key_hash so callers can roll credentials without
    downtime — see POST /account/api/rotate-key.
    """
    from agentgate.server import routes

    if is_admin_key(credentials.credentials, routes.settings.api_key):
        return None
    key_hash = hash_api_key(credentials.credentials)
    async with routes.async_session() as session:
        result = await session.execute(
            select(Organization).where(
                or_(
                    Organization.api_key_hash == key_hash,
                    Organization.secondary_api_key_hash == key_hash,
                )
            )
        )
        org = result.scalar_one_or_none()
        if org:
            return org
    raise HTTPException(status_code=401, detail="Invalid API key")
