"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_ROOT / "data"


class Settings(BaseSettings):
    """Runtime configuration.

    Values are read from process environment variables first, then from
    ``.env`` at the repository root. Secrets are never returned to clients.
    """

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "OpsMind AI"
    app_version: str = "1.0.0"
    environment: str = "development"
    demo_mode: bool = True

    database_url: str = f"sqlite:///{(DATA_DIR / 'opsmind.db').as_posix()}"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    frontend_dist: str = str(PROJECT_ROOT / "frontend" / "dist")

    # --- Groq (LLM inference) -------------------------------------------
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # The Groq SDK appends /openai/v1 itself; include only the host here.
    groq_base_url: str = "https://api.groq.com"
    groq_timeout: float = 60.0

    # --- Hindsight (persistent agent memory) ----------------------------
    hindsight_api_url: str = "https://api.hindsight.vectorize.io"
    hindsight_api_key: str | None = None
    hindsight_bank_id: str = "opsmind-engineering-incidents"
    hindsight_timeout: float = 90.0
    hindsight_budget: str = "mid"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.groq_api_key and self.groq_api_key.strip())

    @property
    def hindsight_configured(self) -> bool:
        return bool(
            self.hindsight_api_key
            and self.hindsight_api_key.strip()
            and self.hindsight_api_url
            and self.hindsight_api_url.strip()
        )

    @property
    def hindsight_endpoint(self) -> str:
        return self.hindsight_api_url.rstrip("/")

    @property
    def database_is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
