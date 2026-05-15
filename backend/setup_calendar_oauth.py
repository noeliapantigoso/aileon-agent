"""
Setup OAuth para Google Calendar (corre LOCAL una sola vez).

Pasos previos en GCP Console:
1. Habilitar Google Calendar API en proyecto agente-ia-organizador
2. OAuth consent screen → External → Test users: chat-1@avgust.com.pe
3. Credentials → Create OAuth Client ID → Application: Desktop app
4. Descargar JSON como `oauth_client.json` en agent/backend/

Uso:
    python setup_calendar_oauth.py

Salida:
    token.json local — contiene access_token + refresh_token
    Después subimos refresh_token a Secret Manager.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    sys.exit(1)


SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

CLIENT_FILE = "oauth_client.json"
TOKEN_FILE = "token.json"


def main():
    if not Path(CLIENT_FILE).exists():
        print(f"\nERROR: falta {CLIENT_FILE}")
        print("\nPasos:")
        print("1. Ve a https://console.cloud.google.com/apis/credentials?project=agente-ia-organizador")
        print("2. CREATE CREDENTIALS → OAuth client ID")
        print("3. Application type: Desktop app")
        print("4. Name: 'Asistente Calendar'")
        print("5. Descargá el JSON")
        print(f"6. Renombralo a '{CLIENT_FILE}' y movelo a agent/backend/")
        print("\nSi no aparece la opción, antes configurá:")
        print("- APIs & Services → OAuth consent screen → External")
        print("- Test users: agregá tu email (chat-1@avgust.com.pe)")
        sys.exit(1)

    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    print(f"\n[OK] Token guardado en {TOKEN_FILE}")
    print(f"  Refresh token (los primeros chars): {creds.refresh_token[:20] if creds.refresh_token else 'NONE'}...")

    # Imprimir cómo subirlo a Secret Manager
    print("\n--- Siguiente paso: subir a Secret Manager ---")
    print("gcloud secrets create google-calendar-token --replication-policy=automatic --project agente-ia-organizador")
    print(f"gcloud secrets versions add google-calendar-token --data-file={TOKEN_FILE} --project agente-ia-organizador")
    print("\nO si el secret ya existe:")
    print(f"gcloud secrets versions add google-calendar-token --data-file={TOKEN_FILE} --project agente-ia-organizador")
    print("\nDespués hay que dar acceso al SA del backend:")
    print("gcloud secrets add-iam-policy-binding google-calendar-token \\")
    print('  --member="serviceAccount:assistant-sa@agente-ia-organizador.iam.gserviceaccount.com" \\')
    print('  --role="roles/secretmanager.secretAccessor" \\')
    print("  --project agente-ia-organizador")


if __name__ == "__main__":
    main()
