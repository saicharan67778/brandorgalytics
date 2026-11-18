"""Environment configuration and safe client-facing failures."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    llm_provider: Literal["gemini", "openai", "anthropic"] = "gemini"
    llm_model: str = ""
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    port: int = Field(4000, ge=1, le=65535)
    rate_limit: int = Field(6, ge=1, le=1000)
    cache_ttl: int = Field(900, ge=0)


class Failure(Exception):
    """Expose only constant, developer-authored messages."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
