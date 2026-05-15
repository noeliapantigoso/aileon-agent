"""
Repara las DBs de Notion: agrega propiedades faltantes según el schema esperado.

No borra ni modifica props existentes. Solo agrega las que falten.

Uso:
    python repair_notion.py
"""

from __future__ import annotations

import os
import sys

try:
    from notion_client import Client
except ImportError:
    print("ERROR: pip install notion-client")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


REQUIRED_SCHEMAS = {
    "tasks": {
        "Status": {"select": {"options": [
            {"name": "Inbox", "color": "gray"},
            {"name": "Next", "color": "blue"},
            {"name": "In Progress", "color": "yellow"},
            {"name": "Waiting", "color": "orange"},
            {"name": "Done", "color": "green"},
        ]}},
        "Priority": {"select": {"options": [
            {"name": "P0", "color": "red"},
            {"name": "P1", "color": "orange"},
            {"name": "P2", "color": "yellow"},
            {"name": "P3", "color": "gray"},
        ]}},
        "Due Date": {"date": {}},
        "Scheduled Date": {"date": {}},
        "Time Estimate": {"number": {"format": "number"}},
        "Energy Level": {"select": {"options": [
            {"name": "High", "color": "red"},
            {"name": "Medium", "color": "yellow"},
            {"name": "Low", "color": "blue"},
        ]}},
        "Tags": {"multi_select": {"options": []}},
        "Created By": {"select": {"options": [
            {"name": "Voice", "color": "purple"},
            {"name": "Text", "color": "blue"},
            {"name": "Manual", "color": "gray"},
        ]}},
    },
    "notes": {
        "Source": {"select": {"options": [
            {"name": "Voice", "color": "purple"},
            {"name": "Text", "color": "blue"},
            {"name": "Meeting", "color": "green"},
            {"name": "Idea", "color": "yellow"},
            {"name": "Quick Capture", "color": "orange"},
        ]}},
        "Status": {"select": {"options": [
            {"name": "Unprocessed", "color": "red"},
            {"name": "Processed", "color": "green"},
        ]}},
        "Tags": {"multi_select": {"options": []}},
        "Audio URL": {"url": {}},
    },
    "goals": {
        "Type": {"select": {"options": [
            {"name": "Short Term", "color": "green"},
            {"name": "Medium Term", "color": "yellow"},
            {"name": "Long Term", "color": "red"},
        ]}},
        "Status": {"select": {"options": [
            {"name": "Active", "color": "green"},
            {"name": "Completed", "color": "blue"},
            {"name": "Paused", "color": "yellow"},
            {"name": "Cancelled", "color": "gray"},
        ]}},
        "Progress": {"number": {"format": "percent"}},
        "Target Date": {"date": {}},
        "Key Results": {"rich_text": {}},
        "Area": {"select": {"options": [
            {"name": "Work", "color": "blue"},
            {"name": "Personal", "color": "purple"},
            {"name": "Health", "color": "green"},
            {"name": "Finance", "color": "yellow"},
            {"name": "Learning", "color": "orange"},
            {"name": "Relationships", "color": "pink"},
        ]}},
    },
    "daily_agenda": {
        "Date": {"date": {}},
        "Score": {"number": {"format": "number"}},
        "Wins": {"rich_text": {}},
    },
}

DB_ENV_KEYS = {
    "tasks": "NOTION_TASKS_DB",
    "notes": "NOTION_NOTES_DB",
    "goals": "NOTION_GOALS_DB",
    "daily_agenda": "NOTION_DAILY_AGENDA_DB",
}


def repair_database(notion: Client, db_name: str, db_id: str, schema: dict) -> int:
    """Agrega props faltantes a una DB. Devuelve cantidad de props agregadas."""
    print(f"\n🔍 Reparando '{db_name}' ({db_id})...")

    try:
        db = notion.databases.retrieve(database_id=db_id)
    except Exception as exc:
        print(f"  ✗ No se pudo leer la DB: {exc}")
        return 0

    existing_props = set(db.get("properties", {}).keys())
    print(f"  Props actuales: {sorted(existing_props)}")

    missing = {
        name: defn
        for name, defn in schema.items()
        if name not in existing_props
    }

    if not missing:
        print(f"  ✓ Sin cambios. Todas las props existen.")
        return 0

    print(f"  Agregando: {list(missing.keys())}")

    try:
        notion.databases.update(database_id=db_id, properties=missing)
        print(f"  ✓ Agregadas {len(missing)} propiedades")
        return len(missing)
    except Exception as exc:
        print(f"  ✗ Falló: {exc}")
        return 0


def main():
    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("ERROR: NOTION_TOKEN no está en .env")
        sys.exit(1)

    notion = Client(auth=token)

    try:
        notion.users.me()
    except Exception as exc:
        print(f"ERROR: No se pudo conectar con Notion: {exc}")
        sys.exit(1)

    total = 0
    for db_name, schema in REQUIRED_SCHEMAS.items():
        env_key = DB_ENV_KEYS[db_name]
        db_id = os.getenv(env_key)
        if not db_id:
            print(f"\n⚠ {env_key} no está en .env, saltando")
            continue
        total += repair_database(notion, db_name, db_id, schema)

    print(f"\n{'='*50}")
    print(f"Total props agregadas: {total}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
