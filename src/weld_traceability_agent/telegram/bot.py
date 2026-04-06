from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from weld_traceability_agent.config import AgentAppConfig, load_agent_config
from weld_traceability_agent.orchestrator import AgentOrchestrator
from weld_traceability_agent.runtime_models import BotAttachment, IncomingMessage


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
            "\u5df2\u542f\u52a8\u3002\u76f4\u63a5\u53d1\u56fe\u7eb8\u3001\u72b6\u6001\u66f4\u65b0\u6216\u710a\u53e3\u7167\u7247\u5373\u53ef\u3002"
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
        if self.config.telegram.ack_text:
            await update.message.reply_text(self.config.telegram.ack_text)

    async def worker_loop(self, application: Application) -> None:
        while True:
            incoming = await self.queue.get()
            try:
                plan = self.orchestrator.process_message(incoming)
                for message in plan.messages:
                    await application.bot.send_message(chat_id=message.chat_id, text=message.text)
            finally:
                self.queue.task_done()

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
