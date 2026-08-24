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
    # esp32_api_key: str  # TODO: descomentar cuando se active ESP32

    # --- Telegram ---
    # Optional: without a bot the agent still runs and answers over
    # POST /api/v1/chat, which is enough to try it out.
    telegram_bot_token: str = ""
    telegram_allowed_user_id: int = 0  # The only Telegram user id accepted
    telegram_webhook_secret: str = ""  # Secret token used to validate webhooks
    telegram_webhook_url: str = ""  # Public webhook URL (Cloud Run)
    # Activity log: a secondary bot that writes to a separate chat, so the
    # agent's internal log never mixes with the real conversation.
    telegram_log_bot_token: str = ""
    telegram_log_chat_id: str = ""  # Falls back to telegram_allowed_user_id
    proactive_secret: str = ""  # Secret para endpoint /proactive (Cloud Scheduler)

    # --- Storage ---
    # "sqlite"    -> a local file. No account, no project, no network.
    # "firestore" -> Google Cloud Firestore. Needs gcp_project_id.
    # "auto"      -> firestore when a GCP project is set, sqlite otherwise.
    storage_backend: str = "auto"
    sqlite_path: str = "data/aileon.db"

    # --- GCP ---
    gcp_project_id: str = ""
    gcs_bucket_name: str = ""
    firestore_collection_prefix: str = "assistant"

    # --- Notion (optional integration) ---
    # Leave empty to run without Notion. Tasks, notes and goals tools are then
    # not offered to the model at all, instead of failing when called.
    notion_token: str = ""
    notion_tasks_db: str = ""
    notion_notes_db: str = ""
    notion_goals_db: str = ""
    notion_key_results_db: str = ""

    # --- Google Calendar (optional integration) ---
    # Enabled when an OAuth token is found, either in Secret Manager or at this
    # path. Without it the planner is disabled and no calendar tools are offered.
    calendar_token_path: str = "token.json"

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
        # A leftover line in .env — an integration that was removed, a variable
        # from another project — must not stop the app from starting.
        extra="ignore",
    )

    # --- Integrations ---------------------------------------------------------
    # Every integration is optional and answers for itself here. One place
    # decides whether something is on, so main.py, the tool list and the prompt
    # can never disagree about it.

    @property
    def notion_enabled(self) -> bool:
        """Notion needs a token and at least one database to be useful."""
        return bool(self.notion_token) and bool(
            self.notion_tasks_db or self.notion_notes_db or self.notion_goals_db
        )

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def model_enabled(self) -> bool:
        """Either an AI Studio key or a GCP project for Vertex."""
        return bool(self.gemini_api_key or self.gcp_project_id)

    def missing_requirements(self) -> list[str]:
        """
        What genuinely stops the agent from working, as opposed to what merely
        limits it. Used at startup to fail loudly and early.
        """
        problems: list[str] = []
        if not self.model_enabled:
            problems.append(
                "No model configured. Set GEMINI_API_KEY (from aistudio.google.com) "
                "or GCP_PROJECT_ID to use Vertex AI."
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    """Retorna singleton de settings cacheado."""
    return Settings()
