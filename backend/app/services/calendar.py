"""
Servicio de Google Calendar.

Carga credenciales OAuth desde Secret Manager (Cloud Run) o file local (dev).
Wrapper con métodos para list/create/update/delete events + utilidades de
free-slot finding y tracking de cumplimiento.

Convención de events del planner:
- summary: prefijo "[plan]" para distinguirlos de eventos manuales
- extendedProperties.private.experiment_id: si el bloque es de un experimento
- extendedProperties.private.goal_id: si el bloque avanza una meta
- extendedProperties.private.task_id: si el bloque es una tarea Notion
- extendedProperties.private.completed: "true"|"false"|"partial" tras verificación
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

PLAN_PREFIX = "[plan]"


class CalendarService:
    """Wrapper Google Calendar API + utilidades de planning."""

    def __init__(
        self,
        token_json: Optional[str] = None,
        calendar_id: str = "primary",
        user_timezone: str = "America/Lima",
    ) -> None:
        self._token_json = token_json
        self._calendar_id = calendar_id
        self._timezone = user_timezone
        self._service = None

    def _build_service(self):
        """Construye el cliente Calendar con auto-refresh del token."""
        if self._service is not None:
            return self._service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if self._token_json is None:
            raise RuntimeError("No OAuth token disponible para Calendar")

        token_data = (
            json.loads(self._token_json) if isinstance(self._token_json, str)
            else self._token_json
        )
        creds = Credentials.from_authorized_user_info(token_data, CALENDAR_SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise RuntimeError("OAuth creds expiradas y sin refresh_token")

        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # ── Read ────────────────────────────────────────────────────────────────

    def list_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: int = 7,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista eventos en rango."""
        svc = self._build_service()
        now = datetime.now(timezone.utc)
        t_min = (start or now).isoformat()
        t_max = (end or now + timedelta(days=days)).isoformat()

        result = svc.events().list(
            calendarId=self._calendar_id,
            timeMin=t_min,
            timeMax=t_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        ).execute()

        return [_simplify_event(e) for e in result.get("items", [])]

    def get_event(self, event_id: str) -> dict[str, Any]:
        svc = self._build_service()
        return _simplify_event(
            svc.events().get(calendarId=self._calendar_id, eventId=event_id).execute()
        )

    def find_free_slots(
        self,
        start: datetime,
        end: datetime,
        min_duration_minutes: int = 30,
    ) -> list[tuple[datetime, datetime]]:
        """Encuentra huecos libres entre dos datetimes."""
        events = self.list_events(start=start, end=end)
        busy: list[tuple[datetime, datetime]] = []
        for e in events:
            es = _parse_dt(e.get("start"))
            ee = _parse_dt(e.get("end"))
            if es and ee:
                busy.append((es, ee))
        busy.sort()

        free: list[tuple[datetime, datetime]] = []
        cursor = start
        for b_start, b_end in busy:
            if b_start > cursor:
                if (b_start - cursor).total_seconds() / 60 >= min_duration_minutes:
                    free.append((cursor, b_start))
            cursor = max(cursor, b_end)
        if (end - cursor).total_seconds() / 60 >= min_duration_minutes:
            free.append((cursor, end))
        return free

    # ── Write ───────────────────────────────────────────────────────────────

    def create_event(
        self,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: Optional[str] = None,
        is_plan: bool = True,
        experiment_id: Optional[str] = None,
        goal_id: Optional[str] = None,
        task_id: Optional[str] = None,
        color_id: Optional[str] = None,
    ) -> dict[str, Any]:
        svc = self._build_service()
        title = f"{PLAN_PREFIX} {summary}" if is_plan else summary

        private_props: dict[str, str] = {}
        if experiment_id:
            private_props["experiment_id"] = experiment_id
        if goal_id:
            private_props["goal_id"] = goal_id
        if task_id:
            private_props["task_id"] = task_id

        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start_iso, "timeZone": self._timezone},
            "end": {"dateTime": end_iso, "timeZone": self._timezone},
        }
        if description:
            body["description"] = description
        if color_id:
            body["colorId"] = color_id
        if private_props:
            body["extendedProperties"] = {"private": private_props}

        result = svc.events().insert(calendarId=self._calendar_id, body=body).execute()
        return _simplify_event(result)

    def update_event(
        self,
        event_id: str,
        summary: Optional[str] = None,
        start_iso: Optional[str] = None,
        end_iso: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        svc = self._build_service()
        event = svc.events().get(calendarId=self._calendar_id, eventId=event_id).execute()
        if summary is not None:
            event["summary"] = summary
        if start_iso is not None:
            event["start"] = {"dateTime": start_iso, "timeZone": self._timezone}
        if end_iso is not None:
            event["end"] = {"dateTime": end_iso, "timeZone": self._timezone}
        if description is not None:
            event["description"] = description
        result = svc.events().update(
            calendarId=self._calendar_id, eventId=event_id, body=event
        ).execute()
        return _simplify_event(result)

    def delete_event(self, event_id: str) -> dict[str, Any]:
        svc = self._build_service()
        svc.events().delete(calendarId=self._calendar_id, eventId=event_id).execute()
        return {"deleted": event_id}

    def mark_event_completed(
        self,
        event_id: str,
        completed: str = "true",
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """Marca cumplimiento usando extendedProperties + emoji en title."""
        svc = self._build_service()
        event = svc.events().get(calendarId=self._calendar_id, eventId=event_id).execute()

        ext = event.get("extendedProperties", {})
        priv = ext.get("private", {}) or {}
        priv["completed"] = completed
        if note:
            priv["completion_note"] = note[:500]
        event["extendedProperties"] = {"private": priv}

        original = event.get("summary", "")
        original = original.replace("✅ ", "").replace("❌ ", "").replace("⚠️ ", "")
        if completed == "true":
            event["summary"] = f"✅ {original}"
        elif completed == "false":
            event["summary"] = f"❌ {original}"
        elif completed == "partial":
            event["summary"] = f"⚠️ {original}"

        result = svc.events().update(
            calendarId=self._calendar_id, eventId=event_id, body=event
        ).execute()
        return _simplify_event(result)

    # ── Stats ───────────────────────────────────────────────────────────────

    def get_completion_history(self, days: int = 14) -> dict[str, Any]:
        """Estadísticas de cumplimiento últimos N días."""
        now = datetime.now(timezone.utc)
        events = self.list_events(
            start=now - timedelta(days=days),
            end=now,
            max_results=500,
        )

        plan_events = [
            e for e in events
            if e.get("summary", "").startswith(PLAN_PREFIX)
            or "[plan]" in (e.get("summary") or "")
        ]
        total = len(plan_events)
        completed = sum(1 for e in plan_events if e.get("completed") == "true")
        failed = sum(1 for e in plan_events if e.get("completed") == "false")
        partial = sum(1 for e in plan_events if e.get("completed") == "partial")
        pending = total - completed - failed - partial

        by_hour: dict[int, dict[str, int]] = {}
        for e in plan_events:
            es = _parse_dt(e.get("start"))
            if not es:
                continue
            h = es.hour
            slot = by_hour.setdefault(h, {"total": 0, "completed": 0})
            slot["total"] += 1
            if e.get("completed") == "true":
                slot["completed"] += 1

        return {
            "days": days,
            "total_plan_events": total,
            "completed": completed,
            "failed": failed,
            "partial": partial,
            "pending": pending,
            "completion_rate": (completed / total) if total > 0 else None,
            "by_hour": by_hour,
        }


def _simplify_event(event: dict) -> dict[str, Any]:
    """Devuelve dict simplificado de un event Calendar."""
    priv = event.get("extendedProperties", {}).get("private", {}) or {}
    return {
        "id": event.get("id"),
        "summary": event.get("summary", ""),
        "description": event.get("description", ""),
        "start": event.get("start", {}).get("dateTime") or event.get("start", {}).get("date"),
        "end": event.get("end", {}).get("dateTime") or event.get("end", {}).get("date"),
        "experiment_id": priv.get("experiment_id"),
        "goal_id": priv.get("goal_id"),
        "task_id": priv.get("task_id"),
        "completed": priv.get("completed"),
        "completion_note": priv.get("completion_note"),
        "html_link": event.get("htmlLink"),
    }


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def load_calendar_token_from_secret(
    project_id: str,
    secret_name: str = "google-calendar-token",
) -> Optional[str]:
    """Lee token de Secret Manager. Devuelve JSON string."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to load calendar token from Secret Manager: %s", exc)
        return None


def load_calendar_token_local(path: str = "token.json") -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
