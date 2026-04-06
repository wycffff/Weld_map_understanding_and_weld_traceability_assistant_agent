from __future__ import annotations

import asyncio
import time

from weld_traceability_agent.runtime_models import BotReplyPlan, IncomingMessage, OutgoingMessage
from weld_traceability_agent.telegram import bot as telegram_bot_module
from weld_traceability_agent.telegram.bot import TelegramBotRuntime


class FakeTelegramMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id


class FakeBot:
    def __init__(self):
        self.sent: list[dict[str, str | int]] = []
        self.edits: list[dict[str, str | int]] = []
        self.deleted: list[dict[str, str | int]] = []
        self._next_message_id = 1

    async def send_message(self, chat_id: str, text: str):
        message = FakeTelegramMessage(self._next_message_id)
        self._next_message_id += 1
        self.sent.append({"chat_id": str(chat_id), "text": text, "message_id": message.message_id})
        return message

    async def edit_message_text(self, chat_id: str, message_id: int, text: str):
        self.edits.append({"chat_id": str(chat_id), "message_id": message_id, "text": text})

    async def delete_message(self, chat_id: str, message_id: int):
        self.deleted.append({"chat_id": str(chat_id), "message_id": message_id})


class FakeOrchestrator:
    def __init__(self, handler):
        self._handler = handler

    def process_message(self, incoming: IncomingMessage) -> BotReplyPlan:
        return self._handler(incoming)


def test_process_incoming_fast_completion_skips_placeholder(monkeypatch):
    monkeypatch.setattr(telegram_bot_module, "PROCESSING_PLACEHOLDER_DELAY_SEC", 0.05)
    runtime = TelegramBotRuntime.__new__(TelegramBotRuntime)
    runtime.orchestrator = FakeOrchestrator(
        lambda incoming: BotReplyPlan(
            messages=[OutgoingMessage(chat_id=incoming.chat_id, text="Imported drawing C-52.")]
        )
    )
    bot = FakeBot()
    incoming = IncomingMessage(chat_id="1001", user_id="501", message_id="msg-fast")

    asyncio.run(runtime.process_incoming(bot, incoming))

    assert bot.sent == [{"chat_id": "1001", "text": "Imported drawing C-52.", "message_id": 1}]
    assert bot.edits == []
    assert bot.deleted == []


def test_process_incoming_slow_completion_edits_placeholder(monkeypatch):
    monkeypatch.setattr(telegram_bot_module, "PROCESSING_PLACEHOLDER_DELAY_SEC", 0.01)

    def slow_success(incoming: IncomingMessage) -> BotReplyPlan:
        time.sleep(0.05)
        return BotReplyPlan(messages=[OutgoingMessage(chat_id=incoming.chat_id, text="Draft created. Please confirm.")])

    runtime = TelegramBotRuntime.__new__(TelegramBotRuntime)
    runtime.orchestrator = FakeOrchestrator(slow_success)
    bot = FakeBot()
    incoming = IncomingMessage(chat_id="1001", user_id="501", message_id="msg-slow")

    asyncio.run(runtime.process_incoming(bot, incoming))

    assert bot.sent == [
        {
            "chat_id": "1001",
            "text": telegram_bot_module.PROCESSING_PLACEHOLDER_TEXT,
            "message_id": 1,
        }
    ]
    assert bot.edits == [
        {
            "chat_id": "1001",
            "message_id": 1,
            "text": "Draft created. Please confirm.",
        }
    ]
    assert bot.deleted == []


def test_process_incoming_slow_failure_edits_placeholder(monkeypatch):
    monkeypatch.setattr(telegram_bot_module, "PROCESSING_PLACEHOLDER_DELAY_SEC", 0.01)

    def slow_failure(incoming: IncomingMessage) -> BotReplyPlan:
        time.sleep(0.05)
        raise RuntimeError("boom")

    runtime = TelegramBotRuntime.__new__(TelegramBotRuntime)
    runtime.orchestrator = FakeOrchestrator(slow_failure)
    bot = FakeBot()
    incoming = IncomingMessage(chat_id="1001", user_id="501", message_id="msg-fail")

    asyncio.run(runtime.process_incoming(bot, incoming))

    assert bot.sent == [
        {
            "chat_id": "1001",
            "text": telegram_bot_module.PROCESSING_PLACEHOLDER_TEXT,
            "message_id": 1,
        }
    ]
    assert bot.edits == [
        {
            "chat_id": "1001",
            "message_id": 1,
            "text": telegram_bot_module.PROCESSING_FAILURE_TEXT,
        }
    ]
    assert bot.deleted == []
