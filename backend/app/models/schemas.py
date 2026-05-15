"""
Modelos Pydantic para requests y responses del API.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Requests ──────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """Mensaje de texto desde la PWA."""

    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = None


# ── Responses ─────────────────────────────────────────────────────────────────


class ActionTaken(BaseModel):
    """Registro de una tool ejecutada por el agente."""

    tool: str
    args: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """Respuesta estándar del agente."""

    message: str
    actions_taken: list[ActionTaken] = Field(default_factory=list)
    source: str = "text"  # "esp32" | "pwa" | "text"
    conversation_id: str = ""


class StreamChunk(BaseModel):
    """Chunk individual para SSE streaming."""

    type: str  # "text" | "action" | "done"
    content: str = ""


class HealthResponse(BaseModel):
    """Respuesta del health check."""

    status: str = "ok"
    version: str = "1.0.0"


class ContextResponse(BaseModel):
    """Contexto actual del usuario (agenda, tareas pendientes)."""

    today_tasks: list[dict] = Field(default_factory=list)
    agenda: dict = Field(default_factory=dict)
    active_goals: list[dict] = Field(default_factory=list)
