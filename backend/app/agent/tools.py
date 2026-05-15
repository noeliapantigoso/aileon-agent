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
                    "enum": ["pending", "in_progress", "done", "all"],
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
        "name": "organize_day",
        "description": (
            "Genera un plan organizado para un día específico. Consulta tareas "
            "pendientes, metas activas y eventos, luego crea un plan con time "
            "blocks y lo guarda en Notion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Fecha a organizar (YYYY-MM-DD). Default: mañana.",
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Áreas en las que el usuario quiere enfocarse",
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
                    "enum": ["active", "completed", "all"],
                    "description": "Filtrar por estado",
                },
            },
        },
    },
    {
        "name": "update_goal_progress",
        "description": (
            "Actualiza el progreso de una meta. Usar cuando el usuario "
            "reporta avances en una meta."
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
        "name": "delegate_to_planner",
        "description": (
            "Delega al subagente PLANNER especializado en planificación de calendario. "
            "Usar cuando el usuario quiere: organizar su día/semana, planificar tiempos para metas, "
            "verificar cumplimiento de bloques, hacer review del día. NO usar para crear "
            "una sola tarea — usar create_task. El planner devuelve un resumen del plan/review "
            "que debes presentar al usuario."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["plan_day", "verify_recent", "daily_review"],
                    "description": "plan_day=crear plan para mañana (o fecha dada), verify_recent=revisar cumplimiento de bloques pasados, daily_review=cierre del día",
                },
                "target_date": {
                    "type": "string",
                    "description": "Para plan_day: fecha objetivo YYYY-MM-DD. Default mañana.",
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
