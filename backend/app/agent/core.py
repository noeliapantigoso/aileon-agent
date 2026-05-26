"""
Agente principal de productividad.

Implementa el patrón LLM-as-Router con function calling de Gemini.
El LLM decide qué herramientas usar basándose en las tool definitions,
el contexto del usuario y el mensaje recibido.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator

from google import genai
from google.genai import types

from app.agent.prompt_builder import build_system_prompt
from app.agent.tool_executor import ToolExecutor
from app.agent.tools import TOOLS
from app.models.schemas import ActionTaken, AgentResponse
from app.services.memory import MemoryManager
from app.services.notion import NotionService

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
MODEL_ID_VERTEX = "gemini-2.5-flash"
MODEL_ID_AISTUDIO = "gemini-2.5-flash"


class ProductivityAgent:
    """
    Agente de productividad con function calling.

    Flujo:
    1. Cargar contexto (perfil, agenda, memorias)
    2. Ensamblar system prompt dinámico
    3. Llamar a Gemini con tool definitions
    4. Loop de tool calling hasta respuesta final
    5. Guardar interacción en memoria
    """

    def __init__(
        self,
        notion_service: NotionService,
        memory_manager: MemoryManager,
        user_timezone: str = "America/Lima",
        gemini_api_key: str = "",
        gcp_project_id: str = "",
        experiment_service: Any = None,
        planner: Any = None,
        calendar_service: Any = None,
    ) -> None:
        if gcp_project_id:
            self._client = genai.Client(
                vertexai=True,
                project=gcp_project_id,
                location="us-central1",
            )
            self._model_id = MODEL_ID_VERTEX
        else:
            self._client = genai.Client(api_key=gemini_api_key)
            self._model_id = MODEL_ID_AISTUDIO
        self._notion = notion_service
        self._memory = memory_manager
        self._timezone = user_timezone
        self._experiments = experiment_service
        self._planner = planner
        self._calendar = calendar_service

    async def process(
        self,
        user_message: str,
        source: str = "text",
    ) -> AgentResponse:
        """
        Procesa un mensaje del usuario y retorna la respuesta completa.

        Args:
            user_message: Texto del usuario (ya transcrito si era audio).
            source: Origen del mensaje ("esp32", "pwa", "text").

        Returns:
            AgentResponse con mensaje y acciones ejecutadas.
        """
        # 1. Cargar contexto
        context = await self._memory.get_full_context(user_message)

        # 2. Construir system prompt
        now = _get_current_datetime(self._timezone)
        system_prompt = build_system_prompt(
            user_profile=context["user_profile"],
            today_context=context["today_context"],
            relevant_memories=context["relevant_memories"],
            current_datetime=now,
            relevant_principles=context.get("relevant_principles", []),
            active_insights=context.get("active_insights", []),
        )

        # 3. Preparar historial de conversación
        contents = _build_contents(
            context["conversation_history"],
            user_message,
        )

        # 4. Preparar tool declarations para Gemini
        gemini_tools = [types.Tool(function_declarations=TOOLS)]

        # 5. Loop de function calling
        tool_executor = ToolExecutor(
            notion=self._notion,
            memory=self._memory,
            source=source,
            experiments=self._experiments,
            planner=self._planner,
            calendar=self._calendar,
        )
        actions_taken: list[ActionTaken] = []

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.debug("Agent iteration %d/%d", iteration + 1, MAX_TOOL_ITERATIONS)

            response = self._client.models.generate_content(
                model=self._model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=gemini_tools,
                    temperature=0.7,
                    max_output_tokens=2048,
                ),
            )

            # Verificar si Gemini quiere llamar a tools
            candidate = response.candidates[0]
            parts = candidate.content.parts

            has_function_calls = any(
                part.function_call is not None for part in parts
            )

            if not has_function_calls:
                # Gemini retornó texto final
                final_text = _extract_text(parts)
                break

            # Ejecutar cada function call
            function_responses = []
            for part in parts:
                if part.function_call is not None:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info("Tool call: %s(%s)", tool_name, tool_args)
                    result = await tool_executor.execute(tool_name, tool_args)

                    actions_taken.append(ActionTaken(
                        tool=tool_name,
                        args=tool_args,
                        result=result,
                    ))

                    function_responses.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response=result,
                        )
                    )

            # Agregar la respuesta del modelo y los resultados de tools al historial
            contents.append(candidate.content)
            contents.append(types.Content(
                role="user",
                parts=function_responses,
            ))
        else:
            # Safety net: se alcanzó el máximo de iteraciones
            logger.warning("Max tool iterations reached (%d)", MAX_TOOL_ITERATIONS)
            final_text = (
                "He ejecutado varias acciones pero alcancé el límite de operaciones. "
                "¿Necesitas que continúe con algo más?"
            )

        # 6. Guardar interacción en memoria (async, no bloquea)
        actions_serialized = [
            {"tool": a.tool, "args": a.args, "result": a.result}
            for a in actions_taken
        ]
        await self._memory.save_interaction(
            user_message, final_text, actions_taken=actions_serialized
        )

        return AgentResponse(
            message=final_text,
            actions_taken=actions_taken,
            source=source,
        )

    async def process_stream(
        self,
        user_message: str,
        source: str = "text",
    ) -> AsyncGenerator[str, None]:
        """
        Versión streaming para SSE.

        Yields chunks de texto y notificaciones de acciones.
        """
        # Cargar contexto
        context = await self._memory.get_full_context(user_message)
        now = _get_current_datetime(self._timezone)
        system_prompt = build_system_prompt(
            user_profile=context["user_profile"],
            today_context=context["today_context"],
            relevant_memories=context["relevant_memories"],
            current_datetime=now,
            relevant_principles=context.get("relevant_principles", []),
            active_insights=context.get("active_insights", []),
        )

        contents = _build_contents(
            context["conversation_history"],
            user_message,
        )
        gemini_tools = [types.Tool(function_declarations=TOOLS)]
        tool_executor = ToolExecutor(
            notion=self._notion,
            memory=self._memory,
            source=source,
            experiments=self._experiments,
            planner=self._planner,
            calendar=self._calendar,
        )

        full_response = ""

        for iteration in range(MAX_TOOL_ITERATIONS):
            response = self._client.models.generate_content_stream(
                model=self._model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=gemini_tools,
                    temperature=0.7,
                    max_output_tokens=2048,
                ),
            )

            collected_parts: list[types.Part] = []
            for chunk in response:
                if not chunk.candidates:
                    continue
                for part in chunk.candidates[0].content.parts:
                    collected_parts.append(part)
                    if part.text:
                        full_response += part.text
                        yield json.dumps({"type": "text", "content": part.text})

            # Check for function calls in collected parts
            function_calls = [
                p for p in collected_parts if p.function_call is not None
            ]
            if not function_calls:
                break

            # Execute function calls
            function_responses = []
            for part in function_calls:
                fc = part.function_call
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                yield json.dumps({
                    "type": "action",
                    "content": f"Ejecutando: {tool_name}",
                })

                result = await tool_executor.execute(tool_name, tool_args)
                function_responses.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response=result,
                    )
                )

            # Add to conversation for next iteration
            model_content = types.Content(
                role="model",
                parts=collected_parts,
            )
            contents.append(model_content)
            contents.append(types.Content(
                role="user",
                parts=function_responses,
            ))

        # Save interaction
        await self._memory.save_interaction(user_message, full_response)
        yield json.dumps({"type": "done", "content": ""})


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_contents(
    conversation_history: list[dict[str, Any]],
    user_message: str,
) -> list[types.Content]:
    """Construye el array de contents para Gemini."""
    from datetime import datetime, timezone, timedelta

    contents: list[types.Content] = []
    prev_ts: datetime | None = None

    for i, msg in enumerate(conversation_history):
        role = "user" if msg["role"] == "user" else "model"
        text = msg.get("content", "")
        is_proactive = msg.get("proactive", False)
        ts_str = msg.get("timestamp", "")

        # Parsear timestamp del mensaje
        curr_ts: datetime | None = None
        if ts_str:
            try:
                curr_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                pass

        # Inyectar marcador de tiempo si hay gap significativo
        if curr_ts and prev_ts:
            delta = curr_ts - prev_ts
            if delta >= timedelta(hours=1):
                hours = int(delta.total_seconds() // 3600)
                days = delta.days
                if days >= 1:
                    gap_note = f"[{days} día(s) sin actividad]"
                else:
                    gap_note = f"[{hours} hora(s) sin actividad]"
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=gap_note)],
                ))
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="Entendido.")],
                ))

        # Marcar mensajes proactivos que no tuvieron respuesta del usuario
        if role == "model" and is_proactive:
            # Verificar si el siguiente mensaje en el buffer es una respuesta del usuario
            next_msg = conversation_history[i + 1] if i + 1 < len(conversation_history) else None
            if next_msg is None or next_msg.get("proactive") or next_msg.get("role") == "assistant":
                text = f"[Mensaje enviado proactivamente, sin respuesta del usuario]\n{text}"

        # Si el mensaje asistente tuvo tool calls, anexar info al texto
        actions = msg.get("actions") or []
        if role == "model" and actions:
            tool_lines = []
            for a in actions:
                tool_name = a.get("tool", "")
                args = json.dumps(a.get("args", {}), ensure_ascii=False)
                result = json.dumps(a.get("result", {}), ensure_ascii=False)
                if len(result) > 400:
                    result = result[:400] + "...}"
                tool_lines.append(f"  - {tool_name}({args}) → {result}")
            if tool_lines:
                text = f"{text}\n\n[tools_usadas:\n" + "\n".join(tool_lines) + "\n]"

        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=text)],
        ))

        if curr_ts:
            prev_ts = curr_ts

    # Inyectar gap entre el último mensaje del historial y el mensaje actual
    now = datetime.now(timezone.utc)
    if prev_ts:
        delta = now - prev_ts
        if delta >= timedelta(hours=1):
            days = delta.days
            hours = int(delta.total_seconds() // 3600)
            if days >= 1:
                gap_note = f"[{days} día(s) desde el último mensaje]"
            else:
                gap_note = f"[{hours} hora(s) desde el último mensaje]"
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text=gap_note)],
            ))
            contents.append(types.Content(
                role="model",
                parts=[types.Part.from_text(text="Entendido.")],
            ))

    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    ))

    return contents


def _extract_text(parts: list[types.Part]) -> str:
    """Extrae texto de las partes de una respuesta de Gemini."""
    texts = []
    for part in parts:
        if part.text:
            texts.append(part.text)
    return "".join(texts)


def _get_current_datetime(timezone_str: str) -> datetime:
    """Obtiene la fecha/hora actual en la zona horaria del usuario."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone_str))
    except Exception:
        from datetime import timezone, timedelta
        # Fallback: Lima es UTC-5
        return datetime.now(timezone(timedelta(hours=-5)))
