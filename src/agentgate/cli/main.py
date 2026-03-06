import json
from pathlib import Path

import click
import httpx
import yaml

from agentgate import __version__

DEFAULT_SERVER = "https://agentgate.sh"


@click.group()
@click.version_option(version=__version__)
def cli():
    """AgentGate — Deploy, connect, and monetize AI agents."""
    pass


@cli.command()
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
def status(server: str):
    """Show AgentGate server status."""
    click.echo(f"AgentGate CLI v{__version__}")
    try:
        r = httpx.get(f"{server}/health", timeout=5)
        data = r.json()
        click.echo(f"Server: {server}")
        click.echo(f"Server version: {data.get('version', 'unknown')}")
        click.echo(f"Status: {data.get('status', 'unknown')}")
    except httpx.ConnectError:
        click.echo(f"Server: {server} (unreachable)")


@cli.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
def deploy(path: str, server: str):
    """Deploy an agent from a local directory.

    PATH is the directory containing your agent and an agentgate.yaml config file.
    """
    agent_dir = Path(path)
    config_file = agent_dir / "agentgate.yaml"

    if not config_file.exists():
        click.echo("Error: agentgate.yaml not found in the agent directory.", err=True)
        click.echo("Create one with at least 'name' and 'url' fields.", err=True)
        raise SystemExit(1)

    with open(config_file) as f:
        config = yaml.safe_load(f)

    required = ["name", "url"]
    for field in required:
        if field not in config:
            click.echo(f"Error: '{field}' is required in agentgate.yaml.", err=True)
            raise SystemExit(1)

    payload = {
        "name": config["name"],
        "url": config["url"],
        "description": config.get("description", ""),
        "version": config.get("version", "1.0.0"),
        "skills": config.get("skills", []),
    }

    click.echo(f"Deploying agent '{payload['name']}' to {server}...")

    try:
        r = httpx.post(f"{server}/agents/", json=payload, timeout=10)
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code == 201:
        agent = r.json()
        click.echo("Agent deployed successfully!")
        click.echo(f"  ID:   {agent['id']}")
        click.echo(f"  Name: {agent['name']}")
        click.echo(f"  Card: {server}/agents/{agent['id']}/card")
    else:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)
