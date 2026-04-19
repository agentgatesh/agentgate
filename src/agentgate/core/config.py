import logging
import sys

from pydantic_settings import BaseSettings

logger = logging.getLogger("agentgate.config")

# Default values that MUST be overridden in production. A self-hosted
# instance that ships with these unchanged is a pre-pwned instance.
_INSECURE_DEFAULTS = {
    "secret_key": "changeme",
    "admin_password": "changeme",
    "deployer_secret": "changeme",
}


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    database_url: str = "postgresql+asyncpg://agentgate:changeme@localhost:5432/agentgate"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    secret_key: str = "changeme"
    api_key: str = ""
    redis_url: str = ""
    log_retention_days: int = 30
    log_max_per_agent: int = 10000
    plugin_config: str = ""
    deploy_dir: str = "/data/deploys"
    docker_network: str = "agentgate_default"
    deploy_port_start: int = 9100
    admin_username: str = "admin"
    admin_password: str = "changeme"
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    base_url: str = "https://agentgate.sh"
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_pro_price_id: str = ""
    stripe_connect_withdrawal_fee_pct: float = 0.03
    stripe_connect_min_withdrawal: float = 10.0
    deployer_url: str = "http://deployer:8100"
    deployer_secret: str = ""
    resend_api_key: str = ""
    email_from: str = "AgentGate <noreply@agentgate.sh>"
    rapidapi_key: str = ""
    disposable_list_path: str = "/app/data/disposable-domains.txt"


settings = Settings()


def validate_secrets(settings_obj: Settings = settings) -> list[str]:
    """Return a list of insecure-default secrets that must be overridden.

    Empty list means we're safe. Non-empty means we refuse to start in
    production.
    """
    bad: list[str] = []
    for field, insecure in _INSECURE_DEFAULTS.items():
        if getattr(settings_obj, field, None) == insecure:
            bad.append(field.upper())
    return bad


def enforce_secrets_or_exit() -> None:
    """Called on server startup. Aborts if insecure defaults are in use
    AND we're not in debug/test mode."""
    bad = validate_secrets()
    if not bad:
        return
    if settings.debug:
        logger.warning(
            "Insecure default secrets detected (%s) — allowed only "
            "because DEBUG=true. Never run this way in production.",
            ", ".join(bad),
        )
        return
    logger.critical(
        "Refusing to start: these secrets still use the 'changeme' "
        "default: %s. Set them to strong random values in your .env "
        "before starting the server.",
        ", ".join(bad),
    )
    sys.exit(1)
