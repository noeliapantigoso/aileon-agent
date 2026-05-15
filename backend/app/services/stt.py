"""
Servicio de Speech-to-Text usando Groq Whisper Large V3.

Acepta audio WAV (16kHz, 16-bit, mono) del ESP32 y WebM/Opus del navegador.
Usa el endpoint compatible con OpenAI de Groq.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Groq Whisper endpoint (compatible con OpenAI)
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # segundos


class STTService:
    """Transcripción de audio a texto con Groq Whisper."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def transcribe(
        self,
        audio_data: bytes,
        content_type: str = "audio/wav",
    ) -> Optional[str]:
        """
        Transcribe audio a texto usando Groq Whisper.

        Args:
            audio_data: Bytes crudos del audio.
            content_type: MIME type del audio (audio/wav o audio/webm).

        Returns:
            Texto transcrito o None si no se detectó habla.
        """
        extension = "wav" if "wav" in content_type else "webm"
        filename = f"audio.{extension}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._client.post(
                    GROQ_STT_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files={"file": (filename, BytesIO(audio_data), content_type)},
                    data={
                        "model": "whisper-large-v3",
                        "language": "es",
                        "response_format": "json",
                    },
                )
                response.raise_for_status()

                result = response.json()
                text = result.get("text", "").strip()

                if not text or _is_noise(text):
                    logger.info("Transcripción vacía o ruido, ignorando")
                    return None

                logger.info("Transcripción exitosa: %s chars", len(text))
                return text

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Groq STT error (intento %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    exc.response.status_code,
                )
                if attempt == MAX_RETRIES:
                    raise
                import asyncio
                await asyncio.sleep(INITIAL_BACKOFF * (2 ** (attempt - 1)))

            except httpx.RequestError as exc:
                logger.warning(
                    "Groq STT connection error (intento %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt == MAX_RETRIES:
                    raise
                import asyncio
                await asyncio.sleep(INITIAL_BACKOFF * (2 ** (attempt - 1)))

        return None

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        await self._client.aclose()


def _is_noise(text: str) -> bool:
    """Detecta si la transcripción es solo ruido o artefactos."""
    noise_patterns = [
        "gracias por ver",
        "suscríbete",
        "subtítulos",
        "...",
        "¡Suscríbete",
    ]
    text_lower = text.lower().strip()
    if len(text_lower) < 3:
        return True
    return any(pattern.lower() in text_lower for pattern in noise_patterns)
