from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://chainguard:replace-me@localhost:5432/chainguard"
    session_ttl_hours: int = Field(default=8, ge=1, le=720)
    environment: str = "development"
    bitcoin_provider_base_url: str = "https://mempool.space/api"
    bitcoin_provider_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ethereum_provider_url: str | None = None
    ethereum_provider_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    evidence_storage_path: Path = Path("./data/evidence")

    @model_validator(mode="after")
    def validate_provider_urls(self) -> "Settings":
        if self.ethereum_provider_url == "":
            self.ethereum_provider_url = None
        urls = [("Bitcoin", self.bitcoin_provider_base_url)]
        if self.ethereum_provider_url is not None:
            urls.append(("Ethereum", self.ethereum_provider_url))
        for label, url in urls:
            parsed = urlparse(url)
            local_http = self.environment == "development" and parsed.hostname in {"localhost", "127.0.0.1"}
            if parsed.scheme != "https" and not (parsed.scheme == "http" and local_http):
                raise ValueError(f"{label} provider URL must use HTTPS (HTTP is allowed only for localhost development).")
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f"{label} provider URL must be an origin/path without credentials, query, or fragment.")
        self.bitcoin_provider_base_url = self.bitcoin_provider_base_url.rstrip("/")
        if self.ethereum_provider_url is not None:
            self.ethereum_provider_url = self.ethereum_provider_url.rstrip("/")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
