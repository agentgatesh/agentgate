"""AgentGate Python SDK — Async client for interacting with an AgentGate server."""

from __future__ import annotations

import httpx


class AsyncAgentGateClient:
    """Async client for the AgentGate API.

    Usage:
        async with AsyncAgentGateClient("https://agentgate.sh", api_key="key") as client:
            agents = await client.list_agents()
            result = await client.send_task("agent-id", "Hello!")
    """

    def __init__(
        self,
        server_url: str = "https://agentgate.sh",
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    def _headers(self, auth: bool = False) -> dict:
        headers = {"Content-Type": "application/json"}
        if auth and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            from agentgate.sdk.client import AgentGateError

            raise AgentGateError(response.status_code, response.text)

    # --- Registry operations ---

    async def list_agents(
        self, skill: str | None = None, tag: str | None = None,
    ) -> list[dict]:
        params = {}
        if skill:
            params["skill"] = skill
        if tag:
            params["tag"] = tag
        r = await self._client.get(f"{self.server_url}/agents/", params=params)
        self._raise_for_status(r)
        return r.json()

    async def list_tags(self) -> dict:
        """List all unique agent tags with counts."""
        r = await self._client.get(f"{self.server_url}/agents/tags")
        self._raise_for_status(r)
        return r.json()

    async def search_agents(
        self,
        q: str | None = None,
        tags: str | None = None,
        skill: str | None = None,
        sort: str = "newest",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Advanced agent search with full-text, multi-tag, and sorting."""
        params: dict = {"sort": sort, "limit": limit, "offset": offset}
        if q:
            params["q"] = q
        if tags:
            params["tags"] = tags
        if skill:
            params["skill"] = skill
        r = await self._client.get(f"{self.server_url}/agents/search", params=params)
        self._raise_for_status(r)
        return r.json()

    async def get_agent(self, agent_id: str) -> dict:
        r = await self._client.get(f"{self.server_url}/agents/{agent_id}")
        self._raise_for_status(r)
        return r.json()

    async def get_agent_versions(self, name: str, version: str | None = None) -> list[dict]:
        params = {}
        if version:
            params["version"] = version
        r = await self._client.get(
            f"{self.server_url}/agents/by-name/{name}", params=params,
        )
        self._raise_for_status(r)
        return r.json()

    async def get_agent_latest(self, name: str) -> dict:
        r = await self._client.get(f"{self.server_url}/agents/by-name/{name}/latest")
        self._raise_for_status(r)
        return r.json()

    async def get_agent_health(self, agent_id: str) -> dict:
        r = await self._client.get(f"{self.server_url}/agents/{agent_id}/health")
        self._raise_for_status(r)
        return r.json()

    async def get_all_health(self) -> dict:
        r = await self._client.get(f"{self.server_url}/health/agents")
        self._raise_for_status(r)
        return r.json()

    async def get_agent_card(self, agent_id: str) -> dict:
        r = await self._client.get(f"{self.server_url}/agents/{agent_id}/card")
        self._raise_for_status(r)
        return r.json()

    async def register_agent(
        self,
        name: str,
        url: str,
        description: str = "",
        version: str = "1.0.0",
        skills: list[dict] | None = None,
    ) -> dict:
        payload = {
            "name": name, "url": url, "description": description,
            "version": version, "skills": skills or [],
        }
        r = await self._client.post(
            f"{self.server_url}/agents/", json=payload,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def update_agent(self, agent_id: str, **fields) -> dict:
        r = await self._client.put(
            f"{self.server_url}/agents/{agent_id}", json=fields,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def delete_agent(self, agent_id: str) -> None:
        r = await self._client.delete(
            f"{self.server_url}/agents/{agent_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)

    # --- Reviews ---

    async def create_review(
        self, agent_id: str, rating: int, comment: str = "", reviewer: str = "anonymous",
    ) -> dict:
        r = await self._client.post(
            f"{self.server_url}/agents/{agent_id}/reviews",
            json={"rating": rating, "comment": comment, "reviewer": reviewer},
        )
        self._raise_for_status(r)
        return r.json()

    async def list_reviews(
        self, agent_id: str, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        r = await self._client.get(
            f"{self.server_url}/agents/{agent_id}/reviews",
            params={"limit": limit, "offset": offset},
        )
        self._raise_for_status(r)
        return r.json()

    async def get_review_stats(self, agent_id: str) -> dict:
        r = await self._client.get(f"{self.server_url}/agents/{agent_id}/reviews/stats")
        self._raise_for_status(r)
        return r.json()

    # --- A2A communication ---

    async def send_task(
        self,
        agent_id: str,
        text: str,
        task_id: str = "task-1",
        agent_api_key: str | None = None,
    ) -> dict:
        payload = {
            "id": task_id,
            "message": {"parts": [{"type": "text", "text": text}]},
        }
        headers = {"Content-Type": "application/json"}
        if agent_api_key:
            headers["Authorization"] = f"Bearer {agent_api_key}"
        r = await self._client.post(
            f"{self.server_url}/agents/{agent_id}/task",
            json=payload, headers=headers,
        )
        self._raise_for_status(r)
        return r.json()

    # --- Logs & Usage ---

    async def get_agent_logs(
        self, agent_id: str, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        r = await self._client.get(
            f"{self.server_url}/agents/{agent_id}/logs",
            params={"limit": limit, "offset": offset},
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def get_agent_usage(self, agent_id: str) -> dict:
        r = await self._client.get(
            f"{self.server_url}/agents/{agent_id}/usage",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def get_usage_breakdown(
        self, agent_id: str, period: str = "day", days: int = 30,
    ) -> dict:
        r = await self._client.get(
            f"{self.server_url}/agents/{agent_id}/usage/breakdown",
            params={"period": period, "days": days},
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    # --- Chains ---

    async def create_chain(
        self, name: str, steps: list[dict], description: str = "",
    ) -> dict:
        r = await self._client.post(
            f"{self.server_url}/chains/",
            json={"name": name, "description": description, "steps": steps},
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def list_chains(self) -> list[dict]:
        r = await self._client.get(
            f"{self.server_url}/chains/",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def get_chain(self, chain_id: str) -> dict:
        r = await self._client.get(
            f"{self.server_url}/chains/{chain_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def update_chain(self, chain_id: str, **fields) -> dict:
        r = await self._client.put(
            f"{self.server_url}/chains/{chain_id}",
            json=fields,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def delete_chain(self, chain_id: str) -> None:
        r = await self._client.delete(
            f"{self.server_url}/chains/{chain_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)

    async def run_chain(self, chain_id: str, input_text: str) -> dict:
        r = await self._client.post(
            f"{self.server_url}/chains/{chain_id}/run",
            json={"input": input_text},
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    # --- Organization management ---

    async def create_org(
        self,
        name: str,
        api_key: str,
        cost_per_invocation: float = 0.001,
        rate_limit: float = 10.0,
        rate_burst: int = 20,
        billing_alert_threshold: float | None = None,
    ) -> dict:
        payload: dict = {
            "name": name, "api_key": api_key,
            "cost_per_invocation": cost_per_invocation,
            "rate_limit": rate_limit, "rate_burst": rate_burst,
        }
        if billing_alert_threshold is not None:
            payload["billing_alert_threshold"] = billing_alert_threshold
        r = await self._client.post(
            f"{self.server_url}/orgs/", json=payload,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def list_orgs(self) -> list[dict]:
        r = await self._client.get(
            f"{self.server_url}/orgs/",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def get_org(self, org_id: str) -> dict:
        r = await self._client.get(
            f"{self.server_url}/orgs/{org_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def update_org(self, org_id: str, **fields) -> dict:
        r = await self._client.put(
            f"{self.server_url}/orgs/{org_id}", json=fields,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def delete_org(self, org_id: str) -> None:
        r = await self._client.delete(
            f"{self.server_url}/orgs/{org_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)

    async def list_org_agents(self, org_id: str) -> list[dict]:
        r = await self._client.get(
            f"{self.server_url}/orgs/{org_id}/agents",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def get_org_billing(self, org_id: str) -> dict:
        r = await self._client.get(
            f"{self.server_url}/orgs/{org_id}/billing",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def get_org_billing_breakdown(self, org_id: str) -> dict:
        r = await self._client.get(
            f"{self.server_url}/orgs/{org_id}/billing/breakdown",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def rotate_org_key(self, org_id: str) -> dict:
        r = await self._client.post(
            f"{self.server_url}/orgs/{org_id}/rotate-key",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    async def confirm_org_key_rotation(self, org_id: str) -> dict:
        r = await self._client.post(
            f"{self.server_url}/orgs/{org_id}/confirm-rotation",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    # --- Discovery & Health ---

    async def discover(self) -> dict:
        r = await self._client.get(f"{self.server_url}/.well-known/agent.json")
        self._raise_for_status(r)
        return r.json()

    async def health(self) -> dict:
        r = await self._client.get(f"{self.server_url}/health")
        self._raise_for_status(r)
        return r.json()

    # --- Lifecycle ---

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
