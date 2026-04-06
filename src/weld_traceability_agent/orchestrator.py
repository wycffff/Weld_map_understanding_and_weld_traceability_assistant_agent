from __future__ import annotations

from typing import Callable

from weld_traceability_agent.assistant_bridge import AssistantBridge
from weld_traceability_agent.config import AgentAppConfig
from weld_traceability_agent.llm import HeuristicLLMAdapter, OpenAIResponsesLLMAdapter
from weld_traceability_agent.runtime_models import BotReplyPlan, ConversationState, IncomingMessage, OutgoingMessage, ProcessedMessage
from weld_traceability_agent.state_store import RuntimeStateStore
from weld_traceability_agent.tools import AgentToolbox


class AgentOrchestrator:
    def __init__(self, config: AgentAppConfig):
        self.config = config
        self.store = RuntimeStateStore(config)
        self.bridge = AssistantBridge(config)
        self.toolbox = AgentToolbox(config, self.store, self.bridge)
        self.heuristic_llm = HeuristicLLMAdapter()
        self.primary_llm = self._build_primary_llm()

    def process_message(self, incoming: IncomingMessage) -> BotReplyPlan:
        if not self.is_allowed(incoming.chat_id, incoming.user_id):
            return BotReplyPlan.empty()

        processed = ProcessedMessage(channel=incoming.channel, chat_id=incoming.chat_id, message_id=incoming.message_id)
        if not self.store.touch_processed_message(processed):
            return BotReplyPlan.empty()

        conversation = self.store.get_conversation(incoming.channel, incoming.chat_id, self.config.agent.state_ttl_hours)
        active_draft = self.store.get_active_draft(incoming.chat_id)

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(incoming, conversation, active_draft)
        self.toolbox.reset_outbox()

        def tool_executor(tool_name: str, arguments: dict) -> dict:
            return self.toolbox.invoke(tool_name, arguments, incoming)

        try:
            reply_text = self._run_with_adapter(
                self.primary_llm,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                incoming=incoming,
                conversation=conversation,
                active_draft=active_draft,
                tool_executor=tool_executor,
            )
        except Exception:
            reply_text = self._run_with_adapter(
                self.heuristic_llm,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                incoming=incoming,
                conversation=conversation,
                active_draft=active_draft,
                tool_executor=tool_executor,
            )

        messages = []
        if reply_text.strip():
            messages.append(OutgoingMessage(chat_id=incoming.chat_id, text=reply_text.strip()))
        messages.extend(self.toolbox.drain_outbox())
        return BotReplyPlan(messages=messages)

    def is_allowed(self, chat_id: str, user_id: str) -> bool:
        allowed_chats = {str(value) for value in self.config.security.allowed_chats}
        allowed_users = {str(value) for value in self.config.security.allowed_users}
        if not allowed_chats and not allowed_users:
            return True
        return str(chat_id) in allowed_chats or str(user_id) in allowed_users

    def _run_with_adapter(
        self,
        adapter,
        *,
        system_prompt: str,
        user_prompt: str,
        incoming: IncomingMessage,
        conversation: ConversationState,
        active_draft,
        tool_executor: Callable[[str, dict], dict],
    ) -> str:
        return adapter.run_turn(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            incoming=incoming,
            conversation=conversation,
            active_draft=active_draft,
            tools=self.toolbox.definitions(),
            tool_executor=tool_executor,
        )

    def _build_primary_llm(self):
        if not self.config.openai.enabled:
            return self.heuristic_llm
        try:
            return OpenAIResponsesLLMAdapter(self.config.openai)
        except Exception:
            return self.heuristic_llm

    def _build_system_prompt(self) -> str:
        return (
            "You are a weld traceability Telegram agent.\n"
            "Always reply in concise Simplified Chinese.\n"
            "Rules:\n"
            "1. Draft parsing must happen before import. Use parse_drawing_draft first, and only call confirm_draft_import after explicit user confirmation.\n"
            "2. Use only the provided business tools. Never pretend that a database write already happened.\n"
            "3. Any database mutation requires a unique drawing context and a unique weld context. If context is ambiguous, ask one short follow-up question.\n"
            "4. Do not return JSON to the user. Give short operator-facing conclusions.\n"
            "5. If a tool returns reply_hint, prefer it when drafting the final reply.\n"
        )

    def _build_user_prompt(self, incoming: IncomingMessage, conversation: ConversationState, active_draft) -> str:
        attachment_lines = [
            f"- id={attachment.attachment_id}, kind={attachment.kind}, file={attachment.file_name}"
            for attachment in incoming.attachments
        ] or ["- none"]
        draft_summary = active_draft.preview_text if active_draft else "none"
        return (
            f"chat_id: {incoming.chat_id}\n"
            f"user_id: {incoming.user_id}\n"
            f"message: {incoming.text or '(empty)'}\n"
            "attachments:\n"
            + "\n".join(attachment_lines)
            + "\n"
            + f"conversation_state: selected_drawing={conversation.selected_drawing_number}, "
            f"selected_weld={conversation.selected_weld_id}, pending_action={conversation.pending_action}\n"
            + f"active_draft: {draft_summary}\n"
        )
