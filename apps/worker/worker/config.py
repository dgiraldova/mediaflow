"""Runtime configuration for the media worker.

All values are sourced from environment variables so that no secret ever
needs to live in source control. See ``.env.example`` at the repository
root for the full documented list of variables.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TemporalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TEMPORAL_", extra="ignore")

    host: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "media-ingestion"
    api_key: str | None = None
    tls: bool = False


class StorageSettings(BaseSettings):
    """Cloudflare R2 (S3-compatible) configuration."""

    model_config = SettingsConfigDict(env_prefix="R2_", extra="ignore")

    account_id: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = "mediaflow-media"
    endpoint_url: str = ""
    region: str = "auto"
    signed_url_ttl_seconds: int = 3600
    presigned_upload_ttl_seconds: int = 3600

    @property
    def resolved_endpoint_url(self) -> str:
        if self.endpoint_url:
            return self.endpoint_url
        if self.account_id:
            return f"https://{self.account_id}.r2.cloudflarestorage.com"
        return "http://localhost:9000"


class TranscriptionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRANSCRIPTION_", extra="ignore")

    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str = "whisper-1"


class TwelveLabsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TWELVE_LABS_", extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.twelvelabs.io/v1.3"
    index_id: str = ""


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", extra="ignore")

    api_key: str = ""
    base_url: str | None = None
    classification_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536


class ApiSettings(BaseSettings):
    """Member B's FastAPI service, which owns the canonical database.

    When ``internal_token`` is set the worker persists through the internal
    worker endpoints; otherwise it falls back to in-memory repositories so the
    pipeline still runs standalone.
    """

    model_config = SettingsConfigDict(env_prefix="MEDIAFLOW_API_", extra="ignore")

    base_url: str = "http://127.0.0.1:3000"
    internal_token: str = ""
    timeout_seconds: float = 30.0


class GoogleDriveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOOGLE_DRIVE_", extra="ignore")

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    credential_encryption_key: str = ""
    sync_interval_minutes: int = 15


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development")
    log_level: str = "INFO"
    temp_dir: str = "/tmp/mediaflow-worker"
    max_upload_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GiB
    analysis_version: str = "2026-07-25.1"
    embedding_batch_size: int = 32

    temporal: TemporalSettings = Field(default_factory=TemporalSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    twelve_labs: TwelveLabsSettings = Field(default_factory=TwelveLabsSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    google_drive: GoogleDriveSettings = Field(default_factory=GoogleDriveSettings)


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
