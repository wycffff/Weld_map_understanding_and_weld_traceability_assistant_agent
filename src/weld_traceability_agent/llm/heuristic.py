from __future__ import annotations

import re
from typing import Callable

from weld_traceability_agent.runtime_models import ConversationState, DraftRecord, IncomingMessage
from weld_traceability_agent.tools import ToolDefinition


class HeuristicLLMAdapter:
    def run_turn(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        incoming: IncomingMessage,
        conversation: ConversationState,
        active_draft: DraftRecord | None,
        tools: list[ToolDefinition],
        tool_executor: Callable[[str, dict], dict],
    ) -> str:
        text = (incoming.text or "").strip()
        normalized_text = text.lower()
        weld_id = _extract_weld_id(text) or conversation.selected_weld_id

        if active_draft and _looks_like_confirmation(text):
            result = tool_executor("confirm_draft_import", {})
            return result.get("reply_hint", "Draft confirmed.")

        if incoming.attachments:
            if conversation.selected_drawing_number and (_looks_like_photo_message(text) or weld_id):
                result = tool_executor(
                    "link_photo",
                    {
                        "weld_id": weld_id,
                        "attachment_id": incoming.attachments[0].attachment_id,
                    },
                )
                return result.get("reply_hint", "Photo linked.")

            result = tool_executor(
                "parse_drawing_draft",
                {"attachment_id": incoming.attachments[0].attachment_id},
            )
            return result.get("reply_hint", "Draft created.")

        if any(keyword in normalized_text for keyword in ("list review", "show review", "review items", "review")):
            result = tool_executor("list_review_items", {})
            return result.get("reply_hint", "Review items listed.")

        if weld_id and ("inspection" in normalized_text or "vt" in normalized_text):
            inspection_status = _extract_inspection_status(text)
            result = tool_executor(
                "update_inspection_status",
                {"weld_id": weld_id, "inspection_status": inspection_status},
            )
            return result.get("reply_hint", "Inspection status updated.")

        if weld_id and any(
            token in normalized_text
            for token in ("done", "blocked", "in progress", "started", "start", "complete", "completed")
        ):
            status = _extract_weld_status(text)
            result = tool_executor(
                "update_weld_status",
                {"weld_id": weld_id, "to_status": status},
            )
            return result.get("reply_hint", "Weld status updated.")

        if any(keyword in normalized_text for keyword in ("dwg", "drawing", "spool")):
            result = tool_executor("search_drawings", {"query": text})
            return result.get("reply_hint", "Drawing search complete.")

        if weld_id:
            result = tool_executor("search_welds", {"query": weld_id})
            return result.get("reply_hint", "Weld search complete.")

        return (
            "I need a bit more information. "
            "You can send a drawing, say 'confirm import', "
            "or provide the drawing number and weld ID directly."
        )


def _looks_like_confirmation(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "confirm",
        "confirm import",
        "confirm entry",
        "confirm database import",
        "yes",
        "ok",
        "okay",
        "approved",
    }


def _looks_like_photo_message(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("photo", "picture", "image"))


def _extract_weld_id(text: str) -> str | None:
    match = re.search(r"\bW[- ]?\d+\b", text, re.IGNORECASE)
    if match:
        return match.group(0).replace(" ", "")
    match = re.search(r"\b\d{1,4}\b", text)
    if match:
        return match.group(0)
    return None


def _extract_weld_status(text: str) -> str:
    lowered = text.lower()
    if "blocked" in lowered:
        return "blocked"
    if any(token in lowered for token in ("in progress", "started", "start")):
        return "in_progress"
    return "done"


def _extract_inspection_status(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("rejected", "failed", "not passed")):
        return "rejected"
    if "pending" in lowered:
        return "pending"
    return "accepted"
