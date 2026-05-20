"""
FastAPI application — Asistente Personal de Productividad.

Input principal: Telegram bot (texto).

Endpoints:
- POST /api/v1/chat     → Recibe texto, procesa con agente (uso interno)
- GET  /api/v1/health   → Health check
- GET  /api/v1/context  → Contexto actual del usuario

# TODO: Descomentar cuando se active ESP32
# - POST /api/v1/voice  → Recibe audio, transcribe, procesa con agente
# - POST /api/v1/chat/stream → Texto con respuesta SSE streaming
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
from app.services.telegram import TelegramBot

# TODO: Descomentar cuando se active ESP32 + voice
# from app.services.stt import STTService
# from app.services.storage import StorageService
# from app.middleware.auth import verify_request

logger = logging.getLogger(__name__)

# ── Estado global de la app (inicializado en lifespan) ───────────────────────
_notion_service: NotionService | None = None
_memory_manager: MemoryManager | None = None
_agent: ProductivityAgent | None = None
_telegram_bot: TelegramBot | None = None
_proactive_service: ProactiveService | None = None
_insight_service: InsightService | None = None
_planner_agent: PlannerAgent | None = None
_calendar_service: CalendarService | None = None

# TODO: Descomentar cuando se active ESP32
# _stt_service: STTService | None = None
# _storage_service: StorageService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Inicializa y limpia recursos al startup/shutdown."""
    global _notion_service, _memory_manager, _agent, _telegram_bot

    settings = get_settings()
    _configure_logging(settings)

    logger.info("Initializing services...")

    # Notion
    _notion_service = NotionService(
        token=settings.notion_token,
        db_ids={
            "tasks": settings.notion_tasks_db,
            "notes": settings.notion_notes_db,
            "goals": settings.notion_goals_db,
            "daily_agenda": settings.notion_daily_agenda_db,
        },
    )

    # Firestore
    firestore_client = _init_firestore(settings)

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

    # Tagging (Gemini Flash compartido)
    tagging_service = _init_tagging_service(settings)

    # Insights service early (necesario por MemoryManager para inyectar en prompt)
    insights_genai = _build_genai_client(settings)
    insight_service_local = None
    if insights_genai is not None:
        insight_service_local = InsightService(
            firestore_client=firestore_client,
            telegram_bot=None,  # se asigna después de crear el bot
            genai_client=insights_genai,
            collection_prefix=settings.firestore_collection_prefix,
            user_id=settings.user_id,
            experiment_service=experiment_service,
            notion_service=_notion_service,
        )

    # Memory Manager
    _memory_manager = MemoryManager(
        firestore_client=firestore_client,
        mem0_client=mem0_client,
        notion_service=_notion_service,
        collection_prefix=settings.firestore_collection_prefix,
        user_id=settings.user_id,
        principle_service=principle_service,
        tagging_service=tagging_service,
        insight_service=insight_service_local,
    )

    # Calendar + Planner
    global _calendar_service, _planner_agent
    token_json = None
    if settings.gcp_project_id:
        token_json = load_calendar_token_from_secret(settings.gcp_project_id)
    if token_json is None:
        token_json = load_calendar_token_local("token.json")

    if token_json:
        _calendar_service = CalendarService(
            token_json=token_json,
            user_timezone=settings.user_timezone,
        )
        _planner_agent = PlannerAgent(
            calendar_service=_calendar_service,
            notion_service=_notion_service,
            experiment_service=experiment_service,
            memory_manager=_memory_manager,
            gemini_api_key=settings.gemini_api_key,
            gcp_project_id=settings.gcp_project_id,
            user_timezone=settings.user_timezone,
        )
        logger.info("Planner agent initialized")
    else:
        logger.warning("Calendar token no disponible — planner deshabilitado")

    # Agent
    _agent = ProductivityAgent(
        notion_service=_notion_service,
        memory_manager=_memory_manager,
        user_timezone=settings.user_timezone,
        gcp_project_id=settings.gcp_project_id,
        experiment_service=experiment_service,
        planner=_planner_agent,
        calendar_service=_calendar_service,
    )

    # Telegram Bot
    _telegram_bot = TelegramBot(
        token=settings.telegram_bot_token,
        allowed_user_id=settings.telegram_allowed_user_id,
        agent=_agent,
    )
    await _telegram_bot.start()

    # Proactive Service + ligar telegram_bot al insight_service ya creado
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
            collection_prefix=settings.firestore_collection_prefix,
            user_id=settings.user_id,
        )

    # Registrar webhook si la URL pública está configurada
    if settings.telegram_webhook_url and settings.telegram_webhook_secret:
        webhook_url = f"{settings.telegram_webhook_url.rstrip('/')}/api/v1/telegram/webhook"
        await _telegram_bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
        )

    # TODO: Descomentar cuando se active ESP32
    # _stt_service = STTService(api_key=settings.groq_api_key)
    # _storage_service = StorageService(
    #     bucket_name=settings.gcs_bucket_name,
    #     project_id=settings.gcp_project_id,
    # )

    logger.info("All services initialized successfully")
    yield

    # Cleanup
    logger.info("Shutting down services...")
    if _telegram_bot:
        await _telegram_bot.stop()
    if _notion_service:
        await _notion_service.close()
    # TODO: Descomentar cuando se active ESP32
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
    allow_origins=["*"],  # Restringir en producción
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
    """Recibe texto y procesa con el agente (uso interno/debug)."""
    assert _agent is not None

    response = await _agent.process(body.message, source="api")
    return response


@app.post("/api/v1/proactive")
async def proactive_trigger(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Llamado por Cloud Scheduler cada hora. Detecta triggers y manda mensajes."""
    settings = get_settings()

    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid proactive secret")

    if _proactive_service is None:
        return {"ok": False, "reason": "proactive service not initialized"}

    # Hora local del usuario
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
    """Llamado por Cloud Scheduler domingo 8pm. Analiza última semana."""
    settings = get_settings()

    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    if _insight_service is None:
        return {"ok": False, "reason": "insights service not initialized"}

    result = await _insight_service.run_weekly_analysis()
    return result


@app.post("/api/v1/planner/plan-tomorrow")
async def planner_plan_tomorrow(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Llamado por Cloud Scheduler 23pm Lima. Planner genera plan para mañana."""
    settings = get_settings()
    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _planner_agent is None:
        return {"ok": False, "reason": "planner not initialized"}
    result = await _planner_agent.plan_day()
    if _telegram_bot is not None:
        summary = result.get("summary", "")[:1500]
        await _telegram_bot.send_proactive_message(f"📅 *Plan de mañana listo*\n\n{summary}")
    return {"ok": True, **result}


@app.post("/api/v1/planner/verify")
async def planner_verify(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Llamado cada 2h. Verifica cumplimiento de bloques pasados."""
    settings = get_settings()
    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _planner_agent is None:
        return {"ok": False, "reason": "planner not initialized"}
    result = await _planner_agent.verify_recent()
    return {"ok": True, **result}


@app.post("/api/v1/planner/daily-review")
async def planner_daily_review(
    x_proactive_secret: str = Header(default=""),
) -> dict:
    """Llamado por Cloud Scheduler 22pm Lima. Pregunta al usuario qué bloques completó."""
    settings = get_settings()
    if not settings.proactive_secret or x_proactive_secret != settings.proactive_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _telegram_bot is None:
        return {"ok": False, "reason": "telegram not initialized"}

    # Listar bloques del día para incluirlos en la pregunta
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
    """Recibe updates de Telegram vía webhook."""
    assert _telegram_bot is not None
    settings = get_settings()

    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    update_data = await request.json()
    await _telegram_bot.process_update(update_data)
    return {"ok": True}


@app.get("/api/v1/context", response_model=ContextResponse)
async def get_context() -> ContextResponse:
    """Retorna el contexto actual: agenda, tareas de hoy, metas activas."""
    assert _notion_service is not None

    today_str = date.today().isoformat()

    today_tasks = await _notion_service.get_tasks(date=today_str)
    agenda = await _notion_service.get_daily_agenda(today_str)
    goals = await _notion_service.get_goals(status="active")

    return ContextResponse(
        today_tasks=today_tasks,
        agenda=agenda,
        active_goals=goals,
    )


# TODO: Descomentar cuando se active ESP32 + voice
# @app.post("/api/v1/voice", response_model=AgentResponse)
# async def process_voice(
#     request: Request,
#     source: str = Depends(verify_request),
# ) -> AgentResponse:
#     """Recibe audio WAV/WebM, transcribe y procesa con el agente."""
#     assert _stt_service is not None
#     assert _agent is not None
#     assert _storage_service is not None
#
#     content_type = request.headers.get("content-type", "audio/wav")
#     audio_data = await request.body()
#
#     if not audio_data:
#         return AgentResponse(message="No se recibió audio.", source=source)
#
#     import asyncio
#     asyncio.create_task(
#         _storage_service.upload_audio(audio_data, content_type, source)
#     )
#
#     transcription = await _stt_service.transcribe(audio_data, content_type)
#     if not transcription:
#         return AgentResponse(
#             message="No pude entender el audio. ¿Puedes repetir?",
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
#     """Recibe texto y retorna SSE streaming."""
#     assert _agent is not None
#     async def event_generator() -> AsyncGenerator[str, None]:
#         async for chunk in _agent.process_stream(body.message, source=source):
#             yield f"data: {chunk}\n\n"
#     return StreamingResponse(
#         event_generator(),
#         media_type="text/event-stream",
#         headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
#     )


# ── Helpers de inicialización ────────────────────────────────────────────────


def _init_firestore(settings: Settings):
    """Inicializa Firestore client. Retorna None si no está disponible."""
    if not settings.gcp_project_id:
        logger.warning("GCP project not configured, Firestore disabled")
        return None
    try:
        from google.cloud import firestore
        return firestore.Client(project=settings.gcp_project_id)
    except Exception as exc:
        logger.warning("Firestore not available: %s", exc)
        return None


def _build_genai_client(settings: Settings):
    """Crea cliente Gemini compartido (Vertex si hay project, AI Studio si solo api key)."""
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
    """Crea TaggingService con cliente Gemini Flash."""
    client = _build_genai_client(settings)
    if client is None:
        return None
    return TaggingService(genai_client=client, model_id="gemini-2.5-flash")


def _init_mem0(settings: Settings):
    """Inicializa Mem0 client. Retorna None si no está disponible."""
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
    """Configura logging estructurado."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Reducir ruido de librerías externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
