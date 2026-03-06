from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    database_url: str = "postgresql+asyncpg://agentgate:changeme@localhost:5432/agentgate"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    secret_key: str = "changeme"
    api_key: str = ""


settings = Settings()
