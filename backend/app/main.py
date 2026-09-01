"""
FastAPI application — personal productivity assistant.

Primary input: a Telegram bot (text).

Endpoints:
- POST /api/v1/chat     -> receive text, run the agent (internal/debug)
- POST /api/v1/tick     -> hourly cycle: cron skills + due reminders
- GET  /api/v1/health   -> health check
- GET  /api/v1/context  -> the user's current context

# TODO: uncomment when voice input is enabled
# - POST /api/v1/voice  -> receive audio, transcribe, run the agent
# - POST /api/v1/chat/stream -> text in, SSE streaming out
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import AsyncGenerator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.agent.core import ProductivityAgent
from app.config import Settings, get_settings
from app.models.schemas import (
    AgentResponse,
    ChatRequest,
    ContextResponse,
    HealthResponse,
)
from app.agent.planner import PlannerAgent
from app.agent.tick import TickHandler
from app.services.calendar import (
    CalendarService,
    load_calendar_token_from_secret,
    load_calendar_token_local,
)
from app.services.experiments import ExperimentService
from app.services.insights import InsightService
from app.services.memory import MemoryManager
from app.services.notion import NotionService
from app.services.principles import PrincipleService
from app.services.proactive import ProactiveService
from app.services.tagging import TaggingService
from app.services.agent_queue import AgentTaskQueue
from app.services.telegram import NullTelegramBot, TelegramBot
from app.services.mcp_client import DocumentStoreTokenStorage, McpIntegration
from app.storage import get_store
from app.storage.schema import Database

# TODO: uncomment when voice input is enabled
# from app.services.stt import STTService
# from app.services.storage import StorageService
# from app.middleware.auth import verify_request

logger = logging.getLogger(__name__)

# ── Global app state (initialised in the lifespan handler) ──────────────────
_notion_service: NotionService | None = None
_memory_manager: MemoryManager | None = None
_agent: ProductivityAgent | None = None
_telegram_bot: TelegramBot | NullTelegramBot | None = None
_proactive_service: ProactiveService | None = None
_insight_service: InsightService | None = None
_planner_agent: PlannerAgent | None = None
_calendar_service: CalendarService | None = None
_agent_queue: AgentTaskQueue | None = None
_tick_handler: TickHandler | None = None
_mcp_integrations: list[McpIntegration] = []

# TODO: uncomment when voice input is enabled
# _stt_service: STTService | None = None
# _storage_service: StorageService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise and tear down resources on startup/shutdown."""
    global _notion_service, _memory_manager, _agent, _telegram_bot

    settings = get_settings()
    _configure_logging(settings)

    logger.info("Initializing services...")

    # Anything genuinely required is checked once, here, with a message that
    # says what to do about it. Everything else is an optional integration.
    problems = settings.missing_requirements()
    if problems:
        for problem in problems:
            logger.error("%s", problem)
        raise RuntimeError("; ".join(problems))

    # Notion — optional. Without it the agent keeps memory, skills, experiments
    # and the proactive cycle; it just has nowhere to file tasks and goals.
    if settings.notion_enabled:
        _notion_service = NotionService(
            token=settings.notion_token,
            db_ids={
                "tasks": settings.notion_tasks_db,
                "notes": settings.notion_notes_db,
                "goals": settings.notion_goals_db,
                "key_results": settings.notion_key_results_db,
            },
        )
        logger.info("Notion integration enabled")
    else:
        _notion_service = None
        logger.info("Notion integration disabled (no token or database ids)")

    # Document store: sqlite by default, Firestore when configured. Still used
    # by the services that have not been converted to real tables yet.
    firestore_client = get_store(settings)

    # The real database. Services move onto this one at a time; the two share
    # the same file, so nothing is duplicated and there is no moment where the
    # app is half-migrated.
    database = Database(settings.sqlite_path)

    # Mem0
    mem0_client = _init_mem0(settings)

    # Principles
    principle_service = PrincipleService(
        firestore_client=firestore_client,
        collection_prefix=settings.firestore_collection_prefix,
    )

    # Experiments
    experiment_service = ExperimentService(
        firestore_client=firestore_client,
        collection_prefix=settings.firestore_collection_prefix,
        user_id=settings.user_id,
    )

    # Tagging (shared Gemini Flash client)
    tagging_service = _init_tagging_service(settings)

    # Insights service is built early: MemoryManager injects it into the prompt
    insights_genai = _build_genai_client(settings)
    insight_service_local = None
    if insights_genai is not None:
        insight_service_local = InsightService(
            firestore_client=firestore_client,
            telegram_bot=None,  # assigned after the bot is created
            genai_client=insights_genai,
            database=database,
            collection_prefix=settings.firestore_collection_prefix,
            user_id=settings.user_id,
            model_id=settings.gemini_model,
            experiment_service=experiment_service,
            notion_service=_notion_service,
        )

    # Memory Manager
    _memory_manager = MemoryManager(
        firestore_client=firestore_client,
        mem0_client=mem0_client,
        notion_service=_notion_service,
        database=database,
        collection_prefix=settings.firestore_collection_prefix,
        user_id=settings.user_id,
        principle_service=principle_service,
        tagging_service=tagging_service,
        insight_service=insight_service_local,
    )

    # Calendar + Planner — optional. Enabled by the presence of an OAuth token,
    # in Secret Manager or on disk.
    global _calendar_service, _planner_agent
    token_json = None
    if settings.gcp_project_id:
        token_json = load_calendar_token_from_secret(settings.gcp_project_id)
    if token_json is None:
        token_json = load_calendar_token_local(settings.calendar_token_path)

    if token_json:
        _calendar_service = CalendarService(
            token_json=token_json,
            user_timezone=settings.user_timezone,
        )
        logger.info("Google Calendar integration enabled")
    else:
        logger.info(
            "Google Calendar integration disabled (no OAuth token at %s)",
            settings.calendar_token_path,
        )

    # The planner turns goals into calendar blocks, so it needs both sides.
    if _calendar_service is not None and _notion_service is not None:
        _planner_agent = PlannerAgent(
            calendar_service=_calendar_service,
            notion_service=_notion_service,
            experiment_service=experiment_service,
            memory_manager=_memory_manager,
            gemini_api_key=settings.gemini_api_key,
            gcp_project_id=settings.gcp_project_id,
            user_timezone=settings.user_timezone,
            model_id=settings.gemini_model,
        )
        logger.info("Planner agent initialized (model=%s)", settings.gemini_model)
    else:
        logger.info("Planner disabled (needs both Calendar and Notion)")

    # One-time reminder queue
    global _agent_queue
    _agent_queue = AgentTaskQueue(
        db=firestore_client,
        collection_prefix=settings.firestore_collection_prefix,
    )

    # MCP servers — optional. Connecting is allowed to fail: an unreachable
    # server disables its tools instead of stopping the agent.
    global _mcp_integrations
    _mcp_integrations = []
    if settings.mcp_notion_enabled_effective:
        notion_mcp = McpIntegration(
            name="notion",
            url=settings.mcp_notion_url,
            token_storage=DocumentStoreTokenStorage(
                firestore_client,
                f"{settings.firestore_collection_prefix}_mcp_auth",
                f"{settings.user_id}_notion",
            ),
        )
        if await notion_mcp.connect():
            _mcp_integrations.append(notion_mcp)

    # Agent
    _agent = ProductivityAgent(
        notion_service=_notion_service,
        memory_manager=_memory_manager,
        user_timezone=settings.user_timezone,
        # Both are passed: the agent picks Vertex when a project is set and
        # falls back to AI Studio otherwise. Without the key, a deployment with
        # no GCP project could not build a client at all.
        gemini_api_key=settings.gemini_api_key,
        gcp_project_id=settings.gcp_project_id,
        experiment_service=experiment_service,
        planner=_planner_agent,
        calendar_service=_calendar_service,
        agent_queue=_agent_queue,
        model_id=settings.gemini_model,
        mcp_integrations=_mcp_integrations,
    )

    # Telegram Bot — optional. Without it the agent is reachable over
    # POST /api/v1/chat, which is enough to try it out before setting up a bot.
    if settings.telegram_enabled:
        _telegram_bot = TelegramBot(
            token=settings.telegram_bot_token,
            allowed_user_id=settings.telegram_allowed_user_id,
            agent=_agent,
            memory_manager=_memory_manager,
            log_bot_token=settings.telegram_log_bot_token,
            log_chat_id=settings.telegram_log_chat_id,
            # Webhook when a public URL is configured, polling otherwise. A
            # home server or a Pi has no public URL, so polling is what makes
            # those deployments possible at all.
            use_polling=not settings.telegram_webhook_url,
        )
        # The agent needs the bot to write to the activity log (e.g. profile
        # changes). Assigned here because TelegramBot takes the agent in its
        # constructor.
        _agent.telegram = _telegram_bot
        await _telegram_bot.start()
        logger.info("Telegram integration enabled")
    else:
        _telegram_bot = NullTelegramBot(memory_manager=_memory_manager)
        _agent.telegram = _telegram_bot
        logger.info(
            "Telegram integration disabled (no bot token) — "
            "the agent answers over POST /api/v1/chat"
        )

    # Skill manager, shared by ProactiveService and TickHandler
    from app.agent.skill_manager import SkillManager
    _tick_skill_manager = SkillManager(
        firestore_client=firestore_client,
        collection_prefix=settings.firestore_collection_prefix,
    )
    _proactive_cfg: dict = {}
    for _sk in _tick_skill_manager.load_all():
        if _sk["name"] == "proactive":
            _proactive_cfg = _sk.get("config") or {}
            break

    # Proactive service, and wire the bot into the insight service built above
    global _proactive_service, _insight_service
    if insight_service_local is not None:
        insight_service_local.telegram = _telegram_bot
        insight_service_local.memory = _memory_manager
        insight_service_local.calendar = _calendar_service
        _insight_service = insight_service_local

        _proactive_service = ProactiveService(
            firestore_client=firestore_client,
            experiment_service=experiment_service,
            notion_service=_notion_service,
            telegram_bot=_telegram_bot,
            genai_client=insights_genai,
            agent=_agent,
            skill_manager=_tick_skill_manager,
            database=database,
            collection_prefix=settings.firestore_collection_prefix,
            user_id=settings.user_id,
            config=_proactive_cfg,
        )

    # Tick Handler (replaces individual Cloud Scheduler jobs)
    global _tick_handler
    _tick_handler = TickHandler(
        skill_manager=_tick_skill_manager,
        planner=_planner_agent,
        proactive=_proactive_service,
        insights=_insight_service,
        agent=_agent,
        agent_queue=_agent_queue,
        telegram_bot=_telegram_bot,
        calendar_service=_calendar_service,
        user_timezone=settings.user_timezone,
    )

    # Register the webhook when a public URL is configured
    if (
        settings.telegram_enabled
        and settings.telegram_webhook_url
        and settings.telegram_webhook_secret
    ):
        webhook_url = f"{settings.telegram_webhook_url.rstrip('/')}/api/v1/telegram/webhook"
        await _telegram_bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
        )

    # TODO: uncomment when voice input is enabled
    # _stt_service = STTService(api_key=settings.groq_api_key)
    # _storage_service = StorageService(
    #     bucket_name=settings.gcs_bucket_name,
    #     project_id=settings.gcp_project_id,
    # )

    _log_startup_summary(settings)
    yield

    # Cleanup
    logger.info("Shutting down services...")
    if _telegram_bot:
        await _telegram_bot.stop()
    if _notion_service:
        await _notion_service.close()
    for _integration in _mcp_integrations:
        await _integration.close()
    # TODO: uncomment when voice input is enabled
    # if _stt_service:
    #     await _stt_service.close()


app = FastAPI(
    title="Productivity Assistant API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse()


@app.post("/api/v1/chat", response_model=AgentResponse)
async def process_chat(body: ChatRequest) -> AgentResponse:
    """Receive text and run it through the agent (internal/debug)."""
    assert _agent is not None

    response = await _agent.process(body.message, source="api")
    return response


@app.post("/api/v1/proactive")
async def proactive_trigger(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Hourly proactive cycle. Detects triggers and sends messages."""
    settings = get_settings()

    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid proactive secret")

    if _proactive_service is None:
        return {"ok": False, "reason": "proactive service not initialized"}

    # The user's local hour
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(settings.user_timezone))
    except Exception:
        from datetime import timezone as _tz, timedelta as _td
        now = datetime.now(_tz(_td(hours=-5)))

    result = await _proactive_service.run_cycle(current_hour_local=now.hour)
    return {"ok": True, **result}


@app.post("/api/v1/weekly-analysis")
async def weekly_analysis(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Weekly analysis over the last 7 days."""
    settings = get_settings()

    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    if _insight_service is None:
        return {"ok": False, "reason": "insights service not initialized"}

    result = await _insight_service.run_weekly_analysis()
    return result


@app.post("/api/v1/tick")
async def tick(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """
    Hourly tick — replaces all individual Cloud Scheduler jobs.

    Evaluates which skills have a matching cron schedule for the current hour,
    executes their jobs, and processes any due one-time tasks from the Firestore queue.
    """
    settings = get_settings()
    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _tick_handler is None:
        return {"ok": False, "reason": "tick handler not initialized"}
    return await _tick_handler.run()


@app.post("/api/v1/planner/plan-tomorrow")
async def planner_plan_tomorrow(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Ask the planner to build tomorrow's plan."""
    settings = get_settings()
    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _planner_agent is None:
        return {"ok": False, "reason": "planner not initialized"}
    result = await _planner_agent.plan_day()
    if _telegram_bot is not None:
        summary = result.get("summary", "")[:1500]
        await _telegram_bot.send_proactive_message(f"📅 *Tomorrow's plan is ready*\n\n{summary}")
    return {"ok": True, **result}


@app.post("/api/v1/planner/daily-review")
async def planner_daily_review(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Ask the user which of today's blocks they completed."""
    settings = get_settings()
    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _telegram_bot is None:
        return {"ok": False, "reason": "telegram not initialized"}

    # List the day's blocks so they can be included in the question
    blocks_text = ""
    if _calendar_service is not None:
        try:
            from zoneinfo import ZoneInfo
            from datetime import timezone as _tz
            tz = ZoneInfo(settings.user_timezone)
            today_local = datetime.now(tz).date()
            day_start = datetime.combine(today_local, datetime.min.time()).replace(tzinfo=_tz.utc)
            day_end = day_start + __import__("datetime").timedelta(days=1)
            events = _calendar_service.list_events(start=day_start, end=day_end)
            plan_events = [e for e in events if "[plan]" in (e.get("summary") or "")]
            if plan_events:
                lines = [f"- {e.get('summary', '?').replace('[plan] ', '')}" for e in plan_events]
                blocks_text = "\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("daily-review: could not fetch blocks: %s", exc)

    question = (
        f"🌙 *Review del día*\n\n"
        f"¿Cuáles de estos bloques completaste hoy?{blocks_text}\n\n"
        f"Respondeme y genero el resumen."
    )
    await _telegram_bot.send_proactive_message(question)
    return {"ok": True, "asked": True}


@app.post("/api/v1/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
) -> dict:
    """Receive Telegram updates over the webhook."""
    assert _telegram_bot is not None
    settings = get_settings()

    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    update_data = await request.json()
    await _telegram_bot.process_update(update_data)
    return {"ok": True}


@app.get("/api/v1/context", response_model=ContextResponse)
async def get_context() -> ContextResponse:
    """Return the current context: today's tasks and active goals."""
    assert _notion_service is not None

    today_str = date.today().isoformat()

    today_tasks = await _notion_service.get_tasks(date=today_str)
    goals = await _notion_service.get_goals(status="active")

    return ContextResponse(
        today_tasks=today_tasks,
        active_goals=goals,
    )


# TODO: uncomment when voice input is enabled
# @app.post("/api/v1/voice", response_model=AgentResponse)
# async def process_voice(
#     request: Request,
#     source: str = Depends(verify_request),
# ) -> AgentResponse:
#     """Receive WAV/WebM audio, transcribe it and run the agent."""
#     assert _stt_service is not None
#     assert _agent is not None
#     assert _storage_service is not None
#
#     content_type = request.headers.get("content-type", "audio/wav")
#     audio_data = await request.body()
#
#     if not audio_data:
#         return AgentResponse(message="No audio received.", source=source)
#
#     import asyncio
#     asyncio.create_task(
#         _storage_service.upload_audio(audio_data, content_type, source)
#     )
#
#     transcription = await _stt_service.transcribe(audio_data, content_type)
#     if not transcription:
#         return AgentResponse(
#             message="I could not understand the audio. Could you repeat it?",
#             source=source,
#         )
#
#     response = await _agent.process(transcription, source=source)
#     response.source = source
#     return response
#
# @app.post("/api/v1/chat/stream")
# async def process_chat_stream(
#     body: ChatRequest,
#     source: str = Depends(verify_request),
# ) -> StreamingResponse:
#     """Receive text and return an SSE stream."""
#     assert _agent is not None
#     async def event_generator() -> AsyncGenerator[str, None]:
#         async for chunk in _agent.process_stream(body.message, source=source):
#             yield f"data: {chunk}\n\n"
#     return StreamingResponse(
#         event_generator(),
#         media_type="text/event-stream",
#         headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
#     )


# ── Initialisation helpers ───────────────────────────────────────────────────


def _telegram_mode(settings: Settings) -> str:
    if not settings.telegram_enabled:
        return "off — POST /api/v1/chat"
    return "webhook" if settings.telegram_webhook_url else "polling"


def _log_startup_summary(settings: Settings) -> None:
    """Print what is on and what is off, so a fresh install is self-explaining."""
    backend = (settings.storage_backend or "auto").lower()
    if backend == "auto":
        backend = "firestore" if settings.gcp_project_id else "sqlite"

    lines = [
        "",
        "  aileon is up",
        f"    storage      {backend}",
        f"    tables       {_agent._memory.sql.journal_mode if _agent else '?'} journal",
        f"    model        {settings.gemini_model}",
        f"    notion       {'on' if settings.notion_enabled else 'off'}",
        f"    calendar     {'on' if _calendar_service else 'off'}",
        f"    planner      {'on' if _planner_agent else 'off'}",
        f"    telegram     {_telegram_mode(settings)}",
        f"    mcp          {', '.join(i.name for i in _mcp_integrations) or 'none'}",
        f"    tools        {len(_agent._tools) if _agent else 0} available",
        "",
    ]
    logger.info("\n".join(lines))


def _build_genai_client(settings: Settings):
    """Build the shared Gemini client (Vertex when a project is set, else AI Studio)."""
    try:
        from google import genai
        if settings.gcp_project_id:
            return genai.Client(
                vertexai=True,
                project=settings.gcp_project_id,
                location="us-central1",
            )
        if settings.gemini_api_key:
            return genai.Client(api_key=settings.gemini_api_key)
        logger.warning("No Gemini config available")
        return None
    except Exception as exc:
        logger.warning("Gemini client init failed: %s", exc)
        return None


def _init_tagging_service(settings: Settings):
    """Build the TaggingService with a Gemini Flash client."""
    client = _build_genai_client(settings)
    if client is None:
        return None
    return TaggingService(genai_client=client, model_id=settings.gemini_model_fast)


def _init_mem0(settings: Settings):
    """Initialise the Mem0 client. Returns None when unavailable."""
    if not settings.mem0_api_key:
        logger.warning("Mem0 API key not configured, episodic memory disabled")
        return None
    try:
        from mem0 import MemoryClient
        return MemoryClient(api_key=settings.mem0_api_key)
    except Exception as exc:
        logger.warning("Mem0 not available: %s", exc)
        return None


def _configure_logging(settings: Settings) -> None:
    """Configure structured logging."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quieten noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
