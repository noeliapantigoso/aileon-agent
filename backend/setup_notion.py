"""
Script de setup: crea todas las páginas y bases de datos de Notion.
Uso:
    python setup_notion.py
    python setup_notion.py --token ntn_xxxx
    python setup_notion.py --parent-id <page_id>  # para crear dentro de una página existente

Al terminar imprime los IDs para pegar en .env
"""

import argparse
import os
import sys

try:
    from notion_client import Client
except ImportError:
    print("ERROR: Instala notion-client primero: pip install notion-client")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv opcional


# ─── Schemas ─────────────────────────────────────────────────────────────────

TASKS_PROPERTIES = {
    "Name": {"title": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "Inbox",       "color": "gray"},
                {"name": "Next",        "color": "blue"},
                {"name": "In Progress", "color": "yellow"},
                {"name": "Waiting",     "color": "orange"},
                {"name": "Done",        "color": "green"},
            ]
        }
    },
    "Priority": {
        "select": {
            "options": [
                {"name": "P0", "color": "red"},
                {"name": "P1", "color": "orange"},
                {"name": "P2", "color": "yellow"},
                {"name": "P3", "color": "gray"},
            ]
        }
    },
    "Due Date":       {"date": {}},
    "Scheduled Date": {"date": {}},
    "Time Estimate":  {"number": {"format": "number"}},
    "Energy Level": {
        "select": {
            "options": [
                {"name": "High",   "color": "red"},
                {"name": "Medium", "color": "yellow"},
                {"name": "Low",    "color": "blue"},
            ]
        }
    },
    "Tags":       {"multi_select": {"options": []}},
    "Created By": {
        "select": {
            "options": [
                {"name": "Voice",  "color": "purple"},
                {"name": "Text",   "color": "blue"},
                {"name": "Manual", "color": "gray"},
            ]
        }
    },
}

NOTES_PROPERTIES = {
    "Name": {"title": {}},
    "Source": {
        "select": {
            "options": [
                {"name": "Voice",         "color": "purple"},
                {"name": "Text",          "color": "blue"},
                {"name": "Meeting",       "color": "green"},
                {"name": "Idea",          "color": "yellow"},
                {"name": "Quick Capture", "color": "orange"},
            ]
        }
    },
    "Status": {
        "select": {
            "options": [
                {"name": "Unprocessed", "color": "red"},
                {"name": "Processed",   "color": "green"},
            ]
        }
    },
    "Tags":      {"multi_select": {"options": []}},
    "Audio URL": {"url": {}},
}

GOALS_PROPERTIES = {
    "Name": {"title": {}},
    "Type": {
        "select": {
            "options": [
                {"name": "Short Term",  "color": "green"},
                {"name": "Medium Term", "color": "yellow"},
                {"name": "Long Term",   "color": "red"},
            ]
        }
    },
    "Status": {
        "select": {
            "options": [
                {"name": "Active",    "color": "green"},
                {"name": "Completed", "color": "blue"},
                {"name": "Paused",    "color": "yellow"},
                {"name": "Cancelled", "color": "gray"},
            ]
        }
    },
    "Progress":    {"number": {"format": "percent"}},
    "Target Date": {"date": {}},
    "Key Results": {"rich_text": {}},
    "Area": {
        "select": {
            "options": [
                {"name": "Work",          "color": "blue"},
                {"name": "Personal",      "color": "purple"},
                {"name": "Health",        "color": "green"},
                {"name": "Finance",       "color": "yellow"},
                {"name": "Learning",      "color": "orange"},
                {"name": "Relationships", "color": "pink"},
            ]
        }
    },
}

DAILY_AGENDA_PROPERTIES = {
    "Name":  {"title": {}},
    "Date":  {"date": {}},
    "Score": {"number": {"format": "number"}},
    "Wins":  {"rich_text": {}},
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def create_parent_page(notion: Client, title: str) -> str:
    """Crea una página raíz en el workspace y devuelve su ID."""
    response = notion.pages.create(
        parent={"type": "workspace", "workspace": True},
        properties={
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
        icon={"type": "emoji", "emoji": "🤖"},
    )
    return response["id"]


def create_database(notion: Client, parent_id: str, title: str, properties: dict, icon: str) -> str:
    """Crea una base de datos dentro de una página y devuelve su ID."""
    response = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_id},
        title=[{"type": "text", "text": {"content": title}}],
        icon={"type": "emoji", "emoji": icon},
        properties=properties,
    )
    return response["id"]


def strip_dashes(page_id: str) -> str:
    return page_id.replace("-", "")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Setup Notion databases para el asistente")
    parser.add_argument("--token",     help="Notion Integration Token (o usa NOTION_TOKEN en .env)")
    parser.add_argument("--parent-id", help="ID de página existente donde crear las DBs (opcional)")
    args = parser.parse_args()

    token = args.token or os.getenv("NOTION_TOKEN")
    if not token:
        print("ERROR: Falta el token de Notion.")
        print("  Opción 1: python setup_notion.py --token ntn_xxxx")
        print("  Opción 2: pon NOTION_TOKEN en tu .env")
        sys.exit(1)

    notion = Client(auth=token)

    # Verificar conexión
    try:
        notion.users.me()
    except Exception as e:
        print(f"ERROR: No se pudo conectar con Notion: {e}")
        sys.exit(1)

    print("\n✓ Conectado a Notion\n")

    # Crear o usar página padre
    if args.parent_id:
        parent_id = args.parent_id
        print(f"Usando página existente: {parent_id}")
    else:
        print("Creando página raíz 'Asistente IA'...")
        try:
            parent_id = create_parent_page(notion, "Asistente IA")
            print(f"  ✓ Página creada: {strip_dashes(parent_id)}")
        except Exception as e:
            print(f"\nERROR al crear página raíz: {e}")
            print("\nSi el error es de permisos, la integración necesita acceso al workspace.")
            print("Alternativa: crea una página vacía en Notion manualmente, copia su ID")
            print("y ejecuta: python setup_notion.py --parent-id <ID_DE_LA_PAGINA>\n")
            sys.exit(1)

    # Crear las 4 bases de datos
    databases = [
        ("Tasks",        TASKS_PROPERTIES,        "✅", "NOTION_TASKS_DB"),
        ("Notes",        NOTES_PROPERTIES,         "📝", "NOTION_NOTES_DB"),
        ("Goals",        GOALS_PROPERTIES,         "🎯", "NOTION_GOALS_DB"),
        ("Daily Agenda", DAILY_AGENDA_PROPERTIES,  "📅", "NOTION_DAILY_AGENDA_DB"),
    ]

    results = {}
    for title, properties, icon, env_key in databases:
        print(f"Creando base de datos '{title}'...")
        try:
            db_id = create_database(notion, parent_id, title, properties, icon)
            clean_id = strip_dashes(db_id)
            results[env_key] = clean_id
            print(f"  ✓ {env_key}={clean_id}")
        except Exception as e:
            print(f"  ✗ Error: {e}")

    # Resultado final
    print("\n" + "="*60)
    print("COPIA ESTAS LÍNEAS EN TU .env:")
    print("="*60)
    for env_key, db_id in results.items():
        print(f"{env_key}={db_id}")
    print("="*60 + "\n")

    if len(results) == 4:
        print("✓ Setup completo. Todas las bases de datos creadas.\n")
    else:
        print(f"⚠ Solo se crearon {len(results)}/4 bases de datos. Revisa los errores.\n")


if __name__ == "__main__":
    main()
