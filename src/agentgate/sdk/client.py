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

    def list_agents(self) -> list[dict]:
        """List all registered agents."""
        r = self._client.get(f"{self.server_url}/agents/")
        self._raise_for_status(r)
        return r.json()

    def get_agent(self, agent_id: str) -> dict:
        """Get a single agent by ID."""
        r = self._client.get(f"{self.server_url}/agents/{agent_id}")
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

    def send_task(self, agent_id: str, text: str, task_id: str = "task-1") -> dict:
        """Send an A2A task to an agent via AgentGate routing.

        Args:
            agent_id: The agent ID (UUID) registered on AgentGate
            text: The text message to send
            task_id: Optional task identifier
        """
        payload = {
            "id": task_id,
            "message": {"parts": [{"type": "text", "text": text}]},
        }
        r = self._client.post(
            f"{self.server_url}/agents/{agent_id}/task",
            json=payload,
        )
        self._raise_for_status(r)
        return r.json()

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
