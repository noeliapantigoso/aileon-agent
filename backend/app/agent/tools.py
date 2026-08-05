"""
Definiciones de herramientas (tools) para Gemini function calling.

Cada tool tiene un nombre, descripción y schema de parámetros.
Gemini usa estas definiciones para decidir qué herramienta invocar.
"""

from __future__ import annotations

TOOLS = [
    {
        "name": "create_task",
        "description": (
            "Crea una nueva tarea en Notion. Usar cuando el usuario quiere "
            "agregar un pendiente, to-do, o algo que debe hacer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Nombre claro y accionable de la tarea",
                },
                "priority": {
                    "type": "string",
                    "enum": ["p0", "p1", "p2", "p3"],
                    "description": (
                        "p0=urgente+importante, p1=importante, "
                        "p2=normal, p3=bajo"
                    ),
                },
                "due_date": {
                    "type": "string",
                    "description": "Fecha límite en formato YYYY-MM-DD",
                },
                "scheduled_date": {
                    "type": "string",
                    "description": "Fecha en que se planea hacer (YYYY-MM-DD)",
                },
                "time_estimate_minutes": {
                    "type": "integer",
                    "description": "Estimación de tiempo en minutos",
                },
                "project": {
                    "type": "string",
                    "description": "Proyecto al que pertenece",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Etiquetas para clasificar la tarea",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_tasks",
        "description": (
            "Consulta tareas de Notion. Usar para ver pendientes, "
            "agenda, o buscar tareas específicas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "waiting", "done", "all"],
                    "description": "Filtrar por estado",
                },
                "date": {
                    "type": "string",
                    "description": "Filtrar por fecha programada (YYYY-MM-DD)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["p0", "p1", "p2", "p3"],
                    "description": "Filtrar por prioridad",
                },
                "project": {
                    "type": "string",
                    "description": "Filtrar por proyecto",
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de tareas a retornar (default 20)",
                },
            },
        },
    },
    {
        "name": "update_task",
        "description": (
            "Actualiza una tarea existente. Usar para marcar como completada, "
            "cambiar prioridad, re-programar, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID de la página de Notion",
                },
                "status": {
                    "type": "string",
                    "enum": ["inbox", "next", "in_progress", "waiting", "done"],
                    "description": "Nuevo estado de la tarea",
                },
                "priority": {
                    "type": "string",
                    "enum": ["p0", "p1", "p2", "p3"],
                    "description": "Nueva prioridad",
                },
                "due_date": {
                    "type": "string",
                    "description": "Nueva fecha límite (YYYY-MM-DD)",
                },
                "scheduled_date": {
                    "type": "string",
                    "description": "Nueva fecha programada (YYYY-MM-DD)",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "save_note",
        "description": (
            "Guarda una nota, idea, pensamiento o información general en Notion. "
            "Usar cuando el usuario quiere capturar algo que no es una tarea."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Contenido de la nota",
                },
                "title": {
                    "type": "string",
                    "description": "Título corto. Si no se da, generar del contenido.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Etiquetas para clasificar la nota",
                },
                "source": {
                    "type": "string",
                    "enum": ["voice", "text", "meeting", "idea"],
                    "description": "Origen de la nota",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "search_notes",
        "description": (
            "Busca en las notas guardadas. Usar cuando el usuario pregunta "
            "por algo que dijo o guardó antes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Texto a buscar",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_daily_agenda",
        "description": (
            "Obtiene la agenda y plan del día. Incluye tareas programadas, "
            "eventos y prioridades."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Fecha en YYYY-MM-DD. Default: hoy.",
                },
            },
        },
    },
    {
        "name": "create_goal",
        "description": (
            "Crea una meta nueva en Notion. Usar cuando el usuario quiere agregar "
            "una meta de corto/mediano/largo plazo. Pregunta área y target_date si faltan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Nombre claro de la meta",
                },
                "goal_type": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term"],
                    "description": "short=1-3meses, medium=3-12meses, long=1+años",
                },
                "area": {
                    "type": "string",
                    "enum": ["work", "personal", "health", "finance", "learning", "relationships"],
                    "description": "Área de vida a la que pertenece",
                },
                "target_date": {
                    "type": "string",
                    "description": "Fecha objetivo YYYY-MM-DD",
                },
                "key_results": {
                    "type": "string",
                    "description": "KRs medibles que indican que la meta se cumplió",
                },
                "initial_progress": {
                    "type": "integer",
                    "description": "Progreso inicial 0-100, default 0",
                },
            },
            "required": ["title", "goal_type"],
        },
    },
    {
        "name": "get_goals",
        "description": "Consulta las metas activas del usuario con su progreso.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term", "all"],
                    "description": "Filtrar por tipo de meta",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "paused", "abandoned", "all"],
                    "description": "Filtrar por estado",
                },
            },
        },
    },
    {
        "name": "update_goal",
        "description": (
            "Edita campos de una meta (título, tipo, área, fecha objetivo, KRs, status). "
            "NO usar para reportar progreso — usar update_goal_progress. "
            "Si el usuario no dio el goal_id, primero llamá get_goals para encontrarlo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "ID de la meta en Notion"},
                "title": {"type": "string", "description": "Nuevo título"},
                "goal_type": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term"],
                },
                "area": {
                    "type": "string",
                    "enum": ["work", "personal", "health", "finance", "learning", "relationships"],
                },
                "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                "key_results": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "paused", "abandoned"],
                },
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "archive_goal",
        "description": (
            "Archiva (soft-delete) una meta. Usar cuando el usuario quiere "
            "borrar o eliminar una meta. CONFIRMAR con el usuario antes de archivar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "ID de la meta en Notion"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "update_goal_progress",
        "description": (
            "Actualiza SOLO el progreso (%) y agrega nota de avance. "
            "Para cambiar título/fecha/área usar update_goal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "ID de la meta en Notion",
                },
                "progress_note": {
                    "type": "string",
                    "description": "Descripción del avance",
                },
                "new_percentage": {
                    "type": "integer",
                    "description": "Nuevo porcentaje de progreso (0-100)",
                },
            },
            "required": ["goal_id", "progress_note"],
        },
    },
    {
        "name": "start_experiment",
        "description": (
            "Inicia un experimento personal: algo que el usuario quiere probar "
            "y trackear por unos días. Ej: 'voy a vacuumear 5min diarios por una semana'. "
            "El bot hará check-ins automáticos según check_in_every_days."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nombre corto y claro del experimento",
                },
                "hypothesis": {
                    "type": "string",
                    "description": "Qué espera lograr/descubrir el usuario",
                },
                "duration_days": {
                    "type": "integer",
                    "description": "Duración total en días (default 7)",
                },
                "check_in_every_days": {
                    "type": "integer",
                    "description": "Cada cuántos días el bot pregunta cómo va (default 3)",
                },
            },
            "required": ["name", "hypothesis"],
        },
    },
    {
        "name": "log_experiment_progress",
        "description": (
            "Registra una entrada en el historial de un experimento activo. "
            "Usar cuando el usuario reporta cómo le fue."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "ID del experimento",
                },
                "note": {
                    "type": "string",
                    "description": "Nota del usuario sobre cómo le fue",
                },
                "did_it": {
                    "type": "boolean",
                    "description": "Si cumplió o no en este check-in",
                },
            },
            "required": ["experiment_id", "note"],
        },
    },
    {
        "name": "close_experiment",
        "description": (
            "Cierra un experimento. Usar cuando el usuario lo da por terminado "
            "(completado, abandonado o pivotado)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "ID del experimento",
                },
                "outcome": {
                    "type": "string",
                    "description": "Reflexión final / qué aprendió",
                },
                "status": {
                    "type": "string",
                    "enum": ["completed", "abandoned", "pivoted"],
                    "description": "Cómo termina",
                },
            },
            "required": ["experiment_id", "outcome"],
        },
    },
    {
        "name": "list_active_experiments",
        "description": "Lista los experimentos que están activos actualmente.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_calendar_events",
        "description": (
            "Lista eventos del Google Calendar del usuario en un rango. "
            "Usar para responder consultas tipo 'qué tengo mañana', 'qué tengo esta semana', "
            "'a qué hora es mi reunión'. Read-only — no modifica nada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string", "description": "ISO datetime inicio (opcional, default ahora)"},
                "end_iso": {"type": "string", "description": "ISO datetime fin (opcional)"},
                "days": {"type": "integer", "description": "Si no hay start/end, días desde ahora. Default 1."},
            },
        },
    },
    {
        "name": "delegate_to_planner",
        "description": (
            "Delega al subagente PLANNER especializado en planificación de calendario. "
            "Usar cuando el usuario quiere: organizar su día/semana, planificar tiempos para metas, "
            "verificar cumplimiento de bloques, hacer review del día, O crear/mover/borrar bloques "
            "puntuales en Calendar (el planner valida coherencia con metas/peak hours antes de aplicar). "
            "NO usar para crear una sola tarea Notion — usar create_task. "
            "El planner devuelve un resumen del plan/review/edit que debes presentar al usuario."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["plan_day", "verify_recent", "daily_review", "edit_request"],
                    "description": (
                        "plan_day=crear plan para mañana (o fecha dada). "
                        "verify_recent=revisar cumplimiento de bloques pasados. "
                        "daily_review=cierre del día. "
                        "edit_request=aplicar un cambio puntual al Calendar (crear/mover/borrar bloque) "
                        "validando contra metas, peak hours y conflictos."
                    ),
                },
                "target_date": {
                    "type": "string",
                    "description": "Para plan_day: fecha objetivo YYYY-MM-DD. Default mañana.",
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "Para edit_request: texto natural del usuario describiendo el cambio. "
                        "Para daily_review: respuesta del usuario indicando qué bloques completó "
                        "(pasar el mensaje del usuario tal cual). "
                        "Ejemplos edit_request: 'agéndame 30min de gym mañana 7pm', "
                        "'mueve mi bloque de lectura de las 7pm a las 10pm'. "
                        "Pásalo tal cual te lo dio el usuario."
                    ),
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "get_user_profile",
        "description": (
            "Obtiene el perfil completo del usuario (metas, preferencias, "
            "rutinas). Usar solo cuando se necesita información que no está "
            "en el contexto actual."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Explicitly save a fact or preference about the user to long-term memory. "
            "Use when the user says 'remember that...', 'keep in mind that...', or shares "
            "something personal they want the assistant to always know."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact to remember, written as a clear statement (e.g. 'Noe prefers short responses when she is stressed')",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "name": "list_memories",
        "description": (
            "List everything stored in long-term memory about the user. "
            "Use when the user asks 'what do you know about me?', 'what do you remember?', or similar."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "forget_memory",
        "description": (
            "Delete a specific memory. Use when the user says 'forget that...', "
            "'that's no longer true', or wants to remove something you remember. "
            "First call list_memories to get the memory ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The ID of the memory to delete (from list_memories)",
                },
                "memory_text": {
                    "type": "string",
                    "description": "The text of the memory being deleted (for confirmation message to user)",
                },
            },
            "required": ["memory_id", "memory_text"],
        },
    },
    {
        "name": "update_my_profile",
        "description": (
            "Update the user's profile — name, work schedule, occupation, preferences, etc. "
            "Use when the user says something like 'my schedule changed', 'I now work at...', "
            "'update my profile', or corrects a fact about themselves."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": (
                        "Which part of the profile to update. Examples: "
                        "'name', 'occupation', 'company', "
                        "'productivity.work_start', 'productivity.work_end', "
                        "'preferences.communication_style'"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "The new value for that field",
                },
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "save_skill",
        "description": (
            "Creates or updates a SKILL.md file — a reusable guide that improves "
            "how you handle a recurring situation. Use this when you notice a pattern "
            "the user repeats, learn a new rule or preference that should stick "
            "permanently, or when the user asks you to remember a procedure. "
            "The skill will be loaded automatically in future conversations when relevant. "
            "IMPORTANT: after calling this tool, always tell the user what skill you created "
            "or updated and why, in one short line. Example: "
            "'📎 Saved skill: handle-interruptions — I'll remember to only move the last block when something comes up.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short slug for the skill (e.g. 'handle-interruptions', "
                        "'meeting-notes'). Use existing skill names to update them."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Full SKILL.md content. Must include YAML frontmatter with "
                        "name, description, and triggers fields, followed by markdown "
                        "instructions. Example frontmatter:\n"
                        "---\n"
                        "name: handle-interruptions\n"
                        "description: How to handle unexpected interruptions during a planned day\n"
                        "triggers: [interrupted, distraction, something came up, surgió algo]\n"
                        "---"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're creating or updating this skill — what pattern triggered it.",
                },
            },
            "required": ["name", "content", "reason"],
        },
    },
    {
        "name": "delete_skill",
        "description": (
            "Permanently deletes a custom skill. Use when the user explicitly asks to "
            "remove a skill, or when a skill is outdated and shouldn't guide future "
            "conversations. Built-in skills (plan-day, daily-review, etc.) cannot be "
            "deleted — use save_skill to override them instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name or slug of the skill to delete (e.g. 'handle-interruptions')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "schedule_task",
        "description": (
            "Schedule a one-time task to run at a specific future datetime. "
            "Use when the user says things like 'remind me in 3 weeks', "
            "'check on this goal next month', 'follow up on X on September 15th'. "
            "The task fires once at the specified time and sends its prompt to the agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short label for the task (shown in the scheduled tasks list)",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "What to do when the task fires — written as a clear instruction. "
                        "Example: 'Check in on the journaling experiment: has the user kept it up for 30 days?'"
                    ),
                },
                "run_at": {
                    "type": "string",
                    "description": (
                        "ISO datetime with Lima offset when the task should run. "
                        "Example: '2026-09-15T09:00:00-05:00'"
                    ),
                },
            },
            "required": ["title", "prompt", "run_at"],
        },
    },
    {
        "name": "cancel_scheduled_task",
        "description": (
            "Cancel a one-time scheduled task so it won't run. "
            "Use when the user says 'cancel that reminder', 'don't check on X anymore', "
            "or when a task is no longer relevant. Call list_scheduled_tasks first to get the task_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID from list_scheduled_tasks",
                },
                "task_title": {
                    "type": "string",
                    "description": "The task title (for confirmation message to user)",
                },
            },
            "required": ["task_id", "task_title"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "description": (
            "List all pending one-time scheduled tasks. "
            "Call this when the user asks 'what do I have scheduled?', "
            "'what reminders do I have?', or before cancelling a task."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def get_gemini_tool_declarations() -> list[dict]:
    """
    Convierte las definiciones de tools al formato que espera
    el SDK de google-genai para function calling.
    """
    declarations = []
    for tool in TOOLS:
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        })
    return declarations
