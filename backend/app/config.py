"""
Centralised configuration for the productivity assistant.

Loads settings from environment variables (Cloud Run / Secret Manager).
o desde un archivo .env para desarrollo local.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables."""

    # --- API Keys ---
    # groq_api_key: str  # TODO: descomentar cuando se active ESP32 + STT
    gemini_api_key: str = ""

    # Which model does the reasoning: chat, planner and the weekly analysis.
    # Override with the GEMINI_MODEL env var; no redeploy of code needed, just a
    # variable change.
    gemini_model: str = "gemini-2.5-pro"

    # A cheaper model for work that is classification, not reasoning. Right now
    # only the tagger, which runs on every single message and only has to fill in
    # a small JSON. Paying reasoning prices there buys nothing.
    gemini_model_fast: str = "gemini-2.5-flash-lite"
    notion_token: str
    # esp32_api_key: str  # TODO: descomentar cuando se active ESP32

    # --- Telegram ---
    telegram_bot_token: str
    telegram_allowed_user_id: int  # The only Telegram user id accepted
    telegram_webhook_secret: str = ""  # Secret token used to validate webhooks
    telegram_webhook_url: str = ""  # Public webhook URL (Cloud Run)
    # Activity log: a secondary bot that writes to a separate chat, so the
    # agent's internal log never mixes with the real conversation.
    telegram_log_bot_token: str = ""
    telegram_log_chat_id: str = ""  # Falls back to telegram_allowed_user_id
    proactive_secret: str = ""  # Secret para endpoint /proactive (Cloud Scheduler)

    # --- GCP ---
    gcp_project_id: str = ""
    gcs_bucket_name: str = ""
    firestore_collection_prefix: str = "assistant"

    # --- Notion Database IDs ---
    notion_tasks_db: str
    notion_notes_db: str
    notion_goals_db: str

    notion_key_results_db: str = ""

    # --- Firebase Auth (PWA) ---
    allowed_email: str = ""
    firebase_credentials_path: str = ""

    # --- Mem0 ---
    mem0_api_key: str = ""

    # --- User (single-tenant deployment) ---
    user_name: str = "User"
    user_timezone: str = "America/Lima"
    user_id: str = "user"

    # --- Server ---
    debug: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Retorna singleton de settings cacheado."""
    return Settings()
