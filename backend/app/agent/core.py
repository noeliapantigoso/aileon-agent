"""
Main productivity agent.

Implements the LLM-as-router pattern with Gemini function calling. The model
decides which tools to use based on the tool declarations, the user context and
the incoming message.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator

from google import genai
from google.genai import types

from app.agent.prompt_builder import build_system_prompt
from app.agent.skill_manager import SkillManager, format_skills_for_prompt
from app.agent.tool_executor import ToolExecutor
from app.agent.tools import get_tools
from app.models.schemas import ActionTaken, AgentResponse
from app.services.memory import MemoryManager
from app.services.notion import NotionService

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8

# Fallback only. The real value comes from settings.gemini_model, passed in by
# main.py, so the model can be changed with an env var and no code edit.
DEFAULT_MODEL_ID = "gemini-2.5-pro"

# Used when Gemini returns a response with no content. In the proactive cycle this
# text is never sent to anyone: it only becomes the silence reason in the log.
_EMPTY_RESPONSE_FALLBACK = "No pude generar una respuesta en este intento."

# Vertex returns 429 RESOURCE_EXHAUSTED when the model quota saturates. It is
# transient: without a retry, the first 429 reaches the user as
# "there was an error processing your message".
_RETRY_DELAYS_SECONDS = (2, 5, 12)


def _is_rate_limit(exc: Exception) -> bool:
    texto = str(exc)
    return "429" in texto or "RESOURCE_EXHAUSTED" in texto


async def generate_with_retry(client, **kwargs):
    """Call Gemini, retrying with increasing backoff on rate limits."""
    import asyncio

    for espera in (*_RETRY_DELAYS_SECONDS, None):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            if not _is_rate_limit(exc) or espera is None:
                raise
            logger.warning("Vertex rate limit, reintentando en %ds", espera)
            await asyncio.sleep(espera)


class ProductivityAgent:
    """
    Productivity agent with function calling.

    Flow:
    1. Load context (profile, agenda, memories)
    2. Assemble the dynamic system prompt
    3. Call Gemini with the tool declarations
    4. Loop over tool calls until a final answer
    5. Save the interaction to memory
    """

    def __init__(
        self,
        notion_service: NotionService | None,
        memory_manager: MemoryManager,
        user_timezone: str = "America/Lima",
        gemini_api_key: str = "",
        gcp_project_id: str = "",
        experiment_service: Any = None,
        planner: Any = None,
        calendar_service: Any = None,
        agent_queue: Any = None,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> None:
        if gcp_project_id:
            self._client = genai.Client(
                vertexai=True,
                project=gcp_project_id,
                location="us-central1",
            )
        else:
            self._client = genai.Client(api_key=gemini_api_key)
        self._model_id = model_id
        self._notion = notion_service
        self._memory = memory_manager
        self._timezone = user_timezone
        self._experiments = experiment_service
        self._planner = planner
        self._calendar = calendar_service
        self._agent_queue = agent_queue
        # The tool list follows from the services that were actually handed in.
        # An integration that was not configured contributes no tools, so the
        # model is never shown a capability this deployment does not have.
        self._integrations: set[str] = set()
        if notion_service is not None:
            self._integrations.add("notion")
        if calendar_service is not None:
            self._integrations.add("calendar")
        if planner is not None:
            self._integrations.add("planner")
        self._tools = get_tools(self._integrations)
        logger.info(
            "Agent tools: %d available (integrations: %s)",
            len(self._tools),
            ", ".join(sorted(self._integrations)) or "none",
        )
        # Assigned by main.py after the bot is built (circular dependency:
        # TelegramBot takes the agent in its constructor).
        self.telegram: Any = None
        self._skills = SkillManager(
            firestore_client=memory_manager.db,
            collection_prefix=memory_manager._prefix,
        )
        self._skills.load_all()

    async def process(
        self,
        user_message: str,
        source: str = "text",
        save_history: bool = True,
        extra_tools: list[dict] | None = None,
    ) -> AgentResponse:
        """
        Process a user message and return the full response.

        Args:
            user_message: The user's text (already transcribed if it was audio).
            source: Where the message came from ("text", "api", "proactive").
            save_history: When False, the interaction is not stored in history.
                Used by proactive wake-ups, where the prompt is internal and the
                outgoing message is already recorded when it is sent.
            extra_tools: Extra tool declarations for this call only
                (e.g. send_message_to_user in the proactive cycle).

        Returns:
            An AgentResponse with the message and the actions taken.
        """
        # 1. Load context
        context = await self._memory.get_full_context(user_message)

        # 2. Build the system prompt
        now = _get_current_datetime(self._timezone)
        relevant_skills = self._skills.get_relevant(user_message)
        active_experiments = self._experiments.list_active() if self._experiments else []
        system_prompt = build_system_prompt(
            user_profile=context["user_profile"],
            today_context=context["today_context"],
            relevant_memories=context["relevant_memories"],
            current_datetime=now,
            relevant_principles=context.get("relevant_principles", []),
            active_insights=context.get("active_insights", []),
            active_skills=format_skills_for_prompt(relevant_skills),
            active_experiments=active_experiments,
            integrations=self._integrations,
        )

        # 3. Prepare the conversation history
        contents = _build_contents(
            context["conversation_history"],
            user_message,
            timezone_str=self._timezone,
        )

        # 4. Prepare the tool declarations
        gemini_tools = [
            types.Tool(function_declarations=self._tools + list(extra_tools or []))
        ]

        # 5. Function-calling loop
        tool_executor = ToolExecutor(
            notion=self._notion,
            memory=self._memory,
            source=source,
            experiments=self._experiments,
            planner=self._planner,
            calendar=self._calendar,
            skill_manager=self._skills,
            agent_queue=self._agent_queue,
            telegram=self.telegram,
        )
        actions_taken: list[ActionTaken] = []

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.debug("Agent iteration %d/%d", iteration + 1, MAX_TOOL_ITERATIONS)

            response = await generate_with_retry(
                self._client,
                model=self._model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=gemini_tools,
                    temperature=0.7,
                    max_output_tokens=2048,
                ),
            )

            # Gemini can return a candidate with no content (safety filter,
            # max_output_tokens cutoff, empty turn). In that case .parts is None
            # and the loop must break instead of trying to iterate it.
            candidates = response.candidates or []
            if not candidates:
                logger.warning("Gemini returned 0 candidates (iteration %d)", iteration + 1)
                final_text = _EMPTY_RESPONSE_FALLBACK
                break

            candidate = candidates[0]
            parts = (candidate.content.parts if candidate.content else None) or []

            if not parts:
                logger.warning(
                    "Gemini returned empty parts (iteration %d, finish_reason=%s)",
                    iteration + 1,
                    getattr(candidate, "finish_reason", None),
                )
                final_text = _EMPTY_RESPONSE_FALLBACK
                break

            has_function_calls = any(
                part.function_call is not None for part in parts
            )

            if not has_function_calls:
                # Gemini returned its final text
                final_text = _extract_text(parts)
                break

            # Execute each function call
            function_responses = []
            for part in parts:
                if part.function_call is not None:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info("Tool call: %s(%s)", tool_name, tool_args)
                    result = await tool_executor.execute(tool_name, tool_args)

                    # Reload skills cache if a skill was saved or deleted
                    if tool_name in ("save_skill", "delete_skill"):
                        self._skills.invalidate_cache()

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

            # Append the model turn and the tool results to the conversation
            contents.append(candidate.content)
            contents.append(types.Content(
                role="user",
                parts=function_responses,
            ))
        else:
            # Safety net: the iteration cap was reached
            logger.warning("Max tool iterations reached (%d)", MAX_TOOL_ITERATIONS)
            final_text = (
                "I ran several actions but hit the operation limit. "
                "Do you want me to keep going?"
            )

        # The model sometimes runs its tools and then returns no text at all.
        # The caller ends up with an empty reply — in Telegram that showed up as a
        # message containing only the tool-name footer and nothing else. It has
        # every tool result in context here, so one more call with no tools
        # available forces it to answer in words.
        if not (final_text or "").strip() and actions_taken:
            logger.warning(
                "Empty final text after %d action(s); asking for a written answer",
                len(actions_taken),
            )
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(
                    text="You ran your tools but did not answer. Reply to the user "
                         "now, in their language, saying what you did and what it "
                         "means for them. Do not call any more tools."
                )],
            ))
            try:
                retry = await generate_with_retry(
                    self._client,
                    model=self._model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.7,
                        max_output_tokens=1024,
                    ),
                )
                cands = retry.candidates or []
                parts = (cands[0].content.parts if cands and cands[0].content else None) or []
                final_text = _extract_text(parts) or final_text
            except Exception as exc:
                logger.error("Retry for a written answer failed: %s", exc)

        # 6. Save the interaction to memory (async, non-blocking)
        if save_history:
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
        Streaming variant for SSE.

        Yields text chunks and action notifications.
        """
        # Load context
        context = await self._memory.get_full_context(user_message)
        now = _get_current_datetime(self._timezone)
        relevant_skills = self._skills.get_relevant(user_message)
        active_experiments = self._experiments.list_active() if self._experiments else []
        system_prompt = build_system_prompt(
            user_profile=context["user_profile"],
            today_context=context["today_context"],
            relevant_memories=context["relevant_memories"],
            current_datetime=now,
            relevant_principles=context.get("relevant_principles", []),
            active_insights=context.get("active_insights", []),
            active_skills=format_skills_for_prompt(relevant_skills),
            active_experiments=active_experiments,
            integrations=self._integrations,
        )

        contents = _build_contents(
            context["conversation_history"],
            user_message,
            timezone_str=self._timezone,
        )
        gemini_tools = [types.Tool(function_declarations=self._tools)]
        tool_executor = ToolExecutor(
            notion=self._notion,
            memory=self._memory,
            source=source,
            experiments=self._experiments,
            planner=self._planner,
            calendar=self._calendar,
            skill_manager=self._skills,
            agent_queue=self._agent_queue,
            telegram=self.telegram,
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
                chunk_content = chunk.candidates[0].content
                for part in (chunk_content.parts if chunk_content else None) or []:
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


def _local_stamp(ts: datetime | None, now_local: datetime, tz) -> str:
    """
    Short local timestamp used to prefix a history message.

    Without it the model does not know when each thing happened: it only saw
    relative gaps and had to infer the rest. That produced errors such as reading
    a 9am "just woke up" as if it had been said at 11.
    """
    if ts is None:
        return ""
    try:
        local = ts.astimezone(tz)
    except Exception:
        return ""
    if local.date() == now_local.date():
        return f"[{local.strftime('%H:%M')}]"
    if (now_local.date() - local.date()).days == 1:
        return f"[ayer {local.strftime('%H:%M')}]"
    return f"[{local.strftime('%d/%m %H:%M')}]"


def _build_contents(
    conversation_history: list[dict[str, Any]],
    user_message: str,
    timezone_str: str = "America/Lima",
) -> list[types.Content]:
    """Build the contents array for Gemini."""
    from datetime import datetime, timezone, timedelta

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = timezone(timedelta(hours=-5))
    now_local = datetime.now(tz)

    contents: list[types.Content] = []
    prev_ts: datetime | None = None

    for i, msg in enumerate(conversation_history):
        role = "user" if msg["role"] == "user" else "model"
        text = msg.get("content", "")
        is_proactive = msg.get("proactive", False)
        ts_str = msg.get("timestamp", "")

        # Parse the message timestamp
        curr_ts: datetime | None = None
        if ts_str:
            try:
                curr_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                pass

        # Every message carries its local time, so hour-level gaps no longer need
        # their own marker — they can be read off the timestamps. Only whole-day
        # gaps are announced, since those do change the meaning of the exchange.
        if curr_ts and prev_ts:
            delta = curr_ts - prev_ts
            if delta.days >= 1:
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(
                        text=f"[{delta.days} day(s) with no activity]"
                    )],
                ))
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="Entendido.")],
                ))

        stamp = _local_stamp(curr_ts, now_local, tz)
        if stamp:
            text = f"{stamp} {text}"

        # Flag proactive messages that got no reply
        if role == "model" and is_proactive:
            # Check whether the next buffered message is a user reply
            next_msg = conversation_history[i + 1] if i + 1 < len(conversation_history) else None
            if next_msg is None or next_msg.get("proactive") or next_msg.get("role") == "assistant":
                text = f"[Sent proactively, the user never replied]\n{text}"

        # If the assistant turn used tools, append a compact trace to the text
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

    # The current message carries its time too, so the model knows how long it has
    # been since the last one without doing arithmetic on timestamps.
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(
            text=f"[{now_local.strftime('%H:%M')}] {user_message}"
        )],
    ))

    return contents


def _extract_text(parts: list[types.Part]) -> str:
    """Extract the text out of a Gemini response's parts."""
    texts = []
    for part in parts:
        if part.text:
            texts.append(part.text)
    return "".join(texts)


def _get_current_datetime(timezone_str: str) -> datetime:
    """Current date and time in the user's timezone."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone_str))
    except Exception:
        from datetime import timezone, timedelta
        # Fallback offset when zoneinfo is unavailable
        return datetime.now(timezone(timedelta(hours=-5)))
