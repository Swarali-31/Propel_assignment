from pathlib import Path
from pydantic_settings import BaseSettings


def _default_data_dir() -> Path:
    docker = Path("/data")
    if docker.exists() or Path("/.dockerenv").exists():
        docker.mkdir(parents=True, exist_ok=True)
        return docker
    local = Path(__file__).resolve().parents[2] / ".data"
    local.mkdir(parents=True, exist_ok=True)
    return local


class Settings(BaseSettings):
    data_dir: Path = _default_data_dir()
    database_url: str | None = None
    debounce_seconds: float = 25.0
    scheduled_grace_before_minutes: int = 20
    scheduled_grace_after_minutes: int = 40
    heartbeat_timeout_seconds: float = 20 * 60
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    seed_on_startup: bool = True
    cors_origins: str = "*"

    class Config:
        env_file = ".env"
        extra = "ignore"

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'kspdb.db').as_posix()}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
