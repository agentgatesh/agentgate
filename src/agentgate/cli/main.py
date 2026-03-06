import click

from agentgate import __version__


@click.group()
@click.version_option(version=__version__)
def cli():
    """AgentGate — Deploy, connect, and monetize AI agents."""
    pass


@cli.command()
def status():
    """Show AgentGate server status."""
    click.echo(f"AgentGate v{__version__}")
    click.echo("Status: CLI is working")
