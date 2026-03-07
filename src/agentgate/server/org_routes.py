"""Organization routes with org-scoped auth, billing, and rate limits."""

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import case, cast, func, select
from sqlalchemy.types import Date

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, TaskLog
from agentgate.server.schemas import AgentResponse, OrgCreate, OrgResponse, OrgUpdate

router = APIRouter(prefix="/orgs", tags=["organizations"])
bearer_scheme = HTTPBearer()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _get_org_by_key(session, credentials: HTTPAuthorizationCredentials) -> Organization:
    """Look up an organization by its API key hash (checks both primary and secondary)."""
    key_hash = _hash_key(credentials.credentials)
    result = await session.execute(
        select(Organization).where(
            (Organization.api_key_hash == key_hash)
            | (Organization.secondary_api_key_hash == key_hash)
        )
    )
    return result.scalar_one_or_none()


def _is_admin_key(credentials: HTTPAuthorizationCredentials) -> bool:
    """Check if the provided credentials match the global admin API key."""
    return bool(settings.api_key and credentials.credentials == settings.api_key)


async def verify_admin_key(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Verify admin API key. Used for org management endpoints."""
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API key not configured on server")
    if credentials.credentials != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def resolve_org_or_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Organization | None:
    """Resolve the caller: returns Organization if org key, None if admin key.

    Raises 401 if the key matches neither.
    """
    if _is_admin_key(credentials):
        return None  # Admin — full access

    async with async_session() as session:
        org = await _get_org_by_key(session, credentials)
        if org:
            return org

    raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# CRUD — admin only for create/list/delete, org-scoped for get/update
# ---------------------------------------------------------------------------


@router.post(
    "/", response_model=OrgResponse, status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
async def create_org(data: OrgCreate):
    """Create a new organization. Requires admin API key."""
    async with async_session() as session:
        existing = await session.execute(
            select(Organization).where(Organization.name == data.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Organization name already exists")
        org = Organization(
            name=data.name,
            api_key_hash=_hash_key(data.api_key),
            cost_per_invocation=data.cost_per_invocation,
            billing_alert_threshold=data.billing_alert_threshold,
            rate_limit=data.rate_limit,
            rate_burst=data.rate_burst,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


@router.get(
    "/", response_model=list[OrgResponse],
    dependencies=[Depends(verify_admin_key)],
)
async def list_orgs():
    """List all organizations. Requires admin API key."""
    async with async_session() as session:
        result = await session.execute(
            select(Organization).order_by(Organization.created_at.desc())
        )
        return result.scalars().all()


@router.get("/{org_id}", response_model=OrgResponse)
async def get_org(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get an organization. Admin sees any, org sees only itself."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        return org


@router.put("/{org_id}", response_model=OrgResponse)
async def update_org(
    org_id: uuid.UUID,
    data: OrgUpdate,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Update an organization. Admin or org owner."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(org, field, value)
        await session.commit()
        await session.refresh(org)
        return org


@router.delete(
    "/{org_id}", status_code=204,
    dependencies=[Depends(verify_admin_key)],
)
async def delete_org(org_id: uuid.UUID):
    """Delete an organization. Requires admin API key."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        await session.delete(org)
        await session.commit()


# ---------------------------------------------------------------------------
# API key rotation
# ---------------------------------------------------------------------------


@router.post("/{org_id}/rotate-key")
async def rotate_org_key(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Start key rotation: generate a new secondary key.

    The old key remains valid. Call /confirm-rotation to promote the
    new key and revoke the old one.

    Returns the new API key (only shown once).
    """
    import secrets

    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    new_key = secrets.token_urlsafe(32)
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        org.secondary_api_key_hash = _hash_key(new_key)
        await session.commit()

    return {"new_api_key": new_key, "status": "pending_confirmation"}


@router.post("/{org_id}/confirm-rotation")
async def confirm_key_rotation(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Confirm key rotation: promote secondary key to primary.

    After this call, only the new key (from /rotate-key) will work.
    """
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        if not org.secondary_api_key_hash:
            raise HTTPException(
                status_code=400,
                detail="No pending rotation. Call /rotate-key first.",
            )
        org.api_key_hash = org.secondary_api_key_hash
        org.secondary_api_key_hash = None
        await session.commit()

    return {"status": "rotation_confirmed"}


# ---------------------------------------------------------------------------
# Org-scoped agent listing
# ---------------------------------------------------------------------------


@router.get("/{org_id}/agents", response_model=list[AgentResponse])
async def list_org_agents(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """List agents belonging to an organization. Admin or org owner."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        result = await session.execute(
            select(Agent).where(Agent.org_id == org_id).order_by(Agent.created_at.desc())
        )
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Org billing
# ---------------------------------------------------------------------------


@router.get("/{org_id}/billing")
async def get_org_billing(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get billing summary for an organization. Admin or org owner."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        agent_result = await session.execute(
            select(Agent.id).where(Agent.org_id == org_id)
        )
        agent_ids = [row[0] for row in agent_result.all()]

        if not agent_ids:
            return {
                "org_id": str(org_id),
                "org_name": org.name,
                "cost_per_invocation": org.cost_per_invocation,
                "total_invocations": 0,
                "total_errors": 0,
                "total_cost": 0.0,
                "alert_threshold": org.billing_alert_threshold,
                "alert_triggered": False,
            }

        result = await session.execute(
            select(
                func.count(TaskLog.id).label("total_invocations"),
                func.count(
                    case((TaskLog.status == "error", TaskLog.id))
                ).label("total_errors"),
            ).where(TaskLog.agent_id.in_(agent_ids))
        )
        row = result.one()

    total_cost = round(row.total_invocations * org.cost_per_invocation, 4)
    alert_triggered = (
        org.billing_alert_threshold is not None
        and total_cost >= org.billing_alert_threshold
    )

    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "cost_per_invocation": org.cost_per_invocation,
        "total_invocations": row.total_invocations,
        "total_errors": row.total_errors,
        "total_cost": total_cost,
        "alert_threshold": org.billing_alert_threshold,
        "alert_triggered": alert_triggered,
    }


@router.get("/{org_id}/billing/breakdown")
async def get_org_billing_breakdown(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get billing breakdown by day for an organization."""
    from datetime import datetime, timedelta, timezone

    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        agent_result = await session.execute(
            select(Agent.id).where(Agent.org_id == org_id)
        )
        agent_ids = [row[0] for row in agent_result.all()]

        if not agent_ids:
            return {
                "org_id": str(org_id),
                "org_name": org.name,
                "cost_per_invocation": org.cost_per_invocation,
                "breakdown": [],
            }

        since = datetime.now(timezone.utc) - timedelta(days=30)
        date_col = cast(TaskLog.created_at, Date).label("period")

        query = (
            select(
                date_col,
                func.count(TaskLog.id).label("invocations"),
                func.count(
                    case((TaskLog.status == "error", TaskLog.id))
                ).label("errors"),
            )
            .where(TaskLog.agent_id.in_(agent_ids), TaskLog.created_at >= since)
            .group_by("period")
            .order_by("period")
        )
        result = await session.execute(query)
        rows = result.all()

    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "cost_per_invocation": org.cost_per_invocation,
        "breakdown": [
            {
                "period": (
                    row.period.isoformat()
                    if hasattr(row.period, "isoformat") else str(row.period)
                ),
                "invocations": row.invocations,
                "errors": row.errors,
                "cost": round(row.invocations * org.cost_per_invocation, 4),
            }
            for row in rows
        ],
    }
