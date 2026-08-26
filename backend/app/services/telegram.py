"""
Telegram bot service for receiving and sending text messages.

Runs in webhook mode when a public URL is configured, and in polling mode
otherwise — a home server or a Raspberry Pi has no public URL, so polling is
what makes those deployments possible.

Only accepts messages from the authorised user id. A second, optional bot
writes the internal activity log to a separate chat.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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

_TELEGRAM_LIMIT = 4096


def _split_message(text: str, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Split text into chunks, respecting paragraphs and Telegram's size limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Find the last newline within the limit
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            # No newline available: cut at the last space
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            # No space either: hard cut
            cut = limit

        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    return chunks


class TelegramBot:
    """Telegram bot that receives text and hands it to the agent."""

    def __init__(
        self,
        token: str,
        allowed_user_id: int,
        agent: ProductivityAgent,
        memory_manager: Any = None,
        log_bot_token: str = "",
        log_chat_id: str = "",
        use_polling: bool = False,
    ) -> None:
        self._token = token
        self._allowed_user_id = allowed_user_id
        self._agent = agent
        self._memory = memory_manager
        self._log_bot_token = log_bot_token
        self._log_chat_id = log_chat_id or str(allowed_user_id)
        self._log_bot: Any = None
        self._app: Application | None = None
        # Webhooks need a public URL. On a home server or a Raspberry Pi there
        # is none, so the bot asks Telegram for updates instead of being called.
        self._use_polling = use_polling

    async def start(self) -> None:
        """Initialise the bot in whichever mode this deployment can support."""
        builder = Application.builder().token(self._token)
        if not self._use_polling:
            # No updater: updates arrive as HTTP requests on the webhook route.
            builder = builder.updater(None)
        self._app = builder.build()

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

        if self._use_polling:
            # Telegram refuses to serve getUpdates while a webhook is
            # registered, and a stale one from an earlier deployment survives
            # on their side. Clearing it is what makes the switch actually work.
            try:
                await self._app.bot.delete_webhook(drop_pending_updates=False)
            except Exception as exc:
                logger.warning("Could not clear the old webhook: %s", exc)
            await self._app.updater.start_polling()
            logger.info("Telegram bot started (polling mode)")
        else:
            logger.info("Telegram bot started (webhook mode)")

    async def stop(self) -> None:
        """Stop the bot."""
        if self._app:
            if self._use_polling and self._app.updater is not None:
                try:
                    await self._app.updater.stop()
                except Exception:
                    pass
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")
        if self._log_bot is not None:
            try:
                await self._log_bot.shutdown()
            except Exception:
                pass

    async def process_update(self, update_data: dict) -> None:
        """Process one update received over the webhook."""
        if self._app is None:
            logger.error("Bot not initialized, skipping update")
            return
        update = Update.de_json(update_data, self._app.bot)
        await self._app.process_update(update)

    async def set_webhook(self, url: str, secret_token: str) -> None:
        """Register the webhook URL with Telegram."""
        if self._app is None:
            return
        await self._app.bot.set_webhook(
            url=url,
            secret_token=secret_token,
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook registered: %s", url)

    async def _get_log_bot(self) -> Any:
        """Lazily built log bot (its own token, separate from the main one)."""
        if not self._log_bot_token:
            return None
        if self._log_bot is None:
            from telegram import Bot
            bot = Bot(token=self._log_bot_token)
            await bot.initialize()
            self._log_bot = bot
        return self._log_bot

    async def send_log_message(self, text: str) -> None:
        """
        Write to the activity log chat, separate from the user's conversation.

        Never stored in history or memory: the agent must not see it as part of the
        conversation. If no log bot is configured this is a no-op. It never raises —
        the log must not be able to break the cycle.
        """
        try:
            bot = await self._get_log_bot()
            if bot is None:
                return
            await bot.send_message(
                chat_id=self._log_chat_id,
                text=text[:_TELEGRAM_LIMIT],
                disable_notification=True,
            )
        except Exception as exc:
            logger.warning("Could not write to log chat: %s", exc)

    async def send_proactive_message(self, text: str) -> None:
        """Send a message to the authorised user without them having written first."""
        if self._app is None:
            logger.error("Bot not initialized, cannot send proactive message")
            return
        sent = False
        try:
            await self._app.bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info("Proactive message sent: %s", text[:80])
            sent = True
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
                sent = True
            except Exception as exc:
                logger.error("Failed to send proactive message: %s", exc)

        # Record it in history so the agent knows it sent this and got no reply
        if sent and self._memory is not None:
            try:
                await self._memory.save_proactive_message(text)
            except Exception as exc:
                logger.warning("Failed to save proactive message to memory: %s", exc)

    # ── Guards ────────────────────────────────────────────────────────────────

    def _is_authorized(self, update: Update) -> bool:
        """Check that the message comes from the authorised user."""
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
        """/start command — initial greeting."""
        if not self._is_authorized(update):
            await update.message.reply_text("Not authorised.")
            return

        await update.message.reply_text(
            "Hi! I'm your productivity assistant. 🧠\n\n"
            "Just tell me what you need:\n"
            "• Create tasks\n"
            "• Organise your day\n"
            "• Check what is pending\n"
            "• Review your goals\n\n"
            "I reply in whatever language you write in.\n\n"
            "Shortcuts:\n"
            "/tareas — pending tasks\n"
            "/agenda — today's agenda\n"
            "/metas — active goals"
        )

    async def _handle_tareas(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/tareas command — shortcut for pending tasks."""
        if not self._is_authorized(update):
            return
        await self._process_and_reply(update, "¿Cuáles son mis tareas pendientes?")

    async def _handle_agenda(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/agenda command — shortcut for today's agenda."""
        if not self._is_authorized(update):
            return
        await self._process_and_reply(update, "¿Cómo está mi agenda de hoy?")

    async def _handle_metas(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/metas command — shortcut for active goals."""
        if not self._is_authorized(update):
            return
        await self._process_and_reply(update, "Muéstrame mis metas activas")

    async def _handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle any free-text message."""
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
        """Hand the message to the agent and reply on Telegram."""
        # "typing..." indicator
        await update.message.chat.send_action("typing")

        try:
            response = await self._agent.process(message, source="telegram")

            reply = (response.message or "").strip()

            # Which tools ran goes to the activity log, not to the conversation.
            # It used to be appended to the reply, which meant that whenever the
            # model answered with no text the user received a message consisting
            # of nothing but a list of tool names.
            if response.actions_taken:
                await self.send_log_message(
                    f"💬 replied to you\n"
                    f"tools: {', '.join(a.tool for a in response.actions_taken)}"
                )

            if not reply:
                logger.error("Agent produced no reply for: %s", message[:80])
                reply = (
                    "I did the work but couldn't put together an answer. "
                    "Ask me again and I'll tell you how it went."
                )

            # Telegram caps messages at 4096 chars
            chunks = _split_message(reply)
            for chunk in chunks:
                try:
                    await update.message.reply_text(chunk, parse_mode="Markdown")
                except Exception as md_exc:
                    logger.warning(
                        "Markdown parse failed, retrying plain: %s", md_exc
                    )
                    try:
                        await update.message.reply_text(chunk)
                    except Exception as plain_exc:
                        logger.error("Failed to send chunk: %s", plain_exc)

        except Exception as exc:
            logger.error("Error processing Telegram message: %s", exc)
            await update.message.reply_text(
                "Something went wrong processing your message. Try again."
            )


class NullTelegramBot:
    """
    Stand-in used when no bot token is configured.

    Telegram is optional, but roughly a dozen call sites across the proactive
    cycle, the tick and the insight service write to it. Rather than guard each
    one, they get an object with the same shape that logs instead of sending.

    Proactive messages are still written to memory, so the agent's own record of
    "I said this and got no reply" stays accurate whether or not anyone is
    listening on the other end.
    """

    def __init__(self, memory_manager: Any = None) -> None:
        self._memory = memory_manager

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def process_update(self, update_data: dict) -> None:
        logger.warning("Telegram update received but no bot is configured")

    async def set_webhook(self, url: str, secret_token: str) -> None:
        return None

    async def send_log_message(self, text: str) -> None:
        logger.info("[log] %s", text[:400])

    async def send_proactive_message(self, text: str) -> None:
        logger.info("[proactive, undelivered — no Telegram configured] %s", text[:400])
        if self._memory is not None:
            try:
                await self._memory.save_proactive_message(text)
            except Exception as exc:
                logger.warning("Failed to save proactive message to memory: %s", exc)
