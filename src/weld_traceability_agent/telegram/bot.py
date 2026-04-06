from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from weld_traceability_agent.config import AgentAppConfig, load_agent_config
from weld_traceability_agent.orchestrator import AgentOrchestrator
from weld_traceability_agent.runtime_models import BotAttachment, IncomingMessage


logger = logging.getLogger(__name__)

PROCESSING_PLACEHOLDER_DELAY_SEC = 5.0
PROCESSING_PLACEHOLDER_TEXT = "Processing..."
PROCESSING_FAILURE_TEXT = "Processing failed. Check the local configuration or logs."


class TelegramBotRuntime:
    def __init__(self, config: AgentAppConfig):
        self.config = config
        self.orchestrator = AgentOrchestrator(config)
        self.queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self.workers: list[asyncio.Task] = []

    async def post_init(self, application: Application) -> None:
        for _ in range(max(1, self.config.telegram.worker_count)):
            self.workers.append(asyncio.create_task(self.worker_loop(application)))

    async def post_shutdown(self, application: Application) -> None:
        for worker in self.workers:
            worker.cancel()
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "Started. Send a drawing, a status update, or a weld photo."
        )

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id) if update.effective_user else ""
        if not self.orchestrator.is_allowed(chat_id, user_id):
            return

        attachments = await self._collect_attachments(update, context)
        incoming = IncomingMessage(
            channel="telegram",
            chat_id=chat_id,
            user_id=user_id,
            message_id=str(update.message.message_id),
            text=update.message.text or update.message.caption or "",
            attachments=attachments,
        )
        await self.queue.put(incoming)

    async def worker_loop(self, application: Application) -> None:
        while True:
            incoming = await self.queue.get()
            try:
                await self.process_incoming(application.bot, incoming)
            finally:
                self.queue.task_done()

    async def process_incoming(self, bot, incoming: IncomingMessage) -> None:
        placeholder_task = asyncio.create_task(self._send_processing_placeholder(bot, incoming.chat_id))
        processing_task = asyncio.create_task(asyncio.to_thread(self.orchestrator.process_message, incoming))
        try:
            plan = await processing_task
        except Exception:
            logger.exception("Failed to process Telegram message")
            placeholder_message = await self._resolve_placeholder_message(placeholder_task)
            await self._send_failure(bot, incoming.chat_id, placeholder_message)
            return

        placeholder_message = await self._resolve_placeholder_message(placeholder_task)
        await self._deliver_plan(bot, incoming.chat_id, plan.messages, placeholder_message)

    async def _resolve_placeholder_message(self, placeholder_task: asyncio.Task):
        if not placeholder_task.done():
            placeholder_task.cancel()
            with suppress(asyncio.CancelledError):
                await placeholder_task
            return None
        try:
            return placeholder_task.result()
        except Exception:
            logger.exception("Failed to send processing placeholder")
            return None

    async def _send_processing_placeholder(self, bot, chat_id: str):
        await asyncio.sleep(PROCESSING_PLACEHOLDER_DELAY_SEC)
        return await bot.send_message(chat_id=chat_id, text=PROCESSING_PLACEHOLDER_TEXT)

    async def _deliver_plan(self, bot, submitter_chat_id: str, messages, placeholder_message) -> None:
        submitter_messages = [message for message in messages if str(message.chat_id) == str(submitter_chat_id)]
        other_messages = [message for message in messages if str(message.chat_id) != str(submitter_chat_id)]

        if placeholder_message:
            if submitter_messages:
                await bot.edit_message_text(
                    chat_id=submitter_chat_id,
                    message_id=placeholder_message.message_id,
                    text=submitter_messages[0].text,
                )
                for message in submitter_messages[1:]:
                    await bot.send_message(chat_id=message.chat_id, text=message.text)
            else:
                await bot.delete_message(
                    chat_id=submitter_chat_id,
                    message_id=placeholder_message.message_id,
                )
        else:
            for message in submitter_messages:
                await bot.send_message(chat_id=message.chat_id, text=message.text)

        for message in other_messages:
            await bot.send_message(chat_id=message.chat_id, text=message.text)

    async def _send_failure(self, bot, chat_id: str, placeholder_message) -> None:
        if placeholder_message:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=placeholder_message.message_id,
                text=PROCESSING_FAILURE_TEXT,
            )
            return
        await bot.send_message(chat_id=chat_id, text=PROCESSING_FAILURE_TEXT)

    async def _collect_attachments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[BotAttachment]:
        message = update.message
        if not message:
            return []

        base_dir = self.config.resolve_path(self.config.agent.inbox_dir) / str(message.chat_id) / str(message.message_id)
        base_dir.mkdir(parents=True, exist_ok=True)
        attachments: list[BotAttachment] = []

        if message.photo:
            photo = message.photo[-1]
            telegram_file = await context.bot.get_file(photo.file_id)
            target = base_dir / f"{uuid4().hex[:8]}_photo.jpg"
            await telegram_file.download_to_drive(custom_path=str(target))
            attachments.append(
                BotAttachment(
                    attachment_id=f"tg_photo_{photo.file_unique_id}",
                    kind="photo",
                    file_name=target.name,
                    local_path=str(target),
                    mime_type="image/jpeg",
                )
            )

        if message.document:
            telegram_file = await context.bot.get_file(message.document.file_id)
            file_name = message.document.file_name or f"{uuid4().hex[:8]}_document"
            target = base_dir / file_name
            await telegram_file.download_to_drive(custom_path=str(target))
            mime_type = message.document.mime_type
            kind = "image" if (mime_type or "").startswith("image/") else "document"
            attachments.append(
                BotAttachment(
                    attachment_id=f"tg_doc_{message.document.file_unique_id}",
                    kind=kind,
                    file_name=target.name,
                    local_path=str(target),
                    mime_type=mime_type,
                )
            )

        return attachments


def build_application(config: AgentAppConfig) -> Application:
    token = os.getenv(config.telegram.bot_token_env)
    if not token:
        raise RuntimeError(f"Missing Telegram bot token in env var {config.telegram.bot_token_env}")

    runtime = TelegramBotRuntime(config)
    application = (
        Application.builder()
        .token(token)
        .post_init(runtime.post_init)
        .post_shutdown(runtime.post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", runtime.start_command))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, runtime.message_handler))
    return application


def run_telegram_bot(config: AgentAppConfig | None = None) -> None:
    app_config = config or load_agent_config()
    application = build_application(app_config)
    application.run_polling()


def main() -> None:
    run_telegram_bot()


if __name__ == "__main__":
    main()
