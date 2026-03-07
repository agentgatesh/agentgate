"""Tests for the agentgate deploy feature (engine, routes, CLI, SDK)."""

import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentgate.server.deploy_engine import (
    DEFAULT_DOCKERFILE,
    _agent_container_name,
    _agent_image_name,
    allocate_port,
    ensure_dockerfile,
    save_agent_files,
)

# ---------------------------------------------------------------------------
# Deploy engine — unit tests
# ---------------------------------------------------------------------------


def test_agent_container_name():
    aid = "12345678-1234-1234-1234-123456789abc"
    assert _agent_container_name(aid) == "agentgate-agent-12345678-123"


def test_agent_image_name():
    aid = "12345678-1234-1234-1234-123456789abc"
    assert _agent_image_name(aid) == "agentgate-agent-12345678-123:latest"


def test_allocate_port_empty():
    assert allocate_port([]) == 9100


def test_allocate_port_with_existing():
    assert allocate_port([9100, 9101, 9102]) == 9103


def test_allocate_port_with_gaps():
    assert allocate_port([9100, 9102]) == 9101


def test_ensure_dockerfile_creates_default(tmp_path):
    ensure_dockerfile(tmp_path, 9100)
    dockerfile = tmp_path / "Dockerfile"
    assert dockerfile.exists()
    content = dockerfile.read_text()
    assert "EXPOSE 9100" in content
    assert "uvicorn" in content
    assert "--port" in content


def test_ensure_dockerfile_preserves_existing(tmp_path):
    custom = "FROM python:3.12\nRUN echo hello"
    (tmp_path / "Dockerfile").write_text(custom)
    ensure_dockerfile(tmp_path, 9100)
    assert (tmp_path / "Dockerfile").read_text() == custom


def test_save_agent_files(tmp_path):
    # Create a tar.gz in memory
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b'print("hello")'
        info = tarfile.TarInfo(name="agent.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    tar_bytes = buf.getvalue()

    with patch("agentgate.server.deploy_engine.settings") as mock_settings:
        mock_settings.deploy_dir = str(tmp_path)
        agent_id = "test-agent-id"
        result = save_agent_files(agent_id, tar_bytes)

    assert result == tmp_path / agent_id
    assert (result / "agent.py").exists()
    assert (result / "agent.py").read_text() == 'print("hello")'


def test_save_agent_files_rejects_path_traversal(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"evil"
        info = tarfile.TarInfo(name="../../../etc/passwd")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    tar_bytes = buf.getvalue()

    with patch("agentgate.server.deploy_engine.settings") as mock_settings:
        mock_settings.deploy_dir = str(tmp_path)
        with pytest.raises(ValueError, match="Unsafe path"):
            save_agent_files("test-id", tar_bytes)


def test_default_dockerfile_template():
    content = DEFAULT_DOCKERFILE.format(port=9100)
    assert "EXPOSE 9100" in content
    assert "FROM python:3.12-slim" in content
    assert "requirements.txt" in content


# ---------------------------------------------------------------------------
# Deploy engine — Docker operations (mocked)
# ---------------------------------------------------------------------------


@patch("agentgate.server.deploy_engine._get_client")
def test_build_image(mock_get_client):
    from agentgate.server.deploy_engine import build_image

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    tag = build_image("test-id", Path("/tmp/test"))
    assert tag == "agentgate-agent-test-id:latest"
    mock_client.images.build.assert_called_once()


@patch("agentgate.server.deploy_engine._get_client")
def test_run_container(mock_get_client):
    from agentgate.server.deploy_engine import run_container

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "container123456"
    mock_client.containers.run.return_value = mock_container
    from docker.errors import NotFound

    mock_client.containers.get.side_effect = NotFound("not found")
    mock_get_client.return_value = mock_client

    cid = run_container("test-id", 9100)
    assert cid == "container123456"
    mock_client.containers.run.assert_called_once()


@patch("agentgate.server.deploy_engine._get_client")
def test_stop_container_found(mock_get_client):
    from agentgate.server.deploy_engine import stop_container

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_get_client.return_value = mock_client

    assert stop_container("test-id") is True
    mock_container.remove.assert_called_once_with(force=True)


@patch("agentgate.server.deploy_engine._get_client")
def test_stop_container_not_found(mock_get_client):
    from docker.errors import NotFound

    from agentgate.server.deploy_engine import stop_container

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = NotFound("not found")
    mock_get_client.return_value = mock_client

    assert stop_container("test-id") is False


@patch("agentgate.server.deploy_engine._get_client")
def test_get_container_status_running(mock_get_client):
    from agentgate.server.deploy_engine import get_container_status

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.id = "abc123def456"
    mock_client.containers.get.return_value = mock_container
    mock_get_client.return_value = mock_client

    status = get_container_status("test-id")
    assert status["running"] is True
    assert status["status"] == "running"


@patch("agentgate.server.deploy_engine._get_client")
def test_get_container_status_not_found(mock_get_client):
    from docker.errors import NotFound

    from agentgate.server.deploy_engine import get_container_status

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = NotFound("not found")
    mock_get_client.return_value = mock_client

    status = get_container_status("test-id")
    assert status["running"] is False
    assert status["status"] == "not_found"


@patch("agentgate.server.deploy_engine._get_client")
def test_get_container_logs(mock_get_client):
    from agentgate.server.deploy_engine import get_container_logs

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.logs.return_value = b"INFO: Started\nINFO: Ready"
    mock_client.containers.get.return_value = mock_container
    mock_get_client.return_value = mock_client

    logs = get_container_logs("test-id", tail=50)
    assert "Started" in logs
    assert "Ready" in logs


@patch("agentgate.server.deploy_engine._get_client")
def test_remove_image(mock_get_client):
    from agentgate.server.deploy_engine import remove_image

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    assert remove_image("test-id") is True
    mock_client.images.remove.assert_called_once()


@patch("agentgate.server.deploy_engine._get_client")
def test_remove_image_not_found(mock_get_client):
    from docker.errors import ImageNotFound

    from agentgate.server.deploy_engine import remove_image

    mock_client = MagicMock()
    mock_client.images.remove.side_effect = ImageNotFound("not found")
    mock_get_client.return_value = mock_client

    assert remove_image("test-id") is False


def test_cleanup_deploy_files(tmp_path):
    from agentgate.server.deploy_engine import cleanup_deploy_files

    agent_dir = tmp_path / "test-agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text("hello")

    with patch("agentgate.server.deploy_engine.settings") as mock_settings:
        mock_settings.deploy_dir = str(tmp_path)
        cleanup_deploy_files("test-agent")

    assert not agent_dir.exists()


# ---------------------------------------------------------------------------
# Deploy routes — API tests (mocked Docker)
# ---------------------------------------------------------------------------


def _make_test_tar() -> bytes:
    """Create a minimal agent tar.gz for testing."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        agent_code = b'''from fastapi import FastAPI
app = FastAPI()

@app.post("/a2a")
async def handle_task(request: dict):
    return {"id": "1", "status": {"state": "completed"}, "artifacts": []}

@app.get("/health")
async def health():
    return {"status": "ok"}
'''
        info = tarfile.TarInfo(name="agent.py")
        info.size = len(agent_code)
        tf.addfile(info, io.BytesIO(agent_code))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI — deploy command tests
# ---------------------------------------------------------------------------


def test_cli_create_tarball(tmp_path):
    from agentgate.cli.main import _create_tarball

    (tmp_path / "agent.py").write_text("print('hello')")
    (tmp_path / "agentgate.yaml").write_text("name: test\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".hidden").write_text("secret")

    tar_path = _create_tarball(tmp_path)
    assert tar_path.exists()

    with tarfile.open(tar_path, "r:gz") as tf:
        names = tf.getnames()
        assert "agent.py" in names
        assert "agentgate.yaml" in names
        assert "__pycache__" not in names
        assert ".hidden" not in names

    tar_path.unlink()


# ---------------------------------------------------------------------------
# SDK — deploy methods
# ---------------------------------------------------------------------------


def test_sdk_deploy_agent_method_exists():
    from agentgate.sdk.client import AgentGateClient

    client = AgentGateClient("http://localhost:8000", api_key="test")
    assert hasattr(client, "deploy_agent")
    assert hasattr(client, "get_deploy_status")
    assert hasattr(client, "get_deploy_logs")
    assert hasattr(client, "undeploy_agent")


def test_sdk_async_deploy_agent_method_exists():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    client = AsyncAgentGateClient("http://localhost:8000", api_key="test")
    assert hasattr(client, "deploy_agent")
    assert hasattr(client, "get_deploy_status")
    assert hasattr(client, "get_deploy_logs")
    assert hasattr(client, "undeploy_agent")


# ---------------------------------------------------------------------------
# DB model — deploy fields
# ---------------------------------------------------------------------------


def test_agent_model_has_deploy_fields():
    from agentgate.db.models import Agent

    agent = Agent(
        name="test",
        url="http://test",
        deployed=True,
        container_id="abc123",
        container_port=9100,
    )
    assert agent.deployed is True
    assert agent.container_id == "abc123"
    assert agent.container_port == 9100


def test_agent_model_deploy_defaults():
    from agentgate.db.models import Agent

    agent = Agent(name="test", url="http://test")
    assert not agent.deployed
    assert agent.container_id is None
    assert agent.container_port is None


# ---------------------------------------------------------------------------
# Schema — deploy field in response
# ---------------------------------------------------------------------------


def test_agent_response_has_deployed_field():
    from agentgate.server.schemas import AgentResponse

    fields = AgentResponse.model_fields
    assert "deployed" in fields


# ---------------------------------------------------------------------------
# Config — deploy settings
# ---------------------------------------------------------------------------


def test_config_has_deploy_settings():
    from agentgate.core.config import settings

    assert hasattr(settings, "deploy_dir")
    assert hasattr(settings, "docker_network")
    assert hasattr(settings, "deploy_port_start")
    assert settings.deploy_port_start == 9100
