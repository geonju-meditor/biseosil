"""환경 변수·.env 설정."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    copilot_model: str = ""
    agent_provider: str = "copilot"  # copilot | deterministic
    copilot_cli_path: str | None = None
    applicationinsights_connection_string: str | None = None

    max_input_chars: int = 30_000
    max_inputs: int = 6
    max_concurrent_runs: int = 4
    rate_limit_runs: int = 30
    rate_limit_window_sec: int = 600
    run_timeout_sec: int = 180


@lru_cache
def get_settings() -> Settings:
    return Settings()
