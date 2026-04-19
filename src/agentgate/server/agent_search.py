"""Agent search / discovery endpoints: /tags, /search, /by-name/*.

Uses late binding against `agentgate.server.routes` for `async_session` so
`patch("agentgate.server.routes.async_session", ...)` in tests still
reaches the code here.
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from agentgate.db.models import Agent, Review
from agentgate.server.schemas import AgentResponse

router = APIRouter()


def _async_session():
    # Late import so tests can patch routes.async_session.
    from agentgate.server import routes
    return routes.async_session


@router.get("/tags")
async def list_tags():
    """List all unique tags across all agents."""
    async with _async_session()() as session:
        result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
        agents = result.scalars().all()
    tags: dict[str, int] = {}
    for agent in agents:
        for t in agent.tags or []:
            tags[t] = tags.get(t, 0) + 1
    return {"tags": [{"name": k, "count": v} for k, v in sorted(tags.items())]}


@router.get("/search")
async def search_agents(
    q: str | None = Query(default=None, description="Full-text search query"),
    tags: str | None = Query(default=None, description="Comma-separated tags (AND logic)"),
    skill: str | None = Query(default=None, description="Filter by skill id or name"),
    sort: str | None = Query(default="newest", pattern="^(newest|name|version|rating)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Advanced agent search with full-text, multi-tag, and sorting."""
    async with _async_session()() as session:
        result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
        agents = list(result.scalars().all())

        review_result = await session.execute(
            select(
                Review.agent_id,
                func.count(Review.id).label("review_count"),
                func.avg(Review.rating).label("avg_rating"),
            ).group_by(Review.agent_id)
        )
        review_stats = {
            row.agent_id: {
                "review_count": row.review_count,
                "avg_rating": round(float(row.avg_rating), 2),
            }
            for row in review_result.all()
        }

    if q:
        q_lower = q.lower()
        agents = [
            a for a in agents
            if q_lower in a.name.lower()
            or q_lower in (a.description or "").lower()
            or any(
                q_lower in s.get("id", "").lower()
                or q_lower in s.get("name", "").lower()
                or q_lower in s.get("description", "").lower()
                for s in (a.skills or [])
            )
            or any(q_lower in t.lower() for t in (a.tags or []))
        ]

    if tags:
        required_tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
        agents = [
            a for a in agents
            if all(
                any(rt == t.lower() for t in (a.tags or []))
                for rt in required_tags
            )
        ]

    if skill:
        skill_lower = skill.lower()
        agents = [
            a for a in agents
            if any(
                skill_lower in s.get("id", "").lower()
                or skill_lower in s.get("name", "").lower()
                for s in (a.skills or [])
            )
        ]

    if sort == "name":
        agents.sort(key=lambda a: a.name.lower())
    elif sort == "version":
        agents.sort(key=lambda a: a.version, reverse=True)
    elif sort == "rating":
        agents.sort(
            key=lambda a: review_stats.get(a.id, {}).get("avg_rating", 0),
            reverse=True,
        )

    total = len(agents)
    agents = agents[offset:offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
                "description": a.description,
                "url": a.url,
                "version": a.version,
                "skills": a.skills,
                "tags": a.tags or [],
                "org_id": str(a.org_id) if a.org_id else None,
                "avg_rating": review_stats.get(a.id, {}).get("avg_rating"),
                "review_count": review_stats.get(a.id, {}).get("review_count", 0),
                "created_at": a.created_at.isoformat(),
                "updated_at": a.updated_at.isoformat(),
            }
            for a in agents
        ],
    }


@router.get("/by-name/{name}", response_model=list[AgentResponse])
async def get_agent_versions(name: str, version: str | None = Query(default=None)):
    """Get all versions of an agent by name. Optionally filter by version."""
    async with _async_session()() as session:
        query = select(Agent).where(Agent.name == name).order_by(Agent.created_at.desc())
        if version:
            query = select(Agent).where(
                Agent.name == name, Agent.version == version,
            ).order_by(Agent.created_at.desc())
        result = await session.execute(query)
        agents = result.scalars().all()
        if not agents:
            raise HTTPException(status_code=404, detail="No agents found with this name")
        return agents


@router.get("/by-name/{name}/latest", response_model=AgentResponse)
async def get_agent_latest(name: str):
    """Get the latest version of an agent by name (most recently created)."""
    async with _async_session()() as session:
        result = await session.execute(
            select(Agent).where(Agent.name == name).order_by(Agent.created_at.desc()).limit(1)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="No agent found with this name")
        return agent
