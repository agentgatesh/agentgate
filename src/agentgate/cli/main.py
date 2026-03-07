import re
import subprocess
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


@cli.command(name="list")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option("--skill", default=None, help="Filter agents by skill ID or name.")
def list_agents(server: str, skill: str | None):
    """List all deployed agents."""
    try:
        params = {}
        if skill:
            params["skill"] = skill
        r = httpx.get(f"{server}/agents/", params=params, timeout=5)
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    agents = r.json()
    if not agents:
        if skill:
            click.echo(f"No agents found with skill '{skill}'.")
        else:
            click.echo("No agents deployed yet.")
        return

    click.echo(f"{'NAME':<25} {'VERSION':<10} {'ID'}")
    click.echo("-" * 70)
    for a in agents:
        click.echo(f"{a['name']:<25} {a['version']:<10} {a['id']}")
    click.echo(f"\n{len(agents)} agent(s) found.")


@cli.command()
@click.argument("agent_id")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
def delete(agent_id: str, server: str, api_key: str):
    """Delete a deployed agent by ID."""
    try:
        r = httpx.delete(
            f"{server}/agents/{agent_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code == 204:
        click.echo(f"Agent {agent_id} deleted successfully.")
    elif r.status_code == 404:
        click.echo(f"Error: agent {agent_id} not found.", err=True)
        raise SystemExit(1)
    else:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument("agent_id")
@click.option("--name", default=None, help="New agent name.")
@click.option("--description", default=None, help="New description.")
@click.option("--url", default=None, help="New agent URL.")
@click.option("--version", "agent_version", default=None, help="New version.")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
def update(agent_id: str, name: str | None, description: str | None, url: str | None,
           agent_version: str | None, server: str, api_key: str):
    """Update an existing agent by ID.

    Pass only the fields you want to change.
    """
    fields = {}
    if name is not None:
        fields["name"] = name
    if description is not None:
        fields["description"] = description
    if url is not None:
        fields["url"] = url
    if agent_version is not None:
        fields["version"] = agent_version

    if not fields:
        click.echo(
            "Error: no fields to update. Use --name, --description, --url, or --version.",
            err=True,
        )
        raise SystemExit(1)

    try:
        r = httpx.put(
            f"{server}/agents/{agent_id}",
            json=fields,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code == 200:
        agent = r.json()
        click.echo(f"Agent {agent_id} updated successfully!")
        click.echo(f"  Name: {agent['name']}")
        click.echo(f"  Version: {agent['version']}")
    elif r.status_code == 404:
        click.echo(f"Error: agent {agent_id} not found.", err=True)
        raise SystemExit(1)
    else:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
@click.option("--name", default=None, help="Agent name (overrides agentgate.yaml).")
@click.option("--register-only", is_flag=True, help="Only register (don't upload & build).")
def deploy(path: str, server: str, api_key: str, name: str | None, register_only: bool):
    """Deploy an agent from a local directory.

    PATH is the directory containing your agent code (agent.py) and optionally
    an agentgate.yaml config file.

    By default, the agent code is packaged, uploaded, and built on the AgentGate
    server. Use --register-only to just register an externally-hosted agent.
    """
    agent_dir = Path(path)
    config_file = agent_dir / "agentgate.yaml"

    config = {}
    if config_file.exists():
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}

    agent_name = name or config.get("name")
    if not agent_name:
        click.echo(
            "Error: agent name required. Set 'name' in agentgate.yaml or use --name.",
            err=True,
        )
        raise SystemExit(1)

    # Register-only mode: old behavior (requires URL)
    if register_only:
        if "url" not in config:
            click.echo("Error: 'url' is required in agentgate.yaml for --register-only.", err=True)
            raise SystemExit(1)

        payload = {
            "name": agent_name,
            "url": config["url"],
            "description": config.get("description", ""),
            "version": config.get("version", "1.0.0"),
            "skills": config.get("skills", []),
        }

        click.echo(f"Registering agent '{agent_name}' on {server}...")

        try:
            r = httpx.post(
                f"{server}/agents/",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
        except httpx.ConnectError:
            click.echo(f"Error: cannot reach server at {server}", err=True)
            raise SystemExit(1)

        if r.status_code == 201:
            agent = r.json()
            click.echo("Agent registered successfully!")
            click.echo(f"  ID:   {agent['id']}")
            click.echo(f"  Name: {agent['name']}")
            click.echo(f"  Card: {server}/agents/{agent['id']}/card")
        else:
            click.echo(f"Error ({r.status_code}): {r.text}", err=True)
            raise SystemExit(1)
        return

    # Full deploy mode: package, upload, build, run
    if not (agent_dir / "agent.py").exists():
        click.echo("Error: agent.py not found in the agent directory.", err=True)
        click.echo("Your agent must have an agent.py with a FastAPI app.", err=True)
        raise SystemExit(1)

    description = config.get("description", "")
    version = config.get("version", "1.0.0")

    click.echo(f"Packaging agent '{agent_name}'...")
    tar_path = _create_tarball(agent_dir)

    click.echo(f"Uploading to {server}...")
    try:
        with open(tar_path, "rb") as f:
            r = httpx.post(
                f"{server}/deploy/",
                files={"file": ("agent.tar.gz", f, "application/gzip")},
                data={"name": agent_name, "description": description, "version": version},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=120,
            )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)
    finally:
        tar_path.unlink(missing_ok=True)

    if r.status_code == 201:
        data = r.json()
        click.echo("Agent deployed successfully!")
        click.echo(f"  ID:   {data['id']}")
        click.echo(f"  Name: {data['name']}")
        click.echo(f"  Task: {data['task_url']}")
        click.echo(f"  Card: {data['card_url']}")
    else:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)


def _create_tarball(agent_dir: Path) -> Path:
    """Create a tar.gz archive of the agent directory."""
    import tarfile
    import tempfile

    tar_path = Path(tempfile.mktemp(suffix=".tar.gz"))
    with tarfile.open(tar_path, "w:gz") as tf:
        for item in agent_dir.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            tf.add(item, arcname=item.name)
    return tar_path


@cli.command()
@click.argument("agent_id")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
def undeploy(agent_id: str, server: str, api_key: str):
    """Stop and remove a deployed agent."""
    click.echo(f"Undeploying agent {agent_id}...")
    try:
        r = httpx.delete(
            f"{server}/deploy/{agent_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code == 200:
        click.echo("Agent undeployed successfully!")
    elif r.status_code == 404:
        click.echo(f"Error: agent {agent_id} not found.", err=True)
        raise SystemExit(1)
    else:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument("agent_id")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
@click.option("--limit", default=20, help="Number of logs to show.")
def logs(agent_id: str, server: str, api_key: str, limit: int):
    """Show invocation logs for an agent."""
    try:
        r = httpx.get(
            f"{server}/agents/{agent_id}/logs",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code != 200:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)

    entries = r.json()
    if not entries:
        click.echo("No logs found for this agent.")
        return

    click.echo(f"{'TIME':<22} {'STATUS':<10} {'LATENCY':<10} {'CALLER IP':<16} {'TASK ID'}")
    click.echo("-" * 80)
    for log in entries:
        ts = log["created_at"][:19].replace("T", " ")
        status = log["status"]
        latency = f"{log['latency_ms']}ms"
        ip = log["caller_ip"]
        tid = log.get("task_id") or "-"
        click.echo(f"{ts:<22} {status:<10} {latency:<10} {ip:<16} {tid}")
    click.echo(f"\n{len(entries)} log(s) shown.")


@cli.command()
@click.argument("agent_id")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
def usage(agent_id: str, server: str, api_key: str):
    """Show usage stats for an agent."""
    try:
        r = httpx.get(
            f"{server}/agents/{agent_id}/usage",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code != 200:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)

    data = r.json()
    click.echo(f"Agent: {data['agent_name']} ({data['agent_id']})")
    click.echo(f"  Total invocations: {data['total_invocations']}")
    click.echo(f"  Total errors:      {data['total_errors']}")
    click.echo(f"  Avg latency:       {data['avg_latency_ms']}ms")
    last = data.get("last_invocation")
    if last:
        click.echo(f"  Last invocation:   {last[:19].replace('T', ' ')}")
    else:
        click.echo("  Last invocation:   never")


@cli.command()
@click.argument("agent_id")
@click.option("--server", default=DEFAULT_SERVER, help="AgentGate server URL.")
@click.option(
    "--api-key", envvar="AGENTGATE_API_KEY", required=True,
    help="API key (or set AGENTGATE_API_KEY).",
)
@click.option("--period", default="day", type=click.Choice(["day", "month"]),
              help="Group by day or month.")
@click.option("--days", default=30, help="Number of days to look back.")
def billing(agent_id: str, server: str, api_key: str, period: str, days: int):
    """Show usage breakdown for an agent by day or month."""
    try:
        r = httpx.get(
            f"{server}/agents/{agent_id}/usage/breakdown",
            params={"period": period, "days": days},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo(f"Error: cannot reach server at {server}", err=True)
        raise SystemExit(1)

    if r.status_code != 200:
        click.echo(f"Error ({r.status_code}): {r.text}", err=True)
        raise SystemExit(1)

    data = r.json()
    click.echo(f"Agent: {data['agent_name']} — {period}ly breakdown (last {days} days)")
    breakdown = data.get("breakdown", [])
    if not breakdown:
        click.echo("No data in this period.")
        return

    click.echo(f"\n{'PERIOD':<14} {'INVOCATIONS':<14} {'ERRORS':<10} {'AVG LATENCY'}")
    click.echo("-" * 52)
    for row in breakdown:
        p = row["period"][:10] if period == "day" else row["period"][:7]
        click.echo(
            f"{p:<14} {row['invocations']:<14} {row['errors']:<10} {row['avg_latency_ms']}ms"
        )


def _bump_version(current: str, part: str) -> str:
    """Bump a semver version string."""
    major, minor, patch = [int(x) for x in current.split(".")]
    if part == "major":
        return f"{major + 1}.0.0"
    elif part == "minor":
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


@cli.command()
@click.argument("part", type=click.Choice(["major", "minor", "patch"]))
@click.option("--tag/--no-tag", default=True, help="Create a git tag (default: yes).")
def bump(part: str, tag: bool):
    """Bump the project version (major, minor, or patch).

    Updates pyproject.toml and __init__.py, then creates a git tag.
    Push the tag to trigger PyPI publish.
    """
    root = Path(__file__).resolve().parents[3]
    pyproject = root / "pyproject.toml"
    init_file = root / "src" / "agentgate" / "__init__.py"

    new_version = _bump_version(__version__, part)

    # Update pyproject.toml
    text = pyproject.read_text()
    text = re.sub(r'version = "[^"]+"', f'version = "{new_version}"', text, count=1)
    pyproject.write_text(text)

    # Update __init__.py
    text = init_file.read_text()
    text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new_version}"', text, count=1)
    init_file.write_text(text)

    click.echo(f"Version bumped: {__version__} -> {new_version}")

    if tag:
        subprocess.run(["git", "add", str(pyproject), str(init_file)], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: bump version to {new_version}"],
            cwd=root, check=True,
        )
        subprocess.run(["git", "tag", f"v{new_version}"], cwd=root, check=True)
        click.echo(f"Git tag v{new_version} created.")
        click.echo("Run 'git push && git push --tags' to publish to PyPI.")
