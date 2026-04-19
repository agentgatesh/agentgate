"""Deploy engine — builds and runs agent containers via the Docker SDK."""

import io
import logging
import tarfile
from pathlib import Path

import docker
from docker.errors import ImageNotFound, NotFound

from agentgate.core.config import settings

logger = logging.getLogger("agentgate.deploy")

DEFAULT_DOCKERFILE = """\
FROM python:3.12-slim
RUN groupadd -r agent && useradd -r -g agent -u 1001 -d /app agent
WORKDIR /app
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN pip install --no-cache-dir fastapi uvicorn
COPY . .
RUN chown -R agent:agent /app
USER agent
EXPOSE {port}
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "{port}"]
"""


def _get_client() -> docker.DockerClient:
    return docker.from_env()


def _agent_container_name(agent_id: str) -> str:
    return f"agentgate-agent-{agent_id[:12]}"


def _agent_image_name(agent_id: str) -> str:
    return f"agentgate-agent-{agent_id[:12]}:latest"


def save_agent_files(agent_id: str, tar_bytes: bytes) -> Path:
    """Extract uploaded tar into the deploy directory. Returns the agent dir."""
    deploy_dir = Path(settings.deploy_dir) / agent_id
    deploy_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        # Security: prevent path traversal
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                raise ValueError(f"Unsafe path in tar: {member.name}")
        tf.extractall(deploy_dir, filter="data")

    return deploy_dir


def ensure_dockerfile(agent_dir: Path, port: int) -> None:
    """Create a default Dockerfile if the user didn't provide one."""
    dockerfile = agent_dir / "Dockerfile"
    if not dockerfile.exists():
        dockerfile.write_text(DEFAULT_DOCKERFILE.format(port=port))


def allocate_port(existing_ports: list[int]) -> int:
    """Find the next free port starting from deploy_port_start."""
    used = set(existing_ports)
    port = settings.deploy_port_start
    while port in used:
        port += 1
    return port


def build_image(agent_id: str, agent_dir: Path) -> str:
    """Build a Docker image from the agent directory. Returns image tag."""
    client = _get_client()
    tag = _agent_image_name(agent_id)
    logger.info("Building image %s from %s", tag, agent_dir)
    client.images.build(path=str(agent_dir), tag=tag, rm=True, forcerm=True)
    return tag


def run_container(agent_id: str, port: int) -> str:
    """Run the agent container. Returns container ID."""
    client = _get_client()
    name = _agent_container_name(agent_id)
    tag = _agent_image_name(agent_id)

    # Remove existing container if any
    try:
        old = client.containers.get(name)
        old.remove(force=True)
    except NotFound:
        pass

    container = client.containers.run(
        tag,
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        ports={f"{port}/tcp": ("127.0.0.1", port)},
        network=settings.docker_network,
    )
    logger.info("Started container %s (port %d)", container.id[:12], port)
    return container.id


def stop_container(agent_id: str) -> bool:
    """Stop and remove the agent container. Returns True if found."""
    client = _get_client()
    name = _agent_container_name(agent_id)
    try:
        container = client.containers.get(name)
        container.remove(force=True)
        logger.info("Removed container %s", name)
        return True
    except NotFound:
        return False


def remove_image(agent_id: str) -> bool:
    """Remove the agent Docker image."""
    client = _get_client()
    tag = _agent_image_name(agent_id)
    try:
        client.images.remove(tag, force=True)
        return True
    except ImageNotFound:
        return False


def get_container_status(agent_id: str) -> dict:
    """Get the status of a deployed agent container."""
    client = _get_client()
    name = _agent_container_name(agent_id)
    try:
        container = client.containers.get(name)
        return {
            "running": container.status == "running",
            "status": container.status,
            "container_id": container.id[:12],
            "name": name,
        }
    except NotFound:
        return {"running": False, "status": "not_found", "container_id": None, "name": name}


def get_container_logs(agent_id: str, tail: int = 100) -> str:
    """Get recent logs from a deployed agent container."""
    client = _get_client()
    name = _agent_container_name(agent_id)
    try:
        container = client.containers.get(name)
        return container.logs(tail=tail).decode("utf-8", errors="replace")
    except NotFound:
        return ""


def cleanup_deploy_files(agent_id: str) -> None:
    """Remove the deploy directory for an agent."""
    import shutil

    deploy_dir = Path(settings.deploy_dir) / agent_id
    if deploy_dir.exists():
        shutil.rmtree(deploy_dir)
