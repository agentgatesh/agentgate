from pydantic_settings import BaseSettings


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


settings = Settings()
