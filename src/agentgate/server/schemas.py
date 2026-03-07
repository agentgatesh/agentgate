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
    org_id: uuid.UUID | None = None
    agent_api_key: str | None = Field(default=None, exclude=True)


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    url: str | None = None
    version: str | None = None
    skills: list[dict] | None = None
    webhook_url: str | None = None
    agent_api_key: str | None = Field(default=None, exclude=True)


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    url: str
    version: str
    skills: list[dict]
    webhook_url: str | None = None
    org_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    api_key: str = Field(..., min_length=8, exclude=True)
    cost_per_invocation: float = 0.001
    billing_alert_threshold: float | None = None
    rate_limit: float = 10.0
    rate_burst: int = 20


class OrgUpdate(BaseModel):
    name: str | None = None
    cost_per_invocation: float | None = None
    billing_alert_threshold: float | None = None
    rate_limit: float | None = None
    rate_burst: int | None = None


class OrgResponse(BaseModel):
    id: uuid.UUID
    name: str
    cost_per_invocation: float
    billing_alert_threshold: float | None = None
    rate_limit: float
    rate_burst: int
    created_at: datetime

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
