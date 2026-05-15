"""
Configuración centralizada del asistente de productividad.

Carga settings desde variables de entorno (Cloud Run / Secret Manager)
o desde un archivo .env para desarrollo local.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación vía variables de entorno."""

    # --- API Keys ---
    # groq_api_key: str  # TODO: descomentar cuando se active ESP32 + STT
    gemini_api_key: str = ""
    notion_token: str
    # esp32_api_key: str  # TODO: descomentar cuando se active ESP32

    # --- Telegram ---
    telegram_bot_token: str
    telegram_allowed_user_id: int  # Tu user ID de Telegram (seguridad)
    telegram_webhook_secret: str = ""  # Secret token para validar webhooks
    telegram_webhook_url: str = ""  # URL pública del webhook (Cloud Run)
    proactive_secret: str = ""  # Secret para endpoint /proactive (Cloud Scheduler)

    # --- GCP ---
    gcp_project_id: str = ""
    gcs_bucket_name: str = ""
    firestore_collection_prefix: str = "assistant"

    # --- Notion Database IDs ---
    notion_tasks_db: str
    notion_notes_db: str
    notion_goals_db: str
    notion_daily_agenda_db: str

    # --- Firebase Auth (PWA) ---
    allowed_email: str = ""
    firebase_credentials_path: str = ""

    # --- Mem0 ---
    mem0_api_key: str = ""

    # --- Usuario (hardcoded, un solo usuario) ---
    user_name: str = "Noe"
    user_timezone: str = "America/Lima"
    user_id: str = "noe"

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
