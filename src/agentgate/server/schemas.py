import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    url: str = Field(..., min_length=1, max_length=2048)
    version: str = "1.0.0"
    skills: list[dict] = []
    webhook_url: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    url: str | None = None
    version: str | None = None
    skills: list[dict] | None = None
    webhook_url: str | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    url: str
    version: str
    skills: list[dict]
    webhook_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentCard(BaseModel):
    """A2A-compliant Agent Card."""

    name: str
    description: str
    url: str
    version: str
    skills: list[dict]
    provider: dict = {"organization": "AgentGate", "url": "https://agentgate.sh"}
    authentication: dict = {"schemes": []}
    capabilities: dict = {}
