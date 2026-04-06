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
            return result.get("reply_hint", "\u8349\u7a3f\u5df2\u786e\u8ba4\u3002")

        if incoming.attachments:
            if conversation.selected_drawing_number and (_looks_like_photo_message(text) or weld_id):
                result = tool_executor(
                    "link_photo",
                    {
                        "weld_id": weld_id,
                        "attachment_id": incoming.attachments[0].attachment_id,
                    },
                )
                return result.get("reply_hint", "\u5df2\u6302\u63a5\u7167\u7247\u3002")

            result = tool_executor(
                "parse_drawing_draft",
                {"attachment_id": incoming.attachments[0].attachment_id},
            )
            return result.get("reply_hint", "\u5df2\u751f\u6210\u8349\u7a3f\u3002")

        if "review" in normalized_text or "\u5ba1\u67e5" in text or "\u590d\u6838" in text:
            result = tool_executor("list_review_items", {})
            return result.get("reply_hint", "\u5df2\u5217\u51fa\u5ba1\u67e5\u9879\u3002")

        if weld_id and ("\u68c0\u9a8c" in text or "inspection" in normalized_text or "vt" in normalized_text):
            inspection_status = _extract_inspection_status(text)
            result = tool_executor(
                "update_inspection_status",
                {"weld_id": weld_id, "inspection_status": inspection_status},
            )
            return result.get("reply_hint", "\u5df2\u66f4\u65b0\u68c0\u9a8c\u72b6\u6001\u3002")

        if weld_id and any(
            token in text
            for token in ("\u5b8c\u6210", "done", "blocked", "\u8fdb\u884c\u4e2d", "\u5f00\u59cb")
        ):
            status = _extract_weld_status(text)
            result = tool_executor(
                "update_weld_status",
                {"weld_id": weld_id, "to_status": status},
            )
            return result.get("reply_hint", "\u5df2\u66f4\u65b0\u710a\u63a5\u72b6\u6001\u3002")

        if any(keyword in normalized_text for keyword in ("dwg", "drawing", "spool")) or any(
            token in text for token in ("\u56fe\u7eb8", "\u56fe\u53f7")
        ):
            result = tool_executor("search_drawings", {"query": text})
            return result.get("reply_hint", "\u5df2\u68c0\u7d22\u56fe\u7eb8\u3002")

        if weld_id:
            result = tool_executor("search_welds", {"query": weld_id})
            return result.get("reply_hint", "\u5df2\u68c0\u7d22\u710a\u53e3\u3002")

        return (
            "\u6211\u8fd8\u9700\u8981\u66f4\u660e\u786e\u4e00\u70b9\u7684\u4fe1\u606f\u3002"
            "\u4f60\u53ef\u4ee5\u53d1\u56fe\u7eb8\u3001\u8bf4\u201c\u786e\u8ba4\u5f55\u5165\u201d\uff0c"
            "\u6216\u8005\u76f4\u63a5\u7ed9\u6211\u56fe\u53f7\u548c\u710a\u53e3\u53f7\u3002"
        )


def _looks_like_confirmation(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "\u786e\u8ba4",
        "\u786e\u8ba4\u5f55\u5165",
        "yes",
        "ok",
        "\u597d\u7684",
        "\u53ef\u4ee5\u5f55\u5165",
        "\u786e\u8ba4\u5165\u5e93",
    }


def _looks_like_photo_message(text: str) -> bool:
    lowered = text.lower()
    return any(token in text for token in ("\u7167\u7247", "\u76f8\u7247", "\u56fe\u7247")) or "photo" in lowered


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
    if "blocked" in lowered or "\u963b\u585e" in text:
        return "blocked"
    if "\u8fdb\u884c\u4e2d" in text or "in progress" in lowered:
        return "in_progress"
    if "\u5f00\u59cb" in text:
        return "in_progress"
    return "done"


def _extract_inspection_status(text: str) -> str:
    lowered = text.lower()
    if "rejected" in lowered or "\u4e0d\u901a\u8fc7" in text or "\u62d2\u6536" in text:
        return "rejected"
    if "pending" in lowered or "\u5f85\u68c0" in text:
        return "pending"
    return "accepted"
