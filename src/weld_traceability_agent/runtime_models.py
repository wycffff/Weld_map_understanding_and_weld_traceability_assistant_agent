from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BotAttachment(BaseModel):
    attachment_id: str
    kind: str
    file_name: str
    local_path: str
    mime_type: str | None = None


class IncomingMessage(BaseModel):
    channel: str = "telegram"
    chat_id: str
    user_id: str
    message_id: str
    text: str = ""
    attachments: list[BotAttachment] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=utc_now)


class ConversationState(BaseModel):
    channel: str = "telegram"
    chat_id: str
    active_task_id: str | None = None
    active_draft_id: str | None = None
    selected_drawing_number: str | None = None
    selected_weld_id: str | None = None
    pending_action: str | None = None
    expires_at: datetime | None = None
    last_message_at: datetime = Field(default_factory=utc_now)

    def refresh(self, ttl_hours: int) -> None:
        self.last_message_at = utc_now()
        self.expires_at = self.last_message_at + timedelta(hours=ttl_hours)

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now or utc_now()) >= self.expires_at


class AgentTask(BaseModel):
    task_id: str
    chat_id: str
    kind: str
    status: str
    source_message_id: str
    draft_path: str | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DraftRecord(BaseModel):
    draft_id: str
    chat_id: str
    task_id: str
    source_message_id: str
    source_attachment_id: str
    input_file_path: str
    structured_json_path: str
    preview_text: str
    drawing_number_candidate: str | None = None
    review_count: int = 0
    supported: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    confirmed_at: datetime | None = None


class ProcessedMessage(BaseModel):
    channel: str
    chat_id: str
    message_id: str
    processed_at: datetime = Field(default_factory=utc_now)


class OutgoingMessage(BaseModel):
    chat_id: str
    text: str


class BotReplyPlan(BaseModel):
    messages: list[OutgoingMessage] = Field(default_factory=list)

    @classmethod
    def empty(cls) -> "BotReplyPlan":
        return cls(messages=[])

