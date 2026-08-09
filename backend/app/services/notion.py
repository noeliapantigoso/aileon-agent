"""
Abstraction layer over the Notion API.

Uses the notion-client Python SDK with an internal integration token.
Every method returns SIMPLIFIED dicts to keep the model's token cost down.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from notion_client import AsyncClient
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)


class NotionService:
    """Full wrapper for the Notion API."""

    def __init__(self, token: str, db_ids: dict[str, str]) -> None:
        """
        Args:
            token: Notion Internal Integration Token.
            db_ids: Mapping of names to Notion database ids.
                    Keys: "tasks", "notes", "goals", "key_results"
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
        tags: Optional[list[str]] = None,
        source: str = "Voice",
        goal_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a new task in the Tasks database."""
        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Status": {"select": {"name": "Inbox"}},
            "Priority": {"select": {"name": priority.upper()}},
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
        if goal_id:
            properties["Goals"] = {"relation": [{"id": goal_id}]}

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
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query tasks with optional filters."""
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
        """Read one task by page id."""
        try:
            page = await self.client.pages.retrieve(page_id=task_id)
            return _simplify_task(page)
        except APIResponseError as exc:
            logger.warning("Notion get_task(%s) error: %s", task_id, exc)
            return None

    async def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        """
        Update the properties of an existing task.

        Covers every editable column of the Tasks database, so any field set
        wrongly at creation time can be corrected later.
        """
        properties: dict[str, Any] = {}

        field_map = {
            "title": ("Name", "title"),
            "status": ("Status", "select"),
            "priority": ("Priority", "select"),
            "due_date": ("Due Date", "date"),
            "scheduled_date": ("Scheduled Date", "date"),
            "time_estimate_minutes": ("Time Estimate", "number"),
            "tags": ("Tags", "multi_select"),
            "goal_id": ("Goals", "relation"),
        }

        for key, value in updates.items():
            if key not in field_map or value is None:
                continue
            prop_name, prop_type = field_map[key]
            if prop_type == "title":
                properties[prop_name] = {"title": [{"text": {"content": value}}]}
            elif prop_type == "select":
                name = value.replace("_", " ").title() if key == "status" else value.upper()
                properties[prop_name] = {"select": {"name": name}}
            elif prop_type == "date":
                properties[prop_name] = {"date": {"start": value}}
            elif prop_type == "number":
                properties[prop_name] = {"number": value}
            elif prop_type == "multi_select":
                properties[prop_name] = {"multi_select": [{"name": t} for t in value]}
            elif prop_type == "relation":
                properties[prop_name] = {"relation": [{"id": value}]}

        if not properties:
            return {"id": task_id, "updated": False, "reason": "no fields to update"}

        try:
            page = await self.client.pages.update(
                page_id=task_id,
                properties=properties,
            )
            return _simplify_task(page)
        except APIResponseError as exc:
            logger.error("Notion update_task error: %s", exc)
            raise

    async def archive_task(self, task_id: str) -> dict[str, Any]:
        """Archive (soft-delete) a task."""
        try:
            await self.client.pages.update(page_id=task_id, archived=True)
            return {"id": task_id, "archived": True}
        except APIResponseError as exc:
            logger.error("Notion archive_task error: %s", exc)
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
        """Save a note to the Notes database."""
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
        """Search notes by text using the Notion Search API."""
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
        """Create a new goal in the Goals database."""
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
            return {"id": goal_id, "updated": False, "reason": "no fields to update"}

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
        """Archive (soft-delete) a goal."""
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
        """Update a goal's progress."""
        properties: dict[str, Any] = {}

        if new_percentage is not None:
            properties["Progress"] = {"number": new_percentage / 100.0}

        try:
            # Update properties when there are changes
            if properties:
                await self.client.pages.update(
                    page_id=goal_id,
                    properties=properties,
                )

            # Append the progress note as a block
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

    # ── Key Results ──────────────────────────────────────────────────────────

    async def create_key_result(
        self,
        goal_id: str,
        title: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a Key Result linked to a goal."""
        if not self.db_ids.get("key_results"):
            raise ValueError("Key Results database not configured (NOTION_KEY_RESULTS_DB)")
        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Goals": {"relation": [{"id": goal_id}]},
            "Status": {"select": {"name": "Pending"}},
        }
        if notes:
            properties["Notes"] = {
                "rich_text": [{"type": "text", "text": {"content": notes[:1900]}}]
            }
        try:
            page = await self.client.pages.create(
                parent={"database_id": self.db_ids["key_results"]},
                properties=properties,
            )
            return _simplify_kr(page)
        except APIResponseError as exc:
            logger.error("Notion create_key_result error: %s", exc)
            raise

    async def get_key_results(
        self,
        goal_id: Optional[str] = None,
        unassigned_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        List Key Results.

        Args:
            goal_id: When given, only that goal's KRs.
            unassigned_only: When True, only the orphan KRs with no goal
                assigned. Takes precedence over goal_id.
        """
        if not self.db_ids.get("key_results"):
            return []

        query: dict[str, Any] = {
            "database_id": self.db_ids["key_results"],
            "page_size": 100,
        }
        if unassigned_only:
            query["filter"] = {"property": "Goals", "relation": {"is_empty": True}}
        elif goal_id:
            query["filter"] = {"property": "Goals", "relation": {"contains": goal_id}}

        try:
            response = await self.client.databases.query(**query)
            return [_simplify_kr(page) for page in response["results"]]
        except APIResponseError as exc:
            logger.error("Notion get_key_results error: %s", exc)
            raise

    async def archive_key_result(self, kr_id: str) -> dict[str, Any]:
        """Archive (soft-delete) a Key Result."""
        try:
            await self.client.pages.update(page_id=kr_id, archived=True)
            return {"id": kr_id, "archived": True}
        except APIResponseError as exc:
            logger.error("Notion archive_key_result error: %s", exc)
            raise

    async def update_key_result(
        self,
        kr_id: str,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        status: Optional[str] = None,
        goal_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Edit an existing KR's title, notes, status or assigned goal."""
        properties: dict[str, Any] = {}
        if goal_id is not None:
            properties["Goals"] = {"relation": [{"id": goal_id}]}
        if title is not None:
            properties["Name"] = {"title": [{"text": {"content": title}}]}
        if notes is not None:
            properties["Notes"] = {
                "rich_text": [{"type": "text", "text": {"content": notes[:1900]}}]
            }
        if status is not None:
            label = "Done" if status.lower() == "done" else "Pending"
            properties["Status"] = {"select": {"name": label}}

        if not properties:
            return {"id": kr_id, "updated": False, "reason": "no fields to update"}
        try:
            page = await self.client.pages.update(page_id=kr_id, properties=properties)
            kr = _simplify_kr(page)
            if status is not None:
                goal_id = _extract_relation_first(page.get("properties", {}), "Goals")
                if goal_id:
                    await self._recalculate_goal_progress(goal_id)
            return kr
        except APIResponseError as exc:
            logger.error("Notion update_key_result error: %s", exc)
            raise

    async def mark_kr_done(self, kr_id: str, note: str = "") -> dict[str, Any]:
        """Mark a KR as Done and recalculate the goal's progress."""
        properties: dict[str, Any] = {"Status": {"select": {"name": "Done"}}}
        try:
            page = await self.client.pages.update(page_id=kr_id, properties=properties)
            if note:
                await self.client.blocks.children.append(
                    block_id=kr_id,
                    children=[{
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{
                                "type": "text",
                                "text": {"content": f"[{datetime.now().strftime('%Y-%m-%d')}] {note}"},
                            }]
                        },
                    }],
                )
            goal_id = _extract_relation_first(page.get("properties", {}), "Goals")
            if goal_id:
                await self._recalculate_goal_progress(goal_id)
            return _simplify_kr(page)
        except APIResponseError as exc:
            logger.error("Notion mark_kr_done error: %s", exc)
            raise

    async def _recalculate_goal_progress(self, goal_id: str) -> None:
        """Recalculate a goal's progress as the fraction of completed KRs."""
        krs = await self.get_key_results(goal_id)
        if not krs:
            return
        done = sum(1 for kr in krs if kr.get("status") == "Done")
        progress = done / len(krs)
        try:
            await self.client.pages.update(
                page_id=goal_id,
                properties={"Progress": {"number": progress}},
            )
        except APIResponseError as exc:
            logger.warning("Could not recalculate goal progress for %s: %s", goal_id, exc)

    async def close(self) -> None:
        """Close the Notion client."""
        await self.client.aclose()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _simplify_task(page: dict) -> dict[str, Any]:
    """Pull the key fields out of a Notion Tasks page object."""
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
        "goal_id": _extract_relation_first(props, "Goals"),
        "url": page.get("url", ""),
    }


def _simplify_kr(page: dict) -> dict[str, Any]:
    """Pull the key fields out of a Notion Key Results page object."""
    props = page.get("properties", {})
    return {
        "id": page["id"],
        "title": _extract_title(page),
        "status": _extract_select(props, "Status") or "Pending",
        "notes": _extract_rich_text(props, "Notes"),
        "goal_id": _extract_relation_first(props, "Goals"),
    }


def _simplify_goal(page: dict) -> dict[str, Any]:
    """Pull the key fields out of a Notion Goals page object."""
    props = page.get("properties", {})
    return {
        "id": page["id"],
        "title": _extract_title(page),
        "type": _extract_select(props, "Type"),
        "status": _extract_select(props, "Status"),
        "progress": round(props.get("Progress", {}).get("number", 0) * 100),
        "target_date": _extract_date(props, "Target Date"),
        "area": _extract_select(props, "Area"),
    }


def _extract_title(page: dict) -> str:
    """Extract the title from a page object."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            if title_parts:
                return title_parts[0].get("text", {}).get("content", "")
    return ""


def _extract_select(props: dict, key: str) -> Optional[str]:
    """Extract the value of a select field."""
    prop = props.get(key, {})
    select = prop.get("select")
    if select:
        return select.get("name")
    return None


def _extract_multi_select(props: dict, key: str) -> list[str]:
    """Extract the values of a multi_select field."""
    prop = props.get(key, {})
    items = prop.get("multi_select", [])
    return [item.get("name", "") for item in items]


def _extract_date(props: dict, key: str) -> Optional[str]:
    """Extract the date from a date field."""
    prop = props.get(key, {})
    date_val = prop.get("date")
    if date_val:
        return date_val.get("start")
    return None


def _extract_rich_text(props: dict, key: str) -> str:
    """Extract the text from a rich_text field."""
    prop = props.get(key, {})
    items = prop.get("rich_text", [])
    return "".join(item.get("text", {}).get("content", "") for item in items)


def _extract_relation_first(props: dict, key: str) -> Optional[str]:
    """Extract the first id from a relation field."""
    prop = props.get(key, {})
    relations = prop.get("relation", [])
    if relations:
        return relations[0].get("id")
    return None


def _text_to_blocks(text: str) -> list[dict]:
    """Convert plain text into Notion paragraph blocks."""
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
