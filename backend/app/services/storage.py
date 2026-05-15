"""
Servicio de almacenamiento en Google Cloud Storage.

Guarda backups de audio para referencia futura.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class StorageService:
    """Wrapper para Google Cloud Storage."""

    def __init__(self, bucket_name: str, project_id: str) -> None:
        self._bucket_name = bucket_name
        self._project_id = project_id
        self._client = None
        self._bucket = None

    def _ensure_client(self) -> None:
        """Inicializa el cliente GCS lazily."""
        if self._client is None:
            try:
                from google.cloud import storage
                self._client = storage.Client(project=self._project_id)
                self._bucket = self._client.bucket(self._bucket_name)
            except Exception as exc:
                logger.warning("Cloud Storage not available: %s", exc)

    async def upload_audio(
        self,
        audio_data: bytes,
        content_type: str = "audio/wav",
        source: str = "esp32",
    ) -> Optional[str]:
        """
        Sube audio a Cloud Storage y retorna la URL.

        Args:
            audio_data: Bytes del audio.
            content_type: MIME type.
            source: Fuente del audio (esp32/pwa).

        Returns:
            URL pública del archivo o None si falla.
        """
        self._ensure_client()
        if self._bucket is None:
            logger.warning("Cloud Storage not configured, skipping upload")
            return None

        try:
            extension = "wav" if "wav" in content_type else "webm"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            blob_name = f"audio/{source}/{timestamp}.{extension}"

            blob = self._bucket.blob(blob_name)
            blob.upload_from_string(audio_data, content_type=content_type)

            url = f"gs://{self._bucket_name}/{blob_name}"
            logger.info("Audio uploaded: %s", url)
            return url

        except Exception as exc:
            logger.error("Failed to upload audio: %s", exc)
            return None
