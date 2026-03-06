"""Tests for AgentGate Python SDK."""

from unittest.mock import MagicMock, patch

import pytest

from agentgate.sdk import AgentGateClient, AgentGateError


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestAgentGateClient:
    def test_health(self):
        client = AgentGateClient("https://example.com")
        with patch.object(client._client, "get", return_value=_mock_response(
            json_data={"status": "ok"}
        )):
            result = client.health()
        assert result == {"status": "ok"}

    def test_list_agents(self):
        agents = [{"id": "1", "name": "test"}]
        client = AgentGateClient("https://example.com")
        with patch.object(client._client, "get", return_value=_mock_response(
            json_data=agents
        )):
            result = client.list_agents()
        assert result == agents

    def test_get_agent(self):
        agent = {"id": "abc", "name": "my-agent"}
        client = AgentGateClient("https://example.com")
        with patch.object(client._client, "get", return_value=_mock_response(
            json_data=agent
        )):
            result = client.get_agent("abc")
        assert result == agent

    def test_register_agent(self):
        created = {"id": "new-id", "name": "new-agent"}
        client = AgentGateClient("https://example.com", api_key="key123")
        with patch.object(client._client, "post", return_value=_mock_response(
            status_code=201, json_data=created
        )):
            result = client.register_agent(
                name="new-agent", url="https://agent.example.com"
            )
        assert result == created

    def test_update_agent(self):
        updated = {"id": "abc", "name": "updated-agent", "version": "2.0.0"}
        client = AgentGateClient("https://example.com", api_key="key123")
        with patch.object(client._client, "put", return_value=_mock_response(
            json_data=updated
        )):
            result = client.update_agent("abc", name="updated-agent", version="2.0.0")
        assert result == updated

    def test_delete_agent(self):
        client = AgentGateClient("https://example.com", api_key="key123")
        with patch.object(client._client, "delete", return_value=_mock_response(
            status_code=204
        )):
            client.delete_agent("abc")  # should not raise

    def test_send_task(self):
        response_data = {
            "id": "task-1",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"type": "text", "text": "Echo: Hi"}]}],
        }
        client = AgentGateClient("https://example.com")
        with patch.object(client._client, "post", return_value=_mock_response(
            json_data=response_data
        )):
            result = client.send_task("https://agent.example.com", "Hi")
        assert result["status"]["state"] == "completed"
        assert result["artifacts"][0]["parts"][0]["text"] == "Echo: Hi"

    def test_discover(self):
        discovery = {"name": "AgentGate", "agents": []}
        client = AgentGateClient("https://example.com")
        with patch.object(client._client, "get", return_value=_mock_response(
            json_data=discovery
        )):
            result = client.discover()
        assert result["name"] == "AgentGate"

    def test_error_raises(self):
        client = AgentGateClient("https://example.com")
        with patch.object(client._client, "get", return_value=_mock_response(
            status_code=404, text="Not found"
        )):
            with pytest.raises(AgentGateError) as exc_info:
                client.get_agent("nonexistent")
            assert exc_info.value.status_code == 404

    def test_context_manager(self):
        with AgentGateClient("https://example.com") as client:
            with patch.object(client._client, "get", return_value=_mock_response(
                json_data={"status": "ok"}
            )):
                result = client.health()
        assert result == {"status": "ok"}

    def test_auth_header_sent(self):
        client = AgentGateClient("https://example.com", api_key="secret")
        mock_post = MagicMock(return_value=_mock_response(
            status_code=201, json_data={"id": "1"}
        ))
        with patch.object(client._client, "post", mock_post):
            client.register_agent(name="a", url="https://a.com")
        call_kwargs = mock_post.call_args
        assert "Bearer secret" in call_kwargs.kwargs["headers"]["Authorization"]
