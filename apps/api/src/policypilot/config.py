"""Application settings from the environment (12-factor). See .env.example for every variable."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelRole = Literal["router", "extractor", "advisor", "writer"]


class Settings(BaseSettings):
    # The repository-root .env is read when tools run from apps/api; containers get real env vars.
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- app
    app_env: Literal["local", "test", "production"] = "local"
    app_secret_key: SecretStr = SecretStr("")
    app_base_url: str = "http://localhost:8080"
    web_origins: list[str] = ["http://localhost:8080", "http://localhost:5173"]
    log_level: str = "info"
    agency_name: str = "Harbor & Pine Insurance Agency"
    default_locale: str = "en"

    # --- database (ADR-0004)
    database_url: str = "postgresql+psycopg://policypilot:policypilot@localhost:5432/policypilot"
    expected_database: str = Field(default="policypilot", alias="POLICYPILOT_EXPECTED_DB")
    db_pool_max: int = 10
    checkpoint_pool_max: int = 5

    # --- models (ADR-0006, ADR-0007, ADR-0017)
    llm_provider: Literal["auto", "anthropic", "fake"] = "auto"
    anthropic_api_key: SecretStr = SecretStr("")
    llm_router_model: str = "claude-haiku-5-5"
    llm_extractor_model: str = "claude-haiku-5-5"
    llm_advisor_model: str = "claude-opus-5-5"
    llm_writer_model: str = "claude-opus-5-5"
    llm_advisor_effort: Literal["low", "medium", "high"] = "medium"
    llm_writer_effort: Literal["low", "medium", "high"] = "low"
    llm_server_side_fallback: bool = True
    llm_fallback_provider: Literal["none", "openai", "bedrock"] = "none"
    openai_api_key: SecretStr = SecretStr("")
    llm_fallback_small_model: str = "gpt-6-luna"
    llm_fallback_large_model: str = "gpt-6.2-sol"
    bedrock_region: str = "us-east-1"
    llm_max_tokens_advisor: int = 2048
    llm_max_tool_rounds: int = 4
    llm_daily_call_limit: int = 2000
    llm_timeout_seconds: float = 60.0

    # --- retrieval (ADR-0008)
    embedding_provider: Literal["fastembed", "hashing"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    fastembed_cache_dir: str | None = None
    documents_output_dir: str = ".data/documents"

    # --- chat limits
    chat_max_message_chars: int = 2000
    chat_turns_per_conversation_per_day: int = 200
    customer_token_ttl_hours: int = 24 * 7
    agent_token_ttl_minutes: int = 60

    # --- WhatsApp (ADR-0009)
    whatsapp_mode: Literal["simulator", "cloud"] = "simulator"
    whatsapp_graph_version: str = "v27.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_verify_token: SecretStr = SecretStr("")

    # --- n8n (ADR-0012)
    n8n_events_url: str = ""
    n8n_webhook_token: SecretStr = SecretStr("")

    # --- MCP (ADR-0010)
    mcp_allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "api:*"]
    mcp_allowed_origins: list[str] = ["http://localhost:*"]

    # --- seed-time local credentials (never used in production)
    seed_agent_email: str = "agent@policypilot.test"
    seed_agent_password: SecretStr = SecretStr("")
    mcp_dev_token: SecretStr = SecretStr("")
    n8n_integration_token: SecretStr = SecretStr("")

    # --- observability (ADR-0011)
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_base_url: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_llm_provider(self) -> Literal["anthropic", "fake"]:
        """`auto` means Claude when a key is configured, otherwise the scripted demo model."""
        if self.llm_provider == "auto":
            return "anthropic" if self.anthropic_api_key.get_secret_value() else "fake"
        return self.llm_provider

    @property
    def demo_mode(self) -> bool:
        return self.resolved_llm_provider == "fake"

    @property
    def psycopg_url(self) -> str:
        """The libpq form of DATABASE_URL, for psycopg pools (LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def require_real_secrets(self) -> None:
        """Refuse to start with empty or placeholder secrets (security-and-privacy.md)."""
        if self.app_env == "test":
            return
        required = {
            "APP_SECRET_KEY": self.app_secret_key,
            "WHATSAPP_APP_SECRET": self.whatsapp_app_secret,
            "WHATSAPP_VERIFY_TOKEN": self.whatsapp_verify_token,
        }
        if self.n8n_events_url:
            required["N8N_WEBHOOK_TOKEN"] = self.n8n_webhook_token
        bad = [
            name
            for name, secret in required.items()
            if not secret.get_secret_value() or "change-me" in secret.get_secret_value()
        ]
        if bad:
            raise RuntimeError(
                f"refusing to start: {', '.join(bad)} missing or placeholder; "
                "run `python scripts/init_env.py`"
            )

    def model_for(self, role: ModelRole) -> str:
        return {
            "router": self.llm_router_model,
            "extractor": self.llm_extractor_model,
            "advisor": self.llm_advisor_model,
            "writer": self.llm_writer_model,
        }[role]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
