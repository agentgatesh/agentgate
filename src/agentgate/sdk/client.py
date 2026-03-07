"""AgentGate Python SDK — Client for interacting with an AgentGate server."""

from __future__ import annotations

import httpx


class AgentGateClient:
    """Client for the AgentGate API.

    Usage:
        client = AgentGateClient("https://agentgate.sh", api_key="your-key")
        agents = client.list_agents()
        result = client.send_task("echo-agent-id", "Hello!")
    """

    def __init__(
        self,
        server_url: str = "https://agentgate.sh",
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def _headers(self, auth: bool = False) -> dict:
        headers = {"Content-Type": "application/json"}
        if auth and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise AgentGateError(response.status_code, response.text)

    # --- Registry operations ---

    def list_agents(
        self, skill: str | None = None, tag: str | None = None,
    ) -> list[dict]:
        """List all registered agents. Optionally filter by skill or tag."""
        params = {}
        if skill:
            params["skill"] = skill
        if tag:
            params["tag"] = tag
        r = self._client.get(f"{self.server_url}/agents/", params=params)
        self._raise_for_status(r)
        return r.json()

    def list_tags(self) -> dict:
        """List all unique agent tags with counts."""
        r = self._client.get(f"{self.server_url}/agents/tags")
        self._raise_for_status(r)
        return r.json()

    def get_agent(self, agent_id: str) -> dict:
        """Get a single agent by ID."""
        r = self._client.get(f"{self.server_url}/agents/{agent_id}")
        self._raise_for_status(r)
        return r.json()

    def get_agent_versions(self, name: str, version: str | None = None) -> list[dict]:
        """Get all versions of an agent by name."""
        params = {}
        if version:
            params["version"] = version
        r = self._client.get(f"{self.server_url}/agents/by-name/{name}", params=params)
        self._raise_for_status(r)
        return r.json()

    def get_agent_latest(self, name: str) -> dict:
        """Get the latest version of an agent by name."""
        r = self._client.get(f"{self.server_url}/agents/by-name/{name}/latest")
        self._raise_for_status(r)
        return r.json()

    def get_agent_health(self, agent_id: str) -> dict:
        """Get health status for an agent."""
        r = self._client.get(f"{self.server_url}/agents/{agent_id}/health")
        self._raise_for_status(r)
        return r.json()

    def get_all_health(self) -> dict:
        """Get health status for all agents."""
        r = self._client.get(f"{self.server_url}/health/agents")
        self._raise_for_status(r)
        return r.json()

    def get_agent_card(self, agent_id: str) -> dict:
        """Get the A2A-compliant Agent Card for an agent."""
        r = self._client.get(f"{self.server_url}/agents/{agent_id}/card")
        self._raise_for_status(r)
        return r.json()

    def register_agent(
        self,
        name: str,
        url: str,
        description: str = "",
        version: str = "1.0.0",
        skills: list[dict] | None = None,
    ) -> dict:
        """Register a new agent. Requires API key."""
        payload = {
            "name": name,
            "url": url,
            "description": description,
            "version": version,
            "skills": skills or [],
        }
        r = self._client.post(
            f"{self.server_url}/agents/",
            json=payload,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def update_agent(self, agent_id: str, **fields) -> dict:
        """Update an agent. Requires API key.

        Pass only the fields to update, e.g.:
            client.update_agent("id", name="new-name", version="2.0.0")
        """
        r = self._client.put(
            f"{self.server_url}/agents/{agent_id}",
            json=fields,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def delete_agent(self, agent_id: str) -> None:
        """Delete an agent by ID. Requires API key."""
        r = self._client.delete(
            f"{self.server_url}/agents/{agent_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)

    # --- A2A communication ---

    def send_task(
        self,
        agent_id: str,
        text: str,
        task_id: str = "task-1",
        agent_api_key: str | None = None,
    ) -> dict:
        """Send an A2A task to an agent via AgentGate routing.

        Args:
            agent_id: The agent ID (UUID) registered on AgentGate
            text: The text message to send
            task_id: Optional task identifier
            agent_api_key: Optional per-agent API key (if agent requires auth)
        """
        payload = {
            "id": task_id,
            "message": {"parts": [{"type": "text", "text": text}]},
        }
        headers = {"Content-Type": "application/json"}
        if agent_api_key:
            headers["Authorization"] = f"Bearer {agent_api_key}"
        r = self._client.post(
            f"{self.server_url}/agents/{agent_id}/task",
            json=payload,
            headers=headers,
        )
        self._raise_for_status(r)
        return r.json()

    def get_agent_logs(
        self, agent_id: str, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """Get invocation logs for an agent. Requires API key."""
        r = self._client.get(
            f"{self.server_url}/agents/{agent_id}/logs",
            params={"limit": limit, "offset": offset},
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def get_agent_usage(self, agent_id: str) -> dict:
        """Get usage stats for an agent. Requires API key."""
        r = self._client.get(
            f"{self.server_url}/agents/{agent_id}/usage",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def get_usage_breakdown(
        self, agent_id: str, period: str = "day", days: int = 30,
    ) -> dict:
        """Get usage breakdown by day or month. Requires API key."""
        r = self._client.get(
            f"{self.server_url}/agents/{agent_id}/usage/breakdown",
            params={"period": period, "days": days},
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    # --- Organization management ---

    def create_org(
        self,
        name: str,
        api_key: str,
        cost_per_invocation: float = 0.001,
        rate_limit: float = 10.0,
        rate_burst: int = 20,
        billing_alert_threshold: float | None = None,
    ) -> dict:
        """Create an organization. Requires admin API key."""
        payload: dict = {
            "name": name, "api_key": api_key,
            "cost_per_invocation": cost_per_invocation,
            "rate_limit": rate_limit, "rate_burst": rate_burst,
        }
        if billing_alert_threshold is not None:
            payload["billing_alert_threshold"] = billing_alert_threshold
        r = self._client.post(
            f"{self.server_url}/orgs/", json=payload,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def list_orgs(self) -> list[dict]:
        """List all organizations. Requires admin API key."""
        r = self._client.get(
            f"{self.server_url}/orgs/",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def get_org(self, org_id: str) -> dict:
        """Get an organization by ID."""
        r = self._client.get(
            f"{self.server_url}/orgs/{org_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def update_org(self, org_id: str, **fields) -> dict:
        """Update an organization."""
        r = self._client.put(
            f"{self.server_url}/orgs/{org_id}", json=fields,
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def delete_org(self, org_id: str) -> None:
        """Delete an organization. Requires admin API key."""
        r = self._client.delete(
            f"{self.server_url}/orgs/{org_id}",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)

    def list_org_agents(self, org_id: str) -> list[dict]:
        """List agents belonging to an organization."""
        r = self._client.get(
            f"{self.server_url}/orgs/{org_id}/agents",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def get_org_billing(self, org_id: str) -> dict:
        """Get billing summary for an organization."""
        r = self._client.get(
            f"{self.server_url}/orgs/{org_id}/billing",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def get_org_billing_breakdown(self, org_id: str) -> dict:
        """Get daily billing breakdown for an organization."""
        r = self._client.get(
            f"{self.server_url}/orgs/{org_id}/billing/breakdown",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def rotate_org_key(self, org_id: str) -> dict:
        """Start API key rotation. Returns new key (shown once)."""
        r = self._client.post(
            f"{self.server_url}/orgs/{org_id}/rotate-key",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    def confirm_org_key_rotation(self, org_id: str) -> dict:
        """Confirm key rotation: promote new key, revoke old."""
        r = self._client.post(
            f"{self.server_url}/orgs/{org_id}/confirm-rotation",
            headers=self._headers(auth=True),
        )
        self._raise_for_status(r)
        return r.json()

    # --- Discovery & Health ---

    def discover(self) -> dict:
        """Get the .well-known/agent.json discovery document."""
        r = self._client.get(f"{self.server_url}/.well-known/agent.json")
        self._raise_for_status(r)
        return r.json()

    def health(self) -> dict:
        """Check server health."""
        r = self._client.get(f"{self.server_url}/health")
        self._raise_for_status(r)
        return r.json()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AgentGateError(Exception):
    """Error from the AgentGate API."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")
