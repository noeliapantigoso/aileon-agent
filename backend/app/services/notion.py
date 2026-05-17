"""
Capa de abstracción sobre la Notion API.

Usa notion-client Python SDK con Internal Integration Token.
Cada método retorna diccionarios SIMPLIFICADOS para reducir tokens del LLM.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from notion_client import AsyncClient
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)


class NotionService:
    """Wrapper completo para la Notion API."""

    def __init__(self, token: str, db_ids: dict[str, str]) -> None:
        """
        Args:
            token: Notion Internal Integration Token.
            db_ids: Mapping de nombres a database IDs.
                    Keys: "tasks", "notes", "goals", "daily_agenda"
        """
        self.client = AsyncClient(auth=token)
        self.db_ids = db_ids

    # ── Tasks ────────────────────────────────────────────────────────────────

    async def create_task(
        self,
        title: str,
        priority: str = "P2",
        due_date: Optional[str] = None,
        scheduled_date: Optional[str] = None,
        time_estimate_minutes: Optional[int] = None,
        project: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source: str = "Voice",
    ) -> dict[str, Any]:
        """Crea una nueva tarea en la database de Tasks."""
        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Status": {"select": {"name": "Inbox"}},
            "Priority": {"select": {"name": priority.upper()}},
            "Created By": {"select": {"name": source}},
        }

        if due_date:
            properties["Due Date"] = {"date": {"start": due_date}}
        if scheduled_date:
            properties["Scheduled Date"] = {"date": {"start": scheduled_date}}
        if time_estimate_minutes is not None:
            properties["Time Estimate"] = {"number": time_estimate_minutes}
        if tags:
            properties["Tags"] = {
                "multi_select": [{"name": t} for t in tags]
            }

        try:
            page = await self.client.pages.create(
                parent={"database_id": self.db_ids["tasks"]},
                properties=properties,
            )
            return _simplify_task(page)
        except APIResponseError as exc:
            logger.error("Notion create_task error: %s", exc)
            raise

    async def get_tasks(
        self,
        status: Optional[str] = None,
        date: Optional[str] = None,
        priority: Optional[str] = None,
        project: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Consulta tareas con filtros opcionales."""
        filters: list[dict] = []

        status_map = {
            "pending": ["Inbox", "Next"],
            "in_progress": ["In Progress"],
            "done": ["Done"],
            "waiting": ["Waiting"],
        }

        if status and status != "all":
            statuses = status_map.get(status, [status])
            if len(statuses) == 1:
                filters.append({
                    "property": "Status",
                    "select": {"equals": statuses[0]},
                })
            else:
                filters.append({
                    "or": [
                        {"property": "Status", "select": {"equals": s}}
                        for s in statuses
                    ]
                })

        if date:
            filters.append({
                "property": "Due Date",
                "date": {"equals": date},
            })

        if priority:
            filters.append({
                "property": "Priority",
                "select": {"equals": priority.upper()},
            })

        filter_param = {}
        if len(filters) == 1:
            filter_param = filters[0]
        elif len(filters) > 1:
            filter_param = {"and": filters}

        try:
            response = await self.client.databases.query(
                database_id=self.db_ids["tasks"],
                filter=filter_param if filter_param else None,
                page_size=min(limit, 100),
            )
            return [_simplify_task(page) for page in response["results"]]
        except APIResponseError as exc:
            logger.error("Notion get_tasks error: %s", exc)
            raise

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Lee una tarea por id (page id)."""
        try:
            page = await self.client.pages.retrieve(page_id=task_id)
            return _simplify_task(page)
        except APIResponseError as exc:
            logger.warning("Notion get_task(%s) error: %s", task_id, exc)
            return None

    async def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        """Actualiza propiedades de una tarea existente."""
        properties: dict[str, Any] = {}

        field_map = {
            "status": ("Status", "select"),
            "priority": ("Priority", "select"),
            "due_date": ("Due Date", "date"),
            "scheduled_date": ("Scheduled Date", "date"),
        }

        for key, value in updates.items():
            if key in field_map and value is not None:
                prop_name, prop_type = field_map[key]
                if prop_type == "select":
                    name = value.replace("_", " ").title() if key == "status" else value.upper()
                    properties[prop_name] = {"select": {"name": name}}
                elif prop_type == "date":
                    properties[prop_name] = {"date": {"start": value}}

        try:
            page = await self.client.pages.update(
                page_id=task_id,
                properties=properties,
            )
            return _simplify_task(page)
        except APIResponseError as exc:
            logger.error("Notion update_task error: %s", exc)
            raise

    # ── Notes ────────────────────────────────────────────────────────────────

    async def save_note(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source: str = "Voice",
        audio_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Guarda una nota en la database de Notes."""
        if not title:
            title = content[:50] + ("..." if len(content) > 50 else "")

        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Source": {"select": {"name": source.title()}},
            "Status": {"select": {"name": "Unprocessed"}},
        }

        if tags:
            properties["Tags"] = {
                "multi_select": [{"name": t} for t in tags]
            }
        if audio_url:
            properties["Audio URL"] = {"url": audio_url}

        # Contenido como bloques de párrafo en el body de la página
        children = _text_to_blocks(content)

        try:
            page = await self.client.pages.create(
                parent={"database_id": self.db_ids["notes"]},
                properties=properties,
                children=children,
            )
            return {
                "id": page["id"],
                "title": title,
                "url": page["url"],
            }
        except APIResponseError as exc:
            logger.error("Notion save_note error: %s", exc)
            raise

    async def search_notes(self, query: str) -> list[dict[str, Any]]:
        """Busca notas por texto usando la Notion Search API."""
        try:
            response = await self.client.search(
                query=query,
                filter={"property": "object", "value": "page"},
                sort={"direction": "descending", "timestamp": "last_edited_time"},
                page_size=10,
            )
            results = []
            for page in response["results"]:
                parent = page.get("parent", {})
                parent_db = parent.get("database_id", "").replace("-", "")
                notes_db = self.db_ids["notes"].replace("-", "")
                if parent_db == notes_db:
                    results.append({
                        "id": page["id"],
                        "title": _extract_title(page),
                        "created_time": page.get("created_time", ""),
                    })
            return results
        except APIResponseError as exc:
            logger.error("Notion search_notes error: %s", exc)
            raise

    # ── Daily Agenda ─────────────────────────────────────────────────────────

    async def get_daily_agenda(self, date_str: Optional[str] = None) -> dict[str, Any]:
        """Obtiene la agenda del día. Si no existe, retorna tareas programadas."""
        if not date_str:
            date_str = date.today().isoformat()

        # Buscar en Daily Agenda database
        try:
            response = await self.client.databases.query(
                database_id=self.db_ids["daily_agenda"],
                filter={
                    "property": "Date",
                    "date": {"equals": date_str},
                },
                page_size=1,
            )

            if response["results"]:
                page = response["results"][0]
                return {
                    "id": page["id"],
                    "date": date_str,
                    "exists": True,
                    "url": page["url"],
                }

        except APIResponseError as exc:
            logger.warning("Could not query daily agenda: %s", exc)

        # Fallback: obtener tareas programadas para el día
        tasks = await self.get_tasks(date=date_str)
        return {
            "date": date_str,
            "exists": False,
            "scheduled_tasks": tasks,
        }

    async def save_daily_plan(
        self,
        date_str: str,
        plan_text: str,
        top_tasks: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Crea o actualiza la agenda del día en Notion."""
        properties: dict[str, Any] = {
            "Date": {"date": {"start": date_str}},
        }

        # Buscar si ya existe
        existing = await self.get_daily_agenda(date_str)

        children = _text_to_blocks(plan_text)

        try:
            if existing.get("exists"):
                # Actualizar página existente
                page_id = existing["id"]
                # Borrar contenido existente y agregar nuevo
                existing_blocks = await self.client.blocks.children.list(
                    block_id=page_id
                )
                for block in existing_blocks["results"]:
                    await self.client.blocks.delete(block_id=block["id"])

                await self.client.blocks.children.append(
                    block_id=page_id,
                    children=children,
                )
                return {
                    "id": page_id,
                    "date": date_str,
                    "action": "updated",
                }
            else:
                # Crear nueva página
                title = f"Plan {date_str}"
                properties["Name"] = {
                    "title": [{"text": {"content": title}}]
                }
                page = await self.client.pages.create(
                    parent={"database_id": self.db_ids["daily_agenda"]},
                    properties=properties,
                    children=children,
                )
                return {
                    "id": page["id"],
                    "date": date_str,
                    "action": "created",
                    "url": page["url"],
                }
        except APIResponseError as exc:
            logger.error("Notion save_daily_plan error: %s", exc)
            raise

    # ── Goals ────────────────────────────────────────────────────────────────

    async def create_goal(
        self,
        title: str,
        goal_type: str = "long_term",
        area: Optional[str] = None,
        target_date: Optional[str] = None,
        key_results: Optional[str] = None,
        initial_progress: int = 0,
    ) -> dict[str, Any]:
        """Crea una nueva meta en Notion DB Goals."""
        type_map = {
            "short_term": "Short Term",
            "medium_term": "Medium Term",
            "long_term": "Long Term",
        }
        area_map = {
            "work": "Work",
            "personal": "Personal",
            "health": "Health",
            "finance": "Finance",
            "learning": "Learning",
            "relationships": "Relationships",
        }

        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Type": {"select": {"name": type_map.get(goal_type.lower(), "Long Term")}},
            "Status": {"select": {"name": "Active"}},
            "Progress": {"number": initial_progress / 100.0},
        }

        if area:
            area_norm = area_map.get(area.lower(), area.title())
            properties["Area"] = {"select": {"name": area_norm}}
        if target_date:
            properties["Target Date"] = {"date": {"start": target_date}}
        if key_results:
            properties["Key Results"] = {
                "rich_text": [{"type": "text", "text": {"content": key_results[:1900]}}]
            }

        try:
            page = await self.client.pages.create(
                parent={"database_id": self.db_ids["goals"]},
                properties=properties,
            )
            return _simplify_goal(page)
        except APIResponseError as exc:
            logger.error("Notion create_goal error: %s", exc)
            raise

    async def get_goals(
        self,
        goal_type: Optional[str] = None,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        """Consulta metas activas con filtros opcionales."""
        filters: list[dict] = []

        type_map = {
            "short_term": "Short Term",
            "medium_term": "Medium Term",
            "long_term": "Long Term",
        }

        if goal_type and goal_type != "all":
            notion_type = type_map.get(goal_type, goal_type)
            filters.append({
                "property": "Type",
                "select": {"equals": notion_type},
            })

        if status and status != "all":
            filters.append({
                "property": "Status",
                "select": {"equals": status.title()},
            })

        filter_param = {}
        if len(filters) == 1:
            filter_param = filters[0]
        elif len(filters) > 1:
            filter_param = {"and": filters}

        try:
            response = await self.client.databases.query(
                database_id=self.db_ids["goals"],
                filter=filter_param if filter_param else None,
                page_size=50,
            )
            return [_simplify_goal(page) for page in response["results"]]
        except APIResponseError as exc:
            logger.error("Notion get_goals error: %s", exc)
            raise

    async def update_goal(
        self,
        goal_id: str,
        title: Optional[str] = None,
        goal_type: Optional[str] = None,
        area: Optional[str] = None,
        target_date: Optional[str] = None,
        key_results: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """Edita campos editables de una meta (NO progreso — usar update_goal_progress)."""
        type_map = {
            "short_term": "Short Term",
            "medium_term": "Medium Term",
            "long_term": "Long Term",
        }
        area_map = {
            "work": "Work",
            "personal": "Personal",
            "health": "Health",
            "finance": "Finance",
            "learning": "Learning",
            "relationships": "Relationships",
        }

        properties: dict[str, Any] = {}
        if title is not None:
            properties["Name"] = {"title": [{"text": {"content": title}}]}
        if goal_type is not None:
            properties["Type"] = {"select": {"name": type_map.get(goal_type.lower(), goal_type)}}
        if area is not None:
            properties["Area"] = {"select": {"name": area_map.get(area.lower(), area.title())}}
        if target_date is not None:
            properties["Target Date"] = {"date": {"start": target_date}}
        if key_results is not None:
            properties["Key Results"] = {
                "rich_text": [{"type": "text", "text": {"content": key_results[:1900]}}]
            }
        if status is not None:
            properties["Status"] = {"select": {"name": status.title()}}

        if not properties:
            return {"id": goal_id, "updated": False, "reason": "sin campos a actualizar"}

        try:
            page = await self.client.pages.update(
                page_id=goal_id,
                properties=properties,
            )
            return _simplify_goal(page)
        except APIResponseError as exc:
            logger.error("Notion update_goal error: %s", exc)
            raise

    async def archive_goal(self, goal_id: str) -> dict[str, Any]:
        """Archiva (soft-delete) una meta."""
        try:
            await self.client.pages.update(page_id=goal_id, archived=True)
            return {"id": goal_id, "archived": True}
        except APIResponseError as exc:
            logger.error("Notion archive_goal error: %s", exc)
            raise

    async def update_goal_progress(
        self,
        goal_id: str,
        progress_note: str,
        new_percentage: Optional[int] = None,
    ) -> dict[str, Any]:
        """Actualiza el progreso de una meta."""
        properties: dict[str, Any] = {}

        if new_percentage is not None:
            properties["Progress"] = {"number": new_percentage}

        try:
            # Actualizar propiedades si hay cambios
            if properties:
                await self.client.pages.update(
                    page_id=goal_id,
                    properties=properties,
                )

            # Agregar nota de progreso como bloque
            await self.client.blocks.children.append(
                block_id=goal_id,
                children=[
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": f"[{datetime.now().strftime('%Y-%m-%d')}] {progress_note}"
                                    },
                                }
                            ]
                        },
                    }
                ],
            )

            return {
                "id": goal_id,
                "progress_note": progress_note,
                "new_percentage": new_percentage,
                "action": "updated",
            }
        except APIResponseError as exc:
            logger.error("Notion update_goal_progress error: %s", exc)
            raise

    async def close(self) -> None:
        """Cierra el cliente de Notion."""
        await self.client.aclose()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _simplify_task(page: dict) -> dict[str, Any]:
    """Extrae campos clave de un page object de Notion Tasks."""
    props = page.get("properties", {})
    return {
        "id": page["id"],
        "title": _extract_title(page),
        "status": _extract_select(props, "Status"),
        "priority": _extract_select(props, "Priority"),
        "due_date": _extract_date(props, "Due Date"),
        "scheduled_date": _extract_date(props, "Scheduled Date"),
        "time_estimate": props.get("Time Estimate", {}).get("number"),
        "tags": _extract_multi_select(props, "Tags"),
        "url": page.get("url", ""),
    }


def _simplify_goal(page: dict) -> dict[str, Any]:
    """Extrae campos clave de un page object de Notion Goals."""
    props = page.get("properties", {})
    return {
        "id": page["id"],
        "title": _extract_title(page),
        "type": _extract_select(props, "Type"),
        "status": _extract_select(props, "Status"),
        "progress": props.get("Progress", {}).get("number", 0),
        "target_date": _extract_date(props, "Target Date"),
        "area": _extract_select(props, "Area"),
    }


def _extract_title(page: dict) -> str:
    """Extrae el título de un page object."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            if title_parts:
                return title_parts[0].get("text", {}).get("content", "")
    return ""


def _extract_select(props: dict, key: str) -> Optional[str]:
    """Extrae valor de un campo select."""
    prop = props.get(key, {})
    select = prop.get("select")
    if select:
        return select.get("name")
    return None


def _extract_multi_select(props: dict, key: str) -> list[str]:
    """Extrae valores de un campo multi_select."""
    prop = props.get(key, {})
    items = prop.get("multi_select", [])
    return [item.get("name", "") for item in items]


def _extract_date(props: dict, key: str) -> Optional[str]:
    """Extrae fecha de un campo date."""
    prop = props.get(key, {})
    date_val = prop.get("date")
    if date_val:
        return date_val.get("start")
    return None


def _text_to_blocks(text: str) -> list[dict]:
    """Convierte texto plano a bloques de párrafo de Notion."""
    paragraphs = text.split("\n\n") if "\n\n" in text else text.split("\n")
    blocks = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Notion limita rich_text a 2000 chars
        chunks = [para[i:i + 2000] for i in range(0, len(para), 2000)]
        for chunk in chunks:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": chunk}}
                    ]
                },
            })
    return blocks
