"""Runtime settings. Secrets come from the environment, never from the repo."""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_database_url(url: str) -> str:
    if not url.startswith("sqlite:///"):
        return url
    raw = url.removeprefix("sqlite:///")
    if raw in {"", ":memory:"}:
        return url
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + path.as_posix()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///data/factory.db"
    host: str = "0.0.0.0"
    port: int = 8741

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # dry_run is the only mode that can "publish" in this version.
    avito_client_mode: str = "dry_run"
    avito_client_id: str = ""
    avito_client_secret: str = ""
    avito_api_base: str = "https://api.avito.ru"
    avito_token_url: str = "https://api.avito.ru/token"

    run_factory_loop: bool = True
    factory_poll_seconds: float = 1.0
    worker_poll_seconds: float = 1.0

    @model_validator(mode="after")
    def _normalize(self) -> "Settings":
        self.database_url = resolve_database_url(self.database_url)
        if self.avito_client_mode not in {"dry_run", "live"}:
            raise ValueError("AVITO_CLIENT_MODE должен быть dry_run или live")
        return self


def get_settings() -> Settings:
    return Settings()
