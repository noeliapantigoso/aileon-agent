"""
Autenticación dual: ESP32 (API key) y PWA (Firebase JWT).

ESP32 → Header: Authorization: Bearer {ESP32_API_KEY}
PWA   → Header: Authorization: Bearer {Firebase_JWT}
"""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def verify_request(
    authorization: str = Header(..., description="Bearer token"),
    settings: Settings = Depends(get_settings),
) -> str:
    """
    Valida el token de autorización y retorna la fuente del request.

    Returns:
        "esp32" si el token coincide con la API key del ESP32.
        "pwa" si el JWT de Firebase es válido.

    Raises:
        HTTPException 401 si el token es inválido.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must start with 'Bearer'",
        )

    token = authorization[7:]  # Strip "Bearer "

    # 1. Verificar si es la API key del ESP32
    if token == settings.esp32_api_key:
        return "esp32"

    # 2. Intentar validar como Firebase JWT
    try:
        return await _verify_firebase_jwt(token, settings)
    except Exception as exc:
        logger.warning("Auth failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def _verify_firebase_jwt(token: str, settings: Settings) -> str:
    """
    Valida un JWT de Firebase Authentication.

    Retorna "pwa" si es válido y el email está autorizado.
    """
    try:
        import firebase_admin  # noqa: F811
        from firebase_admin import auth as firebase_auth

        # Inicializar Firebase si no está inicializado
        if not firebase_admin._apps:
            if settings.firebase_credentials_path:
                cred = firebase_admin.credentials.Certificate(
                    settings.firebase_credentials_path
                )
                firebase_admin.initialize_app(cred)
            else:
                # En Cloud Run usa Application Default Credentials
                firebase_admin.initialize_app()

        decoded = firebase_auth.verify_id_token(token)
        email = decoded.get("email", "")

        # Restringir a un solo email si está configurado
        if settings.allowed_email and email != settings.allowed_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not authorized",
            )

        return "pwa"

    except ImportError:
        logger.warning("firebase-admin not installed, rejecting JWT")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase auth not configured",
        )
