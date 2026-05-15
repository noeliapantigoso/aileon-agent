"""
Servicio de Telegram Bot para recibir mensajes de texto.

Usa python-telegram-bot con polling (no necesita webhook ni dominio público).
Solo acepta mensajes del user ID autorizado (un solo usuario).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:
    from app.agent.core import ProductivityAgent

logger = logging.getLogger(__name__)


class TelegramBot:
    """Bot de Telegram que recibe texto y lo procesa con el agente."""

    def __init__(
        self,
        token: str,
        allowed_user_id: int,
        agent: ProductivityAgent,
    ) -> None:
        self._token = token
        self._allowed_user_id = allowed_user_id
        self._agent = agent
        self._app: Application | None = None

    async def start(self) -> None:
        """Inicializa el bot para recibir updates por webhook."""
        self._app = (
            Application.builder()
            .token(self._token)
            .updater(None)
            .build()
        )

        # Handlers
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("tareas", self._handle_tareas))
        self._app.add_handler(CommandHandler("agenda", self._handle_agenda))
        self._app.add_handler(CommandHandler("metas", self._handle_metas))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self._app.initialize()
        await self._app.start()

        logger.info("Telegram bot started (webhook mode)")

    async def stop(self) -> None:
        """Detiene el bot."""
        if self._app:
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")

    async def process_update(self, update_data: dict) -> None:
        """Procesa un update recibido por webhook."""
        if self._app is None:
            logger.error("Bot not initialized, skipping update")
            return
        update = Update.de_json(update_data, self._app.bot)
        await self._app.process_update(update)

    async def set_webhook(self, url: str, secret_token: str) -> None:
        """Registra el webhook URL en Telegram."""
        if self._app is None:
            return
        await self._app.bot.set_webhook(
            url=url,
            secret_token=secret_token,
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook registered: %s", url)

    async def send_proactive_message(self, text: str) -> None:
        """Envía un mensaje al usuario autorizado sin que haya respondido antes."""
        if self._app is None:
            logger.error("Bot not initialized, cannot send proactive message")
            return
        try:
            await self._app.bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info("Proactive message sent: %s", text[:80])
        except Exception as md_exc:
            logger.warning(
                "Markdown parse failed in proactive, retrying plain: %s", md_exc
            )
            try:
                await self._app.bot.send_message(
                    chat_id=self._allowed_user_id,
                    text=text,
                )
                logger.info("Proactive message sent (plain): %s", text[:80])
            except Exception as exc:
                logger.error("Failed to send proactive message: %s", exc)

    # ── Guards ───────────────────────────────────────────────────────────────

    def _is_authorized(self, update: Update) -> bool:
        """Verifica que el mensaje viene del usuario autorizado."""
        user_id = update.effective_user.id if update.effective_user else None
        if user_id != self._allowed_user_id:
            logger.warning("Unauthorized Telegram user: %s", user_id)
            return False
        return True

    # ── Handlers ─────────────────────────────────────────────────────────────

    async def _handle_start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Comando /start — saludo inicial."""
        if not self._is_authorized(update):
            await update.message.reply_text("No autorizado.")
            return

        await update.message.reply_text(
            "¡Hola! Soy tu asistente de productividad. 🧠\n\n"
            "Escríbeme lo que necesites:\n"
            "• Crear tareas\n"
            "• Guardar notas o ideas\n"
            "• Organizar tu día\n"
            "• Consultar pendientes\n"
            "• Ver tus metas\n\n"
            "Comandos rápidos:\n"
            "/tareas — Ver tareas pendientes\n"
            "/agenda — Ver agenda de hoy\n"
            "/metas — Ver metas activas"
        )

    async def _handle_tareas(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Comando /tareas — atajo para ver pendientes."""
        if not self._is_authorized(update):
            return
        await self._process_and_reply(update, "¿Cuáles son mis tareas pendientes?")

    async def _handle_agenda(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Comando /agenda — atajo para ver agenda de hoy."""
        if not self._is_authorized(update):
            return
        await self._process_and_reply(update, "¿Cómo está mi agenda de hoy?")

    async def _handle_metas(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Comando /metas — atajo para ver metas activas."""
        if not self._is_authorized(update):
            return
        await self._process_and_reply(update, "Muéstrame mis metas activas")

    async def _handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Procesa cualquier mensaje de texto libre."""
        if not self._is_authorized(update):
            return

        user_text = update.message.text
        if not user_text:
            return

        logger.info("Telegram message: %s", user_text[:100])
        await self._process_and_reply(update, user_text)

    async def _process_and_reply(
        self,
        update: Update,
        message: str,
    ) -> None:
        """Envía el mensaje al agente y responde en Telegram."""
        # Indicador de "escribiendo..."
        await update.message.chat.send_action("typing")

        try:
            response = await self._agent.process(message, source="telegram")

            reply = response.message

            # Si hubo acciones, agregar resumen breve
            if response.actions_taken:
                actions_summary = ", ".join(
                    a.tool for a in response.actions_taken
                )
                reply += f"\n\n📋 _{actions_summary}_"

            # Telegram tiene límite de 4096 chars por mensaje
            chunks = (
                [reply[i:i + 4096] for i in range(0, len(reply), 4096)]
                if len(reply) > 4096
                else [reply]
            )
            for chunk in chunks:
                try:
                    await update.message.reply_text(chunk, parse_mode="Markdown")
                except Exception as md_exc:
                    logger.warning(
                        "Markdown parse failed, retrying plain: %s", md_exc
                    )
                    await update.message.reply_text(chunk)

        except Exception as exc:
            logger.error("Error processing Telegram message: %s", exc)
            await update.message.reply_text(
                "Hubo un error procesando tu mensaje. Intenta de nuevo."
            )
