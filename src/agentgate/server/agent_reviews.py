"""Agent review endpoints: POST/GET/stats under /{agent_id}/reviews."""

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select

from agentgate.db.models import Agent, Review
from agentgate.server.schemas import ReviewCreate, ReviewResponse

router = APIRouter()


def _async_session():
    from agentgate.server import routes
    return routes.async_session


@router.post("/{agent_id}/reviews", response_model=ReviewResponse, status_code=201)
async def create_review(agent_id: uuid.UUID, data: ReviewCreate):
    """Submit a review for an agent (1-5 stars). No auth required."""
    async with _async_session()() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        review = Review(
            agent_id=agent_id,
            rating=data.rating,
            comment=data.comment,
            reviewer=data.reviewer,
        )
        session.add(review)
        await session.commit()
        await session.refresh(review)
        return review


@router.get("/{agent_id}/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    agent_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get reviews for an agent, newest first."""
    async with _async_session()() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        result = await session.execute(
            select(Review)
            .where(Review.agent_id == agent_id)
            .order_by(desc(Review.created_at))
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()


@router.get("/{agent_id}/reviews/stats")
async def review_stats(agent_id: uuid.UUID):
    """Get aggregate review stats for an agent."""
    async with _async_session()() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        result = await session.execute(
            select(
                func.count(Review.id).label("review_count"),
                func.avg(Review.rating).label("avg_rating"),
                func.count(Review.id).filter(Review.rating == 5).label("five_star"),
                func.count(Review.id).filter(Review.rating == 4).label("four_star"),
                func.count(Review.id).filter(Review.rating == 3).label("three_star"),
                func.count(Review.id).filter(Review.rating == 2).label("two_star"),
                func.count(Review.id).filter(Review.rating == 1).label("one_star"),
            ).where(Review.agent_id == agent_id)
        )
        row = result.one()
    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "review_count": row.review_count,
        "avg_rating": round(row.avg_rating, 2) if row.avg_rating else None,
        "distribution": {
            "5": row.five_star,
            "4": row.four_star,
            "3": row.three_star,
            "2": row.two_star,
            "1": row.one_star,
        },
    }
