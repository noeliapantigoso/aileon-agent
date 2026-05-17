"""
Carga el perfil de personalidad del usuario en Firestore (assistant_users/<user_id>).

Lee desde `personality.json` (gitignored — datos personales).
Si no existe, falla con instrucción para copiar `personality.example.json`.

Uso:
    python seed_personality.py
    python seed_personality.py --file otro_archivo.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from google.cloud import firestore
except ImportError:
    print("ERROR: pip install google-cloud-firestore")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


PROJECT_ID = os.getenv("GCP_PROJECT_ID", "agente-ia-organizador")
PREFIX = os.getenv("FIRESTORE_COLLECTION_PREFIX", "assistant")
USER_ID = os.getenv("USER_ID", "user")

DEFAULT_FILE = Path(__file__).parent / "personality.json"
EXAMPLE_FILE = Path(__file__).parent / "personality.example.json"


def load_personality(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: {path.name} no encontrado.")
        if EXAMPLE_FILE.exists():
            print(f"Copia la plantilla: cp {EXAMPLE_FILE.name} {path.name}")
            print("Luego llena con tus datos antes de correr este script.")
        sys.exit(1)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_comment", None)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="Ruta al JSON de personalidad")
    args = parser.parse_args()

    data = load_personality(Path(args.file))

    print(f"Connecting to Firestore project: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)
    doc_ref = db.collection(f"{PREFIX}_users").document(USER_ID)

    doc_ref.set(data, merge=True)

    enneagram = data.get("personality", {}).get("enneagram", {})
    struggles = data.get("current_struggles", [])
    proactive = data.get("proactive_messages", {})

    print(f"\n✓ Personality saved into {PREFIX}_users/{USER_ID}")
    print(f"  - Enneagram type: {enneagram.get('type')} ({enneagram.get('name')})")
    print(f"  - Current struggles: {struggles}")
    print(f"  - Proactive config: {proactive}")


if __name__ == "__main__":
    main()